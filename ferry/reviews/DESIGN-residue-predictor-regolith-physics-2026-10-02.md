# Evaporation-residue predictor for rail `residue_composition` (t-1082)

Design-only. No product code in this delivery. Built on green tip
`ad3ca2a4b320de0c8fc11f835490ee826ce06d7e` (`origin/work-v064-green`) plus the
routing pattern in `76b550019` (`review/ia-battery-wiring`: internal-analytical
battery adapter). No invent of extract observations or equipment FKs. Does **not**
redesign melt-redox; d-061 (lance throttle / commanded headspace pressure) is cited
only as a flag-reference for free-evaporation BC vs recipe pressure policy.

**North star:** predict residue oxide (or element) composition after free
(Langmuir) evaporation from the **simulator’s own** Hertz–Knudsen–Langmuir fluxes
(engine vapour pressures × melt activities × evaporation coefficients α),
integrated over the experiment’s run time, routed through the battery producer the
same way `76b550019` routes internal-analytical vapour pressures — **no second
model**.

**Store scope today (green):** 464 admitted residue candidates refuse as
`quantity_not_predicted` (`simulator/battery/score.py` ~L2854–2870;
`tests/battery/test_score.py::test_residue_composition_has_its_own_rail_and_typed_engine_refusal`).
Split: Hashimoto 1983 `kems-015` = 120 (`oxide_wt_percent`); Sossi 2019 `kems-012`
= 344 (`element_ppm_by_mass`). Wang 2001 to acquire (out of scope for this design’s
acceptance fixtures).

---

## 0. Decision summary (one screen)

| # | Decision | Choice |
|---|---|---|
| D1 | Call path | **Smaller kernel producer**, not the hero hourly tick on a full crucible fixture |
| D2 | Vapour pressures | Same engine channels as vapour rail (`internal-analytical`, `openimcc`, …) via equilibrate / IA core route; then HKL |
| D3 | Flux kernel | `engines.builtin.evaporation_flux.BuiltinEvaporationFluxProvider` (authoritative `EVAPORATION_FLUX`) with vacuum BCs; `simulator.chemistry.langmuir_knudsen.langmuir_molar_flux` as the diagnostic twin |
| D4 | Time integration | Integrate over **printed experiment run time** with adaptive sub-steps; refine until per-oxide residue change < acceptance ε |
| D5 | Identity | Extend `RESIDUE_COMPONENT_COMPOSITION` profile: require starting `composition` + `exposure.duration_s` (+ mass / P / oxygen as below) — engine-side change (regolith-main) |
| D6 | α | Per-species from catalog when measured; else **flagged** assumption (Hashimoto: α=1 per eqn 16), never silent |
| D7 | Scoring metric | Hashimoto: absolute wt% residual per oxide per run; Sossi: relative (or dex) on ppm per element per run; pin store revision |

---

## 1. Which simulator path to call

### 1.1 Reject: hero evaporation step on full crucible fixture

The hero path in `PyrolysisSimulator` (`simulator/core.py` ~L13501–13650 and
`simulator/evaporation.py`) is the production hourly tick:

1. `_get_equilibrium()` → vapour pressures / activities  
2. `_calculate_evaporation(equilibrium)` → HKL via builtin provider  
3. `_apply_analytic_evaporation_depletion`  
4. cold-train capacity coupling / overhead bleed  
5. `_route_to_condensation` (8-stage train)  
6. `_update_melt_composition(evap_flux)`  
7. Fe-redox respeciation, campaign phases, turbine / duct geometry  

For battery residue scoring this is the wrong outer loop: condensation train,
capacity coupling, campaign gating (`C0`/`C2A`/…), and equipment designer
side-effects are not part of Hashimoto’s vacuum free-evaporation or Sossi’s open
furnace. Driving a full hero fixture would also drag ~hour ticks and equipment
sizing onto a 16 GB empirical box.

### 1.2 Accept: smaller kernel producer (mirror `76b550019`)

