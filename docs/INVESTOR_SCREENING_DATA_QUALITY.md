# Investor Screening Data Quality Report

**Audit date:** August 29, 2026  
**Audit result:** Complete with zero integrity errors and one source-availability
warning

## Scope

The audit covers the standalone SEC ownership-research foundation under
`data/investor_screening/`:

- Official Form 13F flattened archives.
- Official Forms 3/4/5 insider transaction archives.
- Official N-PORT flattened archives.
- Official N-MFP flattened archives.
- Schedule 13D/G accession submissions.
- Form 144 accession submissions.
- N-CEN accession submissions.
- N-CSR/N-CSRS accession submissions.
- N-PX filings and proxy-vote records.
- The immutable Investor Screening runtime snapshot.

Generated databases, Parquet files, source archives, raw submissions, and the
machine-readable audit report are excluded from Git.

## Coverage

### Form 13F

| Metric | Result |
|---|---:|
| Official archives | 52 |
| Submissions | 397,667 |
| Distinct filer CIKs | 16,327 |
| Information-table rows | 120,094,479 |
| Latest report period | 2026-03-31 |

The production-complete analytical range begins with the June 30, 2013 report
period. Earlier report periods present in later structured submissions remain
stored but are not treated as a complete historical universe.

### Official flattened non-13F archives

| Family | Imported archives |
|---|---:|
| Forms 3/4/5 insider transactions | 82 |
| N-PORT | 27 |
| N-MFP | 97 |

Every TSV source column is retained as a string in ZSTD Parquet. Additive SEC
schema changes are queried with DuckDB `union_by_name`.

### Accession-level detail

| Family | Cataloged | Processed | Typed/XML | Raw-only | Source unavailable |
|---|---:|---:|---:|---:|---:|
| Schedule 13D/G | 68,227 | 68,227 | 56,798 | 11,426 | 3 |
| Form 144 | 123,200 | 123,200 | 123,200 | 0 | 0 |
| N-CEN | 28,638 | 28,638 | 28,638 | 0 | 0 |
| N-CSR/N-CSRS | 14,793 | 14,793 | 12,363 | 2,430 | 0 |
| N-PX | 31,106 | 31,106 | 31,106 | 0 | 0 |

`RAW_ONLY` means the complete SEC submission is retained but no reliable typed
or primary-XML representation was available. It is not treated as a missing
filing.

The three `SOURCE_UNAVAILABLE` Schedule 13D/G accessions were present in the
SEC quarterly index but returned HTTP 404 after repeated retrieval attempts:

- `0002147005-26-000004`
- `0002147005-26-000005`
- `0002147005-26-000006`

They remain cataloged with explicit source-unavailable status.

### N-PX proxy votes

| Filing year | Filings | Vote records |
|---|---:|---:|
| 2024 | 10,476 | 24,163,879 |
| 2025 | 11,303 | 23,464,210 |
| 2026 | 9,327 | 16,913,554 |
| **Total** | **31,106** | **64,541,643** |

N-PX filing metadata is stored in DuckDB. Vote records are stored in yearly
ZSTD Parquet files to avoid millions of JSON-row inserts. Source numeric values
such as `N/A` and blank shares-voted fields remain strings rather than being
dropped or silently converted.

## Integrity evidence

The full audit completed with:

| Check | Result |
|---|---:|
| Integrity errors | 0 |
| Integrity warnings | 1 |
| Orphan filing artifacts | 0 |
| Orphan extracted rows | 0 |
| Parquet files checked | 3,016 |
| Missing Parquet files | 0 |
| Parquet row-count mismatches | 0 |
| Parquet SHA-256 failures | 0 |
| Metadata files verified | 412 |
| Metadata hash failures | 0 |
| Source archives verified | 258 |
| Source archive hash failures | 0 |
| Raw accession submissions verified | 265,965 |
| N-PX vote years verified | 3 |

The raw-submission scan found no UTF-8 replacement markers or unreadable gzip
files. Current ingestion stores and hashes the exact bytes returned by the SEC;
decoded text is used only as a separate parser representation.

## Source-quality warnings

Form 13F validation retains 64,843 as-filed warnings:

- 44,415 summary-value discrepancies.
- 14,226 summary entry-count discrepancies.
- 6,131 confidential-holdings omissions.
- 49 holdings reports without information-table rows.
- 18 holdings reports without summary pages.
- 4 holding rows missing a normally required field.

These warnings are not ingestion errors. The source archive rows were retained
and reconciled exactly. They remain visible because the SEC explicitly warns
that flattened datasets are as-filed and can contain filer or extraction
inconsistencies.

## Screening snapshot

The runtime screen uses an immutable generation referenced by
`screening_snapshot.json`.

| Metric | Result |
|---|---:|
| Managers with 12-quarter metrics | 5,656 |
| Reusable position-quarter facts | 829,856 |
| Default $10B candidates | 71 |
| Current-roster matches | 9 |

The snapshot includes a source-manifest fingerprint. The integrity audit fails
if imported 13F datasets change without a new screening generation. It also
checks the position cube for duplicate keys, missing required values, rows
below the 1% storage floor that are not retained top-10 positions, invalid
quarter indexes, non-positive values, and inconsistent
overall-versus-direct-sleeve weights.

## Hypothetical performance coverage

The published snapshot contains performance facts for the existing 70-manager
production run. The dynamic 3%/12-month best-bet default currently returns 71
managers, 45 of whom have available 3Y estimates. Criteria changes reuse these
facts and never trigger performance recalculation.

| Metric | Result |
|---|---:|
| Cached adjusted-price symbols | 4,043 |
| Confirmed no-data/delisted symbols | 2,149 |
| Managers with available 3Y estimates | 45 of 71 |
| Managers beating SPY over 3Y | 15 of 45 |
| Managers beating QQQ over 3Y | 8 of 45 |
| Managers with available 5Y estimates | 22 of 71 |
| Managers beating SPY over 5Y | 6 of 22 |
| Managers beating QQQ over 5Y | 3 of 22 |

The difference between 3Y and 5Y coverage is primarily continuous-history
availability, not a relaxed price-coverage threshold. Three years is the UI
default; five years remains the higher-confidence comparison.

These values are hypothetical disclosure-lagged reported 13F long-sleeve
estimates. They are not actual fund or account returns.

## Known analytical limitations

- Reported 13F value is not total firm or fund AUM.
- Form 13F omits cash, shorts, private securities, and substantial non-equity
  exposure.
- Holding duration is observed at quarter ends and does not prove continuous
  ownership.
- Estimated turnover remains a diagnostic and still lacks complete
  universe-wide price-drift adjustment; it is not a default eligibility
  filter.
- ETF and pooled-product classification currently uses issuer/title heuristics
  pending a maintained point-in-time security-type reference.
- `RAW_ONLY` legacy filings require future document-specific extraction if
  their narrative fields become necessary for a screen.

## Reproduce the checks

```powershell
python -m investor_screening.cli validate
python -m investor_screening.cli refresh-integrity-metadata
python -m investor_screening.cli refresh-npx-votes
python -m investor_screening.cli refresh-screening
python -m investor_screening.cli audit-integrity
```

The machine-readable result is written to:

```text
data/investor_screening/integrity-report.json
```
