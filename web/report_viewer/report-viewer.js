"use strict";

const { scalarText, fmtNum, fmtRunId, prettySpecies, accountLabel, prettyFeedstock, prettyChemText, speciesColor, esc } = globalThis.ReportLabels;
const RUN_ID = new URLSearchParams(window.location.search).get("run");
const RUN_QUERY = RUN_ID ? `?run=${encodeURIComponent(RUN_ID)}` : "";
const ARTIFACT_URL = RUN_ID
  ? `/api/runs/${encodeURIComponent(RUN_ID)}`
  : "./sample-run-artifact.json";
const SUPPORTED_ARTIFACT_SCHEMA_MAJOR = 0;
const ELLINGHAM_ORDER = ["Na", "K", "Fe", "Cr", "Mn", "Mg", "Si", "Al", "Ti", "Ca"];
const COLORS = ["#e8940f", "#1f7798", "#468466", "#8b63a6", "#a95c43", "#6f8c9e"];
const DISPOSITION_GROUPS = Object.freeze([
  // Account-role buckets only — not feedstock-origin or per-species yield tiers.
  { key: "products", label: "Product accounts" },
  { key: "retained", label: "Retained accounts" },
  { key: "losses", label: "Loss accounts" },
  { key: "terminal_inventory", label: "Terminal inventory accounts" },
  { key: "reagent_cycle", label: "Reagent cycle · excluded" },
  { key: "unclassified", label: "Unclassified accounts" }
]);

const $ = (selector, root = document) => root.querySelector(selector);
// Accept ONLY a real finite number — never coerce. JS Number() turns true→1, false→0, []→0,
// "  "→0, "12"→12, so the old `Number.isFinite(Number(v))` gate let booleans/arrays/blank strings
// through the kg/energy/sum paths and fabricated a confident "0 kg O₂" / "1 kg Fe" / energy total.
// This is the kg/energy twin of the mol path's strictMol guard — same contract (typeof number).
const hasNumber = (value) => typeof value === "number" && Number.isFinite(value);
const n = (value) => hasNumber(value) ? value : null;
const sourceSideO2 = (row) => {
  const canonical = row?.O2_source_side_potential_kg_cumulative;
  if (typeof canonical === "number" && Number.isFinite(canonical)) return canonical;
  const legacy = row?.O2_yield_kg_cumulative;
  return typeof legacy === "number" && Number.isFinite(legacy) ? legacy : null;
};
const sum = (values) => values.reduce((total, value) => total + (n(value) ?? 0), 0);
const sumPresent = (values) => values.length && values.every(hasNumber) ? sum(values) : null;
const sumObject = (object) => {
  if (object === null || object === undefined || typeof object !== "object") return null;
  let total = 0;
  for (const value of Object.values(object)) {
    const amount = typeof value === "object" && value !== null ? sumObject(value) : n(value);
    if (amount === null) return null;
    total += amount;
  }
  return total;
};
const maxPresent = (values) => {
  const emitted = values.map(n).filter((value) => value !== null);
  return emitted.length ? Math.max(...emitted) : null;
};
const minPresent = (values) => {
  const emitted = values.map(n).filter((value) => value !== null);
  return emitted.length ? Math.min(...emitted) : null;
};
const kg = (value) => fmtNum(value, "kg");
const exactValue = (value, unit) => hasNumber(value)
  ? `<span title="${esc(`${String(value)}${unit ? ` ${unit}` : ""}`)}">${esc(fmtNum(value, unit))}</span>`
  : "not emitted";
const exactKg = (value) => exactValue(value, "kg");
const exactMol = (value) => exactValue(value, "mol");
const runIdSpan = (value) => `<span title="${esc(value)}">${esc(fmtRunId(value))}</span>`;
// A nameless live run's `name` is its raw hash id; don't dump 32 hex chars as
// the H1. Show a readable title with the short id instead (full id stays in
// the masthead run-id span + tooltip).
const isHashLike = (value) => typeof value === "string" && /^(?:[0-9a-f]{24,}|[0-9a-f]{8}-[0-9a-f-]{27,})$/i.test(value.trim());
const runTitle = (header) => {
  const name = typeof header.name === "string" ? header.name.trim() : "";
  return name && !isHashLike(name) ? name : `Untitled run · ${fmtRunId(header.run_id ?? name)}`;
};
const accountSpan = (value) => `<span title="${esc(value)}">${esc(accountLabel(value))}</span>`;
// Engine identity strings can be long provenance blobs (binary path, host,
// sha256 digest). Show a readable head; the full value stays on the tooltip so
// nothing emitted is lost.
const IDENTITY_MAX = 44;
const identitySpan = (value) => {
  if (value !== null && typeof value === "object") {
    let raw = "";
    try { raw = JSON.stringify(value); } catch (_error) { raw = ""; }
    return `<span${raw ? ` title="${esc(raw)}"` : ""}>${esc(scalarText(value))}</span>`;
  }
  const text = scalarText(value);
  if (isHashLike(text)) return runIdSpan(text);
  return text.length > IDENTITY_MAX
    ? `<span title="${esc(text)}">${esc(text.slice(0, IDENTITY_MAX).trimEnd())}…</span>`
    : esc(text);
};
const speciesSpan = (value) => esc(prettySpecies(value));
// Ledger cells must never fabricate: JS coercion turns true into "1 mol" and
// []/false/whitespace into "0 mol". Only real finite numbers render as mol.
const strictMol = (value) => typeof value === "number" && Number.isFinite(value)
  ? exactValue(value, "mol")
  : `<span class="trace">non-numeric (${esc(typeof value === "string" ? "string" : Array.isArray(value) ? "array" : typeof value)})</span>`;
const strictMolSum = (values) => values.length && values.every((value) => typeof value === "number" && Number.isFinite(value))
  ? exactValue(values.reduce((total, value) => total + value, 0), "mol")
  : values.length ? "not numeric" : "not emitted";
const money = (value) => hasNumber(value)
  ? Number(value).toLocaleString(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 2 })
  : "not emitted";
const sci = (value) => fmtNum(value);

// The rollup owns the price-authority claim. Preserve its emitted basis,
// count, and parameter names; never substitute array length for its count.
function priceAuthority(artifact) {
  const rollup = artifact?.terminal?.run_metadata?.cost_rollup_diagnostic;
  if (!rollup || typeof rollup !== "object" || Array.isArray(rollup)) return null;
  const basis = typeof rollup.price_basis === "string" ? rollup.price_basis.trim() : "";
  const count = hasNumber(rollup.owner_ratify_placeholder_count)
    ? rollup.owner_ratify_placeholder_count
    : null;
  const names = (Array.isArray(rollup.owner_ratify_placeholders) ? rollup.owner_ratify_placeholders : [])
    .map((item) => item && typeof item === "object" && !Array.isArray(item) ? item.name : null)
    .filter((name) => typeof name === "string" && name.trim())
    .map((name) => name.trim());
  const flagged = /placeholder|awaiting|unratified/i.test(basis)
    || (count !== null && count > 0)
    || names.length > 0;
  return flagged ? { basis, count, names } : null;
}

function pending(task, message) {
  return `<div class="pending"><strong>Pending ${esc(task)}</strong><p>${esc(message)}</p></div>`;
}

function section(number, title, subtitle, content) {
  return `<section><h2><span class="sect">${String(number).padStart(2, "0")}</span>${esc(title)}</h2>` +
    `<p class="sub">${esc(subtitle)}</p>${content}</section>`;
}

