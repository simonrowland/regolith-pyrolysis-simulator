# REVIEW (first, full fidelity): spaargaren-2025-disk-element-volatility @ dc3e16975fb2f48da5491ebdf4fc032dfbac0188

Reviewer: a regolith-empirical seat (Grok Bot), 2026-10-05 ~22:47 ET. Corpus batch 12, section A, sent by regolith-main.
Source: Spaargaren R.J., Herbort O., Wang H.S., Mojzsis S.J. & Sossi P., "Proto-planetary disk composition-dependent element volatility in the
context of rocky planet formation", A&A (2025). The corpus copy is the arXiv:2509.03724v2 manuscript ("A&A proofs: manuscript no. main",
8 Sep 2025). It has 31 PDF pages. Printed "Article number, page n" is PDF page n, so published_page = page = pdf_page_index+1.
This is a computational paper: GGchem equilibrium condensation plus derived planet compositions. It has no laboratory measurement.

VERDICT ON COMMIT: FIX-FIRST

rows checked 10, mismatches 22, printed numbers not carried 59

Counts, broken down:
- rows checked 10 = the 10 rows of Table 1 (30 cells plus the O `<` comparator), every cell checked digit by digit. **Table 1: 0 mismatches.**
  I also checked every context statement in the extract against the page: 16 `numerical_discussion` entries, 29 directional-quote entries (24 distinct), and
  the stellar-sample, model-scope, qualification and figure-locator fields.
- mismatches 22 = 5 content mismatches (C1–C5) + 17 locator mismatches (L1: 10 figure pages; L2: 7 prose locators). No Table 1 number is wrong.
- printed numbers not carried 59 = 47 located main-text numeric statements or equations (list N) + the 12 numbered Appendix A equations
  A.1–A.12. By my hand count those equations hold about 196 printed coefficients and thresholds, and the extract carries none of them.
  Figure axis ticks, colour bars and in-panel labels are figure-only and are not counted (see note F).
