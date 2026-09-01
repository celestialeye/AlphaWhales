from __future__ import annotations

import bisect
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from roster_store import load_roster

from .config import (
    EXPERIMENT_DECOMPOSED_SWEEP,
    EXPERIMENT_FUNDAMENTAL,
    EXPERIMENT_GROUPS,
    EXPERIMENT_MACRO_SECTOR,
    EXPERIMENT_TECHNICAL_COMBINED,
    PROTOCOL_VERSION,
    ResearchConfig,
)
from .macro import (
    DEFAULT_MACRO_DIR,
    SECTOR_ETFS,
    load_macro_bundle,
    macro_features_at,
    sector_proxy_features_at,
    stock_macro_sensitivity_at,
)
from .research import (
    FilingRow,
    FormulaScore,
    HoldingRow,
    ManagerChange,
    ManagerSnapshot,
    SplitAction,
    build_manager_changes,
    build_raw_comparisons,
    build_decomposed_signal_rows,
    build_snapshots,
    detect_split_actions,
    generate_formula_scores,
    normalize_cusip,
    is_direct_common_stock,
    source_fingerprint,
)
from .validation import (
    EvaluationResult,
    apply_selected_candidate,
    evaluate_trust_gate,
    evaluate_walk_forward,
    select_candidate_with_trials,
)


DEFAULT_SOURCE_DB = Path("data/investor_screening/investor_screening.duckdb")
DEFAULT_PERFORMANCE_DB = Path("data/investor_screening/performance.duckdb")
DEFAULT_OUTPUT_DB = Path("data/investor_screening/predictive_sentiment.duckdb")
DEFAULT_ROSTER = Path("roster.json")


RESEARCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_runs (
    run_id VARCHAR PRIMARY KEY,
    protocol_version VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    config_json JSON NOT NULL,
    source_db VARCHAR NOT NULL,
    performance_db VARCHAR NOT NULL,
    roster_path VARCHAR NOT NULL,
    source_fingerprint VARCHAR NOT NULL,
    roster_sha256 VARCHAR NOT NULL,
    trust_status VARCHAR,
    summary_json JSON,
    error VARCHAR
);

