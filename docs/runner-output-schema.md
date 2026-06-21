# Runner Output Schema

This document pins the JSON contract produced by the
`simulator.runner` module and the `python -m simulator.runner` CLI.
It is the single source of truth for both the CLI and the SocketIO
stream's `per_hour_summary` frames; the schema is asserted in
`tests/test_runner_smoke.py::test_runner_schema_shape_contract`.

**Schema version:** `1.3.1`
**Owning goal:** `#18 JSON-RUNNER-HARNESS`

Run the CLI as:

```bash
python -m simulator.runner \
    --feedstock=lunar_mare_low_ti \
    --campaign=C0 \
    --hours=24 \
    --output=runs/lunar_mare_24h.json \
    [--additive=C=30] \
    [--engines=config/engines.yaml] \
    [--engine=vapor_pressure:builtin-vapor-pressure] \
    [--backend=stub|alphamelts] \
    [--track=pyrolysis|mre_baseline] \
    [--allow-fallback-vapor] \
    [--allow-unmeasured-alpha-fallback] \
    [--force-builtin-vapor-pressure] \
    [--sio-start-temperature-c=1050] \
    [--sio-hold-temperature-c=1600] \
    [--sio-ramp-c-per-hr=15] \
    [--sio-liner-temperature-c=1100] \
    [--sio-po2-mbar=1] \
    [--started-at-utc=ISO8601] \
    [--kernel-commit-sha=SHA]
```

The runner always writes a JSON document to `--output` and exits `0`
when the run completes (`status: ok` or `partial`) or `1` when the run
fails (`status: failed` or `refused`).  A non-ok envelope is still a
well-formed JSON document so downstream pipelines never need to parse
stderr.

The `--sio-*` flags are deterministic C2A/SiO pre-run controls. They
apply after session construction and before hour advancement, matching
the in-process P6a trace harness used by the CLI-boundary parity test.

## Top-level structure

```jsonc
{
  "schema_version": "1.3.1",
  "run_metadata": {...},        // see "Run metadata"
  "final_state": {...},         // see "Final state"
  "final": {...},               // see "Final summary"
  "stage_purity_report": {...}, // see "Stage purity report"
  "vapor_pressure_source_report": {...}, // see "Vapor pressure source report"
  "shuttle_refusal_history": [...], // see "Shuttle refusal history"
  "pO2_enforcement_by_hour": [...], // see "pO2 enforcement"
  "per_hour_summary": [...],    // see "Per-hour summary"
  "shadow_trace": [...],        // see "Shadow trace"
  "status": "ok" | "partial" | "failed" | "refused",
  "reason": "",                 // machine-readable refusal reason, if any
  "error_message": ""           // populated when status != "ok"
}
```

All top-level keys are required.  Tests assert the **exact** set --
adding a new key requires bumping `RUNNER_SCHEMA_VERSION` and the
schema-shape assertion.

## Run metadata

```jsonc
"run_metadata": {
  "schema_version": "1.3.1",
  "feedstock_id":   "lunar_mare_low_ti",
  "campaign":       "C0",                    // starting campaign phase
  "hours_requested": 24,
  "hours_completed": 24,                     // <= hours_requested
  "mass_kg":         1000.0,
  "additives_kg":    {"C": 30.0},            // additive species -> kg
  "track":           "pyrolysis",            // or "mre_baseline"
  "backend":         "stub",                 // melt backend name
  "started_at_utc":  "2026-05-15T00:00:00Z", // ISO8601 UTC
  "engines_used": {
    "active": {                               // flat intent -> authoritative provider_id
      "vapor_pressure": "builtin-vapor-pressure",
      "stage0_pretreatment": "builtin-stage0-pretreatment",
      ...
    },
    "requested": {                            // operator overrides (Goal #19 forward-compat)
      "vapor_pressure": "builtin-vapor-pressure"
    },
    "registry": {                             // ChemistryKernel.registry.capability_summary()
      "vapor_pressure": {
        "authoritative": "builtin-vapor-pressure",
        "fallback":      null,
        "shadows":       ["vaporock"]
      },
      ...
    }
  },
  "kernel_commit_sha": "882250f10c...",       // repo HEAD; "unknown" off-tree
  "knudsen_regime_diagnostic": {              // present after condensation routing
    "status": "ok" | "warning" | "refused",
    "reason": "",
    "regime": "viscous" | "transitional" | "free_molecular",
    "segments": [...]
  }
}
```

