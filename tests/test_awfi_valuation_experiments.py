from __future__ import annotations

from datetime import date

import pandas as pd

from predictive_sentiment.valuation_experiments import (
    _rank_ic_edge,
    _valuation_inputs,
)


def _snapshots() -> list[dict]:
    rows = []
    for year in range(2023, 2017, -1):
        rows.append(
            {
                "report_period": date(year, 12, 31),
                "sic": 3571,
                "country_incorporation": "US",
                "assets": 1000 + (year - 2018) * 100,
                "liabilities": 400,
                "equity": 600 + (year - 2018) * 50,
                "cash": 100,
                "long_term_debt_noncurrent": 200,
                "current_assets": 500,
                "goodwill": 50,
                "intangible_assets": 25,
                "shares_outstanding": 100,
                "weighted_average_diluted_shares": 100,
                "eps_diluted": 4 + (year - 2018) * 0.3,
                "revenue": 800 + (year - 2018) * 50,
                "net_income": 100 + (year - 2018) * 10,
                "operating_income": 140 + (year - 2018) * 10,
                "operating_cash_flow": 150 + (year - 2018) * 10,
                "capital_expenditure": 50,
                "interest_expense": 10,
                "dividends_paid": 30,
                "pretax_income": 130,
                "income_tax_expense": 27,
            }
        )
    return rows


def test_historical_inputs_execute_actual_valuation_catalog():
    price_dates = pd.date_range(
        "2017-01-01",
        "2024-03-01",
        freq="B",
    ).date
    prices = pd.Series(
        range(1, len(price_dates) + 1),
        index=price_dates,
        dtype=float,
    ) / 100 + 20
    yield_dates = pd.date_range(
        "2000-01-01",
        "2024-03-01",
        freq="B",
    ).date
    aaa = pd.Series(5.0, index=yield_dates)
    treasury = pd.Series(4.0, index=yield_dates)

    analysis, current_price = _valuation_inputs(
        _snapshots(),
        prices,
        aaa,
        treasury,
        date(2024, 3, 1),
        "COMMON STOCK",
    )
    methods = {
        item["id"]: item
        for item in analysis["methods"]
    }

    assert current_price > 0
    for method_id in (
        "scenario_dcf",
        "residual_income",
        "dividend_discount",
        "normalized_pe",
        "graham_number",
        "graham_revised_growth",
        "graham_conservative_growth",
        "ncav",
        "tangible_asset_value",
    ):
        assert methods[method_id]["value"] is not None


def test_historical_inputs_disable_absolute_values_for_adr_basis():
    price_dates = pd.date_range(
        "2017-01-01",
        "2024-03-01",
        freq="B",
    ).date
    prices = pd.Series(30.0, index=price_dates)
    yield_dates = pd.date_range(
        "2000-01-01",
        "2024-03-01",
        freq="B",
    ).date
    aaa = pd.Series(5.0, index=yield_dates)
    treasury = pd.Series(4.0, index=yield_dates)

    analysis, _ = _valuation_inputs(
        _snapshots(),
        prices,
        aaa,
        treasury,
        date(2024, 3, 1),
        "SPONSORED ADR",
    )

    assert analysis["fair_value"] is None
    assert all(
        method["value"] is None
        for method in analysis["methods"]
        if method["id"] in {
            "scenario_dcf",
            "residual_income",
            "dividend_discount",
            "normalized_pe",
            "graham_number",
            "ncav",
            "tangible_asset_value",
        }
    )


def test_rank_ic_edge_compares_candidate_with_same_covered_rows():
    rows = []
    for quarter_index, period in enumerate(
        pd.date_range("2020-03-31", periods=8, freq="QE").date
    ):
        for security in range(8):
            rows.append(
                {
                    "report_period": period,
                    "horizon": 252,
                    "label_status": "READY",
                    "base_awfi_score": float(security),
                    "awfi_v2_score": float(security + quarter_index),
                    "security_return": float(security * 2),
                }
            )

    result = _rank_ic_edge(pd.DataFrame(rows), horizon=252)

    assert result["rank_ic_quarters"] == 8
    assert result["base_mean_rank_ic"] == 1.0
    assert result["candidate_mean_rank_ic"] == 1.0
    assert result["mean_rank_ic_edge"] == 0.0
