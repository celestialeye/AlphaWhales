from __future__ import annotations

import json
from datetime import date
from unittest import mock

import duckdb
import pandas as pd
import pytest

from investor_screening.database import connect_database
from investor_screening.performance import (
    PERFORMANCE_DISCLAIMER,
    PERFORMANCE_LABEL,
    METHODOLOGY_VERSION,
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
    load_manager_universe,
    normalize_yfinance_symbol,
    reconstruct_filing_chronology,
    refresh_performance,
    refresh_cusip_ticker_mapping,
    refresh_price_cache,
    resolve_market_symbol,
)
from investor_screening.screener import (
    SNAPSHOT_SCHEMA,
    ScreeningService,
    _compatible_performance_rows,
)
from investor_screening.performance import _mark_manager_failed
from investor_screening import performance as performance_module, screener


def test_source_fingerprint_ignores_roster_display_and_retains_archived_aliases(tmp_path, monkeypatch):
    import hashlib

    source = connect_database(tmp_path / "source.duckdb")
    fund = {
        "cik": "0000000002", "historical_ciks": ["0000000001"],
        "manager": "Example", "name": "Example", "group": "Quality Growth",
    }
    other = {
        "cik": "0000000003", "manager": "Other", "name": "Other",
        "group": "Quality Growth",
    }
    monkeypatch.setattr(screener, "ROSTER_PATH", tmp_path / "roster.json")
    monkeypatch.setattr(screener, "FUND_MANAGERS", [fund])
    original = screener.compute_source_fingerprint(source)
    monkeypatch.setattr(screener, "FUND_MANAGERS", [other, {**fund, "manager": "Renamed"}])
    assert screener.compute_source_fingerprint(source) == original
    (tmp_path / "roster_archive.json").write_text(json.dumps([fund]))
    monkeypatch.setattr(screener, "FUND_MANAGERS", [other])
    assert screener.compute_source_fingerprint(source) == original
    assert "'0000000001','0000000002',NULL" in screener._alias_values()
    (tmp_path / "roster_archive.json").write_text("[]")
    assert screener.compute_source_fingerprint(source) != original
    source.execute(
        "INSERT INTO datasets (dataset_id,local_path,source_sha256,status) VALUES ('new','x',?,'IMPORTED')",
        [hashlib.sha256(b"changed").hexdigest()],
    )
    assert screener.compute_source_fingerprint(source) != original
    source.close()


def test_legacy_fingerprint_migration_requires_exact_source_inputs(tmp_path, monkeypatch):
    import hashlib

    source = connect_database(tmp_path / "source.duckdb")
    monkeypatch.setattr(screener, "ROSTER_PATH", tmp_path / "roster.json")
    monkeypatch.setattr(screener, "FUND_MANAGERS", [
        {"cik": "0000000001", "manager": "Example"},
    ])
    legacy = hashlib.sha256(json.dumps({
        "datasets": [], "methodology": "screening-source-v2",
        "fund_pattern": screener.FUND_LIKE_PATTERN,
        "canonical_aliases": screener._alias_values(legacy=True),
    }, sort_keys=True, default=str).encode()).hexdigest()
    path = tmp_path / "performance.duckdb"
    store = duckdb.connect(str(path))
    store.execute("CREATE TABLE performance_runs (run_id VARCHAR, screening_source_fingerprint VARCHAR)")
    store.executemany("INSERT INTO performance_runs VALUES (?,?)", [
        ("compatible", legacy), ("different-source", "unknown"),
    ])
    store.close()
    assert screener._upgrade_legacy_performance_fingerprints(source, path) == 1
    assert screener._upgrade_legacy_performance_fingerprints(source, path) == 0
    store = duckdb.connect(str(path), read_only=True)
    fingerprints = dict(store.execute("SELECT * FROM performance_runs").fetchall())
    assert fingerprints["compatible"] == screener.compute_source_fingerprint(source)
    assert fingerprints["different-source"] == "unknown"
    assert store.execute("SELECT previous_fingerprint FROM performance_source_migrations").fetchone()[0] == legacy
    assert screener._upgrade_legacy_performance_fingerprints(source, path) == 0
    store.close()
    source.close()


