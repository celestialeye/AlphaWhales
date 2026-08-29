from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import duckdb

from .database import DEFAULT_DATA_DIR

SEC_DATASET_PAGE = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
SEC_BASE_URL = "https://www.sec.gov"
VALUE_UNIT_CHANGE_DATE = date(2023, 1, 3)

EXPECTED_COLUMNS = {
    "SUBMISSION.tsv": {
        "ACCESSION_NUMBER", "FILING_DATE", "SUBMISSIONTYPE", "CIK", "PERIODOFREPORT"
    },
    "COVERPAGE.tsv": {
        "ACCESSION_NUMBER", "REPORTCALENDARORQUARTER", "ISAMENDMENT",
        "AMENDMENTNO", "AMENDMENTTYPE", "CONFDENIEDEXPIRED",
        "DATEDENIEDEXPIRED", "DATEREPORTED", "REASONFORNONCONFIDENTIALITY",
        "FILINGMANAGER_NAME", "FILINGMANAGER_STREET1",
        "FILINGMANAGER_STREET2", "FILINGMANAGER_CITY",
        "FILINGMANAGER_STATEORCOUNTRY", "FILINGMANAGER_ZIPCODE",
        "REPORTTYPE", "FORM13FFILENUMBER", "CRDNUMBER", "SECFILENUMBER",
        "PROVIDEINFOFORINSTRUCTION5", "ADDITIONALINFORMATION"
    },
    "SUMMARYPAGE.tsv": {
        "ACCESSION_NUMBER", "OTHERINCLUDEDMANAGERSCOUNT", "TABLEENTRYTOTAL",
        "TABLEVALUETOTAL", "ISCONFIDENTIALOMITTED"
    },
    "SIGNATURE.tsv": {
        "ACCESSION_NUMBER", "NAME", "TITLE", "PHONE", "SIGNATURE", "CITY",
        "STATEORCOUNTRY", "SIGNATUREDATE"
    },
    "OTHERMANAGER.tsv": {
        "ACCESSION_NUMBER", "OTHERMANAGER_SK", "CIK", "FORM13FFILENUMBER",
        "CRDNUMBER", "SECFILENUMBER", "NAME"
    },
    "OTHERMANAGER2.tsv": {
        "ACCESSION_NUMBER", "SEQUENCENUMBER", "CIK", "FORM13FFILENUMBER",
        "CRDNUMBER", "SECFILENUMBER", "NAME"
    },
    "INFOTABLE.tsv": {
        "ACCESSION_NUMBER", "INFOTABLE_SK", "NAMEOFISSUER", "TITLEOFCLASS",
        "CUSIP", "FIGI", "VALUE", "SSHPRNAMT", "SSHPRNAMTTYPE", "PUTCALL",
        "INVESTMENTDISCRETION", "OTHERMANAGER", "VOTING_AUTH_SOLE",
        "VOTING_AUTH_SHARED", "VOTING_AUTH_NONE"
    },
}


@dataclass(frozen=True)
class SecDataset:
    dataset_id: str
    url: str
    period_start: date
    period_end: date


def _request(url: str, identity: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": identity,
            "Host": "www.sec.gov",
        },
    )


def _legacy_period(dataset_id: str) -> tuple[date, date] | None:
    match = re.fullmatch(r"(\d{4})q([1-4])_form13f\.zip", dataset_id, re.IGNORECASE)
    if not match:
        return None
    year, quarter = int(match.group(1)), int(match.group(2))
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    next_month = date(year + (end_month == 12), (end_month % 12) + 1, 1)
    return date(year, start_month, 1), next_month.fromordinal(next_month.toordinal() - 1)


def _dated_period(dataset_id: str) -> tuple[date, date] | None:
    match = re.fullmatch(
        r"(\d{2}[a-z]{3}\d{4})-(\d{2}[a-z]{3}\d{4})_form13f\.zip",
        dataset_id,
        re.IGNORECASE,
    )
    if not match:
        return None
    return (
        datetime.strptime(match.group(1), "%d%b%Y").date(),
        datetime.strptime(match.group(2), "%d%b%Y").date(),
    )


