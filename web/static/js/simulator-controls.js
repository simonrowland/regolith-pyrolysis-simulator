/**
 * Control buttons, feedstock helpers, summaries, and scale toggles.
 */

// --- Control buttons ---

function selectedMreOption() {
    const select = document.getElementById('mre-preset');
    return select ? select.options[select.selectedIndex] : null;
}

function updateMreFields() {
    const enabled = document.getElementById('mre-enabled')?.checked === true;
    const fields = document.getElementById('mre-fields');
    const select = document.getElementById('mre-preset');
    if (fields) fields.hidden = !enabled;
    if (select && !enabled) select.value = 'off';

    const option = selectedMreOption();
    const voltage = enabled && option ? parseFloat(option.dataset.maxVoltage || '0') : 0;
    const target = enabled && option ? option.dataset.targetSpecies || '' : '';
    const included = enabled && option ? option.dataset.includedSpecies || 'none' : 'none';
    const voltageEl = document.getElementById('mre-max-voltage');
    const speciesEl = document.getElementById('mre-included-species');
    if (voltageEl) voltageEl.value = Number.isFinite(voltage) ? voltage.toFixed(3) : '0.000';
    if (speciesEl) {
        speciesEl.textContent = target
            ? `Included species: ${included}`
            : 'Included species: none';
    }
}

function optionForMrePreset(preset) {
    const option = document.createElement('option');
    option.value = preset.id;
    option.textContent = preset.label + (preset.enabled === false ? ' (disabled)' : '');
    option.dataset.c5Enabled = preset.c5_enabled ? 'true' : 'false';
    option.dataset.targetSpecies = preset.mre_target_species || '';
    option.dataset.maxVoltage = String(preset.mre_max_voltage_V || 0);
    option.dataset.includedSpecies = preset.included_species_label || 'none';
    option.disabled = preset.enabled === false;
    if (preset.disabled_reason) option.title = preset.disabled_reason;
    if (preset.id === 'off') option.selected = true;
    return option;
}

function hydrateMrePresetCatalog() {
    const select = document.getElementById('mre-preset');
    if (!select || !select.dataset.catalogUrl) {
        updateMreFields();
        return;
    }
    fetch(select.dataset.catalogUrl)
        .then(r => r.ok ? r.json() : Promise.reject(new Error('catalog unavailable')))
        .then(data => {
            if (!Array.isArray(data.presets)) return;
            select.replaceChildren(...data.presets.map(optionForMrePreset));
            select.value = 'off';
            updateMreFields();
        })
        .catch(() => updateMreFields());
}

function selectedMrePayload() {
    const enabled = document.getElementById('mre-enabled')?.checked === true;
    const option = selectedMreOption();
    if (!enabled || !option || option.value === 'off') {
        return {
            c5_enabled: false,
            mre_target_species: '',
            mre_max_voltage_V: 0,
        };
    }
    return {
        c5_enabled: option.dataset.c5Enabled === 'true',
        mre_target_species: option.dataset.targetSpecies || '',
        mre_max_voltage_V: parseFloat(option.dataset.maxVoltage || '0') || 0,
    };
}

function selectedLeverCampaign() {
    return document.getElementById('lever-campaign')?.value || 'C4';
}

function buildRuntimeCampaignOverrides() {
    const campaign = selectedLeverCampaign();
    const fields = {};
    document.querySelectorAll('.recipe-lever[data-field]').forEach(input => {
        const value = parseFloat(input.value);
        if (input.dataset.field && Number.isFinite(value)) {
            fields[input.dataset.field] = value;
        }
    });
    return Object.keys(fields).length ? {[campaign]: fields} : {};
}

function numericDataAttribute(element, name) {
    const value = parseFloat(element.getAttribute(`data-${name}`) || '');
    return Number.isFinite(value) ? value : null;
}

function knudsenDisplayConfig(indicator) {
    const config = {
        boltzmannConstantJK: numericDataAttribute(indicator, 'boltzmann-constant-j-k'),
        characteristicLengthM: numericDataAttribute(indicator, 'characteristic-length-m'),
        n2CollisionDiameterM: numericDataAttribute(indicator, 'n2-collision-diameter-m'),
        continuumBufferKn: numericDataAttribute(indicator, 'continuum-buffer-kn'),
    };
    if (
        config.boltzmannConstantJK === null
        || config.characteristicLengthM === null
        || config.characteristicLengthM <= 0
        || config.n2CollisionDiameterM === null
        || config.n2CollisionDiameterM <= 0
        || config.continuumBufferKn === null
        || config.continuumBufferKn <= 0
    ) {
        return null;
    }
    return config;
}

