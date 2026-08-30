from __future__ import annotations

import json
from datetime import date

import duckdb
import pandas as pd
import pytest

from investor_screening.database import connect_database
from investor_screening.performance import (
    PERFORMANCE_DISCLAIMER,
    PERFORMANCE_LABEL,
    MAPPING_SOURCE,
    EligiblePosition,
    IntervalResult,
    ManagerUniverseItem,
    PortfolioEvent,
    assign_and_consolidate_execution_dates,
    calculate_interval,
    calculate_summary_metrics,
    connect_performance_store,
    execution_date_after,
    normalize_yfinance_symbol,
    reconstruct_filing_chronology,
    refresh_cusip_ticker_mapping,
    refresh_price_cache,
)
from investor_screening.screener import SNAPSHOT_SCHEMA, ScreeningService


def _add_filing(
    connection: duckdb.DuckDBPyConnection,
    *,
    accession: str,
    cik: str,
    period: date,
    filed: date,
    amendment_type: str = "",
    holdings: tuple[tuple[str, str, float, str | None], ...],
) -> None:
    connection.execute(
        """
        INSERT INTO submissions
        VALUES (?, 'fixture', ?, ?, ?, ?)
        """,
        [
            accession,
            filed,
            "13F-HR/A" if amendment_type else "13F-HR",
            cik,
            period,
        ],
    )
    connection.execute(
        """
        INSERT INTO cover_pages (
            accession_number, is_amendment, amendment_type,
            filing_manager_name
        )
        VALUES (?, ?, ?, 'Fixture Manager')
        """,
        [accession, bool(amendment_type), amendment_type],
    )
    for index, (cusip, issuer, value, put_call) in enumerate(holdings, start=1):
        connection.execute(
            """
            INSERT INTO holdings (
                accession_number, infotable_sk, name_of_issuer,
                title_of_class, cusip, value_reported, value_unit, value_usd,
                shares_or_principal, shares_or_principal_type, put_call
            )
            VALUES (?, ?, ?, 'COM', ?, ?, 'USD', ?, 1, 'SH', ?)
            """,
            [accession, index, issuer, cusip, value, value, put_call],
        )


def _event(
    positions: tuple[EligiblePosition, ...],
    execution: date = date(2024, 1, 2),
) -> PortfolioEvent:
    return PortfolioEvent(
        cik="1",
        manager_name="Manager",
        report_period=date(2023, 12, 31),
        filing_date=date(2024, 1, 1),
        triggering_accession="a",
        effective_accessions=("a",),
        positions=positions,
        execution_date=execution,
    )


def _series(values: list[float], dates: list[date]) -> pd.Series:
    return pd.Series(values, index=pd.Index(dates, name="date"), dtype=float)


def test_amendment_chronology_and_late_amendment_do_not_roll_back(tmp_path):
    connection = connect_database(tmp_path / "source.duckdb")
    try:
        canonical = "0002026053"
        historical = "0001336528"
        q1 = date(2023, 3, 31)
        q2 = date(2023, 6, 30)
        _add_filing(
            connection,
            accession="a",
            cik=historical,
            period=q1,
            filed=date(2023, 5, 1),
            holdings=(("A", "ALPHA", 100.0, None),),
        )
        _add_filing(
            connection,
            accession="b",
            cik=historical,
            period=q1,
            filed=date(2023, 5, 2),
            amendment_type="NEW HOLDINGS",
            holdings=(("B", "BETA", 25.0, None),),
        )
        _add_filing(
            connection,
            accession="c",
            cik=historical,
            period=q1,
            filed=date(2023, 5, 3),
            amendment_type="RESTATEMENT",
            holdings=(
                ("C", "CHARLIE", 80.0, None),
                ("F", "SPDR ETF", 1000.0, None),
                ("O", "OPTION ROW", 1000.0, "PUT"),
            ),
        )
        _add_filing(
            connection,
            accession="d",
            cik=canonical,
            period=q2,
            filed=date(2023, 8, 1),
            holdings=(("D", "DELTA", 90.0, None),),
        )
        _add_filing(
            connection,
            accession="e",
            cik=historical,
            period=q1,
            filed=date(2023, 8, 2),
            amendment_type="NEW HOLDINGS",
            holdings=(("E", "ECHO", 10.0, None),),
        )

        events = reconstruct_filing_chronology(
            connection,
            [ManagerUniverseItem(canonical, "Manager", 1.0)],
        )

        assert [event.triggering_accession for event in events] == [
            "a",
            "b",
            "c",
            "d",
        ]
        assert events[1].effective_accessions == ("a", "b")
        assert events[2].effective_accessions == ("c",)
        assert events[2].positions == (EligiblePosition("C", 80.0),)
        assert events[-1].report_period == q2
    finally:
        connection.close()


