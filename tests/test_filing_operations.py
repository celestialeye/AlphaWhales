from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd

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
