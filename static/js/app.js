// ==========================================
// Alpha Whales Intelligence - Frontend Application
// ==========================================

// Global State
let globalQoQData = [];
let filteredQoQData = [];
let currentSortColumn = 'value_change';
let currentSortAsc = false;
let currentPage = 1;
let pageSize = 50;
let selectedFilingPeriod = null;

let globalAllTickers = [];
let filteredAllTickers = [];
let allTickersSortCol = 'num_holders';
let allTickersSortAsc = false;

let globalInvestorHoldings = [];
let globalInvestorClosed = [];
let currentHoldingsTab = 'ALL';
let investorSortCol = 'portfolio_weight';
let investorSortAsc = false;

// Global SSE Setup
const evtSource = new EventSource('/events');
evtSource.onmessage = function(e) {
    try {
        const data = JSON.parse(e.data);
        if (data.type === 'data_refresh' || data.type === 'fund_updated') {
            updateTimestamp(data.timestamp || new Date().toISOString());
            showToast(`SEC Data Refreshed: ${data.type === 'fund_updated' ? `CIK ${data.cik} updated` : 'All 26 funds updated'}`);

            // Re-fetch current page data
            if (window.location.pathname === '/') {
                loadOverviewData(selectedFilingPeriod);
            } else if (window.location.pathname.startsWith('/ticker')) {
                const parts = window.location.pathname.split('/');
                if (parts.length > 2 && parts[2]) loadTickerDetail(parts[2]);
                else loadAllTickers();
            } else if (window.location.pathname.startsWith('/investor')) {
                const parts = window.location.pathname.split('/');
                if (parts.length > 2 && parts[2]) loadInvestorDetail(parts[2]);
                else loadInvestorsList();
            }
        }
    } catch(err) {
        console.error('Error handling SSE event:', err);
    }
};

function triggerRefresh() {
    const btn = document.getElementById('refresh-btn');
    const spinner = document.getElementById('refresh-spinner');
    const text = document.getElementById('refresh-text');

    if (spinner) spinner.style.display = 'inline-block';
    if (text) text.textContent = ' Refreshing...';
    if (btn) btn.disabled = true;

    showToast('Triggered SEC 13F background refresh across 26 funds...');

    fetch('/api/refresh')
        .then(r => r.json())
        .then(data => {
            setTimeout(() => {
                if (spinner) spinner.style.display = 'none';
                if (text) text.textContent = '↻ Refresh Now';
                if (btn) btn.disabled = false;
            }, 3000);
        })
        .catch(err => {
            showToast('Refresh request failed: ' + err.message);
            if (spinner) spinner.style.display = 'none';
            if (text) text.textContent = '↻ Refresh Now';
            if (btn) btn.disabled = false;
        });
}

function updateTimestamp(isoStr) {
    if (!isoStr) return;
    const d = new Date(isoStr);
    const timeEl = document.getElementById('last-updated-text');
    if (timeEl) {
        timeEl.textContent = 'Updated: ' + d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'});
    }
}

