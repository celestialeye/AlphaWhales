from pathlib import Path
from html.parser import HTMLParser
import json
import shutil
import subprocess

from jinja2 import Environment, FileSystemLoader, select_autoescape
import pandas as pd
import pytest

from data_service import DataService


@pytest.mark.parametrize("prior_values, expected", [
    ([100.0, 300.0, 0.0], 0.4),
    ([0.0, 0.0, 0.0], None),
])
def test_qoq_position_size_uses_median_positive_prior_weight(
    prior_values, expected, tmp_path, monkeypatch
):
    monkeypatch.setattr("data_service.CACHE_DIR", str(tmp_path))
    service = DataService.__new__(DataService)
    fund = {
        "status": "loaded",
        "fund_info": {
            "name": "Example Fund",
            "manager": "Example",
            "group": "Quality Growth",
            "cik": "0000000001",
        },
        "metadata": {"report_period": "2026-06-30"},
        "holdings": pd.DataFrame({
            "Cusip": ["A", "B", "C"],
            "PortfolioWeight": [30.0, 50.0, 20.0],
        }),
        "comparison": pd.DataFrame({
            "Cusip": ["A", "B", "C"],
            "Ticker": ["INTC", "BRKB", "NO_SUCH_TICKER"],
            "Status": ["INCREASED", "INCREASED", "NEW"],
            "Value": [150.0, 250.0, 100.0],
            "PrevValue": prior_values,
            "Shares": [15, 25, 10],
            "PrevShares": [10, 20, 0],
        }),
    }

    result = service.get_qoq_changes(fund_cache={"0000000001": fund})
    initiation = next(row for row in result if row["ticker"] == "NO_SUCH_TICKER")

    assert initiation["position_size_vs_normal"] == expected
    assert initiation["status"] == "NEW"
    assert initiation["portfolio_weight"] == 20.0
    assert initiation["sector"] == "Unclassified"
    assert initiation["industry"] is None
    intel = next(row for row in result if row["ticker"] == "INTC")
    assert intel["sector"] == "Technology"
    assert intel["industry"] == "Semiconductors"
    berkshire = next(row for row in result if row["ticker"] == "BRKB")
    assert berkshire["sector"] == "Financial Services"
    assert DataService._security_classifications()["MU"] == {
        "sector": "Technology", "industry": "Semiconductors",
    }


def test_sector_reuses_expired_profile_without_serving_expired_quotes(tmp_path, monkeypatch):
    monkeypatch.setattr("data_service.CACHE_DIR", str(tmp_path))
    service = DataService.__new__(DataService)
    service.ticker_market_cache = {}
    path = Path(service._get_ticker_market_cache_path("MOD"))
    path.write_text(json.dumps({
        "cache_version": 10,
        "news_filter_version": 1,
        "last_updated": "2020-01-01T00:00:00+00:00",
        "quote": {"price": 100},
        "profile": {"sector": "Consumer Cyclical", "industry_category": "Auto Parts"},
    }), encoding="utf-8")

    assert service._load_ticker_market_data_from_disk("MOD") is None
    profile = service._load_ticker_market_data_from_disk("MOD", profile_only=True)
    assert set(profile) == {"profile"}
    assert service._get_security_classification(" mod ") == {
        "sector": "Consumer Cyclical", "industry": "Auto Parts",
    }


