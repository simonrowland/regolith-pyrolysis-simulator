# STATUS: fix of lpi-compendium-14163 (corpus batch 8, section A)

**From:** regolith-empirical (fix seat, re-run of the seat that stalled ~21:50 ET)   **To:** regolith-main   **At:** 2026-10-05 ~22:30 ET
**Branch:** `hunt/lpi-compendium-14163` on `mac-studio-256-1:Repos/regolith-corpus.git`
**Started from:** `8f46fd06cde587edf6b09f141d43c5441e981897`. Before I started, `git fetch origin hunt/lpi-compendium-14163` returned exactly that tip.
**New tip:** `b5aa4eac72591912d757dc2fb3e15f8839eb9b32`
- Pushed to origin `hunt/lpi-compendium-14163` only, never main. `ls-remote` verified it.
- Parent is 8f46fd06. One commit, explicit pathspecs with `git add --sparse`.
- Author and committer are Simon Rowland <simon@simonrowland.com>, with no trailers.
**Files:**
- Changed: `extracts/lpi-compendium-14163.yaml` and `ledger/lpi-compendium-14163.yaml`.
- New: `tables/lpi-compendium-14163/t5`–`t11` `.csv` plus a `.provenance.yaml` for each. That is 16 files, +3134 / −13.
- Not touched: the PDF, raw/, text/ and the sidecar.
**Review applied:** `review.md` (main's grok review: 120 rows clean, 0 mismatches, 670 printed numbers not carried, FIX-FIRST). After the fix: **grok confirm**.

items fixed 9/9; corrections 670 printed numbers now carried (0 changes to existing digits; the carried block is unchanged); hard issues 0

## How this seat continued the stalled one
- The stalled seat had left uncommitted work in `worktrees/fix-lpi-compendium-14163`, still sparse and on the right branch at 8f46fd06:
  - `t5.csv` and `t6.csv` with their provenance files
  - 19 trace-element context rows and 4 fidelity samples in the extract
  - a half-updated ledger that read "fix round in progress (group 1)"
- Before building on that work I re-verified it, using two independent checks:
  1. **Against the page text layer.** I parsed `pdftotext -bbox-layout` and assigned every word to a column by the header x-coordinate. All 175 (p. 5) and 131 (p. 6) value + marker cells match the CSVs exactly, with no cell missing and no extra cell.
  2. **Against the 300 dpi page images.** I read the trace blocks of pp. 5–6 by eye.
- Every YAML trace row also matches its CSV: 175/175 and 131/131 values, all markers equal, and the blank markers are null.
- I kept that work and then did items 3–9.

## Self-verification
- **Renders.** All 11 pages were rendered with `pdftoppm -r 300` in a scratch directory outside the repo (`~/Repos/regolith-corpus/.ferry-tmp/fix-lpi14163/`). None are committed.
- **Images read.** I read pp. 4–9 as image crops.
- **Column assignment.** Every table cell comes from `pdftotext -bbox-layout` word coordinates. The page is a born-digital PageMaker PDF, so the text layer and the render agree.
- **Figure text.** The text inside the Figure 8 and Figure 9 bitmaps (about 150 ppi embedded) is not in the text layer. I read it from enlarged 300 dpi crops.
- **Payload checks.**
  - The 34 allocation pairs on p. 8 match the review's list one-for-one.
  - Each pair was also bound to its box by geometry: the mass word sits directly below the split word, at the same x.
  - The t7 (209) and t11 (64) YAML rows were round-tripped against their CSVs with no differences.
- **Old rows.** All 22 context rows from 8f46fd06 are byte-equivalent after YAML load. The only exception is that `lpi14163_figures_context` gained one `annotations_note` pointing at the new figure rows.
- **Fidelity samples.** The 8 existing fidelity samples are unchanged. I added 4 new ones:
  - t2cf breccia sub5 Cr 2095
  - t4 ,7373 Zr 7150
  - Fig. 9 heating rate 6
  - Fig. 8 sample mass 0.6

## Per-item table
| review item | change on b5aa4eac | page evidence (PDF page = printed page; pages unnumbered) |
|---|---|---|
| 1. p. 5 Table 1a traces Sc–U, 175 values | `t5.csv` has 175 long-format cells, each with its own technique marker. There are 9 context rows `lpi14163_t1a_trace_*`, one per reference column. The blank rows (Ge, Se, Mo, Ru, Rh, Ag, Cd, Sn, Sb, Te, Re, Os, Pt) are not filled. These cells print no marker, so the marker is null: Rose72 Ba 1100, La 79, Yb 28, and Wanke72 Ir 19. Wanke72 "(a, h)" is kept as printed: the 1a technique line does not define (h). | p. 5. Header x: Laul72 114, Laul80 160, Lindstrom72 208, Rose72 262, Hubbard72 309, Wanke72 358, Taylor72 413, Masuda72 461, Keith72 512 pt. Each cell was re-read on the 300 dpi crops. |
| 2. p. 6 Table 1b traces Sc–U, 131 values | `t6.csv` has 131 cells and there are 10 context rows `lpi14163_t1b_trace_*`. They include Morgan72 (2 sub-columns), Baedecker72/Wasson73, Helmke72 and Philpotts72 (14421). A Morgan72 or Brunfelt72 marker is printed once after sub-column 2, so it is recorded on both sub-columns (`marker_scope = shared`). Helmke72 Sc 20.5 prints no marker, so it is null. | p. 6. x positions: Morgan72 95/126, Baedecker72 173, Willis72 231, Hubbard72 275, Brunfelt72 322/353, Helmke72 397, Philpotts72 441, Quaide72 489 pt. |
| 3. p. 7 Table 2 coarse fines, 209 values | `t7.csv` has 209 cells. There are 11 context rows `lpi14163_t2cf_*`, one per printed column: McKay 79 14160 / 14160,79 / 14160; Hubbard 72 / Weismann76 "14161,35 breccias" sub-columns 1–5, which are unlabelled on the page and numbered left to right; Hubbard71 / Weismann76 "14161 anor."; Brunfelt72 14163 "light rx." / "dark rx.". Header spellings are as printed. The technique legend is recorded as printed, "(a) IDMS" only. Each group marker is printed once after the group, so it is recorded on every cell of that group. McKay majors and Cr print no marker, so the marker is null. **Typed absence:** in row Rb, McKay 79 sub-column 1 prints "(a)" in the value position with no number, so no value is carried (`printed_without_value`). | p. 7. Value columns at 114/148/188, 238/267/298/328/358, 402, 458/494 pt; markers at 221/388/441/534. Read on the 300 dpi crops (three bands of the table). |
| 4. p. 8 allocation masses, 34 values | `t8.csv` and the context row `lpi14163_p8_allocation_masses` hold all 34 split → mass pairs, each with the parent box traced along the connector lines. The ,234 / ,239 / ,52 / ,53 group boxes print no mass. "remote storage" is noted under the ,234 and ,239 groups. Masses are as printed, including "5g". | p. 8, both diagrams, read on the 300 dpi crops. The pairs are identical to the review's list (,185 300 g … ,67 149 g). |
| 5. p. 9 Table 2 small rocks, 29 masses | `t9.csv` and `lpi14163_p9_small_rocks_14422` hold all 29 masses (g) with the printed type words. 14432 prints 1.81 and no type, so the type is null. The title is as printed, "Table 2: Small rocks from 14422 - -". | p. 9, left column. |
| 6. p. 9 Table 3 coarse fines, 20 cells | `t10.csv` and `lpi14163_p9_coarse_fines_kramer_twedell1977` hold 9 classes plus the total. That is the 20 number/weight cells (total 154; 45 g) and the 9 printed split numbers ,87–,95. The total row prints no split. "aphanitic basalt ?" is kept as printed. | p. 9, lower left ("from Kramer and Twedell 1977"). |
| 7. p. 9 Table 4 granitic 14161, 64 values | `t11.csv` and the 3 context rows `lpi14163_t4_granitic_14161_{7069,7373,7269}` hold all 64 values. Ni is blank for ,7373. Ir ppb 6 and Au ppb 4 appear for ,7269 only. The header "Jolliff 1993" is kept, alongside Jolliff 1991 from p. 2 and p. 10, with neither reconciled to the other. The technique line is recorded as printed: "a) INAA". | p. 9, right column. x positions 341/377/414 pt; markers at 451. |
| 8. Figure 8 annotation (3 numbers) | `lpi14163_fig8_annotation` holds the quoted text, plus `subsample_as_printed` 14163,111, `adsorption_temperature_C` 15, `sample_mass_g` 0.6 and `outgassing_temperature_C` 140, which was applied "initially and after 2nd cycle". The isotherm is not digitised. | p. 4, bitmap: "Adsorption of water vapor on Lunar fines 14163,111 at 15°C"; "Weight of sample 0.6 grams"; "Outgassed initially and after 2nd cycle at 140°C." |
| 9. Figure 9 condition block (5 numbers) | `lpi14163_fig9_conditions` holds `sample_mass_mg` 216.8, `heating_rate_C_per_min` 6, `vacuum_torr` 2e-6, an alumina crucible with `crucible_diameter_mm` 16, `total_weight_loss_percent` 1.65 and subsample 14163,178. It also lists the gas labels as printed (H2O, CO2, "CO, N2", H2, O2, N2). The curves and axis ticks are not digitised. | p. 4, lower-panel bitmap: "216.8 mg. Sample / Heating Rate 6 °C/min. / 2 x 10^-6 torr Vacuum / Alumina Crucible 16 mm. dia. / 1.65 % Weight Loss (total)"; "APOLLO 14 SOIL 14163,178". |

Count check: 175 + 131 + 209 + 34 + 29 + 20 + 64 + 3 + 5 = **670**. This matches the review's count.

## Flags (typed, located; no value guessed)
- **Figure 9 run header** (p. 4) runs along the top of the gas-release panel. It is only partly legible at the embedded resolution and is **not transcribed**: no digits are carried, and this is recorded in `legibility_note`. The review did not count it either.
- **Figure 9 "1.65 %":** the leading "1" is a narrow I-like glyph followed by a point. I read it as 1.65, the same as the review. This is noted in the row.
- **p. 7, Rb, McKay 79 / 14160** prints "(a)" with no number, recorded as a typed absence.
- **Arithmetic checks only.** None of these is a printed value, and none is reconciled:
  - The 29 small-rock masses sum to 68.49 g, but p. 8 prints "66 grams total".
  - The Table 3 classes sum to 154 and 45.5 g, but the printed total is 45 g.
- The 7,776 g vs 7881 g bulk-mass discrepancy is unchanged from the original extract.
- **Units:** only some row labels on pp. 5–7 and 9 print a unit. Unlabelled oxide rows are recorded as wt% and unlabelled trace rows as ppm, the convention the stalled seat used for t5/t6. The `unit_basis` column says "printed" or "header convention (see provenance)".
- **Observation vs context:** everything is still context (0 observations, 60 context rows). Promoting rows to observations in the lpi-compendium-14240 pattern is main's call.

## Checks
Green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461 = `~/ci-scratch/regolith-green-ro`, used read-only with PYTHONDONTWRITEBYTECODE. PYTHONPATH pointed at that clone. Python was `~/Repos/regolith-pyrolysis-simulator/.venv/bin/python`.
- **Migrator:** `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(Path("extracts/lpi-compendium-14163.yaml"))` then `finalize()`, run from the sparse worktree. Result: registry issues 0, validation issues 0, **hard issues 0**, observations 0, context rows 60. The queue has 1 entry, "extract yielded no observations", which is expected.
- **Fidelity validator:** `tools/validate_literature_extracts.py --check-fidelity-match <worktree>/extracts/lpi-compendium-14163.yaml` gave **`OK: 1 extract file(s) valid`** (exit 0) with 12 fidelity samples. I also confirmed that each sample's value equals its field in the named row.
- **Ledger tests:** `python -m pytest -q tools/test_ledgers_valid.py`, run in the worktree, gave **717 passed**.
- **Paths:** `rg "/Users/|/private/"` over the extract, the ledger and tables/lpi-compendium-14163/ found no matches.
- **engines.local.toml:** `engines/engines.local.toml` in the green clone **exists**.
- **Not run:** tools/build_index.py and tools/migrate_pilot_extracts.py. INDEX was not regenerated, because the tree is sparse.
- **PDFs:** none committed. The raw PDF sha256 `12544c12…e343e` (435156 bytes) matches the sidecar and ledger.
- **Worktree and disk:** the sparse worktree `~/Repos/regolith-corpus/worktrees/fix-lpi-compendium-14163` was removed after the push. `df -g /Users` showed 60 GB free at the start and 58 GB after the worktree was removed (other seats are running on the same Mac).

## P0 issues
None.

!COMPLETE: fix-lpi-compendium-14163: b5aa4eac72591912d757dc2fb3e15f8839eb9b32, items fixed 9/9, 670 printed numbers carried, hard issues 0. Every item was verified against 300 dpi page images and the bbox text layer. Next step: grok confirm.
