/**
 * Simulation tick handler and live status-panel updates.
 */

// --- Real-time update handler ---

socket.on('simulation_tick', (data) => {
    // Status bar
    const setEl = (id, text) => { const e = document.getElementById(id); if (e) e.textContent = text; };
    setEl('status-hour', 'Hour: ' + data.hour);
    setEl('status-temp', 'T: ' + data.temperature_C.toFixed(0) + ' °C');
    setEl('status-campaign', data.campaign);
    setEl('status-mass', 'Melt: ' + data.melt_mass_kg.toFixed(0) + ' kg');
    const atmosphereLabel = data.atmosphere === 'CO2_BACKPRESSURE'
        ? 'Mars CO₂: ' + (data.p_total_mbar || 0).toFixed(1) + ' mbar'
        : data.atmosphere === 'HARD_VACUUM'
            ? 'Hard vacuum'
            : (data.atmosphere || '—');
    setEl('status-atmosphere', 'Atmosphere: ' + atmosphereLabel);

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