function totalSeries(rows, key) {
  let total = 0;
  let complete = true;
  return rows.map((row) => {
    const value = n(row.summary?.[key]);
    if (value === null) complete = false;
    if (complete) total += value;
    return complete ? total : null;
  });
}

function campaignSpans(timesteps) {
  return timesteps.reduce((spans, timestep, index) => {
    const campaign = timestep.summary.campaign || "unknown";
    const previous = spans.at(-1);
    if (!previous || previous.name !== campaign) spans.push({ name: campaign, startIndex: index, endIndex: index });
    else previous.endIndex = index;
    return spans;
  }, []);
}

function lineChart(id, title, series, options = {}) {
  const width = 520;
  const height = 190;
  const pad = { left: 44, right: 14, top: 15, bottom: 25 };
  const allValues = series.flatMap((item) => item.values).map(n).filter((value) => value !== null);
  if (!series.length || !allValues.length) {
    return `<div class="chartbox"><div class="chart-title">${esc(title)}</div><div class="pending"><strong>Not emitted</strong><p>No numeric series values were emitted.</p></div></div>`;
  }
  const transform = options.log ? (value) => Math.log10(Math.max(value, 1e-12)) : (value) => value;
  const transformed = allValues.map(transform);
  let min = options.zero ? 0 : Math.min(...transformed);
  let max = Math.max(...transformed);
  if (!Number.isFinite(min)) min = 0;
  if (!Number.isFinite(max) || min === max) max = min + 1;
  const pointCount = Math.max(...series.map((item) => item.values.length));
  const x = (index) => pad.left + index / Math.max(1, pointCount - 1) * (width - pad.left - pad.right);
  const y = (value) => height - pad.bottom - (transform(value) - min) / (max - min) * (height - pad.top - pad.bottom);
  const paths = series.map((item, seriesIndex) => {
    const segments = [];
    let points = [];
    item.values.forEach((value, index) => {
      const numeric = n(value);
      if (numeric === null) {
        if (points.length) segments.push(points);
        points = [];
      } else {
        points.push(`${x(index).toFixed(2)},${y(numeric).toFixed(2)}`);
      }
    });
    if (points.length) segments.push(points);
    return segments.map((segment) => `<polyline class="series" style="stroke:${item.color || COLORS[seriesIndex]}" points="${segment.join(" ")}"/>`).join("");
  }).join("");
  const bands = (options.spans || []).map((span, index) => {
    const start = x(span.startIndex);
    const end = x(span.endIndex);
    return `<rect class="campaign-band" x="${start.toFixed(2)}" y="${pad.top}" width="${Math.max(1, end - start).toFixed(2)}" height="${height - pad.top - pad.bottom}" style="opacity:${index % 2 ? .065 : .025}"/>`;
  }).join("");
  const legend = series.map((item, index) => `<span><i class="swatch" style="background:${item.color || COLORS[index]}"></i>${esc(item.label)}</span>`).join("");
  return `<div class="chartbox"><div class="chart-title">${esc(title)}</div><div class="legend">${legend}</div>` +
    `<svg id="${esc(id)}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(title)}">${bands}` +
    `<line class="axis" x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}"/>` +
    `<line class="axis" x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}"/>${paths}` +
    `<line class="marker" data-marker x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}"/>` +
    `<text class="chart-label" x="${pad.left}" y="${height - 7}">h 1</text><text class="chart-label" text-anchor="end" x="${width - pad.right}" y="${height - 7}">h ${pointCount}</text>` +
    `<text class="chart-label" x="3" y="${pad.top + 4}">${esc(options.maxLabel || fmtNum(max))}</text>` +
    `<text class="chart-label" x="3" y="${height - pad.bottom}">${esc(options.minLabel || (options.log ? `10^${fmtNum(min)}` : fmtNum(min)))}</text></svg></div>`;
}

function updateMarkers(index, count) {
  const x = 44 + index / Math.max(1, count - 1) * (520 - 44 - 14);
  document.querySelectorAll("[data-marker]").forEach((marker) => {
    marker.setAttribute("x1", x);
    marker.setAttribute("x2", x);
  });
}

function makeHeader(artifact, rows, energy) {
  const header = artifact.header;
  const finalRow = rows.at(-1) || {};
  const finalMetal = finalRow.metal_yields_kg || {};
  const o2 = sourceSideO2(finalRow);
  const o2Label = prettyChemText(finalRow.O2_metric_label || "O₂ metric label not emitted");
  const temperatures = rows.map((row) => row.T_C);
  const peakTemperature = temperatures.length && temperatures.every(hasNumber) ? maxPresent(temperatures) : null;
  const reportedEnergy = hasNumber(energy.electrical) && hasNumber(energy.evaporation) ? energy.electrical + energy.evaporation : null;
  // join() coerces before esc() can guard, so map entries through scalarText first.
  const campaignChain = Array.isArray(header.campaign_chain)
    ? header.campaign_chain.map(scalarText).join("→") || "—"
    : "—";
  const status = artifact.execution_status;
  // join() coerces before esc() can guard, so scalar-check the parts first.
  const failureText = [artifact.failure?.reason, artifact.failure?.error_message]
    .filter(Boolean).map(scalarText).join(" · ")
    || "No failure reason or error message was emitted in this artifact.";
  const costProvenance = typeof header.cost_block?.provenance === "string" && header.cost_block.provenance.trim()
    ? header.cost_block.provenance.trim()
    : null;
  const feedstockLabel = prettyFeedstock(header.feedstock_id);
  const priceFlag = priceAuthority(artifact);
  const priceFlagTip = [priceFlag?.basis, priceFlag?.names.join(", ")].filter(Boolean).join(" — ");
  const priceFlagNote = priceFlag
    ? `<small class="price-flag"${priceFlagTip ? ` title="${esc(priceFlagTip)}"` : ""}>unratified placeholder prices${priceFlag.count !== null && priceFlag.count > 0 ? ` (${esc(fmtNum(priceFlag.count))})` : ""}</small>`
    : "";
  return `<header>
    <div class="masthead">
      <svg class="mark" viewBox="0 0 42 42" aria-hidden="true"><circle cx="16" cy="27" r="11" fill="none" stroke="currentColor" stroke-width="1.4"/><ellipse cx="16" cy="27" rx="4.8" ry="11" fill="none" stroke="currentColor"/><path d="M6 23q10-4 20 0M6 31q10 4 20 0M29 7l-4 8 8 4 5-2M25 15l-6 2-3-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="30" cy="5" r="2.6" fill="currentColor"/></svg>
      <div class="brand"><strong>DIRECT LEAP</strong> TECHNOLOGIES</div>
      <div class="doc-label">Run report<br><span class="mono">${runIdSpan(header.run_id)}</span></div>
    </div>
    <h1>${esc(runTitle(header))}</h1>
    <p class="lede"><b>${hasNumber(peakTemperature) ? `${exactValue(peakTemperature, "°C")} peak` : "peak temperature not emitted"}</b> · <span title="${esc(header.feedstock_id)}">${esc(feedstockLabel)}</span> · <b>${rows.length} hours</b> · ${esc(campaignChain)}</p>
    <div class="meta-chips">
      <span class="chip">charge ${kg(header.charge_mass_kg)}</span>
      <span class="chip">engine ${esc(header.engine_identity?.name)}</span>
      <span class="chip">schema ${esc(artifact.artifact_schema_version)}</span>
      ${header.c3_dose && Object.keys(header.c3_dose).length ? `<span class="chip accent">C3 dose ${Object.entries(header.c3_dose).map(([key, value]) => `${speciesSpan(key.replace(/_kg$/, ""))} ${exactKg(value)}`).join(" · ")}</span>` : ""}
    </div>
    <div class="status-banner ${["failed", "refused"].includes(status) ? "failed" : ""}">
      <div class="status-icon">${status === "ok" ? "✓" : "!"}</div><div><strong>Execution status: ${esc(status)}</strong>
      <p>Lifecycle: ${esc(artifact.lifecycle)}. ${esc(failureText)}</p></div>
    </div>
    <div class="glance">
      <div class="metric"><div class="k">Fe evolved</div><div class="v">${kg(finalMetal.Fe)}</div></div>
      <div class="metric"><div class="k">${esc(o2Label)}</div><div class="v">${exactKg(o2)}</div></div>
      <div class="metric"><div class="k">Reported energy</div><div class="v">${exactValue(reportedEnergy, "kWh")} <small>electrical + evaporation thermal</small></div></div>
      <div class="metric"><div class="k">Two-price energy cost</div><div class="v">${header.cost_block ? money(energy.totalCost) : "pending W-A5a"}${costProvenance ? `<small>${esc(costProvenance)}</small>` : ""}${priceFlagNote}</div></div>
    </div>
  </header>`;
}

