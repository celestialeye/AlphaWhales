# Operations Guide

## Requirements

- Python 3.11 or newer.
- Internet access for SEC EDGAR, OpenBB/yfinance, FRED, and TradingView.
- An SEC-compliant identity in the `EDGAR_IDENTITY` user environment variable.

## Install and run

```powershell
python -m pip install -r requirements.txt
python run.py
```

Direct Uvicorn:

```powershell
python -m uvicorn main:app --reload --port 8000
```

Open `http://127.0.0.1:8000`.

## Cache warming

Latest filings and market insights:

```powershell
python prefetch.py
```

All 19 historical snapshots:

```powershell
python prefetch.py --history-only
```

Popular ticker market, valuation, timing, and pair caches:

```powershell
python prefetch.py --ticker-intelligence-only
```

Official accession-vintage SEC financial statement archives:

```powershell
python -m investor_screening.cli list-bulk-datasets fundamentals --start-date 2012-01-01
python -m investor_screening.cli backfill-bulk fundamentals --start-date 2012-01-01
python -m investor_screening.cli refresh-bulk-views
```

The `fundamentals` family imports the SEC Financial Statement Data Sets without
semantic deduplication. Original filings, amendments, concepts, units,
contexts, acceptance timestamps, source row numbers, and archive hashes remain
available in the bronze Parquet lake. Research code must select only facts
accepted by the historical feature date.

Run the portfolio-action challenger against a completed AWFI Research v2
snapshot:

```powershell
python -m predictive_sentiment.cli action-backtest
python -m predictive_sentiment.cli action-report
```

Freeze the current AWFI Research v2 score and sweep only portfolio-action
thresholds:

```powershell
python -m predictive_sentiment.cli action-backtest --current-awfi-only
```

Reconstruct and test the actual historical valuation methods:

```powershell
python -m predictive_sentiment.cli valuation-backtest --replace
python -m predictive_sentiment.cli valuation-report

# Exploratory lower-coverage pass for sparse methods
python -m predictive_sentiment.cli valuation-backtest `
  --minimum-feature-availability 0.20 `
  --replace
```

After the SEC fundamentals backfill and bronze-view refresh:

```powershell
python -m predictive_sentiment.cli action-backtest `
  --fundamentals-db data\investor_screening\investor_screening.duckdb `
  --replace
```

Action experiments write to
`data/investor_screening/awfi_action_experiments.duckdb` and do not modify the
published AWFI Research v2 database or runtime signals.

Latest refresh plus both optional warm-ups:

```powershell
python prefetch.py --history --ticker-intelligence
```

## Daily SEC filing operations

`daily_pipeline.py` is the authoritative recurring SEC operation. It checks
the configured roster and every manager's historical CIK chain for both
`13F-HR` and `13F-HR/A`, records accessions in a SQLite ledger, refreshes only
affected manager caches, invalidates dependent historical periods, refreshes
market context, and runs the normal AWFI freshness check.

```powershell
python daily_pipeline.py
```

The command prints one JSON result and uses scheduler-friendly exit codes:

| Exit code | Meaning |
|---|---|
| `0` | Complete or no new filings |
| `20` | Partial completion; inspect the filing operations page |
| `30` | Another daily filing operation owns the cross-process lock |
| `1` | Failed operation |

The first run creates `data/operations/filing_ingestion.sqlite3`, records the
recent 120-day filing inventory as `BASELINED`, and rebuilds the corresponding
manager caches so future checks have accession-aware source metadata. Later
runs are idempotent by SEC accession number. Use a wider recovery window after
a long outage:

```powershell
python daily_pipeline.py --lookback-days 365 --trigger repair
```

Populate the ledger with the complete accession inventory available in the
existing local Investor Screening archive:

```powershell
python daily_pipeline.py --backfill-history
```

This metadata-only operation reads the local DuckDB archive, follows current
and historical manager CIK chains, inserts missing `13F-HR` and `13F-HR/A`
accessions as `HISTORICAL`, and does not fetch SEC data, rebuild holdings, or
change manager caches. It is idempotent and remains separate from the Daily
runs table. To intentionally limit the import to the newest periods, pass
`--history-quarters <count>`.

