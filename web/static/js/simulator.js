/**
 * Simulator UI — Plotly charts + SocketIO real-time updates
 */

const socket = io();

socket.on('connect', () => {
    console.log('Connected to simulator server');
});

socket.on('simulation_status', (data) => {
    const el = document.getElementById('status-text');
    if (el) el.textContent = data.status;
    if (data.message) console.log(data.message);
});

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

// --- Real-time update handler ---

socket.on('simulation_tick', (data) => {
    // Status bar
    const setEl = (id, text) => { const e = document.getElementById(id); if (e) e.textContent = text; };
    setEl('status-hour', 'Hour: ' + data.hour);
    setEl('status-temp', 'T: ' + data.temperature_C.toFixed(0) + ' °C');
    setEl('status-campaign', data.campaign);
    setEl('status-mass', 'Melt: ' + data.melt_mass_kg.toFixed(0) + ' kg');

    // Temperature chart
    Plotly.extendTraces('chart-temperature', {
        x: [[data.hour]], y: [[data.temperature_C]]
    }, [0]);

    // Pressure chart
    Plotly.extendTraces('chart-pressure', {
        x: [[data.hour]], y: [[Math.max(data.pressure_mbar, 0.001)]]
    }, [0]);

    // Composition chart (absolute kg per oxide, with melt mass line)
    const wt = data.composition_wt_pct || {};
    if (!compInitialized) initCompositionChart(wt);
    const compUpdate = { x: [], y: [] };
    const compIndices = [];
    for (const oxide of Object.keys(oxideColors)) {
        const idx = compTraces[oxide];
        if (idx !== undefined) {
            const kg = (wt[oxide] || 0) / 100.0 * data.melt_mass_kg;
            compUpdate.x.push([data.hour]);
            compUpdate.y.push([kg > 0.01 ? kg : null]);  // null hides zero on log scale
            compIndices.push(idx);
        }
    }
    // Melt mass trace (right y-axis)
    const massIdx = compTraces['_melt_mass'];
    if (massIdx !== undefined) {
        compUpdate.x.push([data.hour]);
        compUpdate.y.push([data.melt_mass_kg]);
        compIndices.push(massIdx);
    }
    if (compIndices.length > 0) {
        Plotly.extendTraces('chart-composition', compUpdate, compIndices);
    }

    // Absolute composition chart (oxides above, metals below x-axis)
    if (!absInitialized) initAbsoluteChart();
    // Oxides: compute absolute kg from wt% × melt mass (no customdata)
    const oxideUpdate = { x: [], y: [] };
    const oxideIndices = [];
    for (const oxide of Object.keys(oxideColors)) {
        const idx = absOxideTraces[oxide];
        if (idx !== undefined) {
            const kg = (wt[oxide] || 0) / 100.0 * data.melt_mass_kg;
            oxideUpdate.x.push([data.hour]);
            oxideUpdate.y.push([kg]);
            oxideIndices.push(idx);
        }
    }
    if (oxideIndices.length > 0) {
        Plotly.extendTraces('chart-absolute', oxideUpdate, oxideIndices);
    }
    // Metals: negative values (below x-axis), from condensation totals
    const cond = data.condensation || {};
    const metalOrder = ['Fe', 'Si', 'Mg', 'Na', 'K', 'Ti', 'Cr', 'Mn', 'Al', 'Ca', 'O2', 'SiO2'];
    const metalUpdate = { x: [], y: [], customdata: [] };
    const metalIndices = [];
    for (const metal of metalOrder) {
        const idx = absMetalTraces[metal];
        if (idx !== undefined) {
            const kg = cond[metal] || 0;
            metalUpdate.x.push([data.hour]);
            metalUpdate.y.push([-kg]);       // negative for below x-axis
            metalUpdate.customdata.push([kg]); // positive for hover display
            metalIndices.push(idx);
        }
    }
    if (metalIndices.length > 0) {
        Plotly.extendTraces('chart-absolute', metalUpdate, metalIndices);
    }

    // Evaporation flux chart
    const evap = data.evap_species || {};
    const evapKeys = Object.keys(evap);
    if (evapKeys.length > 0 && !flowInitialized) initFlowChart(evapKeys);
    if (flowInitialized) {
        const flowUpdate = { x: [], y: [] };
        const flowIndices = [];
        for (const sp of Object.keys(flowTraces)) {
            flowUpdate.x.push([data.hour]);
            flowUpdate.y.push([evap[sp] || 0]);
            flowIndices.push(flowTraces[sp]);
        }
        if (flowIndices.length > 0) {
            Plotly.extendTraces('chart-massflow', flowUpdate, flowIndices);
        }
    }

    // Condensation train DOM update (cond already declared above)
    for (const [species, kg] of Object.entries(cond)) {
        const el = document.getElementById('cond-' + species);
        if (el) el.textContent = kg.toFixed(2) + ' kg';
    }

    // Energy
    setEl('energy-cumulative', data.energy_cumulative_kWh.toFixed(1) + ' kWh');
    setEl('energy-hour', data.energy_kWh.toFixed(3) + ' kWh');

    // O2
    setEl('oxygen-total', data.oxygen_kg.toFixed(2) + ' kg');

    // Mass balance
    setEl('mass-error', data.mass_balance_error_pct.toFixed(3) + '%');

    // --- O₂ Budget chart ---
    if (!o2BudgetInitialized) initO2BudgetChart();
    Plotly.extendTraces('chart-o2-budget', {
        x: [[data.hour], [data.hour], [data.hour]],
        y: [
            [data.O2_stored_kg || 0],
            [(data.O2_stored_kg || 0) + (data.O2_vented_cumulative_kg || 0)],
            [data.turbine_shaft_power_kW || 0],
        ],
    }, [0, 1, 2]);

    // --- Melt Inventory chart ---
    if (!meltInvInitialized) initMeltInventoryChart();
    Plotly.extendTraces('chart-melt-inventory', {
        x: [[data.hour]], y: [[data.melt_mass_kg]]
    }, [0]);
    // Add campaign boundary annotation when campaign changes
    if (data.campaign && data.campaign !== lastCampaignForInv) {
        if (lastCampaignForInv !== '') {
            const curLayout = document.getElementById('chart-melt-inventory');
            const shapes = (curLayout && curLayout.layout && curLayout.layout.shapes) ? [...curLayout.layout.shapes] : [];
            const annotations = (curLayout && curLayout.layout && curLayout.layout.annotations) ? [...curLayout.layout.annotations] : [];
            shapes.push({
                type: 'line', x0: data.hour, x1: data.hour,
                y0: 0, y1: 1, yref: 'paper',
                line: { color: '#9ca3af', width: 1, dash: 'dash' },
            });
            annotations.push({
                x: data.hour, y: 1.02, yref: 'paper',
                text: data.campaign, showarrow: false,
                font: { size: 9, color: '#6b7280' },
            });
            Plotly.relayout('chart-melt-inventory', { shapes: shapes, annotations: annotations });
        }
        lastCampaignForInv = data.campaign;
    }

    // --- Gas Train Status panel ---
    setEl('gt-ramp-actual', (data.actual_ramp_rate || 0).toFixed(1));
    setEl('gt-ramp-nominal', '(nominal: ' + (data.nominal_ramp_rate || 0).toFixed(1) + ')');

    const pipeSat = data.transport_saturation_pct || 0;
    setEl('gt-pipe-sat', pipeSat.toFixed(0));
    updateBar('gt-pipe-bar', pipeSat);

    const turbLoad = data.turbine_utilization_pct || 0;
    setEl('gt-turbine-load', turbLoad.toFixed(0));
    updateBar('gt-turbine-bar', turbLoad);

    setEl('gt-shaft-power', (data.turbine_shaft_power_kW || 0).toFixed(3));
    setEl('gt-o2-stored', (data.O2_stored_kg || 0).toFixed(1));
    setEl('gt-o2-vented', (data.O2_vented_cumulative_kg || 0).toFixed(1));
    setEl('gt-vent-rate', '(' + (data.O2_vented_kg_hr || 0).toFixed(3) + ' kg/hr)');

    // Throttle reason
    const throttleEl = document.getElementById('gt-throttle-reason');
    if (throttleEl) {
        if (data.ramp_throttled && data.throttle_reason) {
            throttleEl.textContent = '⚠ ' + data.throttle_reason;
            throttleEl.style.display = 'block';
        } else {
            throttleEl.style.display = 'none';
        }
    }

    // Status bar indicators
    const rampInd = document.getElementById('status-ramp');
    const ventInd = document.getElementById('status-vent');
    if (rampInd) rampInd.style.display = data.ramp_throttled ? 'inline' : 'none';
    if (ventInd) ventInd.style.display = (data.O2_vented_kg_hr > 0) ? 'inline' : 'none';

    // --- Alkali Shuttle Status (C3) ---
    const shuttleCard = document.getElementById('shuttle-card');
    if (shuttleCard) {
        const isC3 = data.campaign && data.campaign.startsWith('C3');
        shuttleCard.style.display = isC3 ? 'block' : 'none';
        if (isC3) {
            setEl('sh-phase', data.shuttle_phase || '—');
            setEl('sh-cycle', data.shuttle_cycle || 0);
            setEl('sh-injected', (data.shuttle_injected_kg_hr || 0).toFixed(3));
            setEl('sh-reduced', (data.shuttle_reduced_kg_hr || 0).toFixed(3));
            setEl('sh-metal', (data.shuttle_metal_produced_kg_hr || 0).toFixed(3));
            setEl('sh-k-inv', (data.shuttle_K_inventory_kg || 0).toFixed(2));
            setEl('sh-na-inv', (data.shuttle_Na_inventory_kg || 0).toFixed(2));
            // Inventory bars — show depletion (100% = full starting inventory)
            const kBar = document.getElementById('sh-k-bar');
            const naBar = document.getElementById('sh-na-bar');
            if (kBar) {
                const kPct = Math.min(100, (data.shuttle_K_inventory_kg || 0) / 30 * 100);
                kBar.style.width = kPct + '%';
                kBar.classList.toggle('bar-warning', kPct < 30);
                kBar.classList.toggle('bar-danger', kPct < 10);
            }
            if (naBar) {
                const naPct = Math.min(100, (data.shuttle_Na_inventory_kg || 0) / 120 * 100);
                naBar.style.width = naPct + '%';
                naBar.classList.toggle('bar-warning', naPct < 30);
                naBar.classList.toggle('bar-danger', naPct < 10);
            }
        }
    }

    // --- MRE Electrolysis Status ---
    const mreCard = document.getElementById('mre-card');
    if (mreCard) {
        const isMRE = data.campaign === 'MRE_BASELINE' || data.campaign === 'C5';
        mreCard.style.display = isMRE ? 'block' : 'none';
        if (isMRE) {
            setEl('mre-voltage', (data.mre_voltage_V || 0).toFixed(2));
            setEl('mre-current', (data.mre_current_A || 0).toFixed(0));
            setEl('mre-energy-hr', (data.mre_energy_kWh || 0).toFixed(3));
            const metals = data.mre_metals_kg_hr || {};
            const parts = [];
            for (const [m, kg] of Object.entries(metals)) {
                if (kg > 0.001) parts.push(m + ': ' + kg.toFixed(3));
            }
            setEl('mre-metals-hr', parts.join(', ') || '--');
        }
    }
});

