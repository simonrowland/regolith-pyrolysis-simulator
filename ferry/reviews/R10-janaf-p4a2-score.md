# R10 — JANAF pure-phase reaction/property score (P4a-2)

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Commit under review:** `7d11e6d60` on `origin/review/janaf-p4a2-score` (NOT landed on green; parent `037f84bce` P4a-1)  
**Files:** `simulator/melt_backend/pure_phase_janaf_score.py`, `scripts/janaf_pure_phase_score.py`, `tests/test_pure_phase_janaf_score.py`

**Intent:** Score MAGEMin / ThermoEngine pure-phase standard-state access against NIST-JANAF 4th: (1) Δ_rG(T) for `2 MgO + SiO2(qz) → Mg2SiO4` and `MgO + SiO2(qz) → MgSiO3` — engine apparent-G reaction sum vs JANAF Δ_fG reaction sum (element reference terms cancel on a balanced reaction); (2) convention-free per-phase S / Cp / H(T)−H(298.15). Polymorph mismatches refused typed; typed absences never become zeros.

**Attack surface:** sign / stoichiometry slips; polymorph mismatches scored instead of refused; JANAF table misread (column / units); typed absences becoming zeros; manufacture / hide residual.

**Method:** Static read of tip files + engine polymorph maps + JANAF `units_as_published` / `parse_ambiguities`. Hand-checked reaction sums against live compilation digits. Adversarial probes (flipped stoich, Δ_fH vs Δ_fG column identity, absence→zero, absence+numeric-0 conflict, H−H298@298.15 %/0, live polymorph recheck, Mg-012 T vs I/II/III). Pytest on tip worktree at `7d11e6d60`. Engines absent on this box — arithmetic / contract tests only. Mode: **ran-tests**.

---

## Findings

### P1 — Mg-012 scored as monolithic `clinoenstatite` across JANAF I↔II / II↔III; high-T rows are fake residuals

**Evidence (file:line on `7d11e6d60`):**

- `pure_phase_janaf_score.py:114` — `JANAF_TABLES['Mg-012']` stamps polymorph `'clinoenstatite'` for the entire table. Compilation title is only `Magnesium Silicate (MgSiO3) | Mg1O3Si1(cr)` — no clino/ortho/proto token in the table body.
- Mg-012 `parse_ambiguities` (live compilation) record solid–solid markers the consumer never reads:
  - `903 K` — `I <--> II`
  - `1258 K` — `II <--> III`
  - (then `1850 K` III↔LIQUID)
- `pure_phase_janaf_score.py:68` — `DEFAULT_TEMPERATURES_K = (298.15, 500, 1000, 1500)` places **two of four** score points after transitions: 1000 K → form **II**; 1500 K → form **III**.
- Polymorph gate is **inverted** relative to those phases (`:366–371`, `:288–298`, `:378–411`):
  - TE `cEn` (clino) + TE `REACTION_ENSTATITE` → `preflight_*` returns `None` → **scored** at 1000/1500 K
  - TE `En` / MAGEMin `en` (ortho) → refused `polymorph_mismatch` against Mg-012
  - TE `pEn` (proto) would refuse under the same monolithic label
- **Constructed trigger:** `PhaseScoreRequest('thermoengine','cEn','Mg-012')` → `preflight_phase_request` is `None`; `build_phase_row(..., 1000.0, ...)` returns `status='scored'`, `janaf_polymorph='clinoenstatite'` while the printed 1000 K row is form II. Same for `REACTION_ENSTATITE` / ThermoEngine at 1000/1500 K via `scripts/janaf_pure_phase_score.py` `build_all_rows` (`:131–177`).

**Why P1 (live-in-data):** Named attack — mismatched polymorph scored instead of refused. The module docstring (`:31–37`) and commit claim “JANAF MgSiO3(cr) is clinoenstatite”; the compilation’s own I/II/III markers contradict that for T ≥ 903 K. Scout Cp matching may justify the **low-T / form I** identification; it does not license scoring past the published solid–solid lines. Residuals at 1000/1500 K for TE enstatite reaction and `cEn` phase rows are not meaningful under the stated contract.