The operation atomically publishes `cache/filing_publication.json`. A running
web process checks this manifest every 15 seconds, reloads externally
published cache files, clears invalidated historical periods, and emits local
SSE notifications. The audit ledger and publication manifest are generated
state and are excluded from Git.

### Windows Task Scheduler

Run the task once daily after the SEC filing day has ended. Task Scheduler
interprets `-At` in the host's local time zone; the example uses 11:00 p.m.
local time. From an elevated PowerShell prompt at the repository root:

```powershell
$repo = (Get-Location).Path
$python = (Get-Command python).Source
$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "`"$repo\daily_pipeline.py`"" `
    -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At 11:00pm
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew
Register-ScheduledTask `
    -TaskName "AlphaWhales Daily SEC Filings" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Check SEC 13F filings and publish AlphaWhales caches"
```

Task Scheduler inherits the account's user environment. Configure a real
`EDGAR_IDENTITY` user environment variable before registering the task, and
run the task under the same account that owns the repository and Python
environment.

### Cron

Example for a host configured in Eastern time:

```cron
0 23 * * * cd /path/to/AlphaWhales && /path/to/python daily_pipeline.py >> /path/to/logs/alphawhales-filings.log 2>&1
```

Use one scheduler on one host. The lock is local-filesystem and
cross-process-safe, but it is not a distributed lock for shared network
storage.

### Monitoring and recovery

Open `/filings` to review the latest 20 runs and the accession-level record.

- `PUBLISHED`: the filing became the active normalized manager cache source.
- `RECORDED`: the filing was retained, but a more complete or later filing
  remained the active source.
- `BASELINED`: initial inventory captured at deployment.
- `FAILED`: the affected manager cache did not publish successfully.

A run can be `PARTIAL` when one source CIK was unavailable while other
managers completed. Rerun the same command; known accessions are skipped and
failed or missing work is reconsidered. Do not delete the lock file to break a
live run. The operating-system lock, not the diagnostic text in the file, is
authoritative.

## Loading behavior

### QoQ period selector

- Latest period: process cache.
- Previously selected period: memory cache.
- Persisted period: disk cache.
- Missing period: live SEC fetch in groups of five managers.

The loader reports cache source and completed fund count.

### Ticker detail

Ticker loading has three visible stages:

1. Current 13F holdings.
2. OpenBB market, valuation, timing, and 20-quarter history.
3. Pair analysis.

Market and pair results use independent six-hour caches.

## Roster management

`roster.json` is the tracked persistent roster. Do not edit `config.py` to add
or remove managers.

On `/screening`:

1. Select one or more managers.
2. Choose **Add**, **Add + flag**, or **Remove**.
3. Newly added managers refresh in the background.

The API validates additions against the compact screening snapshot and writes
the complete roster atomically. Changes update the running process immediately.
Historical period caches contain a roster fingerprint and are regenerated when
their membership no longer matches.

After a major roster revamp:

```powershell
python -m investor_screening.cli refresh-screening
python prefetch.py --history
```

Transient SEC timeouts receive one sequential retry by default. Use
`--retry-attempts 0` to disable retries or up to `--retry-attempts 3` during a
degraded network window. Periods where a manager had no filing are not retried.

Do not rerun `refresh-performance` solely because roster membership changed.
Performance facts are reusable per manager and window.

The pre-revamp 26-manager data snapshot is preserved under
`cache/roster_backups/2026-08-31-prior-roster/`. Its `manifest.json` records
the source commit, file sizes, and SHA-256 hashes for the old roster
configuration, 27 latest cache files, and 19 historical period snapshots.

## Cache maintenance

Generated cache data is excluded from Git.

