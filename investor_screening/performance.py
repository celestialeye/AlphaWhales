from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from openbb_core.provider.utils.errors import EmptyDataError, OpenBBError

from config import FUND_MANAGERS
from .database import DEFAULT_DATABASE_PATH, DEFAULT_DATA_DIR
from .screener import (
    DEFAULT_SNAPSHOT_POINTER,
    FUND_LIKE_PATTERN,
    compute_source_fingerprint,
    resolve_snapshot_path,
)


DEFAULT_PERFORMANCE_PATH = DEFAULT_DATA_DIR / "performance.duckdb"
DEFAULT_PRICE_DIR = DEFAULT_DATA_DIR / "performance" / "prices"
PERFORMANCE_LABEL = (
    "Hypothetical disclosure-lagged reported 13F long-sleeve estimate"
)
PERFORMANCE_DISCLAIMER = "Not a fund or account return."
MAPPING_SOURCE = "edgar.reference.tickers.cusip_ticker_mapping"
METHODOLOGY_VERSION = "13f-disclosure-lag-v1"
MINIMUM_COVERAGE = 0.95
MAX_PRICE_BATCH_SIZE = 30
MAX_FORWARD_FILL_SESSIONS = 5
MAX_MANAGER_ATTEMPTS = 3
NON_COMMON_INSTRUMENT_PATTERN = re.compile(
    r"\b(WARRANTS?|RIGHTS?|UNITS?|PREFERRED|PFD|NOTES?|BONDS?|"
    r"DEBENTURES?|DEBT|CONVERTIBLE)\b",
    re.IGNORECASE,
)


PERFORMANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS performance_runs (
    run_id VARCHAR PRIMARY KEY,
    status VARCHAR NOT NULL,
    methodology_version VARCHAR NOT NULL,
    label VARCHAR NOT NULL,
    disclaimer VARCHAR NOT NULL,
    screening_source_fingerprint VARCHAR NOT NULL,
    screening_generation VARCHAR NOT NULL,
    source_database VARCHAR NOT NULL,
    requested_as_of DATE NOT NULL,
    latest_end_date DATE,
    window_years INTEGER NOT NULL,
    minimum_size_billions DOUBLE,
    cost_bps DOUBLE NOT NULL,
    manager_count INTEGER NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS performance_manager_state (
    run_id VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    manager_name VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error VARCHAR,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, cik)
);

CREATE TABLE IF NOT EXISTS performance_run_universe (
    run_id VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    manager_name VARCHAR NOT NULL,
    median_reported_value_4q DOUBLE NOT NULL,
    PRIMARY KEY (run_id, cik)
);

CREATE TABLE IF NOT EXISTS performance_run_mapping (
    run_id VARCHAR NOT NULL,
    cusip VARCHAR NOT NULL,
    ticker VARCHAR NOT NULL,
    market_symbol VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, cusip)
);

CREATE TABLE IF NOT EXISTS performance_run_options (
    run_id VARCHAR PRIMARY KEY,
    implicit_as_of BOOLEAN NOT NULL,
    publish_on_complete BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS performance_run_prices (
    run_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    min_date DATE NOT NULL,
    max_date DATE NOT NULL,
    parquet_path VARCHAR NOT NULL,
    parquet_sha256 VARCHAR NOT NULL,
    row_count BIGINT NOT NULL,
    PRIMARY KEY (run_id, symbol)
);

CREATE TABLE IF NOT EXISTS cusip_ticker_mapping (
    cusip VARCHAR PRIMARY KEY,
    ticker VARCHAR NOT NULL,
    market_symbol VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS price_manifest (
    symbol VARCHAR PRIMARY KEY,
    status VARCHAR NOT NULL,
    requested_start DATE NOT NULL,
    requested_end DATE NOT NULL,
    min_date DATE,
    max_date DATE,
    parquet_path VARCHAR,
    parquet_sha256 VARCHAR,
    row_count BIGINT NOT NULL,
    error VARCHAR,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS performance_events (
    run_id VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    event_index INTEGER NOT NULL,
    report_period DATE NOT NULL,
    filing_date DATE NOT NULL,
    execution_date DATE NOT NULL,
    triggering_accession VARCHAR NOT NULL,
    effective_accessions JSON NOT NULL,
    eligible_value DOUBLE NOT NULL,
    mapping_coverage DOUBLE NOT NULL,
    PRIMARY KEY (run_id, cik, event_index)
);

CREATE TABLE IF NOT EXISTS performance_event_positions (
    run_id VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    event_index INTEGER NOT NULL,
    cusip VARCHAR,
    ticker VARCHAR,
    market_symbol VARCHAR,
    reported_value DOUBLE NOT NULL
);

CREATE TABLE IF NOT EXISTS performance_intervals (
    run_id VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    interval_index INTEGER NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    cost_bps DOUBLE NOT NULL,
    status VARCHAR NOT NULL,
    mapping_coverage DOUBLE NOT NULL,
    priced_coverage DOUBLE NOT NULL,
    estimated_return DOUBLE,
    unavailable_reason VARCHAR,
    PRIMARY KEY (run_id, cik, interval_index, cost_bps)
);

CREATE TABLE IF NOT EXISTS monthly_returns (
    run_id VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    month_end DATE NOT NULL,
    cost_bps DOUBLE NOT NULL,
    estimated_return DOUBLE NOT NULL,
    spy_return DOUBLE NOT NULL,
    qqq_return DOUBLE NOT NULL,
    PRIMARY KEY (run_id, cik, month_end, cost_bps)
);

CREATE TABLE IF NOT EXISTS manager_performance (
    run_id VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    "window" VARCHAR NOT NULL,
    cost_bps DOUBLE NOT NULL,
    status VARCHAR NOT NULL,
    start_date DATE,
    end_date DATE,
    years DOUBLE,
    estimated_cagr DOUBLE,
    spy_cagr DOUBLE,
    qqq_cagr DOUBLE,
    spy_excess_cagr DOUBLE,
    qqq_excess_cagr DOUBLE,
    max_drawdown DOUBLE,
    monthly_sharpe_rf0 DOUBLE,
    spy_information_ratio DOUBLE,
    qqq_information_ratio DOUBLE,
    spy_quarterly_beat_rate DOUBLE,
    qqq_quarterly_beat_rate DOUBLE,
    mapping_coverage DOUBLE,
    priced_coverage DOUBLE,
    interval_count INTEGER NOT NULL,
    unavailable_reason VARCHAR,
    label VARCHAR NOT NULL,
    disclaimer VARCHAR NOT NULL,
    PRIMARY KEY (run_id, cik, "window", cost_bps)
);
"""


@dataclass(frozen=True)
class ManagerUniverseItem:
    cik: str
    manager_name: str
    median_reported_value_4q: float


@dataclass(frozen=True)
class EligiblePosition:
    cusip: str | None
    reported_value: float


@dataclass(frozen=True)
class PortfolioEvent:
    cik: str
    manager_name: str
    report_period: date
    filing_date: date
    triggering_accession: str
    effective_accessions: tuple[str, ...]
    positions: tuple[EligiblePosition, ...]
    execution_date: date | None = None

    @property
    def eligible_value(self) -> float:
        return sum(item.reported_value for item in self.positions)


@dataclass(frozen=True)
class IntervalResult:
    start_date: date
    end_date: date
    status: str
    mapping_coverage: float
    priced_coverage: float
    estimated_return: float | None
    unavailable_reason: str | None
    daily_nav: pd.Series | None


def connect_performance_store(
    path: str | Path = DEFAULT_PERFORMANCE_PATH,
) -> duckdb.DuckDBPyConnection:
    resolved = Path(path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(resolved))
    connection.execute(PERFORMANCE_SCHEMA)
    return connection


def normalize_cusip(value: object) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", "", str(value)).upper()
    return normalized or None


def normalize_yfinance_symbol(value: object) -> str | None:
    """Normalize symbol syntax only; this function never guesses a CUSIP mapping."""
    if value is None:
        return None
    symbol = str(value).strip().upper()
    if not symbol or symbol in {"NAN", "NONE", "NULL"}:
        return None
    symbol = symbol.replace("/", "-").replace(".", "-")
    # Edgar's current reference has historically represented these two share
    # classes both with and without a separator.
    if symbol in {"BRKA", "BRKB"}:
        symbol = f"BRK-{symbol[-1]}"
    symbol = {
        "HEIA": "HEI-A",
        "LENB": "LEN-B",
    }.get(symbol, symbol)
    return symbol


def _mapping_rows(raw: object) -> list[tuple[str, str]]:
    if isinstance(raw, Mapping):
        candidates = list(raw.items())
    elif isinstance(raw, pd.DataFrame):
        frame = raw.reset_index()
        lower_columns = {str(column).lower(): column for column in frame.columns}
        cusip_column = next(
            (
                lower_columns[name]
                for name in ("cusip", "cusip6", "cusip_number", "index")
                if name in lower_columns
            ),
            None,
        )
        ticker_column = next(
            (
                lower_columns[name]
                for name in ("ticker", "symbol")
                if name in lower_columns
            ),
            None,
        )
        if cusip_column is None or ticker_column is None:
            raise ValueError(
                "EdgarTools CUSIP mapping did not expose CUSIP and ticker columns"
            )
        candidates = list(
            frame[[cusip_column, ticker_column]].itertuples(index=False, name=None)
        )
    else:
        raise TypeError(
            "EdgarTools CUSIP mapping must be a mapping or pandas DataFrame"
        )

    rows: list[tuple[str, str]] = []
    for raw_cusip, raw_ticker in candidates:
        cusip = normalize_cusip(raw_cusip)
        ticker = None if raw_ticker is None else str(raw_ticker).strip().upper()
        market_symbol = normalize_yfinance_symbol(ticker)
        if cusip and ticker and market_symbol:
            rows.append((cusip, ticker))
    return sorted(set(rows))


def refresh_cusip_ticker_mapping(
    connection: duckdb.DuckDBPyConnection,
    *,
    mapping_loader: Callable[[], object] | None = None,
    minimum_rows: int = 1,
) -> dict[str, str]:
    if mapping_loader is None:
        from edgar.reference.tickers import cusip_ticker_mapping

        mapping_loader = cusip_ticker_mapping
    rows = _mapping_rows(mapping_loader())
    if len(rows) < minimum_rows:
        raise ValueError(
            "EdgarTools CUSIP mapping returned "
            f"{len(rows)} usable rows; expected at least {minimum_rows}"
        )
    retrieved_at = datetime.now(timezone.utc)
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute("DELETE FROM cusip_ticker_mapping")
        if rows:
            connection.executemany(
                """
                INSERT INTO cusip_ticker_mapping
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        cusip,
                        ticker,
                        normalize_yfinance_symbol(ticker),
                        MAPPING_SOURCE,
                        retrieved_at,
                    )
                    for cusip, ticker in rows
                ],
            )
        connection.execute("COMMIT")
    except (duckdb.Error, ValueError, TypeError):
        connection.execute("ROLLBACK")
        raise
    result: dict[str, str] = {}
    for cusip, ticker in rows:
        market_symbol = normalize_yfinance_symbol(ticker)
        if market_symbol is not None:
            result[cusip] = market_symbol
    return result


