"use strict";

(function registerCostRollupPanel(root) {
  const { fmtNum, prettySpecies, speciesColor, accountLabel, esc } = root.ReportLabels;

  const COST_VECTOR_FIELDS = [
    ["electrical_kWh", "Electrical energy", "kWh"],
    ["thermal_flux_h", "Thermal exposure proxy", "K·h"],
    ["furnace_h", "Furnace time", "h"],
    ["launch_penalty_kg", "Launch penalty mass", "kg"],
    ["external_reagent_kg", "External reagent mass", "kg"]
  ];

  const TOTAL_FIELDS = [
    ["total_cost_usd", "Canonical total cost", "USD", true],
    ["electrical_energy_kWh", "Total electrical energy", "kWh"],
    ["electrical_cost_usd", "Total electrical cost", "USD"],
    ["process_electrical_energy_kWh", "Process electrical energy", "kWh"],
    ["process_electrical_cost_usd", "Process electrical cost", "USD"],
    ["pumping_electrical_energy_kWh", "Pumping electrical energy", "kWh"],
    ["pumping_electrical_cost_usd", "Pumping electrical cost", "USD"],
    ["evaporation_thermal_energy_kWh", "Evaporation thermal energy", "kWh"],
    ["solar_heat_cost_usd", "Solar heat cost", "USD"]
  ];

  const LABELS = Object.freeze({
    schema_version: "Schema version",
    policy_id: "Allocation policy",
    price_basis: "Diagnostic price basis",
    transition_count: "Cost-ledger transitions",
    transition_balance_max_abs: "Maximum absolute transition residual",
    owner_ratify_placeholder_count: "Emitted placeholder count",
    owner_ratify_money_projection: "Legacy-placeholder money projection",
    allocation_status: "Allocation status",
    thermal_proxy: "Thermal proxy definition",
    auxiliary_electrical_kWh: "Auxiliary electrical energy",
    pumping_electrical_kWh: "Pumping electrical energy",
    components_kWh: "Component energies",
    parameter_metadata: "Parameter metadata",
    ambient_pressure_pa: "Ambient pressure",
    ambient_pressure_source: "Ambient-pressure source",
    feedstock_id: "Feedstock",
    source_tag: "Source tag",
    ratification_note: "Ratification note",
    import_context: "Import context"
  });

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function hasOwn(value, key) {
    return isRecord(value) && Object.prototype.hasOwnProperty.call(value, key);
  }

  function readableKey(key) {
    const value = String(key);
    if (LABELS[value]) return LABELS[value];
    const words = value.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[._-]+/g, " ").trim();
    return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Unlabelled field";
  }

  function pending(path, message = `${path} is not emitted; no value is inferred.`) {
    return `<div class="pending sec-p8-pending"><strong>Pending · ${esc(path)}</strong><p>${esc(message)}</p></div>`;
  }

  function pendingInline(path) {
    return `<span class="sec-p8-inline-pending">pending · ${esc(path)} not emitted</span>`;
  }

  function formatLeaf(value, key, path) {
    if (value === null || value === undefined) return pendingInline(path);
    if (typeof value === "number") {
      const units = ({
        owner_ratify_money_projection: "USD",
        auxiliary_electrical_kWh: "kWh",
        pumping_electrical_kWh: "kWh",
        ambient_pressure_pa: "Pa"
      })[key] || (path.includes(".components_kWh.") ? "kWh" : "");
      return `<span class="mono">${esc(fmtNum(value, units))}</span>`;
    }
    if (typeof value === "boolean") return esc(value ? "true" : "false");
    if (typeof value === "string") return `<span class="mono">${esc(value)}</span>`;
    return pendingInline(path);
  }

  function renderTree(value, path, key = "") {
    if (Array.isArray(value)) {
      if (!value.length) return `<div class="sec-p8-empty">Emitted empty list; no authority or zero value is inferred.</div>`;
      return `<ol class="sec-p8-list">${value.map((item, index) =>
        `<li>${isRecord(item) || Array.isArray(item)
          ? renderTree(item, `${path}[${index}]`)
          : formatLeaf(item, key, `${path}[${index}]`)}</li>`
      ).join("")}</ol>`;
    }
    if (isRecord(value)) {
      const entries = Object.entries(value);
      if (!entries.length) return pending(path, `${path} was emitted with no rows; zero is not inferred.`);
      return `<dl class="sec-p8-tree">${entries.map(([childKey, child]) => {
        const childPath = `${path}.${childKey}`;
        return `<div><dt>${esc(readableKey(childKey))}</dt><dd>${isRecord(child) || Array.isArray(child)
          ? renderTree(child, childPath, childKey)
          : formatLeaf(child, childKey, childPath)}</dd></div>`;
      }).join("")}</dl>`;
    }
    return formatLeaf(value, key, path);
  }

  function expectedLeaf(record, key, label, unit, path) {
    const valuePath = `${path}.${key}`;
    const value = hasOwn(record, key) && typeof record[key] === "number" && Number.isFinite(record[key])
      ? `<span class="mono">${esc(fmtNum(record[key], unit))}</span>`
      : pendingInline(valuePath);
    return `<div class="sec-p8-field"><span>${esc(label)}</span><b>${value}</b></div>`;
  }

  function expectedScalar(record, key, label, path) {
    const valuePath = `${path}.${key}`;
    const value = hasOwn(record, key) && !isRecord(record[key]) && !Array.isArray(record[key])
      ? formatLeaf(record[key], key, valuePath)
      : pendingInline(valuePath);
    return `<div class="sec-p8-field"><span>${esc(label)}</span><b>${value}</b></div>`;
  }

  function renderCostTotals(totals) {
    const path = "terminal.cost_totals";
    if (!isRecord(totals)) return pending(path);
    if (!Object.keys(totals).length) return pending(path, `${path} was emitted empty; no total is inferred.`);
    const known = new Set([...TOTAL_FIELDS.map(([key]) => key), "basis_note"]);
    const cards = TOTAL_FIELDS.map(([key, label, unit, headline]) =>
      `<div class="metric${headline ? " sec-p8-headline" : ""}"><div class="k">${esc(label)}</div><div class="v">${hasOwn(totals, key) && typeof totals[key] === "number" && Number.isFinite(totals[key])
        ? esc(fmtNum(totals[key], unit))
        : pendingInline(`${path}.${key}`)}</div></div>`
    ).join("");
    const basis = hasOwn(totals, "basis_note")
      ? `<div class="note"><b>Emitted cost basis:</b> ${formatLeaf(totals.basis_note, "basis_note", `${path}.basis_note`)}</div>`
      : "";
    const extras = Object.fromEntries(Object.entries(totals).filter(([key]) => !known.has(key)));
    return `<div class="sec-p8-metrics">${cards}</div>${basis}${Object.keys(extras).length
      ? `<details class="sec-p8-details"><summary>Other emitted canonical-total fields</summary>${renderTree(extras, path)}</details>`
      : ""}`;
  }

  function renderCostBlock(costBlock) {
    const path = "header.cost_block";
    if (!isRecord(costBlock)) return pending(path, `${path} is not emitted; viewer price authority is unavailable.`);
    return `<div class="card sec-p8-price-card"><div class="ct">Viewer price inputs · header.cost_block</div>` +
      expectedLeaf(costBlock, "electrical_cost_per_kWh", "Electrical price", "USD/kWh", path) +
      expectedLeaf(costBlock, "solar_heat_cost_per_kWh", "Solar heat price", "USD/kWh", path) +
      `<div class="sec-p8-field"><span>Provenance</span><b>${hasOwn(costBlock, "provenance")
        ? formatLeaf(costBlock.provenance, "provenance", `${path}.provenance`)
        : pendingInline(`${path}.provenance`)}</b></div>` +
      `<p class="sec-p8-caption">Canonical totals use these artifact price inputs. Diagnostic allocation projections below do not.</p></div>`;
  }

  function splitAccountSpecies(rawKey) {
    const separator = String(rawKey).lastIndexOf(":");
    return separator < 0
      ? { account: String(rawKey), species: "" }
      : { account: String(rawKey).slice(0, separator), species: String(rawKey).slice(separator + 1) };
  }

  function costVector(vector, path) {
    if (!isRecord(vector)) return pending(path);
    return `<div class="sec-p8-vector">${COST_VECTOR_FIELDS.map(([key, label, unit]) =>
      expectedLeaf(vector, key, label, unit, path)
    ).join("")}</div>`;
  }

  function mapEntrySummary(rawKey) {
    const { account, species } = splitAccountSpecies(rawKey);
    const marker = species
      ? `<i style="background:${esc(speciesColor(species))}" aria-hidden="true"></i>`
      : "";
    const speciesLabel = species ? ` · ${esc(prettySpecies(species))}` : "";
    return `${marker}<span title="${esc(rawKey)}">${esc(accountLabel(account))}${speciesLabel}</span>`;
  }

  function renderProductEntry(rawKey, entry, path) {
    if (!isRecord(entry)) return pending(path);
    const known = new Set(["quantity_kg", "accumulated_cost", "owner_ratify_money_projection"]);
    const extras = Object.fromEntries(Object.entries(entry).filter(([key]) => !known.has(key)));
    return `<details class="sec-p8-map-entry"><summary>${mapEntrySummary(rawKey)}</summary>` +
      expectedLeaf(entry, "quantity_kg", "Product quantity", "kg", path) +
      `<div class="sec-p8-subhead">Accumulated physical cost vector</div>${hasOwn(entry, "accumulated_cost")
        ? costVector(entry.accumulated_cost, `${path}.accumulated_cost`)
        : pending(`${path}.accumulated_cost`)}` +
      expectedLeaf(entry, "owner_ratify_money_projection", "Legacy-placeholder money projection · not viewer price authority", "USD", path) +
      (Object.keys(extras).length ? renderTree(extras, path) : "") + `</details>`;
  }

  function renderCostMap(map, path, productRows) {
    if (!isRecord(map) || !Object.keys(map).length) {
      return pending(path, `${path} has no emitted rows; sparse allocation is not displayed as zero.`);
    }
    return `<div class="sec-p8-map">${Object.entries(map).map(([rawKey, entry]) => {
      const entryPath = `${path}.${rawKey}`;
      return productRows
        ? renderProductEntry(rawKey, entry, entryPath)
        : `<details class="sec-p8-map-entry"><summary>${mapEntrySummary(rawKey)}</summary>${costVector(entry, entryPath)}</details>`;
    }).join("")}</div>`;
  }

  function renderRunInput(value) {
    const path = "terminal.run_metadata.cost_rollup_diagnostic.run_input_cost";
    if (!isRecord(value)) return pending(path);
    return expectedScalar(value, "thermal_proxy", "Thermal proxy definition", path) +
      expectedScalar(value, "allocation_status", "Allocation status", path) +
      `<div class="sec-p8-subhead">Furnace-input physical cost vector</div>${hasOwn(value, "physical_cost")
        ? costVector(value.physical_cost, `${path}.physical_cost`)
        : pending(`${path}.physical_cost`)}` +
      expectedLeaf(value, "owner_ratify_money_projection", "Legacy-placeholder money projection · not viewer price authority", "USD", path);
  }

  function renderAuxiliary(value) {
    const path = "terminal.run_metadata.cost_rollup_diagnostic.auxiliary_electrical_diagnostic";
    if (!isRecord(value)) return pending(path);
    const rest = Object.fromEntries(Object.entries(value).filter(([key]) => key !== "auxiliary_electrical_kWh"));
    return expectedLeaf(value, "auxiliary_electrical_kWh", "Emitted auxiliary electrical energy", "kWh", path) +
      (Object.keys(rest).length ? renderTree(rest, path) : pending(`${path}.components_kWh`));
  }

  function renderPumping(value) {
    const path = "terminal.run_metadata.cost_rollup_diagnostic.pumping_diagnostic";
    if (!isRecord(value)) return pending(path);
    const rest = Object.fromEntries(Object.entries(value).filter(([key]) => !["status", "pumping_electrical_kWh"].includes(key)));
    const resolved = value.status === "ok" || value.status === "resolved";
    const pumpingValue = hasOwn(value, "pumping_electrical_kWh") && typeof value.pumping_electrical_kWh === "number" && Number.isFinite(value.pumping_electrical_kWh)
      ? `<span class="mono">${esc(fmtNum(value.pumping_electrical_kWh, "kWh"))}</span>`
      : pendingInline(`${path}.pumping_electrical_kWh`);
    const statusLabel = typeof value.status === "string" && value.status.trim() ? value.status.trim() : "not emitted";
    const canonicalTreatment = resolved
      ? "status is eligible for canonical inclusion; terminal.cost_totals remains authoritative"
      : `excluded by the canonical cost-total emitter because status is ${statusLabel}`;
    return expectedScalar(value, "status", "Emitted pumping status", path) +
      `<div class="sec-p8-field"><span>Emitted pumping diagnostic energy</span><b>${pumpingValue}</b></div>` +
      `<div class="sec-p8-field"><span>Canonical-total treatment</span><b>${esc(canonicalTreatment)}</b></div>` +
      `<div class="note">Pumping is a rough diagnostic, not a validated pump design. Its emitted status governs whether canonical totals include its energy.</div>` +
      (Object.keys(rest).length ? renderTree(rest, path) : "");
  }

  function renderPlaceholders(diagnostic) {
    const path = "terminal.run_metadata.cost_rollup_diagnostic.owner_ratify_placeholders";
    if (!hasOwn(diagnostic, "owner_ratify_placeholders") || !Array.isArray(diagnostic.owner_ratify_placeholders)) {
      return pending(path, `${path} is not emitted; placeholder authority is not inferred.`);
    }
    if (!diagnostic.owner_ratify_placeholders.length) {
      return `<div class="sec-p8-empty">Emitted placeholder list is empty. This does not make diagnostic projections viewer price authority.</div>`;
    }
    return renderTree(diagnostic.owner_ratify_placeholders, path);
  }

  function renderWarnings(diagnostic) {
    const path = "terminal.run_metadata.cost_rollup_diagnostic.warnings";
    if (!hasOwn(diagnostic, "warnings") || !Array.isArray(diagnostic.warnings)) return pending(path);
    if (!diagnostic.warnings.length) return `<div class="sec-p8-empty">Emitted warning list is empty.</div>`;
    return renderTree(diagnostic.warnings, path, "warning");
  }

  function renderDiagnostic(diagnostic) {
    const path = "terminal.run_metadata.cost_rollup_diagnostic";
    if (!isRecord(diagnostic)) return pending(path, `${path} is not emitted; no allocation depth is inferred.`);
    const priceBasis = expectedScalar(diagnostic, "price_basis", "Emitted diagnostic price basis", path);
    const identity = expectedScalar(diagnostic, "schema_version", "Schema version", path) +
      expectedScalar(diagnostic, "policy_id", "Allocation policy", path) +
      expectedLeaf(diagnostic, "transition_count", "Cost-ledger transitions", "", path) +
      expectedLeaf(diagnostic, "transition_balance_max_abs", "Maximum absolute transition residual · native cost-vector component", "", path) +
      expectedLeaf(diagnostic, "owner_ratify_placeholder_count", "Emitted placeholder count", "", path);
    return `<div class="sec-p8-authority"><b>Diagnostic allocation · not viewer price authority</b>${priceBasis}<p>Money projections retain the emitted legacy placeholder basis and require owner ratification.</p></div>` +
      `<details class="sec-p8-details"><summary>Diagnostic allocation depth</summary>` +
      `<div class="sec-p8-identity">${identity}</div>` +
      `<details class="sec-p8-details" open><summary>Owner-ratification placeholders</summary>${renderPlaceholders(diagnostic)}</details>` +
      `<details class="sec-p8-details"><summary>Product allocations</summary>${renderCostMap(diagnostic.product_costs, `${path}.product_costs`, true)}</details>` +
      `<details class="sec-p8-details"><summary>Active inventory allocations</summary>${renderCostMap(diagnostic.active_inventory_costs, `${path}.active_inventory_costs`, false)}</details>` +
      `<details class="sec-p8-details"><summary>Run input cost</summary>${renderRunInput(diagnostic.run_input_cost)}</details>` +
      `<details class="sec-p8-details"><summary>Auxiliary electrical diagnostic</summary>${renderAuxiliary(diagnostic.auxiliary_electrical_diagnostic)}</details>` +
      `<details class="sec-p8-details"><summary>Pumping diagnostic</summary>${renderPumping(diagnostic.pumping_diagnostic)}</details>` +
      `<details class="sec-p8-details"><summary>Warnings</summary>${renderWarnings(diagnostic)}</details>` +
      `</details>`;
  }

  function render(artifact) {
    const totals = artifact?.terminal?.cost_totals;
    const costBlock = artifact?.header?.cost_block;
    const diagnostic = artifact?.terminal?.run_metadata?.cost_rollup_diagnostic;
    return `<section class="sec-p8-cost-rollup" id="sec-p8-cost-rollup">` +
      `<h2><span class="sect">P8</span>Cost rollup depth</h2>` +
      `<p class="sub">Canonical totals and their artifact price provenance, followed by diagnostic allocation depth with its legacy-placeholder caveats intact.</p>` +
      `<div class="sec-p8-head"><div>${renderCostTotals(totals)}</div>${renderCostBlock(costBlock)}</div>` +
      renderDiagnostic(diagnostic) +
      `</section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({ id: "sec-p8-cost-rollup", render });
}(globalThis));
