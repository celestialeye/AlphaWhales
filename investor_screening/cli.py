from __future__ import annotations

import argparse
import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

from .database import DEFAULT_DATABASE_PATH, connect_database
from .detail_ingest import ingest_accessions, pending_accessions
from .edgar_catalog import catalog_period
from .edgar_verify import hydrate_tickers, verify_accession
from .flattened_bulk import (
    BULK_FAMILIES,
    discover_bulk_datasets,
    download_bulk_dataset,
    import_flattened_archive,
    refresh_bulk_integrity_metadata,
    refresh_bulk_views,
)
from .forms import FORM_FAMILIES
from .quality import coverage_summary, validate_database
from .integrity import run_integrity_audit, write_integrity_report
from .npx_votes import build_npx_vote_lake
from .screener import build_screening_snapshot
from .sec_bulk import discover_datasets, download_dataset, import_archive

DEFAULT_START_DATE = date(2013, 7, 1)


class JsonEncoder(json.JSONEncoder):
    def default(self, value):
        if isinstance(value, (date, Decimal, Path)):
            return str(value)
        return super().default(value)


def _print(value) -> None:
    print(json.dumps(value, indent=2, cls=JsonEncoder))


def _identity(args) -> str:
    identity = args.identity or os.environ.get("EDGAR_IDENTITY")
    if not identity:
        raise ValueError(
            "SEC network access requires --identity or the EDGAR_IDENTITY environment variable"
        )
    return identity


