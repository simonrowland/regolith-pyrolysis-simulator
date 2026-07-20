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
  const STAGES = Object.freeze([
    { key: "stage_0", label: "Hot Duct (IR)", campaigns: ["C0", "C0B"] },
    { key: "stage_1_fe_condenser", label: "Fe Condenser", campaigns: ["C2B"], productSpecies: "Fe" },
    { key: "stage_2_cr_oxide_harvest", label: "Cr Oxide Harvester", campaigns: [] },
    { key: "stage_3_sio_zone", label: "SiO Zone", campaigns: ["C2A", "C2A_STAGED"], productSpecies: "SiO" },
    { key: "stage_4_alkali_mg_cyclone", label: "Alkali/Mg Cyclone", campaigns: ["C3_K", "C3_NA", "C4"], productSpecies: "Mg" },
    { key: "stage_5", label: "Vortex Dust Filter", campaigns: [] },
    { key: "stage_6", label: "Turbine-Compressor", campaigns: [] },
    { key: "stage_7", label: "Turbine Outlet Monitor", campaigns: [] },
  ]);

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

  function speciesList(value, unit, emptyText) {
    const values = asMap(value);
    if (!values) {
      return `<div class="sec-p15-inline-pending">${esc("Pending — not emitted")}</div>`;
    }
    const entries = numericEntries(values);
    if (!entries.length) {
      const message = Object.keys(values).length ? "No numeric species values emitted" : emptyText;
      return `<div class="sec-p15-empty">${esc(message)}</div>`;
    }
    return `<ul class="sec-p15-species-list">${entries.map(([species, amount]) => (
      `<li><i style="--sec-p15-species:${esc(speciesColor(species))}"></i>` +
      `<span>${esc(prettySpecies(species))}</span><strong>${esc(fmtNum(amount, unit))}</strong></li>`
    )).join("")}</ul>`;
  }

  function metric(label, value, unit) {
    const rendered = hasNumber(value) ? fmtNum(value, unit) : "Pending — not emitted";
    const pendingClass = hasNumber(value) ? "" : " sec-p15-metric--pending";
    return `<div class="sec-p15-metric${pendingClass}"><span>${esc(label)}</span><strong>${esc(rendered)}</strong></div>`;
  }

  function flagChip(label, value) {
    if (typeof value !== "string" || !value.trim()) return "";
    return `<span class="sec-p15-flag" title="${esc(value)}">${esc(label)} · ${esc(readableToken(value))}</span>`;
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
    const productClass = stageDefinition.productSpecies ? " sec-p15-stage--product" : "";
    const productStyle = stageDefinition.productSpecies
      ? ` style="--sec-p15-product:${esc(speciesColor(stageDefinition.productSpecies))}"`
      : "";
    const activeText = active ? " · viewer campaign focus" : "";
    if (!stage) {
      return `<article class="sec-p15-stage${productClass}${activeClass}" data-stage="${stageDefinition.key}"${productStyle} aria-label="${esc(`${label}${activeText}`)}">` +
        `<div class="sec-p15-stage-number">${esc(String(STAGES.indexOf(stageDefinition)))}</div>` +
        `<h4>${esc(label)}</h4><div class="sec-p15-inline-pending">${esc("Terminal stage snapshot pending — not emitted")}</div>` +
        `</article>`;
    }

    const verdict = typeof stage.verdict === "string" && stage.verdict.trim() ? stage.verdict.trim() : "PENDING";
    const verdictClass = ["PURE", "MIXED", "CONTAMINATED"].includes(verdict)
      ? ` sec-p15-verdict--${verdict.toLowerCase()}`
      : " sec-p15-verdict--pending";
    const warning = typeof stage.warning === "string" && stage.warning.trim()
      ? `<p class="sec-p15-stage-warning">${esc(stage.warning.trim())}</p>`
      : "";
    const emptyStage = hasNumber(stage.total_kg) && stage.total_kg === 0
      ? `<p class="sec-p15-stage-empty">${esc("No condensate emitted; verdict is the backend's empty-stage classification")}</p>`
      : "";
    return `<article class="sec-p15-stage${productClass}${activeClass}" data-stage="${stageDefinition.key}"${productStyle} aria-label="${esc(`${label}${activeText}`)}">` +
      `<div class="sec-p15-stage-number">${esc(String(STAGES.indexOf(stageDefinition)))}</div>` +
      `<h4>${esc(label)}</h4>` +
      `<span class="sec-p15-verdict${verdictClass}">${esc(verdict)}</span>${emptyStage}${warning}` +
      metric("Accepted mass (designated + coproduct)", stage.designated_kg, "kg") +
      metric("Impurity mass", stage.impurity_kg, "kg") +
      metric("Emitted purity fraction", stage.purity_fraction, "") +
      `<details class="sec-p15-stage-detail"><summary>Terminal species split</summary>` +
      stageSpeciesGroup("Designated", stage.designated_species_kg) +
      stageSpeciesGroup("Coproduct", stage.coproduct_species_kg) +
      stageSpeciesGroup("Impurity", stage.impurity_species_kg) +
      `</details></article>`;
  }

  function pipeSegment(summary, index) {
    const segment = `stage_${index}_to_stage_${index + 1}`;
    const deposits = asMap(summary.wall_deposit_cumulative_kg);
    const emittedSegment = deposits && hasOwn(deposits, segment) ? asMap(deposits[segment]) : null;
    const entries = numericEntries(emittedSegment);
    const description = entries.length
      ? `Cumulative wall deposit, stage ${index} to ${index + 1}: ${entries.map(([species, amount]) => `${prettySpecies(species)} ${fmtNum(amount, "kg")}`).join(", ")}`
      : `Cumulative wall deposit not emitted for stage ${index} to ${index + 1} at this hour`;
    const stripes = entries.length
      ? entries.map(([species]) => `<i style="--sec-p15-species:${esc(speciesColor(species))}"></i>`).join("")
      : `<i class="sec-p15-pipe-empty"></i>`;
    return `<div class="sec-p15-pipe" aria-label="${esc(description)}" title="${esc(description)}">` +
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
      `<strong>Terminal stage snapshots</strong></div><span>${esc("Per-hour per-stage inventory pending producer emit")}</span></div>` +
      `<div class="sec-p15-stage-line" role="group" aria-label="Eight-stage metal condensation train">${items.join("")}</div>`;
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
      const amount = species && hasNumber(species.O2) ? species.O2 : null;
      const label = accountLabel(account);
      const value = amount === null
        ? (accountPresent ? "O₂ not emitted for this account this hour" : "Not emitted for this hour")
        : fmtNum(amount, "mol");
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
    const label = typeof summary.O2_metric_label === "string" && summary.O2_metric_label.trim()
      ? summary.O2_metric_label.trim()
      : null;
    const amount = hasNumber(summary.O2_yield_kg_cumulative) ? summary.O2_yield_kg_cumulative : null;
    if (label === null || amount === null) {
      return `<div class="sec-p15-source-o2 sec-p15-inline-pending"><strong>${esc("Source-side O₂ readout pending")}</strong>` +
        `<span>${esc("Metric label or cumulative kg value not emitted")}</span></div>`;
    }
    return `<div class="sec-p15-source-o2"><strong>${esc(label)}</strong><span>${esc(fmtNum(amount, "kg"))}</span></div>`;
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
    const flux = asMap(summary.vapor_species_kg_hr);
    const entries = numericEntries(flux);
    if (!flux) {
      return `<div class="sec-p15-vapor sec-p15-vapor--pending" aria-label="${esc("Vapor flux not emitted")}"><i>↑</i></div>`;
    }
    if (!entries.length) {
      return `<div class="sec-p15-vapor" aria-label="${esc("No positive vapor species emitted this hour")}"><i>↑</i></div>`;
    }
    return `<div class="sec-p15-vapor" aria-label="Evolved vapor flux">${entries.map(([species, amount]) => (
      `<i style="--sec-p15-species:${esc(speciesColor(species))}" title="${esc(`${prettySpecies(species)} evolved vapor flux ${fmtNum(amount, "kg/h")}`)}">↑</i>`
    )).join("")}</div>`;
  }

  function tapCard(label, poolName, stratification) {
    const carried = asMap(stratification?.carried_mol);
    const emitted = carried && hasOwn(carried, poolName) ? carried[poolName] : null;
    return `<div class="sec-p15-tap" data-pool="${poolName}"><span>${esc(label)}</span>` +
      speciesList(emitted, "mol", "No positive tap-carried mol emitted") + `</div>`;
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
      `<div class="sec-p15-tap-arrow">↓</div><div class="sec-p15-taps">` +
      tapCard("Bottom-pool tap view · diagnostic carried mol", "bottom_pool", stratification) +
      tapCard("Float-layer skim view · diagnostic carried mol", "float_layer", stratification) +
      `</div><details class="sec-p15-authority"><summary>Tap authority</summary>${tapAuthority(stratification)}</details></div>`;
  }

  function trainTotal(summary) {
    const train = asMap(summary.condensation_train_kg);
    return `<div class="sec-p15-readout"><span>${esc("Cumulative train species inventory · selected hour")}</span>` +
      speciesList(train, "kg", "No positive train inventory emitted") +
      `<small>${esc("One train-wide species map; not allocated to stages")}</small></div>`;
  }

  function pulledFromPot(summary) {
    const yields = asMap(summary.metal_yields_kg);
    return `<div class="sec-p15-readout"><span>${esc("Pulled from pot · cumulative routed mass")}</span>` +
      speciesList(yields, "kg", "No positive routed mass emitted") +
      `<small>${esc("Readout only; never used as a condenser fill")}</small></div>`;
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
    return `<div class="sec-p15-now"><div><span>${esc("Selected timestep")}</span>` +
      `<strong>Hour ${esc(hour)} · ${esc(campaign)}</strong></div>` +
      `<small>${esc("Campaign glow is viewer context only; stage contents remain terminal snapshots")}</small></div>` +
      `<div class="sec-p15-process-grid">` +
      `<div class="sec-p15-zone sec-p15-zone--cryo"><span class="sec-p15-kicker">Cryo train</span>${cryoStore(timestep)}${sourceSideO2(summary)}</div>` +
      `<div class="sec-p15-zone sec-p15-zone--pot"><span class="sec-p15-kicker">Melt pot + taps</span>${meltPot(artifact, summary)}</div>` +
      `<div class="sec-p15-zone sec-p15-zone--train">${stageTrain(artifact, summary, campaign)}` +
      `<div class="sec-p15-readout-row">${trainTotal(summary)}${pulledFromPot(summary)}</div></div>` +
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
      `<p class="sub">${esc("Scrub-driven artifact view · route-specific inventories · terminal condenser snapshots")}</p>` +
      `<div class="sec-p15-legend"><span>${esc("Solid colour = emitted species")}</span>` +
      `<span>${esc("Dashed = pending / not emitted")}</span><span>${esc("Glow = viewer campaign focus")}</span></div>` +
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
