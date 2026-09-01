import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, Request, BackgroundTasks
from pydantic import BaseModel
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse
from config import FUND_MANAGERS, ROSTER_PATH
from data_service import DataService
from awfi_service import AwfiService
from predictive_sentiment.publication import (
    PublicationBusyError,
    research_snapshot_needs_refresh,
    run_research_atomically,
)
from investor_screening.screener import ScreeningService
from roster_store import (
    ALLOWED_ROSTER_GROUPS,
    RosterStore,
    normalize_cik,
)

logger = logging.getLogger(__name__)
data_service = DataService()
screening_service = ScreeningService()
awfi_service = AwfiService()
roster_store = RosterStore(ROSTER_PATH, FUND_MANAGERS)
awfi_refresh_state = "idle"
awfi_refresh_lock = asyncio.Lock()


class RosterMutationRequest(BaseModel):
    action: Literal["include", "exclude"]
    ciks: list[str]
    is_exception: bool = False
    group: str | None = None

def _refresh_awfi_research_if_stale():
    global awfi_refresh_state
    try:
        awfi_refresh_state = "checking"
        if not research_snapshot_needs_refresh():
            awfi_refresh_state = "current"
            return False
        awfi_refresh_state = "building"
        logger.info("AWFI history is stale; rebuilding the research snapshot")
        run_research_atomically()
        awfi_refresh_state = "published"
        logger.info("AWFI research snapshot published")
        return True
    except PublicationBusyError as exc:
        awfi_refresh_state = "external_build"
        logger.info("AWFI research refresh skipped: %s", exc)
        return False
    except Exception:
        awfi_refresh_state = "error"
        logger.exception("AWFI research refresh failed")
        return False


