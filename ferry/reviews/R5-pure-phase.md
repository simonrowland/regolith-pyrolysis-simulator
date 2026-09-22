# R5 — pure-phase G/S/Cp/H (P4a-1)

**Scope:** `037f84bce` on `origin/review/janaf-p4a1-pure-phase` (base `fbe3491b2`). Read-only review; optional `ferry/reviews/R5-fix.patch` not applied. Do not push.

**Engines on this box:** MAGEMin **binary missing** (`REGOLITH_MAGEMIN_BINARY` unset; `MAGEMinBackend._locate_binary` not usable here — `yaml` import blocks import path in this env). ThermoEngine **Python package missing** (`import thermoengine` → ModuleNotFoundError). `engines/alphamelts/thermoengine.py` **source present** and reviewed. Verdict mode: **static** (parser/unit-guard tests not executed against live engines).

**Intent (claimed):** For one named pure phase (forsterite, periclase, quartz, cristobalite, clinoenstatite) at `(T, P)`, return apparent G plus S/Cp/H with polymorph, formula basis, and database named; typed absence when a property is unreachable (MAGEMin S/Cp/H only when the phase is stable — and pure — in the stoichiometric assemblage); refuse a MAGEMin run whose matlab SYS oxide echo differs from the requested bulk (KLB1 trap when first/SiO2 component is 0).

**Attack surface (brief):** wrong polymorph; formula-basis slip (HP `en` is Mg2Si2O6 → must be /2 onto MgSiO3); unit slip (kJ/J, kbar/bar); per-phase factor-scaling slip; any change to existing `equilibrate()` behaviour; any numeric value where typed absence is required.

---

## Findings

### P1 — MAGEMin `pure_phase_properties` converts bar→kbar with `1e-4` (10× low)

**Evidence:**
- `simulator/melt_backend/magemin.py` (commit) `pure_phase_properties`:  
  `pressure_kbar = float(pressure_bar) * 1.0e-4`  
  Comment claims “1 kbar = 1000 bar” but multiplies by **1e-4**.
- Existing `equilibrate` / `_call_magemin` path (unchanged, same file):  
  `_pressure_bar_to_GPa`: `/ 1.0e4` (1 GPa = 10000 bar) then `_GPa_to_kbar`: `* 10` → **net `bar * 1e-3`**.  
  At `pressure_bar=1`: equilibrate → `0.001` kbar; pure-phase → `0.0001` kbar (**0.1 bar**).
- Live KLB1 probe test encodes the same wrong factor:  
  `tests/test_pure_phase_access.py` `test_klb1_fallback_refused_live` passes `pressure_kbar=1.0e-4` as if it were 1 bar.

**Why P1:** Exact “kbar/bar” unit slip on the attack list. Every MAGEMin pure-phase probe runs at **0.1×** the requested pressure. At 1 bar the solid VΔP error is ~J/mol so JANAF bands still pass — the bug **self-masks** in the committed 1-bar anchors. At elevated P (future JANAF reaction checks, geologic P) the state is silently wrong and can fake agreement or disagreement. Fix is one line plus the test literal; prefer reusing `_pressure_bar_to_GPa` + `_GPa_to_kbar` so the conversion stays auditable with equilibrate.

**Fix direction:** See `ferry/reviews/R5-fix.patch`.

---

### P2 — Docs / test prose claim matlab S prints as `31.708 J/K` while code treats kJ and `*1000`

**Evidence:**
- Class notes: “prints S=31.708 J/K with factor 1/3 → 95.13”.
- Test docstring: “raw matlab column is 31.7 J/K … only the corrected value lands near 95.14”.
- Code: `S_J_K_mol = phase_row['S_kJ_K'] / factor * 1000.0 / divisor` (and same `*1000` for H).
- Upstream `dump_function.c` headers say `Entropy[J/K]` / `Enthalpy[J]` / `G[J]`, but G is already treated as kJ (verified quartz ≈ −923); `toolkit.c` builds `phase_entropy = -dGdT*factor` in the same energy unit as G → printed S/H are on the **kJ** scale despite the J headers. So `*1000` after `/factor` is the physically consistent path; the “31.708 J/K” wording is the J-equivalent after `*1000` before `/factor`, not the raw column.

