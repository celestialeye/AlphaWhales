from __future__ import annotations

from decimal import Decimal

import duckdb
from edgar import get_by_accession_number, set_identity


def verify_accession(
    connection: duckdb.DuckDBPyConnection,
    accession_number: str,
    identity: str,
) -> dict:
    set_identity(identity)
    filing = get_by_accession_number(accession_number)
    if filing is None:
        raise ValueError(f"EDGAR could not find accession {accession_number}")
    report = filing.obj()
    infotable = report.infotable
    edgar_rows = 0 if infotable is None else len(infotable)
    edgar_value = (
        Decimal(0)
        if infotable is None or infotable.empty
        else sum((Decimal(str(value)) for value in infotable["Value"]), Decimal(0))
    )
    database_row = connection.execute(
        """
        SELECT count(*), coalesce(sum(value_usd), 0)
        FROM holdings
        WHERE accession_number = ?
        """,
        [accession_number],
    ).fetchone()
    return {
        "accession_number": accession_number,
        "edgartools_rows": edgar_rows,
        "database_rows": database_row[0],
        "edgartools_value_usd": edgar_value,
        "database_value_usd": database_row[1],
        "rows_match": edgar_rows == database_row[0],
        "value_matches": edgar_value == database_row[1],
    }


def hydrate_tickers(
    connection: duckdb.DuckDBPyConnection,
    accession_number: str,
    identity: str,
) -> int:
    set_identity(identity)
    filing = get_by_accession_number(accession_number)
    if filing is None:
        raise ValueError(f"EDGAR could not find accession {accession_number}")
    report = filing.obj()
    holdings = report.holdings
    if holdings is None or holdings.empty or "Ticker" not in holdings.columns:
        return 0

    updated = 0
    ticker_rows = (
        holdings[["Cusip", "Ticker"]]
        .dropna()
        .drop_duplicates(subset=["Cusip"])
        .to_dict(orient="records")
    )
    for row in ticker_rows:
        ticker = str(row["Ticker"]).strip().upper()
        cusip = str(row["Cusip"]).strip()
        if not ticker or ticker in {"NAN", "NONE"}:
            continue
        result = connection.execute(
            """
            UPDATE holdings
            SET ticker = ?
            WHERE accession_number = ? AND cusip = ?
            RETURNING infotable_sk
            """,
            [ticker, accession_number, cusip],
        ).fetchall()
        updated += len(result)
    return updated