`76b550019` taught the pattern: battery scoring must **not** go through
`PyrolysisSimulator._get_equilibrium` as the active-backend path for every engine;
it resolves an engine cell / adapter, reads vapour pressures, and scores. Residue
extends that by one kinetic step:

```
identity (start composition, T, t_run, P, fO2/oxygen, mass, area, α map)
        │
        ▼
engine vapour pressures + activities   ← same channels as vapour rail
(internal-analytical / openimcc / …)     (score.py predict_with_engine arm)
        │
        ▼
BuiltinEvaporationFluxProvider.dispatch  ← EVAPORATION_FLUX
  controls: vapour_batch_flux_pressures_Pa,
            melt_surface_area_m2, alpha, overhead_partials_Pa≈0 (vacuum),
            available_oxide_kg, T
        │
        ▼
time integrator (compose melt ← subtract evaporated oxides over t_run)
        │
        ▼
project residue to observation subtype (oxide wt% or element ppm)
        │
        ▼
MetricOperation.ABSOLUTE (Hashimoto) / RELATIVE-or-DEX (Sossi; see §6)
```

**Concrete symbols (green tip):**

| Role | Symbol |
|---|---|
| Battery refuse today | `predict_with_engine` early return for `Quantity.RESIDUE_COMPONENT_COMPOSITION` (`score.py` ~L2854) |
| IA vapour arm pattern | `76b550019` diff on `score.py` + `diagnostic_helpers/binary_pot_battery.py` (`equilibrate_cell`, `vapor_pressures_Pa`, `vapor_pressure_backend_status_reason`) |
| Authoritative flux | `engines.builtin.evaporation_flux.BuiltinEvaporationFluxProvider` |
| Melt update twin | `_update_melt_composition` / analytic depletion helpers in `simulator/evaporation.py` (~L3274+, ~L4490) — **reuse math, not the hero caller** |
| Diagnostic flux | `simulator.chemistry.langmuir_knudsen.langmuir_molar_flux` |
| Rail / metric already wired | `Rail.RESIDUE_COMPOSITION`; `MetricOperation.ABSOLUTE` (`score.py` ~L241, ~L564) |

### 1.3 Minimum state

| Field | Required? | Source on Hashimoto / Sossi |
|---|---|---|
| Temperature_K | yes | identity / Table 2 |
| Starting composition (oxides or ppm seed) | yes | Hashimoto Table 1 / `point_conditions.starting_composition`; Sossi `starting_component_ppm` |
| Run duration | yes | Hashimoto `point_conditions.time_min` (Table 2); Sossi `time_min` |
| Sample mass | yes for extensive rates | Hashimoto experiments: **printed 100.0 mg** → `0.0001` kg (`extracts/…experiments[].sample.mass_kg`) |
| Melt surface area | yes for HKL | Hashimoto: **not tabulated** (see §Addendum b) |
| Total pressure / vacuum | yes | Hashimoto ~1×10⁻⁴ Torr → ~0.0133 Pa; Sossi 101325 Pa |
| Oxygen condition | yes when experiment declares a buffer / fO2; typed refusal when required-and-missing | Hashimoto `fO2_control=none` + vacuum prose; Sossi `fO2_log` printed |
| α by species | yes (measured or flagged assumption) | §3 |
| Engine id + datapack / liquid-row provenance | yes on every residual notice | §Addendum a |

### 1.4 Time integration and refinement

**Do not** use the hero’s fixed 1 h campaign ticks for lab runs that last 1.29–129 min
(Hashimoto) or 15–930 min (Sossi).

**Algorithm (proposed):**

1. Set `t_end = exposure.duration_s` from the observation (printed).  
2. Start with `N` equal sub-steps, `dt = t_end / N`, `N0 = max(8, ceil(t_end / 60))`
   (at least ~1 min resolution, ≥8 steps).  
3. Each step: re-query vapour pressures at current melt composition (activities
   change as FeO/SiO₂/… deplete); call EVAPORATION_FLUX; apply analytic depletion
   for `dt` (reuse `_apply_analytic_evaporation_depletion` math with `dt_hr = dt/3600`);
   update oxide inventory / normalize for residue projection.  
