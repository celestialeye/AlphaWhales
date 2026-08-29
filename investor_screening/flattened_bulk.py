"""Lossless bronze ingestion for the SEC's non-13F flattened data sets."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import shutil
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath

import duckdb

from .database import DEFAULT_DATA_DIR


SEC_BASE_URL = "https://www.sec.gov"


@dataclass(frozen=True)
class BulkFamily:
    name: str
    page_url: str
    archive_suffix: str
    first_period: date


@dataclass(frozen=True)
class BulkDataset:
    family: str
    dataset_id: str
    url: str
    period_start: date
    period_end: date


BULK_FAMILIES = {
    "insider": BulkFamily(
        "insider",
        "https://www.sec.gov/data-research/sec-markets-data/"
        "insider-transactions-data-sets",
        "form345",
        date(2006, 1, 1),
    ),
    "nport": BulkFamily(
        "nport",
        "https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets",
        "nport",
        date(2019, 1, 1),
    ),
    "nmfp": BulkFamily(
        "nmfp",
        "https://www.sec.gov/data-research/sec-markets-data/"
        "dera-form-n-mfp-data-sets",
        "nmfp",
        date(2010, 1, 1),
    ),
}

_FAMILY_ALIASES = {
    "form345": "insider",
    "insider_transactions": "insider",
    "insider-transactions": "insider",
    "insider_ownership": "insider",
    "n-port": "nport",
    "n_port": "nport",
    "registered_fund_portfolios": "nport",
    "n-mfp": "nmfp",
    "n_mfp": "nmfp",
    "money_market_funds": "nmfp",
}

_METADATA_COLUMNS = (
    ("source_dataset_id", "VARCHAR"),
    ("source_archive_member", "VARCHAR"),
    ("source_row_number", "BIGINT"),
)


def normalize_bulk_family(family: str) -> str:
    normalized = family.strip().lower()
    normalized = _FAMILY_ALIASES.get(normalized, normalized)
    if normalized not in BULK_FAMILIES:
        raise ValueError(
            f"Unknown SEC bulk family {family!r}; choose from "
            f"{', '.join(sorted(BULK_FAMILIES))}"
        )
    return normalized


def _request(url: str, identity: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={"User-Agent": identity, "Host": "www.sec.gov"},
    )


def _last_day(year: int, month: int) -> date:
    next_month = date(year + (month == 12), (month % 12) + 1, 1)
    return date.fromordinal(next_month.toordinal() - 1)


def _dataset_period(dataset_id: str, suffix: str) -> tuple[date, date] | None:
    escaped_suffix = re.escape(suffix)
    quarter = re.fullmatch(
        rf"(\d{{4}})q([1-4])_{escaped_suffix}\.zip",
        dataset_id,
        flags=re.IGNORECASE,
    )
    if quarter:
        year, quarter_number = int(quarter.group(1)), int(quarter.group(2))
        start_month = ((quarter_number - 1) * 3) + 1
        end_month = start_month + 2
        return date(year, start_month, 1), _last_day(year, end_month)

    dated = re.fullmatch(
        rf"(\d{{2}}[a-z]{{3}}\d{{4}})-"
        rf"(\d{{2}}[a-z]{{3}}\d{{4}})_{escaped_suffix}\.zip",
        dataset_id,
        flags=re.IGNORECASE,
    )
    if dated:
        return (
            datetime.strptime(dated.group(1), "%d%b%Y").date(),
            datetime.strptime(dated.group(2), "%d%b%Y").date(),
        )

    numeric_dated = re.fullmatch(
        rf"(\d{{8}})-(\d{{8}})_{escaped_suffix}\.zip",
        dataset_id,
        flags=re.IGNORECASE,
    )
    if numeric_dated:
        return (
            datetime.strptime(numeric_dated.group(1), "%Y%m%d").date(),
            datetime.strptime(numeric_dated.group(2), "%Y%m%d").date(),
        )

    monthly = re.fullmatch(
        rf"(\d{{4}})[_-](\d{{2}})_{escaped_suffix}\.zip",
        dataset_id,
        flags=re.IGNORECASE,
    )
    if monthly:
        year, month = int(monthly.group(1)), int(monthly.group(2))
        if 1 <= month <= 12:
            return date(year, month, 1), _last_day(year, month)
    return None


def discover_bulk_datasets(family: str, identity: str) -> list[BulkDataset]:
    """Discover one family's official archives from the corresponding SEC page."""

    family_name = normalize_bulk_family(family)
    config = BULK_FAMILIES[family_name]
    with urllib.request.urlopen(_request(config.page_url, identity), timeout=60) as response:
        page = response.read().decode("utf-8", errors="replace")

    suffix = re.escape(config.archive_suffix)
    links = re.findall(
        rf"""href\s*=\s*["']([^"']*_{suffix}\.zip(?:\?[^"']*)?)["']""",
        page,
        flags=re.IGNORECASE,
    )
    datasets: dict[str, BulkDataset] = {}
    for raw_link in links:
        link = html.unescape(raw_link)
        url = urllib.parse.urljoin(SEC_BASE_URL, link)
        dataset_id = Path(
            urllib.parse.unquote(urllib.parse.urlparse(url).path)
        ).name
        period = _dataset_period(dataset_id, config.archive_suffix)
        if period is None:
            raise ValueError(f"Unrecognized SEC dataset filename: {dataset_id}")
        datasets[dataset_id.lower()] = BulkDataset(
            family_name,
            dataset_id,
            url,
            period[0],
            period[1],
        )
    return sorted(datasets.values(), key=lambda item: (item.period_start, item.dataset_id))


