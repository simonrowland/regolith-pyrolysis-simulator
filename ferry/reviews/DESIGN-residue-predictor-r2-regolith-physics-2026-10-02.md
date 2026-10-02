# Evaporation-residue predictor r2 for rail `residue_composition` (t-1082)

Design-only. No product code in this delivery. **Edit of r1**
(`DESIGN-residue-predictor-regolith-physics-2026-10-02.md`). Folds the sol
independent review (`sol-review-of-residue-r1.md`, verdict **UNSOUND as written**;
kernel-producer approach viable) and REQ-r2 hard requirements. Built on green tip
`191960ce8a657a25681aad624dd64670bf85df6a` (`origin/work-v064-green`), where
**internal-analytical is a landed battery engine** (no longer a pending wiring tip).
No invent of extract observations or equipment FKs. Does **not** redesign melt-redox;
d-061 (lance throttle / commanded headspace pressure) is cited only as a
flag-reference for free-evaporation BC vs recipe pressure policy.

**North star (unchanged):** predict residue oxide (or element) composition after free
(Langmuir) evaporation from the **simulator’s own** Hertz–Knudsen–Langmuir fluxes
(engine vapour pressures × melt activities × evaporation coefficients α),
integrated over the experiment’s run time, routed through a smaller kernel
producer — **no second model**.

**Store scope today (green `191960ce8`):** 464 live residue candidates refuse as
`quantity_not_predicted`. Split: Hashimoto 1983 `kems-015` = **24 runs × 5 oxides =
120** (`oxide_wt_percent`); Sossi 2019 `kems-012` = **43 runs × 8 elements = 344**
(`element_ppm_by_mass`: Gd, La, Mn, Sc, Ti, V, Yb, Zr). All 464 currently carry
admission status **pending** — keep diagnostic prediction coverage separate from
headline eligibility. Wang 2001 to acquire (out of scope).

**r2 executable acceptance scope (first landing):** Hashimoto 120 + Sossi **Mn and
Ti cells only** (≤86 of 344). The other six Sossi elements (258 cells) are typed
refusals until channels exist — never claimed as predictions, never silently
“retained”. See §6 and **"Dropped or changed from r1"**.

---

## 0. Decision summary (one screen)

| # | Decision | Choice |
|---|---|---|
| D1 | Call path | **Smaller kernel producer**, not the hero hourly tick on a full crucible fixture |
| D2 | Vapour pressures | Same engine channels as vapour rail (`internal-analytical`, `openimcc`, …) via equilibrate / IA core route; then HKL |
| D3 | Flux kernel | `engines.builtin.evaporation_flux.BuiltinEvaporationFluxProvider` (authoritative `EVAPORATION_FLUX`) with vacuum BCs; `simulator.chemistry.langmuir_knudsen.langmuir_molar_flux` as the diagnostic twin |
| D4 | Time integration | Integrate over **printed experiment run time** with adaptive sub-steps; re-evaluate activities **and** engine-consistent pO₂ each step; refine until per-oxide residue change < acceptance ε |
| D5 | Identity | Extend `RESIDUE_COMPONENT_COMPOSITION` profile: require starting `composition` + `exposure.duration_s` + mass / P / oxygen / area (or declared area policy) — engine-side change (regolith-main). Match physical runs by **`experiment_id`** |
| D6 | α | Per-species from catalog when measured; Hashimoto: **declared common-unity sensitivity arm** (αᵢ=αⱼ=1 as a sensitivity choice, **not** author-established absolute α); never silent; never fit α or area to residues |
| D7 | Scoring metric | Hashimoto: signed absolute wt% residual per oxide per run; Sossi: **dex** `log10(c_pred/c_obs)` as primary (positive concentrations), absolute ppm for domain/near-zero; store chosen operation **in the residual record** |
| D8 | Oxygen (r2) | **Engine-consistent, α-weighted congruent-evaporation oxygen balance** at every evolving-composition step, **per engine and per α arm**. Do **not** borrow OpenIMCC pO₂ for IA. Buffered (Sossi) keeps printed oxygen condition and accounts for reservoir exchange; chamber P ≠ surface equilibrium pO₂ |
| D9 | Geometry (r2) | Declare **initial area AND evolution rule**; carry density source + geometry in every residual; publish sensitivity band (constant sphere / constant disk / shrinking sphere) beside each prediction |
| D10 | Sossi scope (r2) | First landing = Hashimoto + Sossi Mn/Ti only; unsupported cells → typed refusal (`OUTSIDE_SUPPORTED_SPECIES` / `channel_missing`), never “retained” |

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

**Kernel reuse is still sufficient** for vacuum laboratory predictions when the
contracts in §1.5 hold: omitting campaign controller, equipment sizing, and
condensation train is appropriate at negligible backpressure (downstream duct
capacity and sweep-gas transport need not throttle the surface flux).

### 1.2 Accept: smaller kernel producer (mirror landed IA battery pattern)

On green `191960ce8`, internal-analytical is a **landed** battery engine. Residue
extends the vapour-rail pattern by one kinetic step:

```
identity (start composition, T, t_run, P, fO2/oxygen, mass, area+evolution, α map)
        │
        ▼
engine vapour pressures + activities   ← same channels as vapour rail
(internal-analytical / openimcc / …)     (score.py predict_with_engine arm)
        │
        ▼
ENGINE-CONSISTENT α-weighted oxygen balance   ← NEW vs r1 (§2.4)
  (per engine, per α arm, every composition step)
        │
        ▼
BuiltinEvaporationFluxProvider.dispatch  ← EVAPORATION_FLUX
  controls: vapour_batch_flux_pressures_Pa,
            melt_surface_area_m2, alpha, overhead_partials_Pa≈0 (vacuum),
            available_oxide_kg, T
        │
        ▼
time integrator (mol-native inventory; analytic parent depletion over t_run)
        │
        ▼
project residue to observation subtype (oxide wt% or element ppm)
  + geometry sensitivity band + oxygen/α/area provenance
        │
        ▼
MetricOperation.ABSOLUTE (Hashimoto) / DEX (Sossi primary; see §6)
```

