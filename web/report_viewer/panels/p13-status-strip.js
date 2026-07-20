"use strict";

(function registerStatusStrip(root) {
  const { scalarText, fmtNum, prettySpecies, esc } = root.ReportLabels;
  const SECTION_ID = "sec-p13-status-strip";
  const LIVE_ID = "p13-status-strip-live";

  const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const hasNumber = (value) => typeof value === "number" && Number.isFinite(value);

  function numberText(value, unit = "") {
    return esc(fmtNum(value, unit));
  }

  function scalarValue(value, missing = "not emitted") {
    if (value === undefined || value === null || value === "") return missing;
    return esc(scalarText(value));
  }

  function detailRow(label, value) {
    return `<div class="sec-p13-detail-row"><dt>${label}</dt><dd>${value}</dd></div>`;
  }

  function redoxAuthorityChips(redox) {
    const definitions = [
      ["authority", "authority"],
      ["authoritative", "authoritative"],
      ["diagnostic_only", "diagnostic_only"],
      ["extrapolation", "extrapolation"],
      ["high_uncertainty", "high_uncertainty"]
    ];
    const chips = definitions.filter(([key]) => hasOwn(redox, key)).map(([key, label]) => {
      const value = redox[key];
      const text = key === "authority"
        ? `authority: ${scalarText(value)}`
        : value === true ? label : `${label}: ${scalarText(value)}`;
      const caution = (key === "authoritative" && value === false)
        || (["diagnostic_only", "extrapolation", "high_uncertainty"].includes(key) && value === true);
      return `<span class="sec-p13-chip${caution ? " sec-p13-chip--caution" : ""}">${esc(text)}</span>`;
    });
    return chips.length
      ? chips.join("")
      : `<span class="sec-p13-chip sec-p13-chip--pending">authority not emitted</span>`;
  }

  function redoxTile(summary) {
    const redox = summary && summary.fe_redox_split;
    if (!isObject(redox)) {
      return `<article class="sec-p13-tile sec-p13-redox" aria-labelledby="sec-p13-redox-title">
        <div class="sec-p13-tile-title" id="sec-p13-redox-title">Melt redox</div>
        <div class="sec-p13-headline">redox not emitted</div>
        <div class="sec-p13-chip-row"><span class="sec-p13-chip sec-p13-chip--pending">authority not emitted</span></div>
      </article>`;
    }

    const ratio = hasNumber(redox.fe3_over_sigma_fe)
      ? `Fe³⁺/ΣFe ${numberText(redox.fe3_over_sigma_fe)}`
      : hasNumber(redox.ferric_frac) || hasNumber(redox.ferrous_frac)
        ? `Ferric ${numberText(redox.ferric_frac)} · ferrous ${numberText(redox.ferrous_frac)}`
        : "Fe split not emitted";
    const nativeState = hasNumber(redox.native_fe_frac) || (redox.status !== undefined && redox.status !== null && redox.status !== "")
      ? `Native Fe ${numberText(redox.native_fe_frac)} · ${scalarValue(redox.status, "status not emitted")}`
      : "Native Fe state not emitted";
    const metadata = [
      ["Status", "status"],
      ["Source", "source"],
      ["Reference", "reference"],
      ["Skip reason", "skip_reason"],
      ["Refusal context", "refusal_context"]
    ].filter(([, key]) => hasOwn(redox, key))
      .map(([label, key]) => detailRow(label, scalarValue(redox[key])))
      .join("");

    return `<article class="sec-p13-tile sec-p13-redox" aria-labelledby="sec-p13-redox-title">
      <div class="sec-p13-tile-title" id="sec-p13-redox-title">Melt redox</div>
      <div class="sec-p13-headline">log fO₂ ${numberText(redox.fO2_log)} <span aria-hidden="true">·</span> ΔIW ${numberText(redox.iw_log)}</div>
      <div class="sec-p13-fact">${ratio}</div>
      <div class="sec-p13-fact">${nativeState}</div>
      <div class="sec-p13-chip-row" aria-label="Emitted redox authority and uncertainty flags">${redoxAuthorityChips(redox)}</div>
      <details class="sec-p13-details">
        <summary>Redox detail</summary>
        <dl>
          ${detailRow("log fO₂", numberText(redox.fO2_log))}
          ${detailRow("ΔIW", numberText(redox.iw_log))}
          ${detailRow("Fe³⁺/ΣFe", numberText(redox.fe3_over_sigma_fe))}
          ${detailRow("Ferric fraction", numberText(redox.ferric_frac))}
          ${detailRow("Ferrous fraction", numberText(redox.ferrous_frac))}
          ${detailRow("Native Fe fraction", numberText(redox.native_fe_frac))}
          ${metadata}
        </dl>
      </details>
    </article>`;
  }

  function emittedKn(summary) {
    if (!summary) return null;
    const value = summary.Kn;
    if (hasNumber(value)) return value;
    return isObject(value) && hasNumber(value.knudsen_number) ? value.knudsen_number : null;
  }

  function flowTile(summary) {
    const regime = typeof summary?.regime === "string" ? summary.regime : "";
    const regimeLabels = {
      free_molecular: "ballistic",
      transitional: "transitional",
      viscous: "viscous / swept"
    };
    const regimeLabel = regimeLabels[regime]
      || (regime ? `regime token: ${esc(regime)}` : "regime not emitted");
    const kn = emittedKn(summary);
    const gauge = kn === null
      ? `<div class="sec-p13-gauge sec-p13-gauge--pending" aria-label="Knudsen number not emitted"></div>`
      : `<meter class="sec-p13-gauge" min="0" max="1" low="0.01" high="0.1" optimum="0" value="${esc(String(kn))}" aria-label="Emitted Knudsen number ${esc(String(kn))}; low is the viscous end and high is the ballistic end"></meter>`;
    const formula = scalarValue(summary?.transport_formula_id);

    return `<article class="sec-p13-tile sec-p13-flow" aria-labelledby="sec-p13-flow-title">
      <div class="sec-p13-tile-title" id="sec-p13-flow-title">Flow regime</div>
      <div class="sec-p13-headline">${regimeLabel}</div>
      <div class="sec-p13-fact">Kn ${kn === null ? "not emitted" : numberText(kn)}</div>
      ${gauge}
      <div class="sec-p13-gauge-labels" aria-hidden="true"><span>viscous / swept</span><span>ballistic</span></div>
      <details class="sec-p13-details">
        <summary>Transport detail</summary>
        <dl>${detailRow("Transport formula", formula)}</dl>
      </details>
    </article>`;
  }

  function derivedVoltageDetail(value) {
    if (value === undefined || value === null) return "not emitted";
    if (hasNumber(value)) return numberText(value, "V");
    if (!isObject(value)) return `malformed diagnostic value (${esc(typeof value)})`;
    const entries = Object.entries(value);
    if (!entries.length) return "not emitted";
    return entries.map(([species, voltage]) =>
      `${esc(prettySpecies(species))}: ${numberText(voltage, "V")}`
    ).join("<br>");
  }

  function diagnosticMetadata(diagnostic, failed) {
    const voltageRows = failed
      ? detailRow("Voltage evidence", "withheld because the emitted diagnostic status reports failure")
      : detailRow("Declared rung", numberText(diagnostic.declared_rung_V, "V"))
        + detailRow("Derived Ed", derivedVoltageDetail(diagnostic.derived_Ed_V));
    const metadata = [
      ["Authority", "authority"],
      ["Status", "status"],
      ["Source", "source"],
      ["Reference", "reference"],
      ["Skip reason", "skip_reason"],
      ["Refusal context", "refusal_context"]
    ].filter(([, key]) => hasOwn(diagnostic, key))
      .map(([label, key]) => detailRow(label, scalarValue(diagnostic[key])))
      .join("");
    return voltageRows + metadata;
  }

  function mreTile(summary) {
    const campaign = scalarValue(summary?.campaign, "campaign not emitted");
    const diagnostic = summary && summary.mre_ellingham_ladder_diagnostic;
    const hasDiagnostic = isObject(diagnostic);
    const status = hasDiagnostic && typeof diagnostic.status === "string" ? diagnostic.status : "";
    const failed = status.startsWith("diagnostic_failed:");
    const ladderHeadline = !hasDiagnostic
      ? "ladder diagnostic: not emitted"
      : failed
        ? `ladder diagnostic: ${esc(status)}`
        : `Ladder ${numberText(diagnostic.declared_rung_V, "V")}`;
    const certification = hasDiagnostic
      ? scalarValue(diagnostic.certification, "certification not emitted")
      : "certification not emitted";
    const details = hasDiagnostic
      ? `<details class="sec-p13-details">
          <summary>Ladder detail</summary>
          <dl>${diagnosticMetadata(diagnostic, failed)}</dl>
        </details>`
      : "";

    return `<article class="sec-p13-tile sec-p13-mre" aria-labelledby="sec-p13-mre-title">
      <div class="sec-p13-tile-title" id="sec-p13-mre-title">MRE facts</div>
      <div class="sec-p13-chip-row"><span class="sec-p13-chip">Campaign: ${campaign}</span></div>
      <div class="sec-p13-headline${failed ? " sec-p13-headline--failed" : ""}">${ladderHeadline}</div>
      ${hasDiagnostic ? `<div class="sec-p13-chip-row"><span class="sec-p13-chip${failed ? " sec-p13-chip--caution" : ""}">${certification}</span></div>` : ""}
      <div class="sec-p13-activity">MRE activity: not emitted</div>
      ${details}
    </article>`;
  }

  function statusMarkup(artifact, index) {
    const timestep = Array.isArray(artifact?.timesteps) ? artifact.timesteps[index] : null;
    const summary = isObject(timestep?.summary) ? timestep.summary : null;
    if (!summary) {
      return `<div class="sec-p13-pending"><strong>Timestep status not emitted</strong><span>No selected timestep summary is available.</span></div>`;
    }
    return `<div class="sec-p13-grid">
      ${redoxTile(summary)}
      ${flowTile(summary)}
      ${mreTile(summary)}
    </div>`;
  }

  function pinNearStepper() {
    if (typeof document === "undefined") return;
    const section = document.querySelector(`#${SECTION_ID}`);
    const stepper = document.querySelector(".stepper");
    const stepperSection = stepper && typeof stepper.closest === "function" ? stepper.closest("section") : null;
    if (!section || !stepperSection || typeof stepperSection.insertAdjacentElement !== "function") return;
    if (stepperSection.nextElementSibling !== section) stepperSection.insertAdjacentElement("afterend", section);
  }

  function render(artifact) {
    return `<section class="sec-p13-status-strip" id="${SECTION_ID}" aria-labelledby="sec-p13-status-strip-heading">
      <h2 id="sec-p13-status-strip-heading"><span class="sect">13</span>Per-timestep status</h2>
      <p class="sec-p13-sub">Emitted melt, transport, and MRE facts for the selected timestep.</p>
      <div id="${LIVE_ID}" class="sec-p13-live" aria-live="polite" aria-atomic="true">${statusMarkup(artifact, 0)}</div>
    </section>`;
  }

  function onTimestep(artifact, index) {
    pinNearStepper();
    if (typeof document === "undefined") return;
    const live = document.querySelector(`#${LIVE_ID}`);
    if (live) live.innerHTML = statusMarkup(artifact, index);
  }

  (root.ReportPanels = root.ReportPanels || []).push({ id: SECTION_ID, render, onTimestep });
}(globalThis));
