from __future__ import annotations

import hashlib

import duckdb


QUALITY_CHECKS = (
    (
        "ERROR",
        "MISSING_COVER_PAGE",
        """
        SELECT
            s.dataset_id,
            s.accession_number,
            'Submission has no imported cover page'
        FROM submissions s
        LEFT JOIN cover_pages cp USING (accession_number)
        WHERE cp.accession_number IS NULL
        """,
    ),
    (
        "ERROR",
        "MISSING_SIGNATURE",
        """
        SELECT
            s.dataset_id,
            s.accession_number,
            'Submission has no imported signature record'
        FROM submissions s
        LEFT JOIN signatures sig USING (accession_number)
        WHERE sig.accession_number IS NULL
        """,
    ),
    (
        "WARNING",
        "SOURCE_HOLDINGS_REPORT_WITHOUT_SUMMARY",
        """
        SELECT
            s.dataset_id,
            s.accession_number,
            '13F holdings report has no imported summary page'
        FROM submissions s
        LEFT JOIN summary_pages sp USING (accession_number)
        WHERE s.submission_type IN ('13F-HR', '13F-HR/A')
          AND sp.accession_number IS NULL
        """,
    ),
    (
        "WARNING",
        "SOURCE_MISSING_REQUIRED_HOLDING_FIELD",
        """
        SELECT
            s.dataset_id,
            h.accession_number,
            'Holding row has a missing issuer, CUSIP, value, or share amount'
        FROM holdings h
        JOIN submissions s USING (accession_number)
        WHERE h.name_of_issuer IS NULL
           OR h.cusip IS NULL
           OR h.value_usd IS NULL
           OR h.shares_or_principal IS NULL
        GROUP BY ALL
        """,
    ),
    (
        "ERROR",
        "NEGATIVE_HOLDING_VALUE",
        """
        SELECT
            s.dataset_id,
            h.accession_number,
            'Holding row has a negative reported value or share amount'
        FROM holdings h
        JOIN submissions s USING (accession_number)
        WHERE h.value_usd < 0 OR h.shares_or_principal < 0
        GROUP BY ALL
        """,
    ),
    (
        "WARNING",
        "SOURCE_HOLDINGS_REPORT_WITHOUT_ROWS",
        """
        SELECT
            s.dataset_id,
            s.accession_number,
            '13F holdings report has no information-table rows'
        FROM submissions s
        LEFT JOIN holdings h USING (accession_number)
        WHERE s.submission_type IN ('13F-HR', '13F-HR/A')
        GROUP BY s.dataset_id, s.accession_number
        HAVING count(h.infotable_sk) = 0
        """,
    ),
    (
        "WARNING",
        "SOURCE_SUMMARY_ENTRY_COUNT_MISMATCH",
        """
        SELECT
            s.dataset_id,
            s.accession_number,
            'Summary table-entry total does not equal imported information-table rows'
        FROM submissions s
        JOIN summary_pages sp USING (accession_number)
        LEFT JOIN holdings h USING (accession_number)
        WHERE s.submission_type IN ('13F-HR', '13F-HR/A')
        GROUP BY s.dataset_id, s.accession_number, sp.table_entry_total
        HAVING sp.table_entry_total IS NOT NULL
           AND sp.table_entry_total <> count(h.infotable_sk)
        """,
    ),
    (
        "WARNING",
        "SOURCE_SUMMARY_VALUE_MISMATCH",
        """
        SELECT
            s.dataset_id,
            s.accession_number,
            'Summary table value does not equal the sum of imported holding values'
        FROM submissions s
        JOIN summary_pages sp USING (accession_number)
        LEFT JOIN holdings h USING (accession_number)
        WHERE s.submission_type IN ('13F-HR', '13F-HR/A')
        GROUP BY s.dataset_id, s.accession_number, sp.table_value_usd
        HAVING sp.table_value_usd IS NOT NULL
           AND sp.table_value_usd <> coalesce(sum(h.value_usd), 0)
        """,
    ),
    (
        "WARNING",
        "CONFIDENTIAL_HOLDINGS_OMITTED",
        """
        SELECT
            s.dataset_id,
            s.accession_number,
            'Filer reported that confidential holdings were omitted'
        FROM submissions s
        JOIN summary_pages sp USING (accession_number)
        WHERE sp.is_confidential_omitted = true
        """,
    ),
)