**Concrete symbols (green tip `191960ce8`):**

| Role | Symbol |
|---|---|
| Battery refuse today | `predict_with_engine` early return for `Quantity.RESIDUE_COMPONENT_COMPOSITION` (`score.py` ~L2854) |
| IA vapour arm (landed) | battery IA path + `diagnostic_helpers/binary_pot_battery.py` (`equilibrate_cell`, `vapor_pressures_Pa`, …) |
| Oxygen-balance notices | `simulator/battery/oxygen_balance.py` (`OXYGEN_BALANCE_EFFUSION_ENGINES`, `fo2_oxygen_balance_effusion_solved:`) — residue must solve **per engine**, not reuse another engine’s root |
| Authoritative flux | `engines.builtin.evaporation_flux.BuiltinEvaporationFluxProvider` |
| Melt update twin | analytic depletion helpers in `simulator/evaporation.py` — **reuse math, not the hero caller**. `_update_melt_composition` (~L4490) only **projects** the ledger; it does **not** perform depletion. Never subtract gas kilograms directly from oxide kilograms |
| Diagnostic flux | `simulator.chemistry.langmuir_knudsen.langmuir_molar_flux` |
| Rail / metric already wired | `Rail.RESIDUE_COMPOSITION`; `MetricOperation.ABSOLUTE` (`score.py`) |

### 1.3 Minimum state

| Field | Required? | Source on Hashimoto / Sossi |
|---|---|---|
| Temperature_K | yes | identity / Table 2 |
| Starting composition (oxides or host+trace) | yes | Hashimoto Table 1 / `point_conditions.starting_composition`; Sossi FCMAS **host inventory** + `starting_component_ppm` on a **declared chemical basis** |
| Run duration | yes | Hashimoto `point_conditions.time_min` (Table 2); Sossi `time_min` |
| Sample mass | yes for extensive rates | Hashimoto: **printed 100.0 mg** → `0.0001` kg; Sossi: **~25 mg** on declared chemical basis |
| Melt surface area + evolution rule | yes for HKL | Hashimoto: **not tabulated** — declare policy (§3 / Addendum b); carry in every residual |
| Total pressure / vacuum | yes | Hashimoto ~1×10⁻⁴ Torr → ~0.0133 Pa; Sossi 101325 Pa (**≠** surface equilibrium pO₂) |
| Oxygen condition | yes when experiment declares a buffer / fO2; typed refusal when required-and-missing; vacuum unbuffered → solve congruent balance (§2.4) | Hashimoto `fO2_control=none` + vacuum prose; Sossi `fO2_log` printed |
| α by species | yes (measured or flagged assumption) | §4 |
| Engine id + datapack / liquid-row provenance | yes on every residual notice | Addendum a |
| Density source (when area from mass+ρ) | yes when sphere / volume policies used | Addendum b — labelled fallback ρ=2700 kg/m³ is a **named** assumption, not a silent constant |

### 1.4 Time integration and refinement

**Do not** use the hero’s fixed 1 h campaign ticks for lab runs that last 1.29–129 min
(Hashimoto) or 15–930 min (Sossi).

**Algorithm (r2):**

1. Set `t_end = exposure.duration_s` from the observation (printed).  
2. Start with `N` equal sub-steps, `dt = t_end / N`. Choose `N0` so that
   `N0 ≤ N_cap` always: e.g. `N0 = min(N_cap, max(8, ceil(t_end / 60)))` with
   **`N_cap = 256`** (or raise cap explicitly and report wall time). r1’s
   `N0 = max(8, ceil(t_end/60))` already exceeds 256 for 720- and 930-minute
   Sossi runs — that contradiction is resolved here by the min-with-cap rule;
   long runs that hit the cap emit `residue_time_refinement_unconverged` and
   still publish the best prediction (predict-and-flag) with measured wall time.  
3. Each step: re-query vapour pressures at current melt composition; **solve
   engine-consistent α-weighted oxygen balance** (§2.4); call EVAPORATION_FLUX;
   apply analytic **parent** depletion for `dt` (mol-native; reuse
   `_apply_analytic_evaporation_depletion` math with `dt_hr = dt/3600`); update
   oxide inventory / normalize for residue projection.  
4. **Refinement check:** re-run with `2N` steps; accept when
   `max_i |w_i(N) − w_i(2N)| < ε` with `ε = 0.05` wt% absolute for
   `oxide_wt_percent`, or an equivalent dex/ppm ε for `element_ppm_by_mass`.  
   Sol probe (sphere, OpenIMCC, α≡1): max component changes 0.1271 (16→32),
   0.0561 (32→64), 0.0262 (64→128) wt%; ~59 s at 128 steps — group by physical
   run and report execution time + convergence status.  
5. Cap `N` at `N_cap` and emit notice `residue_time_refinement_unconverged` if the
   cap hits — still publish the best prediction with the flag.

Isothermal hold at the observation’s `temperature_K`. Non-isothermal schedules are
out of v1 unless `exposure.schedule` is populated.

