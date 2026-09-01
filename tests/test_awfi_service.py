from datetime import date, datetime, timezone

import duckdb
import pandas as pd

from awfi_service import AwfiService
from predictive_sentiment.config import AWFI_VERSION


def _build_database(path):
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        CREATE TABLE research_runs (
            run_id VARCHAR,
            status VARCHAR,
            completed_at TIMESTAMPTZ,
            trust_status VARCHAR
        );
        CREATE TABLE awfi_scores (
            run_id VARCHAR,
            awfi_version VARCHAR,
            report_period DATE,
            as_of_date DATE,
            feature_date DATE,
            cusip VARCHAR,
            ticker VARCHAR,
            market_symbol VARCHAR,
            horizon INTEGER,
            score DOUBLE,
            positive_threshold DOUBLE,
            negative_threshold DOUBLE,
            signal VARCHAR,
            source_status VARCHAR
        );
        """
    )
    connection.execute(
        "INSERT INTO research_runs VALUES (?, 'COMPLETE', ?, ?)",
        [
            "run-1",
            datetime(2026, 8, 31, tzinfo=timezone.utc),
            "NOT_TRUSTWORTHY",
        ],
    )
    connection.executemany(
        """
        INSERT INTO awfi_scores VALUES (
            'run-1', ?, '2026-03-31', '2026-05-15', '2026-05-15',
            ?, ?, ?, ?, ?, ?, ?, ?, 'INSUFFICIENT_SPY_HORIZON'
        )
        """,
        [
            (AWFI_VERSION, "A", "AAA", "AAA", 126, 82.5, 75, 75, "BUY"),
            (AWFI_VERSION, "A", "AAA", "AAA", 252, 41.0, 75, 75, "HOLD"),
        ],
    )
    connection.close()


def test_missing_database_returns_unavailable(tmp_path):
    result = AwfiService(tmp_path / "missing.duckdb").get_period_scores(
        "2026-03-31"
    )
    assert result["scores"] == {}
    assert result["metadata"]["state"] == "UNAVAILABLE"


def test_exact_period_returns_horizon_scores(tmp_path):
    path = tmp_path / "awfi.duckdb"
    _build_database(path)

    result = AwfiService(path).get_period_scores("2026-03-31")

    assert result["metadata"]["state"] == "READY"
    assert result["metadata"]["run_id"] == "run-1"
    assert result["scores"]["AAA"]["126"]["score"] == 82.5
    assert result["scores"]["AAA"]["126"]["signal"] == "BUY"
    assert result["scores"]["AAA"]["126"]["research_signal"] == "BUY"
    assert result["scores"]["AAA"]["252"]["signal"] == "HOLD"


def test_historical_period_never_falls_back_to_current(tmp_path):
    path = tmp_path / "awfi.duckdb"
    _build_database(path)

    result = AwfiService(path).get_period_scores("2025-12-31")

    assert result["scores"] == {}
    assert result["metadata"]["state"] == "UNAVAILABLE"


def test_newer_application_period_marks_awfi_stale(tmp_path):
    path = tmp_path / "awfi.duckdb"
    _build_database(path)

    result = AwfiService(path).get_period_scores(
        "2026-03-31",
        latest_application_period="2026-06-30",
    )

    assert result["metadata"]["state"] == "STALE"
    assert result["metadata"]["stale"] is True


def test_market_symbol_alias_maps_to_dashboard_ticker(tmp_path):
    path = tmp_path / "awfi.duckdb"
    _build_database(path)
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        INSERT INTO awfi_scores VALUES (
            'run-1', ?, '2026-03-31', '2026-05-15', '2026-05-15',
            'BRK-CUSIP', NULL, 'BRK-B', 126, 90, 75, 75, 'BUY',
            'INSUFFICIENT_SPY_HORIZON'
        )
        """,
        [AWFI_VERSION],
    )
    connection.close()

    result = AwfiService(path).get_period_scores("2026-03-31")

    assert result["scores"]["BRKB"]["126"]["score"] == 90


