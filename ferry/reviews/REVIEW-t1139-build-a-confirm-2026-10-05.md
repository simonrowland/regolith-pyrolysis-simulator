# FOCUSED CONFIRM (review of record): t-1139 Build A follow-ups

Reviewer: regolith-empirical (VPS seat). Requested by: regolith-physics (REQ-confirm-t1139-build-a-followups-from-regolith-physics-2026-10-05.md). Date: 2026-10-05, ~18:40 ET.
Reviewed sha: **0c80eef0c6bd561a6fbd30156c3168e982a31408** (11 commits a11d8f6ff..0c80eef0c). Prior review of record: LAND-WITH-FOLLOWUPS at a11d8f6ff (0×P0 4×P1 6×P2 6×P3).
Method: detached worktrees at 0c80eef0c, a11d8f6ff, 304b080c7/cabbd2820 and (addendum only) 3c56d0351. I read every hunk, re-ran the prior probes, and wrote new ones (in the mailbox commit, `ferry/reviews/t1139-build-a-confirm-probes/`). Only targeted tests were run on this 16 GB box, never the full suite. G1 (ulp stoich shift, 1e-12 tolerance) is owner-sanctioned (d-081), so I do not re-litigate it. G2 is the Studio suite.
Tags: **VERIFIED** = command output or file:line at 0c80eef0c. **INFERRED** = my reasoning.

## Verdict: **REVISE at 0c80eef0c**
Counts (new + residual): **0×P0, 3×P1, 1×P2, 11×P3**. New: 0/2/0/5. Residual from the review of record: 0/1/1/6.

The follow-ups themselves are good. Eight of the ten items are FIXED, P1-2 is PARTIAL and pin-first holds. Live pressures and the live legacy view are bit-identical to a11d8f6ff. **But 0c80eef0c carries two new P1 defects.** Both are mechanical, and both are already fixed on the rebased tip 3c56d0351 (see Addendum):
- **N-P1-1: a live-path regression outside the dormant code.** b5cee4ee4 renamed `simulator/battery/generators/janaf.py::_interpolate_formation_gibbs` → `_interpolate_janaf_table` (`janaf.py:2637`). The live caller `simulator/diagnostic_helpers/binary_pot_battery.py:1512` (`_cell_oxide_thermodynamics`, the reactive W/Mo cell oxygen reservoir, since d4ee436ed on 2026-09-28) still imports the old name.
  - VERIFIED (`cell_probe.py`, T = 1800 K):
    - a11d8f6ff: W gives buffer log10 = −7.915395737036834 and Mo gives −7.900625230013852.
    - 0c80eef0c: both raise `ImportError: cannot import name '_interpolate_formation_gibbs'`.
    - 3c56d0351: OK, with values identical to base.
  - The import is lazy, so `test_import_boundary` cannot catch it. On the VPS the reactive-cell tests that reach it already fail for an environment reason (the openimcc pin is not installed), and the others monkeypatch the function. The Studio run of `tests/test_openimcc_battery_engine.py::test_reactive_cell_adds_shared_janaf_oxides_to_engine_pressure_model` should catch it.
- **N-P1-2: the t622 evidence is stale on every platform.** ad4c3e866 and 892cff365 edit `simulator/vapour_rail/catalog.py`, but `validation-data/pin-evidence/t622_additivity_2026-08-12.yaml:28` still says `candidate_compiler_sha256: bac8e9bb…`. At 0c80eef0c, `sha256(catalog.py)` = `b766bfd8…`.
  - VERIFIED: `scripts/prove_t622_cross_revision_additivity.py --check --output <copy>` gives `FAIL: evidence is stale`. A regeneration on the VPS PASSes (229 pre-existing species, 2580 exact grid cases). It differs from the committed file only in the compiler sha, which is platform-independent, and in the grid sha, which is the known VPS-vs-Mac libm difference.
  - So equivalence holds and only a refresh is needed. Even so, `test_t622_cross_revision_additivity_evidence_is_reproducible` would be red on the Studio, so G2 cannot go green at this sha.
