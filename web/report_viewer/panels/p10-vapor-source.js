(function registerVaporSourcePanel(root) {
  "use strict";

  const { fmtNum, prettySpecies, speciesColor, esc } = root.ReportLabels;
  const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const isRecord = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

  function pendingValue(message = "Not emitted · pending producer") {
    return `<span class="sec-p10-pending-value">${message}</span>`;
  }

  function emittedType(value) {
    if (value === null) return "null";
    if (Array.isArray(value)) return "array";
    return typeof value;
  }

  function malformedValue(value) {
    return `<span class="sec-p10-malformed">Malformed ${emittedType(value)} (emitted)</span>`;
  }

  function scalarValue(value, { expected } = {}) {
    if (expected === "number") {
      if (typeof value !== "number" || !Number.isFinite(value)) return malformedValue(value);
      return `<span class="sec-p10-scalar">${esc(fmtNum(value))}</span>`;
    }
    if (expected === "string") {
      if (typeof value !== "string") return malformedValue(value);
      if (value === "") return `<span class="sec-p10-empty-value">empty string (emitted)</span>`;
      return `<span class="sec-p10-scalar">${esc(value)}</span>`;
    }
    if (expected === "boolean-or-null") {
      if (value === null) return `<span class="sec-p10-empty-value">null (emitted)</span>`;
      if (typeof value !== "boolean") return malformedValue(value);
      return `<span class="sec-p10-scalar">${esc(String(value))}</span>`;
    }
    return malformedValue(value);
  }

  function envelopeCard(report, key, label, options = {}) {
    const value = hasOwn(report, key)
      ? scalarValue(report[key], options)
      : pendingValue();
    return `<div class="sec-p10-envelope-card" role="listitem"><div class="sec-p10-envelope-label">${label}</div><div class="sec-p10-envelope-value">${value}</div></div>`;
  }

  function summaryCell(entry, key) {
    if (!isRecord(entry)) return malformedValue(entry);
    if (!hasOwn(entry, key)) return pendingValue();
    return scalarValue(entry[key], { expected: "number" });
  }

  function summaryBlock(report, key, title, emptyMessage) {
    if (!hasOwn(report, key)) {
      return `<div class="pending sec-p10-pending"><strong>Pending producer</strong><p>${title} was not emitted.</p></div>`;
    }
    const summary = report[key];
    if (!isRecord(summary)) {
      return `<div class="pending sec-p10-pending"><strong>Malformed emitted value</strong><p>${title} must be a mapping; this artifact emitted an ${Array.isArray(summary) ? "array" : typeof summary}.</p></div>`;
    }
    const entries = Object.entries(summary);
    if (!entries.length) return `<p class="sec-p10-empty">${emptyMessage}</p>`;
    const rows = entries.map(([token, entry]) => `<tr><th scope="row" class="sec-p10-token">${esc(token)}</th><td class="num">${summaryCell(entry, "count")}</td><td class="num">${summaryCell(entry, "percentage")}</td></tr>`).join("");
    return `<div class="table-wrap sec-p10-table-wrap"><table class="sec-p10-summary-table"><caption>${title}</caption><thead><tr><th scope="col">Emitted token</th><th scope="col" class="num">Count</th><th scope="col" class="num">Percentage</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function speciesBlock(report) {
    if (!hasOwn(report, "species")) {
      return `<div class="pending sec-p10-pending"><strong>Pending producer</strong><p>Per-species source tokens were not emitted.</p></div>`;
    }
    const species = report.species;
    if (!isRecord(species)) {
      return `<div class="pending sec-p10-pending"><strong>Malformed emitted value</strong><p>Per-species source tokens must be a mapping.</p></div>`;
    }
    const entries = Object.entries(species);
    if (!entries.length) return `<p class="sec-p10-empty">No per-species sources emitted.</p>`;
    const rows = entries.map(([name, source]) => {
      const color = speciesColor(name);
      const speciesLabel = esc(prettySpecies(name));
      const token = typeof source === "string"
        ? `<span class="sec-p10-token">${esc(String(source))}</span>`
        : `<span class="sec-p10-malformed">Malformed ${source === null ? "null" : Array.isArray(source) ? "array" : typeof source} source token</span>`;
      return `<tr><th scope="row"><span class="sec-p10-species-dot" style="--sec-p10-species-color: ${esc(color)}" aria-hidden="true"></span><span title="${esc(name)}">${speciesLabel}</span></th><td>${token}</td></tr>`;
    }).join("");
    return `<div class="table-wrap sec-p10-table-wrap"><table class="sec-p10-species-table"><caption>Per-species source tokens</caption><thead><tr><th scope="col">Species</th><th scope="col">Source token · verbatim</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function render(artifact) {
    const terminal = artifact?.terminal;
    const reportEmitted = isRecord(terminal) && hasOwn(terminal, "vapor_pressure_source_report");
    const report = reportEmitted ? terminal.vapor_pressure_source_report : undefined;
    if (!reportEmitted) {
      return `<section id="sec-p10-vapor-source" class="sec-p10-vapor-source" aria-labelledby="sec-p10-vapor-source-title"><h2 id="sec-p10-vapor-source-title"><span class="sect">P10</span>Vapor-pressure source report</h2><p class="sub">Producer-emitted final-equilibrium source tokens and vapor-pressure backend envelope.</p><div class="pending sec-p10-pending"><strong>Pending producer</strong><p>The terminal vapor-pressure source report was not emitted.</p></div></section>`;
    }
    if (!isRecord(report)) {
      const emittedType = report === null ? "null" : Array.isArray(report) ? "array" : typeof report;
      return `<section id="sec-p10-vapor-source" class="sec-p10-vapor-source" aria-labelledby="sec-p10-vapor-source-title"><h2 id="sec-p10-vapor-source-title"><span class="sect">P10</span>Vapor-pressure source report</h2><p class="sub">Producer-emitted final-equilibrium source tokens and vapor-pressure backend envelope.</p><div class="pending sec-p10-pending"><strong>Malformed emitted report</strong><p>The terminal vapor-pressure source report must be a mapping; this artifact emitted ${emittedType}.</p></div></section>`;
    }

    const envelope = [
      envelopeCard(report, "total_species", "Total species", { expected: "number" }),
      envelopeCard(report, "vapor_pressure_backend_status", "Vapor-pressure backend status", { expected: "string" }),
      envelopeCard(report, "vapor_pressure_backend_status_reason", "Backend-status reason", { expected: "string" }),
      envelopeCard(report, "vapor_pressure_fallback_source", "Vapor-pressure fallback source", { expected: "string" }),
      envelopeCard(report, "authoritative_for_requested_vapor_pressure", "Authoritative for requested vapor pressure", { expected: "boolean-or-null" })
    ].join("");

    return `<section id="sec-p10-vapor-source" class="sec-p10-vapor-source" aria-labelledby="sec-p10-vapor-source-title"><h2 id="sec-p10-vapor-source-title"><span class="sect">P10</span>Vapor-pressure source report</h2><p class="sub">Producer-emitted final-equilibrium source tokens and vapor-pressure backend envelope. Source tokens remain verbatim.</p><div class="sec-p10-envelope" role="list" aria-label="Vapor-pressure source report envelope">${envelope}</div><div class="sec-p10-summary-grid">${summaryBlock(report, "summary", "Source summary", "No source-summary entries emitted.")}${summaryBlock(report, "vapor_pressure_backend_status_summary", "Backend-status summary", "No backend-status summary entries emitted.")}</div><div class="sec-p10-species-block"><h3>Species sources</h3>${speciesBlock(report)}</div><div class="pending sec-p10-pending sec-p10-structured-pending" role="note"><strong>Per-species authority detail · pending producer</strong><p>Per-species backend status, authority, reference, diagnostic/certification, fallback, extrapolation, and uncertainty are not emitted as structured fields. Final-equilibrium envelope values are not copied into species rows.</p></div></section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({
    id: "sec-p10-vapor-source",
    render
  });
}(globalThis));
