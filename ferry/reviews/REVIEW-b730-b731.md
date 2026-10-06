# REVIEW OF RECORD: b-730 / b-731 store data corrections (stacked on t-1139 Build A)

Reviewer: regolith-empirical (VPS seat). Requested by: regolith-physics (REQ-review-of-record-b730-b731-from-regolith-physics-2026-10-05.md). Date: 2026-10-05, ~22:30 ET.
Branch: `origin/review/t1139-build-a-store` @ **1e8ed29ae1d9d82f6f6dc75597d5558881a196d3** (`git ls-remote` confirmed). Base: **3c56d0351** (Build A rebased on green ebf8541d3).
Commits read hunk by hunk: 8031cba86 (pin), 618901902 (move), 2a8bfb733 (b-730), 1143062e4 (b-731), 7eb18ab66 (census re-pin), 1e8ed29ae (regen).
Package read: investigation-findings.md, brief.md, findings.md, uncertainty-audit.md, formula-audit.md, regenerated-row-audit.md. I treated the worker audits as claims to test.
Source: USGS Bulletin 1452 PDF. sha256 `3e394ccc…40622` matches the investigation's original. I rendered PDF pp. 21, 22, 27, 28, 30, 31 and 35 at 300 dpi and read the images.

Seat note: this seat stalled at ~21:50 ET on a held approval and was resumed at 22:14 ET. The earlier pass left only probes, no notes. So every finding below was re-derived and re-probed in this session, including the P1.

Method:
- Detached worktrees `/workspace/repos/wt/rv-b730-1e8ed29ae` and `/workspace/repos/wt/rv-b730-3c56d0351`.
- Probes under `ferry/reviews/rv-b730-probes/` on the mailbox branch.
- Targeted pytest only. This is a 16 GB VPS, so I did not run the full suite (ASK below). Pytest ran with `-n 0` instead of the addopts `-n auto`, to bound memory (peak RSS ≤ 0.52 GB).
- The worktrees are clean; I made no edits to product code.

Tags:
- **VERIFIED** = command output, or file:line at 1e8ed29ae.
- **SOURCE** = what the B1452 page image shows.
- **INFERRED** = my reasoning.

## Verdict: **REVISE 1e8ed29ae1d9d82f6f6dc75597d5558881a196d3**

Counts: **0×P0, 1×P1, 3×P2, 5×P3.**

What holds up:
- b-730 is correct on the page. All 214 removed IDs come from formula/uncertainty lines. Every value I read against the images is an uncertainty, and no value-line number was removed.
- The 18-page locator is right on every page I checked.
- The census re-pin changes counts only.
- No shard outside B1452/B1259 changed.
- The d-062 move was pinned first.

What stops LAND:
- **P1.** The new single splitter returns a confident, wrong composition for mixed fractional formulas. In one case (K0.5Na1.5AlSiO4) this is a regression from a correct parse at base. None of these spellings is in the store today, so this is P1 and not P0. But b-731's stated contract ("one splitter distinguishes fractional subscripts from ASCII-dot adducts") does not hold.
- **P2.** Three P2s should be dealt with, or explicitly accepted, before LAND (below).

## Priority checks (REQ items 1–5)

### (1) The 214 removed rows against the PDF: PASS
- **Store diff (VERIFIED, `diff_0003.out`).** Record 0003 goes from 1850 to 1646 observations: 212 removed, 8 added, 1638 common.
  - Removed: 207 S, 4 ΔfH, 1 log10 Kf.
  - With Build A's two Tb S rows (never committed), that is 209 S + 4 ΔfH + 1 log Kf = **214 unique** IDs from **219** source rows, as claimed.
  - The 8 added are the legitimate Tb S/ΔfH/ΔfG/log Kf on the two value lines.
  - Common observations change only these fields: `locator.published_page/pdf_page_index` (1540), `uncertainty.kind/verbatim` (811) and `admission.decided_by.evidence` page fields (588). No identity or value changed.
