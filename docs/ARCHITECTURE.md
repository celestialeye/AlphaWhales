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
| `roster.json` | Persistent manager catalog, CIK history, display metadata, style, and exception status |
| `config.py` | Loads the roster path and owns SEC identity and cache settings |
| `data_service.py` | SEC access, persistence, aggregation, market enrichment, valuation, timing, and ticker history |
| `pair_service.py` | Semantic peer selection and disciplined pair analysis |
| `investor_screening/` | SEC bulk ingestion, DuckDB models, validation, and screening snapshot generation |
| `predictive_sentiment/` | AWFI research protocol, horizon scoring, freshness detection, and atomic publication |
| `awfi_service.py` | Current-period AWFI scoring and persisted ticker-history access |
| `main.py` | FastAPI routes, templates, JSON APIs, and SSE |
| `prefetch.py` | Latest, historical, and ticker-intelligence cache warming |
| `templates/` | Server-rendered page structure |
| `static/js/app.js` | Filtering, rendering, charting, export, and loading UX |
| `static/css/styles.css` | Responsive dashboard design |
| `data/reference/full_universe.csv` | Local semantic peer universe |

## Primary data flow

```text
roster.json
  -> config.FUND_MANAGERS
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
  -> valuation, technical timing, serialized daily close history, and TradingView symbol

Local semantic universe
  -> same-industry candidate peers
  -> five-year OpenBB prices
  -> cointegration and stability gates
  -> six-hour pair signal cache
```

## Persistent storage

The 29-manager dashboard uses JSON snapshots because its state is read-heavy,
naturally partitioned, and regenerated from upstream sources. Investor
Screening uses DuckDB and Parquet because it spans a much larger institutional
filing universe and historical row set.

| Path | Contents |
|---|---|
| `cache/<cik>.json` | Latest filing, holdings, and QoQ comparisons |
| `roster_archive.json` | Removed-manager identity metadata retained for safe re-addition |
| `cache/history/<period>.json` | All-manager snapshot for one quarter |
| `cache/market_insights.json` | Batched high-conviction market context |
| `cache/ticker_market/<ticker>.json` | Quote, fundamentals, valuation, timing, and six years of daily closes |
| `cache/pair_signals/<ticker>.json` | Pair diagnostics and readiness result |
| `data/investor_screening/investor_screening.duckdb` | Normalized SEC metadata and analytical models |
| `data/investor_screening/lake/` | ZSTD Parquet bronze storage for large flattened SEC families |
| `data/investor_screening/lake/npx_votes/` | Yearly lossless N-PX proxy-vote Parquet files |
| `data/investor_screening/raw/` | Compressed accession-level source submissions and provenance |
| `data/investor_screening/screening_snapshot.json` | Atomic pointer to the current immutable screening generation |
| `data/investor_screening/screening_snapshot.<generation>.duckdb` | Compact read-only runtime screening generation |
| `data/investor_screening/performance.duckdb` | AWFI CUSIP mappings, adjusted prices, and performance facts |
| `data/investor_screening/predictive_sentiment.duckdb` | Versioned AWFI research runs, features, scores, and provenance |

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

## AWFI data flow

AWFI uses a newer-current/older-historical split when the application receives
filings before the SEC publishes its next quarterly structured archive:

```text
validated cache/<cik>.json + official latest portfolio
  -> choose newer top-ten direct-stock universe per manager
  -> one frozen current universe and fingerprint

official amendment-aware historical archive
  -> manager snapshots and changes
  -> point-in-time institutional and technical features
  -> 6M, 12M, 18M, and 24M AWFI scores
  -> checkpointed staging DuckDB
  -> pre-publication integrity gates
  -> atomic predictive_sentiment.duckdb replacement
```

The current application quarter can be newer than persisted historical scores.
In that case, `AwfiService` computes one live score using the fixed filing
inputs and the latest cached market session, then appends it to the latest 19
compatible persisted quarters.

Startup, periodic refresh, manual refresh, and roster mutations all run the
same AWFI freshness check. A protocol, model, configuration, roster, source, or
universe mismatch schedules one serialized rebuild. Successful publication
emits `awfi_published` over SSE so ticker pages reload the complete score and
history payload.

See [`AWFI_DATA_LINEAGE.md`](AWFI_DATA_LINEAGE.md) for the complete lineage,
publication, integrity, and incident-recovery design.

## Investor screening data flow

Investor Screening is a separate data subsystem inside the repository:

