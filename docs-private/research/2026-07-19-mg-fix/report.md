# t-081 + t-082 Mg extraction reconciliation

## TL;DR

- **t-081 verdict: real bug, already fixed in HEAD by `6d727259`.** Current non-FeO metal release reads SSO-R melt fO₂; no new functional retune was made.
- Current Mg quantity is the melt-supported equilibrium source pressure, then a separate surface/transport solve applies overhead metal backpressure.
- Pure-MgO congruent vaporization is a third, self-consistent gas boundary condition; its reaction-basis validation does not select overhead O₂ for a buffered melt.
- The stated historical t-081 inputs give 46.5× arithmetically; the factor is trajectory-dependent, not a universal C4 constant.
- **t-082 verdict: 1760/1659 °C are stale.** They extrapolated condensed Mg Antoine 570–670 K past certification and across the boiling transition.
- Current-runtime, Mg-metal-phase-correct targets are **1892 °C Moon / 1768 °C asteroid** (derived roots 1892.647/1768.703 °C) under the frozen activity/ambient-fO₂ premise.
- Two runner goldens moved under canonical regeneration; `corpus_version` was not changed. Everything is staged, not committed.

## 1. Reconciliation verdict

The two prior claims concern different boundary conditions:

1. **Melt dissociation-equilibrium source (the current Mg quantity).** The cleaned melt supports an equilibrium Mg fugacity set by MgO activity, JANAF Mg/MgO thermochemistry, and the melt's intrinsic oxygen chemical potential. The runtime passes overhead `pO2_bar` and `intrinsic_fO2_log` as independent channels. For every non-FeO metal, the provider uses `10**intrinsic_fO2_log` in the activity root. This is regime (b) from the brief.
2. **Surface/transport flux.** The equilibrium source pressure then enters the series-resistance flux as `P_eq - P_bulk`; the overhead metal partial pressure and gas-side resistance remain present. This is the surface metal-transport part of regime (a), and it was not removed or rewired here.
3. **Pure-MgO congruent vaporization validation.** A congruent calculation closes co-evolved gas stoichiometrically (`p_Mg=2 p_O2`) rather than imposing either a remote overhead setpoint or a buffered silicate-melt fO₂. Agreement at this boundary is a separate reaction-basis anchor. The runtime surface subtraction does not itself solve this Mg/O₂ co-evolution, and the anchor cannot establish that far-field overhead O₂ is the correct denominator for Mg release from a redox-buffered silicate melt.

The apparent contradiction came from the stale `data/setpoints.yaml` annotation saying congruent MgO vaporization “follows overhead pO2.” That statement outlived the functional correction in release commit `6d727259`. Current code was already on the correct side of t-081 before this worktree started; this change adds the complete derivation and removes the contradictory metadata rather than retuning coefficients or changing the source equation again.

## 2. t-081 equilibrium, sign, units, and magnitude

For one mole of MgO,

```text
MgO(l) -> Mg(g) + 1/2 O2
K1 = (f_Mg/p0) (fO2/p0)^1/2 / a_MgO
```

The canonical JANAF row is normalized per mole O₂:

```text
2 MgO(l) -> 2 Mg(g) + O2
K2 = K1^2 = (f_Mg/p0)^2 (fO2/p0) / a_MgO^2
f_Mg/p0 = sqrt(K2 a_MgO^2 / (fO2/p0))
```

`K2`, `a_MgO`, `fO2/p0`, and `f_Mg/p0` are dimensionless. The gas-standard rail multiplies the final ratio by `p0=1 bar`; it does not multiply by condensed Mg Antoine again. Therefore

```text
P_Mg proportional to a_MgO * fO2^(-1/2)
P_Mg,melt-basis / P_Mg,overhead-basis = sqrt(pO2_overhead / fO2_melt)
```

The sign is unambiguous: a lower melt fO₂ raises supported Mg pressure. For the inputs stated in the historical t-081 C4 note, `pO2_overhead=8e-5 bar` and `fO2_melt≈3.7e-8 bar`, giving

```text
sqrt(8e-5 / 3.7e-8) = 46.5
```

