# REVIEW — residue-r1a: vacuum congruent-evaporation oxygen balance

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Tip:** `802b69ad7b6197046b27286e98e42bed91322fbc` on `review/residue-r1a`
- **Parent (R0):** `53d71755a4be677d22e50d10a3275981424cc7bd`
- **Green tip context:** `191960ce8a657a25681aad624dd64670bf85df6a` on `work-v064-green`
- **Range:** `53d71755a4be677d22e50d10a3275981424cc7bd..802b69ad7b6197046b27286e98e42bed91322fbc` (one commit)
  - `802b69ad7b6197046b27286e98e42bed91322fbc` Add vacuum oxygen balance
- **REQ:** `/workspace/ferry-inbox/REQ-residue-r1a-from-regolith-physics-2026-10-02.md`
- **Bundle:** `/workspace/ferry-inbox/regolith-physics-residue-2026-10-02/{r1a-report.md, mutate_residue_oxygen_r1a.py, sol-review-of-residue-r1.md}`
- **Design context:** DESIGN-residue-predictor-r2 §8 R1a (vacuum oxygen); sol-review P1 engine-consistent α-weighted balance
- **Date:** 2026-10-02 ~13:43–13:50 ET
- **Mode:** read-only for product code; extracts not edited; no force-push. Targeted VPS unit tests only (no full W3 / full-store score). OpenIMCC installed in seat venv at recorded pin `afcb5d80abb16d931174a5a587e4ffaee6cbcadc` for this review.

## Scope

Residue predictor chunk R1a: vacuum congruent-evaporation oxygen balance per engine; alpha applied per channel before solving; alpha provenance recorded; dormant channels and channels with no parent activity excluded; IA O/O2 terms use the shared gas-phase relation.

Files (`git diff --stat` vs R0):

| Path | Δ |
| --- | --- |
| `simulator/battery/oxygen_balance.py` | +204 / −1 |
| `tests/battery/test_residue_oxygen_balance.py` | +400 (new) |

Mutation script remains outside the commit (ferry bundle only). Pre-existing `has_own_engine_solved_oxygen_balance` / notice prefix / engine set are preserved; the new solver is additive.

## Attack results

### (1) Balance is the engine's OWN (no borrowed root on residue path) — **PASS**

- `_solve_vacuum_oxygen_balance(pressure_model, channels, …)` takes the engine's pressure law as a callable; each evaluation builds a separate OpenIMCC `evaluate_gas` model and a separate IA `equilibrate` model.
- Tip pin at Hashimoto 2073.15 K: openimcc **0.799303 Pa** (α=1) / **0.289332 Pa** (parent α=0.25); IA **1.025329 Pa** / **0.374487 Pa**. Independent recompute on this seat matched those pins (rel ≤ 2e-5).
- Acceptance recomputes each engine's oxygen/parent fluxes at the returned root (`_relative_residual_at`); residual < 1e-9. Cross-substituting the other engine's root leaves residual > 1e-3 on both directions.
- Mutation “borrow the other engine's root” is **detected**. The old IA KEMS adapter's borrow pattern cannot survive this residue acceptance path.

### (2) Excluding channels with no parent activity — **PASS** (correct; does not hide a counting channel at Hashimoto start)

Independent openimcc probe at Hashimoto start composition:

| Parent | a(parent) |
| --- | ---: |
| Na2O / K2O / TiO2 | **0.0** |
| FeO / MgO / SiO2 / CaO / Al2O3 | nonzero |

- OpenIMCC gas-channel list: **30** species. Zero-activity filter excludes **11** (Na, K, Na2, NaO, K2, KO, Ti, TiO, TiO2, Na2O, K2O).
- Of those 11, only **NaO/Na2O** and **KO/K2O** lack a runtime catalog `(formula, parent)` row — matching the worker's “zero-activity Na/K … no catalog row” exploratory failure. The other nine *do* have catalog rows; they are still correctly dropped because `a=0 ⇒ p≈0 ⇒ flux=0` under the Hertz–Knudsen / activity–fugacity law.
- Exclusion therefore does **not** hide a channel that should contribute at this start. P2 awareness for later producer chunks (R1b/R3): when activity becomes nonzero during depletion, the channel set must be re-built; a frozen start-time exclusion list would then under-count.

Dormant channels (`flux_dormant`): FeO (openimcc) / FeO_association_gas (IA) absent from `alpha_sources` as asserted.

### (3) Alpha provenance and O/O2 coefficients — **PASS**

