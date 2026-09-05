// ==========================================
// Alpha Whales Intelligence - Frontend Application
// ==========================================

// Global State
let globalQoQData = [];
let globalPeriodQoQData = [];
let filteredQoQData = [];
let currentSortColumn = 'value_change';
let currentSortAsc = false;
let currentPage = 1;
let pageSize = 50;
let selectedFilingPeriod = null;
let currentOverviewTab = 'overview';
const notableActions = Object.freeze({
    NEW: {key: 'new', badge: 'NEW'},
    INCREASED: {key: 'adds', badge: 'INCREASE'},
    DECREASED: {key: 'cuts', badge: 'DECREASE'},
    UNCHANGED: {key: 'holds', badge: 'UNCHANGED SHARES'},
    CLOSED: {key: 'exits', badge: 'EXIT'}
});
const notableBetsExpanded = {NEW: false, INCREASED: false, DECREASED: false, UNCHANGED: false, CLOSED: false};
let notableAction = 'opportunities';
let notableSector = '';
let selectedAwfiHorizon = 252;
let currentTickerTab = 'decision';
let tickerValuationCategory = 'decision';
let tickerAwfiScores = {};
let tickerAwfiMetadata = {};
let tickerAwfiHistory = [];
let tickerAwfiIntelligence = null;
let tickerAwfiHistoryRefreshTimer = null;
let tickerAwfiHistoryRefreshAttempts = 0;
let tickerAwfiHistorySnapshotVersion = null;
const tickerAwfiHorizons = Object.freeze([
    {key: '126', label: '6M', duration: '6 months', color: '#22d3ee'},
    {key: '252', label: '12M', duration: '12 months', color: '#34d399'},
    {key: '378', label: '18M', duration: '18 months', color: '#f59e0b'},
    {key: '504', label: '24M', duration: '24 months', color: '#a78bfa'}
]);

let globalAllTickers = [];
let filteredAllTickers = [];
let allTickersSortCol = 'num_holders';
let allTickersSortAsc = false;

let globalInvestorHoldings = [];
let globalInvestorClosed = [];
let currentHoldingsTab = 'ALL';
let investorSortCol = 'portfolio_weight';
let investorSortAsc = false;
let currentInvestorCik = null;
let investorHistoryData = null;
let investorHistoryLoading = false;
let investorActivityFilter = 'ALL';
let investorSnapshotOnly = false;

let screeningData = [];
let screeningSortColumn = 'median_reported_value_4q';
let screeningSortAsc = false;
let screeningPage = 1;
const screeningPageSize = 50;
let screeningLoadTimer = null;
let screeningAbortController = null;
const screeningSelectedCiks = new Set();
let screeningRosterBusy = false;
let filingOperationsOffset = 0;
let filingOperationsPageSize = 25;
let filingOperationsTotal = 0;
let filingOperationsLoadTimer = null;
let filingOperationsAbortController = null;
let filingDetailAbortController = null;
const signalChartColors = Object.freeze({
    new: '#60a5fa',
    increased: '#34d399',
    decreased: '#fb7185',
    closed: '#ef4444',
    neutral: '#64748b'
});

const tickerTabDescriptions = Object.freeze({
    decision: 'AWFI, valuation, timing, and educational sizing.',
    market: 'Market metrics, direct-impact news, and the interactive price chart.',
    'whale-activity': 'Manager-relative Alpha Sentiment, conviction history, and contributors.',
    ownership: 'Twenty-quarter ownership history, manager distributions, and current holders.',
    pairs: 'Hypothesis-tier relative-value and pair research.'
});

// Global SSE Setup
const evtSource = new EventSource('/events');
evtSource.onmessage = function(e) {
    try {
        const data = JSON.parse(e.data);
        if (data.type === 'roster_updated') {
            showToast(`Roster updated: ${data.count} configured managers`);
            if (window.location.pathname === '/screening') {
                loadInvestorScreening();
            } else if (window.location.pathname.startsWith('/investor')) {
                loadInvestorsList();
            }
            return;
        }
        if (data.type === 'fund_updated') {
            updateTimestamp(data.timestamp || new Date().toISOString());
            return;
        }
        if (data.type === 'data_refresh') {
            updateTimestamp(data.timestamp || new Date().toISOString());
            showToast('SEC Data Refreshed: All configured funds updated');
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
            } else if (window.location.pathname === '/filings') {
                loadFilingOperations();
            }
        } else if (data.type === 'awfi_published') {
            showToast('AWFI history updated');
            if (window.location.pathname.startsWith('/ticker/')) {
                const ticker = window.location.pathname.split('/')[2];
                if (ticker) loadTickerDetail(ticker);
            } else if (window.location.pathname === '/') {
                loadOverviewData(selectedFilingPeriod);
            }
        } else if (data.type === 'filings_ingested') {
            showToast(
                `${formatInt(data.count)} new SEC filing${data.count === 1 ? '' : 's'} recorded`
            );
            if (window.location.pathname === '/filings') {
                loadFilingOperations(true);
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

    showToast('Triggered SEC 13F background refresh across configured funds...');

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
    return {
        'Value & Contrarian': 'badge-group-value',
        'Quality Growth': 'badge-group-quality',
        'Technology & Innovation': 'badge-group-technology',
        'Opportunistic & Concentrated': 'badge-group-opportunistic',
        'Diversified & Systematic': 'badge-group-diversified'
    }[g] || 'badge-neutral';
}
function getStatusClass(s) {
    if (!s) return 'badge-status-unchanged';
    const sl = s.toLowerCase();
    return `badge-status-${sl}`;
}

const QOQ_ACTION_CHART_STYLES = [
    { status: 'NEW', label: 'New position', color: '#60a5fa' },
    { status: 'INCREASED', label: 'Increased shares', color: '#34d399' },
    { status: 'DECREASED', label: 'Decreased shares', color: '#fb7185' },
    { status: 'CLOSED', label: 'Closed / exited', color: '#f43f5e' }
];

function renderQoQActionBarChart(elementId, moves, options = {}) {
    const element = document.getElementById(elementId);
    if (!element) return;

    const actionStatuses = new Set(QOQ_ACTION_CHART_STYLES.map(action => action.status));
    const rankedMoves = (moves || [])
        .filter(move => actionStatuses.has(move.status) && Number.isFinite(Number(move.value_change)))
        .sort((a, b) => Math.abs(Number(b.value_change)) - Math.abs(Number(a.value_change)))
        .slice(0, options.maxItems || 15)
        .reverse();
    const labels = rankedMoves.map(options.getLabel || (move => move.ticker));
    const hoverDetails = rankedMoves.map(
        options.getHoverDetail || (move => move.issuer || move.fund_name || '')
    );
    const traces = QOQ_ACTION_CHART_STYLES.map(action => ({
        name: action.label,
        x: rankedMoves.map(move => move.status === action.status ? Number(move.value_change) : null),
        y: labels,
        customdata: hoverDetails,
        type: 'bar',
        orientation: 'h',
        marker: { color: action.color },
        hovertemplate: (
            '<b>%{y}</b><br>%{customdata}<br>'
            + `${action.label}<br>Reported value change: $%{x:,.2f}M<extra></extra>`
        )
    })).filter(trace => trace.x.some(value => value !== null));

    const rowHeight = options.rowHeight || 27;
    const chartHeight = Math.max(options.minHeight || 360, rankedMoves.length * rowHeight + 125);
    element.style.height = `${chartHeight}px`;
    const layout = {
        barmode: 'overlay',
        height: chartHeight,
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: '#cbd5e1', family: 'Inter, sans-serif' },
        margin: { t: 62, b: 46, l: options.leftMargin || 120, r: 24 },
        legend: {
            orientation: 'h',
            x: 0,
            y: 1.16,
            xanchor: 'left',
            yanchor: 'bottom',
            font: { size: 12 }
        },
        xaxis: {
            title: options.xAxisTitle || 'Reported Value Change ($M)',
            gridcolor: '#1e293b',
            zeroline: true,
            zerolinecolor: '#64748b',
            zerolinewidth: 1.5
        },
        yaxis: {
            gridcolor: '#1e293b',
            automargin: true
        },
        showlegend: traces.length > 0,
        annotations: traces.length === 0 ? [{
            text: options.emptyMessage || 'No directional share actions are available for this quarter.',
            x: 0.5,
            y: 0.5,
            xref: 'paper',
            yref: 'paper',
            showarrow: false,
            font: { color: '#94a3b8' }
        }] : []
    };

    return Plotly.newPlot(
        elementId,
        traces,
        layout,
        {displayModeBar: false, responsive: true}
    );
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

const overviewTabDescriptions = Object.freeze({
    overview: 'Find meaningful buys, build-ups, reductions, unchanged holdings, and exits.',
    sentiment: 'AWFI ranks forward-looking opportunity and avoidance signals for the selected investment horizon.',
    positioning: 'Supporting context: consensus, existing concentration, reported-value shifts, and market prices.',
    managers: 'Scan each manager’s largest portfolio-weight additions and reductions without leaving the period view.',
    changes: 'Filter, sort, paginate, and export every comparable quarter-over-quarter position change.'
});

function switchOverviewTab(tabName, updateHash = true) {
    if (!Object.hasOwn(overviewTabDescriptions, tabName)) tabName = 'overview';
    currentOverviewTab = tabName;

    document.querySelectorAll('[data-overview-tab]').forEach(tab => {
        const isActive = tab.dataset.overviewTab === tabName;
        tab.classList.toggle('is-active', isActive);
        tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
        tab.tabIndex = isActive ? 0 : -1;
    });
    document.querySelectorAll('[data-overview-panel]').forEach(panel => {
        panel.hidden = panel.dataset.overviewPanel !== tabName;
    });

    const description = document.getElementById('overview-tab-description');
    if (description) description.textContent = overviewTabDescriptions[tabName];

    if (updateHash) {
        window.history.replaceState(null, '', `#${tabName}`);
        document.getElementById('overview-workspace')?.scrollIntoView({block: 'start'});
    }
    if (tabName === 'positioning' && window.Plotly) {
        window.requestAnimationFrame(() => {
            ['summary-chart', 'top-moves-chart'].forEach(id => {
                const chart = document.getElementById(id);
                if (chart?.data) Plotly.Plots.resize(chart);
            });
        });
    }
}

function initializeOverviewTabs() {
    const tabs = [...document.querySelectorAll('[data-overview-tab]')];
    if (!tabs.length) return;
    const workspace = document.querySelector('.qoq-workspace');
    const navbar = document.querySelector('.navbar');
    const tabShell = document.getElementById('overview-workspace');
    if (workspace && navbar && tabShell) {
        const updateStickyOffsets = () => {
            workspace.style.setProperty('--qoq-nav-offset', `${navbar.getBoundingClientRect().height + 8}px`);
            workspace.style.setProperty('--qoq-tabs-height', `${tabShell.getBoundingClientRect().height}px`);
        };
        const observer = new ResizeObserver(updateStickyOffsets);
        observer.observe(navbar);
        observer.observe(tabShell);
        updateStickyOffsets();
    }

    const requestedTab = window.location.hash.slice(1);
    switchOverviewTab(
        Object.hasOwn(overviewTabDescriptions, requestedTab)
            ? requestedTab
            : 'overview',
        false
    );
    document.querySelector('.overview-tabs')?.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const currentIndex = tabs.findIndex(tab => tab.dataset.overviewTab === currentOverviewTab);
        const nextIndex = event.key === 'Home'
            ? 0
            : event.key === 'End'
                ? tabs.length - 1
                : event.key === 'ArrowRight'
                    ? (currentIndex + 1) % tabs.length
                    : (currentIndex - 1 + tabs.length) % tabs.length;
        const nextTab = tabs[nextIndex];
        switchOverviewTab(nextTab.dataset.overviewTab);
        nextTab.focus();
    });
}

function changeAwfiHorizon(value) {
    const parsed = Number(value);
    if (![126, 252, 378, 504].includes(parsed)) return;
    selectedAwfiHorizon = parsed;
    loadOverviewData(selectedFilingPeriod);
}

function formatCalendarDate(value) {
    if (!value) return 'Unavailable';
    return new Date(`${value}T00:00:00Z`).toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        timeZone: 'UTC'
    });
}

function formatNewsTimestamp(value) {
    if (!value) return 'Publication time unavailable';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return 'Publication time unavailable';
    return parsed.toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: 'numeric',
        minute: '2-digit'
    });
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function safeExternalNewsUrl(value) {
    try {
        const parsed = new URL(String(value || ''));
        return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : null;
    } catch {
        return null;
    }
}

function addCalendarDays(value, days) {
    const result = new Date(`${value}T00:00:00Z`);
    result.setUTCDate(result.getUTCDate() + days);
    return result.toISOString().slice(0, 10);
}

