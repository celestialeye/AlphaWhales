from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .config import ResearchConfig
from .action_experiments import (
    DEFAULT_OUTPUT_DB as DEFAULT_ACTION_OUTPUT_DB,
    ActionExperimentConfig,
    load_action_report,
    run_action_experiment,
)
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
from .valuation_experiments import (
    DEFAULT_OUTPUT_DB as DEFAULT_VALUATION_OUTPUT_DB,
    load_valuation_report,
    run_valuation_method_experiment,
)


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

    action_backtest = subparsers.add_parser(
        "action-backtest",
        help=(
            "Run the standalone enter/add/hold/trim/exit AWFI challenger"
        ),
    )
    action_backtest.add_argument(
        "--parent-db",
        type=Path,
        default=DEFAULT_OUTPUT_DB,
    )
    action_backtest.add_argument(
        "--output-db",
        type=Path,
        default=DEFAULT_ACTION_OUTPUT_DB,
    )
    action_backtest.add_argument("--parent-run-id")
    action_backtest.add_argument(
        "--current-awfi-only",
        action="store_true",
        help=(
            "Freeze the AWFI v2 score profile and sweep only action thresholds"
        ),
    )
    action_backtest.add_argument(
        "--fundamentals-db",
        type=Path,
        help=(
            "Optional Investor Screening database with SEC XBRL bronze views"
        ),
    )
    action_backtest.add_argument(
        "--first-test-period",
        type=_date,
        default=date(2023, 3, 31),
    )
    action_backtest.add_argument("--replace", action="store_true")

    action_report = subparsers.add_parser(
        "action-report",
        help="Read the latest standalone AWFI action challenger report",
    )
    action_report.add_argument(
        "--output-db",
        type=Path,
        default=DEFAULT_ACTION_OUTPUT_DB,
    )
    action_report.add_argument("--run-id")

    valuation_backtest = subparsers.add_parser(
        "valuation-backtest",
        help="Backtest reconstructed historical valuation methods with AWFI",
    )
    valuation_backtest.add_argument(
        "--parent-db",
        type=Path,
        default=DEFAULT_OUTPUT_DB,
    )
    valuation_backtest.add_argument(
        "--screening-db",
        type=Path,
        default=DEFAULT_SOURCE_DB,
    )
    valuation_backtest.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/investor_screening/valuation_research"),
    )
    valuation_backtest.add_argument(
        "--output-db",
        type=Path,
        default=DEFAULT_VALUATION_OUTPUT_DB,
    )
    valuation_backtest.add_argument(
        "--parent-run-id",
        default="ebc243d9decb46624a69",
    )
    valuation_backtest.add_argument(
        "--minimum-feature-availability",
        type=float,
        default=0.50,
    )
    valuation_backtest.add_argument("--replace", action="store_true")

    valuation_report = subparsers.add_parser(
        "valuation-report",
        help="Read the latest valuation-method experiment",
    )
    valuation_report.add_argument(
        "--output-db",
        type=Path,
        default=DEFAULT_VALUATION_OUTPUT_DB,
    )
    valuation_report.add_argument("--run-id")
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
    elif args.command == "signals":
        result = load_current_signals(
            args.output_db,
            run_id=args.run_id,
            horizon=args.horizon,
        )
    elif args.command == "action-backtest":
        result = run_action_experiment(
            parent_db=args.parent_db,
            output_db=args.output_db,
            parent_run_id=args.parent_run_id,
            fundamentals_db=args.fundamentals_db,
            config=ActionExperimentConfig(
                first_test_period=args.first_test_period,
                profile_mode=(
                    "AWFI_V2_ONLY"
                    if args.current_awfi_only
                    else "ALL"
                ),
            ),
            replace=args.replace,
        )
    elif args.command == "action-report":
        result = load_action_report(
            args.output_db,
            run_id=args.run_id,
        )
    elif args.command == "valuation-backtest":
        result = run_valuation_method_experiment(
            parent_db=args.parent_db,
            screening_db=args.screening_db,
            data_dir=args.data_dir,
            output_db=args.output_db,
            parent_run_id=args.parent_run_id,
            minimum_feature_availability=(
                args.minimum_feature_availability
            ),
            replace=args.replace,
        )
    else:
        result = load_valuation_report(
            args.output_db,
            run_id=args.run_id,
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