4. **Refinement check:** re-run with `2N` steps; accept when
   `max_i |w_i(N) − w_i(2N)| < ε` with `ε = 0.05` wt% absolute for
   `oxide_wt_percent`, or `ε_rel = 0.02` (2%) for `element_ppm_by_mass`.  
5. Cap `N` (e.g. 256) and emit notice `residue_time_refinement_unconverged` if the
   cap hits — still publish the best prediction with the flag (predict-and-flag).

Isothermal hold at the observation’s `temperature_K` (Hashimoto/Sossi tables are
per-T charges). Non-isothermal schedules are out of v1 unless `exposure.schedule`
is populated.

---

## 2. Free-evaporation boundary conditions

### 2.1 Vacuum (Hashimoto 1983)

Printed on green extracts (`point_conditions` on all 120 admitted Table 3 rows):

| BC | Printed value | Notes |
|---|---|---|
| Atmosphere | “No buffer gas or gas mix during the vacuum runs. Graphite crucible; …” | Vacuum free evaporation; **not** Knudsen cell |
| `fO2_control` | `"none"` | No gas buffer; paper notes high fO2 to keep Fe ferric cannot be sustained |
| Chamber vacuum | ~1×10⁻⁴ Torr (~0.0133 Pa) | Also T-dependent “pressure on throw” prose (~1.5×10⁻⁴ … 2×10⁻³ Torr) — use chamber vacuum as total_P; flag throw-pressure as diagnostic only |
| Regime | `langmuir_free_evaporation` | v1 extract metadata |

**Provider BC:** `overhead_partials_Pa` ≈ 0 for all evaporating species (true free
surface; `p_bulk → 0`). Do **not** raise headspace pressure to “make kinetics fit”
— pressure is an equipment/experiment knob. Cross-ref **d-061** only as: recipe
lance throttle keeps **commanded** headspace pressure; free-evap residue BC is the
opposite limit (vacuum), flagged `bc_free_evaporation_vacuum` so it is never
confused with duct-capacity throttling.

### 2.2 Open furnace / buffered gas (Sossi 2019)

Printed on admitted residue rows:

| BC | Printed | Notes |
|---|---|---|
| `total_pressure_Pa` | 101325 | Open furnace |
| `fO2_log` | per run (e.g. −8, −0.68, …) | **Required** identity / control input |
| `atmosphere` | `unknown` / `not_published` | Do not invent gas mix; use fO2_log as the oxygen condition |
| `time_min` | 15 / 60 / 120 / 720 / 930 | Printed |

For open-furnace, HKL still applies at the melt surface, but `overhead_partials_Pa`
and gas-side resistance may be non-zero if the series model is engaged. v1 residue
predictor: **Langmuir limit** (r_gas→0) unless a printed gas-side constraint exists;
flag `bc_open_furnace_langmuir_limit_assumed` when atmosphere is unpublished.

### 2.3 Typed refusals (oxygen / BC)

| Condition | Refusal |
|---|---|
| Experiment declares buffer / `fO2_log` / `fO2_Pa` but identity lacks oxygen | `ExecutionState.NOT_PROBED`, `RefusalReason.IDENTITY_INCOMPLETE`, reason `oxygen_condition_missing` |
| Vacuum run with contradictory commanded high pO2 | `INVALID_IDENTITY` / `bc_conflict_vacuum_vs_buffered` |
| Missing `total_pressure_Pa` when profile requires it | `IDENTITY_INCOMPLETE` |
| Missing melt area when HKL needs it | `IDENTITY_INCOMPLETE`, reason `melt_surface_area_missing` (after assumptions exhausted — see Addendum b) |

Hashimoto’s `fO2_control=none` is a **declared** oxygen condition (unbuffered
vacuum), not a missing one — do not refuse; flag `oxygen_unbuffered_vacuum`.

---

## 3. Evaporation coefficients

