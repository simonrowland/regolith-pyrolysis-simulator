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
    const nativeEvent = isObject(redox.native_fe_saturation_event)
      ? redox.native_fe_saturation_event
      : null;
    const nativeState = hasNumber(redox.native_fe_frac) || nativeEvent
      ? `Native Fe ${numberText(redox.native_fe_frac)} · event status${nativeEvent
        ? `: ${scalarValue(nativeEvent.native_fe_event_status)}`
        : " not emitted"}${nativeEvent && hasOwn(nativeEvent, "native_fe_event_reason")
        ? ` · event reason: ${scalarValue(nativeEvent.native_fe_event_reason)}`
        : ""}`
      : "Native Fe state not emitted";
    const metadata = [
      ["Redox diagnostic status", "status"],
      ["Source", "source"],
      ["Reference", "reference"],
      ["Skip reason", "skip_reason"],
      ["Refusal context", "refusal_context"]
    ].filter(([, key]) => hasOwn(redox, key))
      .map(([label, key]) => detailRow(label, scalarValue(redox[key])))
      .join("");
    const nativeMetadata = nativeEvent
      ? [
          ["Native Fe event", "native_fe_event"],
          ["Native Fe event status", "native_fe_event_status"],
          ["Native Fe event reason", "native_fe_event_reason"]
        ].filter(([, key]) => hasOwn(nativeEvent, key))
          .map(([label, key]) => detailRow(label, scalarValue(nativeEvent[key])))
          .join("")
      : "";

    return `<article class="sec-p13-tile sec-p13-redox" aria-labelledby="sec-p13-redox-title">
      <div class="sec-p13-tile-title" id="sec-p13-redox-title">Melt redox</div>
      <div class="sec-p13-headline">log fO₂ ${numberText(redox.fO2_log)} <span aria-hidden="true">·</span> IW buffer log fO₂ ${numberText(redox.iw_log)} <span aria-hidden="true">·</span> ΔIW not emitted</div>
      <div class="sec-p13-fact">${ratio}</div>
      <div class="sec-p13-fact">${nativeState}</div>
      <div class="sec-p13-chip-row" aria-label="Emitted redox authority and uncertainty flags">${redoxAuthorityChips(redox)}</div>
      <details class="sec-p13-details">
        <summary>Redox detail</summary>
        <dl>
          ${detailRow("log fO₂", numberText(redox.fO2_log))}
          ${detailRow("IW buffer log fO₂", numberText(redox.iw_log))}
          ${detailRow("ΔIW", "not emitted")}
          ${detailRow("Fe³⁺/ΣFe", numberText(redox.fe3_over_sigma_fe))}
          ${detailRow("Ferric fraction", numberText(redox.ferric_frac))}
          ${detailRow("Ferrous fraction", numberText(redox.ferrous_frac))}
          ${detailRow("Native Fe fraction", numberText(redox.native_fe_frac))}
          ${nativeMetadata}
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

  function voltageMetadata(diagnostic, species) {
    const speciesRows = isObject(diagnostic.species) ? diagnostic.species : null;
    const speciesRow = speciesRows && isObject(speciesRows[species]) ? speciesRows[species] : null;
    if (speciesRow
      && hasOwn(speciesRow, "voltage_authority")
      && hasOwn(speciesRow, "voltage_authoritative")
      && hasOwn(speciesRow, "status")) {
      return {
        authority: speciesRow.voltage_authority,
        authoritative: speciesRow.voltage_authoritative,
        status: speciesRow.status
      };
    }
    const fallbackRows = isObject(diagnostic.non_authoritative_voltage_by_oxide)
      ? diagnostic.non_authoritative_voltage_by_oxide
      : null;
    const fallbackRow = fallbackRows && isObject(fallbackRows[species])
      ? fallbackRows[species]
      : null;
    return fallbackRow
      && hasOwn(fallbackRow, "authority")
      && hasOwn(fallbackRow, "authoritative")
      && hasOwn(fallbackRow, "status")
      ? fallbackRow
      : null;
  }

  function derivedVoltageDetail(diagnostic) {
    const value = diagnostic.derived_Ed_V;
    if (value === undefined || value === null) return "not emitted";
    if (hasNumber(value)) return "voltage withheld; per-species authority/status not emitted";
    if (!isObject(value)) return `malformed diagnostic value (${esc(typeof value)})`;
    const entries = Object.entries(value);
    if (!entries.length) return "not emitted";
    return entries.map(([species, voltage]) => {
      const label = esc(prettySpecies(species));
      if (!hasNumber(voltage)) return `${label}: ${numberText(voltage, "V")}`;
      const emittedMetadata = voltageMetadata(diagnostic, species);
      if (!emittedMetadata) {
        return `${label}: voltage withheld; per-species authority/status not emitted`;
      }
      return `${label}: ${numberText(voltage, "V")}`
        + ` · voltage authority: ${scalarValue(emittedMetadata.authority)}`
        + ` · voltage authoritative: ${scalarValue(emittedMetadata.authoritative)}`
        + ` · voltage status: ${scalarValue(emittedMetadata.status)}`;
    }).join("<br>");
  }

  function diagnosticMetadata(diagnostic, failed) {
    const voltageRows = failed
      ? detailRow("Voltage evidence", "withheld because the emitted diagnostic status reports failure")
      : detailRow("Declared rung", numberText(diagnostic.declared_rung_V, "V"))
        + detailRow("Derived Ed", derivedVoltageDetail(diagnostic));
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
