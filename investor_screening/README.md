# Investor Research Data Foundation

This is a standalone data subsystem inside AlphaWhales. It provides an
auditable SEC ownership-research foundation plus the compact snapshot consumed
by the Investor Screening page. It does not import from, query, or share
runtime storage with the sibling `invest` repository.

The agreed screening methodology and default UI values are documented in
[SCREENING_MODEL.md](SCREENING_MODEL.md).

## Historical range

The default backfill starts with the SEC's 2013 Q3 archive. That is the first
full filing quarter after XML information tables became mandatory on
May 20, 2013, and it includes reports for the quarter ended June 30, 2013.
This provides roughly 13 years of standardized history without relying on the
less-complete legacy text parser. EdgarTools documents approximately 93%
coverage for its 2005-2013 text parser, which is not strong enough for a
universe-wide completeness claim.

## Storage choice

DuckDB is the system of record because the workload is local, append-heavy,
columnar, and analytical. It can scan tens of millions of holding rows
efficiently without operating a database server. The normalized tables retain
every field from the SEC's flattened data files and preserve both the as-filed
value and a dollar-normalized value.

The much larger Insider, N-PORT, and N-MFP flattened data sets use a bronze
lake instead of duplicating their rows into DuckDB. Every source TSV column is
stored as `VARCHAR` in ZSTD Parquet under
`data\investor_screening\lake\<family>\<table>\<dataset_id>.parquet`, together
with source-dataset, archive-member, and row-number metadata. DuckDB stores
only archive/file manifests, hashes, schemas, row counts, and paths. Non-TSV
readmes and metadata are preserved under each family's `_metadata` directory.
`v_bulk_parquet_inventory` lists queryable files, and `bulk_table_sql()` builds
a `union_by_name` scan that tolerates additive SEC schema generations.
Source ZIP archives are retained by default as immutable provenance artifacts.
Use `--delete-archives-after-import` only when archive removal is intentional;
deletion occurs after Parquet and manifest reconciliation succeeds.

Values filed before January 3, 2023 were reported in thousands of dollars.
Later filings use dollars. The importer stores `value_reported`, `value_unit`,
and `value_usd` so analysis remains consistent without losing source meaning.

## Filing coverage

The generic `sec_filings` catalog covers the filing families relevant to
investor and ownership research:

| Family | Forms | Foundation role |
|---|---|---|
| Institutional holdings | 13F-HR, 13F-NT, amendments | Full holdings-detail ingestion first |
| Registered fund portfolios | NPORT-P, NPORT-EX, legacy N-Q, amendments | Mutual fund and ETF position history |
| Fund reports and census | N-CSR/N-CSRS, N-CEN, amendments | Fund identity, policies, and shareholder reporting |
| Money market funds | N-MFP variants, late notices, N-CR | Liquidity, portfolio composition, and stress events |
| Insider ownership | Forms 3, 4, 5, amendments | Insider positions and transactions |
| Beneficial ownership | Schedules 13D/G, amendments | 5% ownership and activist/passive stakes |
| Proxy voting | N-PX, amendments | Manager stewardship and governance behavior |
| Planned insider sales | Form 144, amendments | Intended restricted/control-security sales |

Issuer fundamentals and corporate-event filings such as 10-K, 10-Q, 8-K,
DEF 14A, and earnings data are intentionally outside this ownership-focused
foundation. EdgarTools and OpenBB already provide those capabilities, and
duplicating them here would blur the project boundary. Form ADV is also outside
scope because it is filed through IAPD rather than EDGAR; Form 13H data is not
publicly available at the detail needed for this project.

### Preferred source strategy

| Filing family | Historical backbone | Incremental/repair path |
|---|---|---|
| 13F | Official SEC flattened ZIP archives | EdgarTools |
| Forms 3/4/5 | Official SEC Insider Transactions data sets | EdgarTools |
| NPORT-P | Official SEC Form N-PORT data sets | EdgarTools |
| N-MFP | Official SEC Form N-MFP data sets | EdgarTools |
| N-CEN | EDGAR accession-level XML | EdgarTools |
| 13D/G | EDGAR filing indexes and typed EdgarTools objects | EdgarTools |
| N-CSR/N-CSRS | EDGAR filing indexes and typed EdgarTools objects | EdgarTools |
| N-PX | EDGAR filing indexes and typed EdgarTools objects | EdgarTools |
| Form 144 | EDGAR filing indexes and typed EdgarTools objects | EdgarTools |

This prevents millions of avoidable individual requests while retaining
accession-level raw submissions for forms without a suitable flattened bulk
history.

The structured production boundaries differ by family: N-MFP bulk history
begins in 2010 with multiple schema generations; N-PORT public bulk history
begins in 2019; N-CEN begins in 2018; electronic Form 144 is defensible from
April 13, 2023; modern structured N-PX and N-CSR data are primarily useful from
2024. Older N-Q, N-PX, and shareholder reports remain a separately labeled
legacy extraction tier rather than being presented as equivalent-quality data.

Official SEC quarterly Form 13F ZIP archives are the historical backbone.
EdgarTools catalogs all form families and provides accession-level detail
ingestion.

Every detailed filing stores:

1. The complete SEC submission compressed under `data\investor_screening\raw`.
2. A SHA-256 hash and byte count for provenance checks.
3. EdgarTools' parsed data-object fields as JSON, excluding its live filing
   handle and internal dataframe caches.
4. Every supported DataFrame or structured list as individually hashed JSON rows.
5. Parser version, extraction manifest, timestamp, and explicit failure state.

