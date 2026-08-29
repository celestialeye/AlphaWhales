# AlphaWhales Copilot Instructions

## Commands

Run commands from the repository root.

```powershell
# Install runtime dependencies
python -m pip install -r requirements.txt

# Start the development server at http://127.0.0.1:8000
python run.py

# Equivalent direct Uvicorn command
python -m uvicorn main:app --reload --port 8000

# Fetch all configured funds and rewrite the persistent cache
python prefetch.py

# Prefetch all 19 non-latest QoQ history snapshots
python prefetch.py --history-only

# Prefetch popular ticker market, decision-support, and pair caches
python prefetch.py --ticker-intelligence-only

# Fast, offline Python syntax/import smoke check
python -m compileall -q config.py data_service.py main.py pair_service.py prefetch.py run.py
python -c "import main; print(type(main.app).__name__, len(main.data_service.cache))"

# Focused unit tests
python -m pip install pytest
python -m pytest tests/test_sentiment_conviction.py -q
python -m pytest tests/test_investor_screening.py -q

# Rebuild the compact DuckDB snapshot used by /screening
python -m investor_screening.cli refresh-screening
```

`prefetch.py` and `/api/refresh` make live SEC EDGAR requests. The repository
has focused pytest coverage for sentiment conviction and
Investor Screening. It does not currently configure a linter, formatter, or
frontend build step.

The shared `.github/mcp.json` configures Playwright MCP. Start the application first, then use its browser tools for end-to-end checks against `http://127.0.0.1:8000`.

## Architecture

- `config.py` is the catalog and runtime configuration source. `FUND_MANAGERS` supplies the exact CIK, display metadata, and strategy group for every tracked fund. CIKs are zero-padded strings and also serve as cache keys and filenames.
- `data_service.py` is the application core. The singleton `DataService` loads disk snapshots into pandas DataFrames at import time, fetches current 13F data through `edgartools`, compares quarters, persists snapshots, and converts DataFrames into JSON-ready dashboard models.
- OpenBB's yfinance provider supplies one year of daily prices for holdings that reach at least 5% in any tracked portfolio. The derived 52-week-low metrics are persisted separately in `cache/market_insights.json`.
- Ticker detail pages fetch OpenBB quote, fundamental metrics, company profile, and six years of prices on demand. Persist these six-hour responses in `cache/ticker_market/<ticker>.json`; `/api/ticker/{ticker}/intelligence` combines them with all 20 filing-period snapshots.
- Pair signals are implemented locally in `pair_service.py` using the copied `data/reference/full_universe.csv` semantic peer snapshot. Do not import from or runtime-couple to the sibling `invest` repository. Cache six-hour results under `cache/pair_signals/<ticker>.json`.
- `investor_screening/` is an independent SEC ownership-research subsystem. It owns official flattened archive ingestion, accession-level detail ingestion, DuckDB/Parquet storage, quality checks, analytical views, and the compact snapshot consumed by `ScreeningService`.
- Generated screening databases, archives, Parquet files, and snapshots live under `data/investor_screening/` and are excluded from Git. The `/api/screening` route must query the compact read-only snapshot rather than scanning the historical foundation per request.
- Pair readiness is hypothesis-tier only. Require same-industry peers, five years of dividend-adjusted prices, bidirectional Engle-Granger with Bonferroni correction across both directions, OOS ADF persistence, at least one stable sub-window, positive OLS price hedge ratio, 10-120 day half-life, and absolute z-score >= 1.5. Do not render execution instructions unless status is `READY`.
- The defined-risk pair expression is long the statistically cheap stock plus a put on the expensive stock. It is not equivalent to a market-neutral stock/short-stock pair and must retain the visible premium, theta, implied-volatility, strike, and expiry caveat.
- Alpha Whale Sentiment keeps raw share activity separate from scored conviction and must not use external market price or estimated execution/cost basis. For continuing holdings, relative conviction is signed `shares_change_pct / manager_median_abs_share_change_pct` for that quarter. For `NEW`/`CLOSED`, use signed reported position weight divided by the manager's median prior position weight. Trades below `0.25x` normal and adjustments to positions below `0.25x` normal size are routine; contributions cap at `2x`, and at least three meaningful managers are required.
- Sentiment breadth is the normalized balance of meaningful positive versus negative relative-conviction trades. Conviction score is the normalized balance of capped relative-conviction magnitude. Composite score is their equal 50/50 average bounded to `[-100, 100]`; raw buy/sell breadth remains display-only.
- Preserve `indicative_score` whenever meaningful breadth and conviction are calculable, even below the three-manager validation floor. Chart it as a dashed low-confidence trend; publish the primary `score` and regime only with at least three meaningful managers. Never fill a true no-signal quarter with zero.
- Estimated ticker flow is only a dollar-weighted cross-check for sentiment. It must not enter the sentiment score. Label agreement as `CONFIRMS`, opposition as `DIVERGES`, and sparse/flat cases as `NEUTRAL`.
- The sentiment heatmap and contributor lists must use manager-relative conviction. Show reported share change versus normal adjustment for continuing positions and position size versus normal holding for new/closed positions.
- edgartools filing lists may place partial amendments before complete originals, and legacy values may be dollars, thousands, or 1,000x malformed. Normalize values from median implied per-share price, rebuild total value/weights from holdings, select the latest candidate among those with the largest holdings count, then compare normalized snapshots. Do not trust the first accession or filing summary total.
- `main.py` is a thin FastAPI layer. It creates the process-wide `DataService`, starts its refresh loop through the application lifespan, renders Jinja pages, exposes JSON endpoints, and streams refresh notifications over `/events`.
- `templates/` provides page structure only. The list and detail forms of ticker and investor pages share templates and receive an optional `ticker` or `cik` from FastAPI.
- `static/js/app.js` is the browser application. It fetches the JSON endpoints, stores page-level state in script-global arrays, performs filtering/sorting/pagination, renders Plotly charts and table markup, exports CSVs, and reloads the active view after SSE events.
- `cache/<cik>.json` is generated persistent state used for fast startup. Each snapshot contains metadata plus serialized `holdings`, current QoQ `comparison`, and `previous_comparison` records; `DataService` reconstructs these as DataFrames.
- `cache/history/<report-period>.json` stores lazily generated cross-fund historical snapshots for the QoQ filing-period selector. The selector exposes the latest 20 quarter-ends and defaults to the latest. Do not hand-edit these files; `DataService.get_period_cache()` loads or regenerates them.

