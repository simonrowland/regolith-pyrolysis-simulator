"use strict";

(function registerTapPanel(root) {
  const {
    scalarText, fmtNum, prettySpecies, speciesColor, esc
  } = root.ReportLabels;

  const isMap = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value);
  const hasNumber = (value) => typeof value === "number" && Number.isFinite(value);

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

  function statusChip(label, value, extraClass = "") {
    const rendered = value === undefined
      ? "not emitted"
      : typeof value === "boolean"
        ? (value ? "true" : "false")
        : readableStatus(value);
    const raw = value === undefined ? "not emitted" : value === null ? "malformed" : scalarText(value);
    return `<span class="chip sec-p2-chip ${extraClass}" title="${esc(raw)}"><b>${esc(label)}:</b> ${esc(rendered)}</span>`;
  }

  function speciesToken(species, value, unit) {
    return `<span class="sec-p2-species" style="--species-color:${esc(speciesColor(species))}">` +
      `<b>${esc(prettySpecies(species))}</b> ${exact(value, unit)}</span>`;
  }

  function mapTokens(
    value,
    unit,
    missingText,
    emptyText = "No species entries present in emitted map.",
    malformedText = "Emitted species map is malformed."
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
      .map(([species, amount]) => hasNumber(amount)
        ? speciesToken(species, amount, unit)
        : `<span class="trace"><b>${esc(prettySpecies(species))}</b> amount malformed.</span>`)
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

  function gradeBlock(pool) {
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
    const numeric = entries
      .filter(([, value]) => hasNumber(value) && value >= 0)
      .sort((a, b) => b[1] - a[1]);
    const positive = numeric.filter(([, value]) => value > 0);
    const hasMalformedValue = entries.some(([, value]) => !hasNumber(value) || value < 0);
    // Any non-numeric or negative composition value makes the map malformed for
    // headline purposes even when other shares are numeric — never promote a partial share as
    // "largest emitted" over a broken map.
    const headline = hasMalformedValue
      ? "Elemental pool composition malformed"
      : positive.length
      ? `${prettySpecies(positive[0][0])} ${fmtNum(positive[0][1], "wt%")} · largest emitted pool share`
      : entries.length
        ? "Elemental pool composition · no positive shares"
        : "Elemental pool composition · no species present";
    const chips = entries.length
      ? entries
        .sort((a, b) => {
          if (hasNumber(a[1]) && hasNumber(b[1])) return b[1] - a[1];
          return hasNumber(a[1]) ? -1 : hasNumber(b[1]) ? 1 : String(a[0]).localeCompare(String(b[0]));
        })
        .map(([species, amount]) => hasNumber(amount) && amount >= 0
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
      if (!hasNumber(composition[species]) || composition[species] < 0) {
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
      const hasValidRange = Object.prototype.hasOwnProperty.call(record, "valid_range_K");
      const validRange = !hasValidRange
        ? "not emitted"
        : Array.isArray(record.valid_range_K) && record.valid_range_K.length === 2
          ? `${exact(record.valid_range_K[0], "K")}–${exact(record.valid_range_K[1], "K")}`
          : "malformed";
      return `<div class="sec-p2-provenance">` +
        `<b style="--species-color:${esc(speciesColor(species))}">${esc(prettySpecies(species))}</b>` +
        `<span>${esc(readableStatus(record.status))}</span>` +
        `<span>Correlation temperature ${exact(record.temperature_K, "K")} · fitted range ${validRange}</span>` +
        `<span>${esc(record.source ?? "source not emitted")}</span></div>`;
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
      .map(([key, label]) => statusChip(label, value[key]))
      .join("");
  }

  function renderTapCard({ cardId, title, intent, pool, terminalAccount, interfaceReport, showAssumption = false }) {
    const grade = gradeBlock(pool);
    const poolExists = isMap(pool);
    const poolNotice = mapContainerNotice(pool, title, `pool-${cardId}`);
    return `<article class="card sec-p2-card" data-p2-card="${esc(cardId)}">` +
      `<div class="sec-p2-card-head"><div><div class="ct">${esc(title)}</div>` +
      `<div class="cbig">${esc(grade.headline)}</div></div>${showAssumption ? assumptionChip(interfaceReport) : ""}</div>` +
      `${poolNotice}` +
      `<p class="sec-p2-intent">${esc(intent)}</p>` +
      `<div class="sec-p2-grade" aria-label="${esc(`${title} emitted elemental pool composition by weight`)}">${grade.chips}</div>` +
      `<div class="sec-p2-contaminants" aria-label="${esc(`${title} cobalt and nickel composition shares`)}">${grade.contaminants}</div>` +
      `<div class="sec-p2-kv"><span>Pool density</span><b>${poolExists ? exact(pool.density_kg_m3, "kg/m³") : "not emitted"}</b></div>` +
      `<div class="sec-p2-kv"><span>Buoyancy verdict</span><b>${esc(buoyancySummary(pool))}</b></div>` +
      `<details class="sec-p2-details"><summary>Phase amounts, final-state process pool, and diagnostic provenance</summary>` +
      `<h3>Stratified pool · mol by species</h3><div class="sec-p2-species-list">${mapTokens(pool?.species_mol, "mol", "Pool species mol map not emitted.")}</div>` +
      `<h3>Final-state process pool · mol by species</h3><div class="sec-p2-species-list">${mapTokens(terminalAccount, "mol", "Final-state process-pool mol map not emitted.")}</div>` +
      `<h3>Buoyancy diagnostic</h3>${renderBuoyancy(pool)}` +
      `<h3>Density-correlation provenance</h3>${renderDensityProvenance(pool)}` +
      `<div class="sec-p2-flags" data-p2-flags="pool">${explicitAuthorityFlags(pool)}</div></details></article>`;
  }

  function renderUnclassifiedStaging(value) {
    return `<details class="card sec-p2-interface sec-p2-unclassified" data-p2-region="unclassified">` +
      `<summary>Unclassified metal staging · mol by species</summary>` +
      `<div class="sec-p2-species-list">${mapTokens(
        value,
        "mol",
        "Unclassified metal staging mol map not emitted.",
        "No unclassified species present in emitted map.",
        "Unclassified metal staging mol map is malformed."
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
    const poolsValue = stratification?.pools;
    const pools = isMap(poolsValue) ? poolsValue : {};
    const interfaceReport = stratification?.interface;
    const hour = selected ? esc(scalarText(selected.hour, "not emitted")) : "not emitted";
    const absent = selected
      ? ""
      : `<div class="pending sec-p2-panel-pending"><strong>Pending metal-phase stratification</strong><p>No timestep emitted metal-phase stratification. Elemental pool composition, density, buoyancy, and interface geometry remain pending; final-state process-pool mol accounts are shown without conversion.</p></div>`;
    const malformed = selected && !isMap(stratification)
      ? `<div class="pending sec-p2-panel-pending" data-p2-container="stratification"><strong>Malformed metal-phase stratification</strong><p>The latest emitted stratification value is not a map. Older timestep reports are not substituted.</p></div>`
      : "";
    const provenanceValue = stratification?.provenance;
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
      `<p class="sub">Latest emitted stratification (hour ${hour}) plus final-state process-pool accounts. Elemental pool-composition wt% is backend-emitted; final-state pool amounts remain mol. Inventory is product-classified; no drain tap is simulated.</p>` +
      `${absent}${malformed}${containerNotices}${terminalNotice}${finalStateNotice}<div class="sec-p2-flags" data-p2-flags="stratification">` +
      `${statusChip("Diagnostic status", stratification?.status)}` +
      `${statusChip("Mode", stratification?.mode)}` +
      `${statusChip("Extraction behavior", stratification?.existing_extraction_behavior)}` +
      `${statusChip("Melt-density tier", stratification?.melt_density_tier)}` +
      `${statusChip("Melt-density fallback engaged", stratification?.melt_density_fallback_engaged, stratification?.melt_density_fallback_engaged === true ? "sec-p2-warning" : "")}` +
      `${statusChip("Account-state source", provenance?.account_state_source)}` +
      `${statusChip("Diagnostic derivation", provenance?.diagnostic_derivation)}` +
      `${explicitAuthorityFlags(stratification)}</div>` +
      `<div class="sec-p2-state" data-p2-region="state"><div><span>Emitted temperature</span><b>${exact(stratification?.temperature_K, "K")}</b></div>` +
      `<div><span>Emitted melt density</span><b>${exact(stratification?.melt_density_kg_m3, "kg/m³")}</b></div></div>` +
      `<div class="sec-p2-grid">` +
      `${renderTapCard({ cardId: "float", title: "Upper float-layer inventory", intent: "Al/Si float-layer product inventory", pool: pools.float_layer, terminalAccount: finalState["process.metal_phase_float_layer"], interfaceReport, showAssumption: true })}` +
      `${renderTapCard({ cardId: "bottom", title: "Bottom-pool inventory", intent: "Fe/FeSi bottom-pool product inventory", pool: pools.bottom_pool, terminalAccount: finalState["process.metal_phase_bottom_pool"], interfaceReport })}` +
      `</div>${renderUnclassifiedStaging(stratification?.unclassified_staging_mol)}${renderInterface(interfaceReport)}` +
      `<div class="note"><b>Frozen kg product-pool views pending producer attach.</b> The artifact does not carry kg projections for these process pools; no kg composition or wt% is reconstructed from final-state mol accounts.</div>` +
      `</section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({ id: "sec-p2-taps", render });
}(globalThis));
