"use strict";

(function registerFeRedoxPanel(root) {
  const {
    fmtNum,
    prettySpecies,
    speciesColor,
    accountLabel,
    esc
  } = root.ReportLabels;

  const FIELD_LABELS = Object.freeze({
    native_fe_pool_mol: "Native Fe pool",
    native_fe_vapor_mol: "Native Fe routed to vapor",
    native_fe_tap_mol: "Native Fe routed to tap",
    native_fe_vapor_capacity_mol_hr: "Fe vapor capacity",
    native_fe_vapor_capacity_kg_hr: "Fe vapor mass capacity",
    ordinary_melt_fe_residual_capacity_mol_hr: "Residual capacity for ordinary melt Fe",
    native_fe_vapor_escape_fraction_of_pool: "Native Fe pool fraction routed as vapor",
    native_fe_uncondensed_mol: "Uncondensed native Fe",
    native_fe_uncondensed_fraction_of_pool: "Uncondensed fraction of native Fe pool",
    native_fe_condensed_kg: "Condensed native Fe mass",
    native_fe_source_account: "Native Fe source account",
    native_fe_split_commit_status: "Split commit status",
    native_fe_vapor_route_status: "Vapor route status",
    native_fe_vapor_route_suppressed_mol: "Suppressed vapor route",
    capacity_allocation_rule: "Capacity allocation rule",
    native_pool_activity_argument: "Native-pool activity basis",
    P_reference_Antoine_Pa: "Antoine reference pressure",
    P_eq_Pa: "Equilibrium pressure",
    P_bulk_Pa: "Bulk Fe partial pressure",
    activity_factor: "Activity factor",
    temperature_K: "Temperature",
    overhead_pressure_pa: "Overhead pressure",
    overhead_pressure_source: "Overhead-pressure source",
    carrier_gas: "Carrier gas",
    alpha_Fe: "Fe HKL alpha",
    alpha_source: "HKL alpha source",
    alpha_s_evaluation: "HKL alpha evaluation",
    source_label: "Vapor-pressure source label",
    series_resistance: "Series-resistance model",
    flux_kg_s_m2: "Flux",
    alpha_intrinsic: "Intrinsic alpha",
    alpha_effective: "Effective alpha",
    alpha_eff: "Effective alpha alias",
    k_hk_kg_s_m2_pa: "HKL conductance",
    r_interface: "Interface resistance",
    r_gas: "Gas resistance",
    r_melt: "Melt resistance",
    R_interface_fraction: "Interface resistance fraction",
    R_gas_fraction: "Gas resistance fraction",
    R_melt_fraction: "Melt resistance fraction",
    limiting_resistance_label: "Limiting resistance",
    knudsen_number: "Knudsen number",
    Kn: "Knudsen number alias",
    gas_resistance_weight: "Gas-resistance weight",
    axial_stir_applied: "Applied axial stir factor",
    radial_stir_applied: "Applied radial stir factor",
    axial_stir_clamped: "Axial stir clamped",
    radial_stir_clamped: "Radial stir clamped",
    frozen_skull_stir_clamped: "Frozen-skull stir clamped",
    frozen_skull_stir_ceiling: "Frozen-skull stir ceiling",
    melt_resistance_enabled: "Melt resistance enabled",
    melt_surface_renewal_base_kg_s_m2_pa: "Melt-surface renewal conductance",
    melt_surface_renewal_source: "Melt-surface renewal source",
    k_mt_kg_s_m2_pa: "Gas mass-transfer conductance",
    d_ab_m2_s: "Binary diffusivity",
    transport_length_m: "Transport length",
    species: "Species",
    alpha_s: "Evaluated HKL alpha",
    alpha_s_form: "HKL alpha form",
    alpha_s_temperature_K: "HKL alpha temperature",
    alpha_s_extrapolated: "HKL alpha extrapolated",
    alpha_s_valid_range_K: "HKL alpha valid range",
    alpha_s_temperature_range_K: "HKL alpha temperature range",
    alpha_s_extrapolation_warning: "HKL alpha extrapolation warning",
    temperature_C: "Event temperature",
    native_fe_event: "Event",
    native_fe_event_reason: "Reason",
    native_fe_event_status: "Status",
    net_mol_o2_equiv: "Net attempted redox-source terms",
    delta_ln_fO2: "Change in ln fO2",
    delta_log10_fO2: "Change in log10 fO2",
    redox_source_terms_applied: "Source terms applied",
    redox_source_skip_reason: "Combined skip reason",
    source_campaign_hour: "Source campaign hour",
    respeciation_status: "Respeciation status",
    direction: "Respeciation direction",
    current_ferric_fraction: "Current ferric fraction",
    target_ferric_fraction: "Target ferric fraction",
    o2_account: "O2 account",
    oxygen_source: "Oxygen source",
    gate_authority: "Gate authority",
    kind: "Authority kind",
    fallback_status: "Fallback status",
    reason: "Reason",
    liquidus_status: "Liquidus status",
    floor_T_C: "Temperature floor",
    status: "Status",
    implied_ferric_fraction: "Implied ferric fraction",
    ledger_ferric_fraction: "Ledger ferric fraction",
    delta_abs: "Absolute ferric divergence",
    warning_threshold_abs: "Warning threshold",
    warning_threshold_ferric_fraction_abs: "Ferric-fraction warning threshold",
    sampling_context: "Sampling context",
    warning: "Warning",
    campaign: "Campaign",
    hour: "Hour",
    campaign_hour: "Campaign hour"
  });

  const FIELD_UNITS = Object.freeze({
    native_fe_pool_mol: "mol",
    native_fe_vapor_mol: "mol",
    native_fe_tap_mol: "mol",
    native_fe_vapor_capacity_mol_hr: "mol/h",
    native_fe_vapor_capacity_kg_hr: "kg/h",
    ordinary_melt_fe_residual_capacity_mol_hr: "mol/h",
    native_fe_uncondensed_mol: "mol",
    native_fe_condensed_kg: "kg",
    native_fe_vapor_route_suppressed_mol: "mol",
    P_reference_Antoine_Pa: "Pa",
    P_eq_Pa: "Pa",
    P_bulk_Pa: "Pa",
    temperature_K: "K",
    overhead_pressure_pa: "Pa",
    flux_kg_s_m2: "kg/(s·m²)",
    k_hk_kg_s_m2_pa: "kg/(s·m²·Pa)",
    melt_surface_renewal_base_kg_s_m2_pa: "kg/(s·m²·Pa)",
    k_mt_kg_s_m2_pa: "kg/(s·m²·Pa)",
    d_ab_m2_s: "m²/s",
    transport_length_m: "m",
    alpha_s_temperature_K: "K",
    alpha_s_valid_range_K: "K",
    alpha_s_temperature_range_K: "K",
    temperature_C: "°C",
    net_mol_o2_equiv: "mol O₂-eq",
    floor_T_C: "°C"
  });

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function hasOwn(object, key) {
    return isRecord(object) && Object.prototype.hasOwnProperty.call(object, key);
  }

  function isNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function pendingValue() {
    return '<span class="sec-p1-pending-value">not emitted</span>';
  }

  function numberValue(object, key, unit = "") {
    return hasOwn(object, key) && isNumber(object[key])
      ? esc(fmtNum(object[key], unit))
      : pendingValue();
  }

  function textValue(object, key, emptyText = "none emitted") {
    const state = scalarTextState(object, key, emptyText);
    return state.kind === "absent" ? pendingValue() : esc(state.value);
  }

  function scalarTextState(object, key, emptyText = "none emitted") {
    if (
      !hasOwn(object, key)
      || object[key] === null
      || object[key] === undefined
    ) {
      return { kind: "absent", value: "not emitted" };
    }
    if (typeof object[key] === "object") return { kind: "malformed", value: "malformed" };
    const value = String(object[key]);
    return value
      ? { kind: "value", value }
      : { kind: "empty", value: emptyText };
  }

  function booleanValue(object, key) {
    if (!hasOwn(object, key) || typeof object[key] !== "boolean") return pendingValue();
    return object[key] ? "yes" : "no";
  }

  function accountValue(object, key) {
    if (!hasOwn(object, key) || object[key] === null || typeof object[key] === "object") {
      return pendingValue();
    }
    return esc(accountLabel(object[key]));
  }

  function readableKey(key) {
    if (FIELD_LABELS[key]) return FIELD_LABELS[key];
    return String(key)
      .replace(/_/g, " ")
      .replace(/\bfO2\b/g, "fO₂")
      .replace(/\bO2\b/g, "O₂")
      .replace(/\bFe\b/g, "Fe")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function artifactScalar(key, value) {
    const unit = FIELD_UNITS[key] || "";
    if (isNumber(value)) return esc(fmtNum(value, unit));
    if (typeof value === "boolean") return value ? "yes" : "no";
    if (value === null || value === undefined) return pendingValue();
    if (key === "native_fe_source_account" || key === "o2_account") {
      return esc(accountLabel(value));
    }
    return esc(String(value));
  }

  function flattenRecord(object, prefix = "", rows = []) {
    if (!isRecord(object)) return rows;
    Object.entries(object).forEach(([key, value]) => {
      const label = prefix ? `${prefix} · ${readableKey(key)}` : readableKey(key);
      if (isRecord(value)) {
        flattenRecord(value, label, rows);
      } else if (Array.isArray(value)) {
        const arrayText = value.every((item) => !isRecord(item) && !Array.isArray(item))
          ? value.map((item) => artifactScalar(key, item)).join(" – ")
          : esc(`${value.length} emitted entries`);
        rows.push([label, arrayText || esc("empty")]);
      } else {
        rows.push([label, artifactScalar(key, value)]);
      }
    });
    return rows;
  }

  function factsTable(rows, ariaLabel) {
    if (!rows.length) return "";
    return `<div class="sec-p1-table-wrap"><table aria-label="${esc(ariaLabel)}"><tbody>${rows.map(([label, value]) => (
      `<tr><th scope="row">${esc(label)}</th><td>${value}</td></tr>`
    )).join("")}</tbody></table></div>`;
  }

  function recordTable(object, ariaLabel) {
    const rows = flattenRecord(object);
    return rows.length
      ? factsTable(rows, ariaLabel)
      : '<div class="sec-p1-empty-note">Emitted object contains no detail.</div>';
  }

  function metric(label, value, note = "") {
    return `<div class="metric sec-p1-metric"><div class="k">${label}</div><div class="v">${value}</div>${note ? `<small>${note}</small>` : ""}</div>`;
  }

  function pendingBlock(title, message) {
    return `<div class="pending sec-p1-pending"><strong>${esc(title)}</strong><p>${esc(message)}</p></div>`;
  }

  function authorityTooltip(redox) {
    if (!isRecord(redox)) return "Authority envelope not emitted.";
    const parts = ["status", "source", "reference"].map((key) => {
      const state = scalarTextState(redox, key);
      return `${key}: ${state.value}`;
    });
    return parts.join("; ");
  }

  function scalarChip(redox, key, label) {
    const state = scalarTextState(redox, key);
    const cautious = state.kind !== "value";
    return `<span class="chip sec-p1-flag ${cautious ? "sec-p1-flag-caution" : "sec-p1-flag-clear"}" title="${esc(authorityTooltip(redox))}">${esc(label)} · ${esc(state.value)}</span>`;
  }

  function flagChip(redox, key, label, cautiousWhen) {
    const value = hasOwn(redox, key) && typeof redox[key] === "boolean" ? redox[key] : null;
    const state = value === null ? "not emitted" : value ? "yes" : "no";
    const cautious = value === null || value === cautiousWhen;
    return `<span class="chip sec-p1-flag ${cautious ? "sec-p1-flag-caution" : "sec-p1-flag-clear"}" title="${esc(authorityTooltip(redox))}">${esc(label)} · ${esc(state)}</span>`;
  }

  function renderCore(redox, selected = false) {
    if (!isRecord(redox)) {
      return pendingBlock(
        "Fe-redox state pending",
        `The ${selected ? "selected" : "terminal"} timestep does not emit summary.fe_redox_split. No redox state is inferred.`
      );
    }
    const fe2o3 = esc(prettySpecies("Fe2O3"));
    const feo = esc(prettySpecies("FeO"));
    const headline = [
      metric("Melt log₁₀ fO₂", numberValue(redox, "fO2_log")),
      metric("Fe³⁺ / ΣFe", numberValue(redox, "fe3_over_sigma_fe")),
      metric("Ferric fraction", numberValue(redox, "ferric_frac")),
      metric("Ferrous fraction", numberValue(redox, "ferrous_frac")),
      metric("Native Fe fraction", numberValue(redox, "native_fe_frac"))
    ].join("");
    const detailRows = [
      ["IW-buffer log₁₀ fO₂ (absolute, not ΔIW)", numberValue(redox, "iw_log")],
      ["Temperature", numberValue(redox, "temperature_K", "K")],
      ["Melt-headspace pressure", numberValue(redox, "pressure_bar", "bar")],
      [`${fe2o3} / ${feo} molar ratio`, numberValue(redox, "fe2o3_over_feo_molar")],
      [`${fe2o3} equivalent`, numberValue(redox, "fe2o3_equiv_wt_pct", "wt%")],
      [`${feo} equivalent`, numberValue(redox, "feo_equiv_wt_pct", "wt%")],
      ["Native Fe saturation", booleanValue(redox, "native_fe_saturation")],
      ["Native Fe threshold", textValue(redox, "native_fe_threshold")]
    ];
    return `<div class="sec-p1-headline">${headline}</div>${factsTable(detailRows, `${selected ? "Selected timestep" : "Terminal"} Fe-redox state detail`)}`;
  }

  function renderAuthority(redox) {
    if (!isRecord(redox)) return "";
    const chips = [
      scalarChip(redox, "status", "Status"),
      scalarChip(redox, "source", "Source"),
      scalarChip(redox, "reference", "Reference"),
      flagChip(redox, "authoritative", "Authoritative", false),
      flagChip(redox, "diagnostic_only", "Diagnostic only", true),
      flagChip(redox, "extrapolation", "Extrapolation", true),
      flagChip(redox, "high_uncertainty", "High uncertainty", true)
    ].join("");
    const rows = [
      ["Status", textValue(redox, "status")],
      ["Source", textValue(redox, "source")],
      ["Reference", textValue(redox, "reference")],
      ["Temperature-band case", textValue(redox, "temperature_band_case")],
      ["Temperature-band status", textValue(redox, "temperature_band_status")],
      ["Temperature-band source", textValue(redox, "temperature_band_source")]
    ];
    return `<div class="sec-p1-flags" aria-label="Emitted Fe-redox diagnostic and Kress91 temperature-band flags">${chips}</div><div class="note sec-p1-authority-note">Authoritative, extrapolation and high uncertainty describe the Kress91 temperature band; diagnostic-only describes this Fe-redox split. None is whole-run confidence.</div><details class="sec-p1-disclosure"><summary>Authority and temperature-band detail</summary>${factsTable(rows, "Fe-redox authority and validity envelope")}</details>`;
  }

  function renderPartition(redox) {
    const partition = isRecord(redox) ? redox.native_fe_partition : null;
    const event = isRecord(redox) ? redox.native_fe_saturation_event : null;
    let partitionHtml;
    if (isRecord(partition)) {
      const partitionRows = [
        ["Fe vapor mass capacity", numberValue(partition, "native_fe_vapor_capacity_kg_hr", "kg/h")],
        ["Residual capacity for ordinary melt Fe", numberValue(partition, "ordinary_melt_fe_residual_capacity_mol_hr", "mol/h")],
        ["Condensed native Fe mass", numberValue(partition, "native_fe_condensed_kg", "kg")],
        ["Uncondensed native Fe", numberValue(partition, "native_fe_uncondensed_mol", "mol")],
        ["Uncondensed fraction of native Fe pool", numberValue(partition, "native_fe_uncondensed_fraction_of_pool")],
        ["Native Fe pool fraction routed as vapor", numberValue(partition, "native_fe_vapor_escape_fraction_of_pool")],
        ["Native Fe source account", accountValue(partition, "native_fe_source_account")],
        ["Split commit status", textValue(partition, "native_fe_split_commit_status")],
        ["Vapor route status", textValue(partition, "native_fe_vapor_route_status")],
        ["Suppressed vapor route", numberValue(partition, "native_fe_vapor_route_suppressed_mol", "mol")],
        ["Capacity allocation rule", textValue(partition, "capacity_allocation_rule")],
        ["Native-pool activity basis", textValue(partition, "native_pool_activity_argument")],
        ["Antoine reference pressure", numberValue(partition, "P_reference_Antoine_Pa", "Pa")],
        ["Equilibrium pressure", numberValue(partition, "P_eq_Pa", "Pa")],
        ["Bulk Fe partial pressure", numberValue(partition, "P_bulk_Pa", "Pa")],
        ["Activity factor", numberValue(partition, "activity_factor")],
        ["Partition temperature", numberValue(partition, "temperature_K", "K")],
        ["Overhead pressure", numberValue(partition, "overhead_pressure_pa", "Pa")],
        ["Overhead-pressure source", textValue(partition, "overhead_pressure_source")],
        ["Carrier gas", textValue(partition, "carrier_gas")],
        ["Fe HKL alpha", numberValue(partition, "alpha_Fe")],
        ["HKL alpha source", textValue(partition, "alpha_source")],
        ["Vapor-pressure source label", textValue(partition, "source_label")]
      ];
      const alphaEvaluation = isRecord(partition.alpha_s_evaluation)
        ? recordTable(partition.alpha_s_evaluation, "Native Fe HKL alpha evaluation")
        : pendingBlock("HKL alpha evaluation pending", "summary.fe_redox_split.native_fe_partition.alpha_s_evaluation was not emitted.");
      const seriesResistance = isRecord(partition.series_resistance)
        ? recordTable(partition.series_resistance, "Native Fe series-resistance model")
        : pendingBlock("Series-resistance provenance pending", "summary.fe_redox_split.native_fe_partition.series_resistance was not emitted.");
      partitionHtml = `<div class="sec-p1-partition-head">${[
          metric("Native Fe pool", numberValue(partition, "native_fe_pool_mol", "mol")),
          metric("Vapor route", numberValue(partition, "native_fe_vapor_mol", "mol")),
          metric("Tap route", numberValue(partition, "native_fe_tap_mol", "mol")),
          metric("Vapor capacity", numberValue(partition, "native_fe_vapor_capacity_mol_hr", "mol/h"))
        ].join("")}</div>${factsTable(partitionRows, "Native Fe partition detail")}<details class="sec-p1-nested-disclosure"><summary>HKL evaluation</summary>${alphaEvaluation}</details><details class="sec-p1-nested-disclosure"><summary>Series-resistance provenance</summary>${seriesResistance}</details>`;
    } else {
      partitionHtml = pendingBlock(
        "Native Fe partition pending",
        "summary.fe_redox_split.native_fe_partition is conditional and was not emitted for the terminal timestep. No empty or zero partition is assumed."
      );
    }
    const eventHtml = isRecord(event)
      ? factsTable([
          ["Event", textValue(event, "native_fe_event")],
          ["Reason", textValue(event, "native_fe_event_reason")],
          ["Status", textValue(event, "native_fe_event_status")],
          ["Event temperature", numberValue(event, "temperature_C", "°C")]
        ], "Native Fe saturation event")
      : pendingBlock(
          "Native Fe saturation event pending",
          "summary.fe_redox_split.native_fe_saturation_event was not emitted for the terminal timestep."
        );
    return `<details class="sec-p1-disclosure"><summary>Native Fe partition and saturation event</summary><h3>Partition</h3>${partitionHtml}<h3>Saturation event</h3>${eventHtml}</details>`;
  }

  function renderBreakdown(breakdown) {
    if (!isRecord(breakdown)) {
      return pendingBlock(
        "Redox-source breakdown pending",
        "The terminal timestep does not emit summary.redox_source_breakdown. Source forcing and refusal context are not inferred."
      );
    }
    const summaryRows = [
      ["Net attempted redox-source terms", numberValue(breakdown, "net_mol_o2_equiv", "mol O₂-eq")],
      ["Change in ln fO₂", numberValue(breakdown, "delta_ln_fO2")],
      ["Change in log₁₀ fO₂", numberValue(breakdown, "delta_log10_fO2")],
      ["Source terms applied", booleanValue(breakdown, "redox_source_terms_applied")],
      ["Combined skip reason", textValue(breakdown, "redox_source_skip_reason", "no skip reason")],
      ["Source campaign hour", numberValue(breakdown, "source_campaign_hour")]
    ];
    const nested = [
      { title: "Skipped reasons by source label", key: "skipped_reasons_by_label" },
      { title: "Ferric divergence", key: "ferric_divergence" },
      { title: "Fe redox respeciation", key: "fe_redox_respeciation" },
      { title: "Source context", key: "source_context" },
      { title: "Refusal context", key: "redox_source_refusal_context" }
    ].map(({ title, key }) => {
      const value = breakdown[key];
      return `<details class="sec-p1-nested-disclosure"><summary>${esc(title)}</summary>${isRecord(value)
        ? recordTable(value, title)
        : pendingBlock(`${title} pending`, `summary.redox_source_breakdown.${key} was not emitted.`)
      }</details>`;
    }).join("");
    return `${factsTable(summaryRows, "Redox-source forcing summary")}${nested}`;
  }

  function renderStage3(stage3) {
    if (!isRecord(stage3)) {
      return pendingBlock(
        "Stage 3 capture pending",
        "The terminal timestep does not emit summary.stage_3_capture. Fe contamination is not inferred."
      );
    }
    return `<div class="sec-p1-stage3">${[
      metric("Stage 3 Fe", numberValue(stage3, "Fe_kg", "kg")),
      metric("Stage 3 total", numberValue(stage3, "total_kg", "kg")),
      metric("Stage 3 Fe concentration", numberValue(stage3, "Fe_wt_pct", "wt%"))
    ].join("")}</div><div class="note sec-p1-link-note">Backend-emitted Stage 3 condenser capture. Fe wt% is never recomputed here. See the taps and stage-purity panels for downstream product detail.</div>`;
  }

  function timestepHourValue(timestep) {
    if (!isRecord(timestep) || !hasOwn(timestep, "hour") || timestep.hour === null || timestep.hour === undefined) {
      return "not emitted";
    }
    if (timestep.hour === "") return "empty";
    return isNumber(timestep.hour) ? esc(String(timestep.hour)) : "malformed";
  }

  function renderSelectedTimestep(artifact, index) {
    const timesteps = artifact && Array.isArray(artifact.timesteps) ? artifact.timesteps : [];
    const numericIndex = Number(index);
    const selectedIndex = Number.isInteger(numericIndex) && numericIndex >= 0 && numericIndex < timesteps.length
      ? numericIndex
      : null;
    const timestep = selectedIndex === null || !isRecord(timesteps[selectedIndex])
      ? null
      : timesteps[selectedIndex];
    const summary = timestep && isRecord(timestep.summary) ? timestep.summary : null;
    const redox = summary && isRecord(summary.fe_redox_split) ? summary.fe_redox_split : null;
    return `<div class="sec-p1-selected-head"><h3>Selected timestep Fe-redox</h3><span class="chip">hour ${timestepHourValue(timestep)}</span></div>${renderAuthority(redox)}${renderCore(redox, true)}`;
  }

  function onTimestep(artifact, index) {
    const target = root.document && typeof root.document.querySelector === "function"
      ? root.document.querySelector("#sec-p1-selected-timestep-body")
      : null;
    if (target) target.innerHTML = renderSelectedTimestep(artifact, index);
  }

  function render(artifact) {
    const timesteps = artifact && Array.isArray(artifact.timesteps) ? artifact.timesteps : [];
    const terminalStep = timesteps.length ? timesteps[timesteps.length - 1] : null;
    const summary = isRecord(terminalStep) && isRecord(terminalStep.summary)
      ? terminalStep.summary
      : null;
    const redox = summary && isRecord(summary.fe_redox_split) ? summary.fe_redox_split : null;
    const breakdown = summary && isRecord(summary.redox_source_breakdown)
      ? summary.redox_source_breakdown
      : null;
    const stage3 = summary && isRecord(summary.stage_3_capture) ? summary.stage_3_capture : null;
    const hour = timestepHourValue(terminalStep);

    const terminalHtml = `${renderAuthority(redox)}${renderCore(redox)}${renderPartition(redox)}<details class="sec-p1-disclosure"><summary>Redox-source forcing, refusal and ferric divergence</summary>${renderBreakdown(breakdown)}</details><details class="sec-p1-disclosure"><summary>Stage 3 condenser Fe contamination</summary>${renderStage3(stage3)}</details>`;
    return `<section class="sec-p1-fe-redox" id="sec-p1-fe-redox" style="--sec-p1-fe:${esc(speciesColor("Fe"))}" aria-label="Fe redox diagnostics"><h2><span class="sect">P1</span>Melt Fe redox diagnostics</h2><p class="sub">Terminal timestep · hour ${hour}. Artifact-emitted SSO-R state and downstream Stage 3 Fe consequence; no viewer-derived redox or purity values.</p><div class="sec-p1-terminal-state" id="sec-p1-terminal-state">${terminalHtml}</div><div class="sec-p1-selected-timestep" id="sec-p1-selected-timestep" aria-live="polite"><div id="sec-p1-selected-timestep-body">${renderSelectedTimestep(artifact, 0)}</div></div></section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({
    id: "sec-p1-fe-redox",
    render,
    onTimestep
  });
}(globalThis));