**Why P2:** Not a land blocker if live tests pass, but the prose invites a “fix” that drops `*1000` and would return S/H wrong by 1000× — the exact factor-scaling / kJ–J slip this task is meant to prevent. Align comments and test docstring with “raw ≈ 0.0317 kJ/K (header mislabelled J)”.

---

### P2 — ThermoEngine worker dispatcher now branches on `request_kind` (equilibrate fallthrough OK, key is a landmine)

**Evidence:**
- `engines/alphamelts/thermoengine.py`: handler switched from `_handle_thermoengine_request` to `_handle_thermoengine_worker_request`.
- Pure-phase requests: `request_kind == 'pure_phase'`.
- `equilibrate()` still sends bare kwargs (`temperature_C`, `pressure_bar`, `comp_wt`, …) with **no** `request_kind` → fallthrough unchanged.

**Why P2:** No demonstrated equilibrate behaviour change. Residual risk: any future Mapping payload that accidentally carries `request_kind: 'pure_phase'` is diverted; document that `request_kind` is reserved / assert absence on the equilibrate path.

---

### P3 — MAGEMin `en` is orthoenstatite; JANAF MgSiO3(cr) is clino — reliant on `polymorph` field

**Evidence:** Registry labels `en` → `polymorph='orthoenstatite'` with MgSiO3 basis after `/2`. TE path correctly exposes `cEn` → `clinoenstatite` and tests JANAF Cp(1000) there. Intent list names clinoenstatite; MAGEMin cannot supply that polymorph from ig opx.

**Why P3:** Correctly labelled; downstream must not compare MAGEMin `en` G/S to JANAF clino without reading `polymorph`. No code bug.

---

### P3 — ig `1e-4` mol floor can contaminate “stoichiometric” pure bulks

**Evidence:** Upstream `retrieve_bulk_PT` floors several ig oxides (including SiO2, Al2O3, FeOt when below 1e-4 mol). Pure MgO / SiO2 / Mg2SiO4 bulks therefore run with trace contaminants. Bulk-echo tol (`3e-3`) still accepts; SS purity gate (`0.999`) is the defence for fo/per.

**Why P3:** Latent; live anchors (when engines exist) are the real check. Worth a comment near the registry that purity gate + tol absorb the floor.

---

## What looks sound (attack checklist)

| Attack | Assessment |
| --- | --- |
| Formula basis Mg2Si2O6→MgSiO3 | `formula_divisor=2` on G/S/Cp/H for `en`; basis string names the divisor; TE `cEn` asserts `MgSiO3`. |
| kJ/J on G | Verb=1 gbase ×1000; matlab G treated as kJ despite `G[J]` header — consistent with quartz ≈ −923 kJ. |
| Factor scaling S/H vs Cp | Matches `toolkit.c`: S/H include `factor`, Cp does not; code `/factor` only for S/H. |
| Typed absence when unstable / impure | S/Cp/H → `PHASE_NOT_STABLE_AT_TP` / `PHASE_NOT_PURE_AT_EQUILIBRIUM` / `PHASE_FACTOR_UNAVAILABLE`; G from endmember table may remain (documented). |
| KLB1 bulk guard | SYS wt-fr echo vs request; skips buffer `O`; unit tests for match / KLB1 / missing SYS; live trap test (when binary present). |
| `equilibrate()` body | Diff is additive (imports + parsers + new methods). MAGEMin equilibrate path untouched. TE equilibrate kwargs unchanged. |
| Polymorph discrimination (TE) | Qz vs Crs S/G ordering tested. |

---

## Verdict rationale

No P0. One real unit-contract bug (bar→kbar 10×) that ambient 1-bar JANAF bands will not catch — **LAND-WITH-FIXES**. Apply the pressure fix before relying on elevated-P reaction checks. Re-run live `tests/test_pure_phase_access.py` when MAGEMin binary and ThermoEngine are available.

VERDICT: R5 | LAND-WITH-FIXES | P0=0 P1=1 P2=2 P3=2 | static
