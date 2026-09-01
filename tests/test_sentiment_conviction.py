import pandas as pd

from data_service import DataService


def test_typical_share_adjustment_normalizes_cusip():
    current = pd.DataFrame({
        "Cusip": [" abc ", "DEF"],
        "SharesPrnAmount": [120, 50]
    })
    previous = pd.DataFrame({
        "Cusip": ["ABC", "def "],
        "SharesPrnAmount": [100, 100]
    })

    result = DataService._get_typical_share_adjustment_pct(
        current,
        previous
    )

    assert result == 35.0


def test_continuing_conviction_uses_share_change_vs_normal():
    relative, basis, significance, force_routine = (
        DataService._calculate_relative_conviction(
            status="INCREASED",
            share_change_pct=20.0,
            typical_share_change_pct=10.0,
            previous_weight=3.0,
            current_weight=3.5,
            typical_position_weight=2.0
        )
    )

    assert relative == 2.0
    assert basis == "SHARE_CHANGE"
    assert significance == 1.75
    assert force_routine is False


def test_tiny_continuing_position_is_forced_routine():
    relative, basis, significance, force_routine = (
        DataService._calculate_relative_conviction(
            status="DECREASED",
            share_change_pct=-80.0,
            typical_share_change_pct=10.0,
            previous_weight=0.10,
            current_weight=0.02,
            typical_position_weight=1.0
        )
    )

    assert relative == -8.0
    assert basis == "SHARE_CHANGE"
    assert significance == 0.10
    assert force_routine is True


def test_new_and_closed_use_position_size_significance():
    new_result = DataService._calculate_relative_conviction(
        status="NEW",
        share_change_pct=None,
        typical_share_change_pct=15.0,
        previous_weight=0.0,
        current_weight=4.0,
        typical_position_weight=2.0
    )
    closed_result = DataService._calculate_relative_conviction(
        status="CLOSED",
        share_change_pct=-100.0,
        typical_share_change_pct=15.0,
        previous_weight=0.20,
        current_weight=0.0,
        typical_position_weight=2.0
    )

    assert new_result == (2.0, "POSITION_SIZE", 2.0, False)
    assert closed_result == (-0.10, "POSITION_SIZE", 0.10, False)


def test_missing_manager_baseline_is_unscored():
    result = DataService._calculate_relative_conviction(
        status="INCREASED",
        share_change_pct=25.0,
        typical_share_change_pct=None,
        previous_weight=2.0,
        current_weight=3.0,
        typical_position_weight=2.0
    )

    assert result == (None, "SHARE_CHANGE", 1.5, False)


def test_overview_sentiment_summary_uses_shared_scoring_model():
    service = DataService.__new__(DataService)
    changes = [
        {
            "ticker": "AAA",
            "cik": "1",
            "manager": "One",
            "fund_name": "One",
            "status": "NEW",
            "portfolio_weight": 4.0,
            "previous_portfolio_weight": 0.0,
            "portfolio_weight_change_raw": 4.0,
            "shares_change_pct": None,
            "manager_typical_position_weight": 2.0,
            "manager_typical_share_change_pct": None,
        },
        {
            "ticker": "AAA",
            "cik": "2",
            "manager": "Two",
            "fund_name": "Two",
            "status": "INCREASED",
            "portfolio_weight": 3.0,
            "previous_portfolio_weight": 2.0,
            "portfolio_weight_change_raw": 1.0,
            "shares_change_pct": 20.0,
            "manager_typical_position_weight": 2.0,
            "manager_typical_share_change_pct": 10.0,
        },
        {
            "ticker": "AAA",
            "cik": "3",
            "manager": "Three",
            "fund_name": "Three",
            "status": "DECREASED",
            "portfolio_weight": 2.0,
            "previous_portfolio_weight": 3.0,
            "portfolio_weight_change_raw": -1.0,
            "shares_change_pct": -20.0,
            "manager_typical_position_weight": 2.0,
            "manager_typical_share_change_pct": 10.0,
        },
    ]

    sentiment = service.get_ticker_sentiment_summaries(
        ["AAA"],
        changes,
    )["AAA"]

    assert sentiment["meaningful_count"] == 3
    assert sentiment["score"] == 33.33
    assert sentiment["regime"] == "BULLISH"
