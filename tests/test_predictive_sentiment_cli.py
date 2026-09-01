import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import duckdb
import pytest

from predictive_sentiment import cli, publication
from predictive_sentiment.config import (
    AWFI_VERSION,
    PROTOCOL_VERSION,
    ResearchConfig,
)
from predictive_sentiment.pipeline import top_holdings_fingerprint


def test_run_research_atomically_preserves_published_database(monkeypatch, tmp_path):
    output = tmp_path / "predictive_sentiment.duckdb"
    output.write_bytes(b"published-v1")
    observed = {}

    def fake_run_research(*, output_db, **kwargs):
        staging = Path(output_db)
        observed["staging"] = staging
        assert staging != output
        assert staging.read_bytes() == b"published-v1"
        staging.write_bytes(b"published-v2")
        return "complete"

    monkeypatch.setattr(publication, "run_research", fake_run_research)
    monkeypatch.setattr(
        publication,
        "_validate_staging_snapshot",
        lambda path: None,
    )

    result = publication.run_research_atomically(
        output_db=output,
        source_db=tmp_path / "source.duckdb",
        performance_db=tmp_path / "performance.duckdb",
        roster_path=tmp_path / "roster.json",
    )

    assert result == "complete"
    assert output.read_bytes() == b"published-v2"
    assert not observed["staging"].exists()


def test_atomic_research_publish_rejects_concurrent_writer(tmp_path):
    output = tmp_path / "predictive_sentiment.duckdb"

    with publication._publication_lock(output):
        with pytest.raises(RuntimeError, match="already running"):
            publication.run_research_atomically(output_db=output)


