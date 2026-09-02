# Alpha Whales Intelligence ⚡

An interactive web application powered by **FastAPI**, **edgartools**, **OpenBB**, **Plotly.js**, and **Server-Sent Events (SSE)** to track and analyze 13F equity filings from a persistent, screening-managed roster.

> **Important:** 13F filings are delayed and incomplete. Estimated flow,
> valuation, basis, timing, sizing, and pair outputs are educational research
> models, not reported manager data, personalized advice, or trade instructions.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Methodology and financial definitions](docs/METHODOLOGY.md)
- [Operations and cache management](docs/OPERATIONS.md)
- [Universe-wide investor screening data project](investor_screening/README.md)
- [Investor screening data quality report](docs/INVESTOR_SCREENING_DATA_QUALITY.md)
- [Investor performance methodology comparison](docs/INVESTOR_PERFORMANCE_METHODOLOGY.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)

---

## 🎯 Tracked Fund Managers (29 Total)

`roster.json` is the persistent roster source of truth. Investor Screening can
add, flag, or remove managers without editing Python source.

### Investment style groups

Style describes how a manager invests; it does not indicate when the manager
was added or whether the manager passed the roster performance rule.

- **Value & Contrarian (5):** Sanders Capital, ARGA, Blackhill, Li Lu, and
  Buffett.
- **Quality Growth (4):** Yarbrough Capital, Silvant Capital, Blue Whale, and
  Randolph Co.
- **Technology & Innovation (9):** Newlands Management, Value Aligned,
  Voyager Global, Analog Century, Kinetic Partners, Styrax, Keywise, Dock
  Street, and Laffont.
- **Opportunistic & Concentrated (5):** Lingotto, First Beijing, Crake, Sosin,
  and Ackman.
- **Diversified & Systematic (6):** Systematic Alpha, Y.D. More, Traynor
  Capital, Petredis, Weatherly, and German American Bancorp.

The 24 non-exception managers passed the approved persistent-best-bet structure
and their full-window AlphaWhales 13F estimates beat both SPY and QQQ.

### Approved exceptions (5)

Exception status is displayed separately from investment style.

- **Himalaya Capital / Li Lu** — strong recent followable performance.
- **CAS Investment Partners / Clifford Sosin** — exceptional 3Y clone; full window unavailable.
- **Coatue / Philippe Laffont** — 3Y clone beat both benchmarks; full window unavailable.
- **Berkshire Hathaway / Warren Buffett** — exceptional official record beyond the disclosed 13F sleeve.
- **Pershing Square / Bill Ackman** — strong official strategy record not reproduced by the delayed 13F clone.

---

## 📊 Core Dashboards

### 1. QoQ Changes Across The Board (`/`)
- **Actionable Signal KPIs**: Leading consensus buy and sell with separate increased/new and decreased/exited counts, largest net aggregate reported value inflow and outflow, broadest new idea, highest median conviction with a five-holder floor, and the closest high-conviction holding to its latest OpenBB-derived 52-week low. Each card links directly to its ticker detail.
- **Symmetric QoQ Trade Panels**: Current-quarter buy and sell rankings by reported value change, plus largest additions and reductions by portfolio/share percentage.
- **Manager Activity Matrix**: One row per tracked manager with the five largest share buys and sells, each labeled with the change in portfolio weight in percentage points.
- **Alpha Whale Forward Index (AWFI)**: Most Owned, Highest Median Portfolio, and forward-ranking views show distinct 6-, 12-, 18-, and 24-month AWFI Research v2 scores using profiles selected from the stored production candidate study. The 6-month profile is action-heavy with technical support, 12 months is balanced with technical support, 18 months is balanced without technical timing, and 24 months uses the tested Alpha-only profile rather than duplicating 18 months. Historical periods use exact persisted v2 research scores; a newer current filing period keeps filing inputs fixed while refreshing technical inputs through the latest cached market session. The selected thresholds are ±75 for 6/12/18 months and ±25 for the distinct 24-month Alpha-only profile. See the [model specification](predictive_sentiment/ALPHA_WHALE_FORWARD_INDEX.md) and [data-lineage runbook](docs/AWFI_DATA_LINEAGE.md).
- **20-Quarter Filing History**: The QoQ dashboard defaults to the latest quarter and can switch among the latest 20 quarter-ends. Historical cross-fund snapshots are fetched lazily through edgartools and persisted under `cache/history/<period>.json`.