async def _refresh_awfi_research_if_stale_async():
    async with awfi_refresh_lock:
        loop = asyncio.get_running_loop()
        for _ in range(120):
            published = await loop.run_in_executor(
                None,
                _refresh_awfi_research_if_stale,
            )
            if published:
                await data_service.broadcast_event({
                    "type": "awfi_published",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            if awfi_refresh_state != "external_build":
                return
            await asyncio.sleep(5)
        logger.warning(
            "AWFI publication remained busy beyond the retry window"
        )


async def _refresh_all_data():
    completed = await data_service.refresh_all()
    if completed:
        await _refresh_awfi_research_if_stale_async()


async def _refresh_funds_and_awfi(ciks):
    await data_service.refresh_funds(ciks)
    await _refresh_awfi_research_if_stale_async()


async def _refresh_roster_context_and_awfi():
    await data_service.refresh_roster_market_context()
    await _refresh_awfi_research_if_stale_async()


def _awfi_snapshot_version():
    path = getattr(awfi_service, "database_path", None)
    return (
        path.stat().st_mtime_ns
        if path is not None and path.is_file()
        else None
    )


def _effective_awfi_refresh_state():
    return awfi_refresh_state


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    task = asyncio.create_task(
        data_service.auto_refresh_loop(
            _refresh_awfi_research_if_stale_async
        )
    )
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def static_asset_version(relative_path: str) -> int:
    return os.stat(
        os.path.join(BASE_DIR, "static", relative_path)
    ).st_mtime_ns


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.globals["static_asset_version"] = static_asset_version


@app.middleware("http")
async def disable_html_caching(request: Request, call_next):
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/ticker", response_class=HTMLResponse)
async def ticker_view(request: Request):
    return templates.TemplateResponse("ticker.html", {"request": request, "ticker": None})

@app.get("/ticker/{ticker}", response_class=HTMLResponse)
async def ticker_view_specific(request: Request, ticker: str):
    return templates.TemplateResponse("ticker.html", {"request": request, "ticker": ticker})

@app.get("/investor", response_class=HTMLResponse)
async def investor_view(request: Request):
    return templates.TemplateResponse("investor.html", {"request": request, "cik": None})

@app.get("/investor/{cik}", response_class=HTMLResponse)
async def investor_view_specific(request: Request, cik: str):
    return templates.TemplateResponse("investor.html", {"request": request, "cik": cik})

@app.get("/screening", response_class=HTMLResponse)
async def screening_view(request: Request):
    return templates.TemplateResponse("screening.html", {"request": request})


# --- API Routes ---

async def _resolve_period_awfi(
    selected_period: str,
    periods: list[str],
    period_cache: dict,
    tickers: list[dict],
    changes: list[dict],
) -> dict:
    awfi_result = awfi_service.get_period_scores(
        selected_period,
        latest_application_period=(
            periods[0] if selected_period == periods[0] else None
        ),
    )
    if (
        selected_period == periods[0]
        and awfi_result["metadata"]["state"] not in {"READY", "LIVE"}
    ):
        sentiment_tickers = list(dict.fromkeys(
            item["ticker"] for item in tickers
        ))
        sentiment_summaries = (
            data_service.get_ticker_sentiment_summaries(
                sentiment_tickers,
                changes,
            )
        )
        live_awfi = awfi_service.compute_live_period_scores(
            selected_period,
            period_cache=period_cache,
            ticker_rows=tickers,
            sentiment_summaries=sentiment_summaries,
        )
        if live_awfi["scores"]:
            awfi_result = live_awfi
    return awfi_result

@app.get("/api/qoq-changes")
async def api_qoq_changes(
    group: str = None,
    status: str = None,
    min_value: float = None,
    include_unchanged: bool = False
):
    changes = data_service.get_qoq_changes(include_unchanged=include_unchanged)

    # Filtering
    if group and group != "All":
        changes = [c for c in changes if c["group"] == group]
    if status:
        statuses = status.split(",")
        changes = [c for c in changes if c["status"] in statuses]
    if min_value is not None:
        changes = [c for c in changes if abs(c["value_change"]) >= min_value]

    return {"data": changes, "overview": data_service.get_overview()}

@app.get("/api/ticker-view")
async def api_ticker_all():
    return {"data": data_service.get_ticker_view()}

@app.get("/api/portfolio-stats")
async def api_portfolio_stats():
    return {
        "two_quarter_buys": data_service.get_two_quarter_buys(),
        "near_52_week_low": data_service.get_near_52_week_low(),
        "market_last_updated": data_service.market_last_updated,
        "market_is_refreshing": data_service.is_market_refreshing
    }

@app.get("/api/filing-periods")
async def api_filing_periods():
    periods = data_service.get_available_periods(count=20)
    return {
        "periods": periods,
        "latest": periods[0] if periods else None
    }

@app.get("/api/period-cache-status")
async def api_period_cache_status(period: str):
    return data_service.get_period_cache_status(period)

@app.get("/api/period-view")
async def api_period_view(period: str = None):
    periods = data_service.get_available_periods(count=20)
    if not periods:
        return JSONResponse(
            status_code=503,
            content={"error": "No filing periods are available"}
        )

    selected_period = period or periods[0]
    try:
        period_cache = await data_service.get_period_cache(selected_period)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    changes = data_service.get_qoq_changes(
        include_unchanged=True,
        fund_cache=period_cache,
    )
    tickers = data_service.get_ticker_view(fund_cache=period_cache)
    most_owned = sorted(
        tickers,
        key=lambda item: (
            item["num_holders"],
            item["total_value_across_funds"],
        ),
        reverse=True,
    )[:10]
    highest_weight = sorted(
        (
            item for item in tickers
            if item["num_holders"] >= 5
        ),
        key=lambda item: (
            item["median_weight"],
            item["num_holders"],
        ),
        reverse=True,
    )[:10]
    awfi_result = await _resolve_period_awfi(
        selected_period,
        periods,
        period_cache,
        tickers,
        changes,
    )
    awfi_scores = awfi_result["scores"]
    for item in tickers:
        item["awfi"] = awfi_scores.get(
            str(item["ticker"]).strip().upper(),
            {},
        )

    return {
        "period": selected_period,
        "periods": periods,
        "cache_status": data_service.get_period_cache_status(selected_period),
        "changes": changes,
        "tickers": tickers,
        "awfi_metadata": awfi_result["metadata"],
        "funds": data_service.get_fund_status(fund_cache=period_cache),
        "overview": data_service.get_overview(fund_cache=period_cache),
        "portfolio_stats": {
            "near_52_week_low": data_service.get_near_52_week_low(
                fund_cache=period_cache
            ),
            "market_last_updated": data_service.market_last_updated,
            "market_is_refreshing": data_service.is_market_refreshing
        }
    }

@app.get("/api/ticker/{ticker}")
async def api_ticker_specific(ticker: str):
    periods = data_service.get_available_periods(count=20)
    selected_period = periods[0] if periods else None
    period_cache = (
        await data_service.get_period_cache(selected_period)
        if selected_period
        else data_service.cache
    )
    tickers = data_service.get_ticker_view(fund_cache=period_cache)
    normalized = ticker.strip().upper()
    data = next(
        (
            item for item in tickers
            if str(item.get("ticker", "")).strip().upper() == normalized
        ),
        None,
    )
    if not data:
        return JSONResponse(status_code=404, content={"error": "Ticker not found or no holdings"})
    changes = data_service.get_qoq_changes(
        include_unchanged=True,
        fund_cache=period_cache,
    )
    awfi_result = await _resolve_period_awfi(
        selected_period,
        periods,
        period_cache,
        tickers,
        changes,
    )
    data["awfi"] = awfi_result["scores"].get(normalized, {})
    data["awfi_metadata"] = awfi_result["metadata"]
    awfi_history = awfi_service.get_ticker_history(normalized)
    current_awfi = data["awfi"]
    if current_awfi:
        current_entry = {
            "period": selected_period,
            "scores": current_awfi,
        }
        awfi_history = [
            item
            for item in awfi_history
            if item["period"] != selected_period
        ]
        awfi_history.append(current_entry)
        awfi_history.sort(key=lambda item: item["period"])
        awfi_history = awfi_history[-20:]
    data["awfi_history"] = awfi_history
    data["awfi_history_version"] = _awfi_snapshot_version()
    return {"data": data}


@app.get("/api/ticker/{ticker}/awfi-history")
async def api_ticker_awfi_history(ticker: str):
    normalized = ticker.strip().upper()
    return {
        "ticker": normalized,
        "history": awfi_service.get_ticker_history(normalized),
        "snapshot_version": _awfi_snapshot_version(),
        "refresh_state": _effective_awfi_refresh_state(),
    }


@app.get("/api/ticker/{ticker}/intelligence")
async def api_ticker_intelligence(ticker: str):
    try:
        data = await data_service.get_ticker_intelligence(ticker)
        return {"data": data}
    except Exception as e:
        return JSONResponse(
            status_code=502,
            content={
                "error": f"Could not load market intelligence for {ticker.upper()}",
                "detail": str(e)
            }
        )

@app.get("/api/ticker/{ticker}/pair-signal")
async def api_ticker_pair_signal(ticker: str):
    try:
        data = await data_service.get_pair_signal(ticker)
        return {"data": data}
    except Exception as e:
        return JSONResponse(
            status_code=502,
            content={
                "error": f"Could not calculate pair signal for {ticker.upper()}",
                "detail": str(e)
            }
        )

@app.get("/api/investor-view")
async def api_investor_all():
    return {"data": data_service.get_fund_status()}

@app.get("/api/investor/{cik}")
async def api_investor_specific(cik: str):
    data = data_service.get_investor_view(cik)
    if not data:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None,
            lambda: screening_service.get_investor_detail(cik),
        )
    if not data:
        return JSONResponse(status_code=404, content={"error": "Investor not found"})
    return {"data": data}

@app.get("/api/investor/{cik}/history")
async def api_investor_history(cik: str):
    try:
        data = await data_service.get_investor_history(cik)
        if not data:
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(
                None,
                lambda: screening_service.get_investor_history(cik),
            )
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        logger.error(f"Investor history failed for {cik}: {e}")
        return JSONResponse(
            status_code=502,
            content={
                "error": f"Could not load investor history for {cik}",
                "detail": str(e)
            }
        )
    if not data:
        return JSONResponse(status_code=404, content={"error": "Investor not found"})
    return {"data": data}

@app.get("/api/screening")
async def api_screening(
    minimum_size_billions: float = 10.0,
    minimum_stock_count: int = 1,
    minimum_direct_stock_pct: float = 80.0,
    minimum_top10_pct: float = 40.0,
    minimum_concentration_quarters: int = 6,
    minimum_best_bet_weight_pct: float = 3.0,
    best_bet_duration_months: int = 12,
    minimum_best_bet_count: int = 1,
    benchmark_hurdle: str = "none",
    minimum_excess_cagr_pct: float = 0.0,
    minimum_beat_consistency_pct: float = None,
    maximum_drawdown_pct: float = None,
    maximum_turnover_pct: float = None,
    require_durable_position: bool = False,
    roster_only: bool = False,
    performance_window: str = "3Y",
    minimum_spy_excess_cagr_pct: float = None,
    minimum_qqq_excess_cagr_pct: float = None,
    require_performance: bool = False,
    search: str = None
):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: screening_service.get_screening_results(
            minimum_size_billions=max(0.1, minimum_size_billions),
            minimum_stock_count=min(10, max(1, minimum_stock_count)),
            minimum_direct_stock_pct=min(100.0, max(0.0, minimum_direct_stock_pct)),
            minimum_top10_pct=min(100.0, max(0.0, minimum_top10_pct)),
            minimum_concentration_quarters=min(
                8,
                max(1, minimum_concentration_quarters)
            ),
            minimum_best_bet_weight_pct=min(
                10.0,
                max(1.0, minimum_best_bet_weight_pct),
            ),
            best_bet_duration_months=(
                best_bet_duration_months
                if best_bet_duration_months in {6, 12, 18, 24}
                else 12
            ),
            minimum_best_bet_count=min(
                10,
                max(1, minimum_best_bet_count),
            ),
            benchmark_hurdle=(
                benchmark_hurdle.lower()
                if benchmark_hurdle.lower() in {"none", "spy", "qqq", "both"}
                else "none"
            ),
            minimum_excess_cagr=max(0.0, minimum_excess_cagr_pct) / 100,
            minimum_beat_consistency=(
                min(100.0, max(0.0, minimum_beat_consistency_pct)) / 100
                if minimum_beat_consistency_pct is not None
                else None
            ),
            maximum_drawdown=(
                min(100.0, max(0.0, maximum_drawdown_pct)) / 100
                if maximum_drawdown_pct is not None
                else None
            ),
            maximum_turnover_pct=(
                max(0.0, maximum_turnover_pct)
                if maximum_turnover_pct is not None
                else None
            ),
            require_durable_position=require_durable_position,
            roster_only=roster_only,
            performance_window=performance_window,
            minimum_spy_excess_cagr=(
                minimum_spy_excess_cagr_pct / 100
                if minimum_spy_excess_cagr_pct is not None
                else None
            ),
            minimum_qqq_excess_cagr=(
                minimum_qqq_excess_cagr_pct / 100
                if minimum_qqq_excess_cagr_pct is not None
                else None
            ),
            require_performance=require_performance,
            search=search
        )
    )


