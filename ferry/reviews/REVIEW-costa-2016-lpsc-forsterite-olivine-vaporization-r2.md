# Confirm review, round 2: costa-2016-lpsc-forsterite-olivine-vaporization

VERDICT ON COMMIT: LAND b6386273fe69edc8ace0e17e083c69c7766fb95e

rows checked 34, mismatches 0, printed numbers not carried 0

- Reviewer: regolith-empirical corpus review seat (batch2 section A), 2026-10-05 (ET).
- Commit under review: `b6386273fe69edc8ace0e17e083c69c7766fb95e` on `hunt/costa-2016-lpsc-forsterite-olivine-vaporization`, fetched from `mac-studio-256-1:Repos/regolith-corpus.git`. `git rev-parse origin/hunt/...` after the fetch printed exactly this sha.
- Parent (round-1 reviewed tip): `ece38b9a15df606856f9b3431d264cb5ded83d93`. The fix commit changes one file only: `extracts/costa-2016-lpsc-forsterite-olivine-vaporization.yaml` (+77/−19). The table CSV, table provenance, ledger and sidecar are unchanged since round 1.
- Worktree: a sparse, detached worktree at `~/Repos/regolith-corpus/worktrees/rev2-costa-2016-lpsc-forsterite-olivine-vaporization`, created with `--no-checkout`. Sparse set: `/*`, `!/raw/*/*`, `!/text/*/*`, `/raw/*/sidecar.yaml`, `/raw/<sid>/*`, `/text/<sid>/*`. It used 43 MB. The worktree was clean before and after the review.
- Green reader checkout: `~/ci-scratch/regolith-green-ro` at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`. The interpreter was `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python` with `PYTHONPATH` set to that checkout and `PYTHONDONTWRITEBYTECODE=1`. That checkout was clean before and after. **`engines/engines.local.toml` EXISTS in this green checkout** (1331 bytes, dated 2026-10-04). None of the checks below read it.
- Page images: both PDF pages were rendered with `pdftoppm -r 240 -png`, giving 2040×2640 px per page. I read each page in full and in quadrants. I also read zoomed crops of the Figure 9 fit legend, Table 1 and the Acknowledgments/References block. The text layer was not used as evidence. Pages read: 2.
- Stance: every round-1 finding was treated as NOT FIXED until the page image and the tip proved it fixed. The fixer's report is a claim, not evidence.

## 1. Round-1 findings, re-checked one by one

| # | Round-1 finding (review.md) | What the tip has now | Page evidence | Status |
|---|---|---|---|---|
| 1 | All five Figure 1 rows carried `T_range_K: [1750, 2250]` and `temperature_range_K_as_printed: '1750-2250 K'`, binding the overall study interval to the below-melting Figure 1 series. | Both fields are removed from all five `costa_2016_fig1_*_pressure_figure_only` rows (Fe, Mg, SiO, O, O2). 1750–2250 K is carried once, as `overall_study_temperature_range_K_as_printed: '1750-2250 K; overall study interval, not endpoints for the Figure 1 below-melting series.'`, in `costa_2016_experimental_sample_and_characterization` (p.1, Experimental methods). The Results sentence “Vapor pressure measurements below the melting point are shown in Fig. 1.” is now quoted in `costa_2016_results_qualifications` (p.1, Results). No axis limits or dot positions were turned into endpoints. | p.1 Methods: “The vaporization of olivine of composition Fa0.07Fo0.93 has been studied from 1750-2250 K.” p.1 Results: “Vapor pressure measurements below the melting point are shown in Fig. 1.” The Figure 1 top axis runs 2000–1750 K. | **FIXED.** Migration check: no migrated observation's repr contains 1750 or 2250. All 8 observations have `temperature_K=None`. The only remaining unknown-quantity reason is the `bound_not_point_ordering` figure-only reason. |
| 2 | Summary prints `Fo93Fa0.07` (no decimal before 93), but `experiments[0].sample.printed_composition` claimed `Fo0.93Fa0.07` at the Summary locator. | `printed_composition` is still `Fo0.93Fa0.07`, now located at `figure: '1'` with the note “Caption spelling; Summary prints Fo93Fa0.07, carried separately with the source inconsistency.” The new context row `costa_2016_summary_spelling_and_examples` (p.1, Summary) carries `composition_as_printed: Fo93Fa0.07` with an explicit inconsistency note naming all three spellings. The Methods context row now carries its own ordering, `Fa0.07Fo0.93`. | p.1 Summary: “olivine (Fo93Fa0.07) samples” (zoomed: no decimal before 93). p.1 Figure 1 caption: “olivine (Fo0.93Fa0.07)”. p.1 Methods: “Fa0.07Fo0.93”. | **FIXED.** All three printed spellings are carried at their own locators. The Summary spelling is not silently repaired. The migrated sample keeps the figure-1 locator and its note. |
| 3 | `costa_2016_data_qualifications_and_comparisons` had one p.1 “Summary and Experimental methods” locator but held p.1 Results and p.2 Discussion/Summary sentences. It also held an edited excerpt (“The data is consistent …”) labelled verbatim. | The row is split into six rows: Summary p.1 (now `units: verbatim statement excerpts`), Results p.1, Experimental methods p.1, Discussion p.2, Summary p.2, plus the existing background row. The edited excerpt and the truncated duplicate “A comparison … previous measurements” are both removed. The complete Discussion sentence is restored. | I checked every quoted sentence against the page image word by word (list in §3). All 18 statements match the print. The two Summary p.1 items are true fragments and are now labelled as excerpts. | **FIXED.** Statement conservation: there were 18 statements before. Two edited or duplicate statements were removed, one full sentence was restored, and one new Results sentence was added, giving 18 now. No qualifying or directional statement was lost. |
| 4 | `sample.pretreatment` said the sample was “heated in vacuum at 1853 K for 10 h to remove impurities”. The source says impurities “could be removed”. | The value is now: “Impurities in the mineral could be removed by heating in a vacuum at 1853 K for 10 h; the paper does not explicitly state that every measured sample received this treatment.” The context row's `pretreatment` reads “Impurities could be removed by heating in a vacuum at 1853 K for 10 h.” | p.1 Methods: “It was found that impurities in the mineral could be removed by heating in a vacuum at 1853 K for 10 h.” | **FIXED.** The qualifier is kept and the 1853 K / 10 h figures match. The migrated `Sample.pretreatment` holds the qualified string. (See advisory A3 on style.) |
| 5 | Eleven printed numeric or identifier statements were not carried: 2 affiliation addresses, CoRoT-7b, grant NNX13AE52A, and 7 reference entries. | New located context rows: `costa_2016_affiliation_identifiers` (p.1, affiliation block, 2 entries), `costa_2016_summary_spelling_and_examples` (`exoplanets_as_printed: Kepler 10-b and CoRoT-7b`), `costa_2016_grant_identifier` (p.2, Acknowledgments) and `costa_2016_reference_numeric_metadata` (p.2, References, 7 entries). All are `quoted_unattributed` text, and no scientific rows were manufactured. | Checked digit by digit against the zoomed p.1 header and p.2 right column: 21000 / MS 106-1 / 44135; Campus Box 1169 / 1 Brookings Dr / 63130-4899; CoRoT-7b; NNX13AE52A; [1] GCA 64, 939-955 (2000); [2] ApJ 703, L113-L118 (2009); [3] ApJ 801:144 (15pp) (2015); [4] Nat Geo 8, 918 (2015); [5] ApJ 767:L12 (6 pp) (2013); [6] Calphad, 22, 85-125 (1998); [7] Metall. Mater. Trans. B, 35, 877-889 (2004). | **FIXED.** 11/11 are carried and every digit matches. (See advisory A1 for non-numeric typography.) |

Summary: 5/5 round-1 items are FIXED, 0 are PARTIAL and 0 are NOT FIXED.

## 2. Collateral-damage check on the fix diff

I read every hunk of `git diff ece38b9a b6386273` and checked each against the page.

- **Removed fields.** Only the five `T_range_K` and five `temperature_range_K_as_printed` lines on the Figure 1 rows were removed, plus the old mixed locator and statements, which were re-homed. No numeric value, uncertainty, species, locator, derivation or typed absence was dropped. Table 1 context rows, the Figure 9 fit annotations, the bench, the experiment typed absences, the CSV and the provenance are byte-identical to round 1.
- **Moved statements.** Each re-homed statement sits under the correct page and section: Summary p.1, Results p.1, Experimental methods p.1, Discussion p.2 and Summary p.2. “Our data were compared to activities calculated from two thermodynamic databases in the literature.” starts at the bottom of p.2 col.1 (Summary) and ends at the top of p.2 col.2. Its p.2 Summary locator is correct.
- **New rows.** The new rows have no `quantity` key, unlike the older context rows. The migrator accepts them: 23 context rows migrate, with 0 hard issues, and every payload survives verbatim (see §5). No new row carries a scoreable numeric value.
- **Typed composition re-location.** Moving `printed_composition` to `figure: '1'` is correct: the caption prints exactly `Fo0.93Fa0.07`. The Figure 1 rows' `condensed_composition_as_printed: Fo0.93Fa0.07 olivine` is consistent with that caption.
- **Paths.** `rg "/Users/|/private/"` on the changed extract, `tables/<sid>/` and `ledger/<sid>.yaml` returned no matches (exit 1).
- **YAML.** Strings containing commas in the new rows are quoted. Block or flow mappings with commas are not unquoted. The validator and migrator load the file cleanly.

No collateral damage found.

## 3. Statement-level audit (directional/qualifying quotes vs page image)

All match the print exactly, including the source's own grammar (“lead to”, “were compared”, “data is consistent”).

| Row | Statement | Page | Match |
|---|---|---|---|
| data_qualifications_and_comparisons | “Our results confirm preferential evaporation of fayalite (Fa) relative to forsterite (Fo) reported by [1]” (excerpt) | p.1 Summary | Yes (excerpt, labelled) |
| data_qualifications_and_comparisons | “near-equilibrium vaporization” (excerpt) | p.1 Summary | Yes (excerpt, labelled) |
| results_qualifications | “Vapor pressure measurements below the melting point are shown in Fig. 1.” | p.1 Results | Yes |
| results_qualifications | “Comparing the measured partial pressure over olivine to those over pure materials lead to thermodynamic activities as a function of inverse temperature.” | p.1 Results | Yes |
| results_qualifications | “Partial molar enthalpies are determined from the slope of this plot and listed in Table 1.” | p.1 Results | Yes |
| methods_qualifications | “An iridium cell was fairly inert to olivine below the melting point; however there was some reaction above the melting point.” | p.1 Methods | Yes |
| methods_qualifications | “The onset of melting was observed at ~2050K, consistent with the literature.” | p.1 Methods | Yes |
| methods_qualifications | “It was also observed that significant losses of FeO occurred at the melting point.” | p.1 Methods | Yes |
| discussion_qualifications | “These are constructed with all available thermodynamic and phase data at the time of the assessment and provide a self-consistent thermodynamic description of this system.” | p.2 Discussion | Yes |
| discussion_qualifications | “A comparison of the data from this study to the assessments indicates that the data is consistent with previous measurements and allows inferences about the exact phase composition of the olivine.” | p.2 Discussion | Yes (complete sentence restored) |
| discussion_qualifications | “It also suggests improvements for future assessments.” | p.2 Discussion | Yes |
| discussion_qualifications | “Table 1 compares the measured partial molar enthalpies of each component to those calculated from the two databases discussed above.” | p.2 Discussion | Yes |
| discussion_qualifications | “Agreement between our measurements and the database of Fabrichnaya [6] is reasonable.” | p.2 Discussion | Yes |
| discussion_qualifications | “The relatively low partial enthalpy of MgO and high partial enthalpy of SiO2 are important and need to be included in future assessments.” | p.2 Discussion | Yes (low MgO / high SiO2 direction matches) |
| summary_page2_qualifications | “Thermodynamic data was taken on this composition below the melting point.” | p.2 Summary | Yes |
| summary_page2_qualifications | “The equilibrium constant for each component’s (MgO, Fe/FeO, and SiO2) were compared to the equilibrium constant for the pure material.” | p.2 Summary | Yes |
| summary_page2_qualifications | “Thermodynamic activities and partial molar enthalpies were derived for these components in olivine.” | p.2 Summary | Yes |
| summary_page2_qualifications | “Our data were compared to activities calculated from two thermodynamic databases in the literature.” | p.2 Summary (col.1→col.2) | Yes |
| background_scope_numeric_context | 2 comparative statements: “Many of these planets …” and “Olivine is also one of the most abundant …[1,2].” | p.1 Background | Yes |

## 4. Row audit (every cell, no sampling)

Rows checked: 34. That is 3 CSV rows plus all 31 extract rows (8 observations + 23 context rows), against the 240-dpi images and zoomed crops.

| Table / source | Row | CSV / extract | Print | Match |
|---|---|---|---|---|
| Table 1 p.2 | CSV MgO | −97.4; 4.8; −74.29; −26.21 | −97.4 ± 4.8; −74.29; −26.21 | Yes |
| Table 1 p.2 | CSV “FeO” | −47.7; 4.1; −44.92; −22.44 | −47.7 ± 4.1; −44.92; −22.44 | Yes |
| Table 1 p.2 | CSV SiO2 | 98.0; 3.6; 89.07; 6.15 | 98.0 ± 3.6; 89.07; 6.15 | Yes |
| Table 1 p.2 | t1_mgo_this_study | −97.4, unc 4.8, measured_reduced, derived_from fig9_mgo | −97.4 ± 4.8 | Yes |
| Table 1 p.2 | t1_mgo_fabrichnaya_comparison | −74.29 quoted_attributed [6] | −74.29 | Yes |
| Table 1 p.2 | t1_mgo_pelton_comparison | −26.21 quoted_attributed (Pelton column) | −26.21 | Yes |
| Table 1 p.2 | t1_feo_this_study | −47.7, unc 4.1 | −47.7 ± 4.1 | Yes |
| Table 1 p.2 | t1_feo_fabrichnaya_comparison | −44.92 | −44.92 | Yes |
| Table 1 p.2 | t1_feo_pelton_comparison | −22.44 | −22.44 | Yes |
| Table 1 p.2 | t1_sio2_this_study | 98.0, unc 3.6 | 98.0 ± 3.6 | Yes |
| Table 1 p.2 | t1_sio2_fabrichnaya_comparison | 89.07 | 89.07 | Yes |
| Table 1 p.2 | t1_sio2_pelton_comparison | 6.15 | 6.15 | Yes |
| Figure 9 p.2 | fig9_feo_fit_annotation | 0.3268 ± 0.1136; −2493.4 ± 213.3 | y = 0.3268 ± 0.1136 - 2493.4 ± 213.3 | Yes |
| Figure 9 p.2 | fig9_mgo_fit_annotation | 2.2295 ± 0.1330; −5088.7 ± 249.7 | y = 2.2295 ± 0.1330 - 5088.7 ± 249.7 | Yes |
| Figure 9 p.2 | fig9_sio2_fit_annotation | −3.1418 ± 0.1005; +5118.9 ± 188.6 | y = -3.1418 ± 0.1005 + 5118.9 ± 188.6 | Yes |
| Figure 1 p.1 | fig1_fe / mg / sio / o / o2 pressure figure_only (5 rows) | species; no numeric P; no T range; Fo0.93Fa0.07 caption composition | legend P(Fe), P(SiO), P(Mg), P(O), P(O2); caption Fo0.93Fa0.07 | Yes (5/5; round-1 binding defect gone) |
| Figure 9 p.2 | fig9_feo / mgo / sio2 activity figure_only (3 rows) | a(FeO), a(MgO), a(SiO2); pure-material standard state, phase unspecified | legend a(FeO), a(SiO2), a(MgO); p.2 Summary “pure material” | Yes (3/3) |
| Context p.1 Methods | experimental_sample_and_characterization | Nor Mineral / Miller & Co., Chicago IL; Fa0.07Fo0.93; 1750-2250 K overall; XRD/ICP-OES/EPMA before and after; 1853 K 10 h “could be removed” | as printed | Yes |
| Context p.1 Background | background_scope_numeric_context | over 100; T ≥ 1473 K; (Mg0.9Fe0.1)2SiO4; Fo90Fa10; Kepler-10b | as printed | Yes |
| Context p.1/p.2 | 5 qualification rows (Summary p.1, Results, Methods, Discussion, Summary p.2) | §3 | §3 | Yes |
| Context p.1 Summary | summary_spelling_and_examples | Fo93Fa0.07; Kepler 10-b and CoRoT-7b | “Fo93Fa0.07”; “Kepler 10-b and CoRoT-7b” | Yes |
| Context p.1 header | affiliation_identifiers | 21000, MS 106-1, 44135; Box 1169, 1 Brookings Dr, 63130-4899 | as printed | Yes (numbers); see A1 |
| Context p.2 Ack. | grant_identifier | NASA EPSCOR Program, NNX13AE52A | as printed | Yes |
| Context p.2 Refs | reference_numeric_metadata (7 entries) | see §1 item 5 | as printed | Yes (numbers); see A1 |

Experiment/bench facts and all 15 typed absences are unchanged from round 1. I re-checked them against both pages and they are still supported: iridium cells; Nuclide/MAAS/PATCO 12-90-HT 90° sector; 16.5 eV (Figure 1 caption); no orifice size, sensor, temperature calibration, cross-sections, calibration substance or method, background pressure, sweep gas, regime class, liner, or multiplier/isotope corrections printed. Method classes are unchanged and correct.

## 5. Acceptance checks run

All were run from cwd `~/ci-scratch/regolith-green-ro` (61ec839da), interpreter `.venv/bin/python`, `PYTHONPATH` = that checkout, `PYTHONDONTWRITEBYTECODE=1`.

1. **Migrator.** `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<abs path to sparse worktree extract>)`, then `finalize()`. Result: works 1, experiments 1, benches 1, observations 8, context 23. **validation issues after finalize: 0** (hard 0). There are 8 queue entries, all of them the expected `bound_not_point_ordering` figure-only quantity reasons. No observation carries 1750/2250. Payload survival was confirmed for: `Sample.pretreatment` (qualified string); `printed_composition` (Fo0.93Fa0.07 at figure 1 with note); the new context rows (Fo93Fa0.07, CoRoT-7b, affiliations, NNX13AE52A, 7 references, all qualification statements); and the Table 1 values as floats (e.g. SiO2 98.0 / 3.6).
2. **evidence_for.** Each of the four method classes (`figure_only`, `quoted_unattributed`, `measured_reduced`, `quoted_attributed`) returns a value-tagged Evidence with queue reason None. `lineage_parents_from_source` resolves each `costa_2016_t1_*_this_study` row to its `::costa_2016_fig9_*_activity_figure_only` parent with no prose remainder. `source_derivation_from_source` is non-None for all three.
3. **Fidelity.** `tools/validate_literature_extracts.py --check-fidelity-match <abs path to …/worktrees/rev2-costa-…/extracts/costa-2016-lpsc-forsterite-olivine-vaporization.yaml>` printed `OK: 1 extract file(s) valid`, exit 0. The absolute positional path points the validator at the sparse corpus worktree. Nothing was copied or symlinked.
4. **Ledgers.** From the corpus worktree cwd: `python -m pytest tools/test_ledgers_valid.py -q -p no:cacheprovider -o addopts=''` gave **662 passed**, exit 0.
5. **Sidecar / PDF.** sha256 is `d47d37034fab2c5dfa06cb62d01280db3cc8cdc943160872088d201102c74dab` and size 257621 bytes. Both match the sidecar and `source.corpus_sha256`. The sidecar is unchanged. The citation (Costa, Jacobson & Fegley 2016, 47th LPSC, abstract 1454) matches the header. The licence statement “owner-supplied copy” is unchanged.

Not run (forbidden): `tools/build_index.py`, `tools/migrate_pilot_extracts.py`. No commit or push in the corpus repo.

## 6. Advisories (non-blocking; not counted as mismatches; main may fold into a later touch)

- **A1 (typography in “as printed” bibliographic and address strings).** In reference [3], the print reads “Ito, Y. et al (2015)” with no period after “al”. The extract has “et al.”. The affiliation strings add a trailing period, drop the superscript-joined form (“1NASA” is printed as “1 NASA”) and omit the email “(gustavo.costa@nasa.gov)” that is printed immediately after 44135. Every digit and identifier matches. These are non-numeric typographic normalisations of administrative text, the same class round 1 treated curly quotes as. If main wants byte-literal bibliographic strings, change “et al.” → “et al” in [3] (p.2, References).
- **A2 (inherited, not introduced by the fix).** The nine Table 1 context rows carry `composition_as_printed: Fo0.93Fa0.07 olivine` at the Table 1 locator. The Table 1 caption itself prints no composition. The binding is supported by the p.2 Summary (“Thermodynamic data was taken on this composition”) and the Figure 1 caption, so it is correct in substance, but strictly the “as printed” spelling belongs to the Figure 1 caption. A locator note would make this explicit. Round 1 did not flag it, and it is not a fidelity error.
- **A3 (style).** The structured `pretreatment` value now combines the source wording with an editorial clause (“the paper does not explicitly state …”). That clause is reviewer-voice inside a value field. It is accurate and invents nothing, but it would sit more cleanly in the locator `note`.

!COMPLETE: rev2-costa-2016-lpsc-forsterite-olivine-vaporization — LAND b6386273fe69edc8ace0e17e083c69c7766fb95e, pages read 2, rows checked 34, mismatches 0, printed numbers not carried 0