**Fix direction:** `ferry/reviews/R10-fix.patch` — typed refusal (`janaf_polymorph_transition`) for any Mg-012-backed score at T ≥ 903 K; keep 298.15/500 K. Longer-term: split Mg-012 I/II/III onto clino/ortho/proto and match TE `cEn`/`En`/`pEn` (MAGEMin `en` for ortho) per band.

---

### P2 — Quartz O-037 α↔β (I↔II @ 847 K) unflagged while q→trd metastability @ 1143 K is

**Evidence:**

- O-037 `parse_ambiguities`: `847 K` `I <--> II` (α↔β quartz), `1696 K` II↔LIQUID.
- Consumer only notes when `T > QUARTZ_METASTABLE_ABOVE_K` (1143 K, q→trd) — `pure_phase_janaf_score.py:75`, `:455–462`. At **1000 K** (β-quartz in JANAF, still below 1143) there is **no** note; rows still score under polymorph token `quartz`.
- Both default reactions use O-037 / engine quartz at every default T (`:312–327`, `:332–345`), so α↔β discrepancy is folded into Δ_rG without a typed flag.

**Why P2:** Weaker than P1 because both sides share the token `quartz` and MELTS Qz typically embeds α–β in one phase. Still inconsistent with a “polymorph contract” that already special-cases silica transitions, and the JANAF marker is available in `parse_ambiguities` but ignored.

---

### P3 — Cristobalite high/low both polymorph token `cristobalite`

**Evidence:** `O-035` / `O-036` both `JanafTableSpec(..., 'cristobalite', ...)` at `:106–111`. Not in `DEFAULT_PHASE_REQUESTS` / reactions today. Probe: `preflight_phase_request(PhaseScoreRequest('thermoengine'|'magemin', 'Crs'|'crst', 'O-035'|'O-036'))` returns `None` for all four pairings — low vs high collapsed.

**Why P3:** Latent; same string-collapse class as Mg-012 but unused on the default plan. Δ_fG(high−low) is O(0.1–1) kJ/mol on the grid.

---

### P3 — `PropertyAbsence` ignored when engine value is not `None` (incl. numeric 0.0)

**Evidence:** `build_phase_row` `entry()` (`:589–626`) branches only on `engine_value is None`. Absences are consulted solely as the reason string when value is already None (`:606`). Probe: S/Cp/H = `0.0` **and** `absences=(PropertyAbsence(..., PHASE_NOT_STABLE_AT_TP),...)` → residual scored as `0 − janaf` with `absence_reason=None`.

**Why P3:** Latent under P4a-1 accessors (they set value `None` when absent). Defense-in-depth hole: a buggy engine returning `0.0` plus an absence token would manufacture a large residual and hide the typed absence. Not live on today’s accessors.

---

### P3 — Live polymorph recheck raises; script does not turn it into `RefusalRow`

**Evidence:** `build_reaction_row` / `build_phase_row` call `require_polymorph_match` on the **returned** `props.polymorph` and raise `PolymorphMismatchError` (`:490–494`, `:571–575`). `scripts/janaf_pure_phase_score.py` `build_all_rows` (`:131–177`) only uses static `preflight_*`; no `except` into a typed refusal before write.

