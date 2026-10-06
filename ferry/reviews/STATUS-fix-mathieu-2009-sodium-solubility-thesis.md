# STATUS-fix: mathieu-2009-sodium-solubility-thesis (batch8 item B)

- **sid:** mathieu-2009-sodium-solubility-thesis
- **branch:** `hunt/mathieu-2009-sodium-solubility-thesis` on mirror `mac-studio-256-1:Repos/regolith-corpus.git`
- **start:** 92434ea18e174e0b7a925d3aef20b536a34acedc
- **new tip:** 4330e0df6a5570e31382c4a26f1694c9788de884 (verified with `git ls-remote origin`)
- **green:** 61ec839da3ba288c5df4a80f6d3ef142bd8ab461 at `~/ci-scratch/regolith-green-ro` (read-only); `engines/engines.local.toml` exists there.
- **outcome:** part B steps 1 to 4 are done and pushed, along with the extract fixes. I verified every fix item myself against the page images (220 dpi, with 400 dpi crops where the font is small).
- **counts:** fix items 5/5. 7 uninventoried tables added (141 rows). 421 observations, of which 314 are admitted and 107 pending. 100 context rows. 80 prose claims. 1 bench. **0 hard issues** after finalize.
- **acceptance:** fidelity validator OK; `tools/test_ledgers_valid.py` 674 passed.

## Commits (author Simon Rowland <simon@simonrowland.com>, no AI trailers, explicit `git add --sparse` pathspecs, no PDFs)
| sha | step |
|---|---|
| a4323ed6 | Fix: t1, t2 and t44 column shifts; the 2 missing t38 rows; the t27 duration column; plus the t39 provenance YAML quoting |
| bbc5306a | Fix: 7 uninventoried tables t62–t68; table inventory regenerated (68 tables) |
| 1000872c | B1: 421 observation rows (Na2O solubility and γNa2O against T, fO2 and composition) plus 11 run-condition context rows and 8 fidelity samples |
| c563fde0 | B2: 80 verbatim directional claims and qualifiers from the prose |
| 4330e0df | B3: typed bench record; experiment facts moved to fields the reader keeps; experiment locators corrected |

## Fix items: per-item table (prior seat's findings, each re-verified by me on the page image)
| item | what changed | page evidence |
|---|---|---|
| t1 (Tableau III.1) column shift | The pôle and EDiAn rows were shifted 2 columns left. Their values now sit in the corrected wt%, corrected mol% and γ columns | PDF p145 (printed 122) |
| t2 (Tableau III.2) column shift | Same shift on pôle and EDiAn; fixed the same way | PDF p149–150 (printed 126–127) |
| t44 (Tableau IV.7) column shift | EDiAn reference row: same shift; fixed | PDF p181–182 (printed 158–159) |
| t38 (Tableau IV.1) missing rows | Added ACMA5S22/CMA5S2 and ACMA5S26/CMA5S1, both printed at PNa 1.27 (0.05). t38 now has 50 rows. IV.3 prints CMA5S1 at 1.01; I carried both as printed and flagged the source inconsistency in provenance | PDF p159–161; IV.3 on p173 |
| t27 (Tableau A.10.1) durations | duration_h corrected from 1.0 to 96/79/72/71/99/95 h, as printed | PDF p390 |
| 7 uninventoried tables | t62 = reprinted 2008 JNCS letter Table 1 (12 rows). t63–t68 = chapter III English-article Tables 1–6 (38/8/14/3/33/33 rows). Each has provenance; the inventory now holds 68 tables with true record counts | PDF p78; p121–129 |

Corrections count: 5 row-level fixes (t1 ×2 rows, t2 ×2 rows, t44 ×1 row), 2 rows added (t38), 6 cells corrected (t27), 1 provenance YAML repair (t39), and 7 tables / 141 rows added.

## Part B step status
- **B1 observations: DONE (1000872c).** Every measured row of III.1, III.2 and IV.1–IV.9 (t1, t2, t38–t46) gets two rows:
  - a `concentration_series` row: analysed Na2O wt%, measured_tabulated, 212 rows;
  - an `activity_coefficient` row: γNa2O, measured_reduced, 209 rows. Each one is derived_from the concentration row plus the table's caption run conditions, and carries derivation relation and inputs.
  - Caption fO2 (Ni/NiO, 2.11e-6 atm) lands as fO2_Pa = 0.2138 Pa. It is not landed for t2 or t43 (see P1-2 and P1-3 below).
  - The analysed glass composition (wt% oxides) is joined from appendix Tables A.7.2–A.10.2 only where run, label and analysed Na2O all agree: 64 concentration rows and the γ rows derived from them.
  - 107 rows are pending as `source_internally_inconsistent` (see P1).
- **B2 claims: DONE (c563fde0).** 80 statements: 62 directional, 18 qualifiers.
  - Found by scanning the embedded text layer of chapters II–VII for directional words. The PDF is born-digital with embedded Times fonts.
  - Each statement is quoted verbatim and checked against the rendered page image.
  - Statements attributed to other workers and structural-background statements are not carried.
- **B3 payload survival: DONE (4330e0df).**
  - Every bench and experiment fact now survives into the migrated WORK.
  - The remaining drops are listed in the REPORT under FACTS THE READER DROPS.
- **B4 acceptance: DONE at 4330e0df.** Output is in the REPORT.

## P0
None.

## P1/P2 questions for main (details in the REPORT)
1. **P1, t44 γ scale:** the heading prints γNa2O (x 10-7), but a_source/x reproduces the values only on a ×10^-6 scale. All 52 t44 γ rows are pending.
2. **P1, t2 temperature:** the caption says 1400 °C but uses the NS2 1250 °C source values (aNa2O 4.07e-8, PNa 5.50e-6). The text (p149) says 1250 °C. All 47 t2 rows are pending and fO2 is not landed.
3. **P1, t43 fO2:** the caption prints fO2 = 2.11e-6 atm at 1250 °C, the same value as the 1400 °C tables. fO2 is not landed for t43.
4. **P2, γ = a/x check:** fails by more than 10% for t43 (2 rows), t46 (CMA16S1, CMA16S2, SPM), t38 NAS3 and NCAS3, and t39 NAS3. These rows are pending.
5. **P2, CMA5S1 PNa:** printed as 1.27 in IV.1 and 1.01 in IV.3. Both are carried as printed.
6. **Amendment, no concentration quantity:** there is no closed Quantity for dissolved-oxide concentration or solubility, so the 212 analysed-Na2O values are `unavailable` in WORK (migrate.py:4808–4812, `map_quantity`).
7. **Amendment, composition for chapter IV rows:** 147 chapter IV γ rows have no composition axis. The appendix composition tables t20, t23, t26 and t29 are still raw `numeric_cells_left_to_right`.

## Delivery
- Files: this STATUS and `REPORT-mathieu-2009-sodium-solubility-thesis.md`.
- Destinations: Dropbox from-empirical, VPS /workspace/ferry-inbox/from-empirical/, and the mailbox branch `ferry/reviews/`. The sha256 values are in the seat's final message.
- Worktree: removed after delivery.
