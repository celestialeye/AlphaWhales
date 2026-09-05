from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import duckdb
import pandas as pd
import pytest

import data_service as data_service_module
from data_service import DataService
from filing_operations import FilingOperations


def filing(accession, *, filing_date, report_period, form="13F-HR"):
    return {
        "accession_number": accession,
        "canonical_cik": "0000000001",
        "source_cik": "0000000001",
        "manager_name": "Example Manager",
        "form": form,
        "filing_date": filing_date,
        "report_period": report_period,
        "source_url": f"https://www.sec.gov/{accession}",
    }


def create_filing_detail_archive(database_path, accession=None):
    connection = duckdb.connect(str(database_path))
    connection.execute(
        """
        CREATE TABLE submissions (
            accession_number VARCHAR,
            filing_date DATE,
            submission_type VARCHAR,
            cik VARCHAR,
            period_of_report DATE
        );
        CREATE TABLE cover_pages (
            accession_number VARCHAR,
            is_amendment BOOLEAN,
            amendment_number INTEGER,
            amendment_type VARCHAR,
            report_type VARCHAR,
            additional_information VARCHAR
        );
        CREATE TABLE summary_pages (
            accession_number VARCHAR,
            other_included_managers_count INTEGER,
            table_entry_total BIGINT,
            table_value_usd DECIMAL(38, 0),
            is_confidential_omitted BOOLEAN
        );
        CREATE TABLE signatures (
            accession_number VARCHAR,
            signer_name VARCHAR,
            signer_title VARCHAR,
            signature_date DATE
        );
        CREATE TABLE holdings (
            accession_number VARCHAR,
            ticker VARCHAR,
            name_of_issuer VARCHAR,
            title_of_class VARCHAR,
            cusip VARCHAR,
            put_call VARCHAR,
            value_usd DECIMAL(38, 0),
            shares_or_principal DECIMAL(38, 6),
            shares_or_principal_type VARCHAR
        )
        """
    )
    if accession:
        connection.execute(
            """
            INSERT INTO submissions VALUES (
                ?, '2026-05-15', '13F-HR/A', '1', '2026-03-31'
            )
            """,
            [accession],
        )
    return connection


class FakeDataService:
    def __init__(self, discoveries):
        self.discoveries = discoveries
        self.errors = []
        self.cache = {
            "0000000001": {
                "status": "loaded",
                "metadata": {},
            }
        }
        self.refreshed = []
        self.invalidated_inputs = []
        self.events = []
        self.selected_accession = None
        self.persisted_accession = None

    def _roster_fingerprint(self, funds=None):
        return "test-roster"

    def discover_recent_filings(self, cutoff):
        return {
            "filings": list(self.discoveries),
            "errors": list(self.errors),
            "managers_checked": 1,
        }

    def get_available_periods(self, count=20):
        return [
            "2026-06-30",
            "2026-03-31",
        ][:count]

    async def refresh_funds(self, ciks):
        self.refreshed.append(list(ciks))
        if self.selected_accession:
            self.cache["0000000001"] = {
                "status": "loaded",
                "metadata": {
                    "accession_number": self.selected_accession,
                },
            }
            self.persisted_accession = self.selected_accession
        return True

    def invalidate_filing_periods(self, periods):
        values = list(periods)
        self.invalidated_inputs.append(values)
        return ["2026-06-30", "2026-09-30", "2026-12-31"]

    async def broadcast_event(self, event):
        self.events.append(event)

    def get_persisted_accession(self, cik):
        return self.persisted_accession


def test_first_run_baselines_inventory_and_refreshes_manager_cache():
    with tempfile.TemporaryDirectory() as tempdir:
        service = FakeDataService([
            filing(
                "0000000001-26-000001",
                filing_date="2026-08-14",
                report_period="2026-06-30",
            )
        ])
        operations = FilingOperations(
            service,
            ledger_path=Path(tempdir) / "ledger.sqlite3",
            publication_path=Path(tempdir) / "publication.json",
        )

        result = asyncio.run(operations.run(trigger="scheduler"))
        dashboard = operations.get_dashboard()

    assert result["status"] == "COMPLETE"
    assert result["baseline"] is True
    assert result["new_filings"] == 0
    assert result["baseline_filings"] == 1
    assert result["refreshed_managers"] == 1
    assert service.refreshed == [["0000000001"]]
    assert dashboard["filings"][0]["status"] == "BASELINED"


def test_new_accession_is_published_once_and_invalidates_dependents():
    with tempfile.TemporaryDirectory() as tempdir:
        ledger_path = Path(tempdir) / "ledger.sqlite3"
        publication_path = Path(tempdir) / "publication.json"
        original = filing(
            "0000000001-26-000001",
            filing_date="2026-08-14",
            report_period="2026-06-30",
        )
        service = FakeDataService([original])
        operations = FilingOperations(
            service,
            ledger_path=ledger_path,
            publication_path=publication_path,
        )
        asyncio.run(operations.run(trigger="scheduler"))

        amendment = filing(
            "0000000001-26-000002",
            filing_date="2026-08-21",
            report_period="2026-06-30",
            form="13F-HR/A",
        )
        service.discoveries = [original, amendment]
        service.selected_accession = amendment["accession_number"]
        result = asyncio.run(operations.run(trigger="scheduler"))
        repeat = asyncio.run(operations.run(trigger="scheduler"))
        dashboard = operations.get_dashboard(status="PUBLISHED")

    assert result["new_filings"] == 1
    assert result["published_filings"] == 1
    assert result["invalidated_periods"] == [
        "2026-06-30",
        "2026-09-30",
        "2026-12-31",
    ]
    assert service.invalidated_inputs == [["2026-06-30"]]
    assert repeat["status"] == "NO_CHANGES"
    assert repeat["new_filings"] == 0
    assert len(service.refreshed) == 2
    assert dashboard["total"] == 1
    assert dashboard["filings"][0]["accession_number"] == (
        amendment["accession_number"]
    )
    assert any(
        event["type"] == "filings_ingested"
        for event in service.events
    )