The primary data flow is:

`FUND_MANAGERS` -> `edgartools` 13F fetch -> pandas holdings/comparison DataFrames -> disk and in-memory cache -> `DataService` aggregation methods -> FastAPI JSON -> client-side rendering.

## Repository-Specific Conventions

- Keep SEC access, pandas joins, financial calculations, and response shaping in `DataService`; routes should mainly validate/filter request parameters and return service results.
- `edgartools` calls are synchronous. Keep them behind `run_in_executor` rather than blocking FastAPI's event loop.
- Preserve the controlled refresh cadence in `refresh_all`: funds are processed in groups of five with a delay between groups to stay within SEC request-rate guidance. `is_refreshing` prevents overlapping full refreshes.
- Full refreshes also rebuild two consecutive QoQ comparisons and then refresh OpenBB market insights. Keep OpenBB calls batched and off the FastAPI event loop through `run_in_executor`.
- Treat `config.py` as the source of truth for fund identity, but remember that the current fund count and group labels are repeated in README text, templates, runner messages, and browser copy. When changing the catalog, update those user-facing surfaces together.
- Strategy group strings are contracts across `config.py`, HTML filter values, `getGroupClass()` in `app.js`, and CSS badge classes. Preserve the exact values `Value`, `High-performance concentrated`, `Quality compounder`, and `2026 expansion` unless all consumers are updated.
- QoQ statuses are uppercase contract values: `NEW`, `INCREASED`, `DECREASED`, `CLOSED`, and `UNCHANGED`. Backend filtering, tab logic, badges, charts, and CSV exports depend on those spellings.
- Retain the upstream DataFrame column names while processing SEC data (`Cusip`, `Ticker`, `Value`, `SharesPrnAmount`, `PortfolioWeight`, comparison fields). Convert to the existing snake_case API fields only when building response dictionaries.
- Raw SEC/cache monetary values, including cached `metadata.total_value`, are dollars. Holdings fields such as `value`, `prev_value`, and `value_change`, ticker `total_value_across_funds`, and fund-status `total_value` are converted to millions. Investor metadata adds `total_value_m` and `total_value_b` while retaining the raw value. Keep conversions in `DataService` so templates and JavaScript remain presentation-only.
- Join current holdings to QoQ comparison data by `Cusip`, using deduplicated comparison/weight subsets. Do not use ticker as the primary join key because tickers may be absent or ambiguous.
- OpenBB/yfinance class-share symbols require explicit aliases for SEC tickers such as `BRKA`, `BRKB`, and `HEIA`; preserve the mapping back to the SEC ticker used by dashboard routes.
- Estimated ticker flow values net share changes at the selected quarter-end price. The Alpha Whale price is a 20-quarter estimated weighted-average basis: additions use that quarter's average daily close, reductions remove cost proportionally, and boundary holdings initialize at the oldest quarter average. Always label both as estimates because 13F does not disclose execution prices or cost basis.
- Ticker valuation uses at least two available models from Graham Number, conservative growth value adjusted by current versus 20-year average AAA yields, and trailing EPS multiplied by the median fiscal-window P/E. Historical P/E excludes the current P/E, requires at least three valid fiscal-year observations, and rejects values above 100x. The buy price applies a 20% margin of safety.
- Ticker technical timing keeps value, trend, and short-term entry signals separate. Use RSI(14), RSI(2), 50/200-day moving averages, 6-month momentum, and 63-day annualized volatility. An unavailable valuation/trend or a bearish/under-200-day state must produce a zero illustrative allocation; never return a success-shaped position recommendation from missing inputs.
- All Weather-style ticker sizing is educational risk budgeting, not personalized advice: cap a stock at 5% of a 30% equity sleeve (1.5% of total portfolio before volatility reduction), and expose both ranges plus the assumptions in the UI.
- Ticker `avg_weight` is a mean over holders only (`sum(portfolio_weight) / num_holders`), so it collapses to a single manager's bet when `num_holders` is 1. The conviction ranking uses `median_weight`, applies a five-holder floor (`MIN_CONVICTION_HOLDERS` in `app.js`), and displays the holder count. Single-manager concentration belongs in Big Bets (max single-fund weight), not the conviction card.
- Ticker `median_weight` is the median `portfolio_weight` across holders and is the skew-resistant companion to `avg_weight`; the two can diverge sharply (AAPL: 3.35% mean vs 0.15% median). Prefer the median when describing a typical position size.
- Ticker-view responses reconstruct prior-quarter ownership from each fund's comparison `PrevValue` records. `holder_count_change` is the unique-holder count delta, while `avg_weight_change` and `median_weight_change` are percentage-point changes from reconstructed prior portfolio weights.
- Ticker-view QoQ activity counts aggregate each fund's comparison rows by normalized ticker and classify the net share change as `increased`, `decreased`, `new`, `closed`, or `unchanged`. `median_position_change` is the median portfolio-weight percentage-point change among continuing holders only.
- Ticker `holder_count_change` must equal `qoq_actions.new - qoq_actions.closed`, using only comparable fund filings. Keep the total current holder count separate and expose `qoq_unavailable_holders` when a current holder has no usable QoQ comparison; never infer that missing prior data means a new position.
- OpenBB market insights retain the filing-period close as `quarter_end_price` and calculate `price_return_since_quarter` against the latest close. This is market-price context, not investor cost basis or gain/loss; keep that distinction explicit in labels and tooltips.
- Normalize tickers with trim plus uppercase and omit empty, `NAN`, or `NONE` values from dashboard aggregates. Closed positions come from the comparison DataFrame and are returned separately from active holdings.
- Do not hand-edit cache snapshots as application data. Change fetch/transformation logic or `config.py`, then regenerate snapshots with `python prefetch.py` when live SEC access is intended.
- Frontend behavior is implemented with plain global JavaScript and inline Jinja page initializers; there is no bundler or component framework. Keep API property names synchronized with `app.js`, and guard shared DOM operations because the same script loads on every page.
- The Investor Screening page is implemented in `templates/screening.html` with rendering/filter state in the shared `app.js` and screening-specific CSS in `styles.css`. Preserve its compact-snapshot boundary and visible distinctions between reported 13F value, estimated turnover, and observed persistence.
- The overview KPI bar is signal-oriented rather than a database summary: consensus buy/sell use combined new/increased and closed/decreased investor counts, new consensus ranks initiations, conviction uses five-holder median weight, and the value signal uses OpenBB distance from the 52-week low. Keep cards linked to ticker detail pages.
- The QoQ strategy chart includes `UNCHANGED`, but the detailed changes table intentionally excludes unchanged positions. In that table, status describes share-count action; reported value can move in the opposite direction because quarter-end security prices changed. Display the share percentage under the action badge and label value columns as reported-value changes.
- The manager activity matrix ranks `NEW`/`INCREASED` and `DECREASED`/`CLOSED` moves separately by `portfolio_weight_change`. Display that metric as percentage points (`pp`), calculated as current portfolio weight minus prior-quarter portfolio weight; do not label it as a share-change percentage.
- Historical filings are selected by `report_date`, not filing date. When a manager has changed reporting CIKs, list prior identifiers in `historical_ciks` and prefer the matching filing chain with a usable QoQ comparison.
- Some edgartools comparison fields are null for `NEW` and `CLOSED` rows. In `get_qoq_changes`, derive missing dollar/share changes from current minus previous values so entries and exits participate correctly in flow aggregations; do not silently convert missing changes to zero.
- `DataService` emits `fund_status` when a fund starts loading, `fund_updated` after each fund, and `data_refresh` after the full batch. The global `EventSource` handler currently reacts to `fund_updated` and `data_refresh`; keep emitters and consumers synchronized when changing refresh behavior.
