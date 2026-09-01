from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .config import ResearchConfig
from .pipeline import (
    DEFAULT_APPLICATION_CACHE_DIR,
    DEFAULT_OUTPUT_DB,
    DEFAULT_PERFORMANCE_DB,
    DEFAULT_ROSTER,
    DEFAULT_SOURCE_DB,
    load_current_signals,
    load_report,
    validate_inputs,
)
from .publication import run_research_atomically


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _json_default(value):
    if isinstance(value, (date, Path)):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _clean_json(value):
    if isinstance(value, dict):
        return {key: _clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline Alpha Whale Sentiment predictive research"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-inputs")
    validate.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    validate.add_argument(
        "--performance-db", type=Path, default=DEFAULT_PERFORMANCE_DB
    )
    validate.add_argument("--roster", type=Path, default=DEFAULT_ROSTER)

    run = subparsers.add_parser("run")
    run.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    run.add_argument(
        "--performance-db", type=Path, default=DEFAULT_PERFORMANCE_DB
    )
    run.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    run.add_argument("--roster", type=Path, default=DEFAULT_ROSTER)
    run.add_argument(
        "--application-cache-dir",
        type=Path,
        default=DEFAULT_APPLICATION_CACHE_DIR,
    )
    run.add_argument("--as-of-days", type=int, default=45)
    run.add_argument(
        "--first-test-period", type=_date, default=date(2023, 3, 31)
    )
    run.add_argument("--replace", action="store_true")

    report = subparsers.add_parser("report")
    report.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    report.add_argument("--run-id")

    signals = subparsers.add_parser("signals")
    signals.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    signals.add_argument("--run-id")
    signals.add_argument(
        "--horizon",
        type=int,
        choices=(126, 252, 378, 504),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "validate-inputs":
        result = validate_inputs(
            source_db=args.source_db,
            performance_db=args.performance_db,
            roster_path=args.roster,
        )
    elif args.command == "run":
        result = asdict(
            run_research_atomically(
                source_db=args.source_db,
                performance_db=args.performance_db,
                output_db=args.output_db,
                roster_path=args.roster,
                application_cache_dir=args.application_cache_dir,
                config=ResearchConfig(
                    as_of_days=args.as_of_days,
                    first_test_period=args.first_test_period,
                ),
                replace=args.replace,
            )
        )
    elif args.command == "report":
        result = load_report(args.output_db, run_id=args.run_id)
    else:
        result = load_current_signals(
            args.output_db,
            run_id=args.run_id,
            horizon=args.horizon,
        )
    print(
        json.dumps(
            _clean_json(result),
            indent=2,
            sort_keys=True,
            default=_json_default,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