function yieldsSection(rows, terminal) {
  const evolved = rows.at(-1).metal_yields_kg || {};
  const max = Math.max(maxPresent(Object.values(evolved)) ?? 0, 1);
  const chips = ELLINGHAM_ORDER.map((element) => {
    const widthPct = Math.min(100, Math.sqrt((n(evolved[element]) ?? 0) / max) * 100);
    // "evolved" qualifies a mass; an absent species reads "not emitted", never
    // "not emitted evolved".
    const mass = hasNumber(evolved[element]) ? `${exactKg(evolved[element])} evolved` : exactKg(evolved[element]);
    return `<div class="yield-chip"><div class="el">${speciesSpan(element)}</div><div class="kg">${mass}</div><div class="bar"><i style="width:${widthPct.toFixed(2)}%"></i></div></div>`;
  }).join("");
  const gap = terminal.yield_disposition ? "" : `<div class="note">Per-species feedstock-yield fractions are pending <span class="mono">yield_disposition</span>; none are inferred here.</div>`;
  // "Evolved" only — not recovered product mass or feedstock-origin yield fractions.
  return section(1, "Evolved metal mass — Ellingham order", "Exact evolved mass from the final hourly metal_yields_kg row. Not recovered product mass; feedstock-origin fractions require yield_disposition.", `<div class="yield-track" aria-label="Evolved metal mass by element">${chips}</div>${gap}`);
}

function processSection(artifact, rows, spans) {
  const temperature = rows.map((row) => n(row.T_C));
  const pressure = rows.map((row) => n(row.pO2_bar));
  const carrierPressure = rows.map((row) => n(row.p_carrier_bar));
  const hasCarrierPressure = carrierPressure.some((value) => value !== null);
  const carrierIdentities = [...new Set(rows.map((row) => row.carrier_identity).filter((value) => typeof value === "string" && value.trim()).map((value) => value.trim()))];
  const electrical = totalSeries(artifact.timesteps, "energy_electrical_kWh");
  const thermal = totalSeries(artifact.timesteps, "energy_evaporation_thermal_kWh");
  const vaporKeys = [...new Set(rows.flatMap((row) => Object.keys(row.vapor_species_kg_hr || {})))];
  const topVapors = vaporKeys.map((key) => ({ key, peak: maxPresent(rows.map((row) => row.vapor_species_kg_hr?.[key])) ?? 0 })).sort((a, b) => b.peak - a.peak).slice(0, 4);
  const charts = [
    lineChart("temperature-chart", "Melt temperature · °C", [{ label: "T °C", values: temperature, color: COLORS[0] }], { spans, maxLabel: fmtNum(maxPresent(temperature), "°C") }),
    lineChart("pressure-chart", "O₂ partial pressure · bar (log scale)", [{ label: "pO₂ bar", values: pressure, color: COLORS[1] }], { log: true, spans, maxLabel: fmtNum(maxPresent(pressure), "bar") }),
    lineChart("energy-chart", "Cumulative energy · kWh", [{ label: "electrical", values: electrical, color: COLORS[1] }, { label: "thermal: evaporation total (latent + dissociation breakdown)", values: thermal, color: COLORS[2] }], { zero: true, spans }),
    lineChart("vapor-chart", "Vapor species surges · kg/h", topVapors.map((item, index) => ({ label: prettySpecies(item.key), values: rows.map((row) => n(row.vapor_species_kg_hr?.[item.key])), color: COLORS[index] })), { zero: true, spans })
  ];
  if (hasCarrierPressure) {
    charts.splice(2, 0, lineChart("carrier-pressure-chart", "Carrier pressure · bar", [{ label: "carrier bar", values: carrierPressure, color: COLORS[3] }], { spans, maxLabel: fmtNum(maxPresent(carrierPressure), "bar") }));
  }
  const carrierIdentity = carrierIdentities.length
    ? `<div class="note"><b>Carrier identity:</b> ${carrierIdentities.map((identity) => `<span class="mono">${esc(identity)}</span>`).join(" · ")}</div>`
    : pending("carrier_identity", "summary.carrier_identity is absent. No carrier identity is inferred.");
  const carrierPressureNote = hasCarrierPressure
    ? ""
    : pending("p_carrier_bar", "summary.p_carrier_bar is absent. P_total − pO₂ is not used as a substitute.");
  const firstHour = artifact.timesteps[0].hour === undefined || artifact.timesteps[0].hour === null ? "not emitted" : scalarText(artifact.timesteps[0].hour);
  const lastHour = artifact.timesteps.at(-1).hour === undefined || artifact.timesteps.at(-1).hour === null ? "not emitted" : scalarText(artifact.timesteps.at(-1).hour);
  const firstCampaign = rows[0].campaign ?? "campaign not emitted";
  return section(2, "Process record — per-hour telemetry", "Frozen timestep summaries; shaded bands follow campaign boundaries.",
    `<div class="stepper" role="group" aria-label="Timestep inspector">` +
    `<div class="stepper-head"><div><div class="ct" id="stepper-heading">Timestep inspector</div>` +
    `<output id="step-output" for="stepper" aria-live="polite">Hour ${esc(firstHour)} · ${esc(firstCampaign)}</output></div>` +
    `<span class="status-pill" id="step-position" aria-hidden="true">1 / ${rows.length}</span></div>` +
    `<div class="stepper-controls">` +
    `<button type="button" id="step-prev" class="step-button" aria-label="Previous hour">‹</button>` +
    `<input id="stepper" type="range" min="0" max="${rows.length - 1}" value="0" step="1" aria-labelledby="stepper-heading" aria-valuemin="0" aria-valuemax="${rows.length - 1}" aria-valuenow="0" aria-valuetext="Hour ${esc(firstHour)} of ${rows.length}">` +
    `<button type="button" id="step-next" class="step-button" aria-label="Next hour">›</button></div>` +
    `<div class="range-labels"><span>h ${esc(firstHour)}</span><span>h ${esc(lastHour)}</span></div>` +
    `<div id="current-grid" class="current-grid" role="region" aria-label="Selected hour summary"></div>` +
    `<div class="timestep-ledger"><div class="ct" id="timestep-ledger-heading">Selected timestep ledger · mol-native</div><div id="timestep-ledger" role="region" aria-labelledby="timestep-ledger-heading"></div></div></div>` +
    `<div class="chart-grid">${charts.join("")}</div>` + carrierIdentity + carrierPressureNote);
}

