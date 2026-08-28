"""
Pre-fetch script to warm the local 13F disk cache for all 26 funds.
Run:
    python prefetch.py
    python prefetch.py --history
    python prefetch.py --history-only
"""
import argparse
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_service import DataService

POPULAR_TICKERS = [
    "AAPL", "GOOGL", "GOOG", "MSFT", "AMZN", "META",
    "NVDA", "FICO", "SPGI", "MCO", "MA", "V"
]

async def prefetch_history(ds):
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
        loaded = sum(
            1 for fund_data in period_cache.values()
            if fund_data.get("status") == "loaded"
        )
        print(f"         {loaded}/{len(period_cache)} funds available")

async def prefetch_ticker_intelligence(ds):
    print(f"\n[Alpha Whales] Prefetching {len(POPULAR_TICKERS)} popular ticker intelligence snapshots...")
    for index, ticker in enumerate(POPULAR_TICKERS, start=1):
        print(f"[{index:02d}/{len(POPULAR_TICKERS):02d}] {ticker}")
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
    args = parser.parse_args()

    ds = DataService()
    if not args.history_only and not args.ticker_intelligence_only:
        print("[13F Tracker] Starting latest cache warm-up for all 26 Fund Managers...")
        await ds.refresh_all()
        overview = ds.get_overview()
        print("\n[SUCCESS] Latest Warm-up Complete!")
        print(f"Funds Loaded: {overview['loaded_funds']} / {overview['total_funds']}")
        print(f"Total Tracked AUM: ${overview['total_aum_b']} Billion (${overview['total_aum_m']} Million)")
        print(f"Unique Tickers: {overview['total_tickers']}")
        print(f"Total QoQ Moves: {overview['moves_summary']['total_moves']}")

    if args.history or args.history_only:
        await prefetch_history(ds)
        print("\n[SUCCESS] Historical warm-up complete.")
    if args.ticker_intelligence or args.ticker_intelligence_only:
        await prefetch_ticker_intelligence(ds)
        print("\n[SUCCESS] Ticker intelligence warm-up complete.")

if __name__ == "__main__":
    asyncio.run(main())
