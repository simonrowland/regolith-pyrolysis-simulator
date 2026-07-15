"use strict";

const RUN_ID = new URLSearchParams(window.location.search).get("run");
const RUN_QUERY = RUN_ID ? `?run=${encodeURIComponent(RUN_ID)}` : "";
const ARTIFACT_URL = RUN_ID
  ? `/api/runs/${encodeURIComponent(RUN_ID)}`
  : "./sample-run-artifact.json";
const SUPPORTED_ARTIFACT_SCHEMA_MAJOR = 0;
const ELLINGHAM_ORDER = ["Na", "K", "Fe", "Cr", "Mn", "Mg", "Si", "Al", "Ti", "Ca"];
const COLORS = ["#e8940f", "#1f7798", "#468466", "#8b63a6", "#a95c43", "#6f8c9e"];

const $ = (selector, root = document) => root.querySelector(selector);
const esc = (value) => String(value ?? "—").replace(/[&<>'"]/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[c]));
const hasNumber = (value) => value !== null && value !== "" && Number.isFinite(Number(value));
const n = (value) => hasNumber(value) ? Number(value) : null;
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
const kg = (value, digits = 3) => hasNumber(value)
  ? `${Number(value).toLocaleString(undefined, { maximumFractionDigits: digits })} kg`
  : "not emitted";
const exactValue = (value, unit) => hasNumber(value)
  ? `<span title="${esc(`${String(value)} ${unit}`)}">${Number(value).toLocaleString(undefined, { maximumSignificantDigits: 3 })} ${unit}</span>`
  : "not emitted";
const exactKg = (value) => exactValue(value, "kg");
const exactMol = (value) => exactValue(value, "mol");
const money = (value) => hasNumber(value)
  ? Number(value).toLocaleString(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 2 })
  : "not emitted";
const sci = (value) => hasNumber(value) ? (Number(value) === 0 ? "0" : Number(value).toExponential(3)) : "not emitted";

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
    return `<rect class="campaign-band" x="${start}" y="${pad.top}" width="${Math.max(1, end - start)}" height="${height - pad.top - pad.bottom}" style="opacity:${index % 2 ? .065 : .025}"/>`;
  }).join("");
  const legend = series.map((item, index) => `<span><i class="swatch" style="background:${item.color || COLORS[index]}"></i>${esc(item.label)}</span>`).join("");
  return `<div class="chartbox"><div class="chart-title">${esc(title)}</div><div class="legend">${legend}</div>` +
    `<svg id="${esc(id)}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(title)}">${bands}` +
    `<line class="axis" x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}"/>` +
    `<line class="axis" x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}"/>${paths}` +
    `<line class="marker" data-marker x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}"/>` +
    `<text class="chart-label" x="${pad.left}" y="${height - 7}">h 1</text><text class="chart-label" text-anchor="end" x="${width - pad.right}" y="${height - 7}">h ${pointCount}</text>` +
    `<text class="chart-label" x="3" y="${pad.top + 4}">${esc(options.maxLabel || max.toPrecision(3))}</text>` +
    `<text class="chart-label" x="3" y="${height - pad.bottom}">${esc(options.minLabel || (options.log ? `10^${min.toFixed(1)}` : min.toPrecision(3)))}</text></svg></div>`;
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
  const o2 = finalRow.O2_source_side_potential_kg_cumulative ?? null;
  const o2Label = finalRow.O2_metric_label || "O₂ metric label not emitted";
  const temperatures = rows.map((row) => row.T_C);
  const peakTemperature = temperatures.length && temperatures.every(hasNumber) ? maxPresent(temperatures) : null;
  const reportedEnergy = hasNumber(energy.electrical) && hasNumber(energy.evaporation) ? energy.electrical + energy.evaporation : null;
  const campaignChain = Array.isArray(header.campaign_chain) ? header.campaign_chain.join("→") || "—" : "—";
  const status = artifact.execution_status;
  const failureText = [artifact.failure?.reason, artifact.failure?.error_message].filter(Boolean).join(" · ") || "No failure reason or error message was emitted in this artifact.";
  const costProvenance = typeof header.cost_block?._provenance === "string" && header.cost_block._provenance.trim()
    ? header.cost_block._provenance.trim()
    : null;
  return `<header>
    <div class="masthead">
      <svg class="mark" viewBox="0 0 42 42" aria-hidden="true"><circle cx="16" cy="27" r="11" fill="none" stroke="currentColor" stroke-width="1.4"/><ellipse cx="16" cy="27" rx="4.8" ry="11" fill="none" stroke="currentColor"/><path d="M6 23q10-4 20 0M6 31q10 4 20 0M29 7l-4 8 8 4 5-2M25 15l-6 2-3-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="30" cy="5" r="2.6" fill="currentColor"/></svg>
      <div class="brand"><strong>DIRECT LEAP</strong> TECHNOLOGIES</div>
      <div class="doc-label">Run report<br><span class="mono">${esc(header.run_id)}</span></div>
    </div>
    <h1>${esc(header.name)}</h1>
    <p class="lede"><b>${hasNumber(peakTemperature) ? `${peakTemperature.toLocaleString()} °C` : "not emitted"} peak</b> · ${esc(header.feedstock_id)} · <b>${rows.length} hours</b> · ${esc(campaignChain)}</p>
    <div class="meta-chips">
      <span class="chip">charge ${kg(header.charge_mass_kg, 0)}</span>
      <span class="chip">engine ${esc(header.engine_identity?.name)}</span>
      <span class="chip">schema ${esc(artifact.artifact_schema_version)}</span>
      <span class="chip accent">C3 dose ${Object.entries(header.c3_dose || {}).map(([key, value]) => `${esc(key)} ${esc(value)} kg`).join(" · ")}</span>
    </div>
    <div class="status-banner ${["failed", "refused"].includes(status) ? "failed" : ""}">
      <div class="status-icon">${status === "ok" ? "✓" : "!"}</div><div><strong>Execution status: ${esc(status)}</strong>
      <p>Lifecycle: ${esc(artifact.lifecycle)}. ${esc(failureText)}</p></div>
    </div>
    <div class="glance">
      <div class="metric"><div class="k">Fe evolved</div><div class="v">${kg(finalMetal.Fe, 2)}</div></div>
      <div class="metric"><div class="k">${esc(o2Label)}</div><div class="v">${exactKg(o2)}</div></div>
      <div class="metric"><div class="k">Reported energy</div><div class="v">${hasNumber(reportedEnergy) ? `${Number(reportedEnergy).toFixed(1)} <small>kWh electrical + evaporation thermal</small>` : "not emitted"}</div></div>
      <div class="metric"><div class="k">Two-price energy cost</div><div class="v">${header.cost_block ? money(energy.totalCost) : "pending W-A5a"}${costProvenance ? `<small>${esc(costProvenance)}</small>` : ""}</div></div>
    </div>
  </header>`;
}