| Cache | Maintenance |
|---|---|
| Latest manager snapshot | Run `python prefetch.py` |
| Historical periods | Run `python prefetch.py --history-only` |
| Popular ticker intelligence | Run `python prefetch.py --ticker-intelligence-only` |
| Daily filing ledger and targeted cache publication | Run `python daily_pipeline.py` |
| One ticker market cache | Delete `cache/ticker_market/<ticker>.json` |
| One ticker pair cache | Delete `cache/pair_signals/<ticker>.json` |

Do not edit generated JSON values manually. Change transformation logic and
regenerate the affected cache.

Ticker market cache version 5 includes the serialized daily close history used
by the sentiment/price overlay. Older ticker cache versions are rejected and
rebuilt automatically on the next ticker-intelligence request or prefetch.

## Investor screening data

Investor Screening uses generated DuckDB, Parquet, raw SEC archives, and
screening snapshots under `data/investor_screening/`. These files are excluded
from Git.

Common operations:

```powershell
# Initialize the screening database
python -m investor_screening.cli init

# Import official Form 13F history
python -m investor_screening.cli backfill

# Import official flattened non-13F histories
python -m investor_screening.cli backfill-bulk insider
python -m investor_screening.cli backfill-bulk nport
python -m investor_screening.cli backfill-bulk nmfp

# Catalog and ingest accession-level filing families
python -m investor_screening.cli catalog --start-year 2024 --family beneficial_ownership
python -m investor_screening.cli ingest-details --family beneficial_ownership --workers 8
python -m investor_screening.cli ingest-details --family planned_insider_sales --workers 8
python -m investor_screening.cli ingest-details --family fund_census --workers 8
python -m investor_screening.cli ingest-details --family fund_shareholder_reports --workers 8
python -m investor_screening.cli ingest-details --family proxy_voting --workers 8

# Retry only failed accession artifacts
python -m investor_screening.cli ingest-details --family beneficial_ownership --retry-failed --workers 8

# Create DuckDB views over all imported Parquet source tables
python -m investor_screening.cli refresh-bulk-views

# Build yearly lossless N-PX vote Parquet files from archived submissions
python -m investor_screening.cli refresh-npx-votes

# Backfill Parquet SHA-256 and byte-count metadata
python -m investor_screening.cli refresh-integrity-metadata

# Validate imported datasets and source-level consistency checks
python -m investor_screening.cli validate
python -m investor_screening.cli status

# Rebuild the compact snapshot used by /screening
python -m investor_screening.cli refresh-screening

# Run the full filesystem/hash/row-count/coverage integrity audit
python -m investor_screening.cli audit-integrity
```

Set a real SEC contact identity before network operations:

```powershell
$env:EDGAR_IDENTITY = "Your Name your.email@example.com"
$env:EDGAR_RATE_LIMIT_PER_SEC = "8"
```

`EDGAR_IDENTITY` should be stored as a user environment variable, not committed
to the repository.

### Resumption and failure handling

- Bulk imports are idempotent and skip intact imported archives.
- Archive and Parquet row counts plus deterministic digests must reconcile
  before the manifest is marked imported.
- Accession ingestion commits each filing independently.
- `FAILED` artifacts remain queryable in the catalog and can be retried.
- `RAW_ONLY` means the full SEC submission is preserved but no reliable typed
  or primary-XML model was available.
- `INGESTED_XML_FALLBACK` means the full primary XML was preserved when a typed
  EdgarTools representation was inappropriate or incomplete.
- N-PX uses a lossless XML path because malformed as-filed vote values can
  cause typed parsers to skip individual vote records. Filing metadata remains
  in DuckDB; every `proxyTable` node, including malformed numeric source
  strings, is written to yearly ZSTD Parquet.

### Integrity audit

The full audit writes
`data/investor_screening/integrity-report.json` and checks:

- All dataset manifests are imported.
- Every cataloged accession has an artifact.
- No unresolved failed or orphan artifact rows exist.
- Every Parquet file exists and matches its manifest row count.
- Retained source ZIP SHA-256 values match their manifests.
- Decompressed raw-submission hashes and byte counts match.
- The screening generation pointer resolves, the immutable generation is
  readable, and its source-manifest fingerprint matches the current 13F data.

