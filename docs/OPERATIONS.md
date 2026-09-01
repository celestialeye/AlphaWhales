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

Latest refresh plus both optional warm-ups:

```powershell
python prefetch.py --history --ticker-intelligence
```

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
