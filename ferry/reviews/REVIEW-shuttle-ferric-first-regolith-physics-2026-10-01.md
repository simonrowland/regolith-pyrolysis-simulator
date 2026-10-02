# REVIEW — shuttle ferric-first (b-645)

- **Reviewer:** regolith-empirical (frontier of record; re-seat after prior executor omitted REVIEW/STATUS)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Tip:** `5a1f5486e71229aedd724401d34a63fe78dba82d` on `review/shuttle-ferric-first` (detached seat)
- **Range:** `e7bd0cd5243ea7aa7e3eb565ed94b36f866f1c4c..5a1f5486e71229aedd724401d34a63fe78dba82d` (one commit)
  - `5a1f5486e71229aedd724401d34a63fe78dba82d` Prioritize ferric iron in alkali shuttle
- **Base:** `e7bd0cd5243ea7aa7e3eb565ed94b36f866f1c4c`
- **REQ:** `/workspace/ferry-inbox/REQ-shuttle-ferric-first-from-regolith-physics-2026-10-01.md`
- **Worker report:** `/workspace/ferry-inbox/regolith-physics-alkali-2026-10-01/ferric-first-report.md`
- **Date:** 2026-10-01 ~20:38–21:00 ET
- **Mode:** read-only for product code; extracts not edited; no Mac listen pools; no force-push. Targeted VPS unit tests only (no full W3 / PN2 e2e / staged C2A hero). Left `slot-z14` untouched.

## Scope

Ticket b-645: C3 Na/K shuttle reduces Fe₂O₃ → FeO (`2Na + Fe2O3 → Na2O + 2FeO`; K likewise) before any FeO → Fe, inside the **existing** extent rules (10 wt% alkali-oxide solubility cap; Na accessibility factor unchanged). Fe₂O₃ = 0 must be identical to base. Files: `engines/builtin/metallothermic_step.py`, `simulator/extraction.py` (availability + redox target lists), plus acceptance / redox-source tests.

## Attack results

### (1) Stoichiometry and Fe/O/Na/K atom balance — **PASS**

Per-reaction bookkeeping (mol extents):

| Step | Stoich | Atoms |
| --- | --- | --- |
| Ferric | `2 R + Fe2O3 → R2O + 2 FeO` | R, Fe, O close |
| Ferrous | `2 R + FeO → R2O + Fe` | R, Fe, O close |

Provider builds a single `METALLOTHERMIC_STEP` proposal: debit Fe₂O₃ / FeO (and reagent), credit R₂O (+ FeO from ferric) and metal Fe from the ferrous step only. Independent mass check with project `MOLAR_MASS` closes to ~1e−13–1e−14 g; new `test_c3_alkali_reduces_ferric_iron_before_feo` runs `_atom_check(..., tol=1e-12)` for both Na and K. Fixture (ferric=0.7, ferrous=1.5, dose=2.0): Fe₂O₃ debit 0.7, FeO debit 0.3, FeO credit 1.4, Fe metal 0.3, R₂O 1.0.

### (2) Cap covering the ferric step — dose budget double-count? — **PASS (no double-count)**

**K dose.** When `n_Fe2O3 > NOOP`, injection oxidant budget is `2·(n_Fe2O3 + n_FeO)` mol K — one O-transfer equivalent per **available** oxide molecule. Produced FeO from the ferric step is **not** added to that budget, so the dose does not double-count. With that inject, extents exhaust original Fe₂O₃→FeO and original FeO→Fe; leftover produced FeO remains (full Fe₂O₃→metal would need 6 K/mol Fe₂O₃ and is intentionally out of this cascade). K₂O solubility (10 wt%) still converts headroom → K via `2K/K2O`; K₂O produced = `n_Fe2O3_red + n_FeO_red` = inject/2 when oxidant-limited — consistent, not double-charged.

**Na solubility.** Ferric step uses net melt-loss ratio `(M_Fe2O3 − 2 M_FeO)/M_Na2O` for the first `_product_cap_kg_for_solubility`; after ferric, cap is recomputed with FeO loss ratio and updated removed/added mass before the ferrous extent. Na₂O credits remain on `spent_reductant_residue`. No double-spend of the 10 wt% headroom across the two sub-steps.

**Accessibility / 10 wt%.** Unchanged constants (`NA2O_SOLUBILITY_WT_PCT` / `K2O_SOLUBILITY_WT_PCT` = 10; Ti accessibility factor untouched).

Comment nit (non-blocking): K dose comment says the following FeO→Fe step “consumes the same 2:1 ratio” per Fe₂O₃ — true for **one** FeO molecule, not for the two FeO produced per Fe₂O₃; behavior matches the one-O-transfer dose design above.