function updateKnudsenIndicator() {
    const pressureMbar = parseFloat(document.getElementById('lever-pn2-mbar')?.value || '0');
    const tempC = parseFloat(document.getElementById('lever-stage-temp')?.value || '1600');
    const indicator = document.getElementById('knudsen-indicator');
    if (!indicator || !Number.isFinite(pressureMbar) || pressureMbar <= 0) {
        if (indicator) indicator.textContent = 'Kn: unavailable';
        return;
    }
    const config = knudsenDisplayConfig(indicator);
    if (!config) {
        indicator.textContent = 'Kn: unavailable';
        indicator.classList.remove('config-warning');
        return;
    }
    const pressurePa = pressureMbar * 100;
    const meanFreePathM = config.boltzmannConstantJK * (tempC + 273.15)
        / (Math.SQRT2 * Math.PI * config.n2CollisionDiameterM ** 2 * pressurePa);
    const kn = meanFreePathM / config.characteristicLengthM;
    indicator.textContent = `Kn: ${kn.toExponential(2)}`
        + (kn >= config.continuumBufferKn
            ? ' - molecular flow / coating risk'
            : ' - viscous transport');
    indicator.classList.toggle('config-warning', kn >= config.continuumBufferKn);
}

function updateLeverWarning() {
    const warning = document.getElementById('lever-warning');
    if (!warning) return;
    const messages = [];
    const pressureMbar = parseFloat(document.getElementById('lever-pn2-mbar')?.value || '0');
    const tempC = parseFloat(document.getElementById('lever-stage-temp')?.value || '0');
    if (Number.isFinite(pressureMbar) && (pressureMbar < 5 || pressureMbar > 15)) {
        messages.push('pN2 sweep outside 5-15 mbar viscous-flow band');
    }
    if (Number.isFinite(tempC) && (tempC < 20 || tempC > 1900)) {
        messages.push('stage temperature outside characterized operator band');
    }
    warning.hidden = messages.length === 0;
    warning.textContent = messages.join('; ');
}

hydrateMrePresetCatalog();
document.getElementById('mre-enabled')?.addEventListener('change', updateMreFields);
document.getElementById('mre-preset')?.addEventListener('change', updateMreFields);
document.getElementById('lever-pn2-mbar')?.addEventListener('input', () => {
    updateKnudsenIndicator();
    updateLeverWarning();
});
document.getElementById('lever-stage-temp')?.addEventListener('input', () => {
    updateKnudsenIndicator();
    updateLeverWarning();
});
updateKnudsenIndicator();
updateLeverWarning();

document.getElementById('btn-start').addEventListener('click', () => {
    if (!socket.connected) {
        document.getElementById('status-text').textContent = 'Connection not ready';
        return;
    }

    const feedstock = document.getElementById('feedstock-select').value;
    if (!feedstock) {
        alert('Please select a feedstock first.');
        return;
    }

    const track = document.querySelector('input[name="track"]:checked').value;
    const speedMs = parseInt(document.querySelector('input[name="speed"]:checked').value);
    const mass_kg = parseFloat(document.getElementById('batch-mass').value);
    const mrePayload = selectedMrePayload();

    socket.emit('start_simulation', {
        feedstock: feedstock,
        mass_kg: mass_kg,
        backend: document.getElementById('engine-select').value,
        track: track,
        speed: speedMs / 1000.0,  // Convert ms to seconds for backend
        c4_max_temp_C: parseFloat(document.getElementById('c4-max-temp')?.value) || 1670,
        c5_enabled: mrePayload.c5_enabled,
        mre_target_species: mrePayload.mre_target_species,
        mre_max_voltage_V: mrePayload.mre_max_voltage_V,
        runtime_campaign_overrides: buildRuntimeCampaignOverrides(),
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

    const emptyHint = document.getElementById('empty-hint');
    if (emptyHint) emptyHint.style.display = 'none';

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
    if (e.target.classList.contains('recipe-lever')) {
        const rawValue = e.target.value;
        const value = parseFloat(rawValue);
        if (!Number.isFinite(value)) return;
        if (e.target.dataset.param) {
            socket.emit('adjust_parameter', {param: e.target.dataset.param, value: value});
        }
        if (e.target.dataset.field) {
            socket.emit('adjust_parameter', {
                param: 'campaign_override',
                campaign: selectedLeverCampaign(),
                field: e.target.dataset.field,
                value: value,
            });
        }
        updateLeverWarning();
    }
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