def load_persisted_mapping(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, str]:
    return dict(
        connection.execute(
            "SELECT cusip, market_symbol FROM cusip_ticker_mapping"
        ).fetchall()
    )


def load_manager_universe(
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_POINTER,
    *,
    minimum_size_billions: float = 10.0,
) -> tuple[list[ManagerUniverseItem], str, Path]:
    generation = resolve_snapshot_path(snapshot_path)
    connection = duckdb.connect(str(generation), read_only=True)
    try:
        if minimum_size_billions < 0:
            raise ValueError("minimum_size_billions cannot be negative")
        rows = connection.execute(
            """
            SELECT m.cik, m.manager_name, m.median_reported_value_4q
            FROM manager_metrics m
            WHERE m.median_reported_value_4q >= ?
              AND m.filing_quarters >= 12
              AND m.latest_direct_stock_value > 0
            ORDER BY median_reported_value_4q DESC, cik
            """,
            [minimum_size_billions * 1_000_000_000],
        ).fetchall()
        metadata = connection.execute(
            "SELECT source_fingerprint FROM snapshot_metadata LIMIT 1"
        ).fetchone()
        if metadata is None:
            raise ValueError("Screening snapshot has no metadata")
        return (
            [
                ManagerUniverseItem(str(cik), str(name), float(size))
                for cik, name, size in rows
            ],
            str(metadata[0]),
            generation,
        )
    finally:
        connection.close()


def _cik_identity(cik: object) -> str:
    value = str(cik).strip()
    return str(int(value)) if value.isdigit() else value.upper()


def _source_ciks_by_canonical(
    managers: Sequence[ManagerUniverseItem],
) -> tuple[dict[str, str], set[str]]:
    selected = {_cik_identity(manager.cik): manager.cik for manager in managers}
    canonical_by_source: dict[str, str] = dict(selected)
    source_values = {manager.cik for manager in managers}
    for fund in FUND_MANAGERS:
        canonical_identity = _cik_identity(fund["cik"])
        if canonical_identity not in selected:
            continue
        canonical = selected[canonical_identity]
        for source in [fund["cik"], *fund.get("historical_ciks", [])]:
            canonical_by_source[_cik_identity(source)] = canonical
            source_values.update(
                {str(source), str(source).lstrip("0") or "0", str(source).zfill(10)}
            )
    for manager in managers:
        source_values.update(
            {
                manager.cik,
                manager.cik.lstrip("0") or "0",
                manager.cik.zfill(10),
            }
        )
    return canonical_by_source, source_values


def _eligible_positions(
    accessions: Sequence[str],
    holdings_by_accession: Mapping[str, Sequence[tuple[Any, ...]]],
) -> tuple[EligiblePosition, ...]:
    values: defaultdict[str | None, float] = defaultdict(float)
    for accession in accessions:
        for cusip, issuer, title, put_call, value in holdings_by_accession.get(
            accession, ()
        ):
            if put_call is not None and str(put_call).strip():
                continue
            description = f"{issuer or ''} {title or ''}".upper()
            if re.search(FUND_LIKE_PATTERN, description):
                continue
            if NON_COMMON_INSTRUMENT_PATTERN.search(str(title or "")):
                continue
            numeric_value = float(value or 0)
            if numeric_value > 0:
                values[normalize_cusip(cusip)] += numeric_value
    return tuple(
        EligiblePosition(cusip, reported_value)
        for cusip, reported_value in sorted(
            values.items(), key=lambda item: (item[0] is None, item[0] or "")
        )
    )


def reconstruct_filing_chronology(
    connection: duckdb.DuckDBPyConnection,
    managers: Sequence[ManagerUniverseItem],
    *,
    minimum_filing_date: date | None = None,
    maximum_filing_date: date | None = None,
) -> list[PortfolioEvent]:
    """Reconstruct the portfolio known after each filing, in filing-time order."""
    if not managers:
        return []
    manager_by_cik = {manager.cik: manager for manager in managers}
    canonical_by_source, source_ciks = _source_ciks_by_canonical(managers)
    placeholders = ",".join("?" for _ in source_ciks)
    date_conditions = []
    filing_params: list[object] = sorted(source_ciks)
    if minimum_filing_date is not None:
        date_conditions.append("s.filing_date >= ?")
        filing_params.append(minimum_filing_date)
    if maximum_filing_date is not None:
        date_conditions.append("s.filing_date <= ?")
        filing_params.append(maximum_filing_date)
    date_clause = (
        " AND " + " AND ".join(date_conditions)
        if date_conditions
        else ""
    )
    filing_rows = connection.execute(
        f"""
        SELECT
            s.accession_number,
            s.cik,
            s.period_of_report,
            s.filing_date,
            upper(coalesce(cp.amendment_type, '')) AS amendment_type,
            cp.filing_manager_name
        FROM submissions s
        LEFT JOIN cover_pages cp USING (accession_number)
        WHERE s.submission_type IN ('13F-HR', '13F-HR/A')
          AND s.cik IN ({placeholders})
          {date_clause}
        ORDER BY s.filing_date, s.accession_number
        """,
        filing_params,
    ).fetchall()
    if not filing_rows:
        return []

    accessions = [str(row[0]) for row in filing_rows]
    accession_placeholders = ",".join("?" for _ in accessions)
    raw_holdings = connection.execute(
        f"""
        SELECT
            accession_number, cusip, name_of_issuer, title_of_class,
            put_call, value_usd
        FROM holdings
        WHERE accession_number IN ({accession_placeholders})
        ORDER BY accession_number, infotable_sk
        """,
        accessions,
    ).fetchall()
    holdings_by_accession: defaultdict[str, list[tuple[Any, ...]]] = defaultdict(
        list
    )
    for accession, *holding in raw_holdings:
        holdings_by_accession[str(accession)].append(tuple(holding))

    period_states: dict[
        str, dict[date, tuple[str, list[str], str]]
    ] = defaultdict(dict)
    events: list[PortfolioEvent] = []
    for (
        accession,
        source_cik,
        report_period,
        filing_date,
        amendment_type,
        filing_manager_name,
    ) in filing_rows:
        canonical = canonical_by_source.get(_cik_identity(source_cik))
        if canonical is None:
            continue
        states = period_states[canonical]
        is_addition = "NEW HOLDINGS" in str(amendment_type)
        if is_addition:
            prior = states.get(report_period)
            if prior is None:
                # An additive amendment has no meaning until its same-period base
                # has appeared in filing chronology.
                continue
            base, additions, prior_name = prior
            additions = [*additions, str(accession)]
            states[report_period] = (
                base,
                additions,
                str(filing_manager_name or prior_name),
            )
        else:
            # An original or restatement replaces the period and explicitly
            # clears additions that preceded it.
            states[report_period] = (
                str(accession),
                [],
                str(
                    filing_manager_name
                    or manager_by_cik[canonical].manager_name
                ),
            )

        active_period = max(states)
        if report_period != active_period:
            # A late amendment updates its historical period, but cannot make
            # that period the currently active disclosed portfolio.
            continue
        base, additions, state_name = states[active_period]
        effective = (base, *additions)
        events.append(
            PortfolioEvent(
                cik=canonical,
                manager_name=state_name,
                report_period=active_period,
                filing_date=filing_date,
                triggering_accession=str(accession),
                effective_accessions=effective,
                positions=_eligible_positions(
                    effective,
                    holdings_by_accession,
                ),
            )
        )
    return events