def test_exact_invalidation_deletes_recreated_historical_cache():
    with tempfile.TemporaryDirectory() as tempdir:
        original_cache_dir = data_service_module.CACHE_DIR
        data_service_module.CACHE_DIR = tempdir
        try:
            service = DataService.__new__(DataService)
            service.period_cache_generations = {}
            service.period_caches = {"2026-06-30": {"stale": True}}
            service.period_cache_progress = {
                "2026-06-30": {"state": "ready"}
            }
            path = Path(
                service._get_period_cache_path("2026-06-30")
            )
            path.write_text('{"stale": true}', encoding="utf-8")

            invalidated = service.invalidate_exact_filing_periods(
                ["2026-06-30"]
            )

            assert invalidated == ["2026-06-30"]
            assert not path.exists()
            assert service.period_cache_generations["2026-06-30"] == 1
            assert "2026-06-30" not in service.period_caches
            assert "2026-06-30" not in service.period_cache_progress
        finally:
            data_service_module.CACHE_DIR = original_cache_dir


def test_fund_cache_publication_rejects_older_filing():
    with tempfile.TemporaryDirectory() as tempdir:
        original_cache_dir = data_service_module.CACHE_DIR
        data_service_module.CACHE_DIR = tempdir
        try:
            cik = "0000000001"
            fund = {"cik": cik, "historical_ciks": []}
            service = DataService.__new__(DataService)
            service.cache = {
                cik: {
                    "fund_info": fund,
                    "status": "loaded",
                    "metadata": {
                        "report_period": "2026-06-30",
                        "filing_date": "2026-08-21",
                        "accession_number": "0000000001-26-000002",
                    },
                    "last_updated": "2026-08-21T12:00:00+00:00",
                    "holdings": pd.DataFrame(),
                    "comparison": pd.DataFrame(),
                    "previous_comparison": pd.DataFrame(),
                }
            }
            assert service._save_fund_to_disk_cache(cik) is True

            service.cache[cik]["metadata"] = {
                "report_period": "2026-06-30",
                "filing_date": "2026-08-14",
                "accession_number": "0000000001-26-000001",
            }
            service.cache[cik]["last_updated"] = (
                "2026-08-14T12:00:00+00:00"
            )

            assert service._save_fund_to_disk_cache(cik) is False
            payload = json.loads(
                Path(service._get_disk_cache_path(cik)).read_text(
                    encoding="utf-8"
                )
            )
            assert payload["metadata"]["accession_number"] == (
                "0000000001-26-000002"
            )
        finally:
            data_service_module.CACHE_DIR = original_cache_dir


def test_dashboard_separates_baseline_inventory_and_filters_records():
    with tempfile.TemporaryDirectory() as tempdir:
        ledger_path = Path(tempdir) / "ledger.sqlite3"
        publication_path = Path(tempdir) / "publication.json"
        original = filing(
            "0000000001-26-000001",
            filing_date="2026-08-14",
            report_period="2026-06-30",
        )
        service = FakeDataService([original])
        operations = FilingOperations(
            service,
            ledger_path=ledger_path,
            publication_path=publication_path,
        )
        asyncio.run(operations.run(trigger="scheduler"))

        amendment = filing(
            "0000000001-26-000002",
            filing_date="2026-08-21",
            report_period="2026-06-30",
            form="13F-HR/A",
        )
        service.discoveries = [original, amendment]
        service.selected_accession = amendment["accession_number"]
        asyncio.run(operations.run(trigger="scheduler"))

        dashboard = operations.get_dashboard(
            form="13F-HR/A",
            report_period="2026-06-30",
            search="0000000001",
        )

    assert dashboard["summary"]["known_accessions"] == 2
    assert dashboard["summary"]["baseline_accessions"] == 1
    assert dashboard["summary"]["new_accessions"] == 1
    assert dashboard["summary"]["retry_queue"] == 0
    assert dashboard["total"] == 1
    assert dashboard["filings"][0]["form"] == "13F-HR/A"
    assert dashboard["filter_options"]["forms"] == [
        "13F-HR",
        "13F-HR/A",
    ]
    assert dashboard["filter_options"]["report_periods"] == [
        "2026-06-30",
    ]