**One integrator** for both engines with identical duration, geometry, and α
assumptions. Record actual liquid rows, source, temperature interval, pack digest,
code revision, and oxygen model. Attach provenance according to **rows consumed**,
including IA paths that consume OpenIMCC-derived thermodynamic terms.

### 1.5 Kernel-reuse contracts (pressure, stoichiometry, oxygen)

The smaller producer must retain:

1. **Effective vapour-pressure selection** contract used to drive flux (same as
   vapour rail for that engine).  
2. **Gas-species-to-parent-oxide stoichiometry**, including oxygen coproducts
   (HKL coefficient, molecular mass, α, backpressure enter the oxygen atom
   balance).  
3. **Oxygen / redox state evolution** — or an explicitly bounded fixed-valence
   approximation with a named limitation notice. Unbuffered vacuum → congruent
   balance (§2.4); buffered → printed condition + reservoir exchange.  
4. **Mol-native inventory** and atom-conservation checks. Subtracting gas
   kilograms from oxide kilograms is a hard failure (chunk R1b mutation).

**Stack2 need not block** a diagnostic landing on green. A coherent fixed-FeO
approximation can predict with explicit limitations (including unmodelled
graphite interaction). The generic `stack2_pending` / `feo_melt_redox_stack2_pending`
flag **cannot excuse an unclosed oxygen balance**. Apply **b-648** only when
alkali channels actually contribute; a wholly zero alkali channel must not
manufacture dependence.

---

## 2. Free-evaporation boundary conditions

### 2.1 Vacuum (Hashimoto 1983)

Printed on green extracts (`point_conditions` on all 120 Table 3 rows):

| BC | Printed value | Notes |
|---|---|---|
| Atmosphere | “No buffer gas or gas mix during the vacuum runs. Graphite crucible; …” | Vacuum free evaporation; **not** Knudsen cell |
| `fO2_control` | `"none"` | No gas buffer; paper notes high fO2 to keep Fe ferric cannot be sustained |
| Chamber vacuum | ~1×10⁻⁴ Torr (~0.0133 Pa) | Also T-dependent “pressure on throw” prose — use chamber vacuum as total_P; flag throw-pressure as diagnostic only. **Chamber P ≠ surface equilibrium pO₂** |
| Regime | `langmuir_free_evaporation` | v1 extract metadata |
| Knudsen number (illustrative) | ~755 at 2073 K, L=4 mm | Ballistic escape supported |

**Provider BC:** `overhead_partials_Pa` ≈ 0 for all evaporating species (true free
surface; `p_bulk → 0`). Do **not** raise headspace pressure to “make kinetics fit”.
Cross-ref **d-061** only as: recipe lance throttle keeps **commanded** headspace
pressure; free-evap residue BC is the opposite limit (vacuum), flagged
`bc_free_evaporation_vacuum`.

### 2.2 Open furnace / buffered gas (Sossi 2019)

| BC | Printed | Notes |
|---|---|---|
| `total_pressure_Pa` | 101325 | Open furnace; Kn ~ 1×10⁻⁴ (illustrative hard-sphere at 1773 K, σ=3.7 Å, L=4 mm) |
| `fO2_log` | per run | **Required** identity / control input; **retain printed oxygen condition** |
| `atmosphere` | `unknown` / `not_published` | Do not invent gas mix |
| `time_min` | 15 / 60 / 120 / 720 / 930 | Printed |
| Sample mass | ~25 mg | On declared chemical basis with FCMAS host |
| Host inventory | FCMAS major-element seed | Trace ppm alone is **not** a complete melt composition |

At one atmosphere, zero volatile bulk pressure does **not** eliminate gas-film
resistance. Sossi’s experiments were conducted in controlled-fO₂ gas-mixing
furnaces. A **Langmuir-limit column** (`r_gas→0`) may ship only as a **flagged
diagnostic** (`bc_open_furnace_langmuir_limit_diagnostic`); it cannot establish
like-for-like furnace kinetics by itself. First landing that claims like-for-like
Sossi kinetics must either (a) include an explicit gas-film transport resistance
model with named assumptions, or (b) keep Sossi Mn/Ti under the diagnostic flag
and score Hashimoto as the primary acceptance cohort.

**Buffered oxygen:** keep the printed `fO2_log` / fO₂ condition; account for
oxygen **exchanged with the external reservoir** in the inventory (atoms in/out
of the melt balance against the buffer). Do not replace the printed condition with
a congruent-evaporation solve.

### 2.3 Typed refusals (oxygen / BC)

| Condition | Refusal |
|---|---|
| Experiment declares buffer / `fO2_log` / `fO2_Pa` but identity lacks oxygen | `ExecutionState.NOT_PROBED`, `RefusalReason.IDENTITY_INCOMPLETE`, reason `oxygen_condition_missing` |
| Vacuum run with contradictory commanded high pO2 | `INVALID_IDENTITY` / `bc_conflict_vacuum_vs_buffered` |
| Missing `total_pressure_Pa` when profile requires it | `IDENTITY_INCOMPLETE` |
| Missing melt area when HKL needs it and no declared area policy | `IDENTITY_INCOMPLETE`, reason `melt_surface_area_missing` |
| Species / parent with no evaporation channel | `OUTSIDE_SUPPORTED_SPECIES` / `channel_missing` — **never** treat as complete retention |

Hashimoto’s `fO2_control=none` is a **declared** oxygen condition (unbuffered
vacuum), not a missing one — do not refuse; flag `oxygen_unbuffered_vacuum` and
solve §2.4.

### 2.4 P1 — Engine-consistent α-weighted congruent-evaporation oxygen balance