function showToast(msg) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.innerHTML = `<span>⚡</span><span>${msg}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// Formatters & Class Helpers
function formatNum(num) {
    return (num !== null && num !== undefined && !isNaN(num))
        ? Number(num).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})
        : '0.00';
}
function formatInt(num) {
    return (num !== null && num !== undefined && !isNaN(num))
        ? Number(num).toLocaleString()
        : '0';
}
function formatPct(num) {
    return (num !== null && num !== undefined && !isNaN(num))
        ? Number(num).toFixed(2) + '%'
        : '0.00%';
}
function getGroupClass(g) {
    if (!g) return 'badge-neutral';
    if (g.includes('Value')) return 'badge-group-value';
    if (g.includes('High-performance')) return 'badge-group-high-perf';
    if (g.includes('Quality')) return 'badge-group-quality';
    if (g.includes('2026')) return 'badge-group-2026';
    return 'badge-neutral';
}
function getStatusClass(s) {
    if (!s) return 'badge-status-unchanged';
    const sl = s.toLowerCase();
    return `badge-status-${sl}`;
}
function renderSparkline(pct) {
    const safePct = Math.max(0, Math.min(Number(pct) || 0, 100));
    return `<div class="sparkline-container"><div class="sparkline-bar" style="width: ${safePct}%"></div></div>${(Number(pct)||0).toFixed(2)}%`;
}

// ==========================================
// 1. Overview & QoQ Changes Dashboard
// ==========================================

function formatFilingPeriodLabel(period) {
    const date = new Date(`${period}T00:00:00Z`);
    const quarter = Math.floor(date.getUTCMonth() / 3) + 1;
    return `Q${quarter} ${date.getUTCFullYear()} · ${period}`;
}

async function initializeOverviewPeriodSelector() {
    const select = document.getElementById('filing-period-select');
    const status = document.getElementById('filing-period-status');
    try {
        const response = await fetch('/api/filing-periods');
        if (!response.ok) throw new Error(`Period list failed with HTTP ${response.status}`);
        const result = await response.json();
        const periods = result.periods || [];
        if (!periods.length) throw new Error('No filing periods are available');

        select.innerHTML = periods.map(period => `
            <option value="${period}">${formatFilingPeriodLabel(period)}</option>
        `).join('');
        selectedFilingPeriod = result.latest || periods[0];
        select.value = selectedFilingPeriod;
        await loadOverviewData(selectedFilingPeriod);
    } catch (err) {
        console.error('Error loading filing periods:', err);
        if (status) status.textContent = 'Could not load filing periods';
        showToast('Could not load filing periods', 'error');
    } finally {
        if (select) select.disabled = false;
    }
}

async function changeFilingPeriod(period) {
    selectedFilingPeriod = period;
    await loadOverviewData(period);
}

function updatePeriodLoader(period, status = {}) {
    const progress = document.querySelector('.period-loader-progress');
    const progressBar = document.getElementById('period-loader-progress-bar');
    const title = document.getElementById('period-loader-title');
    const message = document.getElementById('period-loader-message');
    const count = document.getElementById('period-loader-count');
    const source = document.getElementById('period-loader-source');
    const total = status.total_funds || 26;
    const completed = status.completed_funds || 0;
    const percent = total > 0 ? Math.max(4, Math.round((completed / total) * 100)) : 4;

    if (status.state === 'fetching' || status.state === 'uncached') {
        progress?.classList.remove('is-indeterminate');
        if (progressBar) progressBar.style.width = `${percent}%`;
        if (title) title.textContent = `Building ${formatFilingPeriodLabel(period)}`;
        if (message) message.textContent = 'Fetching and comparing manager filings from SEC EDGAR. This first load is cached for future visits.';
        if (count) count.textContent = `${completed} of ${total} funds`;
        if (source) source.textContent = 'Live SEC fetch';
        return;
    }

    progress?.classList.add('is-indeterminate');
    if (title) title.textContent = `Loading ${formatFilingPeriodLabel(period)}`;
    if (message) {
        message.textContent = status.source === 'disk'
            ? 'Reading the saved historical snapshot from local storage...'
            : 'Preparing the cached filing-period view...';
    }
    if (count) count.textContent = `${total} funds cached`;
    if (source) {
        source.textContent = status.source === 'disk'
            ? 'Disk cache'
            : status.source === 'latest'
                ? 'Latest cache'
                : 'Memory cache';
    }
}

async function showPeriodLoader(period) {
    const loader = document.getElementById('period-loader');
    if (!loader) return;
    loader.hidden = false;
    document.body.classList.add('period-loading');
    updatePeriodLoader(period, {state: 'uncached', completed_funds: 0, total_funds: 26});

    try {
        const response = await fetch(`/api/period-cache-status?period=${encodeURIComponent(period)}`);
        if (response.ok) updatePeriodLoader(period, await response.json());
    } catch (err) {
        console.debug('Period cache status unavailable:', err);
    }
}

function hidePeriodLoader() {
    const loader = document.getElementById('period-loader');
    if (loader) loader.hidden = true;
    document.body.classList.remove('period-loading');
}

async function loadOverviewData(period = selectedFilingPeriod) {
    const select = document.getElementById('filing-period-select');
    const status = document.getElementById('filing-period-status');
    if (select) select.disabled = true;
    if (status) status.textContent = `Loading ${period ? formatFilingPeriodLabel(period) : 'latest filing period'}...`;
    const loaderStartedAt = performance.now();
    await showPeriodLoader(period);

    let progressTimer = null;
    if (period) {
        progressTimer = window.setInterval(async () => {
            try {
                const response = await fetch(`/api/period-cache-status?period=${encodeURIComponent(period)}`);
                if (response.ok) updatePeriodLoader(period, await response.json());
            } catch (err) {
                console.debug('Historical load progress unavailable:', err);
            }
        }, 500);
    }

    try {
        const query = period ? `?period=${encodeURIComponent(period)}` : '';
        const response = await fetch(`/api/period-view${query}`);
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.error || `Period view failed with HTTP ${response.status}`);
        }
        const result = await response.json();
        selectedFilingPeriod = result.period;
        if (select) select.value = result.period;

        const allQoQData = result.changes || [];
        globalQoQData = allQoQData.filter(change => change.status !== 'UNCHANGED');
        if (result.overview) updateTimestamp(result.overview.last_updated);
        applyFilters();
        renderOverviewCharts(allQoQData);
        renderManagerActivityMatrix(globalQoQData, result.funds || []);
        renderSignalKpis(result.tickers || [], globalQoQData, result.portfolio_stats || {});
        renderPortfolioStats(result.tickers || [], globalQoQData, result.portfolio_stats || {});
        if (status) {
            const loaded = result.overview?.loaded_funds ?? 0;
            const total = result.overview?.total_funds ?? 0;
            const sourceLabels = {
                latest: 'latest cache',
                memory: 'memory cache',
                disk: 'disk cache',
                sec: 'fresh SEC snapshot'
            };
            const source = sourceLabels[result.cache_status?.source] || 'cached snapshot';
            status.textContent = `${loaded}/${total} funds available · ${source}`;
        }
    } catch(err) {
        console.error('Error loading QoQ changes:', err);
        if (status) status.textContent = `Could not load ${period || 'latest period'}`;
        showToast(err.message || 'Could not load filing-period data', 'error');
    } finally {
        if (progressTimer) window.clearInterval(progressTimer);
        if (select) select.disabled = false;
        const remainingLoaderTime = Math.max(0, 550 - (performance.now() - loaderStartedAt));
        if (remainingLoaderTime > 0) {
            await new Promise(resolve => window.setTimeout(resolve, remainingLoaderTime));
        }
        hidePeriodLoader();
    }
}

function formatWeightChangeChip(move) {
    const change = Number(move.portfolio_weight_change) || 0;
    const sign = change > 0 ? '+' : '';
    const actionClass = ['NEW', 'INCREASED'].includes(move.status) ? 'buy' : 'sell';
    const deltaClass = change > 0 ? 'positive' : change < 0 ? 'negative' : 'neutral';
    return `
        <a class="activity-chip ${actionClass}" href="/ticker/${move.ticker}"
           aria-label="${move.ticker}, ${move.status.toLowerCase()}, ${sign}${change.toFixed(2)} percentage points of portfolio">
            <strong>${move.ticker}</strong>
            <span class="${deltaClass}">${sign}${change.toFixed(2)}pp</span>
        </a>
    `;
}

function renderManagerActivityMatrix(changes, funds) {
    const body = document.getElementById('manager-activity-body');
    if (!body) return;

    const managers = new Map(funds.map(fund => [fund.cik, {
        cik: fund.cik,
        manager: fund.manager,
        fundName: fund.name,
        group: fund.group,
        period: fund.report_period,
        buys: [],
        sells: []
    }]));
    changes.forEach(move => {
        const manager = managers.get(move.cik) || {
            cik: move.cik,
            manager: move.manager,
            fundName: move.fund_name,
            group: move.group,
            period: move.report_period,
            buys: [],
            sells: []
        };
        if (['NEW', 'INCREASED'].includes(move.status)) manager.buys.push(move);
        if (['DECREASED', 'CLOSED'].includes(move.status)) manager.sells.push(move);
        managers.set(move.cik, manager);
    });

    const rows = [...managers.values()]
        .map(manager => ({
            ...manager,
            buys: manager.buys
                .sort((a, b) => b.portfolio_weight_change - a.portfolio_weight_change)
                .slice(0, 5),
            sells: manager.sells
                .sort((a, b) => a.portfolio_weight_change - b.portfolio_weight_change)
                .slice(0, 5)
        }))
        .sort((a, b) => a.manager.localeCompare(b.manager));

    body.innerHTML = rows.map(manager => `
        <tr>
            <td>
                <a class="manager-activity-name" href="/investor/${manager.cik}">
                    <strong>${manager.manager}</strong>
                    <span>${manager.fundName}</span>
                </a>
                <span class="badge ${getGroupClass(manager.group)}">${manager.group}</span>
            </td>
            <td class="font-mono manager-activity-period">${manager.period || '—'}</td>
            <td>
                <div class="activity-chip-list">
                    ${manager.buys.length
                        ? manager.buys.map(formatWeightChangeChip).join('')
                        : '<span class="activity-empty">No buys reported</span>'}
                </div>
            </td>
            <td>
                <div class="activity-chip-list">
                    ${manager.sells.length
                        ? manager.sells.map(formatWeightChangeChip).join('')
                        : '<span class="activity-empty">No sells reported</span>'}
                </div>
            </td>
        </tr>
    `).join('');
}

function updateSignalKpi(id, item, description) {
    const card = document.getElementById(id);
    if (!card) return;

    if (!item) {
        card.href = '/ticker';
        card.querySelector('.kpi-value').textContent = '—';
        card.querySelector('.kpi-sub').textContent = 'No comparable data for this period';
        card.setAttribute('aria-label', `${card.querySelector('.kpi-label').textContent}: no comparable data for this period`);
        return;
    }

    card.href = `/ticker/${item.ticker}`;
    card.querySelector('.kpi-value').textContent = item.ticker;
    card.querySelector('.kpi-sub').textContent = description;
    card.setAttribute('aria-label', `${card.querySelector('.kpi-label').textContent}: ${item.ticker}. ${description}`);
}

function formatFundCount(count, singular) {
    return `${count} ${singular}${count === 1 ? '' : 's'}`;
}

function renderSignalKpis(tickers, changes, portfolioStats) {
    const withActions = tickers.map(item => ({
        ...item,
        buyerCount: (item.qoq_actions?.new || 0) + (item.qoq_actions?.increased || 0),
        sellerCount: (item.qoq_actions?.closed || 0) + (item.qoq_actions?.decreased || 0),
        newCount: item.qoq_actions?.new || 0
    }));

    const consensusBuy = [...withActions]
        .sort((a, b) => b.buyerCount - a.buyerCount || b.num_holders - a.num_holders)[0];
    const consensusSell = [...withActions]
        .sort((a, b) => b.sellerCount - a.sellerCount || b.num_holders - a.num_holders)[0];
    const newIdea = [...withActions]
        .sort((a, b) => b.newCount - a.newCount || b.num_holders - a.num_holders)[0];
    const conviction = tickers
        .filter(item => item.num_holders >= MIN_CONVICTION_HOLDERS)
        .sort((a, b) => b.median_weight - a.median_weight || b.num_holders - a.num_holders)[0];
    const nearLowCandidates = [...(portfolioStats?.near_52_week_low || [])]
        .filter(item => item.ownership_count >= MIN_CONVICTION_HOLDERS);
    const nearLow = (nearLowCandidates.length
        ? nearLowCandidates
        : [...(portfolioStats?.near_52_week_low || [])]
    ).sort((a, b) => a.pct_above_low - b.pct_above_low)[0];

    const netValueByTicker = new Map();
    changes.forEach(change => {
        const aggregate = netValueByTicker.get(change.ticker) || {
            ticker: change.ticker,
            valueChange: 0,
            buyers: new Set(),
            sellers: new Set()
        };
        aggregate.valueChange += change.value_change;
        if (['NEW', 'INCREASED'].includes(change.status)) aggregate.buyers.add(change.manager);
        if (['DECREASED', 'CLOSED'].includes(change.status)) aggregate.sellers.add(change.manager);
        netValueByTicker.set(change.ticker, aggregate);
    });
    const netValueChanges = [...netValueByTicker.values()];
    const dollarInflow = netValueChanges
        .filter(item => item.valueChange > 0)
        .sort((a, b) => b.valueChange - a.valueChange)[0];
    const dollarOutflow = netValueChanges
        .filter(item => item.valueChange < 0)
        .sort((a, b) => a.valueChange - b.valueChange)[0];

    updateSignalKpi(
        'signal-consensus-buy',
        consensusBuy,
        consensusBuy
            ? `${consensusBuy.buyerCount} investors added or initiated · ${consensusBuy.num_holders} holders`
            : null
    );
    updateSignalKpi(
        'signal-dollar-inflow',
        dollarInflow,
        dollarInflow
            ? `+$${formatNum(dollarInflow.valueChange)}M net reported value · ${formatFundCount(dollarInflow.buyers.size, 'buyer')} / ${formatFundCount(dollarInflow.sellers.size, 'seller')}`
            : null
    );
    updateSignalKpi(
        'signal-consensus-sell',
        consensusSell,
        consensusSell
            ? `${consensusSell.sellerCount} investors reduced or exited · ${consensusSell.num_holders} holders`
            : null
    );
    updateSignalKpi(
        'signal-dollar-outflow',
        dollarOutflow,
        dollarOutflow
            ? `-$${formatNum(Math.abs(dollarOutflow.valueChange))}M net reported value · ${formatFundCount(dollarOutflow.buyers.size, 'buyer')} / ${formatFundCount(dollarOutflow.sellers.size, 'seller')}`
            : null
    );
    updateSignalKpi(
        'signal-new-idea',
        newIdea,
        newIdea
            ? `${newIdea.newCount} new investors this quarter · ${newIdea.num_holders} holders now`
            : null
    );
    updateSignalKpi(
        'signal-conviction',
        conviction,
        conviction
            ? `${formatPct(conviction.median_weight)} median position · ${conviction.num_holders} holders`
            : null
    );
    updateSignalKpi(
        'signal-near-low',
        nearLow,
        nearLow
            ? `${formatPct(nearLow.pct_above_low)} above 52-week low · ${nearLow.ownership_count} holders`
            : null
    );
}

function renderStatsList(containerId, items, metricFormatter, detailFormatter = null) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!items.length) {
        container.innerHTML = '<li class="stats-loading">No qualifying positions found.</li>';
        return;
    }

    container.innerHTML = items.map(item => `
        <li class="stats-row">
            <a href="/ticker/${item.ticker}" class="stats-security">
                <strong class="font-mono">${item.ticker}</strong>
                <span>${item.issuer || 'Unknown issuer'}</span>
                ${detailFormatter ? `<small>${detailFormatter(item)}</small>` : ''}
            </a>
            <span class="stats-metric">${metricFormatter(item)}</span>
        </li>
    `).join('');
}

function formatQoqDelta(value, suffix, singularLabel = null) {
    const rounded = Math.round(value * 100) / 100;
    if (rounded === 0) {
        return '<small class="stats-qoq neutral">− No change QoQ</small>';
    }

    const directionClass = rounded > 0 ? 'positive' : rounded < 0 ? 'negative' : 'neutral';
    const arrow = rounded > 0 ? '▲' : rounded < 0 ? '▼' : '−';
    const absoluteValue = Math.abs(rounded);
    const formattedValue = Number.isInteger(absoluteValue)
        ? absoluteValue.toString()
        : absoluteValue.toFixed(2);
    const label = singularLabel && absoluteValue === 1 ? singularLabel : suffix;
    return `<small class="stats-qoq ${directionClass}">${arrow} ${formattedValue}${label} QoQ</small>`;
}

function renderOwnershipActivityTable(items, reportPeriod) {
    const body = document.getElementById('stats-most-owned');
    if (!body) return;

    const marketHeader = document.getElementById('stats-market-period');
    const periodLabel = reportPeriod
        ? new Date(`${reportPeriod}T00:00:00Z`).toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric',
            timeZone: 'UTC'
        })
        : null;
    if (marketHeader && periodLabel) {
        marketHeader.textContent = `${periodLabel} Market`;
    }

    body.innerHTML = items.map(item => {
        const actions = item.qoq_actions || {};
        const positionDelta = item.median_weight_change === null
            ? '<small class="stats-qoq neutral">− No comparable holders</small>'
            : formatQoqDelta(item.median_weight_change, 'pp');
        const priceReturn = item.price_return_since_quarter;
        const marketContext = item.quarter_end_price !== null
            && item.quarter_end_price !== undefined
            && priceReturn !== null
            && priceReturn !== undefined
            ? `
                <strong>$${formatNum(item.quarter_end_price)}</strong>
                ${formatQoqDelta(priceReturn, '%').replace(' QoQ', ' since')}
            `
            : '<span class="stats-table-muted">Unavailable</span>';

        return `
            <tr>
                <td>
                    <a href="/ticker/${item.ticker}" class="stats-table-security">
                        <strong class="font-mono">${item.ticker}</strong>
                        <span>${item.issuer || 'Unknown issuer'}</span>
                    </a>
                </td>
                <td>
                    <div class="stats-table-stack">
                        <strong class="font-mono">${item.num_holders}</strong>
                        ${formatQoqDelta(item.holder_count_change, ' net owners', ' net owner')}
                    </div>
                </td>
                <td>
                    <div class="stats-action-cell">
                        <div>
                            <span class="stats-action increased" title="Investors that increased shares">▲ ${actions.increased || 0}</span>
                            <span class="stats-action decreased" title="Investors that decreased shares">▼ ${actions.decreased || 0}</span>
                        </div>
                        <small>
                            <span class="new">${actions.new || 0} new</span>
                            <span class="closed">${actions.closed || 0} closed</span>
                        </small>
                    </div>
                </td>
                <td>
                    <div class="stats-table-stack">
                        <strong class="font-mono">${formatPct(item.median_weight)}</strong>
                        ${positionDelta}
                    </div>
                </td>
                <td>
                    <div class="stats-table-stack stats-market-cell">
                        ${marketContext}
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

// Conviction should represent a recurring position across a meaningful holder
// sample, not a single manager's concentrated bet.
const MIN_CONVICTION_HOLDERS = 5;

