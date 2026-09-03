from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd


INSTANT_CONCEPTS = {
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "PartnersCapital",
    ),
    "cash": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "short_term_borrowings": (
        "ShortTermBorrowings",
    ),
    "long_term_debt_current": (
        "LongTermDebtCurrent",
    ),
    "debt_current_total": (
        "DebtCurrent",
    ),
    "long_term_debt_noncurrent": (
        "LongTermDebtNoncurrent",
    ),
    "long_term_debt_total": (
        "LongTermDebt",
    ),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "goodwill": ("Goodwill",),
    "intangible_assets": (
        "FiniteLivedIntangibleAssetsNet",
        "IndefiniteLivedIntangibleAssetsExcludingGoodwill",
        "IntangibleAssetsNetExcludingGoodwill",
    ),
    "shares_outstanding": (
        "EntityCommonStockSharesOutstanding",
        "CommonStockSharesOutstanding",
    ),
}

ANNUAL_CONCEPTS = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "cost_of_revenue": (
        "CostOfRevenue",
        "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization",
        "CostOfGoodsSold",
    ),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capital_expenditure": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForAdditionsToPropertyPlantAndEquipment",
    ),
    "interest_expense": (
        "InterestExpenseNonOperating",
        "InterestExpense",
    ),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "eps_basic": ("EarningsPerShareBasic",),
    "weighted_average_diluted_shares": (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ),
    "weighted_average_basic_shares": (
        "WeightedAverageNumberOfSharesOutstandingBasic",
    ),
    "dividends_paid": (
        "PaymentsOfDividends",
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfOrdinaryDividends",
    ),
    "pretax_income": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxes",
    ),
    "income_tax_expense": (
        "IncomeTaxExpenseBenefit",
    ),
}


