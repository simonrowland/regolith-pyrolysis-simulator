"use strict";

(function registerWallCoatingPanel(root) {
  const {
    accountLabel, esc, fmtNum, prettySpecies, scalarText, speciesColor,
  } = root.ReportLabels;
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const isRecord = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const isNumber = (value) => typeof value === "number" && Number.isFinite(value);

  function humanize(value) {
    return scalarText(value).replace(/[_-]+/g, " ");
  }

  function pending(title, detail) {
    return `<div class="pending"><strong>${esc(title)}</strong><p>${esc(detail)}</p></div>`;
  }

  function numberCell(value, unit) {
    return isNumber(value)
      ? `<span class="num">${esc(fmtNum(value, unit))}</span>`
      : `<span class="sec-p3-missing">not emitted</span>`;
  }

  function badge(label, value, title = "") {
    const tooltip = title ? ` title="${esc(title)}"` : "";
    return `<span class="sec-p3-badge"${tooltip}><b>${esc(label)}</b> ${esc(value)}</span>`;
  }

  function diagnosticBadges(record) {
    if (!isRecord(record)) return "";
    const fields = [
      ["status", "Status", true],
      ["authoritative", "Authoritative", false],
      ["diagnostic_only", "Diagnostic only", false],
      ["extrapolation", "Extrapolation", false],
      ["high_uncertainty", "High uncertainty", false],
      ["provisional", "Provisional", false],
      ["output_status", "Output status", true],
      ["source", "Source", false],
      ["source_class", "Source class", true],
      ["reference", "Reference", false],
      ["skip_reason", "Skip reason", true],
      ["reason", "Reason", true],
    ];
    return fields.filter(([key]) => own(record, key)).map(([key, label, readable]) => {
      const raw = record[key];
      const value = typeof raw === "boolean" ? (raw ? "yes" : "no")
        : isNumber(raw) ? fmtNum(raw)
          : readable ? humanize(raw) : scalarText(raw);
      return badge(label, value);
    }).join("");
  }

  function speciesLabel(species) {
    return `<span class="sec-p3-species" title="${esc(species)}">` +
      `<i style="background:${esc(speciesColor(species))}" aria-hidden="true"></i>` +
      `${esc(prettySpecies(species))}</span>`;
  }

  function diagnosticValue(entry, key, fallbackMap, species) {
    if (isRecord(entry) && isNumber(entry[key])) return entry[key];
    if (isRecord(fallbackMap) && own(fallbackMap, species) && isNumber(fallbackMap[species])) {
      return fallbackMap[species];
    }
    return null;
  }

  function speciesNames(aggregate, bySpecies, currentFlux, currentCumulative) {
    const names = new Set();
    [aggregate, bySpecies, currentFlux, currentCumulative].forEach((record) => {
      if (isRecord(record)) Object.keys(record).forEach((name) => names.add(name));
    });
    const hasAggregateRank = isRecord(aggregate) && Object.values(aggregate).some(isNumber);
    const rank = (species) => {
      if (hasAggregateRank) return isNumber(aggregate[species]) ? aggregate[species] : null;
      const entry = isRecord(bySpecies) && isRecord(bySpecies[species]) ? bySpecies[species] : null;
      const cumulative = diagnosticValue(entry, "cumulative_wall_deposit_kg", currentCumulative, species);
      return cumulative !== null
        ? cumulative
        : diagnosticValue(entry, "current_wall_deposit_flux_kg_hr", currentFlux, species);
    };
    return [...names].sort((left, right) => {
      const leftValue = rank(left);
      const rightValue = rank(right);
      if (leftValue !== null && rightValue !== null && leftValue !== rightValue) return rightValue - leftValue;
      if (leftValue !== null && rightValue === null) return -1;
      if (leftValue === null && rightValue !== null) return 1;
      return left.localeCompare(right);
    });
  }

  function renderSpeciesTable(final, coating) {
    const aggregate = isRecord(final) && isRecord(final.wall_deposit_by_species_kg)
      ? final.wall_deposit_by_species_kg : null;
    const bySpecies = isRecord(coating) && isRecord(coating.by_species) ? coating.by_species : null;
    const current = isRecord(coating) && isRecord(coating.current) ? coating.current : null;
    const currentFlux = current && isRecord(current.wall_deposit_flux_kg_hr_by_species)
      ? current.wall_deposit_flux_kg_hr_by_species : null;
    const currentCumulative = current && isRecord(current.wall_deposit_cumulative_kg_by_species)
      ? current.wall_deposit_cumulative_kg_by_species : null;
    const names = speciesNames(aggregate, bySpecies, currentFlux, currentCumulative);
    if (!names.length) {
      return pending(
        "Per-species wall deposits pending",
        "No per-species terminal or coating-diagnostic entries were emitted. No zero or coating verdict is inferred.",
      );
    }
    const rows = names.map((species) => {
      const entry = bySpecies && isRecord(bySpecies[species]) ? bySpecies[species] : null;
      const aggregateValue = aggregate && own(aggregate, species) ? aggregate[species] : null;
      const flux = diagnosticValue(entry, "current_wall_deposit_flux_kg_hr", currentFlux, species);
      const replayCumulative = diagnosticValue(entry, "cumulative_wall_deposit_kg", currentCumulative, species);
      const watch = species === "SiO" ? `<span class="sec-p3-watch">SiO watch species</span>` : "";
      return `<tr><th scope="row">${speciesLabel(species)}${watch}</th>` +
        `<td class="num">${numberCell(aggregateValue, "kg")}</td>` +
        `<td class="num">${numberCell(flux, "kg/hr")}</td>` +
        `<td class="num">${numberCell(replayCumulative, "kg")}</td>` +
        `<td><div class="sec-p3-badges">${diagnosticBadges(entry) || badge("Diagnostic", "not emitted")}</div></td></tr>`;
    }).join("");
    const aggregateNote = aggregate ? "" : pending(
      "Terminal aggregate pending",
      "terminal.final wall deposit by species is absent or malformed. Diagnostic cumulative values are shown separately and are not substituted.",
    );
    return `<div class="table-wrap"><table><thead><tr><th>Species</th>` +
      `<th class="num">Terminal aggregate · kg</th><th class="num">Current coating flux · kg/hr</th>` +
      `<th class="num">Replay cumulative · kg</th><th>Diagnostic context</th></tr></thead>` +
      `<tbody>${rows}</tbody></table></div>${aggregateNote}`;
  }

  function renderSegmentBreakdown(final) {
    if (!isRecord(final) || !own(final, "deposit_by_surface_species_kg")) {
      return pending("Per-segment deposits pending", "No terminal per-segment wall-deposit map was emitted.");
    }
    const segments = final.deposit_by_surface_species_kg;
    if (!isRecord(segments)) {
      return pending("Per-segment deposits malformed", "The emitted terminal per-segment value is not a segment map.");
    }
    const rows = [];
    Object.keys(segments).sort().forEach((segment) => {
      const speciesMap = segments[segment];
      if (!isRecord(speciesMap)) return;
      Object.keys(speciesMap).sort().forEach((species) => {
        rows.push(`<tr><th scope="row">${esc(accountLabel(`process.wall_deposit_segment_${segment}`))}</th>` +
          `<td>${speciesLabel(species)}</td><td class="num">${numberCell(speciesMap[species], "kg")}</td></tr>`);
      });
    });
    if (!rows.length) {
      return pending("No per-segment entries emitted", "The segment map contains no numeric claims; this is not treated as zero deposition.");
    }
    return `<div class="table-wrap"><table><thead><tr><th>Wall segment</th><th>Species</th>` +
      `<th class="num">Terminal deposited mass · kg</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
  }

  function renderPumpOutlet(final) {
    if (!isRecord(final) || !own(final, "pump_outlet_by_species_kg")) {
      return pending("Pump-outlet context pending", "No per-species pump-outlet field was emitted.");
    }
    const outlet = final.pump_outlet_by_species_kg;
    if (!isRecord(outlet)) {
      return `<p class="sec-p3-state">${badge("Pump outlet", humanize(outlet))}</p>`;
    }
    const rows = Object.keys(outlet).sort().map((species) =>
      `<tr><th scope="row">${speciesLabel(species)}</th><td class="num">${numberCell(outlet[species], "kg")}</td></tr>`,
    );
    return rows.length
      ? `<div class="table-wrap"><table><thead><tr><th>Species</th><th class="num">Pump outlet · kg</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>`
      : pending("Pump-outlet entries absent", "The emitted pump-outlet map contains no per-species claims.");
  }

  function renderKnudsen(knudsen) {
    if (!isRecord(knudsen)) {
      return pending("Transport context pending", "The terminal Knudsen-regime diagnostic was not emitted.");
    }
    const chips = [];
    if (own(knudsen, "regime")) chips.push(badge("Regime", humanize(knudsen.regime)));
    if (isNumber(knudsen.knudsen_number)) chips.push(badge("Kn", fmtNum(knudsen.knudsen_number)));
    if (own(knudsen, "carrier_gas")) chips.push(badge("Carrier", prettySpecies(knudsen.carrier_gas)));
    chips.push(diagnosticBadges(knudsen));
    const geometry = isRecord(knudsen.stage_area_geometry_provenance_notice)
      ? knudsen.stage_area_geometry_provenance_notice : null;
    const geometryBadges = geometry
      ? diagnosticBadges(geometry) +
        (own(geometry, "message") ? badge("Details", "hover", scalarText(geometry.message)) : "")
      : "";
    if (!chips.join("") && !geometryBadges) {
      return pending("Transport context pending", "The terminal Knudsen-regime diagnostic contains no emitted context.");
    }
    return `<div class="sec-p3-badges">${chips.join("")}</div>` +
      (geometryBadges ? `<div class="sec-p3-geometry"><span>Coating geometry provenance</span><div class="sec-p3-badges">${geometryBadges}</div></div>` : "");
  }

  function renderLifetime(terminal) {
    const lifetime = isRecord(terminal) ? terminal.wall_lifetime : null;
    if (!isRecord(terminal) || !own(terminal, "wall_lifetime") ||
        !isRecord(lifetime) || !Object.keys(lifetime).length) {
      return `<div class="sec-p3-lifetime"><b>Wall lifetime not assessed</b>` +
        `<span>terminal wall-lifetime evidence is not emitted; no CLEAR or ruined verdict is issued.</span></div>`;
    }
    return `<div class="sec-p3-lifetime"><b>Wall lifetime diagnostic emitted</b>` +
      `<span>No viewer-derived pass/fail verdict.</span><div class="sec-p3-badges">${diagnosticBadges(lifetime)}</div></div>`;
  }

  function renderHourly(summary, hour) {
    const delta = isRecord(summary) && isRecord(summary.wall_deposit_delta_kg)
      ? summary.wall_deposit_delta_kg : null;
    const cumulative = isRecord(summary) && isRecord(summary.wall_deposit_cumulative_kg)
      ? summary.wall_deposit_cumulative_kg : null;
    const segments = new Set([
      ...Object.keys(delta || {}), ...Object.keys(cumulative || {}),
    ]);
    const rows = [];
    [...segments].sort().forEach((segment) => {
      const deltaSpecies = isRecord(delta && delta[segment]) ? delta[segment] : null;
      const cumulativeSpecies = isRecord(cumulative && cumulative[segment]) ? cumulative[segment] : null;
      const species = new Set([
        ...Object.keys(deltaSpecies || {}), ...Object.keys(cumulativeSpecies || {}),
      ]);
      [...species].sort().forEach((name) => {
        const deltaValue = deltaSpecies && own(deltaSpecies, name) ? deltaSpecies[name] : null;
        const cumulativeValue = cumulativeSpecies && own(cumulativeSpecies, name) ? cumulativeSpecies[name] : null;
        rows.push(`<tr><th scope="row">${esc(accountLabel(`process.wall_deposit_segment_${segment}`))}</th>` +
          `<td>${speciesLabel(name)}</td><td class="num">${numberCell(deltaValue, "kg/hour")}</td>` +
          `<td class="num">${numberCell(cumulativeValue, "kg")}</td></tr>`);
      });
    });
    const heading = `<div class="sec-p3-hour-label">Selected hour · ${esc(scalarText(hour, "not emitted"))}</div>`;
    if (!rows.length) {
      return heading + pending(
        "Hourly coating telemetry pending",
        "No segment/species deposit increment or running-load entries were emitted for the selected hour. No zero is inferred.",
      );
    }
    return heading + `<div class="table-wrap"><table><thead><tr><th>Wall segment</th><th>Species</th>` +
      `<th class="num">Hourly deposit · kg/hour</th><th class="num">Running wall load · kg</th>` +
      `</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
  }

  function render(artifact) {
    const terminal = isRecord(artifact && artifact.terminal) ? artifact.terminal : null;
    const final = terminal && isRecord(terminal.final) ? terminal.final : null;
    const metadata = terminal && isRecord(terminal.run_metadata) ? terminal.run_metadata : null;
    const coating = metadata && isRecord(metadata.pressure_coating_pareto_diagnostic)
      ? metadata.pressure_coating_pareto_diagnostic : null;
    const knudsen = metadata && isRecord(metadata.knudsen_regime_diagnostic)
      ? metadata.knudsen_regime_diagnostic : null;
    const timesteps = Array.isArray(artifact && artifact.timesteps) ? artifact.timesteps : [];
    const selected = timesteps.length ? timesteps[timesteps.length - 1] : null;
    const coatingFlags = diagnosticBadges(coating);
    return `<section id="sec-p3-wall-coating" class="sec-p3-wall-coating">` +
      `<h2><span class="sect">P3</span>Wall deposits &amp; coating</h2>` +
      `<p class="sub">Per-species wall inventory and emitted coating rate. Coating is continuous rate → lifetime evidence, not a viewer pass/fail gate.</p>` +
      `<div class="sec-p3-context"><div><span>Transport context</span>${renderKnudsen(knudsen)}</div>` +
      `<div><span>Coating replay authority</span><div class="sec-p3-badges">${coatingFlags || badge("Diagnostic", "not emitted")}</div></div></div>` +
      `<h3>Wall deposit by species</h3>${renderSpeciesTable(final, coating)}` +
      `<details class="sec-p3-details"><summary>Per-wall-segment terminal breakdown</summary>${renderSegmentBreakdown(final)}</details>` +
      `<details class="sec-p3-details"><summary>Selected-hour segment telemetry</summary>` +
      `<div id="sec-p3-wall-coating-hourly">${renderHourly(selected && selected.summary, selected && selected.hour)}</div></details>` +
      `<details class="sec-p3-details"><summary>Pump-outlet loss context</summary>${renderPumpOutlet(final)}</details>` +
      `${renderLifetime(terminal)}</section>`;
  }

  function onTimestep(artifact, index) {
    const target = root.document && typeof root.document.querySelector === "function"
      ? root.document.querySelector("#sec-p3-wall-coating-hourly") : null;
    if (!target) return;
    const timestep = Array.isArray(artifact && artifact.timesteps) ? artifact.timesteps[index] : null;
    target.innerHTML = renderHourly(timestep && timestep.summary, timestep && timestep.hour);
  }

  (root.ReportPanels = root.ReportPanels || []).push({
    id: "sec-p3-wall-coating", render, onTimestep,
  });
}(globalThis));