The normalized 13F tables are the first silver analytical model. The generic
artifact and row tables sit beside the lossless compressed SEC submission,
allowing form-specific silver models to be added without re-downloading EDGAR.
If the installed EdgarTools version does not expose a typed object for an older
structured form, the ingestor preserves the complete submission and converts
its primary XML to JSON with status `INGESTED_XML_FALLBACK`; it does not silently
drop the filing. Legacy filings without typed parsing or primary XML are retained
as compressed complete submissions with status `RAW_ONLY`, making the lower
extraction quality explicit rather than treating the filing as absent.

OpenBB is intentionally not the SEC system of record. It is already valuable
for downstream ticker resolution, prices, fundamentals, and market context,
but EdgarTools plus official SEC bulk files provide stronger accession-level
provenance and broader filing-type coverage. Later enrichment jobs can join
OpenBB data without coupling the raw archive to a market-data provider.

The importer rejects unexpected SEC columns instead of silently discarding
schema changes. Imports are transactional and idempotent.

## Commands

Run commands from the repository root:

```powershell
# Create the database
python -m investor_screening.cli init

# Review the official archives that will be used
python -m investor_screening.cli list-datasets

# Download and import the full XML-era history
python -m investor_screening.cli backfill

# Review and import one official flattened bronze family
python -m investor_screening.cli list-bulk-datasets insider --start-date 2024-01-01
python -m investor_screening.cli backfill-bulk nport --start-date 2019-01-01
python -m investor_screening.cli backfill-bulk nmfp --limit 1
python -m investor_screening.cli backfill-bulk nmfp --limit 1 --delete-archives-after-import

# Import an archive already present on disk
python -m investor_screening.cli import-archive C:\path\to\2013q3_form13f.zip
python -m investor_screening.cli import-bulk-archive insider C:\path\to\2024q1_form345.zip

# Validate row counts, totals, required fields, and confidential omissions
python -m investor_screening.cli validate
python -m investor_screening.cli status

# Catalog all relevant filing families from EDGAR's quarterly indexes
python -m investor_screening.cli catalog --start-year 2025 --family all

# Download raw submissions and all typed detail for cataloged filings
python -m investor_screening.cli ingest-details --family insider_ownership --limit 100
python -m investor_screening.cli ingest-details --family beneficial_ownership --limit 100

# Make every imported flattened SEC table directly queryable in DuckDB
python -m investor_screening.cli refresh-bulk-views

# Rebuild the small read-only snapshot used by the Investor Screening UI
python -m investor_screening.cli refresh-screening

# Compare one imported accession against EdgarTools' parsed filing
python -m investor_screening.cli verify-accession 0001067983-25-000008

# Add ticker mappings for one filing using EdgarTools' CUSIP resolution
python -m investor_screening.cli hydrate-tickers 0001067983-25-000008
```

Set `EDGAR_IDENTITY` to a real contact identity before downloading:

```powershell
$env:EDGAR_IDENTITY = "Your Name your.email@example.com"
```

Generated archives and the DuckDB file live under
`data\investor_screening\` and are excluded from Git.

The screening page is available at `http://127.0.0.1:8000/screening`. Its
default minimum reported 13F value is $10 billion, while the UI can lower or
raise the threshold without rebuilding the snapshot.

## Amendment handling

All original and amended submissions remain stored. Analytical views select
the latest original or restatement for each manager and report period, then
include later amendments marked `NEW HOLDINGS`. This avoids treating an
additive confidential-treatment release as a replacement portfolio.

## Completeness

`status.no_known_ingestion_errors` reports whether imported datasets and parsed
artifacts have any known pipeline failures. It is not a claim that every
planned filing family and year has been backfilled. `filings_without_details`
tracks cataloged non-13F filings that still need raw/object ingestion.
SEC-reported confidential omissions and malformed as-filed records remain
visible as warnings because public data cannot reconstruct undisclosed rows.
The SEC also cautions that its flattened data is as filed and is not a
substitute for reviewing the original filing.

Production research decisions should rely on a screening snapshot only after
the required 13F range is imported, quality errors are resolved, amendment
behavior is sampled against original filings, and the snapshot's coverage
status is reviewed. The interface may still be used during incremental
backfills, but incomplete evidence must remain visible rather than being
treated as a passing or failing value.

## Primary references

- SEC Form 13F data sets:
  https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets
- SEC Insider Transactions data sets:
  https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets
- SEC Form N-PORT data sets:
  https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets
- SEC Form N-MFP data sets:
  https://www.sec.gov/data-research/sec-markets-data/dera-form-n-mfp-data-sets
- EdgarTools repository:
  https://github.com/dgunning/edgartools
- EdgarTools 13F guide:
  https://edgartools.readthedocs.io/en/latest/guides/thirteenf-data-object-guide/
- Insider ownership guide:
  https://edgartools.readthedocs.io/en/latest/insider-filings/
- Schedule 13D/G guide:
  https://edgartools.readthedocs.io/en/latest/guides/schedule13dg-data-object-guide/
- N-PORT guide:
  https://edgartools.readthedocs.io/en/latest/guides/nport-data-object-guide/
- N-CEN guide:
  https://edgartools.readthedocs.io/en/latest/guides/fundcensus-data-object-guide/
- N-CSR guide:
  https://edgartools.readthedocs.io/en/latest/guides/fundshareholderreport-data-object-guide/
- N-PX guide:
  https://edgartools.readthedocs.io/en/latest/guides/npx-data-object-guide/
- Form 144 guide:
  https://edgartools.readthedocs.io/en/latest/guides/form144-data-object-guide/