def _issue_id(dataset_id: str | None, accession_number: str | None, code: str) -> str:
    value = f"{dataset_id or ''}|{accession_number or ''}|{code}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_database(connection: duckdb.DuckDBPyConnection) -> dict:
    connection.execute("DELETE FROM data_quality_issues")
    inserted = 0
    for severity, code, query in QUALITY_CHECKS:
        for dataset_id, accession_number, message in connection.execute(query).fetchall():
            connection.execute(
                """
                INSERT INTO data_quality_issues (
                    issue_id, dataset_id, accession_number,
                    severity, issue_code, issue_message
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    _issue_id(dataset_id, accession_number, code),
                    dataset_id,
                    accession_number,
                    severity,
                    code,
                    message,
                ],
            )
            inserted += 1

    counts = dict(
        connection.execute(
            """
            SELECT severity, count(*)
            FROM data_quality_issues
            GROUP BY severity
            """
        ).fetchall()
    )
    connection.execute(
        """
        INSERT INTO validation_runs (error_count, warning_count)
        VALUES (?, ?)
        """,
        [counts.get("ERROR", 0), counts.get("WARNING", 0)],
    )
    return {
        "issues": inserted,
        "errors": counts.get("ERROR", 0),
        "warnings": counts.get("WARNING", 0),
    }


def coverage_summary(connection: duckdb.DuckDBPyConnection) -> dict:
    row = connection.execute(
        """
        SELECT
            count(DISTINCT dataset_id),
            count(*),
            count(DISTINCT cik),
            count(DISTINCT period_of_report),
            min(period_of_report),
            max(period_of_report)
        FROM submissions
        """
    ).fetchone()
    holdings_count = connection.execute("SELECT count(*) FROM holdings").fetchone()[0]
    non_imported_datasets = connection.execute(
        "SELECT count(*) FROM datasets WHERE status <> 'IMPORTED'"
    ).fetchone()[0]
    bulk_datasets = dict(
        connection.execute(
            """
            SELECT family, count(*)
            FROM bulk_datasets
            WHERE status = 'IMPORTED'
            GROUP BY family
            ORDER BY family
            """
        ).fetchall()
    )
    non_imported_bulk_datasets = connection.execute(
        "SELECT count(*) FROM bulk_datasets WHERE status <> 'IMPORTED'"
    ).fetchone()[0]
    unresolved_errors = connection.execute(
        "SELECT count(*) FROM data_quality_issues WHERE severity = 'ERROR'"
    ).fetchone()[0]
    artifact_status = dict(
        connection.execute(
            """
            SELECT status, count(*)
            FROM filing_artifacts
            GROUP BY status
            """
        ).fetchall()
    )
    filing_families = dict(
        connection.execute(
            """
            SELECT filing_family, count(*)
            FROM sec_filings
            GROUP BY filing_family
            ORDER BY filing_family
            """
        ).fetchall()
    )
    uncaptured_filings = connection.execute(
        """
        SELECT count(*)
        FROM sec_filings sf
        LEFT JOIN filing_artifacts fa USING (accession_number)
        WHERE fa.accession_number IS NULL
          AND NOT (
              sf.filing_family = 'institutional_holdings'
              AND sf.source_kind = 'SEC_13F_BULK'
          )
        """
    ).fetchone()[0]
    latest_validation = connection.execute(
        """
        SELECT validated_at, error_count, warning_count
        FROM validation_runs
        ORDER BY validated_at DESC
        LIMIT 1
        """
    ).fetchone()
    return {
        "datasets": row[0],
        "submissions": row[1],
        "managers": row[2],
        "report_periods": row[3],
        "first_report_period": row[4],
        "last_report_period": row[5],
        "holdings": holdings_count,
        "non_imported_datasets": non_imported_datasets,
        "bulk_datasets": bulk_datasets,
        "non_imported_bulk_datasets": non_imported_bulk_datasets,
        "quality_errors": unresolved_errors,
        "filing_catalog": filing_families,
        "detail_ingestion": artifact_status,
        "filings_without_details": uncaptured_filings,
        "latest_validation": (
            {
                "validated_at": latest_validation[0],
                "errors": latest_validation[1],
                "warnings": latest_validation[2],
            }
            if latest_validation
            else None
        ),
        "no_known_ingestion_errors": (
            non_imported_datasets == 0
            and non_imported_bulk_datasets == 0
            and unresolved_errors == 0
            and artifact_status.get("FAILED", 0) == 0
            and artifact_status.get("INGESTED_PARTIAL", 0) == 0
        ),
    }
