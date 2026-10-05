# REVIEW OF RECORD: t-1139 Build A (dormant generic evaporation-channel generator)

Reviewer: regolith-empirical (VPS seat). Requested by: regolith-physics. Date: 2026-10-05, ~05:20 ET.
Branch: `origin/review/t1139-build-a` @ **a11d8f6ffbd6b6c28eaa0849dc86a6b2f4e43e84**. Base: green **e702b255107e0f34c507c045688ec57062f6d55f**.
Commits reviewed: b8f3a068f (pins), 5a41afc68 (tabulated JANAF, source rail, derived stoich), 02032614c (generator), 4b6e0aef9 (t622 evidence), a11d8f6ff (ΔfG conversion).
Method: I made detached worktrees at a11d8f6ff and e702b2551, read every diff hunk, and ran probes (the scripts are in the mailbox commit, `ferry/reviews/t1139-build-a-review-probes/`). I compiled the live catalog at base and at head and diffed them. I ran only targeted tests on this 16 GB box; I did **not** run the full suite (ASK below).
Tags: **VERIFIED** = command output or file:line at a11d8f6ff. **INFERRED** = my reasoning.

## Verdict: **LAND-WITH-FOLLOWUPS at a11d8f6ff** (gated)
Build A is dormant. Nothing in production imports `source_rail` or `channel_generator`. Live compiled species and every live pressure are bit-identical to base. The only live numeric change is in ledger stoichiometry: 16 of the 34 rows now derived from source_reactions moved by at most 4.7e-15 kg/kg. Two gates apply:
- **(G1)** The owner sanctions that ulp-level ledger shift, together with the pin loosening in 5a41afc68 (P1-1).
- **(G2)** regolith-main gets a green full suite, goldens, and the t622 test on a Mac Studio.

If either gate is refused, the verdict becomes **REVISE**. P1-2 through P1-4 do not block landing dormant code, but they **block Build B (enable)** and should be the first items there.

Count: 0×P0, 4×P1, 6×P2, 6×P3.

## FOLD.md: ABSENT
The bundle `/workspace/ferry-inbox/regolith-physics-t1139-review-2026-10-05/` contains only REQ, design.md, review-brief.md and our REVIEW. `git log --all -- '*FOLD*'` is empty. `rg -l FOLD` over the a11d8f6ff tree hits only unrelated files, plus the docstring at `simulator/vapour_rail/source_rail.py:10` ("FOLD item 3"). The a11d8f6ff message also cites "FOLD item 3 / sol P1-1". So I judged scope against the REQ's own description of Build A (the tabulated JANAF evaluator from our P0-2, source rail, derived stoich, and the multi-carrier steer from our P0-1), not against FOLD.md's text.

---

## The six checks

### (1) Pins byte-identical; live compiled-species hashes unchanged; t622 refresh evidence-only: **PARTIAL**
- VERIFIED: the **pressure** pins are byte-identical across all five commits. The `PRESSURE_PINS` and `STOICH_PINS` blocks of `tests/test_t1139_build_a_pins.py` diff empty between b8f3a068f and a11d8f6ff.
- VERIFIED: the pin **file** is not byte-identical. It is sha `31e2a95e…` at b8f3a068f and `bc7037f8…` at 5a41afc68, 02032614c, 4b6e0aef9 and a11d8f6ff. 5a41afc68 turned the stoich assertions from exact `==` into `pytest.approx(..., rel=0.0, abs=1e-12)` (`tests/test_t1139_build_a_pins.py:307-317`), in the same commit as the move.
- VERIFIED: the original b8f3a068f pin file run against head code gives **16 failed, 106 passed**. The failures are the stoich rows Al2O, AlO, AlO2, Ca2, CrO, CrO2, CrO3, K2, Mg2, Na2, P4O6, PO, Si, Si2, Si3 and TiO. The same file at base gives 122 passed.
- VERIFIED: I compiled the live catalog at e702b2551 and at a11d8f6ff (`dump_catalog.py` + `diff.py`).
  - Species: 231 vs 231.
  - Compiled-dataclass reprs: identical once function addresses are normalized.
  - Pressures: all 231 species × 6 temperatures (1200–2200 K) × 3 pO2 values are identical as float.hex.
  - `legacy_view` has 65 differences, all of three kinds:
    - The 16 stoich values above moved by ≤4.66e-15 absolute. The largest is TiO `stoich_oxide_per_vapor`, 1.25050887796324 → 1.2505088779632354 (3.7e-15 relative).
    - Five integer 1/0 values became floats 1.0/0.0.
    - The `stoichiometry_derivation` provenance strings were dropped for the 34 rows. No Python reads them (`rg stoichiometry_derivation` finds nothing).
  - `legacy_view` feeds the runtime ledger (`simulator/evaporation.py:4308-4350`, `simulator/core.py:1060`). So the claim of "no numeric output changes" is false at the ulp level for runs that flux Si, TiO, AlO, Na2, K2 or Mg2.
