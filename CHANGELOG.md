# Changelog

## 2026-09-01 - AWFI Research v2 and resilient history publication

### Added

- Distinct 6-, 12-, 18-, and 24-month AWFI Research v2 profiles based on the
  stored production candidate study.
- A latest-available roster universe that prefers validated current application
  caches when they are newer than the official quarterly SEC archive.
- Protocol, model, configuration, roster, source, and universe freshness
  detection.
- OS-backed single-writer publication, unique staging databases, DuckDB
  checkpoint/reopen validation, and atomic snapshot replacement.
- Pre-publication checks for duplicate keys, incomplete horizons, invalid
  scores, signal mismatches, missing mappings, and residual WAL files.
- Automatic AWFI rebuild checks after startup, full SEC refreshes, manual
  refreshes, and roster changes.
- `awfi_published` SSE updates plus snapshot-version polling fallback.
- Current-universe mapping and score-coverage metrics in research summaries.
- A dedicated AWFI data-lineage and publication runbook.

### Fixed

- 18- and 24-month AWFI no longer share the same score formula.
- The 24-month signal uses its tested Alpha-only profile rather than changing
  verdicts solely through a lower threshold on the 18-month score.
- INTC history now displays the latest 20 filing periods across all four
  horizons.
- AWFI history no longer disappears while another process rebuilds the
  predictive database.
- Newer application filing caches can no longer be ignored by an older
  official-archive universe.
- Cache writes can no longer expose partially written JSON to the research
  publisher.
- Ticker pages reload the full AWFI payload after publication rather than
  combining old current scores with new history.

## 2026-08-29 - Dynamic investor screening cube

### Added

- A 1,809,508-row, 20-quarter position fact cube supports dynamic best-bet weight,
  6/12/18/24-month duration, and required-count filters without rebuilding
  screening data.
- Performance & Benchmarking filters now support minimum excess CAGR,
  quarterly benchmark beat consistency, and maximum drawdown.
- A Strict Best-Bet preset applies the reviewed $10B, five-stock, persistent
  5%-weight, and beat-SPY/QQQ settings.
- A Persistent Best-Bet preset applies the reviewed $500M, three-stock,
  persistent 3%-weight, full-history, beat-SPY/QQQ settings.
- Qualified-manager rows now support mouse and keyboard navigation into
  investor detail pages. Non-roster managers receive a snapshot-backed,
  explicitly material-position-scoped view.

### Changed

- Long-term-investor eligibility now measures current stocks that remained
  above the selected share of total reported non-option 13F value in every
  required quarterly snapshot.
- Estimated turnover remains visible as a diagnostic but is no longer a
  default eligibility filter.
- Future performance refreshes use broad size/history eligibility rather than
  the active screening preset; criteria changes reuse existing performance
  facts.
- Performance refreshes now checkpoint each manager transactionally, process
  bounded chronology/price batches, and resume matching incomplete campaigns
  without repeating completed managers.
- The first all-manager campaign completed 5,574 checkpoints over the
  five-year/20-quarter window, publishing 16,722 window summaries with no
  failed or exhausted managers.

## 2026-08-29 - Ticker sentiment and market context

### Changed

- Consensus cards now show separate increased/new and decreased/exited
  investor counts instead of combined "or" descriptions.
- The overview 52-week-low signal always uses the latest cached market data.
- Ticker details show the latest 52-week-low price and percentage above it with
  proximity-aware coloring.
- Alpha Whale Sentiment is the first Purchase Decision Support section and
  includes exact action counts, formula-and-interpretation tooltips, dynamic
  regime explanations, and integrated net/gross flow evidence.
- The sentiment chart overlays daily stock prices only across the displayed
  20-quarter window and extends through the latest OpenBB close.
- Standard expected 13F deadline markers and vertical guides appear 45
  calendar days after each report-period end for leading/lagging visual
  analysis.
- Ticker market cache version 5 serializes daily close history.
- Investor portfolio tables now group position, market-context, and quarterly
  activity columns, including implied reported price, latest cached price,
  since-report movement, 52-week-low proximity, market-data date, and expanded
  CSV export fields.
- Investor detail pages now include lazy-loaded Activity History and Portfolio
  History tabs across the latest 20 report periods, with buy/sell filtering,
  actual manager filing dates, signed activity metrics, historical portfolio
  values, position counts, and ranked top-holding chips.
- The investor Portfolio View switcher now remains visible while scrolling, and
  holdings-table metric columns include calculation and interpretation
  tooltips.

## 2026-08-28 - Initial repository release

### Added

- Alpha Whales Intelligence branding and responsive dashboard.
- SEC 13F tracking for 26 configured investment managers.
- Latest and 20-quarter historical filing snapshots.
- Filing-period selector with cache source and live SEC progress.
- Actionable consensus, flow, conviction, and valuation signals.
- Manager Activity Matrix with portfolio-weight changes.
- Symmetric current-quarter buy and sell panels.
- Median ownership and conviction metrics.
- OpenBB 52-week-low market context.
- Ticker fundamentals, valuation, technical timing, and TradingView chart.
- Estimated Alpha Whale basis and estimated institutional flow.
- Twenty-quarter ticker ownership, value, and action trends.
- Graham valuation and 20% margin-of-safety purchase price.
- Educational All Weather-style risk range.
- Local hypothesis-tier pair-trading diagnostics.
- Three-stage ticker loading experience.
- SSE refresh notifications and manual refresh controls.
- Alpha Whale Sentiment index combining investor breadth and portfolio-weight
  conviction, with a manager heatmap and flow-divergence cross-check.
- Manager-relative conviction that distinguishes routine trades from meaningful
  changes without price or cost-basis estimates. Continuing positions use
  share-change percentage versus normal adjustment; new and closed positions
  use position size versus normal holding size.
- Complete-filing selection across original and amended 13F submissions,
  including cross-CIK comparison continuity.
- Legacy 13F dollar/thousand-dollar normalization and rejection of 1,000x
  amendment scaling errors.
- Indicative low-participation sentiment history with a separate validated
  overlay, preserving true no-signal gaps.
- Universe-wide Investor Screening page with configurable size, direct-stock,
  concentration, persistence, turnover, durability, roster, and search
  filters backed by a compact DuckDB snapshot.
- Standalone SEC ownership research foundation with official flattened archive
  ingestion, raw provenance storage, Parquet bronze data, DuckDB analytical
  views, quality validation, and accession-level EdgarTools verification.
- Complete official bulk-history loaders for Form 13F, Forms 3/4/5, N-PORT,
  and N-MFP, retaining source archives and lossless schema-evolving Parquet.
- Accession-level ingestion for Schedule 13D/G, Form 144, N-CEN,
  N-CSR/N-CSRS, and N-PX with typed, XML, and raw-only provenance states.
- Full integrity-audit command covering archive hashes, raw-submission hashes,
  Parquet row counts, accession coverage, orphan records, and screening
  snapshot readability.
- Atomically published read-only Investor Screening snapshot with a $10B
  default, material-operation turnover deadbands, CIK-history continuity,
  dynamic presets, responsive filtering, and keyboard-accessible sorting.

### Data corrections

- Migrated Pershing Square to its current reporting CIK while retaining its
  former CIK for historical continuity.
- Derived missing entry and exit changes instead of converting null fields to
  zero.
- Reconciled net owner changes with new minus closed holders.
- Distinguished share actions from reported market-value changes.

### Documentation

- Added architecture, methodology, operations, and repository instructions.
