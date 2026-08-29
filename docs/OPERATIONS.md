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
python -m compileall -q config.py data_service.py main.py pair_service.py prefetch.py run.py
python -c "import main; print(type(main.app).__name__, len(main.data_service.cache))"
python -m compileall -q investor_screening
node --check static\js\app.js
```

Focused automated tests are available:

```powershell
python -m pip install pytest
python -m pytest tests/test_market_insights.py -q
python -m pytest tests/test_investor_history.py -q
python -m pytest tests/test_sentiment_conviction.py -q
python -m pytest tests/test_investor_screening.py -q
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