function renderTimestepLedger(timestep) {
  if (!Object.prototype.hasOwnProperty.call(timestep, "ledger")) {
    return `<div class="pending timestep-ledger-pending">per-hour ledger not captured in this artifact (schema &lt; 0.2.0 or capture disabled)</div>`;
  }
  const ledger = timestep.ledger;
  if (!ledger || typeof ledger !== "object" || Array.isArray(ledger)) {
    return `<div class="pending timestep-ledger-pending">per-hour ledger captured, but the account map is malformed</div>`;
  }
  const accounts = Object.entries(ledger);
  if (!accounts.length) {
    return `<div class="note">Per-hour ledger captured, empty.</div>`;
  }
  const rows = accounts.map(([account, species]) => {
    if (!species || typeof species !== "object" || Array.isArray(species)) {
      return `<tr><td>${accountSpan(account)}</td><td colspan="2">captured, malformed species map</td></tr>`;
    }
    const entries = Object.entries(species);
    if (!entries.length) {
      return `<tr><td>${accountSpan(account)}</td><td colspan="2">captured, empty account</td></tr>`;
    }
    return entries.map(([name, value], index) => `<tr>${index === 0 ? `<td rowspan="${entries.length}">${accountSpan(account)}</td>` : ""}<td>${speciesSpan(name)}</td><td class="num">${strictMol(value)}</td></tr>`).join("");
  }).join("");
  return `<div class="table-wrap timestep-ledger-wrap"><table><thead><tr><th>Account</th><th>Species</th><th class="num">Amount · mol</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function ledgerSection(finalState) {
  if (finalState === undefined || finalState === null) {
    return section(4, "Full terminal ledger", "Every final_state account and species; no product projection or hidden filtering.",
      `<div class="pending"><strong>Not emitted</strong><p>terminal.final_state is absent from this artifact.</p></div>`);
  }
  if (typeof finalState !== "object" || Array.isArray(finalState)) {
    return section(4, "Full terminal ledger", "Every final_state account and species; no product projection or hidden filtering.",
      `<div class="pending"><strong>Malformed</strong><p>terminal.final_state is present but is not an account map.</p></div>`);
  }
  const accounts = Object.entries(finalState);
  if (!accounts.length) {
    return section(4, "Full terminal ledger", "Every final_state account and species; no product projection or hidden filtering.",
      `<div class="pending"><strong>Empty ledger</strong><p>terminal.final_state was emitted with no accounts.</p></div>`);
  }
  const rows = accounts.map(([account, species]) => {
    const entries = Object.entries(species || {});
    return `<tr><td>${accountSpan(account)}</td><td class="species-list">${entries.length ? entries.map(([name, value]) => `${speciesSpan(name)} ${strictMol(value)}`).join(" · ") : "empty"}</td><td class="num">${strictMolSum(entries.map(([, value]) => value))}</td></tr>`;
  }).join("");
  return section(4, "Full terminal ledger", "Every final_state account and species; no product projection or hidden filtering.", `<div class="table-wrap"><table><thead><tr><th>Account</th><th>Species · mol</th><th class="num">Account total · mol</th></tr></thead><tbody>${rows}</tbody></table></div><div class="note">mol-native ledger; kg conversion is a backend (W-A0) step. Account totals sum species amounts as stored — not feedstock-yield fractions.</div>`);
}

function dispositionRole(account) {
  if ([
    "process.condensation_train", "terminal.drain_tap_material",
    "terminal.chromium_condensed_oxide_stored", "terminal.oxygen_stage0_stored",
    "terminal.oxygen_mre_anode_stored", "terminal.oxygen_melt_offgas_stored",
    "terminal.oxygen_melt_offgas_captured", "reservoir.oxygen_cistern_liquid_inventory"
  ].includes(account)) return "products";
  if ([
    "process.cleaned_melt", "terminal.slag", "process.solid_char_carbon",
    "process.metal_phase", "process.metal_phase_bottom_pool", "process.metal_phase_float_layer",
    "process.raw_feedstock", "process.stage0_carbonate_feed", "process.stage0_perchlorate_feed",
    "process.stage0_salt_feed", "process.stage0_volatile_feed", "reservoir.fo2_buffer",
    "terminal.stage0_chloride_salt_phase", "terminal.stage0_salt_phase",
    "terminal.stage0_sulfide_matte", "terminal.stage0_residual_carbonate_carbon",
    "terminal.stage0_residual_refractory_carbon", "reservoir.stage0_oxidant",
    "reservoir.stage0_process_gas"
  ].includes(account)) return "retained";
  if (account === "terminal.offgas" || account === "terminal.oxygen_melt_offgas_vented_to_vacuum" ||
      account === "process.wall_deposit" || /^process\.wall_deposit_segment_/.test(account)) return "losses";
  if (account === "process.overhead_gas" || account === "process.condensation_retained_holdup") return "terminal_inventory";
  if ([
    "process.reagent_inventory", "process.spent_reductant_residue", "process.c7_al_credit",
    "terminal.oxygen_bubbler_external_vented_to_vacuum"
  ].includes(account) || /^reservoir\.reagent\./.test(account)) return "reagent_cycle";
  return "unclassified";
}

function accountDispositionSection(finalState) {
  const accounts = finalState && typeof finalState === "object" && !Array.isArray(finalState)
    ? Object.entries(finalState)
    : [];
  const grouped = new Map(DISPOSITION_GROUPS.map((group) => [group.key, []]));
  accounts.forEach(([account, species]) => grouped.get(dispositionRole(account)).push([account, species]));
  const content = DISPOSITION_GROUPS.map((group) => {
    const members = grouped.get(group.key);
    if (!members.length) return "";
    const groupValues = members.flatMap(([, species]) => Object.values(species && typeof species === "object" && !Array.isArray(species) ? species : {}));
    const rows = members.map(([account, species]) => {
      const entries = species && typeof species === "object" && !Array.isArray(species) ? Object.entries(species) : [];
      return `<tr><td>${accountSpan(account)}</td><td class="species-list">${entries.length ? entries.map(([name, value]) => `${speciesSpan(name)} ${strictMol(value)}`).join(" · ") : "empty or malformed"}</td><td class="num">${strictMolSum(entries.map(([, value]) => value))}</td></tr>`;
    }).join("");
    return `<div class="card disposition-group"><div class="ct">${esc(group.label)}</div><div class="cbig">${strictMolSum(groupValues)}</div><div class="table-wrap"><table><thead><tr><th>Account</th><th>Species · mol</th><th class="num">Account total · mol</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
  }).join("");
  const body = content || `<div class="pending"><strong>Not emitted</strong><p>No terminal final_state accounts were emitted.</p></div>`;
  return section(3, "Account disposition", "Account disposition — where terminal inventory sits at run end, grouped by role. Group totals sum species amounts (mol) within each account role; they are NOT atom balances and NOT per-species feedstock-yield fractions — those require origin resolution and arrive with yield_disposition (pending).", `<div class="cards disposition-groups">${body}</div><div class="note">Account disposition is account-level inventory, not feedstock origin or recovered yield.</div>`);
}