- t622 (4b6e0aef9): **evidence-only, VERIFIED, with caveats.**
  - The refreshed `candidate_compiler_sha256` `bac8e9bb…` equals `sha256(simulator/vapour_rail/catalog.py)` at head.
  - I regenerated the evidence on the VPS (`scripts/prove_t622_cross_revision_additivity.py --output …`): PASS, 229 pre-existing species, 2580 exact grid cases. The compiled-dataclass sha `87bb62b4…` is the same for baseline and candidate, and matches the committed file.
  - The only regen-vs-committed difference is `baseline/candidate_evaluation_grid_sha256` (VPS `2c54e175…` vs committed `9ae7d758…`). Baseline equals candidate on each machine, so this is platform float/libm dependence, not branch drift. For the same reason the pytest `test_t622_cross_revision_additivity_evidence_is_reproducible` **fails on the VPS**, so it must be judged on a Mac Studio.
  - Caveat: the evidence was already stale at **base**. Base evidence `7c86eebb…` is the sha of catalog.py at bc90b2c04, but base catalog.py is `cab91035…`. Two commits changed it in between: 94183bc69 (2026-09-26) and 1b0528103 (2026-10-03). The refresh therefore also absorbs those two changes. The re-run proof covers them, so this is legitimate, but the commit message understates it (P3-3).

