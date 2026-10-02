# REVIEW — residue-r1b: mol-native finite-step free-evaporation inventory

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Tip:** `69f5828c4ec70877a6d390d712e50ff5909ece49` on `review/residue-r1b`
- **Parent (R1a LAND):** `802b69ad7b6197046b27286e98e42bed91322fbc` — confirmed ancestor (`git merge-base --is-ancestor`)
- **Green tip context:** `191960ce8a657a25681aad624dd64670bf85df6a` on `work-v064-green` (via R0→R1a)
- **Range:** `802b69ad7b6197046b27286e98e42bed91322fbc..69f5828c4ec70877a6d390d712e50ff5909ece49` (one commit)
  - `69f5828c4ec70877a6d390d712e50ff5909ece49` Integrate residue evaporation inventory
- **REQ:** `/workspace/ferry-inbox/REQ-residue-r1b-from-regolith-physics-2026-10-02.md`
- **Bundle:** `/workspace/ferry-inbox/regolith-physics-residue-2026-10-02/r1b-report.md`
- **Design context:** DESIGN-residue-predictor-r2 §8 R1b (inventory integration; sol MINOR; P2 #3 finite-step oxygen closure); synthetic α=0 / p≡0 smoke
- **Date:** 2026-10-02 ~15:03–15:07 ET
- **Mode:** read-only for product code; extracts not edited; no force-push; no Mac listen pools. Targeted VPS unit tests only (no full W3 / full-store / full pytest). Mutation script authored for this review at `/tmp/mutate_residue_r1b.py` (not landed).

## Scope

Residue predictor chunk R1b: mol-native finite-step free-evaporation integrator. Per step the R1a oxygen root for the predicting engine; builtin HKL fluxes × declared area; oxygen balanced from the **actual** parent debits (partition O/O2 by instantaneous root flux ratio only); missing area evolution → typed absence via `score.py` (+14).

Files (`git diff --stat` vs R1a):

| Path | Δ |
| --- | --- |
| `simulator/battery/residue.py` | +485 (new) |
| `simulator/battery/score.py` | +14 |
| `tests/battery/test_residue_oxygen_balance.py` | +207 |
| `tests/battery/test_score.py` | +14 / −3 |

Worker claim “1,381 tests passed” was **not** re-run as a bulk suite on this ~16GB VPS (policy).

## Attack results

### (1) Finite-step oxygen closure from ACTUAL parent debits — **PASS**

- Integrator docstring + body (`integrate_residue_inventory`): after analytic parent depletion, `finite_step_oxygen_mol = Σ(nO_parent × parent_debit) − Σ(nO_gas × product_moles)`; O/O2 from `balance.channel_fluxes_mol_m2_s` set **only** the partition ratio (`partition_scale = finite_step_oxygen_mol / raw_oxygen_atoms`), not the committed oxygen atom count.
- Acceptance `test_residue_finite_step_oxygen_tracks_actual_parent_depletion_and_re_solves`: two-step run re-samples inventory (2 compositions), re-solves pO₂ each step (`pO2[0] ≠ pO2[1]`, rel > 1e-6), each step root residual `< 1e-9`, and `evaporated O2 ≈ (ΔFeO + ΔMgO)/2` within rel 1e-12.
- Independent seat hand-check (Fe + O2, 100 s, area 1e-4 m²): parent debit FeO = evaporated Fe = `6.0611530206561726e-06` mol; evaporated O2 matches debit/2 within rel **−1.5e-14**. Oxygen is not `raw_flux × dt`.

### (2) Atom closure tolerance scale-aware — **PASS**

- Closure gate: `abs(residual) > max(1e-15, 5e-12 × scale)` with `scale = max(|initial|, |final|, 1e-300)` per element — absolute floor for tiny inventories, relative band for large ones.
- Hand-check at FeO start 1e-3: Fe/O residuals **0.0**, tol = `5e-15` (= 5e-12 × 1e-3). Formula probe: `tol(1e-18)=1e-15`, `tol(1)=5e-12`, `tol(1e3)=5e-09`.
- Acceptance `_assert_residue_atoms_close` uses matching rel `5e-12` / abs `1e-15`.

### (3) Three mutations really fail — **PASS**

```
.venv/bin/python /tmp/mutate_residue_r1b.py
gas mass as oxide: detected (AssertionError: )
frozen pO2: detected (AssertionError: )
forced alpha 1 with finite p: detected (AssertionError: )
all three mutations detected
```

| Mutation | Mechanism | Caught by |
| --- | --- | --- |
| gas mass as oxide | After integrate, subtract evaporated Fe **kg** from FeO **mol** inventory (mass/oxide confusion) | `test_residue_two_channel_hkl_and_parent_stoichiometric_anchors` (residue / O2 / atom anchors) |
| frozen pO2 | Keep first-step pO₂ for subsequent steps (no re-solve on depleted inventory) | `test_residue_finite_step_oxygen_tracks_actual_parent_depletion_and_re_solves` (pO₂ change + per-step residual) |
| forced α=1 with finite p | Replace catalog parent α with 1.0 while pressures stay finite | `test_residue_two_channel_hkl_and_parent_stoichiometric_anchors` (external HKL mol anchor uses catalog α=0.02) |

Aligns with DESIGN §8 R1b (“subtract gas mass as oxide mass → failure”) and synthetic smoke note (α mutation must use finite p, not p≡0 alone).

### (4) External HKL anchor hand-checkable — **PASS**

Safarian–Engh / HKL mol rate (same algebra as builtin kg form ÷ M):

`J_mol = α · p / √(2π M R T) · A`

Seat numbers at tip (catalog Fe α=**0.02**, Costa & Jacobson / Ebel provenance):

| Quantity | Value |
| --- | ---: |
| solved pO₂ | `1.7894223040876892e-07` bar |
| p(Fe) | `2.3639787965403625` Pa |
| hand HKL Fe mol (1−e^(−r Δt/N)) | `6.0611530206003955e-06` |
| integrator evaporated Fe | `6.061153020656082e-06` |
| rel diff | **9.2e-12** |
| O2 = Fe/2 | match within ~1e-14 |
| FeO residue | start − Fe (stoich 1:1) |

### (5) No second model — **PASS**

- `_finite_step_fluxes` constructs `BuiltinEvaporationFluxProvider()` and `dispatch`es with engine-solved `vapour_batch_flux_pressures_Pa`, declared area, catalog α, stoich, resistances off / p_bulk=0.
- Spy: exactly **1** `BuiltinEvaporationFluxProvider.dispatch` call per single-step integrate.
- `residue.py` contains **no** inline `√(2πMRT)` HKL formula; pressures come from the caller’s `pressure_model` (engine path) + R1a `_solve_vacuum_oxygen_balance`. Hero-run path = engine pressures + builtin HKL.

### (6) score.py hook is minimal — **PASS**

- Diff vs R1a: **+14 lines only** inside existing `Quantity.RESIDUE_COMPONENT_COMPOSITION` branch: for `engine in OXYGEN_BALANCE_EFFUSION_ENGINES` (`internal-analytical`, `openimcc`) return `NOT_PROBED` / `IDENTITY_INCOMPLETE` / reason `melt_surface_area_evolution_missing` (matches integrator typed absence). Other engines keep prior `UNSUPPORTED` / `quantity_not_predicted`.
- `test_score.py` updated accordingly (+14/−3). No broader score rewrite; leaves room for kems-activity edits of the same file.
- Integrator `ResidueInventoryRefusal("melt_surface_area_evolution_missing")` when `area_evolution_m2` is None/empty — same reason string.

## Targeted tests (VPS)

| Suite | Result |
| --- | --- |
| `tests/battery/test_residue_oxygen_balance.py` | **6 passed** (~23 s) |
| Mutations `/tmp/mutate_residue_r1b.py` | **3/3 detected** |
| `tests/battery/test_score.py -k residue_composition` | **1 passed** (~7 s) |

`rg` of changed symbols:

- `integrate_residue_inventory` / `ResidueChannel` / `ResidueInventoryRefusal` / `_finite_step_fluxes` — definitions in `residue.py`; used from new residue tests only.
- `melt_surface_area_evolution_missing` — `residue.py`, `score.py`, `test_residue_oxygen_balance.py`, `test_score.py`.
- Pre-existing `BuiltinEvaporationFluxProvider` still referenced from chemistry/transport tests (unchanged product path); residue path spies confirm reuse, not a fork.

Not run (policy/cost): full W3, full-store score, Mac Studio full pytest, worker’s claimed 1,381 bulk suite.

## Design §8 R1b / sol-review alignment

R1b acceptance (external HKL/stoichiometric anchors; atom closure; mol-native parent depletion; gas-kg-as-oxide-kg mutation fails; α=0 / p≡0 no-change smoke; α forced with finite p fails; finite-step oxygen from committed debits) is met by tip + mutations. Addresses DESIGN P2 #3 finite-step oxygen closure for the residue inventory path. Score typed-absence for missing area is the declared boundary (geometry policy not invented by integrator).

## Verdict

**LAND `69f5828c4ec70877a6d390d712e50ff5909ece49`**

No P1 REVISE items. Optional P2 awareness only: (a) later producer wiring / Hashimoto geometry policy (R2) still required before scored residue predictions; (b) R1a note stands — channel set must re-admit parents when activity rises from zero during depletion (integrator already rebuilds active parents from current inventory each step).

— regolith-empirical