// --- Gas train bar helper ---
function updateBar(barId, pct) {
    const bar = document.getElementById(barId);
    if (!bar) return;
    const clamped = Math.min(pct, 200);
    bar.style.width = Math.min(clamped, 100) + '%';
    bar.classList.remove('bar-warning', 'bar-danger');
    if (pct > 120) bar.classList.add('bar-danger');
    else if (pct > 80) bar.classList.add('bar-warning');
}

// --- Decision handling ---

socket.on('decision_required', (data) => {
    console.log('Decision required:', data);
    showDecisionModal(data);
});

function showDecisionModal(data) {
    // Remove any existing modal
    const existing = document.getElementById('decision-modal');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'decision-modal';
    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:1000;';

    const modal = document.createElement('div');
    modal.style.cssText = 'background:white;padding:24px;border-radius:12px;max-width:500px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,0.3);';

    const title = document.createElement('h3');
    title.textContent = 'Decision Required: ' + data.type;
    title.style.cssText = 'margin:0 0 12px 0;font-size:16px;';
    modal.appendChild(title);

    const context = document.createElement('p');
    context.textContent = data.context;
    context.style.cssText = 'font-size:13px;color:#555;margin:0 0 16px 0;line-height:1.5;';
    modal.appendChild(context);

    const rec = document.createElement('p');
    rec.textContent = 'Recommended: ' + data.recommendation;
    rec.style.cssText = 'font-size:12px;color:#2563eb;font-weight:600;margin:0 0 16px 0;';
    modal.appendChild(rec);

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;';

    for (const opt of data.options) {
        const btn = document.createElement('button');
        btn.textContent = opt;
        btn.className = 'btn' + (opt === data.recommendation ? ' btn-primary' : '');
        btn.style.cssText = 'padding:8px 20px;border-radius:6px;border:1px solid #ccc;cursor:pointer;font-size:14px;' +
            (opt === data.recommendation ? 'background:#4f46e5;color:white;border-color:#4f46e5;' : '');
        btn.addEventListener('click', () => {
            socket.emit('make_decision', { choice: opt });
            overlay.remove();
        });
        btnRow.appendChild(btn);
    }

    modal.appendChild(btnRow);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);
}