function renderPortfolioStats(tickers, changes, portfolioStats) {
    const period = changes.find(change => change.report_period)?.report_period;

    const mostOwned = [...tickers]
        .sort((a, b) => b.num_holders - a.num_holders || b.total_value_across_funds - a.total_value_across_funds)
        .slice(0, 10);
    renderOwnershipActivityTable(mostOwned, period);

    const highestWeight = tickers
        .filter(t => t.num_holders >= MIN_CONVICTION_HOLDERS)
        .sort((a, b) => b.median_weight - a.median_weight || b.num_holders - a.num_holders)
        .slice(0, 10);
    renderStatsList(
        'stats-highest-weight',
        highestWeight,
        item => `
            <span>${formatPct(item.median_weight)} median</span>
            ${formatQoqDelta(item.median_weight_change, 'pp')}
        `,
        item => `across ${item.num_holders} funds`
    );

    const biggestBets = tickers
        .map(ticker => {
            const largestHolder = [...(ticker.holders || [])]
                .sort((a, b) => b.portfolio_weight - a.portfolio_weight)[0];
            return largestHolder ? {
                ticker: ticker.ticker,
                issuer: ticker.issuer,
                manager: largestHolder.manager,
                cik: largestHolder.cik,
                maxWeight: largestHolder.portfolio_weight,
                numHolders: ticker.num_holders
            } : null;
        })
        .filter(Boolean)
        .sort((a, b) => b.maxWeight - a.maxWeight)
        .slice(0, 10);

    const betsBody = document.getElementById('stats-big-bets');
    if (betsBody) {
        betsBody.innerHTML = biggestBets.map(bet => `
            <tr>
                <td>
                    <a href="/ticker/${bet.ticker}" class="stats-table-security">
                        <strong class="font-mono">${bet.ticker}</strong>
                        <span>${bet.issuer}</span>
                    </a>
                </td>
                <td><a href="/investor/${bet.cik}">${bet.manager}</a></td>
                <td class="font-mono text-cyan"><strong>${formatPct(bet.maxWeight)}</strong></td>
                <td class="font-mono">${bet.numHolders}</td>
            </tr>
        `).join('');
    }

    const buysByTicker = new Map();
    changes
        .filter(change => ['NEW', 'INCREASED'].includes(change.status) && change.value_change > 0)
        .forEach(change => {
            const current = buysByTicker.get(change.ticker) || {
                ticker: change.ticker,
                issuer: change.issuer,
                valueChange: 0,
                managers: new Set()
            };
            current.valueChange += change.value_change;
            current.managers.add(change.manager);
            buysByTicker.set(change.ticker, current);
        });

    const topBuys = [...buysByTicker.values()]
        .sort((a, b) => b.valueChange - a.valueChange)
        .slice(0, 10);
    renderStatsList(
        'stats-top-buys',
        topBuys,
        item => `+$${formatNum(item.valueChange)}M`,
        item => `${item.managers.size} buying fund${item.managers.size === 1 ? '' : 's'}`
    );

    const currentQuarterByWeight = new Map();
    changes
        .filter(change => ['NEW', 'INCREASED'].includes(change.status) && change.portfolio_weight > 0)
        .forEach(change => {
            const current = currentQuarterByWeight.get(change.ticker);
            if (!current || change.portfolio_weight > current.portfolio_weight) {
                currentQuarterByWeight.set(change.ticker, change);
            }
        });

    const currentQuarterWeights = [...currentQuarterByWeight.values()]
        .sort((a, b) => b.portfolio_weight - a.portfolio_weight)
        .slice(0, 10);
    renderStatsList(
        'stats-fastest-adds',
        currentQuarterWeights,
        item => formatPct(item.portfolio_weight),
        item => item.manager
    );

    const sellsByTicker = new Map();
    changes
        .filter(change => ['DECREASED', 'CLOSED'].includes(change.status) && change.value_change < 0)
        .forEach(change => {
            const current = sellsByTicker.get(change.ticker) || {
                ticker: change.ticker,
                issuer: change.issuer,
                valueReduction: 0,
                managers: new Set(),
                closedCount: 0
            };
            current.valueReduction += Math.abs(change.value_change);
            current.managers.add(change.manager);
            if (change.status === 'CLOSED') current.closedCount += 1;
            sellsByTicker.set(change.ticker, current);
        });

    const topSells = [...sellsByTicker.values()]
        .sort((a, b) => b.valueReduction - a.valueReduction)
        .slice(0, 10);
    renderStatsList(
        'stats-top-sells',
        topSells,
        item => `-$${formatNum(item.valueReduction)}M`,
        item => {
            const sellerLabel = `${item.managers.size} selling fund${item.managers.size === 1 ? '' : 's'}`;
            return item.closedCount > 0
                ? `${sellerLabel} · ${item.closedCount} full exit${item.closedCount === 1 ? '' : 's'}`
                : sellerLabel;
        }
    );

    const largestCutsByTicker = new Map();
    changes
        .filter(change => change.status === 'DECREASED' && change.shares_change_pct < 0)
        .forEach(change => {
            const current = largestCutsByTicker.get(change.ticker);
            if (!current || change.shares_change_pct < current.shares_change_pct) {
                largestCutsByTicker.set(change.ticker, change);
            }
        });

    const largestCuts = [...largestCutsByTicker.values()]
        .sort((a, b) => a.shares_change_pct - b.shares_change_pct)
        .slice(0, 10);
    renderStatsList(
        'stats-largest-cuts',
        largestCuts,
        item => `-${formatPct(Math.abs(item.shares_change_pct))}`,
        item => `${item.manager} · ${formatPct(item.portfolio_weight)} remaining`
    );

    const nearLowBody = document.getElementById('stats-near-low');
    if (nearLowBody) {
        const nearLow = portfolioStats.near_52_week_low || [];
        if (!nearLow.length) {
            const status = portfolioStats.market_is_refreshing
                ? 'OpenBB market data is refreshing...'
                : 'No OpenBB market data is cached yet. Refresh data to populate this table.';
            nearLowBody.innerHTML = `<tr><td colspan="6" class="stats-loading">${status}</td></tr>`;
        } else {
            nearLowBody.innerHTML = nearLow.slice(0, 25).map(item => `
                <tr>
                    <td>
                        <a href="/ticker/${item.ticker}" class="stats-table-security">
                            <strong class="font-mono">${item.ticker}</strong>
                            <span>${item.issuer}</span>
                        </a>
                    </td>
                    <td class="font-mono">$${formatNum(item.current_price)}</td>
                    <td class="font-mono">$${formatNum(item.low_52_week)}</td>
                    <td class="font-mono ${item.pct_above_low <= 10 ? 'text-green' : 'text-yellow'}"><strong>${formatPct(item.pct_above_low)}</strong></td>
                    <td class="font-mono">${formatPct(item.max_portfolio_weight)}</td>
                    <td class="font-mono">${item.ownership_count}</td>
                </tr>
            `).join('');
        }
    }
}

function applyFilters() {
    const group = document.getElementById('filter-group')?.value || 'All';
    const statusCbs = document.querySelectorAll('.filter-status:checked');
    const selectedStatuses = Array.from(statusCbs).map(cb => cb.value);
    const minVal = parseFloat(document.getElementById('filter-min-val')?.value) || 0;
    const search = document.getElementById('search-input')?.value.trim().toLowerCase() || '';

    const clearBtn = document.getElementById('clear-search-btn');
    if (clearBtn) clearBtn.style.display = search ? 'block' : 'none';

    filteredQoQData = globalQoQData.filter(row => {
        if (group !== 'All' && row.group !== group) return false;
        if (!selectedStatuses.includes(row.status)) return false;
        if (minVal > 0 && Math.abs(row.value_change) < minVal) return false;
        if (search) {
            const matches = row.ticker.toLowerCase().includes(search) ||
                            row.issuer.toLowerCase().includes(search) ||
                            row.fund_name.toLowerCase().includes(search) ||
                            row.manager.toLowerCase().includes(search);
            if (!matches) return false;
        }
        return true;
    });

    currentPage = 1;
    sortAndRenderQoQTable();
}

function clearSearch() {
    const sInput = document.getElementById('search-input');
    if (sInput) {
        sInput.value = '';
        applyFilters();
    }
}

function resetFilters() {
    if (document.getElementById('filter-group')) document.getElementById('filter-group').value = 'All';
    document.querySelectorAll('.filter-status').forEach(cb => cb.checked = true);
    if (document.getElementById('filter-min-val')) document.getElementById('filter-min-val').value = '';
    if (document.getElementById('search-input')) document.getElementById('search-input').value = '';
    applyFilters();
}

function sortTable(col) {
    if (currentSortColumn === col) {
        currentSortAsc = !currentSortAsc;
    } else {
        currentSortColumn = col;
        currentSortAsc = (col === 'ticker' || col === 'issuer' || col === 'fund_name');
    }

    // Update sort indicator icons
    document.querySelectorAll('.sort-icon').forEach(el => el.textContent = '');
    const activeSortEl = document.getElementById(`sort-${col}`);
    if (activeSortEl) {
        activeSortEl.textContent = currentSortAsc ? '▲' : '▼';
    }

    sortAndRenderQoQTable();
}

function sortAndRenderQoQTable() {
    filteredQoQData.sort((a, b) => {
        let vA = a[currentSortColumn];
        let vB = b[currentSortColumn];

        if (typeof vA === 'string') {
            vA = vA.toLowerCase();
            vB = vB.toLowerCase();
            return currentSortAsc ? vA.localeCompare(vB) : vB.localeCompare(vA);
        } else {
            vA = Number(vA) || 0;
            vB = Number(vB) || 0;
            if (currentSortColumn === 'value_change') {
                // By default sort by absolute magnitude of change if descending
                if (!currentSortAsc) return Math.abs(vB) - Math.abs(vA);
            }
            return currentSortAsc ? vA - vB : vB - vA;
        }
    });

    renderQoQTablePage();
}

function changePageSize() {
    const sel = document.getElementById('table-page-size')?.value;
    pageSize = sel === 'All' ? filteredQoQData.length : parseInt(sel, 10);
    currentPage = 1;
    renderQoQTablePage();
}

function prevPage() {
    if (currentPage > 1) {
        currentPage--;
        renderQoQTablePage();
    }
}

function nextPage() {
    const totalPages = Math.ceil(filteredQoQData.length / pageSize) || 1;
    if (currentPage < totalPages) {
        currentPage++;
        renderQoQTablePage();
    }
}

function renderQoQTablePage() {
    const tbody = document.getElementById('qoq-table-body');
    if (!tbody) return;

    const totalRows = filteredQoQData.length;
    const totalPages = Math.ceil(totalRows / pageSize) || 1;

    if (currentPage > totalPages) currentPage = totalPages;
    if (currentPage < 1) currentPage = 1;

    const startIndex = (currentPage - 1) * pageSize;
    const endIndex = pageSize >= totalRows ? totalRows : Math.min(startIndex + pageSize, totalRows);
    const pageRows = filteredQoQData.slice(startIndex, endIndex);

    // Update count & page labels
    if (document.getElementById('table-results-count')) {
        document.getElementById('table-results-count').textContent = `Showing ${totalRows > 0 ? startIndex + 1 : 0}-${endIndex} of ${totalRows} moves`;
    }
    if (document.getElementById('page-indicator')) {
        document.getElementById('page-indicator').textContent = `Page ${currentPage} of ${totalPages}`;
    }
    if (document.getElementById('prev-page-btn')) document.getElementById('prev-page-btn').disabled = (currentPage <= 1);
    if (document.getElementById('next-page-btn')) document.getElementById('next-page-btn').disabled = (currentPage >= totalPages);

    if (pageRows.length === 0) {
        tbody.innerHTML = `<tr><td colspan="12" class="text-center py-4 text-muted">No position changes match your filter criteria.</td></tr>`;
        return;
    }

    let html = '';
    pageRows.forEach(row => {
        const valChangeClass = row.value_change > 0 ? 'text-green' : (row.value_change < 0 ? 'text-red' : 'text-muted');
        const valChangeSign = row.value_change > 0 ? '+' : '';
        const pctChangeClass = row.value_change_pct > 0 ? 'text-green' : (row.value_change_pct < 0 ? 'text-red' : 'text-muted');
        const pctChangeArrow = row.value_change_pct > 0 ? '↑' : (row.value_change_pct < 0 ? '↓' : '');
        const shareActionDetail = row.status === 'NEW'
            ? 'New holding'
            : row.status === 'CLOSED'
                ? 'Full exit'
                : `${row.shares_change_pct > 0 ? '▲' : '▼'} ${formatPct(Math.abs(row.shares_change_pct))} shares`;

        html += `
            <tr>
                <td>
                    <a href="/investor/${row.cik}"><strong>${row.fund_name}</strong></a>
                    <div class="fund-card-manager">${row.manager}</div>
                </td>
                <td><span class="badge ${getGroupClass(row.group)}">${row.group}</span></td>
                <td><a href="/ticker/${row.ticker}"><strong class="font-mono">${row.ticker}</strong></a></td>
                <td>${row.issuer}</td>
                <td>
                    <div class="qoq-action-cell">
                        <span class="badge ${getStatusClass(row.status)}">${row.status}</span>
                        <small>${shareActionDetail}</small>
                    </div>
                </td>
                <td class="font-mono">${renderSparkline(row.portfolio_weight)}</td>
                <td class="font-mono">${formatNum(row.value)}</td>
                <td class="font-mono">${formatNum(row.prev_value)}</td>
                <td class="font-mono ${valChangeClass}"><strong>${valChangeSign}${formatNum(row.value_change)}</strong></td>
                <td class="font-mono ${pctChangeClass}">${pctChangeArrow} ${formatPct(row.value_change_pct)}</td>
                <td class="font-mono">${formatPct(row.shares_change_pct)}</td>
                <td class="font-mono text-dim">${row.report_period}</td>
            </tr>
        `;
    });
    tbody.innerHTML = html;
}

