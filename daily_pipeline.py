from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time

from data_service import DataService
from filing_operations import FilingOperationBusyError, FilingOperations
from predictive_sentiment.publication import (
    PublicationBusyError,
    research_snapshot_needs_refresh,
    run_research_atomically,
)


def _refresh_awfi_if_stale():
    if not research_snapshot_needs_refresh():
        return "current"
    try:
        run_research_atomically()
        return "published"
    except PublicationBusyError:
        return "busy"


async def _refresh_awfi_if_stale_async():
    loop = asyncio.get_running_loop()
    for _ in range(120):
        result = await loop.run_in_executor(
            None,
            _refresh_awfi_if_stale,
        )
        if result == "published":
            return True
        if result == "current":
            return False
        await loop.run_in_executor(None, time.sleep, 5)
    raise RuntimeError("AWFI publication remained busy beyond 10 minutes")


async def _run(args):
    data_service = DataService()
    operations = FilingOperations(
        data_service,
        after_refresh=_refresh_awfi_if_stale_async,
    )
    if args.backfill_history:
        return await operations.backfill_history(
            quarters=(
                args.history_quarters
                if args.history_quarters > 0
                else None
            ),
        )
    return await operations.run(
        trigger=args.trigger,
        lookback_days=args.lookback_days,
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Check roster-relevant SEC 13F filings and refresh changed caches."
        )
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=120,
        help="SEC filing-date lookback window (default: 120).",
    )
    parser.add_argument(
        "--trigger",
        default="scheduler",
        choices=("scheduler", "manual", "repair"),
        help="Audit label for this run.",
    )
    parser.add_argument(
        "--backfill-history",
        action="store_true",
        help=(
            "Populate the filing ledger from the local Investor Screening "
            "archive without refreshing manager caches."
        ),
    )
    parser.add_argument(
        "--history-quarters",
        type=int,
        default=0,
        help=(
            "Optional newest-quarter limit for --backfill-history "
            "(default: 0, all available history)."
        ),
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(args))
    except FilingOperationBusyError as exc:
        print(json.dumps({"status": "BUSY", "error": str(exc)}))
        return 30
    except Exception as exc:
        logging.exception("Daily SEC filing check failed")
        print(json.dumps({"status": "FAILED", "error": str(exc)}))
        return 1

    print(json.dumps(result, sort_keys=True))
    return 20 if result["status"] == "PARTIAL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