function yieldsSection(rows, terminal) {
  const evolved = rows.at(-1).metal_yields_kg || {};
  const max = Math.max(maxPresent(Object.values(evolved)) ?? 0, 1);
  const chips = ELLINGHAM_ORDER.map((element) => `<div class="yield-chip"><div class="el">${element}</div><div class="kg">${exactKg(evolved[element])} evolved</div><div class="bar"><i style="width:${Math.sqrt((n(evolved[element]) ?? 0) / max) * 100}%"></i></div></div>`).join("");
  const gap = terminal.yield_disposition ? "" : pending("W-A0 / W-A1", "Atom-basis available mass, fraction, and denominator are not emitted. Exact evolved kg is shown; no yield percentage is invented.");
  return section(1, "Extraction yields — Ellingham order", "Exact evolved mass from the final hourly metal_yields_kg row.", `<div class="yield-track">${chips}</div>${gap}`);
}

function processSection(artifact, rows, spans) {
  const temperature = rows.map((row) => n(row.T_C));
  const pressure = rows.map((row) => n(row.pO2_bar));
  const electrical = totalSeries(artifact.timesteps, "energy_electrical_kWh");
  const thermal = totalSeries(artifact.timesteps, "energy_evaporation_thermal_kWh");
  const vaporKeys = [...new Set(rows.flatMap((row) => Object.keys(row.vapor_species_kg_hr || {})))];
  const topVapors = vaporKeys.map((key) => ({ key, peak: maxPresent(rows.map((row) => row.vapor_species_kg_hr?.[key])) ?? 0 })).sort((a, b) => b.peak - a.peak).slice(0, 4);
  const charts = [
    lineChart("temperature-chart", "Melt temperature · °C", [{ label: "T °C", values: temperature, color: COLORS[0] }], { spans, maxLabel: `${maxPresent(temperature)?.toLocaleString() ?? "not emitted"} °C` }),
    lineChart("pressure-chart", "O₂ partial pressure · bar (log scale)", [{ label: "pO₂ bar", values: pressure, color: COLORS[1] }], { log: true, spans, maxLabel: hasNumber(maxPresent(pressure)) ? `${maxPresent(pressure).toExponential(1)} bar` : "not emitted" }),
    lineChart("energy-chart", "Cumulative energy · kWh", [{ label: "electrical", values: electrical, color: COLORS[1] }, { label: "thermal: evaporation total (latent + dissociation breakdown)", values: thermal, color: COLORS[2] }], { zero: true, spans }),
    lineChart("vapor-chart", "Vapor species surges · kg/h", topVapors.map((item, index) => ({ label: item.key, values: rows.map((row) => n(row.vapor_species_kg_hr?.[item.key])), color: COLORS[index] })), { zero: true, spans })
  ].join("");
  return section(2, "Process record — per-hour telemetry", "Frozen timestep summaries; shaded bands follow campaign boundaries.",
    `<div class="stepper"><div class="stepper-head"><div><div class="ct">Timestep inspector</div><output id="step-output">Hour 1 · ${esc(rows[0].campaign)}</output></div><span class="status-pill">1 / ${rows.length}</span></div>` +
    `<input id="stepper" type="range" min="0" max="${rows.length - 1}" value="0" step="1" aria-label="Report hour"><div class="range-labels"><span>h ${esc(artifact.timesteps[0].hour)}</span><span>h ${esc(artifact.timesteps.at(-1).hour)}</span></div><div id="current-grid" class="current-grid"></div></div>` +
    `<div class="chart-grid">${charts}</div>` +
    pending("W-A0", "summary.p_non_O2_bar and carrier_identity are absent. P_total − pO₂ is not used as a substitute, so neutral-sweep pressure is not charted."));
}

