# Architecture

## Overview

Alpha Whales Intelligence is a server-rendered FastAPI application with a
plain JavaScript dashboard. It combines SEC Form 13F holdings, OpenBB market
data, historical filing snapshots, valuation and timing models, and a local
pair-trading research engine.

Financial data access and calculations stay in Python. Jinja templates define
page structure, while `static/js/app.js` renders API responses and Plotly
charts.

## Runtime components

| Component | Responsibility |
|---|---|
| `config.py` | Manager catalog, CIK history, SEC identity, and cache settings |
| `data_service.py` | SEC access, persistence, aggregation, market enrichment, valuation, timing, and ticker history |
| `pair_service.py` | Semantic peer selection and disciplined pair analysis |
| `main.py` | FastAPI routes, templates, JSON APIs, and SSE |
| `prefetch.py` | Latest, historical, and ticker-intelligence cache warming |
| `templates/` | Server-rendered page structure |
| `static/js/app.js` | Filtering, rendering, charting, export, and loading UX |
| `static/css/styles.css` | Responsive dashboard design |
| `data/reference/full_universe.csv` | Local semantic peer universe |

## Primary data flow

```text
FUND_MANAGERS
  -> edgartools 13F-HR filing
  -> holdings and QoQ comparison DataFrames
  -> disk and in-memory snapshots
  -> DataService aggregation
  -> FastAPI JSON
  -> browser tables and Plotly charts
```

Ticker intelligence adds two independent branches:

```text
OpenBB/yfinance
  -> quote, profile, fundamentals, and daily prices
  -> six-hour ticker market cache
  -> valuation, technical timing, and TradingView symbol

Local semantic universe
  -> same-industry candidate peers
  -> five-year OpenBB prices
  -> cointegration and stability gates
  -> six-hour pair signal cache
```

## Persistent storage

The application uses JSON snapshots rather than a database because its state
is read-heavy, naturally partitioned, and regenerated from upstream sources.

| Path | Contents |
|---|---|
| `cache/<cik>.json` | Latest filing, holdings, and QoQ comparisons |
| `cache/history/<period>.json` | All-manager snapshot for one quarter |
| `cache/market_insights.json` | Batched high-conviction market context |
| `cache/ticker_market/<ticker>.json` | Quote, fundamentals, valuation, and timing |
| `cache/pair_signals/<ticker>.json` | Pair diagnostics and readiness result |

All cache paths are generated and excluded from Git.

## Historical filing periods

The QoQ dashboard exposes the latest 20 quarter-ends. Historical quarters load
from memory, then disk, then SEC EDGAR. Live builds process managers in groups
of five and expose progress through `/api/period-cache-status`.

Managers that changed reporting entities can define `historical_ciks`.
Pershing Square uses its current CIK first and falls back to its former CIK
when that chain provides the usable comparison.

## Concurrency

- edgartools and OpenBB calls run through `run_in_executor`.
- Full SEC refreshes retain grouped processing to limit request rates.
- An `asyncio.Lock` prevents duplicate builds of one historical period.
- Ticker market and pair requests run concurrently in the browser.

## External services

- SEC EDGAR through edgartools.
- OpenBB with the yfinance provider.
- FRED public CSV for Moody's Seasoned Aaa Corporate Bond Yield.
- TradingView's free embedded chart widget.

The application has no runtime dependency on the sibling `invest` repository.
The relevant peer reference and calculation patterns are maintained locally.

## Ticker sentiment flow

`DataService.get_ticker_intelligence()` retains manager-level changes for each
of the 20 historical quarters. It derives breadth, robust portfolio-weight
conviction, composite sentiment regimes, score changes, streaks, dollar-flow
cross-checks, and latest-quarter contributors. The frontend receives completed
calculations and only renders the sentiment trend and manager heatmap.
