"use strict";

(function registerTapPanel(root) {
  const {
    scalarText, fmtNum, prettySpecies, speciesColor, esc
  } = root.ReportLabels;

  const isMap = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value);
  const hasNumber = (value) => typeof value === "number" && Number.isFinite(value);
  const isValidWtShare = (value) => hasNumber(value) && value >= 0 && value <= 100;

  function exact(value, unit = "") {
    if (value === undefined) return "not emitted";
    if (!hasNumber(value)) return "malformed";
    const raw = `${String(value)}${unit ? ` ${unit}` : ""}`;
    return `<span title="${esc(raw)}">${esc(fmtNum(value, unit))}</span>`;
  }

  function readableStatus(value) {
    const known = {
      diagnostic_only_no_tap_gate: "Diagnostic only · no tap gate",
      unchanged_diagnostic_only: "Extraction behavior unchanged · diagnostic only",
      engine_liquid_eos: "Engine liquid EOS",
      fallback_basaltic_melt_constant_engine_density_unavailable: "Fallback basaltic-melt constant · engine density unavailable",
      extrapolated_below_valid_range: "Extrapolated below valid range",
      extrapolated_above_valid_range: "Extrapolated above valid range",
      within_valid_range: "Within valid range",
      sink: "Sink",
      float: "Float",
      ambiguous: "Buoyancy ambiguous",
      "BUOYANCY-AMBIGUOUS": "Buoyancy ambiguous"
    };
    if (value === undefined) return "not emitted";
    if (typeof value !== "string") return "malformed";
    if (!value.trim()) return "empty";
    return known[value] || value.replaceAll("_", " ");
  }

  // Status / mode / tier / source fields are non-empty strings at the emitter.
  // Wrong types (boolean, number, null, object) are malformed, not claims.
  function stringStatusValue(value) {
    return readableStatus(value);
  }

  // Authority and fallback flags are real booleans at the emitter.
  // Strings like "true"/"false" and other types are malformed, not coerced.
  function booleanStatusValue(value) {
    if (value === undefined) return "not emitted";
    if (typeof value !== "boolean") return "malformed";
    return value ? "true" : "false";
  }

  function statusChip(label, value, extraClass = "", kind = "string") {
    const rendered = kind === "boolean"
      ? booleanStatusValue(value)
      : stringStatusValue(value);
    const raw = value === undefined
      ? "not emitted"
      : value === null
        ? "malformed"
        : scalarText(value);
    return `<span class="chip sec-p2-chip ${extraClass}" title="${esc(raw)}"><b>${esc(label)}:</b> ${esc(rendered)}</span>`;
  }

  function speciesToken(species, value, unit) {
    return `<span class="sec-p2-species" style="--species-color:${esc(speciesColor(species))}">` +
      `<b>${esc(prettySpecies(species))}</b> ${exact(value, unit)}</span>`;
  }

  // Inventory maps are non-negative mol amounts at the emitter. Negative amounts
  // are malformed inventory, not measured data; exact zero is preserved.
  function mapTokens(
    value,
    unit,
    missingText,
    emptyText = "No species entries present in emitted map.",
    malformedText = "Emitted species map is malformed.",
    { nonNegative = false } = {}
  ) {
    if (value === undefined) {
      return `<span class="trace">${esc(missingText)}</span>`;
    }
    if (!isMap(value)) return `<span class="trace">${esc(malformedText)}</span>`;
    const entries = Object.entries(value);
    if (!entries.length) return `<span class="trace">${esc(emptyText)}</span>`;
    return entries
      .sort((a, b) => {
        if (hasNumber(a[1]) && hasNumber(b[1])) return b[1] - a[1];
        return hasNumber(a[1]) ? -1 : hasNumber(b[1]) ? 1 : String(a[0]).localeCompare(String(b[0]));
      })
      .map(([species, amount]) => {
        if (!hasNumber(amount) || (nonNegative && amount < 0)) {
          return `<span class="trace"><b>${esc(prettySpecies(species))}</b> amount malformed.</span>`;
        }
        return speciesToken(species, amount, unit);
      })
      .join("");
  }

  function mapContainerNotice(value, label, region) {
    let state;
    let detail;
    if (value === undefined) {
      state = "Not emitted";
      detail = `${label} was not emitted.`;
    } else if (!isMap(value)) {
      state = "Malformed";
      detail = `Emitted ${label} is not a map.`;
    } else if (!Object.keys(value).length) {
      state = "Empty";
      detail = `Emitted ${label} map is empty.`;
    } else {
      return "";
    }
    return `<div class="pending sec-p2-inline-pending" data-p2-container="${esc(region)}">` +
      `<strong>${state}</strong><p>${esc(detail)}</p></div>`;
  }

  // When the parent pools container is present but unreadable, do not claim each
  // child inventory was "not emitted" — that misstates a parent parse failure.
  function poolsParentUnavailableNotice(cardId) {
    return `<div class="pending sec-p2-inline-pending" data-p2-container="pool-${esc(cardId)}-unavailable">` +
      `<strong>Unavailable</strong>` +
      `<p>Stratification pools container malformed — child inventory unavailable.</p></div>`;
  }

  function gradeBlock(pool, poolsParentMalformed = false) {
    if (poolsParentMalformed) {
      return {
        headline: "Elemental pool composition unavailable",
        chips: `<div class="pending sec-p2-inline-pending"><strong>Unavailable</strong><p>Stratification pools container malformed — child composition unavailable. Pool mol amounts are not converted into wt%.</p></div>`,
        contaminants: `<span class="trace">Co and Ni composition shares unavailable · pools container malformed.</span>`
      };
    }
    const hasComposition = isMap(pool) && Object.prototype.hasOwnProperty.call(pool, "composition_wt_pct");
    if (!hasComposition) {
      return {
        headline: "Elemental pool composition pending",
        chips: `<div class="pending sec-p2-inline-pending"><strong>Not emitted</strong><p>Elemental pool composition by weight was not emitted. Pool mol amounts are not converted into wt%.</p></div>`,
        contaminants: `<span class="trace">Co and Ni composition shares not emitted.</span>`
      };
    }
    if (!isMap(pool.composition_wt_pct)) {
      return {
        headline: "Elemental pool composition malformed",
        chips: `<div class="pending sec-p2-inline-pending"><strong>Malformed</strong><p>Emitted elemental pool composition by weight is not a species map. Pool mol amounts are not converted into wt%.</p></div>`,
        contaminants: `<span class="trace">Co and Ni composition shares unavailable · emitted composition malformed.</span>`
      };
    }

    const composition = pool.composition_wt_pct;
    const entries = Object.entries(composition);
    // Emitter pool_weight_percent shares are in [0, 100]. Values outside that
    // closed range (or non-finite) are malformed inventory, not data.
    const hasMalformedValue = entries.some(([, value]) => !isValidWtShare(value));
    const positive = entries
      .filter(([, value]) => isValidWtShare(value) && value > 0)
      .sort((a, b) => b[1] - a[1]);
    // Viewer selects the top share from the emitted map — label as viewer-computed.
    const headline = hasMalformedValue
      ? "Elemental pool composition malformed"
      : positive.length
      ? `Viewer-computed top species: ${prettySpecies(positive[0][0])} ${fmtNum(positive[0][1], "wt%")}`
      : entries.length
        ? "Elemental pool composition · no positive shares"
        : "Elemental pool composition · no species present";
    const chips = entries.length
      ? entries
        .sort((a, b) => {
          if (hasNumber(a[1]) && hasNumber(b[1])) return b[1] - a[1];
          return hasNumber(a[1]) ? -1 : hasNumber(b[1]) ? 1 : String(a[0]).localeCompare(String(b[0]));
        })
        .map(([species, amount]) => isValidWtShare(amount)
          ? speciesToken(species, amount, "wt%")
          : `<span class="trace"><b>${esc(prettySpecies(species))}</b> composition value malformed.</span>`)
        .join("")
      : `<span class="trace">No species present in emitted composition.</span>`;
    // Emitter (pool_weight_percent) only emits positive-mass species keys; an
    // absent Co/Ni key on a present map means "not in this pool", not "composition
    // field withheld." Reserve "not emitted" for a missing composition map.
    const contaminants = ["Co", "Ni"].map((species) => {
      if (!Object.prototype.hasOwnProperty.call(composition, species)) {
        const state = hasMalformedValue
          ? `${species} presence indeterminate · emitted composition malformed.`
          : `${species} not present in emitted composition.`;
        return `<span class="trace">${esc(state)}</span>`;
      }
      if (!isValidWtShare(composition[species])) {
        return `<span class="trace">${esc(species)} composition share malformed.</span>`;
      }
      if (composition[species] === 0) {
        return `<span class="trace"><b>${esc(prettySpecies(species))}</b> emitted 0 wt% · not a positive contaminant.</span>`;
      }
      return `<span class="sec-p2-contaminant" style="--species-color:${esc(speciesColor(species))}">` +
        `<b>${esc(prettySpecies(species))} contaminant</b> ${exact(composition[species], "wt%")}</span>`;
    }).join("");
    return { headline, chips, contaminants };
  }

  function booleanState(container, key) {
    if (container !== undefined && !isMap(container)) return "malformed";
    if (!isMap(container) || !Object.prototype.hasOwnProperty.call(container, key)) {
      return "not emitted";
    }
    if (container[key] === true) return "load-bearing";
    if (container[key] === false) return "not load-bearing";
    return "malformed";
  }

  function assumptionChip(interfaceReport) {
    const value = interfaceReport?.aggressive_float_tap_assumption_load_bearing;
    const state = booleanState(interfaceReport, "aggressive_float_tap_assumption_load_bearing");
    const label = `Aggressive float-tap assumption: ${state}`;
    return `<span class="chip sec-p2-chip ${value === true ? "sec-p2-warning" : ""}">${esc(label)}</span>`;
  }

  function provenanceSourceText(value) {
    if (value === undefined) return "source not emitted";
    if (typeof value !== "string") return "malformed";
    if (!value.trim()) return "empty";
    return value;
  }

  function fittedRangeText(record) {
    if (!Object.prototype.hasOwnProperty.call(record, "valid_range_K")) {
      return "not emitted";
    }
    const range = record.valid_range_K;
    // Both endpoints must be finite numbers; a half-good pair is wholly malformed.
    // Do not promote the other bound with a dash (avoids "malformed–1,190 K").
    if (
      !Array.isArray(range) ||
      range.length !== 2 ||
      !hasNumber(range[0]) ||
      !hasNumber(range[1])
    ) {
      return "malformed";
    }
    return `${exact(range[0], "K")}–${exact(range[1], "K")}`;
  }

  function renderDensityProvenance(pool) {
    const provenance = pool?.density_correlation_provenance;
    const containerNotice = mapContainerNotice(
      provenance,
      "density-correlation provenance",
      "density-provenance"
    );
    if (containerNotice) return containerNotice;
    return Object.entries(provenance).map(([species, record]) => {
      if (!isMap(record)) {
        return `<div class="sec-p2-provenance"><b>${esc(prettySpecies(species))}</b><span>Malformed provenance record.</span></div>`;
      }
      const validRange = fittedRangeText(record);
      return `<div class="sec-p2-provenance">` +
        `<b style="--species-color:${esc(speciesColor(species))}">${esc(prettySpecies(species))}</b>` +
        `<span>${esc(readableStatus(record.status))}</span>` +
        `<span>Correlation temperature ${exact(record.temperature_K, "K")} · fitted range ${validRange}</span>` +
        `<span>${esc(provenanceSourceText(record.source))}</span></div>`;
    }).join("");
  }

  function renderBuoyancy(pool) {
    const buoyancy = pool?.buoyancy;
    const containerNotice = mapContainerNotice(
      buoyancy,
      "buoyancy diagnostic",
      "buoyancy"
    );
    if (containerNotice) return containerNotice;
    return `<div class="sec-p2-kv"><span>Emitted verdict</span><b>${esc(readableStatus(buoyancy.verdict))}</b></div>` +
      `<div class="sec-p2-kv"><span>Alloy density</span><b>${exact(buoyancy.alloy_density_kg_m3, "kg/m³")}</b></div>` +
      `<div class="sec-p2-kv"><span>Melt density</span><b>${exact(buoyancy.melt_density_kg_m3, "kg/m³")}</b></div>` +
      `<div class="sec-p2-kv"><span>Density contrast</span><b>${exact(buoyancy.delta_rho_kg_m3, "kg/m³")}</b></div>` +
      `<div class="sec-p2-kv"><span>Ambiguity threshold</span><b>${exact(buoyancy.ambiguity_threshold_kg_m3, "kg/m³")}</b></div>` +
      `<div class="sec-p2-kv"><span>Melt-density uncertainty</span><b>${exact(buoyancy.melt_density_uncertainty_kg_m3, "kg/m³")}</b></div>` +
      `<div class="sec-p2-kv"><span>Alloy-density uncertainty</span><b>${exact(buoyancy.alloy_density_uncertainty_kg_m3, "kg/m³")}</b></div>`;
  }

  function buoyancySummary(pool) {
    if (pool === undefined) return "not emitted";
    if (!isMap(pool)) return "malformed";
    const buoyancy = pool.buoyancy;
    if (buoyancy === undefined) return "not emitted";
    if (!isMap(buoyancy)) return "malformed";
    if (!Object.keys(buoyancy).length) return "empty";
    return readableStatus(buoyancy.verdict);
  }

  function explicitAuthorityFlags(value) {
    if (!isMap(value)) return "";
    const labels = {
      authoritative: "Authoritative",
      diagnostic_only: "Diagnostic only",
      extrapolation: "Extrapolation",
      high_uncertainty: "High uncertainty"
    };
    return Object.entries(labels)
      .filter(([key]) => Object.prototype.hasOwnProperty.call(value, key))
      .map(([key, label]) => statusChip(label, value[key], "", "boolean"))
      .join("");
  }

  function renderTapCard({
    cardId,
    title,
    intent,
    pool,
    terminalAccount,
    interfaceReport,
    showAssumption = false,
    poolsParentMalformed = false
  }) {
    const grade = gradeBlock(pool, poolsParentMalformed);
    const poolExists = isMap(pool);
    // Parent pools unreadable → one unavailable notice; do not say "not emitted".
    const poolNotice = poolsParentMalformed
      ? poolsParentUnavailableNotice(cardId)
      : mapContainerNotice(pool, title, `pool-${cardId}`);
    const densityValue = poolsParentMalformed
      ? "unavailable"
      : poolExists
        ? exact(pool.density_kg_m3, "kg/m³")
        : "not emitted";
    const speciesMol = poolsParentMalformed
      ? `<span class="trace">Pool species mol map unavailable · pools container malformed.</span>`
      : mapTokens(pool?.species_mol, "mol", "Pool species mol map not emitted.", undefined, undefined, { nonNegative: true });
    return `<article class="card sec-p2-card" data-p2-card="${esc(cardId)}">` +
      `<div class="sec-p2-card-head"><div><div class="ct">${esc(title)}</div>` +
      `<div class="cbig">${esc(grade.headline)}</div></div>${showAssumption ? assumptionChip(interfaceReport) : ""}</div>` +
      `${poolNotice}` +
      `<p class="sec-p2-intent">${esc(intent)}</p>` +
      `<div class="sec-p2-grade" aria-label="${esc(`${title} emitted elemental pool composition by weight`)}">${grade.chips}</div>` +
      `<div class="sec-p2-contaminants" aria-label="${esc(`${title} cobalt and nickel composition shares`)}">${grade.contaminants}</div>` +
      `<div class="sec-p2-kv"><span>Pool density</span><b>${densityValue}</b></div>` +
      `<div class="sec-p2-kv"><span>Buoyancy verdict</span><b>${esc(poolsParentMalformed ? "unavailable" : buoyancySummary(pool))}</b></div>` +
      `<details class="sec-p2-details"><summary>Phase amounts, final-state process pool, and diagnostic provenance</summary>` +
      `<h3>Stratified pool · mol by species</h3><div class="sec-p2-species-list">${speciesMol}</div>` +
      `<h3>Final-state process pool · mol by species</h3><div class="sec-p2-species-list">${mapTokens(terminalAccount, "mol", "Final-state process-pool mol map not emitted.", undefined, undefined, { nonNegative: true })}</div>` +
      `<h3>Buoyancy diagnostic</h3>${poolsParentMalformed ? `<span class="trace">Buoyancy diagnostic unavailable · pools container malformed.</span>` : renderBuoyancy(pool)}` +
      `<h3>Density-correlation provenance</h3>${poolsParentMalformed ? `<span class="trace">Density-correlation provenance unavailable · pools container malformed.</span>` : renderDensityProvenance(pool)}` +
      `<div class="sec-p2-flags" data-p2-flags="pool">${poolExists ? explicitAuthorityFlags(pool) : ""}</div></details></article>`;
  }

  function renderUnclassifiedStaging(value) {
    return `<details class="card sec-p2-interface sec-p2-unclassified" data-p2-region="unclassified">` +
      `<summary>Unclassified metal staging · mol by species</summary>` +
      `<div class="sec-p2-species-list">${mapTokens(
        value,
        "mol",
        "Unclassified metal staging mol map not emitted.",
        "No unclassified species present in emitted map.",
        "Unclassified metal staging mol map is malformed.",
        { nonNegative: true }
      )}</div></details>`;
  }

  function renderInterface(interfaceReport) {
    const containerNotice = mapContainerNotice(
      interfaceReport,
      "float-layer interface",
      "interface"
    );
    if (containerNotice) return containerNotice;
    const assumptionState = booleanState(
      interfaceReport,
      "aggressive_float_tap_assumption_load_bearing"
    );
    return `<details class="card sec-p2-interface"><summary>Float-layer interface geometry</summary>` +
      `<div class="sec-p2-interface-grid">` +
      `<div class="sec-p2-kv"><span>Interface area</span><b>${exact(interfaceReport.area_m2, "m²")}</b></div>` +
      `<div class="sec-p2-kv"><span>Float-layer mass</span><b>${exact(interfaceReport.float_layer_mass_kg, "kg")}</b></div>` +
      `<div class="sec-p2-kv"><span>Equivalent film thickness</span><b>${exact(interfaceReport.equivalent_film_thickness_m, "m")}</b></div>` +
      `<div class="sec-p2-kv"><span>Coverage reference thickness</span><b>${exact(interfaceReport.coverage_reference_thickness_m, "m")}</b></div>` +
      `<div class="sec-p2-kv"><span>Coverage fraction at reference thickness</span><b>${exact(interfaceReport.coverage_fraction_at_reference_thickness)}</b></div>` +
      `<div class="sec-p2-kv"><span>Aggressive float-tap assumption</span><b>${assumptionState}</b></div>` +
      `</div></details>`;
  }

  function latestStratification(artifact) {
    const timesteps = Array.isArray(artifact?.timesteps) ? artifact.timesteps : [];
    for (let index = timesteps.length - 1; index >= 0; index -= 1) {
      const summary = timesteps[index]?.summary;
      if (!isMap(summary) || !Object.prototype.hasOwnProperty.call(summary, "metal_phase_stratification")) continue;
      return { report: summary.metal_phase_stratification, hour: timesteps[index]?.hour };
    }
    return null;
  }

  function render(artifact) {
    const terminalValue = artifact?.terminal;
    const finalStateValue = isMap(terminalValue) ? terminalValue.final_state : undefined;
    const finalState = isMap(finalStateValue) ? finalStateValue : {};
    const selected = latestStratification(artifact);
    const stratification = selected?.report;
    const poolsValue = isMap(stratification) ? stratification.pools : undefined;
    // Present non-map pools → parent malformed. Do not treat as empty {} missing children.
    const poolsParentMalformed = isMap(stratification)
      && Object.prototype.hasOwnProperty.call(stratification, "pools")
      && !isMap(poolsValue);
    const pools = isMap(poolsValue) ? poolsValue : {};
    const interfaceReport = isMap(stratification) ? stratification.interface : undefined;
    const hour = selected ? esc(scalarText(selected.hour, "not emitted")) : "not emitted";
    const absent = selected
      ? ""
      : `<div class="pending sec-p2-panel-pending"><strong>Pending metal-phase stratification</strong><p>No timestep emitted metal-phase stratification. Elemental pool composition, density, buoyancy, and interface geometry remain pending; final-state process-pool mol accounts are shown without conversion.</p></div>`;
    const malformed = selected && !isMap(stratification)
      ? `<div class="pending sec-p2-panel-pending" data-p2-container="stratification"><strong>Malformed metal-phase stratification</strong><p>The latest emitted stratification value is not a map. Older timestep reports are not substituted.</p></div>`
      : "";
    const provenanceValue = isMap(stratification) ? stratification.provenance : undefined;
    const provenance = isMap(provenanceValue) ? provenanceValue : null;
    const containerNotices = isMap(stratification)
      ? mapContainerNotice(poolsValue, "stratification pools", "pools") +
        mapContainerNotice(provenanceValue, "stratification provenance", "provenance")
      : "";
    const terminalNotice = mapContainerNotice(terminalValue, "terminal artifact", "terminal");
    const finalStateNotice = isMap(terminalValue)
      ? mapContainerNotice(finalStateValue, "terminal final-state", "final-state")
      : "";

    return `<section id="sec-p2-taps" class="sec-p2-taps">` +
      `<h2><span class="sect">P2</span>Metal-pool product inventory &amp; stratification</h2>` +
      // Scope: stratification accounts are not a drain tap; do not deny run-wide drain taps.
      `<p class="sub">Latest emitted stratification (hour ${hour}) plus final-state process-pool accounts. Elemental pool-composition wt% is backend-emitted; final-state pool amounts remain mol. Inventory is product-classified. These stratification pool accounts do not represent a fired drain tap.</p>` +
      `${absent}${malformed}${containerNotices}${terminalNotice}${finalStateNotice}<div class="sec-p2-flags" data-p2-flags="stratification">` +
      `${statusChip("Diagnostic status", stratification?.status, "", "string")}` +
      `${statusChip("Mode", stratification?.mode, "", "string")}` +
      `${statusChip("Extraction behavior", stratification?.existing_extraction_behavior, "", "string")}` +
      `${statusChip("Melt-density tier", stratification?.melt_density_tier, "", "string")}` +
      `${statusChip("Melt-density fallback engaged", stratification?.melt_density_fallback_engaged, stratification?.melt_density_fallback_engaged === true ? "sec-p2-warning" : "", "boolean")}` +
      `${statusChip("Account-state source", provenance?.account_state_source, "", "string")}` +
      `${statusChip("Diagnostic derivation", provenance?.diagnostic_derivation, "", "string")}` +
      `${explicitAuthorityFlags(stratification)}</div>` +
      `<div class="sec-p2-state" data-p2-region="state"><div><span>Emitted temperature</span><b>${exact(stratification?.temperature_K, "K")}</b></div>` +
      `<div><span>Emitted melt density</span><b>${exact(stratification?.melt_density_kg_m3, "kg/m³")}</b></div></div>` +
      `<div class="sec-p2-grid">` +
      `${renderTapCard({ cardId: "float", title: "Upper float-layer inventory", intent: "Al/Si float-layer product inventory", pool: pools.float_layer, terminalAccount: finalState["process.metal_phase_float_layer"], interfaceReport, showAssumption: true, poolsParentMalformed })}` +
      `${renderTapCard({ cardId: "bottom", title: "Bottom-pool inventory", intent: "Fe/FeSi bottom-pool product inventory", pool: pools.bottom_pool, terminalAccount: finalState["process.metal_phase_bottom_pool"], interfaceReport, poolsParentMalformed })}` +
      `</div>${renderUnclassifiedStaging(isMap(stratification) ? stratification.unclassified_staging_mol : undefined)}${renderInterface(interfaceReport)}` +
      `<div class="note"><b>Frozen kg product-pool views pending producer attach.</b> The artifact does not carry kg projections for these process pools; no kg composition or wt% is reconstructed from final-state mol accounts.</div>` +
      `</section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({ id: "sec-p2-taps", render });
}(globalThis));