function ledgerSection(finalState) {
  const rows = Object.entries(finalState || {}).map(([account, species]) => {
    const entries = Object.entries(species || {});
    return `<tr><td class="mono">${esc(account)}</td><td class="species-list">${entries.length ? entries.map(([name, value]) => `${esc(name)} ${exactMol(value)}`).join(" · ") : "empty"}</td><td class="num">${exactMol(sum(entries.map(([, value]) => value)))}</td></tr>`;
  }).join("");
  return section(3, "Full terminal ledger", "Every final_state account and species; no product projection or hidden filtering.", `<div class="table-wrap"><table><thead><tr><th>Account</th><th>Species · mol</th><th class="num">Account total · mol</th></tr></thead><tbody>${rows}</tbody></table></div><div class="note">mol-native ledger; kg conversion is a backend (W-A0) step.</div>`);
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
      `<div class="kv"><span>Temperature range</span><b>${hasNumber(lowTemperature) && hasNumber(highTemperature) ? `${lowTemperature}–${highTemperature} °C` : "not emitted"}</b></div>` +
      `<div class="kv"><span>Electrical + evaporation thermal</span><b>${hasNumber(energy) ? `${energy.toFixed(3)} kWh` : "not emitted"}</b></div>` +
      `<div class="kv"><span>End pO₂</span><b>${hasNumber(final.pO2_bar) ? `${Number(final.pO2_bar).toExponential(3)} bar` : "not emitted"}</b></div>` +
      `<div class="kv"><span>End regime</span><b>${esc(final.regime)}</b></div></div>`;
  }).join("");
  return section(4, "Campaign results", "Measured campaign spans and end-state signals from the timestep array.", `<div class="cards">${cards}</div>`);
}