def execution_date_after(
    filing_date: date,
    spy_sessions: Sequence[date],
) -> date | None:
    return next((session for session in spy_sessions if session > filing_date), None)


def assign_and_consolidate_execution_dates(
    events: Sequence[PortfolioEvent],
    spy_sessions: Sequence[date],
) -> list[PortfolioEvent]:
    """Assign next-SPY-session execution and retain final same-date manager state."""
    final_by_key: dict[tuple[str, date], PortfolioEvent] = {}
    for event in sorted(
        events,
        key=lambda item: (
            item.filing_date,
            item.triggering_accession,
            item.report_period,
        ),
    ):
        execution_date = execution_date_after(event.filing_date, spy_sessions)
        if execution_date is not None:
            final_by_key[(event.cik, execution_date)] = replace(
                event, execution_date=execution_date
            )
    return sorted(
        final_by_key.values(),
        key=lambda item: (item.cik, item.execution_date or date.min),
    )


PriceFetcher = Callable[[Sequence[str], date, date], pd.DataFrame]


def _openbb_price_fetcher(
    symbols: Sequence[str],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    from openbb import obb

    result = obb.equity.price.historical(
        symbol=list(symbols),
        start_date=start_date,
        end_date=end_date,
        provider="yfinance",
        interval="1d",
        adjustment="splits_and_dividends",
    )
    frame = result.to_df()
    if frame is None:
        return pd.DataFrame()
    return frame


def _normalize_price_frame(
    frame: pd.DataFrame,
    requested_symbols: Sequence[str],
) -> dict[str, pd.DataFrame]:
    if frame.empty:
        return {}
    normalized = frame.reset_index()
    normalized.columns = [str(column).lower() for column in normalized.columns]
    if "date" not in normalized.columns and "index" in normalized.columns:
        normalized = normalized.rename(columns={"index": "date"})
    if "date" not in normalized.columns:
        raise ValueError("OpenBB price response has no date column")
    if "close" not in normalized.columns:
        raise ValueError("OpenBB price response has no close column")
    if "symbol" not in normalized.columns:
        if len(requested_symbols) != 1:
            raise ValueError("OpenBB batch price response has no symbol column")
        normalized["symbol"] = requested_symbols[0]
    normalized["date"] = pd.to_datetime(
        normalized["date"], errors="coerce", utc=True
    ).dt.date
    normalized["symbol"] = normalized["symbol"].map(normalize_yfinance_symbol)
    normalized["close"] = pd.to_numeric(normalized["close"], errors="coerce")
    normalized = normalized.dropna(subset=["date", "symbol", "close"])
    expected = set(requested_symbols)
    result: dict[str, pd.DataFrame] = {}
    for symbol, rows in normalized.groupby("symbol"):
        if symbol not in expected:
            continue
        if (rows["close"] <= 0).any():
            raise ValueError(f"OpenBB returned a non-positive close for {symbol}")
        clean = (
            rows[["date", "close"]]
            .drop_duplicates("date", keep="last")
            .sort_values("date")
        )
        clean.insert(1, "symbol", symbol)
        if not clean.empty:
            result[str(symbol)] = clean.reset_index(drop=True)
    return result


def _safe_symbol_filename(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9_-]", "_", symbol)


def _record_price_manifest(
    connection: duckdb.DuckDBPyConnection,
    *,
    symbol: str,
    status: str,
    requested_start: date,
    requested_end: date,
    min_date: date | None = None,
    max_date: date | None = None,
    parquet_path: Path | None = None,
    parquet_sha256: str | None = None,
    row_count: int = 0,
    error: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO price_manifest
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            symbol,
            status,
            requested_start,
            requested_end,
            min_date,
            max_date,
            str(parquet_path.resolve()) if parquet_path else None,
            parquet_sha256,
            row_count,
            error,
            datetime.now(timezone.utc),
        ],
    )


def _write_symbol_prices(
    connection: duckdb.DuckDBPyConnection,
    symbol: str,
    frame: pd.DataFrame,
    price_dir: Path,
    requested_start: date,
    requested_end: date,
) -> None:
    price_dir.mkdir(parents=True, exist_ok=True)
    partial = price_dir / f"{_safe_symbol_filename(symbol)}.{uuid.uuid4().hex}.partial"
    frame.to_parquet(partial, index=False, compression="zstd")
    digest = hashlib.sha256(partial.read_bytes()).hexdigest()
    output = price_dir / (
        f"{_safe_symbol_filename(symbol)}.{digest[:16]}.parquet"
    )
    if output.is_file():
        partial.unlink()
    else:
        os.replace(partial, output)
    _record_price_manifest(
        connection,
        symbol=symbol,
        status="READY",
        requested_start=requested_start,
        requested_end=requested_end,
        min_date=frame["date"].min(),
        max_date=frame["date"].max(),
        parquet_path=output,
        parquet_sha256=digest,
        row_count=len(frame),
    )


def refresh_price_cache(
    connection: duckdb.DuckDBPyConnection,
    symbols: Sequence[str],
    *,
    start_date: date,
    end_date: date,
    price_dir: str | Path = DEFAULT_PRICE_DIR,
    force: bool = False,
    retry_no_data: bool = False,
    fetcher: PriceFetcher | None = None,
) -> dict[str, str]:
    if start_date > end_date:
        raise ValueError("Price start_date must not be after end_date")
    fetcher = fetcher or _openbb_price_fetcher
    target_dir = Path(price_dir).resolve()
    requested = sorted(
        {
            normalized
            for symbol in symbols
            if (normalized := normalize_yfinance_symbol(symbol))
        }
    )
    pending: list[str] = []
    for symbol in requested:
        prior = connection.execute(
            """
            SELECT status, requested_start, requested_end, parquet_path
            FROM price_manifest
            WHERE symbol = ?
            """,
            [symbol],
        ).fetchone()
        if (
            not force
            and prior is not None
            and prior[1] <= start_date
            and prior[2] >= end_date
        ):
            if (
                prior[0] == "READY"
                and prior[3]
                and Path(prior[3]).is_file()
            ):
                continue
            if prior[0] == "NO_DATA" and not retry_no_data:
                continue
        pending.append(symbol)

    def fetch_individually(symbol: str, batch_error: str | None = None) -> None:
        try:
            returned = _normalize_price_frame(
                fetcher([symbol], start_date, end_date), [symbol]
            )
            if symbol not in returned:
                raise ValueError(f"OpenBB returned no rows for {symbol}")
            _write_symbol_prices(
                connection,
                symbol,
                returned[symbol],
                target_dir,
                start_date,
                end_date,
            )
        except EmptyDataError as exc:
            detail = str(exc)
            if batch_error:
                detail = (
                    f"Batch failed ({batch_error}); "
                    "individual returned no data"
                )
            _record_price_manifest(
                connection,
                symbol=symbol,
                status="NO_DATA",
                requested_start=start_date,
                requested_end=end_date,
                error=detail,
            )

        except (
            OpenBBError,
            RuntimeError,
            ValueError,
            TypeError,
            OSError,
            KeyError,
        ) as exc:
            detail = str(exc)
            if batch_error:
                detail = f"Batch failed ({batch_error}); individual failed ({detail})"
            _record_price_manifest(
                connection,
                symbol=symbol,
                status="ERROR",
                requested_start=start_date,
                requested_end=end_date,
                error=detail,
            )

    def is_rate_limit_error(message: str | None) -> bool:
        normalized = (message or "").lower()
        return "rate limit" in normalized or "too many requests" in normalized
    for offset in range(0, len(pending), MAX_PRICE_BATCH_SIZE):
        batch = pending[offset : offset + MAX_PRICE_BATCH_SIZE]
        returned = {}
        batch_error = None
        for attempt in range(3):
            try:
                returned = _normalize_price_frame(
                    fetcher(batch, start_date, end_date),
                    batch,
                )
                if returned or len(batch) == 1:
                    batch_error = None
                    break
                batch_error = "batch returned no usable rows"
            except (
                OpenBBError,
                RuntimeError,
                ValueError,
                TypeError,
                OSError,
                KeyError,
            ) as exc:
                batch_error = str(exc)
            if attempt < 2:
                time.sleep(2 ** attempt)
        if batch_error and not returned:
            if is_rate_limit_error(batch_error) or batch_error == "batch returned no usable rows":
                for symbol in batch:
                    _record_price_manifest(
                        connection,
                        symbol=symbol,
                        status="ERROR",
                        requested_start=start_date,
                        requested_end=end_date,
                        error=batch_error,
                    )
            else:
                for symbol in batch:
                    fetch_individually(symbol, batch_error)
            time.sleep(0.25)
            continue
        for symbol, frame in returned.items():
            try:
                _write_symbol_prices(
                    connection,
                    symbol,
                    frame,
                    target_dir,
                    start_date,
                    end_date,
                )
            except (ValueError, TypeError, OSError, KeyError) as exc:
                fetch_individually(symbol, str(exc))
        for missing_symbol in set(batch) - set(returned):
            fetch_individually(
                missing_symbol,
                "batch response omitted symbol",
            )
        time.sleep(0.25)

    statuses: dict[str, str] = {}
    for symbol in requested:
        row = connection.execute(
            "SELECT status FROM price_manifest WHERE symbol = ?",
            [symbol],
        ).fetchone()
        statuses[symbol] = str(row[0]) if row else "MISSING"
    return statuses