def test_reconciliation_rejects_changed_positions_before_migrating(tmp_path, monkeypatch):
    source_path = tmp_path / "source.duckdb"
    duckdb.connect(str(source_path)).close()
    path = tmp_path / "performance.duckdb"
    store = connect_performance_store(path)
    store.execute(
        """
        INSERT INTO performance_runs VALUES (
            'run','COMPLETE',?, ?, ?, 'old','generation','source',
            DATE '2025-12-31', DATE '2025-12-31',5,0,0,1,now(),now()
        );
        """,
        [METHODOLOGY_VERSION, PERFORMANCE_LABEL, PERFORMANCE_DISCLAIMER],
    )
    store.execute("INSERT INTO performance_run_universe VALUES ('run','1','Example',100)")
    store.execute(
        "INSERT INTO performance_manager_state VALUES ('run','1','Example','COMPLETE',1,NULL,now(),now(),now())"
    )
    store.execute(
        """
        INSERT INTO performance_events VALUES (
            'run','1',0,'2025-06-30','2025-08-14','2025-08-15','a','["a"]',100,1
        )
        """
    )
    store.execute("INSERT INTO performance_event_positions VALUES ('run','1',0,'CUSIP','X','X',100)")
    store.close()
    monkeypatch.setattr(performance_module, "compute_source_fingerprint", lambda source: "new")
    monkeypatch.setattr(performance_module, "_load_run_prices", lambda *args, **kwargs: {
        "SPY": pd.Series([100, 110], index=[date(2025, 8, 15), date(2025, 12, 31)])
    })
    event = PortfolioEvent(
        "1", "Example", date(2025, 6, 30), date(2025, 8, 14),
        "a", ("a",), (EligiblePosition("CUSIP", 200),),
    )
    monkeypatch.setattr(performance_module, "reconstruct_filing_chronology", lambda *args, **kwargs: [event])
    with pytest.raises(ValueError, match="Filing inputs changed"):
        performance_module.reconcile_performance_source(
            "run", source_path=source_path, performance_path=path
        )
    store = duckdb.connect(str(path))
    assert store.execute("SELECT screening_source_fingerprint FROM performance_runs").fetchone()[0] == "old"
    store.close()
    event = PortfolioEvent(
        "1", "Example", date(2025, 6, 30), date(2025, 8, 14),
        "a", ("a",), (EligiblePosition("CUSIP", 100),),
    )
    result = performance_module.reconcile_performance_source(
        "run", source_path=source_path, performance_path=path
    )
    assert result == {"run_id": "run", "status": "RECONCILED", "managers": 1, "events": 1}
    store = duckdb.connect(str(path), read_only=True)
    assert store.execute("SELECT screening_source_fingerprint FROM performance_runs").fetchone()[0] == "new"
    assert store.execute("SELECT reported_value FROM performance_event_positions").fetchone()[0] == 100
    store.close()


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
        reference_symbols = {"CP", "LRCX", "TEAM"}
        assert resolve_market_symbol(
            "13645T100",
            "CPXXXX",
            reference_symbols,
        ) == "CP"
        assert resolve_market_symbol(
            "512807108",
            "LRCXXXXX",
            reference_symbols,
        ) == "LRCX"
        assert resolve_market_symbol(
            "G06242104",
            "TEAMXXXX",
            reference_symbols,
        ) == "TEAM"
        assert resolve_market_symbol(
            "44267D107",
            "HHC",
            reference_symbols,
        ) == "HHH"
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
    snapshot.executemany(
        "INSERT INTO manager_position_quarters VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "1",
                "stock-a",
                quarter,
                date(2025, 12, 31),
                "000000001",
                "AAA",
                "Stock A",
                "COM",
                800000000,
                4.0,
                4.0,
                1,
            )
            for quarter in range(1, 6)
        ],
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
    assert result["summary"]["structural_candidate_count"] == 1
    assert result["summary"]["structural_performance_available_count"] == 1
    assert result["summary"]["performance_fact_manager_count"] == 1
    row = result["data"][0]
    assert row["performance_status"] == "AVAILABLE"
    assert row["estimated_cagr"] == pytest.approx(0.15)
    assert row["persistent_best_bet_count"] == 1
    assert service.get_screening_results(
        minimum_concentration_quarters=1,
        performance_window="5Y",
        benchmark_hurdle="both",
        minimum_excess_cagr=0.03,
        minimum_beat_consistency=0.55,
        maximum_drawdown=0.20,
    )["summary"]["candidate_count"] == 1
    assert service.get_screening_results(
        minimum_concentration_quarters=1,
        performance_window="5Y",
        benchmark_hurdle="both",
        minimum_excess_cagr=0.04,
    )["summary"]["candidate_count"] == 0
    assert service.get_screening_results(
        minimum_concentration_quarters=1,
        performance_window="5Y",
        benchmark_hurdle="both",
        minimum_beat_consistency=0.60,
    )["summary"]["candidate_count"] == 0
    assert service.get_screening_results(
        minimum_concentration_quarters=1,
        performance_window="5Y",
        maximum_drawdown=0.15,
    )["summary"]["candidate_count"] == 0
    assert row["performance_label"] == PERFORMANCE_LABEL

    excluded = service.get_screening_results(
        minimum_concentration_quarters=1,
        minimum_spy_excess_cagr=0.06,
    )
    assert excluded["summary"]["candidate_count"] == 0


