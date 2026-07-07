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

function formatFurnaceTemperature(value) {
    const numberValue = Number(value);
    if (!Number.isFinite(numberValue)) return 'n/a';
    return Number.isInteger(numberValue)
        ? String(numberValue)
        : String(numberValue);
}

function furnaceMaterialOptionText(material) {
    const name = material.display_name || material.id || 'material';
    const rating = material.service_rating_T_C ?? material.max_service_T_C;
    const service = formatFurnaceTemperature(rating);
    const applied = formatFurnaceTemperature(
        material.effective_applied_ceiling_T_C
    );
    const groundingTier = material.grounding?.tier || '';
    const capLabel = groundingTier === 'proxy-sintering'
        ? `${service} C proxy cap (sintering-based, uncertified)`
        : `service ${service} C`;
    return `${name} (${capLabel}; applied ${applied} C)`;
}

function optionForFurnaceMaterial(material) {
    const option = document.createElement('option');
    const rating = material.service_rating_T_C ?? material.max_service_T_C;
    const ratingText = formatFurnaceTemperature(rating);
    const appliedText = formatFurnaceTemperature(
        material.effective_applied_ceiling_T_C
    );
    const groundingTier = material.grounding?.tier || '';
    const ratingTitle = groundingTier === 'proxy-sintering'
        ? `Proxy cap: ${ratingText} C (sintering-based, uncertified)`
        : `Service rating: ${ratingText} C`;
    option.value = material.id;
    option.textContent = furnaceMaterialOptionText(material);
    option.title = `${ratingTitle}; effective applied ceiling: ${appliedText} C`;
    option.dataset.serviceRatingTC = String(material.service_rating_T_C ?? '');
    option.dataset.effectiveAppliedCeilingTC = String(
        material.effective_applied_ceiling_T_C ?? ''
    );
    return option;
}