function tapsAndPuritySection(terminal) {
  const stageRows = Object.entries(terminal.stage_purity || {}).map(([key, stage]) => {
    const backendVerdict = typeof stage.verdict === "string" && stage.verdict.trim() ? stage.verdict.trim().toUpperCase() : null;
    const verdict = backendVerdict ?? "UNAVAILABLE";
    const verdictClass = ["pure", "mixed", "contaminated"].includes(verdict.toLowerCase()) ? verdict.toLowerCase() : "unavailable";
    const trace = hasNumber(stage.total_kg) && Number(stage.total_kg) < .01 ? ` <span class="trace">· trace (&lt;0.01 kg total)</span>` : "";
    return `<tr><td>${esc(stage.label || key)}<br><span class="trace mono">${esc(key)}</span></td><td class="species-list">${esc((stage.accepted_species || []).join(" · ") || "none designated")}</td>` +
      `<td class="num">${exactKg(stage.total_kg)}${trace}</td><td class="num">${exactKg(stage.designated_kg)}</td><td class="num">${exactKg(stage.impurity_kg)}</td><td class="num">${hasNumber(stage.purity_fraction) ? `${(Number(stage.purity_fraction) * 100).toFixed(4)}%` : "not emitted"}</td><td><span class="verdict ${verdictClass}">${esc(verdict)}</span></td></tr>`;
  }).join("");
  return section(5, "Metal taps & stage purity", "Live backend masses, purity fraction, and verdict. An absent backend verdict is unavailable; trace is an annotation from total_kg.",
    `<div class="table-wrap"><table><thead><tr><th>Stage</th><th>Accepted species</th><th class="num">Total</th><th class="num">Designated</th><th class="num">Impurity</th><th class="num">Purity</th><th>Backend verdict</th></tr></thead><tbody>${stageRows}</tbody></table></div>` +
    pending("W-A10", "Per-species stage activity is not emitted, so intended-versus-contaminant activity is not inferred."));
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
  const o2 = last.O2_source_side_potential_kg_cumulative ?? null;
  const o2Label = last.O2_metric_label || "O₂ metric label not emitted";
  const wall = `<div class="card"><div class="ct">Observed wall deposits · cumulative timestep series</div><div class="cbig">${exactKg(wallTotal)}</div><div class="kv"><span>Species</span><b class="mono">${wallComplete ? Object.entries(wallSpecies).map(([key, value]) => `${esc(key)} ${esc(sci(value))}`).join(" · ") || "none emitted" : "not emitted"}</b></div><div class="kv"><span>Current transport</span><b>${esc(last.regime)} · Kn ${sci(last.Kn && typeof last.Kn === "object" ? last.Kn.knudsen_number : last.Kn)}</b></div></div>`;
  const oxygen = `<div class="card"><div class="ct">${esc(o2Label)}</div><div class="cbig">${exactKg(o2)}</div><div class="kv"><span>Metric field</span><b>O2_source_side_potential_kg_cumulative</b></div><div class="kv"><span>Pumping energy</span><b>${pumping && hasNumber(pumping.pumping_electrical_kWh) ? `${Number(pumping.pumping_electrical_kWh).toFixed(6)} kWh` : "not emitted"}</b></div><div class="kv"><span>Pumping status</span><b>${esc(pumping?.status ?? "not emitted")}</b></div></div>`;
  return section(6, "Wall risk, oxygen & pumping", "Observed deposits and terminal diagnostics only; wall lifetime remains unassessed.", `<div class="cards">${wall}${oxygen}</div>${pending("W-D4", "terminal.wall_lifetime is absent. Wall lifetime is not assessed; this viewer does not issue a CLEAR verdict.")}`);
}

function ceramicSection(terminal) {
  const melt = terminal.final_state?.["process.cleaned_melt"] || {};
  const total = sumObject(melt);
  const rows = Object.entries(melt).sort((a, b) => (n(b[1]) ?? -Infinity) - (n(a[1]) ?? -Infinity)).map(([species, value]) => `<tr><td class="mono">${esc(species)}</td><td class="num">${exactMol(value)}</td><td class="num">${hasNumber(value) && hasNumber(total) && total !== 0 ? `${(Number(value) / total * 100).toFixed(4)}%` : "not emitted"}</td></tr>`).join("");
  return section(7, "Terminal ceramic — cleaned melt", "Composition binds directly to process.cleaned_melt; taxonomy is a separate backend-owned result.", `<div class="table-wrap"><table><thead><tr><th>Oxide / species</th><th class="num">Amount · mol</th><th class="num">mol%</th></tr></thead><tbody>${rows}</tbody></table></div><div class="note">mol-native ledger; kg conversion is a backend (W-A0) step.</div>${terminal.terminal_product_taxonomy ? "" : pending("W-D7", "terminal.terminal_product_taxonomy is absent. No density, value-grade, use-class, or product label is fabricated.")}`);
}

