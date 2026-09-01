"""
Pre-fetch script to warm the local 13F disk cache for the configured roster.
Run:
    python prefetch.py
    python prefetch.py --history
    python prefetch.py --history-only
"""
import argparse
import sys
import os
import asyncio
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_service import DataService
from config import FUND_MANAGERS


def is_retryable_failure(fund_data):
    if fund_data.get("status") == "loaded":
        return False
    message = str(fund_data.get("error") or "").lower()
    return fund_data.get("status") == "error" or any(
        marker in message
        for marker in (
            "timed out",
            "timeout",
            "temporarily unavailable",
            "connection",
            "read operation",
        )
    )


async def retry_latest_failures(ds, attempts):
    retried = False
    for attempt in range(1, attempts + 1):
        pending = [
            cik for cik, fund_data in ds.cache.items()
            if is_retryable_failure(fund_data)
        ]
        if not pending:
            return retried
        retried = True
        print(
            f"\n[13F Tracker] Latest-cache retry {attempt}/{attempts}: "
            f"{len(pending)} managers"
        )
        for index, cik in enumerate(pending, start=1):
            print(f"         [{index:02d}/{len(pending):02d}] {cik}")
            await ds.refresh_fund(cik)
            await asyncio.sleep(0.25)
    return retried


async def retry_period_failures(ds, period, period_cache, attempts):
    fund_by_cik = {fund["cik"]: fund for fund in FUND_MANAGERS}
    loop = asyncio.get_running_loop()
    for attempt in range(1, attempts + 1):
        pending = [
            cik for cik, fund_data in period_cache.items()
            if is_retryable_failure(fund_data)
        ]
        if not pending:
            return
        print(
            f"         Retry {attempt}/{attempts}: "
            f"{len(pending)} unavailable managers"
        )
        for cik in pending:
            result = await loop.run_in_executor(
                None,
                ds._fetch_fund_period_sync,
                fund_by_cik[cik],
                period,
            )
            period_cache[cik].update(result)
            period_cache[cik]["last_updated"] = (
                datetime.now(timezone.utc).isoformat()
            )
            await asyncio.sleep(0.25)
        ds._save_period_cache_to_disk(period, period_cache)


async def prefetch_history(ds, retry_attempts):
    periods = ds.get_available_periods(count=20)
    historical_periods = periods[1:]
    print(f"\n[13F Tracker] Prefetching {len(historical_periods)} historical quarter snapshots...")

    for index, period in enumerate(historical_periods, start=1):
        cache_path = ds._get_period_cache_path(period)
        was_cached = os.path.exists(cache_path)
        print(
            f"[{index:02d}/{len(historical_periods):02d}] {period} "
            f"{'(cached)' if was_cached else '(fetching SEC filings)'}"
        )
        period_cache = await ds.get_period_cache(period)
        await retry_period_failures(
            ds,
            period,
            period_cache,
            retry_attempts,
        )
        loaded = sum(
            1 for fund_data in period_cache.values()
            if fund_data.get("status") == "loaded"
        )
        print(f"         {loaded}/{len(period_cache)} funds available")

async def prefetch_ticker_intelligence(ds):
    tickers = sorted(
        ds.get_ticker_view(),
        key=lambda item: (
            item.get("num_holders", 0),
            item.get("total_value_across_funds", 0),
        ),
        reverse=True,
    )
    popular_tickers = [
        item["ticker"]
        for item in tickers
        if item.get("ticker")
    ][:12]
    print(
        f"\n[Alpha Whales] Prefetching {len(popular_tickers)} "
        "current consensus ticker intelligence snapshots..."
    )
    for index, ticker in enumerate(popular_tickers, start=1):
        print(f"[{index:02d}/{len(popular_tickers):02d}] {ticker}")
        intelligence, pair = await asyncio.gather(
            ds.get_ticker_intelligence(ticker),
            ds.get_pair_signal(ticker),
            return_exceptions=True
        )
        market_price = (
            intelligence["market"]["quote"].get("last_price")
            if isinstance(intelligence, dict)
            else f"error: {intelligence}"
        )
        pair_status = (
            pair.get("status", "UNAVAILABLE")
            if isinstance(pair, dict)
            else f"error: {pair}"
        )
        print(
            f"         market={market_price} pair={pair_status}"
        )

async def main():
    parser = argparse.ArgumentParser(description="Warm Alpha Whales SEC caches.")
    parser.add_argument(
        "--history",
        action="store_true",
        help="Refresh the latest quarter, then prefetch all selectable historical quarters."
    )
    parser.add_argument(
        "--history-only",
        action="store_true",
        help="Prefetch selectable historical quarters without refreshing the latest quarter."
    )
    parser.add_argument(
        "--ticker-intelligence",
        action="store_true",
        help="Also prefetch OpenBB and pair signals for popular ticker shortcuts."
    )
    parser.add_argument(
        "--ticker-intelligence-only",
        action="store_true",
        help="Prefetch popular ticker intelligence without refreshing SEC fund caches."
    )
    parser.add_argument(
        "--retry-attempts",
        type=int,
        default=1,
        help="Sequential retry passes for unavailable latest and historical managers."
    )
    args = parser.parse_args()
    retry_attempts = max(0, min(3, args.retry_attempts))

    ds = DataService()
    if not args.history_only and not args.ticker_intelligence_only:
        print(
            f"[13F Tracker] Starting latest cache warm-up for all "
            f"{len(FUND_MANAGERS)} Fund Managers..."
        )
        await ds.refresh_all()
        retried_latest = await retry_latest_failures(ds, retry_attempts)
        if retried_latest:
            await ds.refresh_market_insights()
        overview = ds.get_overview()
        print("\n[SUCCESS] Latest Warm-up Complete!")
        print(f"Funds Loaded: {overview['loaded_funds']} / {overview['total_funds']}")
        print(f"Total Tracked AUM: ${overview['total_aum_b']} Billion (${overview['total_aum_m']} Million)")
        print(f"Unique Tickers: {overview['total_tickers']}")
        print(f"Total QoQ Moves: {overview['moves_summary']['total_moves']}")

    if args.history or args.history_only:
        await prefetch_history(ds, retry_attempts)
        print("\n[SUCCESS] Historical warm-up complete.")
    if args.ticker_intelligence or args.ticker_intelligence_only:
        await prefetch_ticker_intelligence(ds)
        print("\n[SUCCESS] Ticker intelligence warm-up complete.")

if __name__ == "__main__":
    asyncio.run(main())