### 3.1 Engine policy (already on green)

`BuiltinEvaporationFluxProvider` fails loud on missing α (`missing_evaporation_alpha`,
SC-67) unless `allow_unmeasured_alpha_fallback` is explicitly gated
(`engines/builtin/evaporation_flux.py` ~L1016+, ~L1282+). VapoRock-fit provenance is
refused at runtime (`simulator/evaporation.py::_assert_runtime_alpha_source_not_vaporock`).
Broad-proxy rows are stripped from the measured-α control view
(`_measured_alpha_control_view`).

### 3.2 Per-species plan for residue predictor

| Species family | Source on green | Residue-predictor stance |
|---|---|---|
| Fe (melt) | Fedkin/Hashimoto Langmuir class evidence in `simulator/evaporation_classes.py` (~0.24 central for Fe class) | Prefer **class / catalog α** when the battery already treats it as measured intrinsic; else flagged |
| Mg | Fedkin Mg Hashimoto ~0.25 class; Richter Mg 0.04 (different system) | Same — do not silently mix Richter CAI γ into FCMAS |
| SiO | Hashimoto Langmuir series 0.12–0.21 (Fedkin Table 3 pins); class prior 0.04 with wide residual | Use class / pin with notice naming the observation_id |
| Ca, Al channels | Generally unmeasured intrinsic melt α on green | **Flagged assumption** |
| Hashimoto author’s modelling assumption | Extract: α_i/α_j = 1 in eqn (16); Hertz–Knudsen definition eqn (14); obs ids `…alpha_hertz_knudsen_assumed_unity…` | When running the **Hashimoto like-for-like** cell, default α=1 for all five oxide parents **with** notice `alpha_assumed_unity_hashimoto_eqn16` — never silent |

**Rule:** every α that is not a measured intrinsic row carries
`NoticeKind` / residual notice with `alpha_source`, `alpha_value`, and
`alpha_assumed=true`. Catalog class centrals used outside their evidence system
are also `alpha_assumed=true` (system mismatch).

### 3.3 Interaction with like-for-like scoring

Two scorer arms (both publish α provenance):

1. **Author-comparable arm (Hashimoto):** α≡1 flagged — matches Hashimoto’s Stage
   modelling assumption so residuals diagnose vapour-pressure / activity / area,
   not a second α model.  
2. **Simulator-production arm:** catalog / class α with SC-67 refusals — diagnoses
   production kinetics as the plant would run.

v1 acceptance for t-1082 lands arm (1) for Hashimoto 120 and arm (2) as a parallel
column; Sossi uses arm (2) unless an author α is printed (Sossi Na adopted unity is
alkali-specific evidence, not a blanket for Gd/La/…).

---

## 4. Identity profile for `RESIDUE_COMPONENT_COMPOSITION`

### 4.1 Today (green `ad3ca2a4b`)

`simulator/battery/identity.py` ~L678–679:

```python
elif q is Quantity.RESIDUE_COMPONENT_COMPOSITION:
    req("temperature_K", "subtype")
```

Axes neither required nor `permitted_not_applicable` are **forbidden-as-value**
(~L725). Consequence: starting composition is stripped from equality even though
migrator already stores rich `point_conditions` (`starting_composition`,
`time_min`, `VF_wt_pct`, …) on Hashimoto rows. REQ: composition must carry through;
regolith-main: that change belongs with the engine.

### 4.2 Proposed profile (exact fields)

```text
required:
  temperature_K
  subtype                          # oxide_wt_percent | element_ppm_by_mass | …
  composition                      # STARTING melt / charge composition
  exposure                         # Exposure.duration_s = run time; area_m2 when known
  total_pressure_Pa
  sample_mass_kg                   # extensive inventory for integration

oxygen:
  if subtype/experiment is buffered or fO2 is in point_conditions:
      require fO2_Pa  (or migrate fO2_log → Pa on identity)
  else:
      permit fO2_Pa as not_applicable with reason oxygen_unbuffered_vacuum
      (Hashimoto)

permitted_not_applicable:
  per, standard_pressure_Pa, reaction, formation_elements, reference_state,
  reservoir, sweep_gas, wall
  # fO2_Pa only when unbuffered vacuum declared

forbidden-as-value: (none beyond the usual closed set)
```

