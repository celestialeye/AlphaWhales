from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from config import FILING_LEDGER_PATH, FILING_PUBLICATION_PATH, ROSTER_PATH
from roster_store import load_roster

logger = logging.getLogger(__name__)


class FilingOperationBusyError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _operation_file_lock(database_path: Path):
    lock_path = database_path.with_name(".daily-filings.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stream = lock_path.open("a+b")
    try:
        stream.seek(0)
        if stream.read(1) == b"":
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(
                    stream.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
        except OSError as exc:
            raise FilingOperationBusyError(
                "A daily SEC filing check is already running"
            ) from exc
        stream.seek(0)
        stream.write(
            (
                f"pid={os.getpid()} started_at={_utc_now()}"
            ).encode("utf-8")
        )
        stream.truncate()
        stream.flush()
        yield
    finally:
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        stream.close()


class FilingLedger:
    def __init__(self, path=FILING_LEDGER_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ingestion_runs (
                    run_id TEXT PRIMARY KEY,
                    trigger TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    roster_fingerprint TEXT NOT NULL,
                    managers_checked INTEGER NOT NULL DEFAULT 0,
                    filings_seen INTEGER NOT NULL DEFAULT 0,
                    baseline_filings INTEGER NOT NULL DEFAULT 0,
                    new_filings INTEGER NOT NULL DEFAULT 0,
                    published_filings INTEGER NOT NULL DEFAULT 0,
                    recorded_filings INTEGER NOT NULL DEFAULT 0,
                    failed_filings INTEGER NOT NULL DEFAULT 0,
                    refreshed_managers INTEGER NOT NULL DEFAULT 0,
                    invalidated_periods INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS filings (
                    accession_number TEXT PRIMARY KEY,
                    canonical_cik TEXT NOT NULL,
                    source_cik TEXT NOT NULL,
                    manager_name TEXT NOT NULL,
                    form TEXT NOT NULL,
                    filing_date TEXT NOT NULL,
                    report_period TEXT,
                    source_url TEXT,
                    first_seen_run_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cache_published_at TEXT,
                    last_error TEXT,
                    FOREIGN KEY (first_seen_run_id)
                        REFERENCES ingestion_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS run_filings (
                    run_id TEXT NOT NULL,
                    accession_number TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    PRIMARY KEY (run_id, accession_number),
                    FOREIGN KEY (run_id)
                        REFERENCES ingestion_runs(run_id),
                    FOREIGN KEY (accession_number)
                        REFERENCES filings(accession_number)
                );

                CREATE TABLE IF NOT EXISTS run_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    canonical_cik TEXT,
                    source_cik TEXT,
                    manager_name TEXT,
                    error TEXT NOT NULL,
                    FOREIGN KEY (run_id)
                        REFERENCES ingestion_runs(run_id)
                );

                CREATE INDEX IF NOT EXISTS idx_filings_filing_date
                    ON filings(filing_date DESC, accession_number DESC);
                CREATE INDEX IF NOT EXISTS idx_filings_manager
                    ON filings(canonical_cik, filing_date DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_started_at
                    ON ingestion_runs(started_at DESC);
                """
            )

    def has_initialized_operational_ledger(self) -> bool:
        with self._connection() as connection:
            return bool(
                connection.execute(
                    """
                    SELECT 1
                    FROM ingestion_runs
                    WHERE trigger <> 'history_backfill'
                      AND (
                          baseline_filings > 0
                          OR status IN (
                              'COMPLETE', 'PARTIAL', 'NO_CHANGES'
                          )
                      )
                    LIMIT 1
                    """
                ).fetchone()
            )

    def start_run(self, trigger: str, roster_fingerprint: str) -> str:
        run_id = uuid.uuid4().hex
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO ingestion_runs (
                    run_id, trigger, status, started_at,
                    roster_fingerprint
                ) VALUES (?, ?, 'RUNNING', ?, ?)
                """,
                [run_id, trigger, _utc_now(), roster_fingerprint],
            )
        return run_id

    def get_filing(self, accession_number: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    accession_number, canonical_cik, source_cik,
                    manager_name, form, filing_date, report_period,
                    source_url, first_seen_at, status,
                    cache_published_at, last_error
                FROM filings
                WHERE accession_number = ?
                """,
                [accession_number],
            ).fetchone()
        return dict(row) if row else None

    def record_discoveries(
        self,
        run_id: str,
        discoveries: list[dict],
        errors: list[dict],
        *,
        baseline: bool,
    ) -> dict:
        work_items = []
        new_count = 0
        now = _utc_now()
        with self._connection() as connection:
            for error in errors:
                connection.execute(
                    """
                    INSERT INTO run_errors (
                        run_id, canonical_cik, source_cik,
                        manager_name, error
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        run_id,
                        error.get("canonical_cik"),
                        error.get("source_cik"),
                        error.get("manager_name"),
                        error.get("error") or "Unknown discovery error",
                    ],
                )
            for discovery in discoveries:
                accession_number = discovery.get("accession_number")
                if not accession_number:
                    continue
                existing = connection.execute(
                    """
                    SELECT status
                    FROM filings
                    WHERE accession_number = ?
                    """,
                    [accession_number],
                ).fetchone()
                if existing:
                    if existing["status"] not in {
                        "DISCOVERED",
                        "FAILED",
                        "HISTORICAL",
                    }:
                        continue
                    connection.execute(
                        """
                        INSERT INTO run_filings (
                            run_id, accession_number, outcome
                        ) VALUES (?, ?, ?)
                        ON CONFLICT (run_id, accession_number)
                        DO UPDATE SET outcome = excluded.outcome
                        """,
                        [
                            run_id,
                            accession_number,
                            "BASELINE" if baseline else "RETRY",
                        ],
                    )
                    work_items.append({
                        **discovery,
                        "_is_new": False,
                    })
                    continue
                status = "DISCOVERED"
                connection.execute(
                    """
                    INSERT INTO filings (
                        accession_number, canonical_cik, source_cik,
                        manager_name, form, filing_date, report_period,
                        source_url, first_seen_run_id, first_seen_at,
                        status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        accession_number,
                        discovery["canonical_cik"],
                        discovery["source_cik"],
                        discovery["manager_name"],
                        discovery["form"],
                        discovery["filing_date"],
                        discovery.get("report_period"),
                        discovery.get("source_url"),
                        run_id,
                        now,
                        status,
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO run_filings (
                        run_id, accession_number, outcome
                    ) VALUES (?, ?, ?)
                    """,
                    [
                        run_id,
                        accession_number,
                        "BASELINE" if baseline else status,
                    ],
                )
                work_items.append({
                    **discovery,
                    "_is_new": not baseline,
                })
                if not baseline:
                    new_count += 1
            connection.execute(
                """
                UPDATE ingestion_runs
                SET filings_seen = ?,
                    baseline_filings = ?,
                    new_filings = ?,
                    error_count = ?
                WHERE run_id = ?
                """,
                [
                    len(discoveries),
                    len(work_items) if baseline else 0,
                    new_count,
                    len(errors),
                    run_id,
                ],
            )
        return {
            "work_items": work_items,
            "new_count": new_count,
            "baseline_count": len(work_items) if baseline else 0,
        }

    def recover_interrupted_runs(self):
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE ingestion_runs
                SET status = 'FAILED',
                    completed_at = ?,
                    error = COALESCE(
                        error,
                        'Operation process ended before completion'
                    )
                WHERE status = 'RUNNING'
                """,
                [_utc_now()],
            )

    def record_outcomes(self, run_id: str, outcomes: list[dict]):
        now = _utc_now()
        with self._connection() as connection:
            for outcome in outcomes:
                connection.execute(
                    """
                    UPDATE filings
                    SET status = ?,
                        cache_published_at = ?,
                        last_error = ?
                    WHERE accession_number = ?
                    """,
                    [
                        outcome["status"],
                        (
                            now
                            if outcome["status"] == "PUBLISHED"
                            else None
                        ),
                        outcome.get("error"),
                        outcome["accession_number"],
                    ],
                )
                connection.execute(
                    """
                    UPDATE run_filings
                    SET outcome = ?
                    WHERE run_id = ?
                      AND accession_number = ?
                    """,
                    [
                        outcome["status"],
                        run_id,
                        outcome["accession_number"],
                    ],
                )

    def complete_run(
        self,
        run_id: str,
        *,
        status: str,
        managers_checked: int,
        refreshed_managers: int,
        invalidated_periods: int,
        published_filings: int,
        recorded_filings: int,
        failed_filings: int,
        error: str | None = None,
    ):
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE ingestion_runs
                SET status = ?,
                    completed_at = ?,
                    managers_checked = ?,
                    refreshed_managers = ?,
                    invalidated_periods = ?,
                    published_filings = ?,
                    recorded_filings = ?,
                    failed_filings = ?,
                    error = ?
                WHERE run_id = ?
                """,
                [
                    status,
                    _utc_now(),
                    managers_checked,
                    refreshed_managers,
                    invalidated_periods,
                    published_filings,
                    recorded_filings,
                    failed_filings,
                    error,
                    run_id,
                ],
            )

    def record_historical_inventory(
        self,
        discoveries: list[dict],
        errors: list[dict],
        *,
        roster_fingerprint: str,
        managers_checked: int,
    ) -> dict:
        candidates = {
            item["accession_number"]: item
            for item in discoveries
            if item.get("accession_number")
        }
        now = _utc_now()
        with self._connection() as connection:
            existing = {
                row[0]
                for row in connection.execute(
                    "SELECT accession_number FROM filings"
                ).fetchall()
            }
            accessions = list(candidates)
            pending = [
                candidates[accession]
                for accession in accessions
                if accession not in existing
            ]
            if not pending and not errors:
                return {
                    "run_id": None,
                    "status": "NO_CHANGES",
                    "candidate_filings": len(candidates),
                    "inserted_filings": 0,
                    "existing_filings": len(candidates),
                    "error_count": 0,
                }

            run_id = uuid.uuid4().hex
            status = "PARTIAL" if errors else "COMPLETE"
            connection.execute(
                """
                INSERT INTO ingestion_runs (
                    run_id, trigger, status, started_at, completed_at,
                    roster_fingerprint, managers_checked, filings_seen,
                    baseline_filings, new_filings, published_filings,
                    recorded_filings, failed_filings, refreshed_managers,
                    invalidated_periods, error_count
                ) VALUES (
                    ?, 'history_backfill', ?, ?, ?, ?, ?, ?, 0, 0, 0,
                    ?, 0, 0, 0, ?
                )
                """,
                [
                    run_id,
                    status,
                    now,
                    now,
                    roster_fingerprint,
                    managers_checked,
                    len(candidates),
                    len(pending),
                    len(errors),
                ],
            )
            for error in errors:
                connection.execute(
                    """
                    INSERT INTO run_errors (
                        run_id, canonical_cik, source_cik,
                        manager_name, error
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        run_id,
                        error.get("canonical_cik"),
                        error.get("source_cik"),
                        error.get("manager_name"),
                        error.get("error") or "Unknown history backfill error",
                    ],
                )
            for discovery in pending:
                connection.execute(
                    """
                    INSERT INTO filings (
                        accession_number, canonical_cik, source_cik,
                        manager_name, form, filing_date, report_period,
                        source_url, first_seen_run_id, first_seen_at,
                        status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'HISTORICAL')
                    """,
                    [
                        discovery["accession_number"],
                        discovery["canonical_cik"],
                        discovery["source_cik"],
                        discovery["manager_name"],
                        discovery["form"],
                        discovery["filing_date"],
                        discovery.get("report_period"),
                        discovery.get("source_url"),
                        run_id,
                        now,
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO run_filings (
                        run_id, accession_number, outcome
                    ) VALUES (?, ?, 'HISTORICAL')
                    """,
                    [run_id, discovery["accession_number"]],
                )
        return {
            "run_id": run_id,
            "status": status,
            "candidate_filings": len(candidates),
            "inserted_filings": len(pending),
            "existing_filings": len(candidates) - len(pending),
            "error_count": len(errors),
        }

    def get_dashboard(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
        search: str | None = None,
        form: str | None = None,
        report_period: str | None = None,
    ) -> dict:
        conditions = []
        parameters = []
        if status:
            conditions.append("status = ?")
            parameters.append(status.upper())
        if form:
            conditions.append("form = ?")
            parameters.append(form.upper())
        if report_period:
            conditions.append("report_period = ?")
            parameters.append(report_period)
        if search:
            conditions.append(
                """
                (
                    manager_name LIKE ?
                    OR canonical_cik LIKE ?
                    OR source_cik LIKE ?
                    OR accession_number LIKE ?
                )
                """
            )
            pattern = f"%{search.strip()}%"
            parameters.extend([pattern, pattern, pattern, pattern])
        where_clause = (
            "WHERE " + " AND ".join(conditions)
            if conditions
            else ""
        )
        with self._connection() as connection:
            filings = [
                dict(row)
                for row in connection.execute(
                    f"""
                    SELECT
                        accession_number, canonical_cik, source_cik,
                        manager_name, form, filing_date, report_period,
                        source_url, first_seen_at, status,
                        cache_published_at, last_error
                    FROM filings
                    {where_clause}
                    ORDER BY filing_date DESC, accession_number DESC
                    LIMIT ? OFFSET ?
                    """,
                    [*parameters, limit, offset],
                ).fetchall()
            ]
            total = connection.execute(
                f"""
                SELECT count(*)
                FROM filings
                {where_clause}
                """,
                parameters,
            ).fetchone()[0]
            runs = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT *
                    FROM ingestion_runs
                    WHERE trigger <> 'history_backfill'
                    ORDER BY started_at DESC
                    LIMIT 20
                    """
                ).fetchall()
            ]
            summary_row = connection.execute(
                """
                WITH classified AS (
                    SELECT
                        filings.status,
                        filings.first_seen_at,
                        filings.report_period,
                        first_run.trigger,
                        CASE
                            WHEN COALESCE(first_run.baseline_filings, 0) > 0
                                THEN 1
                            ELSE 0
                        END AS is_baseline_origin
                    FROM filings
                    LEFT JOIN ingestion_runs AS first_run
                        ON first_run.run_id = filings.first_seen_run_id
                )
                SELECT
                    count(*) AS total_filings,
                    count(*) AS known_accessions,
                    count(*) FILTER (
                        WHERE is_baseline_origin = 1
                    ) AS baseline_accessions,
                    count(*) FILTER (
                        WHERE is_baseline_origin = 0
                          AND trigger <> 'history_backfill'
                    ) AS new_accessions,
                    count(*) FILTER (
                        WHERE trigger = 'history_backfill'
                    ) AS historical_accessions,
                    count(*) FILTER (
                        WHERE trigger <> 'history_backfill'
                    ) AS operational_accessions,
                    count(DISTINCT report_period) AS report_period_count,
                    count(*) FILTER (WHERE status = 'PUBLISHED')
                        AS published_filings,
                    count(*) FILTER (WHERE status = 'RECORDED')
                        AS recorded_filings,
                    count(*) FILTER (WHERE status = 'BASELINED')
                        AS baselined_filings,
                    count(*) FILTER (WHERE status = 'FAILED')
                        AS failed_filings,
                    count(*) FILTER (WHERE status = 'DISCOVERED')
                        AS discovered_filings,
                    count(*) FILTER (
                        WHERE status IN ('DISCOVERED', 'FAILED')
                    ) AS retry_queue,
                    max(first_seen_at) AS last_discovered_at
                FROM classified
                """
            ).fetchone()
            filter_options = {
                "forms": [
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT DISTINCT form
                        FROM filings
                        WHERE form IS NOT NULL AND form <> ''
                        ORDER BY form
                        """
                    ).fetchall()
                ],
                "report_periods": [
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT DISTINCT report_period
                        FROM filings
                        WHERE report_period IS NOT NULL
                          AND report_period <> ''
                        ORDER BY report_period DESC
                        """
                    ).fetchall()
                ],
            }
        return {
            "summary": dict(summary_row),
            "runs": runs,
            "filings": filings,
            "filter_options": filter_options,
            "total": total,
            "limit": limit,
            "offset": offset,
        }


class FilingOperations:
    def __init__(
        self,
        data_service,
        *,
        ledger_path=FILING_LEDGER_PATH,
        publication_path=FILING_PUBLICATION_PATH,
        historical_database_path=None,
        after_refresh=None,
    ):
        self.data_service = data_service
        self.ledger = FilingLedger(ledger_path)
        self.publication_path = Path(publication_path)
        if historical_database_path is None:
            from investor_screening.database import DEFAULT_DATABASE_PATH

            historical_database_path = DEFAULT_DATABASE_PATH
        self.historical_database_path = Path(historical_database_path)
        self.after_refresh = after_refresh
        self._run_lock = asyncio.Lock()
        self._last_publication_run_id = self._read_publication_run_id()

    async def run(self, *, trigger="scheduler", lookback_days=120):
        if self._run_lock.locked():
            raise FilingOperationBusyError(
                "A daily SEC filing check is already running"
            )
        async with self._run_lock:
            with _operation_file_lock(self.ledger.path):
                self.ledger.recover_interrupted_runs()
                return await self._run_locked(
                    trigger=trigger,
                    lookback_days=lookback_days,
                )

    async def backfill_history(self, *, quarters=None, database_path=None):
        if self._run_lock.locked():
            raise FilingOperationBusyError(
                "A filing operation is already running"
            )
        async with self._run_lock:
            with _operation_file_lock(self.ledger.path):
                self.ledger.recover_interrupted_runs()
                if database_path is None:
                    from investor_screening.database import (
                        DEFAULT_DATABASE_PATH,
                    )

                    database_path = DEFAULT_DATABASE_PATH
                roster = load_roster(ROSTER_PATH)
                loop = asyncio.get_running_loop()
                inventory = await loop.run_in_executor(
                    None,
                    self._load_historical_inventory,
                    Path(database_path),
                    (
                        max(1, int(quarters))
                        if quarters is not None
                        else None
                    ),
                    roster,
                )
                result = self.ledger.record_historical_inventory(
                    inventory["filings"],
                    inventory["errors"],
                    roster_fingerprint=self.data_service._roster_fingerprint(
                        roster
                    ),
                    managers_checked=len(roster),
                )
                return {
                    **result,
                    "quarters_requested": len(
                        inventory["report_periods"]
                    ),
                    "quarters_found": len(inventory["periods_found"]),
                    "report_periods": inventory["report_periods"],
                }

    def get_filing_detail(self, accession_number: str) -> dict | None:
        filing = self.ledger.get_filing(accession_number)
        if filing is None:
            return None

        archive_detail = self._load_archive_filing_detail(
            accession_number
        )
        if archive_detail is not None:
            return {
                **filing,
                **archive_detail,
                "detail_source": "Official SEC archive",
            }

        cache_detail = self._load_current_cache_filing_detail(filing)
        if cache_detail is not None:
            return {
                **filing,
                **cache_detail,
                "detail_source": "Current normalized manager cache",
            }

        return {
            **filing,
            "detail_source": "Ledger metadata",
            "holdings_available": False,
            "availability_note": (
                "Detailed holdings are not yet available in the local "
                "archive or current manager cache."
            ),
            "summary": {
                "is_amendment": str(filing["form"]).endswith("/A"),
                "amendment_number": None,
                "amendment_type": None,
                "report_type": None,
                "additional_information": None,
                "other_included_managers_count": None,
                "holding_count": None,
                "total_value_usd": None,
                "is_confidential_omitted": None,
                "signer_name": None,
                "signer_title": None,
                "signature_date": None,
                "put_count": None,
                "call_count": None,
            },
            "top_holdings": [],
        }

    def _load_archive_filing_detail(
        self,
        accession_number: str,
    ) -> dict | None:
        if not self.historical_database_path.is_file():
            return None

        import duckdb

        try:
            connection = duckdb.connect(
                str(self.historical_database_path),
                read_only=True,
            )
        except (duckdb.Error, OSError) as exc:
            logger.warning(
                "Could not open filing detail archive %s: %s",
                self.historical_database_path,
                exc,
            )
            return None
        try:
            try:
                row = connection.execute(
                    """
                    WITH holding_stats AS (
                        SELECT
                            count(*) AS holding_count,
                            coalesce(sum(value_usd), 0) AS total_value_usd,
                            count(*) FILTER (
                                WHERE upper(coalesce(put_call, '')) = 'PUT'
                            ) AS put_count,
                            count(*) FILTER (
                                WHERE upper(coalesce(put_call, '')) = 'CALL'
                            ) AS call_count
                        FROM holdings
                        WHERE accession_number = ?
                    )
                    SELECT
                        cover_pages.is_amendment,
                        cover_pages.amendment_number,
                        cover_pages.amendment_type,
                        cover_pages.report_type,
                        cover_pages.additional_information,
                        summary_pages.accession_number IS NOT NULL
                            AS has_summary_page,
                        summary_pages.other_included_managers_count,
                        summary_pages.table_entry_total,
                        summary_pages.table_value_usd,
                        summary_pages.is_confidential_omitted,
                        signatures.signer_name,
                        signatures.signer_title,
                        CAST(signatures.signature_date AS VARCHAR),
                        holding_stats.holding_count,
                        holding_stats.total_value_usd,
                        holding_stats.put_count,
                        holding_stats.call_count
                    FROM submissions
                    LEFT JOIN cover_pages USING (accession_number)
                    LEFT JOIN summary_pages USING (accession_number)
                    LEFT JOIN signatures USING (accession_number)
                    CROSS JOIN holding_stats
                    WHERE submissions.accession_number = ?
                    """,
                    [accession_number, accession_number],
                ).fetchone()
                if row is None:
                    return None

                holdings = connection.execute(
                    """
                    SELECT
                        nullif(trim(ticker), '') AS ticker,
                        name_of_issuer,
                        title_of_class,
                        cusip,
                        upper(coalesce(put_call, '')) AS put_call,
                        sum(value_usd) AS value_usd,
                        sum(shares_or_principal) AS shares_or_principal,
                        max(shares_or_principal_type)
                            AS shares_or_principal_type
                    FROM holdings
                    WHERE accession_number = ?
                    GROUP BY
                        ticker, name_of_issuer, title_of_class, cusip, put_call
                    ORDER BY value_usd DESC, name_of_issuer
                    LIMIT 20
                    """,
                    [accession_number],
                ).fetchall()
            except (duckdb.Error, OSError) as exc:
                logger.warning(
                    "Could not read filing detail %s from archive: %s",
                    accession_number,
                    exc,
                )
                return None
        finally:
            connection.close()

        imported_holding_count = int(row[13] or 0)
        if not row[5] and imported_holding_count == 0:
            return None
        holding_count = (
            int(row[7])
            if row[5] and row[7] is not None
            else imported_holding_count
        )
        total_value = float(
            row[8]
            if row[5] and row[8] is not None
            else row[14] or 0
        )
        return {
            "holdings_available": imported_holding_count > 0,
            "availability_note": None,
            "summary": {
                "is_amendment": (
                    bool(row[0])
                    if row[0] is not None
                    else None
                ),
                "amendment_number": row[1],
                "amendment_type": row[2],
                "report_type": row[3],
                "additional_information": row[4],
                "other_included_managers_count": (
                    int(row[6])
                    if row[6] is not None
                    else None
                ),
                "holding_count": holding_count,
                "total_value_usd": total_value,
                "is_confidential_omitted": (
                    bool(row[9])
                    if row[9] is not None
                    else None
                ),
                "signer_name": row[10],
                "signer_title": row[11],
                "signature_date": row[12],
                "put_count": int(row[15] or 0),
                "call_count": int(row[16] or 0),
            },
            "top_holdings": [
                {
                    "ticker": holding[0],
                    "issuer": holding[1],
                    "title_of_class": holding[2],
                    "cusip": holding[3],
                    "put_call": holding[4] or None,
                    "value_usd": float(holding[5] or 0),
                    "portfolio_weight": (
                        float(holding[5] or 0) / total_value * 100
                        if total_value > 0
                        else None
                    ),
                    "shares_or_principal": float(holding[6] or 0),
                    "shares_or_principal_type": holding[7],
                }
                for holding in holdings
            ],
        }

    def _load_current_cache_filing_detail(
        self,
        filing: dict,
    ) -> dict | None:
        fund_data = self.data_service.cache.get(
            filing["canonical_cik"],
            {},
        )
        metadata = fund_data.get("metadata", {})
        if metadata.get("accession_number") != filing["accession_number"]:
            return None
        holdings_frame = fund_data.get("holdings")
        if holdings_frame is None:
            return None

        grouped = {}
        total_value = 0.0
        put_count = 0
        call_count = 0
        for holding in holdings_frame.to_dict(orient="records"):
            value = self._detail_number(holding.get("Value"))
            shares = self._detail_number(holding.get("SharesPrnAmount"))
            total_value += value
            put_call = str(holding.get("PutCall") or "").strip().upper()
            if put_call == "PUT":
                put_count += 1
            elif put_call == "CALL":
                call_count += 1
            key = (
                self._detail_text(holding.get("Ticker")),
                self._detail_text(holding.get("Issuer")),
                self._detail_text(holding.get("Class")),
                self._detail_text(holding.get("Cusip")),
                put_call,
            )
            entry = grouped.setdefault(
                key,
                {
                    "ticker": key[0],
                    "issuer": key[1],
                    "title_of_class": key[2],
                    "cusip": key[3],
                    "put_call": put_call or None,
                    "value_usd": 0.0,
                    "shares_or_principal": 0.0,
                    "shares_or_principal_type": self._detail_text(
                        holding.get("Type")
                    ),
                },
            )
            entry["value_usd"] += value
            entry["shares_or_principal"] += shares

        top_holdings = sorted(
            grouped.values(),
            key=lambda item: item["value_usd"],
            reverse=True,
        )[:20]
        for holding in top_holdings:
            holding["portfolio_weight"] = (
                holding["value_usd"] / total_value * 100
                if total_value > 0
                else None
            )
        return {
            "holdings_available": bool(top_holdings),
            "availability_note": None,
            "summary": {
                "is_amendment": str(filing["form"]).endswith("/A"),
                "amendment_number": None,
                "amendment_type": None,
                "report_type": None,
                "additional_information": None,
                "other_included_managers_count": 0,
                "holding_count": len(holdings_frame),
                "total_value_usd": total_value,
                "is_confidential_omitted": None,
                "signer_name": None,
                "signer_title": None,
                "signature_date": None,
                "put_count": put_count,
                "call_count": call_count,
            },
            "top_holdings": top_holdings,
        }

    @staticmethod
    def _detail_number(value) -> float:
        try:
            number = float(value)
            return number if number == number else 0.0
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _detail_text(value) -> str | None:
        text = str(value or "").strip()
        return None if text.upper() in {"", "NAN", "NONE"} else text

    @staticmethod
    def _load_historical_inventory(database_path, quarters, roster):
        import duckdb

        from investor_screening.edgar_catalog import filing_source_url

        database_path = Path(database_path)
        if not database_path.exists():
            raise FileNotFoundError(
                f"Investor Screening archive not found: {database_path}"
            )

        canonical_by_source = {}
        manager_by_canonical = {}
        source_ciks = []
        for fund in roster:
            canonical_cik = str(fund["cik"]).zfill(10)
            manager_by_canonical[canonical_cik] = fund["manager"]
            for source_cik in [
                fund["cik"],
                *fund.get("historical_ciks", []),
            ]:
                source_identity = str(int(source_cik))
                canonical_by_source[source_identity] = canonical_cik
                source_ciks.append(int(source_cik))

        connection = duckdb.connect(str(database_path), read_only=True)
        try:
            report_periods = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT DISTINCT
                        CAST(period_of_report AS VARCHAR) AS report_period
                    FROM submissions
                    WHERE CAST(cik AS BIGINT) IN (
                        SELECT * FROM unnest(?)
                    )
                      AND submission_type IN ('13F-HR', '13F-HR/A')
                    ORDER BY report_period DESC
                    """,
                    [source_ciks],
                ).fetchall()
            ]
            if quarters is not None:
                report_periods = report_periods[:quarters]
            if not report_periods:
                raise RuntimeError(
                    "The Investor Screening archive contains no filing "
                    "periods for the current roster"
                )
            rows = connection.execute(
                """
                SELECT
                    submissions.accession_number,
                    CAST(submissions.cik AS VARCHAR) AS source_cik,
                    COALESCE(
                        NULLIF(sec_filings.form, ''),
                        submissions.submission_type
                    ) AS form,
                    CAST(submissions.filing_date AS VARCHAR) AS filing_date,
                    CAST(
                        submissions.period_of_report AS VARCHAR
                    ) AS report_period,
                    COALESCE(sec_filings.source_url, '') AS source_url
                FROM submissions
                LEFT JOIN sec_filings USING (accession_number)
                WHERE CAST(submissions.cik AS BIGINT) IN (
                    SELECT * FROM unnest(?)
                )
                  AND CAST(submissions.period_of_report AS VARCHAR) IN (
                    SELECT * FROM unnest(?)
                )
                  AND submissions.submission_type IN (
                    '13F-HR', '13F-HR/A'
                )
                ORDER BY
                    submissions.filing_date DESC,
                    submissions.accession_number DESC
                """,
                [source_ciks, list(report_periods)],
            ).fetchall()
        finally:
            connection.close()

        filings = []
        periods_found = set()
        errors = []
        for (
            accession_number,
            source_cik,
            form,
            filing_date,
            report_period,
            source_url,
        ) in rows:
            source_identity = str(int(source_cik))
            canonical_cik = canonical_by_source.get(source_identity)
            if canonical_cik is None:
                errors.append(
                    {
                        "source_cik": str(source_cik).zfill(10),
                        "error": (
                            "Historical filing source CIK is not mapped "
                            "to the current roster"
                        ),
                    }
                )
                continue
            normalized_source_cik = source_identity.zfill(10)
            periods_found.add(report_period)
            filings.append(
                {
                    "accession_number": str(accession_number),
                    "canonical_cik": canonical_cik,
                    "source_cik": normalized_source_cik,
                    "manager_name": manager_by_canonical[canonical_cik],
                    "form": str(form),
                    "filing_date": str(filing_date),
                    "report_period": str(report_period),
                    "source_url": (
                        str(source_url)
                        if source_url
                        else filing_source_url(
                            normalized_source_cik,
                            str(accession_number),
                        )
                    ),
                }
            )
        return {
            "filings": filings,
            "report_periods": report_periods,
            "periods_found": sorted(periods_found),
            "errors": errors,
        }

    async def _run_locked(self, *, trigger, lookback_days):
        baseline = not self.ledger.has_initialized_operational_ledger()
        roster_fingerprint = self.data_service._roster_fingerprint()
        run_id = self.ledger.start_run(trigger, roster_fingerprint)
        managers_checked = 0
        inserted = []
        invalidated = []
        outcomes = []
        try:
            cutoff = datetime.now(timezone.utc).date() - timedelta(
                days=max(7, min(730, int(lookback_days)))
            )
            loop = asyncio.get_running_loop()
            discovery = await loop.run_in_executor(
                None,
                self.data_service.discover_recent_filings,
                cutoff,
            )
            managers_checked = discovery["managers_checked"]
            observation = self.ledger.record_discoveries(
                run_id,
                discovery["filings"],
                discovery["errors"],
                baseline=baseline,
            )
            inserted = observation["work_items"]
            current_roster_fingerprint = self.data_service._roster_fingerprint(
                load_roster(ROSTER_PATH)
            )
            if current_roster_fingerprint != roster_fingerprint:
                raise RuntimeError(
                    "Roster changed during SEC filing discovery; retry the run"
                )

            affected_ciks = sorted({
                filing["canonical_cik"]
                for filing in inserted
            })
            if inserted and not baseline:
                invalidated = self.data_service.invalidate_filing_periods(
                    filing.get("report_period")
                    for filing in inserted
                )
            if affected_ciks:
                await self.data_service.refresh_funds(affected_ciks)
                current_roster_fingerprint = (
                    self.data_service._roster_fingerprint(
                        load_roster(ROSTER_PATH)
                    )
                )
                if current_roster_fingerprint != roster_fingerprint:
                    raise RuntimeError(
                        "Roster changed during cache publication; retry the run"
                    )
                awfi_published = False
                if self.after_refresh is not None:
                    awfi_published = bool(await self.after_refresh())
            else:
                awfi_published = False

            for filing in inserted:
                accession_number = filing["accession_number"]
                if baseline:
                    fund_data = self.data_service.cache.get(
                        filing["canonical_cik"],
                        {},
                    )
                    outcomes.append(
                        {
                            "accession_number": accession_number,
                            "status": (
                                "BASELINED"
                                if fund_data.get("status") == "loaded"
                                else "FAILED"
                            ),
                            "error": (
                                None
                                if fund_data.get("status") == "loaded"
                                else (
                                    fund_data.get("error")
                                    or "Manager cache refresh failed"
                                )
                            ),
                        }
                    )
                    continue
                fund_data = self.data_service.cache.get(
                    filing["canonical_cik"],
                    {},
                )
                if fund_data.get("status") != "loaded":
                    outcomes.append({
                        "accession_number": accession_number,
                        "status": "FAILED",
                        "error": (
                            fund_data.get("error")
                            or "Manager cache refresh failed"
                        ),
                    })
                    continue
                selected_accession = (
                    fund_data.get("metadata", {}).get(
                        "accession_number"
                    )
                )
                persisted_accession = (
                    self.data_service.get_persisted_accession(
                        filing["canonical_cik"]
                    )
                )
                if (
                    accession_number == selected_accession
                    and accession_number != persisted_accession
                ):
                    outcomes.append({
                        "accession_number": accession_number,
                        "status": "FAILED",
                        "error": "Durable manager cache verification failed",
                    })
                else:
                    outcomes.append({
                        "accession_number": accession_number,
                        "status": (
                            "PUBLISHED"
                            if (
                                accession_number == selected_accession
                                and accession_number == persisted_accession
                            )
                            else "RECORDED"
                        ),
                    })
            published = sum(
                item["status"] == "PUBLISHED"
                for item in outcomes
            )
            recorded = sum(
                item["status"] in {"RECORDED", "BASELINED"}
                for item in outcomes
            )
            failed = sum(
                item["status"] == "FAILED"
                for item in outcomes
            )
            if discovery["errors"] or failed:
                status = "PARTIAL"
            elif not inserted and not baseline:
                status = "NO_CHANGES"
            else:
                status = "COMPLETE"
            new_filings = observation["new_count"]
            result = {
                "run_id": run_id,
                "status": status,
                "baseline": baseline,
                "managers_checked": managers_checked,
                "filings_seen": len(discovery["filings"]),
                "new_filings": new_filings,
                "baseline_filings": observation["baseline_count"],
                "published_filings": published,
                "recorded_filings": recorded,
                "failed_filings": failed,
                "refreshed_managers": len(affected_ciks),
                "invalidated_periods": invalidated,
                "error_count": len(discovery["errors"]),
                "awfi_published": awfi_published,
            }
            self._publish_manifest(
                {
                    **result,
                    "affected_ciks": affected_ciks,
                    "timestamp": _utc_now(),
                    "roster_fingerprint": roster_fingerprint,
                }
            )
            self.ledger.record_outcomes(run_id, outcomes)
            self.ledger.complete_run(
                run_id,
                status=status,
                managers_checked=managers_checked,
                refreshed_managers=len(affected_ciks),
                invalidated_periods=len(invalidated),
                published_filings=published,
                recorded_filings=recorded,
                failed_filings=failed,
            )
            if new_filings:
                await self.data_service.broadcast_event({
                    "type": "filings_ingested",
                    "run_id": run_id,
                    "count": new_filings,
                    "published": published,
                    "timestamp": _utc_now(),
                })
            return result
        except Exception as exc:
            self.ledger.complete_run(
                run_id,
                status="FAILED",
                managers_checked=managers_checked,
                refreshed_managers=0,
                invalidated_periods=len(invalidated),
                published_filings=0,
                recorded_filings=0,
                failed_filings=len(inserted),
                error=str(exc),
            )
            raise

    def get_dashboard(self, **kwargs):
        return self.ledger.get_dashboard(**kwargs)

    def _read_publication_run_id(self):
        try:
            with self.publication_path.open("r", encoding="utf-8") as stream:
                return json.load(stream).get("run_id")
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return None

    def _publish_manifest(self, payload):
        self.publication_path.parent.mkdir(parents=True, exist_ok=True)
        previous = self._read_publication() or {}
        payload = {
            **payload,
            "generation": int(previous.get("generation") or 0) + 1,
            "cumulative_affected_ciks": sorted({
                *previous.get("cumulative_affected_ciks", []),
                *payload.get("affected_ciks", []),
            }),
            "cumulative_invalidated_periods": sorted({
                *previous.get("cumulative_invalidated_periods", []),
                *payload.get("invalidated_periods", []),
            }),
        }
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.publication_path.parent,
                prefix=".filing-publication.",
                suffix=".json.tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                json.dump(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(40):
                try:
                    os.replace(temporary_path, self.publication_path)
                    break
                except PermissionError:
                    if attempt == 39:
                        raise
                    time.sleep(0.1)
            temporary_path = None
            self._last_publication_run_id = payload["run_id"]
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _read_publication(self):
        try:
            with self.publication_path.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
            if not isinstance(payload, dict) or not payload.get("run_id"):
                return None
            return payload
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return None

    async def watch_publications(self, poll_seconds=15):
        while True:
            await asyncio.sleep(max(5, poll_seconds))
            publication = self._read_publication()
            if (
                publication is None
                or publication["run_id"] == self._last_publication_run_id
            ):
                continue
            self._last_publication_run_id = publication["run_id"]
            self.data_service._load_all_from_disk_cache()
            self.data_service._load_market_insights_from_disk()
            affected_ciks = set(
                publication.get("cumulative_affected_ciks", [])
            )
            self.data_service.manager_adjustment_cache = {
                key: value
                for key, value in (
                    self.data_service.manager_adjustment_cache.items()
                )
                if not (
                    isinstance(key, tuple)
                    and len(key) > 1
                    and key[1] in affected_ciks
                )
            }
            self.data_service.invalidate_exact_filing_periods(
                publication.get(
                    "cumulative_invalidated_periods",
                    [],
                )
            )
            if affected_ciks:
                await self.data_service.broadcast_event({
                    "type": "data_refresh",
                    "timestamp": publication.get("timestamp") or _utc_now(),
                })
            if publication.get("new_filings"):
                await self.data_service.broadcast_event({
                    "type": "filings_ingested",
                    "run_id": publication["run_id"],
                    "count": publication["new_filings"],
                    "published": publication.get(
                        "published_filings",
                        0,
                    ),
                    "timestamp": publication.get("timestamp") or _utc_now(),
                })
            locally_published_awfi = False
            if affected_ciks and self.after_refresh is not None:
                locally_published_awfi = bool(await self.after_refresh())
            if (
                publication.get("awfi_published")
                and not locally_published_awfi
            ):
                await self.data_service.broadcast_event({
                    "type": "awfi_published",
                    "timestamp": publication.get("timestamp") or _utc_now(),
                })
