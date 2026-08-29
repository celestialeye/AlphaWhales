from __future__ import annotations

import dataclasses
import gzip
import hashlib
import http.client
import importlib.metadata
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ElementTree
import urllib.error
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from edgar import Filing, set_identity

from .database import DEFAULT_DATA_DIR

TABLE_METHODS = {
    "13F-HR": ("infotable", "holdings"),
    "13F-HR/A": ("infotable", "holdings"),
    "3": ("to_dataframe",),
    "3/A": ("to_dataframe",),
    "4": ("to_dataframe",),
    "4/A": ("to_dataframe",),
    "5": ("to_dataframe",),
    "5/A": ("to_dataframe",),
    "NPORT-P": (
        "investment_data",
        "securities_data",
        "derivatives_data",
        "swaps_data",
        "options_data",
        "forwards_data",
        "futures_data",
        "swaptions_data",
    ),
    "NPORT-P/A": (
        "investment_data",
        "securities_data",
        "derivatives_data",
        "swaps_data",
        "options_data",
        "forwards_data",
        "futures_data",
        "swaptions_data",
    ),
    "N-CEN": (
        "series_data",
        "service_providers",
        "broker_data",
        "director_data",
        "etf_data",
    ),
    "N-CEN/A": (
        "series_data",
        "service_providers",
        "broker_data",
        "director_data",
        "etf_data",
    ),
    "N-CSR": ("expense_data", "performance_data", "holdings_data"),
    "N-CSR/A": ("expense_data", "performance_data", "holdings_data"),
    "N-CSRS": ("expense_data", "performance_data", "holdings_data"),
    "N-CSRS/A": ("expense_data", "performance_data", "holdings_data"),
    "N-MFP3": (
        "portfolio_data",
        "share_class_data",
        "nav_history",
        "yield_history",
        "liquidity_history",
        "holdings_by_category",
        "collateral_data",
    ),
    "N-MFP3/A": (
        "portfolio_data",
        "share_class_data",
        "nav_history",
        "yield_history",
        "liquidity_history",
        "holdings_by_category",
        "collateral_data",
    ),
    "N-PX": ("to_dataframe",),
    "N-PX/A": ("to_dataframe",),
    "144": ("to_dataframe",),
    "144/A": ("to_dataframe",),
}

for _legacy_money_market_form in (
    "N-MFP",
    "N-MFP/A",
    "N-MFP1",
    "N-MFP1/A",
    "N-MFP2",
    "N-MFP2/A",
):
    TABLE_METHODS[_legacy_money_market_form] = TABLE_METHODS["N-MFP3"]

LIST_ATTRIBUTES = {
    "SCHEDULE 13D": ("reporting_persons", "signatures"),
    "SCHEDULE 13D/A": ("reporting_persons", "signatures"),
    "SCHEDULE 13G": ("reporting_persons", "signatures"),
    "SCHEDULE 13G/A": ("reporting_persons", "signatures"),
    "SC 13D": ("reporting_persons", "signatures"),
    "SC 13D/A": ("reporting_persons", "signatures"),
    "SC 13G": ("reporting_persons", "signatures"),
    "SC 13G/A": ("reporting_persons", "signatures"),
}

RAW_ONLY_BEFORE = {
    "beneficial_ownership": date(2024, 12, 18),
}

XML_FIRST_FAMILIES = {
    "proxy_voting",
}

_DOWNLOAD_LOCK = threading.Lock()
_NEXT_DOWNLOAD_AT = 0.0