- Severity: P1 = N-A (Appendix A, the paper's main numeric product) and N-1 (Eq. 4 α/β); P2 = C1–C5 and the rest of list N; P3 = L1/L2 and the advisories.
  The extract has 0 observations, so no wrong number reaches a score or a ledger today.

## Setup (what I ran, and where)
- Mirror `mac-studio-256-1:Repos/regolith-corpus.git`, branch `hunt/spaargaren-2025-disk-element-volatility`. After `git fetch`, the tip is
  `dc3e16975fb2f48da5491ebdf4fc032dfbac0188`, the assigned sha. There are 2 commits over merge-base 3dab24d7: c294c653 (claim) and dc3e1697 (extract).
  Both are authored by Simon Rowland <simon@simonrowland.com> and carry no AI or co-author trailers.
  Changed files: `extracts/<sid>.yaml` (+606), `ledger/<sid>.yaml` (+12), `tables/<sid>/t1.csv` (+11) + `t1.provenance.yaml` (+13), and
  `raw/<sid>/sidecar.yaml` (−1 line). That last change removes an absolute `provenance.original_path` (/Users/...), which is correct.
- The review worktree on the Mac is SPARSE: `~/Repos/regolith-corpus/worktrees/rev-spaargaren-2025-disk-element-volatility`, created with
  `git worktree add --no-checkout` detached at dc3e1697. Its sparse-checkout is `'/*' '!/raw/*/*' '!/text/*/*' '/raw/*/sidecar.yaml' /raw/<sid>/* /text/<sid>/*`.
  It is 80 MB and was removed after delivery. Mac free disk at the start was 57 GB. I did not run build_index.py or migrate_pilot_extracts.py, and I wrote nothing tracked.
- Green is the read-only detached clone `~/ci-scratch/regolith-green-ro` at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`. I ran
  `~/Repos/regolith-pyrolysis-simulator/.venv/bin/python` with `PYTHONPATH=<green-ro>`.
  `engines/engines.local.toml` **exists** in green-ro (untracked). None of the checks below use it. The extractor reported it absent in its own worktree.
- Page images: I rendered all 31 pages with `pdftoppm -r 220 -png` on the VPS (1819x2573 px) and READ every one. Table 1 (p.3) was also read
  as a full-resolution crop, and so was the p.7 right column (the carbon estimate). pdftotext was used only as a lead.
- PDF sha256 `5a3317c5c7b0c1448672ee7bb9309384062d34646a6b25e36eb4bc4f1d393568` matches the sidecar, the extract's `corpus_sha256` and the census sha.

## Acceptance checks (all pass; the FIX-FIRST is a fidelity verdict, not a tooling one)
- Migrator: `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(Path('extracts/<sid>.yaml'))`, then `finalize()`, run with cwd = the sparse corpus worktree.
  Result: **hard issues after finalize 0**, all issues 0. Output: 1 work (10.1051/0004-6361/202556011), 1 experiment, 0 observations, 6 context rows.
- Payload survival (checked in `result.context_by_work`):
  - The Table 1 Ni row survives as GGchem 1282.0 / L03 1353.0 / W19 1363.0, with classes model_derived / quoted_attributed / quoted_attributed.
  - The O row survives as `GGchem_temperature_upper_bound_K: 400.0`, `GGchem_comparator_as_printed: "<"`, L03 180.0, W19 183.0.
  - `cell_material` and `background_pressure` survive as typed `not_applicable` maps.
  - The wrong carbon estimate (C1) also survives verbatim: "305(+53/−73) K".
- `evidence_for()` returns a known class with no fallthrough for model_derived, quoted_attributed and figure_only. There are no reduced rows.
- Fidelity: `PYTHONPATH=<green-ro> python <green-ro>/tools/validate_literature_extracts.py --check-fidelity-match <sparse worktree>/extracts/<sid>.yaml`
  → `OK: 1 extract file(s) valid` (exit 0). The script came from green; the extract path pointed at the corpus worktree.
- Corpus ledger: `python -m pytest -q -p no:cacheprovider tools/test_ledgers_valid.py` in the worktree → **708 passed**.
- `rg '/Users/|/private/'` over the extract, ledger, sidecar and tables/<sid>/ finds no matches.

## 1. Inventory of tables, equations and numeric statements (page: carried?)
- p.1 Abstract: 1,000 compositions; C/O ≤ 0.75, > 0.75, 0.84–1.04. Carried.
- p.2: Eq. 1 (d_i(T_C)=0.5). The MVE range 600–1300 K and fO2 < IW−6 are carried; Eq. 1 is a definition and needs no number.
  Timmermann "C/O ratios below 0.7" is not carried; minor background.
- p.3: **Table 1** is carried in full and correct. The §2.1 facts are carried (2500→400 K; 10⁻⁴ bar; 24 elements / 552 gas / 240 condensate species;
  20 K / 25 K agreement; H=12, He=10.93; log g<3.5; 91,982). Not carried: "Of the 16 selected elements" (N-2).
- p.4: Eq. 2 [N/O] coefficients are not carried (N-3). Eq. 3 is a definition. 8.0–10.0 and 8.99 are carried, but under the wrong page (L2).
  The 72 K (Ni) to 165 K (Al) metallicity spread is not carried (N-4), nor is log10 0.5 = −0.301 (N-5). 780 / 91,202 / 1,000 are carried.
- p.5: **Eq. 4, α = 3.676 ± 0.142 and β = −11.556 ± 0.436 (Wang et al. 2019a), is not carried (N-1, P1).** This is the devolatilisation law the whole paper applies.
  The 150–200 K drop and 0.936 / 0.94 / 0.875 / 0.828 are carried. The ~40 K Ca and >400 K Si values are carried, but with locator p.6 (L2).
  Not carried: N-6..N-11.
- p.6: The 100 K / 30% and 200 K / 100 K (Na) values are carried. Not carried: N-12..N-14.
- p.7: The fFe relation is not carried (N-18). 20% / 1700 K, 894 K, 875±45 K and 400 K are carried. The carbon estimate is carried WRONG (C1).
  Not carried: N-15..N-17.
- p.8: Fig. 7 and C/O≈0.7 are not carried (N-19).
- p.9: Eq. 5 (Σ/O stoichiometry) and the §4 numbers are not carried (N-20..N-22).
- p.10: The §5 numbers (40–50 vs 20–40 wt%, 2.5, <0.5, 5–12→20 mol%) are carried, but under "§6.1" (L2). Not carried: N-23..N-26.
- p.11: 2000 K at C/O=2.0, 1265 K and 1230 / 1316 K are carried. 2000 K is unattributed (C5). Not carried: N-27..N-30.
- p.12: 40–80 K, 70–160 K and 25–38 wt% are carried. Not carried: N-31..N-35.
- p.13: 19–41 wt% and up to 50 wt% are carried. Not carried: N-36.
- p.14: 0.6, 0.95 and 12.36% are carried. Not carried: N-37..N-40.
- p.15: The Mars / Vesta α, β and 70 wt% are carried and correct.
- p.16: The inhibition temperatures 3000/1400/1000/600 K are carried. Not carried: N-41, N-42.
- p.17: 1 m / 1,000 years is carried, but under "page 16" (L2). Not carried: N-43.
- pp.18–19 Conclusions: carried. One quote is truncated (C4), and "12%" sits on p.19, not p.18 (L2). Not carried: N-44.
- pp.20–21 References: none.
- **pp.22–25 Appendix A: Eqs. A.1–A.12 are NOT carried at all (N-A, P1).**
- pp.26–27 Appendix B: two quotes are carried. Not carried: N-45, N-46.
- pp.27–28 Appendix C: two quotes are carried.
- pp.29–31 Appendix D: 70 wt% and C/O ≥ 0.9 are carried. Ca+Al is mis-bound (C2). Not carried: N-47.
- Figures 1–16, B.1–B.3, C.1–C.2, D.1–D.2 are figure_only. None is digitised, which is correct. Ten page locators are wrong (L1).

## 2. Table 1 (p.3), digit by digit: csv/extract vs print
| row | GGchem csv / print | L03 csv / print | W19 csv / print | match |
|---|---|---|---|---|
| O | `<`,400 / <400 | 180 / 180 | 183 / 183 | yes |
| Na | 941 / 941 | 958 / 958 | 1035 / 1035 | yes |
| Mg | 1336 / 1336 | 1336 / 1336 | 1343 / 1343 | yes |
| Al | 1652 / 1652 | 1653 / 1653 | 1652 / 1652 | yes |
| Si | 1320 / 1320 | 1310 / 1310 | 1314 / 1314 | yes |
| S | 661 / 661 | 664 / 664 | 672 / 672 | yes |
| Ca | 1512 / 1512 | 1517 / 1517 | 1535 / 1535 | yes |
| Ti | 1571 / 1571 | 1582 / 1582 | 1565 / 1565 | yes |
| Fe | 1332 / 1332 | 1334 / 1334 | 1338 / 1338 | yes |
| Ni | 1282 / 1282 | 1353 / 1353 | 1363 / 1363 | yes |

The extract's `rows` and the `fidelity_samples` copy are identical to the CSV.
- Table note (verbatim): "Condensation temperatures T_C (K) of elements in the solar disk simulated with GGchem (± 5 K) using solar abundances from
  Lodders (2003), T_C values from Lodders (2003) (L03), and T_C values from Wood et al. (2019) (W19)".
  The extract has the ±5 K uncertainty on GGchem only, the L03/W19 attributions, and the composition "solar abundances from Lodders (2003)". All correct.
- method_class: GGchem = model_derived and L03/W19 = quoted_attributed. Both are correct.

## 3. Experiment / bench facts and typed absences
The paper is purely computational ("we use the open-source thermochemical equilibrium code GGchem", p.3 §2.1).
So there is nothing to check for cell, orifice, ionisation, calibration, background pressure, temperature sensor, sample preparation or activities.
The extract types every one of these `not_applicable` with a reason. I agree with each.
- `method: engine_evaluation` is the right token.
- `pressure_bar: 0.0001` matches "we first analyse condensation behaviour at 10⁻⁴ bar, representative for the solar disk at 1 AU" (p.3). Correct.
- "cools down from 2500 K to 400 K" (p.3): 2500 / 400 are correct.
- The databases are correct: NIST-JANAF (Chase Jr et al. 1982) and SUPCRTBL (Johnson et al. 1992; Zimmer et al. 2016).
- No activities, so no standard states. Correct.

## 4. Required changes (FIX-FIRST), each with a page locator

### Content mismatches
- **C1 (p.7, §4, last line of the right column).** The print reads "(T^C_C,eff = 305^{+73}_{−135} K)". The extract's `numerical_discussion` (page 7) and
  report both say "305(+53/−73) K". Change it to **305 +73/−135 K**, attributed to Wang et al. (2019a). I checked the crop at full 220 dpi resolution.
- **C2 (p.29, Appendix D, ¶2).** The print says that Ca+Al reservoirs at **low** C/O (C/O ≤ 0.9) occur when planets "primarily sample high-temperature space".
  It then says: "At higher C/O, both Ca and Al become less refractory, and planets forming with major Ca+Al reservoirs occupy a small region of
  (α,β) parameter space, primarily at α > 11 ... This enrichment of Ca and Al disappears at high C/O".
  The extract's p.29 statement "Ca+Al reservoirs occur mainly for α>11 at low C/O" binds α > 11 to the wrong C/O regime.
  Rewrite it as: low C/O (≤ 0.9) → high-T sampling; higher C/O → α > 11; high C/O → enrichment disappears (graphite stable to very high T).
- **C3 (p.3, §2.1).** `model_scope` lists Ti among the constrained elements ("constrains C, O, Na, Mg, Al, Si, S, Ca, Ti, Fe and Ni") and lists it again as an
  additional element. The print says the study constrains "the nine most abundant elements in the Earth (O, Mg, Si, Fe, Ca, Al, Na, Ni, and S),
  as well as carbon", and includes "H, He, and additional elements (Cl, K, Ti, and N)". Remove Ti from the constrained list.
- **C4 (p.18, Conclusions, 2nd bullet).** The quote "the condensation temperatures of rock-forming elements decrease with increasing C/O" (it appears twice
  in `directional_claims...`) drops the printed scope "**In oxygen-rich environments**, ...". Restore the qualifier, because
  past the critical C/O the trend changes. Delete the duplicate quote entries. "For disks with C/O > 1.04 ...", "Sulphur remains ...",
  "each element except Si ..." and "the condensation of Mg, Si, Ca, and Al ..." are each listed twice.
- **C5 (p.11, §6.1).** "Graphite condensation reaches 2000 K at C/O=2.0" is carried as if it were the paper's own result. The print reads "As T_C^graphite rises from
  below 1000 K at C/O < 0.98 to 2000 K at C/O = 2.0 (Lodders & Fegley Jr 1995)". Attribute it (quoted_attributed) and add the 1000 K / C/O < 0.98 lower end.

### Locator mismatches
- **L1, figure-only locators (10 wrong).** Each figure's caption page, checked on the images:
  | figure | correct page | extract says |
  |---|---|---|
  | Fig 4 | p.6 | 7 |
  | Fig 5 | p.6 | 8 |
  | Fig 6 | p.7 | 9 |
  | Fig 7 | p.8 | 10 |
  | Fig 8 | p.9 | 11 |
  | Fig 9 | p.10 | 12 |
  | Fig 10 | p.11 | 13 |
  | Fig 11 | p.13 | 14 |
  | Fig 12 | p.14 | 15 |
  | Fig B.3 | p.28 | 27 |

  These are correct: Figs 1 (p.4), 2 (p.5), 3 (p.6), 13 (p.16), 14 (p.17), 15 (p.18), 16 (p.19), B.1 (p.26), B.2 (p.27), C.1 (p.28), C.2 (p.29),
  D.1 (p.30) and D.2 (p.31). Fix `pdf_page_index` too, which is page−1.
- **L2, prose locators (7 wrong).**
  1. The quote "Fe and Ni only depend on their own abundances" is located "page 5, §3.1". It is not on p.5. It appears on p.18 (Conclusions) and p.22 (A.2: "Aside from metallicity, Fe and Ni only depend on their own abundances").
  2. The "Ca rise about 40 K near C/O=1.045" and "Si rises by over 400 K (C/O=1.022–1.045)" entry is located "page 6". Both are on **p.5**, §3.2.
  3. The "40–50 wt% vs 20–40 wt%; 5–12 → 20 mol%; Mg/Si 2.5; <0.5" entry is located "page 10, §6.1". It is page 10, **§5**.
  4. The p.13 entry adds "high-C/O/Vesta-like cases reach up to 70 wt%". That clause is not on p.13. It is on p.15 §6.3 and p.29 App. D. Move it or remove it.
  5. "an Fe grain of 1 metre ... within 1,000 years" is on **p.17**, not p.16.
  6. "12% of rocky planets have more than 1 mol% C" is on **p.19**, not p.18.
  7. The metallicity range 8.0–10.0 and M⊙ = 8.99 are on **p.4**, not "page 3, §2.2" (they are in §2.2, but on page 4).

### N. Printed numbers not carried (carry each as located context; none is figure-only)
- **N-A (P1), Appendix A, pp.22–25.** None of the parametrisation equations is carried. These are the paper's main numeric product:
  - A.1: ten metallicity polynomials T_M^X(M) for Na, Mg, Al, Si, Ca, Fe, Ni, C, O and S, and the C/O = 1.30 series note.
  - A.2 / A.3: Fe and Ni polynomials (4.776, 12.77, 964.64; 4.396, 18.97, 986.76) and M⊙ = 8.99.
  - A.4: the asymptote form.
  - A.5 Ca (p.23): 3.052·10³, −5.81·10³, 4.001·10³, 1265.77; C/O_crit = 0.95 − 7.245(Ca/O − Ca/O⊙); C/O_upper 1.045; a 1919.6, b 1.356, n 0.1396,
    α −1.76·10⁵, β 1.726·10⁵; 39.52, 53.91; the Ca/Al polynomial 2.83, −17.39, 27.78, −12.05, 1497.29.
  - A.6 Al: C/O' = C/O + 4.47 Al/O − 0.022; −2.6649·10⁵, 7.89·10⁵, −7.79·10⁵, 2.575·10⁵, 1231.46; crit 0.95, upper 1.039; a 9.563·10⁴, b 75.65,
    n 0.03, α −2.23·10⁴, β 2.15·10⁴; 44.67, 55.34.
  - A.7 Mg: 1.184, −0.0856; −5.153·10⁵, 1.418·10⁶, −1.300·10⁶, 3.983·10⁵; 1061.44, −40.65; crit 0.88, upper 0.94; a 1387.91, b 5.697, n 0.588,
    α −8193.75, β 7787.28; 36.33, 31.86.
  - A.8 Si (p.24): three polynomial segments, C/O − 405.23A_O, crit 0.85 − 1.8(Si/O − Si/O⊙), middle 1.022, upper 1.0445; a 750.48, b 5.7914·10⁻², n 3.333,
    α −1, β 1.547; f_Mg/Si (117.69; −67.18, 206.82; 161.89; 0.73, 1.64); f_cations (8.523, 448.63; −890.11, 1401.59; Σ/O_crit 1.0695).
  - A.9 Na: −55.844, 915.76, 862.77; crit 0.77 − 28.82(Na/O − Na/O⊙); a 947.54, b 56.34, n 1463.62, α −2.52·10⁻³, β 1.007; 47.14, −182.86, −148.80;
    0.836, 1.499, 1.975; the Na/Cl polynomial 5.971·10⁻², −1.879, 18.96, "8.77.76".
  - A.10 C (p.25): 2413.80, −1024.77, 743.10; Σ/O_crit 1.0181, C/O_crit 1.079; two asymptote parameter sets.
  - A.11 O: 69.39, 605.66, 1251.52; 154.10, 588.53, 1012.52; logistic −13.539 and 0.847.
  - A.12 S: 700 K, 1100 K, Σ/O = 1.005; −35.40, 811.91, −6085.17, 15540.68; 10480.54; the Mg/S polynomial; the Σ/O upper polynomial, 1.268 and 406.06.

  Transcribe them as printed and keep the printed defects. Do not correct them. Mark each with a located
  `source_internally_inconsistent` / garbled note:
  - T_M^Ni "3.94M² + 2.92 + 931.38" is missing an M.
  - The Na/Cl constant "8.77.76" is garbled.
  - The A.10 C parameter "β − 1.015" is missing its "=".
  - A.6 Al and A.7 Mg use "C/O" in the linear term where the others use C/O'.
  - A.12 is labelled T_C^O but is the S equation.

  The schema has no numeric home for fitted model equations. Per the standing ruling, carry them as located context
  (e.g. a `model_parametrisations` context row keyed by equation, with coefficients as numbers) and list it under AMENDMENTS PROPOSED.
- **N-1 (P1), p.5, Eq. 4:** log f_i = α log T_C^i + β with α = 3.676 ± 0.142 and β = −11.556 ± 0.436 (Wang et al. 2019a). Carry as quoted_attributed.
- N-2 (p.3): "Of the 16 selected elements" (and which are commonly available).
- N-3 (p.4, Eq. 2): [N/O] = log10(10^−1.732 + 10^(A_O − 12 + 2.19)).
- N-4 (p.4): T_C variations "ranging from 72 K for Ni to 165 K for Al" across the GALAH metallicity span.
- N-5 (p.4, §2.3): the 50% threshold "log10 0.5 = −0.301".
- N-6 (p.5): T_C^Mg (C/O = 0.94) and T_C^Al (C/O = 1.039) "decrease by ∼ 50 K before stabilizing".
- N-7 (p.5): MgS stable only over "20-100 K".
- N-8 (p.5): Mg O-depleted condensation at C/O as low as 0.75 (Mg/O > 0.2) and as high as 0.92 (Mg/O < 0.05).
- N-9 (p.5): transition widths of 0.05 C/O (Ca, Al), 0.26 (Si) and 0.17 (Mg).
- N-10 (p.5): forsterite T_C = 1354 K and enstatite T_C = 1316 K (Lodders 2003, quoted_attributed).
- N-11 (p.5): at Mg/Si > 1.6, forsterite is the primary Si- and Mg-bearing condensate.
- N-12 (p.6): solar C/O = 0.55 (Fig. 4 caption) and solar Mg/Si = 1.23 (Fig. 5 caption). See also N-27.
- N-13 (p.6): Ca/Al < 2.0 (gehlenite / perovskite) vs higher Ca/Al (larnite).
- N-14 (p.6, §3.3): Na critical C/O = 0.819.
- N-15 (p.7): albite ~50 K higher when quartz is stable, only at Mg/Si ≤ 0.83; nepheline stable up to Mg/Si = 1.97 (the ≥ 1.5 quote is carried).
- N-16 (p.7): O 50% T_C "only reached at 180 K (Lodders 2003)"; "depleted ... by more than 99%"; the Earth-Sun O depletion factor "only ∼80%".
- N-17 (p.7): at C/O > 0.8, C "initially condenses as graphite above 1100 K".
- N-18 (p.7): T_C^Fe = 4.776A_Fe² + 12.77A_Fe + 964.64.
- N-19 (p.8): graphite "begins at C/O≈ 0.7"; the Fig. 7 disks are C/O = 0.9 and 1.25, with a 0.25 mol% display threshold.
- N-20 (p.9, Eq. 5): Σ/O = (ε_C + 2ε_Si + ε_Mg + ε_Ca + 1.5ε_Al + 0.5ε_Na)/ε_O.
- N-21 (p.9): graphite field extends at C/O > 1.04; CH₄ "<750 K"; devolatilisation cut-off "1391 K for Earth"; T_O,eff "always greater than 500 K".
- N-22 (p.9): FeS primary at C/O < 0.8; MgS/CaS at Σ/O > 1.005; T_S,eff "rarely exceeds 1000 K".
- N-23 (p.10, §5): Mg-depleted planets at C/O > 1.04; Mg+Si-depleted at C/O = 0.84–1.04; star-like Fe/Mg/Si at C/O ≤ 0.84.
- N-24 (p.10): "Ca and Al remain refractory up to C/O = 0.95".
- N-25 (p.10, §6.1): T_C^Si = forsterite only if Mg/Si > 1.5; MgO at Mg/Si ≥ 2.0; SiO₂ at Mg/Si ≤ 0.87.
- N-26 (p.10): T_C drops by 100 K as C/O goes from 0 to 0.8; then a rapid 100–300 K drop.
- N-27 (p.11, Fig. 10 caption): solar C/O = 0.54 (Asplund et al. 2009). This is a **source-internal inconsistency** with 0.55 on p.6. Carry both and flag them.
- N-28 (p.11): Mg and Si moderately volatile at C/O as low as 0.75; N/O restricts O only at N/O > 0.75, and all disks have N/O ≤ 0.25.
- N-29 (p.11): T_C^graphite below 1000 K at C/O < 0.98 (with C5).
- N-30 (p.11): previous Ca estimates of 1240 K (Larimer & Bartholomay 1979) and 1295 K (Lodders & Fegley Jr 1995).
- N-31 (p.12): Si is refractory above C/O 1.02; SiC primary at C/O as low as 0.75; <50% of Si as SiC at C/O < 1.02; plateau at C/O = 1.10;
  T_C^SiC = 1633 K (L&F 1995); CaS limits Si "to at most 6%".
- N-32 (p.12): T_C^Mg stabilises at 1060 K above C/O = 0.94, vs Lodders et al. 1080 K.
- N-33 (p.12): at Σ/O > 1, T_S,eff increases by 100 K; pyrite at [Fe/S] < −0.67; the sample has [Fe/S] ≥ −0.45.
- N-34 (p.12): silicates condense as liquid at P > 10⁻² bar (Ebel et al. 2006).
- N-35 (p.12, §6.2): core fraction underestimated "by ∼ 0.3%"; 0.8% of planets Mg-depleted vs previous 10–50%; Mg/Si < 1.0 vs < 0.75 criterion; 5% have Mg/Si > 1.6.
- N-36 (p.13): C/O 0.9–1.02 "rarely have cores smaller than 40 wt.%"; dichotomy 40–50 wt% (C/O 0.85–1.0) vs 19–41 wt% (< 0.75); oxidized Fe condenses only below 500 K.
- N-37 (p.14): earlier graphite thresholds C/O > 0.8 (Bond et al. 2010b) and as low as 0.65 (Moriarty et al. 2014).
- N-38 (p.14): 11.90% of GALAH stars have C/O > 0.8; Hypatia (within 200 pc) 3.71% and 2.84%.
- N-39 (p.14): planet C budget "20-30 mol% at C/O=1.0", rising to "40-80 mol%" above C/O 1.04; silicates still form below 1000 K.
- N-40 (p.14): MgS/CaS stable mantle phases at fO₂ < IW − 2; "less than 2% of our sample has more than 1 mol% S".
- N-41 (p.15–16): gas-solid reactions rapid above 600 K; grains "< 0.01 µm"; sequential graphite down to C/O 0.6 (Moriarty et al. 2014).
- N-42 (p.16): sequential T_C equals equilibrium for T_C > 1000 K, except Ca at T₀ > 1800 K; Na/K sequential T_C "several 100 K lower".
- N-43 (p.17): graphite only stable above 700 K (T_C,eff^C = 0 if T₀ < 700 K); anorthite forms at 1387 K (Lodders 2003).
- N-44 (p.18): mantle S "does not typically exceed 1 mol%"; graphite buffers the gas to C/O = 1.0.
- N-45 (p.26, App. B): CaS is the primary Ca condensate at C/O as low as 0.8.
- N-46 (p.27): Fig. B.2 / B.3 stability threshold 10⁻⁷ n_H; albite field +50 K once SiO₂ is stable.
- N-47 (p.29, App. D): CMF → 0 at α > 11 and β < −35; Ca+Al reservoirs at low C/O (C/O ≤ 0.9).

### D. Directional statements to add (brief item 6; quote verbatim with locator)
At least these printed directional or comparative statements are missing:
- p.5: "Refractory elements transition to O-depleted condensation at higher C/O ratios ... than the moderately refractory elements".
- p.5: "Although at near-solar C/O Si is the most volatile of these four elements, it becomes the most refractory at high C/O, followed by Ca and Al
  with Mg becoming the most volatile". The extract's ellipsis quote drops exactly this direction.
- p.6: "T_C^Mg increases with both O and Mg abundances"; "At higher Ca/Al, the less refractory larnite ... reducing T_C^Ca".
- p.7: nepheline "is more volatile"; sodium metasilicate has "an even lower condensation temperature".
- p.11: "For Na, this T_C drop is less severe".
- p.12: "Si becomes increasingly refractory as C/O rises above 1.02".
- p.12: "Rock-forming elements scale more strongly with P and M the more refractory they are"; "Fe and Ni scale more strongly with P and more weakly with M".
- p.12: graphite "depends strongly on metallicity but not at all on gas pressure, while SiC increases moderately with both".
- p.15: "Mars is less volatile-depleted than Earth"; "Vesta has a much steeper, more volatile-depleted pattern".
- p.16: "T_C^Cl is higher under sequential condensation"; "planets in high-C/O conditions may be more depleted in Na and K".
- p.17: "increased volatility of Na, K, and S in solar-like compositions"; "sulphur becomes more refractory ... and oxygen more volatile".
- p.28: "Graphite also remains stable to lower temperatures as C/O increases".

Also restore the verbatim spelling "Vesta uniformally" (p.28); the extract normalised it to "uniformly". This is cosmetic.

## 5. Citation, licence, sha, absolute paths
- The extract citation and DOI 10.1051/0004-6361/202556011 are correct for the A&A article.
  Advisory: the asset is the arXiv:2509.03724v2 manuscript ("A&A proofs"), while the sidecar says `access: publisher`. Consider recording `arXiv:2509.03724v2`
  so that page locators are tied to that layout. The sidecar `citation` still contains the HTML entity `&amp;`. That is pre-existing, and this branch did not change it.
- No licence statement is in the extract or the sidecar. This branch did not add one, and I count no mismatch for it.
- The sha256 of the PDF matches the sidecar and the extract. There are no absolute paths. The branch removes the one absolute path the sidecar had, which is correct.

## 6. Advisories (not counted)
- The key `oxygen_and_sulphur` holds the statement "C and S show non-monotonic condensation". Rename it (e.g. `carbon_and_sulphur_non_monotonic`).
  The print (p.7) says "the traditional 50% T_C description fails to capture ... C, O, and S".
- p.3 compares the results with "Wood (1993)" (EPSL 117, 593), while the Table 1 W19 column is "Wood et al. (2019)" (Am. Mineral. 104, 844). The extract carries both as printed,
  which is correct. A one-line note that these are two different references would stop a reader from merging them.
- **F (figure-only, not counted):** Figs. C.1/C.2 print in-panel labels "C stability, T=848.0K / 788.0K / 773.0K / 742.0K" (C/O 0.8, 0.9, 1.0, 1.2).
  These are printed numbers inside figures. Optionally carry them as located figure annotations. Do not digitise contours or colour bars.
- The extract's `extraction.method` says "200 dpi or higher". The report says p.26 was re-rendered at 220 dpi. That is fine.

!COMPLETE: rev-spaargaren-2025-disk-element-volatility — FIX-FIRST, pages read 31, rows checked 10, mismatches 22, printed numbers not carried 59