- **Every removed source row is an uncertainty row (VERIFIED, `rows_0003.tsv`).** All 219 source rows listed in uncertainty-audit.md fall in the generator's blank-FW pairing set (256 rows). No non-paired row has a blank FW, and no paired row holds a value-magnitude number. The only outliers are two damaged uncertainty tokens; see P2-1.
- **Against the images (SOURCE, 300 dpi).** I read **50 of 214** removed IDs on printed pp. 16, 22, 25 and 29 (PDF 22, 28, 31, 35). Each is the second-line uncertainty token under its substance:
  - Ag2S 0.42, As2S3 0.40, Bi2S3 3.30.
  - Tb2O3 4.20, TbO1.714 4.20, TbO1.812 4.20, TeO2 4.20, ThO2 0.21, rutile 0.17, anatase 0.29, Ti2O3 0.21, Ti3O5 1.67, Ti4O7 12.00, Tm2O3 0.85, tialite 0.84, chrysoberyl 0.13.
  - All 28 on p. 25, including malachite ΔfH 2090 → stored 2.09 and azurite 2000 → 2.00, and nesquehonite S 0.59, ΔfH 260 → 0.26 and log Kf 0.088.
  - Leucite 1.70, albites 0.40, NaAlSi3O8 glass 1.50, nepheline 1.25, nepheline-2 ΔfH 2040 → 2.04, analcime 2.51.
  - **Mismatches 0.**
- **The real values remain (VERIFIED), with the correct page:**
  - Tb 80.75/81.17 on printed 22 / PDF 28.
  - Leucite 200.2, p. 29.
  - Ti4O7 198.74, p. 22.
  - Huntite 299.53, p. 25.
  - Mg nitrate 164.01, p. 25.
  - Acanthite 142.84, p. 16.
  - Pb²⁺ ΔfG −24.4 kJ, p. 15.
  - SiO2 glass log Kf 149.015, p. 21.
- **Locator (VERIFIED).** `TABLE_298K_PAGE_START_LINES` (`simulator/battery/generators/usgs_b1452.py:89`) has 18 entries, matching record 0003 printed 12–29 / PDF 18–35. `test_298k_locator_covers_every_summary_page` passes.

### (2) Adduct/decimal rule, adversarial formulas: PARTIAL (P1, P2-2)
VERIFIED with `p1_probe.py`, results in `p1_*.json`:
- **Correct at the tip:**
  - MgSO4.7H2O, Na2B4O7.10H2O, CaCl2.2H2O, CuSO4.5H2O, H2SO4.2H2O, NaCl.2H2O, SrHgO.4CO2.5H2O.
  - 3CaO.Al2O3 and 3CaO·Al2O3, Al2O3.2SiO2.2H2O.
  - Mn.98O → Mn0.98 O1, Ni.947O and Fe.947O → 0.947/1.
  - NbC.98, VC.5, MoN.5, ZrC.96, (Na.78K.22)AlSiO4.
  - TbO1.714, AlO1.5, CuO0.5.
- **Wrong at the tip (P1):**
  - `K0.5Na1.5AlSiO4` → K 0.5, Na 1, Al 5, Si 5, O 20. Base was **correct** (K 0.5, Na 1.5, Al 1, Si 1, O 4) through the deleted retry.
  - `CaSO4.0.5H2O` → Ca 1, S 1, O 9, H 10, i.e. CaSO4·5H2O. Base refused it.
  - `Na.76K.22AlSiO4` (nepheline as B1452 prints it) → Na 0.76, K 1, Al 22, Si 22, O 88. Base gave a different wrong answer.
  - Also `Mg1.5Fe.5SiO4` → Mg 1.5, Fe 1, ×5 SiO4, and `Ca.5Mg.5CO3` → Ca 0.5, Mg 1, ×5 CO3.
- **The data/ rg sweep (VERIFIED, `parse_diff_all_data.out`).** I parsed all 14076 unique formula strings under `data/` (YAML `formula*:` keys and JSON `"formula*"` keys) with the base and tip parsers. **34 parse differently**:
  - 12 are corrected misparses: Fe.90S, Fe.95O, Fe.947O, H15O10.5S(1) (JANAF H-097: base H15 O10 S5), N1.5617O.41959Ar.00937C.00032 (NASA-Glenn Air), (Cs.65Na.19Rb.03)Al2Si4O12·H2O, and so on.
  - 13 change only the error class (AccountingError → UnknownSpeciesError, a subclass of it, e.g. `Fe0.947O(c)`).
  - 4 newly parse to a wrong composition from malformed source text (P2-2).
  - F6.877S is wrong both ways (pre-existing).
  - **None of the P1 spellings occurs in `data/` today.** That keeps P1 off P0.