function renderOverviewCharts(data) {
    if (!data || data.length === 0) return;

    // Chart 1: Group move distribution
    const groupMoves = {};
    data.forEach(r => {
        if (!groupMoves[r.group]) groupMoves[r.group] = { 'NEW': 0, 'INCREASED': 0, 'DECREASED': 0, 'CLOSED': 0, 'UNCHANGED': 0 };
        if (groupMoves[r.group][r.status] !== undefined) groupMoves[r.group][r.status]++;
    });

    const groups = Object.keys(groupMoves);
    const plotData1 = [
        { name: 'NEW', x: groups, y: groups.map(g => groupMoves[g]['NEW']), type: 'bar', marker: { color: '#22c55e' } },
        { name: 'INCREASED', x: groups, y: groups.map(g => groupMoves[g]['INCREASED']), type: 'bar', marker: { color: '#06b6d4' } },
        { name: 'DECREASED', x: groups, y: groups.map(g => groupMoves[g]['DECREASED']), type: 'bar', marker: { color: '#f97316' } },
        { name: 'CLOSED', x: groups, y: groups.map(g => groupMoves[g]['CLOSED']), type: 'bar', marker: { color: '#ef4444' } },
        { name: 'UNCHANGED', x: groups, y: groups.map(g => groupMoves[g]['UNCHANGED']), type: 'bar', marker: { color: '#64748b' } },
    ];

    const layout1 = {
        barmode: 'group',
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: '#cbd5e1', family: 'Inter, sans-serif' },
        margin: { t: 20, b: 60, l: 40, r: 10 },
        legend: { orientation: 'h', y: -0.25 },
        xaxis: { tickangle: -15, gridcolor: '#1e293b' },
        yaxis: { gridcolor: '#1e293b' }
    };
    if (document.getElementById('summary-chart')) {
        Plotly.newPlot('summary-chart', plotData1, layout1, {displayModeBar: false, responsive: true});
    }

    // Chart 2: Top 10 Dollar moves ($M)
    const top10 = [...data].sort((a, b) => Math.abs(b.value_change) - Math.abs(a.value_change)).slice(0, 10).reverse();
    const plotData2 = [{
        x: top10.map(d => d.value_change),
        y: top10.map(d => `${d.ticker} (${d.manager})`),
        type: 'bar',
        orientation: 'h',
        marker: {
            color: top10.map(d => d.value_change >= 0 ? '#06b6d4' : '#f97316')
        }
    }];
    const layout2 = {
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: '#cbd5e1', family: 'Inter, sans-serif' },
        margin: { t: 20, b: 40, l: 140, r: 20 },
        xaxis: { title: 'Value Change ($M)', gridcolor: '#1e293b' },
        yaxis: { gridcolor: '#1e293b' }
    };
    if (document.getElementById('top-moves-chart')) {
        Plotly.newPlot('top-moves-chart', plotData2, layout2, {displayModeBar: false, responsive: true});
    }
}

