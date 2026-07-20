"use strict";

(function registerCarrierPressurePanel(root) {
  const { fmtNum, prettySpecies, esc } = root.ReportLabels;

  function isFiniteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function scalarState(record, key) {
    if (record === null || typeof record !== "object" || !Object.prototype.hasOwnProperty.call(record, key)) {
      return "absent";
    }
    const value = record[key];
    if (value === null || (typeof value === "string" && value.trim() === "")) {
      return "empty";
    }
    if (!isFiniteNumber(value)) {
      return "malformed";
    }
    if (value === 0) {
      return "zero";
    }
    return value < 0 ? "negative" : "positive";
  }

  function emittedBadge() {
    return `<span class="sec-p12-authority" tabindex="0" aria-label="Artifact-emitted; read directly from the artifact, with no viewer-inferred pressure." title="Read directly from the artifact; no pressure is inferred by the viewer.">Artifact-emitted</span>`;
  }

  function pressureMetric(label, record, key, detail) {
    const state = scalarState(record, key);
    const unavailable = {
      absent: ["not emitted", "Pending artifact field."],
      empty: ["empty", "Artifact field is present but empty."],
      malformed: ["malformed", "Artifact field is present but not a finite number."]
    }[state];
    if (unavailable) {
      return `<div class="sec-p12-metric">` +
        `<div class="sec-p12-label">${label}</div>` +
        `<div class="sec-p12-value">${unavailable[0]}</div>` +
        `<div class="sec-p12-detail">${unavailable[1]}</div>` +
        `</div>`;
    }
    const value = record[key];
    return `<div class="sec-p12-metric">` +
      `<div class="sec-p12-label">${label}</div>` +
      `<div class="sec-p12-value">${esc(fmtNum(value, "bar"))}</div>` +
      `<div class="sec-p12-detail">${emittedBadge()} ${detail}</div>` +
      `</div>`;
  }

  function render(artifact, rows) {
    const row = Array.isArray(rows) && rows.length ? rows[rows.length - 1] : null;
    const carrierIdentity = typeof row?.carrier_identity === "string" && row.carrier_identity.trim()
      ? row.carrier_identity.trim()
      : null;
    const carrierPressureState = scalarState(row, "p_carrier_bar");
    const hasCarrierPressure = carrierPressureState === "positive";
    const hasCarrierIdentity = carrierIdentity !== null;
    const identityLabel = hasCarrierIdentity ? esc(prettySpecies(carrierIdentity)) : "Carrier";
    const carrierPressureStatus = {
      absent: "Carrier pressure field · not emitted.",
      empty: "Carrier pressure field · empty.",
      malformed: "Carrier pressure field · malformed.",
      zero: "Carrier pressure field · zero (outside positive emitter contract).",
      negative: "Carrier pressure field · negative (outside positive emitter contract).",
      positive: "Carrier pressure field · artifact-emitted."
    }[carrierPressureState];
    const identityOnlyDetail = {
      absent: "Carrier identity only; pressure not emitted.",
      empty: "Carrier identity emitted; pressure field empty.",
      malformed: "Carrier identity emitted; pressure field malformed.",
      zero: "Carrier identity emitted; pressure field zero (outside positive emitter contract).",
      negative: "Carrier identity emitted; pressure field negative (outside positive emitter contract)."
    }[carrierPressureState];
    const carrierHeadline = hasCarrierIdentity || hasCarrierPressure
      ? `<div class="sec-p12-headline">` +
        `<div class="sec-p12-label">${identityLabel} partial pressure</div>` +
        (hasCarrierPressure
          ? `<div class="sec-p12-headline-value">${esc(fmtNum(row.p_carrier_bar * 1000, "mbar"))}</div>`
          : "") +
        `<div class="sec-p12-detail">${emittedBadge()} ${hasCarrierPressure ? "bar to mbar unit conversion (×1000)." : identityOnlyDetail}</div>` +
        `</div>`
      : "";
    let pending = "";
    if (!hasCarrierIdentity || !hasCarrierPressure) {
      pending = `<div class="sec-p12-pending" role="status"><strong>Pending</strong><p>Pending carrier_identity / P_total−pO₂ is not used as a substitute.</p><p>${carrierPressureStatus}</p></div>`;
    }
    const oxygenLabel = esc(prettySpecies("O2"));
    const contextMetrics = [
      pressureMetric("Total overhead pressure", row, "P_total_bar", "Terminal total-pressure context."),
      pressureMetric(`${oxygenLabel} partial pressure`, row, "pO2_bar", "Terminal oxygen-pressure context.")
    ].join("");

    return `<section class="card sec-p12-carrier-pressure" id="sec-p12-carrier-pressure" aria-labelledby="sec-p12-carrier-pressure-title">` +
      `<h2 id="sec-p12-carrier-pressure-title"><span class="sect">P12</span>Carrier / overhead-pressure lever</h2>` +
      `<p class="sub">Terminal-timestep carrier partial pressure, with total and ${oxygenLabel} pressures kept as separate context.</p>` +
      carrierHeadline + pending +
      `<div class="sec-p12-context" aria-label="Terminal overhead pressure context">${contextMetrics}</div>` +
      `</section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({
    id: "sec-p12-carrier-pressure",
    render
  });
}(globalThis));