Re-evaluating activities as composition changes is correct. Re-evaluating **pO₂**
is also necessary. For congruent loss from fixed-valence oxide parents, the
outgoing oxygen atoms must equal the oxygen removed with those parents. That
balance must include each channel’s HKL coefficient, molecular mass, α, and
backpressure.

**Do not borrow OpenIMCC’s pO₂ for IA.** Sol probe at 2073.15 K on Hashimoto’s
start: OpenIMCC solves pO₂ = 0.79930 Pa; at that borrowed condition IA’s required
oxygen coproduct exceeds the borrowed OpenIMCC O₂+O outflow by **41.5%** (29.3%
shortage relative to demand). α also changes the oxygen solution: metal-channel
α 1→0.25 (oxygen-channel α=1) moves pO₂ 0.799→0.289 Pa and Fe equilibrium pressure
2.177→3.618 Pa under OpenIMCC pressure laws.

**Required (r2):**

1. At every evolving-composition step, solve the **engine-consistent**,
   **α-weighted** congruent-evaporation oxygen balance using **that engine’s**
   pressure laws / activities.  
2. Solve **separately per α arm** (common-unity sensitivity arm vs production-α
   arm) — α enters the balance.  
3. Buffered (Sossi) runs: **do not** run this congruent solve as the equality;
   keep printed oxygen condition + reservoir exchange (§2.2).  
4. Keep chamber total pressure and surface equilibrium pO₂ as **distinct** fields
   on every residual.  
5. Mutation (chunk R1a): borrowing the other engine’s oxygen root → failure.

---

## 3. Geometry (P2) — initial area, evolution, sensitivity band

Sol probe at 2073.15 K × 10 min, ρ=2700 kg/m³ labelled fallback, 100 mg,
α≡1, OpenIMCC composition+oxygen re-eval, builtin HKL, analytic parent depletion
(64 steps) — **sensitivity predictions, not validated experimental predictions**:

| Assumption | FeO residue, wt% | Mass loss, wt% |
|---|---:|---:|
| Constant full sphere area (d≈4.136 mm → A≈53.733 mm²) | 0.467 | 53.103 |
| Constant 4 mm disk area (A≈12.566 mm²) | 24.446 | 18.256 |
| Sphere area shrinking with remaining volume | 2.593 | 47.462 |

Area choice changes FeO by **~24 percentage points**. The disk is not a
demonstrated lower bound without a wetting/contact geometry argument. Neither
cold preform dimensions nor the ρ fallback establish the actual molten area.

**Required (r2):**

1. Declare **initial area** (policy id) **and** whether area is **constant** or
   **evolves** (e.g. shrink-with-volume sphere).  
2. Carry `density_source`, `geometry_policy_id`, `area_m2_initial`, and
   `area_evolution` into **every** residual notice.  
3. Report a **sensitivity band** alongside each prediction (at minimum: constant
   sphere / constant disk / shrinking sphere for Hashimoto FeO-bearing runs).  
4. Fix area (and α) **before** scoring; never fit them to residue observations
   (circular validation).  
5. Exposure equality already requires area (`identity.py`); unknown area →
   `IDENTITY_UNKNOWN`; area marked N/A → `INVALID_IDENTITY`. A declared policy
   that fills `exposure.area_m2` with a flagged assumed value is the implementable
   path.

Until an owner picks a single primary policy, ship the **band** as the
prediction surface and nominate one column as the author-facing default with an
explicit flag (open question §9).

---

## 4. Evaporation coefficients

### 4.1 Engine policy (already on green)

`BuiltinEvaporationFluxProvider` fails loud on missing α (`missing_evaporation_alpha`,
SC-67) unless `allow_unmeasured_alpha_fallback` is explicitly gated. VapoRock-fit
provenance is refused at runtime. Broad-proxy rows are stripped from the
measured-α control view.

### 4.2 Per-species plan

| Species family | Source on green | Residue-predictor stance |
|---|---|---|
| Fe (melt) | Fedkin/Hashimoto Langmuir class evidence | Prefer **class / catalog α** when treated as measured intrinsic; else flagged |
| Mg | Fedkin Mg Hashimoto ~0.25 class; Richter Mg 0.04 (different system) | Same — do not silently mix Richter CAI γ into FCMAS |
| SiO | Hashimoto Langmuir series 0.12–0.21; class prior 0.04 | Use class / pin with notice naming the observation_id |
| Ca, Al channels | Generally unmeasured intrinsic melt α on green | **Flagged assumption** |
| Mn, Ti (Sossi) | Parent channels exist in builtin flux metadata (`MnO` / `TiO2` family) | Production / catalog α with provenance; first-landing Sossi cells |
| Gd, La, Sc, V, Yb, Zr | **No evaporation channel on green** | Typed `channel_missing` — see §6.1 |

### 4.3 Hashimoto α — corrected provenance (P1)

The extract records **`assumed_unity_ratio_not_measured_alpha`**: Hashimoto assumes
**αᵢ/αⱼ = 1**, not absolute αᵢ = 1. Equal ratios leave an unknown common rate
multiplier; they cannot determine depletion over a printed duration.

r1’s “author-comparable α≡1” arm is **renamed** in r2:

- **Name:** `alpha_common_unity_sensitivity` (declared common-unity sensitivity arm).  
- **Provenance notice:** cite extract obs
  `…assumed_unity_ratio…` / eqn (16); state explicitly that absolute α=1 is a
  **sensitivity choice**, not an author-established absolute kinetic model.  
- **Flag keys:** `alpha_assumed_common_unity_sensitivity=true`,
  `alpha_source=declared_common_unity_sensitivity`,
  `extract_assumption=assumed_unity_ratio_not_measured_alpha`.  