// --- Completion handler ---

socket.on('simulation_complete', (data) => {
    const el = document.getElementById('status-text');
    if (el) el.textContent = 'Complete';
    document.getElementById('btn-start').disabled = false;
    document.getElementById('btn-pause').disabled = true;
    console.log('Simulation complete:', data);
});

// --- Control buttons ---

document.getElementById('btn-start').addEventListener('click', () => {
    const feedstock = document.getElementById('feedstock-select').value;
    if (!feedstock) {
        alert('Please select a feedstock first.');
        return;
    }

    const track = document.querySelector('input[name="track"]:checked').value;
    const speedMs = parseInt(document.querySelector('input[name="speed"]:checked').value);
    const mass_kg = parseFloat(document.getElementById('batch-mass').value);

    socket.emit('start_simulation', {
        feedstock: feedstock,
        mass_kg: mass_kg,
        backend: document.getElementById('engine-select').value,
        track: track,
        speed: speedMs / 1000.0,  // Convert ms to seconds for backend
        c4_max_temp_C: parseFloat(document.getElementById('c4-max-temp')?.value) || 1670,
        additives: {
            Na: parseFloat(document.getElementById('add-na').value) || 0,
            K: parseFloat(document.getElementById('add-k').value) || 0,
            Mg: parseFloat(document.getElementById('add-mg').value) || 0,
            Ca: parseFloat(document.getElementById('add-ca').value) || 0,
            C: parseFloat(document.getElementById('add-c').value) || 0,
        },
    });

    // Reset ALL charts — re-initialise temperature & pressure inline,
    // and set lazy-init flags to false so other charts re-create on first tick.
    Plotly.newPlot('chart-temperature', [{
        x: [], y: [], mode: 'lines', name: 'Melt T',
        line: { color: '#dc2626', width: 2 }
    }], {
        ...chartLayout,
        title: { text: 'Temperature Profile', font: { size: 13 } },
        yaxis: { ...chartLayout.yaxis, title: 'T (°C)' },
    }, chartConfig);

    Plotly.newPlot('chart-pressure', [{
        x: [], y: [], mode: 'lines', name: 'Pressure',
        line: { color: '#2563eb', width: 2 }
    }], {
        ...chartLayout,
        title: { text: 'Overhead Pressure', font: { size: 13 } },
        yaxis: { ...chartLayout.yaxis, title: 'mbar' },
    }, chartConfig);

    compInitialized = false;
    flowInitialized = false;
    absInitialized = false;
    o2BudgetInitialized = false;
    meltInvInitialized = false;
    lastCampaignForInv = '';

    // Reset campaign summaries
    const summaryContainer = document.getElementById('campaign-summaries');
    if (summaryContainer) {
        summaryContainer.style.display = 'none';
        // Remove all details children but keep the h3
        const details = summaryContainer.querySelectorAll('details');
        details.forEach(d => d.remove());
    }

    // Reset user-edited flags on additive inputs
    document.querySelectorAll('.additive-grid input').forEach(input => {
        delete input.dataset.userEdited;
    });

    document.getElementById('btn-start').disabled = true;
    document.getElementById('btn-pause').disabled = false;
    document.getElementById('status-text').textContent = 'Running';
});