def test_duplicate_dashboard_mapping_is_unavailable(tmp_path):
    path = tmp_path / "awfi.duckdb"
    _build_database(path)
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        INSERT INTO awfi_scores VALUES (
            'run-1', ?, '2026-03-31', '2026-05-15', '2026-05-15',
            'B', 'AAA', 'AAA', 126, -99, 75, 75, 'SELL',
            'INSUFFICIENT_SPY_HORIZON'
        )
        """,
        [AWFI_VERSION],
    )
    connection.close()

    result = AwfiService(path).get_period_scores("2026-03-31")

    assert "126" not in result["scores"].get("AAA", {})
    assert result["metadata"]["duplicate_tickers"] == ["AAA"]


def test_live_top_tickers_accept_cached_shares_type():
    holdings = pd.DataFrame(
        [
            {
                "Cusip": "A",
                "Ticker": "AAA",
                "Issuer": "AAA INC",
                "Class": "COM",
                "Type": "Shares",
                "PutCall": "",
                "PortfolioWeight": 10,
                "Value": 100,
            },
            {
                "Cusip": "F",
                "Ticker": "ETF",
                "Issuer": "ACME ETF",
                "Class": "SHS",
                "Type": "Shares",
                "PutCall": "",
                "PortfolioWeight": 20,
                "Value": 200,
            },
        ]
    )
    result = AwfiService._current_top_tickers(
        {"1": {"status": "loaded", "holdings": holdings}}
    )

    assert result == {"AAA": "A"}


def test_historical_period_uses_latest_run_containing_that_period(tmp_path):
    path = tmp_path / "awfi.duckdb"
    _build_database(path)
    connection = duckdb.connect(str(path))
    connection.execute(
        "INSERT INTO research_runs VALUES (?, 'COMPLETE', ?, ?)",
        [
            "run-2",
            datetime(2026, 9, 1, tzinfo=timezone.utc),
            "NOT_TRUSTWORTHY",
        ],
    )
    connection.execute(
        """
        INSERT INTO awfi_scores VALUES (
            'run-2', ?, '2026-06-30', '2026-08-14', '2026-08-14',
            'B', 'BBB', 'BBB', 126, 20, 75, 75, 'HOLD',
            'INSUFFICIENT_SPY_HORIZON'
        )
        """,
        [AWFI_VERSION],
    )
    connection.close()

    result = AwfiService(path).get_period_scores("2026-03-31")

    assert result["metadata"]["run_id"] == "run-1"
    assert result["scores"]["AAA"]["126"]["score"] == 82.5


def test_ticker_history_returns_horizons_by_period(tmp_path):
    path = tmp_path / "awfi.duckdb"
    _build_database(path)

    history = AwfiService(path).get_ticker_history("AAA")

    assert history == [
        {
            "period": "2026-03-31",
            "scores": {
                "126": {
                    "score": 82.5,
                    "signal": "BUY",
                    "research_signal": "BUY",
                    "positive_threshold": 75.0,
                    "negative_threshold": 75.0,
                    "as_of_date": "2026-05-15",
                    "feature_date": "2026-05-15",
                },
                "252": {
                    "score": 41.0,
                    "signal": "HOLD",
                    "research_signal": "HOLD",
                    "positive_threshold": 75.0,
                    "negative_threshold": 75.0,
                    "as_of_date": "2026-05-15",
                    "feature_date": "2026-05-15",
                },
            },
        }
    ]


def test_ticker_history_is_limited_to_latest_twenty_periods(tmp_path):
    path = tmp_path / "awfi.duckdb"
    _build_database(path)
    periods = [
        date(year, month, day)
        for year in range(2020, 2025)
        for month, day in (
            (3, 31),
            (6, 30),
            (9, 30),
            (12, 31),
        )
    ]
    periods.append(date(2025, 3, 31))
    connection = duckdb.connect(str(path))
    connection.executemany(
        """
        INSERT INTO awfi_scores VALUES (
            'run-1', ?, ?, ?, ?,
            'A', 'AAA', 'AAA', 126, 10, 75, 75, 'HOLD',
            'READY'
        )
        """,
        [
            (
                AWFI_VERSION,
                period,
                period,
                period,
            )
            for period in periods
        ],
    )
    connection.close()

    history = AwfiService(path).get_ticker_history("AAA")

    assert len(history) == 20
    assert history[0]["period"] == "2020-09-30"
    assert history[-1]["period"] == "2026-03-31"