- Solver refuses empty `alpha_source`; returns `alpha_sources` for every active, α>0 channel.
- O/O2: `_OXYGEN_GAS_ALPHA=1.0` with declared common-unity provenance (`absolute oxygen-gas alpha is not measured`). Parent-bearing channels carry catalog alpha source (or “no alpha row”) behind the Hashimoto `assumed_unity_ratio_not_measured_alpha` / declared common-unity sensitivity wording — aligns with sol-review P1 on α≡1 as sensitivity arm, not measured absolute α.
- Stoichiometry: `pO2_exponent = (nO − dO_parent)/2` for melt channels; IA oxygen-gas channels force O→0.5, O2→1.0. Solver verifies observed log-pressure slope against declared exponent (±1e-6) around the root.
- Uniform α×0.5 on all channels leaves pO2 unchanged (common factor cancels); parent-only α 1→0.25 moves the root (differential α does not cancel).

### (4) Shared O/O2 gas relation for IA vs openimcc — **PASS** (same standard states)

- IA vapor table has no O/O2 entries; tip builds O2 as the commanded surface fugacity and O from `evaluate_gas(…, pO2=1 bar)["O"] * sqrt(pO2)` using the **same** openimcc gas datapack.
- Independent check at IA's unity root: shared-relation O and O2 pressures match `evaluate_gas` at that same pO2 with **rel diff = 0** for both species. Same thermochemical source / standard states; IA does **not** borrow openimcc's *solved* pO2 — only the O↔O2 gas-phase factor.

### (5) Mutations fail — **PASS**

```
.venv/bin/python /workspace/ferry-inbox/regolith-physics-residue-2026-10-02/mutate_residue_oxygen_r1a.py
borrow the other engine's root: detected ()
change alpha without re-solving pO2: detected ()
```

### (6) Landed KEMS effusion path unchanged — **PASS**

- `git diff 53d71755a..802b69ad7` touches **only** `oxygen_balance.py` and the new residue test. Zero diff on `binary_pot_battery.py`, `score.py`, `validate.py`.
- Landed IA `PO2_OXYGEN_BALANCE_EFFUSION` path still obtains pO2 from OpenIMCC (`oxygen_source = "openimcc_oxygen_balance_condition_only"`) via `evaluate_gas_oxygen_balance` — intentional KEMS convention, out of R1a scope.
- New `_solve_vacuum_oxygen_balance` is **not** wired into production score/effusion callers yet (residue acceptance + future producer only). `has_own_engine_solved_oxygen_balance` behaviour unchanged.

## Targeted tests (VPS)

| Suite | Result |
| --- | --- |
| `tests/battery/test_residue_oxygen_balance.py` | **1 passed** (~21 s; openimcc pin installed for review) |
| Mutations `mutate_residue_oxygen_r1a.py` | **2/2 detected** |
| `tests/battery/test_score.py -k oxygen_balance\|has_own_engine_solved` | **1 passed** |
| `tests/battery/test_silent_fills.py -k oxygen_balance\|effusion\|PO2` | **15 passed** |
| `tests/test_openimcc_battery_engine.py -k oxygen_balance` | **1 passed** |

`rg` of changed symbols: `_solve_vacuum_oxygen_balance` / `_VacuumOxygenChannel` / `_OXYGEN_GAS_ALPHA*` used from the new test (+ definitions in `oxygen_balance.py`). Pre-existing `has_own_engine_solved_oxygen_balance` / `OXYGEN_BALANCE_*` still referenced from `score.py`, `validate.py`, and their prior tests — exercised above; no product-path rewrite.

Worker claim “517 passed” was not re-run as a bulk suite on this ~16GB VPS (policy). Targeted referencing files above are green.

Not run (policy/cost): full W3, full-store score, Mac Studio full pytest. Not needed for this R1a review gate.

## Design §8 R1a / sol-review alignment

R1a acceptance (engine-consistent oxygen closure; borrow other engine's root → failure; α applied before solving; α provenance) is met by tip + mutations. Addresses sol-review P1 vacuum-oxygen item for the residue path without retargeting the landed KEMS borrow convention (explicitly attacked as “unchanged”).

## Verdict

**LAND `802b69ad7b6197046b27286e98e42bed91322fbc`**

No P1 REVISE items. Optional P2 awareness only: (a) later integrator must re-admit channels when parent activity rises from zero; (b) wiring this solver into the producer / replacing the KEMS IA borrow is a later chunk, not this one.

— regolith-empirical