Warm all selectable historical snapshots without re-fetching the latest quarter:

```powershell
python prefetch.py --history-only
```

Warm OpenBB fundamentals, valuation/timing models, and local pair signals for the popular ticker shortcuts:

```powershell
python prefetch.py --ticker-intelligence-only
```

When switching periods, the dashboard shows whether it is loading the latest cache, memory, a persisted disk snapshot, or building a new snapshot from live SEC filings. Live historical builds report completed funds as they progress.
- **Superinvestor Portfolio Stats**: Most-owned activity, highest median conviction, current-quarter buys and sells, concentrated single-manager bets, and 5%+ holdings ranked by distance above their OpenBB-derived 52-week low.
- **Investor Directory**: The dedicated Investor Portfolios view provides strategy badges, catalog annotations, AUM, position counts, and top holdings without duplicating the directory on the QoQ dashboard.
- **Interactive Visualizations**:
  - Breakdown of New Buys, Increases, Decreases, Closed by Fund Group (Plotly).
  - Top 10 Dollar Value Shifts ($M) horizontal bar chart (Plotly).
- **Master QoQ Table**: Multi-column sortable, real-time search, filter by investment style, Action Status (NEW, INCREASED, DECREASED, CLOSED), Min $M move, pagination, and one-click CSV export.

### 2. Ticker Level Intelligence (`/ticker` & `/ticker/{ticker}`)
- **Market Snapshot**: OpenBB/yfinance current price, daily move, latest 52-week-low price and percentage above that low, market cap, trailing and forward P/E, 52-week range, beta, one-year return, sector, industry, and exchange.
- **Estimated Alpha Whale Price**: A clearly labeled 20-quarter weighted-average basis model for currently tracked shares. This is an estimate, not reported investor cost basis.
- **Tabbed Ticker Workspace**: Keeps ticker identity and the latest 13F snapshot visible, then organizes the detail view into Decision, Market, Whale Activity, Ownership, and Pairs tabs. Decision opens with an all-horizon AWFI Research v2 breakdown, valuation, technical timing, and educational risk sizing. The complete Alpha Whale Sentiment model and history remain available under Whale Activity.
- **Direct-Impact Ticker News**: Shows up to five recent OpenBB/yfinance headlines only when the company or ticker appears in the headline and the story contains a material event signal such as earnings, guidance, a product launch, a deal, regulation, leadership, or litigation. Fewer items are shown instead of padding the section with loosely related coverage.
- **Technical Timing**: RSI(14), RSI(2), 50/200-day trend distance, six-month momentum, annualized volatility, trend regime, and entry-timing state.
- **Stock-Specific Valuation**: Opens with the recommended framework, primary fair value, margin-of-safety price, and a non-composite method-agreement diagnostic. A compact Decision Set is followed by Intrinsic, Relative, Graham, Asset & Special, and All tabs covering scenario and reverse DCF, residual income, dividend discount, normalized P/E, P/E/PEG/P/B, EV multiples, Graham Number, revised and conservative Graham growth formulas, NCAV, tangible asset value, SOTP, REIT NAV/AFFO, and real options. Each card translates the calculation into a decision-oriented Method Read rather than displaying data readiness as `AVAILABLE`; every method also retains its methodology-and-applicability tooltip.
- **Illustrative Risk Budget**: A non-personalized position range combining valuation, long-term trend, whale flow, and volatility. It caps a stock at 5% of an assumed 30% All Weather-style equity sleeve.
- **Pair Trading Research**: Same-industry economic peers are tested with five years of dividend-adjusted prices, bidirectional cointegration, multiple-testing correction, out-of-sample persistence, sub-window stability, and half-life gates. The view distinguishes READY, WAIT, and NO VALID PAIR and shows both stock/short-stock and stock/paired-put execution structures only when actionable.
- **20-Quarter Trends**: Historical tracked-investor count, total reported holdings value, validated and indicative sentiment, meaningful breadth, relative conviction, raw activity, and a daily stock-price overlay clipped to the same 20-quarter window.
- **Alpha Whale Sentiment**: Separates raw share activity from meaningful manager-relative conviction without using market price or estimated execution/cost basis in the score. The card shows exact new, increased, decreased, and exited counts; continuing positions compare share-change percentage with the manager's normal adjustment; new and closed positions compare reported position size with the manager's normal holding.
- **Integrated Flow Cross-Check**: Estimated net flow, gross inflow, and gross outflow are displayed inside the sentiment section. Flow can confirm or diverge from a directional regime but never enters the sentiment score.
- **Expected 13F Timeline**: Cyan deadline markers and vertical guide lines sit exactly 45 calendar days after each report-period end. They are a standard disclosure expectation, not the selected managers' actual filing dates.
- **Consensus Analytics**: Total value held across all funds, total shares, median portfolio weight, and elite-holder count.
- **Visuals**:
  - Top 20 Most Popular Tickers across elite managers (Plotly).
  - Free embedded TradingView market-price chart.
  - Investor-count and reported-value trends, a multi-trace sentiment chart with daily stock price and expected 13F deadline markers, and a manager conviction heatmap across 20 quarters.
  - Ownership Distribution Donut Chart (Plotly).
  - QoQ Value Shift by Fund Bar Chart (Plotly).
