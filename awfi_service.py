from __future__ import annotations

import logging
import hashlib
import bisect
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from predictive_sentiment.awfi import (
    HORIZON_THRESHOLDS,
    HORIZON_WEIGHTS,
    awfi_signal,
    compose_awfi_score,
    purchase_led_action_score,
)
from predictive_sentiment.config import AWFI_VERSION
from predictive_sentiment.research import (
    is_direct_common_stock,
    normalize_cusip,
)


logger = logging.getLogger(__name__)
DEFAULT_AWFI_DATABASE_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "investor_screening"
    / "predictive_sentiment.duckdb"
)


class AwfiService:
    def __init__(
        self,
        database_path: str | Path = DEFAULT_AWFI_DATABASE_PATH,
    ) -> None:
        self.database_path = Path(database_path).resolve()
        self.performance_path = (
            self.database_path.parent / "performance.duckdb"
        )

    @staticmethod
    def _unavailable(
        requested_period: str,
        reason: str,
    ) -> dict:
        return {
            "scores": {},
            "metadata": {
                "state": "UNAVAILABLE",
                "reason": reason,
                "awfi_version": AWFI_VERSION,
                "requested_period": requested_period,
                "run_id": None,
                "latest_period": None,
                "stale": False,
                "trust_status": "NOT_TRUSTWORTHY",
            },
        }

    def get_period_scores(
        self,
        report_period: str,
        *,
        latest_application_period: str | None = None,
    ) -> dict:
        requested_period = str(report_period)
        if not self.database_path.is_file():
            return self._unavailable(
                requested_period,
                "AWFI research database is missing",
            )
        try:
            connection = duckdb.connect(
                str(self.database_path),
                read_only=True,
            )
            try:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = 'main'
                        """
                    ).fetchall()
                }
                if not {"research_runs", "awfi_scores"}.issubset(tables):
                    return self._unavailable(
                        requested_period,
                        "AWFI score schema is unavailable",
                    )
                latest_run = connection.execute(
                    """
                    SELECT
                        r.run_id,
                        r.completed_at,
                        coalesce(r.trust_status, 'NOT_TRUSTWORTHY'),
                        max(a.report_period)
                    FROM research_runs r
                    JOIN awfi_scores a USING (run_id)
                    WHERE r.status = 'COMPLETE'
                      AND a.awfi_version = ?
                    GROUP BY r.run_id, r.completed_at, r.trust_status
                    ORDER BY r.completed_at DESC
                    LIMIT 1
                    """,
                    [AWFI_VERSION],
                ).fetchone()
                if latest_run is None:
                    return self._unavailable(
                        requested_period,
                        "No completed AWFI Research v2 run is available",
                    )
                run = connection.execute(
                    """
                    SELECT
                        r.run_id,
                        r.completed_at,
                        coalesce(r.trust_status, 'NOT_TRUSTWORTHY')
                    FROM research_runs r
                    JOIN awfi_scores a USING (run_id)
                    WHERE r.status = 'COMPLETE'
                      AND a.awfi_version = ?
                      AND a.report_period = ?
                    GROUP BY r.run_id, r.completed_at, r.trust_status
                    ORDER BY r.completed_at DESC
                    LIMIT 1
                    """,
                    [AWFI_VERSION, requested_period],
                ).fetchone()
                if run is None:
                    result = self._unavailable(
                        requested_period,
                        "No AWFI scores exist for the requested filing period",
                    )
                    result["metadata"]["latest_period"] = (
                        latest_run[3].isoformat()
                        if latest_run[3] is not None
                        else None
                    )
                    result["metadata"]["run_id"] = str(latest_run[0])
                    result["metadata"]["completed_at"] = (
                        latest_run[1].isoformat()
                        if latest_run[1]
                        else None
                    )
                    result["metadata"]["stale"] = bool(
                        latest_application_period
                        and result["metadata"]["latest_period"]
                        and result["metadata"]["latest_period"]
                        < latest_application_period
                    )
                    if result["metadata"]["stale"]:
                        result["metadata"]["state"] = "STALE"
                    return result
                run_id, completed_at, trust_status = run
                latest_period = latest_run[3]
                rows = connection.execute(
                    """
                    SELECT
                        coalesce(nullif(trim(ticker), ''), market_symbol),
                        cusip,
                        horizon,
                        score,
                        positive_threshold,
                        negative_threshold,
                        signal,
                        as_of_date,
                        feature_date,
                        source_status
                    FROM awfi_scores
                    WHERE run_id = ?
                      AND awfi_version = ?
                      AND report_period = ?
                    ORDER BY market_symbol, horizon
                    """,
                    [run_id, AWFI_VERSION, requested_period],
                ).fetchall()
            finally:
                connection.close()
        except (duckdb.Error, OSError, ValueError) as exc:
            logger.warning("Could not read AWFI scores: %s", exc)
            return self._unavailable(
                requested_period,
                f"Could not read AWFI scores: {type(exc).__name__}",
            )

        scores: dict[str, dict[str, dict]] = {}
        duplicates: set[tuple[str, str]] = set()
        for (
            raw_ticker,
            cusip,
            horizon,
            score,
            positive_threshold,
            negative_threshold,
            signal,
            as_of_date,
            feature_date,
            source_status,
        ) in rows:
            ticker = self._dashboard_ticker(raw_ticker)
            if not ticker:
                continue
            horizon_key = str(int(horizon))
            candidate = {
                "score": round(float(score), 2),
                "signal": str(signal),
                "research_signal": str(signal),
                "positive_threshold": float(positive_threshold),
                "negative_threshold": float(negative_threshold),
                "as_of_date": as_of_date.isoformat(),
                "feature_date": feature_date.isoformat(),
                "source_status": str(source_status),
                "horizon_sessions": int(horizon),
                "horizon_months": {
                    126: 6,
                    252: 12,
                    378: 18,
                    504: 24,
                }.get(int(horizon)),
                "cusip": str(cusip),
            }
            existing = scores.setdefault(ticker, {}).get(horizon_key)
            if existing is None:
                scores[ticker][horizon_key] = candidate
            elif existing["cusip"] != candidate["cusip"]:
                duplicates.add((ticker, horizon_key))
        for ticker, horizon_key in duplicates:
            scores.get(ticker, {}).pop(horizon_key, None)
            if not scores.get(ticker):
                scores.pop(ticker, None)

        latest_period_value = (
            latest_period.isoformat() if latest_period is not None else None
        )
        stale = bool(
            latest_application_period
            and latest_period_value
            and latest_period_value < latest_application_period
        )
        state = "READY" if scores and not stale else "STALE" if stale else "UNAVAILABLE"
        return {
            "scores": scores,
            "metadata": {
                "state": state,
                "reason": (
                    None
                    if scores
                    else "No AWFI scores exist for the requested filing period"
                ),
                "awfi_version": AWFI_VERSION,
                "requested_period": requested_period,
                "run_id": str(run_id),
                "completed_at": (
                    completed_at.isoformat() if completed_at else None
                ),
                "latest_period": latest_period_value,
                "stale": stale,
                "trust_status": str(trust_status),
                "duplicate_tickers": sorted(
                    {ticker for ticker, _ in duplicates}
                ),
            },
        }

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _load_price_series(
        self,
        connection: duckdb.DuckDBPyConnection,
        symbol: str,
    ) -> pd.Series | None:
        row = connection.execute(
            """
            SELECT status, parquet_path, parquet_sha256
            FROM price_manifest
            WHERE symbol = ?
            """,
            [symbol],
        ).fetchone()
        if row is None or row[0] != "READY" or not row[1]:
            return None
        path = Path(row[1])
        if (
            not path.is_file()
            or self._file_sha256(path) != row[2]
        ):
            return None
        frame = pd.read_parquet(path, columns=["date", "symbol", "close"])
        if set(frame["symbol"].astype(str).unique()) != {symbol}:
            return None
        frame["date"] = pd.to_datetime(frame["date"]).dt.date
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna(subset=["date", "close"])
        if frame.empty or (frame["close"] <= 0).any():
            return None
        frame = frame.drop_duplicates("date", keep="last").sort_values("date")
        return pd.Series(
            frame["close"].to_numpy(dtype=float),
            index=pd.Index(frame["date"], name="date"),
            name=symbol,
        )

    @staticmethod
    def _current_top_tickers(period_cache: dict) -> dict[str, str]:
        top_tickers: dict[str, str] = {}
        for fund in period_cache.values():
            if fund.get("status") != "loaded":
                continue
            holdings = fund.get("holdings")
            if holdings is None or holdings.empty:
                continue
            aggregated: dict[str, dict] = {}
            for _, row in holdings.iterrows():
                cusip = normalize_cusip(row.get("Cusip"))
                ticker = str(row.get("Ticker") or "").strip().upper()
                if (
                    cusip is None
                    or not ticker
                    or not is_direct_common_stock(
                        issuer=row.get("Issuer"),
                        title=row.get("Class"),
                        shares_type=row.get("Type"),
                        put_call=row.get("PutCall"),
                    )
                ):
                    continue
                item = aggregated.setdefault(
                    cusip,
                    {
                        "ticker": ticker,
                        "portfolio_weight": 0.0,
                        "value": 0.0,
                    },
                )
                item["portfolio_weight"] += float(
                    row.get("PortfolioWeight") or 0.0
                )
                item["value"] += float(row.get("Value") or 0.0)
            ranked = sorted(
                aggregated.items(),
                key=lambda item: (
                    -item[1]["portfolio_weight"],
                    -item[1]["value"],
                    item[0],
                ),
            )[:10]
            for cusip, item in ranked:
                top_tickers[item["ticker"]] = cusip
        return top_tickers

    def compute_live_period_scores(
        self,
        report_period: str,
        *,
        period_cache: dict,
        ticker_rows: list[dict],
        sentiment_summaries: dict[str, dict],
    ) -> dict:
        if not self.performance_path.is_file():
            return self._unavailable(
                report_period,
                "AWFI performance price database is missing",
            )
        from predictive_sentiment.pipeline import (
            _centered_rank,
            _point_in_time_market_features,
        )

        top_tickers = self._current_top_tickers(period_cache)
        period_date = date.fromisoformat(report_period)
        filing_available_date = period_date + timedelta(days=45)
        connection = duckdb.connect(
            str(self.performance_path),
            read_only=True,
        )
        try:
            spy = self._load_price_series(connection, "SPY")
            if spy is None:
                return self._unavailable(
                    report_period,
                    "SPY price history is unavailable",
                )
            spy_dates = list(spy.index)
            entry_index = bisect.bisect_right(
                spy_dates,
                filing_available_date,
            )
            if entry_index >= len(spy_dates):
                return self._unavailable(
                    report_period,
                    "No market session exists after the AWFI as-of date",
                )
            earliest_market_date = spy_dates[entry_index]
            latest_market_date = spy_dates[-1]
            mappings = dict(
                connection.execute(
                    """
                    SELECT cusip, market_symbol
                    FROM cusip_ticker_mapping
                    """
                ).fetchall()
            )
            ticker_by_name = {
                str(item.get("ticker", "")).strip().upper(): item
                for item in ticker_rows
            }
            candidates = []
            for ticker, cusip in sorted(top_tickers.items()):
                summary = sentiment_summaries.get(ticker)
                ticker_row = ticker_by_name.get(ticker)
                symbol = mappings.get(cusip)
                if summary is None or ticker_row is None or not symbol:
                    continue
                alpha_score = summary.get("indicative_score")
                if alpha_score is None:
                    continue
                prices = self._load_price_series(connection, symbol)
                if prices is None:
                    continue
                price_dates = list(prices.index)
                feature_index = (
                    bisect.bisect_right(
                        price_dates,
                        latest_market_date,
                    )
                    - 1
                )
                if feature_index < 0:
                    continue
                feature_date = price_dates[feature_index]
                if feature_date < earliest_market_date:
                    continue
                technical = _point_in_time_market_features(
                    prices,
                    feature_date + timedelta(days=1),
                )
                if technical["feature_date"] is None:
                    continue
                holder_weights = [
                    float(holder.get("portfolio_weight") or 0.0)
                    for holder in ticker_row.get("holders", [])
                    if float(holder.get("portfolio_weight") or 0.0) > 0
                ]
                candidates.append(
                    {
                        "ticker": ticker,
                        "alpha_score": float(alpha_score),
                        "action_score": purchase_led_action_score(
                            summary.get("investor_changes", [])
                        ),
                        "median_weight": float(
                            ticker_row.get("median_weight") or 0.0
                        ),
                        "max_weight": max(holder_weights, default=0.0),
                        "holder_count": int(
                            ticker_row.get("num_holders") or 0
                        ),
                        **technical,
                    }
                )
        finally:
            connection.close()
        if not candidates:
            return self._unavailable(
                report_period,
                "No current top-holding tickers had complete AWFI inputs",
            )

        frame = pd.DataFrame(candidates)
        frame["median_weight_rank"] = _centered_rank(frame["median_weight"])
        frame["max_weight_rank"] = _centered_rank(frame["max_weight"])
        frame["holder_count_rank"] = _centered_rank(frame["holder_count"])
        frame["portfolio_score"] = (
            0.50 * frame["median_weight_rank"]
            + 0.30 * frame["max_weight_rank"]
            + 0.20 * frame["holder_count_rank"]
        )
        frame["momentum_12m1m_rank"] = _centered_rank(
            frame["momentum_12m_minus_1m_pct"]
        )
        frame["momentum_6m_rank"] = _centered_rank(
            frame["momentum_6m_pct"]
        )
        frame["high_proximity_rank"] = _centered_rank(
            frame["price_below_52_week_high_pct"]
        )
        trend_score = frame["trend_regime"].map(
            {"BULLISH": 100.0, "NEUTRAL": 0.0, "BEARISH": -100.0}
        ).fillna(0.0)
        frame["technical_score"] = (
            0.35 * frame["momentum_12m1m_rank"].fillna(0.0)
            + 0.25 * frame["momentum_6m_rank"].fillna(0.0)
            + 0.25 * frame["high_proximity_rank"].fillna(0.0)
            + 0.15 * trend_score
        )

        scores: dict[str, dict[str, dict]] = {}
        for _, row in frame.iterrows():
            ticker = row["ticker"]
            scores[ticker] = {}
            for horizon in HORIZON_WEIGHTS:
                score = compose_awfi_score(
                    horizon=horizon,
                    alpha_score=row["alpha_score"],
                    action_score=row["action_score"],
                    portfolio_score=row["portfolio_score"],
                    technical_score=row["technical_score"],
                )
                threshold = HORIZON_THRESHOLDS[horizon]
                weights = HORIZON_WEIGHTS[horizon]
                component_scores = {
                    "institutional": float(row["alpha_score"]),
                    "purchase_actions": float(row["action_score"]),
                    "portfolio_conviction": float(
                        row["portfolio_score"]
                    ),
                    "technical": float(row["technical_score"]),
                }
                scores[ticker][str(horizon)] = {
                    "score": round(score, 2),
                    "signal": awfi_signal(horizon, score),
                    "research_signal": awfi_signal(horizon, score),
                    "positive_threshold": threshold,
                    "negative_threshold": threshold,
                    "as_of_date": row["feature_date"].isoformat(),
                    "feature_date": row["feature_date"].isoformat(),
                    "filing_available_date": (
                        filing_available_date.isoformat()
                    ),
                    "source_status": "LIVE_PERIOD_CACHE",
                    "horizon_sessions": horizon,
                    "horizon_months": horizon // 21,
                    "component_scores": component_scores,
                    "component_weights": {
                        "institutional": weights[0],
                        "purchase_actions": weights[1],
                        "portfolio_conviction": weights[2],
                        "technical": weights[3],
                    },
                    "component_contributions": {
                        key: round(
                            component_scores[key] * weights[index],
                            2,
                        )
                        for index, key in enumerate(component_scores)
                    },
                }
        return {
            "scores": scores,
            "metadata": {
                "state": "LIVE",
                "reason": None,
                "awfi_version": AWFI_VERSION,
                "requested_period": report_period,
                "run_id": None,
                "completed_at": None,
                "latest_period": report_period,
                "filing_available_date": (
                    filing_available_date.isoformat()
                ),
                "market_data_date": max(
                    item["feature_date"]
                    for ticker_scores in scores.values()
                    for item in ticker_scores.values()
                ),
                "stale": False,
                "trust_status": "NOT_TRUSTWORTHY",
                "source": "LIVE_PERIOD_CACHE",
                "applicable_tickers": len(top_tickers),
                "scored_tickers": len(scores),
            },
        }

    def get_ticker_history(self, ticker: str) -> list[dict]:
        dashboard_ticker = self._dashboard_ticker(ticker)
        if not self.database_path.is_file():
            return []
        try:
            connection = duckdb.connect(
                str(self.database_path),
                read_only=True,
            )
            try:
                run = connection.execute(
                    """
                    SELECT r.run_id
                    FROM research_runs r
                    JOIN awfi_scores a USING (run_id)
                    WHERE r.status = 'COMPLETE'
                      AND a.awfi_version = ?
                    GROUP BY r.run_id, r.completed_at
                    ORDER BY r.completed_at DESC
                    LIMIT 1
                    """,
                    [AWFI_VERSION],
                ).fetchone()
                if run is None:
                    return []
                rows = connection.execute(
                    """
                        SELECT
                            report_period,
                            coalesce(
                                nullif(trim(ticker), ''),
                                market_symbol
                            ),
                            cusip,
                            horizon,
                            score,
                            signal,
                            positive_threshold,
                            negative_threshold,
                            as_of_date,
                            feature_date
                        FROM awfi_scores
                        WHERE run_id = ?
                          AND awfi_version = ?
                        ORDER BY report_period, horizon
                    """,
                    [run[0], AWFI_VERSION],
                ).fetchall()
            finally:
                connection.close()
        except (duckdb.Error, OSError, ValueError) as exc:
            logger.warning("Could not read AWFI ticker history: %s", exc)
            return []

        by_period: dict[str, dict[str, dict]] = {}
        identities: dict[tuple[str, str], str] = {}
        duplicates: set[tuple[str, str]] = set()
        for (
            report_period,
            raw_ticker,
            cusip,
            horizon,
            score,
            signal,
            positive_threshold,
            negative_threshold,
            as_of_date,
            feature_date,
        ) in rows:
            if self._dashboard_ticker(raw_ticker) != dashboard_ticker:
                continue
            period = report_period.isoformat()
            horizon_key = str(int(horizon))
            identity_key = (period, horizon_key)
            prior_cusip = identities.get(identity_key)
            if prior_cusip is not None and prior_cusip != str(cusip):
                duplicates.add(identity_key)
                continue
            identities[identity_key] = str(cusip)
            by_period.setdefault(period, {})[horizon_key] = {
                "score": round(float(score), 2),
                "signal": str(signal),
                "research_signal": str(signal),
                "positive_threshold": float(positive_threshold),
                "negative_threshold": float(negative_threshold),
                "as_of_date": as_of_date.isoformat(),
                "feature_date": feature_date.isoformat(),
            }
        for period, horizon_key in duplicates:
            by_period.get(period, {}).pop(horizon_key, None)
        return [
            {"period": period, "scores": scores}
            for period, scores in sorted(by_period.items())
            if scores
        ][-20:]

    @staticmethod
    def _dashboard_ticker(value: object) -> str:
        ticker = str(value or "").strip().upper()
        return {
            "BRK-A": "BRKA",
            "BRK-B": "BRKB",
            "HEI-A": "HEIA",
            "LEN-B": "LENB",
        }.get(ticker, ticker)
