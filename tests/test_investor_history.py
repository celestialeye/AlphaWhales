import asyncio

import pandas as pd

from data_service import DataService


def test_investor_history_returns_activity_and_ranked_holdings():
    service = DataService.__new__(DataService)
    service.cache = {
        "fund": {
            "fund_info": {
                "cik": "fund",
                "name": "Example Fund",
                "manager": "Example Manager",
            }
        }
    }
    service.get_available_periods = lambda count=20: ["2026-06-30"]
    period_cache = {
        "fund": {
            "status": "loaded",
            "metadata": {
                "report_period": "2026-06-30",
                "filing_date": "2026-08-14",
                "total_value": 150_000_000,
            },
            "holdings": pd.DataFrame({
                "Ticker": ["BBB", "AAA"],
                "Issuer": ["Beta Inc", "Alpha Inc"],
                "PortfolioWeight": [25.0, 60.0],
                "Value": [40_000_000, 90_000_000],
            }),
        }
    }

    async def get_period_cache(period):
        return period_cache

    service.get_period_cache = get_period_cache
    service.get_qoq_changes = lambda fund_cache: [
        {
            "ticker": "AAA",
            "issuer": "Alpha Inc",
            "status": "INCREASED",
            "shares_change": 10_000,
            "shares_change_pct": 25.0,
            "portfolio_weight_change": 5.0,
            "value_change": 12.5,
        }
    ]

    result = asyncio.run(service.get_investor_history("fund"))

    assert result["portfolio_history"][0] == {
        "period": "2026-06-30",
        "filing_date": "2026-08-14",
        "portfolio_value_m": 150.0,
        "portfolio_value_b": 0.15,
        "position_count": 2,
        "top_holdings": [
            {
                "ticker": "AAA",
                "issuer": "Alpha Inc",
                "portfolio_weight": 60.0,
                "value": 90.0,
            },
            {
                "ticker": "BBB",
                "issuer": "Beta Inc",
                "portfolio_weight": 25.0,
                "value": 40.0,
            },
        ],
    }
    assert result["activity"][0]["changes"][0]["status"] == "INCREASED"
