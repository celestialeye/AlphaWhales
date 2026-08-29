from __future__ import annotations

import csv
import tempfile
from collections.abc import Iterable
from pathlib import Path

import duckdb
from edgar import get_filings, set_identity

from .forms import family_for_form, forms_for_family


def filing_source_url(cik: str | int, accession_number: str) -> str:
    normalized_cik = str(int(cik))
    accession_path = accession_number.replace("-", "")
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{normalized_cik}/{accession_path}/{accession_number}.txt"
    )


def catalog_quarter(
    connection: duckdb.DuckDBPyConnection,
    *,
    year: int,
    quarter: int,
    family: str,
    identity: str,
    limit: int | None = None,
) -> dict:
    forms = forms_for_family(family)
    set_identity(identity)
    filings = get_filings(year=year, quarter=quarter, form=list(forms))
    if filings is None:
        return {"year": year, "quarter": quarter, "family": family, "filings": 0}

    rows_by_accession = {}
    for filing in filings.data.to_pylist():
        accession_number = str(filing["accession_number"])
        form = str(filing["form"])
        cik = str(int(filing["cik"])).zfill(10)
        rows_by_accession[accession_number] = [
                accession_number,
                family_for_form(form),
                form,
                cik,
                str(filing.get("company") or ""),
                filing["filing_date"],
                None,
                filing_source_url(cik, accession_number),
                "EDGARTOOLS_INDEX",
                None,
            ]
        if limit is not None and len(rows_by_accession) >= limit:
            break
    rows = list(rows_by_accession.values())

    if rows:
        staging_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                suffix=".tsv",
                delete=False,
            ) as staging:
                staging_path = Path(staging.name)
                writer = csv.writer(staging, delimiter="\t", lineterminator="\n")
                writer.writerow(
                    [
                        "accession_number",
                        "filing_family",
                        "form",
                        "cik",
                        "company_name",
                        "filing_date",
                        "period_of_report",
                        "source_url",
                        "source_kind",
                        "dataset_id",
                    ]
                )
                writer.writerows(rows)

            sql_path = str(staging_path).replace("'", "''")
            connection.execute(
                f"""
                CREATE OR REPLACE TEMP TABLE catalog_rows AS
                SELECT *
                FROM read_csv(
                    '{sql_path}',
                    delim = '\\t',
                    header = true,
                    all_varchar = true,
                    null_padding = false,
                    ignore_errors = false
                )
                """
            )
            connection.execute("BEGIN TRANSACTION")
            connection.execute(
                """
                INSERT INTO sec_filings (
                    accession_number, filing_family, form, cik, company_name,
                    filing_date, period_of_report, source_url, source_kind, dataset_id
                )
                SELECT
                    accession_number,
                    filing_family,
                    form,
                    cik,
                    company_name,
                    try_cast(filing_date AS DATE),
                    try_cast(period_of_report AS DATE),
                    source_url,
                    source_kind,
                    dataset_id
                FROM catalog_rows
                ON CONFLICT (accession_number) DO NOTHING
                """
            )
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except duckdb.TransactionException:
                pass
            raise
        finally:
            if staging_path is not None:
                staging_path.unlink(missing_ok=True)
    return {
        "year": year,
        "quarter": quarter,
        "family": family,
        "filings": len(rows),
    }


def catalog_period(
    connection: duckdb.DuckDBPyConnection,
    *,
    years: Iterable[int],
    quarters: Iterable[int],
    family: str,
    identity: str,
    limit_per_quarter: int | None = None,
) -> list[dict]:
    return [
        catalog_quarter(
            connection,
            year=year,
            quarter=quarter,
            family=family,
            identity=identity,
            limit=limit_per_quarter,
        )
        for year in years
        for quarter in quarters
    ]