def test_historical_inventory_loader_scopes_periods_and_maps_cik_chain():
    with tempfile.TemporaryDirectory() as tempdir:
        database_path = Path(tempdir) / "history.duckdb"
        connection = duckdb.connect(str(database_path))
        connection.execute(
            """
            CREATE TABLE submissions (
                accession_number VARCHAR,
                filing_date DATE,
                submission_type VARCHAR,
                cik VARCHAR,
                period_of_report DATE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE sec_filings (
                accession_number VARCHAR,
                form VARCHAR,
                source_url VARCHAR
            )
            """
        )
        connection.executemany(
            "INSERT INTO submissions VALUES (?, ?, ?, ?, ?)",
            [
                (
                    "0000000001-26-000001",
                    "2026-02-14",
                    "13F-HR",
                    "1",
                    "2025-12-31",
                ),
                (
                    "0000000002-26-000002",
                    "2026-02-20",
                    "13F-HR/A",
                    "2",
                    "2025-12-31",
                ),
                (
                    "0000000001-25-000003",
                    "2025-11-14",
                    "13F-HR",
                    "1",
                    "2025-09-30",
                ),
            ],
        )
        connection.execute(
            """
            INSERT INTO sec_filings VALUES
            ('0000000001-26-000001', '13F-HR', 'https://example.test/one'),
            ('0000000002-26-000002', '13F-HR/A', '')
            """
        )
        connection.close()

        inventory = FilingOperations._load_historical_inventory(
            database_path,
            1,
            [
                {
                    "cik": "0000000001",
                    "manager": "Example Manager",
                    "historical_ciks": ["0000000002"],
                }
            ],
        )

    assert len(inventory["filings"]) == 2
    assert inventory["report_periods"] == ["2025-12-31"]
    assert inventory["periods_found"] == ["2025-12-31"]
    assert inventory["errors"] == []
    assert {
        item["canonical_cik"]
        for item in inventory["filings"]
    } == {"0000000001"}
    assert {
        item["source_cik"]
        for item in inventory["filings"]
    } == {"0000000001", "0000000002"}
    amendment = next(
        item
        for item in inventory["filings"]
        if item["form"] == "13F-HR/A"
    )
    assert amendment["source_url"].endswith(
        "/000000000226000002/0000000002-26-000002.txt"
    )


def test_filing_detail_reads_archive_summary_and_ranked_holdings():
    with tempfile.TemporaryDirectory() as tempdir:
        database_path = Path(tempdir) / "history.duckdb"
        accession = "0000000001-26-000001"
        connection = create_filing_detail_archive(
            database_path,
            accession,
        )
        connection.execute(
            """
            INSERT INTO cover_pages VALUES (
                ?, true, 1, 'RESTATEMENT', '13F HOLDINGS REPORT', 'Corrected'
            )
            """,
            [accession],
        )
        connection.execute(
            "INSERT INTO summary_pages VALUES (?, 0, 2, 1000, true)",
            [accession],
        )
        connection.execute(
            """
            INSERT INTO signatures VALUES (
                ?, 'Signer', 'Compliance Officer', '2026-05-15'
            )
            """,
            [accession],
        )
        connection.executemany(
            "INSERT INTO holdings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    accession,
                    "AAA",
                    "Alpha Inc",
                    "COM",
                    "111111111",
                    None,
                    700,
                    70,
                    "SH",
                ),
                (
                    accession,
                    "BBB",
                    "Beta Inc",
                    "COM",
                    "222222222",
                    "PUT",
                    300,
                    30,
                    "SH",
                ),
            ],
        )
        connection.close()

        service = FakeDataService([])
        operations = FilingOperations(
            service,
            ledger_path=Path(tempdir) / "ledger.sqlite3",
            publication_path=Path(tempdir) / "publication.json",
            historical_database_path=database_path,
        )
        run_id = operations.ledger.start_run("scheduler", "test-roster")
        operations.ledger.record_discoveries(
            run_id,
            [
                filing(
                    accession,
                    filing_date="2026-05-15",
                    report_period="2026-03-31",
                    form="13F-HR/A",
                )
            ],
            [],
            baseline=True,
        )
        operations.ledger.record_outcomes(
            run_id,
            [{"accession_number": accession, "status": "BASELINED"}],
        )

        detail = operations.get_filing_detail(accession)

    assert detail["detail_source"] == "Official SEC archive"
    assert detail["summary"]["is_amendment"] is True
    assert detail["summary"]["amendment_type"] == "RESTATEMENT"
    assert detail["summary"]["is_confidential_omitted"] is True
    assert detail["summary"]["total_value_usd"] == 1000
    assert detail["summary"]["put_count"] == 1
    assert detail["top_holdings"][0]["ticker"] == "AAA"
    assert detail["top_holdings"][0]["portfolio_weight"] == 70