def _normalized_ticker(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _ticker_cik_mapping() -> dict[str, str]:
    from edgar.reference.tickers import get_company_tickers

    companies = get_company_tickers(
        clean_name=False,
        clean_suffix=False,
    )
    mapping = {}
    for cik, ticker in companies[["cik", "ticker"]].itertuples(
        index=False,
        name=None,
    ):
        normalized = _normalized_ticker(ticker)
        if normalized:
            mapping[normalized] = str(int(cik)).zfill(10)
    return mapping


def _concept_lookup() -> dict[str, tuple[str, int, bool]]:
    result = {}
    for is_annual, registry in (
        (False, INSTANT_CONCEPTS),
        (True, ANNUAL_CONCEPTS),
    ):
        for metric, concepts in registry.items():
            for priority, concept in enumerate(concepts):
                result[concept] = (metric, priority, is_annual)
    return result


def _availability_timestamp(
    accepted_at: Any,
    filing_date: Any,
) -> datetime:
    if accepted_at is not None and not pd.isna(accepted_at):
        return pd.Timestamp(accepted_at).to_pydatetime()
    filed = pd.Timestamp(filing_date).date()
    return datetime.combine(filed, time.max)


def _load_fact_events(
    screening: duckdb.DuckDBPyConnection,
    issuer_ciks: list[str],
    *,
    maximum_feature_date: date,
) -> pd.DataFrame:
    if not issuer_ciks:
        return pd.DataFrame()
    required_views = {
        row[0]
        for row in screening.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_name IN (
                'silver_xbrl_submissions',
                'silver_xbrl_facts'
            )
            """
        ).fetchall()
    }
    if required_views != {
        "silver_xbrl_submissions",
        "silver_xbrl_facts",
    }:
        raise ValueError(
            "SEC fundamentals views are unavailable; run "
            "'python -m investor_screening.cli refresh-bulk-views'"
        )
    lookup = _concept_lookup()
    concepts = sorted(lookup)
    cik_placeholders = ",".join("?" for _ in issuer_ciks)
    tag_placeholders = ",".join("?" for _ in concepts)
    rows = screening.execute(
        f"""
        SELECT
            s.issuer_cik,
            s.sic,
            s.country_incorporation,
            s.business_country,
            s.accession_number,
            s.form,
            s.report_period,
            s.filing_date,
            s.accepted_at,
            f.tag,
            f.period_end,
            f.duration_quarters,
            f.unit,
            f.fact_value,
            f.source_row_number
        FROM silver_xbrl_submissions s
        JOIN silver_xbrl_facts f USING (accession_number)
        WHERE s.issuer_cik IN ({cik_placeholders})
          AND s.form IN ('10-K', '10-K/A', '20-F', '20-F/A')
          AND s.filing_date <= ?
          AND f.tag IN ({tag_placeholders})
          AND coalesce(f.segments, '') = ''
          AND coalesce(f.coreg, '') = ''
          AND f.fact_value IS NOT NULL
          AND upper(f.unit) IN ('USD', 'SHARES', 'USD/SHARES')
        """,
        [*issuer_ciks, maximum_feature_date, *concepts],
    ).fetchdf()
    if rows.empty:
        return rows
    rows["report_period"] = pd.to_datetime(rows["report_period"]).dt.date
    rows["period_end"] = pd.to_datetime(rows["period_end"]).dt.date
    rows["available_at"] = [
        _availability_timestamp(accepted, filed)
        for accepted, filed in rows[
            ["accepted_at", "filing_date"]
        ].itertuples(index=False, name=None)
    ]
    rows["fact_value"] = pd.to_numeric(
        rows["fact_value"],
        errors="coerce",
    )
    rows = rows[
        rows["fact_value"].notna()
        & (rows["period_end"] == rows["report_period"])
    ].copy()
    rows["metric"] = rows["tag"].map(lambda tag: lookup[str(tag)][0])
    rows["priority"] = rows["tag"].map(lambda tag: lookup[str(tag)][1])
    rows["is_annual"] = rows["tag"].map(lambda tag: lookup[str(tag)][2])
    rows = rows[
        (~rows["is_annual"] & (rows["duration_quarters"] == 0))
        | (rows["is_annual"] & (rows["duration_quarters"] == 4))
    ].copy()
    return rows.sort_values(
        [
            "issuer_cik",
            "available_at",
            "report_period",
            "metric",
            "priority",
            "source_row_number",
        ]
    )


def _period_snapshots(
    events: pd.DataFrame,
    feature_date: date,
) -> list[dict[str, Any]]:
    if events.empty:
        return []
    available = events[
        events["available_at"]
        <= datetime.combine(feature_date, time.max)
    ]
    if available.empty:
        return []
    selected = (
        available.sort_values(
            ["available_at", "priority", "source_row_number"],
            ascending=[True, False, True],
        )
        .drop_duplicates(
            ["report_period", "metric"],
            keep="last",
        )
    )
    snapshots = []
    for report_period, group in selected.groupby("report_period"):
        values = {
            str(row.metric): float(row.fact_value)
            for row in group.itertuples()
        }
        if values.get("assets", 0) <= 0:
            continue
        snapshots.append(
            {
                "report_period": report_period,
                "available_at": max(group["available_at"]),
                "sic": next(
                    (
                        int(value)
                        for value in group["sic"]
                        if value is not None and not pd.isna(value)
                    ),
                    None,
                ),
                "country_incorporation": next(
                    (
                        str(value).upper()
                        for value in (
                            group["country_incorporation"]
                            if "country_incorporation" in group
                            else []
                        )
                        if value is not None and not pd.isna(value)
                    ),
                    None,
                ),
                "business_country": next(
                    (
                        str(value).upper()
                        for value in (
                            group["business_country"]
                            if "business_country" in group
                            else []
                        )
                        if value is not None and not pd.isna(value)
                    ),
                    None,
                ),
                "accessions": sorted(
                    set(group["accession_number"].astype(str))
                ),
                **values,
            }
        )
    return sorted(
        snapshots,
        key=lambda item: item["report_period"],
        reverse=True,
    )


def _safe_ratio(
    numerator: float | None,
    denominator: float | None,
) -> float:
    if (
        numerator is None
        or denominator is None
        or not math.isfinite(float(numerator))
        or not math.isfinite(float(denominator))
        or float(denominator) == 0
    ):
        return math.nan
    return float(numerator) / float(denominator)


def _raw_factor_row(
    snapshots: list[dict[str, Any]],
    *,
    feature_date: date | None = None,
) -> dict[str, Any]:
    if not snapshots:
        return {}
    current = snapshots[0]
    if (
        feature_date is not None
        and (feature_date - current["report_period"]).days > 550
    ):
        return {}
    previous = snapshots[1] if len(snapshots) > 1 else {}
    assets = current.get("assets")
    prior_assets = previous.get("assets")
    gross_profit = current.get("gross_profit")
    if gross_profit is None:
        revenue = current.get("revenue")
        cost = current.get("cost_of_revenue")
        if revenue is not None and cost is not None:
            gross_profit = revenue - cost
    operating_cash_flow = current.get("operating_cash_flow")
    capital_expenditure = current.get("capital_expenditure")
    free_cash_flow = (
        operating_cash_flow - abs(capital_expenditure)
        if operating_cash_flow is not None
        and capital_expenditure is not None
        else None
    )
    long_term_total = current.get("long_term_debt_total")
    short_term = current.get("short_term_borrowings")
    if long_term_total is not None:
        debt = long_term_total + (short_term or 0.0)
    else:
        current_total = current.get("debt_current_total")
        noncurrent = current.get("long_term_debt_noncurrent")
        if current_total is not None:
            debt = current_total + (noncurrent or 0.0)
        else:
            debt_parts = (
                short_term,
                current.get("long_term_debt_current"),
                noncurrent,
            )
            debt = (
                sum(value for value in debt_parts if value is not None)
                if any(value is not None for value in debt_parts)
                else None
            )
    net_income = current.get("net_income")
    quality_values = {
        "gross_profit_assets": _safe_ratio(gross_profit, assets),
        "return_on_assets": _safe_ratio(net_income, assets),
        "free_cash_flow_assets": _safe_ratio(free_cash_flow, assets),
        "accrual_quality": _safe_ratio(
            (
                operating_cash_flow - net_income
                if operating_cash_flow is not None
                and net_income is not None
                else None
            ),
            assets,
        ),
        "operating_margin": _safe_ratio(
            current.get("operating_income"),
            current.get("revenue"),
        ),
    }
    return {
        "fundamental_report_period": current["report_period"],
        "fundamental_available_at": current["available_at"],
        "fundamental_accessions": current["accessions"],
        "sic": current.get("sic"),
        **quality_values,
        "asset_growth": (
            assets / prior_assets - 1.0
            if assets is not None
            and prior_assets is not None
            and prior_assets > 0
            and 300
            <= (
                current["report_period"] - previous["report_period"]
            ).days
            <= 430
            else math.nan
        ),
        "cash_assets": _safe_ratio(current.get("cash"), assets),
        "equity_assets": _safe_ratio(current.get("equity"), assets),
        "debt_assets": _safe_ratio(debt, assets),
        "current_ratio": _safe_ratio(
            current.get("current_assets"),
            current.get("current_liabilities"),
        ),
        "interest_coverage": _safe_ratio(
            current.get("operating_income"),
            abs(current["interest_expense"])
            if current.get("interest_expense") not in (None, 0)
            else None,
        ),
    }


def _centered_rank(values: pd.Series) -> pd.Series:
    valid = values.dropna()
    result = pd.Series(np.nan, index=values.index, dtype=float)
    if len(valid) == 1:
        result.loc[valid.index] = 0.0
    elif len(valid) > 1:
        ranks = valid.rank(method="average")
        midpoint = (len(valid) + 1) / 2.0
        half_range = (len(valid) - 1) / 2.0
        result.loc[valid.index] = (
            100.0 * (ranks - midpoint) / half_range
        )
    return result


def _industry_rank(
    frame: pd.DataFrame,
    source: str,
    *,
    minimum_group_size: int = 5,
) -> pd.Series:
    result = pd.Series(np.nan, index=frame.index, dtype=float)
    for _, quarter in frame.groupby("report_period"):
        broad = _centered_rank(quarter[source])
        result.loc[quarter.index] = broad
        for _, industry in quarter.groupby("sic_group"):
            if industry[source].notna().sum() < minimum_group_size:
                continue
            result.loc[industry.index] = _centered_rank(industry[source])
    return result


def build_fundamental_features(
    *,
    parent_db: Path,
    screening_db: Path,
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
                f.feature_date
            FROM forward_labels f
            WHERE f.run_id = ? AND f.feature_date IS NOT NULL
            """,
            [parent_run_id],
        ).fetchdf()
        observations["report_period"] = pd.to_datetime(
            observations["report_period"]
        ).dt.date
        observations["feature_date"] = pd.to_datetime(
            observations["feature_date"]
        ).dt.date
        ticker_ciks = _ticker_cik_mapping()
        observations["issuer_cik"] = observations["ticker"].map(
            lambda value: ticker_ciks.get(_normalized_ticker(value))
        )
        mapped = observations["issuer_cik"].dropna().unique().tolist()
        events = _load_fact_events(
            screening,
            mapped,
            maximum_feature_date=max(observations["feature_date"]),
        )
        by_cik = {
            str(cik): group
            for cik, group in events.groupby("issuer_cik")
        }
        raw_rows = []
        for row in observations.itertuples(index=False):
            issuer_cik = row.issuer_cik
            raw = (
                _raw_factor_row(
                    _period_snapshots(
                        by_cik.get(
                            issuer_cik,
                            pd.DataFrame(),
                        ),
                        row.feature_date,
                    ),
                    feature_date=row.feature_date,
                )
                if issuer_cik
                else {}
            )
            raw_rows.append(
                {
                    "report_period": row.report_period,
                    "cusip": row.cusip,
                    "ticker": row.ticker,
                    "feature_date": row.feature_date,
                    "issuer_cik": issuer_cik,
                    "identity_source": "CURRENT_SEC_TICKER_CATALOG",
                    "identity_point_in_time": False,
                    **raw,
                }
            )
        frame = pd.DataFrame(raw_rows)
        for column in (
            "sic",
            "gross_profit_assets",
            "return_on_assets",
            "free_cash_flow_assets",
            "accrual_quality",
            "operating_margin",
            "asset_growth",
            "cash_assets",
            "equity_assets",
            "debt_assets",
            "current_ratio",
            "interest_coverage",
        ):
            if column not in frame:
                frame[column] = np.nan
        frame["sic_group"] = (
            pd.to_numeric(frame["sic"], errors="coerce") // 100
        )
        quality_sources = (
            "gross_profit_assets",
            "return_on_assets",
            "free_cash_flow_assets",
            "accrual_quality",
            "operating_margin",
        )
        quality_ranks = []
        for source in quality_sources:
            output = f"{source}_rank"
            frame[output] = _industry_rank(frame, source)
            quality_ranks.append(output)
        frame["quality_score"] = frame[quality_ranks].mean(
            axis=1,
            skipna=True,
        )
        frame.loc[
            frame[quality_ranks].notna().sum(axis=1) < 3,
            "quality_score",
        ] = np.nan
        frame["investment_score"] = _industry_rank(
            frame,
            "asset_growth",
        ) * -1.0
        safety_directions = {
            "cash_assets": 1.0,
            "equity_assets": 1.0,
            "debt_assets": -1.0,
            "current_ratio": 1.0,
            "interest_coverage": 1.0,
        }
        safety_ranks = []
        for source, direction in safety_directions.items():
            output = f"{source}_rank"
            frame[output] = _industry_rank(frame, source) * direction
            safety_ranks.append(output)
        frame["safety_score"] = frame[safety_ranks].mean(
            axis=1,
            skipna=True,
        )
        frame.loc[
            frame[safety_ranks].notna().sum(axis=1) < 3,
            "safety_score",
        ] = np.nan
        frame["fundamental_balanced_score"] = (
            0.40 * frame["quality_score"]
            + 0.25 * frame["investment_score"]
            + 0.35 * frame["safety_score"]
        )
        return frame
    finally:
        parent.close()
        screening.close()