- **Do not** claim this arm “matches Hashimoto’s Stage modelling assumption” as
  an absolute-α endorsement.

**Rule:** every α that is not a measured intrinsic row carries
`alpha_source`, `alpha_value`, and `alpha_assumed=true`. Catalog class centrals
used outside their evidence system are also `alpha_assumed=true` (system
mismatch). **Never fit α or area to these residue observations.**

### 4.4 Scorer arms

1. **Common-unity sensitivity arm (Hashimoto):** αᵢ=αⱼ=1 as declared sensitivity
   (§4.3) — diagnoses vapour-pressure / activity / area / oxygen under a named
   rate multiplier of unity.  
2. **Simulator-production arm:** catalog / class α with SC-67 refusals —
   diagnoses production kinetics as the plant would run.

v1 acceptance for t-1082 lands arm (1) for Hashimoto 120 as the primary column
and arm (2) as a **required parallel column** once R4 lands (R6 resolves r1’s
“required parallel” vs “optional” contradiction: **required after R4**, not
optional). Sossi Mn/Ti uses arm (2) unless an author α is printed (Sossi Na
adopted unity is alkali-specific evidence, not a blanket for Mn/Ti/Gd/…).

---

## 5. Identity profile for `RESIDUE_COMPONENT_COMPOSITION`

### 5.1 Today (green `191960ce8`)

`simulator/battery/identity.py` still requires only `temperature_K` + `subtype`
for `RESIDUE_COMPONENT_COMPOSITION`. Axes neither required nor
`permitted_not_applicable` are **forbidden-as-value**. Consequence: starting
composition is stripped from equality even though migrator stores rich
`point_conditions`.

**Implementation contract (r2):** `profile_for(identity)` **cannot** inspect
`point_conditions` or an experiment object. Any conditional oxygen requirement
must be expressed as an **explicit profile / builder contract** (e.g. migrator
or identity-builder sets `fO2_Pa` to a value or to `not_applicable` with reason
**before** `profile_for` runs). Do not propose “if point_conditions has fO2_log
then require fO2_Pa” inside `profile_for`.

Exposure equality already requires area: probes returned `IDENTITY_UNKNOWN` for
unknown area and `INVALID_IDENTITY` for area marked not applicable. Live
Hashimoto species phases are often unresolved — an otherwise identical
Hashimoto-shaped identity can still return `IDENTITY_UNKNOWN` on
`species.phase`; handle phase resolution in the identity build, not by relaxing
equality silently.

### 5.2 Proposed profile (exact fields)

```text
required:
  temperature_K
  subtype                          # oxide_wt_percent | element_ppm_by_mass | …
  composition                      # STARTING melt / charge composition (host+trace)
  exposure                         # Exposure.duration_s = run time; area_m2 when known
                                   #   or filled by declared area policy (§3)
  total_pressure_Pa
  sample_mass_kg                   # extensive inventory for integration

oxygen (set by identity builder — not by profile_for introspection):
  buffered / fO2-printed experiments:
      require fO2_Pa  (migrate fO2_log → Pa in builder)
  unbuffered vacuum (Hashimoto):
      permit fO2_Pa as not_applicable with reason oxygen_unbuffered_vacuum
      (congruent balance supplies surface pO2 — distinct from chamber P)

permitted_not_applicable:
  per, standard_pressure_Pa, reaction, formation_elements, reference_state,
  reservoir, sweep_gas, wall
  # fO2_Pa only when unbuffered vacuum declared

forbidden-as-value: (none beyond the usual closed set)
```

`composition` is the **starting** composition (Table 1 / FCMAS host + starting
ppm), not the measured residue. Residue values stay on `observation.value`.

### 5.3 How residue rows are matched

Use existing **`experiment_id`** for physical-run matching. At 2073.15 K, three
distinct ~16.7-minute Hashimoto charges share starting composition and
conditions — **preserve all three observed vectors**. An identical prediction
may be reused, but residual rows remain attached to their **observation_ids**.
Table 1 supplies the start; Table 3 supplies outcomes.

| Axis | Hashimoto | Sossi |
|---|---|---|
| `source_id` | `kems-015-hashimoto-1983` | `kems-012-sossi-2019` |
| Physical run | `experiment_id` (+ Table 3 `row=` token in `observation_id` as locator) | `experiment_id` / run locator |
| `temperature_K` | identity | identity |
| `species.formula` | oxide (`FeO`,`MgO`,`SiO2`,`CaO`,`Al2O3`) | element (`Mn`,`Ti`, …) |
| `subtype` | `oxide_wt_percent` | `element_ppm_by_mass` |
| Starting composition | `identity.composition` equals Table 1 Ave. | FCMAS host + `starting_component_ppm` |
| Run time | `exposure.duration_s` | same |
| Mass | 100.0 mg printed | ~25 mg declared basis |

One integrator call per **(experiment_id / physical run, T)** yields a full
residue vector; then project each species cell. Do not re-integrate per oxide
row independently.

**Admission vs diagnostic coverage:** all 464 live residue records are currently
`admission.status: pending`. Diagnostic prediction may cover the pending cohort;
headline eligibility / “admitted” counts stay separate. Do not claim “464
admitted”.

**VF (vaporized fraction):** **None** of the 120 live Hashimoto rows carries
`VF_wt_pct` in `point_conditions`. VF needs a **source-grounded join** (Table 3
locator / observation sibling), not a read from `point_conditions`. When both
predicted mass-loss fraction and a joined VF exist, emit residual notice
`vf_wt_pct_residual`; otherwise omit without inventing.