def test_filing_detail_falls_back_to_current_manager_cache():
    with tempfile.TemporaryDirectory() as tempdir:
        accession = "0000000001-26-000001"
        database_path = Path(tempdir) / "history.duckdb"
        create_filing_detail_archive(
            database_path,
            accession,
        ).close()
        service = FakeDataService([])
        service.cache["0000000001"] = {
            "status": "loaded",
            "metadata": {"accession_number": accession},
            "holdings": pd.DataFrame(
                [
                    {
                        "Ticker": "AAA",
                        "Issuer": "Alpha Inc",
                        "Class": "COM",
                        "Cusip": "111111111",
                        "PutCall": "",
                        "Value": 750,
                        "SharesPrnAmount": 75,
                        "Type": "Shares",
                    },
                    {
                        "Ticker": "BBB",
                        "Issuer": "Beta Inc",
                        "Class": "COM",
                        "Cusip": "222222222",
                        "PutCall": "CALL",
                        "Value": 250,
                        "SharesPrnAmount": 25,
                        "Type": "Shares",
                    },
                ]
            ),
        }
        operations = FilingOperations(
            service,
            ledger_path=Path(tempdir) / "ledger.sqlite3",
            publication_path=Path(tempdir) / "publication.json",
            historical_database_path=database_path,
        )
        run_id = operations.ledger.start_run("scheduler", "test-roster")
        operations.ledger.record_discoveries(
            run_id,
            [
                filing(
                    accession,
                    filing_date="2026-08-14",
                    report_period="2026-06-30",
                )
            ],
            [],
            baseline=True,
        )
        operations.ledger.record_outcomes(
            run_id,
            [{"accession_number": accession, "status": "BASELINED"}],
        )

        detail = operations.get_filing_detail(accession)

    assert detail["detail_source"] == "Current normalized manager cache"
    assert detail["summary"]["holding_count"] == 2
    assert detail["summary"]["total_value_usd"] == 1000
    assert detail["summary"]["call_count"] == 1
    assert detail["summary"]["is_confidential_omitted"] is None
    assert detail["top_holdings"][0]["ticker"] == "AAA"
    assert detail["top_holdings"][0]["portfolio_weight"] == 75


def test_filing_detail_uses_cache_when_archive_schema_is_unavailable():
    with tempfile.TemporaryDirectory() as tempdir:
        accession = "0000000001-26-000001"
        database_path = Path(tempdir) / "invalid.duckdb"
        connection = duckdb.connect(str(database_path))
        connection.execute("CREATE TABLE unrelated (value INTEGER)")
        connection.close()
        service = FakeDataService([])
        service.cache["0000000001"] = {
            "status": "loaded",
            "metadata": {"accession_number": accession},
            "holdings": pd.DataFrame(
                [{
                    "Ticker": "AAA",
                    "Issuer": "Alpha Inc",
                    "Class": "COM",
                    "Cusip": "111111111",
                    "PutCall": "",
                    "Value": 1000,
                    "SharesPrnAmount": 100,
                    "Type": "Shares",
                }]
            ),
        }
        operations = FilingOperations(
            service,
            ledger_path=Path(tempdir) / "ledger.sqlite3",
            publication_path=Path(tempdir) / "publication.json",
            historical_database_path=database_path,
        )
        run_id = operations.ledger.start_run("scheduler", "test-roster")
        operations.ledger.record_discoveries(
            run_id,
            [
                filing(
                    accession,
                    filing_date="2026-08-14",
                    report_period="2026-06-30",
                )
            ],
            [],
            baseline=True,
        )

        detail = operations.get_filing_detail(accession)

    assert detail["detail_source"] == "Current normalized manager cache"
    assert detail["summary"]["holding_count"] == 1


def test_metadata_only_filing_preserves_amendment_and_unknown_counts():
    with tempfile.TemporaryDirectory() as tempdir:
        accession = "0000000001-26-000001"
        database_path = Path(tempdir) / "history.duckdb"
        create_filing_detail_archive(
            database_path,
            accession,
        ).close()
        service = FakeDataService([])
        operations = FilingOperations(
            service,
            ledger_path=Path(tempdir) / "ledger.sqlite3",
            publication_path=Path(tempdir) / "publication.json",
            historical_database_path=database_path,
        )
        run_id = operations.ledger.start_run("scheduler", "test-roster")
        operations.ledger.record_discoveries(
            run_id,
            [
                filing(
                    accession,
                    filing_date="2026-08-21",
                    report_period="2026-06-30",
                    form="13F-HR/A",
                )
            ],
            [],
            baseline=True,
        )

        detail = operations.get_filing_detail(accession)

    assert detail["detail_source"] == "Ledger metadata"
    assert detail["summary"]["is_amendment"] is True
    assert detail["summary"]["holding_count"] is None
    assert detail["summary"]["total_value_usd"] is None
    assert detail["summary"]["is_confidential_omitted"] is None


def test_history_backfill_is_idempotent_and_hidden_from_daily_runs():
    with tempfile.TemporaryDirectory() as tempdir:
        ledger_path = Path(tempdir) / "ledger.sqlite3"
        publication_path = Path(tempdir) / "publication.json"
        current = filing(
            "0000000001-26-000001",
            filing_date="2026-08-14",
            report_period="2026-06-30",
        )
        historical = filing(
            "0000000001-26-000002",
            filing_date="2026-05-15",
            report_period="2026-03-31",
        )
        service = FakeDataService([current])
        operations = FilingOperations(
            service,
            ledger_path=ledger_path,
            publication_path=publication_path,
        )
        asyncio.run(operations.run(trigger="scheduler"))
        operations._load_historical_inventory = (
            lambda database_path, quarters, roster: {
                "filings": [historical],
                "report_periods": [
                    "2026-06-30",
                    "2026-03-31",
                ],
                "periods_found": ["2026-03-31"],
                "errors": [],
            }
        )

        result = asyncio.run(
            operations.backfill_history(
                database_path=Path(tempdir) / "unused.duckdb"
            )
        )
        repeat = asyncio.run(
            operations.backfill_history(
                database_path=Path(tempdir) / "unused.duckdb"
            )
        )
        dashboard = operations.get_dashboard()
        with operations.ledger._connection() as connection:
            history_runs = connection.execute(
                """
                SELECT count(*)
                FROM ingestion_runs
                WHERE trigger = 'history_backfill'
                """
            ).fetchone()[0]

    assert result["inserted_filings"] == 1
    assert result["quarters_requested"] == 2
    assert result["quarters_found"] == 1
    assert repeat["status"] == "NO_CHANGES"
    assert repeat["inserted_filings"] == 0
    assert history_runs == 1
    assert dashboard["summary"]["historical_accessions"] == 1
    assert dashboard["summary"]["operational_accessions"] == 1
    assert dashboard["summary"]["report_period_count"] == 2
    assert dashboard["runs"][0]["trigger"] == "scheduler"
    historical_row = next(
        item
        for item in dashboard["filings"]
        if item["accession_number"] == historical["accession_number"]
    )
    assert historical_row["status"] == "HISTORICAL"
    assert service.refreshed == [["0000000001"]]