function campaignSection(artifact, spans) {
  const cards = spans.map((span) => {
    const steps = artifact.timesteps.slice(span.startIndex, span.endIndex + 1);
    const summaries = steps.map((step) => step.summary);
    const electrical = sumPresent(summaries.map((row) => row.energy_electrical_kWh));
    const evaporation = sumPresent(summaries.map((row) => row.energy_evaporation_thermal_kWh));
    const energy = hasNumber(electrical) && hasNumber(evaporation) ? electrical + evaporation : null;
    const final = summaries.at(-1);
    const temperatures = summaries.map((row) => row.T_C);
    const lowTemperature = temperatures.every(hasNumber) ? minPresent(temperatures) : null;
    const highTemperature = temperatures.every(hasNumber) ? maxPresent(temperatures) : null;
    return `<div class="card"><div class="ct">${esc(span.name)} · h ${esc(steps[0].hour)}–${esc(steps.at(-1).hour)}</div><div class="cbig">${steps.length} <small>hours</small></div>` +
      `<div class="kv"><span>Temperature range</span><b>${hasNumber(lowTemperature) && hasNumber(highTemperature) ? `${fmtNum(lowTemperature)}–${fmtNum(highTemperature)} °C` : "not emitted"}</b></div>` +
      `<div class="kv"><span>Electrical + evaporation thermal</span><b>${exactValue(energy, "kWh")}</b></div>` +
      `<div class="kv"><span>End pO₂</span><b>${exactValue(final.pO2_bar, "bar")}</b></div>` +
      `<div class="kv"><span>End regime</span><b>${esc(final.regime)}</b></div></div>`;
  }).join("");
  return section(5, "Campaign results", "Measured campaign spans and end-state signals from the timestep array.", `<div class="cards">${cards}</div>`);
}

function tapsAndPuritySection(terminal) {
  if (!Object.prototype.hasOwnProperty.call(terminal || {}, "stage_purity")) {
    return section(6, "Metal taps & stage purity", "Live backend masses, purity fraction, and verdict. An absent backend verdict is unavailable; trace is an annotation from total_kg.",
      `<div class="pending"><strong>Not emitted</strong><p>terminal.stage_purity is absent from this artifact.</p></div>`);
  }
  const purity = terminal.stage_purity;
  if (purity === null || typeof purity !== "object" || Array.isArray(purity)) {
    return section(6, "Metal taps & stage purity", "Live backend masses, purity fraction, and verdict. An absent backend verdict is unavailable; trace is an annotation from total_kg.",
      `<div class="pending"><strong>Malformed</strong><p>terminal.stage_purity is present but is not a stage map.</p></div>`);
  }
  const stages = Object.entries(purity);
  if (!stages.length) {
    return section(6, "Metal taps & stage purity", "Live backend masses, purity fraction, and verdict. An absent backend verdict is unavailable; trace is an annotation from total_kg.",
      `<div class="pending"><strong>Empty</strong><p>terminal.stage_purity was emitted with no stages.</p></div>`);
  }
  const hasActivity = stages.some(([, stage]) => stage.activity && typeof stage.activity === "object" && !Array.isArray(stage.activity) && Object.values(stage.activity).some((value) => typeof value === "boolean"));
  const stageRows = stages.map(([key, stage]) => {
    const backendVerdict = typeof stage.verdict === "string" && stage.verdict.trim() ? stage.verdict.trim().toUpperCase() : null;
    const verdict = backendVerdict ?? "UNAVAILABLE";
    const verdictClass = ["pure", "mixed", "contaminated"].includes(verdict.toLowerCase()) ? verdict.toLowerCase() : "unavailable";
    const trace = hasNumber(stage.total_kg) && Number(stage.total_kg) < .01 ? ` <span class="trace">· trace (&lt;0.01 kg total)</span>` : "";
    const activity = stage.activity && typeof stage.activity === "object" && !Array.isArray(stage.activity) ? stage.activity : null;
    const acceptedSpecies = stage.accepted_species || [];
    const speciesList = activity
      ? acceptedSpecies.map((species) => typeof activity[species] === "boolean" ? `${speciesSpan(species)} · ${activity[species] ? "ACTIVE" : "IDLE"}` : speciesSpan(species)).join("<br>") || "none designated"
      : acceptedSpecies.map(speciesSpan).join(" · ") || "none designated";
    const stageTitle = stage.label || key;
    const stageWarning = typeof stage.warning === "string" && stage.warning.trim()
      ? stage.warning.trim()
      : null;
    return `<tr><td><span title="${esc(key)}">${esc(stageTitle)}</span></td><td class="species-list">${speciesList}</td>` +
      `<td class="num">${exactKg(stage.total_kg)}${trace}</td><td class="num">${exactKg(stage.designated_kg)}</td><td class="num">${exactKg(stage.impurity_kg)}</td><td class="num">${exactValue(hasNumber(stage.purity_fraction) ? Number(stage.purity_fraction) * 100 : null, "%")}</td><td><span class="verdict ${verdictClass}">${esc(verdict)}</span>${stageWarning ? `<div class="stage-warning">${esc(stageWarning)}</div>` : ""}</td></tr>`;
  }).join("");
  return section(6, "Metal taps & stage purity", "Live backend masses, purity fraction, and verdict. An absent backend verdict is unavailable; trace is an annotation from total_kg. Hover a stage name for its raw stage key.",
    `<div class="table-wrap"><table><thead><tr><th>Stage</th><th>Accepted species</th><th class="num">Total</th><th class="num">Designated</th><th class="num">Impurity</th><th class="num">Purity</th><th>Backend verdict</th></tr></thead><tbody>${stageRows}</tbody></table></div>` +
    (hasActivity ? "" : pending("W-A10", "Per-species stage activity is not emitted, so intended-versus-contaminant activity is not inferred.")));
}

function wallAndOxygenSection(artifact, rows) {
  const terminal = artifact.terminal;
  const last = rows.at(-1) || {};
  const wallDeposits = last.wall_deposit_cumulative_kg;
  const wallSpecies = {};
  let wallComplete = wallDeposits !== undefined && wallDeposits !== null && typeof wallDeposits === "object";
  if (wallDeposits && typeof wallDeposits === "object") {
    Object.values(wallDeposits).forEach((segment) => Object.entries(segment || {}).forEach(([species, value]) => {
      if (hasNumber(value)) wallSpecies[species] = (wallSpecies[species] || 0) + Number(value);
      else wallComplete = false;
    }));
  }
  const wallTotal = wallComplete ? sumObject(wallSpecies) : null;
  const pumping = terminal.run_metadata?.cost_rollup_diagnostic?.pumping_diagnostic;
  const geometryNotice = terminal.run_metadata?.knudsen_regime_diagnostic?.stage_area_geometry_provenance_notice;
  const wallAuthorityNotice = geometryNotice?.status === "provisional"
    ? `<div class="meta-chips"><span class="chip accent">wall geometry ${esc(geometryNotice.status)}</span>` +
      `${geometryNotice.source_class ? `<span class="chip">source ${esc(geometryNotice.source_class)}</span>` : ""}` +
      `${geometryNotice.output_status ? `<span class="chip">output ${esc(geometryNotice.output_status)}</span>` : ""}</div>` +
      `${geometryNotice.message ? `<p class="sub">${esc(geometryNotice.message)}</p>` : ""}`
    : "";
  const pumpingEnergy = pumping?.status === "no_rows"
    ? "not computed — no rows"
    : exactValue(pumping?.pumping_electrical_kWh, "kWh");
  const o2 = sourceSideO2(last);
  const o2Label = prettyChemText(last.O2_metric_label || "O₂ metric label not emitted");
  const wall = `<div class="card"><div class="ct">Observed wall deposits · cumulative timestep series</div><div class="cbig">${exactKg(wallTotal)}</div>${wallAuthorityNotice}<div class="kv"><span>Species</span><b>${wallComplete ? Object.entries(wallSpecies).map(([key, value]) => `${speciesSpan(key)} ${exactKg(value)}`).join(" · ") || "none emitted" : "not emitted"}</b></div><div class="kv"><span>Current transport</span><b>${esc(last.regime)} · Kn ${exactValue(last.Kn && typeof last.Kn === "object" ? last.Kn.knudsen_number : last.Kn, "")}</b></div></div>`;
  // Human basis only — raw ledger field names stay out of the visible report surface.
  const oxygen = `<div class="card"><div class="ct">${esc(o2Label)}</div><div class="cbig">${exactKg(o2)}</div><div class="kv"><span>Basis</span><b>cumulative source-side potential · not recovered product</b></div><div class="kv"><span>Pumping energy</span><b>${pumpingEnergy}</b></div><div class="kv"><span>Pumping status</span><b>${esc(pumping?.status ?? "not emitted")}</b></div></div>`;
  return section(7, "Wall risk, oxygen & pumping", "Observed deposits and terminal diagnostics only; wall lifetime remains unassessed. O₂ figures use the artifact's source-side potential metric — not recovered yield.", `<div class="cards">${wall}${oxygen}</div>${pending("W-D4", "terminal.wall_lifetime is absent. Wall lifetime is not assessed; this viewer does not issue a CLEAR verdict.")}`);
}

