"use strict";

(function registerEnergyPanel(root) {
  const { scalarText, fmtNum, esc } = root.ReportLabels;

  const CUMULATIVE_COMPONENTS = Object.freeze([
    ["electrical", "Electrical load"],
    ["evaporation_thermal", "Diagnostic evaporation-enthalpy estimate", "Diagnostic · Ledger-neutral estimate"],
    ["latent", "Latent vaporization component", "Diagnostic · Ledger-neutral estimate"],
    ["dissociation", "Reaction / dissociation component", "Diagnostic · Ledger-neutral estimate"],
    ["electrical_plus_evaporation", "Scoped electrical + evaporation energy", "Contains diagnostic evaporation-enthalpy estimate"]
  ]);

  const EVAPORATION_COMPONENTS = Object.freeze([
    ["evaporation_enthalpy_sink", "Diagnostic evaporation-enthalpy sink estimate"],
    ["reaction_disproportionation_enthalpy_sink", "Reaction / dissociation enthalpy sink"],
    ["product_vapor_enthalpy_sink", "Product-vapor enthalpy sink"],
    ["net_unallocated", "Net unallocated"]
  ]);

  // Authority chip strings must match emitter semantics:
  // - pure diagnostic paths (evaporation thermal / latent / reaction) are ledger-neutral estimates
  // - combined electrical+diagnostic totals only *contain* a diagnostic component
  const PURE_DIAGNOSTIC_AUTHORITY = "Diagnostic · Ledger-neutral estimate";
  const MIXED_COMBINED_AUTHORITY = "Contains diagnostic evaporation-enthalpy estimate";

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function isFiniteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function readableToken(value) {
    if (value === undefined) return "Not emitted";
    if (value === null || (typeof value === "string" && !value.trim())) {
      return "Emitted empty";
    }
    if (typeof value !== "string") {
      const kind = Array.isArray(value) ? "array" : typeof value;
      return `Malformed (${kind})`;
    }
    const raw = scalarText(value).trim();
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

  function authorityChip(authority) {
    if (!authority) return "";
    // Keep the established pure-diagnostic chip markup (bold lead-in). Mixed
    // combined values use the full qualifier as plain chip text so they are
    // not branded as purely diagnostic.
    if (authority === PURE_DIAGNOSTIC_AUTHORITY) {
      return `<span class="sec-p7-energy-chip"><b>Diagnostic</b> · Ledger-neutral estimate</span>`;
    }
    return `<span class="sec-p7-energy-chip">${esc(authority)}</span>`;
  }

  function caveatBlock(summary, authority = null) {
    const scope = isRecord(summary) ? summary.energy_scope : undefined;
    const furnaceHeatStatus = isRecord(summary) ? summary.furnace_heat_status : undefined;
    return `<div class="sec-p7-energy-caveats" aria-label="Energy scope and furnace heat coverage">` +
      `<span class="sec-p7-energy-chip"><b>Scope</b> · ${esc(readableScope(scope))}</span>` +
      `<span class="sec-p7-energy-chip"><b>Furnace heat</b> · ${esc(readableToken(furnaceHeatStatus))}</span>` +
      authorityChip(authority) +
      `</div>`;
  }

  function emittedValue(value) {
    if (isFiniteNumber(value)) return esc(fmtNum(value, "kWh"));
    let state = "malformed";
    if (value === undefined) state = "not emitted";
    if (value === null || (typeof value === "string" && !value.trim())) {
      state = "emitted empty";
    }
    return `<span class="sec-p7-energy-missing">pending · ${state}</span>`;
  }

  function metricCard(label, value, basis, summary, tone, authority = null) {
    return `<article class="sec-p7-energy-card sec-p7-energy-${tone}">` +
      `<h3>${esc(label)}</h3>` +
      `<div class="sec-p7-energy-value">${emittedValue(value)}</div>` +
      `<p class="sec-p7-energy-basis">${esc(basis)}</p>` +
      caveatBlock(summary, authority) +
      `</article>`;
  }

  function breakdownRow(label, value, authority) {
    const authorityBadge = authority && isFiniteNumber(value)
      ? `<span class="sec-p7-energy-row-authority">${esc(authority)}</span>`
      : "";
    return `<tr><th scope="row">${esc(label)}</th><td>${emittedValue(value)}${authorityBadge}</td></tr>`;
  }

  function breakdownRows(breakdown, expectedComponents) {
    const map = isRecord(breakdown) ? breakdown : {};
    const expectedKeys = new Set(expectedComponents.map(([key]) => key));
    const expected = expectedComponents
      .map(([key, label, authority]) => breakdownRow(label, map[key], authority))
      .join("");
    const extras = Object.keys(map)
      .filter((key) => !expectedKeys.has(key))
      .sort((left, right) => left.localeCompare(right))
      .map((key) => breakdownRow(readableToken(key), map[key]))
      .join("");
    return expected + extras;
  }

  function breakdownCard(title, basis, breakdown, expectedComponents, summary, authority = null) {
    let notice = "";
    if (breakdown === undefined) {
      notice = "Breakdown not emitted; no components are inferred.";
    } else if (breakdown === null || (isRecord(breakdown) && !Object.keys(breakdown).length)) {
      notice = "Emitted breakdown is empty; no components are inferred.";
    } else if (!isRecord(breakdown)) {
      notice = "Emitted breakdown is malformed; no components are inferred.";
    }
    const pending = notice
      ? `<div class="sec-p7-energy-pending"><b>Pending breakdown</b><span>${notice}</span></div>`
      : "";
    return `<article class="sec-p7-energy-breakdown">` +
      `<h3>${esc(title)}</h3>` +
      `<p class="sec-p7-energy-basis">${esc(basis)}</p>` +
      pending +
      `<div class="sec-p7-energy-table-wrap"><table><thead><tr><th>Emitted component</th><th>Energy · kWh</th></tr></thead>` +
      `<tbody>${breakdownRows(breakdown, expectedComponents)}</tbody></table></div>` +
      caveatBlock(summary, authority) +
      `</article>`;
  }

  function pendingPanel(message) {
      return `<section class="sec-p7-energy" id="sec-p7-energy">` +
        `<h2><span class="sect">07</span>Energy breakdown</h2>` +
        `<p class="sub">Electrical load and the diagnostic evaporation-enthalpy estimate, without viewer-derived totals.</p>` +
        `<div class="sec-p7-energy-pending"><b>Pending energy data</b><span>${message}</span></div>` +
        caveatBlock(null) +
        `</section>`;
  }

  function render(artifact, rows) {
    if (rows === undefined) {
      return pendingPanel("No terminal timestep summary was emitted.");
    }
    if (rows === null || (Array.isArray(rows) && !rows.length)) {
      return pendingPanel("Terminal timestep rows were empty.");
    }
    if (!Array.isArray(rows)) {
      return pendingPanel("Terminal timestep rows were malformed.");
    }

    const summary = rows.at(-1);
    if (summary === undefined) {
      return pendingPanel("No terminal timestep summary was emitted.");
    }
    if (summary === null || (typeof summary === "string" && !summary.trim())) {
      return pendingPanel("Terminal timestep summary was empty.");
    }
    if (!isRecord(summary)) {
      return pendingPanel("Terminal timestep summary was malformed.");
    }

    const cumulative = isRecord(summary.energy_cumulative_breakdown_kWh)
      ? summary.energy_cumulative_breakdown_kWh
      : null;

    return `<section class="sec-p7-energy" id="sec-p7-energy">` +
      `<h2><span class="sect">07</span>Energy breakdown</h2>` +
      `<p class="sub">Electrical load beside the emitted diagnostic evaporation-enthalpy estimate. Scope, furnace-heat coverage, and diagnostic authority travel with every affected value.</p>` +
      `<div class="sec-p7-energy-grid">` +
      metricCard("Cumulative electrical load", cumulative?.electrical, "Cumulative through the terminal timestep", summary, "electrical") +
      metricCard("Cumulative diagnostic evaporation-enthalpy estimate", cumulative?.evaporation_thermal, "Cumulative through the terminal timestep", summary, "thermal", PURE_DIAGNOSTIC_AUTHORITY) +
      metricCard("Electrical energy", summary.energy_electrical_kWh, "Terminal timestep · one-hour interval", summary, "electrical") +
      metricCard("Diagnostic evaporation-enthalpy estimate", summary.energy_evaporation_thermal_kWh, "Terminal timestep · one-hour interval", summary, "thermal", PURE_DIAGNOSTIC_AUTHORITY) +
      metricCard("Latent vaporization component", summary.energy_latent_kWh, "Terminal timestep · one-hour interval", summary, "thermal", PURE_DIAGNOSTIC_AUTHORITY) +
      metricCard("Reaction / dissociation component", summary.energy_dissociation_kWh, "Terminal timestep · one-hour interval", summary, "thermal", PURE_DIAGNOSTIC_AUTHORITY) +
      metricCard("Terminal-timestep scoped combined energy", summary.energy_electrical_plus_evaporation_kWh, "Terminal timestep · one-hour interval · not viewer-summed", summary, "combined", MIXED_COMBINED_AUTHORITY) +
      metricCard("Cumulative scoped combined energy", summary.energy_electrical_plus_evaporation_cumulative_kWh, "Cumulative through the terminal timestep · not viewer-summed", summary, "combined", MIXED_COMBINED_AUTHORITY) +
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
        "Terminal-timestep diagnostic evaporation breakdown",
        "One-hour interval. The total sink overlaps its emitted latent and reaction components; these rows are not summed in this viewer.",
        summary.energy_evaporation_breakdown_kWh,
        EVAPORATION_COMPONENTS,
        summary,
        PURE_DIAGNOSTIC_AUTHORITY
      ) +
      `</div>` +
      `</section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({
    id: "sec-p7-energy",
    render
  });
}(globalThis));