def test_daily_baseline_processes_history_backfilled_first():
    with tempfile.TemporaryDirectory() as tempdir:
        ledger_path = Path(tempdir) / "ledger.sqlite3"
        publication_path = Path(tempdir) / "publication.json"
        current = filing(
            "0000000001-26-000001",
            filing_date="2026-08-14",
            report_period="2026-06-30",
        )
        service = FakeDataService([current])
        operations = FilingOperations(
            service,
            ledger_path=ledger_path,
            publication_path=publication_path,
        )
        operations._load_historical_inventory = (
            lambda database_path, quarters, roster: {
                "filings": [current],
                "report_periods": ["2026-06-30"],
                "periods_found": ["2026-06-30"],
                "errors": [],
            }
        )

        asyncio.run(
            operations.backfill_history(
                database_path=Path(tempdir) / "unused.duckdb"
            )
        )
        result = asyncio.run(operations.run(trigger="scheduler"))
        repeat = asyncio.run(operations.run(trigger="scheduler"))
        dashboard = operations.get_dashboard()

    assert result["baseline"] is True
    assert result["baseline_filings"] == 1
    assert result["refreshed_managers"] == 1
    assert repeat["baseline"] is False
    assert repeat["status"] == "NO_CHANGES"
    assert dashboard["filings"][0]["status"] == "BASELINED"
    assert service.refreshed == [["0000000001"]]


def test_daily_run_processes_historical_accession_after_baseline():
    with tempfile.TemporaryDirectory() as tempdir:
        ledger_path = Path(tempdir) / "ledger.sqlite3"
        publication_path = Path(tempdir) / "publication.json"
        current = filing(
            "0000000001-26-000001",
            filing_date="2026-08-14",
            report_period="2026-06-30",
        )
        historical = filing(
            "0000000001-26-000002",
            filing_date="2026-08-21",
            report_period="2026-03-31",
        )
        service = FakeDataService([current])
        operations = FilingOperations(
            service,
            ledger_path=ledger_path,
            publication_path=publication_path,
        )
        asyncio.run(operations.run(trigger="scheduler"))
        operations._load_historical_inventory = (
            lambda database_path, quarters, roster: {
                "filings": [historical],
                "report_periods": ["2026-03-31"],
                "periods_found": ["2026-03-31"],
                "errors": [],
            }
        )
        asyncio.run(
            operations.backfill_history(
                database_path=Path(tempdir) / "unused.duckdb"
            )
        )
        service.discoveries = [current, historical]

        result = asyncio.run(operations.run(trigger="scheduler"))
        dashboard = operations.get_dashboard()
        historical_row = next(
            item
            for item in dashboard["filings"]
            if item["accession_number"] == historical["accession_number"]
        )

    assert result["baseline"] is False
    assert result["new_filings"] == 0
    assert result["recorded_filings"] == 1
    assert historical_row["status"] == "RECORDED"
    assert service.refreshed == [
        ["0000000001"],
        ["0000000001"],
    ]


def test_failed_accession_is_retried_without_counting_as_new():
    with tempfile.TemporaryDirectory() as tempdir:
        ledger_path = Path(tempdir) / "ledger.sqlite3"
        publication_path = Path(tempdir) / "publication.json"
        original = filing(
            "0000000001-26-000001",
            filing_date="2026-08-14",
            report_period="2026-06-30",
        )
        service = FakeDataService([original])
        operations = FilingOperations(
            service,
            ledger_path=ledger_path,
            publication_path=publication_path,
        )
        asyncio.run(operations.run(trigger="scheduler"))

        amendment = filing(
            "0000000001-26-000002",
            filing_date="2026-08-21",
            report_period="2026-06-30",
            form="13F-HR/A",
        )
        service.discoveries = [original, amendment]
        service.cache["0000000001"] = {
            "status": "error",
            "metadata": {},
            "error": "temporary SEC failure",
        }
        first_attempt = asyncio.run(
            operations.run(trigger="scheduler")
        )

        service.selected_accession = amendment["accession_number"]
        service.cache["0000000001"] = {
            "status": "loaded",
            "metadata": {},
        }
        retry = asyncio.run(operations.run(trigger="repair"))
        dashboard = operations.get_dashboard(status="PUBLISHED")

    assert first_attempt["status"] == "PARTIAL"
    assert first_attempt["new_filings"] == 1
    assert retry["status"] == "COMPLETE"
    assert retry["new_filings"] == 0
    assert retry["published_filings"] == 1
    assert dashboard["total"] == 1