```text
Official SEC 13F, insider, N-PORT, and N-MFP flattened archives
  -> immutable ZIP archives and SHA-256 manifests
  -> lossless ZSTD Parquet bronze tables

EDGAR accession indexes
  -> compressed full submissions
  -> EdgarTools typed objects or lossless XML/raw fallback
  -> hashed detail rows and normalized ownership/fund views

13F normalized holdings and manager identity history
  -> screening manager metrics
  -> pruned manager-position-quarter fact cube
  -> immutable screening generation
  -> atomically published JSON generation pointer
  -> ScreeningService
  -> /api/screening
  -> /screening
```

The screening page never scans the full historical foundation per request.
`ScreeningService` queries the compact snapshot generated by
`python -m investor_screening.cli refresh-screening`.

`manager_position_quarters` retains direct-stock positions at or above the 1%
minimum selectable overall non-option 13F weight, plus each quarter's top ten
positions regardless of weight, for the latest 20 snapshots. Best-bet
weight, 6/12/18/24-month duration, and required-count changes are dynamic SQL
filters over this cube. Performance thresholds query embedded per-manager/window
facts and do not trigger performance recalculation.

Generated screening data lives under `data/investor_screening/` and is
excluded from Git. Source modules and screening methodology documentation are
versioned.

### Storage layers

| Layer | Storage | Purpose |
|---|---|---|
| Source | SEC ZIP and compressed `.txt.gz` submissions | Immutable provenance and rebuildability |
| Bronze | ZSTD Parquet | Lossless flattened SEC tables with schema evolution |
| Catalog | DuckDB | Dataset/file manifests, hashes, schemas, row counts, filing metadata, and quality results |
| Silver | DuckDB views/tables | Amendment-aware 13F holdings and normalized cross-form research views |
| Runtime | Compact DuckDB snapshot | Stable, low-latency `/api/screening` queries |

DuckDB is used as an embedded analytical engine, not a concurrently written
web-service database. Ingestion has one writer. The web application resolves
the generation pointer and opens that immutable screening database in read-only
mode. Existing requests may finish against an older generation while a new
generation is published.

### Filing-family coverage

- Form 13F structured archives from the June 30, 2013 report period onward.
- Forms 3/4/5 official flattened archives from 2006 onward.
- N-PORT official flattened archives from 2019 onward.
- N-MFP official flattened archives from 2010 onward across schema generations.
- Structured accession histories for Schedule 13D/G, Form 144, N-CEN,
  N-CSR/N-CSRS, and N-PX.
- Legacy documents without a reliable typed/XML representation remain
  `RAW_ONLY`; they are retained rather than silently discarded.

The subsystem is independent from the sibling `invest` repository. It neither
imports its modules nor reads its runtime data.

## Ticker sentiment flow

`DataService.get_ticker_intelligence()` retains manager-level changes for each
of the 20 historical quarters. It derives raw share activity, manager-relative
share-adjustment or position-size conviction, meaningful breadth, composite
sentiment regimes, streaks, dollar-flow cross-checks, and contributors.

Ticker market cache version 5 serializes the OpenBB daily close series used by
the sentiment chart. The frontend clips that price series to the oldest of the
20 report periods, overlays it on a separate dollar axis, and extends it
through the latest available close. It also derives an expected 13F deadline
at `report period + 45 calendar days` and renders cyan vertical guides and
bottom-rail markers. Those dates are standardized expectations, not actual
manager filing timestamps.

Market price, 52-week-low proximity, and the price overlay are presentation
context only. They do not enter raw activity, manager-relative conviction,
meaningful breadth, the composite sentiment score, or regime assignment.

`DataService.get_investor_view()` also enriches holdings from existing
`market_insights` and fresh ticker-market caches. It derives filing-period
reported price from value divided by shares and calculates current-price drift
from that reference. The synchronous investor route never launches one
OpenBB request per holding; uncached rows remain unavailable.

`DataService.get_investor_history()` is the asynchronous history path behind
`/api/investor/{cik}/history`. It loads the same latest 20 period caches used by
ticker intelligence, preserves canonical manager CIK keys, groups non-unchanged
QoQ activity by period, and returns period-level portfolio totals plus the top
20 holdings by portfolio weight. The browser loads this endpoint lazily when
the Activity History or Portfolio History tab is first selected.

Ticker history derives actions directly from consecutive cached snapshots,
which preserves continuity across reporting-entity CIK changes.