def load_cached_prices(
    connection: duckdb.DuckDBPyConnection,
    symbols: Sequence[str],
) -> dict[str, pd.Series]:
    prices: dict[str, pd.Series] = {}
    for symbol in sorted(set(symbols)):
        row = connection.execute(
            """
            SELECT status, parquet_path, parquet_sha256
            FROM price_manifest
            WHERE symbol = ?
            """,
            [symbol],
        ).fetchone()
        if row is None or row[0] != "READY" or not row[1]:
            continue
        path = Path(row[1])
        if not path.is_file():
            connection.execute(
                """
                UPDATE price_manifest
                SET status = 'ERROR',
                    error = ?,
                    updated_at = ?
                WHERE symbol = ?
                """,
                [
                    f"Price cache file is missing: {path}",
                    datetime.now(timezone.utc),
                    symbol,
                ],
            )
            raise FileNotFoundError(f"Price cache file is missing for {symbol}: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != row[2]:
            connection.execute(
                """
                UPDATE price_manifest
                SET status = 'ERROR',
                    error = ?,
                    updated_at = ?
                WHERE symbol = ?
                """,
                [
                    "Price cache hash mismatch",
                    datetime.now(timezone.utc),
                    symbol,
                ],
            )
            raise ValueError(f"Price cache hash mismatch for {symbol}")
        frame = pd.read_parquet(path, columns=["date", "symbol", "close"])
        if set(frame["symbol"].astype(str).unique()) != {symbol}:
            raise ValueError(f"Price cache symbol mismatch for {symbol}")
        index = pd.Index(pd.to_datetime(frame["date"]).dt.date, name="date")
        series = pd.Series(
            pd.to_numeric(frame["close"], errors="coerce").to_numpy(),
            index=index,
            name=symbol,
            dtype=float,
        ).sort_index()
        if series.isna().any() or (series <= 0).any():
            raise ValueError(f"Price cache contains unusable closes for {symbol}")
        prices[symbol] = series[~series.index.duplicated(keep="last")]
    return prices


def _freeze_run_prices(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    symbols: Sequence[str],
) -> None:
    if not symbols:
        return
    placeholders = ",".join("?" for _ in symbols)
    connection.execute(
        f"""
        INSERT OR IGNORE INTO performance_run_prices
        SELECT ?, symbol, min_date, max_date, parquet_path, parquet_sha256,
               row_count
        FROM price_manifest
        WHERE symbol IN ({placeholders})
          AND status = 'READY'
          AND min_date IS NOT NULL
          AND max_date IS NOT NULL
          AND parquet_path IS NOT NULL
          AND parquet_sha256 IS NOT NULL
        """,
        [run_id, *symbols],
    )


def _load_run_prices(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    symbols: Sequence[str],
) -> dict[str, pd.Series]:
    prices: dict[str, pd.Series] = {}
    if not symbols:
        return prices
    placeholders = ",".join("?" for _ in symbols)
    rows = connection.execute(
        f"""
        SELECT symbol, parquet_path, parquet_sha256
        FROM performance_run_prices
        WHERE run_id = ? AND symbol IN ({placeholders})
        ORDER BY symbol
        """,
        [run_id, *symbols],
    ).fetchall()
    for symbol, raw_path, expected_hash in rows:
        path = Path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"Frozen price file is missing for {symbol}: {path}"
            )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected_hash:
            raise ValueError(f"Frozen price hash mismatch for {symbol}")
        frame = pd.read_parquet(path, columns=["date", "symbol", "close"])
        if set(frame["symbol"].astype(str).unique()) != {symbol}:
            raise ValueError(f"Frozen price symbol mismatch for {symbol}")
        index = pd.Index(pd.to_datetime(frame["date"]).dt.date, name="date")
        series = pd.Series(
            pd.to_numeric(frame["close"], errors="coerce").to_numpy(),
            index=index,
            name=symbol,
            dtype=float,
        ).sort_index()
        if series.isna().any() or (series <= 0).any():
            raise ValueError(
                f"Frozen price cache contains unusable closes for {symbol}"
            )
        prices[str(symbol)] = series[
            ~series.index.duplicated(keep="last")
        ]
    return prices


def _aligned_path(
    series: pd.Series,
    sessions: Sequence[date],
    *,
    forward_fill_limit: int = MAX_FORWARD_FILL_SESSIONS,
) -> pd.Series:
    if series.empty:
        return pd.Series(index=pd.Index(sessions), dtype=float)
    ordered = series.sort_index()
    target = pd.Index(sessions, name="date")
    combined = ordered.reindex(ordered.index.union(target)).sort_index()
    aligned = combined.ffill(limit=forward_fill_limit).reindex(target)
    # A forward fill after the provider's final observation would fabricate a
    # post-delisting/post-coverage price, so explicitly remove it.
    aligned.loc[aligned.index > ordered.index.max()] = math.nan
    return aligned


def calculate_interval(
    event: PortfolioEvent,
    end_date: date,
    prices: Mapping[str, pd.Series],
    cusip_mapping: Mapping[str, str],
    *,
    minimum_coverage: float = MINIMUM_COVERAGE,
) -> IntervalResult:
    if event.execution_date is None:
        raise ValueError("Portfolio event has no execution date")
    if end_date < event.execution_date:
        raise ValueError("Interval end precedes execution date")
    spy = prices.get("SPY")
    if spy is None:
        raise ValueError("SPY prices are required to define trading sessions")
    sessions = [
        session
        for session in spy.index
        if event.execution_date <= session <= end_date
    ]
    if not sessions or sessions[0] != event.execution_date or sessions[-1] != end_date:
        raise ValueError("Interval boundaries must be SPY trading sessions")

    eligible_value = event.eligible_value
    if eligible_value <= 0:
        return IntervalResult(
            event.execution_date,
            end_date,
            "UNAVAILABLE",
            0.0,
            0.0,
            None,
            "No positive eligible direct-stock reported value",
            None,
        )

    values_by_symbol: defaultdict[str, float] = defaultdict(float)
    mapped_value = 0.0
    for position in event.positions:
        symbol = (
            cusip_mapping.get(position.cusip)
            if position.cusip is not None
            else None
        )
        if symbol:
            mapped_value += position.reported_value
            values_by_symbol[symbol] += position.reported_value
    mapping_coverage = mapped_value / eligible_value

    valid_paths: dict[str, pd.Series] = {}
    priced_value = 0.0
    for symbol, value in values_by_symbol.items():
        series = prices.get(symbol)
        if series is None:
            continue
        path = _aligned_path(series, sessions)
        if (
            path.notna().all()
            and (path > 0).all()
            and path.iloc[0] > 0
            and path.iloc[-1] > 0
        ):
            valid_paths[symbol] = path
            priced_value += value
    priced_coverage = priced_value / eligible_value

    reasons = []
    if mapping_coverage < minimum_coverage:
        reasons.append(
            f"mapping coverage {mapping_coverage:.2%} is below {minimum_coverage:.0%}"
        )
    if priced_coverage < minimum_coverage:
        reasons.append(
            f"fully priced value coverage {priced_coverage:.2%} is below "
            f"{minimum_coverage:.0%}"
        )
    if reasons:
        return IntervalResult(
            event.execution_date,
            end_date,
            "UNAVAILABLE",
            mapping_coverage,
            priced_coverage,
            None,
            "; ".join(reasons),
            None,
        )

    # Coverage is reported against the original eligible sleeve above. Only
    # after both gates pass are the fully priced positions renormalized.
    nav = pd.Series(0.0, index=pd.Index(sessions, name="date"), dtype=float)
    for symbol, path in valid_paths.items():
        weight = values_by_symbol[symbol] / priced_value
        nav = nav.add(weight * path / path.iloc[0], fill_value=0)
    return IntervalResult(
        event.execution_date,
        end_date,
        "AVAILABLE",
        mapping_coverage,
        priced_coverage,
        float(nav.iloc[-1] - 1),
        None,
        nav,
    )


def _cagr(start: float, end: float, years: float) -> float | None:
    if start <= 0 or end <= 0 or years <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def _annualized_ratio(returns: pd.Series) -> float | None:
    clean = returns.dropna()
    if len(clean) < 2:
        return None
    deviation = float(clean.std(ddof=1))
    if deviation == 0:
        return None
    return float(clean.mean() / deviation * math.sqrt(12))


def _quarterly_beat_rate(
    manager_nav: pd.Series,
    benchmark_nav: pd.Series,
) -> float | None:
    frame = pd.DataFrame({"manager": manager_nav, "benchmark": benchmark_nav})
    periods = pd.PeriodIndex(pd.to_datetime(frame.index), freq="Q")
    quarter_end = frame.groupby(periods).last()
    returns = quarter_end.pct_change().dropna()
    if returns.empty:
        return None
    return float((returns["manager"] > returns["benchmark"]).mean())