---

## 6. Like-for-like scoring plan and residual metric

### 6.1 Cohort and honest coverage

| Cohort | N live | Executable on green first landing | Subtype |
|---|---:|---:|---|
| Hashimoto Table 3 | 120 (24×5) | **120** | `oxide_wt_percent` |
| Sossi Mn + Ti | 86 (43×2) | **≤86** (channels exist) | `element_ppm_by_mass` |
| Sossi Gd, La, Sc, V, Yb, Zr | 258 (43×6) | **0** — typed `channel_missing` | `element_ppm_by_mass` |
| **Total live** | **464** | **≤206 predict**; **258 refuse honestly** | |

Do **not** claim all 464 predict. Unsupported cells: typed refusal, **never**
“retained”.

**What the other six Sossi elements need (list, not invent):**

1. Parent-oxide (or element) **evaporation channels** in builtin /
   engine metadata (gas species, HKL stoichiometry, molecular mass, α policy).  
2. Engine vapour-pressure / activity path for those parents in FCMAS(+trace)
   melts (IA and/or OpenIMCC pack rows).  
3. Same FCMAS host inventory + ~25 mg mass + buffered oxygen + transport
   contract as Mn/Ti.  
4. Acceptance fixtures and residual metric (dex) pinned to store revision.  
5. Explicit decision whether Langmuir-limit remains diagnostic-only once
   channels exist, or a gas-film model lands first.

### 6.2 Residual metric

| Subtype | Primary residual (stored on residual record) | Secondary |
|---|---|---|
| `oxide_wt_percent` | signed absolute `r_i = w_i,pred − w_i,obs` (wt%) | Optional dex on mole fraction — diagnostic only |
| `element_ppm_by_mass` | **dex** `log10(c_pred/c_obs)` when c>0 | Absolute ppm for domain/near-zero |

Store the chosen `MetricOperation` / operation id **in the actual residual
record**, not only a presentation column.

**Per run time:** group residuals by `(source_id, experiment_id, T, t_run)` so a
16.7 min charge is not averaged with a 129 min charge. Publish:

- per-oxide (or per-element) residual  
- vector L2 / max-norm per run  
- geometry sensitivity band (§3)  
- VF residual when a source-grounded VF join exists  
- oxygen model id, α arm id, density/geometry provenance  
- execution time + refinement convergence status  

OpenIMCC column only where the pack can form the vapour channels for that parent;
otherwise typed channel omit with liquid-row provenance still recorded for the
parents that did run.

### 6.3 No second model

OpenIMCC “residue” means: integrate **OpenIMCC vapour pressures** through the
**same** BuiltinEvaporationFluxProvider + integrator (with OpenIMCC’s own oxygen
balance). Same for IA with **IA’s** oxygen balance. No separate residue API.

---

## 7. Physics caveats (carry as flags)

### 7.1 Internal-analytical alkali γ constant @ 1500 K (b-648)

Attach `flag: ia_alkali_gamma_constant_1500K` / `ticket: b-648` **only when alkali
channels actually contribute** to the vapour batch / oxygen balance. A wholly
zero alkali channel must not manufacture b-648 dependence. Hashimoto FCMAS majors
are not alkali-buffered — omit the flag unless alkalis appear in the batch map.

### 7.2 FeO-bearing melts 1973–2273 K — M5 / M2 / M4 (stack2)

Mandatory notice on every Hashimoto (and any FeO-bearing) residual:

`flag: feo_melt_redox_stack2_pending`  
`regimes_named: M5,M2,M4`  
`temperature_window_K: [1973, 2273]`  
`engine_tip: 191960ce8` (green)

Predict and flag. **Does not excuse** skipping §2.4 oxygen closure. When stack2
lands, re-pin store revision and re-score.

### 7.3 OpenIMCC liquid-row extrapolation (Addendum a)

Al₂O₃(l) / CaO(l) / MgO(l) LAM1987 rows are below their fit/melting floors at
Hashimoto T — flag `openimcc_liquid_row_extrapolated` with row ids from Addendum
a. Provenance on every notice keeps scores comparable. Stated LAM1987 ranges
match the installed condensate table on green.

### 7.4 Sossi transport

Flag `bc_open_furnace_langmuir_limit_diagnostic` on any Langmuir-limit Sossi
column. Chamber P = 101325 Pa ≠ surface equilibrium pO₂.

---

## 8. Luna-sized chunks (acceptance + runnable mutations)

Coherent sequence from sol review §§5–7. Split r1’s large integration chunk.
Avoid new adapters or configuration layers when existing helpers suffice.
Schema-key presence tests need **value** checks. R1’s old zero-pressure mutation
**cannot** detect an α bug (flux remains zero either way) — replaced below.

| Chunk | Acceptance and mutation that must fail |
|---|---|
| **R0: identity and joins** | Exact starts, durations, masses, and run IDs (`experiment_id`); corrupt start or merge replicates → failure. VF join is source-grounded or absent — inventing VF from `point_conditions` → failure |
| **R1a: vacuum oxygen** | Engine-consistent α-weighted oxygen closure at each step; borrow the other engine’s root → failure. α arm change without re-solving pO₂ → failure |
| **R1b: inventory integration** | External HKL/stoichiometric anchors and atom closure; subtract gas mass as oxide mass → failure. Mol-native parent depletion required |
| **R2: Hashimoto assumptions** | 24 complete vectors (120 cells), explicit α/area/geometry policy + sensitivity band; strip or alter provenance → failure; fit α/area to residues → failure |
| **R3: refinement** | Re-evaluate activities **and** pO₂; freeze either during significant depletion → failure. Report wall time; N₀ respects N_cap |
| **R4: scoring** | Correct metrics (wt% abs / dex), eligibility vs diagnostic coverage, consumed-row provenance; change operation or row identity → failure |
| **R5: Sossi physics** | Explicit trace support (Mn/Ti first), FCMAS host + mass, buffered boundary + reservoir exchange; remove oxygen or a required channel → **typed** failure (not retention). Langmuir column flagged diagnostic-only |
| **R6: production α** | Required parallel column after R4 (not optional); disagreements flagged, not averaged. Resolve r1 contradiction |