`Exposure` already exists (`identity.py` ~L352–355): `area_m2`, `duration_s`,
optional `schedule`. Map `point_conditions.time_min` → `exposure.duration_s`
(= minutes × 60) in the engine/migrator identity build — design only here; no
silent default duration.

`composition` is the **starting** composition (Table 1 / starting ppm), not the
measured residue. Residue values stay on `observation.value`.

### 4.3 How residue rows are matched

Match key for like-for-like (same row ↔ same prediction):

| Axis | Hashimoto | Sossi |
|---|---|---|
| `source_id` | `kems-015-hashimoto-1983` | `kems-012-sossi-2019` |
| Run / charge | Table 3 `row=` token in `observation_id` (e.g. `c3-(2)`, `b6`) | run / table locator in id |
| `temperature_K` | identity | identity |
| `species.formula` | oxide (`FeO`,`MgO`,`SiO2`,`CaO`,`Al2O3`) | element (`Gd`,`La`,…) |
| `subtype` | `oxide_wt_percent` | `element_ppm_by_mass` |
| Starting composition | `identity.composition` equals Table 1 Ave. (single start on green) | `starting_component_ppm` / composition seed |
| Run time | `exposure.duration_s` | same |

One integrator call per **(run, T)** yields a full residue vector; then project each
species cell. Do not re-integrate per oxide row independently (would drift).

VF (vaporized fraction, Hashimoto Table 3 `VF_wt_pct`) is a **check channel**, not
an identity axis: predicted mass loss / start mass vs printed VF gets its own
residual notice `vf_wt_pct_residual` when both exist.

---

## 5. Physics caveats (carry as flags)

### 5.1 Internal-analytical alkali γ constant @ 1500 K (b-648)

IA alkali activities use a constant γ anchored at 1500 K (K slope diagnosis,
ticket **b-648**). Residue rows that depend on alkali vapour (Sossi alkalis if/when
admitted; any IA path that couples alkali p_i into oxygen balance) must attach:

`flag: ia_alkali_gamma_constant_1500K` / `ticket: b-648`

Hashimoto FCMAS majors (Fe–Mg–Si–Ca–Al) are not alkali-buffered; still emit the
flag whenever the **engine channel** is `internal-analytical` and alkali species
appear in the vapour batch map (defensive). OpenIMCC channel does not inherit this
flag unless it uses the same γ shortcut (it does not on green).

### 5.2 FeO-bearing melts 1973–2273 K — M5 / M2 / M4 (stack2, not on green)

Hashimoto’s T window sits in the Fe redox refactor regimes **M5 / M2 / M4**
described in melt-redox DESIGNs (stack2). **Green tip does not yet carry stack2.**
Residue predictions on green therefore run under pre-stack2 Fe accounting.

Mandatory notice on every Hashimoto (and any FeO-bearing) residual:

`flag: feo_melt_redox_stack2_pending`  
`regimes_named: M5,M2,M4`  
`temperature_window_K: [1973, 2273]`  
`engine_tip: origin/work-v064-green`  

Do not pretend stack2 equalities apply on green; do not block scoring — predict and
flag. When stack2 lands, re-pin store revision and re-score (§6).

### 5.3 OpenIMCC liquid-row extrapolation (see Addendum a)

Al₂O₃(l) / CaO(l) / MgO(l) LAM1987 rows are below their fit/melting floors at
Hashimoto T — flag `openimcc_liquid_row_extrapolated` with the row ids from
Addendum a. Owner d-060 may switch defaults; provenance on every notice keeps
scores comparable.

---

## 6. Like-for-like scoring plan and residual metric

### 6.1 Cohort

