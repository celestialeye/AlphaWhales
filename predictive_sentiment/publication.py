from __future__ import annotations

import json
import hashlib
import os
import secrets
import shutil
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from roster_store import load_roster

from .config import AWFI_VERSION
from .config import PROTOCOL_VERSION, ResearchConfig
from .pipeline import (
    DEFAULT_APPLICATION_CACHE_DIR,
    DEFAULT_OUTPUT_DB,
    DEFAULT_PERFORMANCE_DB,
    DEFAULT_ROSTER,
    DEFAULT_SOURCE_DB,
    _load_cached_top_holdings,
    _load_current_top_holdings,
    _load_source_data,
    _select_latest_top_holdings,
    performance_database_signature,
    run_research,
    source_13f_signature,
    top_holdings_fingerprint,
)
from .research import source_fingerprint


class PublicationBusyError(RuntimeError):
    pass


@contextmanager
def _publication_lock(output_path: Path):
    lock_path = output_path.with_name(f".{output_path.name}.publish.lock")
    staging_path = output_path.with_name(
        f".{output_path.stem}.{os.getpid()}."
        f"{secrets.token_hex(8)}.building{output_path.suffix}"
    )
    lock_stream = lock_path.open("a+b")
    try:
        try:
            lock_stream.seek(0)
            if lock_stream.read(1) == b"":
                lock_stream.write(b"\0")
                lock_stream.flush()
            lock_stream.seek(0)
        except OSError as exc:
            raise PublicationBusyError(
                f"AWFI publication is already running: {lock_path}"
            ) from exc
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(
                    lock_stream.fileno(),
                    msvcrt.LK_NBLCK,
                    1,
                )
            else:
                import fcntl

                fcntl.flock(
                    lock_stream.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
        except OSError as exc:
            raise PublicationBusyError(
                f"AWFI publication is already running: {lock_path}"
            ) from exc
        lock_stream.seek(0)
        lock_stream.write(
            (
                f"pid={os.getpid()} "
                f"started_at={datetime.now(timezone.utc).isoformat()}"
            ).encode("utf-8")
        )
        lock_stream.truncate()
        lock_stream.flush()
        yield staging_path
    finally:
        try:
            lock_stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(
                    lock_stream.fileno(),
                    msvcrt.LK_UNLCK,
                    1,
                )
            else:
                import fcntl

                fcntl.flock(
                    lock_stream.fileno(),
                    fcntl.LOCK_UN,
                )
        except OSError:
            pass
        lock_stream.close()
        staging_path.unlink(missing_ok=True)


def run_research_atomically(
    *,
    output_db: Path = DEFAULT_OUTPUT_DB,
    **kwargs,
):
    output_path = output_db.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with _publication_lock(output_path) as staging_path:
        if output_path.is_file():
            shutil.copy2(output_path, staging_path)
        result = run_research(
            output_db=staging_path,
            **kwargs,
        )
        _validate_staging_snapshot(staging_path)
        with staging_path.open("r+b") as stream:
            os.fsync(stream.fileno())
        for attempt in range(240):
            try:
                os.replace(staging_path, output_path)
                break
            except PermissionError:
                if attempt == 239:
                    raise
                time.sleep(0.5)
        return result


def _validate_staging_snapshot(staging_path: Path) -> None:
    connection = duckdb.connect(str(staging_path))
    try:
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    wal_path = Path(f"{staging_path}.wal")
    if wal_path.exists():
        raise RuntimeError(
            f"AWFI staging WAL remains after checkpoint: {wal_path}"
        )

    connection = duckdb.connect(str(staging_path), read_only=True)
    try:
        run = connection.execute(
            """
            SELECT r.run_id
            FROM research_runs r
            JOIN awfi_scores a USING (run_id)
            WHERE r.status = 'COMPLETE'
              AND r.protocol_version = ?
              AND a.awfi_version = ?
            GROUP BY r.run_id, r.completed_at
            ORDER BY r.completed_at DESC
            LIMIT 1
            """,
            [PROTOCOL_VERSION, AWFI_VERSION],
        ).fetchone()
        if run is None:
            raise RuntimeError(
                "AWFI staging database has no complete current run"
            )
        run_id = run[0]
        duplicate_checks = {
            "run_mapping": """
                SELECT count(*)
                FROM (
                    SELECT cusip
                    FROM run_mapping
                    WHERE run_id = ?
                    GROUP BY cusip
                    HAVING count(*) > 1
                )
            """,
            "run_top_holdings": """
                SELECT count(*)
                FROM (
                    SELECT canonical_cik, holding_rank
                    FROM run_top_holdings
                    WHERE run_id = ?
                    GROUP BY canonical_cik, holding_rank
                    HAVING count(*) > 1
                )
            """,
            "decomposed_features": """
                SELECT count(*)
                FROM (
                    SELECT report_period, cusip, horizon
                    FROM decomposed_features
                    WHERE run_id = ?
                    GROUP BY report_period, cusip, horizon
                    HAVING count(*) > 1
                )
            """,
            "awfi_scores": """
                SELECT count(*)
                FROM (
                    SELECT report_period, cusip, horizon
                    FROM awfi_scores
                    WHERE run_id = ?
                      AND awfi_version = ?
                    GROUP BY report_period, cusip, horizon
                    HAVING count(*) > 1
                )
            """,
        }
        for name, query in duplicate_checks.items():
            parameters = (
                [run_id, AWFI_VERSION]
                if name == "awfi_scores"
                else [run_id]
            )
            if connection.execute(query, parameters).fetchone()[0]:
                raise RuntimeError(
                    f"AWFI staging database has duplicate {name} keys"
                )
        invalid_scores = connection.execute(
            """
            SELECT count(*)
            FROM awfi_scores
            WHERE run_id = ?
              AND awfi_version = ?
              AND (
                  NOT isfinite(score)
                  OR score < -100
                  OR score > 100
                  OR signal <> CASE
                      WHEN score >= positive_threshold THEN 'BUY'
                      WHEN score <= -negative_threshold THEN 'SELL'
                      ELSE 'HOLD'
                  END
              )
            """,
            [run_id, AWFI_VERSION],
        ).fetchone()[0]
        if invalid_scores:
            raise RuntimeError(
                "AWFI staging database contains invalid scores or signals"
            )
        incomplete_horizons = connection.execute(
            """
            SELECT count(*)
            FROM (
                SELECT report_period, cusip
                FROM awfi_scores
                WHERE run_id = ?
                  AND awfi_version = ?
                GROUP BY report_period, cusip
                HAVING count(DISTINCT horizon) <> 4
            )
            """,
            [run_id, AWFI_VERSION],
        ).fetchone()[0]
        if incomplete_horizons:
            raise RuntimeError(
                "AWFI staging database has incomplete horizon sets"
            )
        unmapped_scores = connection.execute(
            """
            SELECT count(*)
            FROM (
                SELECT DISTINCT a.cusip
                FROM awfi_scores a
                LEFT JOIN run_mapping m
                  ON m.run_id = a.run_id
                 AND m.cusip = a.cusip
                WHERE a.run_id = ?
                  AND a.awfi_version = ?
                  AND m.cusip IS NULL
            )
            """,
            [run_id, AWFI_VERSION],
        ).fetchone()[0]
        if unmapped_scores:
            raise RuntimeError(
                "AWFI staging database contains unmapped scores"
            )
    finally:
        connection.close()


def research_snapshot_needs_refresh(
    *,
    output_db: Path = DEFAULT_OUTPUT_DB,
    roster_path: Path = DEFAULT_ROSTER,
    application_cache_dir: Path = DEFAULT_APPLICATION_CACHE_DIR,
    source_db: Path = DEFAULT_SOURCE_DB,
    performance_db: Path = DEFAULT_PERFORMANCE_DB,
    top_n: int = 10,
) -> bool:
    output_path = output_db.resolve()
    if not output_path.is_file():
        return True
    roster = load_roster(roster_path.resolve())
    cached_rows = _load_cached_top_holdings(
        roster,
        application_cache_dir.resolve(),
        top_n=top_n,
    )
    try:
        source = duckdb.connect(
            str(source_db.resolve()),
            read_only=True,
        )
        try:
            archive_rows = _load_current_top_holdings(
                source,
                roster,
                top_n=top_n,
            )
            current_source = source_13f_signature(source)
            filings, holdings, _ = _load_source_data(
                source,
                roster,
            )
            current_source["roster_source_fingerprint"] = (
                source_fingerprint(filings, holdings)
            )
        finally:
            source.close()
    except (duckdb.Error, OSError):
        return True
    current_universe = _select_latest_top_holdings(
        archive_rows,
        cached_rows,
    )
    if not current_universe:
        return False
    current_period = max(
        row["report_period"]
        for row in current_universe
    )
    try:
        connection = duckdb.connect(str(output_path), read_only=True)
        try:
            row = connection.execute(
                """
                SELECT
                    r.protocol_version,
                    r.config_json,
                    r.roster_sha256,
                    p.fingerprint,
                    p.details_json,
                    source.details_json,
                    performance.details_json
                FROM research_runs r
                JOIN run_artifact_provenance p USING (run_id)
                LEFT JOIN run_artifact_provenance source
                  ON source.run_id = r.run_id
                 AND source.artifact_name = 'source_13f'
                LEFT JOIN run_artifact_provenance performance
                  ON performance.run_id = r.run_id
                 AND performance.artifact_name = 'performance_database'
                WHERE r.status = 'COMPLETE'
                  AND p.artifact_name = 'top_holdings_universe'
                  AND EXISTS (
                      SELECT 1
                      FROM awfi_scores a
                      WHERE a.run_id = r.run_id
                        AND a.awfi_version = ?
                  )
                ORDER BY r.completed_at DESC
                LIMIT 1
                """,
                [AWFI_VERSION],
            ).fetchone()
        finally:
            connection.close()
    except (duckdb.Error, OSError):
        return True
    if row is None:
        return True
    protocol_version, config_json, roster_sha256 = row[:3]
    if str(protocol_version) != PROTOCOL_VERSION:
        return True
    stored_config = (
        json.loads(config_json)
        if isinstance(config_json, str)
        else config_json
    )
    if stored_config != ResearchConfig().as_dict():
        return True
    roster_digest = hashlib.sha256(
        roster_path.resolve().read_bytes()
    ).hexdigest()
    if str(roster_sha256) != roster_digest:
        return True
    details = json.loads(row[4]) if isinstance(row[4], str) else row[4]
    published_period = details.get("latest_period")
    if published_period is None:
        return True
    published_date = datetime.fromisoformat(published_period).date()
    if current_period > published_date:
        return True
    if (
        current_period == published_date
        and top_holdings_fingerprint(current_universe) != str(row[3])
    ):
        return True
    stored_source = (
        json.loads(row[5])
        if isinstance(row[5], str)
        else row[5]
    )
    if stored_source is None:
        return True
    stored_source = stored_source.get("signature")
    if stored_source is None:
        return True
    if current_source != stored_source:
        return True
    stored_performance = (
        json.loads(row[6])
        if isinstance(row[6], str)
        else row[6]
    )
    if stored_performance is None:
        return True
    stored_performance = stored_performance.get("signature")
    if stored_performance is None:
        return True
    try:
        performance_path = performance_db.resolve()
        performance = duckdb.connect(
            str(performance_path),
            read_only=True,
        )
        try:
            current_performance = performance_database_signature(
                performance_path,
                performance,
            )
        finally:
            performance.close()
    except (duckdb.Error, OSError):
        return True
    return current_performance != stored_performance