function closeOnOrBefore(priceHistory, targetDate) {
    if (!targetDate) return null;
    let result = null;
    for (const point of priceHistory) {
        if (!point.date || point.date > targetDate) break;
        if (point.close !== null && point.close !== undefined) {
            result = Number(point.close);
        }
    }
    return result;
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
    const total = status.total_funds || 0;
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
    updatePeriodLoader(period, {state: 'uncached', completed_funds: 0, total_funds: 0});

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
        const awfiSelect = document.getElementById('awfi-horizon-select');
        if (awfiSelect) awfiSelect.value = String(selectedAwfiHorizon);

        const allQoQData = result.changes || [];
        globalPeriodQoQData = allQoQData;
        globalQoQData = allQoQData.filter(change => change.status !== 'UNCHANGED');
        renderNotableBets();
        if (result.overview) updateTimestamp(result.overview.last_updated);
        applyFilters();
        renderOverviewCharts(allQoQData);
        renderManagerActivityMatrix(globalQoQData, result.funds || []);
        renderSignalKpis(result.tickers || [], globalQoQData, result.portfolio_stats || {});
        renderPortfolioStats(
            result.tickers || [],
            globalQoQData,
            result.portfolio_stats || {},
            result.awfi_metadata || {}
        );
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

function searchNotableChanges(changes, query, sector = '') {
    const search = query.trim().toLowerCase();
    let matches = changes;
    if (search) {
        const exactTicker = changes.filter(move => move.ticker.toLowerCase() === search);
        matches = exactTicker.length ? exactTicker : changes.filter(move => [
            move.ticker, move.issuer, move.manager, move.fund_name
        ].some(value => String(value || '').toLowerCase().includes(search)));
    }
    return matches.filter(move => !sector || (move.sector || 'Unclassified') === sector);
}

function getNotableBets(changes, criteria, status, sortBy) {
    return changes.filter(move => {
        if (move.status !== status) return false;
        const weight = ['DECREASED', 'CLOSED'].includes(status)
            ? move.previous_portfolio_weight : move.portfolio_weight;
        if (!Number.isFinite(weight) || weight < criteria.minWeight) return false;
        if (status === 'INCREASED') {
            return Number.isFinite(move.shares_change_pct)
                && move.shares_change_pct >= criteria.minShareIncrease
                && Number.isFinite(move.portfolio_weight_change_raw)
                && move.portfolio_weight_change_raw >= criteria.minWeightIncrease;
        }
        if (status === 'DECREASED') {
            return Number.isFinite(move.shares_change_pct)
                && move.shares_change_pct <= -criteria.minShareCut;
        }
        return ['NEW', 'UNCHANGED', 'CLOSED'].includes(status);
    }).sort((a, b) => {
        const left = a[sortBy];
        const right = b[sortBy];
        if (Number.isFinite(left) !== Number.isFinite(right)) {
            return Number.isFinite(left) ? -1 : 1;
        }
        if (Number.isFinite(left) && left !== right) {
            return status === 'DECREASED' && sortBy === 'shares_change_pct'
                ? left - right : right - left;
        }
        return a.ticker.localeCompare(b.ticker)
            || a.manager.localeCompare(b.manager)
            || a.cik.localeCompare(b.cik);
    });
}

function getSectorActionMatrix(changes, criteria, valid = true, sectorNames = []) {
    const sectors = [...new Set([
        ...sectorNames,
        ...changes.map(move => move.sector || 'Unclassified')
    ])].filter(Boolean).sort((a, b) => (
        (a === 'Unclassified') - (b === 'Unclassified') || a.localeCompare(b)
    ));
    const rows = ['', ...sectors].map(sector => ({
        sector,
        actions: Object.fromEntries(Object.keys(notableActions).map(status => [
            status, {total: 0, notable: valid ? 0 : null}
        ]))
    }));
    const bySector = new Map(rows.map(row => [row.sector, row]));
    const qualifying = valid ? new Set(Object.keys(notableActions).flatMap(status =>
        getNotableBets(changes, criteria, status, 'portfolio_weight')
    )) : new Set();
    for (const move of changes) {
        if (!Object.hasOwn(notableActions, move.status)) continue;
        for (const row of [rows[0], bySector.get(move.sector || 'Unclassified')]) {
            row.actions[move.status].total += 1;
            if (qualifying.has(move)) row.actions[move.status].notable += 1;
        }
    }
    return rows;
}

function renderNotableSectorMatrix(changes, criteria, valid) {
    const rows = getSectorActionMatrix(changes, criteria, valid, [
        ...globalPeriodQoQData.map(move => move.sector || 'Unclassified'),
        notableSector
    ]);
    const labels = {NEW: 'Buy', INCREASED: 'Increase', DECREASED: 'Decrease', UNCHANGED: 'Hold', CLOSED: 'Exit'};
    document.getElementById('signal-matrix-body').innerHTML = rows.map(row => {
        const sectorLabel = row.sector || 'All sectors';
        return `<tr class="${row.sector ? '' : 'signal-matrix-total'}">
            <th scope="row"><button type="button" class="signal-matrix-sector"
                data-sector="${escapeHtml(row.sector)}" aria-pressed="${notableSector === row.sector}"
                aria-controls="notable-lanes">${escapeHtml(sectorLabel)}</button></th>
            ${Object.entries(notableActions).map(([status, {key}]) => {
                const {total, notable} = row.actions[status];
                const selected = notableSector === row.sector && (
                    notableAction === status
                    || (notableAction === 'opportunities' && ['NEW', 'INCREASED'].includes(status))
                );
                const read = notable === null ? 'Notable count unavailable' : `${notable} notable`;
                return `<td class="signal-tone-${key}">
                    <button type="button" class="signal-matrix-cell${notable === 0 ? ' is-zero' : ''}"
                        data-sector="${escapeHtml(row.sector)}" data-action="${status}"
                        aria-pressed="${selected}" aria-controls="notable-${key}-lane"
                        aria-label="${escapeHtml(`${sectorLabel}, ${labels[status]}: ${read} of ${total} reported positions. Filter details.`)}">
                        <strong class="font-mono">${notable ?? '—'}</strong>
                        <span class="signal-matrix-denominator">/ ${total}</span>
                    </button>
                </td>`;
            }).join('')}
        </tr>`;
    }).join('');
}

function selectNotableMatrixCell(event) {
    const button = event.target.closest('button[data-sector]');
    if (!button) return;
    notableSector = button.dataset.sector;
    const action = button.dataset.action;
    if (action) notableAction = action;
    const hadFocus = document.activeElement === button;
    renderNotableBets(true);
    if (hadFocus) {
        const replacement = [...document.querySelectorAll('#signal-matrix-body button')].find(item => (
            item.dataset.sector === notableSector && item.dataset.action === action
        ));
        replacement?.focus({preventScroll: true});
    }
}

function selectNotableAction(action) {
    if (action !== 'opportunities' && !Object.hasOwn(notableActions, action)) {
        showToast('Unknown filing action', 'error');
        return;
    }
    notableAction = action;
    renderNotableBets();
    const section = document.getElementById('notable-bets');
    if (section && section.getBoundingClientRect().top < 0) {
        section.scrollIntoView({block: 'start'});
    }
}

function resetNotableFilters() {
    notableSector = '';
    for (const [id, value] of Object.entries({
        'notable-min-weight': '2',
        'notable-min-shares': '50',
        'notable-min-delta': '1',
        'notable-min-cut': '25',
        'notable-search': ''
    })) {
        document.getElementById(id).value = value;
    }
    for (const {key} of Object.values(notableActions)) {
        document.getElementById(`notable-${key}-sort`).selectedIndex = 0;
    }
    renderNotableBets(true);
}

function toggleNotableBets(status) {
    notableBetsExpanded[status] = !notableBetsExpanded[status];
    renderNotableBets();
}

function renderNotableBets(resetExpanded = false) {
    const section = document.getElementById('notable-bets');
    if (!section) return;
    if (resetExpanded) {
        for (const status of Object.keys(notableActions)) notableBetsExpanded[status] = false;
    }
    const minWeight = document.getElementById('notable-min-weight').valueAsNumber;
    const minShareIncrease = document.getElementById('notable-min-shares').valueAsNumber;
    const minWeightIncrease = document.getElementById('notable-min-delta').valueAsNumber;
    const minShareCut = document.getElementById('notable-min-cut').valueAsNumber;
    const valid = [minWeight, minShareIncrease, minWeightIncrease, minShareCut]
        .every(value => Number.isFinite(value) && value >= 0) && minShareCut <= 100;
    const search = document.getElementById('notable-search').value.trim().toLowerCase();
    const sector = notableSector;
    const criteria = {minWeight, minShareIncrease, minWeightIncrease, minShareCut};
    renderNotableSectorMatrix(searchNotableChanges(globalPeriodQoQData, search), criteria, valid);
    const matchingChanges = searchNotableChanges(globalPeriodQoQData, search, sector);
    const summary = document.getElementById('notable-bets-summary');
    const criteriaText = {
        opportunities: `New positions ≥${formatPct(minWeight)}; increases also need shares +${formatNum(minShareIncrease)}% and weight +${formatNum(minWeightIncrease)}pp.`,
        NEW: `New positions ≥${formatPct(minWeight)} of the reported portfolio.`,
        INCREASED: `Ending weight ≥${formatPct(minWeight)}, shares +${formatNum(minShareIncrease)}% or more, and weight +${formatNum(minWeightIncrease)}pp or more.`,
        DECREASED: `Previous weight ≥${formatPct(minWeight)} and shares cut by at least ${formatPct(minShareCut)}. Weight can still rise when prices change.`,
        UNCHANGED: `Unchanged reported shares and ending weight ≥${formatPct(minWeight)}. This is not an AWFI HOLD recommendation.`,
        CLOSED: `Full exits from positions previously ≥${formatPct(minWeight)} of the reported portfolio.`
    };
    summary.textContent = valid
        ? `${selectedFilingPeriod || 'Selected period'} · ${sector || 'All sectors'} · ${criteriaText[notableAction]}${search ? ` Search: "${search}".` : ''}`
        : 'Enter non-negative thresholds; the shares-cut threshold must be between 0 and 100%.';
    document.getElementById('signal-opportunities').setAttribute('aria-pressed', String(notableAction === 'opportunities'));
    document.getElementById('notable-lanes').classList.toggle('is-focused', notableAction !== 'opportunities');

    for (const [status, {key, badge}] of Object.entries(notableActions)) {
        const list = document.getElementById(`notable-${key}-list`);
        const button = document.getElementById(`notable-${key}-toggle`);
        const sortBy = document.getElementById(`notable-${key}-sort`).value;
        document.getElementById(`notable-${key}-lane`).hidden = notableAction === 'opportunities'
            ? !['NEW', 'INCREASED'].includes(status) : notableAction !== status;
        const bets = valid
            ? getNotableBets(matchingChanges, criteria, status, sortBy)
            : [];
        document.getElementById(`notable-${key}-count`).textContent = valid ? bets.length : '—';
        const visibleBets = notableBetsExpanded[status] ? bets : bets.slice(0, 5);
        list.innerHTML = visibleBets.length ? visibleBets.map(move => {
            const relativeSize = Number.isFinite(move.position_size_vs_normal)
                ? `${formatNum(move.position_size_vs_normal)}x normal holding`
                : 'Normal holding size unavailable';
            const previousWeight = Number.isFinite(move.previous_portfolio_weight)
                ? formatPct(move.previous_portfolio_weight) : 'Unavailable';
            const positionValue = status === 'CLOSED' ? move.prev_value : move.value;
            const reportedValue = Number.isFinite(positionValue)
                ? `$${formatNum(positionValue)}M ${status === 'CLOSED' ? 'previous' : 'reported'} position` : 'Reported value unavailable';
            const shareText = status === 'NEW' ? 'Initiated this quarter'
                : status === 'CLOSED' ? 'Fully exited'
                : status === 'UNCHANGED' ? 'Shares unchanged'
                : `Shares <span class="${move.shares_change_pct > 0 ? 'text-green' : 'text-red'}">${move.shares_change_pct > 0 ? '+' : ''}${formatNum(move.shares_change_pct)}%</span>`;
            const delta = move.portfolio_weight_change;
            const deltaClass = delta > 0 ? 'text-green' : delta < 0 ? 'text-red' : 'text-muted';
            const deltaText = Number.isFinite(delta)
                ? `${delta > 0 ? '+' : ''}${formatNum(delta)}pp` : 'Weight change unavailable';
            return `
                <li class="notable-bet">
                    <div class="notable-bet-identity">
                        <a class="notable-bet-ticker font-mono" href="/ticker/${encodeURIComponent(move.ticker)}">${escapeHtml(move.ticker)}</a>
                        <span class="notable-bet-action">${badge}</span>
                        <a class="notable-bet-manager" href="/investor/${encodeURIComponent(move.cik)}">${escapeHtml(move.manager)}</a>
                    </div>
                    <div class="notable-bet-classification" title="Sector and industry reuse cached company profiles, with the local reference snapshot as fallback. Not historical 13F classifications.">
                        ${escapeHtml(move.sector || 'Unclassified')}${move.industry ? ` · ${escapeHtml(move.industry)}` : ''}
                    </div>
                    <div class="notable-bet-evidence">
                        <strong class="font-mono">${previousWeight} &rarr; ${formatPct(move.portfolio_weight)}</strong>
                        <strong class="font-mono ${deltaClass}">${deltaText}</strong>
                    </div>
                    <div class="notable-bet-context">
                        <span>${reportedValue}</span>
                        <span>${shareText}</span>
                        ${status === 'CLOSED' ? '' : `<span title="Current reported portfolio weight divided by the manager's median positive prior-quarter position weight. Not a return forecast or the sentiment score.">${relativeSize}</span>`}
                    </div>
                </li>`;
        }).join('') : `<li class="stats-loading">${valid ? 'No positions match. Try another action, lower thresholds, or reset filters.' : 'Correct the thresholds above to show positions.'}</li>`;
        document.getElementById(`notable-${key}-shown`).textContent = valid
            ? `Showing ${visibleBets.length} of ${bets.length}` : '';
        button.hidden = bets.length <= 5;
        button.setAttribute('aria-expanded', String(notableBetsExpanded[status]));
        button.textContent = notableBetsExpanded[status] ? 'Show fewer' : `View all ${bets.length}`;
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
            ? `${consensusBuy.qoq_actions.increased} increased · ${consensusBuy.qoq_actions.new} initiated · ${consensusBuy.num_holders} current holders`
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
            ? `${consensusSell.qoq_actions.decreased} reduced · ${consensusSell.qoq_actions.closed} exited · ${consensusSell.num_holders} current holders`
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

function getSelectedAwfi(item) {
    return item?.awfi?.[String(selectedAwfiHorizon)] || null;
}

function formatAwfiHorizonLabel(horizon) {
    return `${Math.round(Number(horizon) / 21)}M`;
}

function renderStatsAwfi(awfi, metadata = {}, compact = false) {
    if (metadata.state === 'STALE') {
        return '<span class="stats-awfi stale">— <small>Stale research</small></span>';
    }
    if (!awfi || awfi.score === null || awfi.score === undefined) {
        return '<span class="stats-awfi unavailable">— <small>No AWFI score</small></span>';
    }
    const signal = String(awfi.signal || 'HOLD').toUpperCase();
    const directionClass = signal === 'BUY'
        ? 'buy'
        : signal === 'SELL'
            ? 'sell'
            : 'hold';
    const scoreValue = Number(awfi.score);
    const score = `${scoreValue > 0 ? '+' : ''}${scoreValue.toFixed(0)}`;
    const horizon = formatAwfiHorizonLabel(
        awfi.horizon_sessions || selectedAwfiHorizon
    );
    const signalLabel = signal === 'SELL'
        ? 'SELL / AVOID'
        : signal;
    const tooltip = [
        `AWFI Research v2 ${horizon}`,
        `Signal: ${signalLabel}`,
        `Market data through ${awfi.feature_date || awfi.as_of_date || 'unavailable'}`,
        `Thresholds +${awfi.positive_threshold} / -${awfi.negative_threshold}`,
        'Research model; not investment advice'
    ].join('; ');
    return `
        <span class="stats-awfi ${directionClass}"
              title="${escapeScreeningHtml(tooltip)}">
            <b>${score}</b>
            <small>${compact ? signalLabel : `${signalLabel} · ${horizon}`}</small>
        </span>
    `;
}

function renderOwnershipActivityTable(items, reportPeriod, awfiMetadata) {
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
                    ${renderStatsAwfi(getSelectedAwfi(item), awfiMetadata)}
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

function renderPortfolioStats(tickers, changes, portfolioStats, awfiMetadata = {}) {
    const period = changes.find(change => change.report_period)?.report_period;
    const awfiHeader = document.getElementById('stats-awfi-horizon');
    if (awfiHeader) {
        awfiHeader.textContent = `AWFI ${formatAwfiHorizonLabel(selectedAwfiHorizon)}`;
    }
    const awfiStatusTitle = document.getElementById('awfi-status-title');
    const awfiStatusMessage = document.getElementById('awfi-status-message');
    if (awfiStatusTitle && awfiStatusMessage) {
        const requested = awfiMetadata.requested_period || period || 'selected period';
        const latest = awfiMetadata.latest_period;
        if (awfiMetadata.state === 'READY' || awfiMetadata.state === 'LIVE') {
            awfiStatusTitle.textContent = `AWFI Research v2 · ${formatFilingPeriodLabel(requested)}`;
            awfiStatusMessage.textContent = awfiMetadata.state === 'LIVE'
                ? `Filing inputs are fixed to ${formatFilingPeriodLabel(requested)} and technical inputs are refreshed through ${formatCalendarDate(awfiMetadata.market_data_date)}. All horizons use a consistent ±75 research threshold.`
                : `Exact-period ${formatAwfiHorizonLabel(selectedAwfiHorizon)} research scores from run ${awfiMetadata.run_id || 'unavailable'}.`;
        } else if (awfiMetadata.state === 'STALE') {
            awfiStatusTitle.textContent = 'AWFI research snapshot is stale';
            awfiStatusMessage.textContent = `Holdings are ${requested}, while the latest completed AWFI snapshot is ${latest || 'unavailable'}. Scores will remain unavailable until the offline research snapshot is rebuilt.`;
        } else {
            awfiStatusTitle.textContent = 'AWFI unavailable for this period';
            awfiStatusMessage.textContent = awfiMetadata.reason || 'No exact-period AWFI Research v2 scores are available.';
        }
    }

    const mostOwned = [...tickers]
        .sort((a, b) => b.num_holders - a.num_holders || b.total_value_across_funds - a.total_value_across_funds)
        .slice(0, 10);
    renderOwnershipActivityTable(mostOwned, period, awfiMetadata);

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
            ${renderStatsAwfi(getSelectedAwfi(item), awfiMetadata, true)}
        `,
        item => `across ${item.num_holders} funds`
    );

    const awfiRankings = tickers
        .map(item => {
            const awfi = getSelectedAwfi(item);
            return {
                ...item,
                awfi,
                awfiValue: Number(awfi?.score)
            };
        })
        .filter(item => Number.isFinite(item.awfiValue));
    const positiveAwfi = awfiRankings
        .filter(item => item.awfiValue > 0)
        .sort((a, b) => (
            b.awfiValue - a.awfiValue
            || b.num_holders - a.num_holders
        ))
        .slice(0, 10);
    const negativeAwfi = awfiRankings
        .filter(item => item.awfiValue < 0)
        .sort((a, b) => (
            a.awfiValue - b.awfiValue
            || b.num_holders - a.num_holders
        ))
        .slice(0, 10);
    const awfiDetail = item => {
        const awfi = item.awfi;
        const horizon = formatAwfiHorizonLabel(
            awfi?.horizon_sessions || selectedAwfiHorizon
        );
        return `${horizon} · ${awfi?.signal === 'SELL' ? 'SELL / AVOID' : awfi?.signal || 'HOLD'} · Research v2`;
    };
    renderStatsList(
        'stats-positive-sentiment',
        positiveAwfi,
        item => renderStatsAwfi(item.awfi, awfiMetadata, true),
        awfiDetail
    );
    renderStatsList(
        'stats-negative-sentiment',
        negativeAwfi,
        item => renderStatsAwfi(item.awfi, awfiMetadata, true),
        awfiDetail
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
                    <td class="font-mono text-green"><strong>${formatPct(item.pct_above_low)}</strong></td>
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

    // Chart 1: Investment-style move distribution
    const groupMoves = {};
    data.forEach(r => {
        if (!groupMoves[r.group]) groupMoves[r.group] = { 'NEW': 0, 'INCREASED': 0, 'DECREASED': 0, 'CLOSED': 0, 'UNCHANGED': 0 };
        if (groupMoves[r.group][r.status] !== undefined) groupMoves[r.group][r.status]++;
    });

    const groups = Object.keys(groupMoves);
    const plotData1 = [
        { name: 'NEW', x: groups, y: groups.map(g => groupMoves[g]['NEW']), type: 'bar', marker: { color: signalChartColors.new } },
        { name: 'INCREASED', x: groups, y: groups.map(g => groupMoves[g]['INCREASED']), type: 'bar', marker: { color: signalChartColors.increased } },
        { name: 'DECREASED', x: groups, y: groups.map(g => groupMoves[g]['DECREASED']), type: 'bar', marker: { color: signalChartColors.decreased } },
        { name: 'CLOSED', x: groups, y: groups.map(g => groupMoves[g]['CLOSED']), type: 'bar', marker: { color: signalChartColors.closed } },
        { name: 'UNCHANGED', x: groups, y: groups.map(g => groupMoves[g]['UNCHANGED']), type: 'bar', marker: { color: signalChartColors.neutral } },
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

    renderQoQActionBarChart('top-moves-chart', data, {
        maxItems: 10,
        minHeight: 400,
        leftMargin: 155,
        getLabel: move => `${move.ticker} (${move.manager})`,
        getHoverDetail: move => `${move.issuer} · ${move.fund_name}`
    });
}

function exportQoQToCSV() {
    if (!filteredQoQData || filteredQoQData.length === 0) {
        showToast('No data to export.');
        return;
    }
    const headers = ["Fund", "Manager", "InvestmentStyle", "Ticker", "Issuer", "Action", "WeightPct", "Value_M", "PrevValue_M", "Change_M", "ChangePct", "SharesChangePct", "Period"];
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
        renderConsensusTickerChips(globalAllTickers);
        filterAllTickersTable();
        renderPopularityCharts(globalAllTickers);
    } catch(err) {
        console.error('Error loading all tickers:', err);
    }
}

function renderConsensusTickerChips(tickers) {
    const container = document.getElementById('consensus-ticker-chips');
    if (!container) return;
    const consensus = [...(tickers || [])]
        .filter(item => /^[A-Z0-9.-]+$/.test(String(item.ticker || '')))
        .sort(
            (a, b) => (
                (Number(b.num_holders) || 0) - (Number(a.num_holders) || 0)
                || (Number(b.total_value_across_funds) || 0)
                    - (Number(a.total_value_across_funds) || 0)
            )
        )
        .slice(0, 20);
    container.innerHTML = consensus.length
        ? consensus.map(item => `
            <button class="chip-btn"
                    title="${formatInt(item.num_holders)} current roster holders"
                    onclick="selectTickerChip('${escapeScreeningHtml(item.ticker)}')">
                ${escapeScreeningHtml(item.ticker)}
            </button>
        `).join('')
        : '<span class="text-dim">No current consensus holdings</span>';
}

async function loadConsensusTickerChips() {
    if (globalAllTickers.length) {
        renderConsensusTickerChips(globalAllTickers);
        return;
    }
    try {
        const response = await fetch('/api/ticker-view');
        if (!response.ok) {
            throw new Error(`Ticker request failed with HTTP ${response.status}`);
        }
        const result = await response.json();
        globalAllTickers = result.data || [];
        renderConsensusTickerChips(globalAllTickers);
    } catch (error) {
        console.error('Could not load consensus ticker chips:', error);
        renderConsensusTickerChips([]);
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
                <td><span class="badge ${t.num_holders >= 4 ? 'badge-consensus' : 'badge-neutral'} font-mono">${t.num_holders} funds</span></td>
                <td class="font-mono"><strong>$${formatNum(t.total_value_across_funds)}</strong></td>
                <td class="font-mono">${formatPct(t.median_weight)}</td>
                <td class="text-muted" style="font-size:0.8rem">${t.holders_summary || '--'}</td>
            </tr>
        `;
    });
    tbody.innerHTML = html || `<tr><td colspan="6" class="text-center py-4 text-muted">No matching securities found.</td></tr>`;
}

function navigateToTickerDetail(ticker) {
    const normalizedTicker = String(ticker || '').trim().toUpperCase();
    if (!normalizedTicker) return;
    window.location.assign(`/ticker/${encodeURIComponent(normalizedTicker)}`);
}

function makeTickerAxisLabelsInteractive(chart) {
    chart.querySelectorAll('.ytick').forEach(tick => {
        const ticker = tick.textContent.trim();
        if (!ticker) return;

        tick.setAttribute('role', 'link');
        tick.setAttribute('tabindex', '0');
        tick.setAttribute('aria-label', `View ${ticker} ticker details`);
        tick.onclick = () => navigateToTickerDetail(ticker);
        tick.onkeydown = event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                navigateToTickerDetail(ticker);
            }
        };
    });
}

