# Architecture

## Overview

Alpha Whales Intelligence is a server-rendered FastAPI application with a
plain JavaScript dashboard. It combines SEC Form 13F holdings, OpenBB market
data, historical filing snapshots, valuation and timing models, and a local
pair-trading research engine. A separate DuckDB-based subsystem supports
universe-wide institutional-manager screening.

Financial data access and calculations stay in Python. Jinja templates define
page structure, while `static/js/app.js` renders API responses and Plotly
charts.

## Runtime components

| Component | Responsibility |
|---|---|
| `config.py` | Manager catalog, CIK history, SEC identity, and cache settings |
| `data_service.py` | SEC access, persistence, aggregation, market enrichment, valuation, timing, and ticker history |
| `pair_service.py` | Semantic peer selection and disciplined pair analysis |
| `investor_screening/` | SEC bulk ingestion, DuckDB models, validation, and screening snapshot generation |
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

The 26-manager dashboard uses JSON snapshots because its state is read-heavy,
naturally partitioned, and regenerated from upstream sources. Investor
Screening uses DuckDB and Parquet because it spans a much larger institutional
filing universe and historical row set.

| Path | Contents |
|---|---|
| `cache/<cik>.json` | Latest filing, holdings, and QoQ comparisons |
| `cache/history/<period>.json` | All-manager snapshot for one quarter |
| `cache/market_insights.json` | Batched high-conviction market context |
| `cache/ticker_market/<ticker>.json` | Quote, fundamentals, valuation, and timing |
| `cache/pair_signals/<ticker>.json` | Pair diagnostics and readiness result |
| `data/investor_screening/investor_screening.duckdb` | Normalized SEC metadata and analytical models |
| `data/investor_screening/lake/` | ZSTD Parquet bronze storage for large flattened SEC families |
| `data/investor_screening/raw/` | Compressed accession-level source submissions and provenance |
| `data/investor_screening/screening_snapshot.duckdb` | Compact read-only runtime screening snapshot |

All listed cache and generated screening-data paths are excluded from Git.

## Historical filing periods

The QoQ dashboard exposes the latest 20 quarter-ends. Historical quarters load
from memory, then disk, then SEC EDGAR. Live builds process managers in groups
of five and expose progress through `/api/period-cache-status`.

Managers that changed reporting entities can define `historical_ciks`.
Pershing Square uses its current CIK first and falls back to its former CIK
when that chain provides the usable comparison.

SEC ingestion normalizes legacy dollar/thousand-dollar value scales from
implied per-share prices, rebuilds portfolio totals and weights from holdings,
and selects the latest complete filing among original/amended submissions.
QoQ comparisons are then constructed from the normalized current and previous
snapshots.

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

## Investor screening data flow

Investor Screening is a separate data subsystem inside the repository:

```text
Official SEC flattened archives and EDGAR accessions
  -> raw archive and provenance manifests
  -> Parquet bronze lake and normalized DuckDB analytical models
  -> validated analytical views
  -> compact read-only screening snapshot
  -> ScreeningService
  -> /api/screening
  -> /screening
```

The screening page never scans the full historical foundation per request.
`ScreeningService` queries the compact snapshot generated by
`python -m investor_screening.cli refresh-screening`.

Generated screening data lives under `data/investor_screening/` and is
excluded from Git. Source modules and screening methodology documentation are
versioned.

## Ticker sentiment flow

`DataService.get_ticker_intelligence()` retains manager-level changes for each
of the 20 historical quarters. It derives raw share activity, manager-relative
share-adjustment or position-size conviction, meaningful breadth, composite sentiment
regimes, streaks, dollar-flow cross-checks, and contributors. The frontend
receives completed calculations and only renders the trend and heatmap.

Ticker history derives actions directly from consecutive cached snapshots,
which preserves continuity across reporting-entity CIK changes.
