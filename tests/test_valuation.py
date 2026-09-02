import pandas as pd
import pytest

from data_service import DataService


PERIODS = [
    "2025-12-31",
    "2024-12-31",
    "2023-12-31",
    "2022-12-31",
    "2021-12-31",
]


def _statements(
    revenues=None,
    operating_cash_flows=None,
    capex=None,
    dividends=None,
    equities=None,
):
    revenues = revenues or [100, 86, 74, 64, 55]
    operating_cash_flows = operating_cash_flows or [12, 10, 8.5, 7.2, 6]
    capex = capex or [-4, -3.5, -3, -2.6, -2.2]
    dividends = dividends or [-1, -0.9, -0.8, -0.7, -0.6]
    equities = equities or [20, 18, 16, 14, 12]
    scale = 1_000_000_000
    income = pd.DataFrame([
        {
            "period_ending": period,
            "total_revenue": revenue * scale,
            "diluted_earnings_per_share": 4.0 - index * 0.4,
            "interest_expense": 0.4 * scale,
            "tax_rate_for_calcs": 0.21,
        }
        for index, (period, revenue) in enumerate(zip(PERIODS, revenues))
    ])
    cash = pd.DataFrame([
        {
            "period_ending": period,
            "operating_cash_flow": operating_cash_flow * scale,
            "capital_expenditure": capital_expenditure * scale,
            "cash_dividends_paid": dividend * scale,
        }
        for period, operating_cash_flow, capital_expenditure, dividend in zip(
            PERIODS,
            operating_cash_flows,
            capex,
            dividends,
        )
    ])
    balance = pd.DataFrame([
        {
            "period_ending": period,
            "total_common_equity": equity * scale,
            "total_current_assets": 30 * scale,
            "total_liabilities_net_minority_interest": 24 * scale,
            "total_debt": 10 * scale,
            "net_debt": 5 * scale,
            "ordinary_shares_number": 1 * scale,
        }
        for period, equity in zip(PERIODS, equities)
    ])
    annual_eps = [
        {
            "year": int(period[:4]),
            "period_ending": period,
            "eps": 4.0 - index * 0.4,
        }
        for index, period in enumerate(PERIODS)
    ]
    return annual_eps, income, cash, balance


def _valuation(metrics, profile, statements=None, current_price=100):
    annual_eps, income, cash, balance = statements or _statements()
    metrics = {"currency": "USD", **metrics}
    profile = {"hq_country": "United States", **profile}
    return DataService._compute_valuation_analysis(
        current_price=current_price,
        metrics=metrics,
        profile=profile,
        annual_eps=annual_eps,
        income_statement=income,
        cash_flow=cash,
        balance_sheet=balance,
        average_pe_5y=22.0,
        average_aaa_yield=4.5,
        current_aaa_yield=5.5,
        current_treasury_yield=4.0,
        pe_observation_count=5,
    )


def test_high_growth_stock_recommends_scenario_and_reverse_dcf():
    result = _valuation(
        metrics={
            "market_cap": 100_000_000_000,
            "pe_ratio": 25.0,
            "forward_pe": 22.0,
            "peg_ratio": 1.2,
            "enterprise_to_ebitda": 18.0,
            "enterprise_to_revenue": 6.0,
            "revenue_growth": 0.20,
            "earnings_growth": 0.24,
            "book_value": 20.0,
            "return_on_equity": 0.22,
            "payout_ratio": 0.20,
            "beta": 1.0,
        },
        profile={
            "name": "Cloud Software Inc",
            "sector": "Technology",
            "industry_category": "Software - Infrastructure",
            "shares_outstanding": 1_000_000_000,
        },
    )

    methods = {method["id"]: method for method in result["methods"]}

    assert result["recommended_framework"]["name"] == "Scenario DCF + Reverse DCF"
    assert result["recommended_framework"]["anchor_method_id"] == "scenario_dcf"
    assert result["recommended_framework"]["recommended_method_ids"] == [
        "scenario_dcf",
        "reverse_dcf",
        "relative_multiples",
        "enterprise_multiples",
    ]
    assert result["fair_value"] == methods["scenario_dcf"]["value"]
    assert methods["scenario_dcf"]["status"] == "AVAILABLE"
    assert methods["reverse_dcf"]["status"] == "AVAILABLE"
    assert methods["relative_multiples"]["decision_read"] == (
        "GROWTH-ADJUSTED BALANCED"
    )
    assert methods["relative_multiples"]["decision_tone"] == "caution"
    assert methods["enterprise_multiples"]["decision_read"] == (
        "PEER BENCHMARK NEEDED"
    )
    assert all(
        method["decision_read"] != "AVAILABLE"
        for method in methods.values()
    )
    assert result["valuation_range"]["low"] < result["valuation_range"]["high"]
    assert len(methods) == 15
    assert {
        "graham_number",
        "graham_revised_growth",
        "graham_conservative_growth",
        "ncav",
    }.issubset(methods)
    assert all(method["methodology"] for method in methods.values())
    expected_graham_growth = (
        result["trailing_eps"]
        * (8.5 + 2 * min(result["eps_growth_cagr_pct"], 15.0))
        * 4.4
        / result["current_aaa_yield"]
    )
    assert methods["graham_revised_growth"]["value"] == pytest.approx(
        expected_graham_growth,
        abs=0.02,
    )
    assert (
        methods["graham_revised_growth"]["value"]
        != methods["graham_conservative_growth"]["value"]
    )
    assert methods["tangible_asset_value"]["status"] == "INSUFFICIENT DATA"
    assert methods["tangible_asset_value"]["value"] is None