def calculate_summary_metrics(
    manager_nav: pd.Series,
    spy_nav: pd.Series,
    qqq_nav: pd.Series,
    intervals: Sequence[IntervalResult],
    *,
    window: str,
    end_date: date,
    window_start: date | None = None,
) -> dict[str, object]:
    if window not in {"3Y", "5Y", "FULL"}:
        raise ValueError("window must be 3Y, 5Y, or FULL")
    if manager_nav.empty:
        return {
            "status": "UNAVAILABLE",
            "unavailable_reason": "No contiguous fully eligible performance history",
            "interval_count": 0,
        }
    if window == "FULL" and any(
        interval.status != "AVAILABLE" for interval in intervals
    ):
        return {
            "status": "UNAVAILABLE",
            "unavailable_reason": (
                "One or more full-history intervals failed mapping or pricing "
                "coverage"
            ),
            "interval_count": sum(
                interval.status == "AVAILABLE" for interval in intervals
            ),
        }

    years_required = None if window == "FULL" else int(window[:-1])
    first_available = manager_nav.index.min()
    cutoff = (
        max(first_available, window_start)
        if years_required is None and window_start is not None
        else first_available
        if years_required is None
        else _subtract_years(end_date, years_required)
    )
    if years_required is not None and first_available > cutoff:
        return {
            "status": "UNAVAILABLE",
            "unavailable_reason": f"Less than {years_required} years of contiguous eligible history",
            "interval_count": sum(
                interval.status == "AVAILABLE" for interval in intervals
            ),
        }
    selected_index = [item for item in manager_nav.index if item >= cutoff]
    if len(selected_index) < 2:
        return {
            "status": "UNAVAILABLE",
            "unavailable_reason": "Insufficient observations",
            "interval_count": 0,
        }
    manager = manager_nav.loc[selected_index]
    spy = spy_nav.loc[selected_index]
    qqq = qqq_nav.loc[selected_index]
    start_date = manager.index[0]
    metric_end = manager.index[-1]
    years = (metric_end - start_date).days / 365.2425
    if years <= 0:
        return {
            "status": "UNAVAILABLE",
            "unavailable_reason": "Insufficient elapsed time",
            "interval_count": 0,
        }

    month_periods = pd.PeriodIndex(pd.to_datetime(manager.index), freq="M")
    monthly = pd.DataFrame(
        {"manager": manager, "spy": spy, "qqq": qqq}
    ).groupby(month_periods).last().pct_change().dropna()
    drawdown = manager / manager.cummax() - 1
    relevant_intervals = [
        interval
        for interval in intervals
        if interval.end_date >= start_date
        and interval.start_date <= metric_end
        and interval.status == "AVAILABLE"
    ]
    estimated_cagr = _cagr(float(manager.iloc[0]), float(manager.iloc[-1]), years)
    spy_cagr = _cagr(float(spy.iloc[0]), float(spy.iloc[-1]), years)
    qqq_cagr = _cagr(float(qqq.iloc[0]), float(qqq.iloc[-1]), years)
    return {
        "status": "AVAILABLE",
        "start_date": start_date,
        "end_date": metric_end,
        "years": years,
        "estimated_cagr": estimated_cagr,
        "spy_cagr": spy_cagr,
        "qqq_cagr": qqq_cagr,
        "spy_excess_cagr": (
            estimated_cagr - spy_cagr
            if estimated_cagr is not None and spy_cagr is not None
            else None
        ),
        "qqq_excess_cagr": (
            estimated_cagr - qqq_cagr
            if estimated_cagr is not None and qqq_cagr is not None
            else None
        ),
        "max_drawdown": float(drawdown.min()),
        "monthly_sharpe_rf0": _annualized_ratio(monthly["manager"]),
        "spy_information_ratio": _annualized_ratio(
            monthly["manager"] - monthly["spy"]
        ),
        "qqq_information_ratio": _annualized_ratio(
            monthly["manager"] - monthly["qqq"]
        ),
        "spy_quarterly_beat_rate": _quarterly_beat_rate(manager, spy),
        "qqq_quarterly_beat_rate": _quarterly_beat_rate(manager, qqq),
        "mapping_coverage": min(
            (item.mapping_coverage for item in relevant_intervals),
            default=None,
        ),
        "priced_coverage": min(
            (item.priced_coverage for item in relevant_intervals),
            default=None,
        ),
        "interval_count": len(relevant_intervals),
        "unavailable_reason": None,
    }