def test_interrupted_baseline_remains_retryable():
    with tempfile.TemporaryDirectory() as tempdir:
        ledger_path = Path(tempdir) / "ledger.sqlite3"
        publication_path = Path(tempdir) / "publication.json"
        baseline_filing = filing(
            "0000000001-26-000001",
            filing_date="2026-08-14",
            report_period="2026-06-30",
        )
        service = FakeDataService([baseline_filing])
        service.selected_accession = baseline_filing["accession_number"]
        operations = FilingOperations(
            service,
            ledger_path=ledger_path,
            publication_path=publication_path,
        )
        original_publish = operations._publish_manifest
        operations._publish_manifest = lambda payload: (
            (_ for _ in ()).throw(PermissionError("manifest locked"))
        )

        try:
            asyncio.run(operations.run(trigger="scheduler"))
        except PermissionError:
            pass
        else:
            raise AssertionError("Expected manifest publication failure")

        operations._publish_manifest = original_publish
        retry = asyncio.run(operations.run(trigger="repair"))
        dashboard = operations.get_dashboard(status="PUBLISHED")

    assert retry["status"] == "COMPLETE"
    assert retry["new_filings"] == 0
    assert retry["published_filings"] == 1
    assert dashboard["total"] == 1


def test_later_complete_filing_wins_period_selection():
    service = DataService.__new__(DataService)
    fund = {
        "cik": "0000000001",
        "manager": "Example Manager",
    }
    original_report = SimpleNamespace(
        holdings=pd.DataFrame({"Cusip": ["1", "2"]})
    )
    amendment_report = SimpleNamespace(
        holdings=pd.DataFrame({"Cusip": ["1", "2"]})
    )
    original = SimpleNamespace(obj=lambda: original_report)
    amendment = SimpleNamespace(obj=lambda: amendment_report)
    entries = [
        (
            original,
            "0000000001",
            {
                **filing(
                    "0000000001-26-000001",
                    filing_date="2026-08-14",
                    report_period="2026-06-30",
                ),
            },
        ),
        (
            amendment,
            "0000000001",
            {
                **filing(
                    "0000000001-26-000002",
                    filing_date="2026-08-21",
                    report_period="2026-06-30",
                    form="13F-HR/A",
                ),
                "amendment_type": "RESTATEMENT",
            },
        ),
    ]

    report, source_cik, metadata = service._find_best_report_for_period(
        fund,
        "2026-06-30",
        entries,
    )

    assert report is amendment_report
    assert source_cik == "0000000001"
    assert metadata["accession_number"] == "0000000001-26-000002"


def test_refresh_fund_does_not_report_failed_disk_publication():
    service = DataService.__new__(DataService)
    service.cache = {
        "0000000001": {
            "fund_info": {
                "cik": "0000000001",
                "manager": "Example Manager",
            },
            "status": "loaded",
            "metadata": {"accession_number": "old"},
            "holdings": None,
            "comparison": None,
            "previous_comparison": None,
            "last_updated": "old",
        }
    }
    service.manager_adjustment_cache = {}
    service._fetch_fund_sync = lambda cik: {
        "status": "loaded",
        "metadata": {"accession_number": "new"},
        "holdings": pd.DataFrame(),
        "comparison": None,
        "previous_comparison": None,
    }
    service._save_fund_to_disk_cache = lambda cik: (
        (_ for _ in ()).throw(PermissionError("locked"))
    )
    events = []

    async def broadcast_event(event):
        events.append(event)

    service.broadcast_event = broadcast_event

    result = asyncio.run(service.refresh_fund("0000000001"))

    assert result["status"] == "error"
    assert service.cache["0000000001"]["metadata"] == {
        "accession_number": "old"
    }
    assert service.cache["0000000001"]["status"] == "error"
    assert events[-1]["status"] == "error"


def report_candidate(accession, holdings, *, form="13F-HR", amendment_type=None):
    frame = pd.DataFrame([
        {
            "Cusip": cusip, "Ticker": cusip, "Issuer": cusip,
            "SharesPrnAmount": shares, "Value": shares * 50,
        }
        for cusip, shares in holdings
    ])
    report = SimpleNamespace(
        holdings=frame, total_holdings=len(frame),
        report_period="2026-06-30", management_company_name="Example",
    )
    metadata = {
        **filing(accession, filing_date="2026-08-14", report_period="2026-06-30", form=form),
        "amendment_type": amendment_type,
    }
    return report, "0000000001", metadata


def test_additive_amendment_preserves_base_and_aggregates_overlap():
    candidates = [
        report_candidate("1", [("A", 10), ("B", 20)]),
        report_candidate("2", [("B", 5), ("C", 15)],
                         form="13F-HR/A", amendment_type="NEW HOLDINGS"),
    ]
    report, _, metadata = DataService._assemble_period_reports(
        {"manager": "Example"}, "2026-06-30", candidates[::-1]
    )
    assert report.holdings.set_index("Cusip")["SharesPrnAmount"].to_dict() == {
        "A": 10, "B": 25, "C": 15,
    }
    assert metadata["effective_accessions"] == ["1", "2"]
    assert metadata["accession_number"] == "2"


