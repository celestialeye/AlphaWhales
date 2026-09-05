from __future__ import annotations

import io
import gzip
import hashlib
import json
import tempfile
import unittest
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from unittest import mock

import pandas as pd
import duckdb

from config import FUND_MANAGERS
from investor_screening.database import connect_database
from investor_screening.detail_ingest import (
    pending_accessions,
    persist_filing_details,
    persist_npx_details,
)
from investor_screening.flattened_bulk import (
    bulk_table_sql,
    discover_bulk_datasets,
    import_flattened_archive,
)
from investor_screening import flattened_bulk
from investor_screening import screener
from investor_screening.forms import family_for_form, forms_for_family
from investor_screening.quality import validate_database
from investor_screening.screener import SNAPSHOT_SCHEMA, ScreeningService
from investor_screening.sec_bulk import import_archive


HEADERS = {
    "SUBMISSION.tsv": "ACCESSION_NUMBER\tFILING_DATE\tSUBMISSIONTYPE\tCIK\tPERIODOFREPORT\n",
    "COVERPAGE.tsv": (
        "ACCESSION_NUMBER\tREPORTCALENDARORQUARTER\tISAMENDMENT\tAMENDMENTNO\t"
        "AMENDMENTTYPE\tCONFDENIEDEXPIRED\tDATEDENIEDEXPIRED\tDATEREPORTED\t"
        "REASONFORNONCONFIDENTIALITY\tFILINGMANAGER_NAME\tFILINGMANAGER_STREET1\t"
        "FILINGMANAGER_STREET2\tFILINGMANAGER_CITY\tFILINGMANAGER_STATEORCOUNTRY\t"
        "FILINGMANAGER_ZIPCODE\tREPORTTYPE\tFORM13FFILENUMBER\tCRDNUMBER\t"
        "SECFILENUMBER\tPROVIDEINFOFORINSTRUCTION5\tADDITIONALINFORMATION\n"
    ),
    "SUMMARYPAGE.tsv": (
        "ACCESSION_NUMBER\tOTHERINCLUDEDMANAGERSCOUNT\tTABLEENTRYTOTAL\t"
        "TABLEVALUETOTAL\tISCONFIDENTIALOMITTED\n"
    ),
    "SIGNATURE.tsv": (
        "ACCESSION_NUMBER\tNAME\tTITLE\tPHONE\tSIGNATURE\tCITY\t"
        "STATEORCOUNTRY\tSIGNATUREDATE\n"
    ),
    "OTHERMANAGER.tsv": (
        "ACCESSION_NUMBER\tOTHERMANAGER_SK\tCIK\tFORM13FFILENUMBER\t"
        "CRDNUMBER\tSECFILENUMBER\tNAME\n"
    ),
    "OTHERMANAGER2.tsv": (
        "ACCESSION_NUMBER\tSEQUENCENUMBER\tCIK\tFORM13FFILENUMBER\t"
        "CRDNUMBER\tSECFILENUMBER\tNAME\n"
    ),
    "INFOTABLE.tsv": (
        "ACCESSION_NUMBER\tINFOTABLE_SK\tNAMEOFISSUER\tTITLEOFCLASS\tCUSIP\t"
        "FIGI\tVALUE\tSSHPRNAMT\tSSHPRNAMTTYPE\tPUTCALL\tINVESTMENTDISCRETION\t"
        "OTHERMANAGER\tVOTING_AUTH_SOLE\tVOTING_AUTH_SHARED\tVOTING_AUTH_NONE\n"
    ),
}


def create_archive(path: Path, filings: list[dict], prefix: str = "") -> None:
    rows = {name: [header] for name, header in HEADERS.items()}
    for filing in filings:
        accession = filing["accession"]
        rows["SUBMISSION.tsv"].append(
            f"{accession}\t{filing['filing_date']}\t{filing['form']}\t"
            f"{filing['cik']}\t{filing['period']}\n"
        )
        rows["COVERPAGE.tsv"].append(
            f"{accession}\t{filing['period']}\t{filing.get('is_amendment', 'N')}\t"
            f"{filing.get('amendment_number', '')}\t{filing.get('amendment_type', '')}\t"
            f"N\t\t\t\tTest Manager\t1 Main St\t\tNew York\tNY\t10001\t"
            f"13F HOLDINGS REPORT\t028-00001\t\t\tN\t\n"
        )
        total = sum(item["value"] for item in filing["holdings"])
        rows["SUMMARYPAGE.tsv"].append(
            f"{accession}\t0\t{len(filing['holdings'])}\t{total}\tN\n"
        )
        rows["SIGNATURE.tsv"].append(
            f"{accession}\tSigner\tManager\t555-0100\tSigner\tNew York\tNY\t"
            f"{filing['filing_date']}\n"
        )
        for index, holding in enumerate(filing["holdings"], start=1):
            holding_accession = holding.get("accession", accession)
            rows["INFOTABLE.tsv"].append(
                f"{holding_accession}\t{index}\t{holding['issuer']}\tCOM\t{holding['cusip']}\t\t"
                f"{holding['value']}\t{holding['shares']}\tSH\t\tSOLE\t\t"
                f"{holding['shares']}\t0\t0\n"
            )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in rows.items():
            archive.writestr(f"{prefix}{name}", "".join(content))


