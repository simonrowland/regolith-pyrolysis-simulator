/**
 * Diagnostic advisory panel renderers.
 */

function advisorySetText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function advisoryClear(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
}

function advisoryBadge(text, className) {
    const span = document.createElement('span');
    span.className = 'advisory-badge' + (className ? ' ' + className : '');
    span.textContent = text;
    return span;
}

function advisoryNumber(value) {
    return Number.isFinite(Number(value)) ? Number(value).toFixed(0) : 'n/a';
}

function updateAdvisoryState(id, status) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = status || 'n/a';
    el.className = 'advisory-state advisory-state-' + (status || 'n/a');
}

function renderWallRiskPanel(payload) {
    const content = document.getElementById('wall-risk-content');
    if (!content) return;
    const status = payload && payload.status ? payload.status : 'n/a';
    updateAdvisoryState('wall-risk-state', status);
    let metaText = 'Active vapors: ' + (
        payload && payload.active_species && payload.active_species.length
            ? payload.active_species.join(', ')
            : 'n/a'
    );
    const op = payload && payload.operating_point;
    if (op) {
        const po2 = op.pO2_mbar === null || op.pO2_mbar === undefined ? 'n/a' : op.pO2_mbar;
        const buffer = op.p_buffer_mbar === null || op.p_buffer_mbar === undefined ? 'n/a' : op.p_buffer_mbar;
        metaText += ' | Operating point: pO2 ' + po2 + ' mbar (' + (op.po2_regime || 'n/a') + '), '
            + 'buffer ' + buffer + ' mbar (' + (op.pressure_regime || 'n/a') + ')';
    }
    advisorySetText('wall-risk-meta', metaText);
    advisoryClear(content);
    if (!payload || status === 'n/a' || !payload.zones || !payload.zones.length) {
        content.className = 'advisory-empty';
        content.textContent = 'n/a';
        return;
    }
    content.className = 'advisory-zone-list';
    for (const zone of payload.zones) {
        const details = document.createElement('details');
        details.className = 'advisory-zone';
        if (content.childElementCount === 0) details.open = true;
        const summary = document.createElement('summary');
        summary.textContent = `${zone.label} wall zone - ${advisoryNumber(zone.temperature_C)} C`;
        details.appendChild(summary);

        const tableWrap = document.createElement('div');
        tableWrap.className = 'advisory-table-wrap';
        const table = document.createElement('table');
        table.className = 'advisory-table';
        const thead = document.createElement('thead');
        const headRow = document.createElement('tr');
        for (const label of ['Material', 'Temp', 'Rollup', 'Active species']) {
            const th = document.createElement('th');
            th.textContent = label;
            headRow.appendChild(th);
        }
        thead.appendChild(headRow);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        for (const material of zone.materials || []) {
            const tr = document.createElement('tr');
            if (!material.temp_ok) tr.className = 'advisory-muted';
            const materialCell = document.createElement('td');
            materialCell.textContent = material.label || 'n/a';
            tr.appendChild(materialCell);

            const tempCell = document.createElement('td');
            tempCell.appendChild(advisoryBadge(
                material.temp_verdict || 'n/a',
                'advisory-temp-' + (material.temp_verdict || 'n/a')
            ));
            const limit = document.createElement('span');
            limit.className = 'advisory-note';
            limit.textContent = 'limit ' + advisoryNumber(material.limiting_temperature_C) + ' C';
            tempCell.appendChild(limit);
            tr.appendChild(tempCell);

            const rollupCell = document.createElement('td');
            rollupCell.appendChild(advisoryBadge(
                material.rollup || 'n/a',
                'advisory-rollup-' + (material.rollup || 'n/a')
            ));
            tr.appendChild(rollupCell);

            const speciesCell = document.createElement('td');
            for (const species of material.species || []) {
                const row = document.createElement('div');
                row.className = 'advisory-species-row';
                const label = document.createElement('strong');
                label.textContent = species.species || 'n/a';
                row.appendChild(label);
                appendWallCell(row, 'attack', species.chemical_attack);
                appendWallCell(row, 'stick', species.stickiness);
                if (species.stickiness && species.stickiness.verdict_eligible === false) {
                    const provenance = document.createElement('span');
                    provenance.className = 'advisory-note';
                    provenance.textContent = 'provenance-only ('
                        + (species.stickiness.regime || 'n/a')
                        + ' analog); does not drive verdict';
                    row.appendChild(provenance);
                }
                appendReactiveCell(row, species.reactive);
                speciesCell.appendChild(row);
                if (species.chemical_attack && species.chemical_attack.note) {
                    const attackNote = document.createElement('div');
                    attackNote.className = 'advisory-note advisory-attack-note';
                    attackNote.textContent = species.chemical_attack.note;
                    speciesCell.appendChild(attackNote);
                }
            }
            tr.appendChild(speciesCell);
            tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        tableWrap.appendChild(table);
        details.appendChild(tableWrap);
        content.appendChild(details);
    }
}

function appendWallCell(parent, label, cell) {
    const display = cell && cell.display ? cell.display : 'uncharacterized';
    const badgeClass = cell && cell.uncharacterized ? 'advisory-uncharacterized' : '';
    parent.appendChild(advisoryBadge(label + ' ' + display, badgeClass));
    const evidence = document.createElement('span');
    evidence.className = 'advisory-evidence';
    evidence.textContent = cell && cell.evidence ? cell.evidence : 'uncharacterized';
    parent.appendChild(evidence);
}

function appendReactiveCell(parent, reactive) {
    const verdict = reactive && reactive.verdict ? reactive.verdict : 'uncharacterized';
    let text = 'reactive ' + verdict;
    if (reactive && reactive.matched && reactive.regime) {
        text += ' (' + reactive.regime + ')';
    }
    const classes = ['advisory-reactive-' + verdict];
    if (verdict === 'uncharacterized') classes.push('advisory-uncharacterized');
    parent.appendChild(advisoryBadge(text, classes.join(' ')));
    if (reactive && reactive.matched && reactive.product_phase) {
        const product = document.createElement('span');
        product.className = 'advisory-note';
        product.textContent = 'product: ' + reactive.product_phase;
        parent.appendChild(product);
    }
    if (verdict === 'uncharacterized') {
        const needs = document.createElement('span');
        needs.className = 'advisory-note';
        needs.textContent = 'needs experiment';
        parent.appendChild(needs);
    }
}

function renderCeramicRumpPanel(payload) {
    const content = document.getElementById('ceramic-rump-content');
    if (!content) return;
    const status = payload && payload.status ? payload.status : 'n/a';
    updateAdvisoryState('ceramic-rump-state', status);
    advisoryClear(content);
    if (!payload || status === 'n/a') {
        content.className = 'advisory-empty';
        content.textContent = 'n/a';
        return;
    }
    if (status === 'no-match' || status === 'ambiguous' || !payload.match) {
        content.className = 'advisory-empty';
        content.textContent = status + ' - ' + (payload.reason || '');
        return;
    }

    content.className = 'advisory-result';
    const title = document.createElement('div');
    title.className = 'advisory-result-title';
    title.textContent = payload.match.label || 'n/a';
    content.appendChild(title);
    appendCeramicLine(content, 'Composition', payload.match.composition_kind || 'n/a');
    appendCeramicLine(
        content,
        'Service',
        payload.match.service_temp ? payload.match.service_temp.display : 'n/a'
    );
    appendCeramicLine(
        content,
        'Service kind',
        payload.match.service_temp ? payload.match.service_temp.kind : 'n/a'
    );
    appendCeramicLine(
        content,
        'Liner verdict',
        payload.match.liner_suitability ? payload.match.liner_suitability.verdict : 'n/a'
    );
}

function appendCeramicLine(parent, label, value) {
    const line = document.createElement('div');
    line.textContent = label + ': ' + value;
    parent.appendChild(line);
}

function advisoryObject(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}

function advisoryEntries(value) {
    const payload = advisoryObject(value);
    if (!payload) return [];
    return Object.entries(payload).sort(([left], [right]) => left.localeCompare(right));
}

function advisoryPrettyKey(key) {
    return String(key).replace(/_/g, ' ');
}

function advisoryFormatValue(value, unit) {
    if (typeof value === 'boolean') return String(value);
    const number = Number(value);
    if (Number.isFinite(number)) {
        const abs = Math.abs(number);
        const formatted = abs > 0 && (abs < 0.001 || abs >= 10000)
            ? number.toExponential(2)
            : number.toFixed(3).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
        return unit ? `${formatted} ${unit}` : formatted;
    }
    if (value === null || value === undefined || value === '') return 'n/a';
    if (Array.isArray(value)) return value.length ? value.join(', ') : 'none';
    return String(value);
}

function advisoryNestedValue(value, unit) {
    const payload = advisoryObject(value);
    if (!payload) return advisoryFormatValue(value, unit);
    const parts = advisoryEntries(payload).map(([key, nested]) => {
        const nestedPayload = advisoryObject(nested);
        if (nestedPayload) {
            const inner = advisoryEntries(nestedPayload)
                .map(([innerKey, innerValue]) => (
                    `${advisoryPrettyKey(innerKey)} ${advisoryFormatValue(innerValue, unit)}`
                ))
                .join(', ');
            return `${advisoryPrettyKey(key)} (${inner || 'n/a'})`;
        }
        return `${advisoryPrettyKey(key)} ${advisoryFormatValue(nested, unit)}`;
    });
    return parts.length ? parts.join('; ') : 'n/a';
}

function appendAdvisorySection(parent, title, mapping, unit) {
    const entries = advisoryEntries(mapping);
    if (!entries.length) return false;
    const heading = document.createElement('div');
    heading.className = 'advisory-result-title';
    heading.textContent = title;
    parent.appendChild(heading);
    for (const [key, value] of entries) {
        appendCeramicLine(parent, advisoryPrettyKey(key), advisoryNestedValue(value, unit));
    }
    return true;
}

function setAdvisoryEmpty(content, stateId) {
    if (stateId) updateAdvisoryState(stateId, 'n/a');
    advisoryClear(content);
    content.className = 'advisory-empty';
    content.textContent = 'n/a';
}

function renderProductLedgerPanel(payload) {
    const content = document.getElementById('product-ledger-content');
    if (!content) return;
    const data = advisoryObject(payload);
    if (!data) {
        setAdvisoryEmpty(content, 'product-ledger-state');
        return;
    }

    advisoryClear(content);
    content.className = 'advisory-result';
    let sections = 0;
    if (appendAdvisorySection(content, 'Products', data.products, 'kg')) sections += 1;

    const oxygen = {};
    for (const key of ['oxygen_kg', 'oxygen_stored_kg', 'oxygen_vented_kg']) {
        if (data[key] !== undefined && data[key] !== null) oxygen[key] = data[key];
    }
    if (appendAdvisorySection(content, 'Oxygen', oxygen, 'kg')) sections += 1;

    const mass = {};
    for (const key of ['mass_in_kg', 'mass_out_kg', 'terminal_slag_kg', 'terminal_rump_kg']) {
        if (data[key] !== undefined && data[key] !== null) mass[key] = data[key];
    }
    if (appendAdvisorySection(content, 'Mass ledger', mass, 'kg')) sections += 1;

    if (appendAdvisorySection(content, 'Terminal rump by class', data.terminal_rump_by_class, 'kg')) sections += 1;
    if (appendAdvisorySection(content, 'Terminal rump by species', data.terminal_rump_by_species, 'kg')) sections += 1;
    if (appendAdvisorySection(content, 'Residual inventory', data.residual_inventory_kg, 'kg')) sections += 1;
    if (appendAdvisorySection(content, 'Terminal residual buckets', data.terminal_residual_buckets, 'kg')) sections += 1;

    const spent = advisoryObject(data.process_inventory_spent_reductant);
    if (spent) {
        const spentRows = {};
        if (spent.class_total_kg !== undefined) spentRows.class_total_kg = spent.class_total_kg;
        if (spent.account) spentRows.account = spent.account;
        if (spent.disposition) spentRows.disposition = spent.disposition;
        if (spent.kg_by_species) spentRows.kg_by_species = spent.kg_by_species;
        if (appendAdvisorySection(content, 'Spent reductant residue', spentRows, 'kg')) sections += 1;
    }

    if (!sections) {
        setAdvisoryEmpty(content, 'product-ledger-state');
        return;
    }
    updateAdvisoryState('product-ledger-state', 'ok');
}

function renderOverlapEvaporationPanel(payload) {
    const content = document.getElementById('overlap-evaporation-content');
    if (!content) return;
    const data = advisoryObject(payload);
    if (!data || !Object.keys(data).length) {
        setAdvisoryEmpty(content, 'overlap-evaporation-state');
        return;
    }

    advisoryClear(content);
    content.className = 'advisory-result';
    appendCeramicLine(content, 'Campaign', data.campaign || 'n/a');
    appendCeramicLine(content, 'Campaign hour', advisoryFormatValue(data.campaign_hour));
    appendCeramicLine(content, 'Temperature', advisoryFormatValue(data.temperature_C, 'C'));
    appendCeramicLine(content, 'Completion targets', advisoryFormatValue(data.completion_target_species));
    appendCeramicLine(content, 'Endpoint watch', advisoryFormatValue(data.endpoint_species_monitored));
    appendCeramicLine(content, 'Off-target total', advisoryFormatValue(data.off_target_total_kg_hr, 'kg/hr'));

    const offTarget = advisoryEntries(data.off_target_evaporation);
    if (!offTarget.length) {
        appendCeramicLine(content, 'Off-target species', 'none');
        updateAdvisoryState('overlap-evaporation-state', 'ok');
        return;
    }
    const heading = document.createElement('div');
    heading.className = 'advisory-result-title';
    heading.textContent = 'Off-target species';
    content.appendChild(heading);
    for (const [species, row] of offTarget) {
        const detail = advisoryObject(row) || {};
        appendCeramicLine(
            content,
            species,
            [
                `rate ${advisoryFormatValue(detail.rate_kg_hr, 'kg/hr')}`,
                `stage ${advisoryFormatValue(detail.designated_stage_number)}`,
                `future targets ${advisoryFormatValue(detail.future_campaign_stage_targets)}`,
                `endpoint watch ${advisoryFormatValue(detail.listed_in_endpoint_watch)}`,
                `gates completion ${advisoryFormatValue(detail.gates_completion)}`,
            ].join('; '),
        );
    }
    updateAdvisoryState('overlap-evaporation-state', 'warning');
}

function renderKnudsenRegimePanelFromDiagnostic(diagnostic, titleText) {
    const content = document.getElementById('knudsen-regime-content');
    if (!content) return;
    const data = advisoryObject(diagnostic);
    if (!data || !Object.keys(data).length) {
        setAdvisoryEmpty(content, 'knudsen-regime-state');
        return;
    }

    advisoryClear(content);
    content.className = 'advisory-result';
    const title = document.createElement('div');
    title.className = 'advisory-result-title';
    title.textContent = titleText || 'Knudsen diagnostic';
    content.appendChild(title);
    appendCeramicLine(content, 'Status', data.status || 'n/a');
    appendCeramicLine(content, 'Regime', data.regime || 'n/a');
    appendCeramicLine(content, 'Kn', advisoryFormatValue(data.knudsen_number));
    appendCeramicLine(content, 'Mean free path', advisoryFormatValue(data.mean_free_path_m, 'm'));
    appendCeramicLine(content, 'Pressure', advisoryFormatValue(data.overhead_pressure_mbar, 'mbar'));
    appendCeramicLine(content, 'Gas temperature', advisoryFormatValue(data.gas_temperature_C, 'C'));
    appendCeramicLine(content, 'Carrier gas', data.carrier_gas || 'n/a');
    if (data.reason) appendCeramicLine(content, 'Reason', data.reason);

    const segments = Array.isArray(data.segments) ? data.segments : [];
    if (segments.length) {
        const heading = document.createElement('div');
        heading.className = 'advisory-result-title';
        heading.textContent = 'Segments';
        content.appendChild(heading);
        for (const segment of segments) {
            const row = advisoryObject(segment) || {};
            appendCeramicLine(
                content,
                row.name || 'segment',
                [
                    `Kn ${advisoryFormatValue(row.knudsen_number)}`,
                    `regime ${row.regime || 'n/a'}`,
                    `L ${advisoryFormatValue(row.characteristic_length_m, 'm')}`,
                    `factor ${advisoryFormatValue(row.regime_factor)}`,
                ].join('; '),
            );
        }
    }
    if (Array.isArray(data.warnings) && data.warnings.length) {
        appendCeramicLine(content, 'Warnings', data.warnings.join('; '));
    }
    updateAdvisoryState('knudsen-regime-state', data.status || data.regime || 'ok');
}

function renderKnudsenRegimePanelFromPerHour(summary) {
    const content = document.getElementById('knudsen-regime-content');
    if (!content) return;
    const data = advisoryObject(summary);
    if (!data || (data.Kn === undefined && !data.regime)) {
        setAdvisoryEmpty(content, 'knudsen-regime-state');
        return;
    }
    advisoryClear(content);
    content.className = 'advisory-result';
    const title = document.createElement('div');
    title.className = 'advisory-result-title';
    title.textContent = 'Per-hour transport';
    content.appendChild(title);
    appendCeramicLine(content, 'Hour', advisoryFormatValue(data.hour));
    appendCeramicLine(content, 'Campaign', data.campaign || 'n/a');
    appendCeramicLine(content, 'Kn', advisoryFormatValue(data.Kn));
    appendCeramicLine(content, 'Regime', data.regime || 'n/a');
    appendCeramicLine(content, 'Formula', data.transport_formula_id || 'n/a');
    updateAdvisoryState('knudsen-regime-state', data.regime || 'ok');
}

function renderVaporPressureAuthorityPanel(payload) {
    const content = document.getElementById('vapor-pressure-authority-content');
    if (!content) return;
    const status = payload && payload.status ? payload.status : 'n/a';
    updateAdvisoryState('vapor-pressure-authority-state', status);
    advisoryClear(content);
    if (!payload || status === 'n/a') {
        content.className = 'advisory-empty';
        content.textContent = 'n/a';
        return;
    }

    content.className = 'advisory-result';
    const title = document.createElement('div');
    title.className = 'advisory-result-title';
    title.textContent = status;
    content.appendChild(title);
    appendCeramicLine(content, 'Message', payload.message || 'n/a');
    if (payload.reason) appendCeramicLine(content, 'Reason', payload.reason);
    if (payload.fallback_source) {
        appendCeramicLine(content, 'Fallback source', payload.fallback_source);
    }
    if (
        payload.authoritative_for_requested_vapor_pressure !== null
        && payload.authoritative_for_requested_vapor_pressure !== undefined
    ) {
        appendCeramicLine(
            content,
            'Requested vapor authority',
            String(payload.authoritative_for_requested_vapor_pressure)
        );
    }
    appendCeramicLine(content, 'Diagnostic only', String(!!payload.diagnostic_only));
}

function thermalTrainHeadlineMetric(value, unit) {
    const number = Number(value);
    if (!Number.isFinite(number)) return 'n/a';
    const absolute = Math.abs(number);
    const formatted = absolute > 0 && (absolute < 0.01 || absolute >= 1000000)
        ? number.toPrecision(4)
        : number.toLocaleString(undefined, { maximumFractionDigits: 3 });
    return `${formatted} ${unit}`;
}

// Generation token: bumped on run start/error so an in-flight ledger_api
// acknowledgement from a PREVIOUS run cannot repopulate a cleared strip
// (stale-ack race — a completed run's read can land after 'started').
let thermalTrainHeadlineGeneration = 0;

function clearThermalTrainHeadline(label) {
    const panel = document.getElementById('thermal-train-headline');
    if (!panel) return;
    panel.dataset.state = label === 'unavailable' ? 'error' : 'no_data';
    updateAdvisoryState('thermal-train-headline-state', 'n/a');
    advisorySetText('thermal-train-headline-state', label || 'no run');
    advisorySetText('thermal-train-cold-o2', 'n/a');
    advisorySetText('thermal-train-hot-vapor', 'n/a');
    advisorySetText('thermal-train-closes', 'n/a');
    advisorySetText('thermal-train-excluded-species', '');
    advisorySetText('thermal-train-capacity', 'n/a');
    advisorySetText('thermal-train-capacity-basis', 'observed_peak_design_capacity');
    advisorySetText('thermal-train-capex', 'n/a');
}

function renderThermalTrainHeadline(response) {
    const panel = document.getElementById('thermal-train-headline');
    if (!panel) return;
    const report = advisoryObject(response && response.data);
    if (!report || report.status === 'no_data') {
        clearThermalTrainHeadline();
        return;
    }

    const peaks = advisoryObject(report.peaks) || {};
    const capacity = advisoryObject(report.capacity) || {};
    const costs = advisoryObject(report.display_costs) || {};
    const excluded = advisoryObject(report.excluded_species) || {};
    const excludedNames = Object.keys(excluded).sort();

    panel.dataset.state = 'live';
    updateAdvisoryState('thermal-train-headline-state', report.status || 'n/a');
    advisorySetText(
        'thermal-train-cold-o2',
        thermalTrainHeadlineMetric(peaks.cold_o2_kg_hr, 'kg/hr')
    );
    advisorySetText(
        'thermal-train-hot-vapor',
        thermalTrainHeadlineMetric(peaks.hot_total_vapor_kg_hr, 'kg/hr')
    );
    advisorySetText('thermal-train-closes', report.train_closes_for_run ? 'yes' : 'no');
    advisorySetText(
        'thermal-train-excluded-species',
        !report.train_closes_for_run && excludedNames.length
            ? `excluded: ${excludedNames.join(', ')}`
            : ''
    );
    advisorySetText(
        'thermal-train-capacity',
        `${thermalTrainHeadlineMetric(capacity.thermal_train_overflow_kg_hr, 'kg/hr')} overflow vs ${thermalTrainHeadlineMetric(capacity.rated_cold_train_kg_hr, 'kg/hr')} rated`
    );
    advisorySetText(
        'thermal-train-capacity-basis',
        capacity.basis || 'observed_peak_design_capacity'
    );
    advisorySetText(
        'thermal-train-capex',
        thermalTrainHeadlineMetric(costs.amortized_per_campaign_usd, 'USD')
    );
}

function refreshThermalTrainHeadline(isRetry) {
    if (!document.getElementById('thermal-train-headline')) return;
    const generation = thermalTrainHeadlineGeneration;
    socket.emit(
        'ledger_api',
        { resource: 'view', view: 'thermal_train' },
        (response) => {
            if (generation !== thermalTrainHeadlineGeneration) return;
            if (!response || response.error) {
                // "no active simulation" is the typed no-run case, not a
                // transport/view failure — label the two states distinctly.
                const noRun = response
                    && typeof response.error === 'string'
                    && response.error.includes('no active simulation');
                if (noRun) {
                    clearThermalTrainHeadline('no run');
                    return;
                }
                // Transient errors (e.g. "simulation run changed" during the
                // start/replace window) would otherwise paint a sticky
                // 'unavailable' with nothing to repaint it while a run is
                // paused at a gate — retry once before labeling.
                if (!isRetry) {
                    setTimeout(() => {
                        if (generation !== thermalTrainHeadlineGeneration) return;
                        refreshThermalTrainHeadline(true);
                    }, 1500);
                    return;
                }
                clearThermalTrainHeadline('unavailable');
                return;
            }
            renderThermalTrainHeadline(response);
        }
    );
}

socket.on('simulation_tick', (data) => {
    renderWallRiskPanel(data.wall_risk_panel);
    renderVaporPressureAuthorityPanel(data.vapor_pressure_authority_panel);
    renderOverlapEvaporationPanel(data.overlap_evaporation);
});

socket.on('simulation_complete', (data) => {
    renderProductLedgerPanel(data);
    renderCeramicRumpPanel(data.ceramic_rump_panel);
    renderVaporPressureAuthorityPanel(data.vapor_pressure_authority_panel);
    renderKnudsenRegimePanelFromDiagnostic(
        data.knudsen_regime_diagnostic,
        'Completion diagnostic'
    );
    refreshThermalTrainHeadline();
});

socket.on('simulation_status', (data) => {
    if (data && data.status === 'started') {
        thermalTrainHeadlineGeneration += 1;
        clearThermalTrainHeadline();
    }
    if (data && data.knudsen_regime_diagnostic) {
        renderKnudsenRegimePanelFromDiagnostic(
            data.knudsen_regime_diagnostic,
            'Refusal diagnostic'
        );
    }
    if (data && (data.status === 'refused' || data.status === 'error')) {
        // 'error' statuses can be per-hour advisories on a run that keeps
        // going (e.g. a typed evaporation refusal) — never assume fatal.
        // Re-reading the view is self-truthing: a live run repaints live,
        // a torn-down run returns "no active simulation" -> 'no run'.
        refreshThermalTrainHeadline();
    }
});

socket.on('per_hour_summary', (data) => {
    renderKnudsenRegimePanelFromPerHour(data);
    // Live quick-check: hourly cadence is the natural throttle for the
    // ledger_api view read (walks the snapshot history once per call).
    refreshThermalTrainHeadline();
});

// A run parked at a decision gate emits no further per-hour events — paint
// the strip at the gate, where the operator is actually looking.
socket.on('decision_required', () => {
    refreshThermalTrainHeadline();
});

window.renderWallRiskPanel = renderWallRiskPanel;
window.renderCeramicRumpPanel = renderCeramicRumpPanel;
window.renderVaporPressureAuthorityPanel = renderVaporPressureAuthorityPanel;
window.renderProductLedgerPanel = renderProductLedgerPanel;
window.renderOverlapEvaporationPanel = renderOverlapEvaporationPanel;
window.renderKnudsenRegimePanelFromDiagnostic = renderKnudsenRegimePanelFromDiagnostic;
window.renderKnudsenRegimePanelFromPerHour = renderKnudsenRegimePanelFromPerHour;
window.renderThermalTrainHeadline = renderThermalTrainHeadline;

// Initial paint: a reload mid-run (or onto a paused/completed run) gets the
// current view immediately instead of waiting for the next per-hour event.
// Both hooks are needed: 'connect' can fire before the panel DOM exists (the
// refresh guard no-ops on a missing panel), and DOM-ready can precede the
// socket connection; whichever lands second does the paint.
socket.on('connect', () => {
    refreshThermalTrainHeadline();
});
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        refreshThermalTrainHeadline();
    });
} else {
    refreshThermalTrainHeadline();
}