### (3) No existing non-B1452/B1259 row changed: PASS
- **Summary shards (VERIFIED).** Across 974 shards in `observation_store_summary.yaml`, only 5 differ: B1452 0003, 0202 (new), 0203 (new), B1259 298k-0145-wustite (new) and ht-0084-wustite (new).
- **Changed paths.** `git diff --name-status 3c56d0351 1e8ed29ae` touches no other `observations-v2`, `extracts-v2`, `compilations` or `data/*.yaml` path.
- **Other files.** `works/10.3133_b1259.yaml` and `10.3133_b1452.yaml` only add. The queue diff removes the 5 B1452 summary-uncertainty aliases and adds 2 wustite aliases (342 → 339).
- **Rows that now parse differently but are stored as formula strings only.** Burcat BU-2445 Fe.95O, B2131 Fe.90S and B677 Fe.9470 carry no parser-derived reaction or composition fields, so a regen leaves them byte-identical (VERIFIED by reading the rows).
- **Totals (VERIFIED, migration-report.md).**
  - Observations: 121808 → 121764 (net −44 = +71 −214 +99).
  - Experiments: 3557 → 3561 (+2 Tb, +2 wustite). The REQ's "experiments 3561" is the final count, not unchanged.
  - Hard issues: 3637, unchanged.
  - Advisory `identity_incomplete`: 218171 → 218191 (+20), which nobody stated (P3-4).

### (4) Census re-pin changes only expected counts, with a stated cause: PASS
- **Only constants change (VERIFIED).** Every +/- line in `git show 7eb18ab66` matches `^[-+]B1(452|259)_[A-Z0-9_]+ = \d+$`: **15 constants**, 7 for B1452 and 8 for B1259, and no assertion changed.
- **Arithmetic closes:**
  - B1452: raw 55707 = stored 15012 + refused 19007 + excluded 21688. Refused 19713 − 71 − 635. Excluded 20834 + 854. IDENTITY_10X 205 − 200 = 5. Unresolved 49 → 47 and 53 → 49.
  - B1259: 29809 = 15219 + 2660 + 11930. Unique 14808 + 99 = 14907. Empty records 80 → 78 = 58 + 15 + 3 + 2.
- **Causes.** The commit message states a cause per change (Tb admission, b-730 removal, b-731 wustite).
- **Tests at the tip, all pass (VERIFIED):**
  - B1452 `test_full_census_closes`, `test_b1452_store_is_record_sharded`, `test_b1452_store_census_is_true_of_observations_v2`, `test_b1452_store_dotted_hydrate_reaction_balances`.
  - B1259 `test_full_census_closes`, `test_b1259_store_is_record_sharded`, `test_b1259_store_census_is_true_of_observations_v2`.

### (5) d-062 checklist: PASS (details in the table below)
- **One owner for normalization.** `_LEADING_DOT_SUBSCRIPT_RE`/`_normalize_leading_dot_subscripts` exist only at `simulator/accounting/formulas.py:115,234`. rg finds no other copy.
- **The retry is deleted.** `parse_formula` now returns `_species_from_cleaned` directly (`formulas.py:256`).
- **No guard relaxed.**

## Findings

### P0: none
Nothing introduced at 1e8ed29ae puts a wrong number into a result, score or ledger today, as far as I verified:
- The P1 spellings are absent from data/.
- The malformed strings that newly parse (P2-2) have no live consumer I could find.
- The one implausible uncertainty band (P2-1) sits on a `pending` row.
- The pre-existing wrong-species rows (end section) predate this change.

### P1-1: the single splitter misreads mixed fractional formulas and regresses one that base parsed correctly
- **Where:** `simulator/accounting/formulas.py:551-578` (`_split_formula_segments`, the `molecular_adduct` branch at `:562-575`). The cause is also the deletion of the decimal retry (old `parse_formula` fallback, now `:256`).
- **Evidence (VERIFIED, re-probed this session):** `p1_3c56d0351.json` vs `p1_1e8ed29ae.json`, listed under (2).
  - An integer subscript followed by `.d` and a polyatomic tail (`Na1` + `.5AlSiO4`) is split as an adduct.
  - A leading-dot occupancy followed by `.dd` and a polyatomic tail (`K` + `.22AlSiO4`) is split as an adduct.
  - A decimal adduct coefficient (`.0.5H2O`) folds `.0` into the preceding O4 subscript and then splits `5H2O`, which yields the pentahydrate.