This arithmetic reproduces the claimed 40–50× underestimate for that frozen premise. It is not universal. The checked-in C4 default overhead is now `0.2 mbar = 2e-4 bar`, and SSO-R melt fO₂ depends on the first-liquid reference and prior redox trajectory. An executable 1350 °C first-liquid reference re-referenced to 1600 °C gives about 42.15×; a direct IW evaluation at 1600 °C gives about 134.10×. The bug and sign are real; a single factor without a frozen trajectory is not.

Code state and preservation checks:

- `simulator/core.py` dispatches independent transport pO₂ and intrinsic melt fO₂.
- `engines/builtin/vapor_pressure.py` and the legacy `simulator/equilibrium.py` use melt fO₂ for non-FeO metal source equilibrium.
- `tests/chemistry/test_po2_lever_single_application.py` proves non-FeO pressures are invariant to overhead pO₂ at fixed intrinsic fO₂ and retain the `-1/2` Mg slope.
- The evaporation layer still subtracts overhead species backpressure, so the surface flux path is intact.

## 3. t-082 Mg-metal-phase-correct thresholds

### Legacy reproduction and failure

The stale 1760/1659 °C values can be reproduced only by combining the superseded flat Mg Ellingham row with the condensed Mg Antoine sidecar:

```text
P_eff = sqrt(K2 a_MgO^2 / fO2) * P_sat,Antoine
solve P_eff = 1000 Pa
```

At `a_MgO=0.0926`, this gives 1758.268 °C (Moon) and 1658.010 °C (asteroid), explaining the rounded targets. But the roots evaluate a sidecar certified only over 701–1361 K at 2031.42 K and 1931.16 K: 670.42 K and 570.16 K above its upper limit. Both are also above Mg's 1363.15 K normal-boiling rail boundary. The calculation therefore applies a condensed standard state to gaseous Mg and double-counts vaporization. Its certified error bar is undefined.

The Antoine data remain useful and unchanged inside their certified range. Existing ground-truth tests pin the CRC/Stull table at 1 Pa/701 K, 1000 Pa/971 K, and 100000 Pa/1361 K and pin the NIST normal boiling point near 1363.15 K. No coefficient was retuned and no range was extended.

### Current Mg-metal gas-standard calculation

Above 1363.15 K, runtime selects the JANAF Mg(g) row. Both corrected roots lie in the 2000–2600 K segment:

```text
K2(T) = exp((H - T*S)*1000/(R*T))
H = -1443.971643 kJ/mol-O2
S = -0.402149286 kJ/mol-K/mol-O2
P_Mg = p0 * sqrt(K2(T) * 0.0926^2 / fO2_bar)
solve P_Mg = 0.01 bar = 1000 Pa
```

| Premise | assumed melt fO₂ (bar) | Root (K) | Derived root (°C) | Stored target (°C) |
|---|---:|---:|---:|---:|
| Moon, fixed comparison activity | 1.3e-12 | 2165.797 | **1892.647** | **1892** |
| Asteroid, fixed comparison activity | 1.0e-14 | 2041.853 | **1768.703** | **1768** |

Sanity checks: the lower asteroid floor opens at lower temperature; both roots use Mg(g); and neither root depends on a high-temperature Antoine extrapolation. The stored whole-degree targets follow the owner brief and sit within 1 °C of the executable roots rather than claiming millikelvin process accuracy. These numbers remain premise-qualified. Body alone is insufficient: stage-specific `a_MgO` and the actual SSO-R melt fO₂ trajectory must be frozen, and the test assumes that melt fO₂ has reached the body floor. The current 1670 °C C4 hold is an operational target, not a 0.01-bar gate, so it was not silently raised to 1892 °C.

### Phase-basis scope and remaining standard-state gap

The stale roots were definitively wrong on the **Mg metal phase**: above 1363.15 K the product standard state is Mg(g), so multiplying the gas-fugacity root by condensed Mg Antoine double-counts vaporization. The corrected targets prove and pin that rail, including the Chase 1998 2600 K Mg(g)/MgO(s) source-grid anchor.