function exportQoQToCSV() {
    if (!filteredQoQData || filteredQoQData.length === 0) {
        showToast('No data to export.');
        return;
    }
    const headers = ["Fund", "Manager", "Group", "Ticker", "Issuer", "Action", "WeightPct", "Value_M", "PrevValue_M", "Change_M", "ChangePct", "SharesChangePct", "Period"];
    const rows = filteredQoQData.map(r => [
        `"${r.fund_name}"`,
        `"${r.manager}"`,
        `"${r.group}"`,
        `"${r.ticker}"`,
        `"${r.issuer}"`,
        `"${r.status}"`,
        r.portfolio_weight,
        r.value,
        r.prev_value,
        r.value_change,
        r.value_change_pct,
        r.shares_change_pct,
        `"${r.report_period}"`
    ]);

    const csvContent = "data:text/csv;charset=utf-8," + [headers.join(","), ...rows.map(e => e.join(","))].join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `13F_QoQ_Changes_${new Date().toISOString().slice(0,10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

// ==========================================
// 2. Ticker Level Intelligence
// ==========================================

async function loadAllTickers() {
    const tbody = document.getElementById('all-tickers-table-body');
    if (!tbody) return;

    document.getElementById('all-tickers-view').style.display = 'block';
    const detailView = document.getElementById('ticker-detail-view');
    if (detailView) detailView.style.display = 'none';

    try {
        const r = await fetch('/api/ticker-view');
        const res = await r.json();
        globalAllTickers = res.data || [];
        filterAllTickersTable();
        renderPopularityCharts(globalAllTickers);
    } catch(err) {
        console.error('Error loading all tickers:', err);
    }
}

function filterAllTickersTable() {
    const q = document.getElementById('all-tickers-filter')?.value.trim().toLowerCase() || '';
    filteredAllTickers = globalAllTickers.filter(t => {
        if (!q) return true;
        return t.ticker.toLowerCase().includes(q) ||
               t.issuer.toLowerCase().includes(q) ||
               (t.holders_summary && t.holders_summary.toLowerCase().includes(q));
    });
    sortAndRenderAllTickers();
}

function sortAllTickers(e) {
    const th = e.target.closest('th');
    if (!th || !th.dataset.sort) return;
    const col = th.dataset.sort;

    if (allTickersSortCol === col) {
        allTickersSortAsc = !allTickersSortAsc;
    } else {
        allTickersSortCol = col;
        allTickersSortAsc = (col === 'ticker' || col === 'issuer');
    }
    sortAndRenderAllTickers();
}

function sortAndRenderAllTickers() {
    const tbody = document.getElementById('all-tickers-table-body');
    if (!tbody) return;

    filteredAllTickers.sort((a, b) => {
        let vA = a[allTickersSortCol];
        let vB = b[allTickersSortCol];

        if (typeof vA === 'string') {
            vA = vA.toLowerCase();
            vB = vB.toLowerCase();
            return allTickersSortAsc ? vA.localeCompare(vB) : vB.localeCompare(vA);
        } else {
            vA = Number(vA) || 0;
            vB = Number(vB) || 0;
            return allTickersSortAsc ? vA - vB : vB - vA;
        }
    });

    let html = '';
    filteredAllTickers.slice(0, 150).forEach(t => {
        html += `
            <tr onclick="window.location='/ticker/${t.ticker}'" style="cursor: pointer;">
                <td><a href="/ticker/${t.ticker}"><strong class="font-mono">${t.ticker}</strong></a></td>
                <td>${t.issuer}</td>
                <td><span class="badge ${t.num_holders >= 4 ? 'badge-group-high-perf' : 'badge-neutral'} font-mono">${t.num_holders} funds</span></td>
                <td class="font-mono"><strong>$${formatNum(t.total_value_across_funds)}</strong></td>
                <td class="font-mono">${formatPct(t.median_weight)}</td>
                <td class="text-muted" style="font-size:0.8rem">${t.holders_summary || '--'}</td>
            </tr>
        `;
    });
    tbody.innerHTML = html || `<tr><td colspan="6" class="text-center py-4 text-muted">No matching securities found.</td></tr>`;
}

function renderPopularityCharts(tickers) {
    if (!tickers || tickers.length === 0) return;
    const topByHolders = [...tickers]
        .sort((a, b) => b.num_holders - a.num_holders || b.total_value_across_funds - a.total_value_across_funds)
        .slice(0, 20)
        .reverse();
    const topByValue = [...tickers]
        .sort((a, b) => b.total_value_across_funds - a.total_value_across_funds)
        .slice(0, 20)
        .reverse();

    const holderData = [{
        x: topByHolders.map(t => t.num_holders),
        y: topByHolders.map(t => t.ticker),
        text: topByHolders.map(t => `${t.num_holders} funds`),
        textposition: 'outside',
        cliponaxis: false,
        type: 'bar',
        orientation: 'h',
        marker: { color: '#ec4899' },
        hovertemplate: '<b>%{y}</b><br>%{x} investors<extra></extra>'
    }];

    const holderLayout = {
        title: false,
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: '#cbd5e1', family: 'Inter, sans-serif' },
        margin: { t: 10, b: 40, l: 58, r: 70 },
        xaxis: {
            title: 'Number of Investors',
            dtick: 2,
            gridcolor: '#232a3b',
            zeroline: false
        },
        yaxis: { gridcolor: 'rgba(0,0,0,0)' }
    };
    if (document.getElementById('popularity-chart')) {
        Plotly.newPlot('popularity-chart', holderData, holderLayout, {displayModeBar: false, responsive: true});
    }

    const valueData = [{
        x: topByValue.map(t => t.total_value_across_funds),
        y: topByValue.map(t => t.ticker),
        text: topByValue.map(t => `$${formatNum(t.total_value_across_funds)}M`),
        textposition: 'outside',
        cliponaxis: false,
        type: 'bar',
        orientation: 'h',
        marker: { color: '#06b6d4' },
        hovertemplate: '<b>%{y}</b><br>$%{x:,.2f}M tracked<extra></extra>'
    }];

    const valueLayout = {
        title: false,
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: '#cbd5e1', family: 'Inter, sans-serif' },
        margin: { t: 10, b: 40, l: 58, r: 105 },
        xaxis: {
            title: 'Tracked Position Value ($M)',
            gridcolor: '#232a3b',
            zeroline: false,
            tickformat: '~s'
        },
        yaxis: { gridcolor: 'rgba(0,0,0,0)' }
    };
    if (document.getElementById('value-chart')) {
        Plotly.newPlot('value-chart', valueData, valueLayout, {displayModeBar: false, responsive: true});
    }
}

function formatCompactCurrency(value) {
    if (value === null || value === undefined || isNaN(value)) return '—';
    return new Intl.NumberFormat(undefined, {
        style: 'currency',
        currency: 'USD',
        notation: 'compact',
        maximumFractionDigits: 2
    }).format(Number(value));
}

function formatFlowMillions(value, includeSign = false) {
    if (value === null || value === undefined || isNaN(value)) return '—';
    const number = Number(value);
    const sign = number < 0 ? '-' : includeSign && number > 0 ? '+' : '';
    const absoluteValue = Math.abs(number);
    if (absoluteValue >= 1000) {
        return `${sign}$${formatNum(absoluteValue / 1000)}B`;
    }
    return `${sign}$${formatNum(absoluteValue)}M`;
}

function renderTradingViewChart(symbol) {
    const container = document.getElementById('ticker-tradingview-chart');
    if (!container) return;

    container.innerHTML = `
        <div class="tradingview-widget-container">
            <div class="tradingview-widget-container__widget"></div>
        </div>
    `;
    const widgetContainer = container.querySelector('.tradingview-widget-container');
    const script = document.createElement('script');
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
    script.async = true;
    script.textContent = JSON.stringify({
        autosize: true,
        symbol,
        interval: 'D',
        timezone: 'exchange',
        theme: 'dark',
        style: '1',
        locale: 'en',
        allow_symbol_change: false,
        calendar: false,
        support_host: 'https://www.tradingview.com',
        withdateranges: true,
        range: '12M',
        hide_side_toolbar: false,
        details: true,
        hotlist: false
    });
    widgetContainer.appendChild(script);
}

function renderTickerHistoryCharts(history) {
    if (!history?.length) return;

    const periods = history.map(item => item.period);
    const commonLayout = {
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: '#cbd5e1', family: 'Inter, sans-serif' },
        margin: { t: 15, b: 48, l: 52, r: 24 },
        hovermode: 'x unified',
        xaxis: { gridcolor: '#1e293b', tickangle: -35 },
        yaxis: { gridcolor: '#1e293b', rangemode: 'tozero' },
        showlegend: false
    };
    const plotConfig = {displayModeBar: false, responsive: true};

    Plotly.newPlot('ticker-investor-history-chart', [{
        x: periods,
        y: history.map(item => item.investor_count),
        type: 'scatter',
        mode: 'lines+markers',
        line: {color: '#60a5fa', width: 3},
        marker: {color: '#93c5fd', size: 7},
        fill: 'tozeroy',
        fillcolor: 'rgba(59, 130, 246, 0.12)',
        hovertemplate: '%{y} tracked investors<extra></extra>'
    }], {
        ...commonLayout,
        yaxis: {...commonLayout.yaxis, title: 'Investors', dtick: 1}
    }, plotConfig);

    Plotly.newPlot('ticker-value-history-chart', [{
        x: periods,
        y: history.map(item => item.total_value),
        type: 'scatter',
        mode: 'lines+markers',
        line: {color: '#22d3ee', width: 3},
        marker: {color: '#67e8f9', size: 7},
        fill: 'tozeroy',
        fillcolor: 'rgba(6, 182, 212, 0.11)',
        hovertemplate: '$%{y:,.2f}M tracked value<extra></extra>'
    }], {
        ...commonLayout,
        yaxis: {...commonLayout.yaxis, title: 'Reported Value ($M)', tickformat: '~s'}
    }, plotConfig);

}

function sentimentColor(regime) {
    if (regime?.includes('BULLISH')) return '#22c55e';
    if (regime?.includes('BEARISH')) return '#ef4444';
    if (regime === 'NEUTRAL') return '#f59e0b';
    return '#64748b';
}

function formatSignedScore(value) {
    if (value === null || value === undefined || isNaN(value)) return '—';
    const number = Number(value);
    return `${number > 0 ? '+' : ''}${number.toFixed(1)}`;
}

function renderWhaleContributors(containerId, contributors) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!contributors?.length) {
        container.innerHTML = '<li class="stats-loading">No directional weight changes.</li>';
        return;
    }

    container.innerHTML = contributors.map(item => {
        const detail = item.conviction_basis === 'SHARE_CHANGE'
            ? `${item.status.replace('_', ' ')} · shares ${item.share_change_pct > 0 ? '+' : ''}${formatPct(item.share_change_pct)} vs ${formatPct(item.typical_share_change_pct)} normal`
            : `${item.status.replace('_', ' ')} · position ${formatPct(Math.max(item.previous_weight, item.current_weight))} vs ${formatPct(item.typical_position_weight)} normal`;
        return `
        <li>
            <a href="/investor/${item.cik}">
                <span>
                    <strong>${item.manager}</strong>
                    <small>${detail}</small>
                </span>
                <strong class="font-mono ${item.scored_relative_conviction > 0 ? 'text-green' : 'text-red'}">
                    ${item.scored_relative_conviction > 0 ? '+' : ''}${Number(item.scored_relative_conviction).toFixed(2)}x
                </strong>
            </a>
        </li>
    `;
    }).join('');
}

function resetWhaleSentimentView(message = 'Loading sentiment history...') {
    const regime = document.getElementById('td-whale-sentiment-regime');
    if (regime) {
        regime.textContent = 'LOADING';
        regime.className = 'whale-sentiment-regime';
    }
    [
        'td-whale-sentiment-score',
        'td-whale-sentiment-delta',
        'td-whale-activity',
        'td-whale-activity-counts',
        'td-whale-breadth',
        'td-whale-breadth-counts',
        'td-whale-conviction',
        'td-whale-conviction-pp',
        'td-whale-streak',
        'td-whale-flow-confirmation',
        'td-whale-flow-value'
    ].forEach(id => {
        const element = document.getElementById(id);
        if (element) element.textContent = '—';
    });
    const cap = document.getElementById('td-whale-winsor-cap');
    if (cap) cap.textContent = 'Routine <0.25x · Cap 2x';
    ['ticker-bullish-contributors', 'ticker-bearish-contributors'].forEach(id => {
        const container = document.getElementById(id);
        if (container) container.innerHTML = `<li class="stats-loading">${message}</li>`;
    });
    ['ticker-sentiment-history-chart', 'ticker-conviction-heatmap'].forEach(id => {
        const chart = document.getElementById(id);
        if (chart && chart.data) Plotly.purge(chart);
    });
}

function showWhaleSentimentUnavailable(message) {
    resetWhaleSentimentView(message);
    const regime = document.getElementById('td-whale-sentiment-regime');
    if (regime) regime.textContent = 'UNAVAILABLE';
}

function renderWhaleSentiment(intelligence) {
    const history = intelligence.history || [];
    const sentimentData = intelligence.sentiment || {};
    const latest = sentimentData.latest || {};
    const regime = latest.regime || 'NO SIGNAL';
    const regimeElement = document.getElementById('td-whale-sentiment-regime');
    regimeElement.textContent = regime;
    regimeElement.className = `whale-sentiment-regime ${regime.toLowerCase().replaceAll(' ', '-')}`;

    document.getElementById('td-whale-sentiment-score').textContent = formatSignedScore(latest.score);
    document.getElementById('td-whale-sentiment-delta').textContent = latest.score_change === null || latest.score_change === undefined
        ? 'No prior comparison'
        : `${formatSignedScore(latest.score_change)} vs prior quarter`;
    const hasPublishedScore = latest.score !== null && latest.score !== undefined;
    document.getElementById('td-whale-activity').textContent = formatSignedScore(latest.activity_breadth_score);
    document.getElementById('td-whale-activity-counts').textContent = `${latest.activity_bullish_count || 0} buy actions / ${latest.activity_bearish_count || 0} sell actions`;
    document.getElementById('td-whale-breadth').textContent = hasPublishedScore
        ? formatSignedScore(latest.breadth_score)
        : '—';
    document.getElementById('td-whale-breadth-counts').textContent = `${latest.bullish_count || 0} meaningful bull / ${latest.bearish_count || 0} meaningful bear / ${latest.routine_count || 0} routine / ${latest.unscored_count || 0} unavailable`;
    document.getElementById('td-whale-conviction').textContent = hasPublishedScore
        ? formatSignedScore(latest.conviction_score)
        : '—';
    document.getElementById('td-whale-conviction-pp').textContent = !hasPublishedScore
        ? 'Insufficient participation'
        : `+${formatNum(latest.positive_conviction_x || 0)}x / -${formatNum(latest.negative_conviction_x || 0)}x typical`;
    document.getElementById('td-whale-streak').textContent = latest.regime_streak ? `${latest.regime_streak}Q` : '—';

    const flowConfirmation = document.getElementById('td-whale-flow-confirmation');
    flowConfirmation.textContent = latest.flow_confirmation || 'NEUTRAL';
    flowConfirmation.className = `flow-confirmation ${(latest.flow_confirmation || 'neutral').toLowerCase()}`;
    document.getElementById('td-whale-flow-value').textContent = history.length && history.at(-1).net_flow !== null
        ? `${formatFlowMillions(history.at(-1).net_flow, true)} estimated net flow`
        : 'Flow unavailable';
    document.getElementById('td-whale-winsor-cap').textContent = `Routine <${Number(sentimentData.materiality_threshold_x || 0.25).toFixed(2)}x · Cap ${Number(sentimentData.conviction_cap_x || 2).toFixed(0)}x`;

    const periods = history.map(item => item.period);
    const customData = history.map(item => {
        const sentiment = item.sentiment || {};
        return [
            sentiment.regime || 'NO SIGNAL',
            sentiment.activity_bullish_count || 0,
            sentiment.activity_bearish_count || 0,
            sentiment.bullish_count || 0,
            sentiment.bearish_count || 0,
            sentiment.routine_count || 0,
            sentiment.positive_conviction_x || 0,
            sentiment.negative_conviction_x || 0,
            item.net_flow === null
                ? 'Flow unavailable'
                : formatFlowMillions(item.net_flow, true),
            sentiment.flow_confirmation || 'NEUTRAL'
        ];
    });
    const scoreValues = history.map(item => item.sentiment?.score ?? null);
    const indicativeValues = history.map(
        item => item.sentiment?.indicative_score ?? null
    );
    const lowParticipationValues = history.map(item => {
        const sentiment = item.sentiment || {};
        if (
            sentiment.score !== null
            && sentiment.score !== undefined
        ) {
            return null;
        }
        if (
            sentiment.breadth_score === null
            || sentiment.breadth_score === undefined
            || sentiment.conviction_score === null
            || sentiment.conviction_score === undefined
        ) {
            return null;
        }
        return sentiment.indicative_score ?? null;
    });

    Plotly.react('ticker-sentiment-history-chart', [
        {
            x: periods,
            y: indicativeValues,
            name: 'INDICATIVE TREND',
            type: 'scatter',
            mode: 'lines',
            connectgaps: true,
            line: {
                color: '#94a3b8',
                width: 2,
                dash: 'dash'
            },
            hovertemplate:
                'Indicative score: %{y:.1f}<br>' +
                'Low-sample quarters are not validated' +
                '<extra></extra>'
        },
        {
            x: periods,
            y: scoreValues,
            name: 'VALIDATED SENTIMENT',
            type: 'scatter',
            mode: 'lines+markers',
            line: {color: '#e2e8f0', width: 4},
            marker: {
                size: 9,
                color: history.map(item => sentimentColor(item.sentiment?.regime)),
                line: {color: '#0f172a', width: 1}
            },
            customdata: customData,
            hovertemplate:
                '<b>%{x}</b><br>' +
                'Validated sentiment: %{y:.1f} (%{customdata[0]})<br>' +
                'Raw actions: %{customdata[1]} buys / %{customdata[2]} sells<br>' +
                'Meaningful: %{customdata[3]} bullish / %{customdata[4]} bearish / %{customdata[5]} routine<br>' +
                'Relative conviction: +%{customdata[6]:.2f}x / -%{customdata[7]:.2f}x typical<br>' +
                'Estimated net flow: %{customdata[8]} (%{customdata[9]})' +
                '<extra></extra>'
        },
        {
            x: periods,
            y: history.map(item => (
                item.sentiment?.score !== null
                && item.sentiment?.score !== undefined
                    ? item.sentiment?.breadth_score
                    : null
            )),
            name: 'MEANINGFUL BREADTH',
            type: 'scatter',
            mode: 'lines',
            line: {color: '#60a5fa', width: 1.5, dash: 'dot'},
            hovertemplate: 'Meaningful breadth: %{y:.1f}<extra></extra>',
            visible: 'legendonly'
        },
        {
            x: periods,
            y: history.map(item => (
                item.sentiment?.score !== null
                && item.sentiment?.score !== undefined
                    ? item.sentiment?.conviction_score
                    : null
            )),
            name: 'RELATIVE CONVICTION',
            type: 'scatter',
            mode: 'lines',
            line: {color: '#c084fc', width: 1.5, dash: 'dot'},
            hovertemplate: 'Relative conviction: %{y:.1f}<extra></extra>',
            visible: 'legendonly'
        },
        {
            x: periods,
            y: history.map(item => item.sentiment?.activity_breadth_score ?? null),
            name: 'RAW ACTIVITY',
            type: 'scatter',
            mode: 'lines',
            line: {color: '#64748b', width: 1.2, dash: 'dash'},
            hovertemplate: 'Raw activity breadth: %{y:.1f}<extra></extra>',
            visible: 'legendonly'
        },
        {
            x: periods,
            y: lowParticipationValues,
            name: 'LOW PARTICIPATION',
            type: 'scatter',
            mode: 'markers',
            marker: {
                symbol: 'diamond-open',
                size: 9,
                color: '#94a3b8',
                line: {color: '#94a3b8', width: 1.5}
            },
            customdata: history.map(item => [
                item.sentiment?.bullish_count || 0,
                item.sentiment?.bearish_count || 0,
                item.sentiment?.routine_count || 0
            ]),
            hovertemplate:
                '<b>%{x}</b><br>' +
                'Unscored indication: %{y:.1f}<br>' +
                '%{customdata[0]} meaningful bull / %{customdata[1]} meaningful bear / %{customdata[2]} routine<br>' +
                'Fewer than 3 meaningful managers' +
                '<extra></extra>'
        }
    ], {
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: {color: '#cbd5e1', family: 'Inter, sans-serif'},
        margin: {t: 20, b: 62, l: 58, r: 28},
        hovermode: 'x unified',
        legend: {orientation: 'h', y: -0.18},
        xaxis: {gridcolor: '#1e293b', tickangle: -35},
        yaxis: {
            title: 'Sentiment (-100 to +100)',
            range: [-110, 110],
            gridcolor: '#1e293b',
            zeroline: true,
            zerolinecolor: '#64748b'
        },
        shapes: [
            {type: 'rect', xref: 'paper', x0: 0, x1: 1, y0: 25, y1: 100, fillcolor: 'rgba(34,197,94,0.055)', line: {width: 0}, layer: 'below'},
            {type: 'rect', xref: 'paper', x0: 0, x1: 1, y0: -25, y1: 25, fillcolor: 'rgba(148,163,184,0.035)', line: {width: 0}, layer: 'below'},
            {type: 'rect', xref: 'paper', x0: 0, x1: 1, y0: -100, y1: -25, fillcolor: 'rgba(239,68,68,0.055)', line: {width: 0}, layer: 'below'},
            {type: 'line', xref: 'paper', x0: 0, x1: 1, y0: 25, y1: 25, line: {color: 'rgba(34,197,94,0.35)', dash: 'dot'}},
            {type: 'line', xref: 'paper', x0: 0, x1: 1, y0: -25, y1: -25, line: {color: 'rgba(239,68,68,0.35)', dash: 'dot'}}
        ]
    }, {displayModeBar: false, responsive: true});

    const managerNames = [...new Set(
        history.flatMap(item => item.investor_changes.map(change => change.manager))
    )];
    const latestChanges = new Map(
        (history.at(-1)?.investor_changes || []).map(change => [change.manager, Math.abs(change.relative_conviction || 0)])
    );
    managerNames.sort((a, b) => (latestChanges.get(b) || 0) - (latestChanges.get(a) || 0) || a.localeCompare(b));

    const changeMaps = history.map(item => new Map(
        item.investor_changes.map(change => [change.manager, change])
    ));
    const directionalStatuses = new Set(['NEW', 'INCREASED', 'DECREASED', 'CLOSED']);
    const zValues = managerNames.map(manager => changeMaps.map(map => {
        const change = map.get(manager);
        return change
            && directionalStatuses.has(change.status)
            && change.scored_relative_conviction !== null
            && change.scored_relative_conviction !== undefined
            ? change.scored_relative_conviction
            : null;
    }));
    const heatmapCustom = managerNames.map(manager => changeMaps.map(map => {
        const change = map.get(manager);
        if (!change) return ['NO DATA', '—', '—', '—', 'No directional observation'];
        const operation = change.conviction_basis === 'SHARE_CHANGE'
            ? change.share_change_pct === null
                ? 'Unavailable'
                : `${change.share_change_pct > 0 ? '+' : ''}${Number(change.share_change_pct).toFixed(2)}% shares`
            : `${Number(Math.max(change.previous_weight, change.current_weight)).toFixed(2)}% position`;
        const normal = change.conviction_basis === 'SHARE_CHANGE'
            ? change.typical_share_change_pct === null
                ? 'Unavailable'
                : `${Number(change.typical_share_change_pct).toFixed(2)}% share adjustment`
            : change.typical_position_weight === null
                ? 'Unavailable'
                : `${Number(change.typical_position_weight).toFixed(2)}% position`;
        return [
            change.status,
            operation,
            normal,
            change.relative_conviction === null
                ? 'Unavailable'
                : `${change.relative_conviction > 0 ? '+' : ''}${Number(change.relative_conviction).toFixed(2)}x`,
            change.position_size_gate_applied
                ? 'ROUTINE (position <0.25x normal size)'
                : change.conviction_class
        ];
    }));
    const cap = Number(sentimentData.conviction_cap_x || 2);
    const heatmap = document.getElementById('ticker-conviction-heatmap');
    heatmap.style.height = `${Math.max(480, managerNames.length * 25 + 100)}px`;

    Plotly.react('ticker-conviction-heatmap', [{
        x: periods,
        y: managerNames,
        z: zValues,
        customdata: heatmapCustom,
        type: 'heatmap',
        zmid: 0,
        zmin: -cap,
        zmax: cap,
        colorscale: [
            [0, '#b91c1c'],
            [0.35, '#7f1d1d'],
            [0.5, '#1e293b'],
            [0.65, '#166534'],
            [1, '#22c55e']
        ],
        colorbar: {title: 'Operation / Normal', thickness: 13},
        hovertemplate:
            '<b>%{y}</b><br>%{x}<br>' +
            'Action: %{customdata[0]}<br>' +
            'Reported operation: %{customdata[1]}<br>' +
            'Manager normal: %{customdata[2]}<br>' +
            'Relative conviction: %{customdata[3]}<br>' +
            'Classification: %{customdata[4]}' +
            '<extra></extra>'
    }], {
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: {color: '#cbd5e1', family: 'Inter, sans-serif', size: 10},
        margin: {t: 16, b: 64, l: 145, r: 72},
        xaxis: {tickangle: -35, gridcolor: '#1e293b'},
        yaxis: {automargin: false}
    }, {displayModeBar: false, responsive: true});

    renderWhaleContributors('ticker-bullish-contributors', sentimentData.bullish_contributors);
    renderWhaleContributors('ticker-bearish-contributors', sentimentData.bearish_contributors);
}

function renderTickerIntelligence(intelligence) {
    const market = intelligence.market || {};
    const quote = market.quote || {};
    const metrics = market.metrics || {};
    const profile = market.profile || {};
    const valuation = market.valuation || {};
    const technical = market.technical || {};
    const decision = intelligence.decision_support || {};
    const latest = intelligence.latest || {};
    const actions = latest.actions || {};
    const currentPrice = quote.last_price;
    const dayChange = quote.day_change;
    const dayChangePct = quote.day_change_pct;

    document.getElementById('td-current-price').textContent = (
        currentPrice !== null && currentPrice !== undefined
            ? `$${formatNum(currentPrice)}`
            : '—'
    );
    const dayChangeElement = document.getElementById('td-day-change');
    if (dayChangeElement) {
        const dayClass = dayChange > 0 ? 'text-green' : dayChange < 0 ? 'text-red' : 'text-dim';
        const sign = dayChange > 0 ? '+' : '';
        dayChangeElement.className = `ticker-price-change font-mono ${dayClass}`;
        dayChangeElement.textContent = (
            dayChange !== null && dayChange !== undefined
                ? `${dayChange < 0 ? '-' : sign}$${formatNum(Math.abs(dayChange))} (${sign}${formatPct(dayChangePct)}) today`
                : 'Daily change unavailable'
        );
    }

    document.getElementById('td-sector').textContent = profile.sector || 'Sector unavailable';
    document.getElementById('td-industry').textContent = profile.industry_category || 'Industry unavailable';
    document.getElementById('td-exchange').textContent = quote.exchange || profile.stock_exchange || 'Exchange unavailable';
    document.getElementById('td-market-cap').textContent = formatCompactCurrency(metrics.market_cap || profile.market_cap);
    document.getElementById('td-pe').textContent = metrics.pe_ratio ? Number(metrics.pe_ratio).toFixed(1) : '—';
    document.getElementById('td-forward-pe').textContent = metrics.forward_pe ? Number(metrics.forward_pe).toFixed(1) : '—';
    document.getElementById('td-pe-5y').textContent = valuation.average_pe_5y
        ? `${Number(valuation.average_pe_5y).toFixed(1)}x (${valuation.average_pe_observations || 0} FY)`
        : '—';
    document.getElementById('td-52w-range').textContent = (
        quote.year_low && quote.year_high
            ? `$${formatNum(quote.year_low)} – $${formatNum(quote.year_high)}`
            : '—'
    );
    document.getElementById('td-beta').textContent = metrics.beta ? Number(metrics.beta).toFixed(2) : '—';
    const oneYearReturn = metrics.price_return_1y;
    const oneYearElement = document.getElementById('td-1y-return');
    oneYearElement.textContent = oneYearReturn !== null && oneYearReturn !== undefined
        ? formatPct(Number(oneYearReturn) * 100)
        : '—';
    oneYearElement.className = `font-mono ${oneYearReturn > 0 ? 'text-green' : oneYearReturn < 0 ? 'text-red' : ''}`;
    document.getElementById('td-eps-growth').textContent = (
        valuation.eps_growth_cagr_pct !== null
        && valuation.eps_growth_cagr_pct !== undefined
            ? `${formatPct(valuation.eps_growth_cagr_pct)} (${formatPct(valuation.growth_cap_pct)} cap)`
            : '—'
    );

    const basis = intelligence.estimated_whale_basis;
    document.getElementById('td-whale-basis').textContent = basis ? `$${formatNum(basis)}` : '—';
    const basisGap = intelligence.price_vs_estimated_basis_pct;
    const basisGapElement = document.getElementById('td-basis-gap');
    if (basisGapElement) {
        basisGapElement.className = `ticker-basis-gap font-mono ${basisGap > 0 ? 'text-red' : basisGap < 0 ? 'text-green' : 'text-dim'}`;
        basisGapElement.textContent = basisGap !== null && basisGap !== undefined
            ? `${basisGap > 0 ? '+' : ''}${formatPct(basisGap)} vs estimate`
            : '20-quarter model unavailable';
    }

    document.getElementById('td-qoq-period').textContent = latest.period || 'Latest quarter';
    document.getElementById('td-filing-period').textContent = latest.period
        ? `Filing period ${latest.period}`
        : 'Filing period unavailable';
    document.getElementById('td-qoq-new').textContent = formatInt(actions.new);
    document.getElementById('td-qoq-increased').textContent = formatInt(actions.increased);
    document.getElementById('td-qoq-decreased').textContent = formatInt(actions.decreased);
    document.getElementById('td-qoq-closed').textContent = formatInt(actions.closed);

    const netFlow = Number(latest.net_flow) || 0;
    const netFlowElement = document.getElementById('td-net-flow');
    netFlowElement.className = `ticker-net-flow font-mono ${netFlow > 0 ? 'text-green' : netFlow < 0 ? 'text-red' : 'text-dim'}`;
    netFlowElement.textContent = `${netFlow > 0 ? '+' : ''}${formatFlowMillions(netFlow)}`;
    document.getElementById('td-gross-inflow').textContent = formatFlowMillions(latest.gross_inflow);
    document.getElementById('td-gross-outflow').textContent = formatFlowMillions(latest.gross_outflow);

    const valuationStatus = document.getElementById('td-valuation-status');
    const valuationState = (valuation.assessment || 'UNAVAILABLE').toLowerCase();
    valuationStatus.className = `decision-status ${valuationState}`;
    valuationStatus.textContent = valuation.assessment || 'UNAVAILABLE';
    document.getElementById('td-fair-value').textContent = valuation.fair_value ? `$${formatNum(valuation.fair_value)}` : '—';
    document.getElementById('td-purchase-price').textContent = valuation.purchase_price_20pct_mos ? `$${formatNum(valuation.purchase_price_20pct_mos)}` : '—';
    document.getElementById('td-graham-number').textContent = valuation.graham_number ? `$${formatNum(valuation.graham_number)}` : '—';
    document.getElementById('td-graham-conservative').textContent = valuation.graham_conservative_value ? `$${formatNum(valuation.graham_conservative_value)}` : '—';
    document.getElementById('td-normalized-pe-value').textContent = valuation.normalized_pe_value ? `$${formatNum(valuation.normalized_pe_value)}` : '—';

    const trendStatus = document.getElementById('td-trend-status');
    const trendState = (technical.trend_regime || 'UNAVAILABLE').toLowerCase();
    trendStatus.className = `decision-status ${trendState}`;
    trendStatus.textContent = `TREND: ${technical.trend_regime || 'UNAVAILABLE'}`;
    document.getElementById('td-entry-timing').textContent = technical.entry_timing || 'UNAVAILABLE';
    document.getElementById('td-rsi14').textContent = technical.rsi_14 !== null && technical.rsi_14 !== undefined ? Number(technical.rsi_14).toFixed(1) : '—';
    document.getElementById('td-rsi2').textContent = technical.rsi_2 !== null && technical.rsi_2 !== undefined ? Number(technical.rsi_2).toFixed(1) : '—';
    document.getElementById('td-sma50-distance').textContent = technical.distance_from_sma_50_pct !== null && technical.distance_from_sma_50_pct !== undefined ? `${technical.distance_from_sma_50_pct > 0 ? '+' : ''}${formatPct(technical.distance_from_sma_50_pct)}` : '—';
    document.getElementById('td-sma200-distance').textContent = technical.distance_from_sma_200_pct !== null && technical.distance_from_sma_200_pct !== undefined ? `${technical.distance_from_sma_200_pct > 0 ? '+' : ''}${formatPct(technical.distance_from_sma_200_pct)}` : '—';
    document.getElementById('td-momentum-6m').textContent = technical.momentum_6m_pct !== null && technical.momentum_6m_pct !== undefined ? `${technical.momentum_6m_pct > 0 ? '+' : ''}${formatPct(technical.momentum_6m_pct)}` : '—';
    document.getElementById('td-volatility').textContent = technical.annualized_volatility_pct !== null && technical.annualized_volatility_pct !== undefined ? formatPct(technical.annualized_volatility_pct) : '—';

    document.getElementById('td-model-stance').textContent = decision.stance || 'UNAVAILABLE';
    const equityRange = decision.equity_sleeve_range_pct || [];
    const totalRange = decision.all_weather_total_portfolio_range_pct || [];
    document.getElementById('td-equity-sleeve-range').textContent = equityRange.length === 2
        ? `${equityRange[0].toFixed(2)}% – ${equityRange[1].toFixed(2)}%`
        : '—';
    document.getElementById('td-total-portfolio-range').textContent = totalRange.length === 2
        ? `${totalRange[0].toFixed(2)}% – ${totalRange[1].toFixed(2)}%`
        : '—';

    const chartSymbol = market.tradingview_symbol || intelligence.ticker;
    document.getElementById('td-chart-symbol').textContent = chartSymbol;
    renderTradingViewChart(chartSymbol);
    renderTickerHistoryCharts(intelligence.history || []);
    renderWhaleSentiment(intelligence);
}

function renderPairSignal(pair) {
    const best = pair.best_pair || {};
    const statusElement = document.getElementById('td-pair-status');
    const statusClass = pair.status === 'READY'
        ? 'undervalued'
        : pair.status === 'WAIT'
            ? 'neutral'
            : 'overvalued';
    statusElement.className = `decision-status ${statusClass}`;
    statusElement.textContent = pair.status?.replaceAll('_', ' ') || 'UNAVAILABLE';

    document.getElementById('td-pair-focal').textContent = pair.ticker || '—';
    const peerElement = document.getElementById('td-pair-peer');
    peerElement.textContent = best.peer || 'No valid peer';
    peerElement.href = best.peer ? `/ticker/${best.peer}` : '/ticker';
    const pairTypeLabels = {
        SHARE_CLASS_RELATIVE_VALUE: 'Share-class relative value',
        SAME_INDUSTRY_COINTEGRATION: 'Validated same-industry cointegration',
        SAME_INDUSTRY_CANDIDATE: 'Same-industry candidate'
    };
    document.getElementById('td-pair-type').textContent = pairTypeLabels[pair.pair_type] || 'No validated pair type';
    document.getElementById('td-pair-action').textContent = pair.action || 'Pair signal unavailable';
    const observed = pair.observed_cheap_leg && pair.observed_expensive_leg
        ? `Raw spread as of ${best.as_of || 'the latest common close'} prices ${pair.observed_cheap_leg} as the cheaper leg versus ${pair.observed_expensive_leg}.`
        : 'No relative direction is available.';
    document.getElementById('td-pair-observation').textContent = pair.ready
        ? `${observed} Entry threshold is active.`
        : `${observed} Do not act unless the statistical gates also pass.`;

    document.getElementById('td-pair-zscore').textContent = best.zscore !== undefined ? Number(best.zscore).toFixed(2) : '—';
    document.getElementById('td-pair-quality').textContent = best.quality_score !== undefined ? `${(Number(best.quality_score) * 100).toFixed(1)}%` : '—';
    document.getElementById('td-pair-pvalue').textContent = best.eg_pvalue !== undefined ? Number(best.eg_pvalue).toFixed(4) : '—';
    document.getElementById('td-pair-oos').textContent = best.oos_passes === true ? 'PASS' : best.oos_passes === false ? 'FAIL' : '—';
    document.getElementById('td-pair-half-life').textContent = best.half_life_days ? `${Number(best.half_life_days).toFixed(1)} days` : '—';
    document.getElementById('td-pair-stability').textContent = best.subwindow_pass_count !== undefined ? `${best.subwindow_pass_count}/2 windows` : '—';
    document.getElementById('td-pair-correlation').textContent = best.correlation !== undefined ? Number(best.correlation).toFixed(3) : '—';
    document.getElementById('td-pair-hedge-ratio').textContent = best.hedge_ratio !== undefined ? Number(best.hedge_ratio).toFixed(3) : '—';
    const inactiveReason = pair.status === 'WAIT'
        ? 'Not actionable — entry threshold not reached'
        : 'Inactive — statistical gates failed';
    document.getElementById('td-pair-stock-execution').textContent = pair.stock_execution || inactiveReason;
    document.getElementById('td-pair-put-execution').textContent = pair.put_execution || inactiveReason;
}

function showTickerLoader(ticker) {
    const loader = document.getElementById('ticker-loader');
    if (!loader) return;
    loader.hidden = false;
    document.body.classList.add('period-loading');
    document.getElementById('ticker-loader-title').textContent = `Loading ${ticker.toUpperCase()} intelligence`;
    updateTickerLoader(0, 'Preparing the latest 13F ownership snapshot...', 'Alpha Whales');
}

function updateTickerLoader(stage, message, source) {
    const percent = [8, 34, 72, 100][stage] || 8;
    document.getElementById('ticker-loader-progress-bar').style.width = `${percent}%`;
    document.getElementById('ticker-loader-message').textContent = message;
    document.getElementById('ticker-loader-stage').textContent = stage === 0 ? 'Starting' : `Stage ${stage} of 3`;
    document.getElementById('ticker-loader-source').textContent = source;
}

function hideTickerLoader() {
    const loader = document.getElementById('ticker-loader');
    if (loader) loader.hidden = true;
    document.body.classList.remove('period-loading');
}

async function loadTickerDetail(ticker) {
    const detailView = document.getElementById('ticker-detail-view');
    const allView = document.getElementById('all-tickers-view');
    if (detailView) detailView.style.display = 'block';
    if (allView) allView.style.display = 'block';
    resetWhaleSentimentView();
    const loaderStartedAt = performance.now();
    showTickerLoader(ticker);

    try {
        const r = await fetch(`/api/ticker/${encodeURIComponent(ticker)}`);
        updateTickerLoader(1, '13F holdings loaded. Building market and valuation intelligence...', 'SEC filings');
        if (r.status === 404) {
            document.getElementById('td-ticker').textContent = ticker.toUpperCase();
            document.getElementById('td-issuer').textContent = 'Not held by tracked 26 funds this quarter';
            document.getElementById('td-holders-count').textContent = '0';
            document.getElementById('td-total-value').textContent = '$0.00 M';
            document.getElementById('td-total-shares').textContent = '0';
            document.getElementById('td-median-weight').textContent = '0.00%';
            document.getElementById('holders-table-body').innerHTML = `<tr><td colspan="10" class="text-center py-4 text-muted">No positions in ${ticker.toUpperCase()} found among the 26 elite managers.</td></tr>`;
            showWhaleSentimentUnavailable('No tracked filing history for this ticker.');
            return;
        }

        const intelligenceRequest = fetch(
            `/api/ticker/${encodeURIComponent(ticker)}/intelligence`
        );
        const pairRequest = fetch(
            `/api/ticker/${encodeURIComponent(ticker)}/pair-signal`
        );
        const {data} = await r.json();

        document.getElementById('td-ticker').textContent = data.ticker;
        document.getElementById('td-issuer').textContent = data.issuer;
        document.getElementById('td-holders-summary').textContent = `Owned across ${data.num_holders} tracked portfolio${data.num_holders > 1 ? 's' : ''}`;
        document.getElementById('td-holders-count').textContent = data.num_holders;
        document.getElementById('td-total-value').textContent = `$${formatNum(data.total_value_across_funds)} M`;
        document.getElementById('td-total-shares').textContent = formatInt(data.total_shares);
        document.getElementById('td-median-weight').textContent = formatPct(data.median_weight);
        if (document.getElementById('td-table-ticker')) document.getElementById('td-table-ticker').textContent = data.ticker;

        const intelligenceResponse = await intelligenceRequest;
        if (intelligenceResponse.ok) {
            const intelligenceResult = await intelligenceResponse.json();
            renderTickerIntelligence(intelligenceResult.data);
            updateTickerLoader(2, 'Market, valuation, and 20-quarter trends loaded. Testing economic pairs...', 'OpenBB + historical cache');
        } else {
            const intelligenceError = await intelligenceResponse.json().catch(() => ({}));
            console.error('Ticker intelligence unavailable:', intelligenceError);
            document.getElementById('td-day-change').textContent = 'Market intelligence unavailable';
            document.getElementById('ticker-tradingview-chart').innerHTML = (
                '<div class="stats-loading">Market chart unavailable for this ticker.</div>'
            );
            showWhaleSentimentUnavailable('Ticker sentiment intelligence is unavailable.');
        }

        const pairResponse = await pairRequest;
        if (pairResponse.ok) {
            const pairResult = await pairResponse.json();
            renderPairSignal(pairResult.data);
        } else {
            const pairError = await pairResponse.json().catch(() => ({}));
            console.error('Pair signal unavailable:', pairError);
            renderPairSignal({
                ticker: data.ticker,
                status: 'UNAVAILABLE',
                action: 'Pair analysis unavailable for this ticker.'
            });
        }
        updateTickerLoader(3, 'Ticker intelligence ready.', 'Alpha Whales');

        // Render Holders Table
        let html = '';
        data.holders.sort((a,b) => b.value - a.value).forEach(h => {
            const valChangeClass = h.value_change > 0 ? 'text-green' : (h.value_change < 0 ? 'text-red' : 'text-muted');
            const pctChangeClass = h.value_change_pct > 0 ? 'text-green' : (h.value_change_pct < 0 ? 'text-red' : 'text-muted');

            html += `
                <tr>
                    <td>
                        <a href="/investor/${h.cik}"><strong>${h.fund_name}</strong></a>
                        <div class="fund-card-manager">${h.manager}</div>
                    </td>
                    <td><span class="badge ${getGroupClass(h.group)}">${h.group}</span></td>
                    <td class="text-dim" style="font-size:0.75rem">${h.annotation || '--'}</td>
                    <td class="font-mono">${renderSparkline(h.portfolio_weight)}</td>
                    <td class="font-mono"><strong>$${formatNum(h.value)}</strong></td>
                    <td class="font-mono">${formatInt(h.shares)}</td>
                    <td><span class="badge ${getStatusClass(h.status)}">${h.status}</span></td>
                    <td class="font-mono ${pctChangeClass}">${formatPct(h.value_change_pct)}</td>
                    <td class="font-mono">${formatPct(h.shares_change_pct)}</td>
                    <td class="font-mono text-dim">${h.report_period}</td>
                </tr>
            `;
        });
        document.getElementById('holders-table-body').innerHTML = html;

        // Render Pie Chart (Holders Ownership Share)
        const pieData = [{
            values: data.holders.map(h => h.value),
            labels: data.holders.map(h => h.manager),
            type: 'pie',
            hole: 0.45,
            textinfo: 'label+percent',
            marker: { colors: ['#3b82f6', '#ec4899', '#10b981', '#a855f7', '#f59e0b', '#06b6d4', '#84cc16'] }
        }];
        Plotly.newPlot('ticker-pie-chart', pieData, {
            paper_bgcolor: 'rgba(0,0,0,0)',
            font: { color: '#cbd5e1', family: 'Inter, sans-serif' },
            margin: { t: 20, b: 20, l: 20, r: 20 },
            showlegend: false
        }, {displayModeBar: false, responsive: true});

        // Render Bar Chart (QoQ Value changes)
        const barData = [{
            x: data.holders.map(h => h.value_change),
            y: data.holders.map(h => h.manager),
            type: 'bar',
            orientation: 'h',
            marker: {
                color: data.holders.map(h => h.value_change >= 0 ? '#06b6d4' : '#f97316')
            }
        }];
        Plotly.newPlot('ticker-bar-chart', barData, {
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            font: { color: '#cbd5e1', family: 'Inter, sans-serif' },
            margin: { t: 20, b: 40, l: 120, r: 20 },
            xaxis: { title: 'QoQ Value Change ($M)', gridcolor: '#1e293b' },
            yaxis: { gridcolor: '#1e293b' }
        }, {displayModeBar: false, responsive: true});

    } catch(err) {
        console.error('Error loading ticker detail:', err);
        showWhaleSentimentUnavailable('Ticker sentiment could not be loaded.');
        showToast('Could not load complete ticker intelligence', 'error');
    } finally {
        const remaining = Math.max(0, 700 - (performance.now() - loaderStartedAt));
        if (remaining > 0) {
            await new Promise(resolve => window.setTimeout(resolve, remaining));
        }
        hideTickerLoader();
    }
}

// ==========================================
// 3. Investor Level Portfolio Intelligence
// ==========================================

let globalInvestorsList = [];

async function loadInvestorsList() {
    const el = document.getElementById('investors-grid');
    if (!el) return;

    try {
        const r = await fetch('/api/investor-view');
        const {data} = await r.json();
        globalInvestorsList = data || [];
        filterInvestorsList();
    } catch(err) {
        console.error('Error loading investors list:', err);
    }
}

function filterInvestorsList() {
    const el = document.getElementById('investors-grid');
    if (!el) return;

    const group = document.getElementById('investor-group-filter')?.value || 'All';
    const search = document.getElementById('investor-search-input')?.value.trim().toLowerCase() || '';

    const filtered = globalInvestorsList.filter(f => {
        if (group !== 'All' && f.group !== group) return false;
        if (search) {
            const matches = f.name.toLowerCase().includes(search) || f.manager.toLowerCase().includes(search);
            if (!matches) return false;
        }
        return true;
    });

    let html = '';
    filtered.forEach(f => {
        const topChips = (f.top_holdings || []).map(t => `<span class="fund-top-chip">${t}</span>`).join('');
        html += `
            <div class="card fund-card" onclick="window.location='/investor/${f.cik}'">
                <div class="flex-align-gap" style="justify-content: space-between;">
                    <div class="fund-card-title">${f.name}</div>
                    <span class="badge ${getGroupClass(f.group)}">${f.group}</span>
                </div>
                <div class="fund-card-manager">Manager: <strong>${f.manager}</strong></div>
                ${f.annotation ? `<div class="fund-card-annotation">${f.annotation}</div>` : ''}

                <div class="fund-card-stats">
                    <div>
                        <div class="text-dim" style="font-size:0.7rem">TOTAL AUM</div>
                        <div class="font-mono text-cyan" style="font-weight:700">$${formatNum(f.total_value)} M</div>
                    </div>
                    <div>
                        <div class="text-dim" style="font-size:0.7rem">POSITIONS</div>
                        <div class="font-mono" style="font-weight:700">${f.total_holdings}</div>
                    </div>
                    <div>
                        <div class="text-dim" style="font-size:0.7rem">PERIOD</div>
                        <div class="font-mono text-dim">${f.report_period || '--'}</div>
                    </div>
                </div>
                ${topChips ? `<div class="fund-top-holdings">Top: ${topChips}</div>` : ''}
            </div>
        `;
    });
    el.innerHTML = html || `<div class="text-center py-4 text-muted">No fund managers match your search.</div>`;
}

async function loadInvestorDetail(cik) {
    try {
        const r = await fetch(`/api/investor/${cik}`);
        if (r.status === 404) return;
        const {data} = await r.json();

        // Populate Investor Header
        document.getElementById('inv-name').textContent = data.fund_info.name;
        document.getElementById('inv-manager').textContent = `Manager: ${data.fund_info.manager}`;
        document.getElementById('inv-group-badge').innerHTML = `<span class="badge ${getGroupClass(data.fund_info.group)}">${data.fund_info.group}</span>`;
        document.getElementById('inv-annotation').textContent = data.fund_info.annotation || '';

        document.getElementById('inv-aum').textContent = `$${formatNum(data.metadata.total_value_m)} M`;
        document.getElementById('inv-aum-b').textContent = `($${formatNum(data.metadata.total_value_b)} Billion)`;
        document.getElementById('inv-holdings-count').textContent = data.metadata.total_holdings;
        document.getElementById('inv-period').textContent = data.metadata.report_period;
        document.getElementById('inv-top5-weight').textContent = formatPct(data.stats.top5_weight);
        document.getElementById('inv-top10-weight').textContent = formatPct(data.stats.top10_weight);

        // Populate Tab Counts
        globalInvestorHoldings = data.holdings_list || [];
        globalInvestorClosed = data.closed_list || [];

        const statusCounts = data.stats.status_counts || {};
        if (document.getElementById('tab-cnt-all')) document.getElementById('tab-cnt-all').textContent = globalInvestorHoldings.length;
        if (document.getElementById('tab-cnt-new')) document.getElementById('tab-cnt-new').textContent = statusCounts['NEW'] || 0;
        if (document.getElementById('tab-cnt-inc')) document.getElementById('tab-cnt-inc').textContent = statusCounts['INCREASED'] || 0;
        if (document.getElementById('tab-cnt-dec')) document.getElementById('tab-cnt-dec').textContent = statusCounts['DECREASED'] || 0;
        if (document.getElementById('tab-cnt-unc')) document.getElementById('tab-cnt-unc').textContent = statusCounts['UNCHANGED'] || 0;
        if (document.getElementById('tab-cnt-closed')) document.getElementById('tab-cnt-closed').textContent = globalInvestorClosed.length;

        filterHoldingsTab('ALL');

        // Render Portfolio Allocation Donut Chart
        let top10 = globalInvestorHoldings.slice(0, 10);
        let otherWeight = globalInvestorHoldings.slice(10).reduce((sum, h) => sum + h.portfolio_weight, 0);
        let labels = top10.map(h => h.ticker);
        let vals = top10.map(h => h.portfolio_weight);
        if (otherWeight > 0.01) {
            labels.push('Other');
            vals.push(Number(otherWeight.toFixed(2)));
        }

        const pieData = [{
            values: vals,
            labels: labels,
            type: 'pie',
            hole: 0.45,
            textinfo: 'label+percent',
            marker: { colors: ['#3b82f6', '#ec4899', '#10b981', '#a855f7', '#f59e0b', '#06b6d4', '#84cc16', '#e11d48', '#d97706', '#6366f1', '#64748b'] }
        }];
        Plotly.newPlot('pie-chart', pieData, {
            paper_bgcolor: 'rgba(0,0,0,0)',
            font: { color: '#cbd5e1', family: 'Inter, sans-serif' },
            margin: { t: 20, b: 20, l: 20, r: 20 },
            showlegend: false
        }, {displayModeBar: false, responsive: true});

        // Render QoQ Position Moves Bar Chart
        const allMoves = [...globalInvestorHoldings, ...globalInvestorClosed].filter(h => h.status !== 'UNCHANGED');
        allMoves.sort((a,b) => Math.abs(b.value_change) - Math.abs(a.value_change));
        const topMoves = allMoves.slice(0, 15).reverse();

        const barData = [{
            x: topMoves.map(h => h.value_change),
            y: topMoves.map(h => h.ticker),
            type: 'bar',
            orientation: 'h',
            marker: {
                color: topMoves.map(h => {
                    if (h.status === 'NEW') return '#22c55e';
                    if (h.status === 'INCREASED') return '#06b6d4';
                    if (h.status === 'DECREASED') return '#f97316';
                    return '#ef4444';
                })
            }
        }];
        Plotly.newPlot('bar-chart', barData, {
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            font: { color: '#cbd5e1', family: 'Inter, sans-serif' },
            margin: { t: 20, b: 40, l: 70, r: 20 },
            xaxis: { title: 'Dollar Shift ($M)', gridcolor: '#1e293b' },
            yaxis: { gridcolor: '#1e293b' }
        }, {displayModeBar: false, responsive: true});

    } catch(err) {
        console.error('Error loading investor detail:', err);
    }
}

function filterHoldingsTab(tabName) {
    currentHoldingsTab = tabName;
    document.querySelectorAll('#holdings-tab-group .tab-btn').forEach(btn => btn.classList.remove('active'));
    event?.target?.classList?.add('active');
    filterInvestorTableRows();
}

function filterInvestorTableRows() {
    const q = document.getElementById('investor-table-search')?.value.trim().toLowerCase() || '';
    let pool = currentHoldingsTab === 'CLOSED' ? globalInvestorClosed : globalInvestorHoldings;

    if (currentHoldingsTab !== 'ALL' && currentHoldingsTab !== 'CLOSED') {
        pool = pool.filter(h => h.status === currentHoldingsTab);
    }

    if (q) {
        pool = pool.filter(h => h.ticker.toLowerCase().includes(q) || h.issuer.toLowerCase().includes(q));
    }

    sortAndRenderInvestorTable(pool);
}

function sortInvestorTable(e) {
    const th = e.target.closest('th');
    if (!th || !th.dataset.sort) return;
    const col = th.dataset.sort;

    if (investorSortCol === col) {
        investorSortAsc = !investorSortAsc;
    } else {
        investorSortCol = col;
        investorSortAsc = (col === 'ticker' || col === 'issuer');
    }
    filterInvestorTableRows();
}

function sortAndRenderInvestorTable(rows) {
    const tbody = document.getElementById('investor-holdings-body');
    if (!tbody) return;

    rows.sort((a, b) => {
        let vA = a[investorSortCol];
        let vB = b[investorSortCol];

        if (typeof vA === 'string') {
            vA = vA.toLowerCase();
            vB = vB.toLowerCase();
            return investorSortAsc ? vA.localeCompare(vB) : vB.localeCompare(vA);
        } else {
            vA = Number(vA) || 0;
            vB = Number(vB) || 0;
            return investorSortAsc ? vA - vB : vB - vA;
        }
    });

    if (rows.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" class="text-center py-4 text-muted">No holdings found for this tab or filter.</td></tr>`;
        return;
    }

    let html = '';
    rows.forEach(h => {
        const valChangeClass = h.value_change > 0 ? 'text-green' : (h.value_change < 0 ? 'text-red' : 'text-muted');
        const valChangeSign = h.value_change > 0 ? '+' : '';
        const pctChangeClass = h.value_change_pct > 0 ? 'text-green' : (h.value_change_pct < 0 ? 'text-red' : 'text-muted');

        html += `
            <tr>
                <td><a href="/ticker/${h.ticker}"><strong class="font-mono">${h.ticker}</strong></a></td>
                <td>${h.issuer}</td>
                <td class="font-mono">${renderSparkline(h.portfolio_weight)}</td>
                <td class="font-mono"><strong>$${formatNum(h.value)}</strong></td>
                <td class="font-mono">${formatInt(h.shares)}</td>
                <td><span class="badge ${getStatusClass(h.status)}">${h.status}</span></td>
                <td class="font-mono ${valChangeClass}"><strong>${valChangeSign}${formatNum(h.value_change)}</strong></td>
                <td class="font-mono ${pctChangeClass}">${formatPct(h.value_change_pct)}</td>
                <td class="font-mono">${formatPct(h.shares_change_pct)}</td>
            </tr>
        `;
    });
    tbody.innerHTML = html;
}

function exportInvestorToCSV() {
    const name = document.getElementById('inv-name')?.textContent || 'Portfolio';
    const allRows = [...globalInvestorHoldings, ...globalInvestorClosed];
    if (allRows.length === 0) return;

    const headers = ["Ticker", "Company", "Cusip", "WeightPct", "Value_M", "Shares", "Action", "Change_M", "ChangePct", "SharesChangePct"];
    const rows = allRows.map(h => [
        `"${h.ticker}"`,
        `"${h.issuer}"`,
        `"${h.cusip || ''}"`,
        h.portfolio_weight,
        h.value,
        h.shares,
        `"${h.status}"`,
        h.value_change,
        h.value_change_pct,
        h.shares_change_pct
    ]);

    const csvContent = "data:text/csv;charset=utf-8," + [headers.join(","), ...rows.map(e => e.join(","))].join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `${name.replace(/[^a-zA-Z0-9]/g, '_')}_Holdings.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}
