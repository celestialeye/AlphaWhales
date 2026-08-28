# Changelog

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
  changes using estimated trade weight divided by each manager's typical
  position size.
- Complete-filing selection across original and amended 13F submissions,
  including cross-CIK comparison continuity.
- Legacy 13F dollar/thousand-dollar normalization and rejection of 1,000x
  amendment scaling errors.

### Data corrections

- Migrated Pershing Square to its current reporting CIK while retaining its
  former CIK for historical continuity.
- Derived missing entry and exit changes instead of converting null fields to
  zero.
- Reconciled net owner changes with new minus closed holders.
- Distinguished share actions from reported market-value changes.

### Documentation

- Added architecture, methodology, operations, and repository instructions.
