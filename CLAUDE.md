# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment and commands

Run commands from the repository root. The application requires Python 3.11+ and live-data operations require internet access plus a real SEC-compliant `EDGAR_IDENTITY` environment variable. Form 13F is delayed and incomplete; derived flow, basis, valuation, timing, sizing, and pair outputs are educational estimates—not reported manager data, personalized advice, or trade instructions.

Before SEC network operations in PowerShell, set a real contact identity:

```powershell
$env:EDGAR_IDENTITY = "Your Name your.email@example.com"
```

```bash
# Runtime and test dependencies
python -m pip install -r requirements.txt
python -m pip install pytest

# Development server: http://127.0.0.1:8000
python run.py
# Equivalent direct invocation
python -m uvicorn main:app --reload --port 8000

# Full suite, one file, and one test
python -m pytest -q
python -m pytest tests/test_market_insights.py -q
python -m pytest tests/test_sentiment_conviction.py::test_typical_share_adjustment_normalizes_cusip -q

# Offline smoke checks
python -m compileall -q config.py roster_store.py data_service.py awfi_service.py main.py pair_service.py prefetch.py run.py predictive_sentiment investor_screening
python -c "import main; print(type(main.app).__name__, len(main.data_service.cache))"
node --check static/js/app.js
```

There is no configured Python linter, formatter, type checker, frontend build, or JavaScript test runner. `.github/mcp.json` configures Playwright MCP; start the application before using Playwright MCP for browser checks. For UI changes, verify desktop and a 390px-wide mobile viewport, inspect console errors, and check for page-level horizontal overflow.

Commands below fetch or rebuild data rather than merely validating code:

```bash
# Latest SEC data; optional historical and ticker-intelligence warm-ups
python prefetch.py
python prefetch.py --history-only
python prefetch.py --ticker-intelligence-only

# Daily accession-aware SEC check and targeted cache publication
python daily_pipeline.py

# Rebuild the compact screening generation and reusable performance facts
python -m investor_screening.cli refresh-screening
python -m investor_screening.cli refresh-performance
python -m investor_screening.cli performance-status

# Validate or atomically rebuild AWFI research history
python -m predictive_sentiment.cli validate-inputs
python -m predictive_sentiment.cli run
python -c "from predictive_sentiment.publication import research_snapshot_needs_refresh; print(research_snapshot_needs_refresh())"
```

See `docs/OPERATIONS.md` for full ingestion, integrity-audit, cache-maintenance, and recovery commands. Do not run network refreshes as a substitute for unit tests.

## Architecture

AlphaWhales is a server-rendered FastAPI application with Jinja page shells and a shared plain-JavaScript browser application. The primary runtime flow is:

```text
roster.json
  -> config.FUND_MANAGERS
  -> edgartools 13F filings
  -> normalized pandas holdings/comparison DataFrames
  -> disk and in-memory caches
  -> DataService aggregations
  -> FastAPI JSON endpoints
  -> static/js/app.js tables and Plotly charts
```

- `main.py` is the thin HTTP/lifespan layer. It owns page routes, JSON endpoints, roster mutation orchestration, SSE, and startup/refresh scheduling around process-wide `DataService`, `ScreeningService`, and `AwfiService` instances.
- `data_service.py` is the tracked-manager application core: SEC retrieval, filing selection and normalization, cache persistence, QoQ comparison, overview/ticker/investor response shaping, OpenBB enrichment, valuation/timing models, sentiment, and historical-period loading. Synchronous edgartools/OpenBB work must remain behind `run_in_executor`; full SEC refreshes stay serialized and process managers in rate-limited groups of five.
- `static/js/app.js` is a global browser application loaded across pages. It owns endpoint fetches, page state, filters/sorts/pagination, CSV export, Plotly rendering, and SSE refresh handling. Templates define structure and inline page initializers, not financial calculations. Keep API fields, DOM guards, SSE event names, templates, and shared JavaScript synchronized.
- `pair_service.py` is a local hypothesis-tier pair research engine. It selects same-industry peers from `data/reference/full_universe.csv`, fetches price history, applies statistical readiness gates, and writes a six-hour cache. It must not import from or depend at runtime on the sibling `invest` repository.

### Storage boundaries

The tracked roster uses generated JSON because its state is small and partitioned:

- `cache/<cik>.json`: latest holdings and two QoQ comparisons.
- `cache/history/<period>.json`: lazy all-manager historical snapshots.
- `cache/market_insights.json`: batched market context.
- `cache/ticker_market/<ticker>.json` and `cache/pair_signals/<ticker>.json`: independent six-hour ticker caches.

Do not hand-edit generated cache values. Change the fetch/transformation logic and regenerate the affected cache. Historical caches carry roster fingerprints and become stale when membership changes.

