# REVIEW (first, full fidelity): le-losq-2023-alkali-aluminosilicate-glasses @ 522c1237a010255b3a38b7098d956fd00c0e514d

Reviewer: a regolith-empirical seat (Grok Bot), 2026-10-05 ~22:57 ET. Corpus batch 13, section A, sent by regolith-main (REQ 2026-10-06 ~05:00 ET).
Source: Le Losq C. & Baldoni B., "Machine learning modeling of the atomic structure and physical properties of alkali and alkaline-earth
aluminosilicate glasses and melts", arXiv:2304.12123v2 [cond-mat.mtrl-sci], dated July 11, 2023. 23 PDF pages; printed page n = PDF page n.
This is a model paper (i-Melt greybox neural network trained on literature/SciGlass data). It has no new laboratory measurement.

VERDICT ON COMMIT: FIX-FIRST

rows checked 104, mismatches 9, printed numbers not carried 30

Counts, broken down:
- rows checked 104 = t1 29 rows + t2 9 rows + t3 54 records + f2 12 counts. I checked every one, digit by digit, against the page image.
  That is 171 numeric cells (t1 87, t2 18, t3 54, f2 12). **All 171 cells match the print. 0 transcription mismatches.**
  The extract's inline `rows` copies match the CSVs exactly (scripted compare: t1 0/29, t2 0/9, t3 0/54 differences).
  I also checked all 50 quoted statements, the 9 dataset counts, and the 10 figure summaries against the page images.
- mismatches 9 = 5 content findings (C1–C5) + 4 locator findings (L1–L4). No transcribed number is wrong.
- printed numbers not carried 30 = 17 located numeric statements or equations (N2–N6, N8–N13, N15–N17, N24–N26) + 13 directional, comparative or
  qualifying statements (N1, N7, N14, N18–N23, N27–N30). The brief's item 6 requires these. Figure axis ticks, colour bars and in-panel labels are figure-only and are not counted.
- Severity: no P1. P2 = C1, C2, C3, N26, N15–N17. P3 = everything else. The extract has 0 observations, so no wrong number reaches a score or a ledger today.

## Setup (what I ran, and where)
- Mirror `mac-studio-256-1:Repos/regolith-corpus.git`, branch `hunt/le-losq-2023-alkali-aluminosilicate-glasses`. After `git fetch`, the tip is
  `522c1237a010255b3a38b7098d956fd00c0e514d`, the assigned sha. There are 2 commits over merge-base 3dab24d7: d44c2be8 (claim) and 522c1237 (extract).
  Both are authored by Simon Rowland <simon@simonrowland.com>. Neither has AI or co-author trailers.
  Changed files: `extracts/<sid>.yaml` (+384), `ledger/<sid>.yaml` (37 lines, +32/−5), `tables/<sid>/{t1,t2,t3,f2}.csv` + 4 `*.provenance.yaml`, and
  `text/<sid>/{pdftotext-layout,pdfinfo,pdfimages-list}.txt`. `raw/` is unchanged.
- The review worktree on the Mac was SPARSE: `~/Repos/regolith-corpus/worktrees/rev-le-losq-2023-alkali-aluminosilicate-glasses`, created with
  `git worktree add --no-checkout` and detached at 522c1237. Its sparse-checkout was `'/*' '!/raw/*/*' '!/text/*/*' '/raw/*/sidecar.yaml' /raw/<sid>/* /text/<sid>/*`.
  It was 49 MB. I removed it after delivery. Mac free disk at the start was 62 GB.
  I did not run build_index.py or migrate_pilot_extracts.py, and I wrote nothing tracked. The only untracked residue was `tools/__pycache__/` from pytest, and it went away with the worktree.