def _serialize(value: Any, seen: set[int] | None = None) -> Any:
    if seen is None:
        seen = set()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime, Decimal, Path, Enum)):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, pd.DataFrame):
        return [_serialize(row, seen) for row in value.to_dict(orient="records")]
    if isinstance(value, pd.Series):
        return _serialize(value.to_dict(), seen)
    if isinstance(value, dict):
        return {str(key): _serialize(item, seen) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize(item, seen) for item in value]

    object_id = id(value)
    if object_id in seen:
        return "<cycle>"
    seen.add(object_id)
    try:
        if dataclasses.is_dataclass(value):
            return {
                field.name: _serialize(getattr(value, field.name), seen)
                for field in dataclasses.fields(value)
            }
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return _serialize(model_dump(), seen)
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return _serialize(to_dict(), seen)
        attributes = getattr(value, "__dict__", None)
        if attributes is not None:
            return {
                key: _serialize(item, seen)
                for key, item in attributes.items()
                if key != "_filing" and not key.endswith("_cache")
            }
        return str(value)
    finally:
        seen.discard(object_id)


def _dataframe_from_member(parsed_object: Any, member_name: str) -> pd.DataFrame:
    member = getattr(parsed_object, member_name)
    if callable(member):
        if member_name == "to_dataframe":
            frame = member()
        else:
            frame = member()
    else:
        frame = member
    if frame is None:
        return pd.DataFrame()
    if isinstance(frame, pd.DataFrame):
        return frame
    if hasattr(frame, "data") and isinstance(frame.data, pd.DataFrame):
        return frame.data
    raise TypeError(
        f"{type(parsed_object).__name__}.{member_name} returned "
        f"{type(frame).__name__}, not a DataFrame"
    )


def extract_tables(
    parsed_object: Any,
    form: str,
) -> tuple[dict[str, list[dict]], list[str]]:
    tables: dict[str, list[dict]] = {}
    errors = []
    for member_name in TABLE_METHODS.get(form, ()):
        if not hasattr(parsed_object, member_name):
            errors.append(
                f"{type(parsed_object).__name__} has no expected member {member_name}"
            )
            continue
        try:
            frame = _dataframe_from_member(parsed_object, member_name)
            tables[member_name] = [
                _serialize(row)
                for row in frame.to_dict(orient="records")
            ]
        except Exception as exc:
            errors.append(f"{member_name}: {type(exc).__name__}: {exc}")
    for attribute_name in LIST_ATTRIBUTES.get(form, ()):
        values = getattr(parsed_object, attribute_name, None) or []
        tables[attribute_name] = [_serialize(value) for value in values]
    return tables, errors


def _row_hash(row_json: str) -> str:
    return hashlib.sha256(row_json.encode("utf-8")).hexdigest()


def _raw_path(raw_dir: Path, form: str, filing_date: date, accession_number: str) -> Path:
    form_dir = form.lower().replace(" ", "-").replace("/", "-")
    return raw_dir / form_dir / str(filing_date.year) / f"{accession_number}.txt.gz"


def _write_raw_submission(path: Path, content: str | bytes) -> tuple[str, int]:
    raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    with gzip.open(temporary, "wb", compresslevel=6) as output:
        output.write(raw_bytes)
    temporary.replace(path)
    return hashlib.sha256(raw_bytes).hexdigest(), len(raw_bytes)


def _xml_to_dict(element: ElementTree.Element) -> dict[str, Any]:
    node: dict[str, Any] = {}
    for key, value in element.attrib.items():
        node[f"@{key.rsplit('}', 1)[-1]}"] = value
    text = (element.text or "").strip()
    if text:
        node["#text"] = text
    for child in element:
        if not isinstance(child.tag, str):
            continue
        name = child.tag.rsplit("}", 1)[-1]
        value = _xml_to_dict(child)
        if name in node:
            if not isinstance(node[name], list):
                node[name] = [node[name]]
            node[name].append(value)
        else:
            node[name] = value
    return node


def parse_xml_fallback(xml_content: str) -> dict[str, Any]:
    root = ElementTree.fromstring(xml_content)
    return {root.tag.rsplit("}", 1)[-1]: _xml_to_dict(root)}