* `started_at_utc` and `kernel_commit_sha` are overridable from the CLI
  (`--started-at-utc`, `--kernel-commit-sha`) and from
  `PyrolysisRun.run_metadata_overrides` so fixture-driven tests stay
  byte-stable across machines and clock drift.
* `engines_used.registry` is sourced from
  `ChemistryKernel.registry.capability_summary()` -- the same surface
  used to audit the current builtin-authoritative / VapoRock-shadow
  `VAPOR_PRESSURE` split.
* Any extra keys passed via `run_metadata_overrides` are forwarded
  verbatim; the runner does not interpret them.
* `knudsen_regime_diagnostic` reports the transport-regime check for
  the condensation train when a run reaches condensation routing.

## Final state

```jsonc
"final_state": {
  "process.cleaned_melt": {
    "SiO2": 12.345,             // mol (not kg) -- atom ledger is mol-native
    "FeO":  6.789
  },
  "terminal.offgas": {
    "H2O": 2.0
  },
  "reservoir.reagent.C": {
    "C": 30.0
  },
  ...
}
```

* Sourced from `AtomLedger.mol_by_account()`.  The ledger is the
  canonical store; kg projections are derivable from the registry but
  not duplicated in `final_state` to keep the output compact.
* Zero entries (`abs(mol) == 0.0`) are dropped.  Consumers should
  treat missing keys as 0.0.
* Every account named by `FLOW_MASS_ACCOUNTS` plus every reservoir
  account ever credited during the run is present.

## Final summary

```jsonc
"final": {
  "wall_deposit_by_species_kg": {"SiO": 0.01},
  "deposit_by_surface_species_kg": {
    "stage_0_to_stage_1": {"SiO": 0.004}
  },
  "pump_outlet_by_species_kg": "not_applicable_until_p0"
}
```

* `wall_deposit_by_species_kg` is the aggregate kg projection used by
  existing SiO report surfaces.
* `deposit_by_surface_species_kg` is the final wall deposit projection
  by interstage segment/species, sourced from the same snapshot/trace
  wall-deposit data exported per hour.
* `pump_outlet_by_species_kg` is P0-gated. Runner schema `1.3.1`
  reports the explicit sentinel `not_applicable_until_p0`; P6b will
  replace it with pump/outlet totals after molecular transport lands.

## Stage purity report

```jsonc
"stage_purity_report": {
  "stage_1_fe_condenser": {
    "stage_number": 1,
    "label": "Fe Condenser",
    "accepted_species": ["Fe"],
    "designated_species_kg": {"Fe": 12.345},
    "impurity_species_kg": {"SiO2": 0.123},
    "designated_kg": 12.345,
    "impurity_kg": 0.123,
    "total_kg": 12.468,
    "purity_fraction": 0.9901,
    "verdict": "PURE" | "MIXED" | "CONTAMINATED",
    "warning": ""
  }
}
```

* Sourced from `simulator.condensation.stage_purity_report()`.
* Accepted species come from `simulator/condensation_routing.py`.
* Verdict thresholds: `PURE` when purity is above 95%, `MIXED`
  from 80-95%, and `CONTAMINATED` below 80%.

## Vapor pressure source report

```jsonc
"vapor_pressure_source_report": {
  "species": {
    "Na": "builtin_authoritative",
    "K": "builtin_authoritative"
  },
  "summary": {
    "builtin_authoritative": {"count": 2, "percentage": 100.0}
  },
  "total_species": 2
}
```