def test_financial_stock_uses_residual_income_and_rejects_enterprise_dcf():
    result = _valuation(
        metrics={
            "market_cap": 50_000_000_000,
            "pe_ratio": 10.0,
            "forward_pe": 9.5,
            "price_to_book": 1.25,
            "revenue_growth": 0.06,
            "earnings_growth": 0.08,
            "book_value": 40.0,
            "return_on_equity": 0.15,
            "payout_ratio": 0.35,
            "dividend_yield": 3.0,
            "beta": 1.0,
        },
        profile={
            "name": "Example National Bank",
            "sector": "Financial Services",
            "industry_category": "Banks - Diversified",
            "shares_outstanding": 1_000_000_000,
        },
        current_price=50,
    )

    methods = {method["id"]: method for method in result["methods"]}

    assert result["recommended_framework"]["name"] == (
        "Residual Income + P/B/ROE cross-check"
    )
    assert result["recommended_framework"]["anchor_method_id"] == "residual_income"
    assert result["recommended_framework"]["recommended_method_ids"] == [
        "residual_income",
        "tangible_asset_value",
        "relative_multiples",
        "dividend_discount",
        "normalized_pe",
    ]
    assert methods["scenario_dcf"]["status"] == "NOT APPLICABLE"
    assert methods["residual_income"]["status"] == "AVAILABLE"


def test_regulated_utility_uses_dividend_discount_as_primary_anchor():
    statements = _statements(
        revenues=[40, 38, 36, 34, 32],
        operating_cash_flows=[8, 7.6, 7.2, 6.8, 6.4],
        capex=[-5, -4.8, -4.6, -4.4, -4.2],
        dividends=[-2.0, -1.9, -1.8, -1.7, -1.6],
        equities=[28, 27, 26, 25, 24],
    )
    result = _valuation(
        metrics={
            "market_cap": 50_000_000_000,
            "pe_ratio": 16.0,
            "forward_pe": 15.5,
            "revenue_growth": 0.05,
            "earnings_growth": 0.06,
            "book_value": 28.0,
            "return_on_equity": 0.10,
            "payout_ratio": 0.60,
            "dividend_yield": 4.0,
            "beta": 0.60,
        },
        profile={
            "name": "Example Regulated Power",
            "sector": "Utilities",
            "industry_category": "Utilities - Regulated Electric",
            "shares_outstanding": 1_000_000_000,
        },
        statements=statements,
        current_price=50,
    )

    methods = {method["id"]: method for method in result["methods"]}

    assert result["recommended_framework"]["name"] == (
        "Dividend Discount + Scenario DCF"
    )
    assert result["recommended_framework"]["anchor_method_id"] == (
        "dividend_discount"
    )
    assert methods["dividend_discount"]["status"] == "AVAILABLE"
    assert methods["dividend_discount"]["low"] < methods["dividend_discount"]["high"]


def test_conglomerate_flags_sotp_without_fabricating_segment_value():
    result = _valuation(
        metrics={
            "market_cap": 100_000_000_000,
            "pe_ratio": 18.0,
            "forward_pe": 17.0,
            "revenue_growth": 0.08,
            "earnings_growth": 0.09,
            "book_value": 45.0,
            "return_on_equity": 0.13,
            "payout_ratio": 0.0,
            "beta": 0.9,
        },
        profile={
            "name": "Example Holdings",
            "sector": "Financial Services",
            "industry_category": "Insurance - Diversified",
            "shares_outstanding": 1_000_000_000,
        },
    )

    structural = result["recommended_framework"]["structural_method"]

    assert result["recommended_framework"]["name"] == (
        "SOTP with whole-company fallback"
    )
    assert structural["id"] == "sotp"
    assert structural["status"] == "REQUIRES SEGMENT DATA"
    assert result["recommended_framework"]["anchor_method_id"] != "sotp"
    methods = {method["id"]: method for method in result["methods"]}
    assert methods["sotp"]["status"] == "REQUIRES SEGMENT DATA"
    assert methods["sotp"]["decision_read"] == "MORE DATA NEEDED"


