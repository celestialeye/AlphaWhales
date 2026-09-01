# Alpha Whale Sentiment Predictive Research

This package tests whether a publicly reconstructable Alpha Whale Sentiment
signal predicts a stock's split-and-dividend-adjusted price direction over the
next 126, 252, 378, and 504 trading sessions, approximately 6, 12, 18, and 24
months. SPY excess return remains a secondary diagnostic rather than the
BUY/SELL target.

The study is deliberately separate from dashboard rendering. Historical
signals read the accession-level Investor Screening DuckDB and adjusted-price
manifest in read-only mode. Current `cache/<cik>.json` files may define the
latest top-ten universe when they contain a newer filing period than the
official quarterly archive. Historical signal values still come from the
official archive; the pipeline never substitutes `cache/history/*.json`,
current ticker intelligence, quarter-end prices as signal dates, or a network
price fallback.

## Fixed protocol

- Reconstruct each manager's quarter at exactly 45 calendar days after quarter
  end, using only filings available by that date.
- Apply original, `RESTATEMENT`, and `NEW HOLDINGS` amendment semantics.
- Compare exact consecutive quarters by CUSIP. A missing filing is unknown, not
  a portfolio liquidation.
- Freeze the union of each current roster manager's latest top 10 direct common
  stocks ranked by reported portfolio weight. Select the newer valid source
  separately for each manager: current application cache or official archive.
  Managers with fewer than 10 stocks retain all holdings. Exclude ETFs, funds,
  options, and non-common instruments.
- Detect coordinated split-like share-count changes before measuring manager
  conviction.
- Execute on the first cached SPY session strictly after the fixed as-of date.
- Label exact 126-, 252-, 378-, and 504-session adjusted returns and arithmetic
  SPY excess.
- Test BUY and SELL thresholds independently over the fixed grid
  `{25, 50, 75, 100}`. A candidate can therefore require, for example,
  `+50` to BUY and `-75` to SELL.
- Run sentiment-only and sentiment-plus-technical experiments separately.
- Technical experiments test both the dashboard-compatible trend regime and a
  stricter 50/200-session plus six-month-momentum confirmation.
- Select a candidate inside each outer test quarter using earlier, purged data
  only. Forward labels overlapping the test period are excluded.

The headline result reports accuracy, balanced accuracy, Wilson confidence
intervals, matched walk-forward baselines, prediction coverage, class counts,
quarterly rank information coefficient, and a quarter-block bootstrap.

The `TRUSTWORTHY` gate requires at least 90% accuracy together with substantial
sample size, coverage, class balance, rank correlation, robust confidence
bounds, and point-in-time manager selection. The default study applies today's
roster retrospectively, so it is intentionally blocked by
`CURRENT_ROSTER_RETROSPECTIVE` even if its statistical metrics look strong.
This prevents roster-selection hindsight from becoming a false validation.
The current CUSIP mapping is persisted for audit but is not effective-dated,
so `CURRENT_CUSIP_MAPPING` remains a second mandatory trust blocker until a
historical security master is available.

The original threshold and technical-gate phases kept `alpha_v1_n3` fixed.
Later decomposed experiments tested institutional component weights through a
blocked two-stage selection process. AWFI Research v2 now publishes the
evidence-backed 6-, 12-, 18-, and 24-month profiles documented in
[`ALPHA_WHALE_FORWARD_INDEX.md`](ALPHA_WHALE_FORWARD_INDEX.md). Further changes
to conviction math, support components, or manager participation require a new
versioned experiment.

The `signals` command exposes both `research_signal` and `decision_signal`.
Research candidates may be BUY or SELL, but the deployable decision is forced
to HOLD until every trustworthy-gate criterion passes.

The evidence review and next preregistered feature families are documented in
[`SUPPORT_SIGNALS.md`](SUPPORT_SIGNALS.md).

The decomposed institutional experiment uses a blocked two-stage inner sweep:
institutional recipes are screened first, then technical weight and asymmetric
BUY/SELL thresholds are tuned on a later disjoint block. No outer-quarter
outcome participates in either stage.

The current top-ten-universe results and backfill coverage are documented in
[`TOP10_HOLDINGS_STUDY.md`](TOP10_HOLDINGS_STUDY.md).

The exact tested definition of the new forward index is documented in
[`ALPHA_WHALE_FORWARD_INDEX.md`](ALPHA_WHALE_FORWARD_INDEX.md).

Runtime data lineage, freshness detection, and atomic publication are
documented in
[`../docs/AWFI_DATA_LINEAGE.md`](../docs/AWFI_DATA_LINEAGE.md).

Treasury, DXY, sector-relative, stock-sensitivity, and point-in-time
fundamental challenger results are documented in
[`CHALLENGER_EXPERIMENTS.md`](CHALLENGER_EXPERIMENTS.md).

## Commands

```powershell
python -m predictive_sentiment.cli validate-inputs
python -m predictive_sentiment.cli run
python -m predictive_sentiment.cli report
python -m predictive_sentiment.cli signals --horizon 126
```

Generated results are stored in
`data/investor_screening/predictive_sentiment.duckdb`, which is excluded from
Git with the rest of the generated Investor Screening data.

## Interpretation limits

- Adjusted-price coverage varies by security and can extend to 2010. Newer
  listings retain their actual market-history start, so older filings may
  improve manager baselines without producing a usable forward-return label.
- The persisted CUSIP mapping is not a historical security master.
- Missing terminal prices can create delisting attrition and are reported.
- Filing dates, rather than acceptance timestamps, force next-session
  execution.
- This is hypothesis-tier research, not investment advice or proof of causal
  predictiveness.