def test_performance_universe_is_independent_of_screening_style(tmp_path):
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
            '1', 'Diversified Manager', DATE '2025-12-31', 12,
            20000000000, 20000000000, 10000000000, 200, 50,
            10, 1, 0, 200, 0, NULL, false
        )
        """
    )
    snapshot.close()
    pointer = tmp_path / "screening_snapshot.json"
    pointer.write_text(json.dumps({"generation": generation.name}))

    managers, fingerprint, resolved = load_manager_universe(pointer)

    assert [manager.cik for manager in managers] == ["1"]
    assert fingerprint == "fp"
    assert resolved == generation.resolve()


def test_snapshot_reuses_latest_per_manager_performance_runs(tmp_path):
    performance_path = tmp_path / "performance.duckdb"
    store = connect_performance_store(performance_path)
    try:
        store.executemany(
            """
            INSERT INTO performance_runs
            VALUES (
                ?, 'COMPLETE', ?, ?, ?, 'fp', 'generation', 'source',
                DATE '2025-12-31', DATE '2025-12-31', 5, ?, 0, 1,
                ?, ?
            )
            """,
            [
                (
                    "old",
                    METHODOLOGY_VERSION,
                    PERFORMANCE_LABEL,
                    PERFORMANCE_DISCLAIMER,
                    10.0,
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-02T00:00:00+00:00",
                ),
                (
                    "new",
                    METHODOLOGY_VERSION,
                    PERFORMANCE_LABEL,
                    PERFORMANCE_DISCLAIMER,
                    1.0,
                    "2025-02-01T00:00:00+00:00",
                    "2025-02-02T00:00:00+00:00",
                ),
            ],
        )
        performance_values = (
            "'5Y', 0, 'AVAILABLE', DATE '2021-01-01', "
            "DATE '2025-12-31', 5, .15, .10, .12, .05, .03, -.20, "
            "1.1, .4, .2, .60, .55, .99, .98, 20, NULL, ?, ?"
        )
        store.execute(
            f"""
            INSERT INTO manager_performance
            VALUES ('old', '1', {performance_values})
            """,
            [PERFORMANCE_LABEL, PERFORMANCE_DISCLAIMER],
        )
        store.execute(
            f"""
            INSERT INTO manager_performance
            VALUES ('new', '2', {performance_values})
            """,
            [PERFORMANCE_LABEL, PERFORMANCE_DISCLAIMER],
        )
        store.executemany(
            """
            INSERT INTO monthly_returns
            VALUES (?, ?, DATE '2025-12-31', 0, .01, .01, .01)
            """,
            [("old", "1"), ("new", "2")],
        )
    finally:
        store.close()

    runs, summaries, monthly = _compatible_performance_rows(
        performance_path,
        "fp",
    )

    assert {row[0] for row in runs} == {"old", "new"}
    assert {(row[0], row[-1]) for row in summaries} == {
        ("1", "old"),
        ("2", "new"),
    }
    assert {(row[0], row[-1]) for row in monthly} == {
        ("1", "old"),
        ("2", "new"),
    }


def test_refresh_rejects_mismatched_source_and_leaves_global_failures_resumable(
    tmp_path,
):
    source_path = tmp_path / "source.duckdb"
    duckdb.connect(str(source_path)).close()
    performance_path = tmp_path / "performance.duckdb"
    universe = (
        [ManagerUniverseItem("1", "Manager", 20_000_000_000)],
        "snapshot-fingerprint",
        tmp_path / "snapshot.duckdb",
    )

    with (
        mock.patch(
            "investor_screening.performance.load_manager_universe",
            return_value=universe,
        ),
        mock.patch(
            "investor_screening.performance.compute_source_fingerprint",
            return_value="different-source-fingerprint",
        ),
    ):
        with pytest.raises(ValueError, match="does not match"):
            refresh_performance(
                source_path=source_path,
                performance_path=performance_path,
                price_dir=tmp_path / "prices",
            )

    with (
        mock.patch(
            "investor_screening.performance.load_manager_universe",
            return_value=universe,
        ),
        mock.patch(
            "investor_screening.performance.compute_source_fingerprint",
            return_value="snapshot-fingerprint",
        ),
        mock.patch(
            "investor_screening.performance.refresh_cusip_ticker_mapping",
            side_effect=RuntimeError("fixture failure"),
        ),
    ):
        with pytest.raises(RuntimeError, match="fixture failure"):
            refresh_performance(
                source_path=source_path,
                performance_path=performance_path,
                price_dir=tmp_path / "prices",
            )

    store = connect_performance_store(performance_path)
    try:
        assert store.execute(
            "SELECT status FROM performance_runs"
        ).fetchall() == [("BUILDING",)]
    finally:
        store.close()


def test_refresh_performance_resumes_completed_manager_checkpoints(tmp_path):
    source_path = tmp_path / "source.duckdb"
    duckdb.connect(str(source_path)).close()
    performance_path = tmp_path / "performance.duckdb"
    price_dir = tmp_path / "prices"
    universe = (
        [
            ManagerUniverseItem("1", "Manager One", 20_000_000_000),
            ManagerUniverseItem("2", "Manager Two", 10_000_000_000),
        ],
        "snapshot-fingerprint",
        tmp_path / "snapshot.duckdb",
    )

    def price_fetcher(symbols, start_date, end_date):
        rows = []
        for symbol in symbols:
            rows.extend(
                [
                    {"date": start_date, "symbol": symbol, "close": 100.0},
                    {"date": end_date, "symbol": symbol, "close": 110.0},
                ]
            )
        return pd.DataFrame(rows)

    shared_patches = (
        mock.patch(
            "investor_screening.performance.load_manager_universe",
            return_value=universe,
        ),
        mock.patch(
            "investor_screening.performance.compute_source_fingerprint",
            return_value="snapshot-fingerprint",
        ),
        mock.patch(
            "investor_screening.performance.reconstruct_filing_chronology",
            return_value=[],
        ),
    )
    with shared_patches[0], shared_patches[1], shared_patches[2]:
        first = refresh_performance(
            source_path=source_path,
            performance_path=performance_path,
            price_dir=price_dir,
            as_of=date(2025, 12, 31),
            mapping_loader=lambda: {"000000001": "AAA"},
            price_fetcher=price_fetcher,
            batch_size=1,
            max_managers=1,
        )
    assert first["status"] == "BUILDING"
    assert first["manager_states"] == {
        "PENDING": 1,
        "BUILDING": 0,
        "COMPLETE": 1,
        "FAILED": 0,
        "EXHAUSTED": 0,
    }

    with (
        mock.patch(
            "investor_screening.performance.load_manager_universe",
            return_value=universe,
        ),
        mock.patch(
            "investor_screening.performance.compute_source_fingerprint",
            return_value="snapshot-fingerprint",
        ),
        mock.patch(
            "investor_screening.performance.reconstruct_filing_chronology",
            return_value=[],
        ),
    ):
        second = refresh_performance(
            source_path=source_path,
            performance_path=performance_path,
            price_dir=price_dir,
            as_of=date(2025, 12, 31),
            mapping_loader=lambda: (_ for _ in ()).throw(
                AssertionError("frozen run mapping should be reused")
            ),
            price_fetcher=lambda *_: (_ for _ in ()).throw(
                AssertionError("frozen benchmark prices should be reused")
            ),
            batch_size=1,
        )

    assert second["run_id"] == first["run_id"]
    assert second["status"] == "COMPLETE"
    assert second["processed_this_invocation"] == 1
    assert second["manager_states"] == {
        "PENDING": 0,
        "BUILDING": 0,
        "COMPLETE": 2,
        "FAILED": 0,
        "EXHAUSTED": 0,
    }
    with (
        mock.patch(
            "investor_screening.performance.load_manager_universe",
            return_value=universe,
        ),
        mock.patch(
            "investor_screening.performance.compute_source_fingerprint",
            return_value="snapshot-fingerprint",
        ),
    ):
        repeated = refresh_performance(
            source_path=source_path,
            performance_path=performance_path,
            price_dir=price_dir,
            as_of=date(2025, 12, 31),
        )
    assert repeated["run_id"] == first["run_id"]
    assert repeated["status"] == "COMPLETE"
    assert repeated["processed_this_invocation"] == 0
    assert repeated["reused_complete_run"] is True
    store = connect_performance_store(performance_path)
    try:
        assert store.execute(
            """
            SELECT cik, attempt_count, status
            FROM performance_manager_state
            ORDER BY cik
            """
        ).fetchall() == [
            ("1", 1, "COMPLETE"),
            ("2", 1, "COMPLETE"),
        ]
        assert store.execute(
            """
            SELECT cik, count(*)
            FROM manager_performance
            WHERE run_id = ?
            GROUP BY cik
            ORDER BY cik
            """,
            [first["run_id"]],
        ).fetchall() == [("1", 3), ("2", 3)]
        assert store.execute(
            """
            SELECT count(*)
            FROM performance_run_mapping
            WHERE run_id = ?
            """,
            [first["run_id"]],
        ).fetchone()[0] == 1
        assert store.execute(
            """
            SELECT count(*)
            FROM performance_run_prices
            WHERE run_id = ?
            """,
            [first["run_id"]],
        ).fetchone()[0] == 2
    finally:
        store.close()


def test_manager_failures_become_terminal_after_bounded_retries(tmp_path):
    store = connect_performance_store(tmp_path / "performance.duckdb")
    manager = ManagerUniverseItem("1", "Manager", 1_000_000_000)
    try:
        store.execute(
            """
            INSERT INTO performance_manager_state
            VALUES (
                'run', '1', 'Manager', 'PENDING', 0, NULL, NULL, NULL, now()
            )
            """
        )
        _mark_manager_failed(
            store,
            run_id="run",
            manager=manager,
            error="first",
        )
        assert store.execute(
            """
            SELECT status, attempt_count
            FROM performance_manager_state
            """
        ).fetchone() == ("FAILED", 1)
        _mark_manager_failed(
            store,
            run_id="run",
            manager=manager,
            error="second",
        )
        _mark_manager_failed(
            store,
            run_id="run",
            manager=manager,
            error="third",
        )
        assert store.execute(
            """
            SELECT status, attempt_count, last_error
            FROM performance_manager_state
            """
        ).fetchone() == ("EXHAUSTED", 3, "third")
    finally:
        store.close()