document.getElementById('btn-pause').addEventListener('click', () => {
    socket.emit('pause_simulation');
    document.getElementById('btn-pause').disabled = true;
    document.getElementById('btn-resume').disabled = false;
    document.getElementById('status-text').textContent = 'Paused';
});

document.getElementById('btn-resume').addEventListener('click', () => {
    socket.emit('resume_simulation');
    document.getElementById('btn-resume').disabled = true;
    document.getElementById('btn-pause').disabled = false;
    document.getElementById('status-text').textContent = 'Running';
});

// --- Event delegation for dynamically loaded controls ---
// Handles .ctrl-param elements loaded via HTMX disclosure triangles
document.addEventListener('change', (e) => {
    if (e.target.classList.contains('ctrl-param')) {
        const param = e.target.dataset.param;
        const value = parseFloat(e.target.value);
        if (param && !isNaN(value)) {
            socket.emit('adjust_parameter', { param: param, value: value });
        }
    }
    // Campaign-specific parameter controls
    if (e.target.classList.contains('campaign-ctrl')) {
        const controls = e.target.closest('.campaign-controls');
        if (!controls) return;
        // Map disclosure section names to campaign enum names
        const sectionToCampaign = {
            'C0': 'C0', 'C2A_continuous': 'C2A', 'C2B': 'C2B',
            'C3': 'C3_K', 'C4': 'C4', 'C5': 'C5', 'C6': 'C6',
        };
        const section = controls.dataset.campaign;
        const campaign = sectionToCampaign[section] || section;
        const field = e.target.dataset.field;
        const rawValue = e.target.value;
        if (campaign && field && rawValue !== '') {
            socket.emit('adjust_parameter', {
                param: 'campaign_override',
                campaign: campaign,
                field: field,
                value: parseFloat(rawValue),
            });
            // For C3, also set C3_NA
            if (section === 'C3') {
                socket.emit('adjust_parameter', {
                    param: 'campaign_override',
                    campaign: 'C3_NA',
                    field: field,
                    value: parseFloat(rawValue),
                });
            }
        }
    }
});

