# Confirm review r2: yakovleva-2019-mg-si-isotope-fractionation @ 460832dfbc2c1bcf774ed55f7f1c72b9eb0010db

**From:** regolith-empirical (batch3 A seat)   **To:** regolith-main   **At:** 2026-10-05 ~21:05 ET
Tip under review: `hunt/yakovleva-2019-mg-si-isotope-fractionation` @ `460832dfbc2c1bcf774ed55f7f1c72b9eb0010db` on
`mac-studio-256-1:Repos/regolith-corpus.git` (fetched, tip verified). Earlier review: `review.md` (r1, FIX-FIRST on `4b6a716d`, 8 items).
Stance: every r1 item treated as NOT FIXED until the page image and the tip proved otherwise; then a collateral-damage pass over every context value.

VERDICT ON COMMIT: FIX-FIRST

rows checked 8, mismatches 3, printed numbers not carried 0

## Method

- Sparse detached worktree `~/Repos/regolith-corpus/worktrees/rev2-yakovleva-2019-mg-si-isotope-fractionation` (main's sparse recipe; 48 MB; removed after delivery).
- PDF sha256 `a948acf70a08f64e8df86d6830d3d50defb97b4bc0fd868a9b7ca5f9e53d15e2` = sidecar; 17 pages (pp. 777–793), 2,534,593 bytes.
- All 17 pages rendered `pdftoppm -r 220 -png` and read as images; spot crops at 400–500 dpi of the p. 777 author line, p. 785 left column (K'_D), p. 786 eq. (23) prime marks.
- Every value of all 8 context rows (7 Mg, 1 Si; 140 value keys) compared against the page images. No tables, no observations (0 CSV tables, 0 numeric rows) — confirmed again: no printed data table exists in the article.
- Changed files 4b6a716d → 460832df: extract, ledger, sidecar only. Word-diff of removed text checked: every removed string is replaced by a fuller located transcription; no key, row, locator or value lost (the old `alternative_equations` pointer is superseded by the transcribed (19)–(25) chain).

## r1 items: FIXED / NOT FIXED / PARTIAL

| r1 # | Requirement (r1 locator) | Status | Evidence at 460832df vs page image |
|---|---|---|---|
| 1 | Eqs (30)–(31) as partial derivatives (p. 789 left) | **FIXED** | `equations_30_and_31`: ∂ln γ_MgO/∂ln a_O²⁻ = β_MgO, β = [Mg²⁺]/([Mg²⁺]+[MgO]); ∂ln γ_SiO2/∂ln a_O²⁻ = −λ_SiO2, λ = [SiO3²⁻]/([SiO3²⁻]+[SiO2]). Matches print, sign included. |
| 2 | Background row locators (pp. 777, 778, 780) | **FIXED** | Row locator 777–780; per-value locators: 4.567 Ga p. 777 L; Δ17O = δ17O − 0.52δ18O ≈ −24‰ p. 777; 10Be/26Al/41Ca/60Fe p. 777; ~3‰ p. 778 L; ~40‰ / ~17‰ p. 778 L; 30–80%, 6–30‰, 3–15‰ p. 778 R; ~99% 16O p. 780 R after (6). All match. Attributions (McKeegan & Davis 2003, Ushikubo 2017, Richter 2002, Williams 2017, Mendybaev 2010/2013/2017) match print. |
| 3 | Si standard-state reference (pp. 783–784, 789, 790) | **FIXED** | `measured_standard_state` now cites (15)–(18) pp. 783–784, unnumbered δ equations p. 789 (standard samples), and Fig. 1 series 1/2 initial-sample normalization p. 790; erroneous (30) reference removed; Si locator `4-6; 15-18; unnumbered delta equations on 789`. |
| 4 | Isotope-activity standard state (p. 783 L) | **FIXED** | `isotope_activity_standard_state`: "в стандартном состоянии пар и расплав содержат только один изотоп" carried, kept distinct from the pp. 787–788 pure-component P_i° reference. |
| 5 | Transcribe numbered eqs (4), (8)–(11), (13)–(26), (29) and unnumbered expressions | **FIXED** (one notation defect, see C3) | Each checked symbol-by-symbol against pp. 780–790: (4), (5), (6) incl. ≈ and √(44/46); (7), (8); (9), (10) + substitutions; (11) + αEq reciprocal convention; (12); (13) + Richter form; p. 783 exchange reaction, K_a = K_x·K_γ with both γ quotients in correct orientation, K_x rearrangement, (14)–(17), K_d', K_d''; (18), k_прямая/k_обратная, K_x = K_D = α; p. 784 mole balance; (19), (20), F, 1−F; p. 785 differential chain, integral, (21); (22), (23) with double-prime K_d'' = x25^V/x25^L (crop-verified), (24), α = K_d''/K_d', K_d'' = αK_d', (25) exponent K_d'(α−1); (26); unnumbered activity flux, (27), (28); (29) incl. √(m_Mg/m_SiO); p. 779 J[Mg] = J_Mg + J_MgO; p. 780 simplified J[Mg], J[Si]; p. 789 δ equations with −1 and ×1000; p. 790 caption δ (initial) and J[Si]/J[Mg] ∝ F_Mg/F_Si ∝ (δ^jSi/δ^iMg)^L. R units erg/(mol K) added. All match. |
| 6 | Nine omitted numeric statements (pp. 780–788) | **FIXED** (one mis-statement introduced, see C2) | 1896 and 1 > F > 0 (p. 780); αEq ≈ 1 (p. 781); (39/41)^0.5, (39/41)^0.43 Richter 2011, √(24/25), √(44/45) (p. 782); K_γ ≈ 1 (p. 783); F from 1 to 0 (p. 785); γ > or < 1 in real solutions (p. 788): all 9 carried with correct digits/exponents. |
| 7 | Citation provenance (pp. 777, 793) | **PARTIAL** | Fixed: extract citation now the printed English citation of p. 793 (Yakovlev O. I., Shornikov S. I., "Theoretical Analyses of Chemical and Magnesium and Silica Isotope Fractionation During Vaporization of Ca-Al-Inclusion of Chondrite", Geokhimia 64(8):777–793, DOI matches); sidecar journal → Geokhimia and title labelled as intake translation. **Not fixed / wrong:** the provenance strings assert that p. 777 prints "О. И. Яковлева" and that "Yakovleva" transliterates the Russian first-author name. It does not — see C1. (r1 itself made this misreading; this r2 withdraws that part of r1 item 7.) |
| 8 | Completeness claim, isothermal assumption, p. 782 ideal-model restrictions, p. 791 forsterite→gehlenite | **FIXED** | Ledger note rewritten for fix round 1; `qualifications`: p. 785 constant temperature + constant K_d ("при условии постоянства температуры"), p. 786 extremely non-equilibrium instantaneous removal, pp. 786–787 (24)/(25) imperfect (Yu et al. 2003); `conventional_assumptions`: the three p. 782 restrictions (K-factor independent of residue composition; homogeneous, continuously mixed residue; melt–vapor equilibrium with instantaneous vapor removal); `mineral_evolution_context`: Mg2SiO4 → Ca2Al2SiO7, Mendybaev 2017, p. 791 L, plus opposing MgO concentration/γ effects (p. 791 top). |

r1 items: FIXED 7/8 (1, 2, 3, 4, 5, 6, 8; items 5 and 6 carry the C3/C2 collateral defects below), PARTIAL 1/8 (7), NOT FIXED 0/8.

## Collateral / new findings (the 3 mismatches)

**C1 — author name: the print is О. И. Яковлев, not Яковлева (p. 777 author line; running heads pp. 778–792 even pages; p. 793).**
Crop at 400 dpi of the p. 777 author line reads `О. И. Яковлев^{а,*}, С. И. Шорников^{а,**}`: the "а" is the superscript affiliation
marker (same marker as on Шорников, keyed to "^а Институт геохимии…"), and the PDF text layer merges it into "Яковлева". Even-page running
heads print "ЯКОВЛЕВ, ШОРНИКОВ"; the e-mail is yakovlev@geokhi.ru; p. 793 prints "O. I. Yakovlev". The extract's
`citation_provenance` ("P. 777 prints … О. И. Яковлева … Yakovleva … transliterates the Russian first-author name"), the attribution
strings "Yakovleva and Shornikov" in `yakovleva_2019_model_scope_and_experiment_facts` and `yakovleva_2019_multicomponent_activity_and_acidity_model`,
and the sidecar bracket "transliteration of the Russian first-author name" are therefore wrong about the print.

**C2 — false "source_internally_inconsistent" label for √(44/45) (p. 782 L vs p. 780 eq. 6).**
`ideal_and_experimental_comparisons` says the p. 782 √(44/45) "differs from equation (6), 44/46; source_internally_inconsistent". The print does
not contradict itself: p. 782 pairs √(44/45) with √(24/25) as the ideal factors for α_Mg and α_Si ("кремнезем испаряется из расплава в форме SiO"),
while (6) is written for the 30SiO/28SiO pair (√(m28SiO/m30SiO) ≈ √(44/46)). With 16O (p. 780), 44/45/46 are the 28/29/30 SiO masses, so 44/45
is the mass-ratio for a different Si isotope pair, parallel to 24/25. The paper does not name the pair for √(44/45), but it prints no conflict.
(r1 item 6 asked for this flag; r2 withdraws that instruction.) Keep both values as printed; drop the inconsistency label.

**C3 — printed K'_D silently normalized to K_d_prime (p. 785 left column, two places).**
The print reads "Уравнение Рэлея выводится при условии постоянства K'_D" and "можно представить в виде x^V_24Mg = K'_D x^L_24Mg" (capital-D
subscript, crop-verified); the following lines and (21) print K'_d. `qualifications` and `differential_derivation` write K_d_prime at both
places without noting the printed K'_D. Transcribe as printed, with a reading note.

## Required changes (FIX-FIRST)

1. **C1, p. 777 author line / p. 793 / running heads:** in `species.Mg.context[0].values.citation_provenance` replace the claim that p. 777 prints
   "О. И. Яковлева" with: p. 777 prints "О. И. Яковлев" with superscript affiliation marks "а,*" (the PDF text layer merges the "а" into
   "Яковлева"); running heads print "ЯКОВЛЕВ, ШОРНИКОВ"; p. 793 prints "O. I. Yakovlev"; "yakovleva" in the source_id is a legacy intake key
   from the text layer, not the printed name or a transliteration of it. Change the attribution strings in context[0] (`attribution`) and
   context[5] (`attribution`) from "Yakovleva and Shornikov" to "Yakovlev and Shornikov". In `raw/<sid>/sidecar.yaml` change "Yakovleva, O. I."
   to "Yakovlev, O. I." (or keep it and change the bracket to say it is an intake text-layer misreading of Яковлев^а) and delete "transliteration
   of the Russian first-author name". Do not rename the source_id (main's call; see note below).
2. **C2, p. 782 left column:** in `species.Mg.context[3].values.ideal_and_experimental_comparisons` remove "source_internally_inconsistent, neither
   is silently corrected" and state instead: p. 782 prints the ideal factors √(24/25) and √(44/45) for α_Mg and α_Si (Si evaporating as SiO,
   attributed to Davis et al. 1990, Wang et al. 2001, Richter et al. 2002, Knight et al. 2009); equation (6), p. 780, uses √(44/46) for 30SiO/28SiO;
   the paper does not state which Si isotope pair √(44/45) refers to. Both kept as printed.
3. **C3, p. 785 left column:** in `species.Mg.context[3].values.qualifications` and `differential_derivation` carry the printed K'_D at the two
   places ("constancy of K'_D"; "x_24Mg^V = K'_D*x_24Mg^L") with a note that the subsequent lines and equation (21) print K'_d; do not silently
   change the subscript.
4. Update the ledger note / extraction report wording accordingly (the ledger note's "no remaining formula defect" stays true; add the C1–C3 fix).

## Row audit (all 8 rows, every value)

| Container | Row | Extract vs print | Match |
|---|---|---|---|
| Mg ctx[0] | model_scope_and_experiment_facts | theory-only scope; cell/orifice/calibration/T/background/sample "not applicable" (no apparatus anywhere pp. 777–793; p. 793 abstract "The article presents an alternative expression of the Rayleigh equation and a new expression of the evaporation of Hertz–Knudsen…"); P_i' ≈ 0 p. 788; a_i = x_iγ_i, P_i° pure substance at same T pp. 787–788; isotope single-isotope standard p. 783; tables none | **No** — `citation_provenance` and `attribution` author name (C1); rest yes |
| Mg ctx[1] | background_isotope_and_evaporation_ranges | 4.567; 0.52; −24; ~3; ~40; ~17; 30–80; 6–30; 3–15; 10/26/41/60; ~99 16O; locators 777/777/778/778/778/777/780 | Yes |
| Mg ctx[2] | hertz_knudsen_and_speciation_model | (1)–(6), J[Mg] sum, simplified rates, φ ≤ 1, T^−1/2 qualifier, ~2 orders, species sums, units incl. R erg/(mol K) | Yes |
| Mg ctx[3] | rayleigh_equations_and_caveats | (7)–(25) + unnumbered pp. 781–786; 1896, 1>F>0, R/R0>1, (39/41)^0.5/0.43, √(24/25), √(44/45), Kγ≈1, F 1→0 | **No** — C2 (false inconsistency label), C3 (K'_D); all digits/exponents/signs yes |
| Mg ctx[4] | figure_1_prior_experimental_trends | 1900, 1900, 1800–1900 °C; 23.1 / 9.1 mass% CaO; axes (a) δ25Mg 0–70, δ29Si 0–20, (b) δ26Mg 0–140, δ30Si 0–20; caption δ with initial denominators, −1, ×1000; undigitized | Yes |
| Mg ctx[5] | multicomponent_activity_and_acidity_model | (26)–(31), activity/flux, γ>or<1, >35 mass% SiO2 (p. 787, Richter 2002), <15 mass% MgO (p. 790, Mendybaev 2017), δ standard equations p. 789, ∝ chain p. 790 | **No** — `attribution` author name (C1, same defect as ctx[0]); equations and numbers yes |
| Mg ctx[6] | conclusions_and_directional_qualifiers | CaO↑ → γMgO↑, γSiO2↓, Mg faster, SiO2 slower, F_Mg–F_Si gap ↑, δ29,30Si slows vs δ25,26Mg (p. 790–791); forsterite→gehlenite p. 791 | Yes |
| Si ctx[0] | isotope_standard_and_direction_checks | 24/25/26, 28/29/30, 16O; (15)–(18) + p. 789 δ + p. 790 initial normalization; speed/residue comparisons p. 790 | Yes |

Mismatch count = 3 distinct defects (C1 spans ctx[0], ctx[5] and the sidecar; C2 and C3 both in ctx[3]).
Directional statements re-checked against the images (p. 779 evaporation/condensation and T; p. 787 >35% SiO2; p. 788 γ < 1 hinders transfer, T → ideal;
p. 789 basicity ↑ → a_MgO ↑, a_SiO2 ↓; p. 790 faster element → larger fractionation, F_Mg < F_Si; p. 791 Ca-rich/basic evolution): no reversal.

## Printed numbers not carried: 0

Every r1 numeric-inventory item is now carried. A rescan of pp. 777–793 for scientific numerals found nothing new; excluded per r1 convention:
manuscript dates (17.01.2019, 30.01.2019, 12.02.2019), address digits (119991, 19), equation labels, figure tick labels, bibliography numbers,
p. 778 GCA v. 201 citation context.

Advisory (non-blocking, not counted): p. 789 prints the meanings β = "степень диссоциации MgO", λ = "степень ассоциации SiO2", and "Знак минус у
λ_SiO2 означает, что оксид кремния в расплаве является не донором ионов кислорода, а их акцептором"; worth one added clause in `equations_30_and_31`.

## Gates (executed on the Mac)

- Green: `/Users/simonrowland/ci-scratch/regolith-green-ro` at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461` (clean; used read-only, PYTHONDONTWRITEBYTECODE=1,
  PYTHONPATH = that checkout, simulator venv python). `engines/engines.local.toml` **exists** in that green checkout.
- `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<abs path of the sparse-worktree extract>)` then `finalize()`: 1 work, 0 experiments,
  0 observations, **hard issues 0**; queue 1 (the expected "extract yielded no observations"); contexts 8 source / 8 migrated, **0 differing value maps**.
- `tools/validate_literature_extracts.py --check-fidelity-match <abs path of the sparse-worktree extract>` (positional path pointing into the corpus worktree):
  **OK: 1 extract file(s) valid**, exit 0.
- Corpus `tools/test_ledgers_valid.py` from the sparse worktree (`-c /dev/null -o addopts= -p no:cacheprovider`): **688 passed**.
- `rg '/Users/|/private/|/tmp/'` over extract, ledger, sidecar: no matches. Worktree left clean; nothing committed or pushed to the corpus.
- Gates pass, but they do not check author names or the prose labels in C1–C3.

## Note for main (outside this sid, not acted on)

The text-layer merge "Яковлев + superscript а → Яковлева" probably also produced the "yakovleva" keys of `yakovleva-2019-ca-al-isotope-fractionation`
and `yakovleva-2022-alkali-chondrule-evaporation` (same first author, same journal layout). Worth a check of those extracts' provenance strings by
their seats; renaming source_ids is main's call.

!COMPLETE: rev2-yakovleva-2019-mg-si-isotope-fractionation — FIX-FIRST, pages read 17, rows checked 8, mismatches 3, printed numbers not carried 0
