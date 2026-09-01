import asyncio
import hashlib
import json
import logging
import math
import os
import re
import statistics
import tempfile
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse
import pandas as pd
from edgar import set_identity, Company
from config import FUND_MANAGERS, SEC_IDENTITY, CACHE_DIR, CACHE_TTL_HOURS
from roster_store import fund_fingerprint

logger = logging.getLogger(__name__)

class DataService:
    def __init__(self):
        self.cache = {}
        self.last_updated = None
        self.subscribers = []
        self.is_refreshing = False
        self.market_insights = {}
        self.market_last_updated = None
        self.is_market_refreshing = False
        self.ticker_market_cache = {}
        self.pair_service = None
        self.period_caches = {}
        self.period_cache_locks = {}
        self.period_cache_progress = {}
        self.manager_adjustment_cache = {}
        self.pending_roster_refresh_ciks = set()
        self._refresh_lock = asyncio.Lock()
        self._full_refresh_pending = False

        # Ensure identity is set for SEC EDGAR access
        try:
            set_identity(SEC_IDENTITY)
        except Exception as e:
            logger.warning(f"Could not set SEC identity: {e}")

        # Ensure cache directory exists
        os.makedirs(CACHE_DIR, exist_ok=True)

        # Initialize in-memory cache structure
        for fund in FUND_MANAGERS:
            self.cache[fund["cik"]] = self._new_fund_cache(fund)

        # Try loading from local disk cache on startup for instant availability
        self._load_all_from_disk_cache()
        self._load_market_insights_from_disk()

    @staticmethod
    def _new_fund_cache(fund):
        return {
            "fund_info": fund,
            "status": "loading",
            "metadata": {},
            "holdings": None,
            "comparison": None,
            "previous_comparison": None,
            "last_updated": None,
        }

    @staticmethod
    def _roster_fingerprint(funds=None):
        payload = [
            {
                "cik": fund["cik"],
                "historical_ciks": fund.get("historical_ciks", []),
            }
            for fund in (funds or FUND_MANAGERS)
        ]
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _fund_fingerprint(fund):
        return fund_fingerprint(fund)

    def _get_disk_cache_path(self, cik: str) -> str:
        return os.path.join(CACHE_DIR, f"{cik}.json")

    @staticmethod
    def _detect_13f_value_scale(values, shares):
        valid = pd.DataFrame({
            "value": pd.to_numeric(values, errors="coerce"),
            "shares": pd.to_numeric(shares, errors="coerce")
        }).dropna()
        valid = valid[
            (valid["value"] > 0)
            & (valid["shares"] > 0)
        ]
        if valid.empty:
            return 1.0
        median_implied_price = float(
            (valid["value"] / valid["shares"]).median()
        )
        if median_implied_price < 1.0:
            return 1000.0
        if median_implied_price > 10000.0:
            return 0.001
        return 1.0

    @classmethod
    def _normalize_holdings_values(cls, holdings):
        if (
            holdings is None
            or holdings.empty
            or "Value" not in holdings.columns
            or "SharesPrnAmount" not in holdings.columns
        ):
            return holdings, 1.0
        normalized = holdings.copy()
        scale = cls._detect_13f_value_scale(
            normalized["Value"],
            normalized["SharesPrnAmount"]
        )
        normalized["Value"] = (
            pd.to_numeric(normalized["Value"], errors="coerce")
            .fillna(0.0)
            * scale
        )
        total_value = float(normalized["Value"].sum())
        normalized["PortfolioWeight"] = (
            normalized["Value"] / total_value * 100.0
            if total_value > 0
            else 0.0
        )
        return normalized, scale

    @classmethod
    def _normalize_comparison_values(cls, comparison):
        if comparison is None or comparison.empty:
            return comparison
        normalized = comparison.copy()
        if {"Value", "Shares"}.issubset(normalized.columns):
            current_scale = cls._detect_13f_value_scale(
                normalized["Value"],
                normalized["Shares"]
            )
            normalized["Value"] = (
                pd.to_numeric(normalized["Value"], errors="coerce")
                .fillna(0.0)
                * current_scale
            )
        if {"PrevValue", "PrevShares"}.issubset(normalized.columns):
            previous_scale = cls._detect_13f_value_scale(
                normalized["PrevValue"],
                normalized["PrevShares"]
            )
            normalized["PrevValue"] = (
                pd.to_numeric(normalized["PrevValue"], errors="coerce")
                .fillna(0.0)
                * previous_scale
            )
        if {"Value", "PrevValue"}.issubset(normalized.columns):
            normalized["ValueChange"] = (
                normalized["Value"] - normalized["PrevValue"]
            )
            normalized["ValueChangePct"] = normalized.apply(
                lambda row: (
                    row["ValueChange"] / row["PrevValue"] * 100.0
                    if row["PrevValue"] > 0
                    else None
                ),
                axis=1
            )
        return normalized

    def _save_fund_to_disk_cache(self, cik: str):
        try:
            fund_data = self.cache.get(cik)
            if not fund_data or fund_data.get("status") != "loaded":
                return

            payload = {
                "cik": cik,
                "fund_fingerprint": self._fund_fingerprint(
                    fund_data["fund_info"]
                ),
                "status": fund_data["status"],
                "metadata": fund_data["metadata"],
                "last_updated": fund_data["last_updated"],
                "holdings": fund_data["holdings"].to_dict(orient="records") if fund_data["holdings"] is not None else [],
                "comparison": fund_data["comparison"].to_dict(orient="records") if fund_data["comparison"] is not None else [],
                "previous_comparison": fund_data["previous_comparison"].to_dict(orient="records") if fund_data["previous_comparison"] is not None else []
            }
            cache_path = self._get_disk_cache_path(cik)
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=os.path.dirname(cache_path),
                    prefix=f".{cik}.",
                    suffix=".json.tmp",
                    delete=False,
                ) as stream:
                    temp_path = stream.name
                    json.dump(payload, stream, default=str)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, cache_path)
                temp_path = None
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)
        except Exception as e:
            logger.error(f"Error saving disk cache for {cik}: {e}")

    def _load_all_from_disk_cache(self):
        loaded_count = 0
        latest_time = None
        for fund in FUND_MANAGERS:
            cik = fund["cik"]
            path = self._get_disk_cache_path(cik)
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if (
                        data.get("cik") != cik
                        or data.get("fund_fingerprint") != self._fund_fingerprint(
                        fund
                        )
                    ):
                        logger.info(
                            "Ignoring incompatible fund cache for %s",
                            cik,
                        )
                        continue

                    holdings_df = pd.DataFrame(data.get("holdings", [])) if data.get("holdings") else None
                    comparison_df = pd.DataFrame(data.get("comparison", [])) if data.get("comparison") else None
                    previous_comparison_df = pd.DataFrame(data.get("previous_comparison", [])) if data.get("previous_comparison") else None
                    holdings_df, _ = self._normalize_holdings_values(
                        holdings_df
                    )
                    comparison_df = self._normalize_comparison_values(
                        comparison_df
                    )
                    previous_comparison_df = self._normalize_comparison_values(
                        previous_comparison_df
                    )

                    self.cache[cik]["status"] = data.get("status", "loaded")
                    metadata = data.get("metadata", {})
                    if holdings_df is not None and not holdings_df.empty:
                        metadata["total_value"] = float(
                            holdings_df["Value"].sum()
                        )
                    self.cache[cik]["metadata"] = metadata
                    self.cache[cik]["last_updated"] = data.get("last_updated")
                    self.cache[cik]["holdings"] = holdings_df
                    self.cache[cik]["comparison"] = comparison_df
                    self.cache[cik]["previous_comparison"] = previous_comparison_df

                    if data.get("last_updated"):
                        if latest_time is None or data["last_updated"] > latest_time:
                            latest_time = data["last_updated"]
                    loaded_count += 1
                except Exception as e:
                    logger.warning(f"Failed to load disk cache for {cik}: {e}")

        if loaded_count > 0:
            self.last_updated = latest_time or datetime.now(timezone.utc).isoformat()
            logger.info(f"Loaded {loaded_count} funds from local disk cache.")

    def _get_market_cache_path(self) -> str:
        return os.path.join(CACHE_DIR, "market_insights.json")

    def _load_market_insights_from_disk(self):
        path = self._get_market_cache_path()
        if not os.path.exists(path):
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.market_insights = {
                item["ticker"]: item
                for item in payload.get("data", [])
                if item.get("ticker")
            }
            self.market_last_updated = payload.get("last_updated")
        except Exception as e:
            logger.warning(f"Failed to load OpenBB market cache: {e}")

    def _save_market_insights_to_disk(self):
        payload = {
            "last_updated": self.market_last_updated,
            "data": list(self.market_insights.values())
        }
        try:
            with open(self._get_market_cache_path(), "w", encoding="utf-8") as f:
                json.dump(payload, f, default=str)
        except Exception as e:
            logger.error(f"Error saving OpenBB market cache: {e}")

    def _get_ticker_market_cache_path(self, ticker: str):
        market_dir = os.path.join(CACHE_DIR, "ticker_market")
        os.makedirs(market_dir, exist_ok=True)
        safe_ticker = "".join(char for char in ticker.upper() if char.isalnum() or char in {"-", "."})
        return os.path.join(market_dir, f"{safe_ticker}.json")

    def _load_ticker_market_data_from_disk(self, ticker: str):
        path = self._get_ticker_market_cache_path(ticker)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if payload.get("cache_version") != 5:
                return None
            quote = payload.get("quote", {})
            if quote.get("year_low") is not None and not quote.get("year_low_date"):
                return None
            if payload.get("news_filter_version") != 1:
                return None
            updated_at = datetime.fromisoformat(payload["last_updated"])
            if datetime.now(timezone.utc) - updated_at > timedelta(hours=CACHE_TTL_HOURS):
                return None
            return payload
        except Exception as e:
            logger.warning(f"Failed to load ticker market cache for {ticker}: {e}")
            return None

    def _save_ticker_market_data_to_disk(self, ticker: str, payload):
        try:
            with open(self._get_ticker_market_cache_path(ticker), "w", encoding="utf-8") as f:
                json.dump(payload, f, default=str)
        except Exception as e:
            logger.error(f"Failed to save ticker market cache for {ticker}: {e}")

    @staticmethod
    def _clean_market_record(record):
        cleaned = {}
        for key, value in record.items():
            if value is None or (not isinstance(value, (str, bool)) and pd.isna(value)):
                cleaned[key] = None
            elif hasattr(value, "item"):
                cleaned[key] = value.item()
            else:
                cleaned[key] = value
        return cleaned

    @staticmethod
    def _serialize_price_history(prices):
        if (
            prices is None
            or prices.empty
            or not {"date", "close"}.issubset(prices.columns)
        ):
            return []
        history = prices[["date", "close"]].copy()
        history["date"] = pd.to_datetime(history["date"], errors="coerce")
        history["close"] = pd.to_numeric(history["close"], errors="coerce")
        history = (
            history.dropna(subset=["date", "close"])
            .drop_duplicates(subset=["date"], keep="last")
            .sort_values("date")
        )
        return [
            {
                "date": row["date"].date().isoformat(),
                "close": round(float(row["close"]), 2)
            }
            for _, row in history.iterrows()
        ]

    @staticmethod
    def _serialize_company_news(
        results,
        ticker,
        company_name,
        limit=5,
    ):
        ticker_token = str(ticker or "").strip().casefold()
        company_tokens = re.findall(
            r"[a-z0-9]+",
            str(company_name or "").casefold(),
        )
        legal_suffixes = {
            "ag", "co", "corp", "corporation", "inc", "incorporated",
            "limited", "llc", "lp", "ltd", "nv", "plc", "sa",
        }
        while company_tokens and company_tokens[-1] in legal_suffixes:
            company_tokens.pop()

        company_aliases = set()
        if company_tokens:
            company_aliases.add(" ".join(company_tokens))
            first_token = company_tokens[0]
            if (
                len(first_token) >= 4
                and first_token not in {
                    "american", "global", "international", "taiwan", "the",
                    "united",
                }
            ):
                company_aliases.add(first_token)
            acronym = "".join(
                token[0]
                for token in company_tokens
                if token not in {"the"}
            )
            if len(acronym) >= 2:
                company_aliases.add(acronym)

        impact_terms = {
            "acquisition", "acquire", "antitrust", "appoint", "approval",
            "ban", "buyback", "capex", "ceo", "cfo", "chip", "contract",
            "customer", "deal", "demand", "dividend", "downgrade", "earnings",
            "executive", "export", "factory", "forecast", "guidance",
            "investigation", "investment", "launch", "lawsuit", "layoff",
            "leadership", "manufacturing", "margin", "merger", "order",
            "outlook", "partnership", "plant", "pricing", "production",
            "product", "profit", "rating", "recall", "regulator", "repurchase",
            "resign", "results", "revenue", "sales", "sanction",
            "security breach", "shipment", "sued", "sues", "supply", "tariff",
            "unveil", "upgrade",
        }
        articles = []
        seen = set()
        for item in results or []:
            raw = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            title = " ".join(str(raw.get("title") or "").split())
            normalized_title = " ".join(re.findall(
                r"[a-z0-9]+",
                title.casefold(),
            ))
            summary = " ".join(str(
                raw.get("summary")
                or raw.get("excerpt")
                or raw.get("text")
                or ""
            ).split())
            normalized_context = " ".join(re.findall(
                r"[a-z0-9]+",
                f"{title} {summary}".casefold(),
            ))
            ticker_match = (
                len(ticker_token) >= 2
                and re.search(
                    rf"\b{re.escape(ticker_token)}\b",
                    normalized_title,
                )
            )
            company_match = any(
                re.search(
                    rf"\b{re.escape(alias)}\b",
                    normalized_title,
                )
                for alias in company_aliases
            )
            has_impact_signal = any(
                re.search(
                    rf"\b{re.escape(term)}\w*\b",
                    normalized_context,
                )
                for term in impact_terms
            )
            if not (ticker_match or company_match) or not has_impact_signal:
                continue

            url = str(raw.get("url") or "").strip()
            parsed_url = urlparse(url)
            if (
                not title
                or parsed_url.scheme not in {"http", "https"}
                or not parsed_url.netloc
            ):
                continue
            key = (title.casefold(), url)
            if key in seen:
                continue
            seen.add(key)

            published = pd.to_datetime(
                raw.get("date"), errors="coerce", utc=True
            )
            if len(summary) > 320:
                summary = f"{summary[:317].rstrip()}..."
            articles.append({
                "published_at": (
                    published.isoformat().replace("+00:00", "Z")
                    if pd.notna(published)
                    else None
                ),
                "title": title,
                "source": " ".join(
                    str(raw.get("source") or "News source").split()
                ),
                "url": url,
                "summary": summary,
            })

        articles.sort(
            key=lambda article: article["published_at"] or "",
            reverse=True,
        )
        return articles[:limit]

    @staticmethod
    def _rsi(close: pd.Series, period: int):
        delta = close.diff()
        gains = delta.clip(lower=0)
        losses = -delta.clip(upper=0)
        average_gain = gains.ewm(
            alpha=1 / period,
            min_periods=period,
            adjust=False
        ).mean()
        average_loss = losses.ewm(
            alpha=1 / period,
            min_periods=period,
            adjust=False
        ).mean()
        relative_strength = average_gain / average_loss.replace(0, pd.NA)
        return 100 - (100 / (1 + relative_strength))

    def _compute_technical_analysis(self, prices):
        close = pd.to_numeric(prices["close"], errors="coerce").dropna()
        if len(close) < 200:
            return {}

        current_price = float(close.iloc[-1])
        sma_50 = float(close.rolling(50).mean().iloc[-1])
        sma_200 = float(close.rolling(200).mean().iloc[-1])
        rsi_14_series = self._rsi(close, 14).dropna()
        rsi_2_series = self._rsi(close, 2).dropna()
        returns = close.pct_change().dropna()

        def period_return(days):
            return (
                (current_price / float(close.iloc[-days])) - 1
                if len(close) >= days
                else None
            )

        momentum_1m = period_return(21)
        momentum_3m = period_return(63)
        momentum_6m = period_return(126)
        momentum_12m = period_return(252)
        volatility_63d = (
            float(returns.tail(63).std() * math.sqrt(252))
            if len(returns) >= 20
            else None
        )
        distance_from_high = (
            (current_price / float(close.tail(252).max())) - 1
            if len(close) >= 20
            else None
        )
        rsi_14 = float(rsi_14_series.iloc[-1]) if not rsi_14_series.empty else None
        rsi_2 = float(rsi_2_series.iloc[-1]) if not rsi_2_series.empty else None

        trend_votes = [
            current_price > sma_200,
            sma_50 > sma_200,
            momentum_6m is not None and momentum_6m > 0
        ]
        bullish_votes = sum(trend_votes)
        trend_regime = (
            "BULLISH"
            if bullish_votes == 3
            else "NEUTRAL"
            if bullish_votes == 2
            else "BEARISH"
        )
        if rsi_14 is None:
            momentum_state = "UNAVAILABLE"
        elif rsi_14 >= 70:
            momentum_state = "OVERBOUGHT"
        elif rsi_14 <= 30:
            momentum_state = "OVERSOLD"
        elif momentum_6m is not None and momentum_6m > 0:
            momentum_state = "POSITIVE"
        else:
            momentum_state = "WEAK"

        if current_price < sma_200:
            entry_timing = "WAIT FOR TREND"
        elif rsi_14 is not None and rsi_14 >= 70:
            entry_timing = "EXTENDED"
        elif rsi_2 is not None and rsi_2 < 10:
            entry_timing = "FAVORABLE DIP"
        elif current_price > sma_50 * 1.12:
            entry_timing = "EXTENDED"
        elif momentum_3m is not None and momentum_3m > 0:
            entry_timing = "TREND SUPPORTIVE"
        else:
            entry_timing = "NEUTRAL"

        return {
            "sma_50": round(sma_50, 2),
            "sma_200": round(sma_200, 2),
            "distance_from_sma_50_pct": round(
                ((current_price / sma_50) - 1) * 100,
                2
            ),
            "distance_from_sma_200_pct": round(
                ((current_price / sma_200) - 1) * 100,
                2
            ),
            "rsi_14": round(rsi_14, 2) if rsi_14 is not None else None,
            "rsi_2": round(rsi_2, 2) if rsi_2 is not None else None,
            "momentum_1m_pct": round(momentum_1m * 100, 2) if momentum_1m is not None else None,
            "momentum_3m_pct": round(momentum_3m * 100, 2) if momentum_3m is not None else None,
            "momentum_6m_pct": round(momentum_6m * 100, 2) if momentum_6m is not None else None,
            "momentum_12m_pct": round(momentum_12m * 100, 2) if momentum_12m is not None else None,
            "annualized_volatility_pct": round(volatility_63d * 100, 2) if volatility_63d is not None else None,
            "drawdown_from_52w_high_pct": round(distance_from_high * 100, 2) if distance_from_high is not None else None,
            "trend_regime": trend_regime,
            "momentum_state": momentum_state,
            "entry_timing": entry_timing
        }

    @staticmethod
    def _compute_valuation_analysis(
        current_price,
        metrics,
        annual_eps,
        average_pe_5y,
        average_aaa_yield,
        current_aaa_yield,
        pe_observation_count
    ):
        pe_ratio = metrics.get("pe_ratio")
        forward_pe = metrics.get("forward_pe")
        trailing_eps = (
            current_price / pe_ratio
            if current_price and pe_ratio and pe_ratio > 0
            else None
        )
        forward_eps = (
            current_price / forward_pe
            if current_price and forward_pe and forward_pe > 0
            else None
        )
        book_value = metrics.get("book_value")
        growth_rate = None
        if (
            len(annual_eps) >= 2
            and annual_eps[0]["eps"] > 0
            and annual_eps[-1]["eps"] > 0
            and all(item["eps"] > 0 for item in annual_eps)
        ):
            year_span = annual_eps[0]["year"] - annual_eps[-1]["year"]
            if year_span > 0:
                growth_rate = (
                    (annual_eps[0]["eps"] / annual_eps[-1]["eps"])
                    ** (1 / year_span)
                    - 1
                ) * 100

        graham_growth = None
        graham_conservative = None
        graham_number = None
        normalized_pe_value = None
        if trailing_eps is not None and trailing_eps > 0:
            capped_growth = min(max(growth_rate or 0.0, 0.0), 15.0)
            bond_ratio = (
                average_aaa_yield / current_aaa_yield
                if current_aaa_yield and current_aaa_yield > 0
                else 1.0
            )
            graham_growth = trailing_eps * (8.5 + 2 * capped_growth) * bond_ratio
            graham_conservative = trailing_eps * (7.0 + capped_growth) * bond_ratio
            if book_value and book_value > 0:
                graham_number = math.sqrt(22.5 * trailing_eps * book_value)
            if average_pe_5y and average_pe_5y > 0:
                normalized_pe_value = trailing_eps * average_pe_5y

        model_values = [
            value
            for value in (
                graham_number,
                graham_conservative,
                normalized_pe_value
            )
            if value is not None and value > 0
        ]
        fair_value = (
            statistics.median(model_values)
            if len(model_values) >= 2
            else None
        )
        purchase_price = fair_value * 0.80 if fair_value is not None else None
        if current_price is None or fair_value is None or purchase_price is None:
            assessment = "UNAVAILABLE"
        elif current_price <= purchase_price:
            assessment = "UNDERVALUED"
        elif current_price <= fair_value * 1.10:
            assessment = "NEUTRAL"
        else:
            assessment = "OVERVALUED"

        return {
            "trailing_eps": round(trailing_eps, 2) if trailing_eps is not None else None,
            "forward_eps": round(forward_eps, 2) if forward_eps is not None else None,
            "eps_growth_cagr_pct": round(growth_rate, 2) if growth_rate is not None else None,
            "growth_cap_pct": 15.0,
            "average_pe_5y": round(average_pe_5y, 2) if average_pe_5y is not None else None,
            "average_pe_observations": pe_observation_count,
            "average_aaa_yield": round(average_aaa_yield, 2),
            "current_aaa_yield": round(current_aaa_yield, 2),
            "models_used": len(model_values),
            "graham_growth_value": round(graham_growth, 2) if graham_growth is not None else None,
            "graham_conservative_value": round(graham_conservative, 2) if graham_conservative is not None else None,
            "graham_number": round(graham_number, 2) if graham_number is not None else None,
            "normalized_pe_value": round(normalized_pe_value, 2) if normalized_pe_value is not None else None,
            "fair_value": round(fair_value, 2) if fair_value is not None else None,
            "purchase_price_20pct_mos": round(purchase_price, 2) if purchase_price is not None else None,
            "assessment": assessment,
            "price_to_fair_value_pct": (
                round(((current_price / fair_value) - 1) * 100, 2)
                if current_price is not None and fair_value
                else None
            )
        }

    def _fetch_ticker_market_sync(self, ticker: str):
        from openbb import obb

        symbol_aliases = {
            "BRKA": "BRK-A",
            "BRKB": "BRK-B",
            "HEIA": "HEI-A"
        }
        market_symbol = symbol_aliases.get(ticker, ticker)
        periods = self.get_available_periods(count=20)
        start_date = (
            date.fromisoformat(periods[-1]) - timedelta(days=400)
            if periods
            else date.today() - timedelta(days=365 * 6)
        )

        historical = obb.equity.price.historical(
            symbol=market_symbol,
            start_date=start_date,
            end_date=date.today(),
            provider="yfinance",
            interval="1d"
        ).to_df()
        if historical is None or historical.empty:
            raise RuntimeError(f"OpenBB returned no price history for {ticker}")

        prices = historical.reset_index()
        prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
        prices = prices.dropna(subset=["date"]).sort_values("date")

        errors = {}
        quote = {}
        metrics = {}
        profile = {}
        news = []
        income_statement = None
        for name, fetcher in (
            ("quote", lambda: obb.equity.price.quote(
                symbol=market_symbol, provider="yfinance"
            ).to_df()),
            ("metrics", lambda: obb.equity.fundamental.metrics(
                symbol=market_symbol, provider="yfinance"
            ).to_df()),
            ("profile", lambda: obb.equity.profile(
                symbol=market_symbol, provider="yfinance"
            ).to_df())
        ):
            try:
                frame = fetcher()
                record = (
                    self._clean_market_record(frame.iloc[0].to_dict())
                    if frame is not None and not frame.empty
                    else {}
                )
                if name == "quote":
                    quote = record
                elif name == "metrics":
                    metrics = record
                else:
                    profile = record
            except Exception as e:
                errors[name] = str(e)
                logger.warning(f"OpenBB {name} fetch failed for {ticker}: {e}")

        try:
            income_statement = obb.equity.fundamental.income(
                symbol=market_symbol,
                provider="yfinance",
                period="annual",
                limit=5
            ).to_df()
        except Exception as e:
            errors["income"] = str(e)
            logger.warning(f"OpenBB income statement fetch failed for {ticker}: {e}")

        try:
            news_result = obb.news.company(
                symbol=market_symbol,
                limit=50,
                provider="yfinance",
            )
            news = self._serialize_company_news(
                news_result.results,
                ticker=ticker,
                company_name=profile.get("name"),
                limit=5,
            )
        except Exception as e:
            errors["news"] = str(e)
            logger.warning(f"OpenBB company news failed for {ticker}: {e}")

        quarter_end_prices = {}
        quarter_average_prices = {}
        for period in periods:
            quarter = pd.Period(period, freq="Q")
            quarter_rows = prices[
                (prices["date"].dt.date >= quarter.start_time.date())
                & (prices["date"].dt.date <= quarter.end_time.date())
            ]
            quarter_closes = pd.to_numeric(
                quarter_rows["close"], errors="coerce"
            ).dropna()
            if not quarter_closes.empty:
                quarter_end_prices[period] = round(float(quarter_closes.iloc[-1]), 2)
                quarter_average_prices[period] = round(float(quarter_closes.mean()), 2)

        recent_rows = prices[
            prices["date"].dt.date >= date.today() - timedelta(days=370)
        ]
        recent_lows = pd.to_numeric(recent_rows["low"], errors="coerce").dropna()
        recent_highs = pd.to_numeric(recent_rows["high"], errors="coerce").dropna()
        recent_low_rows = recent_rows[["date", "low"]].copy()
        recent_low_rows["low"] = pd.to_numeric(
            recent_low_rows["low"], errors="coerce"
        )
        recent_low_rows = recent_low_rows.dropna(subset=["date", "low"])
        year_low = None
        year_low_date = None
        if not recent_low_rows.empty:
            low_row = recent_low_rows.loc[recent_low_rows["low"].idxmin()]
            year_low = round(float(low_row["low"]), 2)
            year_low_date = low_row["date"].date().isoformat()
        closes = pd.to_numeric(prices["close"], errors="coerce").dropna()
        current_price = quote.get("last_price") or (
            float(closes.iloc[-1]) if not closes.empty else None
        )
        previous_close = quote.get("prev_close")
        day_change = (
            current_price - previous_close
            if current_price is not None and previous_close
            else None
        )
        day_change_pct = (
            (day_change / previous_close) * 100
            if day_change is not None and previous_close
            else None
        )
        exchange_map = {
            "NMS": "NASDAQ",
            "NGM": "NASDAQ",
            "NCM": "NASDAQ",
            "NYQ": "NYSE",
            "ASE": "AMEX"
        }
        exchange = quote.get("exchange") or profile.get("stock_exchange")
        tradingview_exchange = exchange_map.get(exchange, exchange)
        tradingview_symbol_aliases = {
            "BRKA": "BRK.A",
            "BRKB": "BRK.B",
            "HEIA": "HEI.A"
        }
        tradingview_market_symbol = tradingview_symbol_aliases.get(
            ticker,
            market_symbol
        )
        annual_eps = []
        if (
            income_statement is not None
            and not income_statement.empty
            and {"period_ending", "diluted_earnings_per_share"}.issubset(
                income_statement.columns
            )
        ):
            for _, row in income_statement.iterrows():
                eps = row.get("diluted_earnings_per_share")
                period_ending = pd.to_datetime(
                    row.get("period_ending"),
                    errors="coerce"
                )
                if pd.notna(eps) and pd.notna(period_ending):
                    annual_eps.append({
                        "year": int(period_ending.year),
                        "period_ending": period_ending.date().isoformat(),
                        "eps": float(eps)
                    })
            annual_eps.sort(key=lambda item: item["year"], reverse=True)

        pe_observations = []
        for item in annual_eps:
            period_end = pd.Timestamp(item["period_ending"])
            year_prices = prices[
                (prices["date"] > period_end - pd.Timedelta(days=365))
                & (prices["date"] <= period_end)
            ]
            year_closes = pd.to_numeric(
                year_prices["close"], errors="coerce"
            ).dropna()
            if len(year_closes) >= 200 and item["eps"] > 0:
                annual_pe = float(year_closes.mean()) / item["eps"]
                if 0 < annual_pe <= 100:
                    pe_observations.append(annual_pe)
        average_pe_5y = (
            statistics.median(pe_observations[:5])
            if len(pe_observations) >= 3
            else None
        )

        average_aaa_yield = 4.34
        current_aaa_yield = 5.44
        try:
            aaa = pd.read_csv(
                "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DAAA"
            )
            aaa["observation_date"] = pd.to_datetime(
                aaa["observation_date"],
                errors="coerce"
            )
            aaa["DAAA"] = pd.to_numeric(aaa["DAAA"], errors="coerce")
            aaa = aaa.dropna(subset=["observation_date", "DAAA"])
            if not aaa.empty:
                current_aaa_yield = float(aaa.iloc[-1]["DAAA"])
                cutoff = aaa.iloc[-1]["observation_date"] - pd.DateOffset(years=20)
                recent_aaa = aaa[aaa["observation_date"] >= cutoff]["DAAA"]
                if not recent_aaa.empty:
                    average_aaa_yield = float(recent_aaa.mean())
        except Exception as e:
            errors["aaa_yield"] = str(e)
            logger.warning(f"FRED AAA yield fetch failed for {ticker}: {e}")

        valuation = self._compute_valuation_analysis(
            current_price,
            metrics,
            annual_eps,
            average_pe_5y,
            average_aaa_yield,
            current_aaa_yield,
            len(pe_observations[:5])
        )
        technical = self._compute_technical_analysis(prices)
        price_history = self._serialize_price_history(prices)
        price_as_of = price_history[-1]["date"] if price_history else None

        payload = {
            "cache_version": 5,
            "ticker": ticker,
            "market_symbol": market_symbol,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "price_as_of": price_as_of,
            "price_history": price_history,
            "news_filter_version": 1,
            "news": news,
            "quote": {
                **quote,
                "last_price": round(float(current_price), 2) if current_price is not None else None,
                "day_change": round(float(day_change), 2) if day_change is not None else None,
                "day_change_pct": round(float(day_change_pct), 2) if day_change_pct is not None else None,
                "year_low": year_low or quote.get("year_low"),
                "year_low_date": year_low_date,
                "year_high": quote.get("year_high") or (
                    round(float(recent_highs.max()), 2) if not recent_highs.empty else None
                )
            },
            "metrics": metrics,
            "profile": profile,
            "valuation": valuation,
            "technical": technical,
            "annual_eps": annual_eps,
            "quarter_end_prices": quarter_end_prices,
            "quarter_average_prices": quarter_average_prices,
            "tradingview_symbol": (
                f"{tradingview_exchange}:{tradingview_market_symbol}"
                if tradingview_exchange
                else tradingview_market_symbol
            ),
            "errors": errors
        }
        self._save_ticker_market_data_to_disk(ticker, payload)
        return payload

    async def get_ticker_market_data(self, ticker: str):
        normalized = ticker.strip().upper()
        cached = self.ticker_market_cache.get(normalized)
        if cached is not None:
            return cached
        disk_cached = self._load_ticker_market_data_from_disk(normalized)
        if disk_cached is not None:
            self.ticker_market_cache[normalized] = disk_cached
            return disk_cached

        loop = asyncio.get_event_loop()
        payload = await loop.run_in_executor(
            None, self._fetch_ticker_market_sync, normalized
        )
        self.ticker_market_cache[normalized] = payload
        return payload

    async def get_pair_signal(self, ticker: str):
        if self.pair_service is None:
            from pair_service import PairSignalService
            self.pair_service = PairSignalService()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.pair_service.analyze,
            ticker.strip().upper()
        )

    def get_available_periods(self, count=20):
        report_periods = [
            fund_data.get("metadata", {}).get("report_period")
            for fund_data in self.cache.values()
            if fund_data.get("status") == "loaded"
            and fund_data.get("metadata", {}).get("report_period")
        ]
        if not report_periods:
            return []

        latest_quarter = pd.Period(max(report_periods), freq="Q")
        return [
            (latest_quarter - offset).end_time.date().isoformat()
            for offset in range(count)
        ]

    def _get_period_cache_path(self, report_period: str):
        history_dir = os.path.join(CACHE_DIR, "history")
        os.makedirs(history_dir, exist_ok=True)
        return os.path.join(history_dir, f"{report_period}.json")

    def _load_period_cache_from_disk(self, report_period: str):
        path = self._get_period_cache_path(report_period)
        if not os.path.exists(path):
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if payload.get("roster_fingerprint") != self._roster_fingerprint():
                return None

            period_cache = {}
            fund_payloads = payload.get("funds", {})
            for fund in FUND_MANAGERS:
                data = fund_payloads.get(fund["cik"], {})
                holdings = (
                    pd.DataFrame(data.get("holdings", []))
                    if data.get("holdings")
                    else None
                )
                comparison = (
                    pd.DataFrame(data.get("comparison", []))
                    if data.get("comparison")
                    else None
                )
                holdings, _ = self._normalize_holdings_values(holdings)
                comparison = self._normalize_comparison_values(comparison)
                metadata = data.get("metadata", {})
                if holdings is not None and not holdings.empty:
                    metadata["total_value"] = float(holdings["Value"].sum())
                period_cache[fund["cik"]] = {
                    "fund_info": fund,
                    "status": data.get("status", "unavailable"),
                    "metadata": metadata,
                    "holdings": holdings,
                    "comparison": comparison,
                    "previous_comparison": None,
                    "last_updated": data.get("last_updated"),
                    "error": data.get("error")
                }
            return period_cache
        except Exception as e:
            logger.warning(f"Failed to load historical cache for {report_period}: {e}")
            return None

    def _save_period_cache_to_disk(
        self,
        report_period: str,
        period_cache,
        roster_fingerprint=None,
    ):
        payload = {
            "report_period": report_period,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "roster_fingerprint": (
                roster_fingerprint
                or self._roster_fingerprint()
            ),
            "funds": {}
        }
        for cik, fund_data in period_cache.items():
            payload["funds"][cik] = {
                "status": fund_data.get("status"),
                "metadata": fund_data.get("metadata", {}),
                "holdings": (
                    fund_data["holdings"].to_dict(orient="records")
                    if fund_data.get("holdings") is not None
                    else []
                ),
                "comparison": (
                    fund_data["comparison"].to_dict(orient="records")
                    if fund_data.get("comparison") is not None
                    else []
                ),
                "last_updated": fund_data.get("last_updated"),
                "error": fund_data.get("error")
            }

        try:
            cache_path = self._get_period_cache_path(report_period)
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=os.path.dirname(cache_path),
                    prefix=f".{report_period}.",
                    suffix=".json.tmp",
                    delete=False,
                ) as stream:
                    temporary_path = stream.name
                    json.dump(payload, stream, default=str)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_path, cache_path)
                temporary_path = None
            finally:
                if temporary_path and os.path.exists(temporary_path):
                    os.unlink(temporary_path)
        except Exception as e:
            logger.error(f"Failed to save historical cache for {report_period}: {e}")

    async def get_period_cache(self, report_period: str):
        available_periods = self.get_available_periods()
        if report_period not in available_periods:
            raise ValueError(f"Unsupported filing period: {report_period}")
        if available_periods and report_period == available_periods[0]:
            return self.cache
        if report_period in self.period_caches:
            return self.period_caches[report_period]

        disk_cache = self._load_period_cache_from_disk(report_period)
        if disk_cache is not None:
            self.period_caches[report_period] = disk_cache
            self.period_cache_progress[report_period] = {
                "state": "ready",
                "source": "disk",
                "completed_funds": len(FUND_MANAGERS),
                "total_funds": len(FUND_MANAGERS)
            }
            return disk_cache

        lock = self.period_cache_locks.setdefault(report_period, asyncio.Lock())
        async with lock:
            if report_period in self.period_caches:
                return self.period_caches[report_period]

            roster_snapshot = deepcopy(FUND_MANAGERS)
            roster_fingerprint = self._roster_fingerprint(
                roster_snapshot
            )
            period_cache = {}
            loop = asyncio.get_event_loop()
            self.period_cache_progress[report_period] = {
                "state": "fetching",
                "source": "sec",
                "completed_funds": 0,
                "total_funds": len(roster_snapshot)
            }
            try:
                for chunk_start in range(0, len(roster_snapshot), 5):
                    funds = roster_snapshot[chunk_start:chunk_start + 5]
                    results = await asyncio.gather(*[
                        loop.run_in_executor(
                            None,
                            self._fetch_fund_period_sync,
                            fund,
                            report_period
                        )
                        for fund in funds
                    ])
                    for fund, result in zip(funds, results):
                        period_cache[fund["cik"]] = {
                            "fund_info": fund,
                            "status": "unavailable",
                            "metadata": {},
                            "holdings": None,
                            "comparison": None,
                            "error": None,
                            **result,
                            "previous_comparison": None,
                            "last_updated": datetime.now(timezone.utc).isoformat()
                        }
                    self.period_cache_progress[report_period]["completed_funds"] = len(
                        period_cache
                    )
                    if chunk_start + 5 < len(roster_snapshot):
                        await asyncio.sleep(0.5)

                if self._roster_fingerprint() != roster_fingerprint:
                    raise RuntimeError(
                        "Roster changed while the historical period was "
                        "building; discarded the mixed snapshot"
                    )
                self._save_period_cache_to_disk(
                    report_period,
                    period_cache,
                    roster_fingerprint,
                )
                self.period_caches[report_period] = period_cache
                self.period_cache_progress[report_period] = {
                    "state": "ready",
                    "source": "sec",
                    "completed_funds": len(period_cache),
                    "total_funds": len(roster_snapshot)
                }
                return period_cache
            except Exception as e:
                self.period_cache_progress[report_period] = {
                    "state": "error",
                    "source": "sec",
                    "completed_funds": len(period_cache),
                    "total_funds": len(FUND_MANAGERS),
                    "error": str(e)
                }
                raise

    def get_period_cache_status(self, report_period: str):
        periods = self.get_available_periods()
        if report_period not in periods:
            return {
                "state": "invalid",
                "source": None,
                "completed_funds": 0,
                "total_funds": len(FUND_MANAGERS)
            }
        if periods and report_period == periods[0]:
            return {
                "state": "ready",
                "source": "latest",
                "completed_funds": len(FUND_MANAGERS),
                "total_funds": len(FUND_MANAGERS)
            }
        if report_period in self.period_caches:
            status = self.period_cache_progress.get(report_period, {})
            return {
                "state": "ready",
                "source": status.get("source", "memory"),
                "completed_funds": len(FUND_MANAGERS),
                "total_funds": len(FUND_MANAGERS)
            }
        if report_period in self.period_cache_progress:
            return self.period_cache_progress[report_period]
        if os.path.exists(self._get_period_cache_path(report_period)):
            return {
                "state": "ready",
                "source": "disk",
                "completed_funds": len(FUND_MANAGERS),
                "total_funds": len(FUND_MANAGERS)
            }
        return {
            "state": "uncached",
            "source": "sec",
            "completed_funds": 0,
            "total_funds": len(FUND_MANAGERS)
        }

    @staticmethod
    def _get_typical_share_adjustment_pct(
        current_holdings,
        previous_holdings
    ):
        if (
            current_holdings is None
            or previous_holdings is None
            or current_holdings.empty
            or previous_holdings.empty
            or not {"Cusip", "SharesPrnAmount"}.issubset(
                current_holdings.columns
            )
            or not {"Cusip", "SharesPrnAmount"}.issubset(
                previous_holdings.columns
            )
        ):
            return None

        current = current_holdings[[
            "Cusip",
            "SharesPrnAmount"
        ]].copy()
        previous = previous_holdings[[
            "Cusip",
            "SharesPrnAmount"
        ]].copy()
        current["Cusip"] = (
            current["Cusip"].astype(str).str.strip().str.upper()
        )
        previous["Cusip"] = (
            previous["Cusip"].astype(str).str.strip().str.upper()
        )
        current["SharesPrnAmount"] = pd.to_numeric(
            current["SharesPrnAmount"],
            errors="coerce"
        ).fillna(0.0)
        previous["SharesPrnAmount"] = pd.to_numeric(
            previous["SharesPrnAmount"],
            errors="coerce"
        ).fillna(0.0)
        current = current.groupby("Cusip", as_index=False)[
            "SharesPrnAmount"
        ].sum().rename(columns={"SharesPrnAmount": "CurrentShares"})
        previous = previous.groupby("Cusip", as_index=False)[
            "SharesPrnAmount"
        ].sum().rename(columns={"SharesPrnAmount": "PreviousShares"})
        merged = pd.merge(current, previous, on="Cusip", how="inner")
        continuing = merged[
            (merged["CurrentShares"] > 0)
            & (merged["PreviousShares"] > 0)
            & (merged["CurrentShares"] != merged["PreviousShares"])
        ].copy()
        if continuing.empty:
            return None
        adjustments = (
            (
                continuing["CurrentShares"]
                - continuing["PreviousShares"]
            )
            / continuing["PreviousShares"]
            * 100.0
        ).abs()
        return float(statistics.median(adjustments.tolist()))

    @staticmethod
    def _calculate_relative_conviction(
        status,
        share_change_pct,
        typical_share_change_pct,
        previous_weight,
        current_weight,
        typical_position_weight
    ):
        position_significance = (
            max(float(previous_weight), float(current_weight))
            / float(typical_position_weight)
            if typical_position_weight is not None
            and typical_position_weight > 0
            else None
        )
        if status in {"INCREASED", "DECREASED"}:
            if (
                share_change_pct is None
                or typical_share_change_pct is None
                or typical_share_change_pct <= 0
                or position_significance is None
            ):
                return None, "SHARE_CHANGE", position_significance, False
            direction = 1.0 if status == "INCREASED" else -1.0
            relative_conviction = (
                direction
                * abs(float(share_change_pct))
                / float(typical_share_change_pct)
            )
            return (
                relative_conviction,
                "SHARE_CHANGE",
                position_significance,
                position_significance < 0.25
            )
        if status in {"NEW", "CLOSED"}:
            if position_significance is None:
                return None, "POSITION_SIZE", None, False
            direction = 1.0 if status == "NEW" else -1.0
            return (
                direction * position_significance,
                "POSITION_SIZE",
                position_significance,
                False
            )
        return None, "UNCHANGED", position_significance, False

    def _get_ticker_snapshot_changes(
        self,
        current_ticker_data,
        previous_ticker_data,
        current_cache,
        previous_cache,
        current_available_funds,
        previous_available_funds,
        current_period
    ):
        current_holders = {
            holder["cik"]: holder
            for holder in (
                current_ticker_data["holders"]
                if current_ticker_data
                else []
            )
        }
        previous_holders = {
            holder["cik"]: holder
            for holder in (
                previous_ticker_data["holders"]
                if previous_ticker_data
                else []
            )
        }
        changes = []

        for cik in set(current_holders) | set(previous_holders):
            if (
                cik not in current_available_funds
                or cik not in previous_available_funds
            ):
                continue
            current_holder = current_holders.get(cik)
            previous_holder = previous_holders.get(cik)
            current_shares = (
                float(current_holder["shares"])
                if current_holder
                else 0.0
            )
            previous_shares = (
                float(previous_holder["shares"])
                if previous_holder
                else 0.0
            )
            if current_shares > 0 and previous_shares <= 0:
                status = "NEW"
            elif current_shares <= 0 and previous_shares > 0:
                status = "CLOSED"
            elif current_shares > previous_shares:
                status = "INCREASED"
            elif current_shares < previous_shares:
                status = "DECREASED"
            else:
                status = "UNCHANGED"

            current_fund = current_cache.get(cik, {})
            fund_data = current_fund or previous_cache.get(cik)
            previous_fund = previous_cache.get(cik, {})
            previous_total_value = float(
                previous_fund.get("metadata", {}).get("total_value", 0.0)
            )
            previous_holdings = previous_fund.get("holdings")
            current_holdings = current_fund.get("holdings")
            typical_position_weight = None
            if (
                previous_holdings is not None
                and not previous_holdings.empty
                and "PortfolioWeight" in previous_holdings.columns
            ):
                weights = pd.to_numeric(
                    previous_holdings["PortfolioWeight"],
                    errors="coerce"
                ).dropna()
                weights = weights[weights > 0]
                if not weights.empty:
                    typical_position_weight = float(
                        statistics.median(weights.tolist())
                    )
            typical_share_change_pct = None
            if status in {"INCREASED", "DECREASED"}:
                cache_key = (current_period, cik)
                if cache_key not in self.manager_adjustment_cache:
                    self.manager_adjustment_cache[cache_key] = (
                        self._get_typical_share_adjustment_pct(
                            current_holdings,
                            previous_holdings
                        )
                    )
                typical_share_change_pct = (
                    self.manager_adjustment_cache[cache_key]
                )

            current_weight = (
                float(current_holder["portfolio_weight"])
                if current_holder
                else 0.0
            )
            previous_weight = (
                float(previous_holder["portfolio_weight"])
                if previous_holder
                else 0.0
            )
            shares_change = current_shares - previous_shares
            shares_change_pct = (
                shares_change / previous_shares * 100.0
                if previous_shares > 0
                else None
            )
            changes.append({
                "cik": cik,
                "manager": fund_data["fund_info"]["manager"],
                "fund_name": fund_data["fund_info"]["name"],
                "status": status,
                "shares_change": shares_change,
                "shares_change_pct": shares_change_pct,
                "previous_portfolio_weight": previous_weight,
                "portfolio_weight": current_weight,
                "portfolio_weight_change_raw": (
                    current_weight - previous_weight
                ),
                "previous_portfolio_value_raw": previous_total_value,
                "manager_typical_position_weight": typical_position_weight,
                "manager_typical_share_change_pct": (
                    typical_share_change_pct
                )
            })

        return changes

    def _build_sentiment_investor_changes(self, changes):
        action_counts = {
            "new": 0,
            "increased": 0,
            "decreased": 0,
            "closed": 0,
            "unchanged": 0,
        }
        investor_changes = []
        for move in changes:
            status_key = move["status"].lower()
            if status_key in action_counts:
                action_counts[status_key] += 1
            raw_weight_change = float(
                move.get(
                    "portfolio_weight_change_raw",
                    move.get("portfolio_weight_change", 0.0),
                )
            )
            typical_position_weight = move.get(
                "manager_typical_position_weight"
            )
            typical_share_change_pct = move.get(
                "manager_typical_share_change_pct"
            )
            share_change_pct = move.get("shares_change_pct")
            (
                relative_conviction,
                conviction_basis,
                position_significance,
                force_routine,
            ) = self._calculate_relative_conviction(
                move["status"],
                share_change_pct,
                typical_share_change_pct,
                move.get("previous_portfolio_weight", 0.0),
                move.get("portfolio_weight", 0.0),
                typical_position_weight,
            )
            relative_conviction_valid = (
                relative_conviction is not None
                and math.isfinite(relative_conviction)
            )
            relative_magnitude = (
                abs(relative_conviction)
                if relative_conviction_valid
                else 0.0
            )
            conviction_class = (
                "UNAVAILABLE"
                if not relative_conviction_valid
                else "ROUTINE"
                if force_routine or relative_magnitude < 0.25
                else "MEANINGFUL"
                if relative_magnitude < 0.75
                else "HIGH"
                if relative_magnitude < 1.50
                else "EXCEPTIONAL"
            )
            investor_changes.append({
                "cik": move["cik"],
                "manager": move["manager"],
                "fund_name": move["fund_name"],
                "status": move["status"],
                "previous_weight": move.get(
                    "previous_portfolio_weight",
                    0.0,
                ),
                "current_weight": move.get("portfolio_weight", 0.0),
                "weight_change": round(raw_weight_change, 2),
                "share_change_pct": (
                    round(float(share_change_pct), 2)
                    if share_change_pct is not None
                    else None
                ),
                "typical_share_change_pct": (
                    round(float(typical_share_change_pct), 2)
                    if typical_share_change_pct is not None
                    else None
                ),
                "typical_position_weight": typical_position_weight,
                "position_significance": (
                    round(position_significance, 4)
                    if position_significance is not None
                    else None
                ),
                "conviction_basis": conviction_basis,
                "position_size_gate_applied": force_routine,
                "relative_conviction": (
                    round(relative_conviction, 4)
                    if relative_conviction_valid
                    else None
                ),
                "conviction_class": conviction_class,
            })
        return action_counts, investor_changes

    @staticmethod
    def _score_sentiment_period(
        item,
        previous_score=None,
        previous_regime_bucket=None,
        regime_streak=0,
    ):
        materiality_threshold = 0.25
        conviction_cap = 2.0
        actions = item["actions"]
        activity_bullish_count = actions["new"] + actions["increased"]
        activity_bearish_count = actions["decreased"] + actions["closed"]
        activity_directional_count = (
            activity_bullish_count + activity_bearish_count
        )
        activity_breadth_score = (
            100.0
            * (activity_bullish_count - activity_bearish_count)
            / activity_directional_count
            if activity_directional_count > 0
            else None
        )

        positive_conviction = 0.0
        negative_conviction = 0.0
        meaningful_bullish_count = 0
        meaningful_bearish_count = 0
        routine_count = 0
        unscored_count = 0
        for change in item["investor_changes"]:
            relative_conviction = change["relative_conviction"]
            if change["status"] == "UNCHANGED":
                change["scored_relative_conviction"] = None
                continue
            if relative_conviction is None:
                unscored_count += 1
                change["scored_relative_conviction"] = None
                continue
            if (
                change.get("position_size_gate_applied")
                or abs(relative_conviction) < materiality_threshold
            ):
                routine_count += 1
                change["scored_relative_conviction"] = 0.0
                continue

            scored_conviction = max(
                -conviction_cap,
                min(conviction_cap, relative_conviction),
            )
            change["scored_relative_conviction"] = round(
                scored_conviction,
                4,
            )
            if scored_conviction > 0:
                meaningful_bullish_count += 1
                positive_conviction += scored_conviction
            else:
                meaningful_bearish_count += 1
                negative_conviction += abs(scored_conviction)

        meaningful_count = (
            meaningful_bullish_count + meaningful_bearish_count
        )
        meaningful_breadth_score = (
            100.0
            * (meaningful_bullish_count - meaningful_bearish_count)
            / meaningful_count
            if meaningful_count > 0
            else None
        )
        conviction_total = positive_conviction + negative_conviction
        conviction_score = (
            100.0
            * (positive_conviction - negative_conviction)
            / conviction_total
            if conviction_total > 0
            else None
        )
        sentiment_score = (
            (meaningful_breadth_score + conviction_score) / 2.0
            if meaningful_breadth_score is not None
            and conviction_score is not None
            else None
        )

        published_score = (
            sentiment_score
            if meaningful_count >= 3
            else None
        )
        if meaningful_count < 3 and activity_directional_count > 0:
            regime = "LOW PARTICIPATION"
            regime_bucket = "NO SIGNAL"
        elif sentiment_score is None:
            regime = "NO SIGNAL"
            regime_bucket = "NO SIGNAL"
        elif sentiment_score >= 60:
            regime = "STRONGLY BULLISH"
            regime_bucket = "BULLISH"
        elif sentiment_score >= 25:
            regime = "BULLISH"
            regime_bucket = "BULLISH"
        elif sentiment_score <= -60:
            regime = "STRONGLY BEARISH"
            regime_bucket = "BEARISH"
        elif sentiment_score <= -25:
            regime = "BEARISH"
            regime_bucket = "BEARISH"
        else:
            regime = "NEUTRAL"
            regime_bucket = "NEUTRAL"

        flow_total = (
            item["gross_inflow"] + item["gross_outflow"]
            if item.get("gross_inflow") is not None
            and item.get("gross_outflow") is not None
            else None
        )
        flow_balance_score = (
            100.0 * item["net_flow"] / flow_total
            if flow_total is not None and flow_total > 0
            else None
        )
        if (
            published_score is None
            or abs(published_score) < 25
            or flow_balance_score is None
            or abs(flow_balance_score) < 10
        ):
            flow_confirmation = "NEUTRAL"
        elif (
            published_score > 0 and flow_balance_score > 0
        ) or (
            published_score < 0 and flow_balance_score < 0
        ):
            flow_confirmation = "CONFIRMS"
        else:
            flow_confirmation = "DIVERGES"

        if regime_bucket == "NO SIGNAL":
            next_regime_streak = 0
        elif regime_bucket == previous_regime_bucket:
            next_regime_streak = regime_streak + 1
        else:
            next_regime_streak = 1

        item["sentiment"] = {
            "activity_bullish_count": activity_bullish_count,
            "activity_bearish_count": activity_bearish_count,
            "activity_breadth_score": (
                round(activity_breadth_score, 2)
                if activity_breadth_score is not None
                else None
            ),
            "bullish_count": meaningful_bullish_count,
            "bearish_count": meaningful_bearish_count,
            "meaningful_count": meaningful_count,
            "unchanged_count": actions["unchanged"],
            "routine_count": routine_count,
            "unscored_count": unscored_count,
            "breadth_score": (
                round(meaningful_breadth_score, 2)
                if meaningful_breadth_score is not None
                else None
            ),
            "positive_conviction_x": round(positive_conviction, 2),
            "negative_conviction_x": round(negative_conviction, 2),
            "conviction_score": (
                round(conviction_score, 2)
                if conviction_score is not None
                else None
            ),
            "materiality_threshold_x": materiality_threshold,
            "conviction_cap_x": conviction_cap,
            "indicative_score": (
                round(sentiment_score, 2)
                if sentiment_score is not None
                else None
            ),
            "score": (
                round(published_score, 2)
                if published_score is not None
                else None
            ),
            "score_change": (
                round(published_score - previous_score, 2)
                if published_score is not None
                and previous_score is not None
                else None
            ),
            "regime": regime,
            "regime_streak": next_regime_streak,
            "flow_balance_score": (
                round(flow_balance_score, 2)
                if flow_balance_score is not None
                else None
            ),
            "flow_confirmation": flow_confirmation,
        }
        return published_score, regime_bucket, next_regime_streak

    def get_ticker_sentiment_summaries(self, tickers, changes):
        normalized_tickers = {
            str(ticker).strip().upper()
            for ticker in tickers
            if str(ticker).strip()
        }
        changes_by_ticker = {
            ticker: []
            for ticker in normalized_tickers
        }
        for change in changes:
            ticker = str(change.get("ticker") or "").strip().upper()
            if ticker in changes_by_ticker:
                changes_by_ticker[ticker].append(change)

        summaries = {}
        for ticker, ticker_changes in changes_by_ticker.items():
            actions, investor_changes = self._build_sentiment_investor_changes(
                ticker_changes
            )
            item = {
                "actions": actions,
                "investor_changes": investor_changes,
                "gross_inflow": None,
                "gross_outflow": None,
                "net_flow": None,
            }
            self._score_sentiment_period(item)
            item["sentiment"]["investor_changes"] = investor_changes
            summaries[ticker] = item["sentiment"]
        return summaries

    async def get_ticker_intelligence(self, ticker: str):
        normalized = ticker.strip().upper()
        market = await self.get_ticker_market_data(normalized)
        periods = self.get_available_periods(count=20)
        quarter_end_prices = market.get("quarter_end_prices", {})
        quarter_average_prices = market.get("quarter_average_prices", {})
        history = []
        positions_by_period = {}
        available_funds_by_period = {}
        previous_period_cache = None
        previous_ticker_data = None
        previous_available_funds = None

        for period in reversed(periods):
            period_cache = await self.get_period_cache(period)
            available_funds_by_period[period] = {
                cik
                for cik, fund_data in period_cache.items()
                if fund_data.get("status") == "loaded"
            }
            ticker_data = self.get_ticker_view(
                normalized,
                fund_cache=period_cache
            )
            changes = (
                self._get_ticker_snapshot_changes(
                    ticker_data,
                    previous_ticker_data,
                    period_cache,
                    previous_period_cache,
                    available_funds_by_period[period],
                    previous_available_funds,
                    period
                )
                if previous_period_cache is not None
                else self.get_qoq_changes(
                    include_unchanged=True,
                    fund_cache=period_cache,
                    ticker=normalized
                )
            )
            action_counts, investor_changes = (
                self._build_sentiment_investor_changes(changes)
            )
            gross_inflow = 0.0
            gross_outflow = 0.0
            quarter_price = quarter_end_prices.get(period)
            flow_available = quarter_price is not None

            for move in changes:
                if quarter_price is not None:
                    estimated_flow = (
                        float(move["shares_change"]) * float(quarter_price)
                    ) / 1_000_000.0
                    if estimated_flow > 0:
                        gross_inflow += estimated_flow
                    elif estimated_flow < 0:
                        gross_outflow += abs(estimated_flow)

            holders = ticker_data["holders"] if ticker_data else []
            positions_by_period[period] = {
                holder["cik"]: float(holder["shares"])
                for holder in holders
            }
            history.append({
                "period": period,
                "investor_count": ticker_data["num_holders"] if ticker_data else 0,
                "total_value": (
                    ticker_data["total_value_across_funds"]
                    if ticker_data
                    else 0.0
                ),
                "total_shares": ticker_data["total_shares"] if ticker_data else 0,
                "median_weight": ticker_data["median_weight"] if ticker_data else 0.0,
                "actions": action_counts,
                "investor_changes": investor_changes,
                "gross_inflow": round(gross_inflow, 2) if flow_available else None,
                "gross_outflow": round(gross_outflow, 2) if flow_available else None,
                "net_flow": (
                    round(gross_inflow - gross_outflow, 2)
                    if flow_available
                    else None
                ),
                "quarter_end_price": quarter_price
            })
            previous_period_cache = period_cache
            previous_ticker_data = ticker_data
            previous_available_funds = available_funds_by_period[period]

        materiality_threshold = 0.25
        conviction_cap = 2.0
        previous_score = None
        previous_regime_bucket = None
        regime_streak = 0

        for item in history:
            (
                previous_score,
                previous_regime_bucket,
                regime_streak,
            ) = self._score_sentiment_period(
                item,
                previous_score,
                previous_regime_bucket,
                regime_streak,
            )
            previous_regime_bucket = (
                previous_regime_bucket
                if previous_regime_bucket != "NO SIGNAL"
                else None
            )

        latest_investor_changes = (
            history[-1]["investor_changes"]
            if history
            else []
        )
        bullish_contributors = sorted(
            (
                change
                for change in latest_investor_changes
                if (
                    change.get("scored_relative_conviction") is not None
                    and change["scored_relative_conviction"] > 0
                )
            ),
            key=lambda change: change["scored_relative_conviction"],
            reverse=True
        )[:5]
        bearish_contributors = sorted(
            (
                change
                for change in latest_investor_changes
                if (
                    change.get("scored_relative_conviction") is not None
                    and change["scored_relative_conviction"] < 0
                )
            ),
            key=lambda change: change["scored_relative_conviction"]
        )[:5]

        basis_states = {}
        for item in history:
            period = item["period"]
            current_positions = positions_by_period[period]
            available_funds = available_funds_by_period[period]
            estimated_purchase_price = (
                quarter_average_prices.get(period)
                or quarter_end_prices.get(period)
            )
            for cik in set(basis_states) | set(current_positions):
                if cik not in available_funds:
                    continue
                state = basis_states.setdefault(cik, {"shares": 0.0, "cost": 0.0})
                previous_shares = state["shares"]
                current_shares = current_positions.get(cik, 0.0)
                share_change = current_shares - previous_shares
                if share_change > 0 and estimated_purchase_price is not None:
                    state["cost"] += share_change * float(estimated_purchase_price)
                elif share_change < 0 and previous_shares > 0:
                    state["cost"] *= current_shares / previous_shares
                state["shares"] = current_shares
                if current_shares <= 0:
                    state["cost"] = 0.0

        estimated_cost = sum(state["cost"] for state in basis_states.values())
        estimated_shares = sum(state["shares"] for state in basis_states.values())
        estimated_basis = (
            estimated_cost / estimated_shares
            if estimated_shares > 0
            else None
        )
        current_price = market.get("quote", {}).get("last_price")
        market_price_as_of = (
            market.get("price_as_of")
            or self.market_insights.get(normalized, {}).get("price_as_of")
            or str(market.get("last_updated", ""))[:10]
            or None
        )
        year_low = market.get("quote", {}).get("year_low")
        price_above_52_week_low_pct = (
            ((float(current_price) / float(year_low)) - 1) * 100
            if current_price is not None
            and year_low is not None
            and float(year_low) > 0
            else None
        )
        price_vs_basis_pct = (
            ((float(current_price) / estimated_basis) - 1) * 100
            if current_price is not None
            and estimated_basis is not None
            and estimated_basis > 0
            else None
        )
        valuation = market.get("valuation", {})
        technical = market.get("technical", {})
        valuation_state = valuation.get("assessment", "UNAVAILABLE")
        trend_state = technical.get("trend_regime", "UNAVAILABLE")
        timing_state = technical.get("entry_timing", "UNAVAILABLE")
        latest_flow = (history[-1]["net_flow"] or 0.0) if history else 0.0
        latest_holders = history[-1]["investor_count"] if history else 0

        if valuation_state == "UNAVAILABLE" or trend_state == "UNAVAILABLE":
            stance = "INSUFFICIENT DATA"
            equity_sleeve_range = [0.0, 0.0]
        elif valuation_state == "OVERVALUED":
            stance = "WAIT"
            equity_sleeve_range = [0.0, 0.0]
        elif trend_state == "BEARISH" or timing_state == "WAIT FOR TREND":
            stance = "WAIT FOR TREND"
            equity_sleeve_range = [0.0, 0.0]
        elif timing_state == "EXTENDED":
            stance = "WAIT FOR PULLBACK"
            equity_sleeve_range = [0.0, 1.0]
        elif (
            valuation_state == "UNDERVALUED"
            and trend_state == "BULLISH"
            and latest_flow > 0
        ):
            stance = "ACCUMULATE ZONE"
            equity_sleeve_range = [3.5, 5.0]
        elif valuation_state == "UNDERVALUED":
            stance = "SCALE IN"
            equity_sleeve_range = [2.0, 4.0]
        elif (
            valuation_state == "NEUTRAL"
            and trend_state == "BULLISH"
            and latest_flow > 0
        ):
            stance = "SMALL STARTER / WATCH"
            equity_sleeve_range = [1.5, 3.0]
        else:
            stance = "HOLD / WATCH"
            equity_sleeve_range = [0.5, 2.0]

        volatility = technical.get("annualized_volatility_pct")
        volatility_multiplier = (
            0.6
            if volatility is None or volatility >= 45
            else 0.8
            if volatility is not None and volatility >= 30
            else 1.0
        )
        equity_sleeve_range = [
            round(min(5.0, weight * volatility_multiplier), 2)
            for weight in equity_sleeve_range
        ]
        total_portfolio_range = [
            round(weight * 0.30, 2)
            for weight in equity_sleeve_range
        ]

        return {
            "ticker": normalized,
            "market": market,
            "latest": history[-1] if history else None,
            "history": history,
            "sentiment": {
                "materiality_threshold_x": materiality_threshold,
                "conviction_cap_x": conviction_cap,
                "latest": history[-1]["sentiment"] if history else None,
                "bullish_contributors": bullish_contributors,
                "bearish_contributors": bearish_contributors
            },
            "estimated_whale_basis": (
                round(estimated_basis, 2)
                if estimated_basis is not None
                else None
            ),
            "price_vs_estimated_basis_pct": (
                round(price_vs_basis_pct, 2)
                if price_vs_basis_pct is not None
                else None
            ),
            "price_above_52_week_low_pct": (
                round(price_above_52_week_low_pct, 2)
                if price_above_52_week_low_pct is not None
                else None
            ),
            "market_price_as_of": market_price_as_of,
            "basis_methodology": (
                "Estimated weighted-average basis for current tracked shares. "
                "Quarterly net additions are priced at the average daily close "
                "during each reporting quarter; reductions remove cost proportionally. "
                "Positions already held at the 20-quarter boundary are initialized "
                "at that quarter's average price. This is not reported investor cost basis."
            ),
            "decision_support": {
                "stance": stance,
                "valuation": valuation_state,
                "trend": trend_state,
                "momentum": technical.get("momentum_state", "UNAVAILABLE"),
                "entry_timing": timing_state,
                "net_flow": latest_flow,
                "holder_count": latest_holders,
                "equity_sleeve_range_pct": equity_sleeve_range,
                "all_weather_total_portfolio_range_pct": total_portfolio_range,
                "all_weather_equity_sleeve_assumption_pct": 30.0,
                "single_stock_cap_pct_of_equity_sleeve": 5.0,
                "volatility_multiplier": volatility_multiplier,
                "methodology": (
                    "Illustrative risk-budget range, not personalized advice. "
                    "Uses valuation, 200-day trend, estimated whale flow, and "
                    "63-day annualized volatility. The total-portfolio range "
                    "assumes a 30% All Weather-style equity sleeve and caps a "
                    "single stock at 5% of that sleeve."
                )
            }
        }

    async def add_subscriber(self, queue: asyncio.Queue):
        self.subscribers.append(queue)

    async def remove_subscriber(self, queue: asyncio.Queue):
        if queue in self.subscribers:
            self.subscribers.remove(queue)

    async def broadcast_event(self, event_data: dict):
        for q in list(self.subscribers):
            try:
                await q.put(event_data)
            except Exception:
                pass

    def _build_fund_result(
        self,
        report,
        cik: str,
        include_previous_comparison=True,
        include_comparison=True
    ):
        holdings, value_scale = self._normalize_holdings_values(
            report.holdings
        )
        comp_data = None
        previous_comp_data = None
        previous_report_period = ""
        two_quarters_ago_period = ""

        if include_comparison:
            try:
                comparison = report.compare_holdings()
                if comparison is not None and hasattr(comparison, 'data') and comparison.data is not None:
                    comp_data = self._normalize_comparison_values(
                        comparison.data
                    )
                    previous_report_period = str(comparison.previous_period or "")
            except Exception as comp_err:
                logger.warning(f"Could not compute QoQ comparison for {cik}: {comp_err}")

        if include_previous_comparison:
            try:
                previous_report = report.previous_holding_report()
                if previous_report is not None:
                    previous_report_period = str(previous_report.report_period or previous_report_period)
                    previous_comparison = previous_report.compare_holdings()
                    if previous_comparison is not None and hasattr(previous_comparison, 'data') and previous_comparison.data is not None:
                        previous_comp_data = self._normalize_comparison_values(
                            previous_comparison.data
                        )
                        two_quarters_ago_period = str(previous_comparison.previous_period or "")
            except Exception as previous_comp_err:
                logger.warning(f"Could not compute prior-quarter comparison for {cik}: {previous_comp_err}")

        if holdings is not None and not holdings.empty:
            total_val = float(holdings["Value"].sum())
            if total_val > 0:
                holdings['PortfolioWeight'] = (holdings['Value'].astype(float) / total_val) * 100.0
            else:
                holdings['PortfolioWeight'] = 0.0

            if 'Ticker' in holdings.columns:
                holdings['Ticker'] = holdings['Ticker'].fillna('').astype(str).str.strip().str.upper()

        total_val_num = (
            float(holdings["Value"].sum())
            if holdings is not None and not holdings.empty
            else 0.0
        )
        total_holdings_num = int(report.total_holdings) if report.total_holdings else (
            len(holdings) if holdings is not None else 0
        )

        return {
            "status": "loaded",
            "metadata": {
                "management_company_name": str(report.management_company_name or ""),
                "total_value": total_val_num,
                "total_holdings": total_holdings_num,
                "report_period": str(report.report_period or ""),
                "filing_date": str(getattr(report, 'filing_date', '')),
                "value_scale_applied": value_scale,
                "previous_report_period": previous_report_period,
                "two_quarters_ago_period": two_quarters_ago_period
            },
            "holdings": holdings,
            "comparison": comp_data,
            "previous_comparison": previous_comp_data
        }

    @staticmethod
    def _compare_report_holdings(current_report, previous_report):
        def prepare(report, current):
            holdings, _ = DataService._normalize_holdings_values(
                report.holdings
            )
            if holdings is None or holdings.empty:
                return pd.DataFrame()
            frame = holdings[
                ["Cusip", "Ticker", "Issuer", "SharesPrnAmount", "Value"]
            ].copy()
            frame["Cusip"] = frame["Cusip"].astype(str).str.strip()
            frame["SharesPrnAmount"] = pd.to_numeric(
                frame["SharesPrnAmount"],
                errors="coerce"
            ).fillna(0.0)
            frame["Value"] = pd.to_numeric(
                frame["Value"],
                errors="coerce"
            ).fillna(0.0)
            frame = frame[frame["Cusip"] != ""]
            frame = frame.groupby("Cusip", as_index=False).agg({
                "Ticker": "first",
                "Issuer": "first",
                "SharesPrnAmount": "sum",
                "Value": "sum"
            })
            return frame.rename(columns={
                "Ticker": "TickerCurrent" if current else "TickerPrevious",
                "Issuer": "IssuerCurrent" if current else "IssuerPrevious",
                "SharesPrnAmount": "Shares" if current else "PrevShares",
                "Value": "Value" if current else "PrevValue"
            })

        current = prepare(current_report, True)
        previous = prepare(previous_report, False)
        merged = pd.merge(current, previous, on="Cusip", how="outer")
        merged["Ticker"] = merged["TickerCurrent"].combine_first(
            merged["TickerPrevious"]
        )
        merged["Issuer"] = merged["IssuerCurrent"].combine_first(
            merged["IssuerPrevious"]
        )
        merged["Shares"] = pd.to_numeric(
            merged["Shares"], errors="coerce"
        ).fillna(0.0)
        merged["PrevShares"] = pd.to_numeric(
            merged["PrevShares"], errors="coerce"
        ).fillna(0.0)
        merged["Value"] = pd.to_numeric(
            merged["Value"], errors="coerce"
        ).fillna(0.0)
        merged["PrevValue"] = pd.to_numeric(
            merged["PrevValue"], errors="coerce"
        ).fillna(0.0)
        merged["ShareChange"] = merged["Shares"] - merged["PrevShares"]
        merged["ValueChange"] = merged["Value"] - merged["PrevValue"]
        merged["ShareChangePct"] = merged.apply(
            lambda row: (
                row["ShareChange"] / row["PrevShares"] * 100.0
                if row["PrevShares"] > 0
                else None
            ),
            axis=1
        )
        merged["ValueChangePct"] = merged.apply(
            lambda row: (
                row["ValueChange"] / row["PrevValue"] * 100.0
                if row["PrevValue"] > 0
                else None
            ),
            axis=1
        )

        def classify(row):
            if row["Shares"] > 0 and row["PrevShares"] <= 0:
                return "NEW"
            if row["Shares"] <= 0 and row["PrevShares"] > 0:
                return "CLOSED"
            if row["Shares"] > row["PrevShares"]:
                return "INCREASED"
            if row["Shares"] < row["PrevShares"]:
                return "DECREASED"
            return "UNCHANGED"

        merged["Status"] = merged.apply(classify, axis=1)
        return merged[[
            "Cusip",
            "Ticker",
            "Issuer",
            "Shares",
            "Value",
            "PrevShares",
            "PrevValue",
            "ShareChange",
            "ShareChangePct",
            "ValueChange",
            "ValueChangePct",
            "Status"
        ]]

    def _find_best_report_for_period(self, fund, report_period):
        candidates = []
        for cik in [fund["cik"], *fund.get("historical_ciks", [])]:
            try:
                filings = Company(cik).get_filings(form="13F-HR")
                for filing in filings:
                    if str(filing.report_date or "") == report_period:
                        report = filing.obj()
                        candidates.append((
                            report,
                            cik,
                            str(filing.form),
                            str(filing.filing_date)
                        ))
            except Exception as e:
                logger.warning(
                    f"Could not inspect {report_period} filing for "
                    f"{fund['manager']} under CIK {cik}: {e}"
                )
        if not candidates:
            return None, None

        def holding_count(candidate):
            holdings = candidate[0].holdings
            return len(holdings) if holdings is not None else 0

        max_holding_count = max(
            holding_count(candidate)
            for candidate in candidates
        )
        complete_candidates = [
            candidate
            for candidate in candidates
            if holding_count(candidate) == max_holding_count
        ]
        selected = max(
            complete_candidates,
            key=lambda candidate: candidate[3]
        )
        return selected[0], selected[1]

    def _build_cross_cik_fund_result(
        self,
        fund,
        report,
        include_previous_comparison=True
    ):
        result = self._build_fund_result(
            report,
            fund["cik"],
            include_previous_comparison=False,
            include_comparison=False
        )
        current_period = pd.Period(str(report.report_period), freq="Q")
        previous_period = (current_period - 1).end_time.date().isoformat()
        older_period = (current_period - 2).end_time.date().isoformat()
        previous_report, _ = self._find_best_report_for_period(
            fund,
            previous_period
        )
        older_report = None
        if include_previous_comparison:
            older_report, _ = self._find_best_report_for_period(
                fund,
                older_period
            )
        if previous_report is not None:
            result["comparison"] = self._compare_report_holdings(
                report,
                previous_report
            )
            result["metadata"]["previous_report_period"] = previous_period
        if previous_report is not None and older_report is not None:
            result["previous_comparison"] = self._compare_report_holdings(
                previous_report,
                older_report
            )
            result["metadata"]["two_quarters_ago_period"] = older_period
        return result

    def _fetch_fund_sync(self, cik: str):
        try:
            filings = Company(cik).get_filings(form='13F-HR')
            if not filings or len(filings) == 0:
                filings = Company(cik).get_filings(form='13F-HR/A')

            if not filings or len(filings) == 0:
                return {"status": "error", "error": "No 13F filings found on EDGAR"}

            fund = next(
                (
                    item
                    for item in FUND_MANAGERS
                    if item["cik"] == cik
                ),
                None
            )
            if fund:
                latest_period = str(filings[0].report_date or "")
                report, _ = self._find_best_report_for_period(
                    fund,
                    latest_period
                )
                if report is not None:
                    return self._build_cross_cik_fund_result(fund, report)
            return self._build_fund_result(filings[0].obj(), cik)
        except Exception as e:
            logger.error(f"Error fetching fund {cik}: {e}")
            return {"status": "error", "error": str(e)}

    def _fetch_fund_period_sync(self, fund, report_period: str):
        try:
            report, _ = self._find_best_report_for_period(
                fund,
                report_period
            )
            if report is not None:
                return self._build_cross_cik_fund_result(
                    fund,
                    report,
                    include_previous_comparison=False
                )
            return {
                "status": "unavailable",
                "error": f"No 13F filing found for {report_period}"
            }
        except Exception as e:
            logger.warning(
                f"Could not build {report_period} snapshot for "
                f"{fund['manager']}: {e}"
            )
            return {
                "status": "unavailable",
                "error": str(e)
            }

    async def refresh_fund(self, cik: str):
        loop = asyncio.get_event_loop()
        if cik not in self.cache:
            raise ValueError(f"CIK {cik} is not in the configured roster")
        self.cache[cik]["status"] = "loading"
        await self.broadcast_event({"type": "fund_status", "cik": cik, "status": "loading"})

        result = await loop.run_in_executor(None, self._fetch_fund_sync, cik)

        self.cache[cik].update(result)
        self.cache[cik]["last_updated"] = datetime.now(timezone.utc).isoformat()

        # Persist to disk cache
        if result.get("status") == "loaded":
            self._save_fund_to_disk_cache(cik)

        await self.broadcast_event({
            "type": "fund_updated",
            "cik": cik,
            "status": result["status"]
        })

    def sync_roster(self):
        active_by_cik = {fund["cik"]: fund for fund in FUND_MANAGERS}
        previous_ciks = set(self.cache)
        active_ciks = set(active_by_cik)
        removed = sorted(previous_ciks - active_ciks)
        added = sorted(active_ciks - previous_ciks)

        for cik in removed:
            self.cache.pop(cik, None)
            self.pending_roster_refresh_ciks.discard(cik)
        for cik, fund in active_by_cik.items():
            if cik not in self.cache:
                self.cache[cik] = self._new_fund_cache(fund)
            else:
                self.cache[cik]["fund_info"] = fund

        self.cache = {
            fund["cik"]: self.cache[fund["cik"]]
            for fund in FUND_MANAGERS
        }
        self.period_caches.clear()
        self.period_cache_progress.clear()
        self.manager_adjustment_cache.clear()
        if added:
            self._load_all_from_disk_cache()
        return {"added": added, "removed": removed}

    async def refresh_funds(self, ciks):
        self.pending_roster_refresh_ciks.update(
            cik for cik in ciks
            if cik in self.cache
        )
        if not self.pending_roster_refresh_ciks:
            return
        refresh_lock = getattr(self, "_refresh_lock", None)
        if refresh_lock is None:
            if self.is_refreshing:
                return
            refresh_lock = asyncio.Lock()
            self._refresh_lock = refresh_lock

        async with refresh_lock:
            self.is_refreshing = True
            try:
                while self.pending_roster_refresh_ciks:
                    selected = [
                        cik for cik in self.pending_roster_refresh_ciks
                        if cik in self.cache
                    ]
                    self.pending_roster_refresh_ciks.difference_update(
                        selected
                    )
                    for chunk_start in range(0, len(selected), 5):
                        chunk = selected[chunk_start:chunk_start + 5]
                        await asyncio.gather(*[
                            self.refresh_fund(cik)
                            for cik in chunk
                        ])
                        if chunk_start + 5 < len(selected):
                            await asyncio.sleep(0.5)
                await self.refresh_market_insights()
                self.last_updated = datetime.now(timezone.utc).isoformat()
                await self.broadcast_event({
                    "type": "data_refresh",
                    "timestamp": self.last_updated,
                })
            finally:
                self.is_refreshing = False

    async def refresh_roster_market_context(self):
        refresh_lock = getattr(self, "_refresh_lock", None)
        if refresh_lock is None:
            refresh_lock = asyncio.Lock()
            self._refresh_lock = refresh_lock
        async with refresh_lock:
            refreshed = await self.refresh_market_insights()
            if not refreshed:
                return
            self.last_updated = datetime.now(timezone.utc).isoformat()
            await self.broadcast_event({
                "type": "data_refresh",
                "timestamp": self.last_updated,
            })

    async def refresh_all(self):
        if self.is_refreshing or getattr(
            self,
            "_full_refresh_pending",
            False,
        ):
            return False
        self._full_refresh_pending = True
        refresh_lock = getattr(self, "_refresh_lock", None)
        if refresh_lock is None:
            refresh_lock = asyncio.Lock()
            self._refresh_lock = refresh_lock
        try:
            async with refresh_lock:
                if self.is_refreshing:
                    return False
                self.is_refreshing = True
                logger.info("Starting refresh of all 13F funds...")
                try:
                    tasks = [
                        self.refresh_fund(fund["cik"])
                        for fund in FUND_MANAGERS
                    ]
                    # Process with controlled concurrency to respect SEC rate limit guidelines (10 req/s)
                    for chunk_start in range(0, len(tasks), 5):
                        chunk = tasks[chunk_start:chunk_start + 5]
                        await asyncio.gather(*chunk)
                        await asyncio.sleep(0.5)

                    await self.refresh_market_insights()
                    self.last_updated = datetime.now(timezone.utc).isoformat()
                    await self.broadcast_event({
                        "type": "data_refresh",
                        "timestamp": self.last_updated
                    })
                    logger.info("Completed refreshing all 13F funds.")
                    return True
                finally:
                    self.is_refreshing = False
        finally:
            self._full_refresh_pending = False

    async def auto_refresh_loop(self, after_refresh=None):
        # Initial refresh if cache is completely empty or stale
        loaded_count = sum(1 for c in self.cache.values() if c["status"] == "loaded")
        cache_ready = True
        if loaded_count < len(FUND_MANAGERS):
            logger.info(f"{loaded_count}/{len(FUND_MANAGERS)} loaded from disk. Launching initial background fetch...")
            cache_ready = bool(await self.refresh_all())
        elif not self.market_insights or any(
            "quarter_market_metrics" not in item
            for item in self.market_insights.values()
        ):
            await self.refresh_market_insights()
        if after_refresh is not None and cache_ready:
            await after_refresh()

        while True:
            await asyncio.sleep(CACHE_TTL_HOURS * 3600)
            completed = await self.refresh_all()
            if after_refresh is not None and completed:
                await after_refresh()

    def _get_high_conviction_tickers(self):
        tickers = set()
        for fund_data in self.cache.values():
            holdings = fund_data.get("holdings")
            if fund_data.get("status") != "loaded" or holdings is None or holdings.empty:
                continue
            if "Ticker" not in holdings.columns or "PortfolioWeight" not in holdings.columns:
                continue

            qualifying = holdings[
                pd.to_numeric(holdings["PortfolioWeight"], errors="coerce").fillna(0) >= 5.0
            ]["Ticker"]
            for ticker in qualifying.dropna().astype(str):
                normalized = ticker.strip().upper()
                if normalized and normalized not in {"NAN", "NONE"}:
                    tickers.add(normalized)
        return sorted(tickers)

    def _fetch_market_insights_sync(self, symbols, report_period):
        from openbb import obb

        frames = []
        end_date = date.today()
        report_date = date.fromisoformat(report_period) if report_period else None
        quarter_periods = []
        if report_period:
            latest_quarter = pd.Period(report_period, freq="Q")
            quarter_periods = [
                (latest_quarter - offset).end_time.date().isoformat()
                for offset in range(20)
            ]
        start_date = (
            date.fromisoformat(quarter_periods[-1]) - timedelta(days=10)
            if quarter_periods
            else end_date - timedelta(days=370)
        )
        symbol_aliases = {
            "BRKA": "BRK-A",
            "BRKB": "BRK-B",
            "HEIA": "HEI-A"
        }
        market_symbols = [symbol_aliases.get(symbol, symbol) for symbol in symbols]
        original_symbols = {
            market_symbol: original_symbol
            for original_symbol, market_symbol in zip(symbols, market_symbols)
        }

        for chunk_start in range(0, len(market_symbols), 30):
            chunk = market_symbols[chunk_start:chunk_start + 30]
            try:
                result = obb.equity.price.historical(
                    symbol=chunk,
                    start_date=start_date,
                    end_date=end_date,
                    provider="yfinance",
                    interval="1d"
                )
                frame = result.to_df()
                if frame is not None and not frame.empty:
                    if "symbol" not in frame.columns and len(chunk) == 1:
                        frame["symbol"] = chunk[0]
                    frames.append(frame.reset_index())
            except Exception as e:
                logger.warning(f"OpenBB price history failed for {', '.join(chunk)}: {e}")

        if not frames:
            return []

        prices = pd.concat(frames, ignore_index=True)
        insights = []
        for symbol, rows in prices.groupby("symbol"):
            rows = rows.sort_values("date")
            rows["date"] = pd.to_datetime(rows["date"], errors="coerce")
            recent_rows = rows[
                rows["date"].dt.date >= end_date - timedelta(days=370)
            ]
            lows = pd.to_numeric(recent_rows["low"], errors="coerce").dropna()
            closes = pd.to_numeric(rows["close"], errors="coerce").dropna()
            if lows.empty or closes.empty:
                continue

            low_52_week = float(lows.min())
            low_52_week_date = (
                recent_rows.loc[lows.idxmin(), "date"].date().isoformat()
            )
            current_price = float(closes.iloc[-1])
            if low_52_week <= 0:
                continue

            quarter_end_price = None
            price_return_since_quarter = None
            quarter_end_prices = {}
            quarter_market_metrics = {}
            for period in quarter_periods:
                period_date = date.fromisoformat(period)
                period_rows = rows[rows["date"].dt.date <= period_date]
                period_closes = pd.to_numeric(
                    period_rows["close"], errors="coerce"
                ).dropna()
                if not period_closes.empty:
                    period_price = float(period_closes.iloc[-1])
                    quarter_end_prices[period] = round(period_price, 2)
                    trailing_rows = period_rows[
                        period_rows["date"].dt.date >= period_date - timedelta(days=370)
                    ]
                    trailing_lows = pd.to_numeric(
                        trailing_rows["low"], errors="coerce"
                    ).dropna()
                    if not trailing_lows.empty:
                        period_low = float(trailing_lows.min())
                        if period_low > 0:
                            quarter_market_metrics[period] = {
                                "current_price": round(period_price, 2),
                                "low_52_week": round(period_low, 2),
                                "pct_above_low": round(
                                    ((period_price / period_low) - 1) * 100,
                                    2
                                )
                            }

            if report_date:
                quarter_end_price = quarter_end_prices.get(report_period)
                if quarter_end_price is not None:
                    if quarter_end_price > 0:
                        price_return_since_quarter = (
                            (current_price / quarter_end_price) - 1
                        ) * 100

            insights.append({
                "ticker": original_symbols.get(str(symbol).strip().upper(), str(symbol).strip().upper()),
                "current_price": round(current_price, 2),
                "low_52_week": round(low_52_week, 2),
                "low_52_week_date": low_52_week_date,
                "pct_above_low": round(((current_price / low_52_week) - 1) * 100, 2),
                "price_as_of": rows.iloc[-1]["date"].date().isoformat(),
                "quarter_end_price": round(quarter_end_price, 2) if quarter_end_price is not None else None,
                "quarter_end_period": report_period,
                "quarter_end_prices": quarter_end_prices,
                "quarter_market_metrics": quarter_market_metrics,
                "price_return_since_quarter": (
                    round(price_return_since_quarter, 2)
                    if price_return_since_quarter is not None
                    else None
                )
            })
        return insights

    async def refresh_market_insights(self):
        if self.is_market_refreshing:
            return False

        symbols = self._get_high_conviction_tickers()
        if not symbols:
            logger.info("No 5%+ holdings available for OpenBB market analysis.")
            self.market_insights = {}
            self.market_last_updated = datetime.now(timezone.utc).isoformat()
            self._save_market_insights_to_disk()
            return True

        self.is_market_refreshing = True
        try:
            report_periods = [
                fund_data.get("metadata", {}).get("report_period")
                for fund_data in self.cache.values()
                if fund_data.get("status") == "loaded"
                and fund_data.get("metadata", {}).get("report_period")
            ]
            report_period = max(report_periods) if report_periods else None
            loop = asyncio.get_event_loop()
            insights = await loop.run_in_executor(
                None, self._fetch_market_insights_sync, symbols, report_period
            )
            if not insights:
                logger.warning("OpenBB returned no usable market insights; retaining the previous market cache.")
                return False

            self.market_insights = {item["ticker"]: item for item in insights}
            self.market_last_updated = datetime.now(timezone.utc).isoformat()
            self._save_market_insights_to_disk()
            return True
        finally:
            self.is_market_refreshing = False

    def get_overview(self, fund_cache=None):
        cache = fund_cache if fund_cache is not None else self.cache
        loaded = sum(1 for c in cache.values() if c["status"] == "loaded")
        tickers = set()
        total_aum_raw = 0.0

        for c in cache.values():
            if c["status"] == "loaded":
                total_aum_raw += c["metadata"].get("total_value", 0.0)
                if c["holdings"] is not None and 'Ticker' in c["holdings"].columns:
                    valid_tickers = [t for t in c["holdings"]['Ticker'].dropna().unique() if str(t).strip()]
                    tickers.update(valid_tickers)

        # Calculate QoQ move stats
        changes = self.get_qoq_changes(fund_cache=cache)
        new_count = sum(1 for c in changes if c["status"] == "NEW")
        increased_count = sum(1 for c in changes if c["status"] == "INCREASED")
        decreased_count = sum(1 for c in changes if c["status"] == "DECREASED")
        closed_count = sum(1 for c in changes if c["status"] == "CLOSED")

        return {
            "total_funds": len(cache),
            "loaded_funds": loaded,
            "total_tickers": len(tickers),
            "total_aum_m": round(total_aum_raw / 1_000_000.0, 2),
            "total_aum_b": round(total_aum_raw / 1_000_000_000.0, 2),
            "moves_summary": {
                "new": new_count,
                "increased": increased_count,
                "decreased": decreased_count,
                "closed": closed_count,
                "total_moves": len(changes)
            },
            "last_updated": max(
                (c.get("last_updated") or "" for c in cache.values()),
                default=""
            ) or self.last_updated,
            "is_refreshing": self.is_refreshing if cache is self.cache else False
        }

    def get_qoq_changes(self, include_unchanged=False, fund_cache=None, ticker=None):
        cache = fund_cache if fund_cache is not None else self.cache
        target_ticker = ticker.strip().upper() if ticker else None
        changes = []
        for c in cache.values():
            if c["status"] == "loaded" and c["comparison"] is not None and not c["comparison"].empty:
                comp_df = c["comparison"]
                hold_df = c["holdings"]
                previous_total_value = (
                    float(pd.to_numeric(comp_df["PrevValue"], errors="coerce").fillna(0.0).sum())
                    if "PrevValue" in comp_df.columns
                    else 0.0
                )
                previous_position_values = (
                    pd.to_numeric(comp_df["PrevValue"], errors="coerce")
                    .dropna()
                    .loc[lambda values: values > 0]
                    if "PrevValue" in comp_df.columns
                    else pd.Series(dtype=float)
                )
                manager_typical_position_weight = (
                    float(statistics.median(
                        (previous_position_values / previous_total_value * 100.0).tolist()
                    ))
                    if previous_total_value > 0 and not previous_position_values.empty
                    else None
                )
                manager_typical_share_change_pct = None
                if {"Shares", "PrevShares"}.issubset(comp_df.columns):
                    comparison_shares = pd.to_numeric(
                        comp_df["Shares"],
                        errors="coerce"
                    )
                    comparison_previous_shares = pd.to_numeric(
                        comp_df["PrevShares"],
                        errors="coerce"
                    )
                    continuing_adjustments = (
                        (
                            (
                                comparison_shares
                                - comparison_previous_shares
                            )
                            / comparison_previous_shares
                            * 100.0
                        )
                        .where(
                            (comparison_shares > 0)
                            & (comparison_previous_shares > 0)
                            & (
                                comparison_shares
                                != comparison_previous_shares
                            )
                        )
                        .abs()
                        .dropna()
                    )
                    if not continuing_adjustments.empty:
                        manager_typical_share_change_pct = float(
                            statistics.median(
                                continuing_adjustments.tolist()
                            )
                        )

                # Merge logic to get current portfolio weight if available
                if hold_df is not None and not hold_df.empty and 'Cusip' in hold_df.columns:
                    weight_sub = hold_df[['Cusip', 'PortfolioWeight']].drop_duplicates(subset=['Cusip'])
                    merged = pd.merge(comp_df, weight_sub, on='Cusip', how='left')
                else:
                    merged = comp_df.copy()
                    merged['PortfolioWeight'] = 0.0

                for _, row in merged.iterrows():
                    status = str(row.get('Status', '')).strip().upper()
                    allowed_statuses = ['NEW', 'CLOSED', 'INCREASED', 'DECREASED']
                    if include_unchanged:
                        allowed_statuses.append('UNCHANGED')
                    if status not in allowed_statuses:
                        continue

                    ticker = str(row.get('Ticker', '')).strip().upper()
                    if not ticker or ticker == 'NAN' or ticker == 'NONE':
                        continue
                    if target_ticker and ticker != target_ticker:
                        continue

                    val_raw = float(row.get('Value', 0.0)) if pd.notna(row.get('Value')) else 0.0
                    prev_val_raw = float(row.get('PrevValue', 0.0)) if pd.notna(row.get('PrevValue')) else 0.0
                    val_change_raw = (
                        float(row.get('ValueChange'))
                        if pd.notna(row.get('ValueChange'))
                        else val_raw - prev_val_raw
                    )
                    value_change_pct = (
                        float(row.get('ValueChangePct'))
                        if pd.notna(row.get('ValueChangePct'))
                        else (
                            (val_change_raw / prev_val_raw) * 100.0
                            if prev_val_raw > 0
                            else 0.0
                        )
                    )
                    shares = int(row.get('Shares', 0)) if pd.notna(row.get('Shares')) else 0
                    prev_shares = int(row.get('PrevShares', 0)) if pd.notna(row.get('PrevShares')) else 0
                    shares_change = (
                        float(row.get('ShareChange'))
                        if pd.notna(row.get('ShareChange'))
                        else float(shares - prev_shares)
                    )
                    shares_change_pct = (
                        float(row.get('ShareChangePct'))
                        if pd.notna(row.get('ShareChangePct'))
                        else (
                            (shares_change / prev_shares) * 100.0
                            if prev_shares > 0
                            else 0.0
                        )
                    )
                    portfolio_weight = float(row.get('PortfolioWeight', 0.0)) if pd.notna(row.get('PortfolioWeight')) else 0.0
                    previous_portfolio_weight = (
                        (prev_val_raw / previous_total_value) * 100.0
                        if previous_total_value > 0 and prev_val_raw > 0
                        else 0.0
                    )

                    changes.append({
                        "fund_name": c["fund_info"]["name"],
                        "manager": c["fund_info"]["manager"],
                        "group": c["fund_info"]["group"],
                        "annotation": c["fund_info"].get("annotation", ""),
                        "cik": c["fund_info"]["cik"],
                        "ticker": ticker,
                        "issuer": str(row.get('Issuer', '')).title(),
                        "status": status,
                        "value_change": round(val_change_raw / 1_000_000.0, 2), # In $M
                        "value_change_pct": round(value_change_pct, 2),
                        "shares": shares,
                        "prev_shares": prev_shares,
                        "shares_change": shares_change,
                        "shares_change_pct": round(shares_change_pct, 2),
                        "portfolio_weight": round(portfolio_weight, 2),
                        "previous_portfolio_weight": round(previous_portfolio_weight, 2),
                        "portfolio_weight_change": round(
                            portfolio_weight - previous_portfolio_weight,
                            2
                        ),
                        "portfolio_weight_change_raw": (
                            portfolio_weight - previous_portfolio_weight
                        ),
                        "previous_portfolio_value_raw": previous_total_value,
                        "manager_typical_position_weight": (
                            round(manager_typical_position_weight, 4)
                            if manager_typical_position_weight is not None
                            else None
                        ),
                        "manager_typical_share_change_pct": (
                            round(manager_typical_share_change_pct, 4)
                            if manager_typical_share_change_pct is not None
                            else None
                        ),
                        "value": round(val_raw / 1_000_000.0, 2), # In $M
                        "prev_value": round(prev_val_raw / 1_000_000.0, 2), # In $M
                        "report_period": c["metadata"].get("report_period", "")
                    })

        changes.sort(key=lambda x: abs(x["value_change"]), reverse=True)
        return changes

    def get_two_quarter_buys(self):
        aggregates = {}

        for fund_data in self.cache.values():
            if fund_data["status"] != "loaded":
                continue

            holdings = fund_data.get("holdings")
            current_weights = {}
            if holdings is not None and not holdings.empty and {"Ticker", "PortfolioWeight"}.issubset(holdings.columns):
                weight_frame = holdings[["Ticker", "PortfolioWeight"]].copy()
                weight_frame["Ticker"] = weight_frame["Ticker"].fillna("").astype(str).str.strip().str.upper()
                weight_frame["PortfolioWeight"] = pd.to_numeric(weight_frame["PortfolioWeight"], errors="coerce").fillna(0)
                current_weights = weight_frame.groupby("Ticker")["PortfolioWeight"].max().to_dict()

            comparisons = [
                (fund_data.get("comparison"), fund_data["metadata"].get("report_period", "")),
                (fund_data.get("previous_comparison"), fund_data["metadata"].get("previous_report_period", ""))
            ]

            for comparison, period in comparisons:
                if comparison is None or comparison.empty:
                    continue

                for _, row in comparison.iterrows():
                    status = str(row.get("Status", "")).strip().upper()
                    if status not in {"NEW", "INCREASED"}:
                        continue

                    ticker = str(row.get("Ticker", "")).strip().upper()
                    if not ticker or ticker in {"NAN", "NONE"}:
                        continue

                    value_change = float(row.get("ValueChange", 0.0)) if pd.notna(row.get("ValueChange")) else 0.0
                    if value_change <= 0:
                        continue

                    aggregate = aggregates.setdefault(ticker, {
                        "ticker": ticker,
                        "issuer": str(row.get("Issuer", "")).title(),
                        "value_change": 0.0,
                        "buy_events": 0,
                        "buyers": set(),
                        "periods": set(),
                        "max_portfolio_weight": 0.0,
                        "max_weight_manager": "",
                        "max_weight_cik": ""
                    })
                    aggregate["value_change"] += value_change / 1_000_000.0
                    aggregate["buy_events"] += 1
                    aggregate["buyers"].add(fund_data["fund_info"]["cik"])
                    if period:
                        aggregate["periods"].add(period)

                    current_weight = float(current_weights.get(ticker, 0.0))
                    if current_weight > aggregate["max_portfolio_weight"]:
                        aggregate["max_portfolio_weight"] = current_weight
                        aggregate["max_weight_manager"] = fund_data["fund_info"]["manager"]
                        aggregate["max_weight_cik"] = fund_data["fund_info"]["cik"]

        result = []
        for aggregate in aggregates.values():
            result.append({
                "ticker": aggregate["ticker"],
                "issuer": aggregate["issuer"],
                "value_change": round(aggregate["value_change"], 2),
                "buy_events": aggregate["buy_events"],
                "buyer_count": len(aggregate["buyers"]),
                "periods": sorted(aggregate["periods"], reverse=True),
                "max_portfolio_weight": round(aggregate["max_portfolio_weight"], 2),
                "max_weight_manager": aggregate["max_weight_manager"],
                "max_weight_cik": aggregate["max_weight_cik"]
            })

        result.sort(key=lambda item: item["value_change"], reverse=True)
        return result

    def get_near_52_week_low(self, fund_cache=None):
        cache = fund_cache if fund_cache is not None else self.cache
        ticker_view = {
            item["ticker"]: item
            for item in self.get_ticker_view(fund_cache=cache)
        }
        result = []

        for ticker, market_data in self.market_insights.items():
            ticker_data = ticker_view.get(ticker)
            if not ticker_data:
                continue

            max_weight = max(
                (holder["portfolio_weight"] for holder in ticker_data["holders"]),
                default=0.0
            )
            if max_weight < 5.0:
                continue

            period_metrics = {
                "current_price": market_data.get("current_price"),
                "low_52_week": market_data.get("low_52_week"),
                "low_52_week_date": market_data.get("low_52_week_date"),
                "pct_above_low": market_data.get("pct_above_low")
            }
            if (
                any(period_metrics.get(field) is None for field in (
                    "current_price",
                    "low_52_week",
                    "pct_above_low"
                ))
            ):
                continue

            result.append({
                "ticker": ticker,
                **period_metrics,
                "price_as_of": market_data.get("price_as_of"),
                "issuer": ticker_data["issuer"],
                "ownership_count": ticker_data["num_holders"],
                "max_portfolio_weight": round(max_weight, 2)
            })

        result.sort(key=lambda item: item["pct_above_low"])
        return result

    def get_ticker_view(self, ticker=None, fund_cache=None):
        cache = fund_cache if fund_cache is not None else self.cache
        report_periods = [
            fund_data.get("metadata", {}).get("report_period")
            for fund_data in cache.values()
            if fund_data.get("status") == "loaded"
            and fund_data.get("metadata", {}).get("report_period")
        ]
        selected_report_period = max(report_periods) if report_periods else None
        tickers_data = {}
        previous_weights = {}
        qoq_actions = {}

        for c in cache.values():
            if c["status"] == "loaded" and c["holdings"] is not None and not c["holdings"].empty:
                hold_df = c["holdings"]
                comp_df = c["comparison"]
                fund_prior_weights = {}

                if (
                    comp_df is not None
                    and not comp_df.empty
                    and {"Ticker", "PrevValue"}.issubset(comp_df.columns)
                ):
                    prior_frame = comp_df[["Ticker", "PrevValue"]].copy()
                    prior_frame["Ticker"] = prior_frame["Ticker"].astype(str).str.strip().str.upper()
                    prior_frame["PrevValue"] = pd.to_numeric(
                        prior_frame["PrevValue"], errors="coerce"
                    ).fillna(0.0)
                    prior_total_value = prior_frame["PrevValue"].sum()

                    if prior_total_value > 0:
                        prior_frame = prior_frame[
                            ~prior_frame["Ticker"].isin(["", "NAN", "NONE"])
                            & (prior_frame["PrevValue"] > 0)
                        ]
                        prior_ticker_values = prior_frame.groupby("Ticker")["PrevValue"].sum()
                        for prior_ticker, prior_value in prior_ticker_values.items():
                            prior_weight = (
                                float(prior_value) / float(prior_total_value) * 100.0
                            )
                            fund_prior_weights[prior_ticker] = prior_weight
                            previous_weights.setdefault(prior_ticker, []).append(
                                prior_weight
                            )

                if (
                    comp_df is not None
                    and not comp_df.empty
                    and {"Ticker", "Shares", "PrevShares"}.issubset(comp_df.columns)
                ):
                    action_frame = comp_df[["Ticker", "Shares", "PrevShares"]].copy()
                    action_frame["Ticker"] = (
                        action_frame["Ticker"].astype(str).str.strip().str.upper()
                    )
                    action_frame["Shares"] = pd.to_numeric(
                        action_frame["Shares"], errors="coerce"
                    ).fillna(0.0)
                    action_frame["PrevShares"] = pd.to_numeric(
                        action_frame["PrevShares"], errors="coerce"
                    ).fillna(0.0)
                    action_frame = action_frame[
                        ~action_frame["Ticker"].isin(["", "NAN", "NONE"])
                    ]

                    for action_ticker, action_rows in action_frame.groupby("Ticker"):
                        current_shares = float(action_rows["Shares"].sum())
                        prior_shares = float(action_rows["PrevShares"].sum())
                        if current_shares > 0 and prior_shares <= 0:
                            action = "new"
                        elif current_shares <= 0 and prior_shares > 0:
                            action = "closed"
                        elif current_shares > prior_shares:
                            action = "increased"
                        elif current_shares < prior_shares:
                            action = "decreased"
                        else:
                            action = "unchanged"

                        counts = qoq_actions.setdefault(
                            action_ticker,
                            {
                                "increased": 0,
                                "decreased": 0,
                                "new": 0,
                                "closed": 0,
                                "unchanged": 0
                            }
                        )
                        counts[action] += 1

                if 'Cusip' in hold_df.columns and comp_df is not None and not comp_df.empty and 'Cusip' in comp_df.columns:
                    comp_sub = comp_df[['Cusip', 'Status', 'ValueChangePct', 'ShareChangePct', 'ValueChange', 'ShareChange']].drop_duplicates(subset=['Cusip'])
                    merged = pd.merge(hold_df, comp_sub, on='Cusip', how='left')
                else:
                    merged = hold_df.copy()
                    merged['Status'] = 'UNCHANGED'
                    merged['ValueChangePct'] = 0.0
                    merged['ShareChangePct'] = 0.0
                    merged['ValueChange'] = 0.0
                    merged['ShareChange'] = 0.0

                for _, row in merged.iterrows():
                    t = str(row.get('Ticker', '')).strip().upper()
                    if not t or t == 'NAN' or t == 'NONE':
                        continue

                    if ticker and t != ticker.strip().upper():
                        continue

                    if t not in tickers_data:
                        tickers_data[t] = {
                            "ticker": t,
                            "issuer": str(row.get('Issuer', '')).title(),
                            "num_holders": 0,
                            "total_value_across_funds": 0.0,
                            "total_shares": 0,
                            "holders": []
                        }

                    val_m = float(row.get('Value', 0.0)) / 1_000_000.0 # $M
                    shares_cnt = int(row.get('SharesPrnAmount', 0)) if pd.notna(row.get('SharesPrnAmount')) else 0
                    tickers_data[t]["total_value_across_funds"] += val_m
                    tickers_data[t]["total_shares"] += shares_cnt
                    tickers_data[t]["num_holders"] += 1

                    status_val = str(row.get('Status', 'UNCHANGED')).strip().upper() if pd.notna(row.get('Status')) else 'UNCHANGED'
                    if status_val not in ['NEW', 'CLOSED', 'INCREASED', 'DECREASED', 'UNCHANGED']:
                        status_val = 'UNCHANGED'

                    portfolio_weight = round(float(row.get('PortfolioWeight', 0.0)), 2) if pd.notna(row.get('PortfolioWeight')) else 0.0
                    previous_portfolio_weight = fund_prior_weights.get(t, 0.0)

                    tickers_data[t]["holders"].append({
                        "fund_name": c["fund_info"]["name"],
                        "manager": c["fund_info"]["manager"],
                        "group": c["fund_info"]["group"],
                        "annotation": c["fund_info"].get("annotation", ""),
                        "cik": c["fund_info"]["cik"],
                        "portfolio_weight": portfolio_weight,
                        "previous_portfolio_weight": round(previous_portfolio_weight, 2),
                        "portfolio_weight_change": (
                            round(portfolio_weight - previous_portfolio_weight, 2)
                            if previous_portfolio_weight > 0
                            else None
                        ),
                        "value": round(val_m, 2),
                        "shares": shares_cnt,
                        "status": status_val,
                        "value_change": round(float(row.get('ValueChange', 0.0)) / 1_000_000.0, 2) if pd.notna(row.get('ValueChange')) else 0.0,
                        "value_change_pct": round(float(row.get('ValueChangePct', 0.0)), 2) if pd.notna(row.get('ValueChangePct')) else 0.0,
                        "shares_change_pct": round(float(row.get('ShareChangePct', 0.0)), 2) if pd.notna(row.get('ShareChangePct')) else 0.0,
                        "report_period": c["metadata"].get("report_period", "")
                    })

        # Round aggregate metrics & sort holders
        for t_info in tickers_data.values():
            t_info["total_value_across_funds"] = round(t_info["total_value_across_funds"], 2)
            t_info["holders"].sort(key=lambda h: h["value"], reverse=True)
            prior_weights = previous_weights.get(t_info["ticker"], [])
            action_counts = qoq_actions.get(
                t_info["ticker"],
                {
                    "increased": 0,
                    "decreased": 0,
                    "new": 0,
                    "closed": 0,
                    "unchanged": 0
                }
            )
            t_info["qoq_actions"] = action_counts
            comparable_current_holders = (
                action_counts["increased"]
                + action_counts["decreased"]
                + action_counts["new"]
                + action_counts["unchanged"]
            )
            comparable_previous_holders = (
                action_counts["increased"]
                + action_counts["decreased"]
                + action_counts["closed"]
                + action_counts["unchanged"]
            )
            t_info["previous_num_holders"] = comparable_previous_holders
            t_info["holder_count_change"] = (
                action_counts["new"] - action_counts["closed"]
            )
            t_info["qoq_comparable_holders"] = comparable_current_holders
            t_info["qoq_unavailable_holders"] = max(
                t_info["num_holders"] - comparable_current_holders,
                0
            )

            if t_info["num_holders"] > 0:
                weights = [h["portfolio_weight"] for h in t_info["holders"]]
                current_avg_weight = sum(weights) / t_info["num_holders"]
                current_median_weight = statistics.median(weights)
                t_info["avg_weight"] = round(current_avg_weight, 2)
                # Median resists the skew a single concentrated holder puts on the mean.
                t_info["median_weight"] = round(current_median_weight, 2)
            else:
                current_avg_weight = 0.0
                current_median_weight = 0.0
                t_info["avg_weight"] = 0.0
                t_info["median_weight"] = 0.0

            if prior_weights:
                previous_avg_weight = sum(prior_weights) / len(prior_weights)
                previous_median_weight = statistics.median(prior_weights)
                t_info["previous_avg_weight"] = round(previous_avg_weight, 2)
                t_info["previous_median_weight"] = round(previous_median_weight, 2)
                t_info["avg_weight_change"] = round(
                    current_avg_weight - previous_avg_weight, 2
                )
                t_info["median_weight_change"] = round(
                    current_median_weight - previous_median_weight, 2
                )
            else:
                t_info["previous_avg_weight"] = 0.0
                t_info["previous_median_weight"] = 0.0
                t_info["avg_weight_change"] = t_info["avg_weight"]
                t_info["median_weight_change"] = t_info["median_weight"]

            continuing_weight_changes = [
                holder["portfolio_weight_change"]
                for holder in t_info["holders"]
                if holder["portfolio_weight_change"] is not None
            ]
            t_info["median_position_change"] = (
                round(statistics.median(continuing_weight_changes), 2)
                if continuing_weight_changes
                else None
            )

            market_data = self.market_insights.get(t_info["ticker"], {})
            quarter_end_price = market_data.get("quarter_end_prices", {}).get(
                selected_report_period
            )
            if quarter_end_price is None and (
                market_data.get("quarter_end_period") == selected_report_period
            ):
                quarter_end_price = market_data.get("quarter_end_price")
            current_price = market_data.get("current_price")
            t_info["quarter_end_price"] = quarter_end_price
            t_info["quarter_end_period"] = selected_report_period
            t_info["current_price"] = market_data.get("current_price")
            t_info["price_as_of"] = market_data.get("price_as_of")
            t_info["price_return_since_quarter"] = (
                round(((current_price / quarter_end_price) - 1) * 100, 2)
                if current_price is not None
                and quarter_end_price is not None
                and quarter_end_price > 0
                else None
            )
            t_info["holders_summary"] = ", ".join(h["manager"] for h in t_info["holders"])

        if ticker:
            t_upper = ticker.strip().upper()
            return tickers_data.get(t_upper, None)

        result_list = list(tickers_data.values())
        result_list.sort(key=lambda x: (x["num_holders"], x["total_value_across_funds"]), reverse=True)
        return result_list

    def _get_holding_market_context(self, ticker, reported_price):
        market_data = self.market_insights.get(ticker, {})
        ticker_market = (
            self.ticker_market_cache.get(ticker)
            or self._load_ticker_market_data_from_disk(ticker)
            or {}
        )
        quote = ticker_market.get("quote", {})
        current_price = (
            market_data.get("current_price")
            if market_data.get("current_price") is not None
            else quote.get("last_price")
        )
        low_52_week = (
            market_data.get("low_52_week")
            if market_data.get("low_52_week") is not None
            else quote.get("year_low")
        )
        pct_above_low = market_data.get("pct_above_low")
        if (
            pct_above_low is None
            and current_price is not None
            and low_52_week is not None
            and float(low_52_week) > 0
        ):
            pct_above_low = (
                (float(current_price) / float(low_52_week)) - 1
            ) * 100
        current_vs_reported_pct = (
            ((float(current_price) / float(reported_price)) - 1) * 100
            if current_price is not None
            and reported_price is not None
            and float(reported_price) > 0
            else None
        )
        return {
            "reported_price": (
                round(float(reported_price), 2)
                if reported_price is not None
                else None
            ),
            "current_price": (
                round(float(current_price), 2)
                if current_price is not None
                else None
            ),
            "current_vs_reported_pct": (
                round(current_vs_reported_pct, 2)
                if current_vs_reported_pct is not None
                else None
            ),
            "low_52_week": (
                round(float(low_52_week), 2)
                if low_52_week is not None
                else None
            ),
            "low_52_week_date": (
                market_data.get("low_52_week_date")
                or quote.get("year_low_date")
            ),
            "pct_above_low": (
                round(float(pct_above_low), 2)
                if pct_above_low is not None
                else None
            ),
            "market_price_as_of": (
                market_data.get("price_as_of")
                or ticker_market.get("price_as_of")
            )
        }

    def get_investor_view(self, cik=None):
        if cik:
            c = self.cache.get(cik)
            if not c:
                return None
            if c["status"] != "loaded":
                return {
                    "fund_info": c["fund_info"],
                    "status": c["status"],
                    "metadata": c.get("metadata", {}),
                    "holdings_list": [],
                    "closed_list": [],
                    "stats": {}
                }

            holdings = []
            closed = []

            hold_df = c["holdings"]
            comp_df = c["comparison"]

            if hold_df is not None and not hold_df.empty:
                if comp_df is not None and not comp_df.empty and 'Cusip' in comp_df.columns:
                    comp_sub = comp_df[['Cusip', 'Status', 'ValueChangePct', 'ShareChangePct', 'ValueChange', 'ShareChange', 'PrevValue', 'PrevShares']].drop_duplicates(subset=['Cusip'])
                    merged = pd.merge(hold_df, comp_sub, on='Cusip', how='left')
                else:
                    merged = hold_df.copy()
                    merged['Status'] = 'UNCHANGED'
                    merged['ValueChangePct'] = 0.0
                    merged['ShareChangePct'] = 0.0
                    merged['ValueChange'] = 0.0
                    merged['PrevValue'] = merged['Value']
                    merged['PrevShares'] = merged['SharesPrnAmount']

                for _, row in merged.iterrows():
                    t = str(row.get('Ticker', '')).strip().upper()
                    if not t or t == 'NAN' or t == 'NONE':
                        continue

                    status_val = str(row.get('Status', 'UNCHANGED')).strip().upper() if pd.notna(row.get('Status')) else 'UNCHANGED'
                    if status_val not in ['NEW', 'CLOSED', 'INCREASED', 'DECREASED', 'UNCHANGED']:
                        status_val = 'UNCHANGED'

                    value_raw = float(row.get('Value', 0.0))
                    shares = int(row.get('SharesPrnAmount', 0)) if pd.notna(row.get('SharesPrnAmount')) else 0
                    reported_price = (
                        value_raw / shares
                        if shares > 0
                        else None
                    )
                    holdings.append({
                        "ticker": t,
                        "issuer": str(row.get('Issuer', '')).title(),
                        "cusip": str(row.get('Cusip', '')),
                        "portfolio_weight": round(float(row.get('PortfolioWeight', 0.0)), 2) if pd.notna(row.get('PortfolioWeight')) else 0.0,
                        "value": round(value_raw / 1_000_000.0, 2), # $M
                        "shares": shares,
                        "status": status_val,
                        "value_change": round(float(row.get('ValueChange', 0.0)) / 1_000_000.0, 2) if pd.notna(row.get('ValueChange')) else 0.0,
                        "value_change_pct": round(float(row.get('ValueChangePct', 0.0)), 2) if pd.notna(row.get('ValueChangePct')) else 0.0,
                        "shares_change": float(row.get('ShareChange', 0.0)) if pd.notna(row.get('ShareChange')) else 0.0,
                        "shares_change_pct": round(float(row.get('ShareChangePct', 0.0)), 2) if pd.notna(row.get('ShareChangePct')) else 0.0,
                        "prev_value": round(float(row.get('PrevValue', 0.0)) / 1_000_000.0, 2) if pd.notna(row.get('PrevValue')) else 0.0,
                        "prev_shares": int(row.get('PrevShares', 0)) if pd.notna(row.get('PrevShares')) else 0,
                        **self._get_holding_market_context(
                            t,
                            reported_price
                        )
                    })

            # Check for CLOSED positions in comp_df
            if comp_df is not None and not comp_df.empty and 'Status' in comp_df.columns:
                closed_rows = comp_df[comp_df['Status'] == 'CLOSED']
                for _, row in closed_rows.iterrows():
                    t = str(row.get('Ticker', '')).strip().upper()
                    if not t or t == 'NAN' or t == 'NONE':
                        continue
                    prev_value_raw = float(row.get('PrevValue', 0.0)) if pd.notna(row.get('PrevValue')) else 0.0
                    prev_shares = int(row.get('PrevShares', 0)) if pd.notna(row.get('PrevShares')) else 0
                    reported_price = (
                        prev_value_raw / prev_shares
                        if prev_shares > 0
                        else None
                    )
                    closed.append({
                        "ticker": t,
                        "issuer": str(row.get('Issuer', '')).title(),
                        "cusip": str(row.get('Cusip', '')),
                        "portfolio_weight": 0.0,
                        "value": 0.0,
                        "shares": 0,
                        "status": "CLOSED",
                        "value_change": round(float(row.get('ValueChange', 0.0)) / 1_000_000.0, 2) if pd.notna(row.get('ValueChange')) else 0.0,
                        "value_change_pct": -100.0,
                        "shares_change_pct": -100.0,
                        "prev_value": round(prev_value_raw / 1_000_000.0, 2),
                        "prev_shares": prev_shares,
                        **self._get_holding_market_context(
                            t,
                            reported_price
                        )
                    })

            # Sort active holdings by portfolio weight descending
            holdings.sort(key=lambda x: x["portfolio_weight"], reverse=True)

            # Calculate concentration stats
            top5_w = round(sum(h["portfolio_weight"] for h in holdings[:5]), 2)
            top10_w = round(sum(h["portfolio_weight"] for h in holdings[:10]), 2)

            status_counts = {
                "NEW": sum(1 for h in holdings if h["status"] == "NEW"),
                "INCREASED": sum(1 for h in holdings if h["status"] == "INCREASED"),
                "DECREASED": sum(1 for h in holdings if h["status"] == "DECREASED"),
                "UNCHANGED": sum(1 for h in holdings if h["status"] == "UNCHANGED"),
                "CLOSED": len(closed)
            }

            total_val_m = round(c["metadata"].get("total_value", 0.0) / 1_000_000.0, 2)
            total_val_b = round(c["metadata"].get("total_value", 0.0) / 1_000_000_000.0, 2)
            market_price_dates = [
                holding["market_price_as_of"]
                for holding in [*holdings, *closed]
                if holding.get("market_price_as_of")
            ]

            return {
                "fund_info": c["fund_info"],
                "status": c["status"],
                "metadata": {
                    **c["metadata"],
                    "total_value_m": total_val_m,
                    "total_value_b": total_val_b
                },
                "stats": {
                    "top5_weight": top5_w,
                    "top10_weight": top10_w,
                    "status_counts": status_counts,
                    "active_holdings_count": len(holdings),
                    "closed_holdings_count": len(closed),
                    "market_price_as_of": (
                        max(market_price_dates)
                        if market_price_dates
                        else None
                    )
                },
                "holdings_list": holdings,
                "closed_list": closed,
                "last_updated": c["last_updated"]
            }

        else:
            return self.get_fund_status()

    async def get_investor_history(self, cik):
        current_fund = self.cache.get(cik)
        if not current_fund:
            return None

        activity = []
        portfolio_history = []
        for period in self.get_available_periods(count=20):
            period_cache = await self.get_period_cache(period)
            fund_data = period_cache.get(cik)
            if not fund_data or fund_data.get("status") != "loaded":
                continue

            holdings_frame = fund_data.get("holdings")
            top_holdings = []
            if (
                holdings_frame is not None
                and not holdings_frame.empty
                and {"Ticker", "Issuer", "PortfolioWeight", "Value"}.issubset(
                    holdings_frame.columns
                )
            ):
                ranked = holdings_frame.copy()
                ranked["Ticker"] = (
                    ranked["Ticker"].astype(str).str.strip().str.upper()
                )
                ranked["PortfolioWeight"] = pd.to_numeric(
                    ranked["PortfolioWeight"],
                    errors="coerce"
                ).fillna(0.0)
                ranked["Value"] = pd.to_numeric(
                    ranked["Value"],
                    errors="coerce"
                ).fillna(0.0)
                ranked = ranked[
                    ~ranked["Ticker"].isin(["", "NAN", "NONE"])
                ].sort_values(
                    ["PortfolioWeight", "Value"],
                    ascending=False
                )
                top_holdings = [
                    {
                        "ticker": row["Ticker"],
                        "issuer": str(row["Issuer"]).title(),
                        "portfolio_weight": round(
                            float(row["PortfolioWeight"]),
                            2
                        ),
                        "value": round(float(row["Value"]) / 1_000_000.0, 2)
                    }
                    for _, row in ranked.head(20).iterrows()
                ]

            total_value = float(
                fund_data.get("metadata", {}).get("total_value", 0.0)
            )
            portfolio_history.append({
                "period": period,
                "filing_date": fund_data.get("metadata", {}).get(
                    "filing_date",
                    ""
                ),
                "portfolio_value_m": round(total_value / 1_000_000.0, 2),
                "portfolio_value_b": round(total_value / 1_000_000_000.0, 2),
                "position_count": (
                    len(holdings_frame)
                    if holdings_frame is not None
                    else 0
                ),
                "top_holdings": top_holdings
            })

            period_changes = self.get_qoq_changes(
                fund_cache={cik: fund_data}
            )
            period_changes.sort(
                key=lambda change: (
                    abs(change.get("portfolio_weight_change", 0.0)),
                    abs(change.get("shares_change", 0.0))
                ),
                reverse=True
            )
            activity.append({
                "period": period,
                "filing_date": fund_data.get("metadata", {}).get(
                    "filing_date",
                    ""
                ),
                "changes": [
                    {
                        "ticker": change["ticker"],
                        "issuer": change["issuer"],
                        "status": change["status"],
                        "shares_change": change["shares_change"],
                        "shares_change_pct": change["shares_change_pct"],
                        "portfolio_weight_change": change[
                            "portfolio_weight_change"
                        ],
                        "value_change": change["value_change"]
                    }
                    for change in period_changes
                ]
            })

        return {
            "fund_info": current_fund["fund_info"],
            "activity": activity,
            "portfolio_history": portfolio_history
        }

    def get_fund_status(self, fund_cache=None):
        cache = fund_cache if fund_cache is not None else self.cache
        res = []
        for c in cache.values():
            val_raw = c["metadata"].get("total_value", 0.0)
            top_holdings = []
            if c["holdings"] is not None and not c["holdings"].empty and 'Ticker' in c["holdings"].columns:
                valid = c["holdings"][c["holdings"]['Ticker'].str.len() > 0]
                if not valid.empty:
                    top_holdings = valid.sort_values(by='Value', ascending=False)['Ticker'].head(3).tolist()

            res.append({
                "cik": c["fund_info"]["cik"],
                "name": c["fund_info"]["name"],
                "manager": c["fund_info"]["manager"],
                "group": c["fund_info"]["group"],
                "annotation": c["fund_info"].get("annotation", ""),
                "is_exception": c["fund_info"].get("is_exception", False),
                "roster_reason": c["fund_info"].get("roster_reason", ""),
                "status": c["status"],
                "report_period": c["metadata"].get("report_period", ""),
                "total_value": round(val_raw / 1_000_000.0, 2), # In $M
                "total_value_b": round(val_raw / 1_000_000_000.0, 2), # In $B
                "total_holdings": c["metadata"].get("total_holdings", 0),
                "top_holdings": top_holdings,
                "last_updated": c["last_updated"]
            })
        return res