def test_low_peg_produces_growth_adjusted_read_not_available_status():
    result = _valuation(
        metrics={
            "currency": "USD",
            "market_cap": 100_000_000_000,
            "pe_ratio": 18.0,
            "forward_pe": 17.0,
            "peg_ratio": 0.77,
            "enterprise_to_ebitda": 13.64,
            "enterprise_to_revenue": 6.55,
            "revenue_growth": 0.12,
            "earnings_growth": 0.16,
            "book_value": 20.0,
            "return_on_equity": 0.22,
            "payout_ratio": 0.20,
            "beta": 1.0,
        },
        profile={
            "name": "Example Growth Company",
            "sector": "Technology",
            "industry_category": "Software - Application",
            "hq_country": "United States",
            "shares_outstanding": 1_000_000_000,
        },
    )

    methods = {method["id"]: method for method in result["methods"]}

    assert methods["relative_multiples"]["status"] == "AVAILABLE"
    assert methods["relative_multiples"]["decision_read"] == (
        "GROWTH-ADJUSTED ATTRACTIVE"
    )
    assert methods["relative_multiples"]["decision_tone"] == "positive"
    assert "below 1.0x" in methods["relative_multiples"]["decision_detail"]
    assert methods["enterprise_multiples"]["decision_read"] == (
        "PEER BENCHMARK NEEDED"
    )
    assert methods["enterprise_multiples"]["decision_tone"] == "info"


def test_payment_processor_is_not_forced_into_bank_residual_income_framework():
    result = _valuation(
        metrics={
            "currency": "USD",
            "market_cap": 100_000_000_000,
            "pe_ratio": 24.0,
            "forward_pe": 21.0,
            "revenue_growth": 0.08,
            "earnings_growth": 0.10,
            "book_value": 12.0,
            "return_on_equity": 0.30,
            "payout_ratio": 0.25,
            "beta": 0.9,
        },
        profile={
            "name": "Example Payments Network",
            "sector": "Financial Services",
            "industry_category": "Credit Services",
            "hq_country": "United States",
            "shares_outstanding": 1_000_000_000,
        },
    )

    methods = {method["id"]: method for method in result["methods"]}

    assert result["recommended_framework"]["name"] == (
        "Scenario DCF + Normalized P/E"
    )
    assert methods["scenario_dcf"]["status"] == "AVAILABLE"


def test_excessive_payout_prevents_dividend_discount_anchor():
    statements = _statements(
        revenues=[40, 38, 36, 34, 32],
        operating_cash_flows=[8, 7.6, 7.2, 6.8, 6.4],
        capex=[-5, -4.8, -4.6, -4.4, -4.2],
        dividends=[-2.0, -1.9, -1.8, -1.7, -1.6],
        equities=[28, 27, 26, 25, 24],
    )
    result = _valuation(
        metrics={
            "currency": "USD",
            "market_cap": 50_000_000_000,
            "pe_ratio": 16.0,
            "forward_pe": 15.5,
            "revenue_growth": 0.05,
            "earnings_growth": 0.06,
            "book_value": 28.0,
            "return_on_equity": 0.10,
            "payout_ratio": 0.95,
            "dividend_yield": 4.0,
            "beta": 0.60,
        },
        profile={
            "name": "Example Regulated Power",
            "sector": "Utilities",
            "industry_category": "Utilities - Regulated Electric",
            "hq_country": "United States",
            "shares_outstanding": 1_000_000_000,
        },
        statements=statements,
        current_price=50,
    )

    methods = {method["id"]: method for method in result["methods"]}

    assert methods["dividend_discount"]["status"] == "NOT APPLICABLE"
    assert result["recommended_framework"]["anchor_method_id"] != (
        "dividend_discount"
    )


def test_negative_recent_fcff_is_not_hidden_by_older_positive_years():
    statements = _statements(
        operating_cash_flows=[-5, 10, 9, 8, 7],
        capex=[-4, -3, -3, -3, -3],
    )
    result = _valuation(
        metrics={
            "currency": "USD",
            "market_cap": 100_000_000_000,
            "pe_ratio": 20.0,
            "forward_pe": 18.0,
            "revenue_growth": 0.06,
            "earnings_growth": 0.08,
            "book_value": 20.0,
            "return_on_equity": 0.15,
            "payout_ratio": 0.20,
            "beta": 1.0,
        },
        profile={
            "name": "Example Industrial",
            "sector": "Industrials",
            "industry_category": "Specialty Industrial Machinery",
            "hq_country": "United States",
            "shares_outstanding": 1_000_000_000,
        },
        statements=statements,
    )

    methods = {method["id"]: method for method in result["methods"]}

    assert methods["scenario_dcf"]["status"] == "INSUFFICIENT DATA"
    assert result["recommended_framework"]["anchor_method_id"] == "normalized_pe"


