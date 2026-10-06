# Fidelity review (first review, batch 5): corpus extract banya-hino-nagasaka-1993-hydroxyl @ 32e031c962f0146c1e79beebc09cd45c46f12b2a

**From:** regolith-empirical (review seat rev-b57)   **To:** regolith-main   **At:** 2026-10-05 ~21:30 ET
**Branch:** `hunt/banya-hino-nagasaka-1993-hydroxyl` on `mac-studio-256-1:Repos/regolith-corpus.git`. `git fetch origin hunt/banya-hino-nagasaka-1993-hydroxyl` gave tip
`32e031c962f0146c1e79beebc09cd45c46f12b2a`, which matches the assignment. Parent claim commit: `016b066de0b67d65bd54c2534ab3e3fd3ba39c52`.
**Worktree:** sparse, detached at the tip, `~/Repos/regolith-corpus/worktrees/rev-b57-banya-hino-nagasaka-1993-hydroxyl` (Mac), built with the SEAT-COMMON recipe (49 MB). Removed after delivery.
**Green:** `~/ci-scratch/regolith-green-ro` at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461` (read-only, cwd for checks), interpreter `~/Repos/regolith-pyrolysis-simulator/.venv/bin/python` (3.12).
I changed nothing on the branch. The scratch renders and scripts are under `~/.goal-flight/ferry-out/rev-b57-banya/` (outside the repo).

VERDICT ON COMMIT: FIX-FIRST

rows checked 40, mismatches 2, printed numbers not carried 3

## What the commit is (testing the "3 tables, 30 rows" claim)

The commit adds `extracts/banya-hino-nagasaka-1993-hydroxyl.yaml` as a **duplicate record** of the same-DOI sibling `extracts/slag-002-banya-hino-nagasaka-1993.yaml`.
It also adds `ledger/banya-hino-nagasaka-1993-hydroxyl.yaml`, `text/<sid>/` decode files (p001–p008.png, pdftotext, pdfinfo, pdfimages), and deletes the claim file.

| Claim in the report's final line | What I found |
|---|---|
| tables 3 | **True of the paper.** It prints exactly 3 tables: Table 1 (p. 13), Table 2 (p. 13) and Table 3 (p. 17). |
| rows 30 | **True of the paper, but not of this commit.** 9 + 12 + 9 = 30 printed table rows. The commit adds 0 tables/*.csv, 0 observations and 4 context records. The 21 Table 2 and Table 3 rows are in the sibling, and all 21 match the print digit for digit (see below). The 9 Table 1 rows are carried nowhere. They are a bibliographic system list with reference numbers only, and the duplicate notice types them as such, which I accept. |
| cell absent, calibration absent | Correct. This is a theory paper (regular-solution model applied to literature C'OH data), and no apparatus or run set is described anywhere. |
| migrator completes, hard issues 0 | Confirmed (see Checks). |

Page convention used below: "PDF n / p. m" means PDF page index n, printed page m. The PDF runs 1–8, printed pp. 12–19.

## 1. Inventory of every table, equation and numeric statement (all 8 pages rendered at 220 dpi with pdftoppm and read; 4 quadrant crops per page)

| PDF/p. | Item | Carried? |
|---|---|---|
| 1/12 | Abstract: model agrees with measured "within ±0.002"; minimum at about unit basicity | ±0.002 is in the sibling (fig10 row). The minimum statement is **not carried** (see 3b). |
| 1/12 | Eq. (1) C_OH definition with "atmospheric pressure (101 325 Pa)"; Eq. (2) | **101 325 Pa not carried** (N1). |
| 2/13 | Eqs. (3)–(10) (symbolic, no coefficients) | Symbolic only; nothing to carry. |
| 2/13 | Table 1 (9 systems, references) | Bibliographic. Typed in the duplicate notice; accepted. |
| 2/13 | Table 2 (12 α_i−j with references) | Sibling `banya_hino_nagasaka_1993_table2_alpha_ij`: 12/12 match. |
| 3/14 | §4.1 Kennedy et al.: 1 273–1 573 K, 200–970 MPa, 10–35 mol%; 1 743 K, 40 MPa, about 10 mol%; 1 743–1 993 K β-cristobalite | New record `..._kennedy_attributed_conditions`: all 11 numbers match. |
| 3/14 | Eq. (14) ΔG°11 = 17 450 + 2.82T (J); Eq. (16) ΔG°15 = 9 851 − 4.81T (J) | Sibling eq14 and eq16: match. |
| 3/14 | Fig. 1 (1 873 K, label α_H−Si = +30000 J); intercept "about −22 kJ" | Sibling: match. |
| 3/14 | "Equation (14) has been derived ... in the composition range of X_SiO2 < 0.7" | **Not carried** (N2). The new record carries only the 0.82 figure. |
| 3/14 | Fig. 1 composition range "X_SiO2, is greater than 0.82" | New record `..._uncovered_model_composition_ranges`: 0.82 matches. |
| 3/14 | Eq. (17) α_H−Si = +30 000 J (X_HO0.5 < 0.18) | Sibling: match. |
| 4/15 | Fig. 2 (Steiler data at 1 573, 1 673, 1 773 K; label α_K−Si = −81030 J) | Label is in the sibling. The legend temperatures are figure-only (advisory A3). |
| 4/15 | "measured in the composition range of X_SiO2 = 0.5 to 0.7 as shown in Fig. 8" | New record `..._k2o_sio2_data_range`: values match, **attribution wrong** (M1). |
| 4/15 | slope "in X_SiO2 > 0.45"; Eq. (20) −81 030 J (0.25 < X_KO0.5 < 0.55) | Sibling: match. |
| 4/15 | Pein et al. −142 130 J, "mass% FetO = 5 to 10"; Eq. (21) (0.35 < X_LiO0.5 < 0.6) | Sibling: match (FetO range in the quote). |
| 4/15 | Fig. 3 (Russell 1 550 K; Kurkjian & Russell 1 740, 1 590, 1 430 K; label +9750 J); Eqs. (24) +9 750 (0.25–0.66), (25) +1 500 (0.35–0.60), (26) +13 520 (0.26–0.50) | Sibling: equations and label match. Legend temperatures are figure-only. |
| 5/16 | Fig. 4 (1 873 K, +15100 J); Eq. (28) +15 100 (0.37 < X_CaO < 0.57) | Sibling: match. |
| 5/16 | Fig. 5 box ΔG°7 = −19800 + 26.32T J; Eq. (29) (1 430 K < T < 1 873 K) | Sibling: match. |
| 5/16 | Fig. 6 (label −24400 J); Eq. (31) −24 400 (X_AlO1.5 < 0.5) | Sibling: match. |
| 6/17 | Table 3 (9 α_H−i) | Sibling `banya_hino_nagasaka_1993_table3_alpha_H_i`: 9/9 match. |
| 6/17 | Eqs. (32) +15 800 "(J) X_MgO<0.35)" (opening parenthesis missing in print), (33) −8 230 (X_MnO < 0.20), (34) +7 700 (X_PO2.5 < 0.12) | Sibling: match, including the missing-parenthesis note. |
| 6/17 | Figs. 7 (1 873 K), 8 (Kurkjian & Russell; KO0.5 1 573 K, NaO0.5 1 590 K, LiO0.5 1 573 K), 9 (1 823 K; iso-C'OH contours 0.004–0.012) | Sibling has figure_only rows with T for 7 and 9. Legend and contour values are figure-only. |
| 7/18 | "Ban-ya et al.31) have also found the same tendency in C'OH of this slag at 1 673 K" | **Not carried** (N3). |
| 7/18 | Fig. 10; "within ±0.002" (§5 and Conclusions) | Sibling: match. |
| 8/19 | References 26–35 | Bibliographic. |

## 2. Row-by-row check (every row; no sampling)

### 2a. New extract (the commit under review): 4 context records

| record | field | extract | print (PDF/p.) | match |
|---|---|---|---|---|
| duplicate_notice | duplicate_of / same_doi | slag-002-banya-hino-nagasaka-1993 / true | Sibling DOI 10.2355/isijinternational.33.12 = header PDF 1 | yes |
| duplicate_notice | table_2 12 entries; table_3 9 entries; table_1 bibliographic | as stated | PDF 2/13 and PDF 6/17 | yes |
| duplicate_notice | locator `page: 13, table: '2'` | page 13 | Table 2 is on PDF 2 / printed p. 13 | **convention clash** (M2) |
| duplicate_notice | note "...numeric comparison claims are already transcribed in the referenced sibling" | claim | The sibling has no `context:` block and carries none of the directional or comparative statements listed in 3b, except Fig. 10 ±0.002 and the Fig. 1 intercept | **false** (required change R3) |
| kennedy | temperature_K 1273–1573 | 1273, 1573 | "1 273 to 1 573 K" PDF 3/14 | yes |
| kennedy | total_pressure_MPa 200–970 | 200, 970 | "200 to 970MPa" | yes |
| kennedy | H2O mol% 10–35 | 10, 35 | "10 to 35mol%" | yes |
| kennedy | additional 1743 K, 40 MPa, ~10 mol% | 1743, 40, 10 approx | "At 1 743 K and a total pressure of 40 MPa, H2O content in the melt is about 10 mol%" | yes |
| kennedy | equilibrium solid 1743–1993 K, β-cristobalite | 1743, 1993 | "In the temperature range of 1 743 K to 1 993 K, the solid phase ... is pure solid silica (β-cristobalite)" | yes |
| kennedy | attribution Kennedy et al. (ref. 26) | 26 | ref. 26) G.C. Kennedy et al., Am. J. Sci. 260 (1962) 501, PDF 8/19 | yes |
| eq14 range | X_SiO2 > 0.82 where Eq. 14 is not applicable | 0.82, greater_than | "can not be applied to the composition range of Fig. 1 in which SiO2 content in the melts, X_SiO2, is greater than 0.82" PDF 3/14 | yes (value). The derivation range X_SiO2 < 0.7 is missing (N2). |
| k2o range | X_SiO2 0.5–0.7 | 0.5, 0.7 | "measured in the composition range of X_SiO2 = 0.5 to 0.7 as shown in Fig. 8" PDF 4/15 | value yes; **attribution no** (M1) |

### 2b. Sibling rows the duplicate decision depends on (checked so that "already transcribed" can be relied on)

Table 2 (PDF 2/13), extract against print, all match: Al–Si −127610 (13); Ca–Si −133890 (9); K–Si −81030 (Present work); Li–Si −142130 (35, present work);
Mg–Si −66940 (9); Mn–Si −75310 (7); Na–Si −111290 (8); P–Si +83680 (8, 11); Al–Ca −154810 (13); Mg–Ca −100420 (9); Mn–Ca −92050 (12); P–Ca −251040 (11). **12/12, references included.**

Table 3 (PDF 6/17), all match: H–Al −24400; H–Ca +15100; H–K +13520; H–Li +1500; H–Mg +15800; H–Mn −8230; H–Na +9750; H–P +7700; H–Si +30000. **9/9.**

Equation and intercept rows, all match the print (values, signs and validity ranges): eq14 17450 / 2.82; eq16 9851 / −4.81; eq17 +30000 (X_HO0.5 < 0.18);
eq20 −81030 (0.25 < X_KO0.5 < 0.55; slope range X_SiO2 > 0.45); eq21 −142130 (0.35 < X_LiO0.5 < 0.6); eq24 +9750 (0.25–0.66); eq25 +1500 (0.35–0.60);
eq26 +13520 (0.26–0.50); eq28 +15100 (0.37–0.57); eq29 −19800 / +26.32 (1430–1873 K); eq31 −24400 (< 0.5); eq32 +15800 (< 0.35; parenthesis note correct);
eq33 −8230 (< 0.20); eq34 +7700 (< 0.12); Fig. 1 intercept "about −22 kJ". **15/15.** The figure labels that the sibling carries (Figs. 1, 2, 3, 4, 6 and the Fig. 5 box) also match.

Rows counted: 4 (new) + 21 (Tables 2–3) + 15 (equations and intercept) = **40 rows checked**. The mismatches are both in the new extract. The sibling has 0 value mismatches.

## 3. Findings

### Mismatches (2)

**M1. Wrong attribution on `banya_hino_nagasaka_1993_k2o_sio2_data_range` (PDF 4/15, §4.2.1, left column, and PDF 6/17, Fig. 8).**
The extract says `attribution: Steiler (reference 23), data shown in Figure 2`. The print reads: "The hydroxyl capacity of K2O–SiO2 slag has been measured in the composition range of X_SiO2 = 0.5 to 0.7 **as shown in Fig. 8**."
The Fig. 8 legend reads "Kurkjian & Russell △ KO0.5–SiO2 (1573K)", which is ref. 16) C.R. Kurkjian and L.E. Russell, J. Soc. Glass Tech. 42 (1958) 130T. Table 1 also lists K2O–SiO2 under refs 15), 16).
Steiler measured K2O **activities** (Fig. 2, Eq. 19), not hydroxyl capacities. The paper also has an internal inconsistency here: the text cites "Steiler23)", but in the reference list 23) is Ban-ya, Iguchi and Yamamoto, and Steiler is 33).
Required change: attribution "Kurkjian and Russell (ref. 16); data shown in Fig. 8" with locator figure '8' added. If Steiler is mentioned anywhere, record `reason: source_internally_inconsistent: text cites Steiler as 23), reference list gives Steiler as 33)`.

**M2. Mixed page conventions across the four locators.** The duplicate notice uses `page: 13` (the printed page of Table 2, which is PDF page 2).
The other three records use `page: 3`, `page: 3` and `page: 4`, which are PDF page indices (printed pp. 14, 14, 15).
The sibling uses `page` = printed page plus an explicit `pdf_page_index`, while SCHEMA.md's example (`page: 13, published_page: 3051`) treats `page` as the PDF page. As committed, a reader cannot tell which convention applies.
Required change: give every locator explicit `published_page` and `pdf_page_index`: notice 13/2; Kennedy 14/3; Eq. 14 range 14/3; K2O range 15/4. Keep `page` consistent with whichever convention main prefers.

### Printed numbers not carried (3), carried by neither the new extract nor the sibling

- **N1.** P° = 101 325 Pa, "the atmospheric pressure (101 325 Pa)", §2 under Eq. (1), PDF 1/12, right column. This is the reference pressure in the C_OH and C'OH definitions (Eqs. 1, 2, 8), so the capacities depend on it. Carry it as located context.
- **N2.** X_SiO2 < 0.7, "Equation (14) has been derived, by Ban-ya and Hino,10,12) in the composition range of X_SiO2 < 0.7", §4.1, PDF 3/14, right column. Add it to the Eq. 14 validity record, attributed to refs 10 and 12. Re-attribute 0.82 as the present authors' statement of the Fig. 1 composition range; the current text attributes it to Ban-ya and Hino.
- **N3.** 1 673 K, "Ban-ya et al.31) have also found the same tendency in C'OH of this slag at 1 673 K", §5, PDF 7/18, left column. Carry it as quoted_attributed context (ref. 31).

### Required change R3: directional and comparative statements (brief item 6) are not carried anywhere, although the duplicate notice says they are

The sibling has no `context:` block. Running rg for decrease, increase, minimum, "order of magnitude", Kennedy and 1673 finds none of these statements in the sibling.
The author's report lists them and says the sibling "carries these as equations, narrative quotes, or figure-only context". That is true only of the Fig. 10 ±0.002 quote, the Fig. 1 "does not go through the origin" quote and a paraphrase of the Sachdev scatter.
Carry each one verbatim with its locator in the new extract, or remove the coverage claim from the notice. The brief requires them ("Each item must exist as rows or as located context facts"). Quotes re-checked against the page images:
1. PDF 1/12 Abstract: "It has been found that C'OH calculated by the model showed the minimum value at the slag composition of about unit basicity."
2. PDF 1/12 §2: "the water vapor solubility are proportional to the square root of water vapor pressure in the gas phase".
3. PDF 1–2/12–13 §2: "C'OH decreases when a basic oxide such as CaO or alkaline metal oxide is added to SiO2, but shows a minimum value at a slag composition of about unit basicity and tends to increase with an increasing content of additive oxide as shown in Figs. 7 and 8".
4. PDF 2/13 §2: "Also in CaO–SiO2 containing basic oxide such as MnO, MgO, Li2O, SrS, C'OH shows a minimum at a slag composition of about unit basicity under a constant concentration of these basic oxides.20–24)"
5. PDF 3/14 §4.1: "an increase in the water vapor pressure rapidly decreases the melting point of solid silica by the dissolution of H2O into SiO2."
6. PDF 4/15 §4.2.1: "It is seen from Fig. 2 that there are two linear relations. That is, liquid K2O–SiO2 system can be treated as the subregular solution."
7. PDF 4/15 §4.2.1: "αLi−Si obtained in the present work agreed well with the result obtained by Pein et al.35)"
8. PDF 4/15 §4.2.2: "all the data show linear relations exit between YB and X_NaO0.5. That is, the relation of the regular solution is mostly satisfied in this system." ("exit" sic.)
9. PDF 5/16 §4.3.1: "Though some discrepancies among the previous results are seen in Fig. 4, results observed by one of the present authors18) show good linear relation."
10. PDF 5/16 §4.3.2: "the good linear relation which is independent of the slag system is established between the temperature and RT ln K7."
11. PDF 5/16 §4.4: "a linear relation can be observed between YD and X_AlO1.5 in the literature data, except the results obtained by Sachdev et al.29) whose data show a large scatter."
12. PDF 5/16 §4.4: "linear relations between YD and X_MOx passing through the origin were observed for all slag systems" (MgO, MnO, PO2.5 ternaries; "not enough measurements have been done").
13. PDF 6/17: "one can see that αH−i is about an order of magnitude lower than αi−j. This indicates that the dependence of C'OH on the slag composition is governed by the interactions among the cations".
14. PDF 6/17 §5: "In both the slags, the agreement between the measured and the predicted values is very good."
15. PDF 7/18 §5: "In the composition region of high basicity, the calculated results agree well with the ones that are measured. On the other hand, the calculated C'OH has a slightly higher value than the measured one in acidic and high alumina containing slag." and "...because of the very slow rate of dissolution of water vapor into slag.31)"
16. PDF 7/18 §5: "C'OH calculated by the model shows a minimum value at about unit basicity of CaO/SiO2 under a constant Al2O3 content." (followed by N3).

### Advisory (not counted)

- A1. There is a second source-internal inconsistency at PDF 6/17 §5: "Figures 7 and 8 are the comparisons ... in alkaline silicates and CaO–SiO2 binary slags respectively". The captions are the reverse (Fig. 7 = CaO–SiO2 at 1 873 K; Fig. 8 = NaO0.5/LiO0.5/KO0.5–SiO2). It is worth one `source_internally_inconsistent` note.
- A2. The duplicate rests on a sibling that does not survive migration. On green 61ec839da, the sibling gives 27 observations and **54 hard issues after finalize()**, which reproduces the author's count. Its quantities (`quadratic_formalism_interaction_energy_alpha_*`, `water_vapor_solubility_deltaG`, ...) decode as unavailable. So none of this paper's numbers reach the migrated store. That is pre-existing and outside this commit, but main should decide whether the duplicate should stand or the sibling should be fixed/migrated. The author lists it under AMENDMENTS PROPOSED. The sibling does pass `--check-fidelity-match`.
- A3. The temperatures in the figure legends (Fig. 2: 1 573/1 673/1 773 K; Fig. 3: 1 550/1 740/1 590/1 430 K; Fig. 8: 1 573/1 590/1 573 K) and the Fig. 9 contour labels (0.004–0.012) are figure-only. They are not counted, but the sibling's figure_only rows for Figs. 2, 3 and 8 could carry the legend temperatures cheaply.
- A4. The standard state "pure liquid K2O" (Eq. 18, PDF 3–4/14–15) appears in neither extract. The sibling records only KO0.5(R.S.).
- A5. The sidecar licence reads "Typical licence CC BY-NC-ND 4.0 as stated on current ISIJ International article pages". The PDF itself prints only "© 1993 ISIJ". The sidecar is not in this diff.
- A6. The report says `engines/engines.local.toml` is "absent in the simulator worktree". In `~/ci-scratch/regolith-green-ro` it **exists** (gitignored, .gitignore:105).

## 4. Experiment facts and typed absences
There is no experiment in this paper. I read all 8 pages and found no apparatus, cell, orifice, calibration, ionisation, temperature-measurement or pressure-environment statement.
The report's "not applicable" table is correct, and the extract adds no experiment or bench record, which is right.
The only pressures are the reference P° (N1) and Kennedy's attributed 200–970 MPa / 40 MPa, which is correctly carried as quoted_attributed.

## 5. Standard states and derivations
- HO0.5: "the standard state of the activity of HO0.5 is the hypothetical regular solution of pure liquid HO0.5" (PDF 2/13). The sibling has `HO0.5(R.S.)`, which is correct.
- SiO2: "pure liquid SiO2 and a hypothetical regular solution of pure SiO2 melt" (PDF 3/14), plus β-cristobalite ↔ liquid (Eqs. 15–16). Correct in the sibling.
- KO0.5 / LiO0.5: (R.S.). Correct in the sibling. See A4 for K2O(l).
- There are no measured_reduced rows, so lineage/derived_from does not apply to the new extract. The sibling's 27 model_derived rows lack `derivation`/`derived_from`, which is the 54 hard issues in A2.

## 6. Citation, licence, sha, paths
- Citation and DOI match PDF 1 ("ISIJ International, Vol. 33 (1993), No. 1, pp. 12–19"). The authors are Shiro BAN-YA, Mitsutaka HINO and Tetsuya NAGASAKA.
- Sidecar sha256 `b70ddca6cd2bd130ada5b339a90656d649d8b8aad43de8c00a9ae86b21a20c0f` = `shasum -a 256` of raw/banya-hino-nagasaka-1993-hydroxyl/banya-hino-nagasaka-1993-hydroxyl.pdf. Size 1133285 and 8 pages match.
- My 220-dpi renders are byte-identical in size to the committed text/<sid>/pages/p001–p008.png.
- `rg "/Users/|/private/"` on the changed extract and ledger finds no matches.

## 7. Checks (green 61ec839da, cwd ~/ci-scratch/regolith-green-ro, PYTHONPATH=that clone)
- `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<worktree>/extracts/banya-hino-nagasaka-1993-hydroxyl.yaml)` followed by `finalize()` completes. It gives works 1, observations 0, `validation.ok True`, **hard issues 0**, 4 rows in `context_by_work`.
  All payload numbers survive serialisation (1273/1573, 200/970, 10/35, 1743, 40, 10, 1743/1993, 0.82, 0.5/0.7). Queue: 1 entry, "extract yielded no observations", which is expected for a duplicate.
- `tools/validate_literature_extracts.py --check-fidelity-match <absolute path of the extract in my sparse worktree>`, run from the green clone: `OK: 1 extract file(s) valid`. I pointed it at the corpus file by passing that path as the positional argument.
- Corpus worktree `python3 tools/test_ledgers_valid.py`: exit 0.
- `engines/engines.local.toml` in the green clone: **exists** (gitignored).
- I ran neither tools/build_index.py nor tools/migrate_pilot_extracts.py.

## Required changes (for the fixer), each with its page locator
1. M1: re-attribute `k2o_sio2_data_range` to Kurkjian & Russell (ref. 16), Fig. 8 (PDF 4/15 §4.2.1; PDF 6/17 Fig. 8 legend). Note the Steiler 23)/33) citation inconsistency if Steiler is named.
2. M2: put explicit `published_page` + `pdf_page_index` on all four locators (13/2, 14/3, 14/3, 15/4) and use one `page` convention.
3. N1: carry P° = 101 325 Pa (PDF 1/12 §2, Eq. 1).
4. N2: carry the Eq. 14 derivation range X_SiO2 < 0.7 (refs 10, 12) and attribute the 0.82 figure to the present authors as the Fig. 1 composition range (PDF 3/14 §4.1).
5. N3: carry "same tendency ... at 1 673 K", Ban-ya et al. ref. 31 (PDF 7/18 §5).
6. R3: carry the 16 directional/comparative statements in §3 verbatim with their locators, or drop the notice's claim that they are in the sibling.
7. Optional: A1 (Figs. 7/8 swap note) and A4 (K2O(l) standard state).
Then re-run migrate + finalize (hard issues 0), the fidelity validator and test_ledgers_valid.

!COMPLETE: rev-banya-hino-nagasaka-1993-hydroxyl — FIX-FIRST, pages read 8, rows checked 40, mismatches 2, printed numbers not carried 3