- **Why P1, not P0:** none of these strings is in `data/` today (VERIFIED sweep). But:
  - the parser is the one owner for every future source;
  - B1452's own nepheline formula (p. 29) is one of them (SOURCE, the PDF 35 render, captured as `$Na_{76}K_{22}AlSiO_4$`; the second digit of the Na subscript is hard to read at 300 dpi, .76 or .78, and both misparse);
  - the brief required that the split be decided by a principled rule, or else moved to the source boundary;
  - the worker's own audit flagged the ambiguity (`Zn.7SiO2`).
- **Required:**
  - Make the ambiguous class fail closed, or resolve it with a stated rule. INFERRED suggestion: when the formula already carries a decimal or leading-dot occupancy outside the candidate dot, prefer the single-segment decimal reading if it parses, and otherwise refuse. Middle dot stays an adduct separator.
  - Keep the hydrate and SrHgO controls unchanged.
  - Add tests for `K0.5Na1.5AlSiO4`, `Na.76K.22AlSiO4` and `CaSO4.0.5H2O` (either the right composition or a typed refusal), with a negative witness at 1e8ed29ae.

### P2-1: b-730 attaches printed uncertainty tokens verbatim without validation, and the scorer turns them into decision bands
- **Where:** `simulator/battery/generators/usgs_b1452.py:2212-2219`. The only filter is `{"", "-", "—"}`.
- **Evidence (VERIFIED, `unc_scan.out`).** Of the 811 newly attached tokens (1187 printed in total), four are damaged:
  - SiO2 glass log10 Kf 149.015 gets `verbatim: '2780.374'` (0003.yaml observation at :235838; token at :235965). SOURCE, PDF 27: the uncertainty line prints "2134 2780.374 278 284", a reference number glued to 0.374. The neighbouring SiO2 rows print 0.374.
  - Pb²⁺ ΔfG gets `'-120'` (:50417). SOURCE, PDF 21: the page prints 120 with a speck before it.
  - H2 ΔfH gets `','` (:145773).
  - Nesquehonite (stored as HgCO3·3H2O) ΔfG gets `500'` (the page shows a tick mark).
- **Why it matters (VERIFIED):** `score.py:4694-4700` lets `_printed_uncertainty_band` (`score.py:1999-2085`) override the cell band for every compilation reference.
  - A scalar token is parsed by `re.fullmatch` at `:2014`, and the sign is dropped by `abs` at `:2021`.
  - For the dimensionless log Kf, the glass row would get a **±2780 band**, so any prediction passes. That row is `pending` today.
  - `-120` scales to 0.12 kJ, the correct magnitude.
  - `,` and `500'` do not match and fall back.
- **Required:** validate each token against a numeric grammar and the column's printed grain. Otherwise record a typed absence or exclusion instead of a PRINTED band. Add a regression test for the SiO2 glass row.

### P2-2: the parser now accepts malformed OCR strings that base refused (fail-open on existing store rows)
- **Where:** `formulas.py:542` (normalization of each segment) together with `:551-578`.
- **Evidence (VERIFIED, `parse_diff_all_data.out` and `term_*.json`):**
  - `Fe.9470` (B677 table-1174, named wüstite, 5 rows) → **Fe 0.947, no O**.
  - `W.465` (B677 table-1112, named vanadium nitride, 5 rows) → **W 0.465**.
  - `N1.5617IO.41959Ar.00937C.00032` (NASA-Glenn NG-2032 "InertAir", 1 store row plus a manifest entry) → **I 5617, O 2356.8, Ar 52.6, C 1.80, N 1**.
  - Base refused all three. `validate._term_composition` now returns these compositions too (`validate.py:459-476`).
- **Impact:** no live consumer found (VERIFIED):
  - The B677 rows carry no reaction (`reaction: unknown`).
  - `SourceRail.gases_for_element` (`source_rail.py:475-495`), which would now list NG-2032 as an iodine-bearing gas, has no caller.
  - The worker's formula-audit reported the two B677 strings as source defects "not silently rewritten", but did not note that b-731 changes them from refused to silently accepted.
- **Required, at least one of:**
  - correct the sources with typed corrections (B677 → `Fe.947O`, `VN.465`; NG-2032 against thermo.inp);
  - add typed refusals before LAND.
- **Also:** add a test that pins the chosen behaviour for these three strings.

