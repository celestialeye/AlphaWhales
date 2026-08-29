from __future__ import annotations

import gzip
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from .database import DEFAULT_DATABASE_PATH
from .screener import (
    DEFAULT_SNAPSHOT_PATH,
    compute_source_fingerprint,
    resolve_snapshot_path,
)

DETAIL_FAMILIES = (
    "beneficial_ownership",
    "planned_insider_sales",
    "fund_census",
    "fund_shareholder_reports",
    "proxy_voting",
)


def _sha256(path: Path, *, gzip_content: bool = False) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_count = 0
    opener = gzip.open if gzip_content else open
    with opener(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            byte_count += len(chunk)
    return digest.hexdigest(), byte_count


def _verify_raw_artifact(row: tuple) -> dict | None:
    accession_number, raw_path, expected_hash, expected_bytes = row
    path = Path(raw_path)
    if not path.is_file():
        return {
            "severity": "ERROR",
            "code": "RAW_FILE_MISSING",
            "item": accession_number,
            "message": str(path),
        }
    try:
        actual_hash, actual_bytes = _sha256(path, gzip_content=True)
    except (OSError, EOFError) as exc:
        return {
            "severity": "ERROR",
            "code": "RAW_FILE_UNREADABLE",
            "item": accession_number,
            "message": str(exc),
        }
    if actual_hash != expected_hash or actual_bytes != expected_bytes:
        return {
            "severity": "ERROR",
            "code": "RAW_FILE_HASH_MISMATCH",
            "item": accession_number,
            "message": (
                f"expected {expected_hash}/{expected_bytes}, "
                f"found {actual_hash}/{actual_bytes}"
            ),
        }
    return None


def run_integrity_audit(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    verify_hashes: bool = True,
    workers: int = 8,
) -> dict:
    database = Path(database_path).resolve()
    connection = duckdb.connect(str(database), read_only=True)
    issues = []
    checks = {}
    try:
        checks["database_path"] = str(database)
        checks["database_bytes"] = database.stat().st_size
        checks["13f_datasets"] = connection.execute(
            "SELECT count(*) FROM datasets WHERE status = 'IMPORTED'"
        ).fetchone()[0]
        dataset_counts = connection.execute(
            """
            SELECT
                d.dataset_id,
                d.submission_count,
                count(DISTINCT s.accession_number) AS actual_submissions,
                d.holdings_count,
                count(h.infotable_sk) AS actual_holdings
            FROM datasets d
            LEFT JOIN submissions s USING (dataset_id)
            LEFT JOIN holdings h USING (accession_number)
            WHERE d.status = 'IMPORTED'
            GROUP BY
                d.dataset_id,
                d.submission_count,
                d.holdings_count
            """
        ).fetchall()
        for dataset_id, expected_submissions, actual_submissions, expected_holdings, actual_holdings in dataset_counts:
            if (
                expected_submissions != actual_submissions
                or expected_holdings != actual_holdings
            ):
                issues.append(
                    {
                        "severity": "ERROR",
                        "code": "13F_MANIFEST_COUNT_MISMATCH",
                        "item": dataset_id,
                        "message": (
                            f"submissions {expected_submissions}/{actual_submissions}; "
                            f"holdings {expected_holdings}/{actual_holdings}"
                        ),
                    }
                )
        checks["bulk_datasets"] = dict(
            connection.execute(
                """
                SELECT family, count(*)
                FROM bulk_datasets
                WHERE status = 'IMPORTED'
                GROUP BY family
                """
            ).fetchall()
        )
        non_imported = connection.execute(
            """
            SELECT '13F', dataset_id, status, coalesce(last_error, '')
            FROM datasets WHERE status <> 'IMPORTED'
            UNION ALL
            SELECT family, dataset_id, status, coalesce(last_error, '')
            FROM bulk_datasets WHERE status <> 'IMPORTED'
            """
        ).fetchall()
        for family, dataset_id, status, error in non_imported:
            issues.append(
                {
                    "severity": "ERROR",
                    "code": "DATASET_NOT_IMPORTED",
                    "item": f"{family}/{dataset_id}",
                    "message": f"{status}: {error}",
                }
            )

        detail_rows = connection.execute(
            f"""
            SELECT
                sf.filing_family,
                count(*) AS cataloged,
                count(fa.accession_number) AS processed,
                count(*) FILTER (WHERE fa.status = 'FAILED') AS failed,
                count(*) FILTER (WHERE fa.status = 'INGESTED_PARTIAL') AS partial,
                count(*) FILTER (WHERE fa.status = 'RAW_ONLY') AS raw_only,
                count(*) FILTER (WHERE fa.status = 'SOURCE_UNAVAILABLE')
                    AS source_unavailable
            FROM sec_filings sf
            LEFT JOIN filing_artifacts fa USING (accession_number)
            WHERE sf.filing_family IN (
                {",".join("?" for _ in DETAIL_FAMILIES)}
            )
            GROUP BY sf.filing_family
            ORDER BY sf.filing_family
            """,
            DETAIL_FAMILIES,
        ).fetchall()
        checks["detail_families"] = {}
        for (
            family,
            cataloged,
            processed,
            failed,
            partial,
            raw_only,
            source_unavailable,
        ) in detail_rows:
            checks["detail_families"][family] = {
                "cataloged": cataloged,
                "processed": processed,
                "failed": failed,
                "partial": partial,
                "raw_only": raw_only,
                "source_unavailable": source_unavailable,
            }
        for (
            family,
            cataloged,
            processed,
            failed,
            partial,
            _,
            source_unavailable,
        ) in detail_rows:
            if processed != cataloged:
                issues.append(
                    {
                        "severity": "ERROR",
                        "code": "DETAIL_COVERAGE_GAP",
                        "item": family,
                        "message": f"{processed} of {cataloged} processed",
                    }
                )
            if failed:
                issues.append(
                    {
                        "severity": "ERROR",
                        "code": "DETAIL_FAILURES",
                        "item": family,
                        "message": f"{failed} failed artifacts",
                    }
                )
            if partial:
                issues.append(
                    {
                        "severity": "WARNING",
                        "code": "PARTIAL_TYPED_EXTRACTION",
                        "item": family,
                        "message": f"{partial} partial parser results",
                    }
                )
            if source_unavailable:
                issues.append(
                    {
                        "severity": "WARNING",
                        "code": "SEC_SOURCE_UNAVAILABLE",
                        "item": family,
                        "message": (
                            f"{source_unavailable} cataloged accessions "
                            "currently return HTTP 404"
                        ),
                    }
                )

        orphan_artifacts = connection.execute(
            """
            SELECT count(*)
            FROM filing_artifacts fa
            LEFT JOIN sec_filings sf USING (accession_number)
            WHERE sf.accession_number IS NULL
            """
        ).fetchone()[0]
        orphan_rows = connection.execute(
            """
            SELECT count(*)
            FROM filing_table_rows rows
            LEFT JOIN filing_artifacts fa USING (accession_number)
            WHERE fa.accession_number IS NULL
            """
        ).fetchone()[0]
        checks["orphan_artifacts"] = orphan_artifacts
        checks["orphan_table_rows"] = orphan_rows
        if orphan_artifacts or orphan_rows:
            issues.append(
                {
                    "severity": "ERROR",
                    "code": "ORPHAN_RECORDS",
                    "item": "filing artifacts",
                    "message": (
                        f"{orphan_artifacts} orphan artifacts, "
                        f"{orphan_rows} orphan table rows"
                    ),
                }
            )

        parquet_rows = connection.execute(
            """
            SELECT family, dataset_id, table_name, output_path, parquet_row_count
            FROM bulk_dataset_files
            WHERE status = 'IMPORTED'
            ORDER BY family, dataset_id, table_name
            """
        ).fetchall()
        missing_parquet = 0
        row_mismatches = 0
        for family, dataset_id, table_name, output_path, expected_rows in parquet_rows:
            path = Path(output_path)
            if not path.is_file():
                missing_parquet += 1
                issues.append(
                    {
                        "severity": "ERROR",
                        "code": "PARQUET_FILE_MISSING",
                        "item": f"{family}/{dataset_id}/{table_name}",
                        "message": str(path),
                    }
                )
                continue
            try:
                actual_rows = connection.execute(
                    f"""
                    SELECT count(*)
                    FROM read_parquet('{str(path).replace("'", "''")}')
                    """
                ).fetchone()[0]
            except (duckdb.Error, OSError) as exc:
                issues.append(
                    {
                        "severity": "ERROR",
                        "code": "PARQUET_UNREADABLE",
                        "item": f"{family}/{dataset_id}/{table_name}",
                        "message": str(exc),
                    }
                )
                continue
            if actual_rows != expected_rows:
                row_mismatches += 1
                issues.append(
                    {
                        "severity": "ERROR",
                        "code": "PARQUET_ROW_MISMATCH",
                        "item": f"{family}/{dataset_id}/{table_name}",
                        "message": f"expected {expected_rows}, found {actual_rows}",
                    }
                )
        checks["parquet_files"] = len(parquet_rows)
        checks["missing_parquet_files"] = missing_parquet
        checks["parquet_row_mismatches"] = row_mismatches

        if verify_hashes:
            parquet_hash_rows = connection.execute(
                """
                SELECT
                    family, dataset_id, table_name, output_path,
                    parquet_sha256, parquet_bytes
                FROM bulk_dataset_files
                WHERE status = 'IMPORTED'
                """
            ).fetchall()
            parquet_hash_failures = 0
            for family, dataset_id, table_name, output_path, expected_hash, expected_bytes in parquet_hash_rows:
                path = Path(output_path)
                try:
                    actual_hash, actual_bytes = _sha256(path)
                except OSError as exc:
                    parquet_hash_failures += 1
                    issues.append(
                        {
                            "severity": "ERROR",
                            "code": "PARQUET_HASH_UNREADABLE",
                            "item": f"{family}/{dataset_id}/{table_name}",
                            "message": str(exc),
                        }
                    )
                    continue
                if (
                    not expected_hash
                    or expected_bytes is None
                    or actual_hash != expected_hash
                    or actual_bytes != expected_bytes
                ):
                    parquet_hash_failures += 1
                    issues.append(
                        {
                            "severity": "ERROR",
                            "code": "PARQUET_HASH_MISMATCH",
                            "item": f"{family}/{dataset_id}/{table_name}",
                            "message": (
                                f"expected {expected_hash}/{expected_bytes}, "
                                f"found {actual_hash}/{actual_bytes}"
                            ),
                        }
                    )
            checks["parquet_hash_failures"] = parquet_hash_failures

            metadata_rows = connection.execute(
                """
                SELECT
                    family, dataset_id, source_member, output_path,
                    source_sha256, byte_count
                FROM bulk_dataset_metadata
                """
            ).fetchall()
            metadata_failures = 0
            for family, dataset_id, source_member, output_path, expected_hash, expected_bytes in metadata_rows:
                path = Path(output_path)
                try:
                    actual_hash, actual_bytes = _sha256(path)
                except OSError as exc:
                    metadata_failures += 1
                    issues.append(
                        {
                            "severity": "ERROR",
                            "code": "METADATA_UNREADABLE",
                            "item": f"{family}/{dataset_id}/{source_member}",
                            "message": str(exc),
                        }
                    )
                    continue
                if actual_hash != expected_hash or actual_bytes != expected_bytes:
                    metadata_failures += 1
                    issues.append(
                        {
                            "severity": "ERROR",
                            "code": "METADATA_HASH_MISMATCH",
                            "item": f"{family}/{dataset_id}/{source_member}",
                            "message": (
                                f"expected {expected_hash}/{expected_bytes}, "
                                f"found {actual_hash}/{actual_bytes}"
                            ),
                        }
                    )
            checks["metadata_files_verified"] = len(metadata_rows)
            checks["metadata_hash_failures"] = metadata_failures

            archive_rows = connection.execute(
                """
                SELECT dataset_id, local_path, source_sha256
                FROM datasets
                WHERE status = 'IMPORTED'
                UNION ALL
                SELECT
                    family || '/' || dataset_id,
                    local_archive_path,
                    source_sha256
                FROM bulk_datasets
                WHERE status = 'IMPORTED' AND NOT archive_deleted
                """
            ).fetchall()
            archive_failures = 0
            for item, archive_path, expected_hash in archive_rows:
                path = Path(archive_path)
                if not path.is_file():
                    archive_failures += 1
                    issues.append(
                        {
                            "severity": "ERROR",
                            "code": "ARCHIVE_FILE_MISSING",
                            "item": item,
                            "message": str(path),
                        }
                    )
                    continue
                try:
                    actual_hash, _ = _sha256(path)
                except OSError as exc:
                    archive_failures += 1
                    issues.append(
                        {
                            "severity": "ERROR",
                            "code": "ARCHIVE_UNREADABLE",
                            "item": item,
                            "message": str(exc),
                        }
                    )
                    continue
                if actual_hash != expected_hash:
                    archive_failures += 1
                    issues.append(
                        {
                            "severity": "ERROR",
                            "code": "ARCHIVE_HASH_MISMATCH",
                            "item": item,
                            "message": f"expected {expected_hash}, found {actual_hash}",
                        }
                    )
            checks["archive_files_verified"] = len(archive_rows)
            checks["archive_hash_failures"] = archive_failures

            raw_rows = connection.execute(
                """
                SELECT
                    accession_number,
                    raw_submission_path,
                    raw_submission_sha256,
                    raw_submission_bytes
                FROM filing_artifacts
                WHERE raw_submission_path IS NOT NULL
                ORDER BY accession_number
                """
            ).fetchall()
            with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
                for result in executor.map(_verify_raw_artifact, raw_rows):
                    if result:
                        issues.append(result)
            checks["raw_files_verified"] = len(raw_rows)

        npx_vote_rows = connection.execute(
            """
            SELECT
                report_year, output_path, source_filing_count, vote_count,
                parquet_sha256, parquet_bytes, status
            FROM npx_vote_files
            ORDER BY report_year
            """
        ).fetchall()
        checks["npx_vote_years"] = len(npx_vote_rows)
        for year, output_path, source_filings, expected_votes, expected_hash, expected_bytes, status in npx_vote_rows:
            if status != "IMPORTED":
                issues.append(
                    {
                        "severity": "ERROR",
                        "code": "NPX_VOTE_FILE_NOT_IMPORTED",
                        "item": str(year),
                        "message": status,
                    }
                )
                continue
            current_source = connection.execute(
                """
                SELECT
                    count(*),
                    coalesce(
                        sum(
                            try_cast(
                                json_extract_string(
                                    fa.extractor_manifest,
                                    '$.proxy_votes_source'
                                ) AS BIGINT
                            )
                        ),
                        0
                    )
                FROM sec_filings sf
                JOIN filing_artifacts fa USING (accession_number)
                WHERE sf.filing_family = 'proxy_voting'
                  AND year(sf.filing_date) = ?
                  AND fa.raw_submission_path IS NOT NULL
                """,
                [year],
            ).fetchone()
            path = Path(output_path)
            try:
                actual_votes = connection.execute(
                    f"""
                    SELECT count(*)
                    FROM read_parquet('{str(path).replace("'", "''")}')
                    """
                ).fetchone()[0]
                actual_hash, actual_bytes = _sha256(path)
            except (duckdb.Error, OSError) as exc:
                issues.append(
                    {
                        "severity": "ERROR",
                        "code": "NPX_VOTE_FILE_UNREADABLE",
                        "item": str(year),
                        "message": str(exc),
                    }
                )
                continue
            if (
                current_source[0] != source_filings
                or current_source[1] != expected_votes
                or
                actual_votes != expected_votes
                or actual_hash != expected_hash
                or actual_bytes != expected_bytes
            ):
                issues.append(
                    {
                        "severity": "ERROR",
                        "code": "NPX_VOTE_FILE_MISMATCH",
                        "item": str(year),
                        "message": (
                            f"filings={source_filings}, "
                            f"current_filings={current_source[0]}, "
                            f"source_votes={current_source[1]}, "
                            f"votes={expected_votes}/{actual_votes}, "
                            f"bytes={expected_bytes}/{actual_bytes}"
                        ),
                    }
                )

        pointer = Path(snapshot_path).resolve()
        snapshot = None
        try:
            snapshot = resolve_snapshot_path(pointer)
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            issues.append(
                {
                    "severity": "ERROR",
                    "code": "SCREENING_POINTER_UNREADABLE",
                    "item": "screening snapshot",
                    "message": str(exc),
                }
            )
        checks["screening_snapshot_path"] = str(snapshot)
        if snapshot is None or not snapshot.is_file():
            issues.append(
                {
                    "severity": "ERROR",
                    "code": "SCREENING_SNAPSHOT_MISSING",
                    "item": "screening snapshot",
                    "message": str(snapshot),
                }
            )
        else:
            try:
                screening = duckdb.connect(str(snapshot), read_only=True)
                checks["screening_managers"] = screening.execute(
                    "SELECT count(*) FROM manager_metrics"
                ).fetchone()[0]
                checks["screening_default_candidates"] = screening.execute(
                    """
                    SELECT count(*)
                    FROM manager_metrics
                    WHERE median_reported_value_4q >= 10000000000
                      AND direct_stock_pct >= 80
                      AND top10_pct >= 40
                      AND concentration_pass_quarters >= 6
                      AND annualized_turnover_pct <= 100
                    """
                ).fetchone()[0]
                metadata = screening.execute(
                    """
                    SELECT source_fingerprint
                    FROM snapshot_metadata
                    LIMIT 1
                    """
                ).fetchone()
                current_fingerprint = compute_source_fingerprint(connection)
                if not metadata or metadata[0] != current_fingerprint:
                    issues.append(
                        {
                            "severity": "ERROR",
                            "code": "SCREENING_SNAPSHOT_STALE",
                            "item": str(snapshot),
                            "message": "Source manifest fingerprint does not match",
                        }
                    )
            except (duckdb.Error, OSError) as exc:
                issues.append(
                    {
                        "severity": "ERROR",
                        "code": "SCREENING_SNAPSHOT_UNREADABLE",
                        "item": str(snapshot),
                        "message": str(exc),
                    }
                )
            finally:
                if "screening" in locals():
                    screening.close()
    finally:
        connection.close()

    error_count = sum(1 for issue in issues if issue["severity"] == "ERROR")
    warning_count = sum(1 for issue in issues if issue["severity"] == "WARNING")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "complete": error_count == 0,
        "errors": error_count,
        "warnings": warning_count,
        "checks": checks,
        "issues": issues,
    }


def write_integrity_report(report: dict, output_path: str | Path) -> Path:
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