The review also exposed a pre-existing oxide-standard-state limitation outside this correction: the JANAF formation segments name MgO(s), while `melt_oxide_activity` labels its generic activity convention as a Raoultian pure-liquid reference. No explicit MgO liquid-to-solid standard-state conversion/fusion term is present. The requested values are therefore **Mg-metal-phase-correct roots of the current runtime convention**, not a claim that every oxide reference-state conversion is complete. Fixing that gap needs a separately sourced MgO fusion/chemical-potential treatment; inventing one here would be the prohibited target retune.

## 4. Changes

- Added full premise → algebra → unit → limiting-case derivations to both authoritative and fallback non-FeO source calculations.
- Corrected the provider module contract: melt fO₂ owns non-FeO source equilibrium; overhead owns transport/backpressure and the explicit SiO lever.
- Replaced the false `0.5 bar Mg at 1600 °C` scalar with parsed, premise-qualified Moon/asteroid threshold metadata.
- Marked the 18–42 kg/t C4 yield as an ungrounded legacy projection and withdrew the pN₂ `~0.1 bar` claim.
- Updated chemistry methods, concepts, and recipe documentation to keep melt equilibrium, surface transport, and pure-MgO congruent validation separate.
- Added an executable bisection regression that binds the YAML targets to the canonical JANAF gas rail and CF-1 floors.

## 5. Golden impact

Canonical executable: `.venv/bin/python3 scripts/regenerate_runner_goldens.py`.

Moved and staged:

- `tests/fixtures/runner/lunar_mare_low_ti_C0_24h.json`
- `tests/fixtures/runner/mars_basalt_C2A_12h.json`

Regenerated but byte-identical:

- `tests/fixtures/runner/ci_carbonaceous_chondrite_C2B_12h.json`

The moved fixtures are deterministic on a second regeneration. The causal chain is indirect but task-owned: adding parsed C4 threshold metadata changes the full functional setpoints digest; that digest participates in reduced-real cache keys; the new key misses the existing cache; and the scenarios engage the current melt-redox floor fallback. Lunar consequently moves fallback diagnostics plus downstream Fe redox, fO₂-buffer inventory, deposits, and small mass fields; Mars moves fallback-engagement diagnostics. These scenarios stop before C4, so the deltas do **not** demonstrate earlier physical response to the Mg threshold itself. They demonstrate the repository's full-config cache-identity policy. `data/corpus_version.yaml` is unchanged.

## 6. Null-hypothesis rejection

- **No target retune:** no Mg coefficient, JANAF segment, activity coefficient, vacuum floor, or runtime equation was changed to force 1892/1768. The target test solves the existing Mg-metal-phase-correct executable.
- **Surface path preserved:** equilibrium still feeds `P_eq`; evaporation still applies `P_eq-P_bulk` and gas/melt/interface resistances. Existing pO₂-slope/flux tests pass.
- **Mg metal phase proven:** both roots are above the Mg boiling boundary, select `metal_phase_kind == gas`, and use `P_Mg=(f_Mg/p0)*p0`. Condensed Antoine remains separately tested only against certified anchors. The separate MgO(s)-versus-liquid-activity standard-state limitation is disclosed above rather than hidden by retuning.
- **Congruent anchor kept separate:** no JANAF coefficient or pressure rail was changed. The reconciliation treats pure-MgO self-consistent co-evolution as an independent reaction-basis anchor, not as a runtime path proven by `P_eq-P_bulk` or as a license to alias overhead and melt oxygen activities.

## 7. Verification

- Required: `.venv/bin/python3 -m pytest tests/test_physics_ground_truth.py tests/test_mass_balance.py -k "mg or dissoc or vapor" -n0 -q` → **27 passed, 57 deselected**.
- Basis/phase/flux focus → **24 passed, 93 deselected** (three pre-existing SiO fallback warnings).
- Full runner/golden suite: `tests/test_runner_smoke.py -n0 -q` → **60 passed**.
- Canonical regeneration repeated twice → stable fixture hashes.
- `git diff --check` → clean.
- No commit, no push, no corpus-version bump.
