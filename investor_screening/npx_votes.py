from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from edgar import Filing
from lxml import etree

from .database import DEFAULT_DATA_DIR
from .detail_ingest import _xml_to_dict

NPX_VOTE_SCHEMA = pa.schema(
    [
        ("accession_number", pa.string()),
        ("filing_date", pa.date32()),
        ("form", pa.string()),
        ("row_index", pa.int64()),
        ("issuer_name", pa.string()),
        ("cusip", pa.string()),
        ("isin", pa.string()),
        ("meeting_date_reported", pa.string()),
        ("vote_description", pa.string()),
        ("shares_voted_reported", pa.string()),
        ("shares_on_loan_reported", pa.string()),
        ("vote_series", pa.string()),
        ("recovery_mode", pa.bool_()),
        ("row_json", pa.string()),
    ]
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _node_text(row: dict, name: str) -> str | None:
    value = row.get(name)
    if isinstance(value, dict):
        text = value.get("#text")
        return str(text) if text is not None else None
    if value is None:
        return None
    return str(value)


def _write_batch(
    writer: pq.ParquetWriter,
    batch: list[dict],
) -> None:
    if not batch:
        return
    writer.write_table(pa.Table.from_pylist(batch, schema=NPX_VOTE_SCHEMA))
    batch.clear()


def build_npx_vote_lake(
    connection: duckdb.DuckDBPyConnection,
    *,
    lake_dir: str | Path = DEFAULT_DATA_DIR / "lake" / "npx_votes",
) -> dict:
    lake = Path(lake_dir).resolve()
    lake.mkdir(parents=True, exist_ok=True)
    years = [
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT year(filing_date)
            FROM sec_filings
            WHERE filing_family = 'proxy_voting'
            ORDER BY 1
            """
        ).fetchall()
    ]
    results = []
    for year in years:
        filings = connection.execute(
            """
            SELECT
                sf.accession_number,
                sf.filing_date,
                sf.form,
                fa.raw_submission_path
            FROM sec_filings sf
            JOIN filing_artifacts fa USING (accession_number)
            WHERE sf.filing_family = 'proxy_voting'
              AND year(sf.filing_date) = ?
              AND fa.raw_submission_path IS NOT NULL
            ORDER BY sf.filing_date, sf.accession_number
            """,
            [year],
        ).fetchall()
        target = lake / f"{year}.parquet"
        temporary = target.with_suffix(".building.parquet")
        temporary.unlink(missing_ok=True)
        writer = pq.ParquetWriter(
            temporary,
            NPX_VOTE_SCHEMA,
            compression="zstd",
            use_dictionary=True,
        )
        filing_count = 0
        vote_count = 0
        batch: list[dict] = []
        try:
            for accession_number, filing_date, form, raw_path in filings:
                raw = gzip.open(raw_path, "rb").read()
                filing = Filing.from_sgml_text(
                    raw.decode("utf-8", errors="replace")
                )
                filing_vote_count = 0
                for attachment in filing.attachments:
                    if attachment.document_type != "PROXY VOTING RECORD":
                        continue
                    xml_bytes = attachment.content.encode(
                        "utf-8",
                        errors="replace",
                    )
                    expected = len(
                        re.findall(
                            rb"<(?:[A-Za-z0-9_]+:)?proxyTable(?:\s|>)",
                            xml_bytes,
                        )
                    )
                    context = etree.iterparse(
                        io.BytesIO(xml_bytes),
                        events=("end",),
                        recover=True,
                        huge_tree=True,
                    )
                    attachment_vote_count = 0
                    for _, element in context:
                        if element.tag.rsplit("}", 1)[-1] != "proxyTable":
                            continue
                        row = _xml_to_dict(element)
                        batch.append(
                            {
                                "accession_number": accession_number,
                                "filing_date": filing_date,
                                "form": form,
                                "row_index": filing_vote_count,
                                "issuer_name": _node_text(row, "issuerName"),
                                "cusip": _node_text(row, "cusip"),
                                "isin": _node_text(row, "isin"),
                                "meeting_date_reported": _node_text(
                                    row,
                                    "meetingDate",
                                ),
                                "vote_description": _node_text(
                                    row,
                                    "voteDescription",
                                ),
                                "shares_voted_reported": _node_text(
                                    row,
                                    "sharesVoted",
                                ),
                                "shares_on_loan_reported": _node_text(
                                    row,
                                    "sharesOnLoan",
                                ),
                                "vote_series": _node_text(row, "voteSeries"),
                                "recovery_mode": True,
                                "row_json": json.dumps(
                                    row,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                            }
                        )
                        filing_vote_count += 1
                        attachment_vote_count += 1
                        vote_count += 1
                        element.clear()
                        if len(batch) >= 5000:
                            _write_batch(writer, batch)
                    if attachment_vote_count != expected:
                        raise ValueError(
                            f"{accession_number}: expected {expected} votes, "
                            f"parsed {attachment_vote_count}"
                        )
                filing_count += 1
            _write_batch(writer, batch)
        finally:
            writer.close()

        temporary.replace(target)
        actual_votes = pq.ParquetFile(target).metadata.num_rows
        if actual_votes != vote_count:
            raise ValueError(
                f"N-PX {year}: expected {vote_count} votes, "
                f"wrote {actual_votes}"
            )
        connection.execute(
            """
            INSERT INTO npx_vote_files (
                report_year, output_path, source_filing_count, vote_count,
                parquet_sha256, parquet_bytes, status, built_at, last_error
            )
            VALUES (?, ?, ?, ?, ?, ?, 'IMPORTED', now(), NULL)
            ON CONFLICT (report_year) DO UPDATE SET
                output_path = excluded.output_path,
                source_filing_count = excluded.source_filing_count,
                vote_count = excluded.vote_count,
                parquet_sha256 = excluded.parquet_sha256,
                parquet_bytes = excluded.parquet_bytes,
                status = excluded.status,
                built_at = now(),
                last_error = NULL
            """,
            [
                year,
                str(target),
                filing_count,
                vote_count,
                _sha256(target),
                target.stat().st_size,
            ],
        )
        results.append(
            {
                "year": year,
                "filings": filing_count,
                "votes": vote_count,
                "path": str(target),
            }
        )

    paths = [
        Path(row[0]).resolve()
        for row in connection.execute(
            """
            SELECT output_path
            FROM npx_vote_files
            WHERE status = 'IMPORTED'
            ORDER BY report_year
            """
        ).fetchall()
    ]
    if paths:
        sql_paths = ", ".join(
            "'" + str(path).replace("'", "''") + "'"
            for path in paths
        )
        connection.execute(
            f"""
            CREATE OR REPLACE VIEW v_proxy_votes AS
            SELECT *
            FROM read_parquet(
                [{sql_paths}],
                union_by_name = true,
                filename = true
            )
            """
        )
    connection.execute(
        "DELETE FROM filing_table_rows WHERE table_name = 'proxy_votes'"
    )
    return {
        "years": results,
        "total_filings": sum(item["filings"] for item in results),
        "total_votes": sum(item["votes"] for item in results),
    }