- **Holders Table**: Complete breakdown of which funds hold the stock, individual weights, dollar values, share counts, and QoQ actions.
- **Search & Quick-Picks**: The 12 highest-consensus tickers are derived dynamically from the current roster by holder count and tracked value; ticker-intelligence prefetch warms the same set.

### 3. Investor Portfolio View (`/investor` & `/investor/{cik}`)
- **Investor Selector Gallery**: Filterable card gallery of the current roster.
- **Fund Deep Dive**:
  - Header with Manager name, investment style, Catalog annotation, CIK, and Filing Period.
  - Concentration Stats: Top 5 Weight %, Top 10 Weight %, Active vs Closed counts.
  - Interactive Donut Chart of Top 10 Positions + Other.
  - QoQ Position Shifts Bar Chart ($M).
  - A sticky Portfolio View switcher for Current Holdings, Activity History, and Portfolio History.
  - Current Holdings table with grouped position, market-context, and quarterly-activity columns. Market context includes implied reported price, latest cached price, price movement since the report, 52-week low, and percentage above the low. Derived and time-sensitive columns include formula-and-interpretation tooltips.
  - Activity History grouped by filing period, with actual filing date, exact share and portfolio-weight changes, and All Activity, Buys, and Sells filters.
  - Portfolio History across the latest 20 report periods, including reported portfolio value, position count, and the top 20 holdings by portfolio weight.
  - One-click Portfolio CSV export.

### 4. Investor Screening (`/screening`)
- **Mega High-Conviction Default**: Starts at a $10B four-quarter median
  reported 13F value, 80% direct-company-stock sleeve, 40% top-10
  concentration, six-of-eight-quarter persistence, and at least one current
  stock observed at or above 3% of reported non-option 13F value in each of the
  latest five quarterly snapshots, spanning approximately 12 months.
- **Dynamic Presets and Controls**: Relaxed Scale, Mega High-Conviction,
  Patient Tilt, Strict Best-Bet, and Persistent Best-Bet presets with editable
  size, stock-count, direct-stock, concentration, long-term-best-bet,
  performance, roster-only, and manager-search filters.
- **Precomputed Read-Only Snapshot**: Queries a compact DuckDB snapshot rather
  than rescanning the full historical 13F foundation on every request. A
  reusable 1,809,508-row, 20-quarter position cube supports dynamic best-bet
  weight, duration, and count filters without rebuilding data.
- **Disclosure-Lagged Performance Comparison**: Shows hypothetical 3Y/5Y
  annualized sleeve returns, excess CAGR versus SPY and QQQ, drawdown, and
  price coverage. Filters support SPY/QQQ hurdles, minimum excess CAGR,
  quarterly beat consistency, and maximum drawdown.
- **Manager Detail Navigation**: Every screening result row opens an investor
  detail page. Configured managers retain their full cached dashboard; other
  13F managers use the compact snapshot's material 1%+ and quarterly top-ten
  positions across up to 20 periods.