### (2) tabulated_janaf interpolation, one-P°-per-reaction rule, 1 atm→1 bar shift: **interpolation OK; the P° rule is not enforced by the compiler; the shift is correct at reaction level only**
- **Interpolation** (`simulator/vapour_rail/tabulated_gibbs.py:34-52`, `:121-136`): linear in ΔfG(T) on the printed grid, refuses out-of-range values, requires a strictly increasing grid (`:83-101`).
  - VERIFIED: away from element transitions the midpoint error versus a Hermite interpolant (slopes from the table's own ΔfH, i.e. dΔfG/dT = (ΔfG−ΔfH)/T) is ≤4 J/mol (Ga-005, 1200–2400 K).
  - VERIFIED: compound tables print no node at element transitions (Cu-005 and Li-015 have no 1358/1615 K rows). Per-record error there reaches 0.13 kJ (Cu-005, Cu melting), 0.54 kJ (B-098, B melting) and 2.2 kJ/mol (Li-015 1600–1700 K, Li boiling).
  - INFERRED: interpolation is a linear operator and these kinks cancel in ΣνΔfG, so reaction-level ΔrG is accurate when all participants share a grid. Only per-record comparisons (`compare_g_over_overlap`) see the kink error (P3-4).
  - VERIFIED: no current JANAF table has an interior blank ΔfG node (scan: 0). There is still no guard against interpolating across one, which the battery copy has (`simulator/battery/generators/janaf.py:2648-2660`) (P2-3).
- **One P° per reaction:** enforced only where records enter the rail (every record is stored at `STANDARD_PRESSURE_PA`, `source_rail.py:45`) and in the generator. **The catalog compiler does not enforce it.** VERIFIED probe (`mix_probe.py`): I set one participant's `reference_pressure_Pa` to 101325 in the generated B2O3 family. It compiled, and p/p0 = 1.0, meaning the record's P° is silently ignored. Each record is compiled independently (`catalog.py:3015-3030`); only the model-level `Pstd` (`catalog.py:2984`) is used. → P1-4.
- **1 atm→1 bar** (`source_rail.py:135-149`, applied to Pankratz B677 gases only at `:840-846`): the sign and magnitude of the per-gas shift RT ln(1 bar/1 atm) are correct. JANAF 4th, NASA Glenn and Burcat need no shift (all 1 bar).
  - The shift is applied to the **species only**, not as Δν_g(formation)·RT ln(p2/p1). Each Pankratz ΔfG therefore differs from the true 1-bar ΔfG by −Σν_el,gas·RT ln(1 bar/1 atm). Example: B2O3(l) should move by +0.30 kJ/mol at 1800 K but moves by 0.
  - INFERRED: within a single-source reaction the element terms cancel, so ΔrG is still correct. Pankratz has no O2(g) key (probe: 1178 keys, none O2), so it is only ever chosen for congruent reactions, where Δn_gas is fully covered.
  - The repo already has the correct Δν_g form at `simulator/battery/compilation_tier.py:505-515`. → P2-2.

### (3) ΔfG conversion uses the SAME compilation's element references, including CEA allotropes (B(b)): **YES for B(b); NO in one case: Si silently falls back to the gas reference**
- VERIFIED: `FormationGibbsThermo` (`source_rail.py:241-272`) resolves element references through the **same** `_CompilationIndex` (`_with_formation_convention(record, self)`, `:340-342`; `_element_reference_g_j_per_mol_atom(index, …)`, `:201-238`). Allotropes map to condensed_solid (`:82-84`, `:109-119`).
- VERIFIED (`ref_probe.py`): at 1400/1600/1800 K the boron reference is NG-1293 `B(b)` (300–2350 K). Ga, Cu, Pb, Li, Sn, Ge, In, Rb, Cs and Al resolve to their L records; V to cr; Fe to c/d; Ti to b; C to gr; O to the O2 gas record. The NASA O2 converted ΔfG is 0 (test passes).
- **Bug, VERIFIED:** NASA `Si(cr)` NG-1858 fails to load, because thermo.inp publishes an inverted first interval, 300→298.15 K, which the manifest lists as an ambiguity. `_nasa9_from_document` then returns None (`source_rail.py:596-597`). `_element_reference_g_j_per_mol_atom` silently falls back to **Si(g)** (`:226-231`) whenever no condensed record covers T. Effect on converted NASA ΔfG versus JANAF: SiO(g) **+241.8 / +213.0 kJ/mol** at 1400/1600 K, Si(g) +243.3 / +214.5. At 1800 K Si(L) covers and the residual is −1.7 / −0.15. A scan of all NASA elements finds Si is the only element hit. There is no runtime consumer today (generated families carry raw single-source coefficients), but this is the conversion a11d8f6ff exists to provide. → P1-2.
- The a11d8f6ff residuals reproduce exactly (VERIFIED, `pytest -s`): Ga(g) −0.455/−0.479/−0.496; Cu(g) +0.225/+0.232/+0.239; B2O3(g) +0.982/+1.070/+1.152; B2O3(l) +2.480/+2.711/+2.906; B2O3(l)→B2O3(g) −1.497/−1.641/−1.753 kJ/mol. The test samples no Si, Al or Fe species, which is why it missed the bug.

### (4) Derived stoichiometry equals the 34 retired rows; AlO1.5 parsing does not break hydrates: **equal to ≤4.7e-15 (not bit-identical); hydrates unchanged**
- VERIFIED: base YAML declares stoich for 34 species and head declares 0. The retired set equals `CATALOG_DERIVED_STOICH_SPECIES` exactly (`retired.py`; `stoich.py:17-54`). All 34 values agree within abs 4.66e-15, and 16 are not bit-identical (see check 1). `oxide = 1 + O2` holds (`stoich.py:116-121`).
- Hydrates, VERIFIED (`parse_probe.py`, head vs base): H2SO4.2H2O, CaSO4.2H2O, MgSO4.7H2O, CuSO4·5H2O, Na2CO3.10H2O and Al2O3.2SiO2.2H2O parse identically. AlO1.5, CuO0.5, GaO1.5, InO1.5, NaO0.5, Ca0.5Mg0.5SiO3 and Fe0.95O now parse; at base they raised. The fallback runs only after the hydrate split fails (`simulator/accounting/formulas.py:245-260`), so a string like H2SO4.2H2O can never be re-read as a decimal subscript.
- Pre-existing, not a regression: a decimal subscript followed by more elements still misparses whenever the hydrate split succeeds. "AlO1.5Si" → {Al:1, O:1, Si:5} and "O1.5Al" → {Al:5, O:1}, the same at base. (P3-2)
- Robustness: `catalog.py:3901-3902` swallows `ValueError`, and `AccountingError`/`UnknownSpeciesError` subclass it. A future data edit that breaks derivation therefore silently drops the stoich. For `Si` that falls back to `STOICH_RATIOS` (`evaporation.py:4352-4365`); for the other 33 it raises only at run time. `:3891-3893` falls back to the first reaction when `source_reaction_id` does not match. → P2-4.

### (5) Generated channels never enter the live catalog: **YES**
- VERIFIED: the compiled live catalog (`emit_u0_request_rules=True`) has 231 species and **0** `t1139` ids. `legacy_view` has 0; `load_config_bundle().vapor_pressures` has 0 occurrences.
- `rg` finds no importer of `source_rail`, `channel_generator` or `tabulated_gibbs` outside their own modules and tests. The only exceptions are `catalog.py:70-77`, which imports `stoich` and `tabulated_gibbs` but not the generator.
- No `t1139_` string appears in `data/`. Generated rows are `flux_dormant: True`, `request_rule: dormant_pending_validation`, α `no_data`/`refuse_nonzero_flux` (`channel_generator.py:216, 262, 270-274, 283`).
- The only guard is the species-count pin (231); there is no explicit "zero t1139_* live" test. → P3-5.
- Generator output, VERIFIED: **45 channels, 259 gaps** (226 `non_oxide_carrier`, 33 `metaborate`), as claimed.

### (6) d-068: do `pure_phase_janaf_score.py` and `species_rail_differential.py` duplicate the new conversion? **species_rail_differential: YES, a true second copy. pure_phase_janaf_score: the arithmetic kernel only.**
- `simulator/diagnostic_helpers/species_rail_differential.py:621-669` `cea_delta_fG_kJ_mol` implements the same law: ΔfG = G_sp − Σ(n_el/n_std)·G_el_std from CEA polynomials. It also has its own diatomic map `_DIATOMIC_STANDARD_FORMULA` (`:187-193`), which duplicates `source_rail.py:67-73`. Its reference resolver `_elemental_cea_entry` (`:600-619`) is in fact **stricter**: it gives a typed refusal on ambiguity or no cover where source_rail silently falls back to gas (the cause of P1-2). The worker's note ("a diatomic-standard map for scoring") understates this. Recommendation: one kernel, `formation_gibbs_from_absolute(g_abs, composition, element_ref_g)` in `simulator/vapour_rail/source_rail.py` (layer 1), with a pluggable reference resolver. species_rail_differential (layer 3) calls it, a downward and allowed import. source_rail adopts the typed-refusal semantics. → P2-1.
- `simulator/melt_backend/pure_phase_janaf_score.py:444-481` `formation_g_from_apparent_kJ_mol` (with `:429-441` `element_reference_g_kJ_mol`) uses the same arithmetic (G_a − Σ n_el·g_ref) on a different input. Engine apparent G is referenced to JANAF `ref` tables (`:129-139`), not to records from the same compilation. The domains really do differ, and the "same compilation" rule cannot apply. Sharing the 3-line kernel would be tidy, but I would **not** force it now (P3-6).
- Other second copies found while checking: `simulator/battery/generators/janaf.py:2632-2645` `_interpolate_formation_gibbs` is a line-for-line copy of `tabulated_gibbs.interpolate_tabulated` (Decimal instead of float), and `simulator/battery/compilation_tier.py:505-515` has the Δν_g atm→bar shift (P2-2, P2-3).

---

## d-062 five reviewer questions
1. **Second copy of added or edited logic anywhere? YES.**
   - `rg -n "def (derive_stoichiometry|formula_atoms|_formula_atoms|_balance_oxide_reaction|derive_stoich_oxide_per_vapor|_evaporation_stoich|_oxide_per_product_kg|strip_phase|_strip_phase_suffix)\b"` finds `tools/compose_vapour_rail_carriers.py:175,272`, `simulator/chemistry/sgte_unary.py:1438`, `simulator/evaporation.py:4300`, `simulator/thermal_budget.py:1054`, `scripts/generate_rail_demand_manifest.py:63`, the new `channel_generator.py:134` (a new formula balancer) and `stoich.py:57,68`, plus `catalog.py:327,3732,3759`.
   - The new `formulas.py:114` `_UNGROUPED_FORMULA_RE` repeats the tokenizer already at `catalog.py:3760-3761`.
   - ΔfG-from-absolute: `species_rail_differential.py:621`. Interpolator: `battery/generators/janaf.py:2632`. atm→bar: `battery/compilation_tier.py:505-515`. Diatomic map: `species_rail_differential.py:187`.
   - The feedstock enumerator **is** reused (`janaf.feedstock_element_symbols`, `channel_generator.py:15`), so there is no fifth copy. Good.
2. **Rule, threshold or physics in a presentation or wiring layer? YES (two cases).**
   - (a) The one-source / one-family-per-reaction rule lives only in the generator (`channel_generator.py:366, 400-411`) and in a test. The runtime owner `catalog.py` compiles a mixed-basis reaction silently. VERIFIED probe: JANAF ΔfG liquid plus NASA absolute gas in one B2O3 reaction compiled, p/p0 = **4.2e20** at 1600 K.
   - (b) Derived stoich is computed inside the legacy compatibility projection `_legacy_species_row` (`catalog.py:3877-3926`), not on the compiled species. The compiled dataclasses are unchanged and carry no stoich.
3. **New import crosses a forbidden layer or creates a cycle? NO.**
   - New edges: `vapour_rail`(1) → `reference_data`(1), `accounting.formulas`(1), `yaml_cache`(0) (`tests/import_layers.toml`). `reference_data` imports only `yaml_cache` and nasa_glenn (burcat), so there is no cycle.
   - `tests/import_boundary_baseline.json` is untouched.
   - `pytest -x -q tests/test_import_boundary.py` → **14 passed**.
4. **Every behaviour-preserving move backed by a pin committed first? PARTIAL.** The pins (b8f3a068f) come before the move (5a41afc68), and pressures and compiled species are bit-identical. But the move is not bit-preserving for 16 ledger stoich values, and the pin was loosened inside the move commit (`tests/test_t1139_build_a_pins.py:307-317`). The original pins fail 16/122 on head. Parity-only pins: no external anchor was added (our earlier P2-12).
5. **Relaxed a guard, added a baseline entry, or moved code into a module whose guards are already relaxed? YES, one relaxation.** The stoich pins went from exact to abs 1e-12 (5a41afc68). No baseline entry was added, and nothing went into `vapour_rail/request.py` or other already-baselined modules. The t622 refresh is evidence, not relaxation: the proof re-run passes and the dataclass sha is unchanged. Related guard **gap**: `tabulated_janaf` was added to `RUNTIME_THERMO_EVALUATOR_FAMILIES` (`catalog.py:102-104`), mixing a formation-basis family into an absolute-basis runtime set, with no per-reaction basis or P° check (P1-4).

---

## Findings (ranked)
**P0:** none.

**P1**
1. **Ledger stoich moved at ulp level, and the pin was loosened inside the move commit.** Evidence: `tests/test_t1139_build_a_pins.py:307-317`; the original pins fail 16/122 on head; the largest drift is TiO 4.66e-15 (`legacy_view` → `evaporation.py:4321-4322`). Fix: the owner sanctions the shift as intended (derived values are more precise than the 15-digit hand decimals) and it is recorded as such, plus a Mac Studio golden run. Otherwise REVISE.
2. **Silent gas-reference fallback in the new ΔfG conversion: Si off by 213–243 kJ/mol below 1690 K.** Evidence: `source_rail.py:226-231`, `:596-597`; NG-1858 inverted interval. Fix: skip the documented inverted or zero-width interval instead of dropping the record. Allow the gas reference only above the element's condensed coverage (boiling, per JANAF `ref` convention); otherwise raise `SourceCoverageGap`. Add Si/Al/Fe samples to the residual test.
3. **"JANAF first" (d-071) reaches only 3 of 45 channels.** The JANAF O2 table is `O-029 (ref)`, and `_janaf_skip_reason` drops every `ref` table (`source_rail.py:468-471`), so no JANAF reaction can include O2(g). VERIFIED: 42/45 generated channels are nasa-glenn; only PbO, B2O3 and Li2O (congruent) are JANAF. Consequence: the NASA liquid parents start at Tm, so the generated valid domains sit above the furnace window: Ga 2080, In 2186, V 2230, Sn 1903, Li 1726, Cu 1517 K. JANAF has supercooled liquid tables from 298 K for Cu2O (Cu-020), V2O3 (O-063), Li2O (Li-015) and PbO (O-007). Fix: index gas-only element `ref` tables (O2, H2, N2, F2, Cl2, noble gases) as gas records with ΔfG ≡ 0. Ga, In, Ge, Sn, Rb and Cs still need the supercooled-liquid construction from our earlier P1-5 in Build B.
4. **No per-reaction Gibbs-basis or P° guard in the compiler, and generated records are mislabelled.** Probes: mixed basis compiles with p off by 4.2e20×; mixed P° compiles with P° ignored (`catalog.py:3015-3030`). Generated NASA records carry `gibbs_convention: formation_gibbs` (`source_rail.py:280`, copied at `channel_generator.py:398`) while their coefficients evaluate absolute G, and the catalog never reads the label. Fix: a compile-time check that all `species_thermo` in one reaction share a basis class (tabulated_janaf vs absolute families) and `reference_pressure_Pa == Pstd`. Label NASA/Burcat payloads `absolute_G`.

**P2**
1. d-068: `species_rail_differential.py:621-669` and `:187-193` duplicate the conversion law and the diatomic map. Share one kernel in source_rail, and source_rail adopts its typed refusals.
2. Pankratz atm→bar is species-only (`source_rail.py:840-846`), so per-record ΔfG is off by up to ~0.3 kJ/mol per O2 at 1800 K. Reaction level is correct. Use Δν_g(formation), as in `battery/compilation_tier.py:505-515`, or document that it is reaction-level only.
3. `battery/generators/janaf.py:2632-2645` duplicates `tabulated_gibbs.py:34-52`. Keep one, and carry over the battery's missing-printed-node refusal (`:2648-2660`).
4. `catalog.py:3901-3902` silently swallows `except ValueError`, and `:3891-3893` falls back to the first reaction. For the 34 derived species, raise `CatalogCompileError`. Move the derivation onto the compiled species (d-062 Q2b).
5. Multi-carrier steer, partly met: every manifest carrier is evaluated or a typed gap (test), Ge→GeO ✓, B→BO2 + B2O3 + metaborate gap ✓, V→VO2/VO ✓, Cu→Cu2O/CuO0.5 ✓. **Sn parent is still SnO2** (`channel_generator.py:48`; we asked for SnO/Sn²⁺, valence FROM MEMORY). Generated rows use `transport_headspace` with `pO2_reference_bar: 1.0` (`:249-250`), against our earlier P1-6 (intrinsic_melt). Fine while dormant; Build B must not inherit it.
6. The stoich/balancer copies from our earlier P1-4 remain (d-062 Q1 list), and the generator adds a new balancer (`channel_generator.py:134-174`). It should share `catalog._formula_atoms`/`stoich`.

**P3**
1. `PREFERRED_CARRIERS` (`channel_generator.py:63-68`) is dead code: only a test reads it.
2. Pre-existing decimal-subscript misparse when the hydrate split succeeds ("AlO1.5Si" → Si5). Not a regression.
3. The t622 commit message understates the refresh: base evidence was stale since 94183bc69. The evaluation-grid sha is platform-dependent (VPS ≠ Mac).
4. Per-record JANAF interpolation across element transitions errs up to 2.2 kJ/mol (Li2O(l) 1600–1700 K). It cancels at reaction level on shared grids. Li-015 mixes 100/200 K spacing.
5. Add an explicit test "zero `t1139_*` ids in the live catalog / legacy_view / config bundle".
6. Minor: `_janaf_skip_reason` reads `name`, which the manifest lacks (`title_as_published`), so the title branch is dead. `charge` still filters ions correctly, because it is the string '1'/'-1'. OCR-garbled Pankratz formulas are indexed ('(A10)2', '(8e0)2'). The pure_phase_janaf_score kernel could share the P2-1 helper later.

---

## Tests run (VPS, `/tmp/admit-review-venv`, `-o addopts=""`, no xdist)
| Command | Result |
|---|---|
| `pytest -x -q tests/test_t1139_build_a_pins.py tests/test_t1139_source_rail.py tests/test_t1139_channel_generator.py` @a11d8f6ff | **174 passed** (9.5 s) |
| b8f3a068f pin file @e702b2551 (base code) | **122 passed** |
| original b8f3a068f pin file @a11d8f6ff | **16 failed, 106 passed** (stoich exact-equality rows) |
| `pytest -x -q tests/test_import_boundary.py` | **14 passed** |
| `pytest -q tests/test_vapour_rail_catalog.py::test_t622_cross_revision_additivity_evidence_is_reproducible` | **FAILED on VPS**: platform-dependent grid sha (see check 1); needs the Mac |
| `scripts/prove_t622_cross_revision_additivity.py --output <tmp>` | **PASS**, 229 species / 2580 exact cases; differs from committed only in the grid sha |
| `pytest -x -q tests/test_janaf_compilation.py tests/chemistry/test_builtin_evaporation_transition_provider.py` | **67 passed, 1656 skipped** |
| `pytest -s -k "residuals_on_common_basis or same_reaction_agree"` | 2 passed; residuals reproduce the a11d8f6ff message |

## ASK for regolith-main (Mac Studio)
1. Full `python -m pytest tests/` plus the golden and snapshot suites at a11d8f6ff, with a diff of any golden against e702b2551. This decides gate G1/G2: whether the ≤4.7e-15 stoich shift moves any golden.
2. `tests/test_vapour_rail_catalog.py::test_t622_cross_revision_additivity_evidence_is_reproducible` on the Mac. It cannot be judged on the VPS, because the grid sha is platform-dependent.
3. The owner sanctions (or refuses) the pin loosening in 5a41afc68 (P1-1).
