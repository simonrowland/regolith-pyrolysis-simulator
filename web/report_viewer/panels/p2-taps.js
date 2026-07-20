"use strict";

(function registerTapPanel(root) {
  const {
    scalarText, fmtNum, prettySpecies, speciesColor, esc
  } = root.ReportLabels;

  const isMap = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value);
  const hasNumber = (value) => typeof value === "number" && Number.isFinite(value);

  function exact(value, unit = "") {
    if (!hasNumber(value)) return "not emitted";
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
      ambiguous: "Buoyancy ambiguous"
    };
    if (typeof value !== "string" || !value.trim()) return "not emitted";
    return known[value] || value.replaceAll("_", " ");
  }

  function statusChip(label, value, extraClass = "") {
    const rendered = value === undefined || value === null
      ? "not emitted"
      : typeof value === "boolean"
        ? (value ? "true" : "false")
        : readableStatus(value);
    const raw = value === undefined || value === null ? "not emitted" : scalarText(value);
    return `<span class="chip sec-p2-chip ${extraClass}" title="${esc(raw)}"><b>${esc(label)}:</b> ${esc(rendered)}</span>`;
  }

  function speciesToken(species, value, unit) {
    return `<span class="sec-p2-species" style="--species-color:${esc(speciesColor(species))}">` +
      `<b>${esc(prettySpecies(species))}</b> ${exact(value, unit)}</span>`;
  }

  function mapTokens(value, unit, missingText) {
    if (!isMap(value)) {
      return `<span class="trace">${esc(missingText)}</span>`;
    }
    const entries = Object.entries(value);
    if (!entries.length) return `<span class="trace">No species entries emitted.</span>`;
    return entries
      .sort((a, b) => {
        if (hasNumber(a[1]) && hasNumber(b[1])) return b[1] - a[1];
        return hasNumber(a[1]) ? -1 : hasNumber(b[1]) ? 1 : String(a[0]).localeCompare(String(b[0]));
      })
      .map(([species, amount]) => speciesToken(species, amount, unit))
      .join("");
  }

  function gradeBlock(pool) {
    const composition = isMap(pool?.composition_wt_pct) ? pool.composition_wt_pct : null;
    if (!composition) {
      return {
        headline: "Elemental tap grade pending",
        chips: `<div class="pending sec-p2-inline-pending"><strong>Not emitted</strong><p>Elemental composition by weight was not emitted. Pool mol amounts are not converted into a grade.</p></div>`,
        contaminants: `<span class="trace">Co and Ni contaminant grades not emitted.</span>`
      };
    }

    const entries = Object.entries(composition);
    const numeric = entries.filter(([, value]) => hasNumber(value)).sort((a, b) => b[1] - a[1]);
    const headline = numeric.length
      ? `${prettySpecies(numeric[0][0])} ${fmtNum(numeric[0][1], "wt%")} · largest emitted share`
      : "Elemental tap grade pending";
    const chips = entries.length
      ? entries
        .sort((a, b) => {
          if (hasNumber(a[1]) && hasNumber(b[1])) return b[1] - a[1];
          return hasNumber(a[1]) ? -1 : hasNumber(b[1]) ? 1 : String(a[0]).localeCompare(String(b[0]));
        })
        .map(([species, amount]) => speciesToken(species, amount, "wt%"))
        .join("")
      : `<span class="trace">No elemental grade entries emitted.</span>`;
    const contaminants = ["Co", "Ni"].map((species) => {
      if (!Object.prototype.hasOwnProperty.call(composition, species)) {
        return `<span class="trace">${esc(species)} contaminant grade not emitted.</span>`;
      }
      return `<span class="sec-p2-contaminant" style="--species-color:${esc(speciesColor(species))}">` +
        `<b>${esc(prettySpecies(species))} contaminant</b> ${exact(composition[species], "wt%")}</span>`;
    }).join("");
    return { headline, chips, contaminants };
  }

  function assumptionChip(interfaceReport) {
    const value = interfaceReport?.aggressive_float_tap_assumption_load_bearing;
    const label = value === true
      ? "Aggressive float-tap assumption: load-bearing"
      : value === false
        ? "Aggressive float-tap assumption: not load-bearing"
        : "Aggressive float-tap assumption: not emitted";
    return `<span class="chip sec-p2-chip ${value === true ? "sec-p2-warning" : ""}">${esc(label)}</span>`;
  }

  function renderDensityProvenance(pool) {
    const provenance = isMap(pool?.density_correlation_provenance)
      ? pool.density_correlation_provenance
      : null;
    if (!provenance || !Object.keys(provenance).length) {
      return `<div class="pending sec-p2-inline-pending"><strong>Not emitted</strong><p>Density-correlation provenance is unavailable.</p></div>`;
    }
    return Object.entries(provenance).map(([species, record]) => {
      if (!isMap(record)) {
        return `<div class="sec-p2-provenance"><b>${esc(prettySpecies(species))}</b><span>Malformed provenance record.</span></div>`;
      }
      const validRange = Array.isArray(record.valid_range_K) && record.valid_range_K.length === 2
        ? `${exact(record.valid_range_K[0], "K")}–${exact(record.valid_range_K[1], "K")}`
        : "not emitted";
      return `<div class="sec-p2-provenance">` +
        `<b style="--species-color:${esc(speciesColor(species))}">${esc(prettySpecies(species))}</b>` +
        `<span>${esc(readableStatus(record.status))}</span>` +
        `<span>Correlation temperature ${exact(record.temperature_K, "K")} · fitted range ${validRange}</span>` +
        `<span>${esc(record.source ?? "source not emitted")}</span></div>`;
    }).join("");
  }

  function renderBuoyancy(pool) {
    const buoyancy = isMap(pool?.buoyancy) ? pool.buoyancy : null;
    if (!buoyancy) return `<div class="pending sec-p2-inline-pending"><strong>Not emitted</strong><p>Buoyancy diagnostic is unavailable.</p></div>`;
    return `<div class="sec-p2-kv"><span>Emitted verdict</span><b>${esc(readableStatus(buoyancy.verdict))}</b></div>` +
      `<div class="sec-p2-kv"><span>Alloy density</span><b>${exact(buoyancy.alloy_density_kg_m3, "kg/m³")}</b></div>` +
      `<div class="sec-p2-kv"><span>Melt density</span><b>${exact(buoyancy.melt_density_kg_m3, "kg/m³")}</b></div>` +
      `<div class="sec-p2-kv"><span>Density contrast</span><b>${exact(buoyancy.delta_rho_kg_m3, "kg/m³")}</b></div>` +
      `<div class="sec-p2-kv"><span>Ambiguity threshold</span><b>${exact(buoyancy.ambiguity_threshold_kg_m3, "kg/m³")}</b></div>` +
      `<div class="sec-p2-kv"><span>Melt-density uncertainty</span><b>${exact(buoyancy.melt_density_uncertainty_kg_m3, "kg/m³")}</b></div>` +
      `<div class="sec-p2-kv"><span>Alloy-density uncertainty</span><b>${exact(buoyancy.alloy_density_uncertainty_kg_m3, "kg/m³")}</b></div>`;
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

  function renderTapCard({ title, intent, pool, terminalAccount, interfaceReport, showAssumption = false }) {
    const grade = gradeBlock(pool);
    const poolExists = isMap(pool);
    return `<article class="card sec-p2-card">` +
      `<div class="sec-p2-card-head"><div><div class="ct">${esc(title)}</div>` +
      `<div class="cbig">${esc(grade.headline)}</div></div>${showAssumption ? assumptionChip(interfaceReport) : ""}</div>` +
      `<p class="sec-p2-intent">${esc(intent)}</p>` +
      `<div class="sec-p2-grade" aria-label="${esc(`${title} emitted elemental composition by weight`)}">${grade.chips}</div>` +
      `<div class="sec-p2-contaminants" aria-label="${esc(`${title} cobalt and nickel contaminant grades`)}">${grade.contaminants}</div>` +
      `<div class="sec-p2-kv"><span>Pool density</span><b>${poolExists ? exact(pool.density_kg_m3, "kg/m³") : "not emitted"}</b></div>` +
      `<div class="sec-p2-kv"><span>Buoyancy verdict</span><b>${poolExists && isMap(pool.buoyancy) ? esc(readableStatus(pool.buoyancy.verdict)) : "not emitted"}</b></div>` +
      `<details class="sec-p2-details"><summary>Phase amounts, terminal account, and diagnostic provenance</summary>` +
      `<h3>Stratified pool · mol by species</h3><div class="sec-p2-species-list">${mapTokens(pool?.species_mol, "mol", "Pool species mol map not emitted.")}</div>` +
      `<h3>Terminal ledger account · mol by species</h3><div class="sec-p2-species-list">${mapTokens(terminalAccount, "mol", "Terminal mol account not emitted.")}</div>` +
      `<h3>Buoyancy diagnostic</h3>${renderBuoyancy(pool)}` +
      `<h3>Density-correlation provenance</h3>${renderDensityProvenance(pool)}` +
      `<div class="sec-p2-flags">${explicitAuthorityFlags(pool)}</div></details></article>`;
  }

  function renderInterface(interfaceReport) {
    if (!isMap(interfaceReport)) {
      return `<div class="pending"><strong>Not emitted</strong><p>Float-layer interface geometry is unavailable. No film thickness or coverage is inferred.</p></div>`;
    }
    return `<details class="card sec-p2-interface"><summary>Float-layer interface geometry</summary>` +
      `<div class="sec-p2-interface-grid">` +
      `<div class="sec-p2-kv"><span>Interface area</span><b>${exact(interfaceReport.area_m2, "m²")}</b></div>` +
      `<div class="sec-p2-kv"><span>Float-layer mass</span><b>${exact(interfaceReport.float_layer_mass_kg, "kg")}</b></div>` +
      `<div class="sec-p2-kv"><span>Equivalent film thickness</span><b>${exact(interfaceReport.equivalent_film_thickness_m, "m")}</b></div>` +
      `<div class="sec-p2-kv"><span>Coverage reference thickness</span><b>${exact(interfaceReport.coverage_reference_thickness_m, "m")}</b></div>` +
      `<div class="sec-p2-kv"><span>Coverage fraction at reference thickness</span><b>${exact(interfaceReport.coverage_fraction_at_reference_thickness)}</b></div>` +
      `<div class="sec-p2-kv"><span>Aggressive float-tap assumption</span><b>${interfaceReport.aggressive_float_tap_assumption_load_bearing === true ? "load-bearing" : interfaceReport.aggressive_float_tap_assumption_load_bearing === false ? "not load-bearing" : "not emitted"}</b></div>` +
      `</div></details>`;
  }

  function latestStratification(artifact) {
    const timesteps = Array.isArray(artifact?.timesteps) ? artifact.timesteps : [];
    for (let index = timesteps.length - 1; index >= 0; index -= 1) {
      const value = timesteps[index]?.summary?.metal_phase_stratification;
      if (isMap(value)) return { report: value, hour: timesteps[index]?.hour };
    }
    return null;
  }

  function render(artifact) {
    const finalState = isMap(artifact?.terminal?.final_state) ? artifact.terminal.final_state : {};
    const selected = latestStratification(artifact);
    const stratification = selected?.report;
    const pools = isMap(stratification?.pools) ? stratification.pools : {};
    const interfaceReport = isMap(stratification?.interface) ? stratification.interface : null;
    const hour = selected ? esc(scalarText(selected.hour, "not emitted")) : "not emitted";
    const absent = selected
      ? ""
      : `<div class="pending sec-p2-panel-pending"><strong>Pending metal-phase stratification</strong><p>No timestep emitted metal-phase stratification. Elemental tap grade, density, buoyancy, and interface geometry remain pending; terminal mol accounts are shown without conversion.</p></div>`;
    const provenance = isMap(stratification?.provenance) ? stratification.provenance : null;

    return `<section id="sec-p2-taps" class="sec-p2-taps">` +
      `<h2><span class="sect">P2</span>Metal-pot taps &amp; stratification</h2>` +
      `<p class="sub">Latest emitted stratification (hour ${hour}) plus literal terminal tap accounts. Elemental wt% is backend-emitted; terminal amounts remain mol.</p>` +
      `${absent}<div class="sec-p2-flags">` +
      `${statusChip("Diagnostic status", stratification?.status)}` +
      `${statusChip("Mode", stratification?.mode)}` +
      `${statusChip("Extraction behavior", stratification?.existing_extraction_behavior)}` +
      `${statusChip("Melt-density tier", stratification?.melt_density_tier)}` +
      `${statusChip("Melt-density fallback engaged", stratification?.melt_density_fallback_engaged, stratification?.melt_density_fallback_engaged === true ? "sec-p2-warning" : "")}` +
      `${statusChip("Account-state source", provenance?.account_state_source)}` +
      `${statusChip("Diagnostic derivation", provenance?.diagnostic_derivation)}` +
      `${explicitAuthorityFlags(stratification)}</div>` +
      `<div class="sec-p2-state"><div><span>Emitted temperature</span><b>${exact(stratification?.temperature_K, "K")}</b></div>` +
      `<div><span>Emitted melt density</span><b>${exact(stratification?.melt_density_kg_m3, "kg/m³")}</b></div></div>` +
      `<div class="sec-p2-grid">` +
      `${renderTapCard({ title: "Upper float tap", intent: "Al/Si float-layer product pool", pool: pools.float_layer, terminalAccount: finalState["process.metal_phase_float_layer"], interfaceReport, showAssumption: true })}` +
      `${renderTapCard({ title: "Bottom pool tap", intent: "Fe/FeSi bottom product pool", pool: pools.bottom_pool, terminalAccount: finalState["process.metal_phase_bottom_pool"], interfaceReport })}` +
      `</div>${renderInterface(interfaceReport)}` +
      `<div class="note"><b>Frozen kg tap views pending producer attach.</b> The artifact does not carry the live ledger tap views; no kg composition or grade is reconstructed from terminal mol accounts.</div>` +
      `</section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({ id: "sec-p2-taps", render });
}(globalThis));
