"use strict";

(function registerEquipmentDiagram(root) {
  const {
    scalarText,
    fmtNum,
    prettySpecies,
    accountLabel,
    speciesColor,
    esc,
  } = root.ReportLabels;

  const PANEL_ID = "sec-p15-equipment-diagram";
  const STATE_ID = "p15-equipment-state";
  const STATUS_ID = "p15-equipment-status";
  const STORED_O2_ACCOUNTS = Object.freeze([
    "terminal.oxygen_melt_offgas_stored",
    "terminal.oxygen_mre_anode_stored",
  ]);
  // Campaign → stage glow is a designed-route cue only (not emitted activity).
  // C3_K/C3_NA are alkali-shuttle reagents; product destinations are Fe/Cr
  // condenser stages and Ti metal-phase — not Stage 4 (see PRODUCT_DESTINATIONS).
  const STAGES = Object.freeze([
    { key: "stage_0", label: "Hot Duct (IR)", campaigns: ["C0", "C0B"] },
    { key: "stage_1_fe_condenser", label: "Fe Condenser", campaigns: ["C2B", "C3_K", "C3_NA"], productSpecies: ["Fe"] },
    { key: "stage_2_cr_oxide_harvest", label: "Cr Oxide Harvester", campaigns: ["C3_NA"], productSpecies: ["Cr", "CrO2"] },
    { key: "stage_3_sio_zone", label: "SiO Zone", campaigns: ["C2A", "C2A_STAGED"], productSpecies: ["SiO", "SiO2"] },
    { key: "stage_4_alkali_mg_cyclone", label: "Alkali/Mg Cyclone", campaigns: ["C4"], productSpecies: ["Na", "K", "Mg"] },
    { key: "stage_5", label: "Vortex Dust Filter", campaigns: [] },
    { key: "stage_6", label: "Turbine-Compressor", campaigns: [] },
    { key: "stage_7", label: "Turbine Outlet Monitor", campaigns: [] },
  ]);
  // Extra non-condenser destinations for multi-product campaigns (honest cue text).
  const CAMPAIGN_NON_CONDENSER_NOTES = Object.freeze({
    C3_NA: "Ti metal-phase (non-condenser)",
  });

  const hasNumber = (value) => typeof value === "number" && Number.isFinite(value);
  const asMap = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : null;
  const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value || {}, key);

  function emittedText(value, pendingText = "not emitted") {
    return typeof value === "string" && value.trim() ? value.trim() : pendingText;
  }

  function readableToken(value) {
    return emittedText(value).replace(/[_-]+/g, " ");
  }

  function numericEntries(value) {
    const values = asMap(value);
    if (!values) return [];
    return Object.entries(values)
      .filter(([, amount]) => hasNumber(amount))
      .sort(([left], [right]) => left.localeCompare(right));
  }

  function speciesQuantityState(value) {
    const values = asMap(value);
    if (!values) return null;
    const entries = numericEntries(values);
    return {
      values,
      positive: entries.filter(([, amount]) => amount > 0),
      zero: entries.filter(([, amount]) => amount === 0),
      negative: entries.filter(([, amount]) => amount < 0),
      malformed: Object.entries(values).filter(([, amount]) => !hasNumber(amount)),
    };
  }

  function speciesList(value, unit, emptyText) {
    const state = speciesQuantityState(value);
    if (!state) {
      const message = value === undefined
        ? "Pending — not emitted"
        : "Pending — malformed species container emitted";
      return `<div class="sec-p15-inline-pending">${esc(message)}</div>`;
    }
    if (state.negative.length || state.malformed.length) {
      return `<div class="sec-p15-inline-pending">${esc("Pending — malformed species quantity emitted")}</div>`;
    }
    if (!state.positive.length) {
      const message = state.zero.length
        ? "Emitted zero species quantity"
        : emptyText;
      return `<div class="sec-p15-empty">${esc(message)}</div>`;
    }
    return `<ul class="sec-p15-species-list">${state.positive.map(([species, amount]) => (
      `<li><i style="--sec-p15-species:${esc(speciesColor(species))}"></i>` +
      `<span>${esc(prettySpecies(species))}</span><strong>${esc(fmtNum(amount, unit))}</strong></li>`
    )).join("")}</ul>`;
  }

  function metric(label, value, unit, options = {}) {
    const { min = null, max = null } = options;
    const finite = hasNumber(value);
    const inDomain = finite
      && (min === null || value >= min)
      && (max === null || value <= max);
    let rendered;
    if (inDomain) {
      rendered = fmtNum(value, unit);
    } else if (finite) {
      rendered = "Pending — malformed quantity emitted";
    } else {
      rendered = "Pending — not emitted";
    }
    const pendingClass = inDomain ? "" : " sec-p15-metric--pending";
    return `<div class="sec-p15-metric${pendingClass}"><span>${esc(label)}</span><strong>${esc(rendered)}</strong></div>`;
  }

  function flagChip(label, value) {
    let rendered;
    if (typeof value === "boolean") {
      rendered = value ? "engaged" : "not engaged";
    } else if (typeof value === "string" && value.trim()) {
      rendered = readableToken(value);
    } else {
      return "";
    }
    return `<span class="sec-p15-flag" title="${esc(String(value))}">${esc(label)} · ${esc(rendered)}</span>`;
  }

  function textChip(label, value) {
    if (typeof value !== "string" || !value.trim()) return "";
    const rendered = value.trim();
    return `<span class="sec-p15-flag" title="${esc(rendered)}">${esc(label)} · ${esc(rendered)}</span>`;
  }

  function stageSpeciesGroup(label, value) {
    return `<div class="sec-p15-stage-group"><span>${esc(label)}</span>${speciesList(value, "kg", "None emitted")}</div>`;
  }

  function stageCard(stageDefinition, stagePurity, campaign) {
    const stage = asMap(stagePurity?.[stageDefinition.key]);
    const label = stage && typeof stage.label === "string" && stage.label.trim()
      ? stage.label.trim()
      : stageDefinition.label;
    const active = stageDefinition.campaigns.includes(campaign);
    const activeClass = active ? " sec-p15-stage--active" : "";
    const designated = asMap(stage?.designated_species_kg);
    const emittedProductSpecies = stageDefinition.productSpecies?.find((species) => (
      designated && hasNumber(designated[species]) && designated[species] > 0
    ));
    const emittedProduct = Boolean(emittedProductSpecies);
    const productClass = emittedProduct ? " sec-p15-stage--product" : "";
    const productStyle = emittedProduct
      ? ` style="--sec-p15-product:${esc(speciesColor(emittedProductSpecies))}"`
      : "";
    const routeNote = stageDefinition.productSpecies
      ? `<span class="sec-p15-stage-route">${esc(
        `Designed product route · ${stageDefinition.productSpecies.map(prettySpecies).join(" / ")} · ${
          emittedProduct ? "terminal designated mass emitted" : "collection not implied"
        }`
      )}</span>`
      : "";
    const activeText = active ? " · designed campaign-route focus; activity not emitted" : "";
    if (!stage) {
      return `<article class="sec-p15-stage${productClass}${activeClass}" data-stage="${stageDefinition.key}"${productStyle} aria-label="${esc(`${label} · terminal product-destination classification; includes stage-routed tap metal; not condenser inventory${activeText}`)}">` +
        `<div class="sec-p15-stage-number">${esc(String(STAGES.indexOf(stageDefinition)))}</div>` +
        `<h4>${esc(label)}</h4>${routeNote}<div class="sec-p15-inline-pending">${esc("Terminal product-destination classification pending — not emitted")}</div>` +
        `</article>`;
    }

    const hasVerdict = typeof stage.verdict === "string" && stage.verdict.trim();
    const verdict = hasVerdict ? stage.verdict.trim() : "PENDING";
    const verdictClass = ["PURE", "MIXED", "CONTAMINATED"].includes(verdict)
      ? ` sec-p15-verdict--${verdict.toLowerCase()}`
      : " sec-p15-verdict--pending";
    const warning = typeof stage.warning === "string" && stage.warning.trim()
      ? `<p class="sec-p15-stage-warning">${esc(stage.warning.trim())}</p>`
      : "";
    // Empty-stage classification sentence only when the backend emitted a
    // verdict; inventing "backend's empty-stage classification" for a missing
    // verdict contradicts the PENDING badge and the artifact.
    let emptyStage = "";
    if (hasNumber(stage.total_kg) && stage.total_kg === 0) {
      emptyStage = hasVerdict
        ? `<p class="sec-p15-stage-empty">${esc("No classified product mass emitted; verdict is the backend's empty-stage classification")}</p>`
        : `<p class="sec-p15-stage-empty">${esc("No classified product mass emitted; verdict pending — not emitted")}</p>`;
    }
    return `<article class="sec-p15-stage${productClass}${activeClass}" data-stage="${stageDefinition.key}"${productStyle} aria-label="${esc(`${label} · terminal product-destination classification; includes stage-routed tap metal; not condenser inventory${activeText}`)}">` +
      `<div class="sec-p15-stage-number">${esc(String(STAGES.indexOf(stageDefinition)))}</div>` +
      `<h4>${esc(label)}</h4>${routeNote}` +
      `<span class="sec-p15-verdict${verdictClass}">${esc(verdict)}</span>${emptyStage}${warning}` +
      metric("Designated + coproduct mass", stage.designated_kg, "kg", { min: 0 }) +
      metric("Impurity mass", stage.impurity_kg, "kg", { min: 0 }) +
      metric("Emitted purity fraction", stage.purity_fraction, "", { min: 0, max: 1 }) +
      `<details class="sec-p15-stage-detail"><summary>Terminal species split</summary>` +
      stageSpeciesGroup("Designated", stage.designated_species_kg) +
      stageSpeciesGroup("Coproduct", stage.coproduct_species_kg) +
      stageSpeciesGroup("Impurity", stage.impurity_species_kg) +
      `</details></article>`;
  }

  function pipeSegment(summary, index) {
    const segment = `stage_${index}_to_stage_${index + 1}`;
    const containerPresent = hasOwn(summary, "wall_deposit_cumulative_kg");
    const deposits = asMap(summary.wall_deposit_cumulative_kg);
    const segmentPresent = Boolean(deposits && hasOwn(deposits, segment));
    const state = segmentPresent ? speciesQuantityState(deposits[segment]) : null;
    let description;
    if (!containerPresent) {
      description = `Cumulative wall deposit not emitted for stage ${index} to ${index + 1} at this hour`;
    } else if (!deposits || (segmentPresent && !state) || state?.negative.length || state?.malformed.length) {
      description = `Cumulative wall deposit malformed for stage ${index} to ${index + 1} at this hour`;
    } else if (!segmentPresent || (!state.positive.length && !state.zero.length)) {
      description = `No non-negligible cumulative wall deposit recorded for stage ${index} to ${index + 1}`;
    } else if (!state.positive.length) {
      description = `Cumulative wall deposit emitted as zero for stage ${index} to ${index + 1}`;
    } else {
      description = `Cumulative wall deposit, stage ${index} to ${index + 1}: ${state.positive.map(([species, amount]) => `${prettySpecies(species)} ${fmtNum(amount, "kg")}`).join(", ")}`;
    }
    const stripes = state?.positive.length && !state.negative.length && !state.malformed.length
      ? state.positive.map(([species]) => `<i style="--sec-p15-species:${esc(speciesColor(species))}"></i>`).join("")
      : `<i class="sec-p15-pipe-empty"></i>`;
    return `<div class="sec-p15-pipe" data-segment="${esc(segment)}" aria-label="${esc(description)}" title="${esc(description)}">` +
      `<span class="sec-p15-pipe-flow">→</span><span class="sec-p15-pipe-coating">${stripes}</span></div>`;
  }

  function stageTrain(artifact, summary, campaign) {
    const stagePurity = asMap(artifact.terminal)?.stage_purity;
    const items = [];
    STAGES.forEach((stage, index) => {
      items.push(stageCard(stage, stagePurity, campaign));
      if (index < STAGES.length - 1) items.push(pipeSegment(summary, index));
    });
    return `<div class="sec-p15-train-head"><div><span class="sec-p15-kicker">Metal condensation train</span>` +
      `<strong>Terminal product-destination classification</strong></div><span>${esc("Per-hour per-stage inventory pending producer emit")}</span></div>` +
      `<div class="sec-p15-stage-line" role="group" aria-label="Terminal product-destination classification; includes stage-routed tap metal; not condenser inventory">${items.join("")}</div>`;
  }

  function storedO2Accounts(ledger) {
    const extras = Object.keys(ledger || {}).filter((account) => (
      /oxygen/i.test(account) && /(?:stored|captured)/i.test(account)
    ));
    return [...new Set([...STORED_O2_ACCOUNTS, ...extras])].sort((left, right) => {
      const leftKnown = STORED_O2_ACCOUNTS.indexOf(left);
      const rightKnown = STORED_O2_ACCOUNTS.indexOf(right);
      if (leftKnown >= 0 || rightKnown >= 0) {
        if (leftKnown < 0) return 1;
        if (rightKnown < 0) return -1;
        return leftKnown - rightKnown;
      }
      return left.localeCompare(right);
    });
  }

  function cryoStore(timestep) {
    const ledger = asMap(timestep.ledger) || {};
    const segments = storedO2Accounts(ledger).map((account) => {
      const accountPresent = hasOwn(ledger, account);
      const species = accountPresent ? asMap(ledger[account]) : null;
      const o2Present = Boolean(species && hasOwn(species, "O2"));
      const amount = o2Present && hasNumber(species.O2) && species.O2 >= 0
        ? species.O2
        : null;
      const label = accountLabel(account);
      let value;
      if (!accountPresent) {
        value = "Not emitted for this hour";
      } else if (!species) {
        value = "Pending — malformed account emitted";
      } else if (!o2Present) {
        value = "O₂ not emitted for this account this hour";
      } else if (amount === null) {
        value = "Pending — malformed O₂ quantity emitted";
      } else {
        value = fmtNum(amount, "mol");
      }
      const stateClass = amount === null ? " sec-p15-cryo-segment--pending" : "";
      return `<div class="sec-p15-cryo-segment${stateClass}" data-account-slot="${esc(account)}" ` +
        `style="--sec-p15-species:${esc(speciesColor("O2"))}" title="${esc(account)}">` +
        `<span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
    }).join("");
    return `<div class="sec-p15-cryo" role="group" aria-label="Separate stored oxygen ledger accounts">` +
      `<div class="sec-p15-cryo-cap"></div><div class="sec-p15-cryo-body">${segments}</div>` +
      `<div class="sec-p15-cryo-foot">${esc("Separate raw-ledger bins · mol · no combined total")}</div></div>`;
  }

  function sourceSideO2(summary) {
    // Label and kg value are independent emitter fields; preserve whichever
    // arrived. Dropping an emitted O2_metric_label when kg is absent discards
    // the human-facing "not recovered" authority (runner.py O2_metric_label).
    const label = typeof summary.O2_metric_label === "string" && summary.O2_metric_label.trim()
      ? summary.O2_metric_label.trim()
      : null;
    const amount = hasNumber(summary.O2_yield_kg_cumulative) ? summary.O2_yield_kg_cumulative : null;
    if (label === null && amount === null) {
      return `<div class="sec-p15-source-o2 sec-p15-inline-pending"><strong>${esc("Source-side O₂ readout pending")}</strong>` +
        `<span>${esc("Metric label and cumulative kg value not emitted")}</span></div>`;
    }
    const renderedLabel = label !== null
      ? label
      : "Source-side O₂ metric label pending — not emitted";
    const renderedAmount = amount !== null
      ? fmtNum(amount, "kg")
      : "Cumulative kg value pending — not emitted";
    const pendingClass = (label === null || amount === null) ? " sec-p15-inline-pending" : "";
    return `<div class="sec-p15-source-o2${pendingClass}"><strong>${esc(renderedLabel)}</strong>` +
      `<span>${esc(renderedAmount)}</span></div>`;
  }

  function gasDome(summary) {
    const carrier = typeof summary.carrier_identity === "string" && summary.carrier_identity.trim()
      ? prettySpecies(summary.carrier_identity.trim())
      : "carrier not emitted";
    const carrierPressure = hasNumber(summary.p_carrier_bar)
      ? fmtNum(summary.p_carrier_bar, "bar")
      : "carrier pressure not emitted";
    const regime = typeof summary.regime === "string" && summary.regime.trim()
      ? readableToken(summary.regime)
      : "regime pending";
    return `<div class="sec-p15-dome"><span class="sec-p15-dome-title">Overhead gas</span>` +
      `<div>${esc(carrier)} · ${esc(carrierPressure)}</div>` +
      `<div>pO₂ ${esc(fmtNum(summary.pO2_bar, "bar"))} · total ${esc(fmtNum(summary.P_total_bar, "bar"))}</div>` +
      `<div>${esc(regime)}</div></div>`;
  }

  function vaporArrows(summary) {
    const fieldPresent = hasOwn(summary, "vapor_species_kg_hr");
    const state = speciesQuantityState(summary.vapor_species_kg_hr);
    if (!fieldPresent) {
      return `<div class="sec-p15-vapor sec-p15-vapor--pending" aria-label="${esc("Vapor flux not emitted")}"><i>↑</i></div>`;
    }
    if (!state || state.negative.length || state.malformed.length) {
      return `<div class="sec-p15-vapor sec-p15-vapor--pending" aria-label="${esc("Vapor flux malformed — negative or non-numeric species quantity emitted")}"><i>↑</i></div>`;
    }
    if (!state.positive.length) {
      const description = state.zero.length
        ? "Vapor species emitted as zero this hour"
        : "No non-negligible vapor species recorded this hour";
      return `<div class="sec-p15-vapor" aria-label="${esc(description)}"><i>↑</i></div>`;
    }
    return `<div class="sec-p15-vapor" aria-label="Evolved vapor flux">${state.positive.map(([species, amount]) => (
      `<i style="--sec-p15-species:${esc(speciesColor(species))}" title="${esc(`${prettySpecies(species)} evolved vapor flux ${fmtNum(amount, "kg/h")}`)}">↑</i>`
    )).join("")}</div>`;
  }

  function tapCard(label, poolName, stratification) {
    const poolsPresent = Boolean(stratification && hasOwn(stratification, "pool_mol_after"));
    const rawPools = stratification?.pool_mol_after;
    const pools = asMap(rawPools);
    const emitted = !poolsPresent
      ? undefined
      : (!pools ? rawPools : (hasOwn(pools, poolName) ? pools[poolName] : undefined));
    return `<div class="sec-p15-tap" data-pool="${poolName}"><span>${esc(label)}</span>` +
      speciesList(emitted, "mol", "No positive diagnostic pool inventory emitted") + `</div>`;
  }

  function densityProvenanceFlags(stratification) {
    const pools = asMap(stratification?.pools);
    if (!pools) return [];
    const flags = [];
    Object.entries(pools).forEach(([poolName, pool]) => {
      const poolRecord = asMap(pool);
      const provenance = asMap(poolRecord?.density_correlation_provenance);
      Object.entries(provenance || {}).forEach(([species, record]) => {
        const densityRecord = asMap(record);
        const baseLabel = `${readableToken(poolName)} ${prettySpecies(species)} density`;
        if (!densityRecord) {
          flags.push(textChip(`${baseLabel} provenance`, "malformed emitted record"));
          return;
        }
        flags.push(flagChip(`${baseLabel} status`, densityRecord.status));
        flags.push(textChip(`${baseLabel} source`, densityRecord.source));
        if (hasOwn(densityRecord, "valid_range_K")) {
          const range = densityRecord.valid_range_K;
          const rangeText = Array.isArray(range) && range.length === 2 && range.every(hasNumber)
            ? `${fmtNum(range[0], "K")} to ${fmtNum(range[1], "K")}`
            : "malformed emitted value";
          flags.push(textChip(`${baseLabel} valid range`, rangeText));
        }
        if (hasOwn(densityRecord, "temperature_K")) {
          const temperature = hasNumber(densityRecord.temperature_K)
            ? fmtNum(densityRecord.temperature_K, "K")
            : "malformed emitted value";
          flags.push(textChip(`${baseLabel} evaluation temperature`, temperature));
        }
      });
      flags.push(flagChip(
        `${readableToken(poolName)} buoyancy verdict`,
        asMap(poolRecord?.buoyancy)?.verdict
      ));
    });
    return [...new Set(flags.filter(Boolean))];
  }

  function siDestinationFlags(stratification) {
    // Si pool placement is decided by si_destination_buoyancy (candidate-bottom
    // buoyancy used to choose the pool). Pool-bulk buoyancy is post-routing and
    // is not a substitute — surface the Si-destination verdict + uncertainty.
    if (!hasOwn(stratification, "si_destination_buoyancy")) return [];
    const siDest = asMap(stratification.si_destination_buoyancy);
    if (!siDest) {
      return [textChip("Si destination buoyancy", "malformed emitted record")];
    }
    const flags = [];
    flags.push(flagChip("Si destination buoyancy verdict", siDest.verdict));
    if (hasOwn(siDest, "delta_rho_kg_m3")) {
      const delta = hasNumber(siDest.delta_rho_kg_m3)
        ? fmtNum(siDest.delta_rho_kg_m3, "kg/m³")
        : "malformed emitted value";
      flags.push(textChip("Si destination Δρ", delta));
    }
    if (hasOwn(siDest, "ambiguity_threshold_kg_m3")) {
      const threshold = hasNumber(siDest.ambiguity_threshold_kg_m3)
        ? fmtNum(siDest.ambiguity_threshold_kg_m3, "kg/m³")
        : "malformed emitted value";
      flags.push(textChip("Si destination ambiguity threshold", threshold));
    }
    if (hasOwn(siDest, "melt_density_uncertainty_kg_m3")) {
      const meltU = hasNumber(siDest.melt_density_uncertainty_kg_m3)
        ? fmtNum(siDest.melt_density_uncertainty_kg_m3, "kg/m³")
        : "malformed emitted value";
      flags.push(textChip("Si destination melt density uncertainty", meltU));
    }
    if (hasOwn(siDest, "alloy_density_uncertainty_kg_m3")) {
      const alloyU = hasNumber(siDest.alloy_density_uncertainty_kg_m3)
        ? fmtNum(siDest.alloy_density_uncertainty_kg_m3, "kg/m³")
        : "malformed emitted value";
      flags.push(textChip("Si destination alloy density uncertainty", alloyU));
    }
    return flags.filter(Boolean);
  }

  function tapAuthority(stratification) {
    if (!stratification) {
      return `<div class="sec-p15-inline-pending">${esc("Tap authority flags not emitted")}</div>`;
    }
    const provenance = asMap(stratification.provenance);
    const flags = [
      flagChip("Status", stratification.status),
      flagChip("Source", provenance?.account_state_source),
      flagChip("Derivation", provenance?.diagnostic_derivation),
      flagChip("Behavior", stratification.existing_extraction_behavior),
      flagChip("Melt density fallback", stratification.melt_density_fallback_engaged),
      flagChip("Melt density tier", stratification.melt_density_tier),
      ...siDestinationFlags(stratification),
      ...densityProvenanceFlags(stratification),
    ].filter(Boolean);
    return flags.length
      ? `<div class="sec-p15-flags">${flags.join("")}</div>`
      : `<div class="sec-p15-inline-pending">${esc("Tap authority flags not emitted")}</div>`;
  }

  function meltPot(artifact, summary) {
    const stratification = asMap(summary.metal_phase_stratification);
    const charge = asMap(artifact.header) ? artifact.header.charge_mass_kg : null;
    return `<div class="sec-p15-pot-wrap">${gasDome(summary)}${vaporArrows(summary)}` +
      `<div class="sec-p15-pot"><div class="sec-p15-melt-fill sec-p15-melt-fill--pending">` +
      `<span>${esc("Current melt mass pending")}</span></div></div>` +
      `<div class="sec-p15-charge">${esc(hasNumber(charge) ? `${fmtNum(charge, "kg")} initial charge` : "Initial charge not emitted")}` +
      `<small>${esc("Per-hour melt mass is not emitted; depletion is not derived from routed yields")}</small></div>` +
      `<div class="sec-p15-taps">` +
      `<div class="sec-p15-tap-route sec-p15-tap-route--bottom">` +
      `<div class="sec-p15-tap-arrow sec-p15-tap-arrow--down" aria-label="${esc("Bottom tray downward route; tap flow and disposition not emitted")}">↓` +
      `<span>${esc("Bottom tray · downward · flow/disposition pending")}</span></div>` +
      tapCard("Bottom-pool diagnostic inventory · no tap gate", "bottom_pool", stratification) +
      `</div>` +
      `<div class="sec-p15-tap-route sec-p15-tap-route--float">` +
      `<div class="sec-p15-tap-arrow sec-p15-tap-arrow--side" aria-label="${esc("Float skim lateral route; tap flow and disposition not emitted")}">→` +
      `<span>${esc("Float skim · lateral · flow/disposition pending")}</span></div>` +
      tapCard("Float-layer diagnostic inventory · no tap gate", "float_layer", stratification) +
      `</div></div>` +
      `<div class="sec-p15-authority"><strong>${esc("Diagnostic authority")}</strong>${tapAuthority(stratification)}</div></div>`;
  }

  function trainTotal(summary) {
    // condensation_train_kg is the backend cumulative condensation projection
    // (runner serializes snapshot.condensation_totals, which injects terminal
    // melt-offgas stored O2). Do not call it pure metal-train inventory.
    const train = summary.condensation_train_kg;
    return `<div class="sec-p15-readout" data-readout="condensation-train-projection">` +
      `<span>${esc("Cumulative condensation projection · selected hour")}</span>` +
      speciesList(train, "kg", "No positive condensation projection emitted") +
      `<small>${esc("Backend projection may include terminal melt-offgas stored O₂; not stage-allocated metal-train inventory alone")}</small></div>`;
  }

  function pulledFromPot(summary) {
    const yields = summary.metal_yields_kg;
    return `<div class="sec-p15-readout" data-readout="metal-product-yields"><span>${esc("Metal product yields · cumulative product-ledger projection")}</span>` +
      speciesList(yields, "kg", "No positive metal product yield emitted") +
      `<small>${esc("Route-wide product readout only; never used as a condenser fill")}</small></div>`;
  }

  function pumpAndVent(summary) {
    const carrier = typeof summary.carrier_identity === "string" && summary.carrier_identity.trim()
      ? prettySpecies(summary.carrier_identity.trim())
      : "Carrier not emitted";
    return `<div class="sec-p15-pump" role="group" aria-label="Oxygen separation and inert return">` +
      `<div class="sec-p15-pump-symbol"><i></i><i></i><i></i></div>` +
      `<strong>${esc("O₂ separation pump")}</strong>` +
      `<div class="sec-p15-route sec-p15-route--o2">← ${esc("O₂ to cryo store")}</div>` +
      `<div class="sec-p15-route sec-p15-route--inert">↺ ${esc(carrier)} · ${esc("carrier separation / recycle target · recovery not emitted")}</div>` +
      `<div class="sec-p15-vent"><b>↑</b><span>${esc("Vent")}</span><small>${esc("Flow not emitted · separate from inert return")}</small></div></div>`;
  }

  function timestepAt(artifact, index) {
    const timesteps = Array.isArray(artifact?.timesteps) ? artifact.timesteps : [];
    if (!timesteps.length) return null;
    const numericIndex = Number.isInteger(index) ? index : Number.parseInt(index, 10);
    const safeIndex = Number.isFinite(numericIndex)
      ? Math.max(0, Math.min(timesteps.length - 1, numericIndex))
      : 0;
    return timesteps[safeIndex];
  }

  function equipmentState(artifact, index) {
    const timestep = timestepAt(artifact, index);
    if (!timestep) {
      return `<div class="sec-p15-panel-pending"><strong>${esc("Equipment state pending")}</strong>` +
        `<p>${esc("No timestep rows were emitted for this run.")}</p></div>`;
    }
    const summary = asMap(timestep.summary) || {};
    const campaign = typeof summary.campaign === "string" && summary.campaign.trim()
      ? summary.campaign.trim()
      : "campaign not emitted";
    const hour = timestep.hour === null || timestep.hour === undefined
      ? "not emitted"
      : scalarText(timestep.hour);
    const focusedStages = STAGES.filter((stage) => stage.campaigns.includes(campaign));
    const nonCondenserNote = CAMPAIGN_NON_CONDENSER_NOTES[campaign] || "";
    let focusText;
    if (focusedStages.length || nonCondenserNote) {
      const cueParts = focusedStages.map((stage) => stage.label);
      if (nonCondenserNote) cueParts.push(nonCondenserNote);
      focusText = `Designed campaign-route cue: ${cueParts.join(" + ")} · not emitted stage activity`;
    } else if (campaign === "campaign not emitted") {
      focusText = "Campaign-to-stage focus pending — campaign not emitted";
    } else {
      focusText = `Campaign-to-stage map not emitted for ${campaign}; no stage glow inferred`;
    }
    return `<div class="sec-p15-now"><div><span>${esc("Selected timestep")}</span>` +
      `<strong>Hour ${esc(hour)} · ${esc(campaign)}</strong></div>` +
      `<small>${esc(focusText)}</small></div>` +
      `<div class="sec-p15-process-grid">` +
      `<div class="sec-p15-zone sec-p15-zone--cryo"><span class="sec-p15-kicker">Cryo train</span>${cryoStore(timestep)}${sourceSideO2(summary)}</div>` +
      `<div class="sec-p15-zone sec-p15-zone--pot"><span class="sec-p15-kicker">Melt pot + taps</span>${meltPot(artifact, summary)}</div>` +
      `<div class="sec-p15-zone-connector sec-p15-zone-connector--pot-train" role="img" aria-label="${esc("Pot vapor path to condensation train")}" title="${esc("Pot → condensation train")}">→</div>` +
      `<div class="sec-p15-zone sec-p15-zone--train">${stageTrain(artifact, summary, campaign)}` +
      `<div class="sec-p15-readout-row">${trainTotal(summary)}${pulledFromPot(summary)}</div></div>` +
      `<div class="sec-p15-zone-connector sec-p15-zone-connector--train-pump" role="img" aria-label="${esc("Condensation train path to oxygen separation pump")}" title="${esc("Condensation train → O₂ separation pump")}">→</div>` +
      `<div class="sec-p15-zone sec-p15-zone--pump"><span class="sec-p15-kicker">Cold-end routing</span>${pumpAndVent(summary)}</div>` +
      `</div>`;
  }

  function timestepStatus(artifact, index) {
    const timestep = timestepAt(artifact, index);
    if (!timestep) return "Equipment state pending; no timesteps emitted";
    const summary = asMap(timestep.summary) || {};
    const campaign = typeof summary.campaign === "string" && summary.campaign.trim()
      ? summary.campaign.trim()
      : "campaign not emitted";
    const hour = timestep.hour === null || timestep.hour === undefined
      ? "not emitted"
      : scalarText(timestep.hour);
    return `Hour ${hour} · ${campaign}`;
  }

  function render(artifact) {
    return `<section class="sec-p15-equipment" id="${PANEL_ID}">` +
      `<h2><span class="sect">15</span>${esc("Equipment process schematic")}</h2>` +
      `<p class="sub">${esc("Scrub-driven artifact view · route-specific inventories · terminal product-destination classification, not condenser inventory")}</p>` +
      `<div class="sec-p15-legend"><span>${esc("Solid colour = classified designated species mass, not vessel fill")}</span>` +
      `<span>${esc("Dashed = pending / not emitted")}</span><span>${esc("Glow = designed campaign-route cue, not emitted activity")}</span><span>${esc("Stage masses include stage-routed tap metal; not physical condenser inventory")}</span></div>` +
      `<div class="sec-p15-sr-only" id="${STATUS_ID}" role="status" aria-live="polite" aria-atomic="true">${esc(timestepStatus(artifact, 0))}</div>` +
      `<div class="sec-p15-equipment-state" id="${STATE_ID}">${equipmentState(artifact, 0)}</div>` +
      `</section>`;
  }

  function onTimestep(artifact, index) {
    const state = root.document?.getElementById(STATE_ID);
    const status = root.document?.getElementById(STATUS_ID);
    if (state) state.innerHTML = equipmentState(artifact, index);
    if (status) status.textContent = timestepStatus(artifact, index);
  }

  (root.ReportPanels = root.ReportPanels || []).push({
    id: PANEL_ID,
    render,
    onTimestep,
  });
}(globalThis));
