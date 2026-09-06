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
    ["total_cost_usd", "Canonical energy cost total", "USD", true],
    ["electrical_energy_kWh", "Total electrical energy", "kWh"],
    ["electrical_cost_usd", "Total electrical cost", "USD"],
    ["process_electrical_energy_kWh", "Process electrical energy", "kWh"],
    ["process_electrical_cost_usd", "Process electrical cost", "USD"],
    ["pumping_electrical_energy_kWh", "Pumping electrical energy", "kWh"],
    ["pumping_electrical_cost_usd", "Pumping electrical cost", "USD"],
    ["evaporation_thermal_energy_kWh", "Evaporation thermal energy", "kWh"],
    ["solar_heat_cost_usd", "Solar heat cost", "USD"]
  ];

  // Always co-emitted by simulator/accounting/run_artifact.py::_canonical_energy_cost_totals
  // on success. Optional pumping_* and basis_note are not required for binding.
  const CANONICAL_TOTAL_CORE = [
    "total_cost_usd",
    "electrical_cost_usd",
    "solar_heat_cost_usd",
    "electrical_energy_kWh",
    "process_electrical_energy_kWh",
    "process_electrical_cost_usd",
    "evaporation_thermal_energy_kWh"
  ];

  const MONEY_PROJECTION_LABEL = "Diagnostic money projection · not viewer price authority";

  const LABELS = Object.freeze({
    schema_version: "Schema version",
    policy_id: "Allocation policy",
    price_basis: "Diagnostic price basis",
    transition_count: "Cost-ledger transitions",
    transition_balance_max_abs: "Maximum absolute transition residual",
    owner_ratify_placeholder_count: "Emitted placeholder count",
    owner_ratify_money_projection: MONEY_PROJECTION_LABEL,
    allocation_status: "Allocation status",
    thermal_proxy: "Thermal proxy definition",
    auxiliary_electrical_kWh: "Auxiliary electrical energy",
    pumping_electrical_kWh: "Pumping electrical energy",
    energy_kWh: "Pump stage energy",
    components_kWh: "Component energies",
    parameter_metadata: "Parameter metadata",
    ambient_pressure_pa: "Ambient pressure",
    ambient_pressure_source: "Ambient-pressure source",
    feedstock_id: "Feedstock",
    source_tag: "Source tag",
    ratification_note: "Ratification note",
    import_context: "Import context",
    required_pump_speed_m3_s: "Required pump speed",
    line_conductance_m3_s: "Line conductance",
    effective_speed_ceiling_m3_s: "Effective speed ceiling",
    feasible: "Feasible",
    units: "Units",
    ticket: "Ticket",
    notice: "Notice",
    authority: "Authority",
    characterised_envelope: "Characterised envelope",
    envelope_floor_pa: "Envelope floor"
  });

  const EXTRAPOLATED_NUMBER_KEYS = new Set([
    "pumping_electrical_energy_kWh",
    "pumping_electrical_cost_usd",
    "electrical_energy_kWh",
    "electrical_cost_usd",
    "total_cost_usd",
    "pumping_electrical_kWh",
    "energy_kWh"
  ]);

  const LEAF_UNITS = Object.freeze({
    owner_ratify_money_projection: "USD",
    auxiliary_electrical_kWh: "kWh",
    pumping_electrical_kWh: "kWh",
    energy_kWh: "kWh",
    ambient_pressure_pa: "Pa",
    required_pump_speed_m3_s: "m\u00b3/s",
    line_conductance_m3_s: "m\u00b3/s",
    effective_speed_ceiling_m3_s: "m\u00b3/s"
  });

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function hasOwn(value, key) {
    return isRecord(value) && Object.prototype.hasOwnProperty.call(value, key);
  }

  function finiteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function isUnavailableQuantity(value) {
    return isRecord(value) && value.status === "unavailable";
  }

  function extrapolatedFlag(record, key) {
    if (!EXTRAPOLATED_NUMBER_KEYS.has(key) || !isRecord(record)) return "";
    const notice = record.notice;
    if (!isRecord(notice) || notice.authority !== "extrapolated") return "";
    return ` <span class="sec-p8-inline-pending">extrapolated</span>`;
  }

  function formatUnavailable(value) {
    const reason = typeof value.reason === "string" && value.reason.trim()
      ? value.reason.trim()
      : "unspecified";
    return `<span class="sec-p8-inline-pending">unavailable · ${esc(reason)}</span>`;
  }

  function hasCanonicalTotals(totals) {
    return isRecord(totals) && CANONICAL_TOTAL_CORE.every(key => finiteNumber(totals[key]));
  }

  function hasAnyCanonicalTotalField(totals) {
    return isRecord(totals) && TOTAL_FIELDS.some(([key]) => finiteNumber(totals[key]));
  }

  function readableKey(key) {
    const value = String(key);
    if (LABELS[value]) return LABELS[value];
    // Avoid turning unit-bearing suffixes like m3_s into "m3 s" (loses the division).
    if (/_m3_s$/i.test(value)) {
      const stem = value.slice(0, -5).replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[._-]+/g, " ").trim();
      return stem ? `${stem.charAt(0).toUpperCase() + stem.slice(1)} (m\u00b3/s)` : "Volumetric rate (m\u00b3/s)";
    }
    const words = value.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[._-]+/g, " ").trim();
    return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Unlabelled field";
  }

  function leafUnit(key, path) {
    if (LEAF_UNITS[key]) return LEAF_UNITS[key];
    if (path.includes(".components_kWh.")) return "kWh";
    if (/_m3_s$/i.test(String(key))) return "m\u00b3/s";
    if (/_kWh$/i.test(String(key))) return "kWh";
    return "";
  }

  function pending(path, message = `${path} is not emitted; no value is inferred.`) {
    return `<div class="pending sec-p8-pending"><strong>Pending · ${esc(path)}</strong><p>${esc(message)}</p></div>`;
  }

  function pendingInline(path) {
    return `<span class="sec-p8-inline-pending">pending · ${esc(path)} not emitted</span>`;
  }

  function malformedInline(path, expected) {
    return `<span class="sec-p8-inline-pending">malformed · ${esc(path)} expected ${esc(expected)}</span>`;
  }

  function emptyInline(path, kind) {
    return `<span class="sec-p8-inline-pending">empty · ${esc(path)} emitted ${esc(kind)}</span>`;
  }

  function structuredProblem(value, path, expected, absentMessage = `${path} is not emitted; no value is inferred.`) {
    if (value === undefined) return pending(path, absentMessage);
    if (value === null) return pending(path, `${path} was emitted null; no value is inferred.`);
    return pending(path, `${path} was emitted malformed; expected ${expected}; no value is inferred.`);
  }

  function formatLeaf(value, key, path) {
    if (value === undefined) return pendingInline(path);
    if (value === null) return emptyInline(path, "null");
    if (isUnavailableQuantity(value)) return formatUnavailable(value);
    if (typeof value === "number") {
      if (!Number.isFinite(value)) return malformedInline(path, "a finite number");
      return `<span class="mono">${esc(fmtNum(value, leafUnit(key, path)))}</span>`;
    }
    if (typeof value === "boolean") return esc(value ? "true" : "false");
    if (typeof value === "string") return value.trim()
      ? `<span class="mono">${esc(value)}</span>`
      : emptyInline(path, "an empty string");
    return malformedInline(path, "a scalar");
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
        const flag = isRecord(child) || Array.isArray(child)
          ? ""
          : extrapolatedFlag(value, childKey);
        return `<div><dt>${esc(readableKey(childKey))}</dt><dd>${isRecord(child) || Array.isArray(child)
          ? renderTree(child, childPath, childKey)
          : formatLeaf(child, childKey, childPath)}${flag}</dd></div>`;
      }).join("")}</dl>`;
    }
    return formatLeaf(value, key, path);
  }

  function numericValue(record, key, unit, path) {
    const valuePath = `${path}.${key}`;
    if (!hasOwn(record, key)) return pendingInline(valuePath);
    if (isUnavailableQuantity(record[key])) return formatUnavailable(record[key]);
    return typeof record[key] === "number" && Number.isFinite(record[key])
      ? `<span class="mono">${esc(fmtNum(record[key], unit))}</span>${extrapolatedFlag(record, key)}`
      : malformedInline(valuePath, "a finite number");
  }

  function expectedLeaf(record, key, label, unit, path) {
    const value = numericValue(record, key, unit, path);
    return `<div class="sec-p8-field"><span>${esc(label)}</span><b>${value}</b></div>`;
  }

  function scalarValue(record, key, path) {
    const valuePath = `${path}.${key}`;
    if (!hasOwn(record, key)) return pendingInline(valuePath);
    if (record[key] === null) return emptyInline(valuePath, "null");
    if (typeof record[key] === "string" && !record[key].trim()) return emptyInline(valuePath, "an empty string");
    if (isRecord(record[key]) || Array.isArray(record[key])) return malformedInline(valuePath, "a scalar");
    return formatLeaf(record[key], key, valuePath);
  }

  function expectedScalar(record, key, label, path) {
    const value = scalarValue(record, key, path);
    return `<div class="sec-p8-field"><span>${esc(label)}</span><b>${value}</b></div>`;
  }

  function totalsBindingState(costTotals) {
    if (hasCanonicalTotals(costTotals)) return "canonical";
    if (costTotals === undefined) return "absent";
    if (costTotals === null) return "null";
    if (isRecord(costTotals) && !Object.keys(costTotals).length) return "empty";
    if (isRecord(costTotals)) return "partial";
    return "malformed";
  }

  function totalsStatePhrase(state) {
    if (state === "canonical") return "canonical totals remain artifact-emitted values";
    if (state === "absent") return "canonical energy-cost totals are not emitted in this artifact";
    if (state === "null") return "canonical energy-cost totals were emitted null, so no price-to-total binding is inferred";
    if (state === "empty") return "canonical energy-cost totals were emitted empty, so no price-to-total binding is inferred";
    if (state === "partial") {
      return "terminal.cost_totals is present but no complete canonical energy-cost field set is emitted; no price-to-total binding is inferred";
    }
    return "canonical energy-cost totals were emitted malformed, so no price-to-total binding is inferred";
  }

  function renderCostTotals(totals) {
    const path = "terminal.cost_totals";
    if (!isRecord(totals)) return structuredProblem(totals, path, "an object");
    if (!Object.keys(totals).length) return pending(path, `${path} was emitted empty; no total is inferred.`);
    const known = new Set([...TOTAL_FIELDS.map(([key]) => key), "basis_note"]);
    const cards = TOTAL_FIELDS.map(([key, label, unit, headline]) =>
      `<div class="metric${headline ? " sec-p8-headline" : ""}"><div class="k">${esc(label)}</div><div class="v">${numericValue(totals, key, unit, path)}</div></div>`
    ).join("");
    const basis = `<div class="note"><b>Emitted cost basis:</b> ${scalarValue(totals, "basis_note", path)}</div>`;
    const extras = Object.fromEntries(Object.entries(totals).filter(([key]) => !known.has(key)));
    // Scope caption only when at least one known energy/cost total leaf is finite —
    // a foreign-key-only bag is not a canonical energy-cost scope claim.
    const scopeCaption = hasAnyCanonicalTotalField(totals) || hasCanonicalTotals(totals)
      ? `<p class="sec-p8-caption">Canonical energy-cost scope: electrical + evaporation solar heat.</p>`
      : `<p class="sec-p8-caption">terminal.cost_totals is present but no canonical energy-cost fields are emitted; no price-to-total binding is inferred.</p>`;
    return `<div class="sec-p8-metrics">${cards}</div>` +
      scopeCaption +
      `${basis}${Object.keys(extras).length
      ? `<details class="sec-p8-details"><summary>Other emitted canonical-total fields</summary>${renderTree(extras, path)}</details>`
      : ""}`;
  }

  function renderCostBlock(costBlock, costTotals) {
    const path = "header.cost_block";
    if (!isRecord(costBlock)) return structuredProblem(costBlock, path, "an object", `${path} is not emitted; viewer price authority is unavailable.`);
    const pricesPresent = ["electrical_cost_per_kWh", "solar_heat_cost_per_kWh"].every(key =>
      hasOwn(costBlock, key) && finiteNumber(costBlock[key])
    );
    const provenancePresent = hasOwn(costBlock, "provenance") &&
      typeof costBlock.provenance === "string" && Boolean(costBlock.provenance.trim());
    const totalsState = totalsBindingState(costTotals);
    let caption;
    if (!pricesPresent || !provenancePresent) {
      // Incomplete provenance never claims a binding, and must not claim
      // "totals remain artifact-emitted" when no complete totals were emitted.
      caption = `Canonical-total price provenance cannot be validated from this artifact; ${totalsStatePhrase(totalsState)}.`;
    } else if (totalsState === "canonical") {
      caption = "Emitted canonical energy-cost totals use these artifact price inputs. Diagnostic allocation projections below do not.";
    } else if (totalsState === "absent") {
      caption = "Viewer price inputs are emitted in header.cost_block; canonical energy-cost totals are not emitted in this artifact.";
    } else if (totalsState === "null") {
      caption = "Viewer price inputs are emitted in header.cost_block; canonical energy-cost totals were emitted null, so no price-to-total binding is inferred.";
    } else if (totalsState === "empty") {
      caption = "Viewer price inputs are emitted in header.cost_block; canonical energy-cost totals were emitted empty, so no price-to-total binding is inferred.";
    } else if (totalsState === "partial") {
      caption = "Viewer price inputs are emitted in header.cost_block; terminal.cost_totals is present but no complete canonical energy-cost field set is emitted; no price-to-total binding is inferred.";
    } else {
      caption = "Viewer price inputs are emitted in header.cost_block; canonical energy-cost totals were emitted malformed, so no price-to-total binding is inferred.";
    }
    return `<div class="card sec-p8-price-card"><div class="ct">Viewer price inputs · header.cost_block</div>` +
      expectedLeaf(costBlock, "electrical_cost_per_kWh", "Electrical price", "USD/kWh", path) +
      expectedLeaf(costBlock, "solar_heat_cost_per_kWh", "Solar heat price", "USD/kWh", path) +
      `<div class="sec-p8-field"><span>Provenance</span><b>${hasOwn(costBlock, "provenance")
        ? formatLeaf(costBlock.provenance, "provenance", `${path}.provenance`)
        : pendingInline(`${path}.provenance`)}</b></div>` +
      `<p class="sec-p8-caption">${esc(caption)}</p></div>`;
  }

  function splitAccountSpecies(rawKey) {
    const separator = String(rawKey).lastIndexOf(":");
    return separator < 0
      ? { account: String(rawKey), species: "" }
      : { account: String(rawKey).slice(0, separator), species: String(rawKey).slice(separator + 1) };
  }

  function costVector(vector, path) {
    if (isUnavailableQuantity(vector)) {
      return `<div class="sec-p8-field"><span>Physical cost vector</span><b>${formatUnavailable(vector)}</b></div>`;
    }
    if (!isRecord(vector)) return structuredProblem(vector, path, "an object");
    if (!Object.keys(vector).length) return pending(path, `${path} was emitted empty; zero is not inferred.`);
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
    if (!isRecord(entry)) return structuredProblem(entry, path, "an object");
    const known = new Set(["quantity_kg", "accumulated_cost", "owner_ratify_money_projection"]);
    const extras = Object.fromEntries(Object.entries(entry).filter(([key]) => !known.has(key)));
    return `<details class="sec-p8-map-entry"><summary>${mapEntrySummary(rawKey)}</summary>` +
      expectedLeaf(entry, "quantity_kg", "Product quantity", "kg", path) +
      `<div class="sec-p8-subhead">Accumulated physical cost vector</div>${costVector(entry.accumulated_cost, `${path}.accumulated_cost`)}` +
      expectedLeaf(entry, "owner_ratify_money_projection", MONEY_PROJECTION_LABEL, "USD", path) +
      (Object.keys(extras).length ? renderTree(extras, path) : "") + `</details>`;
  }

  function renderCostMap(map, path, productRows) {
    if (!isRecord(map)) return structuredProblem(map, path, "an object", `${path} is not emitted; sparse allocation is not displayed as zero.`);
    if (!Object.keys(map).length) return pending(path, `${path} was emitted empty; sparse allocation is not displayed as zero.`);
    return `<div class="sec-p8-map">${Object.entries(map).map(([rawKey, entry]) => {
      const entryPath = `${path}.${rawKey}`;
      return productRows
        ? renderProductEntry(rawKey, entry, entryPath)
        : `<details class="sec-p8-map-entry"><summary>${mapEntrySummary(rawKey)}</summary>${costVector(entry, entryPath)}</details>`;
    }).join("")}</div>`;
  }

  function renderRunInput(value) {
    const path = "terminal.run_metadata.cost_rollup_diagnostic.run_input_cost";
    if (!isRecord(value)) return structuredProblem(value, path, "an object");
    return expectedScalar(value, "thermal_proxy", "Thermal proxy definition", path) +
      expectedScalar(value, "allocation_status", "Allocation status", path) +
      `<div class="sec-p8-subhead">Furnace-input physical cost vector</div>${costVector(value.physical_cost, `${path}.physical_cost`)}` +
      expectedLeaf(value, "owner_ratify_money_projection", MONEY_PROJECTION_LABEL, "USD", path);
  }

  function renderAuxiliary(value) {
    const path = "terminal.run_metadata.cost_rollup_diagnostic.auxiliary_electrical_diagnostic";
    if (!isRecord(value)) return structuredProblem(value, path, "an object");
    const componentsPath = `${path}.components_kWh`;
    const components = !hasOwn(value, "components_kWh")
      ? pending(componentsPath)
      : !isRecord(value.components_kWh)
        ? structuredProblem(value.components_kWh, componentsPath, "an object")
        : !Object.keys(value.components_kWh).length
          ? pending(componentsPath, `${componentsPath} was emitted empty; zero is not inferred.`)
          : renderTree(value.components_kWh, componentsPath);
    const rest = Object.fromEntries(Object.entries(value).filter(([key]) => !["auxiliary_electrical_kWh", "components_kWh"].includes(key)));
    return expectedLeaf(value, "auxiliary_electrical_kWh", "Emitted auxiliary electrical energy", "kWh", path) +
      `<div class="sec-p8-subhead">Emitted component energies</div>${components}` +
      (Object.keys(rest).length ? renderTree(rest, path) : "");
  }

  function renderPumping(value) {
    const path = "terminal.run_metadata.cost_rollup_diagnostic.pumping_diagnostic";
    if (!isRecord(value)) return structuredProblem(value, path, "an object");
    const rest = Object.fromEntries(Object.entries(value).filter(([key]) => !["status", "pumping_electrical_kWh"].includes(key)));
    return expectedScalar(value, "status", "Emitted pumping status", path) +
      expectedLeaf(value, "pumping_electrical_kWh", "Emitted pumping diagnostic energy", "kWh", path) +
      `<div class="note">Pumping is a rough diagnostic, not a validated pump design. Canonical pumping treatment is emitted only by terminal.cost_totals: pumping_electrical_energy_kWh and pumping_electrical_cost_usd record inclusion; optional basis_note records exclusion or status. Diagnostic status is not reinterpreted here.</div>` +
      (Object.keys(rest).length ? renderTree(rest, path) : "");
  }

  function renderImportContext(diagnostic) {
    const path = "terminal.run_metadata.cost_rollup_diagnostic.import_context";
    if (!hasOwn(diagnostic, "import_context")) return pending(path, `${path} is not emitted; import-classification authority is unavailable.`);
    if (!isRecord(diagnostic.import_context)) return structuredProblem(diagnostic.import_context, path, "an object");
    if (!Object.keys(diagnostic.import_context).length) return pending(path, `${path} was emitted empty; import-classification authority is not inferred.`);
    return renderTree(diagnostic.import_context, path);
  }

  function renderPlaceholders(diagnostic) {
    const path = "terminal.run_metadata.cost_rollup_diagnostic.owner_ratify_placeholders";
    if (!hasOwn(diagnostic, "owner_ratify_placeholders")) return pending(path, `${path} is not emitted; placeholder authority is not inferred.`);
    if (!Array.isArray(diagnostic.owner_ratify_placeholders)) return structuredProblem(diagnostic.owner_ratify_placeholders, path, "a list");
    if (!diagnostic.owner_ratify_placeholders.length) {
      return `<div class="sec-p8-empty">Emitted placeholder list is empty. This does not make diagnostic projections viewer price authority.</div>`;
    }
    return renderTree(diagnostic.owner_ratify_placeholders, path);
  }

  function renderWarnings(diagnostic) {
    const path = "terminal.run_metadata.cost_rollup_diagnostic.warnings";
    if (!hasOwn(diagnostic, "warnings")) return pending(path);
    if (!Array.isArray(diagnostic.warnings)) return structuredProblem(diagnostic.warnings, path, "a list");
    if (!diagnostic.warnings.length) return `<div class="sec-p8-empty">Emitted warning list is empty.</div>`;
    return renderTree(diagnostic.warnings, path, "warning");
  }

  function renderDiagnostic(diagnostic) {
    const path = "terminal.run_metadata.cost_rollup_diagnostic";
    if (!isRecord(diagnostic)) return structuredProblem(diagnostic, path, "an object", `${path} is not emitted; no allocation depth is inferred.`);
    const priceBasis = expectedScalar(diagnostic, "price_basis", "Emitted diagnostic price basis", path);
    const rawPriceBasis = diagnostic.price_basis;
    let authorityMessage;
    if (!hasOwn(diagnostic, "price_basis")) {
      authorityMessage = "Diagnostic price basis is not emitted; money-projection authority cannot be validated from this artifact.";
    } else if (rawPriceBasis === null) {
      authorityMessage = "Diagnostic price basis was emitted null; money-projection authority cannot be validated from this artifact.";
    } else if (typeof rawPriceBasis === "string" && !rawPriceBasis.trim()) {
      authorityMessage = "Diagnostic price basis was emitted empty; money-projection authority cannot be validated from this artifact.";
    } else if (typeof rawPriceBasis !== "string") {
      authorityMessage = "Diagnostic price basis was emitted malformed; money-projection authority cannot be validated from this artifact.";
    } else if (rawPriceBasis === "legacy_placeholder_awaiting_owner_ratification") {
      authorityMessage = "Money projections use the emitted legacy-placeholder basis and require owner ratification.";
    } else {
      authorityMessage = "Money projections use the emitted diagnostic price basis shown above; they remain separate from viewer price authority.";
    }
    const identity = expectedScalar(diagnostic, "schema_version", "Schema version", path) +
      expectedScalar(diagnostic, "policy_id", "Allocation policy", path) +
      expectedLeaf(diagnostic, "transition_count", "Cost-ledger transitions", "", path) +
      expectedLeaf(diagnostic, "transition_balance_max_abs", "Maximum absolute transition residual · native cost-vector component", "", path) +
      expectedLeaf(diagnostic, "owner_ratify_placeholder_count", "Emitted placeholder count", "", path);
    return `<div class="sec-p8-authority"><b>Diagnostic allocation · not viewer price authority</b>${priceBasis}<p>${esc(authorityMessage)}</p></div>` +
      `<details class="sec-p8-details"><summary>Diagnostic allocation depth</summary>` +
      `<div class="sec-p8-identity">${identity}</div>` +
      `<details class="sec-p8-details"><summary>Import classification context</summary>${renderImportContext(diagnostic)}</details>` +
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
      `<p class="sub">Canonical energy-cost totals and artifact price provenance, followed by diagnostic allocation depth with emitted basis disclosures intact.</p>` +
      `<div class="sec-p8-head"><div>${renderCostTotals(totals)}</div>${renderCostBlock(costBlock, totals)}</div>` +
      renderDiagnostic(diagnostic) +
      `</section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({ id: "sec-p8-cost-rollup", render });
}(globalThis));
