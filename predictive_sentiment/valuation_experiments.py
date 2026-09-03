from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

from data_service import DataService

from .action_experiments import (
    ACTION_PROTOCOL_VERSION,
    PORTFOLIO_ACTIONS,
    ActionExperimentConfig,
    _action_summary,
    _clean_json,
    _holm_adjust,
    _source_fingerprint,
    action_significance,
    evaluate_walk_forward,
    load_action_observations,
)
from .fundamentals import (
    _industry_rank,
    _load_fact_events,
    _period_snapshots,
    _ticker_cik_mapping,
)


VALUATION_PROTOCOL_VERSION = "awfi-valuation-methods-v1"
DEFAULT_OUTPUT_DB = Path(
    "data/investor_screening/awfi_valuation_experiments.duckdb"
)
METHOD_IDS = (
    "recommended_anchor",
    "scenario_dcf",
    "reverse_dcf",
    "residual_income",
    "dividend_discount",
    "normalized_pe",
    "graham_number",
    "graham_revised_growth",
    "graham_conservative_growth",
    "ncav",
    "tangible_asset_value",
)
SUPPORT_WEIGHTS = (0.10, 0.20, 0.30)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS valuation_experiment_runs (
    run_id VARCHAR PRIMARY KEY,
    protocol_version VARCHAR NOT NULL,
    parent_run_id VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL,
    config_json JSON NOT NULL,
    source_fingerprint VARCHAR NOT NULL,
    summary_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS valuation_method_features (
    run_id VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    cusip VARCHAR NOT NULL,
    ticker VARCHAR,
    market_symbol VARCHAR,
    feature_date DATE NOT NULL,
    issuer_cik VARCHAR,
    method_id VARCHAR NOT NULL,
    raw_signal DOUBLE,
    method_score DOUBLE,
    method_value DOUBLE,
    current_price DOUBLE,
    details_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS valuation_candidate_results (
    run_id VARCHAR NOT NULL,
    method_id VARCHAR NOT NULL,
    support_weight DOUBLE NOT NULL,
    horizon INTEGER NOT NULL,
    selected_outer_quarters INTEGER NOT NULL,
    prediction_count INTEGER NOT NULL,
    macro_success_rate DOUBLE,
    macro_t_stat DOUBLE,
    macro_p_value DOUBLE,
    mean_rank_ic_edge DOUBLE,
    rank_ic_edge_t_stat DOUBLE,
    rank_ic_edge_p_value DOUBLE,
    rank_ic_edge_holm_p_value DOUBLE,
    details_json JSON NOT NULL
);

ALTER TABLE valuation_candidate_results
ADD COLUMN IF NOT EXISTS macro_p_value DOUBLE;

ALTER TABLE valuation_candidate_results
ADD COLUMN IF NOT EXISTS mean_rank_ic_edge DOUBLE;

ALTER TABLE valuation_candidate_results
ADD COLUMN IF NOT EXISTS rank_ic_edge_t_stat DOUBLE;

ALTER TABLE valuation_candidate_results
ADD COLUMN IF NOT EXISTS rank_ic_edge_p_value DOUBLE;

ALTER TABLE valuation_candidate_results
ADD COLUMN IF NOT EXISTS rank_ic_edge_holm_p_value DOUBLE;
"""


def _json(value: Any) -> str:
    return json.dumps(
        _clean_json(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _load_hashed_parquet(
    directory: Path,
    stem: str,
) -> pd.DataFrame:
    manifest_path = directory / f"{stem}_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = Path(manifest["path"])
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != manifest["sha256"]:
        raise ValueError(f"{stem} artifact hash mismatch")
    return pd.read_parquet(path)


def _series_map(
    frame: pd.DataFrame,
    value_column: str,
) -> dict[str, pd.Series]:
    result = {}
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    for symbol, rows in frame.groupby("symbol"):
        result[str(symbol)] = pd.Series(
            pd.to_numeric(rows[value_column], errors="coerce").to_numpy(),
            index=rows["date"],
            dtype=float,
        ).dropna().sort_index()
    return result


def _last_value(series: pd.Series, feature_date: date) -> float | None:
    values = series[series.index <= feature_date].dropna()
    return float(values.iloc[-1]) if not values.empty else None


def _debt(snapshot: dict[str, Any]) -> float:
    long_term_total = snapshot.get("long_term_debt_total")
    short_term = snapshot.get("short_term_borrowings")
    if long_term_total is not None:
        return float(long_term_total) + float(short_term or 0.0)
    current_total = snapshot.get("debt_current_total")
    noncurrent = snapshot.get("long_term_debt_noncurrent")
    if current_total is not None:
        return float(current_total) + float(noncurrent or 0.0)
    return sum(
        float(value)
        for value in (
            short_term,
            snapshot.get("long_term_debt_current"),
            noncurrent,
        )
        if value is not None
    )


def _shares(snapshot: dict[str, Any]) -> float | None:
    for key in (
        "shares_outstanding",
        "weighted_average_diluted_shares",
        "weighted_average_basic_shares",
    ):
        value = snapshot.get(key)
        if value is not None and value > 0:
            return float(value)
    return None


def _eps(snapshot: dict[str, Any]) -> float | None:
    for key in ("eps_diluted", "eps_basic"):
        value = snapshot.get(key)
        if value is not None and math.isfinite(float(value)):
            return float(value)
    shares = _shares(snapshot)
    income = snapshot.get("net_income")
    if shares and income is not None:
        return float(income) / shares
    return None


def _growth(latest: float | None, prior: float | None) -> float | None:
    if latest is None or prior is None or prior == 0:
        return None
    value = latest / prior - 1.0
    return value if -0.50 <= value <= 1.00 else None


def _historical_pe(
    snapshots: list[dict[str, Any]],
    prices: pd.Series,
) -> tuple[float | None, int]:
    observations = []
    for snapshot in snapshots[:6]:
        eps = _eps(snapshot)
        if eps is None or eps <= 0:
            continue
        period = snapshot["report_period"]
        start = date.fromordinal(period.toordinal() - 365)
        window = prices[
            (prices.index >= start) & (prices.index <= period)
        ]
        if len(window) < 200:
            continue
        pe = float(window.mean()) / eps
        if 0 < pe <= 100:
            observations.append(pe)
    return (
        (float(np.median(observations)), len(observations))
        if len(observations) >= 3
        else (None, len(observations))
    )


def _profile_from_sic(sic: int | None) -> tuple[str, str]:
    if sic is None:
        return "Unknown", "Unknown"
    if sic == 6798:
        return "Real Estate", "Real Estate Investment Trust REIT"
    if 6000 <= sic <= 6099:
        return "Financial Services", "Banks"
    if 6100 <= sic <= 6199:
        return "Financial Services", "Consumer Finance"
    if 6200 <= sic <= 6299:
        return "Financial Services", "Securities Brokerage"
    if 6300 <= sic <= 6499:
        return "Financial Services", "Insurance"
    if 6500 <= sic <= 6599:
        return "Real Estate", "Real Estate Services"
    if 6700 <= sic <= 6799:
        return "Industrials", "Holding Company Conglomerate"
    if 4900 <= sic <= 4999:
        if 4920 <= sic <= 4929:
            return "Utilities", "Regulated Gas Utilities"
        if 4940 <= sic <= 4949:
            return "Utilities", "Regulated Water Utilities"
        return "Utilities", "Regulated Electric Utilities"
    if 1000 <= sic <= 1499:
        if 1300 <= sic <= 1399:
            return "Energy", "Oil & Gas Exploration"
        if 1040 <= sic <= 1049:
            return "Basic Materials", "Gold Mining"
        return "Basic Materials", "Other Industrial Metals"
    if 2900 <= sic <= 2999:
        return "Energy", "Oil & Gas Refining"
    if sic == 2836:
        return "Healthcare", "Biotechnology"
    if 2830 <= sic <= 2835:
        return "Healthcare", "Drug Manufacturers"
    if 3500 <= sic <= 3699 or 7300 <= sic <= 7379:
        return "Technology", "Technology Hardware and Software"
    if 4800 <= sic <= 4899:
        return "Communication Services", "Telecommunications"
    if 5000 <= sic <= 5999:
        return "Consumer Cyclical", "Consumer and Retail"
    if 2000 <= sic <= 3999:
        return "Industrials", "Operating Company"
    return "Industrials", "Operating Company"


def _valuation_inputs(
    snapshots: list[dict[str, Any]],
    prices: pd.Series,
    aaa: pd.Series,
    treasury: pd.Series,
    feature_date: date,
    security_title: str,
) -> tuple[dict[str, Any], float] | None:
    if not snapshots:
        return None
    current = snapshots[0]
    current_price = _last_value(prices, feature_date)
    shares = _shares(current)
    eps = _eps(current)
    if current_price is None or shares is None or shares <= 0:
        return None
    prior = snapshots[1] if len(snapshots) > 1 else {}
    equity = current.get("equity")
    net_income = current.get("net_income")
    dividends = current.get("dividends_paid")
    payout = (
        abs(float(dividends)) / float(net_income)
        if dividends is not None
        and net_income is not None
        and net_income > 0
        else None
    )
    roe = (
        float(net_income) / float(equity)
        if net_income is not None and equity is not None and equity > 0
        else None
    )
    tax_rate = 0.21
    pretax = current.get("pretax_income")
    tax = current.get("income_tax_expense")
    if pretax is not None and tax is not None and pretax > 0:
        tax_rate = min(max(float(tax) / float(pretax), 0.0), 0.35)
    average_pe, pe_count = _historical_pe(snapshots, prices)
    annual_eps = [
        {"year": item["report_period"].year, "eps": value}
        for item in snapshots[:6]
        if (value := _eps(item)) is not None
    ]
    income_rows = []
    cash_rows = []
    balance_rows = []
    for item in snapshots[:6]:
        period = item["report_period"]
        item_shares = _shares(item)
        tangible = None
        if item.get("equity") is not None:
            tangible = float(item["equity"]) - float(
                item.get("goodwill") or 0.0
            ) - float(item.get("intangible_assets") or 0.0)
        item_pretax = item.get("pretax_income")
        item_tax = item.get("income_tax_expense")
        item_tax_rate = (
            min(
                max(float(item_tax) / float(item_pretax), 0.0),
                0.35,
            )
            if item_pretax is not None
            and item_tax is not None
            and item_pretax > 0
            else tax_rate
        )
        income_rows.append(
            {
                "period_ending": period,
                "total_revenue": item.get("revenue"),
                "interest_expense": item.get("interest_expense"),
                "tax_rate_for_calcs": item_tax_rate,
            }
        )
        cash_rows.append(
            {
                "period_ending": period,
                "operating_cash_flow": item.get("operating_cash_flow"),
                "capital_expenditure": item.get("capital_expenditure"),
                "cash_dividends_paid": item.get("dividends_paid"),
            }
        )
        balance_rows.append(
            {
                "period_ending": period,
                "ordinary_shares_number": item_shares,
                "share_issued": item_shares,
                "total_debt": _debt(item),
                "cash_and_cash_equivalents": item.get("cash"),
                "total_current_assets": item.get("current_assets"),
                "total_liabilities_net_minority_interest": item.get(
                    "liabilities"
                ),
                "tangible_book_value": tangible,
            }
        )
    aaa_value = _last_value(aaa, feature_date) or 5.44
    treasury_value = _last_value(treasury, feature_date) or 4.0
    aaa_start = date(feature_date.year - 20, feature_date.month, 1)
    aaa_window = aaa[
        (aaa.index >= aaa_start) & (aaa.index <= feature_date)
    ].dropna()
    average_aaa = (
        float(aaa_window.mean()) if not aaa_window.empty else 4.34
    )
    sector, industry = _profile_from_sic(current.get("sic"))
    country = (
        current.get("country_incorporation")
        or current.get("business_country")
        or ""
    )
    is_adr = any(
        token in str(security_title or "").upper()
        for token in ("ADR", "ADS", "DEPOSITARY")
    )
    is_us_basis = str(country).upper() == "US" and not is_adr
    revenue_growth = _growth(
        current.get("revenue"),
        prior.get("revenue"),
    )
    earnings_growth = _growth(
        current.get("net_income"),
        prior.get("net_income"),
    )
    metrics = {
        "currency": "USD",
        "market_cap": current_price * shares,
        "pe_ratio": current_price / eps if eps is not None and eps > 0 else None,
        "book_value": (
            float(equity) / shares
            if equity is not None and equity > 0
            else None
        ),
        "revenue_growth": revenue_growth,
        "earnings_growth": earnings_growth,
        "payout_ratio": payout,
        "return_on_equity": roe,
    }
    profile = {
        "sector": sector,
        "industry_category": industry,
        "name": "",
        "currency": "USD",
        "hq_country": "United States" if is_us_basis else "Unknown",
        "shares_outstanding": shares,
        "market_cap": current_price * shares,
    }
    analysis = DataService._compute_valuation_analysis(
        current_price,
        metrics,
        profile,
        annual_eps,
        pd.DataFrame(income_rows),
        pd.DataFrame(cash_rows),
        pd.DataFrame(balance_rows),
        average_pe,
        average_aaa,
        aaa_value,
        treasury_value,
        pe_count,
    )
    return analysis, current_price


def build_valuation_method_features(
    *,
    parent_db: Path,
    screening_db: Path,
    data_dir: Path,
    parent_run_id: str,
) -> pd.DataFrame:
    parent = duckdb.connect(str(parent_db), read_only=True)
    screening = duckdb.connect(str(screening_db), read_only=True)
    try:
        observations = parent.execute(
            """
            SELECT DISTINCT
                f.report_period,
                f.cusip,
                f.ticker,
                f.market_symbol,
                f.feature_date,
                coalesce(t.security_title, '') AS security_title
            FROM forward_labels f
            LEFT JOIN (
                SELECT cusip, arg_max(title, report_period)
                    AS security_title
                FROM run_top_holdings
                WHERE run_id = ?
                GROUP BY cusip
            ) t USING (cusip)
            WHERE f.run_id = ? AND f.feature_date IS NOT NULL
            """,
            [parent_run_id, parent_run_id],
        ).fetchdf()
        for column in ("report_period", "feature_date"):
            observations[column] = pd.to_datetime(
                observations[column]
            ).dt.date
        ticker_ciks = _ticker_cik_mapping()
        observations["issuer_cik"] = observations["ticker"].map(
            lambda value: ticker_ciks.get(
                "".join(
                    character
                    for character in str(value or "").upper()
                    if character.isalnum()
                )
            )
        )
        events = _load_fact_events(
            screening,
            observations["issuer_cik"].dropna().unique().tolist(),
            maximum_feature_date=max(observations["feature_date"]),
        )
        by_cik = {
            str(cik): group
            for cik, group in events.groupby("issuer_cik")
        }
        raw_prices = _load_hashed_parquet(data_dir, "raw_prices")
        price_map = _series_map(raw_prices, "raw_close")
        yields = _load_hashed_parquet(data_dir, "fred_yields")
        yields["date"] = pd.to_datetime(
            yields["observation_date"]
        ).dt.date
        aaa = pd.Series(
            pd.to_numeric(yields["DAAA"], errors="coerce").to_numpy(),
            index=yields["date"],
            dtype=float,
        ).dropna()
        treasury = pd.Series(
            pd.to_numeric(yields["DGS10"], errors="coerce").to_numpy(),
            index=yields["date"],
            dtype=float,
        ).dropna()
        result_rows = []
        for row in observations.itertuples(index=False):
            issuer_events = by_cik.get(
                row.issuer_cik,
                pd.DataFrame(),
            )
            prices = price_map.get(row.market_symbol)
            if issuer_events.empty or prices is None:
                continue
            snapshots = _period_snapshots(
                issuer_events,
                row.feature_date,
            )
            valued = _valuation_inputs(
                snapshots,
                prices,
                aaa,
                treasury,
                row.feature_date,
                row.security_title,
            )
            if valued is None:
                continue
            analysis, current_price = valued
            current_sic = snapshots[0].get("sic") if snapshots else None
            method_by_id = {
                method["id"]: method
                for method in analysis["methods"]
            }
            for method_id in METHOD_IDS:
                if method_id == "recommended_anchor":
                    method = {
                        "value": analysis.get("fair_value"),
                        "status": analysis.get("assessment"),
                        "assessment": analysis.get("assessment"),
                        "decision_read": analysis.get("assessment"),
                        "fit": 100,
                    }
                else:
                    method = method_by_id.get(method_id)
                if method is None:
                    continue
                value = method.get("value")
                raw_signal = None
                if (
                    value is not None
                    and value > 0
                    and current_price > 0
                    and float(method.get("fit") or 0) >= 60
                ):
                    raw_signal = float(value) / current_price - 1.0
                elif (
                    method_id == "reverse_dcf"
                    and float(method.get("fit") or 0) >= 60
                ):
                    gap = (method.get("inputs") or {}).get(
                        "expectations_gap_pct"
                    )
                    if gap is not None:
                        raw_signal = float(gap) / 100.0
                result_rows.append(
                    {
                        "report_period": row.report_period,
                        "cusip": row.cusip,
                        "ticker": row.ticker,
                        "market_symbol": row.market_symbol,
                        "feature_date": row.feature_date,
                        "issuer_cik": row.issuer_cik,
                        "sic": current_sic,
                        "sic_group": (
                            int(current_sic) // 100
                            if current_sic is not None
                            else None
                        ),
                        "method_id": method_id,
                        "raw_signal": raw_signal,
                        "method_value": value,
                        "current_price": current_price,
                        "status": method.get("status"),
                        "assessment": method.get("assessment"),
                        "decision_read": method.get("decision_read"),
                        "fit": method.get("fit"),
                    }
                )
        frame = pd.DataFrame(result_rows)
        if frame.empty:
            return frame
        frames = []
        for method_id, method_rows in frame.groupby("method_id"):
            method_rows = method_rows.copy()
            method_rows["method_score"] = _industry_rank(
                method_rows,
                "raw_signal",
            )
            frames.append(method_rows)
        return pd.concat(frames, ignore_index=True)
    finally:
        parent.close()
        screening.close()


def _macro_success(predictions: pd.DataFrame) -> dict[str, Any]:
    if predictions.empty or not {
        "action",
        "success",
        "report_period",
    }.issubset(predictions.columns):
        return {
            "quarters": 0,
            "macro_success_rate": math.nan,
            "macro_t_stat": math.nan,
            "macro_p_value": math.nan,
        }
    actionable = predictions[
        predictions["action"].isin(PORTFOLIO_ACTIONS)
        & predictions["success"].notna()
    ]
    if actionable.empty:
        return {
            "quarters": 0,
            "macro_success_rate": math.nan,
            "t_stat": math.nan,
            "p_value": math.nan,
        }
    quarter_actions = (
        actionable.groupby(["report_period", "action"])["success"]
        .mean()
        .astype(float)
        .reset_index()
    )
    quarter_macro = quarter_actions.groupby("report_period")[
        "success"
    ].mean()
    edges = quarter_macro - 0.50
    if len(edges) >= 5 and float(edges.std(ddof=1)) > 0:
        model = sm.OLS(
            edges.to_numpy(dtype=float),
            np.ones((len(edges), 1), dtype=float),
        ).fit(
            cov_type="HAC",
            cov_kwds={
                "maxlags": min(3, len(edges) - 1),
                "use_correction": True,
            },
        )
        t_stat = float(model.tvalues[0])
        p_value = float(stats.t.sf(t_stat, df=len(edges) - 1))
    else:
        t_stat = math.nan
        p_value = math.nan
    return {
        "quarters": int(len(quarter_macro)),
        "macro_success_rate": float(quarter_macro.mean()),
        "macro_t_stat": t_stat,
        "macro_p_value": p_value,
    }


def _rank_ic_edge(
    rows: pd.DataFrame,
    *,
    horizon: int,
) -> dict[str, Any]:
    selected = rows[
        (rows["horizon"] == horizon)
        & (rows["label_status"] == "READY")
    ].dropna(
        subset=[
            "base_awfi_score",
            "awfi_v2_score",
            "security_return",
        ]
    )
    records = []
    for period, quarter in selected.groupby("report_period"):
        if (
            len(quarter) < 5
            or quarter["base_awfi_score"].nunique() < 2
            or quarter["awfi_v2_score"].nunique() < 2
            or quarter["security_return"].nunique() < 2
        ):
            continue
        base_ic = quarter["base_awfi_score"].corr(
            quarter["security_return"],
            method="spearman",
        )
        candidate_ic = quarter["awfi_v2_score"].corr(
            quarter["security_return"],
            method="spearman",
        )
        if pd.notna(base_ic) and pd.notna(candidate_ic):
            records.append(
                {
                    "report_period": period,
                    "base_ic": float(base_ic),
                    "candidate_ic": float(candidate_ic),
                    "edge": float(candidate_ic - base_ic),
                }
            )
    frame = pd.DataFrame(records)
    if frame.empty:
        return {
            "rank_ic_quarters": 0,
            "base_mean_rank_ic": math.nan,
            "candidate_mean_rank_ic": math.nan,
            "mean_rank_ic_edge": math.nan,
            "rank_ic_edge_t_stat": math.nan,
            "rank_ic_edge_p_value": math.nan,
            "positive_edge_fraction": math.nan,
        }
    edge = frame["edge"]
    lag_by_horizon = {126: 1, 252: 3, 378: 5, 504: 7}
    if len(edge) >= 5 and float(edge.std(ddof=1)) > 0:
        model = sm.OLS(
            edge.to_numpy(dtype=float),
            np.ones((len(edge), 1), dtype=float),
        ).fit(
            cov_type="HAC",
            cov_kwds={
                "maxlags": min(
                    lag_by_horizon[horizon],
                    len(edge) - 1,
                ),
                "use_correction": True,
            },
        )
        t_stat = float(model.tvalues[0])
        p_value = float(stats.t.sf(t_stat, df=len(edge) - 1))
    else:
        t_stat = math.nan
        p_value = math.nan
    return {
        "rank_ic_quarters": int(len(frame)),
        "base_mean_rank_ic": float(frame["base_ic"].mean()),
        "candidate_mean_rank_ic": float(
            frame["candidate_ic"].mean()
        ),
        "mean_rank_ic_edge": float(edge.mean()),
        "rank_ic_edge_t_stat": t_stat,
        "rank_ic_edge_p_value": p_value,
        "positive_edge_fraction": float((edge > 0).mean()),
    }


def run_valuation_method_experiment(
    *,
    parent_db: Path,
    screening_db: Path,
    data_dir: Path,
    output_db: Path = DEFAULT_OUTPUT_DB,
    parent_run_id: str = "ebc243d9decb46624a69",
    minimum_feature_availability: float = 0.50,
    replace: bool = False,
) -> dict[str, Any]:
    config = ActionExperimentConfig(
        profile_mode="AWFI_V2_ONLY",
        minimum_feature_availability=minimum_feature_availability,
    )
    parent = duckdb.connect(str(parent_db), read_only=True)
    observations = load_action_observations(
        parent,
        parent_run_id,
        config,
    )
    parent.close()
    features = build_valuation_method_features(
        parent_db=parent_db,
        screening_db=screening_db,
        data_dir=data_dir,
        parent_run_id=parent_run_id,
    )
    wide = features.pivot_table(
        index=["report_period", "cusip", "feature_date"],
        columns="method_id",
        values="method_score",
        aggfunc="last",
    ).reset_index()
    wide.columns = [
        (
            f"valuation_{column}_score"
            if column in METHOD_IDS
            else column
        )
        for column in wide.columns
    ]
    observations = observations.merge(
        wide,
        on=["report_period", "cusip", "feature_date"],
        how="left",
    )
    parent_signature_connection = duckdb.connect(
        str(parent_db),
        read_only=True,
    )
    parent_fingerprint = _source_fingerprint(
        parent_signature_connection,
        parent_run_id,
    )
    parent_signature_connection.close()
    feature_columns = sorted(features.columns)
    feature_payload = features.sort_values(
        ["method_id", "report_period", "cusip", "feature_date"],
    )[feature_columns].to_json(
        orient="records",
        date_format="iso",
        default_handler=str,
    )
    implementation_digest = hashlib.sha256()
    for path in (
        Path(__file__),
        Path(__file__).with_name("action_experiments.py"),
        Path(__file__).with_name("fundamentals.py"),
        Path(__file__).with_name("research.py"),
        Path(__file__).with_name("config.py"),
        Path(__file__).with_name("awfi.py"),
        Path(__file__).resolve().parents[1] / "data_service.py",
    ):
        implementation_digest.update(path.read_bytes())
    artifact_digest = hashlib.sha256()
    for name in ("raw_prices_manifest.json", "fred_yields_manifest.json"):
        artifact_digest.update((data_dir / name).read_bytes())
    source_fingerprint = hashlib.sha256(
        _json(
            {
                "parent": parent_fingerprint,
                "features": hashlib.sha256(
                    feature_payload.encode("utf-8")
                ).hexdigest(),
                "implementation": implementation_digest.hexdigest(),
                "artifacts": artifact_digest.hexdigest(),
            }
        ).encode("utf-8")
    ).hexdigest()
    run_id = hashlib.sha256(
        _json(
            {
                "protocol": VALUATION_PROTOCOL_VERSION,
                "parent": parent_run_id,
                "source": source_fingerprint,
                "weights": SUPPORT_WEIGHTS,
                "methods": METHOD_IDS,
                "config": config.as_dict(),
            }
        ).encode("utf-8")
    ).hexdigest()[:20]
    output_db.parent.mkdir(parents=True, exist_ok=True)
    output = duckdb.connect(str(output_db))
    output.execute(SCHEMA_SQL)
    existing = output.execute(
        """
        SELECT summary_json
        FROM valuation_experiment_runs
        WHERE run_id = ? AND status = 'COMPLETE'
        """,
        [run_id],
    ).fetchone()
    if existing and not replace:
        output.close()
        return json.loads(str(existing[0]))

    results = []
    all_p_values = {}
    observations["base_awfi_score"] = observations["awfi_v2_score"]
    for method_id in METHOD_IDS:
        score_column = f"valuation_{method_id}_score"
        if score_column not in observations:
            continue
        for weight in SUPPORT_WEIGHTS:
            candidate_rows = observations.copy()
            available = candidate_rows[score_column].notna()
            candidate_rows.loc[available, "awfi_v2_score"] = (
                (1.0 - weight)
                * candidate_rows.loc[available, "awfi_v2_score"]
                + weight * candidate_rows.loc[available, score_column]
            ).clip(-100.0, 100.0)
            candidate_rows.loc[~available, "awfi_v2_score"] = np.nan
            for horizon in config.horizons:
                rank_metrics = _rank_ic_edge(
                    candidate_rows,
                    horizon=horizon,
                )
                predictions, selections, _ = evaluate_walk_forward(
                    candidate_rows,
                    horizon=horizon,
                    config=config,
                )
                macro = _macro_success(predictions)
                action_metrics = {
                    action: _action_summary(predictions, action)
                    for action in PORTFOLIO_ACTIONS
                }
                significance = action_significance(predictions)
                record = {
                    "method_id": method_id,
                    "support_weight": weight,
                    "horizon": horizon,
                    "selected_outer_quarters": int(
                        (
                            selections.get(
                                "status",
                                pd.Series(dtype=str),
                            )
                            == "SELECTED"
                        ).sum()
                    ),
                    "prediction_count": int(
                        predictions.get(
                            "action",
                            pd.Series(dtype=str),
                        ).isin(PORTFOLIO_ACTIONS).sum()
                    ),
                    **macro,
                    **rank_metrics,
                    "actions": action_metrics,
                    "action_significance": significance,
                }
                results.append(record)
                if horizon == 252:
                    all_p_values[f"{method_id}:{weight:.2f}"] = (
                        rank_metrics["rank_ic_edge_p_value"]
                    )
    adjusted = _holm_adjust(all_p_values)
    for result in results:
        key = f"{result['method_id']}:{result['support_weight']:.2f}"
        result["holm_p_value"] = (
            adjusted.get(key, math.nan)
            if result["horizon"] == 252
            else math.nan
        )
    primary = [
        item for item in results
        if item["horizon"] == 252
    ]
    eligible_primary = [
        item
        for item in primary
        if item["selected_outer_quarters"] >= 8
    ]
    best = max(
        eligible_primary,
        key=lambda item: (
            item["mean_rank_ic_edge"]
            if math.isfinite(item["mean_rank_ic_edge"])
            else -math.inf,
            item["macro_success_rate"]
            if math.isfinite(item["macro_success_rate"])
            else -math.inf,
            item["prediction_count"],
        ),
        default=None,
    )
    coverage = {
        method_id: float(
            features.loc[
                features["method_id"] == method_id,
                "method_score",
            ].notna().mean()
        )
        for method_id in METHOD_IDS
        if method_id in set(features["method_id"])
    }
    summary = {
        "run_id": run_id,
        "protocol_version": VALUATION_PROTOCOL_VERSION,
        "parent_run_id": parent_run_id,
        "source_fingerprint": source_fingerprint,
        "config": config.as_dict(),
        "method_coverage": coverage,
        "best_primary_candidate": best,
        "primary_candidates": primary,
        "all_results": results,
        "research_blockers": [
            "CURRENT_TICKER_TO_CIK_MAPPING_IS_NOT_POINT_IN_TIME",
            "RAW_YFINANCE_PRICE_LEVELS_REQUIRE_PROVIDER_AUDIT",
            "FORWARD_ESTIMATE_METHODS_UNAVAILABLE",
        ],
        "promotion_status": "NOT_PROMOTABLE",
    }
    output.execute("BEGIN TRANSACTION")
    for table in (
        "valuation_method_features",
        "valuation_candidate_results",
        "valuation_experiment_runs",
    ):
        output.execute(f"DELETE FROM {table} WHERE run_id = ?", [run_id])
    output.execute(
        """
        INSERT INTO valuation_experiment_runs
        VALUES (?, ?, ?, now(), 'COMPLETE', ?, ?, ?)
        """,
        [
            run_id,
            VALUATION_PROTOCOL_VERSION,
            parent_run_id,
            _json(asdict(config)),
            source_fingerprint,
            _json(summary),
        ],
    )
    output.executemany(
        """
        INSERT INTO valuation_method_features
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                row.report_period,
                row.cusip,
                row.ticker,
                row.market_symbol,
                row.feature_date,
                row.issuer_cik,
                row.method_id,
                row.raw_signal,
                row.method_score,
                row.method_value,
                row.current_price,
                _json(row._asdict()),
            )
            for row in features.itertuples(index=False)
        ],
    )
    output.executemany(
        """
        INSERT INTO valuation_candidate_results (
            run_id, method_id, support_weight, horizon,
            selected_outer_quarters, prediction_count,
            macro_success_rate, macro_t_stat, macro_p_value,
            mean_rank_ic_edge, rank_ic_edge_t_stat,
            rank_ic_edge_p_value, rank_ic_edge_holm_p_value,
            details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                item["method_id"],
                item["support_weight"],
                item["horizon"],
                item["selected_outer_quarters"],
                item["prediction_count"],
                item["macro_success_rate"],
                item["macro_t_stat"],
                item["macro_p_value"],
                item["mean_rank_ic_edge"],
                item["rank_ic_edge_t_stat"],
                item["rank_ic_edge_p_value"],
                item["holm_p_value"],
                _json(item),
            )
            for item in results
        ],
    )
    output.execute("COMMIT")
    output.close()
    return summary


def load_valuation_report(
    output_db: Path = DEFAULT_OUTPUT_DB,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    connection = duckdb.connect(str(output_db), read_only=True)
    try:
        if run_id:
            row = connection.execute(
                """
                SELECT summary_json
                FROM valuation_experiment_runs
                WHERE run_id = ? AND status = 'COMPLETE'
                """,
                [run_id],
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT summary_json
                FROM valuation_experiment_runs
                WHERE status = 'COMPLETE'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            raise ValueError("No completed valuation experiment was found")
        return json.loads(str(row[0]))
    finally:
        connection.close()