- **Residual P1 (P1-2 PARTIAL): the Burcat conversion still takes a premature gas reference.** NASA is fixed. Burcat has crystal-only element records that end at Tm, and the new rule ("gas only above condensed coverage", `source_rail.py:251-261`) therefore uses the gas reference from Tm upward.
  - Burcat elements with no condensed record at all also fall back to gas silently at 0c80eef0c. The rebased tip fixes that case (523d0af3f).
  - Dormant: no generated channel draws Burcat (25 NASA, 20 JANAF). Like the original P1-2, this blocks Build B, not dormant landing.

If the train lands the rebased tip instead of 0c80eef0c, N-P1-1 and N-P1-2 are gone (VERIFIED by probe). I did not fully review 3c56d0351; see the Addendum.

---

## Per-item status
| Item | Commit(s) | Status | Evidence |
|---|---|---|---|
| P1-2 inverted NASA interval; premature gas reference | dfca10963 | **PARTIAL** | `source_rail.py:674` skips a `t_min >= t_max` interval, so NG-1858 Si(cr) loads 298.15–1690 K (`si_probe.py`). `:251-261` raises `SourceCoverageGap` inside condensed coverage. NASA SiO(g) JANAF−NASA residual is now **−1.74/−1.79/−1.69** kJ/mol at 1400/1600/1800 K (was +241.8/+213.0/−1.7). Si(g) is −0.19/−0.25/−0.15. `fallback_scan.py` finds 0 NASA fallbacks. The interval skip admits 10 records (Ca a, CrN, FeCl3, FeOCl, Fe3O4, Li cr, NH4F, Si cr, Ti3O5 a, U3O8 II), all with the same 300→298.15 (or 300→300) artefact and contiguous remaining intervals, so it is sound. **Residual: Burcat** (`gasref_probe.py`, `burcat_probe.py`). Condensed coverage ends at Tm for Fe 1042, Cu 1358, Si 1690, Ge 1211, Ni 1728, Ca 1115 K, and also Co, Ag, Au, Bi, Th, Zr. JANAF−Burcat ΔfG: Fe(g) **+209/+182/+156**, Cu(g) +157/+135/+113, FeO(g) +209/+182/+156, Si(g) at 1800 K +189 kJ/mol. With no condensed record (Li, Mn, Sn, V, Hg, Br2), 0c80eef0c silently uses gas: Mn(g) +97/+74/+53, V(g) +303/+274/+245. Fix: gas only above the element's boiling point (JANAF `ref` convention), or only once a condensed **liquid** record's coverage has ended. Otherwise raise `SourceCoverageGap`. |
| P1-3a JANAF O2 / gas-only refs at ΔfG = 0 | 32070d527 | **FIXED** | `source_rail.py:84` plus `_janaf_gas_only_reference`. `test_janaf_o2_reference_is_zero_by_definition` (`tests/test_t1139_source_rail.py:262`) checks O-029 (ref → gas, `gibbs_defined_zero`), and that Br2 and condensed element refs are still skipped. `mix_probe2.py`: JANAF O2 gas count 1. Channel sources are now **20 JANAF + 25 NASA** (the REQ says 18; see N-P3-4). |
| P1-3b furnace-window domains (NASA liquids below Tm, flagged) | 77d9d978f | **FIXED** | `source_rail.py:716` `supercooled_liquid_from_crystal` shifts b1/b2 (NASA-9) or a6/a7 (NASA-7) so that G_ext(Tm) = G_l(Tm) with ΔCp = 0 below Tm. I checked the algebra: G is continuous at Tm, and H and S carry the liquid's fusion step. It sets the `supercooled_liquid_extension`, `melting_temperature_K` and `supercooled_crystal_record_id` flags, plus `liquid_parent_extension` on the row (`channel_generator.py:206`). `gen_probe.py`: all 45 domains contain 1600 K, and 24 channels are extended (Ga, In, Sn and Cs from 300 K, Rb from 613 K, Ge from 1308 K, plus V4O10). Tests at `:141` and `:328` pass. Caveat N-P3-5 (unbounded extension span). |
| P1-4 one Gibbs basis and one gas P° per reaction | ad4c3e866 | **FIXED** | `catalog.py:2921` `_coefficient_gibbs_basis` checks the label against the family. `catalog.py:3054-3080` enforces one basis per reaction and gas P° = Pstd. Condensed 1-atm labels are exempt; the live CEA condensed rows carry them, and condensed VΔP is neglected. NASA/Burcat payloads are labelled with the native convention (`source_rail.py:325`). Probes: a mixed JANAF/NASA basis is refused (`mix_probe2.py`, was p/p0 = 4.2e20); B2O3(g) at 101325 Pa is refused; B2O3(l) at 101325 compiles; B2O3(l) at 2e5 is refused (`p0_probe.py`). `test_compiler_rejects_mixed_gibbs_basis_and_pressure` (`:174`) passes. |
| P2-1 one ΔfG law, one diatomic map | cabbd2820 | **FIXED** | `source_rail.py:186` `formation_gibbs_from_absolute` and `:68` `DIATOMIC_GAS_REFERENCE`. `species_rail_differential.py:82,595-597,667-670` uses both. The CEA-key resolver stays in the differential, which is justified. Bit-check (`conv_dump.py`): 875 NASA/Burcat ΔfG values (175 records × 5 T) at a11d8f6ff vs cabbd2820 → **0 differ**. The differential's pin (`test_cea_formation_gibbs_pin`) passes. |
| P2-3 one tabulated interpolator, missing-node refusal kept | b5cee4ee4 | **FIXED** (with the regression N-P1-1) | `tabulated_gibbs.py:66` is the single interpolator, and its missing-node refusal is at `:38-63`. The battery calls it at `janaf.py:32,2637,2699`, keeping the refusal and the "outside the JANAF table range" wording. `TabulatedThermo.missing_nodes` is at `:118,179`. The JANAF fusion pins pass. Latent gap N-P3-1: the compiled `tabulated_janaf` path drops `missing_nodes`. **The rename broke the live caller (N-P1-1).** |
| P2-4 CatalogCompileError for the derived species | 892cff365 | **FIXED** (error path) | `catalog.py:3948-3967` raises on an unmatched `source_reaction_id` or a derivation `ValueError`, for the 34 `CATALOG_DERIVED_STOICH_SPECIES`. `test_derived_species_refuses_an_unmatched_source_reaction` (`:183`) passes, and the live catalog still compiles. The relocation onto compiled species (d-062 Q2b) is not done (residual P3). |
| P2-5 Sn parent is SnO | 279ca11e4 | **FIXED** | `channel_generator.py:55`. NG-1888 SnO(l) is 1250 K up, and the extended domain starts at 300 K. `test_sn_parent_is_sno` and `test_sn_oxide_liquids_support_both_valences` pass. The `transport_headspace` / `pO2_reference_bar: 1.0` part (`channel_generator.py:236-237`) remains a Build B carry (residual P3). |
| P2-6 one balancer | 433ce8ca8 | **FIXED** | `stoich.py:57` `balance_oxide_evaporation` is called from `channel_generator.py:331` with `catalog._formula_atoms`. The generator's own balancer is gone. The left-in-place copies are listed with reasons in the commit message (tools/compose, sgte_unary, scripts manifest, evaporation runtime). |
| P3-5 zero t1139 ids live | 0c80eef0c | **FIXED** | `tests/test_t1139_build_a_pins.py:412` walks the compiled catalog with U0 rules, legacy_view, `vapor_pressure_legacy_view` and the config bundle. It passes. |
| Pin-first | 304b080c7 | **FIXED** | 304b080c7 sits directly before cabbd2820, b5cee4ee4 and 433ce8ca8. Its 9 pins pass at 304b080c7 and at 0c80eef0c. The only later edit (433ce8ca8) re-points the Ga2O3 call site and leaves the asserted values unchanged. Gap: there was no pin on the second consumer of the renamed interpolator, which is how N-P1-1 slipped through (see d-062 Q4). |

