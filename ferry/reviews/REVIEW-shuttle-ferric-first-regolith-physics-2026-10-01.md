# REVIEW — shuttle ferric-first (C3 Na/K, ticket b-645)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Tip:** `5a1f5486e71229aedd724401d34a63fe78dba82d` on `review/shuttle-ferric-first` (detached seat)
- **Range:** `e7bd0cd5243ea7aa7e3eb565ed94b36f866f1c4c..5a1f5486e71229aedd724401d34a63fe78dba82d` (one commit)
  - `5a1f5486e71229aedd724401d34a63fe78dba82d` Prioritize ferric iron in alkali shuttle
- **Parent confirmed:** `e7bd0cd5243ea7aa7e3eb565ed94b36f866f1c4c`
- **REQ:** `/workspace/ferry-inbox/REQ-shuttle-ferric-first-from-regolith-physics-2026-10-01.md`
- **Worker report:** `/workspace/ferry-inbox/regolith-physics-alkali-2026-10-01/ferric-first-report.md`
- **Date:** 2026-10-01 ~20:23–20:55 ET
- **Mode:** read-only for product code; extracts not edited; no force-push of feature branches. Targeted VPS unit tests only (no full W3 / PN2 e2e / staged C2A heroes).

## Scope

C3 Na/K shuttle: reduce `Fe2O3 → FeO` before any `FeO → Fe` (`2Na + Fe2O3 → Na2O + 2FeO`; K likewise), within **existing** extent rules (10 wt% oxide solubility caps, Ti accessibility 0.75, 1/3 inventory/hour). `Fe2O3 = 0` must stay identical to base. Redox source-term refresh must see new Fe2O3/FeO. Ledger-contract amendment: `METALLOTHERMIC_STEP` may debit Fe2O3 and credit FeO on `process.cleaned_melt`.

Files: `engines/builtin/metallothermic_step.py`, `simulator/extraction.py`, `tests/chemistry/test_builtin_metallothermic_step_provider.py`, `tests/test_sso_r_r20_state.py`.

## Attack results

### (1) Stoichiometry and Fe/O/Na/K atom balance — **PASS**

Reactions (mol extents `f` = Fe2O3 reduced, `g` = FeO reduced to metal):

| Step | Reaction | Debits | Credits |
| --- | --- | --- | --- |
| Ferric | `2 A + Fe2O3 → A2O + 2 FeO` | `2f` A, `f` Fe2O3 | `f` A2O, `2f` FeO |
| Ferrous | `2 A + FeO → A2O + Fe` | `2g` A, `g` FeO | `g` A2O, `g` Fe |

(A = Na or K; Na2O credits `spent_reductant_residue`, K2O credits `cleaned_melt` — unchanged routing.)

Combined proposal: Fe atoms `2f+g` close; O atoms `3f+g` close (O moves Fe2O3→A2O; FeO credit returns 2f O into melt). Seat probe at `(f,g)=(0.7,0.3)` for both Na and K closed to `1e−12`. New tests `test_c3_alkali_reduces_ferric_iron_before_feo` (param Na/K) + `_atom_check` assert the same.

Mass spot-checks in comments match MOLAR_MASS (e.g. `45.98+159.69 = 61.98+2×71.84`; `78.20+159.69 = 94.20+2×71.84`).

### (2) Existing extent rules unchanged; Fe2O3=0 identical to base — **PASS**

- Constants unchanged vs base: `K2O_SOLUBILITY_WT_PCT = NA2O_SOLUBILITY_WT_PCT = 10.0`, `TI_ACCESSIBILITY = 0.75`, inventory fraction `1/3` per hour.
- Fe2O3=0 K path: no Fe2O3 debit, no FeO credit, debit FeO / credit K2O+Fe only — seat probe + `test_c3_na_feo_only_transition_keeps_legacy_shape`.
- When `mol_Fe2O3_available ≤ OXYGEN_RESERVOIR_NOOP_MOL`, K dose uses the legacy `FeO_available_kg / (M_FeO/(2 M_K))` expression (else branch), not the ferric sum.

### (3) Dose budget / solubility cap — double-count? — **PASS (no double-count)**

**K dose (`K_for_FeO_kg`):** when Fe2O3 present, budget is `2·(n_Fe2O3 + n_FeO)·M_K/1000` — one 2:1 alkali step per mole of each oxide **present at start**. It does **not** also fund FeO→Fe on the FeO produced from Fe2O3. Seat: Fe2O3-only inventory under that budget yields `f=1`, `g=0` (FeO credited, Fe metal extent 0). So the dose does not double-count the same Fe atoms for both steps.

