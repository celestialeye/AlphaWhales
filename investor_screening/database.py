from __future__ import annotations

from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "investor_screening"
DEFAULT_DATABASE_PATH = DEFAULT_DATA_DIR / "investor_screening.duckdb"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sec_filings (
    accession_number VARCHAR PRIMARY KEY,
    filing_family VARCHAR NOT NULL,
    form VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    company_name VARCHAR,
    filing_date DATE NOT NULL,
    period_of_report DATE,
    source_url VARCHAR NOT NULL,
    source_kind VARCHAR NOT NULL,
    dataset_id VARCHAR,
    discovered_at TIMESTAMP NOT NULL DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS filing_artifacts (
    accession_number VARCHAR PRIMARY KEY,
    status VARCHAR NOT NULL,
    raw_submission_path VARCHAR,
    raw_submission_sha256 VARCHAR,
    raw_submission_bytes BIGINT,
    object_type VARCHAR,
    object_json JSON,
    extractor_manifest JSON,
    edgartools_version VARCHAR,
    ingested_at TIMESTAMP,
    last_error VARCHAR
);

CREATE TABLE IF NOT EXISTS filing_table_rows (
    accession_number VARCHAR NOT NULL,
    table_name VARCHAR NOT NULL,
    row_index BIGINT NOT NULL,
    row_hash VARCHAR NOT NULL,
    row_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_runs (
    validated_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
    error_count BIGINT NOT NULL,
    warning_count BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS datasets (
    dataset_id VARCHAR PRIMARY KEY,
    source_url VARCHAR,
    local_path VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL,
    period_start DATE,
    period_end DATE,
    status VARCHAR NOT NULL,
    submission_count BIGINT DEFAULT 0,
    holdings_count BIGINT DEFAULT 0,
    imported_at TIMESTAMP,
    last_error VARCHAR
);

CREATE TABLE IF NOT EXISTS bulk_datasets (
    family VARCHAR NOT NULL,
    dataset_id VARCHAR NOT NULL,
    archive_filename VARCHAR NOT NULL,
    source_url VARCHAR,
    local_archive_path VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL,
    period_start DATE,
    period_end DATE,
    status VARCHAR NOT NULL,
    source_table_count BIGINT DEFAULT 0,
    source_row_count BIGINT DEFAULT 0,
    parquet_row_count BIGINT DEFAULT 0,
    archive_deleted BOOLEAN NOT NULL DEFAULT false,
    imported_at TIMESTAMP,
    last_error VARCHAR,
    PRIMARY KEY (family, dataset_id)
);

CREATE TABLE IF NOT EXISTS bulk_dataset_files (
    family VARCHAR NOT NULL,
    dataset_id VARCHAR NOT NULL,
    table_name VARCHAR NOT NULL,
    source_member VARCHAR NOT NULL,
    source_row_count BIGINT NOT NULL,
    parquet_row_count BIGINT NOT NULL,
    schema_json JSON NOT NULL,
    output_path VARCHAR NOT NULL,
    parquet_sha256 VARCHAR,
    parquet_bytes BIGINT,
    status VARCHAR NOT NULL,
    imported_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (family, dataset_id, table_name)
);

CREATE TABLE IF NOT EXISTS bulk_dataset_metadata (
    family VARCHAR NOT NULL,
    dataset_id VARCHAR NOT NULL,
    source_member VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL,
    output_path VARCHAR NOT NULL,
    byte_count BIGINT NOT NULL,
    PRIMARY KEY (family, dataset_id, source_member)
);

CREATE TABLE IF NOT EXISTS npx_vote_files (
    report_year INTEGER PRIMARY KEY,
    output_path VARCHAR NOT NULL,
    source_filing_count BIGINT NOT NULL,
    vote_count BIGINT NOT NULL,
    parquet_sha256 VARCHAR NOT NULL,
    parquet_bytes BIGINT NOT NULL,
    status VARCHAR NOT NULL,
    built_at TIMESTAMP,
    last_error VARCHAR
);

CREATE TABLE IF NOT EXISTS submissions (
    accession_number VARCHAR PRIMARY KEY,
    dataset_id VARCHAR NOT NULL,
    filing_date DATE NOT NULL,
    submission_type VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    period_of_report DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS cover_pages (
    accession_number VARCHAR PRIMARY KEY,
    report_calendar_or_quarter DATE,
    is_amendment BOOLEAN,
    amendment_number INTEGER,
    amendment_type VARCHAR,
    confidential_denied_expired BOOLEAN,
    date_denied_expired DATE,
    date_reported DATE,
    reason_for_non_confidentiality VARCHAR,
    filing_manager_name VARCHAR,
    filing_manager_street1 VARCHAR,
    filing_manager_street2 VARCHAR,
    filing_manager_city VARCHAR,
    filing_manager_state_or_country VARCHAR,
    filing_manager_zipcode VARCHAR,
    report_type VARCHAR,
    form13f_file_number VARCHAR,
    crd_number VARCHAR,
    sec_file_number VARCHAR,
    provide_info_for_instruction5 VARCHAR,
    additional_information VARCHAR
);

CREATE TABLE IF NOT EXISTS summary_pages (
    accession_number VARCHAR PRIMARY KEY,
    other_included_managers_count INTEGER,
    table_entry_total BIGINT,
    table_value_reported DECIMAL(38, 0),
    table_value_unit VARCHAR,
    table_value_usd DECIMAL(38, 0),
    is_confidential_omitted BOOLEAN
);

CREATE TABLE IF NOT EXISTS signatures (
    accession_number VARCHAR PRIMARY KEY,
    signer_name VARCHAR,
    signer_title VARCHAR,
    signer_phone VARCHAR,
    signature VARCHAR,
    city VARCHAR,
    state_or_country VARCHAR,
    signature_date DATE
);

CREATE TABLE IF NOT EXISTS other_managers (
    accession_number VARCHAR NOT NULL,
    source_table VARCHAR NOT NULL,
    manager_key VARCHAR NOT NULL,
    sequence_number INTEGER,
    cik VARCHAR,
    form13f_file_number VARCHAR,
    crd_number VARCHAR,
    sec_file_number VARCHAR,
    manager_name VARCHAR,
    PRIMARY KEY (accession_number, source_table, manager_key)
);

CREATE TABLE IF NOT EXISTS holdings (
    accession_number VARCHAR NOT NULL,
    infotable_sk BIGINT NOT NULL,
    name_of_issuer VARCHAR,
    title_of_class VARCHAR,
    cusip VARCHAR,
    figi VARCHAR,
    value_reported DECIMAL(38, 0),
    value_unit VARCHAR NOT NULL,
    value_usd DECIMAL(38, 0),
    shares_or_principal DECIMAL(38, 6),
    shares_or_principal_type VARCHAR,
    put_call VARCHAR,
    investment_discretion VARCHAR,
    other_manager VARCHAR,
    voting_authority_sole DECIMAL(38, 6),
    voting_authority_shared DECIMAL(38, 6),
    voting_authority_none DECIMAL(38, 6),
    ticker VARCHAR,
    PRIMARY KEY (accession_number, infotable_sk)
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    issue_id VARCHAR PRIMARY KEY,
    dataset_id VARCHAR,
    accession_number VARCHAR,
    severity VARCHAR NOT NULL,
    issue_code VARCHAR NOT NULL,
    issue_message VARCHAR NOT NULL,
    detected_at TIMESTAMP NOT NULL DEFAULT current_timestamp
);

CREATE INDEX IF NOT EXISTS submissions_manager_period_idx
    ON submissions(cik, period_of_report);
CREATE INDEX IF NOT EXISTS sec_filings_family_date_idx
    ON sec_filings(filing_family, filing_date);
CREATE INDEX IF NOT EXISTS sec_filings_cik_form_idx
    ON sec_filings(cik, form);
CREATE INDEX IF NOT EXISTS filing_table_rows_table_idx
    ON filing_table_rows(table_name);
CREATE INDEX IF NOT EXISTS filing_table_rows_accession_idx
    ON filing_table_rows(accession_number);
CREATE INDEX IF NOT EXISTS holdings_accession_idx
    ON holdings(accession_number);
CREATE INDEX IF NOT EXISTS holdings_cusip_idx
    ON holdings(cusip);
CREATE INDEX IF NOT EXISTS bulk_dataset_files_table_idx
    ON bulk_dataset_files(family, table_name);

CREATE OR REPLACE VIEW v_bulk_parquet_inventory AS
SELECT
    f.family,
    f.table_name,
    f.dataset_id,
    d.period_start,
    d.period_end,
    d.source_url,
    d.source_sha256,
    f.source_member,
    f.source_row_count,
    f.parquet_row_count,
    f.schema_json,
    f.output_path
FROM bulk_dataset_files f
JOIN bulk_datasets d USING (family, dataset_id)
WHERE d.status = 'IMPORTED'
  AND f.status = 'IMPORTED';

CREATE OR REPLACE VIEW v_effective_accessions AS
WITH filing_versions AS (
    SELECT
        s.accession_number,
        s.cik,
        s.period_of_report,
        s.filing_date,
        s.submission_type,
        cp.filing_manager_name,
        upper(coalesce(cp.amendment_type, '')) AS amendment_type
    FROM submissions s
    LEFT JOIN cover_pages cp USING (accession_number)
    WHERE s.submission_type IN ('13F-HR', '13F-HR/A')
),
base_ranked AS (
    SELECT
        *,
        row_number() OVER (
            PARTITION BY cik, period_of_report
            ORDER BY filing_date DESC, accession_number DESC
        ) AS version_rank
    FROM filing_versions
    WHERE amendment_type NOT LIKE '%NEW HOLDINGS%'
),
bases AS (
    SELECT * FROM base_ranked WHERE version_rank = 1
)
SELECT
    cik,
    period_of_report,
    filing_manager_name,
    accession_number,
    'BASE' AS effective_role
FROM bases
UNION ALL
SELECT
    additions.cik,
    additions.period_of_report,
    coalesce(additions.filing_manager_name, bases.filing_manager_name),
    additions.accession_number,
    'ADDITION' AS effective_role
FROM filing_versions additions
JOIN bases
  ON bases.cik = additions.cik
 AND bases.period_of_report = additions.period_of_report
WHERE additions.amendment_type LIKE '%NEW HOLDINGS%'
  AND (
      additions.filing_date > bases.filing_date
      OR (
          additions.filing_date = bases.filing_date
          AND additions.accession_number > bases.accession_number
      )
  );

CREATE OR REPLACE VIEW v_effective_holdings AS
WITH aggregated AS (
    SELECT
        ea.cik,
        ea.filing_manager_name,
        ea.period_of_report,
        h.cusip,
        h.figi,
        h.ticker,
        h.name_of_issuer,
        h.title_of_class,
        h.shares_or_principal_type,
        nullif(trim(h.put_call), '') AS put_call,
        sum(h.value_usd) AS value_usd,
        sum(h.shares_or_principal) AS shares_or_principal,
        sum(h.voting_authority_sole) AS voting_authority_sole,
        sum(h.voting_authority_shared) AS voting_authority_shared,
        sum(h.voting_authority_none) AS voting_authority_none
    FROM v_effective_accessions ea
    JOIN holdings h USING (accession_number)
    GROUP BY ALL
),
weighted AS (
    SELECT
        *,
        sum(value_usd) OVER (
            PARTITION BY cik, period_of_report
        ) AS portfolio_value_usd
    FROM aggregated
)
SELECT
    *,
    CASE
        WHEN portfolio_value_usd > 0
        THEN (value_usd / portfolio_value_usd) * 100
        ELSE 0
    END AS portfolio_weight_pct
FROM weighted;

CREATE OR REPLACE VIEW v_beneficial_ownership_filings AS
SELECT
    sf.accession_number,
    sf.form,
    sf.filing_date,
    fa.status AS detail_status,
    json_extract_string(fa.object_json, '$.issuer_info.cik') AS issuer_cik,
    json_extract_string(fa.object_json, '$.issuer_info.name') AS issuer_name,
    coalesce(
        json_extract_string(fa.object_json, '$.security_info.cusip'),
        json_extract_string(fa.object_json, '$.issuer_info.cusip')
    ) AS cusip,
    json_extract_string(fa.object_json, '$.security_info.title') AS security_title,
    coalesce(
        try_strptime(
            json_extract_string(fa.object_json, '$.date_of_event'),
            '%m/%d/%Y'
        )::DATE,
        try_strptime(
            json_extract_string(fa.object_json, '$.event_date'),
            '%m/%d/%Y'
        )::DATE
    ) AS event_date,
    try_cast(
        json_extract_string(fa.object_json, '$.amendment_number')
        AS INTEGER
    ) AS amendment_number,
    json_extract_string(
        fa.object_json,
        '$.items.item4_purpose_of_transaction'
    ) AS purpose_of_transaction,
    sf.source_url
FROM sec_filings sf
JOIN filing_artifacts fa USING (accession_number)
WHERE sf.filing_family = 'beneficial_ownership';

CREATE OR REPLACE VIEW v_beneficial_owners AS
SELECT
    sf.accession_number,
    sf.form,
    sf.filing_date,
    json_extract_string(rows.row_json, '$.cik') AS reporting_person_cik,
    json_extract_string(rows.row_json, '$.name') AS reporting_person_name,
    json_extract_string(rows.row_json, '$.type_of_reporting_person')
        AS reporting_person_type,
    try_cast(
        json_extract_string(rows.row_json, '$.aggregate_amount')
        AS DECIMAL(38, 6)
    ) AS aggregate_shares,
    try_cast(
        json_extract_string(rows.row_json, '$.percent_of_class')
        AS DECIMAL(18, 6)
    ) AS percent_of_class,
    try_cast(
        json_extract_string(rows.row_json, '$.sole_voting_power')
        AS DECIMAL(38, 6)
    ) AS sole_voting_power,
    try_cast(
        json_extract_string(rows.row_json, '$.shared_voting_power')
        AS DECIMAL(38, 6)
    ) AS shared_voting_power,
    try_cast(
        json_extract_string(rows.row_json, '$.sole_dispositive_power')
        AS DECIMAL(38, 6)
    ) AS sole_dispositive_power,
    try_cast(
        json_extract_string(rows.row_json, '$.shared_dispositive_power')
        AS DECIMAL(38, 6)
    ) AS shared_dispositive_power,
    json_extract_string(rows.row_json, '$.comment') AS ownership_comment
FROM filing_table_rows rows
JOIN sec_filings sf USING (accession_number)
WHERE sf.filing_family = 'beneficial_ownership'
  AND rows.table_name = 'reporting_persons';

CREATE OR REPLACE VIEW v_form144_sales AS
SELECT
    sf.accession_number,
    sf.filing_date,
    json_extract_string(rows.row_json, '$.person_selling') AS person_selling,
    json_extract_string(rows.row_json, '$.issuer') AS issuer_name,
    json_extract_string(rows.row_json, '$.issuer_cik') AS issuer_cik,
    json_extract_string(rows.row_json, '$.security_class') AS security_class,
    try_cast(
        json_extract_string(rows.row_json, '$.units_to_be_sold')
        AS DECIMAL(38, 6)
    ) AS units_to_be_sold,
    try_cast(
        json_extract_string(rows.row_json, '$.market_value')
        AS DECIMAL(38, 6)
    ) AS market_value,
    coalesce(
        try_strptime(
            json_extract_string(rows.row_json, '$.approx_sale_date'),
            '%m/%d/%Y'
        )::DATE,
        try_cast(
            json_extract_string(rows.row_json, '$.approx_sale_date')
            AS DATE
        )
    ) AS approximate_sale_date,
    json_extract_string(rows.row_json, '$.exchange_name') AS exchange_name,
    json_extract_string(rows.row_json, '$.broker_name') AS broker_name,
    try_cast(
        json_extract_string(rows.row_json, '$.is_amendment')
        AS BOOLEAN
    ) AS is_amendment
FROM filing_table_rows rows
JOIN sec_filings sf USING (accession_number)
WHERE sf.filing_family = 'planned_insider_sales'
  AND rows.table_name = 'to_dataframe';

CREATE OR REPLACE VIEW v_fund_census_series AS
SELECT
    sf.accession_number,
    sf.filing_date,
    json_extract_string(rows.row_json, '$.series_id') AS series_id,
    json_extract_string(rows.row_json, '$.name') AS series_name,
    json_extract_string(rows.row_json, '$.lei') AS series_lei,
    json_extract_string(rows.row_json, '$.fund_type') AS fund_type,
    try_cast(
        json_extract_string(rows.row_json, '$.avg_net_assets')
        AS DECIMAL(38, 6)
    ) AS average_net_assets,
    try_cast(
        json_extract_string(rows.row_json, '$.aggregate_commission')
        AS DECIMAL(38, 6)
    ) AS aggregate_commission,
    try_cast(
        json_extract_string(rows.row_json, '$.num_advisers')
        AS INTEGER
    ) AS adviser_count,
    try_cast(
        json_extract_string(rows.row_json, '$.num_custodians')
        AS INTEGER
    ) AS custodian_count,
    try_cast(
        json_extract_string(rows.row_json, '$.has_etf')
        AS BOOLEAN
    ) AS has_etf
FROM filing_table_rows rows
JOIN sec_filings sf USING (accession_number)
WHERE sf.filing_family = 'fund_census'
  AND rows.table_name = 'series_data';

CREATE OR REPLACE VIEW v_fund_service_providers AS
SELECT
    sf.accession_number,
    sf.filing_date,
    json_extract_string(rows.row_json, '$.series_id') AS series_id,
    json_extract_string(rows.row_json, '$.series_name') AS series_name,
    json_extract_string(rows.row_json, '$.role') AS provider_role,
    json_extract_string(rows.row_json, '$.provider_name') AS provider_name,
    json_extract_string(rows.row_json, '$.lei') AS provider_lei,
    try_cast(
        json_extract_string(rows.row_json, '$.affiliated')
        AS BOOLEAN
    ) AS is_affiliated
FROM filing_table_rows rows
JOIN sec_filings sf USING (accession_number)
WHERE sf.filing_family = 'fund_census'
  AND rows.table_name = 'service_providers';

CREATE OR REPLACE VIEW v_shareholder_report_metrics AS
SELECT
    sf.accession_number,
    sf.filing_date,
    rows.table_name AS metric_table,
    rows.row_json
FROM filing_table_rows rows
JOIN sec_filings sf USING (accession_number)
WHERE sf.filing_family = 'fund_shareholder_reports'
  AND rows.table_name IN ('expense_data', 'performance_data', 'holdings_data');

CREATE OR REPLACE VIEW v_proxy_voting_filings AS
SELECT
    sf.accession_number,
    sf.filing_date,
    json_extract_string(
        rows.row_json,
        '$.edgarSubmission.headerData.filerInfo.filer.issuerCredentials.cik."#text"'
    ) AS filer_cik,
    json_extract_string(
        rows.row_json,
        '$.edgarSubmission.formData.coverPage.reportingPerson.name."#text"'
    ) AS fund_name,
    coalesce(
        try_strptime(
            json_extract_string(
                rows.row_json,
                '$.edgarSubmission.headerData.filerInfo.periodOfReport."#text"'
            ),
            '%m/%d/%Y'
        )::DATE,
        try_cast(
            json_extract_string(
                rows.row_json,
                '$.edgarSubmission.headerData.filerInfo.periodOfReport."#text"'
            ) AS DATE
        )
    ) AS period_of_report,
    json_extract_string(
        rows.row_json,
        '$.edgarSubmission.headerData.submissionType."#text"'
    ) AS form,
    sf.form LIKE '%/A' AS is_amendment,
    try_cast(
        json_extract_string(fa.extractor_manifest, '$.proxy_votes_source')
        AS BIGINT
    ) AS proxy_vote_count,
    rows.row_json
FROM filing_table_rows rows
JOIN sec_filings sf USING (accession_number)
JOIN filing_artifacts fa USING (accession_number)
WHERE sf.filing_family = 'proxy_voting'
  AND rows.table_name = 'npx_filing';

"""


def connect_database(path: str | Path = DEFAULT_DATABASE_PATH) -> duckdb.DuckDBPyConnection:
    database_path = Path(path).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(database_path))
    connection.execute("PRAGMA disable_progress_bar")
    connection.execute(SCHEMA_SQL)
    connection.execute(
        "ALTER TABLE bulk_dataset_files ADD COLUMN IF NOT EXISTS parquet_sha256 VARCHAR"
    )
    connection.execute(
        "ALTER TABLE bulk_dataset_files ADD COLUMN IF NOT EXISTS parquet_bytes BIGINT"
    )
    connection.execute(
        "ALTER TABLE filing_artifacts ADD COLUMN IF NOT EXISTS raw_submission_bytes BIGINT"
    )
    return connection