### Earlier findings not listed in the REQ
- **P1-1** (ulp stoich shift, pin loosened in the move commit): **closed by owner sanction** (G1, d-081). VERIFIED: it is unchanged. The original b8f3a068f pin file on 0c80eef0c still gives **16 failed / 106 passed**, on the same 16 rows.
- **P2-2** (Pankratz atm→bar is species-only, `source_rail.py:141-155` and its call at `:1062`): **open**, unchanged (residual P2).
- **P3-1** `PREFERRED_CARRIERS` is dead (`channel_generator.py:70`; only tests read it): **open**.
- **P3-2** pre-existing decimal-subscript misparse: **open** (not a regression).
- **P3-3** t622 commit message: historical. It is superseded by N-P1-2.
- **P3-4** per-record JANAF kink across element transitions: **open** (inherent; needs a doc note).
- **P3-6** `_janaf_skip_reason` reads `name` (`source_rail.py:560`), a dead title branch; OCR-garbled Pankratz keys; pure_phase_janaf_score kernel: **open**.

## Required checks
- **Live pins identical to base: YES.** VERIFIED (`dump_catalog.py`, `diff.py`, `repr_norm.py`):
  - 0c80eef0c vs a11d8f6ff: 231 = 231 species. All 231 × 6 T × 3 pO2 pressures are identical as float.hex. The compiled reprs are identical once addresses are normalized. **legacy_view has 0 diffs.**
  - 0c80eef0c vs base e702b2551: pressures and reprs are identical. legacy_view has the same 65 diffs as before (the G1 stoich ulps, int→float, and the dropped provenance strings).
  - The pin file's PRESSURE_PINS and STOICH_PINS blocks are untouched; 0c80eef0c only appends a test.
