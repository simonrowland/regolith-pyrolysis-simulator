# R19 — P4a chain SERIES (pure-phase access + JANAF banded score)

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Series under review:** `efaed2ae8..bba465959` on `origin/review/p4a-chain` (NOT landed on green)  
**Tip SHA:** `bba4659590315e2489afa97490a32479d24919b7`  
**Worktree:** `/workspace/repos/wt/slot-03` @ detached `bba465959`  
**Commits (4):**
1. `efaed2ae8` — typed pure-phase G/S/Cp/H access (MAGEMin + ThermoEngine) (P4a-1)
2. `0972c2a75` — pressure bar→kbar via production converter; S/Cp/H as FD of pure endmember G; solver status + NaN guards; quartz MELTS −1291 J label
3. `11cdb8a2c` — JANAF reaction/phase residual consumer
4. `bba465959` — JANAF polymorph temperature bands from printed transition markers

**Files:** `simulator/melt_backend/pure_phase.py`, `simulator/melt_backend/magemin.py`, `simulator/melt_backend/thermoengine.py`, `engines/alphamelts/thermoengine.py`, `simulator/melt_backend/pure_phase_janaf_score.py`, `scripts/janaf_pure_phase_score.py`, `tests/test_pure_phase_access.py`, `tests/test_pure_phase_janaf_score.py`

**Intent:** Typed pure-phase standard-state G/S/Cp/H from MAGEMin (endmember gbase + central-difference S/Cp/H) and ThermoEngine (Berman analytical); score balanced Δ_rG and convention-free S/Cp/H−H298 against NIST-JANAF 4th inside printed polymorph temperature bands; refuse mismatched bands typed (never score a fake residual).

**Attack surface:** FD step size / 2nd-deriv noise in Cp; sign of S (= −dG/dT); reaction stoichiometry; band edges inclusive/exclusive; any scored row whose engine polymorph is not the JANAF band that contains T; R10 Mg-012 monolithic-clino finding if still live.

**Method:** Static read of tip files + live compilation `parse_ambiguities` / value rows. Hand-checked reaction sums against compilation digits. Adversarial probes (band edges at markers, Mg-012/cEn/En/en preflight at all default T, quartz SAME_MINERAL subforms, cristobalite collapse, stoich flip, FD analytic recovery, quartz@847 no value row, absence-token-wins). Pytest on tip worktree. Engines absent on this box — arithmetic / contract / compilation tests only (live MAGEMin/TE rows skipped). Mode: **ran-tests**.

---

## Findings

### P3 — Quartz λ-stencil still returns FD S/Cp/H (flagged, not refused)

**Evidence (file:line on `bba465959`):**

- `magemin.py:386` — `_MAGEMIN_FD_DELTA_K = 10.0`
- `magemin.py:613–629` — `_lambda_stencil_warning` fires when `|T − 847| ≤ 10` for endmember `q`
- `magemin.py:2287–2311` — central differences always populate `S_J_K_mol` / `Cp_J_K_mol` / `H_J_mol`; the lambda note is appended to `warnings`, not turned into a typed absence

**Why P3 (latent):** Default score grid `(298.15, 500, 1000, 1500)` never sits inside the ±10 K window, so the JANAF consumer does not ingest polluted quartz Cp today. Constructed trigger for the accessor alone: `pure_phase_properties('q', temperature_K=840.0, pressure_bar=1.0)` still returns finite Cp that mixes α/β branches across the Landau spike. Contract documents “flagged, not dropped”; residual is defense-in-depth, not a silent fake score.

**Fix direction (optional):** Treat a straddling stencil like a typed absence for S/Cp/H (keep G at mid-T), or refuse the call when `|T − 847| ≤ δ`.

---

## R10 carry-forward

| R10 item | Status on `bba465959` |
| --- | --- |
| **P1** Mg-012 scored as monolithic `clinoenstatite` at 1000/1500 K | **Fixed / not live.** Bands are printed `i`/`ii`/`iii` (`[-inf,903)`, `[903,1258)`, `[1258,1850)`). Engine `clinoenstatite` / `orthoenstatite` match none. Preflight refuses `cEn`/`En`/`en` and both enstatite reaction engines at every default T. |
| **P2** Quartz α↔β unflagged | **Fixed.** O-037 bands `alpha`/`beta`; `SAME_MINERAL_SUBFORMS` scores engine `quartz` against the containing sub-form; notes name the printed sub-form. |
| **P3** Cristobalite high/low collapsed to one token | **Fixed.** Titles resolve to `cristobalite_high` / `cristobalite_low`; engine `cristobalite` matches neither (latent requests refuse). |
| **P3** `PropertyAbsence` ignored when value is non-None | **Fixed.** Absence wins; number not scored (`pure_phase_janaf_score.py:870–881`). |
| **P3** Live polymorph recheck aborts script | **Fixed.** `scripts/janaf_pure_phase_score.py:159–168,186–196` catch into `RefusalRow`. |

