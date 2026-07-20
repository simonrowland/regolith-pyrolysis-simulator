"use strict";

(function registerCarrierPressurePanel(root) {
  const { fmtNum, prettySpecies, esc } = root.ReportLabels;

  function isFiniteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function emittedBadge() {
    return `<span class="sec-p12-authority" tabindex="0" aria-label="Artifact-emitted; read directly from the artifact, with no viewer-inferred pressure." title="Read directly from the artifact; no pressure is inferred by the viewer.">Artifact-emitted</span>`;
  }

  function pressureMetric(label, value, detail) {
    if (!isFiniteNumber(value)) {
      return `<div class="sec-p12-metric">` +
        `<div class="sec-p12-label">${label}</div>` +
        `<div class="sec-p12-value">not emitted</div>` +
        `<div class="sec-p12-detail">Pending artifact field.</div>` +
        `</div>`;
    }
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
    const hasCarrierPressure = isFiniteNumber(row?.p_carrier_bar);
    const hasCarrierIdentity = carrierIdentity !== null;
    const identityLabel = hasCarrierIdentity ? esc(prettySpecies(carrierIdentity)) : "Carrier";
    const carrierHeadline = hasCarrierIdentity || hasCarrierPressure
      ? `<div class="sec-p12-headline">` +
        `<div class="sec-p12-label">${identityLabel} partial pressure</div>` +
        (hasCarrierPressure
          ? `<div class="sec-p12-headline-value">${esc(fmtNum(row.p_carrier_bar * 1000, "mbar"))}</div>`
          : "") +
        `<div class="sec-p12-detail">${emittedBadge()}${hasCarrierPressure ? " bar to mbar unit conversion (×1000)." : " Carrier identity only; pressure not emitted."}</div>` +
        `</div>`
      : "";
    let pending = "";
    if (!hasCarrierIdentity || !hasCarrierPressure) {
      pending = `<div class="sec-p12-pending" role="status"><strong>Pending</strong><p>Pending carrier_identity / P_total−pO₂ is not used as a substitute.</p></div>`;
    }
    const oxygenLabel = esc(prettySpecies("O2"));
    const contextMetrics = [
      pressureMetric("Total overhead pressure", row?.P_total_bar, "Terminal total-pressure context."),
      pressureMetric(`${oxygenLabel} partial pressure`, row?.pO2_bar, "Terminal oxygen-pressure context.")
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