**Synthetic kernel smoke (inside R1b):** α=0 all species → residue = start within
1e-12 relative; α>0 and p>0 → mass leaves; α>0 and p≡0 → no change; **α≡0 with
p>0 → no change** (α must be on a path that can fail when wrongly forced non-zero
with finite p — do not use p≡0 as the sole α mutation).

---

## Addendum (a) — OpenIMCC liquid-row provenance in residual notices

Pending owner **d-060**. Until that lands, every OpenIMCC residue residual notice
**must** record exactly which condensate/liquid rows fed the gas channels.

**Default rows as shipped** (`openimcc/data/gas/condensate.csv`, `LAM1987` /
`lam1987_transcribed`):

| Parent oxide | Liquid / condensate row id | Fit T range (K) | Source tag |
|---|---|---|---|
| MgO | `MgO(l)` | 3100–3500 | `LAM1987` |
| CaO | `CaO(l)` | 2900–3800 | `LAM1987` |
| Al2O3 | `Al2O3(l)` | 2327–3000 | `LAM1987` |
| SiO2 | `SiO2(l)` | 1996–3000 | `LAM1987` |

Hashimoto window 1973–2273 K lies **below** MgO(l)/CaO(l)/Al2O3(l) floors.

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
pack_digest: <digest>
code_revision: <git sha>
oxygen_model: <engine-consistent congruent | buffered printed>
d060_pending: true
```

When d-060 switches defaults, scores remain comparable by grouping on
`liquid_rows_used`. IA paths that consume OpenIMCC-derived thermodynamic terms
must still list **rows consumed**.

---

## Addendum (b) — Hashimoto printed vs assumed kinetic inputs

Census on green extracts. **No numbers invented.**

### Printed (use as-is)

| Input | Printed | Where |
|---|---|---|
| Starting composition (wt%) | SiO₂ 35.43, Al₂O₃ 3.16, FeO 35.04, MgO 23.84, CaO 2.53 | Table 1 Ave.; `point_conditions.starting_composition` on all 120 |
| Temperature | 1700–2000 °C → 1973.15–2273.15 K | Table 2 / identity |
| Run time | 1.29 … 129 min | Table 2 → `point_conditions.time_min` |
| Sample mass | **100.0 mg** (0.0001 kg) | experiments[].sample.mass_kg |
| Preform geometry (cold) | height 4.0 mm, diameter 4.0 mm, hole 1.2 mm | metadata `starting_preform_mm` — **not** molten free-surface area |
| Vacuum level | ~1×10⁻⁴ Torr | total_pressure_Pa / prose |
| fO2 control | `none`; graphite crucible; no buffer gas | point_conditions |
| Residue oxides | Table 3 measured rows | observation values |
| Author α modelling | αᵢ/αⱼ assumed unity (eqn 16); extract `assumed_unity_ratio_not_measured_alpha` | obs + PDF |

### Not tabulated / must be assumed (each → flagged; never silent; never fit to residues)

| Input | Status on green | Proposed assumption | Flag |
|---|---|---|---|
| **Molten free-surface area + evolution** | `sample_surface_area_status: not_tabulated` | Declare policy + evolution (§3); ship sensitivity band (constant sphere / constant disk / shrinking sphere) | `melt_area_assumed_<policy_id>`; `area_evolution=<rule>`; density source named |
| Melt density (if sphere / volume policy) | not printed | Named reference (labelled fallback ρ=2700 kg/m³ is one option) | `melt_density_assumed` + citation/label |
| Absolute α | author assumes **ratio** unity only | Common-unity **sensitivity** arm (§4.3); production arm separate | `alpha_assumed_common_unity_sensitivity` |
| Oxygen fugacity number | control=`none` | Congruent engine-consistent solve (§2.4); do not invent IW±X | `oxygen_unbuffered_vacuum` |
| Transient “pressure on throw” | about-nominal by T | Not used as total_P driver | `pressure_on_throw_ignored_for_bc` |
| VF_wt_pct on identity | **not** in `point_conditions` on live 120 | Source-grounded join only | omit or `vf_joined_from_<locator>` |

**Explicitly not assumed silently:** run time, sample mass, starting composition, T.

---

## 9. Out of scope / non-goals

- Redesigning melt-redox M0–M6 or implementing stack2 on green.  
- Acquiring Wang 2001 (track only).  
- Hero campaign optimization / cold-train sizing for residue scores.  
- Inventing equipment FKs, extract rows, or VF values.  
- Claiming predictions for Sossi Gd/La/Sc/V/Yb/Zr before channels exist.  
- Fitting α or area to residue observations.  
- Mac listen pools / overnight CI beyond this design delivery.  
- Borrowing OpenIMCC pO₂ for IA (or any cross-engine oxygen root).

---

## 10. Open questions for physics / main

1. **Hashimoto primary geometry column** within the required sensitivity band
   (constant sphere vs disk vs shrinking sphere) — owner pick for author-facing
   default; band still always published.  
2. **d-060** liquid-row defaults for Al/Ca/Mg below ~2300 K.  
3. **VF** scored vs diagnostic-only once a source-grounded join exists.  
4. Identity profile / builder sequencing with regolith-main (engine owns profile;
   builder must pre-resolve oxygen + area policy before `profile_for`).  
5. **Sossi gas-film model** timeline vs keeping Langmuir as diagnostic-only for
   Mn/Ti first landing.  
6. Whether production-α arm (R6) shares the same geometry band or freezes the
   primary geometry column only.

---

## 11. References (tips / files)

| Item | Ref |
|---|---|
| Green tip (r2 base) | `191960ce8a657a25681aad624dd64670bf85df6a` |
| r1 DESIGN | `DESIGN-residue-predictor-regolith-physics-2026-10-02.md` |
| Sol review | `regolith-physics-residue-2026-10-02/sol-review-of-residue-r1.md` |
| REQ r2 | `REQ-residue-predictor-r2-from-regolith-physics-2026-10-02.md` |
| d-061 (BC flag only) | `RULING-lance-throttle-from-regolith-physics-2026-10-02.md` |
| Identity | `simulator/battery/identity.py` |
| Score refuse | `simulator/battery/score.py` |
| Oxygen-balance notices | `simulator/battery/oxygen_balance.py` |
| Flux provider | `engines/builtin/evaporation_flux.py` |
| Hero tick (rejected outer loop) | `simulator/core.py` / `simulator/evaporation.py` |
| Hashimoto / Sossi extracts | `data/literature/extracts-v2/kems-015-…`, `kems-012-…` |
| OpenIMCC liquid rows | `openimcc/data/gas/condensate.csv` (`MgO(l)`, `CaO(l)`, `Al2O3(l)`, `SiO2(l)` / LAM1987) |

---

## Dropped or changed from r1

| r1 item | r2 disposition |
|---|---|
| Base tip `ad3ca2a4b` + pattern tip `76b550019` (IA wiring pending) | **Updated** to green `191960ce8` — IA is a landed battery engine |
| Borrow / share OpenIMCC pO₂ for IA vacuum oxygen (landed vapour-adapter pattern) | **Dropped** — solve engine-consistent α-weighted congruent balance per engine and per α arm at every step (§2.4) |
| Chamber pressure treated as sufficient oxygen story for vacuum | **Changed** — chamber P ≠ surface equilibrium pO₂; both recorded |
| “Hashimoto author-comparable α≡1” / `alpha_assumed_unity_hashimoto_eqn16` as author absolute-α | **Renamed/corrected** to declared **common-unity sensitivity** arm; provenance `assumed_unity_ratio_not_measured_alpha` (αᵢ/αⱼ=1, not α=1); never fit α/area to residues |
| Claim / plan that all 464 residue cells predict on this wiring | **Dropped** — first landing ≤206 (Hashimoto 120 + Sossi Mn/Ti ≤86); 258 cells typed `channel_missing`, never “retained” |
| Sossi melt = trace ppm seed only; Langmuir limit as v1 BC | **Changed** — require FCMAS host + ~25 mg declared basis; Langmuir-limit column = **flagged diagnostic only** at 1 atm (Kn~1e-4); buffered oxygen + reservoir exchange |
| “20 runs × 5 oxides” Hashimoto acceptance | **Corrected** to **24 × 5 = 120** |
| “464 admitted” wording | **Corrected** — live 464 are admission **pending**; diagnostic coverage ≠ headline eligibility |
| Conditional oxygen inside `profile_for` via `point_conditions` introspection | **Dropped** — explicit identity-builder contract; `profile_for` cannot inspect experiments |
| Match key without `experiment_id`; risk of merging replicate charges | **Changed** — match physical runs by `experiment_id`; preserve all observation_ids (e.g. three 16.7 min charges at 2073 K) |
| VF read from `point_conditions.VF_wt_pct` | **Dropped** — none of 120 live Hashimoto rows carry it there; source-grounded join only |
| Single area assumption without evolution rule or sensitivity band | **Changed** — declare initial area **and** evolution; carry density/geometry on every residual; publish sphere/disk/shrinking-sphere band (§3) |
| Kernel reuse without explicit pressure / stoichiometry / oxygen / mol-native contracts | **Tightened** (§1.5); `_update_melt_composition` is projection-only; gas-kg−oxide-kg forbidden |
| `stack2_pending` / b-648 as broad excuses | **Tightened** — stack2 flag cannot excuse unclosed oxygen; b-648 only when alkali channels contribute |
| Sossi primary metric “relative or dex” (undecided) | **Chose dex** as primary; store operation on residual record; abs ppm for near-zero |
| Chunks R0–R6 with single fat R1; zero-pressure α mutation; R2 “20×5”; R6 “optional”; N₀ vs 256 cap contradiction | **Replaced** by R0 / R1a / R1b / R2 / R3 / R4 / R5 / R6 table (§8); α mutation must use finite p; R6 required after R4; N₀ = min(N_cap, …) |
| Production-α column “optional after R4” | **Required** parallel column after R4 (R6) |
| Open questions included “Sossi metric: relative vs dex” | **Resolved** (dex); geometry primary column remains an open owner pick **within** the mandatory band |

Unchanged from r1 and retained: smaller kernel producer (D1); same vapour channels + BuiltinEvaporationFluxProvider; reject hero outer loop; d-061 as BC flag only; Addendum a LAM1987 liquid-row provenance; printed Hashimoto mass/times/Table 1/vacuum; no invent of extract data; no Mac listen pools; design-only delivery.

— regolith-empirical, seat `slot-y17`, 2026-10-02 ~11:35 ET