| Cohort | N live | Subtype | Store pin |
|---|---:|---|---|
| Hashimoto Table 3 | 120 | `oxide_wt_percent` | green store revision that admits these 120 (c04bff249 landing lineage; tip `ad3ca2a4b`) |
| Sossi residue | 344 | `element_ppm_by_mass` | same store revision |
| **Total** | **464** | | refuse → predict |

Same rows, same store revision, same observation_ids. Engines: at least
`internal-analytical` and `openimcc` where openimcc can produce a residue
(FCMAS parents in pack; refuse cleanly outside supported species — already a
pattern in `score.py`).

### 6.2 Residual metric

Keep `MetricOperation.ABSOLUTE` as the **default enum** for the quantity (already
set). Specialize in the residue producer / report layer:

| Subtype | Primary residual | Secondary |
|---|---|---|
| `oxide_wt_percent` | `r_i = w_i,pred − w_i,obs` (wt%, same 100% normalized basis as printed) | Optional dex on mole fraction for diagnostics only — **not** the decision metric |
| `element_ppm_by_mass` | relative `r = (c_pred − c_obs)/c_obs`, or dex `log10(c_pred/c_obs)` when c>0 | Absolute ppm for near-zero cells |

**Per run time:** group residuals by `(source_id, run_id, T, t_run)` so a 16.7 min
charge is not averaged with a 129 min charge. Publish:

- per-oxide (or per-element) residual  
- vector L2 / max-norm per run  
- VF residual when printed  

OpenIMCC column only where the pack can form the vapour channels for that parent;
otherwise typed `OUTSIDE_SUPPORTED_SPECIES` / channel omit with liquid-row
provenance still recorded for the parents that did run.

### 6.3 No second model

OpenIMCC “residue” means: integrate **OpenIMCC vapour pressures** through the
**same** BuiltinEvaporationFluxProvider + integrator. It does **not** mean a
separate OpenIMCC residue API. Same for IA.

---

## 7. Luna-sized chunks (acceptance + runnable mutations)

### R0 — Identity profile + migrator wiring (engine-side)

Widen `profile_for(RESIDUE_COMPONENT_COMPOSITION)`; map `time_min` →
`exposure.duration_s`; map starting composition onto `identity.composition`; keep
qualitative `fO2_control` / atmosphere types (residue-rail delta already guards
this).

**Acceptance:** identity equality on two Hashimoto cells of the same run shares
composition+exposure; profile forbids stripping composition.  
**Mutation:** drop `composition` from required → equality must fail / producer
refuses `IDENTITY_INCOMPLETE`.

### R1 — Kernel producer skeleton

New battery helper (name sketch: `diagnostic_helpers/residue_battery.py` or arm
inside score) calling engine vapour → EVAPORATION_FLUX → integrator. No hero
fixture.

**Acceptance:** for a synthetic pot with α=0 for all species, residue = start
composition within 1e-12 relative.  
**Mutation:** force α=0 path off / inject unit α with p≡0 → still no change;
inject p>0 with α=0 → still no change; α>0 and p>0 → mass leaves.

### R2 — Hashimoto BC + printed kinetics

Wire vacuum BC, printed mass 100 mg, printed times, flagged area assumption
(Addendum b), α≡1 flagged.

**Acceptance:** 20 runs × 5 oxides cohort builds 120 predictions without
`quantity_not_predicted`; every row carries α and area notices.  
**Mutation:** silence α notice → test fails; invent mass ≠ 100 mg without flag →
fails.

### R3 — Time refinement gate

**Acceptance:** doubling N changes each oxide by < 0.05 wt% on a mid-VF Hashimoto
run (e.g. VF~20–35%).  
**Mutation:** fix N=1 coarse step and assert refinement notice fires vs refined
baseline.

### R4 — Like-for-like score report

Emit residual table for 120 Hashimoto + 344 Sossi at pinned store revision;
OpenIMCC column with liquid-row provenance (Addendum a).

**Acceptance:** report schema includes `liquid_rows_used`, `alpha_provenance`,
`bc_flags`, `feo_melt_redox_stack2_pending`, `store_revision`.  
**Mutation:** strip `liquid_rows_used` → schema test fails.