def test_additive_amendment_preserves_security_identity_for_shared_cusip():
    base = report_candidate("1", [("A", 10)])
    addition = report_candidate(
        "2", [("A", 5)],
        form="13F-HR/A", amendment_type="NEW HOLDINGS",
    )
    for candidate, put_call in ((base, ""), (addition, "PUT")):
        candidate[0].holdings["Class"] = "COM"
        candidate[0].holdings["Type"] = "Shares"
        candidate[0].holdings["PutCall"] = put_call

    report, _, _ = DataService._assemble_period_reports(
        {"manager": "Example"}, "2026-06-30", [base, addition]
    )

    holdings = report.holdings.set_index("PutCall")
    assert holdings["SharesPrnAmount"].to_dict() == {"": 10, "PUT": 5}
    assert holdings["Value"].to_dict() == {"": 500, "PUT": 250}


def test_smaller_restatement_replaces_base_and_prior_additions():
    candidates = [
        report_candidate("1", [("A", 10), ("B", 20)]),
        report_candidate("2", [("C", 15)],
                         form="13F-HR/A", amendment_type="NEW HOLDINGS"),
        report_candidate("3", [("D", 25)],
                         form="13F-HR/A", amendment_type="RESTATEMENT"),
    ]
    report, _, metadata = DataService._assemble_period_reports(
        {"manager": "Example"}, "2026-06-30", candidates
    )
    assert report.holdings["Cusip"].tolist() == ["D"]
    assert metadata["effective_accessions"] == ["3"]


@pytest.mark.parametrize("amendment_type", ["NEW HOLDINGS", None])
def test_amendment_without_usable_base_or_type_is_not_a_complete_snapshot(amendment_type):
    with pytest.raises(ValueError):
        DataService._assemble_period_reports(
            {"manager": "Example"}, "2026-06-30",
            [report_candidate("2", [("A", 10)],
                              form="13F-HR/A", amendment_type=amendment_type)],
        )


def test_amendment_type_is_read_from_namespaced_primary_xml():
    service = DataService.__new__(DataService)
    base = report_candidate("1", [("A", 10)])
    addition = report_candidate("2", [("B", 20)], form="13F-HR/A")
    entries = [
        (SimpleNamespace(obj=lambda: base[0]), base[1], base[2]),
        (SimpleNamespace(
            obj=lambda: addition[0],
            xml=lambda: '<edgarSubmission xmlns="urn:sec"><amendmentType>'
                        'NEW HOLDINGS</amendmentType></edgarSubmission>',
        ), addition[1], addition[2]),
    ]
    report, _, _ = service._find_best_report_for_period(
        {"manager": "Example"}, "2026-06-30", entries
    )
    assert set(report.holdings["Cusip"]) == {"A", "B"}


def test_failed_amendment_does_not_silently_fall_back_to_original():
    service = DataService.__new__(DataService)
    base = report_candidate("1", [("A", 10)])

    def failed():
        raise TimeoutError("SEC timed out")

    entries = [
        (SimpleNamespace(obj=lambda: base[0]), base[1], base[2]),
        (SimpleNamespace(obj=failed), base[1], {
            **base[2], "accession_number": "2", "form": "13F-HR/A",
        }),
    ]
    with pytest.raises(RuntimeError, match="SEC timed out"):
        service._find_best_report_for_period(
            {"manager": "Example"}, "2026-06-30", entries
        )


def test_discovery_failure_remains_retryable_not_confirmed_absence():
    from prefetch import is_retryable_failure

    service = DataService.__new__(DataService)
    service._list_fund_filings = lambda fund: ([], [{"error": "HTTP 429"}])
    result = service._fetch_fund_period_sync(
        {"cik": "1", "manager": "Example"}, "2026-03-31"
    )
    assert result["status"] == "error"
    assert "HTTP 429" in result["error"]
    assert is_retryable_failure(result)
    service._list_fund_filings = lambda fund: ([], [])
    absent = service._fetch_fund_period_sync(
        {"cik": "1", "manager": "Example"}, "2026-03-31"
    )
    assert absent["status"] == "unavailable"
    assert not is_retryable_failure(absent)


def test_discovery_stops_on_throttling_and_defers_remaining_managers(monkeypatch):
    roster = [{"cik": str(i), "manager": str(i)} for i in range(1, 4)]
    calls = []

    def company(cik):
        calls.append(cik)
        if cik == "2":
            raise HTTPError("https://data.sec.gov", 429, "Rate limited", {}, None)
        return SimpleNamespace(get_filings=lambda **kwargs: [])

    monkeypatch.setattr(data_service_module, "FUND_MANAGERS", roster)
    monkeypatch.setattr(data_service_module, "Company", company)
    service = DataService.__new__(DataService)
    result = service.discover_recent_filings(pd.Timestamp("2026-01-01").date())
    assert calls == ["1", "2"]
    assert result["managers_checked"] == 2
    assert len(result["errors"]) == 2
    assert all(error["rate_limited"] for error in result["errors"])
    service._list_fund_filings(roster[0])
    assert calls == ["1", "2"]


def test_throttled_daily_run_does_not_refresh_discovered_accessions(tmp_path):
    service = FakeDataService([
        filing("1", filing_date="2026-08-14", report_period="2026-06-30")
    ])
    service.errors = [{"error": "SEC throttled", "rate_limited": True}]
    operations = FilingOperations(
        service, ledger_path=tmp_path / "ledger.sqlite3",
        publication_path=tmp_path / "publication.json",
    )
    result = asyncio.run(operations.run())
    assert result["status"] == "PARTIAL"
    assert result["failed_filings"] == 1
    assert result["refreshed_managers"] == 0
    assert service.refreshed == []