function ceramicSection(terminal) {
  const finalState = terminal.final_state;
  const hasCleanedMelt = finalState && typeof finalState === "object" && !Array.isArray(finalState)
    && Object.prototype.hasOwnProperty.call(finalState, "process.cleaned_melt");
  if (!hasCleanedMelt) {
    return section(8, "Terminal ceramic — cleaned melt", "Composition binds directly to process.cleaned_melt; taxonomy is a separate backend-owned result.",
      `<div class="pending"><strong>Not emitted</strong><p>process.cleaned_melt is absent from terminal.final_state.</p></div>` +
      (terminal.terminal_product_taxonomy ? "" : pending("W-D7", "terminal.terminal_product_taxonomy is absent. No density, value-grade, use-class, or product label is fabricated.")));
  }
  const melt = finalState["process.cleaned_melt"];
  if (melt === null || typeof melt !== "object" || Array.isArray(melt)) {
    return section(8, "Terminal ceramic — cleaned melt", "Composition binds directly to process.cleaned_melt; taxonomy is a separate backend-owned result.",
      `<div class="pending"><strong>Malformed</strong><p>process.cleaned_melt is present but is not a species map.</p></div>`);
  }
  const entries = Object.entries(melt);
  if (!entries.length) {
    return section(8, "Terminal ceramic — cleaned melt", "Composition binds directly to process.cleaned_melt; taxonomy is a separate backend-owned result.",
      `<div class="pending"><strong>Empty account</strong><p>process.cleaned_melt was emitted with no species.</p></div>` +
      (terminal.terminal_product_taxonomy ? "" : pending("W-D7", "terminal.terminal_product_taxonomy is absent. No density, value-grade, use-class, or product label is fabricated.")));
  }
  const total = sumObject(melt);
  const rows = entries.sort((a, b) => (n(b[1]) ?? -Infinity) - (n(a[1]) ?? -Infinity)).map(([species, value]) => `<tr><td>${speciesSpan(species)}</td><td class="num">${exactMol(value)}</td><td class="num">${exactValue(hasNumber(value) && hasNumber(total) && total !== 0 ? Number(value) / total * 100 : null, "%")}</td></tr>`).join("");
  return section(8, "Terminal ceramic — cleaned melt", "Composition binds directly to process.cleaned_melt; taxonomy is a separate backend-owned result.", `<div class="table-wrap"><table><thead><tr><th>Oxide / species</th><th class="num">Amount · mol</th><th class="num">mol%</th></tr></thead><tbody>${rows}</tbody></table></div><div class="note">mol-native ledger; kg conversion is a backend (W-A0) step.</div>${terminal.terminal_product_taxonomy ? "" : pending("W-D7", "terminal.terminal_product_taxonomy is absent. No density, value-grade, use-class, or product label is fabricated.")}`);
}

function costSection(artifact, energy) {
  const prices = artifact.header.cost_block;
  if (!prices) {
    return section(9, "Energy & two-price cost", "Canonical prices come only from header.cost_block.",
      pending("W-A5a", "header.cost_block is absent. Energy cost cannot be calculated without backend-provided prices."));
  }
  const hasCostShare = hasNumber(energy.totalCost) && energy.totalCost !== 0 && hasNumber(energy.electricalCost);
  const electricalShare = hasCostShare ? energy.electricalCost / energy.totalCost * 100 : null;
  const provenance = typeof prices.provenance === "string" && prices.provenance.trim()
    ? `<div class="note"><b>Cost provenance:</b> ${esc(prices.provenance.trim())}</div>`
    : "";
  const pumpingRows = energy.canonicalCostTotals
    ? `<div class="kv"><span>Process electrical</span><b>${hasNumber(energy.processElectrical) ? `${exactValue(energy.processElectrical, "kWh")} · ${money(energy.processElectricalCost)}` : "not emitted"}</b></div><div class="kv"><span>Pumping</span><b>${hasNumber(energy.pumpingElectrical) ? `${exactValue(energy.pumpingElectrical, "kWh")} · ${money(energy.pumpingElectricalCost)}` : "excluded / not emitted"}</b></div>`
    : "";
  const basisNote = typeof energy.basisNote === "string" && energy.basisNote.trim()
    ? `<div class="note"><b>Cost basis:</b> ${esc(energy.basisNote.trim())}</div>`
    : "";
  const authority = priceAuthority(artifact);
  const authorityParts = [
    authority?.basis ? `basis <span class="mono">${esc(authority.basis)}</span>` : null,
    authority && authority.count !== null && authority.count > 0
      ? `${esc(fmtNum(authority.count))} placeholder price parameter${authority.count === 1 ? "" : "s"} awaiting owner ratification`
      : null,
    authority && authority.names.length
      ? `parameters ${authority.names.map((name) => `<span class="mono">${esc(name)}</span>`).join(", ")}`
      : null
  ].filter(Boolean);
  const authorityNote = authority
    ? `<div class="note price-authority"><b>Price authority:</b> ${authorityParts.join(" · ")}</div>`
    : "";
  const totalFormula = energy.canonicalCostTotals
    ? `<div class="note"><b>Total ${money(energy.totalCost)}</b> binds terminal.cost_totals: ${exactValue(energy.electrical, "kWh")} total electrical plus ${exactValue(energy.thermal, "kWh")} evaporation thermal. Latent (${exactValue(energy.latent, "kWh")}) and dissociation (${exactValue(energy.dissociation, "kWh")}) are the breakdown of evaporation thermal, not additional energy.</div>`
    : `<div class="note"><b>Total ${money(energy.totalCost)}</b> = ${exactValue(energy.electrical, "kWh")} × ${money(prices.electrical_cost_per_kWh)} + ${exactValue(energy.thermal, "kWh")} evaporation thermal × ${money(prices.solar_heat_cost_per_kWh)}. Latent (${exactValue(energy.latent, "kWh")}) and dissociation (${exactValue(energy.dissociation, "kWh")}) are the breakdown of evaporation thermal, not additional energy.</div>`;
  return section(9, "Energy & two-price cost", "Canonical prices come only from header.cost_block.",
    authorityNote + provenance + basisNote + `<div class="cards"><div class="card"><div class="ct">Electrical</div><div class="cbig">${exactValue(energy.electrical, "kWh")}</div>${pumpingRows}<div class="kv"><span>Price</span><b>${money(prices.electrical_cost_per_kWh)} / kWh</b></div><div class="kv"><span>Subtotal</span><b>${money(energy.electricalCost)}</b></div></div>` +
    `<div class="card"><div class="ct">Solar heat · evaporation thermal total</div><div class="cbig">${exactValue(energy.thermal, "kWh")}</div><div class="kv"><span>Latent breakdown</span><b>${exactValue(energy.latent, "kWh")}</b></div><div class="kv"><span>Dissociation breakdown</span><b>${exactValue(energy.dissociation, "kWh")}</b></div><div class="kv"><span>Price</span><b>${money(prices.solar_heat_cost_per_kWh)} / kWh</b></div><div class="kv"><span>Subtotal</span><b>${money(energy.thermalCost)}</b></div></div></div>` +
    `${hasCostShare ? `<div class="cost-stack" role="img" aria-label="${esc(`Cost share: ${fmtNum(electricalShare)}% electrical, ${fmtNum(100 - electricalShare)}% solar heat`)}"><span style="width:${electricalShare.toFixed(2)}%"></span><span style="width:${(100 - electricalShare).toFixed(2)}%"></span></div><div class="legend"><span><i class="swatch" style="background:var(--blue)" aria-hidden="true"></i>electrical cost</span><span><i class="swatch" style="background:var(--green)" aria-hidden="true"></i>solar-heat cost</span></div>` : pending("energy values", "Cost share is unavailable because one or more energy or price values were not emitted.")}` +
    totalFormula);
}