### R5 — Sossi oxygen completeness

**Acceptance:** missing `fO2_log`/`fO2_Pa` on a buffered Sossi-shaped identity →
typed `oxygen_condition_missing`. Printed fO2 runs predict.  
**Mutation:** delete fO2 from fixture → must refuse, not assume air.

### R6 — Production-α parallel column (optional after R4)

Catalog/class α arm beside Hashimoto α≡1 arm.  
**Acceptance:** both columns present; disagreements flagged, not averaged away.

---

## Addendum (a) — OpenIMCC liquid-row provenance in residual notices

Pending owner **d-060** (which liquid rows are the default for Al/Ca/Mg below
~2300 K). Until that lands, every OpenIMCC residue residual notice **must** record
exactly which condensate/liquid rows fed the gas channels.

**Default rows as shipped in current openimcc datapack**
(`openimcc/data/gas/condensate.csv`, provenance `LAM1987` /
`lam1987_transcribed` in `gas.py`):

| Parent oxide | Liquid / condensate row id | Fit T range (K) | Source tag |
|---|---|---|---|
| MgO | `MgO(l)` | 3100–3500 | `LAM1987` / `lam1987_transcribed` (LH87 Table 2) |
| CaO | `CaO(l)` | 2900–3800 | `LAM1987` / `lam1987_transcribed` (LH87 Table 2) |
| Al2O3 | `Al2O3(l)` | 2327–3000 | `LAM1987` / `lam1987_transcribed` (LH87 Table 2/3) |
| SiO2 | `SiO2(l)` | 1996–3000 | `LAM1987` |

Hashimoto window 1973–2273 K lies **below** MgO(l)/CaO(l)/Al2O3(l) floors (and
below/near SiO2(l) floor at the cold end). REQ cites regolith-engine Al.md T7:
LAM1987 Al₂O₃/CaO/MgO(l) gas-side fits ~0.3 / 0.5 / 0.2 dex too low at 1933 K,
worse at lower T.

**Notice payload (required keys):**

```text
liquid_rows_used:
  - {parent: MgO, row: "MgO(l)", source: LAM1987, T_fit_K: [3100, 3500],
     extrapolated_below_floor: true}
  - {parent: CaO, row: "CaO(l)", source: LAM1987, T_fit_K: [2900, 3800],
     extrapolated_below_floor: true}
  - {parent: Al2O3, row: "Al2O3(l)", source: LAM1987, T_fit_K: [2327, 3000],
     extrapolated_below_floor: true}
  - {parent: SiO2, row: "SiO2(l)", source: LAM1987, T_fit_K: [1996, 3000],
     extrapolated_below_floor: <bool at this T>}
datapack_version: <openimcc pack id>
d060_pending: true
```

When d-060 switches defaults, scores remain comparable by grouping on
`liquid_rows_used` (not by assuming the pack is immutable).

---

## Addendum (b) — Hashimoto printed vs assumed kinetic inputs

Census on green extracts (`data/literature/extracts-v2/kems-015-hashimoto-1983.yaml`
admitted Table 3 + twin `extracts/kems-015-hashimoto-1983.yaml` experiments /
metadata). **No numbers invented.**

### Printed (use as-is)

| Input | Printed | Where |
|---|---|---|
| Starting composition (wt%) | SiO₂ 35.43, Al₂O₃ 3.16, FeO 35.04, MgO 23.84, CaO 2.53 | Table 1 Ave.; `point_conditions.starting_composition` on all 120 |
| Temperature | 1700–2000 °C → 1973.15–2273.15 K | Table 2 / identity |
| Run time | 1.29, 2.15, 3.59, 5.99, 10, 16.7, 27.8, 46.4, 77.4, 129 min | Table 2 → `point_conditions.time_min` |
| Sample mass | **100.0 mg** (0.0001 kg) all 31 experiment rows | Sample preparation / experiments[].sample.mass_kg |
| Preform geometry (cold) | height 4.0 mm, diameter 4.0 mm, hole 1.2 mm; tubular pellet | metadata `starting_preform_mm` |
| Vacuum level | ~1×10⁻⁴ Torr during experiments | Results prose / total_pressure_Pa |
| fO2 control | `none`; graphite crucible; no buffer gas | point_conditions |
| Residue oxides + VF | Table 3 measured rows | observation values + `VF_wt_pct` |
| Author α modelling | α_i/α_j assumed unity (eqn 16) | extract obs + PDF p. 133 |