def test_partial_history_is_not_persisted_or_memoized(tmp_path, monkeypatch):
    monkeypatch.setattr(data_service_module, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data_service_module, "FUND_MANAGERS", [
        {"cik": "1", "manager": "Example"}
    ])
    service = DataService.__new__(DataService)
    service.get_available_periods = lambda: ["2026-06-30", "2026-03-31"]
    service.period_caches = {}
    service.period_cache_locks = {}
    service.period_cache_progress = {}
    service.period_cache_generations = {}
    service._fetch_fund_period_sync = lambda *args: {
        "status": "error", "error": "HTTP 429",
    }
    result = asyncio.run(service.get_period_cache("2026-03-31"))
    assert result["1"]["status"] == "error"
    assert service.period_caches == {}
    assert not (tmp_path / "history" / "2026-03-31.json").exists()
    assert service.period_cache_progress["2026-03-31"]["state"] == "partial"


def test_publication_generations_apply_only_unseen_invalidations(tmp_path):
    service = FakeDataService([])
    service.manager_adjustment_cache = {}
    service._load_all_from_disk_cache = lambda: None
    service._load_market_insights_from_disk = lambda: None
    invalidated = []
    service.invalidate_exact_filing_periods = lambda periods: invalidated.extend(periods)
    paths = {
        "ledger_path": tmp_path / "ledger.sqlite3",
        "publication_path": tmp_path / "publication.json",
    }
    writer = FilingOperations(service, **paths)
    reader = FilingOperations(service, **paths)
    writer._publish_manifest({
        "run_id": "1", "affected_ciks": ["1"], "invalidated_periods": ["2025-03-31"],
    })
    asyncio.run(reader._apply_publication(reader._read_publication()))
    writer._publish_manifest({
        "run_id": "2", "affected_ciks": [], "invalidated_periods": [],
    })
    asyncio.run(reader._apply_publication(reader._read_publication()))
    assert invalidated == ["2025-03-31"]
    writer._publish_manifest({
        "run_id": "3", "affected_ciks": ["1"], "invalidated_periods": ["2025-06-30"],
    })
    writer._publish_manifest({
        "run_id": "4", "affected_ciks": ["1"], "invalidated_periods": ["2025-09-30"],
    })
    asyncio.run(reader._apply_publication(reader._read_publication()))
    assert invalidated == ["2025-03-31", "2025-06-30", "2025-09-30"]
    assert reader._last_publication_generation == 4


def test_repaired_period_notification_keeps_new_disk_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(data_service_module, "CACHE_DIR", str(tmp_path))
    service = DataService.__new__(DataService)
    service.period_caches = {"2025-03-31": {"stale": True}}
    service.period_cache_progress = {"2025-03-31": {"state": "ready"}}
    service.period_cache_generations = {}
    service.manager_adjustment_cache = {}

    async def broadcast(event):
        pass

    service.broadcast_event = broadcast
    service._load_all_from_disk_cache = lambda: None
    service._load_market_insights_from_disk = lambda: None
    path = Path(service._get_period_cache_path("2025-03-31"))
    path.write_text('{"repaired": true}')
    operations = FilingOperations(
        service, ledger_path=tmp_path / "ledger.sqlite3",
        publication_path=tmp_path / "publication.json",
    )
    asyncio.run(operations._apply_publication({
        "run_id": "repair", "generation": 2,
        "invalidated_period_generations": {"2025-03-31": 1},
        "refreshed_period_generations": {"2025-03-31": 2},
    }))
    assert path.read_text() == '{"repaired": true}'
    assert service.period_caches == {}
    assert service.period_cache_generations["2025-03-31"] == 1


def test_legacy_publication_history_is_not_replayed_on_upgrade(tmp_path):
    service = FakeDataService([])
    paths = {
        "ledger_path": tmp_path / "ledger.sqlite3",
        "publication_path": tmp_path / "publication.json",
    }
    paths["publication_path"].write_text(json.dumps({
        "run_id": "legacy", "generation": 5,
        "cumulative_invalidated_periods": ["2025-03-31"],
        "cumulative_affected_ciks": ["1"],
    }))
    writer = FilingOperations(service, **paths)
    reader = FilingOperations(service, **paths)
    writer._publish_manifest({
        "run_id": "no-changes", "invalidated_periods": [], "affected_ciks": [],
    })
    asyncio.run(reader._apply_publication(reader._read_publication()))
    assert reader._last_publication_generation == 6
    assert service.events == []


def test_historical_publication_failure_is_not_silently_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(data_service_module, "CACHE_DIR", str(tmp_path))
    service = DataService.__new__(DataService)
    service._roster_fingerprint = lambda: "roster"

    def fail_replace(*args):
        raise PermissionError("publication locked")

    service._replace_file_with_retry = fail_replace
    with pytest.raises(PermissionError, match="publication locked"):
        service._save_period_cache_to_disk(
            "2026-03-31", {"1": {"status": "loaded"}}
        )
    assert not list((tmp_path / "history").glob("*.tmp"))