function provenanceSection(artifact) {
  const meta = artifact.terminal.run_metadata || {};
  const closure = artifact.terminal.mass_balance_closure || {};
  const confidence = artifact.terminal.confidence;
  const confidenceGrade = typeof confidence?.grade === "string" && confidence.grade.trim()
    ? confidence.grade.trim()
    : null;
  const confidenceClass = ({ high: "pure", medium: "mixed", low: "contaminated" })[confidenceGrade?.toLowerCase()] || "unavailable";
  const confidenceReasons = Array.isArray(confidence?.reasons) ? confidence.reasons : [];
  const confidenceContent = confidence && typeof confidence === "object"
    ? `<div class="note"><b>Confidence:</b> <span class="verdict ${confidenceClass}">${esc(confidenceGrade || "grade not emitted")}</span>` +
      (confidenceReasons.length
        ? `<ul>${confidenceReasons.map((reason) => `<li>${esc(reason)}</li>`).join("")}</ul>`
        : `<p>Confidence reasons not emitted.</p>`) +
      `</div>`
    : pending("confidence", "Confidence not emitted (requires mass-balance evidence).");
  const hoursRequested = meta.hours_requested === undefined || meta.hours_requested === null ? "not emitted" : meta.hours_requested;
  const hoursCompleted = meta.hours_completed === undefined || meta.hours_completed === null ? "not emitted" : meta.hours_completed;
  const facts = [
    ["Artifact schema", artifact.artifact_schema_version], ["Runner schema", meta.schema_version],
    ["Backend evidence", meta.evidence_class], ["Backend authoritative", meta.backend_authoritative],
    ["Certification allowed", meta.certification_allowed], ["Hours requested / completed", `${hoursRequested} / ${hoursCompleted}`],
    // Concatenation coerces before esc() can guard, so scalar-check the basis here.
    ["Mass-balance residual", hasNumber(closure.residual_pct ?? closure.residual)
      ? `${fmtNum(closure.residual_pct ?? closure.residual, "%")} · ${scalarText(closure.basis || "basis not emitted")}`
      : "not emitted"]
  ];
  const identityFacts = [
    ["Kernel commit", artifact.header.engine_identity?.kernel_commit_sha],
    ["Engine cache version", artifact.header.engine_identity?.cache_version]
  ];
  return section(10, "Provenance & confidence", "Status-bearing metadata preserved from the frozen artifact.", `<div class="table-wrap"><table><tbody>${facts.map(([key, value]) => `<tr><th>${esc(key)}</th><td class="mono">${esc(value)}</td></tr>`).join("")}${identityFacts.map(([key, value]) => `<tr><th>${esc(key)}</th><td class="mono">${value == null ? "not emitted" : identitySpan(value)}</td></tr>`).join("")}</tbody></table></div>${confidenceContent}`);
}

// Blind-fire panel registry: panels live in their OWN module files under panels/ and
// self-register by pushing onto globalThis.ReportPanels — no worker edits this file (see
// docs-private blind-fire-prompts/_PREAMBLE.md §3 section-ownership). The controller owns
// the panel <script> tags in index.html; tag order = render order = panel number.
// Entry shape: { id: "sec-p<N>-<slug>", render(artifact, rows, spans, energy),
// onTimestep(artifact, index)? }. Panels render between the ceramic and cost sections; a
// throwing panel is contained to its own section so it cannot take down the report, and
// onTimestep lets a panel update as the stepper scrubs without touching renderCurrent.
function panelRegistry() {
  return Array.isArray(globalThis.ReportPanels) ? globalThis.ReportPanels : [];
}

function panelSectionsHtml(artifact, rows, spans, energy) {
  return panelRegistry().map((panel) => {
    try {
      return panel.render(artifact, rows, spans, energy);
    } catch (error) {
      return `<section class="card" id="${esc(panel.id)}"><h2>${esc(panel.id)}</h2><div class="pending"><strong>Panel failed to render</strong><p class="mono">${esc(error && error.message ? error.message : String(error))}</p></div></section>`;
    }
  }).join("");
}

function renderCurrent(artifact, index) {
  const timestep = artifact.timesteps[index];
  const row = timestep.summary;
  const hour = timestep.hour === undefined || timestep.hour === null ? "not emitted" : scalarText(timestep.hour);
  const count = artifact.timesteps.length;
  const stepOutput = $("#step-output");
  if (stepOutput) stepOutput.textContent = `Hour ${hour} · ${scalarText(row.campaign ?? "campaign not emitted")}`;
  const pill = $("#step-position") || $("#step-pill") || $(".status-pill");
  if (pill) pill.textContent = `${index + 1} / ${count}`;
  const stepper = $("#stepper");
  if (stepper) {
    stepper.value = String(index);
    stepper.setAttribute("aria-valuenow", String(index));
    stepper.setAttribute("aria-valuetext", `Hour ${hour}, step ${index + 1} of ${count}`);
  }
  const prev = $("#step-prev");
  const next = $("#step-next");
  if (prev) prev.disabled = index <= 0;
  if (next) next.disabled = index >= count - 1;
  const grid = $("#current-grid");
  if (grid) {
    grid.innerHTML = [
      ["Temperature", fmtNum(row.T_C, "°C")], ["Total pressure", fmtNum(row.P_total_bar, "bar")],
      ["pO₂", fmtNum(row.pO2_bar, "bar")], ["Carrier pressure", fmtNum(row.p_carrier_bar, "bar")],
      ["Carrier identity", typeof row.carrier_identity === "string" && row.carrier_identity.trim() ? row.carrier_identity.trim() : "not emitted"], ["Electrical", fmtNum(row.energy_electrical_kWh, "kWh")],
      ["Evaporation thermal", fmtNum(row.energy_evaporation_thermal_kWh, "kWh")], [prettyChemText(row.O2_metric_label || "O₂ metric label not emitted"), kg(sourceSideO2(row))],
      ["Regime", row.regime], ["Kn", row.Kn == null ? "not emitted" : row.Kn && typeof row.Kn === "object" ? sci(row.Kn.knudsen_number) : sci(row.Kn)]
    ].map(([key, value]) => `<div class="current"><div class="k">${esc(key)}</div><div class="v">${esc(value)}</div></div>`).join("");
  }
  const ledger = $("#timestep-ledger");
  if (ledger) ledger.innerHTML = renderTimestepLedger(timestep);
  updateMarkers(index, count);
  panelRegistry().forEach((panel) => {
    if (typeof panel.onTimestep !== "function") return;
    try {
      panel.onTimestep(artifact, index);
    } catch (error) {
      console.error(`panel ${panel.id} onTimestep failed:`, error);
    }
  });
}