def test_next_spy_session_is_strict_and_same_execution_state_is_consolidated():
    sessions = [
        date(2024, 1, 5),
        date(2024, 1, 8),
        date(2024, 1, 9),
    ]
    assert execution_date_after(date(2024, 1, 5), sessions) == date(2024, 1, 8)
    first = _event((EligiblePosition("A", 1.0),))
    first = PortfolioEvent(
        **{
            **first.__dict__,
            "filing_date": date(2024, 1, 5),
            "triggering_accession": "a",
            "execution_date": None,
        }
    )
    final = PortfolioEvent(
        **{
            **first.__dict__,
            "triggering_accession": "b",
            "positions": (EligiblePosition("B", 1.0),),
        }
    )
    consolidated = assign_and_consolidate_execution_dates(
        [first, final], sessions
    )
    assert len(consolidated) == 1
    assert consolidated[0].execution_date == date(2024, 1, 8)
    assert consolidated[0].triggering_accession == "b"


def test_mapping_provenance_symbol_normalization_and_bounded_price_fallback(
    tmp_path,
):
    store = connect_performance_store(tmp_path / "performance.duckdb")
    try:
        mapping = refresh_cusip_ticker_mapping(
            store,
            mapping_loader=lambda: {"084670702": "BRK.B", "084670108": "BRKA"},
        )
        assert mapping == {
            "084670108": "BRK-A",
            "084670702": "BRK-B",
        }
        assert normalize_yfinance_symbol("BRKB") == "BRK-B"
        assert normalize_yfinance_symbol("HEIA") == "HEI-A"
        provenance = store.execute(
            """
            SELECT DISTINCT source, retrieved_at IS NOT NULL
            FROM cusip_ticker_mapping
            """
        ).fetchone()
        assert provenance == (MAPPING_SOURCE, True)

        calls: list[tuple[str, ...]] = []

        def offline_fetcher(symbols, start_date, end_date):
            calls.append(tuple(symbols))
            if len(symbols) > 1:
                raise RuntimeError("deterministic batch failure")
            return pd.DataFrame(
                {
                    "date": [start_date, end_date],
                    "symbol": [symbols[0], symbols[0]],
                    "close": [10.0, 11.0],
                }
            )

        symbols = [f"S{index:02d}" for index in range(31)]
        statuses = refresh_price_cache(
            store,
            symbols,
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
            price_dir=tmp_path / "prices",
            fetcher=offline_fetcher,
        )
        assert set(statuses.values()) == {"READY"}
        assert max(map(len, calls)) == 30
        manifest = store.execute(
            """
            SELECT count(*), min(row_count), count(parquet_sha256)
            FROM price_manifest
            """
        ).fetchone()
        assert manifest == (31, 2, 31)

        calls.clear()
        refresh_price_cache(
            store,
            symbols,
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
            price_dir=tmp_path / "prices",
            fetcher=offline_fetcher,
        )
        assert calls == []

        store.execute(
            """
            INSERT OR REPLACE INTO price_manifest
            VALUES (
                'MISSING', 'NO_DATA', DATE '2024-01-02',
                DATE '2024-01-03', NULL, NULL, NULL, NULL, 0,
                'confirmed no data', now()
            )
            """
        )
        calls.clear()
        refresh_price_cache(
            store,
            ["MISSING"],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
            price_dir=tmp_path / "prices",
            fetcher=offline_fetcher,
        )
        assert calls == []
        refresh_price_cache(
            store,
            ["MISSING"],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
            price_dir=tmp_path / "prices",
            retry_no_data=True,
            fetcher=offline_fetcher,
        )
        assert calls == [("MISSING",)]
    finally:
        store.close()


def test_mapping_and_fully_priced_gates_are_applied_before_renormalizing():
    dates = [date(2024, 1, 2), date(2024, 1, 3)]
    prices = {
        "SPY": _series([100, 101], dates),
        "AAA": _series([10, 11], dates),
    }
    passing = calculate_interval(
        _event(
            (
                EligiblePosition("A", 95.0),
                EligiblePosition("MISSING", 5.0),
            )
        ),
        dates[-1],
        prices,
        {"A": "AAA"},
    )
    assert passing.status == "AVAILABLE"
    assert passing.mapping_coverage == pytest.approx(0.95)
    assert passing.priced_coverage == pytest.approx(0.95)
    assert passing.estimated_return == pytest.approx(0.10)

    mapping_failure = calculate_interval(
        _event(
            (
                EligiblePosition("A", 94.9),
                EligiblePosition("MISSING", 5.1),
            )
        ),
        dates[-1],
        prices,
        {"A": "AAA"},
    )
    assert mapping_failure.status == "UNAVAILABLE"
    assert "mapping coverage" in mapping_failure.unavailable_reason

    pricing_failure = calculate_interval(
        _event((EligiblePosition("A", 94.0), EligiblePosition("B", 6.0))),
        dates[-1],
        prices,
        {"A": "AAA", "B": "BBB"},
    )
    assert pricing_failure.status == "UNAVAILABLE"
    assert pricing_failure.mapping_coverage == 1.0
    assert pricing_failure.priced_coverage == pytest.approx(0.94)


