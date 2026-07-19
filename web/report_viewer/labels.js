"use strict";

(function exposeReportLabels(root) {
  const SUBSCRIPTS = Object.freeze({
    0: "₀", 1: "₁", 2: "₂", 3: "₃", 4: "₄",
    5: "₅", 6: "₆", 7: "₇", 8: "₈", 9: "₉"
  });

  const ACCOUNT_LABELS = Object.freeze({
    "process.cleaned_melt": "Cleaned melt",
    "process.condensation_train": "Condensation train",
    "process.overhead_gas": "Overhead gas",
    "process.wall_deposit": "Wall deposit",
    "process.metal_phase": "Metal pool",
    "process.metal_phase_bottom_pool": "Metal pool (bottom)",
    "process.metal_phase_float_layer": "Metal pool (float)",
    "process.solid_char_carbon": "Solid char",
    "process.raw_feedstock": "Unprocessed charge",
    "process.spent_reductant_residue": "Spent reductant",
    "process.reagent_inventory": "Reagent inventory",
    "process.c7_al_credit": "Al credit line",
    "process.stage0_carbonate_feed": "Stage 0 carbonate feed",
    "process.stage0_perchlorate_feed": "Stage 0 perchlorate feed",
    "process.stage0_salt_feed": "Stage 0 salt feed",
    "process.stage0_volatile_feed": "Stage 0 volatile feed",
    "process.condensation_retained_holdup": "Condensation-train holdup",
    "terminal.offgas": "Offgas",
    "terminal.slag": "Slag",
    "terminal.drain_tap_material": "Tapped metal",
    "terminal.chromium_condensed_oxide_stored": "Stored chromium oxide",
    "terminal.oxygen_stage0_stored": "O₂ stored · Stage 0",
    "terminal.oxygen_mre_anode_stored": "O₂ stored · MRE anode",
    "terminal.oxygen_melt_offgas_stored": "O₂ stored · melt offgas",
    "terminal.oxygen_melt_offgas_captured": "O₂ captured · melt offgas",
    "terminal.oxygen_melt_offgas_vented_to_vacuum": "O₂ vented · melt offgas",
    "terminal.oxygen_bubbler_external_vented_to_vacuum": "O₂ vented · bubbler",
    "terminal.stage0_chloride_salt_phase": "Stage 0 chloride salt",
    "terminal.stage0_salt_phase": "Stage 0 salt phase",
    "terminal.stage0_sulfide_matte": "Stage 0 sulfide matte",
    "terminal.stage0_residual_carbonate_carbon": "Stage 0 residual carbonate carbon",
    "terminal.stage0_residual_refractory_carbon": "Stage 0 residual refractory carbon",
    "reservoir.oxygen_cistern_liquid_inventory": "O₂ cistern (LOX)",
    "reservoir.fo2_buffer": "fO₂ buffer",
    "reservoir.stage0_oxidant": "Stage 0 oxidant",
    "reservoir.stage0_process_gas": "Stage 0 process gas"
  });

  function fmtNum(value, unit = "") {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") return "not emitted";
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "not emitted";
    const suffix = unit ? ` ${unit}` : "";
    if (numeric === 0) return `0${suffix}`;
    if (Math.abs(numeric) < 1e-3) return `${numeric.toExponential(2)}${suffix}`;
    return `${numeric.toLocaleString(undefined, { maximumSignificantDigits: 4 })}${suffix}`;
  }

  function fmtRunId(id) {
    const value = String(id ?? "");
    const hashLike = /^(?:[0-9a-f]{24,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;
    return hashLike.test(value) ? `${value.slice(0, 8)}…` : value;
  }

  function prettySpecies(name) {
    const value = String(name ?? "");
    if (!/^(?:[A-Z][a-z]?\d*)+$/.test(value)) return value;
    return value.replace(/\d/g, (digit) => SUBSCRIPTS[digit]);
  }

  function accountLabel(account) {
    const value = String(account ?? "");
    if (ACCOUNT_LABELS[value]) return ACCOUNT_LABELS[value];
    const wallSegment = value.match(/^process\.wall_deposit_segment_stage_(\d+)_to_stage_(\d+)$/);
    if (wallSegment) return `Wall deposit · stage ${wallSegment[1]}→${wallSegment[2]}`;
    const reagent = value.match(/^reservoir\.reagent\.(.+)$/);
    if (reagent) return `Reagent reserve · ${prettySpecies(reagent[1])}`;
    const withoutPrefix = value.includes(".") ? value.split(".").slice(1).join(" ") : value;
    const words = withoutPrefix.replace(/[._]+/g, " ").trim();
    return words ? words.replace(/\b\w/g, (character) => character.toUpperCase()) : "Unclassified account";
  }

  // Snake/kebab feedstock ids → readable title case (lunar_mare_low_ti → Lunar Mare Low Ti).
  function prettyFeedstock(id) {
    const value = String(id ?? "").trim();
    if (!value) return "not emitted";
    return value
      .replace(/[_-]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/\b[a-z]/g, (character) => character.toUpperCase());
  }

  // Subscript digits inside free-text chemical tokens (O2 → O₂) without inventing species.
  function prettyChemText(text) {
    return String(text ?? "").replace(/\b(?:[A-Z][a-z]?\d*)+\b/g, (token) => prettySpecies(token));
  }

  root.ReportLabels = Object.freeze({
    fmtNum, fmtRunId, prettySpecies, accountLabel, prettyFeedstock, prettyChemText
  });
}(globalThis));