def _download_submission(source_url: str, identity: str) -> bytes:
    global _NEXT_DOWNLOAD_AT
    rate = max(1.0, float(os.environ.get("EDGAR_RATE_LIMIT_PER_SEC", "8")))
    with _DOWNLOAD_LOCK:
        now = time.monotonic()
        wait = max(0.0, _NEXT_DOWNLOAD_AT - now)
        if wait:
            time.sleep(wait)
        _NEXT_DOWNLOAD_AT = max(now, _NEXT_DOWNLOAD_AT) + (1.0 / rate)
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": identity, "Host": "www.sec.gov"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError, http.client.IncompleteRead):
            if attempt == 3:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError(f"Unable to download SEC submission: {source_url}")


def persist_filing_details(
    connection: duckdb.DuckDBPyConnection,
    *,
    accession_number: str,
    form: str,
    filing_date: date,
    raw_submission: str | bytes,
    parsed_object: Any,
    raw_dir: str | Path = DEFAULT_DATA_DIR / "raw",
    status: str = "INGESTED",
    object_type_override: str | None = None,
    prior_extraction_errors: list[str] | None = None,
    extracted_tables: dict[str, list[dict]] | None = None,
) -> dict:
    raw_path = _raw_path(Path(raw_dir).resolve(), form, filing_date, accession_number)
    raw_hash, raw_size = _write_raw_submission(raw_path, raw_submission)
    object_type = object_type_override or (
        f"{type(parsed_object).__module__}.{type(parsed_object).__name__}"
    )
    object_json = json.dumps(_serialize(parsed_object), sort_keys=True, separators=(",", ":"))
    if extracted_tables is None:
        tables, extraction_errors = extract_tables(parsed_object, form)
    else:
        tables = extracted_tables
        extraction_errors = []
    extraction_errors = [*(prior_extraction_errors or []), *extraction_errors]
    if extraction_errors and status == "INGESTED":
        status = "INGESTED_PARTIAL"
    manifest = {name: len(rows) for name, rows in tables.items()}

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            "DELETE FROM filing_table_rows WHERE accession_number = ?",
            [accession_number],
        )
        for table_name, rows in tables.items():
            payload = []
            for row_index, row in enumerate(rows):
                row_json = json.dumps(row, sort_keys=True, separators=(",", ":"))
                payload.append(
                    [accession_number, table_name, row_index, _row_hash(row_json), row_json]
                )
            if payload:
                connection.executemany(
                    """
                    INSERT INTO filing_table_rows (
                        accession_number, table_name, row_index, row_hash, row_json
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    payload,
                )
        connection.execute(
            """
            INSERT INTO filing_artifacts (
                accession_number, status, raw_submission_path,
                raw_submission_sha256, raw_submission_bytes,
                object_type, object_json, extractor_manifest,
                edgartools_version, ingested_at, last_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, now(), ?)
            ON CONFLICT (accession_number) DO UPDATE SET
                status = excluded.status,
                raw_submission_path = excluded.raw_submission_path,
                raw_submission_sha256 = excluded.raw_submission_sha256,
                raw_submission_bytes = excluded.raw_submission_bytes,
                object_type = excluded.object_type,
                object_json = excluded.object_json,
                extractor_manifest = excluded.extractor_manifest,
                edgartools_version = excluded.edgartools_version,
                ingested_at = now(),
                last_error = excluded.last_error
            """,
            [
                accession_number,
                status,
                str(raw_path),
                raw_hash,
                raw_size,
                object_type,
                object_json,
                json.dumps(manifest, sort_keys=True),
                importlib.metadata.version("edgartools"),
                json.dumps(extraction_errors) if extraction_errors else None,
            ],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return {
        "accession_number": accession_number,
        "form": form,
        "raw_bytes": raw_size,
        "object_type": object_type,
        "status": status,
        "tables": manifest,
        "extraction_errors": extraction_errors,
    }


def persist_npx_details(
    connection: duckdb.DuckDBPyConnection,
    *,
    accession_number: str,
    form: str,
    filing_date: date,
    raw_submission: bytes,
    raw_dir: str | Path,
) -> dict:
    raw_path = _raw_path(Path(raw_dir).resolve(), form, filing_date, accession_number)
    raw_hash, raw_size = _write_raw_submission(raw_path, raw_submission)
    filing = Filing.from_sgml_text(raw_submission.decode("utf-8", errors="replace"))
    primary = None
    source_vote_count = 0
    recovery_errors = []

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            "DELETE FROM filing_table_rows WHERE accession_number = ?",
            [accession_number],
        )
        for attachment in filing.attachments:
            if not attachment.is_xml:
                continue
            if attachment.document_type in {"N-PX", "N-PX/A"}:
                primary = parse_xml_fallback(attachment.content)
                row_json = json.dumps(
                    primary,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                connection.execute(
                    """
                    INSERT INTO filing_table_rows (
                        accession_number, table_name, row_index, row_hash, row_json
                    )
                    VALUES (?, 'npx_filing', 0, ?, ?)
                    """,
                    [accession_number, _row_hash(row_json), row_json],
                )
                continue
            if attachment.document_type != "PROXY VOTING RECORD":
                continue
            xml_bytes = attachment.content.encode("utf-8", errors="replace")
            source_vote_count += len(
                re.findall(
                    rb"<(?:[A-Za-z0-9_]+:)?proxyTable(?:\s|>)",
                    xml_bytes,
                )
            )
        if primary is None:
            raise ValueError("N-PX primary XML document was not found")

        object_json = json.dumps(
            primary,
            sort_keys=True,
            separators=(",", ":"),
        )
        manifest = {
            "npx_filing": 1,
            "proxy_votes_source": source_vote_count,
        }
        status = (
            "INGESTED_PARTIAL"
            if recovery_errors
            else "INGESTED_XML_FALLBACK"
        )
        connection.execute(
            """
            INSERT INTO filing_artifacts (
                accession_number, status, raw_submission_path,
                raw_submission_sha256, raw_submission_bytes,
                object_type, object_json, extractor_manifest,
                edgartools_version, ingested_at, last_error
            )
            VALUES (
                ?, ?, ?, ?, ?, 'npx.lossless_xml',
                ?, ?, ?, now(), ?
            )
            ON CONFLICT (accession_number) DO UPDATE SET
                status = excluded.status,
                raw_submission_path = excluded.raw_submission_path,
                raw_submission_sha256 = excluded.raw_submission_sha256,
                raw_submission_bytes = excluded.raw_submission_bytes,
                object_type = excluded.object_type,
                object_json = excluded.object_json,
                extractor_manifest = excluded.extractor_manifest,
                edgartools_version = excluded.edgartools_version,
                ingested_at = now(),
                last_error = NULL
            """,
            [
                accession_number,
                status,
                str(raw_path),
                raw_hash,
                raw_size,
                object_json,
                json.dumps(manifest, sort_keys=True),
                importlib.metadata.version("edgartools"),
                json.dumps(recovery_errors) if recovery_errors else None,
            ],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return {
        "accession_number": accession_number,
        "form": form,
        "raw_bytes": raw_size,
        "object_type": "npx.lossless_xml",
        "status": status,
        "tables": manifest,
        "extraction_errors": recovery_errors,
    }


def _record_failure(
    connection: duckdb.DuckDBPyConnection,
    accession_number: str,
    error: Exception,
) -> None:
    connection.execute(
        """
        INSERT INTO filing_artifacts (accession_number, status, last_error, ingested_at)
        VALUES (?, 'FAILED', ?, now())
        ON CONFLICT (accession_number) DO UPDATE SET
            status = 'FAILED',
            last_error = excluded.last_error,
            ingested_at = now()
        """,
        [accession_number, f"{type(error).__name__}: {error}"],
    )


def _record_source_unavailable(
    connection: duckdb.DuckDBPyConnection,
    accession_number: str,
    error: Exception,
) -> None:
    connection.execute(
        """
        INSERT INTO filing_artifacts (
            accession_number, status, object_type, object_json,
            extractor_manifest, edgartools_version, ingested_at, last_error
        )
        VALUES (
            ?, 'SOURCE_UNAVAILABLE', 'sec.source_unavailable',
            '{"source_available":false}', '{}', ?, now(), ?
        )
        ON CONFLICT (accession_number) DO UPDATE SET
            status = excluded.status,
            object_type = excluded.object_type,
            object_json = excluded.object_json,
            extractor_manifest = excluded.extractor_manifest,
            edgartools_version = excluded.edgartools_version,
            ingested_at = now(),
            last_error = excluded.last_error
        """,
        [
            accession_number,
            importlib.metadata.version("edgartools"),
            f"{type(error).__name__}: {error}",
        ],
    )


def _prepare_detail(
    accession_number: str,
    metadata: tuple,
    identity: str,
) -> dict:
    raw_submission = _download_submission(metadata[5], identity)
    status = "INGESTED"
    object_type_override = None
    parser_errors = []
    raw_only_before = RAW_ONLY_BEFORE.get(metadata[2])
    if raw_only_before and metadata[1] < raw_only_before:
        parsed_object = {
            "form": metadata[0],
            "structured_detail_available": False,
        }
        status = "RAW_ONLY"
        object_type_override = "raw.sec_submission"
        extracted_tables = None
    elif metadata[2] in XML_FIRST_FAMILIES:
        parsed_object = {"form": metadata[0]}
        status = "INGESTED_XML_FALLBACK"
        object_type_override = "npx.lossless_xml"
        extracted_tables = None
    else:
        extracted_tables = None
        filing = Filing.from_sgml_text(
            raw_submission.decode("utf-8", errors="replace")
        )
        filing.form = metadata[0]
        filing.cik = int(metadata[3])
        filing.company = metadata[4] or filing.company
        try:
            parsed_object = filing.obj()
        except Exception as exc:
            parser_errors.append(f"filing.obj: {type(exc).__name__}: {exc}")
            parsed_object = None
        if parsed_object is None:
            xml_content = filing.xml()
            if xml_content:
                parsed_object = parse_xml_fallback(xml_content)
                status = (
                    "INGESTED_PARTIAL"
                    if parser_errors
                    else "INGESTED_XML_FALLBACK"
                )
                object_type_override = "xml.etree.ElementTree"
            else:
                parser_errors.append(
                    f"No typed parser or primary XML is available for form {metadata[0]}"
                )
                parsed_object = {
                    "form": metadata[0],
                    "structured_detail_available": False,
                }
                status = "RAW_ONLY"
                object_type_override = "raw.sec_submission"
    return {
        "accession_number": accession_number,
        "metadata": metadata,
        "raw_submission": raw_submission,
        "parsed_object": parsed_object,
        "status": status,
        "object_type_override": object_type_override,
        "parser_errors": parser_errors,
        "extracted_tables": extracted_tables,
    }


def ingest_accessions(
    connection: duckdb.DuckDBPyConnection,
    accessions: Iterable[str],
    *,
    identity: str,
    raw_dir: str | Path = DEFAULT_DATA_DIR / "raw",
    include_results: bool = True,
    failure_example_limit: int = 20,
    workers: int = 4,
) -> dict:
    set_identity(identity)
    ingested = []
    ingested_count = 0
    status_counts: dict[str, int] = {}
    extracted_rows = 0
    failures = []
    failure_count = 0
    metadata_rows = {}
    for accession_number in accessions:
        metadata = connection.execute(
            """
            SELECT
                form, filing_date, filing_family, cik, company_name, source_url
            FROM sec_filings
            WHERE accession_number = ?
            """,
            [accession_number],
        ).fetchone()
        if metadata is None:
            error = ValueError(
                f"Accession is not present in sec_filings: {accession_number}"
            )
            _record_failure(connection, accession_number, error)
            failure_count += 1
            if include_results or len(failures) < failure_example_limit:
                failures.append(
                    {
                        "accession_number": accession_number,
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
            continue
        metadata_rows[accession_number] = metadata

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(
                _prepare_detail,
                accession_number,
                metadata,
                identity,
            ): accession_number
            for accession_number, metadata in metadata_rows.items()
        }
        for future in as_completed(futures):
            accession_number = futures[future]
            try:
                prepared = future.result()
                metadata = prepared["metadata"]
                if metadata[2] in XML_FIRST_FAMILIES:
                    result = persist_npx_details(
                        connection,
                        accession_number=accession_number,
                        form=metadata[0],
                        filing_date=metadata[1],
                        raw_submission=prepared["raw_submission"],
                        raw_dir=raw_dir,
                    )
                else:
                    result = persist_filing_details(
                        connection,
                        accession_number=accession_number,
                        form=metadata[0],
                        filing_date=metadata[1],
                        raw_submission=prepared["raw_submission"],
                        parsed_object=prepared["parsed_object"],
                        raw_dir=raw_dir,
                        status=prepared["status"],
                        object_type_override=prepared["object_type_override"],
                        prior_extraction_errors=prepared["parser_errors"],
                        extracted_tables=prepared["extracted_tables"],
                    )
                ingested_count += 1
                status_counts[result["status"]] = status_counts.get(result["status"], 0) + 1
                extracted_rows += sum(result["tables"].values())
                if include_results:
                    ingested.append(result)
            except Exception as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                    _record_source_unavailable(
                        connection,
                        accession_number,
                        exc,
                    )
                    status_counts["SOURCE_UNAVAILABLE"] = (
                        status_counts.get("SOURCE_UNAVAILABLE", 0) + 1
                    )
                    ingested_count += 1
                    continue
                _record_failure(connection, accession_number, exc)
                failure_count += 1
                if include_results or len(failures) < failure_example_limit:
                    failures.append(
                        {
                            "accession_number": accession_number,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
    return {
        "ingested": ingested,
        "ingested_count": ingested_count,
        "status_counts": status_counts,
        "extracted_rows": extracted_rows,
        "failures": failures,
        "failure_count": failure_count,
    }


def pending_accessions(
    connection: duckdb.DuckDBPyConnection,
    *,
    family: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    retry_failed: bool = False,
    retry_incomplete: bool = False,
    force: bool = False,
    limit: int | None = None,
) -> list[str]:
    if force:
        conditions = ["1 = 1"]
        parameters: list[Any] = []
    elif retry_incomplete:
        retry_statuses = [
            "FAILED",
            "INGESTED_PARTIAL",
            "INGESTED_XML_FALLBACK",
            "RAW_ONLY",
        ]
        status_placeholders = ", ".join("?" for _ in retry_statuses)
        conditions = [
            f"(fa.accession_number IS NULL OR fa.status IN ({status_placeholders}))"
        ]
        parameters = retry_statuses.copy()
    elif retry_failed:
        conditions = ["(fa.accession_number IS NULL OR fa.status = 'FAILED')"]
        parameters = []
    else:
        conditions = ["fa.accession_number IS NULL"]
        parameters = []
    if family and family != "all":
        conditions.append("sf.filing_family = ?")
        parameters.append(family)
    if start_date:
        conditions.append("sf.filing_date >= ?")
        parameters.append(start_date)
    if end_date:
        conditions.append("sf.filing_date <= ?")
        parameters.append(end_date)
    limit_sql = ""
    if limit is not None:
        limit_sql = "LIMIT ?"
        parameters.append(limit)
    rows = connection.execute(
        f"""
        SELECT sf.accession_number
        FROM sec_filings sf
        LEFT JOIN filing_artifacts fa USING (accession_number)
        WHERE {' AND '.join(conditions)}
        ORDER BY sf.filing_date, sf.accession_number
        {limit_sql}
        """,
        parameters,
    ).fetchall()
    return [row[0] for row in rows]
