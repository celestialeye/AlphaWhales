# AWFI Data Lineage and Publication

## Purpose

This document explains how Alpha Whale Forward Index (AWFI) data moves from
SEC filings and application caches into historical and current ticker scores.
It also defines the freshness, publication, and integrity controls that prevent
stale or incomplete history from being displayed.

The quantitative model is documented separately in
[`predictive_sentiment/ALPHA_WHALE_FORWARD_INDEX.md`](../predictive_sentiment/ALPHA_WHALE_FORWARD_INDEX.md).

## Versioned baseline

The baseline verified on 2026-09-01 is:

| Item | Value |
|---|---|
| AWFI model | `awfi-research-v2` |
| Predictive protocol | `alpha-whale-predictive-v9.6` |
| Target horizons | 126, 252, 378, and 504 trading sessions |
| Display horizons | 6, 12, 18, and 24 months |
| Active managers | 29 |
| Latest universe source period | 2026-06-30 |
| Latest official 13F source period | 2026-03-31 |
| Frozen manager/top-holding rows | 282 |
| Frozen unique CUSIPs | 135 |
| Mapped CUSIPs | 132 |
| Scored CUSIPs | 122 |
| Mapping coverage | 97.8% |
| Score coverage | 90.4% |

The latest universe can be newer than the official bulk archive because the
application receives roster filings directly through EdgarTools before the SEC
publishes the next quarterly structured archive.

## Source layers

| Layer | Location | Role |
|---|---|---|
| Current roster cache | `cache/<cik>.json` | Newest filing for each configured manager |
| Historical application cache | `cache/history/<period>.json` | Latest 19 non-current selectable quarters |
| Official 13F archive | `data/investor_screening/investor_screening.duckdb` | Amendment-aware historical filings and holdings |
| Price and mapping store | `data/investor_screening/performance.duckdb` | CUSIP mappings and adjusted price histories |
| Predictive research store | `data/investor_screening/predictive_sentiment.duckdb` | Runs, universes, features, labels, scores, and provenance |

Current cache files are written through a temporary file and `os.replace`.
Readers therefore see either the prior complete JSON document or the new
complete document, never a partially written file.

Cache files are accepted only when:

- the embedded CIK matches the expected manager;
- the roster-derived `fund_fingerprint` matches;
- status is `loaded`;
- `metadata.report_period` is a valid date; and
- holdings are a JSON array with valid direct-stock rows.

## Canonical data flow

```text
roster.json
  -> latest official top-ten direct holdings per manager
  -> latest validated application-cache top ten per manager
  -> choose the newer source separately for each manager
  -> freeze one current 29-manager universe
  -> hash the complete universe

official historical 13F archive
  -> amendment-aware manager snapshots
  -> consecutive-quarter manager changes
  -> Alpha sentiment and decomposed institutional features

performance.duckdb
  -> CUSIP-to-market-symbol mapping
  -> point-in-time technical features
  -> exact forward labels for research evaluation

frozen universe + historical features
  -> four AWFI Research v2 horizon scores
  -> predictive_sentiment.duckdb staging generation
  -> integrity validation
  -> atomic publication
  -> FastAPI
  -> ticker table and Plotly history
```

## Latest-per-manager universe selection

AWFI does not use every security held by every manager. It takes each
manager's ten largest direct-stock positions, then unions those CUSIPs.

For each manager:

1. Load the newest official archive portfolio.
2. Load the validated application cache portfolio.
3. Compare report periods.
4. Use the cache only when it is newer than the archive.
5. Otherwise use the official archive.
6. Deduplicate by CUSIP and retain the top ten by portfolio weight, reported
   value, and CUSIP.

This prevents the previous failure where Q2 application data was visible in
the dashboard while AWFI still froze its universe from Q1 and omitted INTC.

The selected rows retain `universe_source` in memory and publish aggregate
source counts plus the latest universe period in `run_artifact_provenance`.

## Historical and current-period semantics

Historical AWFI values and the newest live value have different timing roles.

### Historical values

- Filing inputs come from the official amendment-aware archive.
- The signal date is quarter-end plus 45 calendar days.
- Technical inputs use only prices strictly before the first following SPY
  session.
- Scores are persisted under one complete protocol, model, roster, source, and
  universe lineage.

### Current application period

The newest application period can arrive before the official archive.

- Filing inputs come from the current roster cache.
- The filing period remains fixed.
- Technical inputs advance through the latest cached market session.
- The API appends this live score to the compatible persisted history.
- The browser displays at most the latest 20 filing periods.

The current live point is not written into the historical research database
until the corresponding official source data becomes available and a new
research generation is published.

## Horizon model contract

Every eligible score row must contain all four horizons:

| Horizon | Profile |
|---|---|
| 6 months | Action-heavy institutional score with technical support |
| 12 months | Balanced institutional score with technical support |
| 18 months | Balanced institutional score without technical timing |
| 24 months | Tested Alpha-only long-horizon profile |

The 18- and 24-month profiles are intentionally distinct. Publication fails if
any period/CUSIP score set does not contain exactly four horizons.

## Freshness detection

