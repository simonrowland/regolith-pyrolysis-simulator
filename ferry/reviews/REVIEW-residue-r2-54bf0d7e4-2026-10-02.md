# REVIEW — residue-r2: Hashimoto 24-run dual-α geometry-band predictions

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-y17`
- **Tip:** `54bf0d7e48529c90db8198ffc56dd0ef26c36f70` on `review/residue-r2`
- **Parent (R1b LAND):** `69f5828c4ec70877a6d390d712e50ff5909ece49` — confirmed ancestor (`git merge-base --is-ancestor`)
- **Range:** `69f5828c4ec70877a6d390d712e50ff5909ece49..54bf0d7e48529c90db8198ffc56dd0ef26c36f70` (one commit)
  - `54bf0d7e48529c90db8198ffc56dd0ef26c36f70` Add Hashimoto residue predictions
- **REQ:** `/workspace/ferry-inbox/REQ-residue-r2-from-regolith-physics-2026-10-02.md`
- **Bundle:** `/workspace/ferry-inbox/regolith-physics-residue-2026-10-02/r2-report.md`
- **Design context:** DESIGN-residue-predictor-r2 D6 (α arms), D9 (geometry band), §8 R2 Hashimoto landing; no residue-fitting
- **Date:** 2026-10-02 ~16:40–16:50 ET
- **Mode:** read-only for product code; extracts not edited; no force-push; no Mac listen pools. Targeted VPS unit tests only (no full W3 / full-store / full pytest).

## Scope

Residue predictor chunk R2: all 24 Hashimoto runs (120 cells) under both α arms (common-unity sensitivity αᵢ/αⱼ=1 absolute, and runtime catalog where available), geometry band (sphere constant / sphere shrinking / 4 mm disk), per-prediction provenance (assumed area, 2700 kg/m³ density fallback, unbuffered vacuum O from R1a, liquid/gas rows, d060_pending). Primary = common-unity + shrinking sphere, declared without reading residues. Exhaustion floor sqrt(eps)×initial oxide moles (16-ULP min); engine nonconvergence → one half-step retry then typed per-run partial refusal (cohort continues). Cached openimcc base-pressure scaling.

Files (`git diff --stat` vs R1b):

| Path | Δ |
| --- | --- |
| `simulator/battery/residue.py` | +856 / −1 |
| `tests/battery/test_hashimoto_residue_predictions.py` | +323 (new) |
| `tests/battery/test_residue_oxygen_balance.py` | +98 |

Worker claim “481 tests / 145 s” was **not** re-run as a bulk suite on this ~16GB VPS (policy). Seat re-ran the tip-touched suites only.

## Attack results

### (1) Primary choice and α policy defensibility — **PASS** (no tune)

- `_hashimoto_primary_policy()` returns the constants `(_HASHIMOTO_PRIMARY_ALPHA_ARM, _HASHIMOTO_PRIMARY_GEOMETRY)` = `(alpha_common_unity_sensitivity, sphere_shrinking)` with **no data input** (docstring + body).
- Provenance `geometry_primary_argument` states the fixed-density receding-melt sphere rationale and explicitly “declared without using residue observations.”
- Author extract assumption is ratio-only (`αᵢ/αⱼ=1`); absolute α=1 is flagged as declared sensitivity (`_HASHIMOTO_COMMON_UNITY_SOURCE`, `author_alpha_assumption.absolute_alpha_established=False`, assumption flags `alpha_assumed_unity_ratio_not_measured_alpha` / `alpha_assumed_common_unity_sensitivity`).
- Runtime-catalog arm records measured catalog α (Fe α=0.02 verified in test) with `alpha_assumed_runtime_catalog_system_mismatch` — not selected as primary.
- **Defensibility:** primary matches DESIGN D6/D9. Systematic FeO over-evaporation under primary (measured near disk end of geometry band) is a **physics/geometry/α-sensitivity outcome**, not grounds to retarget primary toward residues. Band is published beside every prediction; scoring remains out of R2 scope.

### (2) FeO over-loss: code defect vs physics — **PASS (physics/geometry, not code)**

Hand-check on `hashimoto-1983-run-17c5-1` (common-unity, T=1973.15 K, t=1668 s, A₀(sphere)=5.3733e-5 m²):

| Quantity | Value |
| --- | ---: |
| solved pO₂ (step 0) | `2.2340328845371497e-06` bar |
| engine p(Fe) | `0.6340413358085869` Pa |
| FeO parent channels | Fe(g) + FeO(g), both α=1 |
| hand HKL Fe mol/s (`α p / √(2πMRT) · A`) | `4.4903476084720103e-07` |
| BuiltinEvaporationFluxProvider Fe mol/s | `4.490347608472011e-07` (rel **1.2e-16**) |
| hand Fe from shared-parent analytic debit | `4.488063928813716e-07` |
| integrator evaporated Fe (1 s) | `4.488063928813583e-07` (rel **3.0e-14**) |
| cohort primary FeO wt% | **7.116857** (matches report 7.1169) |
| geometry band FeO | [3.8063, 27.1343]; measured 22.74 (near disk) |
| disk FeO / sphere_shrinking FeO | 27.13 / 7.12; A_sphere/A_disk ≈ 4.28 |

Fe channel pressure → builtin HKL → analytic parent debit path matches the integrator. Concurrent FeO(g) must be included in the FeO parent draw (Fe-only HKL leaves a ~5e-5 rel hole). No evidence of Fe-channel pressure/activity, area-evolution, or oxygen-root code defect driving the over-loss. Over-loss tracks the larger evaporating area under shrinking-sphere + absolute α=1 vs the disk end of the declared band.

### (3) Exhaustion floor and retry policy — **PASS**

- Floor: `max(√ulp(1)·N₀, 16·ulp(N₀))` with N₀ = initial sample oxide moles. Seat probe at Hashimoto start: floor = `2.600e-11` (= √eps·N; 16-ULP term smaller).
- Exhausted oxides zeroed and removed from active parent channels **before** engine pressure calls; remainder retained in atom-closure (`exhausted_atoms` subtracted). Injected test: FeO=6.84e-10 never reaches engine inventory; atom residual < 1e-15.
- Nonconvergence: one interval → two half-steps; either half failure → `ResidueEngineNonconvergence` with `retry` notice, `composition_mol` / `step` / `time_reached_s`; cohort continues. Injected pressure_model raises: calls==2 (coarse + first half), refusal typed, second run completes. Predictor monkeypatch: one `partial_diagnostic`, other 47 rows `complete`, 120 primary cells retained.

### (4) Provenance completeness — **PASS**

Required keys present on all 48 arm×run rows (test): experiment/source/locator, T, duration, mass, starting composition + preform mm, `sample_surface_area_status=not_tabulated`, primary geometry + argument, all three geometry policies with area/evolution/flags, density 2700 + source, α arm/source/author assumption/α_by_channel, runtime omissions, R1a oxygen model + unbuffered boundary, total P, pO₂ ranges by geometry, liquid_rows_used (openimcc gas + liquid row provenance), d060_pending, datapack/pack_digest, code_revision, integration (steps, pending_r3 refinement, exhaustion rule + floor, retry text), exhaustion/atom-closure/refusal maps by geometry, run_refusal, prediction_status, assumption_flags (incl. feo_melt_redox_stack2_pending, density_fallback, oxygen_unbuffered_vacuum). Also carries `openimcc_pin`, `openimcc_version`, `openimcc_model_id`.

### (5) No-residue-reading mutation — **PASS**

- `test_hashimoto_primary_policy_cannot_choose_the_arm_closest_to_residues`: blocks `Path.read_text` on `extracts-v2/kems-015-hashimoto-1983.yaml`; asserts closest-to-data `(alpha_runtime_catalog, disk_4mm_constant) ≠ _hashimoto_primary_policy()` and policy equals declared constants.
- Predictor sorts primary arm first with comment “selection is independent of measured rows”; `_predict_hashimoto_residue_cohort` docstring: “without reading residues.”
- Seat: policy function takes no arguments and cannot see measured Table-3 rows.

### (6) OpenIMCC pin — **PASS**

- Provenance `openimcc_pin` = `openimcc_bridge.OPENIMCC_RECORDED_PIN` = `openimcc @ git+https://github.com/simonrowland/openimcc@afcb5d80abb16d931174a5a587e4ffaee6cbcadc`.
- Matches `pyproject.toml` `[imcc]` extra pin `@afcb5d80abb16d931174a5a587e4ffaee6cbcadc`.
- Cached base-pressure (evaluate_gas at pO₂=1, then × pO₂^ν × 1e5 Pa) vs direct evaluate_gas at solved pO₂: **max rel 2.53e-16 across 19 channels** (report claim 2.1e-16). Seat used the recorded-pin install in the shared empirical venv.