---

## Attack checklist

| Attack | Result |
| --- | --- |
| FD step size / Cp 2nd-deriv noise | **Fail (safe) on default plan.** Formula `Cp = −T (G₊ − 2G₀ + G₋)/δ²` with `δ=10 K` (`magemin.py:2288–2290`); comment measures worst Cp spread ~0.4 J/K vs JANAF 1–2% bands; analytic probe recovers constant-Cp to ~0.002%. Latent λ-window pollution → P3 above. TE path is analytical, not FD. |
| Sign of S | **Fail (safe).** `S = −(G₊ − G₋)/(2δ)` (`:2287`). Wrong-sign probe yields −S. Gibbs closure `G − (H − T S) = 0` pinned in live-style unit test (skipped here without binary). |
| Reaction stoichiometry | **Fail (safe).** Products-positive terms (`:543`, `:561`); hand anchors match compilation: fo Δ_rG(1000)=−62.438, en=−34.750 kJ/mol. Flipped-stoich probe yields the sign-inverted sum. TE quartz −1.291 kJ variant: `drg_no_adj = drg − ν·ADJ` (`:792`) correct for reactant ν=−1. |
| Band edges inclusive/exclusive | **Fail (safe).** Half-open `[t_min, t_max)` (`PolymorphBand` docstring `:148–153`; `_band_contains` `:266–271`). Marker T belongs to the arriving phase (903→`ii`, 847→`beta`, 1850/1696→no band). Marker lines live in `parse_ambiguities` only — `janaf_values_at(903/847)` is `None` (fail-closed `ValueError`, not a scored row). |
| Scored row whose polymorph ≠ JANAF band | **Fail (safe) for false residuals.** Mg-012/cEn/En/en never score. Quartz scores with `polymorph='quartz'` and `janaf_polymorph='alpha'/'beta'` only via declared `SAME_MINERAL_SUBFORMS` (`:139–141`); notes record the printed sub-form. No default scored row pairs a wrong mineral with a band. |
| R10 Mg-012 still live | **Not live** (see carry-forward). |

---

## What looks sound

- Apparent-G vs Δ_fG cancellation derivation unchanged and correct for balanced reactions.
- MAGEMin pressure uses production `bar → GPa → kbar` (`:2264`); old `×1e−4` 10×-low path removed; high-P volume-integral test documents the trap.
- Solver status: missing/3/4 refuse; 0/1/2/−1 keep pre-minimisation gbase with warnings (`:565–592`) — coherent with gbase-before-PGE-guard.
- Non-finite gbase tokens and FD results refuse typed (`_finite_gbase_token`, `:2291–2296`).
- MELTS quartz −1291 J labelled in TE provenance; S/Cp/H-increment unaffected (constant cancels).
- Engine polymorph maps still imported from engine modules (not restated).

### Residual notes (not severity)

- No positive MgSiO3 score remains on the default plan (all engine clino/ortho/proto refused against printed `i`/`ii`/`iii`). Honest until bands are mapped to TE `cEn`/`En`/`pEn` (and MAGEMin `en`) per form.
- Missing whole JANAF temperature still raises `ValueError` rather than `RefusalRow(NO_JANAF_ROW_AT_T)` inside builders; script fail-closes (no partial fake JSON).
- O-035 / O-036 bands are unbounded (no melt marker in ambiguities); unused on the default plan.

---

## Tests

Tip worktree at `bba465959`, repo `.venv` via `/workspace/repos/regolith-pyrolysis-simulator/.venv`, `-o addopts=`:

- `tests/test_pure_phase_access.py` + `tests/test_pure_phase_janaf_score.py`: **33 passed, 13 skipped** (engine binaries absent)
- Adversarial probes (bands/edges, Mg-012 refusals, stoich anchors, FD sign/Cp recovery, quartz subform scoring, cristobalite mismatch): **passed / R10 P1 confirmed dead**

No push. No fix patch (optional λ-refusal only).

---

## Verdict rationale

Named attacks on FD sign/noise, stoichiometry, band edges, and false polymorph scoring do not land a live wrong residual on the default plan. R10’s P1 is closed by printed Mg-012 bands that refuse every engine enstatite pairing. One latent accessor footgun remains near the quartz Landau point. **LAND.**

VERDICT: R19 | LAND | P0=0 P1=0 P2=0 P3=1 | ran-tests