* Sourced from `EquilibriumResult.vapor_pressures_source` after the
  post-equilibrium kernel refresh. Current mainline values are
  `builtin_authoritative` for the builtin Antoine/Ellingham provider,
  `thermoengine` when a backend pressure exactly confirms the builtin
  value, `builtin_fallback` only for an explicit fallback path, and
  `kernel_diagnostic` as a sentinel. Legacy backend labels such as
  `alphamelts_python_api`, `alphamelts_text`, and `vaporock` can appear
  only on older or fallback artifacts; the current VapoRock shadow payload
  is diagnostic-only.
* Percentages are species-count percentages for the latest vapor
  pressure surface used by the evaporation path.
* When VapoRock shadow data exists, `simulator.runner` copies it from
  `_last_vapor_pressure_diagnostic["vaporock_full_speciation_Pa"]` into
  the runner diagnostics under the same key. It is not an authoritative
  source-report value.

## Shuttle refusal history

```jsonc
"shuttle_refusal_history": [
  {
    "reaction_family": "C3_K",                 // "C3_K" | "C3_NA"
    "reagent": "K",                            // "K" | "Na"
    "hour": 24,                                // batch hour (absolute)
    "campaign_hour": 4,                        // hours into current campaign
    "campaign": "C3_K",                        // CampaignPhase.name
    "temperature_C": 1275.0,                   // melt T at the refused step
    "target_stage": "feo_cleanup",             // Na-shuttle only
    "diagnostic": {                            // engine-emitted detail
      "reason_refused": "thermodynamic_margin_nonpositive",
      "thermo_deltaG_kJ_per_mol_O2": -52.2,
      "k_reduction_margin_kJ_per_mol_O2": -125.7,
      "accepted_targets": [],
      "refused_targets": ["FeO"]
    }
  }
]
```

* Empty list when no shuttle step was refused. Every entry is one C3
  K-shuttle (`C3_K`) or Na-shuttle (`C3_NA`) dispatch that the S1b
  shuttle T-acceptance gate rejected (thermodynamic margin ≤ 0 at the
  current melt T per the post-V1c JANAF Ellingham crossovers).
* Sourced from `simulator/extraction.py::_shuttle_inject_K` /
  `_shuttle_inject_Na`; accumulated on
  `PyrolysisSimulator._shuttle_refusal_history` and surfaced verbatim
  here so an operator can see WHICH recipe step the engine refused and
  WHY (autoreview r3 P2, 2026-05-27 — previously refusal vs benign
  no-op were indistinguishable to downstream consumers).
* Status remains `ok` / `partial` when only individual shuttle steps
  are refused (the recipe can still complete; the C3 cleanup target is
  what suffers). `status='refused'` is reserved for whole-run refusals
  that cannot continue, e.g. `KnudsenRegimeRefusal`.

## Per-hour summary

```jsonc
"per_hour_summary": [
  {
    "hour":     1,                           // simulated hours since batch start
    "campaign": "C0",                        // CampaignPhase.name
    "T_C":      75.0,                        // melt temperature in Celsius
    "P_total_bar": 0.0,                      // overhead total pressure (bar)
    "pO2_bar":     0.0,                      // pO2 in bar
    "mass_balance_pct": 2.6e-13,             // |mass_in - mass_out| / mass_in * 100
    "O2_yield_kg_cumulative": 0.0,           // legacy key; source-side O2 potential, kg
    "O2_source_side_potential_kg_cumulative": 0.0, // honest alias for the same value
    "O2_metric_label": "source-side O2 potential (emitted; not recovered)",
    "metal_yields_kg": {                     // metal product totals so far
      "Fe": 5.0
    },
    "condensation_train_kg": {               // condensation train cumulative kg
      "H2O": 0.3
    },
    "vapor_species_kg_hr": {"SiO": 0.01},    // vapor flux by species (kg/hr)
    "wall_deposit_delta_kg": {               // this hour's wall deposit kg
      "stage_0_to_stage_1": {"SiO": 0.001}
    },
    "wall_deposit_cumulative_kg": {          // cumulative wall deposit kg
      "stage_0_to_stage_1": {"SiO": 0.004}
    },
    "Kn": 0.00035,                           // Knudsen number, or null
    "regime": "viscous",                    // named Knudsen regime, or ""
    "transport_formula_id": "not_applicable_until_p0"
  },
  ...
]
```