- **Nothing moved outside the dormant path: NO.**
  - (a) N-P1-1: the battery rename broke the live `binary_pot_battery` caller.
  - (b) Intended and behaviour-neutral: ad4c3e866 and 892cff365 add compile-time guards to the live `catalog.py`. The live output is unchanged.
  - (c) The live `catalog.py` now imports `source_rail` at module load (`catalog.py:70`) for three constants. That pulls burcat, janaf, nasa_glenn and the pankratz loader into every catalog import: +~10 ms, within noise (N-P3-2).
- **No new defect: NO**, see N-P1-1 and N-P1-2, plus the P3s below.

## d-062 reviewer checklist
1. **Second copy of added or edited logic anywhere? YES (small residuals).** Resolved: the ΔfG law (one, `source_rail.py:186`), the gas-diatomic map (one, `:68`), the interpolator (one, `tabulated_gibbs.py:66`) and the generator balancer (one, `stoich.py:57`). `rg` for the old names (`_interpolate_formation_gibbs`, `_missing_node_for_interpolation`, `_element_reference_g_j_per_mol_atom`, `_balance_oxide_reaction`, `_DIATOMIC_STANDARD_FORMULA`, `_DIATOMIC_GAS_REFERENCE`) finds only the dangling caller `binary_pot_battery.py:1512,1542`. Copies that remain:
   - `_GAS_ONLY_REFERENCE_FORMULAS` (`source_rail.py:84`) restates `DIATOMIC_GAS_REFERENCE.values()` ∪ `_NOBLE_GAS_ELEMENTS` (`:68`, `:79`).
   - The JANAF blank-node reader exists twice: `source_rail._janaf_points` (`:512-534`) and `battery/generators/janaf.py:2612` `_fusion_missing_gibbs_temperatures`.
   - `channel_generator` counts atoms with both `parse_formula` (`:131`, `:137`) and the private `catalog._formula_atoms` (`:17`, `:334-335`).
   - The documented balancer leftovers.
   - The pure_phase_janaf_score kernel (P3-6, deferred).

   `rg -i "supercool|s_fus|fusion_entropy"` finds no second liquid-from-crystal construction. `melt_activity_resolver.py:506` is a label id only.
2. **Rule, threshold or physics in a presentation or wiring layer? YES (one residual, pre-existing).** The basis and P° rule now lives in the compiler owner (`catalog.py:2921`, `:3054-3080`), which closes 2a. Derived stoich is still computed inside the legacy projection `_legacy_species_row` (`catalog.py:~3930-3975`), so 2b is open.
3. **New import crosses a forbidden layer or creates a cycle? NO.**
   - New edges: `battery`(3) → `vapour_rail.tabulated_gibbs`(1); `diagnostic_helpers`(3) → `vapour_rail.source_rail`(1); `vapour_rail.catalog` → `vapour_rail.source_rail` and `channel_generator` → `catalog`, both inside layer 1.
   - No cycle: source_rail does not import catalog, catalog does not import channel_generator, and vapour_rail, reference_data and accounting do not import battery.
   - `tests/import_layers.toml` and `tests/import_boundary_baseline.json` are unchanged. `pytest tests/test_import_boundary.py` → **14 passed**.
   - The dangling lazy import (N-P1-1) is not a layering violation, but the boundary test does not see it.
