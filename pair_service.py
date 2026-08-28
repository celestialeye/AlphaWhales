import json
import math
import os
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
from openbb import obb
from statsmodels.api import OLS, add_constant
from statsmodels.tsa.stattools import adfuller, coint

from config import CACHE_DIR, CACHE_TTL_HOURS


class PairSignalService:
    ENTRY_ZSCORE = 1.5
    EXIT_ZSCORE = 0.5
    MIN_OBSERVATIONS = 504
    WINDOW_DAYS = 1260
    MARKET_ALIASES = {
        "BRKA": "BRK-A",
        "BRKB": "BRK-B",
        "HEIA": "HEI-A"
    }
    INTERNAL_ALIASES = {value: key for key, value in MARKET_ALIASES.items()}
    SHARE_CLASS_PAIRS = {
        frozenset({"GOOG", "GOOGL"}),
        frozenset({"BRKA", "BRKB"})
    }

    def __init__(self):
        self.universe_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data",
            "reference",
            "full_universe.csv"
        )

    def _cache_path(self, ticker):
        cache_dir = os.path.join(CACHE_DIR, "pair_signals")
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, f"{ticker}.json")

    def _load_cache(self, ticker):
        path = self._cache_path(ticker)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if payload.get("cache_version") != 2:
                return None
            updated_at = datetime.fromisoformat(payload["last_updated"])
            if datetime.now(timezone.utc) - updated_at <= timedelta(hours=CACHE_TTL_HOURS):
                return payload
        except Exception:
            return None
        return None

    def _save_cache(self, ticker, payload):
        with open(self._cache_path(ticker), "w", encoding="utf-8") as f:
            json.dump(payload, f, default=str)

    @staticmethod
    def _market_symbol(ticker):
        return PairSignalService.MARKET_ALIASES.get(ticker, ticker)

    @staticmethod
    def _internal_symbol(ticker):
        return PairSignalService.INTERNAL_ALIASES.get(ticker, ticker)

    def _economic_peers(self, ticker, n_peers=10):
        universe = pd.read_csv(self.universe_path)
        if "active" in universe.columns:
            universe = universe[
                universe["active"].astype(str).str.lower().isin({"true", "1"})
            ]
        market_ticker = self._market_symbol(ticker)
        focal = universe[universe["ticker"] == market_ticker]
        if focal.empty:
            return [], None, None

        focal_row = focal.iloc[0]
        industry = focal_row["industry"]
        candidates = universe[
            (universe["industry"] == industry)
            & (universe["ticker"] != market_ticker)
        ].copy()
        candidates["market_cap_b"] = pd.to_numeric(
            candidates["market_cap_b"], errors="coerce"
        ).fillna(0)
        candidates = candidates.sort_values("market_cap_b", ascending=False)
        peer_rows = candidates.head(n_peers).to_dict("records")
        for row in peer_rows:
            row["ticker"] = self._internal_symbol(row["ticker"])
        return peer_rows, industry, focal_row.get("name")

    @staticmethod
    def _half_life(spread):
        series = pd.Series(spread).dropna()
        lag = series.shift(1).dropna()
        diff = series.diff().dropna()
        lag, diff = lag.align(diff, join="inner")
        if len(lag) < 10:
            return float("inf")
        result = OLS(diff.values, lag.values).fit()
        phi = float(result.params[0])
        if phi >= 0 or 1 + phi <= 0:
            return float("inf")
        return -math.log(2) / math.log(1 + phi)

    @staticmethod
    def _bidirectional_cointegration(a, b):
        try:
            p_ab = float(coint(a, b)[1])
        except Exception:
            p_ab = 1.0
        try:
            p_ba = float(coint(b, a)[1])
        except Exception:
            p_ba = 1.0
        return min(p_ab, p_ba), p_ab, p_ba

    @staticmethod
    def _subwindow_passes(a, b):
        n = len(a)
        windows = [
            (a[: int(n * 0.4)], b[: int(n * 0.4)]),
            (a[int(n * 0.6):], b[int(n * 0.6):])
        ]
        return sum(
            1
            for sub_a, sub_b in windows
            if len(sub_a) >= 200
            and PairSignalService._bidirectional_cointegration(sub_a, sub_b)[0] < 0.10
        )

    @staticmethod
    def _quality_score(rank, pool_size, pvalue, oos_passes, half_life, subwindows):
        semantic = (
            1.0
            if pool_size <= 1
            else max(0.0, 1.0 - (rank - 1) / (pool_size - 1))
        )
        pvalue_score = min(-math.log10(max(pvalue, 1e-10)), 5.0) / 5.0
        if not math.isfinite(half_life) or half_life <= 10 or half_life >= 90:
            half_life_score = 0.0
        elif half_life <= 30:
            half_life_score = (half_life - 10) / 20
        else:
            half_life_score = (90 - half_life) / 60
        composite = (
            0.25 * semantic
            + 0.20 * pvalue_score
            + 0.20 * (1.0 if oos_passes else 0.0)
            + 0.15 * half_life_score
            + 0.20 * (subwindows / 2)
        )
        if pvalue > 0.10:
            composite = min(composite, 0.30)
        return max(0.0, min(1.0, composite))

    def _analyze_candidate(self, ticker, peer, prices, rank, pool_size):
        aligned = pd.concat(
            [prices[ticker], prices[peer]],
            axis=1,
            keys=[ticker, peer]
        ).dropna().tail(self.WINDOW_DAYS)
        if len(aligned) < self.MIN_OBSERVATIONS:
            return None

        a = aligned[ticker].values.astype(float)
        b = aligned[peer].values.astype(float)
        pvalue, p_ab, p_ba = self._bidirectional_cointegration(a, b)
        regression = OLS(a, add_constant(b)).fit()
        intercept = float(regression.params[0])
        hedge_ratio = float(regression.params[1])
        spread = a - intercept - hedge_ratio * b
        spread_std = float(np.std(spread, ddof=1))
        zscore = float(spread[-1] / spread_std) if spread_std > 0 else 0.0
        half_life = self._half_life(spread)

        split = int(len(a) * 0.7)
        train_regression = OLS(a[:split], add_constant(b[:split])).fit()
        test_spread = (
            a[split:]
            - float(train_regression.params[0])
            - float(train_regression.params[1]) * b[split:]
        )
        try:
            oos_pvalue = float(adfuller(test_spread, autolag="AIC")[1])
        except Exception:
            oos_pvalue = 1.0
        oos_passes = oos_pvalue < 0.10
        subwindows = self._subwindow_passes(a, b)
        quality = self._quality_score(
            rank,
            pool_size,
            pvalue,
            oos_passes,
            half_life,
            subwindows
        )
        return {
            "peer": peer,
            "observations": len(aligned),
            "correlation": round(float(aligned.corr().iloc[0, 1]), 4),
            "eg_pvalue": round(pvalue, 6),
            "eg_pvalue_a_on_b": round(p_ab, 6),
            "eg_pvalue_b_on_a": round(p_ba, 6),
            "oos_adf_pvalue": round(oos_pvalue, 6),
            "oos_passes": oos_passes,
            "subwindow_pass_count": subwindows,
            "half_life_days": round(half_life, 2) if math.isfinite(half_life) else None,
            "hedge_ratio": round(hedge_ratio, 4),
            "zscore": round(zscore, 3),
            "quality_score": round(quality, 3),
            "as_of": aligned.index[-1].date().isoformat()
        }

    def analyze(self, ticker):
        ticker = ticker.strip().upper()
        cached = self._load_cache(ticker)
        if cached is not None:
            return cached

        peers, industry, focal_name = self._economic_peers(ticker)
        if not peers:
            payload = {
                "cache_version": 2,
                "ticker": ticker,
                "status": "NO_PAIR_FOUND",
                "confidence": "none",
                "evidence_tier": "HYPOTHESIS",
                "message": "No same-industry economic peers are available.",
                "last_updated": datetime.now(timezone.utc).isoformat()
            }
            self._save_cache(ticker, payload)
            return payload

        peer_tickers = [row["ticker"] for row in peers]
        symbols = [ticker, *peer_tickers]
        market_symbols = [self._market_symbol(symbol) for symbol in symbols]
        reverse_symbols = dict(zip(market_symbols, symbols))
        frame = obb.equity.price.historical(
            symbol=market_symbols,
            start_date=date.today() - timedelta(days=365 * 6),
            end_date=date.today(),
            provider="yfinance",
            interval="1d",
            adjustment="splits_and_dividends"
        ).to_df()
        if frame is None or frame.empty:
            raise RuntimeError(f"No pair price data returned for {ticker}")
        frame = frame.reset_index()
        if "symbol" not in frame.columns and len(market_symbols) == 1:
            frame["symbol"] = market_symbols[0]
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["symbol"] = frame["symbol"].astype(str).str.upper().map(
            lambda symbol: reverse_symbols.get(symbol, symbol)
        )
        prices = {
            symbol: rows.sort_values("date").set_index("date")["close"].astype(float)
            for symbol, rows in frame.groupby("symbol")
        }
        if ticker not in prices:
            raise RuntimeError(f"No focal price history returned for {ticker}")

        candidates = []
        for rank, peer_row in enumerate(peers, start=1):
            peer = peer_row["ticker"]
            if peer not in prices:
                continue
            result = self._analyze_candidate(
                ticker,
                peer,
                prices,
                rank,
                len(peers)
            )
            if result is not None:
                result["peer_name"] = peer_row.get("name")
                result["semantic_rank"] = rank
                candidates.append(result)

        candidates.sort(key=lambda item: item["quality_score"], reverse=True)
        if not candidates:
            raise RuntimeError(f"No candidate pair had sufficient history for {ticker}")

        best = candidates[0]
        corrected_alpha = 0.05 / max(2 * len(peers), 1)
        disciplined = (
            best["eg_pvalue"] < corrected_alpha
            and best["oos_passes"]
            and best["half_life_days"] is not None
            and 10 <= best["half_life_days"] <= 120
            and best["subwindow_pass_count"] >= 1
            and best["hedge_ratio"] > 0
        )
        confidence = (
            "high"
            if disciplined and best["quality_score"] >= 0.75
            else "medium"
            if disciplined and best["quality_score"] >= 0.55
            else "low"
            if disciplined and best["quality_score"] >= 0.35
            else "none"
        )
        peer = best["peer"]
        zscore = best["zscore"]
        observed_cheap_leg = ticker if zscore < 0 else peer
        observed_expensive_leg = peer if zscore < 0 else ticker
        is_share_class_pair = frozenset({ticker, peer}) in self.SHARE_CLASS_PAIRS
        pair_type = (
            "SHARE_CLASS_RELATIVE_VALUE"
            if is_share_class_pair
            else "SAME_INDUSTRY_COINTEGRATION"
            if disciplined
            else "SAME_INDUSTRY_CANDIDATE"
        )

        if not disciplined:
            status = "NO_VALID_PAIR"
            ready = False
            cheap_leg = None
            expensive_leg = None
            action = "WAIT - pair fails disciplined statistical gates"
        elif abs(zscore) < self.ENTRY_ZSCORE:
            status = "WAIT"
            ready = False
            cheap_leg = ticker if zscore < 0 else peer
            expensive_leg = peer if zscore < 0 else ticker
            action = f"WAIT - spread z-score {zscore:+.2f} has not reached +/-{self.ENTRY_ZSCORE:.1f}"
        else:
            status = "READY"
            ready = True
            cheap_leg = ticker if zscore < 0 else peer
            expensive_leg = peer if zscore < 0 else ticker
            action = f"BUY {cheap_leg}; hedge the relatively expensive {expensive_leg}"

        if ready and cheap_leg == ticker:
            stock_execution = (
                f"Long 1 share of {ticker} / short {best['hedge_ratio']:.3f} "
                f"shares of {peer} (OLS price hedge ratio, not return beta)"
            )
        elif ready:
            stock_execution = (
                f"Long {best['hedge_ratio']:.3f} shares of {peer} / short 1 "
                f"share of {ticker} (OLS price hedge ratio, not return beta)"
            )
        else:
            stock_execution = None

        payload = {
            "cache_version": 2,
            "ticker": ticker,
            "status": status,
            "ready": ready,
            "confidence": confidence,
            "evidence_tier": "HYPOTHESIS",
            "industry": industry,
            "pair_type": pair_type,
            "best_pair": best,
            "corrected_alpha": round(corrected_alpha, 6),
            "entry_zscore": self.ENTRY_ZSCORE,
            "exit_zscore": self.EXIT_ZSCORE,
            "cheap_leg": cheap_leg,
            "expensive_leg": expensive_leg,
            "observed_cheap_leg": observed_cheap_leg,
            "observed_expensive_leg": observed_expensive_leg,
            "action": action,
            "stock_execution": stock_execution,
            "put_execution": (
                f"Buy {cheap_leg} stock / buy a put on {expensive_leg}"
                if ready
                else None
            ),
            "put_execution_note": (
                "The stock-plus-put version has defined downside on the hedge leg, "
                "but it is not market-neutral and introduces option premium, theta, "
                "implied-volatility, strike, and expiry risk."
            ),
            "candidates": candidates[:5],
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "methodology": (
                "Economic peers are restricted to the same industry. Candidates use "
                "five years of prices, bidirectional Engle-Granger tests, Bonferroni "
                "correction, 70/30 out-of-sample ADF persistence, two non-overlapping "
                "sub-window checks, and a 10-120 day half-life gate. Signal readiness "
                "also requires |z-score| >= 1.5. Hypothesis-tier only; no live forward record."
            )
        }
        self._save_cache(ticker, payload)
        return payload