function hydrateFurnaceMaterialCatalog() {
    const select = document.getElementById('furnace-material');
    if (!select || !select.dataset.catalogUrl) return;
    const defaultOption = select.querySelector('option[value=""]') || new Option('Default (1800 C ceiling)', '');
    fetch(select.dataset.catalogUrl)
        .then(r => r.ok ? r.json() : Promise.reject(new Error('catalog unavailable')))
        .then(data => {
            if (!Array.isArray(data.materials)) return;
            select.replaceChildren(defaultOption, ...data.materials.map(optionForFurnaceMaterial));
            select.value = '';
        })
        .catch(() => {
            select.value = '';
        });
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

function selectedFurnaceMaterialId() {
    return (document.getElementById('furnace-material')?.value || '').trim();
}

function selectedC4MaxTempC() {
    const input = document.getElementById('c4-max-temp');
    const selected = parseFloat(input?.value);
    if (Number.isFinite(selected)) return selected;
    const renderedDefault = parseFloat(
        input?.defaultValue || input?.getAttribute('value') || ''
    );
    return Number.isFinite(renderedDefault) ? renderedDefault : undefined;
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

let loadedRecipePatch = null;
let recipeChoices = [];
let lastRecipeTick = null;
let lastRecipeSummary = null;

function clearLoadedRecipeForManualEdit() {
    if (!loadedRecipePatch) return;
    loadedRecipePatch = null;
    setStatusText('Recipe edited from loaded controls');
}

function handleRecipeDefiningControlEdit(e) {
    if (e.target?.closest?.('.recipe-defining-control')) {
        clearLoadedRecipeForManualEdit();
    }
}

socket.on('simulation_tick', (data) => {
    lastRecipeTick = data || null;
});

socket.on('per_hour_summary', (data) => {
    lastRecipeSummary = data || null;
    renderRedoxSummary(lastRecipeSummary);
});

const REDOX_EMPTY = '—';

function objectOrNull(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}

function finiteNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

function formatRedoxNumber(value, digits = 3) {
    const number = finiteNumber(value);
    if (number === null) return REDOX_EMPTY;
    const abs = Math.abs(number);
    if ((abs > 0 && abs < 0.001) || abs >= 10000) {
        return number.toExponential(2);
    }
    return number.toFixed(digits).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
}

function formatRedoxUnit(value, unit, digits = 3) {
    const formatted = formatRedoxNumber(value, digits);
    return formatted === REDOX_EMPTY ? REDOX_EMPTY : `${formatted} ${unit}`;
}

function textOrDash(value) {
    return value === undefined || value === null || value === '' ? REDOX_EMPTY : String(value);
}

function setRedoxText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function nativeFeEventStatus(event) {
    const payload = objectOrNull(event);
    if (!payload) return REDOX_EMPTY;
    return textOrDash(payload.native_fe_event_status ?? payload.native_fe_event);
}

function nativeFePartitionText(partition) {
    const payload = objectOrNull(partition);
    if (!payload) return REDOX_EMPTY;
    const parts = [
        ['native_fe_pool_mol', 'pool'],
        ['native_fe_tap_mol', 'tap'],
        ['native_fe_vapor_mol', 'vapor'],
    ].flatMap(([key, label]) => (
        finiteNumber(payload[key]) === null ? [] : [`${label} ${formatRedoxNumber(payload[key])} mol`]
    ));
    return parts.length ? parts.join(' / ') : REDOX_EMPTY;
}

function redoxSourceLabels(summary) {
    // Render APPLIED source terms plainly and SKIPPED ones with an explicit
    // tag — the total terms dict includes skipped terms, and showing them
    // unlabelled reads as if they happened (M3-L3 P2).
    const breakdown = objectOrNull(summary?.redox_source_breakdown);
    if (!breakdown) return REDOX_EMPTY;
    const applied = objectOrNull(breakdown.applied_terms_mol_o2_equiv_by_label) || {};
    const skipped = objectOrNull(breakdown.skipped_terms_mol_o2_equiv_by_label) || {};
    const parts = [];
    const pushEntries = (terms, suffix) => {
        Object.entries(terms)
            .filter(([, value]) => finiteNumber(value) !== null)
            .sort(([left], [right]) => left.localeCompare(right))
            .forEach(([label, value]) => {
                parts.push(`${label}: ${formatRedoxNumber(value)} mol O2e${suffix}`);
            });
    };
    pushEntries(applied, '');
    pushEntries(skipped, ' (skipped)');
    if (!parts.length) return REDOX_EMPTY;
    return parts.join(', ');
}

function renderRedoxSummary(summary) {
    const redox = objectOrNull(summary?.fe_redox_split) || {};
    const stage3 = objectOrNull(summary?.stage_3_capture) || {};

    setRedoxText('redox-fo2-log', formatRedoxNumber(redox.fO2_log));
    setRedoxText('redox-ferric-frac', formatRedoxNumber(redox.ferric_frac, 4));
    setRedoxText('redox-native-fe-frac', formatRedoxNumber(redox.native_fe_frac, 4));
    setRedoxText(
        'redox-native-fe-status',
        nativeFeEventStatus(redox.native_fe_saturation_event),
    );
    setRedoxText(
        'redox-native-fe-partition',
        nativeFePartitionText(redox.native_fe_partition),
    );
    setRedoxText('redox-stage3-fe', formatRedoxUnit(stage3.Fe_kg, 'kg'));
    setRedoxText('redox-stage3-total', formatRedoxUnit(stage3.total_kg, 'kg'));
    setRedoxText('redox-stage3-fe-wt', formatRedoxUnit(stage3.Fe_wt_pct, 'wt%'));
    setRedoxText('redox-source-labels', redoxSourceLabels(summary));
}

function setRecipeError(id, message) {
    const el = document.getElementById(id);
    if (!el) return;
    el.hidden = !message;
    el.textContent = message || '';
}

function showRecipeModal(id) {
    const el = document.getElementById(id);
    if (el) el.hidden = false;
}

function hideRecipeModal(id) {
    const el = document.getElementById(id);
    if (el) el.hidden = true;
}

function selectedSocketId() {
    return socket && socket.id ? socket.id : '';
}

function recipePreviewText() {
    const overrides = buildRuntimeCampaignOverrides();
    const campaign = selectedLeverCampaign();
    const mre = selectedMrePayload();
    const tick = lastRecipeTick || {};
    const summary = lastRecipeSummary || {};
    const redox = objectOrNull(summary.fe_redox_split) || {};
    const stage3 = objectOrNull(summary.stage_3_capture) || {};
    return [
        `feedstock: ${document.getElementById('feedstock-select')?.value || 'not selected'}`,
        `campaign: ${campaign}`,
        `temperature ladder: ${JSON.stringify(overrides[campaign] || {})}`,
        `pO2_mbar: ${tick.pO2_mbar ?? document.getElementById('lever-po2-mbar')?.value ?? 'not captured'}`,
        `p_total_mbar: ${tick.p_total_mbar ?? document.getElementById('lever-pn2-mbar')?.value ?? 'not captured'}`,
        `furnace_max_T_C: ${selectedC4MaxTempC() ?? 'not captured'}`,
        `mre_enabled: ${mre.c5_enabled ? 'true' : 'false'}`,
        `oxygen_kg: ${tick.oxygen_kg ?? 'not captured'}`,
        `energy_electrical_plus_evaporation_kWh: ${tick.energy_electrical_plus_evaporation_cumulative_kWh ?? tick.energy_electrical_plus_evaporation_kWh ?? 'not captured'}`,
        `energy_scope: ${tick.energy_scope ?? 'not captured'}`,
        `furnace_heat_status: ${tick.furnace_heat_status ?? 'not captured'}`,
        `mass_balance_error_pct: ${tick.mass_balance_error_pct ?? 'not captured'}`,
        `wall_deposit_kg: ${summary.wall_deposit_cumulative_kg ? '[captured]' : 'not captured'}`,
        `melt_fO2_log: ${formatRedoxNumber(redox.fO2_log)}`,
        `ferric_frac: ${formatRedoxNumber(redox.ferric_frac, 4)}`,
        `native_fe_frac: ${formatRedoxNumber(redox.native_fe_frac, 4)}`,
        `native_fe_status: ${nativeFeEventStatus(redox.native_fe_saturation_event)}`,
        `stage_3_Fe_kg: ${formatRedoxNumber(stage3.Fe_kg)}`,
        `stage_3_total_kg: ${formatRedoxNumber(stage3.total_kg)}`,
        `redox_source_labels: ${redoxSourceLabels(summary)}`,
    ].join('\n');
}

function updateSaveRecipePreview() {
    const preview = document.getElementById('recipe-save-preview');
    if (preview) preview.textContent = recipePreviewText();
}

function setStatusText(message) {
    const statusEl = document.getElementById('status-text');
    if (statusEl) statusEl.textContent = message;
}

function recipeFetchJson(url, options) {
    return fetch(url, options).then(response => (
        response.json().catch(() => ({})).then(payload => {
            if (!response.ok) {
                throw new Error(payload.error || `request failed: ${response.status}`);
            }
            return payload;
        })
    ));
}

function populateRecipeSelect(recipes) {
    recipeChoices = Array.isArray(recipes) ? recipes : [];
    const select = document.getElementById('recipe-load-select');
    if (!select) return;
    select.replaceChildren();
    for (const recipe of recipeChoices) {
        const option = document.createElement('option');
        option.value = recipe.name || '';
        option.textContent = recipe.title || recipe.name || '';
        select.appendChild(option);
    }
    updateSelectedRecipeSummary();
}

function updateSelectedRecipeSummary() {
    const select = document.getElementById('recipe-load-select');
    const titleEl = document.getElementById('recipe-load-selected-title');
    const summaryEl = document.getElementById('recipe-load-selected-summary');
    const selected = recipeChoices.find(recipe => recipe.name === select?.value);
    if (titleEl) titleEl.textContent = selected ? selected.title || selected.name || '' : '';
    if (summaryEl) summaryEl.textContent = selected ? selected.summary || '' : '';
    const loadButton = document.getElementById('recipe-load-confirm');
    if (loadButton) loadButton.disabled = !selected;
}

function loadRecipeList() {
    setRecipeError('recipe-load-error', '');
    return recipeFetchJson('/recipes')
        .then(populateRecipeSelect)
        .catch(error => setRecipeError('recipe-load-error', error.message));
}

function applyLoadedRecipeControls(controls) {
    if (!controls || typeof controls !== 'object') return;
    const setValue = (id, value) => {
        const el = document.getElementById(id);
        if (el && value !== undefined && value !== null) el.value = String(value);
    };
    setValue('lever-campaign', controls.lever_campaign);
    setValue('lever-po2-mbar', controls.pO2_mbar);
    setValue('lever-pn2-mbar', controls.p_total_mbar);
    setValue('lever-stage-temp', controls.stage_temp_C);
    setValue('c4-max-temp', controls.c4_max_temp_C ?? controls.furnace_max_T_C);
    if (controls.mre_enabled !== undefined) {
        const mreEnabled = document.getElementById('mre-enabled');
        if (mreEnabled) mreEnabled.checked = controls.mre_enabled === true;
        updateMreFields();
    }
    updateKnudsenIndicator();
    updateLeverWarning();
}

function numericDataAttribute(element, name) {
    const value = parseFloat(element?.getAttribute(`data-${name}`) || '');
    return Number.isFinite(value) ? value : null;
}

function knudsenDisplayConfig(indicator) {
    const pressureBandMinMbar = numericDataAttribute(indicator, 'default-pressure-band-min-mbar');
    const pressureBandMaxMbar = numericDataAttribute(indicator, 'default-pressure-band-max-mbar');
    const config = {
        boltzmannConstantJK: numericDataAttribute(indicator, 'boltzmann-constant-j-k'),
        characteristicLengthM: numericDataAttribute(indicator, 'characteristic-length-m'),
        n2CollisionDiameterM: numericDataAttribute(indicator, 'n2-collision-diameter-m'),
        continuumBufferKn: numericDataAttribute(indicator, 'continuum-buffer-kn'),
        meanFreePathFormulaId: indicator?.getAttribute('data-mean-free-path-formula-id') || '',
        meanFreePathDenominatorFactor: numericDataAttribute(
            indicator,
            'mean-free-path-denominator-factor',
        ),
        temperatureKOffset: numericDataAttribute(indicator, 'temperature-k-offset'),
        pressurePaPerMbar: numericDataAttribute(indicator, 'pressure-pa-per-mbar'),
        defaultPressureBand: {
            role: indicator?.getAttribute('data-default-pressure-band-role') || '',
            minMbar: pressureBandMinMbar,
            maxMbar: pressureBandMaxMbar,
            label: indicator?.getAttribute('data-default-pressure-band-label') || '',
            warningMessage: (
                indicator?.getAttribute('data-default-pressure-band-warning-message') || ''
            ),
        },
    };
    if (
        config.boltzmannConstantJK === null
        || config.characteristicLengthM === null
        || config.characteristicLengthM <= 0
        || config.n2CollisionDiameterM === null
        || config.n2CollisionDiameterM <= 0
        || config.continuumBufferKn === null
        || config.continuumBufferKn <= 0
        || !config.meanFreePathFormulaId
        || config.meanFreePathDenominatorFactor === null
        || config.meanFreePathDenominatorFactor <= 0
        || config.temperatureKOffset === null
        || config.pressurePaPerMbar === null
        || config.pressurePaPerMbar <= 0
        || config.defaultPressureBand.role !== 'default'
        || config.defaultPressureBand.minMbar === null
        || config.defaultPressureBand.maxMbar === null
        || config.defaultPressureBand.minMbar > config.defaultPressureBand.maxMbar
        || !config.defaultPressureBand.label
        || !config.defaultPressureBand.warningMessage
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
    const pressurePa = pressureMbar * config.pressurePaPerMbar;
    const meanFreePathM = config.boltzmannConstantJK
        * (tempC + config.temperatureKOffset)
        / (
            config.meanFreePathDenominatorFactor
            * config.n2CollisionDiameterM ** 2
            * pressurePa
        );
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
    const band = knudsenDisplayConfig(
        document.getElementById('knudsen-indicator'),
    )?.defaultPressureBand;
    if (
        band
        && Number.isFinite(pressureMbar)
        && (pressureMbar < band.minMbar || pressureMbar > band.maxMbar)
    ) {
        messages.push(band.warningMessage);
    }
    if (Number.isFinite(tempC) && (tempC < 20 || tempC > 1900)) {
        messages.push('stage temperature outside characterized operator band');
    }
    warning.hidden = messages.length === 0;
    warning.textContent = messages.join('; ');
}

hydrateMrePresetCatalog();
hydrateFurnaceMaterialCatalog();
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

document.getElementById('btn-save-recipe')?.addEventListener('click', () => {
    setRecipeError('recipe-save-error', '');
    updateSaveRecipePreview();
    showRecipeModal('save-recipe-modal');
    document.getElementById('recipe-save-title')?.focus();
});

document.getElementById('recipe-save-cancel')?.addEventListener('click', () => {
    hideRecipeModal('save-recipe-modal');
});

document.getElementById('recipe-save-confirm')?.addEventListener('click', () => {
    const title = document.getElementById('recipe-save-title')?.value || '';
    const sid = selectedSocketId();
    setRecipeError('recipe-save-error', '');
    recipeFetchJson('/recipes/save', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({title, sid}),
    }).then(data => {
        hideRecipeModal('save-recipe-modal');
        setStatusText(`Saved recipe: ${data.title || data.name}`);
    }).catch(error => setRecipeError('recipe-save-error', error.message));
});

document.getElementById('btn-load-recipe')?.addEventListener('click', () => {
    setRecipeError('recipe-load-error', '');
    showRecipeModal('load-recipe-modal');
    loadRecipeList();
});

document.getElementById('recipe-load-cancel')?.addEventListener('click', () => {
    hideRecipeModal('load-recipe-modal');
});

document.getElementById('recipe-load-select')?.addEventListener('change', updateSelectedRecipeSummary);

document.getElementById('recipe-load-confirm')?.addEventListener('click', () => {
    const name = document.getElementById('recipe-load-select')?.value || '';
    const sid = selectedSocketId();
    setRecipeError('recipe-load-error', '');
    recipeFetchJson('/recipes/load', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, sid}),
    }).then(data => {
        loadedRecipePatch = data.setpoints_patch || null;
        applyLoadedRecipeControls(data.controls || {});
        hideRecipeModal('load-recipe-modal');
        setStatusText(`Loaded recipe: ${data.title || data.name}`);
    }).catch(error => setRecipeError('recipe-load-error', error.message));
});

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
    const furnaceMaterialId = selectedFurnaceMaterialId();

    const payload = {
        feedstock: feedstock,
        mass_kg: mass_kg,
        backend: document.getElementById('engine-select').value,
        track: track,
        speed: speedMs / 1000.0,  // Convert ms to seconds for backend
        c4_max_temp_C: selectedC4MaxTempC(),
        c5_enabled: mrePayload.c5_enabled,
        mre_target_species: mrePayload.mre_target_species,
        mre_max_voltage_V: mrePayload.mre_max_voltage_V,
        additives: {
            Na: parseFloat(document.getElementById('add-na').value) || 0,
            K: parseFloat(document.getElementById('add-k').value) || 0,
            Mg: parseFloat(document.getElementById('add-mg').value) || 0,
            Ca: parseFloat(document.getElementById('add-ca').value) || 0,
            C: parseFloat(document.getElementById('add-c').value) || 0,
        },
    };
    if (loadedRecipePatch) {
        payload.setpoints_patch = loadedRecipePatch;
    } else {
        payload.runtime_campaign_overrides = buildRuntimeCampaignOverrides();
    }
    if (furnaceMaterialId) payload.furnace_material_id = furnaceMaterialId;
    socket.emit('start_simulation', payload);

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
document.addEventListener('input', handleRecipeDefiningControlEdit);
document.addEventListener('change', (e) => {
    handleRecipeDefiningControlEdit(e);

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
        [
            'Electrical + known evaporation enthalpy (partial)',
            summary.energy_electrical_plus_evaporation_kWh.toFixed(1) + ' kWh',
        ],
        ['Energy scope', summary.energy_scope],
        [
            'Furnace heat status',
            summary.furnace_heat_status
                + '; feed sensible, fusion, radiation, full furnace heat omitted',
        ],
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