Application startup, scheduled full refreshes, manual refreshes, and roster
changes check whether AWFI must be rebuilt.

The published run is stale when any of these differ:

- `AWFI_VERSION`;
- predictive `PROTOCOL_VERSION`;
- score-affecting `ResearchConfig`;
- roster file SHA-256;
- latest-per-manager universe fingerprint;
- latest universe report period;
- official 13F filing count;
- official 13F holding count;
- latest official filing date;
- latest official report period;
- a roster-scoped content fingerprint over consumed filings and holdings;
- performance database size and modification time;
- mapping count and latest mapping retrieval time;
- price-manifest status counts, row counts, and latest update times.

The current implementation uses `LATEST_AVAILABLE_ROSTER_TOP10` as the
universe mode. A same-quarter cache change therefore triggers a rebuild even
when the maximum report date does not change.

## Serialized refresh behavior

All full cache refresh callers wait for an active refresh to finish instead of
returning early. AWFI freshness is checked only after the manager cache set
reaches a stable terminal state.

The following paths trigger the same coordinated flow:

- application startup;
- the controlled automatic refresh loop;
- `/api/refresh`;
- roster additions;
- roster removals; and
- roster metadata updates.

Only one AWFI publisher can run across processes. The publisher uses an
OS-backed nonblocking file lock. Lock ownership is released by the operating
system when the process exits.

## Atomic publication

`python -m predictive_sentiment.cli run` performs these steps:

1. Acquire the interprocess publication lock.
2. Copy the currently published database to a unique staging path.
3. Build the new run only in staging.
4. Execute a DuckDB `CHECKPOINT`.
5. Require the staging WAL to be absent.
6. Reopen staging read-only.
7. Run all pre-publication integrity gates.
8. Flush the staging database.
9. Replace the published database atomically.
10. Keep the prior database if any build or validation step fails.

Windows replacement retries for up to approximately two minutes when a reader
briefly holds the published database open.

## Pre-publication integrity gates

Publication is rejected when any of these checks fail:

- no complete run exists for the current protocol and AWFI version;
- duplicate `run_mapping(run_id, cusip)` keys;
- duplicate `run_top_holdings(run_id, canonical_cik, holding_rank)` keys;
- duplicate `decomposed_features(run_id, report_period, cusip, horizon)` keys;
- duplicate `awfi_scores(run_id, report_period, cusip, horizon)` keys;
- non-finite scores;
- scores outside `[-100, 100]`;
- signals that do not recompute from score and threshold;
- a period/CUSIP with fewer or more than four horizons;
- an AWFI score without a run-specific CUSIP mapping; or
- a WAL remaining after checkpoint.

The completed run summary also records:

- current-universe CUSIPs;
- mapped current-universe CUSIPs;
- scored current-universe CUSIPs;
- mapping coverage; and
- score coverage.

## Browser synchronization

Successful local publication emits an `awfi_published` SSE event.

Ticker pages then reload the complete ticker payload, including:

- current scores;
- current AWFI metadata;
- persisted history;
- filing period; and
- market-data date.

The lightweight `/api/ticker/{ticker}/awfi-history` endpoint provides snapshot
version and refresh state for polling fallback. A changed snapshot reloads the
full ticker payload instead of merging old current scores into new history.

## Expected coverage gaps

Not every current top holding has 20 historical AWFI periods.

Legitimate causes include:

- a newly public security;
- a recently initiated manager position;
- no historical position in the official source;
- no usable manager-relative signal;
- unavailable CUSIP mapping;
- unavailable adjusted prices; or
- insufficient pre-signal market history.

Examples from the 2026-09-01 audit:

- AAPL, INTC, and NVDA displayed 20 periods.
- CRCL displayed two periods.
- AEM displayed only the current live period.

These are valid eligibility differences. A ticker that remains eligible in the
current universe must not lose established history merely because the official
archive lags the application cache. A ticker that leaves the current universe
can legitimately become unavailable in a later canonical run.

## Verified integrity results

The 2026-09-01 audit reported:

| Check | Result |
|---|---:|
| Duplicate AWFI keys | 0 |
| Invalid or out-of-range scores | 0 |
| Signal/threshold mismatches | 0 |
| Formula recomputation mismatches | 0 of 9,104 joined rows |
| 18M/24M identical rows | 1 of 2,637 |
| Mean absolute 18M/24M difference | 20.56 points |
| INTC persisted score rows | 84 |
| INTC displayed filing periods | 20 |
| INTC horizons per displayed period | 4 |

## Operational checks

Check whether the current snapshot is stale:

```powershell
python -c "from predictive_sentiment.publication import research_snapshot_needs_refresh; print(research_snapshot_needs_refresh())"
```

Build and atomically publish AWFI:

```powershell
python -m predictive_sentiment.cli run
```

Run focused tests:

```powershell
python -m pytest tests/test_awfi.py tests/test_awfi_service.py tests/test_awfi_period_view.py tests/test_predictive_sentiment.py tests/test_predictive_sentiment_cli.py -q
```

The running application should report `refresh_state: current` from:

```text
/api/ticker/{ticker}/awfi-history
```