def download_bulk_dataset(
    dataset: BulkDataset,
    identity: str,
    download_dir: str | Path = DEFAULT_DATA_DIR / "downloads",
) -> Path:
    """Download atomically, retaining partial files only while a request is active."""

    family = normalize_bulk_family(dataset.family)
    target_dir = Path(download_dir).resolve() / family
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / dataset.dataset_id
    partial = target.with_suffix(target.suffix + ".partial")
    if target.exists() and zipfile.is_zipfile(target):
        return target

    partial.unlink(missing_ok=True)
    try:
        with urllib.request.urlopen(_request(dataset.url, identity), timeout=180) as response:
            with partial.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
        if not zipfile.is_zipfile(partial):
            raise ValueError(f"Downloaded file is not a valid ZIP archive: {dataset.url}")
        partial.replace(target)
        return target
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sql_string(value: str | Path) -> str:
    return str(value).replace("'", "''")


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _safe_member_path(member: str) -> PurePosixPath:
    path = PurePosixPath(member.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"Unsafe ZIP member path: {member!r}")
    return path


def _table_name(member_path: PurePosixPath) -> str:
    table_name = re.sub(r"[^a-z0-9]+", "_", member_path.stem.lower()).strip("_")
    if not table_name:
        raise ValueError(f"Cannot derive a source table name from {member_path}")
    return table_name


def _source_columns(source_file: Path) -> list[str]:
    with source_file.open("r", encoding="utf-8-sig", newline="") as stream:
        try:
            columns = next(csv.reader(stream, delimiter="\t"))
        except StopIteration as exc:
            raise ValueError(f"TSV has no header: {source_file.name}") from exc
    if not columns or any(not column for column in columns):
        raise ValueError(f"TSV has a blank source column name: {source_file.name}")
    folded = [column.casefold() for column in columns]
    duplicates = sorted({name for name in folded if folded.count(name) > 1})
    if duplicates:
        raise ValueError(
            f"TSV has duplicate case-insensitive columns in {source_file.name}: {duplicates}"
        )
    reserved = sorted(set(folded) & {item[0].casefold() for item in _METADATA_COLUMNS})
    if reserved:
        raise ValueError(
            f"TSV columns conflict with bronze metadata in {source_file.name}: {reserved}"
        )
    return columns


def _read_csv_expression(source_file: Path) -> str:
    return (
        f"read_csv('{_sql_string(source_file.resolve())}', "
        "delim = '\\t', header = true, all_varchar = true, "
        "sample_size = -1, null_padding = false, ignore_errors = false, "
        "parallel = false, max_line_size = 134217728)"
    )


def _describe_query(
    connection: duckdb.DuckDBPyConnection,
    query: str,
) -> list[tuple[str, str]]:
    return [
        (str(row[0]), str(row[1]).upper())
        for row in connection.execute(f"DESCRIBE {query}").fetchall()
    ]


def _source_query(
    source_file: Path,
    columns: list[str],
    dataset_id: str,
    source_member: str,
) -> str:
    selected = ", ".join(_identifier(column) for column in columns)
    return (
        "SELECT "
        f"{selected}, "
        f"'{_sql_string(dataset_id)}'::VARCHAR AS source_dataset_id, "
        f"'{_sql_string(source_member)}'::VARCHAR AS source_archive_member, "
        "row_number() OVER ()::BIGINT AS source_row_number "
        f"FROM {_read_csv_expression(source_file)}"
    )