def test_bulk_snapshot_publication_preserves_typed_rows(tmp_path, monkeypatch):
    source_path = tmp_path / "source.duckdb"
    archive = tmp_path / "fixture.zip"
    create_archive(archive, [
        {
            "accession": f"0000000001-26-00000{index}",
            "filing_date": filed, "form": "13F-HR", "cik": "1",
            "period": period,
            "holdings": [{
                "issuer": "Example Corporation", "cusip": "123456789",
                "value": value, "shares": 100,
            }],
        }
        for index, (period, filed, value) in enumerate([
            (
                quarter.end_time.date().isoformat(),
                (quarter.end_time + pd.Timedelta(days=45)).date().isoformat(),
                8000,
            )
            for quarter in pd.period_range("2023Q2", "2025Q3", freq="Q")
        ] + [
            ("2025-12-31", "2026-02-13", 10000),
            ("2026-03-31", "2026-05-15", 12000),
        ], start=1)
    ])
    source = connect_database(source_path)
    import_archive(source, archive)
    source.close()
    monkeypatch.setattr(screener, "FUND_MANAGERS", [
        {"cik": "0000000001", "manager": "Example Manager"},
    ])
    monkeypatch.setattr(screener, "ROSTER_PATH", tmp_path / "roster.json")
    pointer = tmp_path / "snapshot.json"
    result = screener.build_screening_snapshot(
        source_path, pointer, tmp_path / "absent-performance.duckdb"
    )
    assert result["manager_count"] == 1
    assert result["position_quarter_count"] == 12
    snapshot = duckdb.connect(str(screener.resolve_snapshot_path(pointer)), read_only=True)
    try:
        assert snapshot.execute(
            """
            SELECT cik, report_period, reported_value, weight_nonoption_pct
            FROM manager_position_quarters
            WHERE report_period >= DATE '2025-12-31'
            ORDER BY report_period
            """
        ).fetchall() == [
            ("0000000001", date(2025, 12, 31), 10000, 100),
            ("0000000001", date(2026, 3, 31), 12000, 100),
        ]
        assert snapshot.execute(
            "SELECT is_current_roster FROM manager_metrics"
        ).fetchone()[0] is True
    finally:
        snapshot.close()


def create_flattened_archive(
    path: Path,
    *,
    extra_column: bool = False,
    value: str = "first",
) -> None:
    columns = "ACCESSION_NUMBER\tOWNER_CIK\tNOTE"
    row = f"0000000001-24-000001\t0000000042\t{value}"
    if extra_column:
        columns += "\tUNANNOUNCED_SEC_COLUMN"
        row += "\tretained"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "wrapper/deep/OWNER.tsv",
            f"{columns}\r\n{row}\r\n",
        )
        archive.writestr(
            "wrapper/deep/NONDERIV_TRANS.tsv",
            "ACCESSION_NUMBER\tSECURITY_TITLE\tSHARES\n"
            "0000000001-24-000001\tCOMMON STOCK\t100.00\n",
        )
        archive.writestr(
            "wrapper/README.txt",
            b"Official flattened fixture metadata.\r\n",
        )


class InvestorScreeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.connection = connect_database(self.root / "test.duckdb")

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def test_import_normalizes_pre_2023_values_and_is_idempotent(self):
        archive = self.root / "2022q1_form13f.zip"
        create_archive(
            archive,
            [{
                "accession": "0000000001-22-000001",
                "filing_date": "14-FEB-2022",
                "form": "13F-HR",
                "cik": "1",
                "period": "31-DEC-2021",
                "holdings": [{"issuer": "ALPHA", "cusip": "000000001", "value": 125, "shares": 10}],
            }],
            prefix="01JAN2022-31MAR2022_form13f/",
        )
        import_archive(self.connection, archive)
        import_archive(self.connection, archive)
        row = self.connection.execute(
            "SELECT value_reported, value_unit, value_usd FROM holdings"
        ).fetchone()
        self.assertEqual(row, (125, "THOUSANDS_USD", 125000))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM holdings").fetchone()[0], 1)
        catalog = self.connection.execute(
            "SELECT filing_family, source_kind FROM sec_filings"
        ).fetchone()
        self.assertEqual(catalog, ("institutional_holdings", "SEC_13F_BULK"))
        self.assertEqual(validate_database(self.connection)["errors"], 0)

    def test_restatement_and_new_holdings_amendment_are_applied(self):
        archive = self.root / "2023q4_form13f.zip"
        create_archive(
            archive,
            [
                {
                    "accession": "0000000001-23-000001",
                    "filing_date": "01-NOV-2023",
                    "form": "13F-HR",
                    "cik": "1",
                    "period": "30-SEP-2023",
                    "holdings": [{"issuer": "OLD", "cusip": "000000001", "value": 100, "shares": 10}],
                },
                {
                    "accession": "0000000001-23-000002",
                    "filing_date": "02-NOV-2023",
                    "form": "13F-HR/A",
                    "cik": "1",
                    "period": "30-SEP-2023",
                    "is_amendment": "Y",
                    "amendment_number": "1",
                    "amendment_type": "RESTATEMENT",
                    "holdings": [{"issuer": "BASE", "cusip": "000000002", "value": 200, "shares": 20}],
                },
                {
                    "accession": "0000000001-23-000003",
                    "filing_date": "03-NOV-2023",
                    "form": "13F-HR/A",
                    "cik": "1",
                    "period": "30-SEP-2023",
                    "is_amendment": "Y",
                    "amendment_number": "2",
                    "amendment_type": "NEW HOLDINGS",
                    "holdings": [{"issuer": "ADDED", "cusip": "000000003", "value": 50, "shares": 5}],
                },
            ],
        )
        import_archive(self.connection, archive)
        rows = self.connection.execute(
            "SELECT name_of_issuer, value_usd FROM v_effective_holdings ORDER BY name_of_issuer"
        ).fetchall()
        self.assertEqual(rows, [("ADDED", 50), ("BASE", 200)])

    def test_failed_source_reconciliation_rolls_back_archive_rows(self):
        archive = self.root / "2024q1_form13f.zip"
        create_archive(
            archive,
            [{
                "accession": "0000000001-24-000001",
                "filing_date": "14-FEB-2024",
                "form": "13F-HR",
                "cik": "1",
                "period": "31-DEC-2023",
                "holdings": [{
                    "accession": "UNKNOWN-ACCESSION",
                    "issuer": "ALPHA",
                    "cusip": "000000001",
                    "value": 125,
                    "shares": 10,
                }],
            }],
        )
        with self.assertRaisesRegex(ValueError, "Holdings count mismatch"):
            import_archive(self.connection, archive)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM submissions").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM holdings").fetchone()[0], 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT status FROM datasets WHERE dataset_id = '2024q1_form13f.zip'"
            ).fetchone()[0],
            "FAILED",
        )

    def test_flattened_bulk_preserves_nested_tsv_schema_and_metadata(self):
        archive = self.root / "2024q1_form345.zip"
        create_flattened_archive(archive, extra_column=True)
        lake = self.root / "lake"

        result = import_flattened_archive(
            self.connection,
            archive,
            "insider",
            lake_dir=lake,
        )

        self.assertEqual(result["source_row_count"], result["parquet_row_count"])
        self.assertFalse(result["archive_deleted"])
        self.assertTrue(archive.exists())
        owner_path = (
            lake / "insider" / "owner" / "2024q1_form345.zip.parquet"
        )
        self.assertTrue(owner_path.is_file())
        columns = self.connection.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{owner_path.as_posix()}')"
        ).fetchall()
        self.assertEqual(
            [row[0] for row in columns],
            [
                "ACCESSION_NUMBER",
                "OWNER_CIK",
                "NOTE",
                "UNANNOUNCED_SEC_COLUMN",
                "source_dataset_id",
                "source_archive_member",
                "source_row_number",
            ],
        )
        values = self.connection.execute(
            f"""
            SELECT OWNER_CIK, UNANNOUNCED_SEC_COLUMN, source_row_number
            FROM read_parquet('{owner_path.as_posix()}')
            """
        ).fetchone()
        self.assertEqual(values, ("0000000042", "retained", 1))
        parquet_hash = self.connection.execute(
            """
            SELECT parquet_sha256, parquet_bytes
            FROM bulk_dataset_files
            WHERE family = 'insider'
              AND dataset_id = '2024q1_form345.zip'
              AND table_name = 'owner'
            """
        ).fetchone()
        self.assertEqual(parquet_hash[0], hashlib.sha256(owner_path.read_bytes()).hexdigest())
        self.assertEqual(parquet_hash[1], owner_path.stat().st_size)
        metadata = self.connection.execute(
            """
            SELECT output_path
            FROM bulk_dataset_metadata
            WHERE family = 'insider'
              AND dataset_id = '2024q1_form345.zip'
            """
        ).fetchone()
        self.assertTrue(Path(metadata[0]).read_bytes().startswith(b"Official"))

    def test_flattened_bulk_official_page_discovery_is_offline_testable(self):
        page = b"""
        <a href="/files/dera/data/form-n-port/2019q3_nport.zip">old</a>
        <a href='https://www.sec.gov/files/01jan2024-31mar2024_nport.zip?x=1'>new</a>
        """
        with mock.patch(
            "investor_screening.flattened_bulk.urllib.request.urlopen",
            return_value=io.BytesIO(page),
        ):
            datasets = discover_bulk_datasets("n-port", "Tester test@example.com")
        self.assertEqual(
            [item.dataset_id for item in datasets],
            ["2019q3_nport.zip", "01jan2024-31mar2024_nport.zip"],
        )
        self.assertEqual(datasets[0].family, "nport")
        self.assertEqual(datasets[0].period_start, date(2019, 7, 1))

    def test_fundamental_bulk_discovery_accepts_bare_quarter_archives(self):
        page = b"""
        <a href="/files/dera/data/financial-statement-data-sets/2009q1.zip">old</a>
        <a href="/files/dera/data/financial-statement-data-sets/2026q2.zip">new</a>
        """
        with mock.patch(
            "investor_screening.flattened_bulk.urllib.request.urlopen",
            return_value=io.BytesIO(page),
        ):
            datasets = discover_bulk_datasets(
                "xbrl",
                "Tester test@example.com",
            )

        self.assertEqual(
            [item.dataset_id for item in datasets],
            ["2009q1.zip", "2026q2.zip"],
        )
        self.assertEqual(datasets[0].family, "fundamentals")
        self.assertEqual(datasets[0].period_start, date(2009, 1, 1))

    def test_fundamental_bulk_import_accepts_sec_txt_tables(self):
        archive = self.root / "2024q1.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr(
                "sub.txt",
                "adsh\tcik\tname\tsic\tcountryinc\tcountryba\t"
                "form\tperiod\tfy\tfp\tfiled\t"
                "accepted\tinstance\n"
                "0000000000-24-000001\t42\tTEST INC\t3571\tUS\tUS\t10-K\t"
                "20231231\t2023\tFY\t20240201\t"
                "2024-02-01 12:00:00.000\t"
                "test-20231231.htm\n",
            )
            output.writestr(
                "num.txt",
                "adsh\ttag\tversion\tddate\tqtrs\tuom\tsegments\tcoreg\t"
                "value\tfootnote\n"
                "0000000000-24-000001\tAssets\tus-gaap/2023\t"
                "20231231\t0\tUSD\t\t\t100\t\n",
            )

        result = import_flattened_archive(
            self.connection,
            archive,
            "fundamentals",
            lake_dir=self.root / "lake",
        )
        views = flattened_bulk.refresh_bulk_views(self.connection)

        self.assertEqual(result["table_count"], 2)
        self.assertIn("silver_xbrl_submissions", views)
        self.assertIn("silver_xbrl_facts", views)
        self.assertEqual(
            self.connection.execute(
                "SELECT issuer_cik FROM silver_xbrl_submissions"
            ).fetchone()[0],
            "0000000042",
        )
        self.assertIsNotNone(
            self.connection.execute(
                "SELECT accepted_at FROM silver_xbrl_submissions"
            ).fetchone()[0]
        )

    def test_flattened_bulk_is_idempotent_and_union_by_name_queryable(self):
        lake = self.root / "lake"
        first = self.root / "2024q1_form345.zip"
        second = self.root / "2024q2_form345.zip"
        create_flattened_archive(first)
        create_flattened_archive(second, extra_column=True, value="second")

        import_flattened_archive(
            self.connection,
            first,
            "insider",
            lake_dir=lake,
            delete_archive=False,
        )
        skipped = import_flattened_archive(
            self.connection,
            first,
            "insider",
            lake_dir=lake,
            delete_archive=False,
        )
        import_flattened_archive(
            self.connection,
            second,
            "insider",
            lake_dir=lake,
            delete_archive=False,
        )

        self.assertEqual(skipped["status"], "skipped")
        self.assertEqual(
            self.connection.execute(
                """
                SELECT count(*)
                FROM bulk_dataset_files
                WHERE family = 'insider' AND table_name = 'owner'
                """
            ).fetchone()[0],
            2,
        )
        union_rows = self.connection.execute(
            "SELECT NOTE, UNANNOUNCED_SEC_COLUMN FROM ("
            + bulk_table_sql(self.connection, "insider", "OWNER.tsv")
            + ") rows ORDER BY source_dataset_id"
        ).fetchall()
        self.assertEqual(len(union_rows), 2)
        self.assertIsNone(union_rows[0][1])
        self.assertEqual(union_rows[1][1], "retained")

        create_flattened_archive(first, value="changed-content")
        with self.assertRaisesRegex(ValueError, "changed source content"):
            import_flattened_archive(
                self.connection,
                first,
                "insider",
                lake_dir=lake,
                delete_archive=False,
            )
        self.assertEqual(
            self.connection.execute(
                """
                SELECT status
                FROM bulk_datasets
                WHERE family = 'insider'
                  AND dataset_id = '2024q1_form345.zip'
                """
            ).fetchone()[0],
            "IMPORTED",
        )

    def test_flattened_bulk_reconciliation_failure_rolls_back_manifest_and_files(self):
        archive = self.root / "2024q1_nport.zip"
        create_flattened_archive(archive)
        lake = self.root / "lake"
        writer = flattened_bulk._write_and_validate_parquet

        def mismatched_count(*args, **kwargs):
            source_count, parquet_count, schema_json, parquet_hash, parquet_bytes = writer(
                *args,
                **kwargs,
            )
            return (
                source_count,
                parquet_count + 1,
                schema_json,
                parquet_hash,
                parquet_bytes,
            )

        with mock.patch.object(
            flattened_bulk,
            "_write_and_validate_parquet",
            side_effect=mismatched_count,
        ):
            with self.assertRaisesRegex(ValueError, "Dataset row count mismatch"):
                import_flattened_archive(
                    self.connection,
                    archive,
                    "nport",
                    lake_dir=lake,
                    delete_archive=False,
                )

        self.assertEqual(
            self.connection.execute(
                """
                SELECT status
                FROM bulk_datasets
                WHERE family = 'nport' AND dataset_id = '2024q1_nport.zip'
                """
            ).fetchone()[0],
            "FAILED",
        )
        self.assertEqual(
            self.connection.execute(
                """
                SELECT count(*)
                FROM bulk_dataset_files
                WHERE family = 'nport' AND dataset_id = '2024q1_nport.zip'
                """
            ).fetchone()[0],
            0,
        )
        self.assertFalse(
            (lake / "nport" / "owner" / "2024q1_nport.zip.parquet").exists()
        )
        self.assertTrue(archive.exists())

    def test_flattened_bulk_failed_repair_restores_prior_generation(self):
        archive = self.root / "2024q1_form345.zip"
        create_flattened_archive(archive)
        lake = self.root / "lake"
        import_flattened_archive(
            self.connection,
            archive,
            "insider",
            lake_dir=lake,
        )
        original_files = {
            Path(path): Path(path).read_bytes()
            for (path,) in self.connection.execute(
                """
                SELECT output_path
                FROM bulk_dataset_files
                WHERE family = 'insider'
                  AND dataset_id = '2024q1_form345.zip'
                ORDER BY table_name
                """
            ).fetchall()
        }
        original_replace = Path.replace
        moved_parquet = 0

        def fail_second_staged_parquet(source, target):
            nonlocal moved_parquet
            source_path = Path(source)
            if (
                ".staging" in source_path.parts
                and "parquet" in source_path.parts
            ):
                moved_parquet += 1
                if moved_parquet == 2:
                    raise OSError("simulated repair move failure")
            return original_replace(source, target)

        with (
            mock.patch.object(
                flattened_bulk,
                "_import_is_intact",
                return_value=False,
            ),
            mock.patch.object(
                Path,
                "replace",
                fail_second_staged_parquet,
            ),
        ):
            with self.assertRaisesRegex(
                OSError,
                "simulated repair move failure",
            ):
                import_flattened_archive(
                    self.connection,
                    archive,
                    "insider",
                    lake_dir=lake,
                )

        self.assertEqual(
            self.connection.execute(
                """
                SELECT status
                FROM bulk_datasets
                WHERE family = 'insider'
                  AND dataset_id = '2024q1_form345.zip'
                """
            ).fetchone()[0],
            "IMPORTED",
        )
        for path, content in original_files.items():
            self.assertEqual(path.read_bytes(), content)

    def test_flattened_bulk_deletes_archive_only_after_success(self):
        archive = self.root / "2024q1_nmfp.zip"
        create_flattened_archive(archive)
        result = import_flattened_archive(
            self.connection,
            archive,
            "nmfp",
            lake_dir=self.root / "lake",
            delete_archive=True,
        )
        self.assertTrue(result["archive_deleted"])
        self.assertFalse(archive.exists())
        manifest = self.connection.execute(
            """
            SELECT source_sha256, source_row_count, parquet_row_count,
                   archive_deleted, status
            FROM bulk_datasets
            WHERE family = 'nmfp' AND dataset_id = '2024q1_nmfp.zip'
            """
        ).fetchone()
        self.assertTrue(manifest[0])
        self.assertEqual(manifest[1], manifest[2])
        self.assertEqual(manifest[3:], (True, "IMPORTED"))

    def test_relevant_form_families_are_explicit(self):
        self.assertEqual(family_for_form("4"), "insider_ownership")
        self.assertEqual(family_for_form("SCHEDULE 13D/A"), "beneficial_ownership")
        self.assertIn("NPORT-P", forms_for_family("registered_fund_portfolios"))
        self.assertEqual(family_for_form("N-PX"), "proxy_voting")
        self.assertEqual(family_for_form("144"), "planned_insider_sales")
        self.assertEqual(family_for_form("N-Q"), "registered_fund_portfolios")
        self.assertEqual(family_for_form("N-CR"), "money_market_funds")

    def test_detail_ingestion_preserves_raw_object_and_tables(self):
        @dataclass
        class FakeOwner:
            name: str
            is_director: bool

        class FakeForm:
            def __init__(self):
                self.owner = FakeOwner("Test Owner", True)
                self.remarks = "Open-market purchase"

            def to_dataframe(self):
                return pd.DataFrame(
                    [{"Security": "COMMON STOCK", "Shares": 100, "Code": "P"}]
                )

        self.connection.execute(
            """
            INSERT INTO sec_filings (
                accession_number, filing_family, form, cik, company_name,
                filing_date, period_of_report, source_url, source_kind
            )
            VALUES (
                '0000000001-25-000001', 'insider_ownership', '4',
                '0000000001', 'TEST', DATE '2025-01-02', DATE '2025-01-01',
                'https://www.sec.gov/example', 'TEST'
            )
            """
        )
        result = persist_filing_details(
            self.connection,
            accession_number="0000000001-25-000001",
            form="4",
            filing_date=date(2025, 1, 2),
            raw_submission="<SEC-DOCUMENT>test</SEC-DOCUMENT>",
            parsed_object=FakeForm(),
            raw_dir=self.root / "raw",
        )
        self.assertEqual(result["tables"], {"to_dataframe": 1})
        artifact = self.connection.execute(
            """
            SELECT status, raw_submission_sha256, object_type, extractor_manifest
            FROM filing_artifacts
            """
        ).fetchone()
        self.assertEqual(artifact[0], "INGESTED")
        self.assertTrue(artifact[1])
        self.assertIn("FakeForm", artifact[2])
        self.assertEqual(json.loads(str(artifact[3])), {"to_dataframe": 1})
        row = self.connection.execute(
            "SELECT table_name, row_json FROM filing_table_rows"
        ).fetchone()
        self.assertEqual(row[0], "to_dataframe")
        self.assertIn('"Code":"P"', str(row[1]))

        self.connection.execute(
            """
            UPDATE filing_artifacts
            SET status = 'INGESTED_PARTIAL'
            WHERE accession_number = '0000000001-25-000001'
            """
        )
        self.assertEqual(pending_accessions(self.connection), [])
        self.assertEqual(
            pending_accessions(self.connection, retry_incomplete=True),
            ["0000000001-25-000001"],
        )

    def test_raw_submission_preserves_exact_bytes(self):
        raw = b"<SEC-DOCUMENT>legacy-\x96-byte</SEC-DOCUMENT>"
        persist_filing_details(
            self.connection,
            accession_number="0000000001-25-000002",
            form="4",
            filing_date=date(2025, 1, 2),
            raw_submission=raw,
            parsed_object={"form": "4"},
            raw_dir=self.root / "raw",
            extracted_tables={},
        )
        path, stored_hash, stored_bytes = self.connection.execute(
            """
            SELECT raw_submission_path, raw_submission_sha256, raw_submission_bytes
            FROM filing_artifacts
            WHERE accession_number = '0000000001-25-000002'
            """
        ).fetchone()
        self.assertEqual(gzip.open(path, "rb").read(), raw)
        self.assertEqual(stored_hash, hashlib.sha256(raw).hexdigest())
        self.assertEqual(stored_bytes, len(raw))

    def test_npx_lossless_xml_preserves_malformed_vote_values(self):
        class FakeAttachment:
            def __init__(self, document_type, content):
                self.document_type = document_type
                self.content = content
                self.is_xml = True

        class FakeFiling:
            attachments = [
                FakeAttachment(
                    "N-PX",
                    """<?xml version="1.0"?>
                    <edgarSubmission xmlns="http://www.sec.gov/edgar/npx">
                      <headerData><submissionType>N-PX</submissionType></headerData>
                    </edgarSubmission>""",
                ),
                FakeAttachment(
                    "PROXY VOTING RECORD",
                    """<?xml version="1.0"?>
                    <proxyVoteTable xmlns="http://www.sec.gov/edgar/document/npxproxy/informationtable">
                      <proxyTable><issuerName>Alpha\u0001Fund</issuerName><sharesVoted>N/A</sharesVoted></proxyTable>
                      <proxyTable><issuerName>Beta</issuerName><sharesVoted></sharesVoted></proxyTable>
                    </proxyVoteTable>""",
                ),
            ]

        raw = b"<SEC-DOCUMENT>npx</SEC-DOCUMENT>"
        with mock.patch(
            "investor_screening.detail_ingest.Filing.from_sgml_text",
            return_value=FakeFiling(),
        ):
            result = persist_npx_details(
                self.connection,
                accession_number="0000000001-25-000003",
                form="N-PX",
                filing_date=date(2025, 1, 2),
                raw_submission=raw,
                raw_dir=self.root / "raw",
            )
        self.assertEqual(
            result["tables"],
            {"npx_filing": 1, "proxy_votes_source": 2},
        )
        self.assertEqual(result["status"], "INGESTED_XML_FALLBACK")
        self.assertEqual(result["extraction_errors"], [])

    def test_npx_failed_reingestion_preserves_existing_rows(self):
        self.connection.execute(
            """
            INSERT INTO filing_table_rows
            VALUES (
                '0000000001-25-000004',
                'npx_filing',
                0,
                'hash',
                '{"existing":true}'
            )
            """
        )

        class MissingPrimaryFiling:
            attachments = []

        with mock.patch(
            "investor_screening.detail_ingest.Filing.from_sgml_text",
            return_value=MissingPrimaryFiling(),
        ):
            with self.assertRaisesRegex(ValueError, "primary XML"):
                persist_npx_details(
                    self.connection,
                    accession_number="0000000001-25-000004",
                    form="N-PX",
                    filing_date=date(2025, 1, 2),
                    raw_submission=b"<SEC-DOCUMENT>npx</SEC-DOCUMENT>",
                    raw_dir=self.root / "raw",
                )
        self.assertEqual(
            self.connection.execute(
                """
                SELECT row_json
                FROM filing_table_rows
                WHERE accession_number = '0000000001-25-000004'
                """
            ).fetchone()[0],
            '{"existing":true}',
        )

    def test_screening_persistence_uses_selected_top10_threshold(self):
        generation = self.root / "screening.generation.duckdb"
        snapshot = duckdb.connect(str(generation))
        snapshot.execute(SNAPSHOT_SCHEMA)
        snapshot.execute(
            """
            INSERT INTO snapshot_metadata
            VALUES (
                DATE '2025-12-31',
                now(),
                'screening-v1',
                'test',
                'test',
                'fingerprint'
            )
            """
        )
        snapshot.execute(
            """
            INSERT INTO manager_metrics
            VALUES (
                '0000000001', 'Test Manager', DATE '2025-12-31', 12,
                20000000000, 20000000000, 20000000000, 7, 100,
                45, 15, 8, 20, 1, NULL, false
            )
            """
        )
        snapshot.executemany(
            "INSERT INTO manager_quarter_concentration VALUES (?, ?, ?)",
            [
                ("0000000001", 1, 45),
                ("0000000001", 2, 44),
                ("0000000001", 3, 43),
                ("0000000001", 4, 42),
                ("0000000001", 5, 39),
                ("0000000001", 6, 38),
                ("0000000001", 7, 37),
                ("0000000001", 8, 36),
            ],
        )
        snapshot.executemany(
            "INSERT INTO manager_position_quarters VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "0000000001",
                    "stock-a",
                    quarter,
                    date(2025, 12, 31),
                    "000000001",
                    "AAA",
                    "Stock A",
                    "COM",
                    weight * 200000000,
                    weight,
                    weight,
                    1,
                )
                for quarter, weight in enumerate(
                    [5.0, 4.5, 4.0, 3.5, 3.0],
                    start=1,
                )
            ]
            + [
                (
                    "0000000001",
                    "stock-b",
                    quarter,
                    date(2025, 12, 31),
                    "000000002",
                    "BBB",
                    "Stock B",
                    "COM",
                    700000000,
                    3.5,
                    3.5,
                    2,
                )
                for quarter in range(1, 4)
            ],
        )
        snapshot.close()
        pointer = self.root / "screening_snapshot.json"
        pointer.write_text(
            json.dumps({"generation": generation.name}),
            encoding="utf-8",
        )
        service = ScreeningService(pointer)
        self.assertEqual(
            service.get_screening_results(
                minimum_top10_pct=40,
                minimum_concentration_quarters=4,
            )["summary"]["candidate_count"],
            1,
        )
        self.assertEqual(
            service.get_screening_results(
                minimum_top10_pct=50,
                minimum_concentration_quarters=1,
            )["summary"]["candidate_count"],
            0,
        )
        self.assertEqual(
            service.get_screening_results(
                minimum_stock_count=7,
                minimum_concentration_quarters=1,
            )["summary"]["candidate_count"],
            1,
        )
        self.assertEqual(
            service.get_screening_results(
                minimum_stock_count=8,
                minimum_concentration_quarters=1,
            )["summary"]["candidate_count"],
            0,
        )
        six_months = service.get_screening_results(
            minimum_concentration_quarters=1,
            best_bet_duration_months=6,
            minimum_best_bet_count=2,
        )
        self.assertEqual(six_months["summary"]["candidate_count"], 1)
        self.assertEqual(
            six_months["data"][0]["persistent_best_bet_count"],
            2,
        )
        self.assertEqual(
            len(six_months["data"][0]["persistent_best_bets"]),
            2,
        )
        self.assertEqual(
            service.get_screening_results(
                minimum_concentration_quarters=1,
                best_bet_duration_months=12,
                minimum_best_bet_count=2,
            )["summary"]["candidate_count"],
            0,
        )
        self.assertEqual(
            service.get_screening_results(
                minimum_concentration_quarters=1,
                minimum_best_bet_weight_pct=4,
            )["summary"]["candidate_count"],
            0,
        )
        detail = service.get_investor_detail("0000000001")
        self.assertIsNotNone(detail)
        self.assertTrue(detail["screening_snapshot_only"])
        self.assertEqual(detail["fund_info"]["manager"], "Test Manager")
        self.assertEqual(detail["holdings_list"][0]["ticker"], "AAA")
        history = service.get_investor_history("0000000001")
        self.assertIsNotNone(history)
        self.assertTrue(history["screening_snapshot_only"])
        self.assertEqual(len(history["portfolio_history"]), 1)

        original_roster = list(FUND_MANAGERS)
        try:
            FUND_MANAGERS[:] = [{
                "group": "Quality Growth",
                "cik": "0000000001",
                "name": "Test Manager",
                "manager": "Test Manager",
                "annotation": "Test",
                "is_exception": True,
                "roster_reason": "Test exception",
            }]
            roster_result = service.get_screening_results(
                minimum_concentration_quarters=1,
                roster_only=True,
            )
            self.assertEqual(roster_result["summary"]["candidate_count"], 1)
            self.assertEqual(
                roster_result["metadata"]["configured_roster_count"],
                1,
            )
            self.assertTrue(roster_result["data"][0]["is_current_roster"])
            self.assertTrue(roster_result["data"][0]["roster_is_exception"])
        finally:
            FUND_MANAGERS[:] = original_roster


if __name__ == "__main__":
    unittest.main()