- **Transparent Research Caveats**: Clearly distinguishes reported 13F value
  from AUM, observed duration from continuous ownership, and estimated turnover
  from actual manager trading. Performance is a current-cohort reported-sleeve
  estimate before fees, taxes, and transaction costs—not a fund return.

---

## ⚡ Live Updates & Real-Time Sync

- **Server-Sent Events (SSE)**: Uses `/events` stream to broadcast updates whenever SEC 13F data refreshes.
- **AWFI Publication Events**: Completed research snapshots emit
  `awfi_published`; ticker pages reload the complete current-score and history
  payload.
- **Manual Refresh**: Click **"Refresh Now"** in the top navigation bar to trigger a background re-fetch.
- **Automated Caching**: In-memory + persistent disk cache (`cache/`) with a 6-hour TTL and fast cached startup.
- **Market Context**: OpenBB's yfinance provider supplies daily price history for high-conviction holdings; results are persisted in `cache/market_insights.json`.
- **Ticker Market Cache**: On-demand OpenBB quote, fundamental, profile, valuation, technical, and serialized daily close history is cached for six hours under `cache/ticker_market/<ticker>.json`.
- **Pair Signal Cache**: Pair candidates and statistical diagnostics are cached for six hours under `cache/pair_signals/<ticker>.json`. The semantic peer universe is owned locally at `data/reference/full_universe.csv`; the application has no runtime dependency on the sibling `invest` repository.

---

## 🚀 Getting Started

### 1. Run the application
```bash
python run.py
```
Or with uvicorn:
```bash
python -m uvicorn main:app --reload --port 8000
```

### 2. Pre-fetch / Warm Cache (Optional)
```bash
python prefetch.py
```

### 3. Access Dashboard
Open your browser at `http://localhost:8000`

---

## 🔌 API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/qoq-changes` | GET | All QoQ position changes (params: `group`, `status`, `min_value`) |
| `/api/portfolio-stats` | GET | Two-quarter buy aggregates and OpenBB 52-week-low proximity data |
| `/api/filing-periods` | GET | Latest 20 selectable quarter-end periods |
| `/api/period-cache-status` | GET | Historical snapshot cache source and live build progress |
| `/api/period-view` | GET | Complete dashboard payload for one filing period |
| `/api/ticker-view` | GET | All tickers aggregated with holders and total value |
| `/api/ticker/{ticker}` | GET | Detailed consensus and holders for a specific ticker |
| `/api/ticker/{ticker}/awfi-history` | GET | Persisted AWFI history plus publication state and snapshot version |
| `/api/ticker/{ticker}/intelligence` | GET | OpenBB market snapshot, valuation, timing, estimated whale basis, flow, sizing, and 20-quarter history |
| `/api/ticker/{ticker}/pair-signal` | GET | Local hypothesis-tier pair diagnostics and readiness |
| `/api/investor-view` | GET | Summary status list for the configured roster |
| `/api/investor/{cik}` | GET | Detailed portfolio holdings, closed positions, and stats for a fund |
| `/api/investor/{cik}/history` | GET | Lazy-loaded 20-quarter investor activity and portfolio history |
| `/api/screening` | GET | Filtered universe-wide investor screening snapshot and summary |
| `/api/roster` | GET | Current persistent roster and exception flags |
| `/api/roster` | POST | Bulk include, include-and-flag, or exclude screening managers |
| `/api/fund-status` | GET | Real-time loading status and KPI summary |
| `/api/refresh` | GET | Trigger background refresh across all configured funds |
| `/events` | GET | Server-Sent Events (SSE) live data stream |

## Validation

```powershell
# Application smoke checks
python -m compileall -q config.py roster_store.py data_service.py awfi_service.py main.py pair_service.py prefetch.py run.py predictive_sentiment
python -c "import main; print(type(main.app).__name__, len(main.data_service.cache))"

# Focused unit tests
python -m pip install pytest
python -m pytest tests/test_market_insights.py -q
python -m pytest tests/test_investor_history.py -q
python -m pytest tests/test_sentiment_conviction.py -q
python -m pytest tests/test_awfi_service.py -q
python -m pytest tests/test_awfi.py tests/test_awfi_period_view.py tests/test_predictive_sentiment.py tests/test_predictive_sentiment_cli.py -q
python -m pytest tests/test_investor_screening.py -q
python -m pytest tests/test_roster_store.py -q
```