def _validate_source_schema(
    connection: duckdb.DuckDBPyConnection,
    source_file: Path,
    expected_columns: list[str],
) -> None:
    described = _describe_query(
        connection,
        f"SELECT * FROM {_read_csv_expression(source_file)}",
    )
    actual_columns = [item[0] for item in described]
    if actual_columns != expected_columns:
        raise ValueError(
            f"TSV header changed while parsing {source_file.name}; "
            f"header={expected_columns!r}, parsed={actual_columns!r}"
        )
    non_varchar = [item for item in described if item[1] != "VARCHAR"]
    if non_varchar:
        raise ValueError(
            f"Source columns were not retained as VARCHAR in {source_file.name}: "
            f"{non_varchar!r}"
        )


def _write_and_validate_parquet(
    connection: duckdb.DuckDBPyConnection,
    source_file: Path,
    parquet_file: Path,
    columns: list[str],
    dataset_id: str,
    source_member: str,
) -> tuple[int, int, str, str, int]:
    source_query = _source_query(
        source_file,
        columns,
        dataset_id,
        source_member,
    )
    source_count = int(
        connection.execute(
            f"SELECT count(*) FROM ({source_query}) source_rows"
        ).fetchone()[0]
    )
    connection.execute(
        f"""
        COPY ({source_query})
        TO '{_sql_string(parquet_file.resolve())}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )
    parquet_expression = (
        f"read_parquet('{_sql_string(parquet_file.resolve())}')"
    )
    parquet_count = int(
        connection.execute(
            f"SELECT count(*) FROM {parquet_expression}"
        ).fetchone()[0]
    )

    expected_schema = [(column, "VARCHAR") for column in columns] + list(_METADATA_COLUMNS)
    parquet_schema = _describe_query(
        connection,
        f"SELECT * FROM {parquet_expression}",
    )
    if parquet_schema != expected_schema:
        raise ValueError(
            f"Parquet schema mismatch for {source_member}; "
            f"source={expected_schema!r}, parquet={parquet_schema!r}"
        )
    if source_count != parquet_count:
        raise ValueError(
            f"Row count mismatch for {source_member}: "
            f"source={source_count}, parquet={parquet_count}"
        )

    all_columns = [*columns, *(item[0] for item in _METADATA_COLUMNS)]
    row_hash = f"hash({', '.join(_identifier(column) for column in all_columns)})"
    source_digest = connection.execute(
        f"""
        SELECT
            coalesce(sum({row_hash}::HUGEINT), 0),
            coalesce(bit_xor({row_hash}), 0)
        FROM ({source_query}) source_rows
        """
    ).fetchone()
    parquet_digest = connection.execute(
        f"""
        SELECT
            coalesce(sum({row_hash}::HUGEINT), 0),
            coalesce(bit_xor({row_hash}), 0)
        FROM {parquet_expression}
        """
    ).fetchone()
    if source_digest != parquet_digest:
        raise ValueError(
            f"Data digest mismatch for {source_member}: "
            f"source={source_digest}, parquet={parquet_digest}"
        )

    schema_json = json.dumps(
        {
            "source_columns": [
                {"name": column, "type": "VARCHAR"} for column in columns
            ],
            "metadata_columns": [
                {"name": name, "type": data_type}
                for name, data_type in _METADATA_COLUMNS
            ],
            "parquet_columns": [
                {"name": name, "type": data_type}
                for name, data_type in parquet_schema
            ],
            "row_digest": {
                "sum_hash": str(source_digest[0]),
                "xor_hash": str(source_digest[1]),
            },
        },
        separators=(",", ":"),
    )
    return (
        source_count,
        parquet_count,
        schema_json,
        _sha256(parquet_file),
        parquet_file.stat().st_size,
    )


def _import_is_intact(
    connection: duckdb.DuckDBPyConnection,
    family: str,
    dataset_id: str,
) -> bool:
    files = connection.execute(
        """
        SELECT output_path, parquet_row_count
        FROM bulk_dataset_files
        WHERE family = ? AND dataset_id = ? AND status = 'IMPORTED'
        """,
        [family, dataset_id],
    ).fetchall()
    if not files:
        return False
    for output_path, expected_count in files:
        path = Path(str(output_path))
        if not path.is_file():
            return False
        try:
            count = connection.execute(
                f"SELECT count(*) FROM read_parquet('{_sql_string(path.resolve())}')"
            ).fetchone()[0]
        except duckdb.Error:
            return False
        if count != expected_count:
            return False
    return True


def import_flattened_archive(
    connection: duckdb.DuckDBPyConnection,
    archive_path: str | Path,
    family: str,
    *,
    source_url: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    lake_dir: str | Path = DEFAULT_DATA_DIR / "lake",
    delete_archive: bool = False,
) -> dict:
    """Import a flattened ZIP into Parquet, retaining the source ZIP by default."""

    family_name = normalize_bulk_family(family)
    config = BULK_FAMILIES[family_name]
    archive = Path(archive_path).resolve()
    if not archive.is_file() or not zipfile.is_zipfile(archive):
        raise ValueError(f"Not a valid ZIP archive: {archive}")

    dataset_id = archive.name
    inferred_period = _dataset_period(dataset_id, config.archive_suffix)
    if inferred_period:
        period_start = period_start or inferred_period[0]
        period_end = period_end or inferred_period[1]
    source_hash = _sha256(archive)

    existing = connection.execute(
        """
        SELECT source_sha256, status, source_url
        FROM bulk_datasets
        WHERE family = ? AND dataset_id = ?
        """,
        [family_name, dataset_id],
    ).fetchone()
    manifest_source_url = (
        source_url
        or (str(existing[2]) if existing and existing[2] else None)
        or archive.as_uri()
    )
    if existing and existing[1] == "IMPORTED" and existing[0] != source_hash:
        raise ValueError(
            f"Dataset {family_name}/{dataset_id} has changed source content; "
            "use a new dataset ID or explicitly rebuild the bronze dataset"
        )
    if (
        existing
        and existing[1] == "IMPORTED"
        and existing[0] == source_hash
        and _import_is_intact(connection, family_name, dataset_id)
    ):
        archive_deleted = False
        if delete_archive:
            archive.unlink()
            archive_deleted = True
        connection.execute(
            """
            UPDATE bulk_datasets
            SET source_url = coalesce(?, source_url),
                local_archive_path = ?,
                archive_deleted = ?
            WHERE family = ? AND dataset_id = ?
            """,
            [
                manifest_source_url,
                str(archive),
                archive_deleted,
                family_name,
                dataset_id,
            ],
        )
        totals = connection.execute(
            """
            SELECT source_table_count, source_row_count, parquet_row_count
            FROM bulk_datasets
            WHERE family = ? AND dataset_id = ?
            """,
            [family_name, dataset_id],
        ).fetchone()
        return {
            "family": family_name,
            "dataset_id": dataset_id,
            "status": "skipped",
            "table_count": totals[0],
            "source_row_count": totals[1],
            "parquet_row_count": totals[2],
            "sha256": source_hash,
            "archive_deleted": archive_deleted,
        }

    connection.execute(
        """
        INSERT INTO bulk_datasets (
            family, dataset_id, archive_filename, source_url,
            local_archive_path, source_sha256, period_start, period_end,
            status, archive_deleted
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'IMPORTING', false)
        ON CONFLICT (family, dataset_id) DO UPDATE SET
            archive_filename = excluded.archive_filename,
            source_url = coalesce(excluded.source_url, bulk_datasets.source_url),
            local_archive_path = excluded.local_archive_path,
            source_sha256 = excluded.source_sha256,
            period_start = coalesce(excluded.period_start, bulk_datasets.period_start),
            period_end = coalesce(excluded.period_end, bulk_datasets.period_end),
            status = 'IMPORTING',
            archive_deleted = false,
            last_error = NULL
        """,
        [
            family_name,
            dataset_id,
            archive.name,
            manifest_source_url,
            str(archive),
            source_hash,
            period_start,
            period_end,
        ],
    )

    lake = Path(lake_dir).resolve()
    staging_root = (
        lake
        / ".staging"
        / family_name
        / f"{dataset_id}-{source_hash[:12]}-{uuid.uuid4().hex}"
    )
    staged_source = staging_root / "source"
    staged_parquet = staging_root / "parquet"
    staged_metadata = staging_root / "metadata"
    moved_paths: list[Path] = []
    transaction_open = False
    manifest_committed = False

    try:
        staged_source.mkdir(parents=True, exist_ok=False)
        staged_parquet.mkdir(parents=True, exist_ok=False)
        staged_metadata.mkdir(parents=True, exist_ok=False)

        table_members: list[tuple[str, PurePosixPath, zipfile.ZipInfo]] = []
        metadata_members: list[tuple[PurePosixPath, zipfile.ZipInfo]] = []
        seen_member_paths: set[str] = set()
        seen_tables: dict[str, str] = {}
        with zipfile.ZipFile(archive) as source:
            for info in source.infolist():
                if info.is_dir():
                    continue
                member_path = _safe_member_path(info.filename)
                folded_path = member_path.as_posix().casefold()
                if folded_path in seen_member_paths:
                    raise ValueError(
                        f"Archive contains duplicate case-insensitive member: {info.filename}"
                    )
                seen_member_paths.add(folded_path)
                if member_path.suffix.casefold() == ".tsv":
                    table_name = _table_name(member_path)
                    if table_name in seen_tables:
                        raise ValueError(
                            "Archive TSV table-name collision: "
                            f"{seen_tables[table_name]!r} and {info.filename!r}"
                        )
                    seen_tables[table_name] = info.filename
                    table_members.append((table_name, member_path, info))
                else:
                    metadata_members.append((member_path, info))

            if not table_members:
                raise ValueError(f"Archive contains no TSV source tables: {archive.name}")

            file_manifests = []
            for index, (table_name, member_path, info) in enumerate(
                sorted(table_members, key=lambda item: item[0])
            ):
                source_file = staged_source / f"{index:04d}.tsv"
                with source.open(info) as archive_file, source_file.open("wb") as output:
                    shutil.copyfileobj(archive_file, output, length=1024 * 1024)
                columns = _source_columns(source_file)
                _validate_source_schema(connection, source_file, columns)
                parquet_file = staged_parquet / f"{index:04d}.parquet"
                (
                    source_count,
                    parquet_count,
                    schema_json,
                    parquet_sha256,
                    parquet_bytes,
                ) = _write_and_validate_parquet(
                    connection,
                    source_file,
                    parquet_file,
                    columns,
                    dataset_id,
                    member_path.as_posix(),
                )
                final_path = lake / family_name / table_name / f"{dataset_id}.parquet"
                file_manifests.append(
                    {
                        "table_name": table_name,
                        "source_member": member_path.as_posix(),
                        "source_count": source_count,
                        "parquet_count": parquet_count,
                        "schema_json": schema_json,
                        "parquet_sha256": parquet_sha256,
                        "parquet_bytes": parquet_bytes,
                        "staged_path": parquet_file,
                        "final_path": final_path,
                    }
                )
                source_file.unlink(missing_ok=True)

            metadata_manifests = []
            for member_path, info in metadata_members:
                staged_path = staged_metadata.joinpath(*member_path.parts)
                staged_path.parent.mkdir(parents=True, exist_ok=True)
                with source.open(info) as archive_file, staged_path.open("wb") as output:
                    shutil.copyfileobj(archive_file, output, length=1024 * 1024)
                final_path = (
                    lake
                    / family_name
                    / "_metadata"
                    / dataset_id
                ).joinpath(*member_path.parts)
                metadata_manifests.append(
                    {
                        "source_member": member_path.as_posix(),
                        "source_sha256": _sha256(staged_path),
                        "byte_count": staged_path.stat().st_size,
                        "staged_path": staged_path,
                        "final_path": final_path,
                    }
                )

        total_source_rows = sum(item["source_count"] for item in file_manifests)
        total_parquet_rows = sum(item["parquet_count"] for item in file_manifests)
        if total_source_rows != total_parquet_rows:
            raise ValueError(
                "Dataset row count mismatch: "
                f"source={total_source_rows}, parquet={total_parquet_rows}"
            )

        connection.execute("BEGIN TRANSACTION")
        transaction_open = True
        connection.execute(
            "DELETE FROM bulk_dataset_files WHERE family = ? AND dataset_id = ?",
            [family_name, dataset_id],
        )
        connection.execute(
            "DELETE FROM bulk_dataset_metadata WHERE family = ? AND dataset_id = ?",
            [family_name, dataset_id],
        )

        for item in [*file_manifests, *metadata_manifests]:
            final_path = item["final_path"]
            final_path.parent.mkdir(parents=True, exist_ok=True)
            final_path.unlink(missing_ok=True)
            item["staged_path"].replace(final_path)
            moved_paths.append(final_path)

        for item in file_manifests:
            connection.execute(
                """
                INSERT INTO bulk_dataset_files (
                    family, dataset_id, table_name, source_member,
                    source_row_count, parquet_row_count, schema_json,
                    output_path, parquet_sha256, parquet_bytes, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'IMPORTED')
                """,
                [
                    family_name,
                    dataset_id,
                    item["table_name"],
                    item["source_member"],
                    item["source_count"],
                    item["parquet_count"],
                    item["schema_json"],
                    str(item["final_path"]),
                    item["parquet_sha256"],
                    item["parquet_bytes"],
                ],
            )
        for item in metadata_manifests:
            connection.execute(
                """
                INSERT INTO bulk_dataset_metadata (
                    family, dataset_id, source_member, source_sha256,
                    output_path, byte_count
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    family_name,
                    dataset_id,
                    item["source_member"],
                    item["source_sha256"],
                    str(item["final_path"]),
                    item["byte_count"],
                ],
            )
        connection.execute(
            """
            UPDATE bulk_datasets
            SET status = 'IMPORTED',
                source_table_count = ?,
                source_row_count = ?,
                parquet_row_count = ?,
                imported_at = current_timestamp,
                last_error = NULL
            WHERE family = ? AND dataset_id = ?
            """,
            [
                len(file_manifests),
                total_source_rows,
                total_parquet_rows,
                family_name,
                dataset_id,
            ],
        )
        connection.execute("COMMIT")
        transaction_open = False
        manifest_committed = True

        archive_deleted = False
        cleanup_error = None
        if delete_archive:
            try:
                archive.unlink()
                archive_deleted = True
            except OSError as exc:
                cleanup_error = str(exc)
            if archive_deleted:
                try:
                    connection.execute(
                        """
                        UPDATE bulk_datasets
                        SET archive_deleted = true
                        WHERE family = ? AND dataset_id = ?
                        """,
                        [family_name, dataset_id],
                    )
                except duckdb.Error as exc:
                    cleanup_error = str(exc)

        result = {
            "family": family_name,
            "dataset_id": dataset_id,
            "status": "imported",
            "table_count": len(file_manifests),
            "source_row_count": total_source_rows,
            "parquet_row_count": total_parquet_rows,
            "metadata_file_count": len(metadata_manifests),
            "sha256": source_hash,
            "archive_deleted": archive_deleted,
        }
        if cleanup_error:
            result["archive_cleanup_error"] = cleanup_error
        return result
    except Exception as exc:
        if manifest_committed:
            raise
        if transaction_open:
            try:
                connection.execute("ROLLBACK")
            except duckdb.TransactionException:
                pass
        for path in moved_paths:
            path.unlink(missing_ok=True)
        connection.execute(
            """
            UPDATE bulk_datasets
            SET status = 'FAILED', last_error = ?
            WHERE family = ? AND dataset_id = ?
            """,
            [str(exc), family_name, dataset_id],
        )
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def import_bulk_archive(*args, **kwargs) -> dict:
    """Compatibility spelling for callers that use the shorter bulk name."""

    return import_flattened_archive(*args, **kwargs)


