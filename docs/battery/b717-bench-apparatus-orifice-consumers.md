# b-717 — Consumers of bench / apparatus / orifice

Inventory first (backlog #12). No behaviour change in this commit.
Source tip when written: `8089eadbf` (`work-v064-green`). Target rows: 71
baseline physical-context assignments from REVIEW-b714-r2 (63 Hastie default
0.34 mm orifice, 6 Plante & Hastie 1983 composite Pt KMS/TMS apparatus, 2
Sauerborn MELTS on solar-furnace bench).

## Runtime consumers (gates / identity / scoring)

| Consumer | Path | What it reads | Effect when foreign geometry is present |
|---|---|---|---|
| `underdetermined_apparatus` | `simulator/battery/validity.py` | `experiment.apparatus.geometry` (`orifice_area_m2`, `orifice_diameter_m`, clausing), `apparatus.calibration`, `apparatus.wall` | Emits `RefusalReason.UNDERDETERMINED_APPARATUS` when quantity scopes by method and required geometry/calibration is missing or non-finite; also used when geometry is present but incomplete |
| Effusion-regime gate | `simulator/battery/validity.py` (`_printed_orifice_diameter`, orifice Kn / p÷d routes) | `experiment.apparatus.geometry.orifice_diameter_m` (+ cell pressure / calibration) | `EFFUSION_REGIME_UNVERIFIED` when orifice Kn cannot be grounded |
| Validity absorb | `run_validity_gates` → `absorb(underdetermined_apparatus(...))` | same | Row refused before numeric engine predict |
| Score flagged stratum | `simulator/battery/score.py` (`_unverified_apparatus_notices`, `FLAGGED_STRATUM_UNVERIFIED_APPARATUS`) | validity check payloads (`orifice_knudsen`, geometry determinants) | Marks `unverified-apparatus` stratum; blocks numeric residual |
| Cell-apparatus inference notices | `score.py` `_cell_apparatus_inference_notices` | `bench.cell_material*`, `experiment.apparatus.cell_material_and_liner` | Inference notices when cell material is used without grounded print |
| Bench resolve | `score.py` `_bench_for_score` | `experiment.bench_id` → `context.benches` | Solar-furnace / KMS / TMS bench facts join the row |
| Consumer inputs | `simulator/battery/consumer_inputs.py` `collect_consumer_inputs` | `bench.geometry` **and** `experiment.apparatus.geometry` for `orifice_diameter_m`, `orifice_area_m2`, `clausing_factor`; wall surfaces; calibrations | Builds waypoints consumed by readiness / engines |
| Escape-area waypoint | `simulator/battery/waypoints.py` `effective_escape_area` | `bench.geometry.orifice_*` (+ clausing, count) | Printed/derived effective orifice area |
| Orifice Knudsen waypoint | `waypoints.py` `_orifice_knudsen` / `consumer_readiness` | `bench.geometry.orifice_diameter_m` + T/P | Readiness gap `knudsen_number_orifice` |
| Validate bench FK | `simulator/battery/validate.py` | `experiment.bench_id` must resolve and match work | Hard validation error on dangling bench |
| Public export | `simulator/battery/__init__.py` | re-exports `underdetermined_apparatus` | Test / external callers |

Identity composition itself does **not** read orifice geometry; identity total
pressure may still be unknown while a foreign orifice is present. The
apparatus/orifice consumers above are the ones that turn an inherited physical
cell into gate behaviour.

## Migration / promotion (how foreign geometry lands)

| Path | Role |
|---|---|
| `apparatus_from_equipment` (`migrate.py`) | Builds `Apparatus` / `ApparatusGeometry` from observation `equipment` / values (incl. `orifice_mm` → `orifice_diameter_m` via `mm_to_m`) |
| `_ensure_experiment` + `_merge_experiment_lab_params` | Merges each landed observation’s apparatus onto the shared experiment (default `::{source_id}` when unbound) |
| Bench registry | Explicit `bench_id` on registry experiments (Hastie KMS/TMS, Plante kms/tms, Sauerborn `dlr-solar-vacuum-system`) |

## Tip audit at `8089eadbf` (before scoping fix)

| Cohort | Count | Inherited physical fact | Experiment at tip |
|---|---:|---|---|
| Hastie default orifice | 46 still dirty (16 of original 63 already on `hastie-1981-table2-quoted-fits` after b-714 R3; 1 R2 hash retired) | `apparatus.geometry.orifice_diameter_m = 0.00034` (from `remaining_figures_ledger` values.figures['20'].orifice_mm merged onto default) | `…::kems-020-hastie-1981-nbsir` |
| Plante composite apparatus | 6 | `apparatus.cell_material_and_liner` = composite Pt KMS+TMS (from unbound `fig19_nabo2_logP_ls` equipment) | `…::kems-027-plante-hastie-1983` |
| Sauerborn MELTS | 2 | `bench_id` → `dlr-solar-vacuum-system` | `…::jsc1-solar-series` |

Own-evidence orifice that **should** remain: `hastie_1981_k2_slag_orifice_0p34mm*` on `hastie-1981-pt-kms` (p.28 / Fig.20 caption).

## Out of scope here

Item #13 (store-wide class sweep of model_derived / quoted / compilation / ledger
on physical benches) is report-only and separate. Short note: cleaning the
Hastie default orifice and Plante default composite cell also clears those
facts for other unbound inheritors of the same defaults; full source-by-source
counts belong in the #13 report.