- Green is the read-only detached clone `~/ci-scratch/regolith-green-ro` at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`. I ran
  `~/Repos/regolith-pyrolysis-simulator/.venv/bin/python` with `PYTHONPATH=<green-ro>`.
  `engines/engines.local.toml` **exists** in green-ro (untracked). None of the checks below use it.
- Page images: I rendered all 23 pages with `pdftoppm -r 220 -png` on the VPS and READ every one, including the reference pages 18–23.
  Tables 1 (p.7), 2 (p.13) and 3 (p.14) were also read as 300 dpi crops. pdftotext was used only as a lead.
- PDF sha256 is `937d0c95ed53f31217e2de1029e7f028387e9cb3814c69246d5ed7d2919dcb90`, 1161762 bytes. It matches the sidecar and the extract's `corpus_sha256`.

## Acceptance checks (all pass; the FIX-FIRST is a fidelity verdict, not a tooling one)
- Migrator: `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(Path('extracts/<sid>.yaml'))`, then `finalize()`, run with cwd = the sparse corpus worktree.
  Result: `result.validation` has **hard_issues 0** and issues 0. `evidence_fallthrough {}`, `registry_issues []`, `unrecognised_polymorphs {}`.
  Output: 1 work (`1d9263fe…c8dd`), 1 experiment (method `engine_evaluation`), 0 observations, 7 context rows.
- Payload survival (checked in `result.context_by_work`): Table 3 SiO2 survives as `{percentile_2_5: 24.1, percentile_50: 25.7, percentile_97_5: 27.1,
  literature_min: 26.0, literature_max: 27.5}` and `{model_intercept: 80, model_intercept_uncertainty: 2, literature_intercept: 81.37}`.
  `cell_material` survives as a typed `not_applicable` map with locator p.2. All 7 contexts survive non-empty, with 1.1–12.4 kB serialised each.
- `evidence_for()` returns a known class with no fallthrough for quoted_attributed, model_derived and figure_only. There are no reduced rows.
- Fidelity: `PYTHONPATH=<green-ro> python <green-ro>/tools/validate_literature_extracts.py --check-fidelity-match <sparse worktree abs path>/extracts/<sid>.yaml`
  → `OK: 1 extract file(s) valid` (exit 0). The script came from green; the extract path pointed at the corpus worktree.
- Corpus ledger: `python -m pytest -q -p no:cacheprovider tools/test_ledgers_valid.py` in the worktree → **708 passed**.
- `rg '/Users/|/private/'` over every changed file finds no matches.

## 1. Inventory of tables, equations and numeric statements (page: carried?)
- p.1 Abstract: the accuracy list (0.4 log10 Pa·s over 10⁻¹–10¹⁵; ≤1 J/mol/K; ≤60 K; ≤16 K; ≤3 %; ≤0.02 g cm⁻³; ≤0.006; ≤4; ≤6 GPa; ≤1.1×10⁻⁶ K⁻¹; ≤25 %). Carried and correct.
- p.2: the dataset counts are carried and correct: Dviscosity 790, Ddensity 668, DRaman 252, Doptical 610, DCpl 95, DAbbe 296, Delastic 1006 and DCTE 2122,
  with units as printed. GlassPy 0.3 is carried. The p.2 "fairly complete, albeit sparse" quote is carried, but under the wrong section (L4).
- p.3: Dliquidus 4505 is carried. The four dataset-size qualifiers are carried. Fig. 1 is figure-only, carried.
- p.4: **Fig. 2** printed bar counts are carried in f2.csv: 12/12 correct. The 0.1 mol% leakage check and the 0.8/0.1/0.1 split are carried (C5 for wording).
  Not carried: N1, N2, N3.
- p.5: 3–4 layers / 350–500 units, dropout ~0.3, ADAM 1×10⁻⁴–3×10⁻⁴, 10 best models and 100 samples → 1000 are carried. Not carried: N4.
- p.6: ≤0.4 RMSE, ~0.1 MAE, 0.4–0.5 vs 0.2–0.3 and ~0.4 previous version are carried. Fig. 3 is figure-only. Not carried: N5, N6, N7.
- p.7: **Table 1** is carried in full and correct (§2).
- p.8: 16 K / 1 J, ~60 K, ~3 % vs 5 %, 0.02 / 0.006, 6 GPa, and ~16 % / 25 % MAPE are carried. Fig. 4 is figure-only.
  The "1100 cm⁻¹" and "NBO/T … 475 cm⁻¹" quotes are on this page but carry the wrong locator (L1, L2). There is a Raman MAPE inconsistency (C1).
  Not carried: N8, N9, N10, N29.
- p.9: ~1030 cm⁻¹ and 0.18 Al2O3 / 560 cm⁻¹ are carried. Fig. 5 is figure-only (L3). Not carried: N11, N12, N13, N14.
- p.10: the two MME statements and the 95 % CI statement are carried. Fig. 6 is figure-only. Not carried: N30.
- p.11: **Eqs. (1), (2), (3) are not carried (N15–N17).** The two fragility-trend quotes are carried. Not carried: N18, N19.
- p.12: 84–90 % MC-Dropout coverage, "too narrow", and the conformal 95 % statement are carried. They contradict Table 2 (C2). Fig. 7 is figure-only. Not carried: N20.
- p.13: **Table 2** is carried in full and correct (§2). The <0.05 / 0.05–0.1 / >1 g cm⁻³ statements are carried. Not carried: N21, N22.
- p.14: **Table 3** is carried in full and correct (§2). Its literature columns carry the wrong class (C3). Fig. 8 is figure-only. Not carried: N23, N24 (begins p.14).
- p.15: all six XAl Vm/Cp statements are carried and correct (~39→~35; ~3 / ~5; ~2; ~15; ~11 / ~17; 20–25, step ~0.4, ~151→~172). Fig. 9 is figure-only. Not carried: N25.
- p.16: 25.575 cm³/mol (corundum), ≤~2 %, and the composition/T/P statement are carried. Fig. 10 is figure-only.
  **Not carried: N26 (80.3 / 79.9 / 70.0 J mol⁻¹ K⁻¹ at 300 K, Richet et al. [116])**, plus N27 and N28.
- p.17 Conclusion: one quote is carried (C5 for wording). The acknowledgement, funding (ANR-18-IDEX-0001) and data-availability text are non-data and need no carry.
- pp.18–23 References [1]–[129]: no data.
- Figures 1–10 are all figure_only. None is digitised, which is correct. Only the printed Fig. 2 bar labels are transcribed, which is acceptable.

## 2. Tables, digit by digit (csv/extract vs print)
### Table 1 (p.7): training | validation | testing
| row | csv | print | match |
|---|---|---|---|
| Visc. Adam-Gibbs RMSE / MAE | 0.2 0.3 0.4 / 0.1 0.1 0.1 | 0.2 0.3 0.4 / 0.1 0.1 0.1 | yes |
| Visc. Free Volume RMSE / MAE | 0.2 0.2 0.3 / 0.1 0.1 0.1 | 0.2 0.2 0.3 / 0.1 0.1 0.1 | yes |
| Visc. VTF RMSE / MAE | 0.2 0.3 0.3 / 0.1 0.1 0.1 | 0.2 0.3 0.3 / 0.1 0.1 0.1 | yes |
| Visc. MYEGA RMSE / MAE | 0.2 0.3 0.3 / 0.1 0.1 0.1 | 0.2 0.3 0.3 / 0.1 0.1 0.1 | yes |
| Visc. Avramov-Milchev RMSE / MAE | 0.2 0.3 0.3 / 0.1 0.1 0.1 | 0.2 0.3 0.3 / 0.1 0.1 0.1 | yes |
| Density RMSE / MAE | 0.01 0.01 0.02 / 0.004 0.006 0.006 | same | yes |
| Raman MAPE | 17 17 25 | 17 17 25 | yes |
| Refractive index RMSE / MAE | 0.003 0.005 0.006 / 0.0009 0.0009 0.0013 | same | yes |
| CTE RMSE / MAE | 1.0 0.9 1.1 / 0.4 0.5 0.5 | same | yes |
| Elastic modulus RMSE / MAE | 4 8 6 / 2 2 2 | same | yes |
| Abbe RMSE / MAE | 1.1 0.4 3.7 / 0.3 0.2 0.5 | same | yes |
| Liquidus RMSE / MAE | 61 66 60 / 38 39 39 | same | yes |
| Tg (10¹² Pa·s) RMSE / MAE | 13 16 12 / 5 6 9 | same | yes |
| Heat capacity RMSE / MAE | 2 3 3 / 1 3 1 | same | yes |
| Glass entropy RMSE / MAE | 0.5 1.4 0.8 / 0.3 0.8 0.3 | same | yes |
The units column matches the row labels (log10 Pa·s, g cm⁻³, %, –, 10⁻⁶ K⁻¹, GPa, –, K, K, J mol⁻¹ K⁻¹). The caption is carried as a footnote. 29/29 rows correct.

### Table 2 (p.13): MC dropout | Conformal c.i.
Viscosity 90/94, Density 87/87, Raman 70/97, Refractive index 90/97, CTE 84/97, Elastic Modulus 76/94, Abbe 81/78, Liquidus 85/96, Heat capacity 57/86.
CSV and extract agree with the print in every cell: 9/9. The bold "closest to 95 %" marks are not carried. The caption carries their meaning, which is acceptable.

### Table 3 (p.14): Vm 2.5th / 50th / 97.5th / literature range; Cp liquid model; Cp liquid literature
| oxide | csv | print | match |
|---|---|---|---|
| SiO2 | 24.1 25.7 27.1 26.0–27.5; 80±2; 81.37 | 24.1 25.7 27.1 26.0-27.5; 80 ± 2; 81.37 | yes |
| Al2O3 | 34.5 37.8 38.4 37.0–39.0; 118±5 + 0.028±0.006·T; 130.2 + 0.0357·T | same | yes |
| Na2O | 21.3 22.9 29.1 25.0–29.0; 99±7; 100.6 | same | yes |
| K2O | 34.7 36.9 50.1 40.0–46.0; 71±14 + 0.017±0.005·T; 50.13 + 0.01578·T | same | yes |
| MgO | 8.1 13.1 16.5 11.0–13.0; 81±8; 85.78 | same | yes |
| CaO | 14.0 14.9 18.7 14.0–18.0; 92±11; 86.05 | same | yes |
The slope units J mol⁻¹ K⁻² are correct. The footnote ("important error bars indicate compositional dependence … do not reflect model uncertainties")
and the reference attribution ([97,98,99,47] Vm; [40,51] Cp) are carried. 54/54 records correct.

### Fig. 2 (p.4) printed bar labels → f2.csv
Viscosity: SiO2 780, Al2O3 620, Na2O 228, K2O 112, MgO 325, CaO 439. Density: SiO2 664, Al2O3 305, Na2O 392, K2O 155, MgO 109, CaO 370. 12/12 correct.

## 3. Experiment / bench facts and typed absences
The paper reports no laboratory work. It says: "The data were selected via a review of the existing literature as well as of the SciGlass database"
(p.2 §2.1), and the data and references are "provided in the database available in the Github software repository that hosts i-Melt … as well as on Zenodo".
- `method: engine_evaluation` is the right token.
- Cell, liner, orifice, pressure calibration, ionisation, multiplier, chamber background and activity facts are all `not_applicable`. I agree.
- Temperature sensor, calibration and uncertainty, sample preparation, purity, and composition as-prepared/after-run are `unknown/not_published`. I agree:
  they belong to the underlying studies and are not printed here.
- No activities are reported, so there are no standard states. That is correct.
- The data-selection sentence (hand selection for viscosity, density, Raman and RI; SciGlass via GlassPy 0.3 for Abbe, elastic, CTE and liquidus) matches p.2.

## 4. Required changes (FIX-FIRST), each with a page locator

### Content findings
- **C1 (P2; p.8 ¶3 vs Table 1 p.7).** The prose says "the median absolute percentage errors (MAPEs) on the training and validation subsets are both
  equal to ~16 %". Table 1 prints Raman MAPE training 17 and validation 17. The extract carries both without comment.
  Add a located `source_internally_inconsistent:` qualification on the Table 1 Raman row (or on the p.8 statement), quoting both values.
  Keep the Table 1 values as printed.
- **C2 (P2; p.12 ¶3–5 vs Table 2 p.13).** The prose says the MC-Dropout intervals for "all properties but Raman spectra, Elastic modulus and liquid heat
  capacity … encompass between 84 and 90 %". But Table 2 prints Abbe number at 81 %, which is not excluded. The prose also says "95 % of the test data, or more in some
  cases, now fall within the 95 % scaled confidence intervals, with the exception of the Abbe number". But Table 2 prints Density 87 % and Heat capacity 86 %.
  Add a located `source_internally_inconsistent:` qualification to the Table 2 context and to the two p.12 quotes. Do not change the printed values.
- **C3 (P2; Table 3 p.14, caption "Ranges of reported values are from [97, 98, 99, 47] … and [40, 51]").** The 20 literature cells are other workers' values:
  12 Vm range endpoints, 6 Cp intercepts and 2 Cp slopes. The whole record carries `method_class: model_derived`.
  Split the literature columns into their own context record (or per-block class) with `method_class: quoted_attributed`, attributed to
  Bottinga et al. 1983 [97], Lange & Carmichael 1987 [98], Liu 2006 [99] and Neuville & Le Losq 2022 [47] for Vm, and Richet & Bottinga 1985 [40] and
  Courtial & Richet 1993 [51] for Cp. The model columns stay model_derived. Model the split on extracts/shornikov-2007-cao-aluminosilicate-melts.yaml (STANDING RULING).
- **C4 (P3; extract `le_losq_2023_datasets_and_experiment_provenance.values.attribution`).** It reads "Le et al. (2023)". The authors are Le Losq & Baldoni (p.1).
  Fix the string. The extract's `source.citation` is already correct.
- **C5 (P3; quotes edited inside the "verbatim" list without marking).**
  - (a) p.8: the heat-capacity quote drops "[40] … of Courtial and Richet [51], following Giordano and Russell [52]" and rewrites "Cpliquid" as
    "liquid heat capacity".
  - (b) p.17: the Conclusion quote silently drops "(i.e., if acting as a network modifier or charge compensator)".
  - (c) p.4: "Compositions in the train, valid and test subsets show differences larger than 0.1 mol%" is a re-capitalised parenthetical from
    "we systematically checked that there was no sign of data leakage (compositions in the train, …)".
  Restore the text, or mark each elision with "…".

### Locator findings
- **L1 (p.8, last ¶).** "At null or very low Al2O3 concentrations, a strong Raman signal intensity near 1100 cm⁻¹ is observed." The extract says PDF p.9. The correct page is **p.8**.
- **L2 (pp.8–9).** "Upon the addition of Al2O3 in the float glass composition … NBO/T … decreases. A significant increase in intensity near 475 cm⁻¹ …"
  starts on the last line of p.8 and finishes at the top of p.9. The extract says p.9. Change it to **pp.8–9**.
- **L3 (Fig. 5).** The extract says "PDF pp.9-10". Fig. 5 and its caption are on **p.9** only (p.10 only refers back to it).
- **L4 (p.2).** "i-Melt was trained on melt and glass compositions in the Na2O-K2O-MgO-CaO-Al2O3-SiO2 system, …" is in the **Introduction** (¶3 of §1, p.2),
  not "Methods".

### Printed numbers / statements not carried (carry each as a located context statement; none is figure-only)
- N1 p.4 right col: "we have significantly less compositions including Na2O and K2O than other elements in the viscosity dataset, while the density dataset includes less MgO-bearing compositions (Fig. 2)".
- N2 p.4 last ¶ / p.5: "The model takes six inputs …" and "A total of 39 descriptors, including initial melt composition, are fed into a neural network composed of n hidden layers …".
- N3 p.4 right col: the stratified split is "a hack of the StratifiedGroupKFold function of the scikit-learn library version 1.1.2 [31]". GlassPy 0.3 is carried, so carry this too.
- N4 p.5 ¶1: "the second one returns 34 different values" (the list of output parameters follows).
- N5 p.6 last ¶: "The model RMSE on viscosity is lower than that of the more generalistic ViscNet machine learning model of melt viscosity [1.1 on its testing dataset, see 49]."
- N6 p.6 last ¶: "The accuracy on viscosity predictions of the model actually approaches … that of the thermodynamic model of the viscosity of alkali silicate melts of Le Losq and Neuville [10] (~0.2 log10 Pa·s)."
- N7 p.6 last ¶: "They are also lower than those affecting existing thermodynamic models for quaternary alkali aluminosilicate melts [11, 47], or than those affecting empirical models such as Russell and Giordano [48] for albite-anorthite-diopside melts."
- N8 p.8 ¶2: "For Tg, such an accuracy is better than that achieved by the first version of i-Melt (19 K), while for Sconf(Tg) it is comparable."
- N9 p.8 ¶2: "Such values are comparable to, or better than those for the original i-Melt version … For glass density, the model standard error further compares very well with those of dedicated parametric [e.g., 0.02 in 53] or machine learning [e.g., 0.02 to 0.03 in 54] models."
- N10 p.8 ¶2: "Glass elastic modulus is predicted to within 6 GPa, an accuracy that approaches those achieved by topological models [e.g., 55] but is higher than that of dedicated machine learning models [e.g., 3 GPa in 54]." The carried quote stops at "6 GPa", which loses the directional comparison.
- N11 p.9 ¶1: "In pure silica, this vibrational mode of Q4 units is typically observed at around 1200 cm⁻¹, but here the presence of aluminum in Q4 units causes a decrease in its frequency."
- N12 p.9 ¶2: "Typically, this vibrational mode yields a signal near 606 cm⁻¹ in silica."
- N13 p.9 ¶2: "… with the D1 signal near 490 cm⁻¹ assigned to breathing vibrations of four-membered rings".
- N14 pp.9–10: "The addition of Al into the glass structure leads to a decrease in the frequency of the D2 signal as Al replaces Si in the three-membered rings."
- N15 p.11 Eq. (1): log10 η = Ae + Be / (T[Sconf(Tg) + ∫_Tg^T Cp^conf/T dT]), with the variable/unit definitions printed below it.
- N16 p.11 Eq. (2): Cp^conf(T) = Cp^liquid(T) − Cp^glass(Tg). Also: Cp^glass(Tg) is from the Dulong and Petit limit.
- N17 p.11 Eq. (3): m ∝ 1 + Cp^conf(Tg)/Sconf(Tg).
- N18 p.11 last ¶: "Ca aluminate compositions present the highest fragilities and Cpconf(Tg)/Sconf(Tg) (magenta symbols in Fig. 7)."
- N19 p.11 ¶4–5: "We observe a general trend located in between those reported in [89] and [90]" and "Here, the scatter is much more limited" than the previous version.
- N20 p.12 ¶3 and last ¶: "The proportion of data included in the 95% confidence intervals is lower for Raman spectra, Elastic modulus and liquid heat capacity." Also "for most properties, the model conformal confidence intervals are reliable, even slightly conservative (i.e. the 95% confidence intervals actually encompass more than 95% of the data)."
- N21 p.13 ¶1: "Unfortunately, such scaling is not possible for latent properties, for which MC Dropout still allows obtaining a reasonable approximation of their confidence intervals."
- N22 p.13 §4 ¶1: "the error metrics for the extended model are comparable to, or even better than those of the original version".
- N23 p.14 ¶3: "Predicted partial molar volumes of oxide components fall close to those reported in previous publications (Table 3), but the model predicts that they depend on composition. A similar comment can be made for partial molar Cpliquid." This qualifies Table 3; attach it there.
- N24 pp.14–15: "In sodium aluminosilicate glasses with 75 mol% SiO2, this correlates with an increase in Na+ CN as the Al2O3/(Na2O+Al2O3) ratio increases … [101]."
- N25 p.15 ¶1: "[103] … proposed an increase in Na+ CN from 6 to 9 as Al is introduced into the glass network."
- **N26 (P2) p.16 ¶1:** "in calcium aluminosilicate glasses, … the partial contributions of Al in CN 4, 5 and 6 to the Cp^glass were estimated to be of 80.3, 79.9 and 70.0 J mol⁻¹ K⁻¹, at 300 K respectively [116]". Carry these as quoted_attributed (Richet et al. 2009 [116]).
- N27 p.16 ¶1: "the higher glass transition temperatures of Al-rich and alkaline-earth bearing melts naturally incur higher values of Al2O3 Cpliquid."
- N28 p.16 ¶1: "While no systematic relationship is observed between partial Al2O3 Cpliquid and the fraction of [5]Al and [6]Al, a systematic trend is observed for Al2O3 partial molar volume."
- N29 p.8 ¶2: the liquidus uncertainty is "an uncertainty that approaches those of dedicated polynomial and machine learning models [e.g., 50]". The carried quote stops at "~60 K".
- N30 p.10 ¶3: "The model accurately predicts the mixing effect of Ca and Mg on Tg in silicate and aluminosilicate compositions, as well as the Na-K mixing effect in float glass. Additionally, the variations of Tg upon mixing Na and Ca in silicate glasses or along the anorthite-albite binary are also well reproduced." Also attribute Fig. 6's data symbols to [82–85] (caption).

## 5. Advisories (not counted; main's call)
- A1 ledger: the extract commit **removes the `relevance:` line** ("…relevant to alkali shuttle.") from ledger/<sid>.yaml. Restore it unless removal is intended.
  `completeness: figure-only` is debatable: three tables are transcribed in full, even though none is a measured-property table. `full` with a note may be truer.
- A2 sidecar (pre-existing on mirror main, not touched by this branch): citation "Le et al. (2023)" should be "Le Losq C. & Baldoni B. (2023)".
  The licence is "not recorded". arXiv lists a licence per paper, and main may wish to record it before redistribution. The extract carries no licence statement.
- A3 the `le_losq_2023_datasets_and_experiment_provenance` context has no `experiment:` link. The other six do. Harmless after migration (7 contexts linked to the work), but inconsistent.
- A4 f2 counts are classed figure_only although they are printed numerals. That is acceptable as figure labels; the class choice is main's.
- A5 Table 1 "Median Absolute Error (MAE)" is the paper's own definition (not mean absolute error). The extract keeps "MAE" without expanding it. Consider adding the printed expansion so nobody reads it as mean.

## 6. Activity standard states and reduced rows
None. The paper reports no activity, ion-current ratio or reduced quantity. Table 3 is model partial molar Vm/Cp plus literature comparisons (C3).

!COMPLETE: rev-le-losq-2023-alkali-aluminosilicate-glasses — FIX-FIRST, pages read 23, rows checked 104, mismatches 9, printed numbers not carried 30