* One entry per simulated hour up to `hours_requested`, or until the
  simulator marks the batch `is_complete()` (whichever comes first).
* `mass_balance_pct` is the simulator's own
  `HourSnapshot.mass_balance_error_pct` -- expected to stay below
  `5e-12 %` per the invariant tracked in `tests/test_mass_balance.py`.
  The runner does not enforce this on its own; the golden fixtures do.
* `O2_yield_kg_cumulative` is retained for serialized compatibility but
  must be read as source-side emitted O2 potential, not recovered,
  captured, or analyzer-visible O2. `O2_source_side_potential_kg_cumulative`
  is the preferred alias with the same numeric value. The downstream
  sink/recovery model is Phase B; recovered-O2 claims do not belong in
  this runner output yet.
* `metal_yields_kg` is sourced from `PyrolysisSimulator.product_ledger`
  filtered to a curated list of metal species (see
  `simulator/runner.py::_METAL_PRODUCT_SPECIES`).  Non-metal products
  appear in `final_state` and `condensation_train_kg`.
* Lab-schedule runs may include `pO2_enforcement` on each affected
  per-hour row: `{hour, schedule_id, schedule_time_h, setpoint_mbar,
  achieved_mbar, p_total_mbar, limited_by_total_pressure, status}`.
  The top-level `pO2_enforcement_by_hour` list repeats those rows for
  artifact consumers that do not scan every hour.
* P6a wall-deposit fields are direct projections of
  `HourSnapshot.wall_deposit_by_segment_species_delta` and the running
  sum of those deltas. The report layer does not recompute deposits.
* `vapor_species_kg_hr` is copied from `HourSnapshot.evap_flux`.
* `Kn` and `regime` are copied from
  `HourSnapshot.knudsen_regime_summary` when overhead transport has run.
* `transport_formula_id` is P0-gated and remains the explicit sentinel
  `not_applicable_until_p0` until P6b.

## Shadow trace

```jsonc
"shadow_trace": [
  {                                          // operator-decision auto-apply
    "event": "operator_decision",
    "hour": 18,
    "decision_type": "PATH_AB",
    "choice": "A",
    "recommendation": "A",
    "options": ["A", "B"],
    "context": "default Path A for pyrolysis track"
  },
  {                                          // kernel parity warning
    "event": "parity_warning",
    "intent": "vapor_pressure",
    "authoritative_provider": "builtin-vapor-pressure",
    "shadow_provider": "vaporock",
    "delta": ...
  }
]
```

Two event types appear today:

* **`operator_decision`** -- emitted whenever the runner auto-applies a
  decision the simulator paused for.  `choice` equals `recommendation`
  when one is set; otherwise the first option in `options`.  This is
  Goal #18's contract for unattended runs: every routing decision the
  runner made on the operator's behalf shows up in the trace.
* **`parity_warning`** / **`parity_error`** -- forwarded verbatim from
  the kernel's `Planner.shadow_trace` so a downstream consumer can
  diff authoritative vs. shadow provider results without
  instrumenting the kernel directly.

The runner deliberately drops the kernel's bulk `shadow_dispatch`
records (one per shadow call) -- they are useful for kernel-internal
diagnostics but noise for the operator-facing JSON.

## Determinism

* Run the same scenario twice in the same process: byte-identical JSON
  output.  Enforced by `test_runner_is_deterministic`.
* Cross-machine fixture stability requires pinning `started_at_utc` and
  `kernel_commit_sha`; without those the run still produces valid JSON
  but a different bytes-on-disk result.
* No RNG seeds are pinned today because the simulator does not call
  `random` / `numpy.random` directly in its mainline step path.  If
  Goal #19 or #21 introduce stochastic providers, this section will
  list the seeded surfaces.

## Web stream parity

`web/events.py` calls `simulator.runner.build_per_hour_summary` for
every `simulation_tick` so the SocketIO stream emits the *same* per-hour
shape the CLI commits to fixtures.  Web ticks additionally carry the
existing `simulation_tick` keys (turbine telemetry, decision payload,
etc.) -- the runner contract is a strict subset.