def _chain_available_tail(
    intervals: Sequence[IntervalResult],
    prices: Mapping[str, pd.Series],
    *,
    cost_bps: float,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    last_invalid = max(
        (
            index
            for index, interval in enumerate(intervals)
            if interval.status != "AVAILABLE"
        ),
        default=-1,
    )
    tail = list(intervals[last_invalid + 1 :])
    if not tail or tail[-1].daily_nav is None:
        empty = pd.Series(dtype=float)
        return empty, empty, empty

    chained = pd.Series(dtype=float)
    current_nav = 1.0
    for index, interval in enumerate(tail):
        assert interval.daily_nav is not None
        local = interval.daily_nav / interval.daily_nav.iloc[0]
        if index:
            current_nav *= 1 - cost_bps / 10_000
        local = local * current_nav
        if not chained.empty:
            chained = chained.drop(index=local.index[0], errors="ignore")
        chained = pd.concat([chained, local])
        current_nav = float(local.iloc[-1])
    spy = prices["SPY"].reindex(chained.index)
    qqq = _aligned_path(prices["QQQ"], list(chained.index))
    if spy.isna().any() or qqq.isna().any():
        raise ValueError("Benchmark path is incomplete on chained SPY sessions")
    return chained, spy / spy.iloc[0], qqq / qqq.iloc[0]


def _monthly_rows(
    manager_nav: pd.Series,
    spy_nav: pd.Series,
    qqq_nav: pd.Series,
) -> list[tuple[date, float, float, float]]:
    if manager_nav.empty:
        return []
    frame = pd.DataFrame(
        {"manager": manager_nav, "spy": spy_nav, "qqq": qqq_nav}
    )
    periods = pd.PeriodIndex(pd.to_datetime(frame.index), freq="M")
    monthly_levels = frame.groupby(periods).last()
    monthly_dates = pd.Series(frame.index, index=periods).groupby(level=0).last()
    monthly_returns = monthly_levels.pct_change()
    rows = []
    for period, values in monthly_returns.dropna().iterrows():
        rows.append(
            (
                monthly_dates.loc[period],
                float(values["manager"]),
                float(values["spy"]),
                float(values["qqq"]),
            )
        )
    return rows


def _subtract_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _select_events_for_window(
    events: Sequence[PortfolioEvent],
    *,
    calculation_start: date,
    requested_as_of: date,
) -> list[PortfolioEvent]:
    selected: list[PortfolioEvent] = []
    before = [
        event for event in events if event.filing_date < calculation_start
    ]
    if before:
        selected.append(before[-1])
    selected.extend(
        event
        for event in events
        if calculation_start <= event.filing_date <= requested_as_of
    )
    return selected


def _manager_state_counts(
    connection: duckdb.DuckDBPyConnection,
    run_id: str,
) -> dict[str, int]:
    counts = dict(
        connection.execute(
            """
            SELECT status, count(*)
            FROM performance_manager_state
            WHERE run_id = ?
            GROUP BY status
            """,
            [run_id],
        ).fetchall()
    )
    return {
        status: int(counts.get(status, 0))
        for status in (
            "PENDING",
            "BUILDING",
            "COMPLETE",
            "FAILED",
            "EXHAUSTED",
        )
    }


def _mark_manager_failed(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    manager: ManagerUniverseItem,
    error: object,
    increment_attempt: bool = True,
) -> None:
    failed_at = datetime.now(timezone.utc)
    connection.execute(
        """
        UPDATE performance_manager_state
        SET status = CASE
                WHEN attempt_count + ? >= ? THEN 'EXHAUSTED'
                ELSE 'FAILED'
            END,
            attempt_count = attempt_count + ?,
            last_error = ?,
            completed_at = ?,
            updated_at = ?
        WHERE run_id = ? AND cik = ?
        """,
        [
            int(increment_attempt),
            MAX_MANAGER_ATTEMPTS,
            int(increment_attempt),
            str(error),
            failed_at,
            failed_at,
            run_id,
            manager.cik,
        ],
    )


def _persist_manager_performance(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    manager: ManagerUniverseItem,
    manager_events: Sequence[PortfolioEvent],
    latest_end: date,
    prices: Mapping[str, pd.Series],
    mapping: Mapping[str, str],
    ticker_by_cusip: Mapping[str, str],
    cost_bps: float,
    calculation_start: date,
) -> None:
    started_at = datetime.now(timezone.utc)
    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(
            """
            UPDATE performance_manager_state
            SET status = 'BUILDING',
                attempt_count = attempt_count + 1,
                last_error = NULL,
                started_at = ?,
                completed_at = NULL,
                updated_at = ?
            WHERE run_id = ? AND cik = ?
            """,
            [started_at, started_at, run_id, manager.cik],
        )
        for table_name in (
            "performance_events",
            "performance_event_positions",
            "performance_intervals",
            "monthly_returns",
            "manager_performance",
        ):
            connection.execute(
                f"DELETE FROM {table_name} WHERE run_id = ? AND cik = ?",
                [run_id, manager.cik],
            )

        interval_results: list[IntervalResult] = []
        for index, event in enumerate(manager_events):
            assert event.execution_date is not None
            end = (
                manager_events[index + 1].execution_date
                if index + 1 < len(manager_events)
                else latest_end
            )
            assert end is not None
            mapping_coverage = (
                sum(
                    position.reported_value
                    for position in event.positions
                    if position.cusip in mapping
                )
                / event.eligible_value
                if event.eligible_value > 0
                else 0.0
            )
            connection.execute(
                """
                INSERT INTO performance_events
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    manager.cik,
                    index,
                    event.report_period,
                    event.filing_date,
                    event.execution_date,
                    event.triggering_accession,
                    json.dumps(event.effective_accessions),
                    event.eligible_value,
                    mapping_coverage,
                ],
            )
            position_rows = [
                (
                    run_id,
                    manager.cik,
                    index,
                    position.cusip,
                    ticker_by_cusip.get(position.cusip)
                    if position.cusip
                    else None,
                    mapping.get(position.cusip) if position.cusip else None,
                    position.reported_value,
                )
                for position in event.positions
            ]
            if position_rows:
                connection.executemany(
                    """
                    INSERT INTO performance_event_positions
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    position_rows,
                )
            result = calculate_interval(event, end, prices, mapping)
            interval_results.append(result)
            connection.execute(
                """
                INSERT INTO performance_intervals
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    manager.cik,
                    index,
                    result.start_date,
                    result.end_date,
                    cost_bps,
                    result.status,
                    result.mapping_coverage,
                    result.priced_coverage,
                    result.estimated_return,
                    result.unavailable_reason,
                ],
            )

        manager_nav, spy_nav, qqq_nav = _chain_available_tail(
            interval_results,
            prices,
            cost_bps=cost_bps,
        )
        monthly_index = [
            item for item in manager_nav.index if item >= calculation_start
        ]
        monthly = _monthly_rows(
            manager_nav.loc[monthly_index],
            spy_nav.loc[monthly_index],
            qqq_nav.loc[monthly_index],
        )
        if monthly:
            connection.executemany(
                """
                INSERT INTO monthly_returns
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        manager.cik,
                        month,
                        cost_bps,
                        estimate,
                        spy,
                        qqq,
                    )
                    for month, estimate, spy, qqq in monthly
                ],
            )
        for window in ("3Y", "5Y", "FULL"):
            metrics = calculate_summary_metrics(
                manager_nav,
                spy_nav,
                qqq_nav,
                interval_results,
                window=window,
                end_date=latest_end,
                window_start=calculation_start,
            )
            connection.execute(
                """
                INSERT INTO manager_performance
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    run_id,
                    manager.cik,
                    window,
                    cost_bps,
                    metrics["status"],
                    metrics.get("start_date"),
                    metrics.get("end_date"),
                    metrics.get("years"),
                    metrics.get("estimated_cagr"),
                    metrics.get("spy_cagr"),
                    metrics.get("qqq_cagr"),
                    metrics.get("spy_excess_cagr"),
                    metrics.get("qqq_excess_cagr"),
                    metrics.get("max_drawdown"),
                    metrics.get("monthly_sharpe_rf0"),
                    metrics.get("spy_information_ratio"),
                    metrics.get("qqq_information_ratio"),
                    metrics.get("spy_quarterly_beat_rate"),
                    metrics.get("qqq_quarterly_beat_rate"),
                    metrics.get("mapping_coverage"),
                    metrics.get("priced_coverage"),
                    metrics["interval_count"],
                    metrics.get("unavailable_reason"),
                    PERFORMANCE_LABEL,
                    PERFORMANCE_DISCLAIMER,
                ],
            )
        completed_at = datetime.now(timezone.utc)
        connection.execute(
            """
            UPDATE performance_manager_state
            SET status = 'COMPLETE',
                completed_at = ?,
                updated_at = ?
            WHERE run_id = ? AND cik = ?
            """,
            [completed_at, completed_at, run_id, manager.cik],
        )
        connection.execute("COMMIT")
    except Exception as exc:
        try:
            connection.execute("ROLLBACK")
        except duckdb.Error:
            pass
        _mark_manager_failed(
            connection,
            run_id=run_id,
            manager=manager,
            error=exc,
            increment_attempt=True,
        )


def _process_manager_batch(
    source: duckdb.DuckDBPyConnection,
    store: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    managers: Sequence[ManagerUniverseItem],
    requested_as_of: date,
    calculation_start: date,
    latest_end: date,
    spy_sessions: Sequence[date],
    mapping: Mapping[str, str],
    ticker_by_cusip: Mapping[str, str],
    price_dir: str | Path,
    force_prices: bool,
    force_refreshed_symbols: set[str],
    retry_no_data: bool,
    cost_bps: float,
    price_fetcher: PriceFetcher | None,
) -> None:
    raw_events = reconstruct_filing_chronology(
        source,
        managers,
        minimum_filing_date=calculation_start - timedelta(days=400),
        maximum_filing_date=requested_as_of,
    )
    raw_by_manager: defaultdict[str, list[PortfolioEvent]] = defaultdict(list)
    for event in raw_events:
        raw_by_manager[event.cik].append(event)
    selected_by_manager = {
        manager.cik: _select_events_for_window(
            raw_by_manager.get(manager.cik, []),
            calculation_start=calculation_start,
            requested_as_of=requested_as_of,
        )
        for manager in managers
    }
    symbols = {
        mapping[position.cusip]
        for manager_events in selected_by_manager.values()
        for event in manager_events
        for position in event.positions
        if position.cusip in mapping
    }
    symbols.update({"SPY", "QQQ"})
    price_start = calculation_start - timedelta(days=400)
    frozen_symbols = {
        str(symbol)
        for (symbol,) in store.execute(
            """
            SELECT symbol
            FROM performance_run_prices
            WHERE run_id = ?
            """,
            [run_id],
        ).fetchall()
    }
    unfrozen_symbols = symbols - frozen_symbols
    if force_prices:
        new_force_symbols = sorted(
            unfrozen_symbols - force_refreshed_symbols
        )
        if new_force_symbols:
            refresh_price_cache(
                store,
                new_force_symbols,
                start_date=price_start,
                end_date=requested_as_of,
                price_dir=price_dir,
                force=True,
                retry_no_data=retry_no_data,
                fetcher=price_fetcher,
            )
            force_refreshed_symbols.update(new_force_symbols)
    statuses = refresh_price_cache(
        store,
        sorted(unfrozen_symbols),
        start_date=price_start,
        end_date=requested_as_of,
        price_dir=price_dir,
        force=False,
        retry_no_data=retry_no_data,
        fetcher=price_fetcher,
    )
    _freeze_run_prices(
        store,
        run_id=run_id,
        symbols=sorted(unfrozen_symbols),
    )
    prices = _load_run_prices(
        store,
        run_id=run_id,
        symbols=sorted(symbols),
    )
    statuses.update(
        {
            symbol: "READY"
            for symbol in frozen_symbols
            if symbol in symbols
        }
    )
    executable = assign_and_consolidate_execution_dates(
        [
            event
            for manager_events in selected_by_manager.values()
            for event in manager_events
        ],
        spy_sessions,
    )
    executable_by_manager: defaultdict[str, list[PortfolioEvent]] = defaultdict(
        list
    )
    for event in executable:
        if event.execution_date and event.execution_date <= latest_end:
            executable_by_manager[event.cik].append(event)

    transient_symbols = {
        symbol
        for symbol, status in statuses.items()
        if status in {"ERROR", "MISSING"}
    }
    for manager in managers:
        manager_events = executable_by_manager.get(manager.cik, [])
        blocked_exposure = max(
            (
                sum(
                    position.reported_value
                    for position in event.positions
                    if mapping.get(position.cusip) in transient_symbols
                )
                / event.eligible_value
                for event in manager_events
                if event.eligible_value > 0
            ),
            default=0.0,
        )
        if blocked_exposure > 1 - MINIMUM_COVERAGE:
            _mark_manager_failed(
                store,
                run_id=run_id,
                manager=manager,
                error=(
                    "Transient price failures affect "
                    f"{blocked_exposure:.2%} of an eligible interval"
                ),
            )
            continue
        _persist_manager_performance(
            store,
            run_id=run_id,
            manager=manager,
            manager_events=manager_events,
            latest_end=latest_end,
            prices=prices,
            mapping=mapping,
            ticker_by_cusip=ticker_by_cusip,
            cost_bps=cost_bps,
            calculation_start=calculation_start,
        )


def refresh_performance(
    *,
    source_path: str | Path = DEFAULT_DATABASE_PATH,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_POINTER,
    performance_path: str | Path = DEFAULT_PERFORMANCE_PATH,
    price_dir: str | Path = DEFAULT_PRICE_DIR,
    minimum_size_billions: float = 0.0,
    as_of: date | None = None,
    window_years: int = 5,
    batch_size: int = 10,
    max_managers: int | None = None,
    resume: bool = True,
    resume_run_id: str | None = None,
    force_prices: bool = False,
    retry_no_data: bool = False,
    cost_bps: float = 0.0,
    mapping_loader: Callable[[], object] | None = None,
    price_fetcher: PriceFetcher | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    if window_years < 1:
        raise ValueError("window_years must be at least 1")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if max_managers is not None and max_managers < 1:
        raise ValueError("max_managers must be at least 1")
    if resume_run_id and not resume:
        raise ValueError("resume_run_id cannot be combined with resume=False")
    if cost_bps < 0:
        raise ValueError("cost_bps cannot be negative")
    managers, fingerprint, generation = load_manager_universe(
        snapshot_path,
        minimum_size_billions=minimum_size_billions,
    )
    store = connect_performance_store(performance_path)
    source = duckdb.connect(str(Path(source_path).resolve()), read_only=True)
    started_at = datetime.now(timezone.utc)
    try:
        source_fingerprint = compute_source_fingerprint(source)
        if source_fingerprint != fingerprint:
            raise ValueError(
                "Performance source database does not match the screening "
                "snapshot fingerprint"
            )
        run = None
        if resume:
            run_id_filter = "AND r.run_id = ?" if resume_run_id else ""
            date_filter = "AND r.requested_as_of = ?" if as_of is not None else ""
            implicit_filter = (
                "AND o.implicit_as_of" if as_of is None and not resume_run_id else ""
            )
            run_params: list[object] = [
                METHODOLOGY_VERSION,
                fingerprint,
                window_years,
                minimum_size_billions,
                cost_bps,
            ]
            if resume_run_id:
                run_params.append(resume_run_id)
            if as_of is not None:
                run_params.append(as_of)
            run = store.execute(
                f"""
                SELECT r.run_id, r.requested_as_of, r.source_database
                FROM performance_runs r
                LEFT JOIN performance_run_options o USING (run_id)
                WHERE r.status = 'BUILDING'
                  AND r.methodology_version = ?
                  AND r.screening_source_fingerprint = ?
                  AND r.window_years = ?
                  AND coalesce(r.minimum_size_billions, 0) = ?
                  AND r.cost_bps = ?
                  {run_id_filter}
                  {date_filter}
                  {implicit_filter}
                ORDER BY r.started_at DESC
                LIMIT 1
                """,
                run_params,
            ).fetchone()
            if resume_run_id and run is None:
                raise ValueError(
                    f"No matching BUILDING performance run: {resume_run_id}"
                )
            if run is None and resume_run_id is None:
                completed_as_of = as_of or date.today()
                completed = store.execute(
                    """
                    SELECT r.run_id, r.latest_end_date
                    FROM performance_runs r
                    JOIN (
                        SELECT
                            run_id,
                            count(*) AS state_count,
                            count(*) FILTER (
                                WHERE status IN ('COMPLETE', 'EXHAUSTED')
                            ) AS terminal_count
                        FROM performance_manager_state
                        GROUP BY run_id
                    ) s USING (run_id)
                    WHERE r.status = 'COMPLETE'
                      AND r.methodology_version = ?
                      AND r.screening_source_fingerprint = ?
                      AND r.requested_as_of = ?
                      AND r.window_years = ?
                      AND coalesce(r.minimum_size_billions, 0) = ?
                      AND r.cost_bps = ?
                      AND r.source_database = ?
                      AND s.state_count = r.manager_count
                      AND s.terminal_count = r.manager_count
                    ORDER BY r.completed_at DESC
                    LIMIT 1
                    """,
                    [
                        METHODOLOGY_VERSION,
                        fingerprint,
                        completed_as_of,
                        window_years,
                        minimum_size_billions,
                        cost_bps,
                        str(Path(source_path).resolve()),
                    ],
                ).fetchone()
                if completed:
                    completed_run_id = str(completed[0])
                    available = store.execute(
                        """
                        SELECT count(*)
                        FROM manager_performance
                        WHERE run_id = ?
                          AND "window" = '5Y'
                          AND status = 'AVAILABLE'
                        """,
                        [completed_run_id],
                    ).fetchone()[0]
                    return {
                        "run_id": completed_run_id,
                        "status": "COMPLETE",
                        "manager_count": len(managers),
                        "processed_this_invocation": 0,
                        "manager_states": _manager_state_counts(
                            store,
                            completed_run_id,
                        ),
                        "available_5y_count": available,
                        "latest_end_date": completed[1],
                        "performance_path": str(
                            Path(performance_path).resolve()
                        ),
                        "price_dir": str(Path(price_dir).resolve()),
                        "label": PERFORMANCE_LABEL,
                        "disclaimer": PERFORMANCE_DISCLAIMER,
                        "reused_complete_run": True,
                    }
        if run:
            run_id = str(run[0])
            requested_as_of = run[1]
            if Path(str(run[2])).resolve() != Path(source_path).resolve():
                raise ValueError(
                    "Matching performance campaign uses a different source "
                    "database"
                )
            new_run = False
        else:
            run_id = uuid.uuid4().hex
            requested_as_of = as_of or date.today()
            new_run = True
        expected_universe = {
            manager.cik: manager
            for manager in managers
        }
        stored_universe = {
            str(cik)
            for (cik,) in store.execute(
                """
                SELECT cik
                FROM performance_run_universe
                WHERE run_id = ?
                """,
                [run_id],
            ).fetchall()
        }
        if stored_universe and stored_universe != set(expected_universe):
            raise ValueError(
                "Matching performance campaign has a different manager "
                "universe"
            )
        if new_run or not stored_universe:
            store.execute("BEGIN TRANSACTION")
            try:
                if new_run:
                    store.execute(
                        """
                        INSERT INTO performance_runs
                        VALUES (
                            ?, 'BUILDING', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?,
                            ?, ?, NULL
                        )
                        """,
                        [
                            run_id,
                            METHODOLOGY_VERSION,
                            PERFORMANCE_LABEL,
                            PERFORMANCE_DISCLAIMER,
                            fingerprint,
                            str(generation),
                            str(Path(source_path).resolve()),
                            requested_as_of,
                            window_years,
                            minimum_size_billions,
                            cost_bps,
                            len(managers),
                            started_at,
                        ],
                    )
                store.execute(
                    """
                    INSERT OR IGNORE INTO performance_run_options
                    VALUES (?, ?, true)
                    """,
                    [run_id, as_of is None],
                )
                store.executemany(
                    """
                    INSERT OR IGNORE INTO performance_run_universe
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (
                            run_id,
                            manager.cik,
                            manager.manager_name,
                            manager.median_reported_value_4q,
                        )
                        for manager in managers
                    ],
                )
                store.executemany(
                    """
                    INSERT OR IGNORE INTO performance_manager_state
                    VALUES (?, ?, ?, 'PENDING', 0, NULL, NULL, NULL, ?)
                    """,
                    [
                        (
                            run_id,
                            manager.cik,
                            manager.manager_name,
                            started_at,
                        )
                        for manager in managers
                    ],
                )
                store.execute("COMMIT")
            except duckdb.Error:
                store.execute("ROLLBACK")
                raise
        state_count = store.execute(
            """
            SELECT count(*)
            FROM performance_manager_state
            WHERE run_id = ?
            """,
            [run_id],
        ).fetchone()[0]
        if state_count != len(managers):
            raise ValueError(
                "Performance campaign checkpoint count does not match its "
                "manager universe"
            )
        store.execute(
            """
            INSERT OR IGNORE INTO performance_run_options
            VALUES (?, ?, true)
            """,
            [run_id, as_of is None],
        )
        store.execute(
            """
            UPDATE performance_manager_state
            SET status = 'PENDING',
                attempt_count = greatest(attempt_count - 1, 0),
                last_error = 'Recovered interrupted manager transaction',
                started_at = NULL,
                completed_at = NULL,
                updated_at = ?
            WHERE run_id = ? AND status = 'BUILDING'
            """,
            [started_at, run_id],
        )
        if (force_prices or retry_no_data) and not new_run:
            raise ValueError(
                "--force-prices and --retry-no-data require --no-resume so "
                "completed manager checkpoints remain immutable"
            )
        run_mapping_count = store.execute(
            """
            SELECT count(*)
            FROM performance_run_mapping
            WHERE run_id = ?
            """,
            [run_id],
        ).fetchone()[0]
        if not run_mapping_count:
            refresh_cusip_ticker_mapping(
                store,
                mapping_loader=mapping_loader,
                minimum_rows=1000 if mapping_loader is None else 1,
            )
            store.execute("BEGIN TRANSACTION")
            try:
                store.execute(
                    """
                    INSERT INTO performance_run_mapping
                    SELECT ?, cusip, ticker, market_symbol, source, retrieved_at
                    FROM cusip_ticker_mapping
                    """,
                    [run_id],
                )
                store.execute("COMMIT")
            except duckdb.Error:
                store.execute("ROLLBACK")
                raise
        mapping = dict(
            store.execute(
                """
                SELECT cusip, market_symbol
                FROM performance_run_mapping
                WHERE run_id = ?
                """,
                [run_id],
            ).fetchall()
        )
        ticker_by_cusip = dict(
            store.execute(
                """
                SELECT cusip, ticker
                FROM performance_run_mapping
                WHERE run_id = ?
                """,
                [run_id],
            ).fetchall()
        )
        calculation_start = _subtract_years(requested_as_of, window_years)
        run_price_count = store.execute(
            """
            SELECT count(*)
            FROM performance_run_prices
            WHERE run_id = ?
            """,
            [run_id],
        ).fetchone()[0]
        if not new_run and run_price_count == 0:
            store.execute(
                """
                UPDATE performance_manager_state
                SET status = 'PENDING',
                    completed_at = NULL,
                    updated_at = ?
                WHERE run_id = ? AND status = 'COMPLETE'
                """,
                [started_at, run_id],
            )
        frozen_benchmarks = {
            str(symbol)
            for (symbol,) in store.execute(
                """
                SELECT symbol
                FROM performance_run_prices
                WHERE run_id = ? AND symbol IN ('SPY', 'QQQ')
                """,
                [run_id],
            ).fetchall()
        }
        missing_benchmarks = sorted({"SPY", "QQQ"} - frozen_benchmarks)
        refresh_price_cache(
            store,
            missing_benchmarks,
            start_date=calculation_start - timedelta(days=400),
            end_date=requested_as_of,
            price_dir=price_dir,
            force=force_prices,
            retry_no_data=retry_no_data,
            fetcher=price_fetcher,
        )
        _freeze_run_prices(
            store,
            run_id=run_id,
            symbols=missing_benchmarks,
        )
        benchmark_prices = _load_run_prices(
            store,
            run_id=run_id,
            symbols=["SPY", "QQQ"],
        )
        if "SPY" not in benchmark_prices or "QQQ" not in benchmark_prices:
            raise RuntimeError(
                "SPY and QQQ must both have READY price caches before calculation"
            )
        benchmark_bounds = {
            str(symbol): (min_date, max_date)
            for symbol, min_date, max_date in store.execute(
                """
                SELECT symbol, min_date, max_date
                FROM performance_run_prices
                WHERE run_id = ? AND symbol IN ('SPY', 'QQQ')
                """,
                [run_id],
            ).fetchall()
        }
        for symbol in ("SPY", "QQQ"):
            min_date, max_date = benchmark_bounds[symbol]
            if min_date > calculation_start:
                raise RuntimeError(
                    f"{symbol} price history does not cover calculation start"
                )
            if (requested_as_of - max_date).days > 7:
                raise RuntimeError(
                    f"{symbol} price history is stale at {max_date}"
                )
        common_dates = sorted(
            set(benchmark_prices["SPY"].index)
            .intersection(benchmark_prices["QQQ"].index)
            .intersection(
                {
                    item
                    for item in benchmark_prices["SPY"].index
                    if item <= requested_as_of
                }
            )
        )
        if not common_dates:
            raise RuntimeError("SPY and QQQ have no common date on or before as-of")
        latest_end = common_dates[-1]
        spy_sessions = [
            item for item in benchmark_prices["SPY"].index
            if item <= latest_end
        ]
        manager_by_cik = {manager.cik: manager for manager in managers}
        pending_ciks = [
            str(row[0])
            for row in store.execute(
                """
                SELECT cik
                FROM performance_manager_state
                WHERE run_id = ?
                  AND status IN ('PENDING', 'BUILDING', 'FAILED')
                  AND attempt_count < ?
                ORDER BY
                    CASE status
                        WHEN 'PENDING' THEN 0
                        WHEN 'BUILDING' THEN 1
                        ELSE 2
                    END,
                    cik
                """,
                [run_id, MAX_MANAGER_ATTEMPTS],
            ).fetchall()
        ]
        if max_managers is not None:
            pending_ciks = pending_ciks[:max_managers]

        force_refreshed_symbols = (
            {"SPY", "QQQ"} if force_prices else set()
        )
        for offset in range(0, len(pending_ciks), batch_size):
            batch_ciks = pending_ciks[offset : offset + batch_size]
            batch_managers = [
                manager_by_cik[cik]
                for cik in batch_ciks
                if cik in manager_by_cik
            ]
            try:
                _process_manager_batch(
                    source,
                    store,
                    run_id=run_id,
                    managers=batch_managers,
                    requested_as_of=requested_as_of,
                    calculation_start=calculation_start,
                    latest_end=latest_end,
                    spy_sessions=spy_sessions,
                    mapping=mapping,
                    ticker_by_cusip=ticker_by_cusip,
                    price_dir=price_dir,
                    force_prices=force_prices,
                    force_refreshed_symbols=force_refreshed_symbols,
                    retry_no_data=retry_no_data,
                    cost_bps=cost_bps,
                    price_fetcher=price_fetcher,
                )
            except Exception as exc:
                for manager in batch_managers:
                    _mark_manager_failed(
                        store,
                        run_id=run_id,
                        manager=manager,
                        error=f"Batch failure: {exc}",
                    )
            if progress_callback:
                progress_callback(
                    {
                        "run_id": run_id,
                        "processed": min(
                            offset + len(batch_ciks),
                            len(pending_ciks),
                        ),
                        "selected": len(pending_ciks),
                        "total": len(managers),
                        "states": _manager_state_counts(store, run_id),
                    }
                )

        states = _manager_state_counts(store, run_id)
        if (
            states["PENDING"] == 0
            and states["BUILDING"] == 0
            and states["FAILED"] == 0
        ):
            store.execute(
                """
                UPDATE performance_runs
                SET status = 'COMPLETE', latest_end_date = ?, completed_at = ?
                WHERE run_id = ?
                """,
                [latest_end, datetime.now(timezone.utc), run_id],
            )
            status = "COMPLETE"
        else:
            store.execute(
                """
                UPDATE performance_runs
                SET latest_end_date = ?
                WHERE run_id = ?
                """,
                [latest_end, run_id],
            )
            status = "BUILDING"
        available = store.execute(
            """
            SELECT count(*)
            FROM manager_performance
            WHERE run_id = ? AND "window" = '5Y' AND status = 'AVAILABLE'
            """,
            [run_id],
        ).fetchone()[0]
        return {
            "run_id": run_id,
            "status": status,
            "manager_count": len(managers),
            "processed_this_invocation": len(pending_ciks),
            "manager_states": states,
            "available_5y_count": available,
            "latest_end_date": latest_end,
            "performance_path": str(Path(performance_path).resolve()),
            "price_dir": str(Path(price_dir).resolve()),
            "label": PERFORMANCE_LABEL,
            "disclaimer": PERFORMANCE_DISCLAIMER,
        }
    finally:
        source.close()
        store.close()


