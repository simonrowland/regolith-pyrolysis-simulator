/**
 * Plotly chart setup and chart initialization helpers.
 */

// --- Plotly Chart Initialization ---

const chartLayout = {
    margin: { t: 30, r: 20, b: 40, l: 55 },
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    font: { size: 11, family: '-apple-system, sans-serif' },
    xaxis: { title: 'Hour', gridcolor: '#e9ecef' },
    yaxis: { gridcolor: '#e9ecef' },
};
const chartConfig = { responsive: true, displayModeBar: false };

// Temperature profile
Plotly.newPlot('chart-temperature', [{
    x: [], y: [], mode: 'lines', name: 'Melt T',
    line: { color: '#dc2626', width: 2 }
}], {
    ...chartLayout,
    title: { text: 'Temperature Profile', font: { size: 13 } },
    yaxis: { ...chartLayout.yaxis, title: 'T (°C)' },
}, chartConfig);

// Melt composition (stacked area)
const oxideColors = {
    SiO2: '#6366f1', TiO2: '#8b5cf6', Al2O3: '#ec4899',
    FeO: '#ef4444', MgO: '#22c55e', CaO: '#eab308',
    Na2O: '#06b6d4', K2O: '#14b8a6', Cr2O3: '#f97316',
    MnO: '#a855f7', P2O5: '#64748b',
};
const compTraces = {};
let compInitialized = false;

// Overhead pressure
Plotly.newPlot('chart-pressure', [{
    x: [], y: [], mode: 'lines', name: 'Pressure',
    line: { color: '#2563eb', width: 2 }
}], {
    ...chartLayout,
    title: { text: 'Overhead Pressure', font: { size: 13 } },
    yaxis: { ...chartLayout.yaxis, title: 'mbar' },
}, chartConfig);

// Mass flow (evaporation rates by species)
const flowTraces = {};
let flowInitialized = false;

// Absolute composition chart (oxides above x-axis, metals below)
const metalColors = {
    Fe: '#ef4444', Si: '#6366f1', Mg: '#22c55e', Na: '#06b6d4',
    K: '#14b8a6', Ti: '#8b5cf6', Cr: '#f97316', Mn: '#a855f7',
    Al: '#ec4899', Ca: '#eab308', O2: '#3b82f6', SiO2: '#818cf8',
};
const absOxideTraces = {};
const absMetalTraces = {};
let absInitialized = false;

function initAbsoluteChart() {
    const traces = [];
    let idx = 0;
    // Oxides above x-axis (stacked area)
    for (const oxide of Object.keys(oxideColors)) {
        traces.push({
            x: [], y: [], name: oxide, stackgroup: 'oxides',
            line: { color: oxideColors[oxide], width: 0 },
            fillcolor: oxideColors[oxide] + '99',
            hovertemplate: oxide + ': %{y:.1f} kg<extra></extra>',
        });
        absOxideTraces[oxide] = idx++;
    }
    // Metals below x-axis (stacked, negative values)
    const metalOrder = ['Fe', 'Si', 'Mg', 'Na', 'K', 'Ti', 'Cr', 'Mn', 'Al', 'Ca', 'O2', 'SiO2'];
    for (const metal of metalOrder) {
        traces.push({
            x: [], y: [], name: metal + ' (product)', stackgroup: 'metals',
            line: { color: metalColors[metal] || '#999', width: 0 },
            fillcolor: (metalColors[metal] || '#999') + '66',
            hovertemplate: metal + ': %{customdata:.1f} kg<extra></extra>',
            customdata: [],
        });
        absMetalTraces[metal] = idx++;
    }
    Plotly.newPlot('chart-absolute', traces, {
        ...chartLayout,
        title: { text: 'Mass Inventory (kg)', font: { size: 13 } },
        yaxis: { ...chartLayout.yaxis, title: 'kg', zeroline: true, zerolinewidth: 2, zerolinecolor: '#374151' },
        hovermode: 'x unified',
        legend: { font: { size: 9 }, orientation: 'h', y: -0.2 },
    }, chartConfig);
    absInitialized = true;
}

// O₂ Budget chart (stored, vented, shaft power)
let o2BudgetInitialized = false;