// HTMX feedstock card loading + additive auto-population
// Use htmx.ajax() directly instead of re-triggering 'change' (which
// would cause an infinite event loop between JS listener and HTMX).
document.getElementById('feedstock-select').addEventListener('change', (e) => {
    const val = e.target.value;
    if (val) {
        htmx.ajax('GET', '/partials/feedstock-card/' + val, '#feedstock-info');
        fetchAdditives(val);
    }
});

// Re-fetch additives when batch mass changes
document.getElementById('batch-mass').addEventListener('change', () => {
    const feedstock = document.getElementById('feedstock-select').value;
    if (feedstock) fetchAdditives(feedstock);
});

function fetchAdditives(feedstockKey) {
    const mass = parseFloat(document.getElementById('batch-mass').value) || 1000;
    fetch('/api/additive-calc/' + feedstockKey + '?mass_kg=' + mass)
        .then(r => r.json())
        .then(data => {
            if (data.error) return;
            const fields = {Na: 'add-na', K: 'add-k', Mg: 'add-mg', Ca: 'add-ca', C: 'add-c'};
            for (const [species, elId] of Object.entries(fields)) {
                const el = document.getElementById(elId);
                if (el && !el.dataset.userEdited) {
                    el.value = (data[species] || 0).toFixed(1);
                }
            }
        })
        .catch(() => {});
}