function render(artifact) {
  if (!artifact || !Array.isArray(artifact.timesteps) || !artifact.header || !artifact.terminal) {
    throw new Error("Artifact is missing the required header, timesteps, or terminal envelope fields.");
  }
  const schemaMajor = Number.parseInt(String(artifact.artifact_schema_version).split(".")[0], 10);
  if (!Number.isInteger(schemaMajor) || schemaMajor !== SUPPORTED_ARTIFACT_SCHEMA_MAJOR) {
    throw new Error(`Unsupported artifact schema ${artifact.artifact_schema_version ?? "(missing)"}; this viewer supports major ${SUPPORTED_ARTIFACT_SCHEMA_MAJOR}.`);
  }
  const rows = artifact.timesteps.map((timestep) => timestep.summary);
  const spans = campaignSpans(artifact.timesteps);
  const energy = {
    electrical: sumPresent(rows.map((row) => row.energy_electrical_kWh)),
    evaporation: sumPresent(rows.map((row) => row.energy_evaporation_thermal_kWh)),
    latent: sumPresent(rows.map((row) => row.energy_latent_kWh)),
    dissociation: sumPresent(rows.map((row) => row.energy_dissociation_kWh))
  };
  energy.thermal = energy.evaporation;
  const canonicalCostTotals = artifact.terminal.cost_totals && typeof artifact.terminal.cost_totals === "object" && !Array.isArray(artifact.terminal.cost_totals)
    ? artifact.terminal.cost_totals
    : null;
  if (canonicalCostTotals) {
    energy.canonicalCostTotals = true;
    energy.processElectrical = n(canonicalCostTotals.process_electrical_energy_kWh);
    energy.pumpingElectrical = n(canonicalCostTotals.pumping_electrical_energy_kWh);
    energy.electrical = n(canonicalCostTotals.electrical_energy_kWh);
    energy.thermal = n(canonicalCostTotals.evaporation_thermal_energy_kWh);
    energy.processElectricalCost = n(canonicalCostTotals.process_electrical_cost_usd);
    energy.pumpingElectricalCost = n(canonicalCostTotals.pumping_electrical_cost_usd);
    energy.electricalCost = n(canonicalCostTotals.electrical_cost_usd);
    energy.thermalCost = n(canonicalCostTotals.solar_heat_cost_usd);
    energy.totalCost = n(canonicalCostTotals.total_cost_usd);
    energy.basisNote = canonicalCostTotals.basis_note;
  } else {
    energy.canonicalCostTotals = false;
    energy.electricalCost = hasNumber(energy.electrical) && hasNumber(artifact.header.cost_block?.electrical_cost_per_kWh) ? energy.electrical * Number(artifact.header.cost_block.electrical_cost_per_kWh) : null;
    energy.thermalCost = hasNumber(energy.thermal) && hasNumber(artifact.header.cost_block?.solar_heat_cost_per_kWh) ? energy.thermal * Number(artifact.header.cost_block.solar_heat_cost_per_kWh) : null;
    energy.totalCost = hasNumber(energy.electricalCost) && hasNumber(energy.thermalCost) ? energy.electricalCost + energy.thermalCost : null;
  }
  // Order: yields → process inspector → account disposition → full ledger →
  // campaign spans → taps → wall/O₂ → ceramic → cost → provenance. Section
  // numbers match this visual order (no duplicate / out-of-order sect labels).
  const timestepSections = rows.length
    ? yieldsSection(rows, artifact.terminal) + processSection(artifact, rows, spans)
    : section(1, "Evolved metal mass — Ellingham order", "No timestep rows were emitted for this run.", `<div class="pending"><strong>Not emitted</strong><p>Evolved metal mass is unavailable because this execution has zero timesteps.</p></div>`) +
      section(2, "Process record — per-hour telemetry", "No timestep rows were emitted for this run.", `<div class="pending"><strong>Not emitted</strong><p>This execution has zero timesteps; header, failure, and terminal data remain available below.</p></div>`);
  const campaignBlock = rows.length
    ? campaignSection(artifact, spans)
    : section(5, "Campaign results", "No timestep rows were emitted for this run.", `<div class="pending"><strong>Not emitted</strong><p>Campaign spans and end-state signals are unavailable because this execution has zero timesteps.</p></div>`);
  document.title = `${runTitle(artifact.header)} · Run report`;
  $("#report").innerHTML = makeHeader(artifact, rows, energy) + timestepSections +
    accountDispositionSection(artifact.terminal.final_state) +
    ledgerSection(artifact.terminal.final_state) +
    campaignBlock +
    tapsAndPuritySection(artifact.terminal) + wallAndOxygenSection(artifact, rows) + ceramicSection(artifact.terminal) +
    panelSectionsHtml(artifact, rows, spans, energy) +
    costSection(artifact, energy) + provenanceSection(artifact) +
    `<footer class="footer"><span>Frozen flatfile report · engine-free · artifact-only rendering</span><span class="footer-links"><a href="./library.html">Run library</a><a href="./settings.html${RUN_QUERY}">Captured settings</a></span><span class="mono">${runIdSpan(artifact.header.run_id)}</span></footer>`;
  if (rows.length) {
    const stepper = $("#stepper");
    const go = (index) => {
      const clamped = Math.max(0, Math.min(rows.length - 1, Number(index) || 0));
      renderCurrent(artifact, clamped);
    };
    stepper.addEventListener("input", () => go(stepper.value));
    stepper.addEventListener("change", () => go(stepper.value));
    $("#step-prev")?.addEventListener("click", () => go(Number(stepper.value) - 1));
    $("#step-next")?.addEventListener("click", () => go(Number(stepper.value) + 1));
    // Arrow keys when the inspector (or its controls) has focus.
    $(".stepper")?.addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
        event.preventDefault();
        go(Number(stepper.value) - 1);
      } else if (event.key === "ArrowRight" || event.key === "ArrowDown") {
        event.preventDefault();
        go(Number(stepper.value) + 1);
      } else if (event.key === "Home") {
        event.preventDefault();
        go(0);
      } else if (event.key === "End") {
        event.preventDefault();
        go(rows.length - 1);
      }
    });
    renderCurrent(artifact, 0);
  }
}

function artifactFetchError(response) {
  if (response.status === 404) {
    return RUN_ID
      ? `No run artifact was found for run id “${RUN_ID}”. Check the library for a valid id.`
      : "The sample run artifact was not found on this server.";
  }
  return `Artifact request failed (${response.status})`;
}

fetch(ARTIFACT_URL)
  .then(async (response) => {
    if (!response.ok) throw new Error(artifactFetchError(response));
    try {
      return await response.json();
    } catch (_error) {
      throw new Error("Artifact response was not valid JSON (corrupt or truncated payload).");
    }
  })
  .then(render)
  .catch((error) => {
    const hint = RUN_ID
      ? `<p>Open the <a href="./library.html">run library</a> to pick another run, or check that the API is serving this artifact.</p>`
      : `<p>If your browser blocks local <code>file:</code> fetches, serve this directory with any offline local static server and open <code>index.html</code> there.</p>`;
    $("#report").innerHTML = `<div class="fatal"><div class="eyebrow">Report unavailable</div><h1>Could not read the frozen artifact</h1><p>${esc(error.message)}</p>${hint}</div>`;
  });