def test_interval_buy_and_hold_math_and_missing_prelisting_price():
    dates = [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
    ]
    event = _event(
        (EligiblePosition("A", 50.0), EligiblePosition("B", 50.0))
    )
    prices = {
        "SPY": _series([100, 101, 102], dates),
        "AAA": _series([10, 11, 12], dates),
        "BBB": _series([20, 19, 18], dates),
    }
    result = calculate_interval(
        event, dates[-1], prices, {"A": "AAA", "B": "BBB"}
    )
    assert result.status == "AVAILABLE"
    assert result.estimated_return == pytest.approx(0.05)
    assert result.daily_nav.iloc[-1] == pytest.approx(1.05)

    no_backfill = {
        **prices,
        "BBB": _series([19, 18], dates[1:]),
    }
    missing = calculate_interval(
        event, dates[-1], no_backfill, {"A": "AAA", "B": "BBB"}
    )
    assert missing.status == "UNAVAILABLE"
    assert missing.priced_coverage == pytest.approx(0.50)


def test_summary_metrics_are_deterministic_and_labeled_as_estimates():
    dates = list(pd.date_range("2020-01-02", "2024-01-02", freq="B").date)
    manager = _series(
        [1 + index / (len(dates) - 1) for index in range(len(dates))],
        dates,
    )
    spy = _series(
        [1 + 0.5 * index / (len(dates) - 1) for index in range(len(dates))],
        dates,
    )
    qqq = _series(
        [1 + 0.8 * index / (len(dates) - 1) for index in range(len(dates))],
        dates,
    )
    interval = IntervalResult(
        dates[0],
        dates[-1],
        "AVAILABLE",
        1.0,
        1.0,
        1.0,
        None,
        manager,
    )
    metrics = calculate_summary_metrics(
        manager,
        spy,
        qqq,
        [interval],
        window="FULL",
        end_date=dates[-1],
    )
    assert metrics["status"] == "AVAILABLE"
    assert metrics["estimated_cagr"] > metrics["spy_cagr"]
    assert metrics["max_drawdown"] == pytest.approx(0.0)
    assert metrics["mapping_coverage"] == 1.0
    assert PERFORMANCE_LABEL == (
        "Hypothetical disclosure-lagged reported 13F long-sleeve estimate"
    )
    assert "fund performance" not in PERFORMANCE_LABEL.lower()
    assert "fund or account return" in PERFORMANCE_DISCLAIMER.lower()


def test_full_summary_respects_configured_fetch_window():
    dates = list(pd.date_range("2019-01-02", "2024-01-02", freq="B").date)
    manager = _series(
        [1 + index / (len(dates) - 1) for index in range(len(dates))],
        dates,
    )
    interval = IntervalResult(
        dates[0],
        dates[-1],
        "AVAILABLE",
        1.0,
        1.0,
        1.0,
        None,
        manager,
    )

    metrics = calculate_summary_metrics(
        manager,
        manager,
        manager,
        [interval],
        window="FULL",
        end_date=dates[-1],
        window_start=date(2020, 1, 2),
    )

    assert metrics["status"] == "AVAILABLE"
    assert metrics["start_date"] == date(2020, 1, 2)
    assert metrics["years"] == pytest.approx(4.0, rel=0.01)


def test_screening_snapshot_returns_and_filters_current_performance(tmp_path):
    generation = tmp_path / "screening.duckdb"
    snapshot = duckdb.connect(str(generation))
    snapshot.execute(SNAPSHOT_SCHEMA)
    snapshot.execute(
        """
        INSERT INTO snapshot_metadata
        VALUES (
            DATE '2025-12-31', now(), 'screening-v1', 'test', 'test', 'fp'
        )
        """
    )
    snapshot.execute(
        """
        INSERT INTO manager_metrics
        VALUES (
            '1', 'Manager', DATE '2025-12-31', 12,
            20000000000, 20000000000, 20000000000, 10, 100,
            50, 20, 8, 25, 0, NULL, false
        )
        """
    )
    snapshot.execute(
        "INSERT INTO manager_quarter_concentration VALUES ('1', 1, 50)"
    )
    snapshot.execute(
        """
        INSERT INTO manager_performance
        VALUES (
            '1', '5Y', 0, 'AVAILABLE', DATE '2021-01-01',
            DATE '2025-12-31', 5, .15, .10, .12, .05, .03, -.20,
            1.1, .4, .2, .60, .55, .99, .98, 20, NULL, ?, ?, 'run'
        )
        """,
        [PERFORMANCE_LABEL, PERFORMANCE_DISCLAIMER],
    )
    snapshot.close()
    pointer = tmp_path / "screening_snapshot.json"
    pointer.write_text(json.dumps({"generation": generation.name}))

    service = ScreeningService(pointer)
    result = service.get_screening_results(
        minimum_concentration_quarters=1,
        performance_window="5Y",
        minimum_spy_excess_cagr=0.04,
        minimum_qqq_excess_cagr=0.02,
        require_performance=True,
    )
    assert result["summary"]["candidate_count"] == 1
    row = result["data"][0]
    assert row["performance_status"] == "AVAILABLE"
    assert row["estimated_cagr"] == pytest.approx(0.15)
    assert row["performance_label"] == PERFORMANCE_LABEL

    excluded = service.get_screening_results(
        minimum_concentration_quarters=1,
        minimum_spy_excess_cagr=0.06,
    )
    assert excluded["summary"]["candidate_count"] == 0