// Mark additive inputs as user-edited when manually changed
document.querySelectorAll('.additive-grid input').forEach(input => {
    input.addEventListener('input', () => { input.dataset.userEdited = 'true'; });
});

// --- Campaign summary handler ---
socket.on('campaign_complete_summary', (summary) => {
    const container = document.getElementById('campaign-summaries');
    if (!container) return;
    container.style.display = 'block';

    const details = document.createElement('details');
    const summaryEl = document.createElement('summary');
    summaryEl.textContent = summary.campaign + ' Complete — '
        + summary.duration_h + ' hrs, '
        + summary.mass_lost_kg.toFixed(1) + ' kg extracted';
    details.appendChild(summaryEl);

    const div = document.createElement('div');
    div.className = 'disclosure-content';

    // Build a summary table
    const table = document.createElement('table');
    table.className = 'param-table';

    const rows = [
        ['Duration', summary.duration_h + ' hours'],
        ['Start Mass', summary.start_mass_kg.toFixed(1) + ' kg'],
        ['End Mass', summary.end_mass_kg.toFixed(1) + ' kg'],
        ['Mass Lost', summary.mass_lost_kg.toFixed(1) + ' kg'],
        ['Energy This Campaign', summary.energy_kWh.toFixed(1) + ' kWh'],
        ['O\u2082 Produced', summary.O2_kg.toFixed(2) + ' kg'],
    ];

    for (const [label, value] of rows) {
        const tr = document.createElement('tr');
        const td1 = document.createElement('td');
        td1.textContent = label;
        const td2 = document.createElement('td');
        td2.textContent = value;
        tr.appendChild(td1);
        tr.appendChild(td2);
        table.appendChild(tr);
    }

    // Species breakdown
    if (summary.species_extracted && Object.keys(summary.species_extracted).length > 0) {
        const specRow = document.createElement('tr');
        const specTd1 = document.createElement('td');
        specTd1.textContent = 'Species Extracted';
        specTd1.style.verticalAlign = 'top';
        const specTd2 = document.createElement('td');
        const specParts = [];
        for (const [sp, kg] of Object.entries(summary.species_extracted)) {
            if (kg > 0.01) specParts.push(sp + ': ' + kg.toFixed(2) + ' kg');
        }
        specTd2.textContent = specParts.join(', ');
        specRow.appendChild(specTd1);
        specRow.appendChild(specTd2);
        table.appendChild(specRow);
    }

    div.appendChild(table);
    details.appendChild(div);
    container.appendChild(details);
});

// --- Log/Linear scale toggles ---

// Composition chart: starts in LOG mode (active)
const compScaleBtn = document.getElementById('comp-scale-toggle');
if (compScaleBtn) {
    compScaleBtn.classList.add('active');
    compScaleBtn.addEventListener('click', () => {
        const chartEl = document.getElementById('chart-composition');
        if (!chartEl || !chartEl.layout) return;
        const currentType = chartEl.layout.yaxis?.type || 'linear';
        const newType = currentType === 'log' ? 'linear' : 'log';
        Plotly.relayout('chart-composition', { 'yaxis.type': newType });
        compScaleBtn.textContent = newType === 'log' ? 'LOG' : 'LIN';
        compScaleBtn.classList.toggle('active', newType === 'log');
    });
}

// Absolute (yield) chart: starts in LINEAR mode
const absScaleBtn = document.getElementById('abs-scale-toggle');
if (absScaleBtn) {
    absScaleBtn.addEventListener('click', () => {
        const chartEl = document.getElementById('chart-absolute');
        if (!chartEl || !chartEl.layout) return;
        const currentType = chartEl.layout.yaxis?.type || 'linear';
        const newType = currentType === 'log' ? 'linear' : 'log';
        Plotly.relayout('chart-absolute', { 'yaxis.type': newType });
        absScaleBtn.textContent = newType === 'log' ? 'LOG' : 'LIN';
        absScaleBtn.classList.toggle('active', newType === 'log');
    });
}