### P2-3: b-730 changes scoring bands for 153 admitted B1452 references; nobody stated or measured this
- **Evidence (VERIFIED):** 811 tokens are newly attached in 0003. Of them, 153 are on **admitted** rows of scorer-scaled quantities: 75 S, 39 ΔfG, 39 log10 Kf. The other 40 admitted are ΔfH, which `_printed_uncertainty_band` ignores because no J/mol scale exists for DELTA_FH (`score.py:2070`).
  - Every one of the 153 carries `unit='J/mol·K'`, `'J/mol'` or `'dimensionless'`, and a matching `derivation.output_unit`.
  - So `score.py:4694-4700` now replaces their default band with the printed per-cell band.
- **INFERRED:** these bands are the source's own uncertainties, so this is probably more correct. But it is a score-semantics change, not just a data correction. findings.md and the commit messages do not mention it, and there was no score diff.
- **Required:** a before/after score diff on the Mac Studio (ASK) and one line in the commit or findings.

### P3-1: stale comment in the validator
`simulator/battery/validate.py:461-464` still says "Decimal subscripts (NaO0.5, Fe0.947O) fail parse_formula and must still use the JANAF tokenizer". Since b-731 (and Build A) both parse, and `_term_composition` routes them through `parse_formula`. Update the comment.

### P3-2: private name imported across packages
`simulator/diagnostic_helpers/species_rail.py:30` imports `_normalize_leading_dot_subscripts` from `simulator.accounting.formulas`. There is no cycle (VERIFIED: accounting imports only `accounting.exceptions` and `yaml_cache`; `tests/test_import_boundary.py` 14 passed). But the owner should expose a public name.

### P3-3: the formula audit was not exhaustive
formula-audit.md's rg pattern `[A-Z][a-z]?\.[0-9]+` misses formulas with a digit before the dot whose parse b-731 changes:
- `H15O10.5S` / `H15O10.5S1`: JANAF H-097 (4 store rows) plus the species_rail_differential ledger key. Base gave H15 O10 S5; the tip is correct. The ledger uses the unchanged diagnostic tokenizer and is a typed refusal, so there is no ledger change.
- `F6.877S` / `F6 .877 S`: B1259 pyrrhotite, an OCR form of Fe.877S, 1 stored S row. Base gave F6 S877, the tip gives F6.877 S1; both are wrong.

The 14076-string parse sweep (`parse_diff_all_data.out`) is the complete list.

### P3-4: advisory delta not stated
migration-report.md advisory `identity_incomplete` goes 218171 → 218191 (+20). Neither findings.md nor the regen commit mentions it. Explain it (INFERRED: the newly admitted Tb/wustite rows minus the removed ones).

### P3-5: bisectability
7eb18ab66 re-pins the store-backed census constants before 1e8ed29ae regenerates the store. So the store-reading census tests fail at 2a8bfb733, 1143062e4 and 7eb18ab66. The brief allowed one final regen, but consider landing the re-pin and the regen together.

## d-062 reviewer checklist (5 questions)

| # | Question | Answer | Evidence |
|---|---|---|---|
| 1 | Duplicated logic? | **No** (new) | The leading-dot regex/substitution now has one definition (`formulas.py:115,234`). The diagnostic copy was deleted in 618901902; rg finds no other. `_is_298k_uncertainty_pair` (`usgs_b1452.py:1017`) is the one predicate shared by formula binding (`:1046`) and value classification (`:1854-1861`). Pre-existing, untouched: `_parse_counts` is copied in `usgs_b1452.py:1362` and `usgs_b1259.py:990`, and `_PAREN_GROUP_RE` in `validate.py:103` and `usgs_b1544.py:258`. |
| 2 | Rule/physics in presentation or wiring? | **No** | Code changes are only in `simulator/accounting/formulas.py`, `simulator/battery/generators/usgs_b1452.py` and `simulator/diagnostic_helpers/species_rail.py`. Nothing in web/, tools/, scripts/ or report writers. |
| 3 | Forbidden import or cycle? | **No** | species_rail → accounting is a new edge, but accounting imports nothing from diagnostics or reference_data. `tests/test_import_boundary.py`: 14 passed at the tip. A private name crosses packages (P3-2). |
| 4 | Behaviour-preserving moves pinned first? | **Yes** | 8031cba86 is tests-only (25 lines) and comes before the move in 618901902. The pins pass on pre-move code: 8031cba86's test file run against 3c56d0351 gave 6 passed. The same pins pass at the tip, within the 19-passed formula/pin run. Negative witness: the tip's `test_printed_decimal_subscripts_preserve_molecular_adducts` run against base code fails 4 cases (Fe.947O, Fe.90S, NbC.98, (Na.78K.22)AlSiO4) and passes 8. |
| 5 | Relaxed guard or baseline entry? | **No** | Test diffs add tests and change 15 count constants only; no assertion was removed or weakened, and no allowlist or baseline file was touched. The commented-out `stored_ids <= generated_ids` assertion predates this change (from ec3fbe2eb) and was not touched. Deleting the hydrate-first retry is not a guard relaxation, but it causes the K0.5Na1.5AlSiO4 regression (P1-1). |

