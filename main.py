import asyncio
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse
from data_service import DataService

data_service = DataService()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    task = asyncio.create_task(data_service.auto_refresh_loop())
    yield
    # Shutdown
    task.cancel()

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

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


# --- API Routes ---

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

    return {
        "period": selected_period,
        "periods": periods,
        "cache_status": data_service.get_period_cache_status(selected_period),
        "changes": data_service.get_qoq_changes(
            include_unchanged=True,
            fund_cache=period_cache
        ),
        "tickers": data_service.get_ticker_view(fund_cache=period_cache),
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
    data = data_service.get_ticker_view(ticker)
    if not data:
        return JSONResponse(status_code=404, content={"error": "Ticker not found or no holdings"})
    return {"data": data}

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
        return JSONResponse(status_code=404, content={"error": "Investor not found"})
    return {"data": data}

@app.get("/api/fund-status")
async def api_fund_status():
    return {"data": data_service.get_fund_status(), "overview": data_service.get_overview()}

@app.get("/api/refresh")
async def api_refresh(background_tasks: BackgroundTasks):
    background_tasks.add_task(data_service.refresh_all)
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