function initO2BudgetChart() {
    Plotly.newPlot('chart-o2-budget', [
        {
            x: [], y: [], mode: 'lines', name: 'O₂ Stored',
            line: { color: '#2563eb', width: 2 },
            fill: 'tozeroy', fillcolor: 'rgba(37,99,235,0.15)',
        },
        {
            x: [], y: [], mode: 'lines', name: 'O₂ Vented',
            line: { color: '#dc2626', width: 2 },
            fill: 'tonexty', fillcolor: 'rgba(220,38,38,0.15)',
        },
        {
            x: [], y: [], mode: 'lines', name: 'Shaft Power',
            line: { color: '#f59e0b', width: 2, dash: 'dot' },
            yaxis: 'y2',
        },
    ], {
        ...chartLayout,
        title: { text: 'O₂ Budget & Turbine Power', font: { size: 13 } },
        yaxis: { ...chartLayout.yaxis, title: 'kg (cumulative)' },
        yaxis2: {
            title: 'kW',
            overlaying: 'y',
            side: 'right',
            gridcolor: 'transparent',
            showgrid: false,
        },
        legend: { x: 0.01, y: 0.99, font: { size: 10 } },
    }, chartConfig);
    o2BudgetInitialized = true;
}

// Melt Inventory chart (total mass over time, log scale, campaign boundaries)
let meltInvInitialized = false;
let lastCampaignForInv = '';

function initMeltInventoryChart() {
    Plotly.newPlot('chart-melt-inventory', [{
        x: [], y: [], mode: 'lines', name: 'Melt Mass',
        fill: 'tozeroy', fillcolor: 'rgba(99,102,241,0.15)',
        line: { color: '#6366f1', width: 2 },
        hovertemplate: '%{y:.0f} kg<extra></extra>',
    }], {
        ...chartLayout,
        title: { text: 'Melt Inventory', font: { size: 13 } },
        yaxis: { ...chartLayout.yaxis, title: 'kg', type: 'log' },
        shapes: [],
        annotations: [],
    }, chartConfig);
    meltInvInitialized = true;
}

// --- Composition chart helpers ---

function initCompositionChart(wt) {
    const traces = [];
    let idx = 0;
    for (const oxide of Object.keys(oxideColors)) {
        traces.push({
            x: [], y: [], name: oxide,
            mode: 'lines',
            line: { color: oxideColors[oxide], width: 2 },
            hovertemplate: oxide + ': %{y:.1f} kg<extra></extra>',
        });
        compTraces[oxide] = idx++;
    }
    // Melt mass line on right y-axis
    traces.push({
        x: [], y: [], name: 'Melt Mass',
        mode: 'lines',
        line: { color: '#374151', width: 2, dash: 'dot' },
        yaxis: 'y2',
        hovertemplate: 'Melt: %{y:.0f} kg<extra></extra>',
    });
    compTraces['_melt_mass'] = idx;
    Plotly.newPlot('chart-composition', traces, {
        ...chartLayout,
        title: { text: 'Melt Composition (kg)', font: { size: 13 } },
        yaxis: { ...chartLayout.yaxis, title: 'kg', type: 'log', dtick: 1 },
        yaxis2: {
            title: 'Total kg', overlaying: 'y', side: 'right',
            gridcolor: 'transparent', showgrid: false,
        },
        hovermode: 'x unified',
        legend: { x: 1.08, y: 1, font: { size: 10 } },
    }, chartConfig);
    compInitialized = true;
}

function initFlowChart(species) {
    const colors = ['#dc2626', '#2563eb', '#22c55e', '#eab308', '#8b5cf6', '#06b6d4', '#f97316', '#ec4899'];
    const traces = [];
    let idx = 0;
    for (const sp of species) {
        traces.push({
            x: [], y: [], mode: 'lines', name: sp,
            line: { color: colors[idx % colors.length], width: 2 },
        });
        flowTraces[sp] = idx++;
    }
    Plotly.newPlot('chart-massflow', traces, {
        ...chartLayout,
        title: { text: 'Evaporation Flux', font: { size: 13 } },
        yaxis: { ...chartLayout.yaxis, title: 'kg/hr' },
    }, chartConfig);
    flowInitialized = true;
}