4. **Every behaviour-preserving move backed by a pin committed before the move? PARTIAL.**
   - 304b080c7 comes before all three moves. Its pins pass on both sides, and cabbd2820 is bit-identical over 875 rail values.
   - But b5cee4ee4 had a second consumer of the moved interpolator, `_cell_oxide_thermodynamics`, with no pin, and that consumer broke.
   - 433ce8ca8 edits the pin's call site inside the move commit. The values are unchanged, so this is acceptable.
5. **Relaxed a guard, added a baseline entry, or moved code into a module whose guards are already relaxed? NO.**
   - No baseline or layer entries were added.
   - The guards were tightened: basis/P°, CatalogCompileError for derived stoich, and the gas-reference gap.
   - The condensed-1-atm exemption is a justified scoped carve-out inside a new guard (`catalog.py:3063`).
   - The NASA interval skip narrows a loader refusal only for the documented inverted or zero-width artefact (10 records, all verified).
   - The battery `except TabulatedDomainError: pass` (`janaf.py:2707`) behaves the same as the old `None` return out of range.
   - Not a relaxation, but a red pin: the t622 digest was not refreshed (N-P1-2).

## Findings (new and residual)
**P0:** none.

**P1**
1. *(new)* N-P1-1: the rename broke `binary_pot_battery._cell_oxide_thermodynamics` (`:1512`) with an ImportError for W/Mo cells. Fix: keep the `_interpolate_formation_gibbs` name, as 0bcbcd857 on the rebased tip does, and pin one W and one Mo `_cell_oxide_thermodynamics(…, 1800)` value.
2. *(new)* N-P1-2: the t622 `candidate_compiler_sha256` is stale (`bac8e9bb…` committed vs `b766bfd8…` actual). Fix: regenerate on the Mac (523d0af3f does this).
3. *(residual, P1-2)* The Burcat gas reference is used from crystal Tm, off by 100–300 kJ/mol (Fe, Cu, Si>1690, Ni, Ca, Ge, …). At 0c80eef0c it is also used silently when there are no condensed records. This blocks Build B. Fix as in the table above.

**P2**
1. *(residual, P2-2)* The Pankratz atm→bar shift is species-only.

**P3**
1. *(new)* N-P3-1: the compiled `tabulated_janaf` (`catalog._polynomial_from_thermo_record`) does not carry `missing_nodes`, so a compiled channel would interpolate across a blank. Today's channel participants have blanks only at 100/200 K, below the first printed node, so there is no effect. 523d0af3f fixes it.
2. *(new)* N-P3-2: the live `catalog.py` imports the dormant `source_rail` for `ATM_PRESSURE_PA` and the `GIBBS_CONVENTION_*` constants. Move them to a leaf module.
3. *(new)* N-P3-3: the three small duplicates from d-062 Q1: the gas-only set, the blank-node reader, and the two atom parsers in channel_generator (including a private cross-module import).
4. *(new)* N-P3-4: the REQ and worker say "JANAF sources 18 of 45". I measured **20** (B×7, Pb/PbO, Cu×3, V/VO/VO2, Li×5). This is a claim accuracy issue only.
5. *(new)* N-P3-5: the supercooled extension (ΔCp = 0) runs to the crystal's T_min, as far as 1780 K below Tm for Ga2O3. It is flagged and harmless while dormant. Build B should cap it or carry an extrapolation-span notice.
6. *(residual)* P2-4b: move the derived stoich onto the compiled species (d-062 Q2b).
7. *(residual)* P2-5b: `transport_headspace` / `pO2_reference_bar: 1.0` must not reach Build B.
8. *(residual)* P3-1: `PREFERRED_CARRIERS` is dead.
9. *(residual)* P3-2: decimal-subscript misparse (pre-existing).
10. *(residual)* P3-4: per-record JANAF transition kink (doc).
11. *(residual)* P3-6: `_janaf_skip_reason` `name` branch, OCR Pankratz keys, and the pure_phase kernel.