def test_cached_profile_precedes_reference_and_new_profiles_are_not_pinned_unknown(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("data_service.CACHE_DIR", str(tmp_path))
    service = DataService.__new__(DataService)
    service.ticker_market_cache = {}
    assert service._get_security_classification("NO_SUCH_TICKER")["sector"] == "Unclassified"

    service.ticker_market_cache["NO_SUCH_TICKER"] = {
        "profile": {"sector": "Industrials", "industry": "Machinery"},
    }
    assert service._get_security_classification("NO_SUCH_TICKER") == {
        "sector": "Industrials", "industry": "Machinery",
    }
    service.ticker_market_cache["INTC"] = {
        "profile": {"sector": "Updated sector", "industry_category": "Updated industry"},
    }
    assert service._get_security_classification("INTC")["sector"] == "Updated sector"


@pytest.mark.parametrize("profile", [None, {}, {"sector": ""}, {"sector": "Unknown"}])
def test_missing_profile_classification_falls_back_to_reference(profile, tmp_path, monkeypatch):
    monkeypatch.setattr("data_service.CACHE_DIR", str(tmp_path))
    service = DataService.__new__(DataService)
    service.ticker_market_cache = {"INTC": {"profile": profile}}
    assert service._get_security_classification("INTC") == {
        "sector": "Technology", "industry": "Semiconductors",
    }


def test_browser_discovery_criteria_and_rankings():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to exercise the shared browser JavaScript")

    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = vm.createContext({EventSource: class {}});
vm.runInContext(fs.readFileSync(path.join('static', 'js', 'app.js'), 'utf8'), context);
const criteria = {minWeight: 2, minShareIncrease: 50, minWeightIncrease: 1, minShareCut: 25};
const base = {
    ticker: 'TEST', manager: 'Example', cik: '1', status: 'NEW',
    portfolio_weight: 2, portfolio_weight_change_raw: 2, value: 100,
    position_size_vs_normal: 3
};
const select = (rows, status = 'NEW', sort = 'value', filters = criteria) =>
    context.getNotableBets(rows, filters, status, sort);
assert.equal(select([base]).length, 1);
assert.equal(select([{...base, portfolio_weight: 1.99}]).length, 0);
for (const portfolio_weight of [null, undefined, NaN, Infinity]) {
    assert.equal(select([{...base, portfolio_weight}]).length, 0);
}
const add = {...base, status: 'INCREASED', shares_change_pct: 50,
    portfolio_weight_change_raw: 1};
assert.equal(select([add], 'INCREASED').length, 1);
assert.equal(select([add]).length, 0);
assert.equal(select([base], 'INCREASED').length, 0);
assert.equal(select([{...add, shares_change_pct: 49.99}], 'INCREASED').length, 0);
assert.equal(select([{...add, portfolio_weight_change_raw: 0.999}], 'INCREASED').length, 0);
for (const missing of [null, undefined, NaN, Infinity]) {
    assert.equal(select([{...add, shares_change_pct: missing}], 'INCREASED').length, 0);
    assert.equal(select([{...add, portfolio_weight_change_raw: missing}], 'INCREASED').length, 0);
}
for (const status of ['DECREASED', 'CLOSED', 'UNCHANGED']) {
    assert.equal(select([{...add, status}], 'INCREASED').length, 0);
}
assert.equal(select([{...base, portfolio_weight: 1}], 'NEW', 'value',
    {...criteria, minWeight: 1}).length, 1);
assert.equal(select([{...add, shares_change_pct: 30}], 'INCREASED', 'value',
    {...criteria, minShareIncrease: 30}).length, 1);

const intc = {...base, ticker: 'INTC', manager: 'Laffont', portfolio_weight: 3.47,
    value: 1687.29, position_size_vs_normal: 6.6859, sector: 'Technology'};
const mu = {...add, ticker: 'MU', manager: 'Laffont', portfolio_weight: 7.46,
    shares_change_pct: 1793.72, portfolio_weight_change_raw: 7.27, sector: 'Technology'};
const searchRows = [intc, mu, {...base, ticker: 'TMUS', issuer: 'Communications'}];
assert.equal(context.searchNotableChanges(searchRows, ' mu ').length, 1);
assert.equal(context.searchNotableChanges(searchRows, ' mu ')[0].ticker, 'MU');
assert.equal(context.searchNotableChanges(searchRows, 'laffont').length, 2);
assert.equal(context.searchNotableChanges(searchRows, '').length, 3);
assert.equal(context.searchNotableChanges(searchRows, 'no-match').length, 0);
assert.equal(context.searchNotableChanges(searchRows, '', 'Technology').length, 2);
assert.equal(context.searchNotableChanges(searchRows, 'mu', 'Technology').length, 1);
assert.equal(context.searchNotableChanges(searchRows, 'mu', 'Unclassified').length, 0);
assert.equal(context.searchNotableChanges(searchRows, '', 'Unclassified').length, 1);
assert.equal(context.searchNotableChanges(searchRows, 'laffont', 'Financial Services').length, 0);
assert.equal(select([intc])[0].ticker, 'INTC');
assert.equal(select([mu], 'INCREASED')[0].ticker, 'MU');
const many = Array.from({length: 30}, (_, i) => ({
    ...base, ticker: `T${i}`, portfolio_weight: 4 + i, value: i
}));
const rows = [...many, intc];
const original = JSON.stringify(rows);
assert.equal(select(rows).length, 31);
assert.equal(select(rows)[0].ticker, 'INTC');
assert.equal(select(rows, 'NEW', 'portfolio_weight').at(-1).ticker, 'INTC');
assert.equal(JSON.stringify(rows), original);
const unavailable = {...base, ticker: 'MISSING', position_size_vs_normal: null};
assert.equal(select([unavailable, intc], 'NEW', 'position_size_vs_normal')[0].ticker, 'INTC');
assert.equal(select([unavailable, intc], 'NEW', 'position_size_vs_normal')[1].ticker, 'MISSING');
assert.equal(select([base, {...base, ticker: 'AAA'}])[0].ticker, 'AAA');
assert.equal(select([]).length, 0);

const cut = {...base, status: 'DECREASED', previous_portfolio_weight: 2,
    portfolio_weight: 0.5, shares_change_pct: -25};
assert.equal(select([cut], 'DECREASED', 'shares_change_pct').length, 1);
assert.equal(select([{...cut, shares_change_pct: -24.99}], 'DECREASED').length, 0);
assert.equal(select([{...cut, previous_portfolio_weight: 1.99}], 'DECREASED').length, 0);
// Appreciation can lift weight and reported dollars despite an important share cut.
assert.equal(select([{...cut, portfolio_weight: 4, portfolio_weight_change_raw: 2,
    value_change: 100}], 'DECREASED').length, 1);
const largerCut = {...cut, ticker: 'CUT', shares_change_pct: -90};
assert.equal(select([cut, largerCut], 'DECREASED', 'shares_change_pct')[0].ticker, 'CUT');
const exit = {...base, status: 'CLOSED', portfolio_weight: 0,
    previous_portfolio_weight: 2, prev_value: 200, shares_change_pct: -100};
assert.equal(select([exit], 'CLOSED', 'previous_portfolio_weight').length, 1);
assert.equal(select([{...exit, previous_portfolio_weight: 1.99}], 'CLOSED').length, 0);
for (const previous_portfolio_weight of [null, undefined, NaN, Infinity]) {
    assert.equal(select([{...exit, previous_portfolio_weight}], 'CLOSED').length, 0);
    assert.equal(select([{...cut, previous_portfolio_weight}], 'DECREASED').length, 0);
}
assert.equal(select([{...cut, shares_change_pct: null}], 'DECREASED').length, 0);
assert.equal(select([{...base, status: 'UNCHANGED', shares_change_pct: 0}], 'UNCHANGED').length, 1);
assert.equal(select([{...base, status: 'UNCHANGED', portfolio_weight: 1.99}], 'UNCHANGED').length, 0);
assert.equal(select([base, add, cut, exit], 'UNCHANGED').length, 0);
assert.equal(select([{...base, status: 'UNKNOWN'}], 'UNKNOWN').length, 0);
const matrixInput = [intc, mu, cut, exit, {...base, sector: 'Technology',
    status: 'UNCHANGED', shares_change_pct: 0}, {...base, portfolio_weight: 0.1}];
const matrix = context.getSectorActionMatrix(matrixInput, criteria, true, ['Energy']);
assert.equal(matrix[0].sector, '');
const tech = matrix.find(row => row.sector === 'Technology');
assert.equal(tech.actions.NEW.notable, 1);
assert.equal(tech.actions.INCREASED.notable, 1);
assert.equal(tech.actions.UNCHANGED.notable, 1);
const unknown = matrix.find(row => row.sector === 'Unclassified');
assert.equal(unknown.actions.NEW.total, 1);
assert.equal(unknown.actions.NEW.notable, 0);
assert.equal(unknown.actions.CLOSED.notable, 1);
assert.equal(matrix.find(row => row.sector === 'Energy').actions.NEW.total, 0);
for (const status of ['NEW','INCREASED','DECREASED','UNCHANGED','CLOSED']) {
    assert.equal(matrix[0].actions[status].total,
        matrix.slice(1).reduce((sum,row)=>sum+row.actions[status].total,0));
    assert.equal(matrix[0].actions[status].notable,
        matrix.slice(1).reduce((sum,row)=>sum+row.actions[status].notable,0));
}
const invalidMatrix = context.getSectorActionMatrix(matrixInput, criteria, false);
assert.equal(invalidMatrix[0].actions.NEW.notable, null);
assert.equal(invalidMatrix[0].actions.NEW.total, 2);
const searchedMatrix = context.getSectorActionMatrix(
    context.searchNotableChanges(matrixInput, 'MU'), criteria);
assert.equal(searchedMatrix[0].actions.INCREASED.total, 1);
assert.equal(searchedMatrix[0].actions.NEW.total, 0);
// Shared script also runs on non-overview pages.
context.document = {getElementById: () => null};
context.renderNotableBets();
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_signal_desk_template_keeps_context_secondary_and_ids_unique():
    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.elements = []

        def handle_starttag(self, tag, attrs):
            self.elements.append((tag, dict(attrs)))

    templates = Path(__file__).resolve().parents[1] / "templates"
    environment = Environment(
        loader=FileSystemLoader(templates),
        autoescape=select_autoescape(),
    )
    html = environment.get_template("index.html").render(
        static_asset_version=lambda _: 1
    )
    parser = Elements()
    parser.feed(html)
    ids = [attrs["id"] for _, attrs in parser.elements if "id" in attrs]
    assert len(ids) == len(set(ids))
    assert "signal-matrix-body" in ids
    assert "notable-sector" not in ids
    actions = {
        attrs["data-signal-column"]
        for _, attrs in parser.elements
        if "data-signal-column" in attrs
    }
    assert actions == {"NEW", "INCREASED", "DECREASED", "UNCHANGED", "CLOSED"}
    default_panels = [
        attrs["id"] for _, attrs in parser.elements
        if attrs.get("data-overview-panel") == "overview"
    ]
    assert default_panels == ["notable-bets"]
    kpis = next(attrs for _, attrs in parser.elements if attrs.get("id") == "kpi-banner")
    assert kpis["data-overview-panel"] == "positioning"
    assert "hidden" in kpis
    for key in ["new", "adds", "cuts", "holds", "exits"]:
        for suffix in ["lane", "list", "sort", "toggle", "count", "shown"]:
            assert f"notable-{key}-{suffix}" in ids
