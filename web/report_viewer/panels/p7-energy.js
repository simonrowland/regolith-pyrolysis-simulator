"use strict";

(function registerEnergyPanel(root) {
  const { scalarText, fmtNum, esc } = root.ReportLabels;

  const CUMULATIVE_COMPONENTS = Object.freeze([
    ["electrical", "Electrical load"],
    ["evaporation_thermal", "Known evaporation thermal sink"],
    ["latent", "Latent vaporization component"],
    ["dissociation", "Dissociation component"],
    ["electrical_plus_evaporation", "Scoped electrical + evaporation energy"]
  ]);

  const EVAPORATION_COMPONENTS = Object.freeze([
    ["evaporation_enthalpy_sink", "Evaporation enthalpy sink"],
    ["reaction_disproportionation_enthalpy_sink", "Reaction / disproportionation enthalpy sink"],
    ["product_vapor_enthalpy_sink", "Product-vapor enthalpy sink"],
    ["net_unallocated", "Net unallocated"]
  ]);

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function isFiniteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function readableToken(value) {
    const raw = scalarText(value, "not emitted").trim();
    if (!raw) return "not emitted";
    const words = raw
      .replace(/[_-]+/g, " ")
      .replace(/\s+/g, " ");
    return words.charAt(0).toUpperCase() + words.slice(1);
  }

  function readableScope(value) {
    if (value === "electrical_plus_known_evaporation_enthalpy") {
      return "Electrical + known evaporation enthalpy";
    }
    if (value === "electrical_only") return "Electrical only";
    return readableToken(value);
  }

  function caveatBlock(summary) {
    const scope = isRecord(summary) ? summary.energy_scope : undefined;
    const furnaceHeatStatus = isRecord(summary) ? summary.furnace_heat_status : undefined;
    return `<div class="sec-p7-energy-caveats" aria-label="Energy scope and furnace heat coverage">` +
      `<span class="sec-p7-energy-chip"><b>Scope</b> · ${esc(readableScope(scope))}</span>` +
      `<span class="sec-p7-energy-chip"><b>Furnace heat</b> · ${esc(readableToken(furnaceHeatStatus))}</span>` +
      `</div>`;
  }

  function emittedValue(value) {
    return isFiniteNumber(value)
      ? esc(fmtNum(value, "kWh"))
      : `<span class="sec-p7-energy-missing">pending · not emitted</span>`;
  }

  function metricCard(label, value, basis, summary, tone) {
    return `<article class="sec-p7-energy-card sec-p7-energy-${tone}">` +
      `<h3>${esc(label)}</h3>` +
      `<div class="sec-p7-energy-value">${emittedValue(value)}</div>` +
      `<p class="sec-p7-energy-basis">${esc(basis)}</p>` +
      caveatBlock(summary) +
      `</article>`;
  }

  function breakdownRow(label, value) {
    return `<tr><th scope="row">${esc(label)}</th><td>${emittedValue(value)}</td></tr>`;
  }

  function breakdownRows(breakdown, expectedComponents) {
    const map = isRecord(breakdown) ? breakdown : {};
    const expectedKeys = new Set(expectedComponents.map(([key]) => key));
    const expected = expectedComponents
      .map(([key, label]) => breakdownRow(label, map[key]))
      .join("");
    const extras = Object.keys(map)
      .filter((key) => !expectedKeys.has(key))
      .sort((left, right) => left.localeCompare(right))
      .map((key) => breakdownRow(readableToken(key), map[key]))
      .join("");
    return expected + extras;
  }

  function breakdownCard(title, basis, breakdown, expectedComponents, summary) {
    const absent = !isRecord(breakdown)
      ? `<div class="sec-p7-energy-pending"><b>Pending breakdown</b><span>Breakdown not emitted; no components are inferred.</span></div>`
      : "";
    return `<article class="sec-p7-energy-breakdown">` +
      `<h3>${esc(title)}</h3>` +
      `<p class="sec-p7-energy-basis">${esc(basis)}</p>` +
      absent +
      `<div class="sec-p7-energy-table-wrap"><table><thead><tr><th>Emitted component</th><th>Energy · kWh</th></tr></thead>` +
      `<tbody>${breakdownRows(breakdown, expectedComponents)}</tbody></table></div>` +
      caveatBlock(summary) +
      `</article>`;
  }

  function render(artifact, rows) {
    const summary = Array.isArray(rows) && rows.length ? rows.at(-1) : null;
    if (!isRecord(summary)) {
      return `<section class="sec-p7-energy" id="sec-p7-energy">` +
        `<h2><span class="sect">07</span>Energy breakdown</h2>` +
        `<p class="sub">Electrical load and the known evaporation-enthalpy sink, without viewer-derived totals.</p>` +
        `<div class="sec-p7-energy-pending"><b>Pending energy data</b><span>No terminal timestep summary was emitted.</span></div>` +
        caveatBlock(null) +
        `</section>`;
    }

    const cumulative = isRecord(summary.energy_cumulative_breakdown_kWh)
      ? summary.energy_cumulative_breakdown_kWh
      : null;

    return `<section class="sec-p7-energy" id="sec-p7-energy">` +
      `<h2><span class="sect">07</span>Energy breakdown</h2>` +
      `<p class="sub">Electrical load beside the emitted known evaporation-enthalpy sink. Scope and furnace-heat coverage travel with every value.</p>` +
      `<div class="sec-p7-energy-grid">` +
      metricCard("Cumulative electrical load", cumulative?.electrical, "Cumulative through the terminal timestep", summary, "electrical") +
      metricCard("Cumulative known evaporation thermal sink", cumulative?.evaporation_thermal, "Cumulative through the terminal timestep", summary, "thermal") +
      metricCard("Electrical energy", summary.energy_electrical_kWh, "Terminal timestep · one-hour interval", summary, "electrical") +
      metricCard("Known evaporation thermal sink", summary.energy_evaporation_thermal_kWh, "Terminal timestep · one-hour interval", summary, "thermal") +
      metricCard("Latent vaporization component", summary.energy_latent_kWh, "Terminal timestep · one-hour interval", summary, "thermal") +
      metricCard("Dissociation component", summary.energy_dissociation_kWh, "Terminal timestep · one-hour interval", summary, "thermal") +
      metricCard("Terminal-timestep scoped combined energy", summary.energy_electrical_plus_evaporation_kWh, "Terminal timestep · one-hour interval · not viewer-summed", summary, "combined") +
      metricCard("Cumulative scoped combined energy", summary.energy_electrical_plus_evaporation_cumulative_kWh, "Cumulative through the terminal timestep · not viewer-summed", summary, "combined") +
      `</div>` +
      `<div class="sec-p7-energy-breakdowns">` +
      breakdownCard(
        "Cumulative emitted component breakdown",
        "Cumulative through the terminal timestep. The combined row is emitted; component rows are not summed in this viewer.",
        summary.energy_cumulative_breakdown_kWh,
        CUMULATIVE_COMPONENTS,
        summary
      ) +
      breakdownCard(
        "Terminal-timestep evaporation breakdown",
        "One-hour interval. The total sink overlaps its emitted latent and reaction components; these rows are not summed in this viewer.",
        summary.energy_evaporation_breakdown_kWh,
        EVAPORATION_COMPONENTS,
        summary
      ) +
      `</div>` +
      `</section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({
    id: "sec-p7-energy",
    render
  });
}(globalThis));