def performance_status(
    performance_path: str | Path = DEFAULT_PERFORMANCE_PATH,
) -> dict[str, object]:
    path = Path(performance_path).resolve()
    if not path.is_file():
        return {
            "status": "NOT_GENERATED",
            "performance_path": str(path),
            "runs": [],
            "prices": {},
        }
    connection = duckdb.connect(str(path), read_only=True)
    try:
        runs = connection.execute(
            """
            SELECT
                run_id, status, requested_as_of, latest_end_date, window_years,
                minimum_size_billions, cost_bps, manager_count, started_at,
                completed_at
            FROM performance_runs
            ORDER BY started_at DESC
            LIMIT 10
            """
        )
        columns = [column[0] for column in runs.description]
        run_rows = runs.fetchall()
        price_counts = dict(
            connection.execute(
                """
                SELECT status, count(*)
                FROM price_manifest
                GROUP BY status
                ORDER BY status
                """
            ).fetchall()
        )
        has_manager_state = bool(
            connection.execute(
                """
                SELECT count(*)
                FROM information_schema.tables
                WHERE table_schema = 'main'
                  AND table_name = 'performance_manager_state'
                """
            ).fetchone()[0]
        )
        manager_state_counts = (
            [
                dict(zip(
                    ("run_id", "status", "manager_count"),
                    row,
                ))
                for row in connection.execute(
                    """
                    SELECT run_id, status, count(*)
                    FROM performance_manager_state
                    GROUP BY run_id, status
                    ORDER BY run_id, status
                    """
                ).fetchall()
            ]
            if has_manager_state
            else []
        )
        manager_failures = (
            [
                dict(zip(
                    (
                        "run_id",
                        "cik",
                        "manager_name",
                        "status",
                        "attempt_count",
                        "last_error",
                        "updated_at",
                    ),
                    row,
                ))
                for row in connection.execute(
                    """
                    SELECT
                        run_id, cik, manager_name, status, attempt_count,
                        last_error, updated_at
                    FROM performance_manager_state
                    WHERE status IN ('FAILED', 'EXHAUSTED')
                    ORDER BY updated_at DESC
                    LIMIT 50
                    """
                ).fetchall()
            ]
            if has_manager_state
            else []
        )
        return {
            "status": "READY",
            "performance_path": str(path),
            "runs": [
                dict(zip(columns, row)) for row in run_rows
            ],
            "manager_states": manager_state_counts,
            "manager_failures": manager_failures,
            "prices": price_counts,
            "label": PERFORMANCE_LABEL,
            "disclaimer": PERFORMANCE_DISCLAIMER,
        }
    finally:
        connection.close()