function costSection(artifact, energy) {
  const prices = artifact.header.cost_block;
  if (!prices) {
    return section(8, "Energy & two-price cost", "Canonical prices come only from header.cost_block.",
      pending("W-A5a", "header.cost_block is absent. Energy cost cannot be calculated without backend-provided prices."));
  }
  const hasCostShare = hasNumber(energy.totalCost) && energy.totalCost !== 0;
  const electricalShare = hasCostShare ? energy.electricalCost / energy.totalCost * 100 : null;
  const provenance = typeof prices._provenance === "string" && prices._provenance.trim()
    ? `<div class="note"><b>Cost provenance:</b> ${esc(prices._provenance.trim())}</div>`
    : "";
  return section(8, "Energy & two-price cost", "Canonical prices come only from header.cost_block.",
    provenance + `<div class="cards"><div class="card"><div class="ct">Electrical</div><div class="cbig">${hasNumber(energy.electrical) ? `${energy.electrical.toFixed(6)} <small>kWh</small>` : "not emitted"}</div><div class="kv"><span>Price</span><b>${money(prices.electrical_cost_per_kWh)} / kWh</b></div><div class="kv"><span>Subtotal</span><b>${money(energy.electricalCost)}</b></div></div>` +
    `<div class="card"><div class="ct">Solar heat · evaporation thermal total</div><div class="cbig">${hasNumber(energy.thermal) ? `${energy.thermal.toFixed(6)} <small>kWh</small>` : "not emitted"}</div><div class="kv"><span>Latent breakdown</span><b>${hasNumber(energy.latent) ? `${energy.latent.toFixed(6)} kWh` : "not emitted"}</b></div><div class="kv"><span>Dissociation breakdown</span><b>${hasNumber(energy.dissociation) ? `${energy.dissociation.toFixed(6)} kWh` : "not emitted"}</b></div><div class="kv"><span>Price</span><b>${money(prices.solar_heat_cost_per_kWh)} / kWh</b></div><div class="kv"><span>Subtotal</span><b>${money(energy.thermalCost)}</b></div></div></div>` +
    `${hasCostShare ? `<div class="cost-stack" aria-label="Cost share"><span style="width:${electricalShare}%"></span><span style="width:${100 - electricalShare}%"></span></div><div class="legend"><span><i class="swatch" style="background:var(--blue)"></i>electrical cost</span><span><i class="swatch" style="background:var(--green)"></i>solar-heat cost</span></div>` : pending("energy values", "Cost share is unavailable because one or more energy or price values were not emitted.")}` +
    `<div class="note"><b>Total ${money(energy.totalCost)}</b> = ${hasNumber(energy.electrical) ? energy.electrical.toFixed(6) : "not emitted"} kWh × ${money(prices.electrical_cost_per_kWh)} + ${hasNumber(energy.thermal) ? energy.thermal.toFixed(6) : "not emitted"} kWh evaporation thermal × ${money(prices.solar_heat_cost_per_kWh)}. Latent (${hasNumber(energy.latent) ? energy.latent.toFixed(6) : "not emitted"} kWh) and dissociation (${hasNumber(energy.dissociation) ? energy.dissociation.toFixed(6) : "not emitted"} kWh) are the breakdown of evaporation thermal, not additional energy.</div>`);
}

function provenanceSection(artifact) {
  const meta = artifact.terminal.run_metadata || {};
  const closure = artifact.terminal.mass_balance_closure || {};
  const facts = [
    ["Artifact schema", artifact.artifact_schema_version], ["Runner schema", meta.schema_version],
    ["Backend evidence", meta.evidence_class], ["Backend authoritative", meta.backend_authoritative],
    ["Certification allowed", meta.certification_allowed], ["Hours requested / completed", `${meta.hours_requested} / ${meta.hours_completed}`],
    ["Mass-balance residual", `${sci(closure.residual_pct ?? closure.residual)} % · ${closure.basis || "basis not emitted"}`],
    ["Kernel identity", artifact.header.engine_identity?.cache_version]
  ];
  return section(9, "Provenance & confidence", "Status-bearing metadata preserved from the frozen artifact.", `<div class="table-wrap"><table><tbody>${facts.map(([key, value]) => `<tr><th>${esc(key)}</th><td class="mono">${esc(value)}</td></tr>`).join("")}</tbody></table></div>`);
}