### Not tabulated / must be assumed (each → flagged assumption, never silent default)

| Input | Status on green | Proposed assumption | Flag |
|---|---|---|---|
| **Molten free-surface area** | `sample_surface_area_status: not_tabulated`; preform dims “are not a Knudsen orifice or molten free surface area” | Equal-volume sphere from 100 mg with a declared melt density, **or** sessile-drop area from preform diameter π(d/2)² as a lower bound — **owner picks one**; until pick, refuse area-missing **or** run both as sensitivity columns | `melt_area_assumed_<policy_id>`; never publish a bare area without this flag |
| Melt density (if sphere policy) | not printed | Must be a named reference density with citation; not a magic number in code | `melt_density_assumed` |
| α production values | author assumes unity; catalog has class centrals from Fedkin/Hashimoto series | See §3 — α≡1 for author-comparable arm | `alpha_assumed_unity_hashimoto_eqn16` |
| Oxygen fugacity number | control=`none` (unbuffered vacuum) | Do not invent IW±X; leave equality absent / unbuffered | `oxygen_unbuffered_vacuum` |
| Transient “pressure on throw” | printed as about-nominal by T | Not used as total_P driver in v1 | `pressure_on_throw_ignored_for_bc` |

**Explicitly not assumed silently:** run time (printed), sample mass (printed),
starting composition (printed), T (printed).

---

## 8. Out of scope / non-goals

- Redesigning melt-redox M0–M6 or implementing stack2 on green.  
- Acquiring Wang 2001 (track only).  
- Hero campaign optimization / cold-train sizing for residue scores.  
- Inventing equipment FKs or extract rows.  
- Mac listen pools / overnight CI beyond this design delivery.

---

## 9. Open questions for physics / main

1. **Melt area policy for Hashimoto** (Addendum b): equal-volume sphere vs sessile
   disk vs dual sensitivity — needs an owner pick before R2 hard-accepts.  
2. **d-060** liquid-row defaults for Al/Ca/Mg below ~2300 K — design already
   records LAM1987 row ids; switch must not rewrite old notices.  
3. **Sossi metric:** relative vs dex as the decision residual for ppm series.  
4. **Whether VF is a scored channel** or diagnostic-only on Hashimoto.  
5. Identity profile change sequencing with regolith-main (engine owns profile).

---

## 10. References (tips / files)

| Item | Ref |
|---|---|
| Green tip | `ad3ca2a4b320de0c8fc11f835490ee826ce06d7e` |
| IA battery wiring pattern | `76b550019` |
| REQ | `REQ-residue-predictor-design-from-regolith-physics-2026-10-02.md` |
| d-061 (BC flag only) | `RULING-lance-throttle-from-regolith-physics-2026-10-02.md` |
| Identity | `simulator/battery/identity.py` ~L352–355, ~L678–679 |
| Score refuse | `simulator/battery/score.py` ~L2854–2870 |
| Flux provider | `engines/builtin/evaporation_flux.py` |
| Hero tick (rejected outer loop) | `simulator/core.py` ~L13501+ |
| Hashimoto / Sossi extracts | `data/literature/extracts-v2/kems-015-…`, `kems-012-…` |
| OpenIMCC liquid rows | `openimcc/data/gas/condensate.csv` (`MgO(l)`, `CaO(l)`, `Al2O3(l)`, `SiO2(l)` / LAM1987) |

— regolith-empirical, seat `slot-y17`, 2026-10-02 ~10:50 ET