Use `--quick` only for a manifest/coverage audit without filesystem hashing:

```powershell
python -m investor_screening.cli audit-integrity --quick
```

See `investor_screening/README.md` and
`investor_screening/SCREENING_MODEL.md` for the complete ingestion and
screening methodology.

## Smoke checks

```powershell
python -m compileall -q config.py roster_store.py data_service.py main.py pair_service.py prefetch.py run.py
python -m compileall -q filing_operations.py daily_pipeline.py
python -c "import main; print(type(main.app).__name__, len(main.data_service.cache))"
python -m compileall -q investor_screening
python -m compileall -q predictive_sentiment awfi_service.py
node --check static\js\app.js
```

Focused automated tests are available:

```powershell
python -m pip install pytest
python -m pytest tests/test_market_insights.py -q
python -m pytest tests/test_investor_history.py -q
python -m pytest tests/test_sentiment_conviction.py -q
python -m pytest tests/test_investor_screening.py -q
python -m pytest tests/test_filing_operations.py -q
python -m pytest tests/test_awfi.py tests/test_awfi_service.py tests/test_awfi_period_view.py tests/test_predictive_sentiment.py tests/test_predictive_sentiment_cli.py -q
```

There is no configured linter, formatter, or frontend build step. Use the
workspace Playwright MCP server for browser tests.

## Troubleshooting

### SEC period is stale

Confirm the manager's reporting CIK. Add former identifiers to
`historical_ciks` and refresh the affected snapshot.

### Historical period takes time

Run `python prefetch.py --history-only`.

### Ticker view takes time

The first visit downloads OpenBB fundamentals and pair candidate prices. The
three-stage loader remains visible. Warm popular tickers with the ticker
intelligence prefetch command.

### Action and value direction disagree

Action is based on share-count change. Reported value can move in the opposite
direction because the security price changed.

### Pair signal says no valid pair

Correlation alone is insufficient. A pair can fail corrected cointegration,
out-of-sample persistence, stability, hedge-ratio, or half-life gates.
## AWFI Snapshot Publication

AWFI research history is built in an isolated staging database and published
through an atomic file replacement. Readers continue using the previous complete
snapshot while a new run is building.

Application startup and full SEC refreshes compare the published AWFI run
against:

- the current predictive protocol and configuration;
- the roster file hash;
- the latest per-manager top-10 universe, preferring a newer application cache
  over the lagging official quarterly SEC archive;
- the official 13F source signature; and
- the published AWFI model version.

Any mismatch schedules one serialized rebuild. An OS-backed interprocess lock
prevents concurrent publishers, and ticker pages refresh through the
`awfi_published` SSE event with polling as a fallback.

Run a manual atomic refresh with:

```powershell
python -m predictive_sentiment.cli run
```

The completed run summary records current-universe mapping and scoring
coverage. Investigate any material decline before treating missing ticker
history as a legitimate eligibility gap.

Detailed source lineage, publication gates, expected coverage gaps, and the
freshness contract are documented in
[`AWFI_DATA_LINEAGE.md`](AWFI_DATA_LINEAGE.md).

Check freshness without starting a build:

```powershell
python -c "from predictive_sentiment.publication import research_snapshot_needs_refresh; print(research_snapshot_needs_refresh())"
```

The application performs the same check:

- once after startup cache initialization;
- after every scheduled full SEC refresh;
- after `/api/refresh`; and
- after roster mutations.

`/api/ticker/{ticker}/awfi-history` exposes `refresh_state` and
`snapshot_version`. Normal steady state is `current`.

### AWFI source-period lag

The application cache may advance before the official SEC quarterly archive.
This is expected. The latest cache can define the frozen current universe while
persisted historical signal inputs still end at the latest official archive
period. The current application period is appended as a live score until the
official source catches up.

Do not copy scores or decomposed features from an older protocol run to fill
this gap. AWFI cross-sectional components depend on the run universe and
protocol.