@app.get("/api/roster")
async def api_roster():
    roster = roster_store.snapshot()
    return {
        "data": roster,
        "count": len(roster),
        "exception_count": sum(
            1 for item in roster
            if item.get("is_exception")
        ),
    }


@app.post("/api/roster")
async def api_update_roster(
    payload: RosterMutationRequest,
    background_tasks: BackgroundTasks,
):
    if data_service.is_refreshing or data_service.is_market_refreshing:
        return JSONResponse(
            status_code=409,
            content={
                "error": "Wait for the current data refresh to finish before changing the roster"
            },
        )
    if not payload.ciks or len(payload.ciks) > 200:
        return JSONResponse(
            status_code=400,
            content={"error": "Choose between 1 and 200 managers"},
        )
    try:
        ciks = list(dict.fromkeys(normalize_cik(cik) for cik in payload.ciks))
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    if payload.action == "exclude":
        result = roster_store.remove_many(ciks)
        changed_ciks = result["removed"]
    else:
        if (
            payload.group is not None
            and payload.group not in ALLOWED_ROSTER_GROUPS
        ):
            return JSONResponse(
                status_code=400,
                content={"error": f"Unsupported roster group: {payload.group}"},
            )
        current_by_cik = {
            item["cik"]: item
            for item in roster_store.snapshot()
        }
        unclassified_ciks = [
            cik for cik in ciks
            if cik not in current_by_cik and payload.group is None
        ]
        if unclassified_ciks:
            return JSONResponse(
                status_code=400,
                content={
                    "error": (
                        "Choose an investment style before adding new managers"
                    )
                },
            )
        new_ciks = [
            cik
            for cik in ciks
            if cik not in current_by_cik
        ]
        if not new_ciks:
            changed_ciks = []
            roster = roster_store.snapshot()
            return {
                "data": roster,
                "count": len(roster),
                "exception_count": sum(
                    1 for item in roster
                    if item.get("is_exception")
                ),
                "changed_ciks": changed_ciks,
            }
        loop = asyncio.get_running_loop()
        summaries = await loop.run_in_executor(
            None,
            lambda: screening_service.get_manager_summaries(new_ciks),
        )
        missing = [cik for cik in new_ciks if cik not in summaries]
        if missing:
            return JSONResponse(
                status_code=404,
                content={
                    "error": (
                        "Managers are not present in the screening snapshot: "
                        + ", ".join(missing)
                    )
                },
            )

        entries = []
        for cik in new_ciks:
            summary = summaries[cik]
            performance_available = (
                summary.get("performance_status") == "AVAILABLE"
            )
            if performance_available:
                annotation = (
                    f"Full 13F est. {summary['estimated_cagr']:.2%}; "
                    f"{summary['spy_excess_cagr']:+.2%} SPY; "
                    f"{summary['qqq_excess_cagr']:+.2%} QQQ"
                )
            else:
                annotation = "Added from Investor Screening"
            entries.append({
                "group": payload.group,
                "cik": cik,
                "name": summary["manager_name"],
                "manager": summary["manager_name"],
                "annotation": annotation,
                "is_exception": payload.is_exception,
                "roster_reason": (
                    "Manually flagged screening exception"
                    if payload.is_exception
                    else "Included from Investor Screening"
                ),
            })
        result = roster_store.upsert_many(entries)
        changed_ciks = [*result["added"], *result["updated"]]

    sync_result = data_service.sync_roster()
    await data_service.broadcast_event({
        "type": "roster_updated",
        "count": len(FUND_MANAGERS),
        "ciks": changed_ciks,
    })
    if sync_result["added"]:
        background_tasks.add_task(
            _refresh_funds_and_awfi,
            sync_result["added"],
        )
    elif sync_result["removed"]:
        background_tasks.add_task(
            _refresh_roster_context_and_awfi,
        )
    elif changed_ciks:
        background_tasks.add_task(
            _refresh_awfi_research_if_stale_async,
        )
    roster = roster_store.snapshot()
    return {
        "data": roster,
        "count": len(roster),
        "exception_count": sum(
            1 for item in roster
            if item.get("is_exception")
        ),
        "changed_ciks": changed_ciks,
    }


@app.get("/api/fund-status")
async def api_fund_status():
    return {"data": data_service.get_fund_status(), "overview": data_service.get_overview()}

@app.get("/api/refresh")
async def api_refresh(background_tasks: BackgroundTasks):
    background_tasks.add_task(_refresh_all_data)
    return {"message": "Refresh triggered in background"}

@app.get("/events")
async def sse_events(request: Request):
    async def event_generator():
        q = asyncio.Queue()
        await data_service.add_subscriber(q)
        try:
            while True:
                if await request.is_disconnected():
                    break
                event = await q.get()
                yield {
                    "event": "message",
                    "id": "message_id",
                    "retry": 15000,
                    "data": json.dumps(event)
                }
        finally:
            await data_service.remove_subscriber(q)

    return EventSourceResponse(event_generator())