## Targeted tests (VPS)

| Suite | Result |
| --- | --- |
| `tests/battery/test_hashimoto_residue_predictions.py` | **3 passed** |
| `tests/battery/test_residue_oxygen_balance.py` | **10 passed** (incl. +4 R2 exhaustion/nonconvergence nodes × IA/openimcc) |
| **Total tip-touched** | **13 passed** in ~134 s |

Not run (policy/cost): full W3, full-store score, Mac Studio full pytest, worker’s claimed 481-node bulk suite.

## Raw-result awareness (not scored; not a REVISE)

Primary systematically over-evaporates FeO (e.g. 17c5-1 pred 7.12 vs meas 22.74; 17c7 0.62 vs 15.53; several `!e` where 3–9 wt% FeO remains). Measured values sit inside the geometry band near the disk end. Per attack (2) this is consistent with declared area/α sensitivity, not an integrator or Fe-channel defect. Later scoring / R3 refinement / geometry policy debate is out of R2 landing scope; do not retarget primary toward residues.

## Verdict

**LAND `54bf0d7e48529c90db8198ffc56dd0ef26c36f70`**

No P1 REVISE items. Optional P2 awareness only: (a) primary FeO over-loss vs measured is a declared-assumption / geometry-band story for scoring, not a code fix; (b) `refinement_status: pending_r3` as self-declared; (c) d060 / feo_melt_redox_stack2 remain pending flags.

— regolith-empirical