def bulk_table_paths(
    connection: duckdb.DuckDBPyConnection,
    family: str,
    table_name: str,
) -> list[Path]:
    """Return imported Parquet paths for one logical source table."""

    family_name = normalize_bulk_family(family)
    normalized_table = re.sub(
        r"[^a-z0-9]+",
        "_",
        Path(table_name).stem.lower(),
    ).strip("_")
    return [
        Path(str(row[0]))
        for row in connection.execute(
            """
            SELECT output_path
            FROM v_bulk_parquet_inventory
            WHERE family = ? AND table_name = ?
            ORDER BY period_start, dataset_id
            """,
            [family_name, normalized_table],
        ).fetchall()
    ]


def bulk_table_sql(
    connection: duckdb.DuckDBPyConnection,
    family: str,
    table_name: str,
) -> str:
    """Build a union-by-name scan over every imported file for a source table."""

    paths = bulk_table_paths(connection, family, table_name)
    if not paths:
        raise ValueError(f"No imported Parquet files for {family}/{table_name}")
    path_list = ", ".join(
        f"'{_sql_string(path.resolve())}'" for path in paths
    )
    return (
        f"SELECT * FROM read_parquet([{path_list}], "
        "union_by_name = true, filename = true)"
    )


def refresh_bulk_views(connection: duckdb.DuckDBPyConnection) -> list[str]:
    """Create queryable bronze views for every imported SEC source table."""

    views = []
    tables = connection.execute(
        """
        SELECT DISTINCT family, table_name
        FROM v_bulk_parquet_inventory
        ORDER BY family, table_name
        """
    ).fetchall()
    for family, table_name in tables:
        view_name = f"bronze_{family}_{table_name}"
        connection.execute(
            f"CREATE OR REPLACE VIEW {_identifier(view_name)} "
            f"AS {bulk_table_sql(connection, family, table_name)}"
        )
        views.append(view_name)

    available = set(views)
    analysis_views = {
        "silver_insider_filings": (
            {"bronze_insider_submission"},
            """
            SELECT
                ACCESSION_NUMBER AS accession_number,
                coalesce(
                    try_strptime(FILING_DATE, '%d-%b-%Y')::DATE,
                    try_cast(FILING_DATE AS DATE)
                ) AS filing_date,
                coalesce(
                    try_strptime(PERIOD_OF_REPORT, '%d-%b-%Y')::DATE,
                    try_cast(PERIOD_OF_REPORT AS DATE)
                ) AS period_of_report,
                DOCUMENT_TYPE AS form,
                lpad(ISSUERCIK, 10, '0') AS issuer_cik,
                ISSUERNAME AS issuer_name,
                ISSUERTRADINGSYMBOL AS issuer_ticker,
                REMARKS AS remarks,
                AFF10B5ONE AS has_10b5_1_plan,
                source_dataset_id
            FROM bronze_insider_submission
            """,
        ),
        "silver_insider_reporting_owners": (
            {"bronze_insider_reportingowner"},
            """
            SELECT
                ACCESSION_NUMBER AS accession_number,
                lpad(RPTOWNERCIK, 10, '0') AS reporting_owner_cik,
                RPTOWNERNAME AS reporting_owner_name,
                RPTOWNER_RELATIONSHIP AS relationship,
                RPTOWNER_TITLE AS officer_title,
                FILE_NUMBER AS file_number,
                source_dataset_id
            FROM bronze_insider_reportingowner
            """,
        ),
        "silver_insider_transactions": (
            {"bronze_insider_nonderiv_trans", "bronze_insider_deriv_trans"},
            """
            SELECT
                ACCESSION_NUMBER AS accession_number,
                'NON_DERIVATIVE' AS transaction_kind,
                SECURITY_TITLE AS security_title,
                coalesce(
                    try_strptime(TRANS_DATE, '%d-%b-%Y')::DATE,
                    try_cast(TRANS_DATE AS DATE)
                ) AS transaction_date,
                TRANS_CODE AS transaction_code,
                TRANS_ACQUIRED_DISP_CD AS acquired_disposed,
                try_cast(TRANS_SHARES AS DECIMAL(38, 6)) AS transaction_shares,
                try_cast(TRANS_PRICEPERSHARE AS DECIMAL(38, 6)) AS price_per_share,
                try_cast(SHRS_OWND_FOLWNG_TRANS AS DECIMAL(38, 6)) AS shares_after,
                DIRECT_INDIRECT_OWNERSHIP AS ownership_type,
                NATURE_OF_OWNERSHIP AS ownership_nature,
                NULL::VARCHAR AS underlying_security,
                NULL::DECIMAL(38, 6) AS underlying_shares,
                source_dataset_id
            FROM bronze_insider_nonderiv_trans
            UNION ALL
            SELECT
                ACCESSION_NUMBER,
                'DERIVATIVE',
                SECURITY_TITLE,
                coalesce(
                    try_strptime(TRANS_DATE, '%d-%b-%Y')::DATE,
                    try_cast(TRANS_DATE AS DATE)
                ),
                TRANS_CODE,
                TRANS_ACQUIRED_DISP_CD,
                try_cast(TRANS_SHARES AS DECIMAL(38, 6)),
                try_cast(TRANS_PRICEPERSHARE AS DECIMAL(38, 6)),
                try_cast(SHRS_OWND_FOLWNG_TRANS AS DECIMAL(38, 6)),
                DIRECT_INDIRECT_OWNERSHIP,
                NATURE_OF_OWNERSHIP,
                UNDLYNG_SEC_TITLE,
                try_cast(UNDLYNG_SEC_SHARES AS DECIMAL(38, 6)),
                source_dataset_id
            FROM bronze_insider_deriv_trans
            """,
        ),
        "silver_nport_filings": (
            {
                "bronze_nport_submission",
                "bronze_nport_registrant",
                "bronze_nport_fund_reported_info",
            },
            """
            SELECT
                s.ACCESSION_NUMBER AS accession_number,
                coalesce(
                    try_strptime(s.FILING_DATE, '%d-%b-%Y')::DATE,
                    try_cast(s.FILING_DATE AS DATE)
                ) AS filing_date,
                coalesce(
                    try_strptime(s.REPORT_DATE, '%d-%b-%Y')::DATE,
                    try_cast(s.REPORT_DATE AS DATE)
                ) AS report_date,
                s.SUB_TYPE AS form,
                lpad(r.CIK, 10, '0') AS registrant_cik,
                r.REGISTRANT_NAME AS registrant_name,
                r.LEI AS registrant_lei,
                f.SERIES_ID AS series_id,
                f.SERIES_NAME AS series_name,
                f.SERIES_LEI AS series_lei,
                try_cast(f.TOTAL_ASSETS AS DECIMAL(38, 6)) AS total_assets,
                try_cast(f.NET_ASSETS AS DECIMAL(38, 6)) AS net_assets,
                s.source_dataset_id
            FROM bronze_nport_submission s
            LEFT JOIN bronze_nport_registrant r USING (ACCESSION_NUMBER, source_dataset_id)
            LEFT JOIN bronze_nport_fund_reported_info f
              USING (ACCESSION_NUMBER, source_dataset_id)
            """,
        ),
        "silver_nport_holdings": (
            {"bronze_nport_fund_reported_holding"},
            """
            SELECT
                ACCESSION_NUMBER AS accession_number,
                HOLDING_ID AS holding_id,
                ISSUER_NAME AS issuer_name,
                ISSUER_LEI AS issuer_lei,
                ISSUER_TITLE AS security_title,
                ISSUER_CUSIP AS cusip,
                try_cast(BALANCE AS DECIMAL(38, 6)) AS balance,
                UNIT AS balance_unit,
                CURRENCY_CODE AS currency_code,
                try_cast(CURRENCY_VALUE AS DECIMAL(38, 6)) AS value_usd,
                try_cast(PERCENTAGE AS DECIMAL(38, 10)) AS portfolio_weight_pct,
                ASSET_CAT AS asset_category,
                ISSUER_TYPE AS issuer_type,
                INVESTMENT_COUNTRY AS investment_country,
                IS_RESTRICTED_SECURITY AS is_restricted,
                FAIR_VALUE_LEVEL AS fair_value_level,
                DERIVATIVE_CAT AS derivative_category,
                source_dataset_id
            FROM bronze_nport_fund_reported_holding
            """,
        ),
        "silver_nport_identifiers": (
            {"bronze_nport_identifiers"},
            """
            SELECT
                HOLDING_ID AS holding_id,
                IDENTIFIER_ISIN AS isin,
                IDENTIFIER_TICKER AS ticker,
                OTHER_IDENTIFIER AS other_identifier,
                OTHER_IDENTIFIER_DESC AS other_identifier_type,
                source_dataset_id
            FROM bronze_nport_identifiers
            """,
        ),
        "silver_nmfp_filings": (
            {"bronze_nmfp_nmfp_submission"},
            """
            SELECT
                ACCESSION_NUMBER AS accession_number,
                coalesce(
                    try_strptime(FILING_DATE, '%d-%b-%Y')::DATE,
                    try_cast(FILING_DATE AS DATE)
                ) AS filing_date,
                coalesce(
                    try_strptime(REPORTDATE, '%d-%b-%Y')::DATE,
                    try_cast(REPORTDATE AS DATE)
                ) AS report_date,
                SUBMISSIONTYPE AS form,
                lpad(coalesce(FILER_CIK, CIK), 10, '0') AS registrant_cik,
                coalesce(REGISTRANTFULLNAME, REGISTRANT) AS registrant_name,
                SERIESID AS series_id,
                coalesce(NAMEOFSERIES, SERIES_NAME) AS series_name,
                LEIOFSERIES AS series_lei,
                source_dataset_id
            FROM bronze_nmfp_nmfp_submission
            """,
        ),
        "silver_nmfp_securities": (
            {"bronze_nmfp_nmfp_schportfoliosecurities"},
            """
            SELECT
                ACCESSION_NUMBER AS accession_number,
                SECURITY_ID AS security_id,
                NAMEOFISSUER AS issuer_name,
                TITLEOFISSUER AS security_title,
                CUSIP_NUMBER AS cusip,
                LEI AS issuer_lei,
                ISIN AS isin,
                INVESTMENTCATEGORY AS investment_category,
                BRIEFDESCRIPTION AS description,
                try_cast(PERCENTAGEOFMONEYMARKETFUNDNET AS DECIMAL(38, 10))
                    AS fund_net_assets_pct,
                source_dataset_id
            FROM bronze_nmfp_nmfp_schportfoliosecurities
            """,
        ),
    }
    for view_name, (dependencies, query) in analysis_views.items():
        if dependencies.issubset(available):
            connection.execute(
                f"CREATE OR REPLACE VIEW {_identifier(view_name)} AS {query}"
            )
            views.append(view_name)
    return views


def refresh_bulk_integrity_metadata(
    connection: duckdb.DuckDBPyConnection,
) -> dict:
    updated = 0
    rows = connection.execute(
        """
        SELECT family, dataset_id, table_name, output_path
        FROM bulk_dataset_files
        WHERE status = 'IMPORTED'
        ORDER BY family, dataset_id, table_name
        """
    ).fetchall()
    for family, dataset_id, table_name, output_path in rows:
        path = Path(output_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        connection.execute(
            """
            UPDATE bulk_dataset_files
            SET parquet_sha256 = ?, parquet_bytes = ?
            WHERE family = ? AND dataset_id = ? AND table_name = ?
            """,
            [
                _sha256(path),
                path.stat().st_size,
                family,
                dataset_id,
                table_name,
            ],
        )
        updated += 1
    return {"updated_files": updated}