## Tests run (VPS, `/tmp/admit-review-venv`, `-o addopts=""`, no xdist, targeted only)
| Command @0c80eef0c unless noted | Result |
|---|---|
| `pytest tests/test_t1139_build_a_pins.py tests/test_t1139_source_rail.py tests/test_t1139_channel_generator.py tests/test_t1139_consolidation_pins.py` | **192 passed** (14.9 s) |
| `pytest tests/test_import_boundary.py` | **14 passed** |
| `pytest tests/battery/test_janaf_generator.py tests/chemistry/test_species_rail_differential.py` | **76 passed, 3 skipped** |
| `pytest tests/test_janaf_compilation.py tests/chemistry/test_builtin_evaporation_transition_provider.py` | **67 passed, 1656 skipped** |
| `pytest tests/test_t1139_consolidation_pins.py` @304b080c7 | **9 passed** |
| original b8f3a068f pin file @0c80eef0c | **16 failed, 106 passed** (G1 rows, unchanged) |
| `pytest …::test_t622_cross_revision_additivity_evidence_is_reproducible` | **FAILED**: the evidence is stale (compiler sha, all platforms) and the grid sha differs (VPS-only) |
| `prove_t622… --check` / regen to tmp | `FAIL: evidence is stale` / regen **PASS** 229 species, 2580 exact cases |
| `pytest tests/test_openimcc_battery_engine.py -k reactive_cell` | 2 passed, 2 failed at **both** a11d8f6ff and 0c80eef0c (env: openimcc pin missing). Not diagnostic; see `cell_probe.py` |
| t1139 test set @3c56d0351 (addendum) | **196 passed** |

## ASK for regolith-main (Mac Studio, G2)
Run G2 on the sha that will land. For 3c56d0351 that means: the full suite plus goldens, `test_t622_cross_revision_additivity_evidence_is_reproducible`, and `tests/test_openimcc_battery_engine.py -k reactive_cell` with the openimcc pin installed. N-P1-1 shows only there.

---

## Addendum: rebased origin/review/t1139-build-a @ 3c56d0351 (quick look, not a full review)
- **Range-diff** `a11d8f6ff..0c80eef0c` vs `35cf3b1e0..94367de5a`, where 35cf3b1e0 is the rebased a11d8f6ff:
  - The pairing is 1:1.
  - **10 of 11 commits are `=` (patch-identical).** The original Build A 5 are also all `=` (b8f3a068f…a11d8f6ff ↔ 7f15e423d…35cf3b1e0).
  - The only changed commit is **b5cee4ee4 → 0bcbcd857**, resolved against green's 173156476. It **keeps the `_interpolate_formation_gibbs` name**, because the cell-oxide caller imports it, and it keeps the JANAF grid policy `_missing_node_for_interpolation` in janaf.py.
  - Per-file patch-ids of the cumulative series diff are equal for every file except `simulator/battery/generators/janaf.py`.
  - **Equivalence verdict:** the rebased series is equivalent except in janaf.py, and that difference removes N-P1-1. VERIFIED: `cell_probe.py` @3c56d0351 gives W −7.915395737036834 and Mo −7.900625230013852, identical to base.
- **523d0af3f** "refuse a gas reference with no condensed coverage":
  - No condensed records now raises `SourceCoverageGap`. VERIFIED: Burcat Li, Mn and V go from silent gas to a gap.
  - JANAF `missing_nodes` now travel on species_thermo, and the catalog's tabulated compiler validates and rebuilds them (fixes N-P3-1).
  - The t622 `candidate_compiler_sha256` is refreshed to `b7b39d0a…`, which equals `sha256(catalog.py)` @3c56d0351 (fixes N-P1-2).
  - **It does not fix the Burcat crystal-ends-at-Tm case.** VERIFIED @3c56d0351: Fe(g) is still +209/+182/+156, Cu(g) +157/+135/+113, Si(g) at 1800 K +189 kJ/mol. The residual P1 stands for Build B.
- **3c56d0351** "refresh t1139 fusion crossings onto green JANAF": it re-reads two pin values in `tests/test_t1139_consolidation_pins.py`. Al2O3 Tm goes 2325.9319 → 2326.5285 K and MgO 3104.9456 → 3104.9682 K; the ΔG_fus pins are unchanged. This is attributed to green's missing-node overlap filter (173156476), not to this series. The landing reviewer should confirm that green's own JANAF fusion tests moved the same way.
- I expect 3c56d0351 to be LAND-able subject to G2 and a short confirm of 523d0af3f, with the Burcat residual carried to Build B. **This is not a verdict of record on 3c56d0351.**