function bindTickerRankingChart(chart) {
    if (typeof chart.removeAllListeners === 'function') {
        chart.removeAllListeners('plotly_click');
        chart.removeAllListeners('plotly_afterplot');
    }
    chart.on('plotly_click', event => {
        navigateToTickerDetail(event.points?.[0]?.customdata);
    });
    chart.on('plotly_afterplot', () => makeTickerAxisLabelsInteractive(chart));
    makeTickerAxisLabelsInteractive(chart);
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
        customdata: topByHolders.map(t => t.ticker),
        text: topByHolders.map(t => `${t.num_holders} funds`),
        textposition: 'outside',
        cliponaxis: false,
        type: 'bar',
        orientation: 'h',
        marker: { color: '#ec4899' },
        hovertemplate: '<b>%{y}</b><br>%{x} investors<br><b>Click to view details</b><extra></extra>'
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
        Plotly.newPlot(
            'popularity-chart',
            holderData,
            holderLayout,
            {displayModeBar: false, responsive: true}
        ).then(bindTickerRankingChart);
    }

    const valueData = [{
        x: topByValue.map(t => t.total_value_across_funds),
        y: topByValue.map(t => t.ticker),
        customdata: topByValue.map(t => t.ticker),
        text: topByValue.map(t => `$${formatNum(t.total_value_across_funds)}M`),
        textposition: 'outside',
        cliponaxis: false,
        type: 'bar',
        orientation: 'h',
        marker: { color: '#06b6d4' },
        hovertemplate: '<b>%{y}</b><br>$%{x:,.2f}M tracked<br><b>Click to view details</b><extra></extra>'
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
        Plotly.newPlot(
            'value-chart',
            valueData,
            valueLayout,
            {displayModeBar: false, responsive: true}
        ).then(bindTickerRankingChart);
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

function describeSentimentRegime(ticker, sentiment) {
    const score = sentiment.score;
    const breadth = sentiment.breadth_score;
    const conviction = sentiment.conviction_score;
    const meaningfulCount = (
        (sentiment.bullish_count || 0)
        + (sentiment.bearish_count || 0)
    );
    const label = ticker || 'This ticker';

    if (score !== null && score !== undefined) {
        let thresholdExplanation;
        if (score >= 60) {
            thresholdExplanation = `${formatSignedScore(score)} meets the +60 strongly bullish threshold`;
        } else if (score >= 25) {
            thresholdExplanation = `${formatSignedScore(score)} is at least +25 but below +60`;
        } else if (score <= -60) {
            thresholdExplanation = `${formatSignedScore(score)} meets the -60 strongly bearish threshold`;
        } else if (score <= -25) {
            thresholdExplanation = `${formatSignedScore(score)} is at most -25 but above -60`;
        } else {
            thresholdExplanation = `${formatSignedScore(score)} is above -25 and below +25`;
        }
        return (
            `Current ${label}: Meaningful Breadth ${formatSignedScore(breadth)} and `
            + `Relative Conviction ${formatSignedScore(conviction)} average to `
            + `Current Score ${formatSignedScore(score)}. The overall regime is `
            + `${sentiment.regime || 'NEUTRAL'} because ${thresholdExplanation}. `
            + 'Raw Share Activity and Dollar Flow Cross-Check do not enter this composite.'
        );
    }

    if (sentiment.indicative_score !== null && sentiment.indicative_score !== undefined) {
        return (
            `Current ${label}: the indicative score is `
            + `${formatSignedScore(sentiment.indicative_score)}, but only `
            + `${meaningfulCount} meaningful manager${meaningfulCount === 1 ? '' : 's'} `
            + 'qualified. At least 3 are required to publish an overall score and regime.'
        );
    }

    return (
        `Current ${label}: no overall score is available because meaningful breadth `
        + 'and relative conviction could not both be calculated. Raw activity alone '
        + 'does not produce a sentiment regime.'
    );
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
    const chartIds = [
        'ticker-investor-history-chart',
        'ticker-value-history-chart'
    ];
    if (!history?.length) {
        chartIds.forEach(id => {
            const chart = document.getElementById(id);
            if (chart) {
                chart.innerHTML = '<div class="stats-loading">Ownership history is unavailable for this ticker.</div>';
            }
        });
        return;
    }
    chartIds.forEach(id => {
        document.querySelector(`#${id} > .stats-loading`)?.remove();
    });

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
    ['ticker-sentiment-history-chart', 'ticker-conviction-heatmap'].forEach(id => {
        const chart = document.getElementById(id);
        if (chart) chart.innerHTML = `<div class="stats-loading">${message}</div>`;
    });
}

function renderWhaleSentiment(intelligence) {
    const history = intelligence.history || [];
    const sentimentData = intelligence.sentiment || {};
    const latest = sentimentData.latest || {};
    const latestActions = history.at(-1)?.actions || {};
    const regime = latest.regime || 'NO SIGNAL';
    const regimeElement = document.getElementById('td-whale-sentiment-regime');
    regimeElement.textContent = regime;
    regimeElement.className = `whale-sentiment-regime ${regime.toLowerCase().replaceAll(' ', '-')}`;
    const regimeExplanation = describeSentimentRegime(intelligence.ticker, latest);
    regimeElement.title = regimeExplanation;
    const sentimentInfo = document.getElementById('td-whale-sentiment-info');
    if (sentimentInfo) {
        sentimentInfo.dataset.tooltip = regimeExplanation;
    }

    document.getElementById('td-whale-sentiment-score').textContent = formatSignedScore(latest.score);
    document.getElementById('td-whale-sentiment-delta').textContent = latest.score_change === null || latest.score_change === undefined
        ? 'No prior comparison'
        : `${formatSignedScore(latest.score_change)} vs prior quarter`;
    const hasPublishedScore = latest.score !== null && latest.score !== undefined;
    document.getElementById('td-whale-activity').textContent = formatSignedScore(latest.activity_breadth_score);
    document.getElementById('td-whale-activity-counts').textContent = (
        `${latestActions.new || 0} new · ${latestActions.increased || 0} increased / `
        + `${latestActions.decreased || 0} decreased · ${latestActions.closed || 0} exited`
    );
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
    const latestNetFlow = history.at(-1)?.net_flow;
    const flowValue = document.getElementById('td-whale-flow-value');
    flowValue.className = `font-mono ${
        latestNetFlow > 0 ? 'text-green' : latestNetFlow < 0 ? 'text-red' : 'text-dim'
    }`;
    flowValue.textContent = latestNetFlow !== null && latestNetFlow !== undefined
        ? `${formatFlowMillions(latestNetFlow, true)} estimated net flow`
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
    const chartStartDate = periods[0] || null;
    const dailyPriceHistory = (intelligence.market?.price_history || [])
        .filter(point => (
            point.date
            && point.close !== null
            && point.close !== undefined
            && (!chartStartDate || point.date >= chartStartDate)
        ))
        .sort((a, b) => a.date.localeCompare(b.date));
    const priceDates = dailyPriceHistory.map(point => point.date);
    const priceValues = dailyPriceHistory.map(point => Number(point.close));
    const priceHoverContext = dailyPriceHistory.map(point => (
        `Daily close: ${formatCalendarDate(point.date)}`
    ));
    const latestMarketDate = intelligence.market_price_as_of;
    const latestMarketPrice = intelligence.market?.quote?.last_price;
    if (
        !priceDates.length
        && periods.length
    ) {
        priceDates.push(...periods);
        priceValues.push(
            ...history.map(item => item.quarter_end_price ?? null)
        );
        priceHoverContext.push(
            ...history.map(item => (
                `Quarter-end close: ${formatCalendarDate(item.period)}`
            ))
        );
    }
    if (
        priceDates.length
        && latestMarketDate
        && latestMarketPrice !== null
        && latestMarketPrice !== undefined
        && (!priceDates.length || latestMarketDate > priceDates.at(-1))
    ) {
        priceDates.push(latestMarketDate);
        priceValues.push(latestMarketPrice);
        priceHoverContext.push(
            `Latest market close: ${formatCalendarDate(latestMarketDate)}`
        );
    }
    const expectedFilingPoints = history.map(item => ({
        ...item,
        expected_filing_date: addCalendarDays(item.period, 45)
    }));
    const expectedFilingCustomData = expectedFilingPoints.map(item => {
        const expectedDateClose = closeOnOrBefore(
            dailyPriceHistory,
            item.expected_filing_date
        );
        return [
            formatCalendarDate(item.period),
            formatCalendarDate(item.expected_filing_date),
            item.sentiment?.score !== null
            && item.sentiment?.score !== undefined
                ? `${formatSignedScore(item.sentiment.score)} validated`
                : item.sentiment?.indicative_score !== null
                  && item.sentiment?.indicative_score !== undefined
                    ? `${formatSignedScore(item.sentiment.indicative_score)} indicative`
                    : 'No sentiment score',
            expectedDateClose === null
                ? 'Unavailable'
                : `$${formatNum(expectedDateClose)}`
        ];
    });

    document.querySelector('#ticker-sentiment-history-chart > .stats-loading')?.remove();
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
            y: history.map(
                item => item.sentiment?.breadth_score ?? null
            ),
            name: 'MEANINGFUL BREADTH',
            type: 'scatter',
            mode: 'lines',
            connectgaps: true,
            opacity: 0.55,
            line: {color: '#60a5fa', width: 1.5, dash: 'dot'},
            hovertemplate: 'Meaningful breadth: %{y:.1f}<extra></extra>'
        },
        {
            x: periods,
            y: history.map(
                item => item.sentiment?.conviction_score ?? null
            ),
            name: 'RELATIVE CONVICTION',
            type: 'scatter',
            mode: 'lines',
            connectgaps: true,
            opacity: 0.55,
            line: {color: '#c084fc', width: 1.5, dash: 'dot'},
            hovertemplate: 'Relative conviction: %{y:.1f}<extra></extra>'
        },
        {
            x: periods,
            y: history.map(item => item.sentiment?.activity_breadth_score ?? null),
            name: 'RAW ACTIVITY',
            type: 'scatter',
            mode: 'lines',
            connectgaps: true,
            opacity: 0.35,
            line: {color: '#64748b', width: 1.2, dash: 'dash'},
            hovertemplate: 'Raw activity breadth: %{y:.1f}<extra></extra>'
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
        },
        {
            x: expectedFilingPoints.map(item => item.expected_filing_date),
            y: expectedFilingPoints.map(() => 0.04),
            yaxis: 'y3',
            name: 'EXPECTED 13F DEADLINE',
            type: 'scatter',
            mode: 'markers',
            marker: {
                symbol: 'triangle-up',
                size: 10,
                color: '#22d3ee',
                line: {color: '#ecfeff', width: 1}
            },
            customdata: expectedFilingCustomData,
            hovertemplate:
                '<b>Expected 13F availability</b><br>' +
                'Report period ended: %{customdata[0]}<br>' +
                'Standard 45-day mark: %{customdata[1]}<br>' +
                'Sentiment: %{customdata[2]}<br>' +
                'Stock close on/before 45-day mark: %{customdata[3]}' +
                '<extra></extra>'
        },
        {
            x: priceDates,
            y: priceValues,
            name: 'STOCK PRICE',
            type: 'scatter',
            mode: 'lines',
            yaxis: 'y2',
            connectgaps: false,
            line: {color: '#f59e0b', width: 1.7},
            customdata: priceHoverContext,
            hovertemplate:
                'Stock price: $%{y:,.2f}<br>' +
                '%{customdata}' +
                '<extra></extra>'
        },
        {
            x: periods,
            y: history.map(item => item.quarter_end_price ?? null),
            type: 'scatter',
            mode: 'markers',
            yaxis: 'y2',
            showlegend: false,
            hoverinfo: 'skip',
            marker: {
                symbol: 'circle',
                size: 6,
                color: '#fbbf24',
                line: {color: '#78350f', width: 1}
            }
        },
        {
            x: latestMarketDate ? [latestMarketDate] : [],
            y: latestMarketPrice !== null && latestMarketPrice !== undefined
                ? [latestMarketPrice]
                : [],
            type: 'scatter',
            mode: 'markers',
            yaxis: 'y2',
            showlegend: false,
            hoverinfo: 'skip',
            marker: {
                symbol: 'diamond',
                size: 8,
                color: '#fbbf24',
                line: {color: '#78350f', width: 1}
            }
        }
    ], {
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: {color: '#cbd5e1', family: 'Inter, sans-serif'},
        margin: {t: 20, b: 126, l: 58, r: 70},
        hovermode: 'x unified',
        legend: {
            orientation: 'h',
            x: 0,
            xanchor: 'left',
            y: -0.24,
            yanchor: 'top'
        },
        xaxis: {gridcolor: '#1e293b', tickangle: -35},
        yaxis: {
            title: 'Sentiment (-100 to +100)',
            range: [-110, 110],
            gridcolor: '#1e293b',
            zeroline: true,
            zerolinecolor: '#64748b'
        },
        yaxis2: {
            title: 'Stock Price ($)',
            overlaying: 'y',
            side: 'right',
            showgrid: false,
            tickprefix: '$',
            separatethousands: true
        },
        yaxis3: {
            overlaying: 'y',
            visible: false,
            fixedrange: true,
            range: [0, 1]
        },
        shapes: [
            {type: 'rect', xref: 'paper', x0: 0, x1: 1, y0: 25, y1: 100, fillcolor: 'rgba(34,197,94,0.055)', line: {width: 0}, layer: 'below'},
            {type: 'rect', xref: 'paper', x0: 0, x1: 1, y0: -25, y1: 25, fillcolor: 'rgba(148,163,184,0.035)', line: {width: 0}, layer: 'below'},
            {type: 'rect', xref: 'paper', x0: 0, x1: 1, y0: -100, y1: -25, fillcolor: 'rgba(239,68,68,0.055)', line: {width: 0}, layer: 'below'},
            {type: 'line', xref: 'paper', yref: 'y3', x0: 0, x1: 1, y0: 0.04, y1: 0.04, line: {color: 'rgba(34,211,238,0.18)', width: 1}, layer: 'below'},
            ...expectedFilingPoints.map(item => ({
                type: 'line',
                xref: 'x',
                yref: 'paper',
                x0: item.expected_filing_date,
                x1: item.expected_filing_date,
                y0: 0,
                y1: 1,
                line: {
                    color: 'rgba(34,211,238,0.22)',
                    width: 1,
                    dash: 'dot'
                },
                layer: 'below'
            })),
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

    heatmap.querySelector(':scope > .stats-loading')?.remove();
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

function organizeTickerPanels() {
    const assignments = {
        decision: ['ticker-decision-heading', 'ticker-decision-grid'],
        market: ['ticker-market-summary', 'ticker-news-card', 'ticker-market-chart-card'],
        'whale-activity': ['ticker-alpha-sentiment-card', 'ticker-conviction-card'],
        ownership: ['ticker-ownership-heading', 'ticker-history-grid', 'ticker-ownership-charts', 'ticker-holders-card'],
        pairs: ['ticker-pair-card']
    };
    Object.entries(assignments).forEach(([panelName, ids]) => {
        const panel = document.querySelector(`[data-ticker-panel="${panelName}"]`);
        if (!panel) return;
        ids.forEach(id => {
            const element = document.getElementById(id);
            if (element && element.parentElement !== panel) panel.appendChild(element);
        });
    });
}

function resizeTickerPanelCharts(panelName) {
    const chartIds = {
        decision: ['ticker-awfi-history-chart'],
        market: [],
        'whale-activity': ['ticker-sentiment-history-chart', 'ticker-conviction-heatmap'],
        ownership: ['ticker-investor-history-chart', 'ticker-value-history-chart', 'ticker-holder-concentration-chart', 'ticker-bar-chart'],
        pairs: []
    }[panelName] || [];
    window.requestAnimationFrame(() => {
        chartIds.forEach(id => {
            const chart = document.getElementById(id);
            if (chart?.data && window.Plotly) Plotly.Plots.resize(chart);
        });
        if (panelName === 'market') window.dispatchEvent(new Event('resize'));
    });
}

function switchTickerTab(tabName, updateHash = true) {
    if (!Object.hasOwn(tickerTabDescriptions, tabName)) tabName = 'decision';
    currentTickerTab = tabName;
    document.querySelectorAll('[data-ticker-tab]').forEach(tab => {
        const selected = tab.dataset.tickerTab === tabName;
        tab.classList.toggle('is-active', selected);
        tab.setAttribute('aria-selected', selected ? 'true' : 'false');
        tab.tabIndex = selected ? 0 : -1;
        if (selected && updateHash) {
            tab.scrollIntoView({block: 'nearest', inline: 'nearest'});
        }
    });
    document.querySelectorAll('[data-ticker-panel]').forEach(panel => {
        panel.hidden = panel.dataset.tickerPanel !== tabName;
    });
    const description = document.getElementById('ticker-tab-description');
    if (description) description.textContent = tickerTabDescriptions[tabName];
    if (updateHash) {
        window.history.replaceState(
            null,
            '',
            `${window.location.pathname}#ticker-${tabName}`
        );
    }
    resizeTickerPanelCharts(tabName);
}

function initializeTickerWorkspace() {
    organizeTickerPanels();
    const navbar = document.querySelector('.navbar');
    if (navbar && window.ResizeObserver) {
        const updateNavHeight = () => {
            document.documentElement.style.setProperty(
                '--nav-sticky-height',
                `${navbar.getBoundingClientRect().height}px`
            );
        };
        new ResizeObserver(updateNavHeight).observe(navbar);
        updateNavHeight();
    }
    const tabs = [...document.querySelectorAll('[data-ticker-tab]')];
    if (!tabs.length) return;
    tabs.forEach((tab, index) => {
        tab.addEventListener('click', () => switchTickerTab(tab.dataset.tickerTab));
        tab.addEventListener('keydown', event => {
            let nextIndex = null;
            if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
            if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
            if (event.key === 'Home') nextIndex = 0;
            if (event.key === 'End') nextIndex = tabs.length - 1;
            if (nextIndex === null) return;
            event.preventDefault();
            tabs[nextIndex].focus();
            switchTickerTab(tabs[nextIndex].dataset.tickerTab);
        });
    });
    const hashTab = window.location.hash.replace('#ticker-', '');
    switchTickerTab(
        Object.hasOwn(tickerTabDescriptions, hashTab) ? hashTab : 'decision',
        false
    );
}

function renderTickerAwfiHistory(
    history = tickerAwfiHistory,
    intelligence = tickerAwfiIntelligence
) {
    tickerAwfiHistory = (history || []).slice(-20);
    tickerAwfiIntelligence = intelligence || null;
    const chart = document.getElementById('ticker-awfi-history-chart');
    if (!chart || !window.Plotly) return;
    if (tickerAwfiHistory.length < 2) {
        if (chart.data) Plotly.purge(chart);
        const onlyPeriod = tickerAwfiHistory[0]?.period;
        chart.innerHTML = onlyPeriod
            ? `<div class="stats-loading">Historical AWFI needs at least two scored filing periods. Only ${escapeHtml(formatFilingPeriodLabel(onlyPeriod))} is currently available for this ticker.</div>`
            : '<div class="stats-loading">No historical AWFI filing periods are available for this ticker.</div>';
        return;
    }
    const periods = tickerAwfiHistory.map(item => item.period);
    const chartStartDate = periods[0] || null;
    const market = tickerAwfiIntelligence?.market || {};
    const dailyPriceHistory = (market.price_history || [])
        .filter(point => (
            point.date
            && point.close !== null
            && point.close !== undefined
            && (!chartStartDate || point.date >= chartStartDate)
        ))
        .sort((a, b) => a.date.localeCompare(b.date));
    const priceDates = dailyPriceHistory.map(point => point.date);
    const priceValues = dailyPriceHistory.map(point => Number(point.close));
    const priceHoverContext = dailyPriceHistory.map(point => (
        `Daily close: ${formatCalendarDate(point.date)}`
    ));
    const latestMarketDate = tickerAwfiIntelligence?.market_price_as_of;
    const latestMarketPrice = market.quote?.last_price;
    if (
        latestMarketDate
        && latestMarketPrice !== null
        && latestMarketPrice !== undefined
        && (!priceDates.length || latestMarketDate > priceDates.at(-1))
    ) {
        priceDates.push(latestMarketDate);
        priceValues.push(Number(latestMarketPrice));
        priceHoverContext.push(
            `Latest market close: ${formatCalendarDate(latestMarketDate)}`
        );
    }
    const expectedFilingPoints = tickerAwfiHistory.map(item => ({
        period: item.period,
        expected_filing_date: addCalendarDays(item.period, 45),
        score_as_of_date: tickerAwfiHorizons
            .map(horizon => item.scores?.[horizon.key]?.as_of_date)
            .find(Boolean) || null
    }));
    const expectedFilingCustomData = expectedFilingPoints.map(item => {
        const expectedDateClose = closeOnOrBefore(
            dailyPriceHistory,
            item.expected_filing_date
        );
        return [
            formatCalendarDate(item.period),
            formatCalendarDate(item.expected_filing_date),
            item.score_as_of_date
                ? formatCalendarDate(item.score_as_of_date)
                : 'Unavailable',
            expectedDateClose === null
                ? 'Unavailable'
                : `$${formatNum(expectedDateClose)}`
        ];
    });
    const traces = tickerAwfiHorizons.map(horizon => ({
        x: periods,
        y: tickerAwfiHistory.map(item => item.scores?.[horizon.key]?.score ?? null),
        customdata: tickerAwfiHistory.map(item => {
            const score = item.scores?.[horizon.key];
            return [
                score?.research_signal || score?.signal || 'UNAVAILABLE',
                score?.feature_date || score?.as_of_date || '—'
            ];
        }),
        name: horizon.label,
        type: 'scatter',
        mode: 'lines+markers',
        connectgaps: false,
        line: {color: horizon.color, width: 2.2},
        marker: {
            color: horizon.color,
            size: 5,
            line: {color: '#0f172a', width: 1}
        },
        opacity: 0.88,
        hovertemplate: `<b>${horizon.label}</b><br>%{x}<br>Research score %{y:.1f}<br>Raw research verdict %{customdata[0]}<br>Market data through %{customdata[1]}<extra></extra>`
    }));
    traces.push({
        x: expectedFilingPoints.map(item => item.expected_filing_date),
        y: expectedFilingPoints.map(() => 0.04),
        yaxis: 'y3',
        name: 'EXPECTED 13F DEADLINE',
        type: 'scatter',
        mode: 'markers',
        marker: {
            symbol: 'triangle-up',
            size: 10,
            color: '#22d3ee',
            line: {color: '#ecfeff', width: 1}
        },
        customdata: expectedFilingCustomData,
        hovertemplate:
            '<b>Expected 13F availability</b><br>' +
            'Report period ended: %{customdata[0]}<br>' +
            'Standard 45-day mark: %{customdata[1]}<br>' +
            'AWFI score as of: %{customdata[2]}<br>' +
            'Stock close on/before 45-day mark: %{customdata[3]}' +
            '<extra></extra>'
    });
    if (priceDates.length) {
        traces.push({
            x: priceDates,
            y: priceValues,
            name: 'STOCK PRICE',
            type: 'scatter',
            mode: 'lines',
            yaxis: 'y2',
            connectgaps: false,
            line: {color: '#f59e0b', width: 1.7},
            customdata: priceHoverContext,
            hovertemplate:
                'Stock price: $%{y:,.2f}<br>' +
                '%{customdata}' +
                '<extra></extra>'
        });
    }
    if (
        latestMarketDate
        && latestMarketPrice !== null
        && latestMarketPrice !== undefined
    ) {
        traces.push({
            x: latestMarketDate ? [latestMarketDate] : [],
            y: latestMarketPrice !== null && latestMarketPrice !== undefined
                ? [latestMarketPrice]
                : [],
            type: 'scatter',
            mode: 'markers',
            yaxis: 'y2',
            showlegend: false,
            hoverinfo: 'skip',
            marker: {
                symbol: 'diamond',
                size: 8,
                color: '#fbbf24',
                line: {color: '#78350f', width: 1}
            }
        });
    }
    chart.querySelector(':scope > .stats-loading')?.remove();
    Plotly.react(chart, traces, {
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(2,6,23,0.22)',
        font: {color: '#cbd5e1', family: 'Inter, sans-serif'},
        margin: {t: 18, r: priceDates.length ? 70 : 26, b: 108, l: 58},
        hovermode: 'x unified',
        legend: {
            orientation: 'h',
            x: 0,
            xanchor: 'left',
            y: -0.24,
            yanchor: 'top'
        },
        xaxis: {
            type: 'date',
            gridcolor: 'rgba(51,65,85,0.42)',
            tickformat: '%Y',
            tickangle: -35,
            rangeslider: {visible: false}
        },
        yaxis: {
            title: 'AWFI score',
            range: [-105, 105],
            dtick: 25,
            gridcolor: 'rgba(51,65,85,0.42)',
            zerolinecolor: '#64748b'
        },
        yaxis2: {
            title: 'Stock Price ($)',
            overlaying: 'y',
            side: 'right',
            visible: priceDates.length > 0,
            showgrid: false,
            tickprefix: '$',
            separatethousands: true
        },
        yaxis3: {
            overlaying: 'y',
            visible: false,
            fixedrange: true,
            range: [0, 1]
        },
        shapes: [
            {
                type: 'line',
                xref: 'paper',
                yref: 'y3',
                x0: 0,
                x1: 1,
                y0: 0.04,
                y1: 0.04,
                line: {color: 'rgba(34,211,238,0.18)', width: 1},
                layer: 'below'
            },
            ...expectedFilingPoints.map(item => ({
                type: 'line',
                xref: 'x',
                yref: 'paper',
                x0: item.expected_filing_date,
                x1: item.expected_filing_date,
                y0: 0,
                y1: 1,
                line: {
                    color: 'rgba(34,211,238,0.22)',
                    width: 1,
                    dash: 'dot'
                },
                layer: 'below'
            }))
        ]
    }, {displayModeBar: false, responsive: true});
}

function formatTickerAwfiContribution(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return '—';
    return `${parsed > 0 ? '+' : ''}${parsed.toFixed(1)}`;
}

function renderTickerAwfiDrivers(awfi) {
    const contributions = awfi.component_contributions;
    if (!contributions) {
        return '<span class="ticker-awfi-driver-unavailable">Component detail is unavailable for this historical research snapshot.</span>';
    }
    const drivers = [
        ['Whale sentiment', contributions.institutional],
        ['Purchase actions', contributions.purchase_actions],
        ['Portfolio conviction', contributions.portfolio_conviction],
        ['Technical', contributions.technical]
    ];
    return `
        <div class="ticker-awfi-drivers">
            ${drivers.map(([label, value]) => {
                const parsed = Number(value);
                const direction = parsed > 0
                    ? 'positive'
                    : parsed < 0
                        ? 'negative'
                        : 'neutral';
                return `
                    <span>
                        ${label}
                        <strong class="font-mono ${direction}">${formatTickerAwfiContribution(value)}</strong>
                    </span>
                `;
            }).join('')}
        </div>
    `;
}

function renderTickerAwfi(
    scores = tickerAwfiScores,
    metadata = tickerAwfiMetadata,
    history = tickerAwfiHistory
) {
    tickerAwfiScores = scores || {};
    tickerAwfiMetadata = metadata || {};
    tickerAwfiHistory = history || [];
    const state = document.getElementById('td-awfi-state');
    const rows = document.getElementById('td-awfi-horizon-rows');
    const filingPeriod = document.getElementById('td-awfi-filing-period');
    const marketDate = document.getElementById('td-awfi-market-date');
    const disclaimer = document.getElementById('td-awfi-disclaimer');
    const liveRegion = document.getElementById('td-awfi-live-region');
    if (!state || !rows || !filingPeriod || !marketDate) return;
    renderTickerAwfiHistory();

    const metadataState = String(metadata.state || '').toUpperCase();
    const availableScores = tickerAwfiHorizons
        .map(horizon => tickerAwfiScores[horizon.key])
        .filter(Boolean);
    const latestFeatureDate = metadata.market_data_date || availableScores
        .map(awfi => awfi.feature_date || awfi.as_of_date)
        .filter(Boolean)
        .sort()
        .at(-1);
    state.className = 'ticker-awfi-state';
    state.textContent = metadataState === 'LIVE'
        ? 'LIVE MARKET UPDATE'
        : metadataState === 'READY'
            ? 'HISTORICAL RESEARCH'
            : metadataState || 'UNAVAILABLE';
    filingPeriod.textContent = metadata.requested_period
        ? formatFilingPeriodLabel(metadata.requested_period)
        : '—';
    marketDate.textContent = latestFeatureDate
        ? formatCalendarDate(latestFeatureDate)
        : '—';
    if (disclaimer) {
        disclaimer.textContent = 'Research model only. Each horizon uses its historically selected score profile and threshold; negative signals mean avoid or review, not automatic liquidation.';
    }

    rows.innerHTML = tickerAwfiHorizons.map(horizon => {
        const awfi = tickerAwfiScores[horizon.key];
        if (!awfi) {
            const reason = metadata.reason || `No ${horizon.duration} score is available.`;
            return `
                <tr class="unavailable">
                    <th scope="row" data-label="Horizon">
                        <strong>${horizon.label}</strong>
                        <span>${horizon.duration}</span>
                    </th>
                    <td data-label="AWFI score" class="font-mono">—</td>
                    <td data-label="Signal"><span class="ticker-awfi-signal unavailable">Unavailable</span></td>
                    <td data-label="Weighted score drivers" class="ticker-awfi-read">${escapeHtml(reason)}</td>
                </tr>
            `;
        }
        const score = Number(awfi.score);
        const rawSignal = String(awfi.signal || 'HOLD').toUpperCase();
        const signal = ['BUY', 'SELL', 'HOLD'].includes(rawSignal)
            ? rawSignal
            : 'HOLD';
        const signalLabel = signal === 'SELL'
            ? 'SELL / AVOID'
            : signal;
        const scoreText = Number.isFinite(score)
            ? `${score > 0 ? '+' : ''}${score.toFixed(1)}`
            : '—';
        const scoreDirection = score > 0
            ? 'positive'
            : score < 0
                ? 'negative'
                : 'neutral';
        return `
            <tr class="${signal.toLowerCase()}">
                <th scope="row" data-label="Horizon">
                    <strong>${horizon.label}</strong>
                    <span>${horizon.duration}</span>
                </th>
                <td data-label="AWFI score">
                    <strong class="ticker-awfi-table-score font-mono ${scoreDirection}">${scoreText}</strong>
                </td>
                <td data-label="Signal">
                    <span class="ticker-awfi-signal ${signal.toLowerCase()}">${signalLabel}</span>
                    <small class="ticker-awfi-signal-threshold font-mono">
                        BUY ≥ +${formatTickerAwfiContribution(awfi.positive_threshold).replace('+', '')}
                        · AVOID ≤ -${formatTickerAwfiContribution(awfi.negative_threshold).replace('+', '')}
                    </small>
                </td>
                <td data-label="Weighted score drivers" class="ticker-awfi-read">${renderTickerAwfiDrivers(awfi)}</td>
            </tr>
        `;
    }).join('');

    if (liveRegion) {
        const availableCount = tickerAwfiHorizons.filter(
            horizon => tickerAwfiScores[horizon.key]
        ).length;
        liveRegion.textContent = `AWFI updated with ${availableCount} of 4 horizons available through ${formatCalendarDate(latestFeatureDate)}.`;
    }
}

function scheduleTickerAwfiHistoryRefresh(ticker) {
    clearTimeout(tickerAwfiHistoryRefreshTimer);
    if (tickerAwfiHistoryRefreshAttempts >= 80) {
        return;
    }
    tickerAwfiHistoryRefreshTimer = setTimeout(async () => {
        tickerAwfiHistoryRefreshAttempts += 1;
        try {
            const response = await fetch(
                `/api/ticker/${encodeURIComponent(ticker)}/awfi-history`,
                {cache: 'no-store'}
            );
            if (response.ok) {
                const result = await response.json();
                const snapshotChanged = (
                    result.snapshot_version !== null
                    && result.snapshot_version !== tickerAwfiHistorySnapshotVersion
                );
                tickerAwfiHistorySnapshotVersion = result.snapshot_version;
                if (snapshotChanged) {
                    loadTickerDetail(ticker);
                    return;
                }
                if (!['checking', 'building', 'external_build'].includes(
                    result.refresh_state
                )) {
                    return;
                }
            }
        } catch (error) {
            console.warn('AWFI history refresh is not ready:', error);
        }
        scheduleTickerAwfiHistoryRefresh(ticker);
    }, 15000);
}

function formatValuationMethodMetric(method) {
    const value = method.metric_value;
    if (value === null || value === undefined || value === '') return 'Unavailable';
    if (method.metric_format === 'currency') return `$${formatNum(value)}`;
    if (method.metric_format === 'percent') return formatPct(value);
    return String(value);
}

const valuationCategoryDescriptions = Object.freeze({
    decision: 'The methods selected for this company’s business model and current data.',
    intrinsic: 'Cash-flow, dividend, and economic-profit methods that estimate value independently of market peers.',
    relative: 'Market-pricing methods that compare earnings, growth, book value, revenue, or operating profit.',
    graham: 'Benjamin Graham’s defensive, growth-adjusted, and liquidation-oriented valuation checks.',
    special: 'Asset, segment, property, and option-based methods used for specialized company structures.',
    all: 'Every supported valuation method, including unavailable and non-applicable frameworks.',
});

function valuationMethodsForCategory(methods, framework, category) {
    const recommendedIds = new Set(framework.recommended_method_ids || []);
    if (category === 'decision') {
        return methods.filter(method => recommendedIds.has(method.id));
    }
    if (category === 'intrinsic') {
        return methods.filter(method => (
            method.family === 'Absolute'
            || method.family === 'Expectations'
        ));
    }
    if (category === 'relative') {
        return methods.filter(method => (
            method.id === 'normalized_pe'
            || method.id === 'relative_multiples'
            || method.id === 'enterprise_multiples'
        ));
    }
    if (category === 'graham') {
        return methods.filter(method => (
            method.id === 'graham_number'
            || method.id === 'graham_revised_growth'
            || method.id === 'graham_conservative_growth'
            || method.id === 'ncav'
        ));
    }
    if (category === 'special') {
        return methods.filter(method => (
            method.id === 'tangible_asset_value'
            || method.id === 'sotp'
            || method.id === 'reit_nav_affo'
            || method.id === 'real_options'
        ));
    }
    return methods;
}

function renderValuationAgreement(methods, anchorName) {
    const pricedMethods = methods.filter(method => (
        method.value !== null
        && method.value !== undefined
        && Number(method.value) > 0
        && ['UNDERVALUED', 'NEUTRAL', 'OVERVALUED'].includes(method.assessment)
    ));
    const counts = {
        UNDERVALUED: pricedMethods.filter(method => method.assessment === 'UNDERVALUED').length,
        NEUTRAL: pricedMethods.filter(method => method.assessment === 'NEUTRAL').length,
        OVERVALUED: pricedMethods.filter(method => method.assessment === 'OVERVALUED').length,
    };
    const total = pricedMethods.length;
    const agreementElement = document.getElementById('td-valuation-agreement');
    const detailElement = document.getElementById('td-valuation-agreement-detail');
    if (!total) {
        agreementElement.textContent = 'NO PRICED METHODS';
        detailElement.textContent = anchorName
            ? `Diagnostic only · Primary method: ${anchorName}`
            : 'Diagnostic only · No calculated fair-value methods are available.';
    } else {
        const orderedStates = Object.entries(counts)
            .sort((left, right) => right[1] - left[1]);
        const dominant = (
            orderedStates[0][1] > orderedStates[1][1]
                ? orderedStates[0][0]
                : 'MIXED'
        );
        agreementElement.textContent = dominant === 'MIXED'
            ? `MIXED · ${total} METHODS`
            : `${orderedStates[0][1]}/${total} ${dominant}`;
        detailElement.textContent = (
            `Diagnostic only · ${counts.UNDERVALUED} undervalued · ${counts.NEUTRAL} neutral · `
            + `${counts.OVERVALUED} overvalued`
        );
    }
    document.getElementById('td-agreement-undervalued').style.width = (
        `${total ? (counts.UNDERVALUED / total) * 100 : 0}%`
    );
    document.getElementById('td-agreement-neutral').style.width = (
        `${total ? (counts.NEUTRAL / total) * 100 : 0}%`
    );
    document.getElementById('td-agreement-overvalued').style.width = (
        `${total ? (counts.OVERVALUED / total) * 100 : 0}%`
    );
}

function renderValuationMethodCards(valuation) {
    const framework = valuation.recommended_framework || {};
    const methods = Array.isArray(valuation.methods) ? valuation.methods : [];
    const filteredMethods = valuationMethodsForCategory(
        methods,
        framework,
        tickerValuationCategory,
    );
    const availableCount = methods.filter(method => method.status === 'AVAILABLE').length;
    document.getElementById('td-valuation-model-count').textContent = (
        `${filteredMethods.length} shown · ${availableCount}/${methods.length} data-ready`
    );
    document.getElementById('td-valuation-category-description').textContent = (
        valuationCategoryDescriptions[tickerValuationCategory]
        || valuationCategoryDescriptions.all
    );
    document.querySelectorAll('.valuation-method-tab').forEach(button => {
        const selected = button.dataset.valuationCategory === tickerValuationCategory;
        button.classList.toggle('is-active', selected);
        button.setAttribute('aria-pressed', String(selected));
    });

    const categoryCounts = {
        decision: valuationMethodsForCategory(methods, framework, 'decision').length,
        intrinsic: valuationMethodsForCategory(methods, framework, 'intrinsic').length,
        relative: valuationMethodsForCategory(methods, framework, 'relative').length,
        graham: valuationMethodsForCategory(methods, framework, 'graham').length,
        special: valuationMethodsForCategory(methods, framework, 'special').length,
        all: methods.length,
    };
    Object.entries(categoryCounts).forEach(([category, count]) => {
        const countElement = document.getElementById(`td-valuation-count-${category}`);
        if (countElement) countElement.textContent = String(count);
    });

    const container = document.getElementById('td-valuation-methods');
    if (!filteredMethods.length) {
        container.innerHTML = '<div class="valuation-method-empty">No methods belong to this category for the current stock.</div>';
        return;
    }

    container.innerHTML = filteredMethods.map(method => {
        const isAnchor = method.id === framework.anchor_method_id;
        const methodologyTooltip = [
            method.methodology || method.summary || 'Methodology unavailable.',
            method.caveat ? `Main limitation: ${method.caveat}` : '',
        ].filter(Boolean).join(' ');
        const decisionTone = (method.decision_tone || 'muted')
            .toLowerCase()
            .replaceAll(' ', '-');
        const rangeText = (
            method.low !== null
            && method.low !== undefined
            && method.high !== null
            && method.high !== undefined
        )
            ? `$${formatNum(method.low)} – $${formatNum(method.high)}`
            : '';
        return `
            <article class="valuation-method ${isAnchor ? 'is-anchor' : ''}">
                <div class="valuation-method-topline">
                    <span>${escapeHtml(method.family || 'Valuation')}</span>
                    <span class="valuation-method-fit">${escapeHtml(String(method.fit || 0))}% fit</span>
                </div>
                <div class="valuation-method-title">
                    <strong>${escapeHtml(method.name)}</strong>
                    ${isAnchor ? '<span>PRIMARY</span>' : ''}
                </div>
                <div class="valuation-method-output">
                    <span>${escapeHtml(method.metric_label || method.status || 'Result')}</span>
                    <strong class="font-mono">${escapeHtml(formatValuationMethodMetric(method))}</strong>
                    ${rangeText ? `<small>${escapeHtml(rangeText)}</small>` : ''}
                </div>
                <p>${escapeHtml(method.summary || '')}</p>
                <div class="valuation-method-footer">
                    <div class="valuation-method-read">
                        <span>Method read</span>
                        <strong class="${escapeHtml(decisionTone)}">${escapeHtml(method.decision_read || 'MORE DATA NEEDED')}</strong>
                        <small>${escapeHtml(method.decision_detail || 'This method cannot support a reliable conclusion with current data.')}</small>
                    </div>
                    <span class="stats-info" tabindex="0" role="img" aria-label="${escapeHtml(method.name)} methodology" data-tooltip="${escapeHtml(methodologyTooltip)}">i</span>
                </div>
            </article>
        `;
    }).join('');
}

function setValuationMethodCategory(category) {
    if (!Object.hasOwn(valuationCategoryDescriptions, category)) return;
    tickerValuationCategory = category;
    const valuation = tickerAwfiIntelligence?.market?.valuation;
    if (valuation) renderValuationMethodCards(valuation);
}

function renderValuationMethods(valuation) {
    const framework = valuation.recommended_framework || {};
    const frameworkName = framework.name || 'No reliable framework available';
    const anchorName = framework.anchor_method_name;
    document.getElementById('td-valuation-framework').textContent = frameworkName;
    document.getElementById('td-valuation-reason').textContent = (
        framework.reason
        || 'The available market and statement data do not support a reliable recommendation.'
    );
    document.getElementById('td-fair-value-label').textContent = anchorName
        ? `${anchorName} Fair Value`
        : 'Primary Fair-Value Estimate';

    const structural = framework.structural_method;
    const dataWarnings = Array.isArray(framework.data_warnings)
        ? framework.data_warnings
        : [];
    const structuralElement = document.getElementById('td-valuation-structural');
    const valuationNotes = [];
    if (structural) {
        valuationNotes.push(`
            <div class="valuation-data-note">
                <strong>${escapeHtml(structural.name)}</strong>
                <span>${escapeHtml(structural.status)}</span>
                <p>${escapeHtml(structural.reason)}</p>
            </div>
        `);
    }
    dataWarnings.forEach(warning => {
        valuationNotes.push(`
            <div class="valuation-data-note valuation-data-warning">
                <strong>Data-basis limitation</strong>
                <span>VALUES DISABLED</span>
                <p>${escapeHtml(warning)}</p>
            </div>
        `);
    });
    if (valuationNotes.length) {
        structuralElement.hidden = false;
        structuralElement.innerHTML = valuationNotes.join('');
    } else {
        structuralElement.hidden = true;
        structuralElement.replaceChildren();
    }

    const range = valuation.valuation_range || {};
    document.getElementById('td-valuation-range').textContent = (
        range.low !== null
        && range.low !== undefined
        && range.high !== null
        && range.high !== undefined
    )
        ? `Modeled range $${formatNum(range.low)} – $${formatNum(range.high)}`
        : 'Scenario range unavailable';

    const methods = Array.isArray(valuation.methods) ? valuation.methods : [];
    renderValuationAgreement(methods, anchorName);
    renderValuationMethodCards(valuation);
}

function renderTickerIntelligence(intelligence) {
    tickerAwfiIntelligence = intelligence;
    const market = intelligence.market || {};
    const quote = market.quote || {};
    const metrics = market.metrics || {};
    const profile = market.profile || {};
    const valuation = market.valuation || {};
    const technical = market.technical || {};
    const decision = intelligence.decision_support || {};
    const latest = intelligence.latest || {};
    const currentPrice = quote.last_price;
    const dayChange = quote.day_change;
    const dayChangePct = quote.day_change_pct;
    renderTickerNews(market.news || []);

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
    const lowDistance = intelligence.price_above_52_week_low_pct;
    const lowDistanceElement = document.getElementById('td-52w-low-distance');
    if (lowDistanceElement) {
        const hasLowDistance = lowDistance !== null && lowDistance !== undefined;
        const lowDistanceClass = !hasLowDistance
            ? 'text-dim'
            : lowDistance >= 0
                ? 'text-green'
                : 'text-red';
        document.getElementById('td-52w-low-value').textContent = hasLowDistance
            ? `$${formatNum(quote.year_low)}`
            : '—';
        document.getElementById('td-52w-low-date').textContent = (
            hasLowDistance && quote.year_low_date
                ? `on ${formatCalendarDate(quote.year_low_date)}`
                : ''
        );
        const lowPercentElement = document.getElementById('td-52w-low-percent');
        lowPercentElement.className = `ticker-price-low-percent ${lowDistanceClass}`;
        lowPercentElement.textContent = hasLowDistance
            ? `${formatPct(lowDistance)} above`
            : 'Unavailable';
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
        basisGapElement.className = `ticker-basis-gap font-mono ${basisGap > 0 ? 'text-green' : basisGap < 0 ? 'text-red' : 'text-dim'}`;
        basisGapElement.textContent = basisGap !== null && basisGap !== undefined
            ? `${basisGap > 0 ? '+' : ''}${formatPct(basisGap)} vs estimate`
            : '20-quarter model unavailable';
    }

    document.getElementById('td-filing-period').textContent = latest.period
        ? `Filing period ${latest.period}`
        : 'Filing period unavailable';
    document.getElementById('td-gross-inflow').textContent = formatFlowMillions(latest.gross_inflow);
    document.getElementById('td-gross-outflow').textContent = formatFlowMillions(latest.gross_outflow);

    const valuationStatus = document.getElementById('td-valuation-status');
    const valuationState = (valuation.assessment || 'UNAVAILABLE').toLowerCase();
    valuationStatus.className = `decision-status ${valuationState}`;
    valuationStatus.textContent = valuation.assessment || 'UNAVAILABLE';
    document.getElementById('td-fair-value').textContent = valuation.fair_value ? `$${formatNum(valuation.fair_value)}` : '—';
    document.getElementById('td-purchase-price').textContent = valuation.purchase_price_20pct_mos ? `$${formatNum(valuation.purchase_price_20pct_mos)}` : '—';
    renderValuationMethods(valuation);

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
    renderTickerAwfiHistory(tickerAwfiHistory, intelligence);
    renderWhaleSentiment(intelligence);
}

function renderTickerNews(news) {
    const container = document.getElementById('td-news-list');
    const badge = document.getElementById('td-news-badge');
    if (!container) return;

    const articles = (news || [])
        .map(article => ({
            ...article,
            safeUrl: safeExternalNewsUrl(article.url)
        }))
        .filter(article => article.title && article.safeUrl)
        .slice(0, 5);

    if (!articles.length) {
        if (badge) badge.textContent = 'No direct matches';
        container.innerHTML = '<div class="ticker-news-empty">No directly impactful company news passed the relevance filter.</div>';
        return;
    }

    if (badge) {
        badge.textContent = `${articles.length} direct-impact article${articles.length === 1 ? '' : 's'}`;
    }
    container.innerHTML = articles.map((article, index) => `
        <a class="ticker-news-item"
           href="${escapeHtml(article.safeUrl)}"
           target="_blank"
           rel="noopener noreferrer">
            <div class="ticker-news-meta">
                <span>${escapeHtml(article.source || 'News source')}</span>
                <time datetime="${escapeHtml(article.published_at || '')}">${escapeHtml(formatNewsTimestamp(article.published_at))}</time>
            </div>
            <strong>${escapeHtml(article.title)}</strong>
            ${article.summary ? `<p>${escapeHtml(article.summary)}</p>` : ''}
            <span class="ticker-news-link">Open article <span aria-hidden="true">↗</span></span>
            <span class="ticker-news-rank">${String(index + 1).padStart(2, '0')}</span>
        </a>
    `).join('');
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
    const newsContainer = document.getElementById('td-news-list');
    if (newsContainer) {
        newsContainer.innerHTML = '<div class="ticker-news-empty">Loading directly related company news...</div>';
    }
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

function resetTickerDetailData(ticker) {
    clearTimeout(tickerAwfiHistoryRefreshTimer);
    tickerAwfiHistoryRefreshTimer = null;
    tickerAwfiHistoryRefreshAttempts = 0;
    tickerAwfiHistorySnapshotVersion = null;
    tickerAwfiIntelligence = null;
    tickerValuationCategory = 'decision';
    const values = {
        'td-ticker': ticker.toUpperCase(),
        'td-issuer': 'Loading ticker data...',
        'td-holders-summary': 'Preparing current ownership snapshot',
        'td-sector': 'Sector unavailable',
        'td-industry': 'Industry unavailable',
        'td-exchange': 'Exchange unavailable',
        'td-current-price': '—',
        'td-day-change': 'Loading market intelligence...',
        'td-52w-low-value': '—',
        'td-52w-low-date': '',
        'td-52w-low-percent': 'Unavailable',
        'td-whale-basis': '—',
        'td-basis-gap': '20-quarter model unavailable',
        'td-filing-period': 'Filing period unavailable',
        'td-holders-count': '0',
        'td-total-value': '$0.00 M',
        'td-total-shares': '0',
        'td-median-weight': '0.00%',
        'td-market-cap': '—',
        'td-pe': '—',
        'td-forward-pe': '—',
        'td-pe-5y': '—',
        'td-52w-range': '—',
        'td-beta': '—',
        'td-1y-return': '—',
        'td-eps-growth': '—',
        'td-valuation-status': '—',
        'td-valuation-framework': 'Loading company profile...',
        'td-valuation-reason': 'Matching valuation methods to the business model and available fundamentals.',
        'td-fair-value-label': 'Primary Fair-Value Estimate',
        'td-fair-value': '—',
        'td-valuation-range': 'Scenario range unavailable',
        'td-purchase-price': '—',
        'td-valuation-agreement': '—',
        'td-valuation-agreement-detail': 'Waiting for method assessments',
        'td-valuation-category-description': valuationCategoryDescriptions.decision,
        'td-valuation-model-count': '—',
        'td-trend-status': 'TREND: —',
        'td-entry-timing': 'Loading...',
        'td-rsi14': '—',
        'td-rsi2': '—',
        'td-sma50-distance': '—',
        'td-sma200-distance': '—',
        'td-momentum-6m': '—',
        'td-volatility': '—',
        'td-model-stance': '—',
        'td-equity-sleeve-range': '—',
        'td-total-portfolio-range': '—',
        'td-chart-symbol': 'Market Data',
        'td-table-ticker': ticker.toUpperCase(),
        'td-holders-subtitle': 'Loading individual portfolio weights...',
        'td-news-badge': 'Loading',
        'td-pair-status': 'LOADING',
        'td-pair-focal': ticker.toUpperCase(),
        'td-pair-peer': '—',
        'td-pair-type': 'Analyzing relationship...',
        'td-pair-action': 'Running disciplined pair gates...',
        'td-pair-observation': 'The observed spread direction will appear here.',
        'td-pair-zscore': '—',
        'td-pair-quality': '—',
        'td-pair-pvalue': '—',
        'td-pair-oos': '—',
        'td-pair-half-life': '—',
        'td-pair-stability': '—',
        'td-pair-correlation': '—',
        'td-pair-hedge-ratio': '—',
        'td-pair-stock-execution': 'Waiting for a valid signal',
        'td-pair-put-execution': 'Waiting for a valid signal'
    };
    Object.entries(values).forEach(([id, value]) => {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
    });
    ['undervalued', 'neutral', 'overvalued'].forEach(state => {
        const segment = document.getElementById(`td-agreement-${state}`);
        if (segment) segment.style.width = '0%';
    });
    document.querySelectorAll('.valuation-method-tab').forEach(button => {
        const selected = button.dataset.valuationCategory === 'decision';
        button.classList.toggle('is-active', selected);
        button.setAttribute('aria-pressed', String(selected));
        const count = button.querySelector('span');
        if (count) count.textContent = '0';
    });
    const valuationMethods = document.getElementById('td-valuation-methods');
    if (valuationMethods) {
        valuationMethods.innerHTML = '<div class="valuation-method-empty">Loading valuation methods...</div>';
    }
    const structuralValuation = document.getElementById('td-valuation-structural');
    if (structuralValuation) {
        structuralValuation.hidden = true;
        structuralValuation.replaceChildren();
    }
    const pairPeer = document.getElementById('td-pair-peer');
    if (pairPeer) pairPeer.href = '/ticker';
    const news = document.getElementById('td-news-list');
    if (news) {
        news.innerHTML = '<div class="ticker-news-empty">Loading directly related company news...</div>';
    }
    const holders = document.getElementById('holders-table-body');
    if (holders) {
        holders.innerHTML = '<tr><td colspan="9" class="text-center py-4 text-muted">Loading current holders...</td></tr>';
    }
    const tradingView = document.getElementById('ticker-tradingview-chart');
    if (tradingView) {
        tradingView.innerHTML = '<div class="stats-loading">Loading TradingView chart...</div>';
    }
    [
        'ticker-sentiment-history-chart',
        'ticker-conviction-heatmap',
        'ticker-investor-history-chart',
        'ticker-value-history-chart',
        'ticker-holder-concentration-chart',
        'ticker-bar-chart'
    ].forEach(id => {
        const chart = document.getElementById(id);
        if (!chart) return;
        if (chart.data && window.Plotly) Plotly.purge(chart);
        chart.innerHTML = '<div class="stats-loading">Loading chart...</div>';
    });
    resetWhaleSentimentView();
    renderTickerAwfi({}, {state: 'LOADING', reason: 'Loading AWFI scores...'}, []);
}

async function loadTickerDetail(ticker) {
    const detailView = document.getElementById('ticker-detail-view');
    const allView = document.getElementById('all-tickers-view');
    if (detailView) detailView.style.display = 'block';
    if (allView) allView.style.display = 'none';
    resetTickerDetailData(ticker);
    const loaderStartedAt = performance.now();
    showTickerLoader(ticker);

    try {
        const r = await fetch(`/api/ticker/${encodeURIComponent(ticker)}`);
        updateTickerLoader(1, '13F holdings loaded. Building market and valuation intelligence...', 'SEC filings');
        if (r.status === 404) {
            document.getElementById('td-ticker').textContent = ticker.toUpperCase();
            document.getElementById('td-issuer').textContent = 'Not held by the configured roster this quarter';
            document.getElementById('td-holders-count').textContent = '0';
            document.getElementById('td-total-value').textContent = '$0.00 M';
            document.getElementById('td-total-shares').textContent = '0';
            document.getElementById('td-median-weight').textContent = '0.00%';
            document.getElementById('td-holders-subtitle').textContent = 'No current holders in the configured roster';
            document.getElementById('holders-table-body').innerHTML = `<tr><td colspan="9" class="text-center py-4 text-muted">No positions in ${ticker.toUpperCase()} found among configured managers.</td></tr>`;
            showWhaleSentimentUnavailable('No tracked filing history for this ticker.');
            renderTickerAwfi({}, {state: 'UNAVAILABLE', reason: 'Ticker is not held in the current roster.'}, []);
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
        if (document.getElementById('td-holders-subtitle')) {
            document.getElementById('td-holders-subtitle').textContent = (
                `${data.num_holders} current holder${data.num_holders === 1 ? '' : 's'} · ` +
                'share action vs reported value movement'
            );
        }
        renderTickerAwfi(
            data.awfi || {},
            data.awfi_metadata || {},
            data.awfi_history || []
        );
        tickerAwfiHistorySnapshotVersion = data.awfi_history_version ?? null;
        scheduleTickerAwfiHistoryRefresh(data.ticker);

        const intelligenceResponse = await intelligenceRequest;
        if (intelligenceResponse.ok) {
            const intelligenceResult = await intelligenceResponse.json();
            renderTickerIntelligence(intelligenceResult.data);
            updateTickerLoader(2, 'Market, valuation, and 20-quarter trends loaded. Testing economic pairs...', 'OpenBB + historical cache');
        } else {
            const intelligenceError = await intelligenceResponse.json().catch(() => ({}));
            console.error('Ticker intelligence unavailable:', intelligenceError);
            renderTickerNews([]);
            document.getElementById('td-day-change').textContent = 'Market intelligence unavailable';
            document.getElementById('ticker-tradingview-chart').innerHTML = (
                '<div class="stats-loading">Market chart unavailable for this ticker.</div>'
            );
            renderTickerHistoryCharts([]);
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
            const pctChangeClass = h.value_change_pct > 0 ? 'text-green' : (h.value_change_pct < 0 ? 'text-red' : 'text-muted');
            const reportedValueChange = `${h.value_change_pct > 0 ? '+' : ''}${formatPct(h.value_change_pct)}`;
            const shareActionDetail = h.status === 'NEW'
                ? 'New holding'
                : h.status === 'CLOSED'
                    ? 'Full exit'
                    : `${h.shares_change_pct > 0 ? '+' : ''}${formatPct(h.shares_change_pct)} shares`;

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
                    <td>
                        <div class="qoq-action-cell">
                            <span class="badge ${getStatusClass(h.status)}">${h.status}</span>
                            <small>${shareActionDetail}</small>
                        </div>
                    </td>
                    <td class="font-mono ${pctChangeClass}">${reportedValueChange}</td>
                    <td class="font-mono text-dim">${h.report_period}</td>
                </tr>
            `;
        });
        document.getElementById('holders-table-body').innerHTML = html;

        // Render ranked concentration bars so large holder sets remain legible.
        const rankedHolders = [...data.holders]
            .filter(holder => Number.isFinite(holder.value) && holder.value > 0)
            .sort((a, b) => b.value - a.value);
        const totalHolderValue = rankedHolders.reduce((sum, holder) => sum + holder.value, 0);
        const concentrationBadge = document.getElementById('ticker-holder-concentration-badge');
        const concentrationChart = document.getElementById('ticker-holder-concentration-chart');

        if (!rankedHolders.length || totalHolderValue <= 0) {
            if (concentrationBadge) concentrationBadge.textContent = 'No reported values';
            if (concentrationChart) {
                concentrationChart.innerHTML = '<div class="stats-loading">Holder concentration is unavailable.</div>';
            }
        } else {
            const holderShares = rankedHolders.map(holder => (holder.value / totalHolderValue) * 100);
            const topFiveShare = holderShares.slice(0, 5).reduce((sum, share) => sum + share, 0);
            if (concentrationBadge) {
                concentrationBadge.textContent = `${rankedHolders.length} holders · Top 5 ${topFiveShare.toFixed(1)}%`;
            }
            if (concentrationChart) {
                concentrationChart.style.height = `${Math.max(320, rankedHolders.length * 28 + 72)}px`;
                concentrationChart.querySelector(':scope > .stats-loading')?.remove();
            }

            Plotly.newPlot('ticker-holder-concentration-chart', [{
                x: holderShares,
                y: rankedHolders.map(holder => holder.manager),
                type: 'bar',
                orientation: 'h',
                text: holderShares.map(share => `${share.toFixed(1)}%`),
                textposition: 'outside',
                cliponaxis: false,
                customdata: rankedHolders.map(holder => [holder.fund_name, holder.value]),
                marker: {
                    color: rankedHolders.map((_, index) => (
                        ['#22d3ee', '#38bdf8', '#60a5fa'][index] || '#475569'
                    )),
                    line: {color: 'rgba(255,255,255,0.08)', width: 1}
                },
                hovertemplate:
                    '<b>%{y}</b><br>' +
                    '%{customdata[0]}<br>' +
                    'Share of tracked holder value: %{x:.2f}%<br>' +
                    'Reported value: $%{customdata[1]:,.2f}M' +
                    '<extra></extra>'
            }], {
                paper_bgcolor: 'rgba(0,0,0,0)',
                plot_bgcolor: 'rgba(0,0,0,0)',
                font: {color: '#cbd5e1', family: 'Inter, sans-serif', size: 11},
                margin: {t: 8, b: 44, l: 132, r: 58},
                bargap: 0.28,
                showlegend: false,
                xaxis: {
                    title: 'Share of Reported Holder Value',
                    ticksuffix: '%',
                    rangemode: 'tozero',
                    gridcolor: '#1e293b',
                    zeroline: false
                },
                yaxis: {
                    autorange: 'reversed',
                    automargin: true,
                    gridcolor: 'rgba(0,0,0,0)'
                }
            }, {displayModeBar: false, responsive: true});
        }

        renderQoQActionBarChart('ticker-bar-chart', data.qoq_moves || data.holders, {
            maxItems: 26,
            minHeight: 400,
            leftMargin: 145,
            getLabel: move => move.manager,
            getHoverDetail: move => move.fund_name
        });

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
                    <div class="flex-align-gap">
                        ${f.is_exception ? '<span class="screening-roster-badge exception">EXCEPTION</span>' : ''}
                        <span class="badge ${getGroupClass(f.group)}">${f.group}</span>
                    </div>
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
        currentInvestorCik = cik;
        investorHistoryData = null;
        investorActivityFilter = 'ALL';
        const r = await fetch(`/api/investor/${cik}`);
        if (r.status === 404) return;
        const {data} = await r.json();
        investorSnapshotOnly = Boolean(data.screening_snapshot_only);

        // Populate Investor Header
        document.getElementById('inv-name').textContent = data.fund_info.name;
        document.getElementById('inv-manager').textContent = `Manager: ${data.fund_info.manager}`;
        document.getElementById('inv-group-badge').innerHTML = `
            ${data.fund_info.is_exception ? '<span class="screening-roster-badge exception">EXCEPTION</span>' : ''}
            <span class="badge ${getGroupClass(data.fund_info.group)}">${data.fund_info.group}</span>
        `;
        document.getElementById('inv-annotation').textContent = data.fund_info.annotation || '';

        document.getElementById('inv-aum').textContent = `$${formatNum(data.metadata.total_value_m)} M`;
        document.getElementById('inv-aum-b').textContent = `($${formatNum(data.metadata.total_value_b)} Billion)`;
        document.getElementById('inv-holdings-count').textContent = data.metadata.total_holdings;
        document.getElementById('inv-period').textContent = data.metadata.report_period;
        document.getElementById('inv-top5-weight').textContent = formatPct(data.stats.top5_weight);
        document.getElementById('inv-top10-weight').textContent = formatPct(data.stats.top10_weight);
        const marketAsOf = document.getElementById('inv-market-as-of');
        if (marketAsOf) {
            marketAsOf.textContent = data.stats.market_price_as_of
                ? `Market data through ${formatCalendarDate(data.stats.market_price_as_of)}`
                : 'Market data unavailable';
        }
        globalInvestorHoldings = data.holdings_list || [];
        globalInvestorClosed = data.closed_list || [];
        const detailDescription = document.getElementById('investor-detail-description');
        const holdingsTitle = document.getElementById('investor-holdings-title');
        const holdingsDescription = document.getElementById('investor-holdings-description');
        const activityTab = document.getElementById('investor-view-activity-tab');
        const historyTab = document.getElementById('investor-view-history-tab');
        const backLink = document.getElementById('investor-back-link');
        if (investorSnapshotOnly) {
            if (detailDescription) {
                detailDescription.textContent = 'Screening-focused view of material reported positions retained in the compact 20-quarter snapshot.';
            }
            if (holdingsTitle) holdingsTitle.textContent = 'Material Screening Positions';
            if (holdingsDescription) {
                holdingsDescription.textContent = 'Direct-stock positions at or above 1% of reported non-option 13F value, plus the quarter’s top ten positions.';
            }
            if (activityTab) activityTab.hidden = true;
            if (historyTab) historyTab.textContent = 'Material Position History';
            if (backLink) {
                backLink.href = '/screening';
                backLink.textContent = '◀ Investor Screening';
            }
            document.getElementById('inv-active-closed-count').textContent = `${globalInvestorHoldings.length} material positions shown`;
        } else {
            if (activityTab) activityTab.hidden = false;
            if (historyTab) historyTab.textContent = 'Portfolio History';
            if (backLink) {
                backLink.href = '/investor';
                backLink.textContent = '◀ All Fund Managers';
            }
            document.getElementById('inv-active-closed-count').textContent =
                `${globalInvestorHoldings.length} current · ${globalInvestorClosed.length} exited this quarter`;
        }

        // Populate Tab Counts
        const statusCounts = data.stats.status_counts || {};
        if (document.getElementById('tab-cnt-all')) document.getElementById('tab-cnt-all').textContent = globalInvestorHoldings.length + globalInvestorClosed.length;
        if (document.getElementById('tab-cnt-new')) document.getElementById('tab-cnt-new').textContent = statusCounts['NEW'] || 0;
        if (document.getElementById('tab-cnt-inc')) document.getElementById('tab-cnt-inc').textContent = statusCounts['INCREASED'] || 0;
        if (document.getElementById('tab-cnt-dec')) document.getElementById('tab-cnt-dec').textContent = statusCounts['DECREASED'] || 0;
        if (document.getElementById('tab-cnt-unc')) document.getElementById('tab-cnt-unc').textContent = statusCounts['UNCHANGED'] || 0;
        if (document.getElementById('tab-cnt-closed')) document.getElementById('tab-cnt-closed').textContent = globalInvestorClosed.length;

        filterHoldingsTab('ALL');

        // Render Portfolio Allocation Donut Chart
        let top10 = globalInvestorHoldings.slice(0, 10);
        let otherWeight = globalInvestorHoldings.slice(10).reduce((sum, h) => sum + h.portfolio_weight, 0);
        const trackedWeight = globalInvestorHoldings.reduce(
            (sum, holding) => sum + Number(holding.portfolio_weight || 0),
            0
        );
        let labels = top10.map(h => h.ticker || h.issuer);
        let vals = top10.map(h => h.portfolio_weight);
        if (otherWeight > 0.01) {
            labels.push(investorSnapshotOnly ? 'Other material positions' : 'Other');
            vals.push(Number(otherWeight.toFixed(2)));
        }
        if (investorSnapshotOnly && trackedWeight < 99.99) {
            labels.push('Not retained in screening snapshot');
            vals.push(Number(Math.max(0, 100 - trackedWeight).toFixed(2)));
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
        const barChart = document.getElementById('bar-chart');
        await renderQoQActionBarChart('bar-chart', allMoves, {
            maxItems: 15,
            minHeight: 380,
            leftMargin: 80,
            getLabel: move => move.ticker,
            getHoverDetail: move => move.issuer
        });
        barChart.on('plotly_click', event => {
            navigateToTickerDetail(event.points?.[0]?.y);
        });
        barChart.on('plotly_afterplot', () => makeTickerAxisLabelsInteractive(barChart));
        makeTickerAxisLabelsInteractive(barChart);

    } catch(err) {
        console.error('Error loading investor detail:', err);
    }
}

function escapeInvestorHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

async function switchInvestorView(view, button) {
    const panels = {
        CURRENT: 'investor-current-panel',
        ACTIVITY: 'investor-activity-panel',
        HISTORY: 'investor-history-panel'
    };
    document.querySelectorAll('.investor-view-tabs .tab-btn').forEach(tab => {
        const isActive = tab === button;
        tab.classList.toggle('active', isActive);
        tab.setAttribute('aria-selected', String(isActive));
    });
    Object.entries(panels).forEach(([name, id]) => {
        const panel = document.getElementById(id);
        if (panel) panel.hidden = name !== view;
    });
    if (view !== 'CURRENT' && !investorHistoryData) {
        await loadInvestorHistory();
    }
}

async function loadInvestorHistory() {
    if (!currentInvestorCik || investorHistoryLoading) return;
    investorHistoryLoading = true;
    const activityBody = document.getElementById('investor-activity-history-body');
    const portfolioBody = document.getElementById('investor-portfolio-history-body');
    if (activityBody) {
        activityBody.innerHTML = '<tr><td colspan="6" class="text-center py-4">Loading 20-quarter activity history...</td></tr>';
    }
    if (portfolioBody) {
        portfolioBody.innerHTML = '<tr><td colspan="4" class="text-center py-4">Loading 20-quarter portfolio history...</td></tr>';
    }

    try {
        const response = await fetch(`/api/investor/${currentInvestorCik}/history`);
        if (!response.ok) {
            throw new Error(`Investor history request failed with HTTP ${response.status}`);
        }
        const result = await response.json();
        if (result.error) throw new Error(result.error);
        investorHistoryData = result.data || {};
        renderInvestorActivityHistory();
        renderInvestorPortfolioHistory();
    } catch (error) {
        console.error('Investor history failed:', error);
        const message = escapeInvestorHtml(error.message);
        if (activityBody) {
            activityBody.innerHTML = `<tr><td colspan="6" class="text-center py-4 text-red">Could not load activity history: ${message}</td></tr>`;
        }
        if (portfolioBody) {
            portfolioBody.innerHTML = `<tr><td colspan="4" class="text-center py-4 text-red">Could not load portfolio history: ${message}</td></tr>`;
        }
    } finally {
        investorHistoryLoading = false;
    }
}

function filterInvestorActivity(filter, button) {
    investorActivityFilter = filter;
    document.querySelectorAll('#investor-activity-filter .tab-btn').forEach(tab => {
        tab.classList.toggle('active', tab === button);
    });
    renderInvestorActivityHistory();
}

function renderInvestorActivityHistory() {
    const body = document.getElementById('investor-activity-history-body');
    if (!body || !investorHistoryData) return;
    const bullishStatuses = new Set(['NEW', 'INCREASED']);
    const bearishStatuses = new Set(['DECREASED', 'CLOSED']);
    let html = '';

    (investorHistoryData.activity || []).forEach(period => {
        const changes = (period.changes || []).filter(change => (
            investorActivityFilter === 'ALL'
            || (investorActivityFilter === 'BUYS' && bullishStatuses.has(change.status))
            || (investorActivityFilter === 'SELLS' && bearishStatuses.has(change.status))
        ));
        if (!changes.length) return;

        html += `
            <tr class="investor-period-row">
                <td colspan="6">
                    <strong>${formatFilingPeriodLabel(period.period)}</strong>
                    <span>${period.filing_date ? `Filed ${formatCalendarDate(period.filing_date)}` : 'Filing date unavailable'}</span>
                </td>
            </tr>`;

        changes.forEach(change => {
            const directionClass = bullishStatuses.has(change.status)
                ? 'text-green'
                : 'text-red';
            const sharesSign = change.shares_change > 0 ? '+' : '';
            const weightSign = change.portfolio_weight_change > 0 ? '+' : '';
            html += `
                <tr>
                    <td>
                        <a class="investor-history-security" href="/ticker/${encodeURIComponent(change.ticker)}">
                            <strong class="font-mono">${escapeInvestorHtml(change.ticker)}</strong>
                            <span>${escapeInvestorHtml(change.issuer)}</span>
                        </a>
                    </td>
                    <td><span class="badge ${getStatusClass(change.status)}">${escapeInvestorHtml(change.status)}</span></td>
                    <td class="font-mono ${directionClass}">${sharesSign}${formatInt(change.shares_change)}</td>
                    <td class="font-mono ${directionClass}">${change.shares_change_pct > 0 ? '+' : ''}${formatPct(change.shares_change_pct)}</td>
                    <td class="font-mono ${change.portfolio_weight_change > 0 ? 'text-green' : change.portfolio_weight_change < 0 ? 'text-red' : 'text-muted'}">${weightSign}${formatNum(change.portfolio_weight_change)} pp</td>
                    <td class="font-mono ${change.value_change > 0 ? 'text-green' : change.value_change < 0 ? 'text-red' : 'text-muted'}">${formatFlowMillions(change.value_change, true)}</td>
                </tr>`;
        });
    });

    body.innerHTML = html || '<tr><td colspan="6" class="text-center py-4 text-muted">No activity matches this filter.</td></tr>';
}

function renderInvestorPortfolioHistory() {
    const body = document.getElementById('investor-portfolio-history-body');
    if (!body || !investorHistoryData) return;
    const rows = investorHistoryData.portfolio_history || [];
    if (!rows.length) {
        body.innerHTML = '<tr><td colspan="4" class="text-center py-4 text-muted">No historical portfolio snapshots are available.</td></tr>';
        return;
    }

    body.innerHTML = rows.map(period => {
        const holdings = (period.top_holdings || []).map(holding => {
            const title = `${escapeInvestorHtml(holding.issuer)} · ${formatPct(holding.portfolio_weight)} · $${formatNum(holding.value)}M`;
            const label = escapeInvestorHtml(holding.ticker || holding.issuer);
            if (!holding.ticker) {
                return `
                    <span class="investor-history-holding" title="${title}">
                        <span>${label}</span>
                        <b>${formatPct(holding.portfolio_weight)}</b>
                    </span>`;
            }
            return `
                <a class="investor-history-holding" href="/ticker/${encodeURIComponent(holding.ticker)}"
                   title="${title}">
                    <span class="font-mono">${label}</span>
                    <b>${formatPct(holding.portfolio_weight)}</b>
                </a>`;
        }).join('');
        return `
            <tr>
                <td>
                    <strong>${formatFilingPeriodLabel(period.period)}</strong>
                    <small>${period.filing_date ? `Filed ${formatCalendarDate(period.filing_date)}` : 'Filing date unavailable'}</small>
                </td>
                <td class="font-mono"><strong>${formatFlowMillions(period.portfolio_value_m)}</strong></td>
                <td class="font-mono">${formatInt(period.position_count)}</td>
                <td><div class="investor-history-holdings">${holdings || '<span class="text-muted">No reported holdings</span>'}</div></td>
            </tr>`;
    }).join('');
}

function filterHoldingsTab(tabName) {
    currentHoldingsTab = tabName;
    document.querySelectorAll('#holdings-tab-group .tab-btn').forEach(btn => btn.classList.remove('active'));
    event?.target?.classList?.add('active');
    filterInvestorTableRows();
}

function filterInvestorTableRows() {
    const q = document.getElementById('investor-table-search')?.value.trim().toLowerCase() || '';
    let pool;
    if (currentHoldingsTab === 'ALL') {
        pool = [...globalInvestorHoldings, ...globalInvestorClosed];
    } else if (currentHoldingsTab === 'CLOSED') {
        pool = globalInvestorClosed;
    } else {
        pool = globalInvestorHoldings;
    }

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
        tbody.innerHTML = `<tr><td colspan="14" class="text-center py-4 text-muted">No holdings found for this tab or filter.</td></tr>`;
        return;
    }

    let html = '';
    rows.forEach(h => {
        const valChangeClass = h.value_change > 0 ? 'text-green' : (h.value_change < 0 ? 'text-red' : 'text-muted');
        const valChangeSign = h.value_change > 0 ? '+' : '';
        const pctChangeClass = h.value_change_pct > 0 ? 'text-green' : (h.value_change_pct < 0 ? 'text-red' : 'text-muted');
        const marketMoveClass = h.current_vs_reported_pct > 0
            ? 'text-green'
            : h.current_vs_reported_pct < 0
                ? 'text-red'
                : 'text-muted';
        const lowDistanceClass = h.pct_above_low === null || h.pct_above_low === undefined
            ? 'text-muted'
            : h.pct_above_low >= 0
                ? 'text-green'
                : 'text-red';
        const reportedPrice = h.reported_price === null || h.reported_price === undefined
            ? '—'
            : `$${formatNum(h.reported_price)}`;
        const currentPrice = h.current_price === null || h.current_price === undefined
            ? '—'
            : `$${formatNum(h.current_price)}`;
        const currentVsReported = h.current_vs_reported_pct === null || h.current_vs_reported_pct === undefined
            ? '—'
            : `${h.current_vs_reported_pct > 0 ? '+' : ''}${formatPct(h.current_vs_reported_pct)}`;
        const low52Week = h.low_52_week === null || h.low_52_week === undefined
            ? '—'
            : `$${formatNum(h.low_52_week)}`;
        const pctAboveLow = h.pct_above_low === null || h.pct_above_low === undefined
            ? '—'
            : formatPct(h.pct_above_low);
        const marketPriceTitle = h.market_price_as_of
            ? `Latest cached close as of ${formatCalendarDate(h.market_price_as_of)}`
            : 'Latest cached close unavailable';
        const tickerCell = h.ticker
            ? `<a href="/ticker/${encodeURIComponent(h.ticker)}"><strong class="font-mono">${escapeInvestorHtml(h.ticker)}</strong></a>`
            : '<span class="text-muted">—</span>';
        const sharesCell = investorSnapshotOnly ? '—' : formatInt(h.shares);
        const actionCell = investorSnapshotOnly
            ? '<span class="text-muted">—</span>'
            : `<span class="badge ${getStatusClass(h.status)}">${h.status}</span>`;
        const valueChangeCell = investorSnapshotOnly
            ? '—'
            : `<strong>${valChangeSign}${formatNum(h.value_change)}</strong>`;
        const valuePctCell = investorSnapshotOnly
            ? '—'
            : formatPct(h.value_change_pct);
        const sharesPctCell = investorSnapshotOnly
            ? '—'
            : formatPct(h.shares_change_pct);

        html += `
            <tr>
                <td>${tickerCell}</td>
                <td>${escapeInvestorHtml(h.issuer)}</td>
                <td>${actionCell}</td>
                <td class="font-mono">${renderSparkline(h.portfolio_weight)}</td>
                <td class="font-mono"><strong>$${formatNum(h.value)}</strong></td>
                <td class="font-mono">${sharesCell}</td>
                <td class="font-mono investor-market-cell">${reportedPrice}</td>
                <td class="font-mono investor-market-cell" title="${marketPriceTitle}"><strong>${currentPrice}</strong></td>
                <td class="font-mono investor-market-cell ${marketMoveClass}"><strong>${currentVsReported}</strong></td>
                <td class="font-mono investor-market-cell">${low52Week}</td>
                <td class="font-mono investor-market-cell ${lowDistanceClass}">${pctAboveLow}</td>
                <td class="font-mono ${valChangeClass}">${valueChangeCell}</td>
                <td class="font-mono ${pctChangeClass}">${valuePctCell}</td>
                <td class="font-mono">${sharesPctCell}</td>
            </tr>
        `;
    });
    tbody.innerHTML = html;
}

function exportInvestorToCSV() {
    const name = document.getElementById('inv-name')?.textContent || 'Portfolio';
    const allRows = [...globalInvestorHoldings, ...globalInvestorClosed];
    if (allRows.length === 0) return;

    const headers = [
        "Ticker",
        "Company",
        "Cusip",
        "WeightPct",
        "Value_M",
        "Shares",
        "ReportedPrice",
        "LatestPrice",
        "SinceReportPct",
        "52WeekLow",
        "PctAbove52WeekLow",
        "MarketPriceAsOf",
        "Action",
        "Change_M",
        "ChangePct",
        "SharesChangePct"
    ];
    const rows = allRows.map(h => [
        `"${h.ticker}"`,
        `"${h.issuer}"`,
        `"${h.cusip || ''}"`,
        h.portfolio_weight,
        h.value,
        h.shares,
        h.reported_price ?? '',
        h.current_price ?? '',
        h.current_vs_reported_pct ?? '',
        h.low_52_week ?? '',
        h.pct_above_low ?? '',
        `"${h.market_price_as_of || ''}"`,
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

// ==========================================
// 4. Investor Screening
// ==========================================

const screeningPresets = {
    broad: {
        size: 1,
        minimumStocks: 1,
        directStock: 60,
        top10: 30,
        persistence: 4,
        bestBetWeight: 2,
        bestBetDuration: 6,
        bestBetCount: 1,
        performanceWindow: '3Y'
    },
    mega: {
        size: 10,
        minimumStocks: 1,
        directStock: 80,
        top10: 40,
        persistence: 6,
        bestBetWeight: 3,
        bestBetDuration: 12,
        bestBetCount: 1,
        performanceWindow: '3Y'
    },
    patient: {
        size: 1,
        minimumStocks: 1,
        directStock: 90,
        top10: 50,
        persistence: 8,
        bestBetWeight: 3,
        bestBetDuration: 24,
        bestBetCount: 2,
        performanceWindow: '3Y'
    },
    strict: {
        size: 10,
        minimumStocks: 5,
        directStock: 80,
        top10: 40,
        persistence: 8,
        bestBetWeight: 5,
        bestBetDuration: 12,
        bestBetCount: 3,
        performanceWindow: 'FULL',
        benchmarkFilter: 'both',
        requirePerformance: false,
        minimumExcessCagr: '0',
        beatConsistency: '',
        maximumDrawdown: ''
    },
    persistent: {
        size: 0.5,
        minimumStocks: 3,
        directStock: 50,
        top10: 50,
        persistence: 8,
        bestBetWeight: 3,
        bestBetDuration: 12,
        bestBetCount: 3,
        performanceWindow: 'FULL',
        benchmarkFilter: 'both',
        requirePerformance: false,
        minimumExcessCagr: '0',
        beatConsistency: '',
        maximumDrawdown: ''
    }
};

function escapeScreeningHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function updateScreeningRange(inputId, outputId, suffix) {
    const input = document.getElementById(inputId);
    const output = document.getElementById(outputId);
    if (input && output) output.textContent = `${input.value}${suffix}`;
}

function updateScreeningMinimumStocks() {
    const input = document.getElementById('screen-min-stocks');
    const output = document.getElementById('screen-min-stocks-value');
    if (input && output) {
        output.textContent = input.value === '10' ? '10+' : input.value;
    }
}

function updateScreeningBestBetCount() {
    const input = document.getElementById('screen-best-bet-count');
    const output = document.getElementById('screen-best-bet-count-value');
    if (input && output) {
        output.textContent = input.value === '10' ? '10+' : input.value;
    }
}

function updateScreeningPerformanceControls() {
    const hurdle = document.getElementById('screen-benchmark-filter')?.value || 'none';
    const excess = document.getElementById('screen-min-excess-cagr');
    const custom = document.getElementById('screen-min-excess-cagr-custom');
    const consistency = document.getElementById('screen-beat-consistency');
    const benchmarkRequired = hurdle !== 'none';
    if (excess) excess.disabled = !benchmarkRequired;
    if (consistency) consistency.disabled = !benchmarkRequired;
    if (custom) {
        const customSelected = benchmarkRequired && excess?.value === 'custom';
        custom.hidden = !customSelected;
        custom.disabled = !customSelected;
    }
}

function setScreeningControl(id, value) {
    const element = document.getElementById(id);
    if (!element) return;
    if (element.type === 'checkbox') element.checked = Boolean(value);
    else element.value = value;
}

function applyScreeningPreset(name) {
    const preset = screeningPresets[name];
    if (!preset) return;
    setScreeningControl('screen-min-size', preset.size);
    setScreeningControl('screen-min-stocks', preset.minimumStocks);
    setScreeningControl('screen-direct-stock', preset.directStock);
    setScreeningControl('screen-top10', preset.top10);
    setScreeningControl('screen-persistence', preset.persistence);
    setScreeningControl('screen-best-bet-weight', preset.bestBetWeight);
    setScreeningControl('screen-best-bet-duration', preset.bestBetDuration);
    setScreeningControl('screen-best-bet-count', preset.bestBetCount);
    setScreeningControl('screen-performance-window', preset.performanceWindow);
    setScreeningControl(
        'screen-benchmark-filter',
        preset.benchmarkFilter ?? 'none'
    );
    setScreeningControl(
        'screen-performance-required',
        preset.requirePerformance ?? false
    );
    setScreeningControl(
        'screen-min-excess-cagr',
        preset.minimumExcessCagr ?? '0'
    );
    setScreeningControl(
        'screen-beat-consistency',
        preset.beatConsistency ?? ''
    );
    setScreeningControl(
        'screen-max-drawdown',
        preset.maximumDrawdown ?? ''
    );
    updateScreeningRange('screen-direct-stock', 'screen-direct-stock-value', '%');
    updateScreeningRange('screen-top10', 'screen-top10-value', '%');
    updateScreeningRange('screen-persistence', 'screen-persistence-value', '/8');
    updateScreeningRange('screen-best-bet-weight', 'screen-best-bet-weight-value', '%');
    updateScreeningMinimumStocks();
    updateScreeningBestBetCount();
    updateScreeningPerformanceControls();
    document.querySelectorAll('.screening-preset').forEach(button => {
        button.classList.toggle('active', button.dataset.preset === name);
    });
    screeningPage = 1;
    loadInvestorScreening();
}

function resetScreeningDefaults() {
    setScreeningControl('screen-roster-only', false);
    setScreeningControl('screening-search', '');
    applyScreeningPreset('mega');
}

function scheduleScreeningLoad() {
    clearTimeout(screeningLoadTimer);
    screeningLoadTimer = setTimeout(() => {
        screeningPage = 1;
        loadInvestorScreening();
    }, 250);
}

function updateScreeningRosterControls() {
    const selectedCount = screeningSelectedCiks.size;
    const group = document.getElementById('screening-roster-group')?.value || '';
    const count = document.getElementById('screening-selected-count');
    if (count) {
        count.textContent = `${selectedCount} selected`;
    }
    [
        'screening-add-selected',
        'screening-flag-selected'
    ].forEach(id => {
        const button = document.getElementById(id);
        if (button) {
            button.disabled = screeningRosterBusy || selectedCount === 0 || !group;
        }
    });
    const removeButton = document.getElementById('screening-remove-selected');
    if (removeButton) {
        removeButton.disabled = screeningRosterBusy || selectedCount === 0;
    }

    const start = (screeningPage - 1) * screeningPageSize;
    const pageRows = screeningData.slice(start, start + screeningPageSize);
    const selectPage = document.getElementById('screening-select-page');
    if (selectPage) {
        const selectedOnPage = pageRows.filter(
            manager => screeningSelectedCiks.has(manager.cik)
        ).length;
        selectPage.checked = pageRows.length > 0 && selectedOnPage === pageRows.length;
        selectPage.indeterminate = selectedOnPage > 0 && selectedOnPage < pageRows.length;
    }
}

function toggleScreeningSelection(cik, checked) {
    if (checked) screeningSelectedCiks.add(cik);
    else screeningSelectedCiks.delete(cik);
    updateScreeningRosterControls();
}

function toggleScreeningPageSelection(checked) {
    const start = (screeningPage - 1) * screeningPageSize;
    screeningData
        .slice(start, start + screeningPageSize)
        .forEach(manager => {
            if (checked) screeningSelectedCiks.add(manager.cik);
            else screeningSelectedCiks.delete(manager.cik);
        });
    renderScreeningTable();
}

async function mutateScreeningRoster(event, action, ciks, isException = false) {
    event?.preventDefault();
    event?.stopPropagation();
    if (screeningRosterBusy || !ciks.length) return;

    screeningRosterBusy = true;
    updateScreeningRosterControls();
    try {
        const group = document.getElementById('screening-roster-group')?.value || null;
        if (action === 'include' && !group) {
            throw new Error('Choose an investment style before adding managers');
        }
        const response = await fetch('/api/roster', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                action,
                ciks,
                is_exception: isException,
                group: action === 'include' ? group : null
            })
        });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || `Roster update failed with HTTP ${response.status}`);
        }
        screeningSelectedCiks.clear();
        const actionLabel = action === 'exclude'
            ? 'Removed'
            : (isException ? 'Added and flagged' : 'Added');
        showToast(`${actionLabel} ${ciks.length} manager${ciks.length === 1 ? '' : 's'}`);
        await loadInvestorScreening();
    } catch (error) {
        console.error('Roster update failed:', error);
        showToast(`Roster update failed: ${error.message}`);
    } finally {
        screeningRosterBusy = false;
        updateScreeningRosterControls();
    }
}

function updateSelectedScreeningRoster(action, isException = false) {
    mutateScreeningRoster(
        null,
        action,
        Array.from(screeningSelectedCiks),
        isException
    );
}

async function initializeInvestorScreening() {
    updateScreeningMinimumStocks();
    updateScreeningBestBetCount();
    updateScreeningRange('screen-direct-stock', 'screen-direct-stock-value', '%');
    updateScreeningRange('screen-top10', 'screen-top10-value', '%');
    updateScreeningRange('screen-persistence', 'screen-persistence-value', '/8');
    updateScreeningRange('screen-best-bet-weight', 'screen-best-bet-weight-value', '%');
    updateScreeningPerformanceControls();
    await loadInvestorScreening();
}

async function loadInvestorScreening() {
    const tbody = document.getElementById('screening-table-body');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="14" class="text-center py-4">Applying screening criteria...</td></tr>';

    const params = new URLSearchParams({
        minimum_size_billions: document.getElementById('screen-min-size')?.value || '10',
        minimum_stock_count: document.getElementById('screen-min-stocks')?.value || '1',
        minimum_direct_stock_pct: document.getElementById('screen-direct-stock')?.value || '80',
        minimum_top10_pct: document.getElementById('screen-top10')?.value || '40',
        minimum_concentration_quarters: document.getElementById('screen-persistence')?.value || '6',
        minimum_best_bet_weight_pct: document.getElementById('screen-best-bet-weight')?.value || '3',
        best_bet_duration_months: document.getElementById('screen-best-bet-duration')?.value || '12',
        minimum_best_bet_count: document.getElementById('screen-best-bet-count')?.value || '1',
        benchmark_hurdle: document.getElementById('screen-benchmark-filter')?.value || 'none',
        roster_only: document.getElementById('screen-roster-only')?.checked || false,
        performance_window: document.getElementById('screen-performance-window')?.value || '3Y'
    });
    const benchmarkFilter = document.getElementById('screen-benchmark-filter')?.value || 'none';
    const excessSelection = document.getElementById('screen-min-excess-cagr')?.value || '0';
    const minimumExcess = excessSelection === 'custom'
        ? document.getElementById('screen-min-excess-cagr-custom')?.value
        : excessSelection;
    const beatConsistency = document.getElementById('screen-beat-consistency')?.value;
    const maximumDrawdown = document.getElementById('screen-max-drawdown')?.value;
    const requirePerformance = document.getElementById('screen-performance-required')?.checked || false;
    if (benchmarkFilter !== 'none' && minimumExcess !== '') {
        params.set('minimum_excess_cagr_pct', minimumExcess || '0');
    }
    if (benchmarkFilter !== 'none' && beatConsistency) {
        params.set('minimum_beat_consistency_pct', beatConsistency);
    }
    if (maximumDrawdown) {
        params.set('maximum_drawdown_pct', maximumDrawdown);
    }
    params.set(
        'require_performance',
        String(
            requirePerformance
            || benchmarkFilter !== 'none'
            || Boolean(maximumDrawdown)
        )
    );
    const search = document.getElementById('screening-search')?.value.trim();
    if (search) params.set('search', search);

    screeningAbortController?.abort();
    screeningAbortController = new AbortController();
    const activeController = screeningAbortController;
    try {
        const response = await fetch(
            `/api/screening?${params}`,
            {signal: activeController.signal}
        );
        if (!response.ok) throw new Error(`Screening request failed with HTTP ${response.status}`);
        const result = await response.json();
        if (activeController !== screeningAbortController) return;
        if (result.error) throw new Error(result.error);
        screeningData = result.data || [];
        updateScreeningSummary(result.summary || {}, result.metadata || {});
        sortScreeningData();
        renderScreeningTable();
    } catch (error) {
        if (error.name === 'AbortError') return;
        console.error('Investor screening failed:', error);
        tbody.innerHTML = `
            <tr>
                <td colspan="14" class="text-center py-4 text-red">
                    Could not load the screening snapshot: ${escapeScreeningHtml(error.message)}
                </td>
            </tr>`;
    }
}

function updateScreeningSummary(summary, metadata) {
    const setText = (id, value) => {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
    };
    const candidateCount = summary.candidate_count || 0;
    const structuralCount = summary.structural_candidate_count ?? candidateCount;
    const structuralPerformanceCount = (
        summary.structural_performance_available_count
        ?? summary.performance_available_count
        ?? 0
    );
    const performanceFactManagerCount = summary.performance_fact_manager_count || 0;
    setText('screening-count', formatInt(candidateCount));
    setText('screening-roster-count', formatInt(summary.roster_count || 0));
    setText('screening-median-size', `$${formatNum(summary.median_size_billions || 0)}B`);
    setText('screening-median-turnover', formatPct(summary.median_turnover_pct || 0));
    setText(
        'screening-performance-count',
        formatInt(structuralPerformanceCount)
    );
    setText('screening-beat-spy', formatInt(summary.beat_spy_count || 0));
    setText('screening-beat-qqq', formatInt(summary.beat_qqq_count || 0));
    setText(
        'screening-active-roster-count',
        formatInt(metadata.configured_roster_count || 0)
    );
    setText(
        'screening-roster-total',
        `${formatInt(metadata.configured_roster_count || 0)} configured managers`
    );
    const performanceWindow = document.getElementById('screen-performance-window')?.value || '3Y';
    setText(
        'screening-performance-window-label',
        `${performanceWindow === 'FULL' ? 'Full-history' : performanceWindow}: `
        + `${formatInt(structuralPerformanceCount)} of ${formatInt(structuralCount)}`
        + ` structural matches · ${formatInt(performanceFactManagerCount)} computed`
    );
    setText(
        'screening-count-note',
        candidateCount === structuralCount
            ? `$${document.getElementById('screen-min-size')?.value || 10}B minimum reported value`
            : `${formatInt(candidateCount)} pass performance filters; `
                + `${formatInt(structuralPerformanceCount)} of `
                + `${formatInt(structuralCount)} have estimates`
    );
    if (metadata.report_period) {
        setText('screening-report-period', formatFilingPeriodLabel(String(metadata.report_period)));
    }
    if (metadata.generated_at) {
        const generated = new Date(metadata.generated_at);
        setText('screening-generated-at', `Snapshot built ${generated.toLocaleString()}`);
    }
    updateScreeningRosterControls();
}

function sortScreening(column) {
    if (screeningSortColumn === column) screeningSortAsc = !screeningSortAsc;
    else {
        screeningSortColumn = column;
        screeningSortAsc = column === 'manager_name';
    }
    screeningPage = 1;
    sortScreeningData();
    renderScreeningTable();
}

function sortScreeningData() {
    screeningData.sort((a, b) => {
        const aValue = a[screeningSortColumn];
        const bValue = b[screeningSortColumn];
        if (typeof aValue === 'string') {
            return screeningSortAsc
                ? aValue.localeCompare(bValue)
                : bValue.localeCompare(aValue);
        }
        return screeningSortAsc
            ? (Number(aValue) || 0) - (Number(bValue) || 0)
            : (Number(bValue) || 0) - (Number(aValue) || 0);
    });
}

function formatScreeningSize(value) {
    const billions = (Number(value) || 0) / 1_000_000_000;
    return billions >= 1000
        ? `$${(billions / 1000).toFixed(2)}T`
        : `$${billions.toFixed(2)}B`;
}

function formatPerformancePercent(value, includeSign = false) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return '—';
    }
    const percent = Number(value) * 100;
    const sign = includeSign && percent > 0 ? '+' : '';
    return `${sign}${percent.toFixed(2)}%`;
}

function formatBeatRate(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return '—';
    }
    return `${(Number(value) * 100).toFixed(1)}%`;
}

function openScreeningManager(event, cik) {
    if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) {
        return;
    }
    if (event.target.closest('a, button, input, select, textarea')) {
        return;
    }
    event.preventDefault();
    window.location.assign(`/investor/${encodeURIComponent(cik)}`);
}

function renderScreeningTable() {
    const tbody = document.getElementById('screening-table-body');
    const summary = document.getElementById('screening-page-summary');
    if (!tbody) return;

    const pageCount = Math.max(1, Math.ceil(screeningData.length / screeningPageSize));
    screeningPage = Math.min(screeningPage, pageCount);
    const start = (screeningPage - 1) * screeningPageSize;
    const rows = screeningData.slice(start, start + screeningPageSize);

    if (!rows.length) {
        tbody.innerHTML = `
            <tr>
                <td colspan="14" class="screening-empty">
                    <strong>No managers match this combination.</strong>
                    <span>Lower the size, concentration, or direct-stock threshold.</span>
                </td>
            </tr>`;
    } else {
        tbody.innerHTML = rows.map(manager => {
            const positionChips = (
                manager.persistent_best_bets
                || manager.durable_positions
                || []
            ).slice(0, 3).map(position => `
                <span class="screening-position-chip">
                    ${escapeScreeningHtml(position.ticker || position.issuer)}
                    <b>${Number(position.latest_weight_pct).toFixed(1)}%</b>
                </span>
            `).join('');
            const concentrationRisk = Number(manager.maximum_position_pct) > 20
                ? '<span class="screening-risk-flag" title="Largest position exceeds 20%">CONCENTRATED</span>'
                : '';
            const rosterBadge = manager.is_current_roster
                ? `
                    <span class="screening-roster-badge ${manager.roster_is_exception ? 'exception' : ''}">
                        ${manager.roster_is_exception ? 'EXCEPTION' : 'ROSTER'}
                    </span>
                `
                : '';
            const performanceAvailable = manager.performance_status === 'AVAILABLE';
            const coverage = performanceAvailable
                ? Math.min(
                    Number(manager.performance_mapping_coverage) || 0,
                    Number(manager.performance_priced_coverage) || 0
                )
                : null;
            const spyClass = Number(manager.spy_excess_cagr) > 0 ? 'text-green' : 'text-red';
            const qqqClass = Number(manager.qqq_excess_cagr) > 0 ? 'text-green' : 'text-red';
            return `
                <tr class="screening-manager-row"
                    tabindex="0"
                    role="link"
                    aria-label="Open detailed investor view for ${escapeScreeningHtml(manager.manager_name)}"
                    onclick="openScreeningManager(event, '${escapeScreeningHtml(manager.cik)}')"
                    onkeydown="openScreeningManager(event, '${escapeScreeningHtml(manager.cik)}')">
                    <td class="screening-select-cell">
                        <input type="checkbox"
                               aria-label="Select ${escapeScreeningHtml(manager.manager_name)}"
                               ${screeningSelectedCiks.has(manager.cik) ? 'checked' : ''}
                               onclick="event.stopPropagation()"
                               onchange="toggleScreeningSelection('${escapeScreeningHtml(manager.cik)}', this.checked)">
                    </td>
                    <td>
                        <div class="screening-manager-cell">
                            <div>
                                <strong>${escapeScreeningHtml(manager.manager_name)}</strong>
                                ${rosterBadge}${concentrationRisk}
                            </div>
                            <span class="font-mono">${escapeScreeningHtml(manager.cik)}</span>
                            ${positionChips ? `<div class="screening-position-chips">${positionChips}</div>` : ''}
                        </div>
                    </td>
                    <td class="font-mono"><strong>${formatScreeningSize(manager.median_reported_value_4q)}</strong></td>
                    <td class="font-mono">${formatPct(manager.direct_stock_pct)}</td>
                    <td class="font-mono">${formatPct(manager.top10_pct)}</td>
                    <td class="font-mono">${formatPct(manager.maximum_position_pct)}</td>
                    <td class="font-mono">${formatPct(manager.annualized_turnover_pct)}</td>
                    <td>
                        <span class="screening-durable-count">${formatInt(manager.persistent_best_bet_count)}</span>
                    </td>
                    <td class="font-mono screening-performance-cell">
                        ${performanceAvailable
                            ? `
                                <strong>${formatPerformancePercent(manager.estimated_cagr)}</strong>
                                <span class="screening-performance-detail">
                                    Quarterly wins: SPY ${formatBeatRate(manager.spy_quarterly_beat_rate)}
                                    · QQQ ${formatBeatRate(manager.qqq_quarterly_beat_rate)}
                                </span>
                            `
                            : `<span class="screening-performance-unavailable" title="${escapeScreeningHtml(manager.performance_unavailable_reason || 'Unavailable')}">Unavailable</span>`}
                    </td>
                    <td class="font-mono ${performanceAvailable ? spyClass : ''}">
                        ${performanceAvailable ? formatPerformancePercent(manager.spy_excess_cagr, true) : '—'}
                    </td>
                    <td class="font-mono ${performanceAvailable ? qqqClass : ''}">
                        ${performanceAvailable ? formatPerformancePercent(manager.qqq_excess_cagr, true) : '—'}
                    </td>
                    <td class="font-mono ${performanceAvailable ? 'text-red' : ''}">
                        ${performanceAvailable ? formatPerformancePercent(manager.max_drawdown) : '—'}
                    </td>
                    <td class="font-mono">
                        ${coverage === null ? '—' : formatPerformancePercent(coverage)}
                    </td>
                    <td class="screening-roster-cell">
                        ${manager.is_current_roster
                            ? `
                                <span class="screening-roster-state ${manager.roster_is_exception ? 'exception' : ''}">
                                    ${manager.roster_is_exception ? 'FLAGGED' : 'IN'}
                                </span>
                                <button type="button" class="screening-roster-button remove"
                                        onclick="mutateScreeningRoster(event, 'exclude', ['${escapeScreeningHtml(manager.cik)}'])">
                                    Remove
                                </button>
                            `
                            : `
                                <button type="button" class="screening-roster-button add"
                                        onclick="mutateScreeningRoster(event, 'include', ['${escapeScreeningHtml(manager.cik)}'])">
                                    Add
                                </button>
                                <button type="button" class="screening-roster-button flag"
                                        onclick="mutateScreeningRoster(event, 'include', ['${escapeScreeningHtml(manager.cik)}'], true)">
                                    Add + flag
                                </button>
                            `}
                    </td>
                </tr>`;
        }).join('');
    }

    if (summary) {
        const end = Math.min(start + screeningPageSize, screeningData.length);
        summary.textContent = screeningData.length
            ? `${start + 1}-${end} of ${screeningData.length} managers`
            : '0 managers';
    }
    updateScreeningRosterControls();
}

function changeScreeningPage(direction) {
    const pageCount = Math.max(1, Math.ceil(screeningData.length / screeningPageSize));
    screeningPage = Math.max(1, Math.min(pageCount, screeningPage + direction));
    renderScreeningTable();
    document.querySelector('.screening-table-card')?.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function filingStatusClass(status) {
    return {
        PUBLISHED: 'published',
        COMPLETE: 'published',
        NO_CHANGES: 'neutral',
        RECORDED: 'recorded',
        BASELINED: 'baseline',
        HISTORICAL: 'historical',
        PARTIAL: 'warning',
        FAILED: 'failed',
        RUNNING: 'running'
    }[status] || 'neutral';
}

function formatOperationTimestamp(value) {
    if (!value) return 'Unavailable';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return escapeHtml(value);
    return parsed.toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: 'numeric',
        minute: '2-digit'
    });
}

function scheduleFilingOperationsLoad() {
    clearTimeout(filingOperationsLoadTimer);
    filingOperationsAbortController?.abort();
    filingOperationsLoadTimer = setTimeout(
        () => loadFilingOperations(true),
        250
    );
}

async function loadFilingOperations(reset = false) {
    if (reset) filingOperationsOffset = 0;
    const status = document.getElementById('filings-status')?.value || '';
    const search = document.getElementById('filings-search')?.value.trim() || '';
    const form = document.getElementById('filings-form-filter')?.value || '';
    const reportPeriod = document.getElementById('filings-period-filter')?.value || '';
    const params = new URLSearchParams({
        limit: String(filingOperationsPageSize),
        offset: String(filingOperationsOffset)
    });
    if (status) params.set('status', status);
    if (search) params.set('search', search);
    if (form) params.set('form', form);
    if (reportPeriod) params.set('report_period', reportPeriod);

    filingOperationsAbortController?.abort();
    const activeController = new AbortController();
    filingOperationsAbortController = activeController;
    setFilingOperationLoading(true);

    try {
        const response = await fetch(
            `/api/filings?${params.toString()}`,
            {signal: activeController.signal}
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        if (activeController !== filingOperationsAbortController) return;
        filingOperationsTotal = Number(payload.total || 0);
        syncFilingOperationFilterOptions(payload.filter_options || {});
        renderFilingOperationSummary(payload.summary || {}, payload.runs || []);
        renderFilingOperationRuns(payload.runs || []);
        renderFilingOperationRows(payload.filings || []);
        renderFilingOperationPagination();
        renderFilingOperationFilterState(payload.summary || {});
    } catch (error) {
        if (error.name === 'AbortError') return;
        const runsBody = document.getElementById('filings-runs-body');
        const filingsBody = document.getElementById('filings-table-body');
        if (runsBody) {
            runsBody.innerHTML = `<tr><td colspan="9" class="text-center py-4 text-red">Could not load run history: ${escapeHtml(error.message)}</td></tr>`;
        }
        if (filingsBody) {
            filingsBody.innerHTML = `<tr><td colspan="8" class="text-center py-4 text-red">Could not load filing records: ${escapeHtml(error.message)}</td></tr>`;
        }
    } finally {
        if (activeController === filingOperationsAbortController) {
            setFilingOperationLoading(false);
        }
    }
}

function renderFilingOperationSummary(summary, runs) {
    const latestRun = runs[0] || null;
    const health = document.getElementById('filings-health');
    const healthDetail = document.getElementById('filings-health-detail');
    const healthLabels = {
        COMPLETE: Number(latestRun?.baseline_filings) ? 'Baseline complete' : 'Complete',
        NO_CHANGES: 'Up to date',
        PARTIAL: 'Review needed',
        FAILED: 'Failed',
        RUNNING: 'Running'
    };
    if (health) {
        const status = latestRun?.status || '';
        health.textContent = healthLabels[status] || 'Not started';
        health.className = status === 'PARTIAL' || status === 'RUNNING'
            ? 'text-orange'
            : status === 'FAILED'
                ? 'text-red'
                : status
                    ? 'text-green'
                    : '';
    }
    if (healthDetail) {
        healthDetail.textContent = latestRun
            ? `${formatInt(latestRun.managers_checked)} managers checked · ${formatOperationTimestamp(latestRun.completed_at || latestRun.started_at)}`
            : 'No operational runs recorded';
    }

    setFilingMetric(
        'filings-new',
        latestRun?.new_filings,
        'filings-new-detail',
        latestRun
            ? `${formatInt(latestRun.filings_seen)} filings observed in the latest scan`
            : 'No operational runs recorded'
    );
    setFilingMetric(
        'filings-refreshed',
        latestRun?.refreshed_managers,
        'filings-refreshed-detail',
        Number(latestRun?.refreshed_managers)
            ? 'Manager caches rebuilt after detected work'
            : 'No cache rebuild was needed'
    );
    setFilingMetric(
        'filings-retry',
        summary.retry_queue,
        'filings-retry-detail',
        `${formatInt(summary.discovered_filings || 0)} pending · ${formatInt(summary.failed_filings || 0)} failed`,
        Number(summary.retry_queue) ? 'text-red' : ''
    );
    setFilingMetric(
        'filings-source-errors',
        latestRun?.error_count,
        'filings-source-errors-detail',
        Number(latestRun?.error_count)
            ? 'SEC source lookups need review'
            : 'No SEC source lookup errors',
        Number(latestRun?.error_count) ? 'text-orange' : ''
    );

    const inventorySummary = document.getElementById('filings-inventory-summary');
    if (inventorySummary) {
        inventorySummary.textContent = `${formatInt(summary.known_accessions || 0)} accessions across ${formatInt(summary.report_period_count || 0)} quarters · ${formatInt(summary.historical_accessions || 0)} from the historical archive · ${formatInt(summary.operational_accessions || 0)} from daily operations`;
    }

    const lastRun = document.getElementById('filings-last-run');
    if (lastRun) {
        lastRun.textContent = runs.length
            ? `Last run ${formatOperationTimestamp(runs[0].started_at)}`
            : 'No operational runs recorded';
    }
}

function setFilingMetric(valueId, value, detailId, detail, className = '') {
    const valueElement = document.getElementById(valueId);
    const detailElement = document.getElementById(detailId);
    if (valueElement) {
        valueElement.textContent = formatInt(value || 0);
        valueElement.className = className;
    }
    if (detailElement) detailElement.textContent = detail;
}

function syncFilingOperationFilterOptions(options) {
    syncFilingOperationSelect(
        'filings-form-filter',
        options.forms || [],
        'All forms',
        value => value
    );
    syncFilingOperationSelect(
        'filings-period-filter',
        options.report_periods || [],
        'All periods',
        value => formatCalendarDate(value)
    );
}

function syncFilingOperationSelect(id, values, emptyLabel, labelFormatter) {
    const select = document.getElementById(id);
    if (!select) return;
    const selectedValue = select.value;
    select.innerHTML = [
        `<option value="">${escapeHtml(emptyLabel)}</option>`,
        ...values.map(value => (
            `<option value="${escapeHtml(value)}">${escapeHtml(labelFormatter(value))}</option>`
        ))
    ].join('');
    select.value = values.includes(selectedValue) ? selectedValue : '';
}

function renderFilingOperationFilterState(summary) {
    const search = document.getElementById('filings-search')?.value.trim() || '';
    const status = document.getElementById('filings-status')?.value || '';
    const form = document.getElementById('filings-form-filter')?.value || '';
    const reportPeriod = document.getElementById('filings-period-filter')?.value || '';
    const hasFilters = Boolean(search || status || form || reportPeriod);
    const clearButton = document.getElementById('filings-clear-filters');
    const resultSummary = document.getElementById('filings-results-summary');

    if (clearButton) clearButton.disabled = !hasFilters;
    if (resultSummary) {
        resultSummary.textContent = hasFilters
            ? `${formatInt(filingOperationsTotal)} matching of ${formatInt(summary.known_accessions || 0)} ledger filings`
            : `${formatInt(filingOperationsTotal)} filing records`;
    }
}

function clearFilingOperationsFilters() {
    const search = document.getElementById('filings-search');
    const status = document.getElementById('filings-status');
    const form = document.getElementById('filings-form-filter');
    const reportPeriod = document.getElementById('filings-period-filter');
    if (search) search.value = '';
    if (status) status.value = '';
    if (form) form.value = '';
    if (reportPeriod) reportPeriod.value = '';
    loadFilingOperations(true);
}

function setFilingOperationLoading(loading) {
    const results = document.getElementById('filings-results');
    const previous = document.getElementById('filings-prev');
    const next = document.getElementById('filings-next');
    if (results) results.setAttribute('aria-busy', String(loading));
    if (previous) previous.disabled = loading || filingOperationsOffset === 0;
    if (next) {
        next.disabled = loading
            || filingOperationsOffset + filingOperationsPageSize >= filingOperationsTotal;
    }
}

function renderFilingOperationRuns(runs) {
    const body = document.getElementById('filings-runs-body');
    if (!body) return;
    if (!runs.length) {
        body.innerHTML = '<tr><td colspan="9" class="text-center py-4">No daily checks have run yet.</td></tr>';
        return;
    }
    body.innerHTML = runs.map(run => `
        <tr>
            <td>${escapeHtml(formatOperationTimestamp(run.started_at))}</td>
            <td><span class="filings-status ${filingStatusClass(run.status)}">${escapeHtml(run.status)}</span></td>
            <td>${escapeHtml(run.trigger)}</td>
            <td>${formatInt(run.managers_checked)}</td>
            <td>${formatInt(run.filings_seen)}</td>
            <td>${formatInt(run.new_filings)}</td>
            <td class="text-green">${formatInt(run.published_filings)}</td>
            <td>${formatInt(run.refreshed_managers)}</td>
            <td class="${Number(run.error_count) ? 'text-red' : ''}">${formatInt(run.error_count)}</td>
        </tr>
    `).join('');
}

function renderFilingOperationRows(filings) {
    const body = document.getElementById('filings-table-body');
    if (!body) return;
    if (!filings.length) {
        body.innerHTML = '<tr><td colspan="8" class="text-center py-4">No filing records match the current filters.</td></tr>';
        return;
    }
    body.innerHTML = filings.map(filing => {
        const accession = escapeHtml(filing.accession_number);
        return `
            <tr>
                <td>${escapeHtml(formatCalendarDate(filing.filing_date))}</td>
                <td>
                    <a href="/investor/${escapeHtml(filing.canonical_cik)}">
                        <strong>${escapeHtml(filing.manager_name)}</strong>
                    </a>
                    <span class="filings-manager-cik font-mono">${escapeHtml(filing.canonical_cik)}</span>
                </td>
                <td><span class="filings-form">${escapeHtml(filing.form)}</span></td>
                <td>${escapeHtml(formatCalendarDate(filing.report_period))}</td>
                <td class="font-mono">
                    <button type="button"
                            class="filings-accession-button"
                            onclick="openFilingDetail('${accession}')"
                            aria-label="Open readable filing detail for ${accession}">
                        ${accession}
                    </button>
                </td>
                <td class="font-mono">${escapeHtml(filing.source_cik)}</td>
                <td><span class="filings-status ${filingStatusClass(filing.status)}">${escapeHtml(filing.status)}</span></td>
                <td>${escapeHtml(formatOperationTimestamp(filing.first_seen_at))}</td>
            </tr>
        `;
    }).join('');
}

async function openFilingDetail(accession) {
    const dialog = document.getElementById('filing-detail-dialog');
    const title = document.getElementById('filing-detail-title');
    const accessionLabel = document.getElementById('filing-detail-accession');
    const body = document.getElementById('filing-detail-body');
    const footer = document.getElementById('filing-detail-footer');
    const status = document.getElementById('filing-detail-status');
    if (!dialog || !title || !accessionLabel || !body || !footer || !status) return;

    filingDetailAbortController?.abort();
    filingDetailAbortController = new AbortController();
    title.textContent = 'Filing detail';
    accessionLabel.textContent = accession;
    body.innerHTML = '<div class="filing-detail-loading">Loading filing detail...</div>';
    body.setAttribute('aria-busy', 'true');
    status.textContent = `Loading filing ${accession}`;
    footer.hidden = true;
    if (!dialog.open) {
        document.documentElement.classList.add('filing-detail-open');
        dialog.showModal();
    }

    try {
        const params = new URLSearchParams({accession});
        const response = await fetch(
            `/api/filings/detail?${params.toString()}`,
            {signal: filingDetailAbortController.signal}
        );
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.error || `HTTP ${response.status}`);
        }
        renderFilingDetail(payload.data || {});
    } catch (error) {
        if (error.name === 'AbortError') return;
        body.setAttribute('aria-busy', 'false');
        status.textContent = `Could not load filing ${accession}`;
        body.innerHTML = `
            <div class="filing-detail-empty">
                <strong>Could not load this filing.</strong>
                <span>${escapeHtml(error.message)}</span>
            </div>`;
    }
}

function renderFilingDetail(filing) {
    const title = document.getElementById('filing-detail-title');
    const body = document.getElementById('filing-detail-body');
    const footer = document.getElementById('filing-detail-footer');
    const sourceLink = document.getElementById('filing-detail-source');
    const status = document.getElementById('filing-detail-status');
    if (!title || !body || !footer || !sourceLink || !status) return;

    const summary = filing.summary || {};
    const sourceUrl = safeExternalNewsUrl(filing.source_url);
    const flags = [
        `<span class="filings-form">${escapeHtml(filing.form || '13F')}</span>`,
        `<span class="filings-status ${filingStatusClass(filing.status)}">${escapeHtml(filing.status || 'RECORDED')}</span>`,
        (summary.is_amendment ?? String(filing.form || '').endsWith('/A'))
            ? '<span class="filing-detail-flag amendment">AMENDMENT</span>'
            : '',
        summary.is_confidential_omitted
            ? '<span class="filing-detail-flag warning">CONFIDENTIAL HOLDINGS OMITTED</span>'
            : ''
    ].filter(Boolean).join('');
    const amendmentDetail = [
        summary.amendment_type,
        summary.amendment_number !== null && summary.amendment_number !== undefined
            ? `Amendment ${summary.amendment_number}`
            : null
    ].filter(Boolean).join(' · ');
    const signature = [summary.signer_name, summary.signer_title]
        .filter(Boolean)
        .map(escapeHtml)
        .join(' · ');

    title.textContent = filing.manager_name || 'Filing detail';
    body.innerHTML = `
        <section class="filing-detail-overview">
            <div class="filing-detail-flags">${flags}</div>
            <p>
                Reported for <strong>${escapeHtml(formatCalendarDate(filing.report_period))}</strong>
                and filed <strong>${escapeHtml(formatCalendarDate(filing.filing_date))}</strong>.
            </p>
            ${amendmentDetail
                ? `<p class="filing-detail-note">${escapeHtml(amendmentDetail)}</p>`
                : ''}
        </section>
        <section class="filing-detail-metrics" aria-label="Filing summary">
            ${filingDetailMetric('Reported value', formatFilingDollarValue(summary.total_value_usd))}
            ${filingDetailMetric('Information-table entries', formatFilingCount(summary.holding_count))}
            ${filingDetailMetric('Source CIK', filing.source_cik || 'Unavailable', true)}
            ${filingDetailMetric('Data source', filing.detail_source || 'Ledger')}
            ${filingDetailMetric('Put entries', formatFilingCount(summary.put_count))}
            ${filingDetailMetric('Call entries', formatFilingCount(summary.call_count))}
            ${filingDetailMetric(
                'Confidential holdings',
                summary.is_confidential_omitted === true
                    ? 'Omitted'
                    : summary.is_confidential_omitted === false
                        ? 'No omission reported'
                        : 'Unavailable'
            )}
        </section>
        ${signature
            ? `
                <section class="filing-detail-signature">
                    <span>Signed by</span>
                    <strong>${signature}</strong>
                    ${summary.signature_date
                        ? `<small>${escapeHtml(formatCalendarDate(summary.signature_date))}</small>`
                        : ''}
                </section>
            `
            : ''}
        ${renderFilingTopHoldings(filing)}
        ${summary.additional_information
            ? `
                <section class="filing-detail-additional">
                    <h3>Additional filing information</h3>
                    <p>${escapeHtml(summary.additional_information)}</p>
                </section>
            `
            : ''}
    `;
    body.setAttribute('aria-busy', 'false');
    status.textContent = `Filing detail loaded for ${filing.manager_name || filing.accession_number}`;

    if (sourceUrl) {
        sourceLink.href = sourceUrl;
        footer.hidden = false;
    } else {
        footer.hidden = true;
    }
}

function filingDetailMetric(label, value, mono = false) {
    return `
        <div>
            <span>${escapeHtml(label)}</span>
            <strong class="${mono ? 'font-mono' : ''}">${escapeHtml(value)}</strong>
        </div>`;
}

function renderFilingTopHoldings(filing) {
    const holdings = filing.top_holdings || [];
    if (!filing.holdings_available || !holdings.length) {
        return `
            <section class="filing-detail-empty">
                <strong>Holdings detail is not available locally.</strong>
                <span>${escapeHtml(filing.availability_note || 'The filing metadata remains available above.')}</span>
            </section>`;
    }
    return `
        <section class="filing-detail-holdings">
            <div class="filing-detail-section-heading">
                <div>
                    <h3>Largest reported positions</h3>
                    <p>Top ${formatInt(holdings.length)} information-table positions by reported value.</p>
                </div>
            </div>
            <div class="table-container filing-detail-table-wrap">
                <table class="data-table filing-detail-table">
                    <thead>
                        <tr>
                            <th>Position</th>
                            <th>Security</th>
                            <th>Class / CUSIP</th>
                            <th>Reported value</th>
                            <th>Weight</th>
                            <th>Shares / principal</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${holdings.map((holding, index) => `
                            <tr>
                                <td class="font-mono">${formatInt(index + 1)}</td>
                                <td>
                                    <strong>${escapeHtml(holding.ticker || holding.issuer || 'Unavailable')}</strong>
                                    <span>${escapeHtml(holding.issuer || '')}</span>
                                    ${holding.put_call
                                        ? `<small>${escapeHtml(holding.put_call)}</small>`
                                        : ''}
                                </td>
                                <td>
                                    ${escapeHtml(holding.title_of_class || '—')}
                                    <span class="font-mono">${escapeHtml(holding.cusip || '—')}</span>
                                </td>
                                <td class="font-mono">${escapeHtml(formatFilingDollarValue(holding.value_usd))}</td>
                                <td class="font-mono">${holding.portfolio_weight === null || holding.portfolio_weight === undefined
                                    ? '—'
                                    : `${Number(holding.portfolio_weight).toFixed(2)}%`}</td>
                                <td class="font-mono">
                                    ${formatFilingQuantity(holding.shares_or_principal)}
                                    ${escapeHtml(holding.shares_or_principal_type || '')}
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        </section>`;
}

function formatFilingDollarValue(value) {
    if (value === null || value === undefined || value === '') {
        return 'Unavailable';
    }
    const number = Number(value);
    if (!Number.isFinite(number)) return 'Unavailable';
    const absolute = Math.abs(number);
    if (absolute >= 1_000_000_000_000) return `$${(number / 1_000_000_000_000).toFixed(2)}T`;
    if (absolute >= 1_000_000_000) return `$${(number / 1_000_000_000).toFixed(2)}B`;
    if (absolute >= 1_000_000) return `$${(number / 1_000_000).toFixed(2)}M`;
    if (absolute >= 1_000) return `$${(number / 1_000).toFixed(2)}K`;
    return `$${number.toFixed(0)}`;
}

function formatFilingCount(value) {
    return value === null || value === undefined
        ? 'Unavailable'
        : formatInt(value);
}

function formatFilingQuantity(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return number.toLocaleString(undefined, {maximumFractionDigits: 2});
}

function closeFilingDetail() {
    filingDetailAbortController?.abort();
    document.getElementById('filing-detail-dialog')?.close();
}

function handleFilingDetailClosed() {
    filingDetailAbortController?.abort();
    document.documentElement.classList.remove('filing-detail-open');
}

function closeFilingDetailFromBackdrop(event) {
    if (event.target === event.currentTarget) closeFilingDetail();
}

function renderFilingOperationPagination() {
    const summary = document.getElementById('filings-page-summary');
    const indicator = document.getElementById('filings-page-indicator');
    const previous = document.getElementById('filings-prev');
    const next = document.getElementById('filings-next');
    const start = filingOperationsTotal ? filingOperationsOffset + 1 : 0;
    const end = Math.min(
        filingOperationsOffset + filingOperationsPageSize,
        filingOperationsTotal
    );
    const pageCount = Math.max(
        1,
        Math.ceil(filingOperationsTotal / filingOperationsPageSize)
    );
    const page = Math.min(
        pageCount,
        Math.floor(filingOperationsOffset / filingOperationsPageSize) + 1
    );
    if (summary) {
        summary.textContent = `${formatInt(start)}-${formatInt(end)} of ${formatInt(filingOperationsTotal)}`;
    }
    if (indicator) {
        indicator.textContent = `Page ${formatInt(page)} of ${formatInt(pageCount)}`;
    }
    if (previous) previous.disabled = filingOperationsOffset === 0;
    if (next) {
        next.disabled = filingOperationsOffset + filingOperationsPageSize >= filingOperationsTotal;
    }
}

function changeFilingOperationsPage(direction) {
    const nextOffset = filingOperationsOffset
        + direction * filingOperationsPageSize;
    if (nextOffset < 0 || nextOffset >= filingOperationsTotal) return;
    filingOperationsOffset = nextOffset;
    loadFilingOperations();
    document.querySelector('.filings-record-header')?.scrollIntoView({
        behavior: 'smooth',
        block: 'start'
    });
}

function changeFilingOperationsPageSize() {
    const value = Number(document.getElementById('filings-page-size')?.value);
    filingOperationsPageSize = [25, 50, 100].includes(value) ? value : 25;
    loadFilingOperations(true);
}