**Na solubility:** ferric extent capped with melt-loss factor `(M_Fe2O3 − 2 M_FeO)/M_Na2O`; then a **recomputed** cap after ferric (updated `removed_kg` / `added_product_kg`, FeO loss factor) limits ferrous. Sequential, not a second debit of the same Na2O headroom. Alkali `mol_Na` decremented once per atom used.

**K2O solubility** remains the pre-existing static headroom (`total_kg·10% − K2O_current`) converted at 2K/K2O — same structure as base FeO-only; K2O produced is still `f+g = mol_K_used/2`.

### (4) Redox refresh seeing new Fe2O3/FeO — **PASS**

`simulator/extraction.py` passes `true_available_mol_by_species` including Fe2O3 and sets `target_oxides=('Fe2O3','FeO',…)` into `_apply_transition_redox_source_terms`.

Source-term helper: `o2_equiv = credit_O2 − debit_O2` on cleaned_melt targets. For extents `f,g`: debit `1.5f + 0.5g`, credit `1.0f` (from `2f` FeO) → `o2_equiv = −0.5(f+g)`. Matches updated `test_c3_na_source_term_comes_from_committed_transition` (`fe2o3=0.7`, FeO credit `1.4`, expected `-(1.5f + 0.5g − 0.5·credit_FeO)`).

### (5) Ledger-contract amendment (METALLOTHERMIC_STEP may debit Fe2O3 / credit FeO) — **ACCEPT (code); docs follow-up**

Tip already emits Fe2O3 debits and FeO credits on `process.cleaned_melt` inside existing `DECLARED_ACCOUNTS` (no new accounts). Atom-balance proof + kernel commit path cover conservation. **Documentation** of the amendment (binding/ledger contract text) is **not** in this commit — accept as proposed non-blocking follow-up; not a REVISE blocker for b-645.

### (6) Two provider-file failures at base — **CONFIRMED (pre-existing)**

| Test | Tip | Base `e7bd0cd52` |
| --- | --- | --- |
| `test_c6_ci_empty_window_records_binding_refusal_without_transitions` | FAIL ~116 s — `fallback["recent"]` source/status assertion | **same FAIL** ~118 s |
| `test_full_run_mass_balance_holds_with_kernel_committed_metallothermic[mars_basalt-…]` | FAIL ~24 s — `OriginUnresolvedError: partial withdrawal from mixed-origin process.cleaned_melt.K` during evaporation | **same FAIL** ~25 s |

Not introduced by ferric-first. Lunar mass-balance param **passed** on tip (~383 s). Nightly C6 static-hold (MAGEMin live) skipped on VPS (binary unavailable) — out of scope.

### (7) `rg` + targeted tests — **PASS**

Changed symbols (`mol_Fe2O3_*`, ferric-first extents, `target_oxides` Fe2O3, new tests) concentrated in metallothermic provider + extraction shuttle injectors + SSO-R source-term test.

| Suite | Result |
| --- | --- |
| New ferric + FeO-only + Na source-term | **4 passed** |
| Seat stoich/atom/dose probes (Na+K) | **PASS** |
| `test_builtin_metallothermic_step_provider.py` (deselect 2 nightly heroes that are the known base fails; keep short body) | **63 passed, 1 skipped** (~95 s) |
| Lunar mass-balance nightly | **1 passed** (~383 s) |
| C6 empty-window + mars mass-balance | **2 failed — base-identical** |
| `test_sso_r_r20_state.py` (`c3_na_source` / shuttle / redox_source) | **9 passed** |
| `test_wave10_metallothermic_failclosed` + `test_s4_shuttle_broadening` | **14 passed** |
| `test_kernel_capabilities` / `test_extraction_ledger` (`metallothermic\|shuttle\|c3_`) | **5 passed** |

Not run (policy/cost): full W3, PN2 native-Fe e2e, staged C2A hero to C3, full redox_authority_floor, 170 h runs.

## Non-blocking notes

- K path writes `metal_phase.Fe = 0.0` when only the ferric step runs; harmless for atom balance / diagnostics.
- Module docstring / ledger-contract prose should record Fe2O3 debit + FeO credit (worker proposal).
- Comment on K dose mentioning “the following FeO→Fe step” is slightly easy to misread; budget is one step per starting oxide mole, not a full cascade.

## Verdict

**LAND `5a1f5486e71229aedd724401d34a63fe78dba82d`**

Ferric-first Na/K stoichiometry, atom closure, unchanged 10 wt% / accessibility rules, Fe2O3=0 legacy shape, non-double-counting dose/cap sequencing, and redox source-term refresh all check out. The two metallothermic-provider failures reproduce at base and are not blockers for this ticket.

— regolith-empirical