### (3) Redox refresh seeing new Fe₂O₃/FeO — **PASS**

`ExtractionMixin` now passes `true_available_mol_by_species` including `Fe2O3` and calls `_apply_transition_redox_source_terms` with `target_oxides=('Fe2O3','FeO',…)` for both shuttles. Source-term helper is net O₂-equivalent on cleaned_melt debits−credits for those species, so FeO **credited** from ferric reduction correctly offsets:

`source = −(1.5·n_Fe2O3_debit + 0.5·n_FeO_debit − 0.5·n_FeO_credit)`.

Updated `test_c3_na_source_term_comes_from_committed_transition` seeds 0.7 mol Fe₂O₃ and asserts debit/credit + breakdown. Seat: C3 source-term filter **5 passed**.

### (4) Proposed ledger-contract amendment — **ACCEPT (document; code already does it)**

Tip already debits `process.cleaned_melt.Fe2O3` and credits `process.cleaned_melt.FeO` under existing `METALLOTHERMIC_STEP` / `DECLARED_ACCOUNTS` (accounts unchanged; species within cleaned_melt). Provider module account-declaration blurb already mentions ferric-reduction FeO credit. Endorse worker’s formal amendment: document that `METALLOTHERMIC_STEP` may debit Fe₂O₃ and credit FeO on `process.cleaned_melt`, with redox-source species selection tracking both alongside FeO→metal. Not a code REVISE blocker.

### (5) Two provider-file failures at base — **CONFIRMED (pre-existing)**

Tip `tests/chemistry/test_builtin_metallothermic_step_provider.py`: **64 passed, 1 skipped, 2 failed** (~11.5 min). Same two names fail on base `e7bd0cd52` (fresh worktree):

1. `test_c6_ci_empty_window_records_binding_refusal_without_transitions` — C6 refusal assertion (`assert all(...)` false).
2. `test_full_run_mass_balance_holds_with_kernel_committed_metallothermic[mars_basalt-additives_kg1]` — `OriginUnresolvedError` / `ProposalRejected`: partial withdrawal from mixed-origin `process.cleaned_melt.K` requires an explicitly amalgamated pool (evaporation path).

Not introduced by ferric-first; do not block LAND.

### (6) `rg` + targeted tests — **PASS**

Changed symbols (`mol_Fe2O3_*`, ferric-first extents, `target_oxides` Fe₂O₃, new tests) referenced from metallothermic provider tests, `test_sso_r_r20_state` C3 source terms, S4 shuttle broadening, redox authority floor.

| Suite | Result |
| --- | --- |
| New ferric + FeO-only shape + Na source-term | **4 passed** (~10 s) |
| C3 source-term filter (`c3_na_source` / `c3_k_source` / cr_ti / spent) | **5 passed** |
| Full `test_builtin_metallothermic_step_provider.py` | **64 passed, 1 skipped, 2 failed** (base-same) |
| `test_s4_shuttle_broadening.py` + `test_redox_authority_floor.py` | **63 passed** (~182 s) |

Not run on VPS (policy / cost): full W3, PN2 native-Fe e2e, staged C2A inventory-to-C3 hero, full mass_balance suite. Worker reported those green except the known provider-file pair; seat did not re-run heroes.

`test_c3_na_feo_only_transition_keeps_legacy_shape` pins Fe₂O₃-absent Na transition keys/extents; K path with `n_Fe2O3 ≤ NOOP` keeps legacy `K_for_FeO_kg` expression and emits no Fe₂O₃ debit / FeO credit.

## Non-blocking notes

- Ellingham gate for K (and Na Fe target) remains the FeO pair — conservative vs Fe₂O₃; within “existing extent rules,” not a new thermo model.
- Diagnostic `oxide_reduced_kg` sums Fe₂O₃+FeO **debits** and does not net out FeO credited from ferric; telemetry only.
- Staged lunar C2A→C3 Fe₂O₃ inventory at shuttle start still unmeasured (worker: 10 min timeout / continuous runner saw 0 mol Fe₂O₃ on a different path) — out of b-645 acceptance.

## Verdict

**LAND `5a1f5486e71229aedd724401d34a63fe78dba82d`**

Ferric-first Na/K stoichiometry, atom closure, non-double-counting dose/solubility caps, redox source terms on Fe₂O₃/FeO, and Fe₂O₃=0 legacy shape all hold. Provider-file failures reproduce at base. Ledger-contract wording amendment endorsed as docs follow-up.

— regolith-empirical
