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
| `filing_operations.py` | Daily accession discovery, SQLite run ledger, targeted cache orchestration, and publication manifest |
| `daily_pipeline.py` | Scheduler-facing one-shot daily SEC operation |
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
  -> stock-profile classification and valuation method catalog
  -> technical timing, serialized daily close history, and TradingView symbol

Local semantic universe
  -> same-industry candidate peers
  -> five-year OpenBB prices
  -> cointegration and stability gates
  -> six-hour pair signal cache
```

### Valuation decision pipeline

Ticker valuation is calculated in `DataService._compute_valuation_analysis()`
and persisted in cache-version-10 ticker-market payloads. The pipeline keeps
method calculation, framework selection, and browser presentation separate:

```text
OpenBB quote + profile + metrics + annual statements + FRED yields
  -> normalize currency/share-basis eligibility and historical inputs
  -> calculate all supported valuation methods
  -> classify sector, industry, growth, payout, and structural profile
  -> choose recommended framework and primary calculated anchor
  -> emit method values, ranges, fit, methodology, readiness, and Method Read
  -> browser Decision Set and categorized valuation tabs
```

The complete catalog includes scenario FCF DCF, reverse DCF, residual income,
two-stage DDM, normalized historical P/E, equity and enterprise multiples,
Graham Number, revised Graham growth, the conservative Graham adaptation,
NCAV, tangible asset value, SOTP, REIT NAV/AFFO, and real options.

The primary fair value is the first valid calculated method in the recommended
framework. It is never a median or vote across incompatible methods. The
browser defaults to the recommended `Decision Set`, while `Intrinsic`,
`Relative`, `Graham`, `Asset & Special`, and `All` tabs retain access to the
full catalog.

Method readiness is backend state and must not be presented as the investment
conclusion. Each card instead exposes a decision-oriented `Method Read`:
price-versus-value for calculated fair values, expectations analysis for
reverse DCF, growth-adjusted interpretation for PEG, peer-benchmark warnings
for unbenchmarked multiples, or explicit not-a-fit/data-required states.

SOTP, property NAV/AFFO, and real-options methods remain visible when relevant,
but do not fabricate values without segment, property, reserve, pipeline,
volatility, debt-maturity, or exercise data. Absolute per-share methods are
also disabled when statement currency and traded-share or ADR basis cannot be
reconciled.

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
| `cache/filing_publication.json` | Atomic notification that a daily operation published external cache changes |
| `data/operations/filing_ingestion.sqlite3` | Daily run history and accession-level filing record |
| `data/investor_screening/investor_screening.duckdb` | Normalized SEC metadata and analytical models |
| `data/investor_screening/lake/` | ZSTD Parquet bronze storage for large flattened SEC families |
| `data/investor_screening/lake/npx_votes/` | Yearly lossless N-PX proxy-vote Parquet files |
| `data/investor_screening/raw/` | Compressed accession-level source submissions and provenance |
| `data/investor_screening/screening_snapshot.json` | Atomic pointer to the current immutable screening generation |
| `data/investor_screening/screening_snapshot.<generation>.duckdb` | Compact read-only runtime screening generation |
| `data/investor_screening/performance.duckdb` | AWFI CUSIP mappings, adjusted prices, and performance facts |
| `data/investor_screening/predictive_sentiment.duckdb` | Versioned AWFI research runs, features, scores, and provenance |

All listed cache and generated screening-data paths are excluded from Git.

## Daily filing operations

The recurring SEC schedule is external to the web process. Windows Task
Scheduler or cron invokes `python daily_pipeline.py` once per day:

```text
roster + historical CIK chains
  -> 120-day 13F-HR / 13F-HR/A metadata discovery
  -> accession-idempotent SQLite ledger
  -> affected manager and report-period set
  -> DataService targeted cache refresh
  -> P / P+1 / P+2 historical cache invalidation
  -> market-context refresh
  -> AWFI freshness and atomic publication
  -> atomic filing publication manifest
```

The first run baselines the recent accession inventory. Later runs treat only
previously unseen accessions as new. The manager cache records the selected
accession, form, source CIK, filing date, report period, and SEC source URL.
The filing operation uses a cross-process lock so duplicate scheduler
instances cannot publish concurrently.

Every FastAPI process watches the publication manifest. When an external
operation completes, the process reloads disk-backed manager and market
caches, clears invalidated historical periods, and emits `data_refresh`,
`filings_ingested`, and, when applicable, `awfi_published` over its local SSE
stream. `/filings` reads the SQLite ledger rather than relying on ephemeral
SSE history.

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
- Initial and targeted SEC refreshes retain grouped processing to limit request rates.
- An `asyncio.Lock` prevents duplicate builds of one historical period.
- The daily operation adds a local-filesystem cross-process lock and an
  accession-idempotent SQLite WAL ledger.
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

## NautilusTrader adoption boundary

**Decision:** retain pandas, DuckDB, NumPy, SciPy, and statsmodels as the AWFI
research and statistical-validation layer. Do not rewrite signal discovery,
point-in-time feature construction, cross-sectional normalization, nested
walk-forward selection, or multiple-testing controls in NautilusTrader.

[NautilusTrader](https://nautilustrader.io/docs/latest/concepts/overview/) is
reserved for a downstream portfolio and execution-simulation layer after a
candidate passes the AWFI promotion gates.

```text
SEC/XBRL + immutable analyst snapshots + prices
  -> pandas/DuckDB point-in-time features
  -> AWFI cross-sectional and walk-forward research
  -> statistical, coverage, mapping, and provenance gates
  -> frozen target actions and portfolio weights
  -> NautilusTrader event-driven portfolio simulation
  -> execution-aware CAGR, drawdown, turnover, cost, and exposure results
```

The existing research layer remains responsible for:

- amendment-aware filing reconstruction and historical information cutoffs;
- historical security identity and universe membership;
- factor and valuation-method calculation;
- industry normalization and cross-sectional ranking;
- 126-, 252-, 378-, and 504-session forward outcomes;
- purged inner selection and untouched outer-quarter evaluation;
- HAC, block-bootstrap, multiple-testing, and promotion decisions.

The future NautilusTrader layer may own:

- portfolio cash and capital allocation;
- simultaneous-signal prioritization;
- explicit `ENTER`, `INCREASE`, `HOLD`, `DECREASE`, and `EXIT` position sizes;
- orders, fills, delays, slippage, commissions, and liquidity constraints;
- corporate-action-aware position accounting;
- turnover, concentration, exposure, CAGR, drawdown, and risk statistics;
- eventual research/live execution parity if live automation is approved.

NautilusTrader does not solve missing point-in-time fundamentals, analyst
history, survivorship, security mapping, or statistical overfitting. It must
not be used to make an unvalidated signal appear more credible through a more
detailed execution simulation.

Integration should begin only when a candidate has:

1. passed its prespecified `t > 3`, Holm, block-robustness, coverage, and
   minimum-outer-quarter requirements;
2. resolved effective-dated security mapping and data-provenance blockers;
3. defined deterministic target weights for all five portfolio actions; and
4. published an immutable action artifact that can be replayed without
   recalculating research features inside NautilusTrader.

There is currently no NautilusTrader runtime or development dependency in this
repository.

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