def _bulk_family(args) -> str:
    family = args.bulk_family or args.family
    if not family:
        raise ValueError("Choose a bulk family: insider, nport, or nmfp")
    return family


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mine official SEC Form 13F data into an analytical DuckDB database."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--identity", help="SEC identity in 'Name email@example.com' form")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create or upgrade the DuckDB schema")

    list_parser = subparsers.add_parser("list-datasets", help="List official SEC archives")
    list_parser.add_argument("--start-date", type=date.fromisoformat, default=DEFAULT_START_DATE)

    backfill_parser = subparsers.add_parser(
        "backfill",
        help="Download and import official SEC archives",
    )
    backfill_parser.add_argument("--start-date", type=date.fromisoformat, default=DEFAULT_START_DATE)
    backfill_parser.add_argument("--end-date", type=date.fromisoformat)
    backfill_parser.add_argument("--limit", type=int, help="Maximum number of archives to import")
    backfill_parser.add_argument("--force", action="store_true")

    list_bulk_parser = subparsers.add_parser(
        "list-bulk-datasets",
        help="List official SEC Insider, N-PORT, or N-MFP flattened archives",
    )
    list_bulk_parser.add_argument("family", nargs="?", choices=sorted(BULK_FAMILIES))
    list_bulk_parser.add_argument(
        "--family",
        dest="bulk_family",
        choices=sorted(BULK_FAMILIES),
    )
    list_bulk_parser.add_argument("--start-date", type=date.fromisoformat)
    list_bulk_parser.add_argument("--end-date", type=date.fromisoformat)
    list_bulk_parser.add_argument("--limit", "--dataset-limit", type=int)

    backfill_bulk_parser = subparsers.add_parser(
        "backfill-bulk",
        help="Download SEC flattened archives into the Parquet bronze lake",
    )
    backfill_bulk_parser.add_argument("family", nargs="?", choices=sorted(BULK_FAMILIES))
    backfill_bulk_parser.add_argument(
        "--family",
        dest="bulk_family",
        choices=sorted(BULK_FAMILIES),
    )
    backfill_bulk_parser.add_argument("--start-date", type=date.fromisoformat)
    backfill_bulk_parser.add_argument("--end-date", type=date.fromisoformat)
    backfill_bulk_parser.add_argument("--limit", "--dataset-limit", type=int)
    backfill_bulk_parser.add_argument(
        "--delete-archives-after-import",
        action="store_true",
        help="Delete ZIPs only after successful, reconciled imports",
    )

    archive_parser = subparsers.add_parser("import-archive", help="Import an existing SEC ZIP")
    archive_parser.add_argument("archive", type=Path)
    archive_parser.add_argument("--source-url")

    bulk_archive_parser = subparsers.add_parser(
        "import-bulk-archive",
        help="Import an existing SEC flattened ZIP into the Parquet bronze lake",
    )
    bulk_archive_parser.add_argument("family", choices=sorted(BULK_FAMILIES))
    bulk_archive_parser.add_argument("archive", type=Path)
    bulk_archive_parser.add_argument("--source-url")
    bulk_archive_parser.add_argument(
        "--delete-archives-after-import",
        action="store_true",
        help="Delete the ZIP only after a successful, reconciled import",
    )

    subparsers.add_parser(
        "refresh-bulk-views",
        help="Create DuckDB bronze views over every imported Parquet source table",
    )
    subparsers.add_parser(
        "refresh-integrity-metadata",
        help="Record SHA-256 and byte counts for imported Parquet files",
    )
    subparsers.add_parser(
        "refresh-npx-votes",
        help="Build yearly lossless N-PX vote Parquet files from raw submissions",
    )
    subparsers.add_parser(
        "refresh-screening",
        help="Build the read-only Investor Screening snapshot",
    )
    integrity_parser = subparsers.add_parser(
        "audit-integrity",
        help="Run full archive, Parquet, raw-filing, and coverage checks",
    )
    integrity_parser.add_argument(
        "--quick",
        action="store_true",
        help="Skip archive and raw-file SHA-256 verification",
    )
    integrity_parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/investor_screening/integrity-report.json"),
    )

    detail_parser = subparsers.add_parser(
        "ingest-details",
        help="Download raw submissions and persist full EdgarTools detail objects",
    )
    detail_parser.add_argument(
        "--family",
        choices=[*sorted(FORM_FAMILIES), "all"],
        default="all",
    )
    detail_parser.add_argument("--start-date", type=date.fromisoformat)
    detail_parser.add_argument("--end-date", type=date.fromisoformat)
    detail_parser.add_argument("--limit", type=int)
    detail_parser.add_argument("--retry-failed", action="store_true")
    detail_parser.add_argument(
        "--retry-incomplete",
        action="store_true",
        help="Retry failed, partial, and XML-fallback artifacts",
    )
    detail_parser.add_argument(
        "--force",
        action="store_true",
        help="Reingest every matching filing, including successful artifacts",
    )
    detail_parser.add_argument("--raw-dir", type=Path)
    detail_parser.add_argument("--workers", type=int, default=4)
    detail_parser.add_argument(
        "--verbose-results",
        action="store_true",
        help="Print one result object per filing instead of aggregate counts",
    )

    subparsers.add_parser("validate", help="Run database completeness and consistency checks")
    subparsers.add_parser("status", help="Show database coverage")

    catalog_parser = subparsers.add_parser(
        "catalog",
        help="Catalog relevant SEC filings through edgartools without parsing documents",
    )
    catalog_parser.add_argument("--start-year", type=int, required=True)
    catalog_parser.add_argument("--end-year", type=int)
    catalog_parser.add_argument(
        "--family",
        choices=[*sorted(FORM_FAMILIES), "all"],
        default="all",
    )
    catalog_parser.add_argument("--quarter", type=int, choices=[1, 2, 3, 4])
    catalog_parser.add_argument("--limit-per-quarter", type=int)

    verify_parser = subparsers.add_parser(
        "verify-accession",
        help="Compare one imported filing with edgartools",
    )
    verify_parser.add_argument("accession_number")

    ticker_parser = subparsers.add_parser(
        "hydrate-tickers",
        help="Resolve ticker symbols for one filing through edgartools",
    )
    ticker_parser.add_argument("accession_number")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    connection = connect_database(args.database)
    try:
        if args.command == "init":
            _print({"database": str(Path(args.database).resolve()), "status": "initialized"})
        elif args.command == "list-datasets":
            datasets = [
                item
                for item in discover_datasets(_identity(args))
                if item.period_end >= args.start_date
            ]
            _print([item.__dict__ for item in datasets])
        elif args.command == "backfill":
            datasets = [
                item
                for item in discover_datasets(_identity(args))
                if item.period_end >= args.start_date
                and (args.end_date is None or item.period_start <= args.end_date)
            ]
            if args.limit:
                datasets = datasets[: args.limit]
            results = []
            for dataset in datasets:
                prior = connection.execute(
                    "SELECT status FROM datasets WHERE dataset_id = ?",
                    [dataset.dataset_id],
                ).fetchone()
                if prior and prior[0] == "IMPORTED" and not args.force:
                    results.append({"dataset_id": dataset.dataset_id, "status": "skipped"})
                    continue
                archive = download_dataset(dataset, _identity(args))
                result = import_archive(
                    connection,
                    archive,
                    source_url=dataset.url,
                    period_start=dataset.period_start,
                    period_end=dataset.period_end,
                )
                results.append(result)
            _print(results)
        elif args.command == "list-bulk-datasets":
            family = _bulk_family(args)
            datasets = [
                item
                for item in discover_bulk_datasets(family, _identity(args))
                if (
                    args.start_date is None
                    or item.period_end >= args.start_date
                )
                and (
                    args.end_date is None
                    or item.period_start <= args.end_date
                )
            ]
            if args.limit is not None:
                datasets = datasets[: args.limit]
            _print([item.__dict__ for item in datasets])
        elif args.command == "backfill-bulk":
            family = _bulk_family(args)
            identity = _identity(args)
            datasets = [
                item
                for item in discover_bulk_datasets(family, identity)
                if (
                    args.start_date is None
                    or item.period_end >= args.start_date
                )
                and (
                    args.end_date is None
                    or item.period_start <= args.end_date
                )
            ]
            if args.limit is not None:
                datasets = datasets[: args.limit]
            results = []
            for dataset in datasets:
                prior = connection.execute(
                    """
                    SELECT status
                    FROM bulk_datasets
                    WHERE family = ? AND dataset_id = ?
                    """,
                    [dataset.family, dataset.dataset_id],
                ).fetchone()
                if prior and prior[0] == "IMPORTED":
                    results.append(
                        {
                            "family": dataset.family,
                            "dataset_id": dataset.dataset_id,
                            "status": "skipped",
                        }
                    )
                    continue
                archive = download_bulk_dataset(
                    dataset,
                    identity,
                    download_dir=Path(args.database).resolve().parent / "downloads",
                )
                results.append(
                    import_flattened_archive(
                        connection,
                        archive,
                        dataset.family,
                        source_url=dataset.url,
                        period_start=dataset.period_start,
                        period_end=dataset.period_end,
                        lake_dir=Path(args.database).resolve().parent / "lake",
                        delete_archive=args.delete_archives_after_import,
                    )
                )
            _print(results)
        elif args.command == "import-archive":
            _print(
                import_archive(
                    connection,
                    args.archive,
                    source_url=args.source_url,
                )
            )
        elif args.command == "import-bulk-archive":
            _print(
                import_flattened_archive(
                    connection,
                    args.archive,
                    args.family,
                    source_url=args.source_url,
                    lake_dir=Path(args.database).resolve().parent / "lake",
                    delete_archive=args.delete_archives_after_import,
                )
            )
        elif args.command == "refresh-bulk-views":
            views = refresh_bulk_views(connection)
            _print({"view_count": len(views), "views": views})
        elif args.command == "refresh-integrity-metadata":
            _print(refresh_bulk_integrity_metadata(connection))
        elif args.command == "refresh-npx-votes":
            _print(build_npx_vote_lake(connection))
        elif args.command == "refresh-screening":
            connection.close()
            connection = None
            _print(build_screening_snapshot(args.database))
        elif args.command == "audit-integrity":
            connection.close()
            connection = None
            report = run_integrity_audit(
                args.database,
                verify_hashes=not args.quick,
            )
            output_path = write_integrity_report(report, args.output)
            _print(
                {
                    "complete": report["complete"],
                    "errors": report["errors"],
                    "warnings": report["warnings"],
                    "output": str(output_path),
                }
            )
            if not report["complete"]:
                return 1
        elif args.command == "ingest-details":
            accessions = pending_accessions(
                connection,
                family=args.family,
                start_date=args.start_date,
                end_date=args.end_date,
                retry_failed=args.retry_failed,
                retry_incomplete=args.retry_incomplete,
                force=args.force,
                limit=args.limit,
            )
            result = ingest_accessions(
                connection,
                accessions,
                identity=_identity(args),
                raw_dir=args.raw_dir or Path(args.database).resolve().parent / "raw",
                include_results=args.verbose_results,
                workers=args.workers,
            )
            _print(result)
            if result["failure_count"]:
                return 1
        elif args.command == "validate":
            _print(validate_database(connection))
        elif args.command == "status":
            _print(coverage_summary(connection))
        elif args.command == "catalog":
            end_year = args.end_year or args.start_year
            quarters = [args.quarter] if args.quarter else [1, 2, 3, 4]
            _print(
                catalog_period(
                    connection,
                    years=range(args.start_year, end_year + 1),
                    quarters=quarters,
                    family=args.family,
                    identity=_identity(args),
                    limit_per_quarter=args.limit_per_quarter,
                )
            )
        elif args.command == "verify-accession":
            _print(verify_accession(connection, args.accession_number, _identity(args)))
        elif args.command == "hydrate-tickers":
            updated = hydrate_tickers(connection, args.accession_number, _identity(args))
            _print({"accession_number": args.accession_number, "updated_rows": updated})
        return 0
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
