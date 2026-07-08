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

socket.on('simulation_tick', (data) => {
    renderWallRiskPanel(data.wall_risk_panel);
    renderVaporPressureAuthorityPanel(data.vapor_pressure_authority_panel);
});

socket.on('simulation_complete', (data) => {
    renderCeramicRumpPanel(data.ceramic_rump_panel);
    renderVaporPressureAuthorityPanel(data.vapor_pressure_authority_panel);
});

window.renderWallRiskPanel = renderWallRiskPanel;
window.renderCeramicRumpPanel = renderCeramicRumpPanel;
window.renderVaporPressureAuthorityPanel = renderVaporPressureAuthorityPanel;