`investor_screening/` is a separate, universe-wide ownership-research subsystem. It ingests official SEC archives and accession detail into provenance-preserving raw files, ZSTD Parquet, and DuckDB analytical models. Its web boundary is an immutable compact DuckDB generation, resolved through `data/investor_screening/screening_snapshot.json` and opened read-only by `ScreeningService`; `/api/screening` must never scan the historical foundation or fetch market data per request. Ingestion is single-writer, while atomic generation publication lets existing readers finish on the previous snapshot. Screening criteria are dynamic SQL over the compact fact cube; changing them does not require re-ingestion or performance recomputation.

`predictive_sentiment/` is the offline AWFI research and publication subsystem. It reads the screening foundation and adjusted-price manifest, builds point-in-time features and walk-forward evidence in staging, validates provenance/coverage, then atomically replaces the published research DuckDB. `awfi_service.py` is the runtime adapter: it reads persisted history and can append one live current-period score when application filing caches are newer than the official archive. Startup, SEC refresh, and roster mutation share the same freshness contract and publish `awfi_published` over SSE after a successful rebuild. See `docs/AWFI_DATA_LINEAGE.md` before changing this flow.

`filing_operations.py` owns the daily roster-scoped operational ledger and
targeted cache orchestration. `daily_pipeline.py` is the scheduler-facing
entry point. Daily runs are idempotent by accession number, include original
and amended 13F filings across historical CIK chains, invalidate dependent
historical periods, and publish an atomic manifest that running web processes
use to reload externally written caches. The `/filings` page is the durable
record; SSE is notification only.

## Data and domain contracts

- `roster.json` is the persistent manager source of truth; use `RosterStore` or the screening roster workflow rather than editing `config.py`. CIKs are zero-padded 10-character strings and may include `historical_ciks` to preserve reporting-entity continuity. `roster_archive.json` retains removed identities for safe re-addition.
- Investment-style strings and uppercase QoQ statuses (`NEW`, `INCREASED`, `DECREASED`, `CLOSED`, `UNCHANGED`) are cross-layer contracts used by Python, templates, JavaScript, filters, and CSS.
- Keep upstream SEC/DataFrame names (`Cusip`, `Ticker`, `Value`, `SharesPrnAmount`, `PortfolioWeight`, and comparison fields) during processing. Join current holdings to comparisons by deduplicated CUSIP, not ticker; normalize to snake_case only at API response construction.
- Raw SEC/cache monetary values are dollars. Selected API holdings and aggregates convert values to millions inside `DataService`; do not move unit conversion into templates or JavaScript.
- Preserve visible methodology and caveats for all derived financial outputs. Missing valuation, trend, or other required inputs must produce unavailable or zero-risk output, not a success-shaped fallback. Pair execution text may appear only for a `READY` signal.
- Ticker valuation is decision-first but keeps the complete method catalog accessible. Select the primary method from the stock-specific recommended framework; do not average incompatible values. Keep Decision Set, Intrinsic, Relative, Graham, Asset & Special, and All views synchronized with the API. Visible Method Reads must interpret the result—never expose backend `AVAILABLE` as the decision signal.
- Keep Graham Number, revised Graham growth, the conservative Graham adaptation, and NCAV separate. SOTP, REIT NAV/AFFO, and real-options values require their specialized inputs; show the framework and missing-data state rather than fabricating a number. Disable absolute per-share methods when statement currency and ADR/share basis cannot be reconciled.
- Use median portfolio weight—not holder-only mean—when describing a typical holder.
- Select historical filings by report date, not filing date. Amendments can precede complete originals in edgartools listings, and legacy values can have inconsistent scale. Choose the latest candidate among those with the largest holdings count, normalize from implied per-share values, and rebuild totals/weights before comparing quarters. A missing filing is unknown, not liquidation.
- QoQ status is based on share-count change, so reported value may move in the opposite direction as price changes. Some `NEW`/`CLOSED` comparison fields are null; derive their changes from current minus previous values rather than coercing them to zero.
- Alpha Whale Sentiment measures manager-relative conviction from filing data. Market price, estimated cost basis, technical data, and estimated dollar flow are context/cross-checks only and must not enter the score. Preserve an `indicative_score` below the three-meaningful-manager publication floor; represent a true no-signal period as missing, not zero.
- AWFI historical signals use point-in-time official filing data and first-session-after-disclosure execution. Current application cache may define a newer frozen top-ten universe, but must not rewrite historical features. Changes to formulas, components, manager participation, thresholds, or support signals require a versioned experiment rather than retroactive score copying.
- Performance facts are reusable per manager/window and independent of UI screening thresholds or roster membership. `refresh-performance` is resumable; do not rerun it only because screening filters or the roster changed.

## Repository boundaries

Commit source, templates, static assets, documentation, and reference data. Do not commit generated `cache/` or `data/investor_screening/` content, `.env` files, credentials, browser traces, or virtual environments. Use Conventional Commits.

The detailed system map is in `docs/ARCHITECTURE.md`; methodology-sensitive definitions are in `docs/METHODOLOGY.md`, `investor_screening/SCREENING_MODEL.md`, and `predictive_sentiment/ALPHA_WHALE_FORWARD_INDEX.md`.