function renderCurrent(artifact, index) {
  const timestep = artifact.timesteps[index];
  const row = timestep.summary;
  $("#step-output").textContent = `Hour ${timestep.hour} · ${row.campaign}`;
  $(".status-pill").textContent = `${index + 1} / ${artifact.timesteps.length}`;
  $("#current-grid").innerHTML = [
    ["Temperature", hasNumber(row.T_C) ? `${Number(row.T_C).toLocaleString()} °C` : "not emitted"], ["Total pressure", hasNumber(row.P_total_bar) ? `${Number(row.P_total_bar).toExponential(3)} bar` : "not emitted"],
    ["pO₂", hasNumber(row.pO2_bar) ? `${Number(row.pO2_bar).toExponential(3)} bar` : "not emitted"], ["Electrical", hasNumber(row.energy_electrical_kWh) ? `${Number(row.energy_electrical_kWh).toFixed(4)} kWh` : "not emitted"],
    ["Evaporation thermal", hasNumber(row.energy_evaporation_thermal_kWh) ? `${Number(row.energy_evaporation_thermal_kWh).toFixed(4)} kWh` : "not emitted"], [row.O2_metric_label || "O₂ metric label not emitted", kg(row.O2_source_side_potential_kg_cumulative, 4)],
    ["Regime", row.regime], ["Kn", row.Kn == null ? "not emitted" : row.Kn && typeof row.Kn === "object" ? sci(row.Kn.knudsen_number) : sci(row.Kn)]
  ].map(([key, value]) => `<div class="current"><div class="k">${esc(key)}</div><div class="v">${esc(value)}</div></div>`).join("");
  updateMarkers(index, artifact.timesteps.length);
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
  energy.electricalCost = hasNumber(energy.electrical) && hasNumber(artifact.header.cost_block?.electrical_cost_per_kWh) ? energy.electrical * Number(artifact.header.cost_block.electrical_cost_per_kWh) : null;
  energy.thermalCost = hasNumber(energy.thermal) && hasNumber(artifact.header.cost_block?.solar_heat_cost_per_kWh) ? energy.thermal * Number(artifact.header.cost_block.solar_heat_cost_per_kWh) : null;
  energy.totalCost = hasNumber(energy.electricalCost) && hasNumber(energy.thermalCost) ? energy.electricalCost + energy.thermalCost : null;
  const timestepSections = rows.length
    ? yieldsSection(rows, artifact.terminal) + processSection(artifact, rows, spans) + campaignSection(artifact, spans)
    : section(1, "Per-hour telemetry", "No timestep rows were emitted for this run.", `<div class="pending"><strong>Not emitted</strong><p>This execution has zero timesteps; header, failure, and terminal data remain available below.</p></div>`);
  $("#report").innerHTML = makeHeader(artifact, rows, energy) + timestepSections +
    ledgerSection(artifact.terminal.final_state) +
    tapsAndPuritySection(artifact.terminal) + wallAndOxygenSection(artifact, rows) + ceramicSection(artifact.terminal) +
    costSection(artifact, energy) + provenanceSection(artifact) +
    `<footer class="footer"><span>Frozen flatfile report · engine-free · artifact-only rendering</span><a href="./settings.html${RUN_QUERY}">Captured settings</a><span class="mono">${esc(artifact.header.run_id)}</span></footer>`;
  if (rows.length) {
    const stepper = $("#stepper");
    stepper.addEventListener("input", () => renderCurrent(artifact, Number(stepper.value)));
    renderCurrent(artifact, 0);
  }
}

fetch(ARTIFACT_URL)
  .then((response) => {
    if (!response.ok) throw new Error(`Artifact request failed (${response.status})`);
    return response.json();
  })
  .then(render)
  .catch((error) => {
    $("#report").innerHTML = `<div class="fatal"><div class="eyebrow">Report unavailable</div><h1>Could not read the frozen artifact</h1><p>${esc(error.message)}</p><p>If your browser blocks local <code>file:</code> fetches, serve this directory with any offline local static server and open <code>index.html</code> there.</p></div>`;
  });