## Pre-existing issues (not counted against 1e8ed29ae; route as a new b-ticket)
The store holds **wrong-species rows from OCR misreads**. Their values are correct for the real substance but filed under the wrong formula.
- **B1452 record 0003** (VERIFIED by `fw_check_head.out`, formula mass vs printed FW, cross-checked against the PDF 31 / PDF 22 renders):
  - wüstite stored as `Fe2O3` (polymorph wüstite, S 57.59 admitted, plus ΔfG and log Kf; printed FW 68.887);
  - magnetite as `Fe2O4`, hercynite as `FeAl2O6`;
  - methane as `CH6`, NH4⁺ as `NH6`;
  - the Mg→Hg family: magnesite `HgCO3`, nesquehonite `HgCO3.3H2O`, artinite `Hg2(OH)2CO3.3H2O`, huntite `CaHg3(CO3)4`, tremolite `Ca2Hg5[Si8O22](OH)2`, hydromagnesite `SrHgO.4CO2.5H2O` for 5MgO·4CO2·5H2O;
  - the Mo→Ho family: `HoO2`, `HoO3`, `HoS2` for MoO2, MoO3, MoS2;
  - nepheline-2 ΔfH as `Na76K22AlSiO4` (printed Na.76K.22AlSiO4).
  - A few other FW mismatches in that probe (e.g. Al2(SO4)3, Ca3(PO4)2) may come from the probe matching rows by name slug. I did not verify them.
- **B1259** pyrrhotite as `F6.877S`.
- **B677** `Fe.9470` / `W.465` source strings (see P2-2).
- I did not trace whether any score consumes these rows. The wüstite-as-Fe2O3 row is the one most likely to meet a hematite comparison, so the polymorph separation should be checked.
- Fixing the nepheline formula at source will hit P1-1 unless P1-1 is fixed first.

## Tests run (VPS, targeted, `-n 0`)
- `tests/test_t1139_source_rail.py` + `tests/test_t1139_consolidation_pins.py -k "decimal or leading_dot or adduct"`: 19 passed.
- `tests/battery/test_usgs_b1452_generator.py -k "formula_lines_are_uncertainties or locator_covers_every_summary_page or next_line_formula"`: 3 passed.
- B1452 `-k "test_full_census_closes or store_is_record_sharded or dotted_hydrate"`: 3 passed (49.6 s). `-k store_census_is_true_of_observations_v2`: 1 passed (39.7 s).
- B1259 `-k "test_full_census_closes or store_is_record_sharded or store_census_is_true"`: 3 passed (42.9 s).
- `tests/test_import_boundary.py`: 14 passed.
- Not run here: the full suite, the r16/r17 store-regen staleness files, and any score run.

## ASK (Mac Studio)
1. Full suite at 1e8ed29ae (and later at the fix tip), including the r16/r17 store-regen staleness files.
2. A before/after battery score diff, 3c56d0351 vs the fix tip, scoped to B1452 0003 references (P2-3).

## Required for LAND
- **P1-1:** fail closed or apply a stated rule for mixed fractional formulas, with the three tests.
- **P2-1:** validate uncertainty tokens; SiO2 glass `2780.374` must not become a band.
- **P2-2:** correct or refuse the three malformed strings, plus a pin test.
- **P2-3:** a score diff, and one stated line about it.
- P3s at the worker's discretion.
- Then a focused confirm on the new tip.

rows checked 50 (removed-uncertainty IDs against 300 dpi renders) + 8 kept-value/locator spot checks, mismatches 0; formulas swept 14076, changed 34; findings 0×P0 1×P1 3×P2 5×P3.

VERDICT ON COMMIT: REVISE 1e8ed29ae1d9d82f6f6dc75597d5558881a196d3
— regolith-empirical