def discover_datasets(identity: str) -> list[SecDataset]:
    with urllib.request.urlopen(_request(SEC_DATASET_PAGE, identity), timeout=60) as response:
        html = response.read().decode("utf-8", errors="replace")

    links = re.findall(
        r'href="([^"]+_form13f\.zip)"',
        html,
        flags=re.IGNORECASE,
    )
    datasets = []
    for link in links:
        dataset_id = Path(link).name
        period = _legacy_period(dataset_id) or _dated_period(dataset_id)
        if period is None:
            raise ValueError(f"Unrecognized SEC dataset filename: {dataset_id}")
        url = link if link.startswith("http") else f"{SEC_BASE_URL}{link}"
        datasets.append(SecDataset(dataset_id, url, period[0], period[1]))
    return sorted({item.dataset_id: item for item in datasets}.values(), key=lambda item: item.period_start)


def download_dataset(
    dataset: SecDataset,
    identity: str,
    download_dir: str | Path = DEFAULT_DATA_DIR / "downloads",
) -> Path:
    target_dir = Path(download_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / dataset.dataset_id
    partial = target.with_suffix(target.suffix + ".partial")
    if target.exists() and zipfile.is_zipfile(target):
        return target

    with urllib.request.urlopen(_request(dataset.url, identity), timeout=180) as response:
        with partial.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
    if not zipfile.is_zipfile(partial):
        partial.unlink(missing_ok=True)
        raise ValueError(f"Downloaded file is not a valid ZIP archive: {dataset.url}")
    partial.replace(target)
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def _columns(connection: duckdb.DuckDBPyConnection, table_name: str) -> set[str]:
    return {
        str(row[1]).upper()
        for row in connection.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    }


def _date_expr(column: str) -> str:
    return (
        f"coalesce(try_strptime({column}, '%d-%b-%Y')::DATE, "
        f"try_strptime({column}, '%m-%d-%Y')::DATE, try_cast({column} AS DATE))"
    )


def _bool_expr(column: str) -> str:
    return (
        f"CASE WHEN upper(trim(coalesce({column}, ''))) IN ('Y', 'YES', 'TRUE', '1') "
        f"THEN true WHEN upper(trim(coalesce({column}, ''))) IN ('N', 'NO', 'FALSE', '0') "
        f"THEN false ELSE NULL END"
    )


def _load_source_table(
    connection: duckdb.DuckDBPyConnection,
    source_file: Path,
    table_name: str,
    expected_columns: set[str],
) -> None:
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE {table_name} AS
        SELECT *
        FROM read_csv(
            '{_sql_path(source_file)}',
            delim = '\\t',
            header = true,
            all_varchar = true,
            null_padding = false,
            ignore_errors = false
        )
        """
    )
    actual_columns = _columns(connection, table_name)
    if actual_columns != expected_columns:
        missing = sorted(expected_columns - actual_columns)
        unknown = sorted(actual_columns - expected_columns)
        raise ValueError(
            f"SEC schema drift in {source_file.name}; missing={missing}, unknown={unknown}"
        )


def import_archive(
    connection: duckdb.DuckDBPyConnection,
    archive_path: str | Path,
    *,
    source_url: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
) -> dict:
    archive = Path(archive_path).resolve()
    if not zipfile.is_zipfile(archive):
        raise ValueError(f"Not a valid ZIP archive: {archive}")

    dataset_id = archive.name
    inferred_period = _legacy_period(dataset_id) or _dated_period(dataset_id)
    if inferred_period:
        period_start = period_start or inferred_period[0]
        period_end = period_end or inferred_period[1]

    source_hash = _sha256(archive)
    existing_dataset = connection.execute(
        "SELECT source_sha256, status FROM datasets WHERE dataset_id = ?",
        [dataset_id],
    ).fetchone()
    if (
        existing_dataset
        and existing_dataset[1] == "IMPORTED"
        and existing_dataset[0] != source_hash
    ):
        raise ValueError(
            f"Dataset {dataset_id} has changed source content. "
            "Import it under a new dataset ID or rebuild the database explicitly."
        )
    connection.execute(
        """
        INSERT INTO datasets (
            dataset_id, source_url, local_path, source_sha256,
            period_start, period_end, status
        )
        VALUES (?, ?, ?, ?, ?, ?, 'IMPORTING')
        ON CONFLICT (dataset_id) DO UPDATE SET
            source_url = excluded.source_url,
            local_path = excluded.local_path,
            source_sha256 = excluded.source_sha256,
            period_start = excluded.period_start,
            period_end = excluded.period_end,
            status = 'IMPORTING',
            last_error = NULL
        """,
        [dataset_id, source_url, str(archive), source_hash, period_start, period_end],
    )

    try:
        with tempfile.TemporaryDirectory(prefix="alpha-whales-13f-") as temp_dir:
            extraction_dir = Path(temp_dir)
            with zipfile.ZipFile(archive) as source:
                members = {
                    Path(member).name.upper(): member
                    for member in source.namelist()
                    if Path(member).name
                }
                required = {"SUBMISSION.tsv", "COVERPAGE.tsv", "SUMMARYPAGE.tsv", "INFOTABLE.tsv"}
                missing_files = sorted(
                    name for name in required if name.upper() not in members
                )
                if missing_files:
                    raise ValueError(f"Archive is missing required files: {missing_files}")
                for name in EXPECTED_COLUMNS:
                    member = members.get(name.upper())
                    if member:
                        with source.open(member) as archive_file:
                            with (extraction_dir / name).open("wb") as extracted_file:
                                shutil.copyfileobj(archive_file, extracted_file)

            source_tables = {}
            for filename, expected in EXPECTED_COLUMNS.items():
                source_file = extraction_dir / filename
                if not source_file.exists():
                    continue
                table_name = f"src_{filename.removesuffix('.tsv').lower()}"
                _load_source_table(connection, source_file, table_name, expected)
                source_tables[filename] = table_name

            source_submission_count = connection.execute(
                "SELECT count(*) FROM src_submission"
            ).fetchone()[0]
            source_holdings_count = connection.execute(
                "SELECT count(*) FROM src_infotable"
            ).fetchone()[0]

            connection.execute("BEGIN TRANSACTION")
            connection.execute(
                """
                DELETE FROM holdings
                WHERE accession_number IN (SELECT ACCESSION_NUMBER FROM src_submission);
                DELETE FROM other_managers
                WHERE accession_number IN (SELECT ACCESSION_NUMBER FROM src_submission);
                DELETE FROM signatures
                WHERE accession_number IN (SELECT ACCESSION_NUMBER FROM src_submission);
                DELETE FROM summary_pages
                WHERE accession_number IN (SELECT ACCESSION_NUMBER FROM src_submission);
                DELETE FROM cover_pages
                WHERE accession_number IN (SELECT ACCESSION_NUMBER FROM src_submission);
                DELETE FROM submissions
                WHERE accession_number IN (SELECT ACCESSION_NUMBER FROM src_submission);
                """
            )

            connection.execute(
                f"""
                INSERT INTO submissions
                SELECT
                    trim(ACCESSION_NUMBER),
                    ?,
                    {_date_expr('FILING_DATE')},
                    trim(SUBMISSIONTYPE),
                    lpad(trim(CIK), 10, '0'),
                    {_date_expr('PERIODOFREPORT')}
                FROM src_submission
                """,
                [dataset_id],
            )
            connection.execute(
                f"""
                INSERT INTO cover_pages
                SELECT
                    trim(ACCESSION_NUMBER),
                    {_date_expr('REPORTCALENDARORQUARTER')},
                    {_bool_expr('ISAMENDMENT')},
                    try_cast(AMENDMENTNO AS INTEGER),
                    nullif(trim(AMENDMENTTYPE), ''),
                    {_bool_expr('CONFDENIEDEXPIRED')},
                    {_date_expr('DATEDENIEDEXPIRED')},
                    {_date_expr('DATEREPORTED')},
                    nullif(trim(REASONFORNONCONFIDENTIALITY), ''),
                    nullif(trim(FILINGMANAGER_NAME), ''),
                    nullif(trim(FILINGMANAGER_STREET1), ''),
                    nullif(trim(FILINGMANAGER_STREET2), ''),
                    nullif(trim(FILINGMANAGER_CITY), ''),
                    nullif(trim(FILINGMANAGER_STATEORCOUNTRY), ''),
                    nullif(trim(FILINGMANAGER_ZIPCODE), ''),
                    nullif(trim(REPORTTYPE), ''),
                    nullif(trim(FORM13FFILENUMBER), ''),
                    nullif(trim(CRDNUMBER), ''),
                    nullif(trim(SECFILENUMBER), ''),
                    nullif(trim(PROVIDEINFOFORINSTRUCTION5), ''),
                    nullif(trim(ADDITIONALINFORMATION), '')
                FROM src_coverpage
                """
            )
            connection.execute(
                """
                INSERT INTO sec_filings (
                    accession_number, filing_family, form, cik, company_name,
                    filing_date, period_of_report, source_url, source_kind, dataset_id
                )
                SELECT
                    s.accession_number,
                    'institutional_holdings',
                    s.submission_type,
                    s.cik,
                    cp.filing_manager_name,
                    s.filing_date,
                    s.period_of_report,
                    concat(
                        'https://www.sec.gov/Archives/edgar/data/',
                        cast(try_cast(s.cik AS BIGINT) AS VARCHAR),
                        '/',
                        replace(s.accession_number, '-', ''),
                        '/',
                        s.accession_number,
                        '.txt'
                    ),
                    'SEC_13F_BULK',
                    s.dataset_id
                FROM submissions s
                LEFT JOIN cover_pages cp USING (accession_number)
                WHERE s.dataset_id = ?
                ON CONFLICT (accession_number) DO UPDATE SET
                    filing_family = excluded.filing_family,
                    form = excluded.form,
                    cik = excluded.cik,
                    company_name = excluded.company_name,
                    filing_date = excluded.filing_date,
                    period_of_report = excluded.period_of_report,
                    source_url = excluded.source_url,
                    source_kind = excluded.source_kind,
                    dataset_id = excluded.dataset_id
                """,
                [dataset_id],
            )
            connection.execute(
                f"""
                INSERT INTO summary_pages
                SELECT
                    trim(sp.ACCESSION_NUMBER),
                    try_cast(sp.OTHERINCLUDEDMANAGERSCOUNT AS INTEGER),
                    try_cast(sp.TABLEENTRYTOTAL AS BIGINT),
                    try_cast(sp.TABLEVALUETOTAL AS DECIMAL(38, 0)),
                    CASE
                        WHEN s.filing_date < DATE '{VALUE_UNIT_CHANGE_DATE.isoformat()}'
                        THEN 'THOUSANDS_USD'
                        ELSE 'USD'
                    END,
                    CASE
                        WHEN s.filing_date < DATE '{VALUE_UNIT_CHANGE_DATE.isoformat()}'
                        THEN try_cast(sp.TABLEVALUETOTAL AS DECIMAL(38, 0)) * 1000
                        ELSE try_cast(sp.TABLEVALUETOTAL AS DECIMAL(38, 0))
                    END,
                    {_bool_expr('sp.ISCONFIDENTIALOMITTED')}
                FROM src_summarypage sp
                JOIN submissions s ON s.accession_number = trim(sp.ACCESSION_NUMBER)
                """
            )
            if "SIGNATURE.tsv" in source_tables:
                connection.execute(
                    f"""
                    INSERT INTO signatures
                    SELECT
                        trim(ACCESSION_NUMBER),
                        nullif(trim(NAME), ''),
                        nullif(trim(TITLE), ''),
                        nullif(trim(PHONE), ''),
                        nullif(trim(SIGNATURE), ''),
                        nullif(trim(CITY), ''),
                        nullif(trim(STATEORCOUNTRY), ''),
                        {_date_expr('SIGNATUREDATE')}
                    FROM src_signature
                    """
                )
            if "OTHERMANAGER.tsv" in source_tables:
                connection.execute(
                    """
                    INSERT INTO other_managers
                    SELECT
                        trim(ACCESSION_NUMBER),
                        'OTHERMANAGER',
                        trim(OTHERMANAGER_SK),
                        NULL,
                        nullif(lpad(trim(CIK), 10, '0'), '0000000000'),
                        nullif(trim(FORM13FFILENUMBER), ''),
                        nullif(trim(CRDNUMBER), ''),
                        nullif(trim(SECFILENUMBER), ''),
                        nullif(trim(NAME), '')
                    FROM src_othermanager
                    """
                )
            if "OTHERMANAGER2.tsv" in source_tables:
                connection.execute(
                    """
                    INSERT INTO other_managers
                    SELECT
                        trim(ACCESSION_NUMBER),
                        'OTHERMANAGER2',
                        concat(
                            coalesce(trim(SEQUENCENUMBER), ''),
                            ':',
                            row_number() OVER (
                                PARTITION BY ACCESSION_NUMBER
                                ORDER BY
                                    try_cast(SEQUENCENUMBER AS INTEGER),
                                    coalesce(CIK, ''),
                                    coalesce(NAME, ''),
                                    coalesce(FORM13FFILENUMBER, '')
                            )
                        ),
                        try_cast(SEQUENCENUMBER AS INTEGER),
                        nullif(lpad(trim(CIK), 10, '0'), '0000000000'),
                        nullif(trim(FORM13FFILENUMBER), ''),
                        nullif(trim(CRDNUMBER), ''),
                        nullif(trim(SECFILENUMBER), ''),
                        nullif(trim(NAME), '')
                    FROM src_othermanager2
                    """
                )
            connection.execute(
                f"""
                INSERT INTO holdings
                SELECT
                    trim(h.ACCESSION_NUMBER),
                    try_cast(h.INFOTABLE_SK AS BIGINT),
                    nullif(trim(h.NAMEOFISSUER), ''),
                    nullif(trim(h.TITLEOFCLASS), ''),
                    nullif(trim(h.CUSIP), ''),
                    nullif(trim(h.FIGI), ''),
                    try_cast(h.VALUE AS DECIMAL(38, 0)),
                    CASE
                        WHEN s.filing_date < DATE '{VALUE_UNIT_CHANGE_DATE.isoformat()}'
                        THEN 'THOUSANDS_USD'
                        ELSE 'USD'
                    END,
                    CASE
                        WHEN s.filing_date < DATE '{VALUE_UNIT_CHANGE_DATE.isoformat()}'
                        THEN try_cast(h.VALUE AS DECIMAL(38, 0)) * 1000
                        ELSE try_cast(h.VALUE AS DECIMAL(38, 0))
                    END,
                    try_cast(h.SSHPRNAMT AS DECIMAL(38, 6)),
                    nullif(trim(h.SSHPRNAMTTYPE), ''),
                    nullif(upper(trim(h.PUTCALL)), ''),
                    nullif(trim(h.INVESTMENTDISCRETION), ''),
                    nullif(trim(h.OTHERMANAGER), ''),
                    try_cast(h.VOTING_AUTH_SOLE AS DECIMAL(38, 6)),
                    try_cast(h.VOTING_AUTH_SHARED AS DECIMAL(38, 6)),
                    try_cast(h.VOTING_AUTH_NONE AS DECIMAL(38, 6)),
                    NULL
                FROM src_infotable h
                JOIN submissions s ON s.accession_number = trim(h.ACCESSION_NUMBER)
                """
            )
            submission_count = connection.execute(
                "SELECT count(*) FROM submissions WHERE dataset_id = ?",
                [dataset_id],
            ).fetchone()[0]
            holdings_count = connection.execute(
                """
                SELECT count(*)
                FROM holdings h
                JOIN submissions s USING (accession_number)
                WHERE s.dataset_id = ?
                """,
                [dataset_id],
            ).fetchone()[0]
            if submission_count != source_submission_count:
                raise ValueError(
                    f"Submission count mismatch: source={source_submission_count}, "
                    f"database={submission_count}"
                )
            if holdings_count != source_holdings_count:
                raise ValueError(
                    f"Holdings count mismatch: source={source_holdings_count}, "
                    f"database={holdings_count}"
                )
            connection.execute(
                """
                UPDATE datasets
                SET status = 'IMPORTED',
                    submission_count = ?,
                    holdings_count = ?,
                    imported_at = current_timestamp,
                    last_error = NULL
                WHERE dataset_id = ?
                """,
                [submission_count, holdings_count, dataset_id],
            )
            connection.execute("COMMIT")

        return {
            "dataset_id": dataset_id,
            "submission_count": submission_count,
            "holdings_count": holdings_count,
            "sha256": source_hash,
        }
    except Exception as exc:
        try:
            connection.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        connection.execute(
            "UPDATE datasets SET status = ?, last_error = ? WHERE dataset_id = ?",
            [
                "IMPORTED"
                if existing_dataset and existing_dataset[1] == "IMPORTED"
                else "FAILED",
                str(exc),
                dataset_id,
            ],
        )
        raise


# The normalized 13F importer above remains intentionally separate from the
# lossless Parquet bronze importer, but these re-exports keep SEC bulk entry
# points discoverable from the original module.
from .flattened_bulk import (  # noqa: E402
    BULK_FAMILIES,
    BulkDataset,
    bulk_table_paths,
    bulk_table_sql,
    discover_bulk_datasets,
    download_bulk_dataset,
    import_bulk_archive,
    import_flattened_archive,
)