def test_research_snapshot_freshness_checks_universe_and_source(
    monkeypatch,
    tmp_path,
):
    output = tmp_path / "predictive_sentiment.duckdb"
    source = tmp_path / "source.duckdb"
    performance_path = tmp_path / "performance.duckdb"
    roster_path = tmp_path / "roster.json"
    roster_path.write_text("roster", encoding="utf-8")
    roster = [{"cik": "0000000001"}]
    cached_rows = [
        {
            "canonical_cik": "0000000001",
            "manager_name": "Manager",
            "report_period": date(2026, 6, 30),
            "holding_rank": 1,
            "universe_source": "APPLICATION_CACHE",
            "cusip": "A",
            "issuer": "Issuer",
            "title": "COM",
            "portfolio_weight": 10.0,
            "reported_value": 100.0,
        }
    ]
    monkeypatch.setattr(publication, "load_roster", lambda path: roster)
    monkeypatch.setattr(
        publication,
        "_load_cached_top_holdings",
        lambda *args, **kwargs: cached_rows,
    )
    monkeypatch.setattr(
        publication,
        "_load_current_top_holdings",
        lambda *args, **kwargs: [],
    )
    source_digest = {"value": "source-v1"}
    monkeypatch.setattr(
        publication,
        "_load_source_data",
        lambda *args, **kwargs: ([], {}, []),
    )
    monkeypatch.setattr(
        publication,
        "source_fingerprint",
        lambda *args, **kwargs: source_digest["value"],
    )

    source_connection = duckdb.connect(str(source))
    source_connection.execute(
        """
        CREATE TABLE sec_filings (
            filing_family VARCHAR,
            filing_date DATE,
            period_of_report DATE
        );
        CREATE TABLE holdings (id INTEGER);
        INSERT INTO sec_filings
        VALUES ('institutional_holdings', '2026-08-14', '2026-06-30');
        INSERT INTO holdings VALUES (1);
        """
    )
    source_signature = publication.source_13f_signature(
        source_connection
    )
    source_signature["roster_source_fingerprint"] = "source-v1"
    source_connection.close()
    performance_connection = duckdb.connect(str(performance_path))
    performance_connection.execute(
        """
        CREATE TABLE cusip_ticker_mapping (
            cusip VARCHAR,
            retrieved_at TIMESTAMPTZ
        );
        CREATE TABLE price_manifest (
            status VARCHAR,
            row_count BIGINT,
            updated_at TIMESTAMPTZ
        );
        INSERT INTO cusip_ticker_mapping
        VALUES ('A', '2026-08-31T00:00:00Z');
        INSERT INTO price_manifest
        VALUES ('READY', 100, '2026-08-31T00:00:00Z');
        """
    )
    performance_connection.close()
    performance_connection = duckdb.connect(
        str(performance_path),
        read_only=True,
    )
    performance_signature = publication.performance_database_signature(
        performance_path,
        performance_connection,
    )
    performance_connection.close()

    connection = duckdb.connect(str(output))
    connection.execute(
        """
        CREATE TABLE research_runs (
            run_id VARCHAR,
            protocol_version VARCHAR,
            status VARCHAR,
            completed_at TIMESTAMP,
            config_json JSON,
            roster_sha256 VARCHAR
        );
        CREATE TABLE awfi_scores (
            run_id VARCHAR,
            awfi_version VARCHAR
        );
        CREATE TABLE run_artifact_provenance (
            run_id VARCHAR,
            artifact_name VARCHAR,
            fingerprint VARCHAR,
            details_json JSON
        );
        """
    )
    roster_sha256 = hashlib.sha256(roster_path.read_bytes()).hexdigest()
    connection.execute(
        """
        INSERT INTO research_runs
        VALUES ('run', ?, 'COMPLETE', now(), ?, ?)
        """,
        [
            PROTOCOL_VERSION,
            json.dumps(ResearchConfig().as_dict()),
            roster_sha256,
        ],
    )
    connection.execute(
        "INSERT INTO awfi_scores VALUES ('run', ?)",
        [AWFI_VERSION],
    )
    connection.executemany(
        "INSERT INTO run_artifact_provenance VALUES ('run', ?, ?, ?)",
        [
            (
                "top_holdings_universe",
                top_holdings_fingerprint(cached_rows),
                json.dumps(
                    {
                        "rows": 1,
                        "latest_period": "2026-06-30",
                        "sources": {"APPLICATION_CACHE": 1},
                    }
                ),
            ),
            (
                "source_13f",
                "",
                json.dumps({"signature": source_signature}),
            ),
            (
                "performance_database",
                "",
                json.dumps({"signature": performance_signature}),
            ),
        ],
    )
    connection.close()

    assert publication.research_snapshot_needs_refresh(
        output_db=output,
        roster_path=roster_path,
        application_cache_dir=tmp_path,
        source_db=source,
        performance_db=performance_path,
    ) is False

    cached_rows[0]["portfolio_weight"] = 11.0

    assert publication.research_snapshot_needs_refresh(
        output_db=output,
        roster_path=roster_path,
        application_cache_dir=tmp_path,
        source_db=source,
        performance_db=performance_path,
    ) is True

    cached_rows[0]["portfolio_weight"] = 10.0
    source_digest["value"] = "source-v2"

    assert publication.research_snapshot_needs_refresh(
        output_db=output,
        roster_path=roster_path,
        application_cache_dir=tmp_path,
        source_db=source,
        performance_db=performance_path,
    ) is True


def test_validate_inputs_cli_uses_supported_arguments(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(
        sys,
        "argv",
        ["predictive_sentiment", "validate-inputs"],
    )
    monkeypatch.setattr(
        cli,
        "validate_inputs",
        lambda **kwargs: captured.update(kwargs) or {"status": "ok"},
    )

    cli.main()

    assert set(captured) == {
        "source_db",
        "performance_db",
        "roster_path",
    }
    assert '"status": "ok"' in capsys.readouterr().out


def test_run_cli_forwards_application_cache_directory(
    monkeypatch,
    tmp_path,
    capsys,
):
    captured = {}
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "predictive_sentiment",
            "run",
            "--application-cache-dir",
            str(cache_dir),
        ],
    )
    monkeypatch.setattr(
        cli,
        "run_research_atomically",
        lambda **kwargs: captured.update(kwargs) or {"status": "COMPLETE"},
    )
    monkeypatch.setattr(cli, "asdict", lambda value: value)

    cli.main()

    assert captured["application_cache_dir"] == cache_dir
    assert '"status": "COMPLETE"' in capsys.readouterr().out