**Why P3:** Fail-closed (abort, no partial JSON of fake residuals) rather than score. Contract still held; reporting path is less uniform than preflight refusals.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| Sign / stoichiometry slips | **Fail (safe) on arithmetic.** Products-positive terms (`:317`, `:335`); hand anchors match compilation: fo Δ_rG(1000)=−62.438, en=−34.750 kJ/mol; TE/MM fixture sums and quartz-adjustment algebra pinned in tests. Flipped-stoich probe yields the sign-inverted sum. |
| Polymorph mismatches scored instead of refused | **Hit — P1** on Mg-012 at 1000/1500 K (clino scored, ortho/proto refused). Static ortho→clino refusals for MM `en` / TE `En` / `trd` work as designed at the map level. Live recheck refuses via exception (P3). Quartz α↔β unflagged (P2). Cristobalite collapse latent (P3). |
| JANAF column / units misread | **Fail (safe).** Keys `formation_gibbs_energy` / `heat_capacity` / `entropy` / `enthalpy_increment` match `units_as_published` (kJ mol⁻¹ for G/H−H298; J K⁻¹ mol⁻¹ for Cp/S). Real-table test pins Mg-008@1000. Column-swap probe: Δ_fH ≠ Δ_fG. Exact-T match only (no silent interpolation). |
| Typed absences → zeros | **Fail (safe) on None path.** Absent engine property → `residual is None`, `absence_reason` preserved (`PHASE_NOT_STABLE_AT_TP`); absent G refuses reaction sum with explicit “never a zero” (`:495–499`). H−H298@298.15: `residual_pct is None` when ref=0. Residual hole: non-None value wins over absences (P3). |
| Manufacture / hide residual | **Fail (safe) on reporting.** Sign is `engine − JANAF` and declared in JSON; markdown lists scored rows and typed refusals; summary max‖Δ_rG‖ does not invent phase residuals. P1 manufactures *misleading* high-T enstatite residuals via wrong polymorph pairing (covered above), not by zeroing/hiding. |

---

## What looks sound

- Apparent-G vs Δ_fG cancellation derivation is correct for balanced reactions; no single-phase G vs Δ_fG comparison (`:11–28`, `:169–177`).
- Engine polymorph labels imported from engine maps (not restated) (`:55–56`, `:277–285`).
- TE MELTS quartz adjustment (−1.291 kJ/mol) reported as a separate variant; S/Cp/H-increment unaffected (constant cancels) (`:77–85`, `:526–538`).
- MAGEMin host-phase impurity note when `host_phase` set (`:648–657`).
- Pressure fixed at 1 bar (`PRESSURE_BAR = 1.0`); R5 MAGEMin bar→kbar `1e-4` slip is inherited from P4a-1 and self-masks at 1 bar (~J/mol VΔP) — out of scope for this consumer’s new code.

### Residual notes (not severity)

- `H_increment` absence reason falls back to generic `'absent'` when H is missing only at 298.15 (lookup uses `props.absences` at T, not `props_298`).
- Missing whole JANAF temperature raises `ValueError` rather than `RefusalRow(NO_JANAF_ROW_AT_T)`; per-cell nulls are typed absences.

---

## Tests

Tip worktree at `7d11e6d60`, repo `.venv`, `-o addopts=`:

- `tests/test_pure_phase_janaf_score.py`: **18 passed** (unpatched tip)
- Adversarial probes (stoich flip, column identity, absences, absence+0, /0 pct, Mg-012 T vs transitions, cristobalite preflight): **passed / P1 confirmed**
- Optional fix patch re-run: **21 passed** (not applied to the review branch)

Optional fix: `ferry/reviews/R10-fix.patch` (Mg-012 / enstatite T≥903 K typed refusal). Not applied to `review/janaf-p4a2-score`; do not push that branch.

---

## Verdict rationale

Arithmetic, units, absences-on-None, and static ortho≠clino refusals are solid. One contract-level defect on the named polymorph attack: high-T Mg-012 / TE enstatite rows are scored under a false single-polymorph label while better-matching engine phases are refused. **LAND-WITH-FIXES** — apply the T≥903 K refusal (or split I/II/III) before trusting 1000/1500 K enstatite residuals.

VERDICT: R10 | LAND-WITH-FIXES | P0=0 P1=1 P2=1 P3=3 | ran-tests