def test_foreign_issuer_disables_unreconciled_per_share_absolute_values():
    result = _valuation(
        metrics={
            "currency": "USD",
            "market_cap": 100_000_000_000,
            "pe_ratio": 25.0,
            "forward_pe": 22.0,
            "peg_ratio": 1.2,
            "enterprise_to_ebitda": 18.0,
            "revenue_growth": 0.20,
            "earnings_growth": 0.24,
            "book_value": 20.0,
            "return_on_equity": 0.22,
            "payout_ratio": 0.20,
            "beta": 1.0,
        },
        profile={
            "name": "Example ADR",
            "sector": "Technology",
            "industry_category": "Semiconductor Equipment",
            "hq_country": "Netherlands",
            "shares_outstanding": 1_000_000_000,
        },
    )

    methods = {method["id"]: method for method in result["methods"]}

    assert result["fair_value"] is None
    assert result["assessment"] == "UNAVAILABLE"
    assert methods["scenario_dcf"]["status"] == "INSUFFICIENT DATA"
    assert methods["relative_multiples"]["status"] == "AVAILABLE"
    assert result["recommended_framework"]["data_warnings"]


def test_reit_primary_anchor_stays_inside_recommended_method_set():
    result = _valuation(
        metrics={
            "market_cap": 40_000_000_000,
            "pe_ratio": 20.0,
            "forward_pe": 18.0,
            "revenue_growth": 0.06,
            "earnings_growth": 0.08,
            "book_value": 30.0,
            "return_on_equity": 0.10,
            "payout_ratio": 0.0,
            "beta": 0.8,
        },
        profile={
            "name": "Example Property REIT",
            "sector": "Real Estate",
            "industry_category": "REIT - Industrial",
            "shares_outstanding": 1_000_000_000,
        },
    )

    framework = result["recommended_framework"]

    assert framework["name"] == "NAV / AFFO with dividend cross-check"
    assert framework["anchor_method_id"] == "normalized_pe"
    assert framework["anchor_method_id"] in framework["recommended_method_ids"]


def test_missing_profile_basis_disables_absolute_values():
    annual_eps, income, cash, balance = _statements()

    result = DataService._compute_valuation_analysis(
        current_price=100,
        metrics={
            "currency": "USD",
            "market_cap": 100_000_000_000,
            "pe_ratio": 25.0,
            "forward_pe": 22.0,
            "peg_ratio": 1.2,
            "enterprise_to_ebitda": 18.0,
            "revenue_growth": 0.20,
            "earnings_growth": 0.24,
            "book_value": 20.0,
            "return_on_equity": 0.22,
            "payout_ratio": 0.20,
            "beta": 1.0,
        },
        profile={
            "name": "Unknown Basis Company",
            "sector": "Technology",
            "industry_category": "Software - Infrastructure",
        },
        annual_eps=annual_eps,
        income_statement=income,
        cash_flow=cash,
        balance_sheet=balance,
        average_pe_5y=22.0,
        average_aaa_yield=4.5,
        current_aaa_yield=5.5,
        current_treasury_yield=4.0,
        pe_observation_count=5,
    )

    methods = {method["id"]: method for method in result["methods"]}

    assert result["fair_value"] is None
    assert methods["scenario_dcf"]["status"] == "INSUFFICIENT DATA"
    assert methods["graham_number"]["status"] == "INSUFFICIENT DATA"
    assert result["recommended_framework"]["data_warnings"]


def test_primary_anchor_does_not_escape_recommended_framework():
    statements = _statements(
        operating_cash_flows=[-5, -4, -3, -2, -1],
        capex=[-4, -4, -4, -4, -4],
    )
    result = _valuation(
        metrics={
            "market_cap": 100_000_000_000,
            "pe_ratio": 25.0,
            "forward_pe": 22.0,
            "peg_ratio": 1.2,
            "revenue_growth": 0.20,
            "earnings_growth": 0.24,
            "book_value": 20.0,
            "return_on_equity": 0.22,
            "payout_ratio": 0.20,
            "beta": 1.0,
        },
        profile={
            "name": "Cash Consuming Software",
            "sector": "Technology",
            "industry_category": "Software - Infrastructure",
            "shares_outstanding": 1_000_000_000,
        },
        statements=statements,
    )

    methods = {method["id"]: method for method in result["methods"]}

    assert methods["residual_income"]["value"] is not None
    assert methods["scenario_dcf"]["value"] is None
    assert result["recommended_framework"]["anchor_method_id"] is None
    assert result["fair_value"] is None
    assert result["assessment"] == "UNAVAILABLE"
