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

  function numberClaim(record, key) {
    if (!isRecord(record) || !own(record, key)) return { state: "absent", value: null };
    if (typeof record[key] === "string" && !record[key].trim()) {
      return { state: "empty", value: record[key] };
    }
    return isNumber(record[key])
      ? { state: "value", value: record[key] }
      : { state: "malformed", value: record[key] };
  }

  function recordValueClaim(value, present) {
    if (!present) return { state: "absent", value: null };
    if (!isRecord(value)) return { state: "malformed", value };
    return Object.keys(value).length
      ? { state: "value", value }
      : { state: "empty", value };
  }

  function recordFieldClaim(record, key) {
    return recordValueClaim(isRecord(record) ? record[key] : undefined, isRecord(record) && own(record, key));
  }

  function arrayFieldClaim(record, key) {
    if (!isRecord(record) || !own(record, key)) return { state: "absent", value: null };
    const value = record[key];
    if (!Array.isArray(value)) return { state: "malformed", value };
    return value.length ? { state: "value", value } : { state: "empty", value };
  }

  function arrayElementClaim(arrayClaim, index) {
    if (!arrayClaim || !Array.isArray(arrayClaim.value)
      || !Number.isInteger(index) || index < 0 || index >= arrayClaim.value.length) {
      return { state: "absent", value: null };
    }
    return recordValueClaim(arrayClaim.value[index], true);
  }

  function malformedType(value) {
    if (value === null) return "null";
    if (Array.isArray(value)) return "array";
    return typeof value;
  }

  function claimText(claim) {
    if (claim.state === "absent") return "not emitted";
    if (claim.state === "empty") return "empty";
    if (claim.state === "malformed") return `malformed (${malformedType(claim.value)})`;
    return "emitted";
  }

  function claimBadge(label, claim) {
    return claim.state === "value" ? "" : badge(label, claimText(claim));
  }

  function claimNotice(label, claim) {
    if (claim.state === "absent" || claim.state === "value") return "";
    return pending(`${label} ${claim.state}`, `The emitted ${label.toLowerCase()} container is ${claimText(claim)}.`);
  }

  function numberCell(claim, unit) {
    if (claim.state === "value") return `<span class="num">${esc(fmtNum(claim.value, unit))}</span>`;
    return `<span class="sec-p3-missing">${esc(claimText(claim))}</span>`;
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
          : typeof raw === "string" && !raw.trim() ? "empty"
            : raw === null || raw === undefined ? `malformed (${raw === null ? "null" : "undefined"})`
              : typeof raw !== "string" ? `malformed (${malformedType(raw)})`
                : readable ? humanize(raw) : scalarText(raw);
      return badge(label, value);
    }).join("");
  }

  function speciesLabel(species) {
    return `<span class="sec-p3-species" title="${esc(species)}">` +
      `<i style="background:${esc(speciesColor(species))}" aria-hidden="true"></i>` +
      `${esc(prettySpecies(species))}</span>`;
  }

  function diagnosticClaim(entry, key, fallbackMap, species) {
    const direct = numberClaim(entry, key);
    return direct.state === "absent" ? numberClaim(fallbackMap, species) : direct;
  }

  function speciesNames(aggregate, bySpecies, currentFlux, currentCumulative) {
    const names = new Set();
    [aggregate, bySpecies, currentFlux, currentCumulative].forEach((record) => {
      if (isRecord(record)) Object.keys(record).forEach((name) => names.add(name));
    });
    const rank = (species) => {
      const terminal = numberClaim(aggregate, species);
      if (terminal.state === "value") return terminal.value;
      const entry = isRecord(bySpecies) && isRecord(bySpecies[species]) ? bySpecies[species] : null;
      const cumulative = diagnosticClaim(entry, "cumulative_wall_deposit_kg", currentCumulative, species);
      if (cumulative.state === "value") return cumulative.value;
      const flux = diagnosticClaim(entry, "current_wall_deposit_flux_kg_hr", currentFlux, species);
      return flux.state === "value" ? flux.value : null;
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

  function renderSpeciesTable(final, coatingClaim) {
    const coating = isRecord(coatingClaim && coatingClaim.value) ? coatingClaim.value : null;
    const aggregatePresent = isRecord(final) && own(final, "wall_deposit_by_species_kg");
    const aggregateRaw = aggregatePresent ? final.wall_deposit_by_species_kg : null;
    const aggregate = isRecord(aggregateRaw) ? aggregateRaw : null;
    const bySpeciesClaim = recordFieldClaim(coating, "by_species");
    const bySpecies = isRecord(bySpeciesClaim.value) ? bySpeciesClaim.value : null;
    const currentClaim = recordFieldClaim(coating, "current");
    const current = isRecord(currentClaim.value) ? currentClaim.value : null;
    const currentFluxClaim = recordFieldClaim(current, "wall_deposit_flux_kg_hr_by_species");
    const currentFlux = isRecord(currentFluxClaim.value) ? currentFluxClaim.value : null;
    const currentCumulativeClaim = recordFieldClaim(current, "wall_deposit_cumulative_kg_by_species");
    const currentCumulative = isRecord(currentCumulativeClaim.value) ? currentCumulativeClaim.value : null;
    const diagnosticNotes = [
      claimNotice("Per-species diagnostic map", bySpeciesClaim),
      claimNotice("Coating current map", currentClaim),
      current ? claimNotice("Current coating flux map", currentFluxClaim) : "",
      current ? claimNotice("Current coating cumulative map", currentCumulativeClaim) : "",
    ].join("");
    const aggregateNote = !aggregatePresent ? pending(
      "Terminal aggregate pending",
      "terminal.final wall deposit by species was not emitted. Diagnostic cumulative values are shown separately and are not substituted.",
    ) : !aggregate ? pending(
      "Terminal aggregate malformed",
      "The emitted terminal wall-deposit value is not a species map. Diagnostic cumulative values remain separate.",
    ) : !Object.keys(aggregate).length ? pending(
      "Terminal aggregate empty",
      "The terminal wall-deposit map was emitted but contains no species entries.",
    ) : "";
    const names = speciesNames(aggregate, bySpecies, currentFlux, currentCumulative);
    if (!names.length) {
      return pending(
        "Per-species wall deposits pending",
        "No per-species terminal or coating-diagnostic entries were emitted. No zero or coating verdict is inferred.",
      ) + aggregateNote + diagnosticNotes;
    }
    const rows = names.map((species) => {
      const entryClaim = recordFieldClaim(bySpecies, species);
      const entry = isRecord(entryClaim.value) ? entryClaim.value : null;
      const aggregateValue = numberClaim(aggregate, species);
      const flux = diagnosticClaim(entry, "current_wall_deposit_flux_kg_hr", currentFlux, species);
      const diagnosticCumulative = diagnosticClaim(entry, "cumulative_wall_deposit_kg", currentCumulative, species);
      const watch = species === "SiO" ? `<span class="sec-p3-watch">SiO watch species</span>` : "";
      const entryBadges = diagnosticBadges(entry)
        || (entryClaim.state === "absent"
          ? badge("Per-species status", "not emitted")
          : badge("Per-species entry", claimText(entryClaim)));
      return `<tr><th scope="row">${speciesLabel(species)}${watch}</th>` +
        `<td class="num">${numberCell(aggregateValue, "kg")}</td>` +
        `<td class="num">${numberCell(flux, "kg/hr")}</td>` +
        `<td class="num">${numberCell(diagnosticCumulative, "kg")}</td>` +
        `<td><div class="sec-p3-badges">${entryBadges}</div></td></tr>`;
    }).join("");
    return `<div class="table-wrap"><table><thead><tr><th>Species</th>` +
      `<th class="num">Terminal aggregate · kg</th><th class="num">Current coating flux · kg/hr</th>` +
      `<th class="num">Diagnostic cumulative · kg</th><th>Per-species diagnostic context</th></tr></thead>` +
      `<tbody>${rows}</tbody></table></div>${aggregateNote}${diagnosticNotes}`;
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
    const segmentNotes = [];
    Object.keys(segments).sort().forEach((segment) => {
      const speciesMapClaim = recordFieldClaim(segments, segment);
      const speciesMap = isRecord(speciesMapClaim.value) ? speciesMapClaim.value : null;
      if (speciesMapClaim.state !== "value") {
        segmentNotes.push(claimNotice(`Segment ${segment}`, speciesMapClaim));
      }
      if (!speciesMap) return;
      Object.keys(speciesMap).sort().forEach((species) => {
        rows.push(`<tr><th scope="row">${esc(accountLabel(`process.wall_deposit_segment_${segment}`))}</th>` +
          `<td>${speciesLabel(species)}</td><td class="num">${numberCell(numberClaim(speciesMap, species), "kg")}</td></tr>`);
      });
    });
    if (!rows.length) {
      return (segmentNotes.join("") || pending(
        "Per-segment map empty",
        "The emitted segment map contains no segment/species entries; this is not treated as zero deposition.",
      ));
    }
    return `<div class="table-wrap"><table><thead><tr><th>Wall segment</th><th>Species</th>` +
      `<th class="num">Terminal deposited mass · kg</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>` +
      segmentNotes.join("");
  }

  function renderPumpOutlet(final) {
    if (!isRecord(final) || !own(final, "pump_outlet_by_species_kg")) {
      return pending("Pump-outlet context pending", "No per-species pump-outlet field was emitted.");
    }
    const outlet = final.pump_outlet_by_species_kg;
    if (typeof outlet === "string") {
      return outlet.trim()
        ? `<p class="sec-p3-state">${badge("Pump outlet", humanize(outlet))}</p>`
        : pending("Pump-outlet context empty", "The pump-outlet field was emitted as an empty string.");
    }
    if (!isRecord(outlet)) return pending("Pump-outlet context malformed", "The emitted pump-outlet value is neither a state token nor a species map.");
    const rows = Object.keys(outlet).sort().map((species) =>
      `<tr><th scope="row">${speciesLabel(species)}</th><td class="num">${numberCell(numberClaim(outlet, species), "kg")}</td></tr>`,
    );
    return rows.length
      ? `<div class="table-wrap"><table><thead><tr><th>Species</th><th class="num">Pump outlet · kg</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>`
      : pending("Pump-outlet map empty", "The emitted pump-outlet map contains no per-species entries.");
  }

  function renderKnudsen(knudsenClaim) {
    if (!knudsenClaim || knudsenClaim.state === "absent") {
      return pending("Transport context pending", "The terminal Knudsen-regime diagnostic was not emitted.");
    }
    if (knudsenClaim.state === "malformed") {
      return pending("Transport context malformed", `The emitted Knudsen-regime diagnostic is ${claimText(knudsenClaim)}.`);
    }
    if (knudsenClaim.state === "empty") {
      return pending("Transport context empty", "The terminal Knudsen-regime diagnostic was emitted without context.");
    }
    const knudsen = knudsenClaim.value;
    const chips = [];
    if (own(knudsen, "regime")) {
      const regime = typeof knudsen.regime === "string" && knudsen.regime.trim()
        ? humanize(knudsen.regime)
        : typeof knudsen.regime === "string" ? "empty" : `malformed (${malformedType(knudsen.regime)})`;
      chips.push(badge("Regime", regime));
    }
    const kn = numberClaim(knudsen, "knudsen_number");
    if (kn.state !== "absent") chips.push(badge("Kn", kn.state === "value" ? fmtNum(kn.value) : claimText(kn)));
    if (own(knudsen, "carrier_gas")) {
      const carrier = typeof knudsen.carrier_gas === "string" && knudsen.carrier_gas.trim()
        ? prettySpecies(knudsen.carrier_gas)
        : typeof knudsen.carrier_gas === "string" ? "empty" : `malformed (${malformedType(knudsen.carrier_gas)})`;
      chips.push(badge("Carrier", carrier));
    }
    chips.push(diagnosticBadges(knudsen));
    const geometryClaim = recordFieldClaim(knudsen, "stage_area_geometry_provenance_notice");
    const geometry = isRecord(geometryClaim.value) ? geometryClaim.value : null;
    const geometryMessage = geometry && own(geometry, "message")
      ? typeof geometry.message === "string" && geometry.message.trim()
        ? badge("Details", "hover", geometry.message)
        : typeof geometry.message === "string"
          ? badge("Details", "empty")
          : badge("Details", `malformed (${malformedType(geometry.message)})`)
      : "";
    const geometryBadges = geometryClaim.state === "value"
      ? (diagnosticBadges(geometry) + geometryMessage || badge("Geometry notice", "emitted (no supported fields)"))
      : geometryClaim.state === "absent" ? "" : badge("Geometry notice", claimText(geometryClaim));
    if (!chips.join("") && !geometryBadges) {
      return pending("Transport context emitted", "The terminal Knudsen-regime diagnostic contains no P3-supported context fields.");
    }
    return `<div class="sec-p3-badges">${chips.join("")}</div>` +
      (geometryBadges ? `<div class="sec-p3-geometry"><span>Coating geometry provenance</span><div class="sec-p3-badges">${geometryBadges}</div></div>` : "");
  }

  function fieldLabel(key) {
    const readable = humanize(key);
    return readable ? readable.charAt(0).toUpperCase() + readable.slice(1) : readable;
  }

  function lifetimePayloadBadges(lifetime) {
    // Authority/status flags are rendered by diagnosticBadges; surface remaining
    // emitted payload keys without inventing a viewer pass/fail gate.
    const reserved = new Set([
      "status", "authoritative", "diagnostic_only", "extrapolation",
      "high_uncertainty", "provisional", "output_status", "source",
      "source_class", "reference", "skip_reason", "reason",
    ]);
    return Object.keys(lifetime).filter((key) => !reserved.has(key)).sort().map((key) => {
      const label = fieldLabel(key);
      const raw = lifetime[key];
      if (isNumber(raw)) return badge(label, fmtNum(raw));
      if (typeof raw === "boolean") return badge(label, raw ? "yes" : "no");
      if (typeof raw === "string") {
        return badge(label, raw.trim() ? humanize(raw) : "empty");
      }
      if (raw === null || raw === undefined) {
        return badge(label, `malformed (${raw === null ? "null" : "undefined"})`);
      }
      // Nested objects/arrays stay opaque — do not invent a flattened summary.
      return badge(label, `emitted (${malformedType(raw)})`);
    }).join("");
  }

  function renderLifetime(terminalClaim) {
    if (terminalClaim && terminalClaim.state === "malformed") {
      return `<div class="sec-p3-lifetime"><b>Wall lifetime source malformed</b>` +
        `<span>The terminal result is ${esc(claimText(terminalClaim))}; no viewer pass/fail verdict is issued.</span></div>`;
    }
    const terminal = terminalClaim && isRecord(terminalClaim.value) ? terminalClaim.value : null;
    const lifetimeClaim = recordFieldClaim(terminal, "wall_lifetime");
    if (lifetimeClaim.state === "absent") {
      return `<div class="sec-p3-lifetime"><b>Wall lifetime not assessed</b>` +
        `<span>Terminal wall-lifetime evidence was not emitted; no viewer pass/fail verdict is issued.</span></div>`;
    }
    if (lifetimeClaim.state === "malformed") {
      return `<div class="sec-p3-lifetime"><b>Wall lifetime malformed</b>` +
        `<span>The emitted wall-lifetime value is not a diagnostic object; no viewer pass/fail verdict is issued.</span></div>`;
    }
    if (lifetimeClaim.state === "empty") {
      return `<div class="sec-p3-lifetime"><b>Wall lifetime empty</b>` +
        `<span>The wall-lifetime object was emitted without evidence; no viewer pass/fail verdict is issued.</span></div>`;
    }
    const lifetime = lifetimeClaim.value;
    const flags = diagnosticBadges(lifetime);
    const payload = lifetimePayloadBadges(lifetime);
    return `<div class="sec-p3-lifetime"><b>Wall lifetime diagnostic emitted</b>` +
      `<span>No viewer-derived pass/fail verdict.</span>` +
      `<div class="sec-p3-badges">${flags}${payload}</div></div>`;
  }

  function nestedNumberClaim(containerClaim, key) {
    if (containerClaim.state === "malformed") {
      return { state: "malformed", value: containerClaim.value };
    }
    return numberClaim(isRecord(containerClaim.value) ? containerClaim.value : null, key);
  }

  function renderHourly(summaryClaim, hourClaim, timestepClaim, timestepsClaim) {
    const hourText = hourClaim && hourClaim.state === "value" ? fmtNum(hourClaim.value) : claimText(hourClaim || { state: "absent" });
    const heading = `<div class="sec-p3-hour-label">Selected hour · ${esc(hourText)}</div>`;
    if (timestepsClaim && timestepsClaim.state === "malformed") {
      return heading + pending("Timestep list malformed", `The emitted timesteps container is ${claimText(timestepsClaim)}.`);
    }
    if (timestepsClaim && timestepsClaim.state === "empty") {
      return heading + pending("Timestep list empty", "The timesteps array was emitted without entries.");
    }
    if (timestepClaim && timestepClaim.state === "malformed") {
      return heading + pending("Selected timestep malformed", `The selected timestep is ${claimText(timestepClaim)}.`);
    }
    if (timestepClaim && timestepClaim.state === "empty") {
      return heading + pending("Selected timestep empty", "The selected timestep was emitted without fields.");
    }
    if (summaryClaim && summaryClaim.state === "malformed") {
      return heading + pending("Selected-hour summary malformed", `The emitted summary is ${claimText(summaryClaim)}.`);
    }
    if (summaryClaim && summaryClaim.state === "empty") {
      return heading + pending("Selected-hour summary empty", "The selected-hour summary was emitted without telemetry fields.");
    }
    const summary = summaryClaim && isRecord(summaryClaim.value) ? summaryClaim.value : null;
    const deltaClaim = recordFieldClaim(summary, "wall_deposit_delta_kg");
    const delta = isRecord(deltaClaim.value) ? deltaClaim.value : null;
    const cumulativeClaim = recordFieldClaim(summary, "wall_deposit_cumulative_kg");
    const cumulative = isRecord(cumulativeClaim.value) ? cumulativeClaim.value : null;
    const mapNotes = [
      claimNotice("Hourly deposit map", deltaClaim),
      claimNotice("Running wall-load map", cumulativeClaim),
    ];
    const segments = new Set([
      ...Object.keys(delta || {}), ...Object.keys(cumulative || {}),
    ]);
    const rows = [];
    [...segments].sort().forEach((segment) => {
      const deltaSpeciesClaim = recordFieldClaim(delta, segment);
      const deltaSpecies = isRecord(deltaSpeciesClaim.value) ? deltaSpeciesClaim.value : null;
      const cumulativeSpeciesClaim = recordFieldClaim(cumulative, segment);
      const cumulativeSpecies = isRecord(cumulativeSpeciesClaim.value) ? cumulativeSpeciesClaim.value : null;
      if (deltaSpeciesClaim.state !== "absent" && deltaSpeciesClaim.state !== "value") {
        mapNotes.push(claimNotice(`Hourly segment ${segment}`, deltaSpeciesClaim));
      }
      if (cumulativeSpeciesClaim.state !== "absent" && cumulativeSpeciesClaim.state !== "value") {
        mapNotes.push(claimNotice(`Running-load segment ${segment}`, cumulativeSpeciesClaim));
      }
      const species = new Set([
        ...Object.keys(deltaSpecies || {}), ...Object.keys(cumulativeSpecies || {}),
      ]);
      [...species].sort().forEach((name) => {
        rows.push(`<tr><th scope="row">${esc(accountLabel(`process.wall_deposit_segment_${segment}`))}</th>` +
          `<td>${speciesLabel(name)}</td><td class="num">${numberCell(nestedNumberClaim(deltaSpeciesClaim, name), "kg/hour")}</td>` +
          `<td class="num">${numberCell(nestedNumberClaim(cumulativeSpeciesClaim, name), "kg")}</td></tr>`);
      });
    });
    const notes = mapNotes.join("");
    if (!rows.length) {
      return heading + (notes || pending(
        "Hourly coating telemetry pending",
        "No segment/species deposit increment or running-load entries were emitted for the selected hour. No zero is inferred.",
      ));
    }
    return heading + `<div class="table-wrap"><table><thead><tr><th>Wall segment</th><th>Species</th>` +
      `<th class="num">Hourly deposit · kg/hour</th><th class="num">Running wall load · kg</th>` +
      `</tr></thead><tbody>${rows.join("")}</tbody></table></div>${notes}`;
  }

  function render(artifact) {
    const artifactClaim = recordValueClaim(artifact, artifact !== null && artifact !== undefined);
    const artifactRecord = isRecord(artifactClaim.value) ? artifactClaim.value : null;
    const terminalClaim = recordFieldClaim(artifactRecord, "terminal");
    const terminal = isRecord(terminalClaim.value) ? terminalClaim.value : null;
    const finalClaim = recordFieldClaim(terminal, "final");
    const final = isRecord(finalClaim.value) ? finalClaim.value : null;
    const metadataClaim = recordFieldClaim(terminal, "run_metadata");
    const metadata = isRecord(metadataClaim.value) ? metadataClaim.value : null;
    const coatingClaim = recordFieldClaim(metadata, "pressure_coating_pareto_diagnostic");
    const knudsenClaim = recordFieldClaim(metadata, "knudsen_regime_diagnostic");
    const timestepsClaim = artifactClaim.state === "malformed"
      ? { state: "malformed", value: artifactClaim.value }
      : arrayFieldClaim(artifactRecord, "timesteps");
    const timesteps = Array.isArray(timestepsClaim.value) ? timestepsClaim.value : [];
    const selectedClaim = arrayElementClaim(timestepsClaim, timesteps.length - 1);
    const selected = isRecord(selectedClaim.value) ? selectedClaim.value : null;
    const summaryClaim = recordFieldClaim(selected, "summary");
    const hourClaim = numberClaim(selected, "hour");
    const coatingFlags = coatingClaim.state === "value"
      ? diagnosticBadges(coatingClaim.value)
      : claimBadge("Coating diagnostic", coatingClaim);
    const sourceBadges = [];
    if (artifactClaim.state !== "value") {
      sourceBadges.push(claimBadge("Report artifact", artifactClaim));
    } else if (terminalClaim.state !== "value") {
      sourceBadges.push(claimBadge("Terminal result", terminalClaim));
    } else {
      sourceBadges.push(claimBadge("Final summary", finalClaim));
      sourceBadges.push(claimBadge("Run metadata", metadataClaim));
    }
    if (artifactClaim.state === "value") sourceBadges.push(claimBadge("Timesteps", timestepsClaim));
    const sourceState = sourceBadges.join("")
      ? `<div class="sec-p3-badges">${sourceBadges.join("")}</div>` : "";
    return `<section id="sec-p3-wall-coating" class="sec-p3-wall-coating">` +
      `<h2><span class="sect">P3</span>Wall deposits &amp; coating</h2>` +
      `<p class="sub">Per-species wall inventory and emitted coating rate. Coating is continuous rate → lifetime evidence, not a viewer pass/fail gate.</p>` +
      `${sourceState}<div class="sec-p3-context"><div><span>Transport context</span>${renderKnudsen(knudsenClaim)}</div>` +
      `<div class="sec-p3-coating-status"><span>Coating replay diagnostic status</span>` +
      `<small>Diagnostic-only surface (schema); emitted status is operational.</small>` +
      `<div class="sec-p3-badges">${coatingFlags || badge("Diagnostic status", "not emitted")}</div></div></div>` +
      `<h3>Wall deposit by species</h3>${renderSpeciesTable(final, coatingClaim)}` +
      `<details class="sec-p3-details"><summary>Per-wall-segment terminal breakdown</summary>${renderSegmentBreakdown(final)}</details>` +
      `<details class="sec-p3-details"><summary>Selected-hour segment telemetry</summary>` +
      `<div id="sec-p3-wall-coating-hourly">${renderHourly(summaryClaim, hourClaim, selectedClaim, timestepsClaim)}</div></details>` +
      `<details class="sec-p3-details"><summary>Pump-outlet loss context</summary>${renderPumpOutlet(final)}</details>` +
      `${renderLifetime(terminalClaim)}</section>`;
  }

  function onTimestep(artifact, index) {
    const target = root.document && typeof root.document.querySelector === "function"
      ? root.document.querySelector("#sec-p3-wall-coating-hourly") : null;
    if (!target) return;
    const artifactClaim = recordValueClaim(artifact, artifact !== null && artifact !== undefined);
    const artifactRecord = isRecord(artifactClaim.value) ? artifactClaim.value : null;
    const timestepsClaim = artifactClaim.state === "malformed"
      ? { state: "malformed", value: artifactClaim.value }
      : arrayFieldClaim(artifactRecord, "timesteps");
    const timestepClaim = arrayElementClaim(timestepsClaim, index);
    const timestep = isRecord(timestepClaim.value) ? timestepClaim.value : null;
    target.innerHTML = renderHourly(
      recordFieldClaim(timestep, "summary"),
      numberClaim(timestep, "hour"),
      timestepClaim,
      timestepsClaim,
    );
  }

  (root.ReportPanels = root.ReportPanels || []).push({
    id: "sec-p3-wall-coating", render, onTimestep,
  });
}(globalThis));