CREATE TABLE IF NOT EXISTS manager_snapshots (
    run_id VARCHAR NOT NULL,
    canonical_cik VARCHAR NOT NULL,
    manager_name VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    as_of_date DATE NOT NULL,
    status VARCHAR NOT NULL,
    effective_accessions JSON NOT NULL,
    source_accessions JSON NOT NULL,
    eligible_value DOUBLE NOT NULL,
    position_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshot_positions (
    run_id VARCHAR NOT NULL,
    canonical_cik VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    cusip VARCHAR NOT NULL,
    shares DOUBLE NOT NULL,
    reported_value DOUBLE NOT NULL,
    portfolio_weight DOUBLE NOT NULL
);

CREATE TABLE IF NOT EXISTS run_mapping (
    run_id VARCHAR NOT NULL,
    cusip VARCHAR NOT NULL,
    ticker VARCHAR NOT NULL,
    market_symbol VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    retrieved_at VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS run_top_holdings (
    run_id VARCHAR NOT NULL,
    canonical_cik VARCHAR NOT NULL,
    manager_name VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    holding_rank INTEGER NOT NULL,
    cusip VARCHAR NOT NULL,
    issuer VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    portfolio_weight DOUBLE NOT NULL,
    reported_value DOUBLE NOT NULL
);

CREATE TABLE IF NOT EXISTS run_artifact_provenance (
    run_id VARCHAR NOT NULL,
    artifact_name VARCHAR NOT NULL,
    fingerprint VARCHAR NOT NULL,
    details_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS split_actions (
    run_id VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    cusip VARCHAR NOT NULL,
    factor DOUBLE NOT NULL,
    manager_count INTEGER NOT NULL,
    support_count INTEGER NOT NULL,
    support_fraction DOUBLE NOT NULL,
    median_ratio DOUBLE NOT NULL
);

CREATE TABLE IF NOT EXISTS manager_changes (
    run_id VARCHAR NOT NULL,
    canonical_cik VARCHAR NOT NULL,
    manager_name VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    as_of_date DATE NOT NULL,
    cusip VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    previous_shares DOUBLE NOT NULL,
    current_shares DOUBLE NOT NULL,
    comparison_current_shares DOUBLE NOT NULL,
    previous_value DOUBLE NOT NULL,
    current_value DOUBLE NOT NULL,
    previous_weight DOUBLE NOT NULL,
    current_weight DOUBLE NOT NULL,
    share_change_pct DOUBLE,
    typical_share_change_pct DOUBLE,
    typical_position_weight DOUBLE,
    position_significance DOUBLE,
    relative_conviction DOUBLE,
    force_routine BOOLEAN NOT NULL,
    split_factor DOUBLE
);

CREATE TABLE IF NOT EXISTS formula_scores (
    run_id VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    as_of_date DATE NOT NULL,
    cusip VARCHAR NOT NULL,
    formula_id VARCHAR NOT NULL,
    score DOUBLE,
    published BOOLEAN NOT NULL,
    meaningful_count INTEGER NOT NULL,
    bullish_count INTEGER NOT NULL,
    bearish_count INTEGER NOT NULL,
    breadth_score DOUBLE,
    conviction_score DOUBLE,
    comparable_manager_count INTEGER NOT NULL,
    current_holder_count INTEGER NOT NULL,
    median_current_weight DOUBLE,
    split_affected BOOLEAN NOT NULL,
    reported_value DOUBLE NOT NULL
);

CREATE TABLE IF NOT EXISTS forward_labels (
    run_id VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    as_of_date DATE NOT NULL,
    cusip VARCHAR NOT NULL,
    ticker VARCHAR,
    market_symbol VARCHAR,
    horizon INTEGER NOT NULL,
    status VARCHAR NOT NULL,
    entry_date DATE,
    exit_date DATE,
    entry_index INTEGER,
    exit_index INTEGER,
    security_return DOUBLE,
    spy_return DOUBLE,
    excess_return DOUBLE,
    label INTEGER,
    excess_label INTEGER,
    feature_date DATE,
    price_above_52_week_low_pct DOUBLE,
    price_below_52_week_high_pct DOUBLE,
    sma_50 DOUBLE,
    sma_200 DOUBLE,
    distance_from_sma_200_pct DOUBLE,
    momentum_6m_pct DOUBLE,
    momentum_12m_minus_1m_pct DOUBLE,
    trend_regime VARCHAR
);

CREATE TABLE IF NOT EXISTS walk_forward_selections (
    run_id VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    experiment_group VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    status VARCHAR NOT NULL,
    details_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS walk_forward_predictions (
    run_id VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    report_period DATE NOT NULL,
    cusip VARCHAR NOT NULL,
    formula_id VARCHAR NOT NULL,
    score DOUBLE NOT NULL,
    threshold DOUBLE NOT NULL,
    positive_threshold DOUBLE NOT NULL,
    negative_threshold DOUBLE NOT NULL,
    context_id VARCHAR NOT NULL,
    experiment_group VARCHAR NOT NULL,
    decision_signal VARCHAR NOT NULL,
    label INTEGER NOT NULL,
    prediction INTEGER NOT NULL,
    baseline_prediction INTEGER NOT NULL,
    security_return DOUBLE NOT NULL,
    excess_return DOUBLE NOT NULL,
    split_affected BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_trials (
    run_id VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    experiment_group VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    candidate_order INTEGER NOT NULL,
    selected BOOLEAN NOT NULL,
    details_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS decomposed_features (
    run_id VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    cusip VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    features_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS macro_sector_features (
    run_id VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    cusip VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    features_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_metrics (
    run_id VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    experiment_group VARCHAR NOT NULL,
    metric VARCHAR NOT NULL,
    value DOUBLE
);

CREATE TABLE IF NOT EXISTS rank_ic_by_quarter (
    run_id VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    experiment_group VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    formula_id VARCHAR NOT NULL,
    rank_ic DOUBLE NOT NULL
);

CREATE TABLE IF NOT EXISTS trust_gate_results (
    run_id VARCHAR NOT NULL,
    horizon INTEGER,
    criterion VARCHAR NOT NULL,
    passed BOOLEAN NOT NULL,
    observed_value VARCHAR,
    required_value VARCHAR
);

CREATE TABLE IF NOT EXISTS production_candidates (
    run_id VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    formula_id VARCHAR NOT NULL,
    threshold DOUBLE NOT NULL,
    positive_threshold DOUBLE NOT NULL,
    negative_threshold DOUBLE NOT NULL,
    context_id VARCHAR NOT NULL,
    experiment_group VARCHAR NOT NULL,
    training_end_period DATE NOT NULL,
    details_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS production_candidate_trials (
    run_id VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    candidate_order INTEGER NOT NULL,
    selected BOOLEAN NOT NULL,
    details_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS current_signals (
    run_id VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    experiment_group VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    as_of_date DATE NOT NULL,
    cusip VARCHAR NOT NULL,
    ticker VARCHAR,
    market_symbol VARCHAR,
    alpha_score DOUBLE,
    combined_score DOUBLE,
    research_signal VARCHAR NOT NULL,
    decision_signal VARCHAR NOT NULL,
    decision_reason VARCHAR NOT NULL,
    feature_date DATE,
    price_above_52_week_low_pct DOUBLE,
    trend_regime VARCHAR
);
"""


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    status: str
    snapshot_count: int
    signal_count: int
    label_count: int
    trust_status: str
    blocking_reasons: tuple[str, ...]
    quality: dict[str, float]
    research_signal_counts: dict[str, dict[str, int]]
    current_signal_counts: dict[str, dict[str, int]]
    metrics: dict[str, dict[str, float | int | None]]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _json(value: Any) -> str:
    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(key): clean(value) for key, value in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(value) for value in item]
        if isinstance(item, np.generic):
            return clean(item.item())
        if isinstance(item, float) and not math.isfinite(item):
            return None
        return item

    return json.dumps(
        clean(value),
        default=_json_default,
        sort_keys=True,
        allow_nan=False,
    )


def _validate_local_path(path: Path, label: str) -> Path:
    if "://" in str(path):
        raise ValueError(f"{label} must be a local path")
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} not found: {resolved}")
    return resolved


def _required_columns(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    required: set[str],
) -> None:
    columns = {
        str(row[0])
        for row in connection.execute(f"DESCRIBE {table}").fetchall()
    }
    missing = required - columns
    if missing:
        raise ValueError(
            f"{table} is missing required columns: {sorted(missing)}"
        )


def _load_price_series(
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
    if not path.is_file():
        raise FileNotFoundError(
            f"READY price cache file is missing for {symbol}: {path}"
        )
    actual_hash = _sha256_file(path)
    if actual_hash != row[2]:
        raise ValueError(f"Price cache hash mismatch for {symbol}")
    frame = pd.read_parquet(path, columns=["date", "symbol", "close"])
    symbols = set(frame["symbol"].astype(str).unique())
    if symbols != {symbol}:
        raise ValueError(
            f"Price cache symbol mismatch for {symbol}: {sorted(symbols)}"
        )
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    if frame["close"].isna().any() or (frame["close"] <= 0).any():
        raise ValueError(f"Price cache contains invalid closes for {symbol}")
    frame = frame.drop_duplicates("date", keep="last").sort_values("date")
    return pd.Series(
        frame["close"].to_numpy(dtype=float),
        index=pd.Index(frame["date"], name="date"),
        name=symbol,
    )


def _point_in_time_market_features(
    security: pd.Series,
    entry_date: date,
) -> dict[str, float | str | date | None]:
    history = security[security.index < entry_date]
    if len(history) < 200:
        return {
            "feature_date": None,
            "price_above_52_week_low_pct": None,
            "price_below_52_week_high_pct": None,
            "sma_50": None,
            "sma_200": None,
            "distance_from_sma_200_pct": None,
            "momentum_6m_pct": None,
            "momentum_12m_minus_1m_pct": None,
            "trend_regime": None,
        }
    feature_price = float(history.iloc[-1])
    low_52_week = float(history.tail(252).min())
    high_52_week = float(history.tail(252).max())
    sma_50 = float(history.tail(50).mean())
    sma_200 = float(history.tail(200).mean())
    momentum_6m = feature_price / float(history.iloc[-126]) - 1.0
    momentum_12m_minus_1m = (
        float(history.iloc[-21]) / float(history.iloc[-252]) - 1.0
        if len(history) >= 252
        else None
    )
    bullish_votes = sum(
        (
            feature_price > sma_200,
            sma_50 > sma_200,
            momentum_6m > 0,
        )
    )
    return {
        "feature_date": history.index[-1],
        "price_above_52_week_low_pct": (
            100.0 * (feature_price / low_52_week - 1.0)
            if low_52_week > 0
            else None
        ),
        "price_below_52_week_high_pct": (
            100.0 * (feature_price / high_52_week - 1.0)
            if high_52_week > 0
            else None
        ),
        "sma_50": sma_50,
        "sma_200": sma_200,
        "distance_from_sma_200_pct": (
            100.0 * (feature_price / sma_200 - 1.0)
            if sma_200 > 0
            else None
        ),
        "momentum_6m_pct": 100.0 * momentum_6m,
        "momentum_12m_minus_1m_pct": (
            100.0 * momentum_12m_minus_1m
            if momentum_12m_minus_1m is not None
            else None
        ),
        "trend_regime": (
            "BULLISH"
            if bullish_votes == 3
            else "NEUTRAL"
            if bullish_votes == 2
            else "BEARISH"
        ),
    }


def validate_inputs(
    *,
    source_db: Path = DEFAULT_SOURCE_DB,
    performance_db: Path = DEFAULT_PERFORMANCE_DB,
    roster_path: Path = DEFAULT_ROSTER,
) -> dict:
    source_path = _validate_local_path(source_db, "source database")
    performance_path = _validate_local_path(
        performance_db, "performance database"
    )
    roster = load_roster(_validate_local_path(roster_path, "roster"))

    source = duckdb.connect(str(source_path), read_only=True)
    performance = duckdb.connect(str(performance_path), read_only=True)
    try:
        _required_columns(
            source,
            "submissions",
            {
                "accession_number",
                "filing_date",
                "submission_type",
                "cik",
                "period_of_report",
            },
        )
        _required_columns(
            source,
            "holdings",
            {
                "accession_number",
                "cusip",
                "value_usd",
                "shares_or_principal",
                "shares_or_principal_type",
                "put_call",
            },
        )
        _required_columns(
            performance,
            "price_manifest",
            {
                "symbol",
                "status",
                "parquet_path",
                "parquet_sha256",
            },
        )
        _required_columns(
            performance,
            "cusip_ticker_mapping",
            {
                "cusip",
                "ticker",
                "market_symbol",
                "source",
                "retrieved_at",
            },
        )
        spy = _load_price_series(performance, "SPY")
        if spy is None or spy.empty:
            raise ValueError("A READY SPY price series is required")
        source_ciks = sorted(
            {
                value
                for fund in roster
                for value in [
                    fund["cik"],
                    *fund.get("historical_ciks", []),
                ]
            }
        )
        placeholders = ",".join("?" for _ in source_ciks)
        filing_summary = source.execute(
            f"""
            SELECT
                min(period_of_report),
                max(period_of_report),
                count(DISTINCT accession_number),
                count(DISTINCT cik)
            FROM submissions
            WHERE submission_type IN ('13F-HR', '13F-HR/A')
              AND cik IN ({placeholders})
            """,
            source_ciks,
        ).fetchone()
        mapping_count = performance.execute(
            "SELECT count(*) FROM cusip_ticker_mapping"
        ).fetchone()[0]
        return {
            "source_db": str(source_path),
            "performance_db": str(performance_path),
            "roster_managers": len(roster),
            "source_ciks": len(source_ciks),
            "minimum_report_period": filing_summary[0],
            "maximum_report_period": filing_summary[1],
            "accession_count": filing_summary[2],
            "filing_cik_count": filing_summary[3],
            "mapping_count": mapping_count,
            "spy_min_date": min(spy.index),
            "spy_max_date": max(spy.index),
            "spy_sessions": len(spy),
        }
    finally:
        source.close()
        performance.close()


def _load_source_data(
    source: duckdb.DuckDBPyConnection,
    roster: list[dict],
) -> tuple[
    list[FilingRow],
    dict[str, list[HoldingRow]],
    dict[str, str],
]:
    managers = {fund["cik"]: fund["manager"] for fund in roster}
    canonical_by_source = {
        source_cik: fund["cik"]
        for fund in roster
        for source_cik in [
            fund["cik"],
            *fund.get("historical_ciks", []),
        ]
    }
    source_ciks = sorted(canonical_by_source)
    placeholders = ",".join("?" for _ in source_ciks)
    rows = source.execute(
        f"""
        SELECT
            s.accession_number,
            s.cik,
            s.period_of_report,
            s.filing_date,
            s.submission_type,
            upper(coalesce(cp.amendment_type, '')),
            coalesce(cp.filing_manager_name, '')
        FROM submissions s
        LEFT JOIN cover_pages cp USING (accession_number)
        WHERE s.submission_type IN ('13F-HR', '13F-HR/A')
          AND s.cik IN ({placeholders})
        ORDER BY s.period_of_report, s.filing_date, s.accession_number
        """,
        source_ciks,
    ).fetchall()
    filings = [
        FilingRow(
            accession_number=str(row[0]),
            canonical_cik=canonical_by_source[str(row[1])],
            source_cik=str(row[1]),
            report_period=row[2],
            filing_date=row[3],
            submission_type=str(row[4]),
            amendment_type=str(row[5]),
            manager_name=(
                str(row[6]).strip()
                or managers[canonical_by_source[str(row[1])]]
            ),
        )
        for row in rows
    ]
    holding_rows = source.execute(
        f"""
        SELECT
            h.accession_number,
            h.cusip,
            h.value_usd,
            h.shares_or_principal,
            coalesce(h.shares_or_principal_type, ''),
            coalesce(h.put_call, ''),
            coalesce(h.name_of_issuer, ''),
            coalesce(h.title_of_class, '')
        FROM holdings h
        JOIN submissions s USING (accession_number)
        WHERE s.submission_type IN ('13F-HR', '13F-HR/A')
          AND s.cik IN ({placeholders})
        ORDER BY h.accession_number, h.infotable_sk
        """,
        source_ciks,
    ).fetchall()
    holdings: defaultdict[str, list[HoldingRow]] = defaultdict(list)
    for row in holding_rows:
        holdings[str(row[0])].append(
            HoldingRow(
                accession_number=str(row[0]),
                cusip=normalize_cusip(row[1]),
                value_usd=float(row[2] or 0.0),
                shares=float(row[3] or 0.0),
                shares_type=str(row[4]),
                put_call=str(row[5]),
                issuer=str(row[6]),
                title=str(row[7]),
            )
        )
    return filings, dict(holdings), managers


def _load_current_top_holdings(
    source: duckdb.DuckDBPyConnection,
    roster: list[dict],
    *,
    top_n: int,
) -> list[dict]:
    result = []
    for fund in roster:
        source_ciks = [
            fund["cik"],
            *fund.get("historical_ciks", []),
        ]
        placeholders = ",".join("?" for _ in source_ciks)
        period_row = source.execute(
            f"""
            SELECT max(period_of_report)
            FROM v_effective_holdings
            WHERE cik IN ({placeholders})
            """,
            source_ciks,
        ).fetchone()
        report_period = period_row[0] if period_row else None
        if report_period is None:
            continue
        rows = source.execute(
            f"""
            SELECT
                cusip, name_of_issuer, title_of_class,
                portfolio_weight_pct, value_usd,
                shares_or_principal_type, put_call
            FROM v_effective_holdings
            WHERE cik IN ({placeholders})
              AND period_of_report = ?
            ORDER BY portfolio_weight_pct DESC, value_usd DESC, cusip
            """,
            [*source_ciks, report_period],
        ).fetchall()
        aggregated: dict[str, dict] = {}
        for (
            raw_cusip,
            issuer,
            title,
            portfolio_weight,
            value_usd,
            shares_type,
            put_call,
        ) in rows:
            cusip = normalize_cusip(raw_cusip)
            if (
                cusip is None
                or not is_direct_common_stock(
                    issuer=issuer,
                    title=title,
                    shares_type=shares_type,
                    put_call=put_call,
                )
            ):
                continue
            item = aggregated.setdefault(
                cusip,
                {
                    "cusip": cusip,
                    "issuer": str(issuer or ""),
                    "title": str(title or ""),
                    "portfolio_weight": 0.0,
                    "reported_value": 0.0,
                },
            )
            item["portfolio_weight"] += float(portfolio_weight or 0.0)
            item["reported_value"] += float(value_usd or 0.0)
        ranked = sorted(
            aggregated.values(),
            key=lambda item: (
                -item["portfolio_weight"],
                -item["reported_value"],
                item["cusip"],
            ),
        )[:top_n]
        for holding_rank, item in enumerate(ranked, start=1):
            result.append(
                {
                    "canonical_cik": fund["cik"],
                    "manager_name": fund["manager"],
                    "report_period": report_period,
                    "holding_rank": holding_rank,
                    **item,
                }
            )
    return result


def _load_mapping(
    performance: duckdb.DuckDBPyConnection,
) -> dict[str, tuple[str, str, str, str]]:
    return {
        str(cusip): (
            str(ticker),
            str(market_symbol),
            str(source),
            str(retrieved_at),
        )
        for cusip, ticker, market_symbol, source, retrieved_at in (
            performance.execute(
                """
                SELECT cusip, ticker, market_symbol, source, retrieved_at
                FROM cusip_ticker_mapping
                """
            ).fetchall()
        )
    }


def _build_labels(
    scores: list[FormulaScore],
    *,
    mapping: dict[str, tuple[str, str, str, str]],
    performance: duckdb.DuckDBPyConnection,
    config: ResearchConfig,
) -> tuple[list[dict], dict[str, float]]:
    published = {
        (score.report_period, score.cusip): score
        for score in scores
        if score.formula_id == "alpha_v1_n3"
        and score.score is not None
        and score.meaningful_count >= config.minimum_meaningful_managers
        and score.current_holder_count >= config.minimum_current_holders
    }
    total_value = sum(score.reported_value for score in published.values())
    mapped_value = sum(
        score.reported_value
        for score in published.values()
        if score.cusip in mapping
    )
    mapping_coverage = mapped_value / total_value if total_value else 0.0
    spy = _load_price_series(performance, "SPY")
    if spy is None:
        raise ValueError("SPY price series is unavailable")
    spy_dates = list(spy.index)
    price_cache: dict[str, pd.Series | None] = {"SPY": spy}
    labels: list[dict] = []
    for (report_period, cusip), score in sorted(published.items()):
        mapping_row = mapping.get(cusip)
        ticker = mapping_row[0] if mapping_row else None
        symbol = mapping_row[1] if mapping_row else None
        entry_index = bisect.bisect_right(spy_dates, score.as_of_date)
        for horizon in config.horizons:
            record = {
                "report_period": report_period,
                "as_of_date": score.as_of_date,
                "cusip": cusip,
                "ticker": ticker,
                "market_symbol": symbol,
                "horizon": horizon,
                "status": "READY",
                "entry_date": None,
                "exit_date": None,
                "entry_index": None,
                "exit_index": None,
                "security_return": None,
                "spy_return": None,
                "excess_return": None,
                "label": None,
                "excess_label": None,
                "feature_date": None,
                "price_above_52_week_low_pct": None,
                "price_below_52_week_high_pct": None,
                "sma_50": None,
                "sma_200": None,
                "distance_from_sma_200_pct": None,
                "momentum_6m_pct": None,
                "momentum_12m_minus_1m_pct": None,
                "trend_regime": None,
            }
            if entry_index >= len(spy_dates):
                record["status"] = "INSUFFICIENT_SPY_HORIZON"
                labels.append(record)
                continue
            entry_date = spy_dates[entry_index]
            if (entry_date - score.as_of_date).days > 7:
                record["status"] = "NO_SPY_ENTRY_SESSION"
                labels.append(record)
                continue
            exit_index = entry_index + horizon
            record.update(
                {
                    "entry_date": entry_date,
                    "entry_index": entry_index,
                    "exit_index": (
                        exit_index if exit_index < len(spy_dates) else None
                    ),
                }
            )
            if mapping_row is None:
                record["status"] = (
                    "NO_MAPPING"
                    if exit_index < len(spy_dates)
                    else "INSUFFICIENT_SPY_HORIZON"
                )
                labels.append(record)
                continue
            if symbol == "SPY":
                record["status"] = "BENCHMARK_SECURITY"
                labels.append(record)
                continue
            if symbol not in price_cache:
                price_cache[symbol] = _load_price_series(
                    performance, symbol
                )
            security = price_cache[symbol]
            if security is None:
                record["status"] = "NO_PRICE_SERIES"
                labels.append(record)
                continue
            if entry_date not in security.index:
                record["status"] = "NO_ENTRY_PRICE"
                labels.append(record)
                continue
            record.update(
                _point_in_time_market_features(security, entry_date)
            )
            if exit_index >= len(spy_dates):
                record["status"] = "INSUFFICIENT_SPY_HORIZON"
                labels.append(record)
                continue
            exit_date = spy_dates[exit_index]
            record.update(
                {"exit_date": exit_date, "exit_index": exit_index}
            )
            if exit_date not in security.index:
                record["status"] = "NO_EXIT_PRICE"
            else:
                security_return = (
                    float(security.loc[exit_date])
                    / float(security.loc[entry_date])
                    - 1.0
                )
                spy_return = (
                    float(spy.loc[exit_date])
                    / float(spy.loc[entry_date])
                    - 1.0
                )
                excess = security_return - spy_return
                record.update(
                    {
                        "security_return": security_return,
                        "spy_return": spy_return,
                        "excess_return": excess,
                        "label": int(security_return > 0),
                        "excess_label": int(excess > 0),
                    }
                )
            labels.append(record)
    quality = {
        "mapping_coverage": mapping_coverage,
    }
    label_coverages = []
    terminal_rates = []
    for horizon in config.horizons:
        horizon_rows = [
            item for item in labels if item["horizon"] == horizon
        ]
        eligible = [
            item
            for item in horizon_rows
            if item["status"]
            not in {
                "INSUFFICIENT_SPY_HORIZON",
                "NO_SPY_ENTRY_SESSION",
                "BENCHMARK_SECURITY",
            }
        ]
        ready = sum(item["status"] == "READY" for item in eligible)
        label_coverage = ready / len(eligible) if eligible else 0.0
        terminal_expected = [
            item
            for item in horizon_rows
            if item["status"] in {"READY", "NO_EXIT_PRICE"}
        ]
        terminal_missing = sum(
            item["status"] == "NO_EXIT_PRICE"
            for item in terminal_expected
        )
        terminal_rate = (
            terminal_missing / len(terminal_expected)
            if terminal_expected
            else 0.0
        )
        quality[f"label_coverage_{horizon}"] = label_coverage
        quality[f"missing_terminal_price_rate_{horizon}"] = terminal_rate
        label_coverages.append(label_coverage)
        terminal_rates.append(terminal_rate)
    quality["label_coverage"] = min(label_coverages, default=0.0)
    quality["missing_terminal_price_rate"] = max(
        terminal_rates,
        default=0.0,
    )
    return labels, quality


def _observation_frame(
    scores: list[FormulaScore],
    labels: list[dict],
    *,
    config: ResearchConfig,
) -> pd.DataFrame:
    label_by_key = {
        (item["report_period"], item["cusip"], item["horizon"]): item
        for item in labels
        if item["status"] == "READY"
    }
    rows = []
    for score in scores:
        if (
            score.score is None
            or score.meaningful_count < config.minimum_meaningful_managers
            or score.current_holder_count < config.minimum_current_holders
        ):
            continue
        for horizon in {
            key[2]
            for key in label_by_key
            if key[0] == score.report_period and key[1] == score.cusip
        }:
            label = label_by_key[
                (score.report_period, score.cusip, horizon)
            ]
            rows.append(
                {
                    "report_period": score.report_period,
                    "cusip": score.cusip,
                    "formula_id": score.formula_id,
                    "score": score.score,
                    "split_affected": score.split_affected,
                    "horizon": horizon,
                    "entry_index": label["entry_index"],
                    "exit_index": label["exit_index"],
                    "excess_return": label["excess_return"],
                    "target_return": label["security_return"],
                    "label": label["label"],
                    "excess_label": label["excess_label"],
                    "feature_date": label["feature_date"],
                    "price_above_52_week_low_pct": label[
                        "price_above_52_week_low_pct"
                    ],
                    "price_below_52_week_high_pct": label[
                        "price_below_52_week_high_pct"
                    ],
                    "sma_50": label["sma_50"],
                    "sma_200": label["sma_200"],
                    "distance_from_sma_200_pct": label[
                        "distance_from_sma_200_pct"
                    ],
                    "momentum_6m_pct": label["momentum_6m_pct"],
                    "momentum_12m_minus_1m_pct": label[
                        "momentum_12m_minus_1m_pct"
                    ],
                    "trend_regime": label["trend_regime"],
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "report_period",
            "cusip",
            "formula_id",
            "score",
            "split_affected",
            "horizon",
            "entry_index",
            "exit_index",
            "excess_return",
            "target_return",
            "label",
            "excess_label",
            "feature_date",
            "price_above_52_week_low_pct",
            "price_below_52_week_high_pct",
            "sma_50",
            "sma_200",
            "distance_from_sma_200_pct",
            "momentum_6m_pct",
            "momentum_12m_minus_1m_pct",
            "trend_regime",
        ],
    )


def _centered_rank(values: pd.Series) -> pd.Series:
    result = pd.Series(math.nan, index=values.index, dtype=float)
    valid = values.dropna()
    if valid.empty:
        return result
    if valid.nunique() == 1:
        result.loc[valid.index] = 0.0
        return result
    count = len(valid)
    midpoint = (count + 1.0) / 2.0
    half_range = (count - 1.0) / 2.0
    result.loc[valid.index] = (
        (valid.rank(method="average") - midpoint) / half_range * 100.0
    )
    return result


def _decomposed_observation_frame(
    changes: list[ManagerChange],
    scores: list[FormulaScore],
    labels: list[dict],
    *,
    config: ResearchConfig,
    require_ready: bool = True,
) -> pd.DataFrame:
    institutional = pd.DataFrame(
        build_decomposed_signal_rows(changes, scores)
    )
    if institutional.empty:
        return pd.DataFrame()
    institutional = institutional[
        institutional["current_holder_count"]
        >= config.minimum_current_holders
    ].copy()
    rank_sources = {
        "median_weight_rank": "median_current_weight",
        "max_weight_rank": "max_current_weight",
        "holder_count_rank": "current_holder_count",
        "crowding_hhi_rank": "held_value_hhi",
        "top_holder_rank": "top_holder_value_share",
    }
    for output, source in rank_sources.items():
        institutional[output] = institutional.groupby(
            "report_period",
            group_keys=False,
        )[source].apply(_centered_rank)
    institutional["portfolio_weight_score"] = (
        0.50 * institutional["median_weight_rank"].fillna(0.0)
        + 0.30 * institutional["max_weight_rank"].fillna(0.0)
        + 0.20 * institutional["holder_count_rank"].fillna(0.0)
    )
    institutional["crowding_score"] = -(
        0.60 * institutional["crowding_hhi_rank"].fillna(0.0)
        + 0.40 * institutional["top_holder_rank"].fillna(0.0)
    )
    institutional["acceleration_score"] = institutional[
        "alpha_acceleration"
    ].fillna(0.0)

    feature_labels = pd.DataFrame(
        [
            item
            for item in labels
            if item["feature_date"] is not None
        ]
    )
    if feature_labels.empty:
        return pd.DataFrame()
    label_columns = [
        "report_period",
        "cusip",
        "horizon",
        "status",
        "ticker",
        "market_symbol",
        "feature_date",
        "entry_index",
        "exit_index",
        "security_return",
        "excess_return",
        "label",
        "excess_label",
        "price_above_52_week_low_pct",
        "price_below_52_week_high_pct",
        "distance_from_sma_200_pct",
        "momentum_6m_pct",
        "momentum_12m_minus_1m_pct",
        "trend_regime",
    ]
    frame = institutional.merge(
        feature_labels[label_columns],
        on=["report_period", "cusip"],
        how="inner",
    )
    for output, source in {
        "momentum_6m_rank": "momentum_6m_pct",
        "momentum_12m1m_rank": "momentum_12m_minus_1m_pct",
        "high_proximity_rank": "price_below_52_week_high_pct",
        "sma_200_distance_rank": "distance_from_sma_200_pct",
    }.items():
        frame[output] = frame.groupby(
            ["report_period", "horizon"],
            group_keys=False,
        )[source].apply(_centered_rank)
    trend_score = frame["trend_regime"].map(
        {"BULLISH": 100.0, "NEUTRAL": 0.0, "BEARISH": -100.0}
    ).fillna(0.0)
    frame["technical_score"] = (
        0.35 * frame["momentum_12m1m_rank"].fillna(0.0)
        + 0.25 * frame["momentum_6m_rank"].fillna(0.0)
        + 0.25 * frame["high_proximity_rank"].fillna(0.0)
        + 0.15 * trend_score
    )
    frame["formula_id"] = "decomposed_v1"
    frame["score"] = frame["alpha_score"]
    frame["target_return"] = frame["security_return"]
    if require_ready:
        frame = frame[frame["status"] == "READY"].copy()
    return frame


def _awfi_base_scores(frame: pd.DataFrame) -> pd.Series:
    numerator = (
        frame["new_strength"]
        + 0.75 * frame["increased_strength"]
        - 0.25 * frame["decreased_strength"]
        - 0.25 * frame["closed_strength"]
    )
    denominator = (
        frame["new_strength"]
        + 0.75 * frame["increased_strength"]
        + 0.25 * frame["decreased_strength"]
        + 0.25 * frame["closed_strength"]
    )
    action_score = pd.Series(
        np.where(denominator > 0, 100.0 * numerator / denominator, 0.0),
        index=frame.index,
        dtype=float,
    )
    result = pd.Series(0.0, index=frame.index, dtype=float)
    weights = {
        126: (0.34, 0.34, 0.17, 0.15),
        252: (0.425, 0.2125, 0.2125, 0.15),
        378: (0.50, 0.25, 0.25, 0.0),
        504: (0.50, 0.25, 0.25, 0.0),
    }
    for horizon, values in weights.items():
        selected = frame["horizon"] == horizon
        result.loc[selected] = (
            values[0] * frame.loc[selected, "alpha_score"]
            + values[1] * action_score.loc[selected]
            + values[2] * frame.loc[selected, "portfolio_weight_score"]
            + values[3] * frame.loc[selected, "technical_score"]
        )
    return result.clip(-100.0, 100.0)


def _macro_sector_observation_frame(
    decomposed_current: pd.DataFrame,
    performance: duckdb.DuckDBPyConnection,
    macro_bundle: dict[str, pd.Series],
) -> pd.DataFrame:
    if decomposed_current.empty:
        return pd.DataFrame()
    frame = decomposed_current.copy()
    spy = _load_price_series(performance, "SPY")
    if spy is None:
        raise ValueError("SPY is required for macro-sector features")
    sector_prices = {
        symbol: series
        for symbol in SECTOR_ETFS
        if (series := _load_price_series(performance, symbol)) is not None
    }
    if len(sector_prices) != len(SECTOR_ETFS):
        missing = sorted(set(SECTOR_ETFS) - set(sector_prices))
        raise ValueError(f"Sector ETF price history is missing: {missing}")
    stock_cache: dict[str, pd.Series | None] = {}
    macro_cache: dict[date, dict] = {}
    sector_cache: dict[tuple[str, date], dict] = {}
    sensitivity_cache: dict[tuple[str, date], dict] = {}
    macro_rows = []
    for _, row in frame.iterrows():
        feature_date = row["feature_date"]
        symbol = row["market_symbol"]
        if feature_date not in macro_cache:
            macro_cache[feature_date] = macro_features_at(
                macro_bundle,
                spy,
                feature_date=feature_date,
            )
        key = (symbol, feature_date)
        if key not in sector_cache:
            if symbol not in stock_cache:
                stock_cache[symbol] = _load_price_series(
                    performance,
                    symbol,
                )
            stock = stock_cache[symbol]
            sector_cache[key] = (
                sector_proxy_features_at(
                    stock,
                    sector_prices,
                    spy,
                    feature_date=feature_date,
                )
                if stock is not None
                else {
                    "sector_proxy": None,
                    "relative_12m1m": None,
                    "relative_6m": None,
                    "sector_vs_spy_6m": None,
                }
            )
        if key not in sensitivity_cache:
            macro_values = macro_cache[feature_date]
            sector_symbol = sector_cache[key].get("sector_proxy")
            stock = stock_cache.get(symbol)
            if (
                stock is not None
                and sector_symbol in sector_prices
                and macro_values.get("market_score") is not None
                and macro_values.get("yield_6m_change_score") is not None
                and macro_values.get("dxy_6m_score") is not None
            ):
                sensitivity_cache[key] = stock_macro_sensitivity_at(
                    stock,
                    sector_prices[sector_symbol],
                    spy,
                    macro_bundle["DGS10"],
                    macro_bundle["DXY"],
                    feature_date=feature_date,
                    market_score=macro_values["market_score"],
                    yield_6m_score=macro_values[
                        "yield_6m_change_score"
                    ],
                    dxy_6m_score=macro_values["dxy_6m_score"],
                )
            else:
                sensitivity_cache[key] = {
                    "market_sensitivity_raw": None,
                    "rate_sensitivity_raw": None,
                    "dxy_sensitivity_raw": None,
                }
        macro_rows.append(
            {
                **macro_cache[feature_date],
                **sector_cache[key],
                **sensitivity_cache[key],
            }
        )
    macro_frame = pd.DataFrame(macro_rows, index=frame.index)
    frame = pd.concat([frame, macro_frame], axis=1)
    for output, source in {
        "relative_12m1m_rank": "relative_12m1m",
        "relative_6m_rank": "relative_6m",
        "sector_vs_spy_rank": "sector_vs_spy_6m",
    }.items():
        frame[output] = frame.groupby(
            ["report_period", "horizon"],
            group_keys=False,
        )[source].apply(_centered_rank)
    frame["sector_score"] = (
        0.45 * frame["relative_12m1m_rank"]
        + 0.30 * frame["relative_6m_rank"]
        + 0.25 * frame["sector_vs_spy_rank"]
    )
    for output, source in {
        "market_sensitivity_rank": "market_sensitivity_raw",
        "rate_sensitivity_rank": "rate_sensitivity_raw",
        "dxy_sensitivity_rank": "dxy_sensitivity_raw",
    }.items():
        frame[output] = frame.groupby(
            ["report_period", "horizon"],
            group_keys=False,
        )[source].apply(_centered_rank)
    frame["sensitivity_score"] = (
        0.40 * frame["market_sensitivity_rank"]
        + 0.30 * frame["rate_sensitivity_rank"]
        + 0.30 * frame["dxy_sensitivity_rank"]
    )
    frame["base_awfi_score"] = _awfi_base_scores(frame)
    frame["formula_id"] = "awfi_msr_v1"
    frame["score"] = frame["base_awfi_score"]
    return frame[frame["status"] == "READY"].copy()


def _build_current_signals(
    scores: list[FormulaScore],
    labels: list[dict],
    observations: pd.DataFrame,
    decomposed_current: pd.DataFrame,
    *,
    config: ResearchConfig,
    deployable: bool,
) -> tuple[
    list[dict],
    list[dict],
    list[dict],
    dict[str, dict[str, int]],
    dict[str, dict[str, int]],
]:
    feature_rows = {
        (item["report_period"], item["cusip"], item["horizon"]): item
        for item in labels
        if item["feature_date"] is not None
    }
    score_rows = []
    for score in scores:
        if (
            score.score is None
            or score.meaningful_count < config.minimum_meaningful_managers
            or score.current_holder_count < config.minimum_current_holders
        ):
            continue
        for horizon in config.horizons:
            feature = feature_rows.get(
                (score.report_period, score.cusip, horizon)
            )
            if feature is None:
                continue
            score_rows.append(
                {
                    "report_period": score.report_period,
                    "as_of_date": score.as_of_date,
                    "cusip": score.cusip,
                    "ticker": feature["ticker"],
                    "market_symbol": feature["market_symbol"],
                    "formula_id": score.formula_id,
                    "score": score.score,
                    "horizon": horizon,
                    "entry_index": feature["entry_index"],
                    "feature_date": feature["feature_date"],
                    "price_above_52_week_low_pct": feature[
                        "price_above_52_week_low_pct"
                    ],
                    "sma_50": feature["sma_50"],
                    "sma_200": feature["sma_200"],
                    "momentum_6m_pct": feature["momentum_6m_pct"],
                    "trend_regime": feature["trend_regime"],
                }
            )
    feature_frame = (
        decomposed_current.copy()
        if not decomposed_current.empty
        else pd.DataFrame(score_rows)
    )
    candidates: list[dict] = []
    production_trials: list[dict] = []
    signals: list[dict] = []
    research_counts: dict[str, dict[str, int]] = {}
    decision_counts: dict[str, dict[str, int]] = {}
    for horizon in config.horizons:
        horizon_frame = feature_frame[
            feature_frame["horizon"] == horizon
        ] if not feature_frame.empty else pd.DataFrame()
        if horizon_frame.empty:
            empty = {"BUY": 0, "HOLD": 0, "SELL": 0}
            research_counts[str(horizon)] = dict(empty)
            decision_counts[str(horizon)] = dict(empty)
            continue
        latest_period = horizon_frame["report_period"].max()
        current = horizon_frame[
            horizon_frame["report_period"] == latest_period
        ].copy()
        current_entry_index = int(current["entry_index"].min())
        training = _production_training_rows(
            observations,
            horizon=horizon,
            latest_period=latest_period,
            current_entry_index=current_entry_index,
            embargo_sessions=config.embargo_sessions,
        )
        candidate, horizon_trials = select_candidate_with_trials(
            training,
            config,
            experiment_group=EXPERIMENT_DECOMPOSED_SWEEP,
        )
        for trial in horizon_trials:
            production_trials.append(
                {
                    "horizon": horizon,
                    "selected": (
                        candidate is not None
                        and trial["candidate_order"]
                        == candidate["candidate_order"]
                    ),
                    **trial,
                }
            )
        if candidate is None:
            current = current.copy()
            current["score"] = current.get(
                "alpha_score",
                current.get("score"),
            )
            current["decision_signal"] = "HOLD"
            current["selected_formula_id"] = "decomposed_v1"
            current["selected_positive_threshold"] = 25.0
            current["selected_negative_threshold"] = 25.0
            current["selected_context_id"] = "NO_SELECTABLE_CANDIDATE"
        else:
            current = apply_selected_candidate(current, candidate)
            candidates.append(
                {
                    "horizon": horizon,
                    "formula_id": candidate["formula_id"],
                    "positive_threshold": float(
                        candidate["positive_threshold"]
                    ),
                    "negative_threshold": float(
                        candidate["negative_threshold"]
                    ),
                    "context_id": candidate["context_id"],
                    "experiment_group": (
                        EXPERIMENT_DECOMPOSED_SWEEP
                    ),
                    "training_end_period": training[
                        "report_period"
                    ].max(),
                    "details_json": candidate,
                }
            )
        for _, row in current.iterrows():
            research_signal = row["decision_signal"]
            decision_signal = research_signal if deployable else "HOLD"
            if not deployable:
                reason = "RESEARCH_NOT_TRUSTWORTHY"
            elif research_signal != "HOLD":
                reason = row["selected_context_id"]
            elif (
                -float(row["selected_negative_threshold"])
                < float(row["score"])
                < float(row["selected_positive_threshold"])
            ):
                reason = "ALPHA_SENTIMENT_NEUTRAL"
            elif row["feature_date"] is None:
                reason = "SUPPORT_FEATURES_UNAVAILABLE"
            elif row["selected_context_id"] == "NO_SELECTABLE_CANDIDATE":
                reason = "NO_SELECTABLE_CANDIDATE"
            else:
                reason = "SUPPORT_NOT_CONFIRMED"
            signals.append(
                {
                    "horizon": horizon,
                    "experiment_group": (
                        EXPERIMENT_DECOMPOSED_SWEEP
                    ),
                    "report_period": row["report_period"],
                    "as_of_date": row["as_of_date"],
                    "cusip": row["cusip"],
                    "ticker": row["ticker"],
                    "market_symbol": row["market_symbol"],
                    "alpha_score": row.get("alpha_score", row["score"]),
                    "combined_score": row["score"],
                    "research_signal": research_signal,
                    "decision_signal": decision_signal,
                    "decision_reason": reason,
                    "feature_date": row["feature_date"],
                    "price_above_52_week_low_pct": row[
                        "price_above_52_week_low_pct"
                    ],
                    "trend_regime": row["trend_regime"],
                }
            )
        research_value_counts = current["decision_signal"].value_counts()
        research_counts[str(horizon)] = {
            signal: int(research_value_counts.get(signal, 0))
            for signal in ("BUY", "HOLD", "SELL")
        }
        decision_counts[str(horizon)] = (
            dict(research_counts[str(horizon)])
            if deployable
            else {
                "BUY": 0,
                "HOLD": int(len(current)),
                "SELL": 0,
            }
        )
    return (
        candidates,
        production_trials,
        signals,
        research_counts,
        decision_counts,
    )


def _production_training_rows(
    observations: pd.DataFrame,
    *,
    horizon: int,
    latest_period: date,
    current_entry_index: int,
    embargo_sessions: int,
) -> pd.DataFrame:
    return observations[
        (observations["horizon"] == horizon)
        & (observations["report_period"] < latest_period)
        & (
            observations["exit_index"]
            < current_entry_index - embargo_sessions
        )
    ]


def _connect_output(path: Path) -> duckdb.DuckDBPyConnection:
    if "://" in str(path):
        raise ValueError("output database must be a local path")
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(resolved))
    connection.execute(RESEARCH_SCHEMA)
    connection.execute(
        "ALTER TABLE forward_labels ADD COLUMN IF NOT EXISTS feature_date DATE"
    )
    connection.execute(
        """
        ALTER TABLE forward_labels
        ADD COLUMN IF NOT EXISTS price_above_52_week_low_pct DOUBLE
        """
    )
    connection.execute(
        """
        ALTER TABLE forward_labels
        ADD COLUMN IF NOT EXISTS price_below_52_week_high_pct DOUBLE
        """
    )
    connection.execute(
        "ALTER TABLE forward_labels ADD COLUMN IF NOT EXISTS sma_50 DOUBLE"
    )
    connection.execute(
        "ALTER TABLE forward_labels ADD COLUMN IF NOT EXISTS sma_200 DOUBLE"
    )
    connection.execute(
        """
        ALTER TABLE forward_labels
        ADD COLUMN IF NOT EXISTS distance_from_sma_200_pct DOUBLE
        """
    )
    connection.execute(
        """
        ALTER TABLE forward_labels
        ADD COLUMN IF NOT EXISTS momentum_6m_pct DOUBLE
        """
    )
    connection.execute(
        """
        ALTER TABLE forward_labels
        ADD COLUMN IF NOT EXISTS momentum_12m_minus_1m_pct DOUBLE
        """
    )
    connection.execute(
        """
        ALTER TABLE forward_labels
        ADD COLUMN IF NOT EXISTS trend_regime VARCHAR
        """
    )
    connection.execute(
        """
        ALTER TABLE forward_labels
        ADD COLUMN IF NOT EXISTS excess_label INTEGER
        """
    )
    connection.execute(
        """
        ALTER TABLE formula_scores
        ADD COLUMN IF NOT EXISTS current_holder_count INTEGER DEFAULT 0
        """
    )
    connection.execute(
        """
        ALTER TABLE formula_scores
        ADD COLUMN IF NOT EXISTS median_current_weight DOUBLE
        """
    )
    connection.execute(
        """
        ALTER TABLE walk_forward_predictions
        ADD COLUMN IF NOT EXISTS context_id VARCHAR DEFAULT 'SENTIMENT_ONLY'
        """
    )
    connection.execute(
        """
        ALTER TABLE walk_forward_predictions
        ADD COLUMN IF NOT EXISTS decision_signal VARCHAR DEFAULT 'HOLD'
        """
    )
    connection.execute(
        """
        ALTER TABLE walk_forward_predictions
        ADD COLUMN IF NOT EXISTS security_return DOUBLE
        """
    )
    connection.execute(
        """
        ALTER TABLE walk_forward_selections
        ADD COLUMN IF NOT EXISTS experiment_group VARCHAR
        DEFAULT 'SENTIMENT_ONLY'
        """
    )
    connection.execute(
        """
        ALTER TABLE walk_forward_predictions
        ADD COLUMN IF NOT EXISTS positive_threshold DOUBLE DEFAULT 25
        """
    )
    connection.execute(
        """
        ALTER TABLE walk_forward_predictions
        ADD COLUMN IF NOT EXISTS negative_threshold DOUBLE DEFAULT 25
        """
    )
    connection.execute(
        """
        ALTER TABLE walk_forward_predictions
        ADD COLUMN IF NOT EXISTS experiment_group VARCHAR
        DEFAULT 'SENTIMENT_ONLY'
        """
    )
    connection.execute(
        """
        ALTER TABLE evaluation_metrics
        ADD COLUMN IF NOT EXISTS experiment_group VARCHAR
        DEFAULT 'SENTIMENT_ONLY'
        """
    )
    connection.execute(
        """
        ALTER TABLE rank_ic_by_quarter
        ADD COLUMN IF NOT EXISTS experiment_group VARCHAR
        DEFAULT 'SENTIMENT_ONLY'
        """
    )
    connection.execute(
        """
        ALTER TABLE production_candidates
        ADD COLUMN IF NOT EXISTS positive_threshold DOUBLE DEFAULT 25
        """
    )
    connection.execute(
        """
        ALTER TABLE production_candidates
        ADD COLUMN IF NOT EXISTS negative_threshold DOUBLE DEFAULT 25
        """
    )
    connection.execute(
        """
        ALTER TABLE production_candidates
        ADD COLUMN IF NOT EXISTS experiment_group VARCHAR
        DEFAULT 'TECHNICAL_COMBINED'
        """
    )
    connection.execute(
        """
        ALTER TABLE current_signals
        ADD COLUMN IF NOT EXISTS experiment_group VARCHAR
        DEFAULT 'TECHNICAL_COMBINED'
        """
    )
    connection.execute(
        """
        ALTER TABLE current_signals
        ADD COLUMN IF NOT EXISTS research_signal VARCHAR DEFAULT 'HOLD'
        """
    )
    connection.execute(
        """
        ALTER TABLE current_signals
        ADD COLUMN IF NOT EXISTS combined_score DOUBLE
        """
    )
    return connection


def _delete_run(connection: duckdb.DuckDBPyConnection, run_id: str) -> None:
    tables = [
        "manager_snapshots",
        "snapshot_positions",
        "run_mapping",
        "run_top_holdings",
        "run_artifact_provenance",
        "split_actions",
        "manager_changes",
        "formula_scores",
        "forward_labels",
        "walk_forward_selections",
        "walk_forward_predictions",
        "candidate_trials",
        "decomposed_features",
        "macro_sector_features",
        "evaluation_metrics",
        "rank_ic_by_quarter",
        "trust_gate_results",
        "production_candidates",
        "production_candidate_trials",
        "current_signals",
        "research_runs",
    ]
    for table in tables:
        connection.execute(f"DELETE FROM {table} WHERE run_id = ?", [run_id])


def _persist_research(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    snapshots: list[ManagerSnapshot],
    splits: dict[tuple[date, str], SplitAction],
    changes: list[ManagerChange],
    scores: list[FormulaScore],
    labels: list[dict],
    mapping: dict[str, tuple[str, str, str, str]],
    top_holdings: list[dict],
    evaluations: list[EvaluationResult],
    trust_criteria: list[dict],
    production_candidates: list[dict],
    production_candidate_trials: list[dict],
    current_signals: list[dict],
    decomposed_observations: pd.DataFrame,
    macro_sector_observations: pd.DataFrame,
    artifact_provenance: dict[str, dict],
) -> None:
    connection.executemany(
        "INSERT INTO manager_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                run_id,
                item.canonical_cik,
                item.manager_name,
                item.report_period,
                item.as_of_date,
                item.status,
                _json(item.effective_accessions),
                _json(item.source_accessions),
                item.eligible_value,
                len(item.positions),
            )
            for item in snapshots
        ],
    )
    position_rows = [
        (
            run_id,
            snapshot.canonical_cik,
            snapshot.report_period,
            position.cusip,
            position.shares,
            position.reported_value,
            position.weight,
        )
        for snapshot in snapshots
        for position in snapshot.positions
    ]
    if position_rows:
        connection.executemany(
            "INSERT INTO snapshot_positions VALUES (?, ?, ?, ?, ?, ?, ?)",
            position_rows,
        )
    used_cusips = sorted(
        {item["cusip"] for item in labels if item["cusip"] in mapping}
    )
    if used_cusips:
        connection.executemany(
            "INSERT INTO run_mapping VALUES (?, ?, ?, ?, ?, ?)",
            [
                (run_id, cusip, *mapping[cusip])
                for cusip in used_cusips
            ],
        )
    if top_holdings:
        connection.executemany(
            """
            INSERT INTO run_top_holdings
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    item["canonical_cik"],
                    item["manager_name"],
                    item["report_period"],
                    item["holding_rank"],
                    item["cusip"],
                    item["issuer"],
                    item["title"],
                    item["portfolio_weight"],
                    item["reported_value"],
                )
                for item in top_holdings
            ],
        )
    if artifact_provenance:
        connection.executemany(
            """
            INSERT INTO run_artifact_provenance
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    name,
                    values["fingerprint"],
                    _json(values),
                )
                for name, values in sorted(artifact_provenance.items())
            ],
        )
    if splits:
        connection.executemany(
            "INSERT INTO split_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id,
                    item.report_period,
                    item.cusip,
                    item.factor,
                    item.manager_count,
                    item.support_count,
                    item.support_fraction,
                    item.median_ratio,
                )
                for item in splits.values()
            ],
        )
    if changes:
        connection.executemany(
            """
            INSERT INTO manager_changes VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [(run_id, *asdict(item).values()) for item in changes],
        )
    if scores:
        connection.executemany(
            """
            INSERT INTO formula_scores (
                run_id, report_period, as_of_date, cusip, formula_id,
                score, published, meaningful_count, bullish_count,
                bearish_count, breadth_score, conviction_score,
                comparable_manager_count, current_holder_count,
                median_current_weight, split_affected, reported_value
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [(run_id, *asdict(item).values()) for item in scores],
        )
    if labels:
        connection.executemany(
            """
            INSERT INTO forward_labels (
                run_id, report_period, as_of_date, cusip, ticker,
                market_symbol, horizon, status, entry_date, exit_date,
                entry_index, exit_index, security_return, spy_return,
                excess_return, label, feature_date,
                excess_label,
                price_above_52_week_low_pct,
                price_below_52_week_high_pct,
                sma_50, sma_200, distance_from_sma_200_pct,
                momentum_6m_pct, momentum_12m_minus_1m_pct,
                trend_regime
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                (
                    run_id,
                    item["report_period"],
                    item["as_of_date"],
                    item["cusip"],
                    item["ticker"],
                    item["market_symbol"],
                    item["horizon"],
                    item["status"],
                    item["entry_date"],
                    item["exit_date"],
                    item["entry_index"],
                    item["exit_index"],
                    item["security_return"],
                    item["spy_return"],
                    item["excess_return"],
                    item["label"],
                    item["feature_date"],
                    item["excess_label"],
                    item["price_above_52_week_low_pct"],
                    item["price_below_52_week_high_pct"],
                    item["sma_50"],
                    item["sma_200"],
                    item["distance_from_sma_200_pct"],
                    item["momentum_6m_pct"],
                    item["momentum_12m_minus_1m_pct"],
                    item["trend_regime"],
                )
                for item in labels
            ],
        )
    for evaluation in evaluations:
        if not evaluation.selections.empty:
            connection.executemany(
                """
                INSERT INTO walk_forward_selections (
                    run_id, horizon, experiment_group, report_period,
                    status, details_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        evaluation.horizon,
                        evaluation.experiment_group,
                        row["report_period"],
                        row["status"],
                        _json(row.to_dict()),
                    )
                    for _, row in evaluation.selections.iterrows()
                ],
            )
        if not evaluation.predictions.empty:
            connection.executemany(
                """
                INSERT INTO walk_forward_predictions (
                    run_id, horizon, report_period, cusip, formula_id,
                    score, threshold, positive_threshold,
                    negative_threshold, context_id, experiment_group,
                    decision_signal,
                    label, prediction,
                    baseline_prediction, security_return, excess_return,
                    split_affected
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    (
                        run_id,
                        evaluation.horizon,
                        row["report_period"],
                        row["cusip"],
                        row["selected_formula_id"],
                        row["score"],
                        row["selected_positive_threshold"],
                        row["selected_positive_threshold"],
                        row["selected_negative_threshold"],
                        row["selected_context_id"],
                        evaluation.experiment_group,
                        row["decision_signal"],
                        row["label"],
                        row["prediction"],
                        row["baseline_prediction"],
                        row["target_return"],
                        row["excess_return"],
                        row["split_affected"],
                    )
                    for _, row in evaluation.predictions.iterrows()
                ],
            )
        if not evaluation.candidate_trials.empty:
            connection.executemany(
                """
                INSERT INTO candidate_trials (
                    run_id, horizon, experiment_group, report_period,
                    candidate_order, selected, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        evaluation.horizon,
                        evaluation.experiment_group,
                        row["report_period"],
                        row["candidate_order"],
                        row["selected"],
                        _json(row.to_dict()),
                    )
                    for _, row in evaluation.candidate_trials.iterrows()
                ],
            )
        connection.executemany(
            """
            INSERT INTO evaluation_metrics (
                run_id, horizon, experiment_group, metric, value
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    evaluation.horizon,
                    evaluation.experiment_group,
                    key,
                    (
                        float(value)
                        if isinstance(value, (int, float))
                        and math.isfinite(float(value))
                        else None
                    ),
                )
                for key, value in evaluation.metrics.items()
                if key not in {"horizon", "experiment_group"}
            ],
        )
        if not evaluation.rank_ic_by_quarter.empty:
            connection.executemany(
                """
                INSERT INTO rank_ic_by_quarter (
                    run_id, horizon, experiment_group, report_period,
                    formula_id, rank_ic
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        evaluation.horizon,
                        evaluation.experiment_group,
                        row["report_period"],
                        row["selected_formula_id"],
                        row["rank_ic"],
                    )
                    for _, row in evaluation.rank_ic_by_quarter.iterrows()
                ],
            )
    connection.executemany(
        "INSERT INTO trust_gate_results VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                run_id,
                item["horizon"],
                item["criterion"],
                item["passed"],
                str(item["observed_value"]),
                str(item["required_value"]),
            )
            for item in trust_criteria
        ],
    )
    if production_candidates:
        connection.executemany(
            """
            INSERT INTO production_candidates (
                run_id, horizon, formula_id, threshold,
                positive_threshold, negative_threshold, context_id,
                experiment_group, training_end_period, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    item["horizon"],
                    item["formula_id"],
                    item["positive_threshold"],
                    item["positive_threshold"],
                    item["negative_threshold"],
                    item["context_id"],
                    item["experiment_group"],
                    item["training_end_period"],
                    _json(item["details_json"]),
                )
                for item in production_candidates
            ],
        )
    if production_candidate_trials:
        connection.executemany(
            """
            INSERT INTO production_candidate_trials
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    item["horizon"],
                    item["candidate_order"],
                    item["selected"],
                    _json(item),
                )
                for item in production_candidate_trials
            ],
        )
    if current_signals:
        connection.executemany(
            """
            INSERT INTO current_signals (
                run_id, horizon, experiment_group, report_period,
                as_of_date, cusip,
                ticker, market_symbol, alpha_score, combined_score,
                research_signal,
                decision_signal, decision_reason, feature_date,
                price_above_52_week_low_pct, trend_regime
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    item["horizon"],
                    item["experiment_group"],
                    item["report_period"],
                    item["as_of_date"],
                    item["cusip"],
                    item["ticker"],
                    item["market_symbol"],
                    item["alpha_score"],
                    item["combined_score"],
                    item["research_signal"],
                    item["decision_signal"],
                    item["decision_reason"],
                    item["feature_date"],
                    item["price_above_52_week_low_pct"],
                    item["trend_regime"],
                )
                for item in current_signals
            ],
        )
    if not decomposed_observations.empty:
        excluded = {
            "security_return",
            "excess_return",
            "target_return",
            "label",
            "excess_label",
        }
        connection.executemany(
            """
            INSERT INTO decomposed_features
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    row["report_period"],
                    row["cusip"],
                    row["horizon"],
                    _json(
                        {
                            key: value
                            for key, value in row.to_dict().items()
                            if key not in excluded
                        }
                    ),
                )
                for _, row in decomposed_observations.iterrows()
            ],
        )
    if not macro_sector_observations.empty:
        excluded = {
            "security_return",
            "excess_return",
            "target_return",
            "label",
            "excess_label",
        }
        connection.executemany(
            """
            INSERT INTO macro_sector_features
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    row["report_period"],
                    row["cusip"],
                    row["horizon"],
                    _json(
                        {
                            key: value
                            for key, value in row.to_dict().items()
                            if key not in excluded
                        }
                    ),
                )
                for _, row in macro_sector_observations.iterrows()
            ],
        )


def run_research(
    *,
    source_db: Path = DEFAULT_SOURCE_DB,
    performance_db: Path = DEFAULT_PERFORMANCE_DB,
    output_db: Path = DEFAULT_OUTPUT_DB,
    roster_path: Path = DEFAULT_ROSTER,
    config: ResearchConfig = ResearchConfig(),
    replace: bool = False,
) -> RunSummary:
    source_path = _validate_local_path(source_db, "source database")
    performance_path = _validate_local_path(
        performance_db, "performance database"
    )
    roster_file = _validate_local_path(roster_path, "roster")
    roster = load_roster(roster_file)
    source = duckdb.connect(str(source_path), read_only=True)
    performance = duckdb.connect(str(performance_path), read_only=True)
    try:
        macro_bundle, macro_fingerprint = load_macro_bundle(
            DEFAULT_MACRO_DIR
        )
        filings, holdings, managers = _load_source_data(source, roster)
        top_holdings = _load_current_top_holdings(
            source,
            roster,
            top_n=config.top_holdings_per_manager,
        )
        top_cusips = {item["cusip"] for item in top_holdings}
        source_digest = source_fingerprint(filings, holdings)
        roster_digest = _sha256_file(roster_file)
        universe_fingerprint = hashlib.sha256(
            _json(top_holdings).encode("utf-8")
        ).hexdigest()
        run_payload = {
            "protocol": PROTOCOL_VERSION,
            "config": config.as_dict(),
            "source_fingerprint": source_digest,
            "roster_sha256": roster_digest,
            "universe_fingerprint": universe_fingerprint,
            "macro_fingerprint": macro_fingerprint,
            "performance_size": performance_path.stat().st_size,
            "performance_mtime_ns": performance_path.stat().st_mtime_ns,
        }
        run_id = hashlib.sha256(_json(run_payload).encode("utf-8")).hexdigest()[
            :20
        ]
        output = _connect_output(output_db)
        prior = None
        started_at = datetime.now(timezone.utc)
        try:
            prior = output.execute(
                """
                SELECT status, summary_json
                FROM research_runs
                WHERE run_id = ?
                """,
                [run_id],
            ).fetchone()
            if prior is not None and prior[0] == "COMPLETE" and not replace:
                payload = json.loads(prior[1])
                return RunSummary(
                    run_id=run_id,
                    status="COMPLETE",
                    snapshot_count=payload["snapshot_count"],
                    signal_count=payload["signal_count"],
                    label_count=payload["label_count"],
                    trust_status=payload["trust_status"],
                    blocking_reasons=tuple(payload["blocking_reasons"]),
                    quality=payload.get("quality", {}),
                    research_signal_counts=payload.get(
                        "research_signal_counts", {}
                    ),
                    current_signal_counts=payload.get(
                        "current_signal_counts", {}
                    ),
                    metrics=payload["metrics"],
                )
            snapshots = build_snapshots(
                filings,
                holdings,
                managers,
                as_of_days=config.as_of_days,
            )
            raw_comparisons = build_raw_comparisons(snapshots)
            splits = detect_split_actions(raw_comparisons, config)
            changes = build_manager_changes(raw_comparisons, splits)
            changes = [
                item for item in changes if item.cusip in top_cusips
            ]
            scores = generate_formula_scores(changes)
            mapping = _load_mapping(performance)
            labels, quality = _build_labels(
                scores,
                mapping=mapping,
                performance=performance,
                config=config,
            )
            quality["top_holding_rows"] = len(top_holdings)
            quality["top_holding_cusips"] = len(top_cusips)
            quality["top_holding_managers"] = len(
                {item["canonical_cik"] for item in top_holdings}
            )
            observations = _observation_frame(
                scores,
                labels,
                config=config,
            )
            decomposed_observations = _decomposed_observation_frame(
                changes,
                scores,
                labels,
                config=config,
            )
            decomposed_current = _decomposed_observation_frame(
                changes,
                scores,
                labels,
                config=config,
                require_ready=False,
            )
            macro_sector_observations = _macro_sector_observation_frame(
                decomposed_current,
                performance,
                macro_bundle,
            )
            evaluations = [
                evaluate_walk_forward(
                    (
                        decomposed_observations
                        if experiment_group == EXPERIMENT_DECOMPOSED_SWEEP
                        else macro_sector_observations
                        if experiment_group == EXPERIMENT_MACRO_SECTOR
                        else observations
                    ),
                    horizon=horizon,
                    config=config,
                    experiment_group=experiment_group,
                )
                for horizon in config.horizons
                for experiment_group in EXPERIMENT_GROUPS
            ]
            trust_status, trust_criteria = evaluate_trust_gate(
                [
                    item
                    for item in evaluations
                    if item.experiment_group
                    == EXPERIMENT_DECOMPOSED_SWEEP
                ],
                mapping_coverage=quality["mapping_coverage"],
                label_coverage=quality["label_coverage"],
                missing_terminal_price_rate=quality[
                    "missing_terminal_price_rate"
                ],
                config=config,
            )
            (
                production_candidates,
                production_candidate_trials,
                current_signals,
                research_signal_counts,
                current_signal_counts,
            ) = _build_current_signals(
                scores,
                labels,
                decomposed_observations,
                decomposed_current,
                config=config,
                deployable=trust_status == "TRUSTWORTHY",
            )
            blocking = tuple(
                (
                    f"{item['horizon']}:{item['criterion']}"
                    if item["horizon"] is not None
                    else item["criterion"]
                )
                for item in trust_criteria
                if not item["passed"]
            )
            metrics = {
                f"{item.horizon}:{item.experiment_group}": item.metrics
                for item in evaluations
            }
            summary = RunSummary(
                run_id=run_id,
                status="COMPLETE",
                snapshot_count=len(snapshots),
                signal_count=sum(
                    score.formula_id == "alpha_v1_n3"
                    and score.score is not None
                    and score.meaningful_count
                    >= config.minimum_meaningful_managers
                    for score in scores
                ),
                label_count=sum(item["status"] == "READY" for item in labels),
                trust_status=trust_status,
                blocking_reasons=blocking,
                quality=quality,
                research_signal_counts=research_signal_counts,
                current_signal_counts=current_signal_counts,
                metrics=metrics,
            )
            output.execute("BEGIN TRANSACTION")
            _delete_run(output, run_id)
            output.execute(
                """
                INSERT INTO research_runs (
                    run_id, protocol_version, status, started_at,
                    config_json, source_db, performance_db, roster_path,
                    source_fingerprint, roster_sha256
                ) VALUES (?, ?, 'BUILDING', ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    PROTOCOL_VERSION,
                    started_at,
                    _json(config.as_dict()),
                    str(source_path),
                    str(performance_path),
                    str(roster_file),
                    source_digest,
                    roster_digest,
                ],
            )
            _persist_research(
                output,
                run_id=run_id,
                snapshots=snapshots,
                splits=splits,
                changes=changes,
                scores=scores,
                labels=labels,
                mapping=mapping,
                top_holdings=top_holdings,
                evaluations=evaluations,
                trust_criteria=trust_criteria,
                production_candidates=production_candidates,
                production_candidate_trials=production_candidate_trials,
                current_signals=current_signals,
                decomposed_observations=decomposed_observations,
                macro_sector_observations=macro_sector_observations,
                artifact_provenance={
                    "macro_manifest": {
                        "fingerprint": macro_fingerprint,
                        "path": str(DEFAULT_MACRO_DIR.resolve()),
                    },
                    "top_holdings_universe": {
                        "fingerprint": universe_fingerprint,
                        "rows": len(top_holdings),
                        "cusips": len(top_cusips),
                    },
                },
            )
            output.execute(
                """
                UPDATE research_runs
                SET status = 'COMPLETE',
                    completed_at = ?,
                    trust_status = ?,
                    summary_json = ?
                WHERE run_id = ?
                """,
                [
                    datetime.now(timezone.utc),
                    trust_status,
                    _json(asdict(summary)),
                    run_id,
                ],
            )
            output.execute("COMMIT")
            return summary
        except Exception as exc:
            try:
                output.execute("ROLLBACK")
            except duckdb.Error:
                pass
            if prior is None:
                output.execute(
                    """
                    INSERT OR REPLACE INTO research_runs (
                        run_id, protocol_version, status, started_at,
                        completed_at, config_json, source_db, performance_db,
                        roster_path, source_fingerprint, roster_sha256, error
                    ) VALUES (?, ?, 'FAILED', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        run_id,
                        PROTOCOL_VERSION,
                        started_at,
                        datetime.now(timezone.utc),
                        _json(config.as_dict()),
                        str(source_path),
                        str(performance_path),
                        str(roster_file),
                        source_digest,
                        roster_digest,
                        f"{type(exc).__name__}: {exc}",
                    ],
                )
            raise
        finally:
            output.close()
    finally:
        source.close()
        performance.close()


def load_report(
    output_db: Path = DEFAULT_OUTPUT_DB,
    *,
    run_id: str | None = None,
) -> dict:
    path = _validate_local_path(output_db, "output database")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        if run_id is None:
            row = connection.execute(
                """
                SELECT run_id, summary_json
                FROM research_runs
                WHERE status = 'COMPLETE'
                ORDER BY completed_at DESC
                LIMIT 1
                """
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT run_id, summary_json
                FROM research_runs
                WHERE run_id = ? AND status = 'COMPLETE'
                """,
                [run_id],
            ).fetchone()
        if row is None:
            raise ValueError("No completed research run was found")
        return json.loads(row[1])
    finally:
        connection.close()


def load_current_signals(
    output_db: Path = DEFAULT_OUTPUT_DB,
    *,
    run_id: str | None = None,
    horizon: int | None = None,
) -> list[dict]:
    path = _validate_local_path(output_db, "output database")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        if run_id is None:
            row = connection.execute(
                """
                SELECT run_id
                FROM research_runs
                WHERE status = 'COMPLETE'
                ORDER BY completed_at DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                raise ValueError("No completed research run was found")
            run_id = str(row[0])
        condition = ""
        params: list[object] = [run_id]
        if horizon is not None:
            condition = "AND horizon = ?"
            params.append(horizon)
        rows = connection.execute(
            f"""
            SELECT
                horizon, experiment_group, report_period, as_of_date,
                cusip, ticker,
                market_symbol, alpha_score, combined_score,
                research_signal, decision_signal,
                decision_reason, feature_date,
                price_above_52_week_low_pct, trend_regime
            FROM current_signals
            WHERE run_id = ?
              {condition}
            ORDER BY horizon, decision_signal, abs(alpha_score) DESC, cusip
            """,
            params,
        ).fetchall()
        columns = [
            "horizon",
            "experiment_group",
            "report_period",
            "as_of_date",
            "cusip",
            "ticker",
            "market_symbol",
            "alpha_score",
            "combined_score",
            "research_signal",
            "decision_signal",
            "decision_reason",
            "feature_date",
            "price_above_52_week_low_pct",
            "trend_regime",
        ]
        return [dict(zip(columns, row, strict=True)) for row in rows]
    finally:
        connection.close()
