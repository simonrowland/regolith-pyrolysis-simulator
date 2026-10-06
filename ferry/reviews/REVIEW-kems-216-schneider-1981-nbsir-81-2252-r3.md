# Round-three confirm review: kems-216-schneider-1981-nbsir-81-2252

VERDICT ON COMMIT: LAND 082651452c3b878c412ebb7e7570a63a7a184213

rows checked 37, mismatches 0, printed numbers not carried 0

Reviewer: regolith-empirical corpus review seat (batch3 A), 2026-10-05 ~21:00–21:30 ET.
Tip under review: `082651452c3b878c412ebb7e7570a63a7a184213` on `hunt/kems-216-schneider-1981-nbsir-81-2252`
at `mac-studio-256-1:Repos/regolith-corpus.git` (`git fetch origin hunt/<sid>` → FETCH_HEAD = that sha, verified).
Earlier rounds: r1 `review.md` @ `ba308952ab96b6ee501c544d5f27ec921d8bd998` (FIX-FIRST, 15/74);
r2 `review-r2.md` @ `514e98115e5610ab6da11be9cc238b32645ccf37` (FIX-FIRST, single item R2-1).
Worktree: sparse, `~/Repos/regolith-corpus/worktrees/rev3-kems-216-schneider-1981-nbsir-81-2252`, detached at the tip
(recipe from SEAT-COMMON / main's 04:25 NOTE; 45 MB). Green readers: `/Users/simonrowland/ci-scratch/regolith-green-ro`
@ `61ec839da3ba288c5df4a80f6d3ef142bd8ab461` (clean); `engines/engines.local.toml` **exists** there.
Not run: `tools/build_index.py`, `tools/migrate_pilot_extracts.py`. Nothing tracked was modified.

## Summary

The tip differs from the r2-reviewed commit by one line only: `ledger/<sid>.yaml` `stages.extracted.context_rows: 45 → 67`.
The extract YAML, all four CSVs, provenance sidecars, raw PDF and sidecar are byte-identical to 514e9811.

r2's single required change, **R2-1 (change the p.13 zirconia atmosphere exponent from 10^-5 to 10^-6 in five places)**,
was not implemented by the fixer (solfix-AO disputed it on image evidence). Under a NOT-FIXED stance I re-adjudicated it
on the page itself, at native scan resolution, against this document's own 5 and 6 glyphs. **The page prints
1.2 × 10^-5 ppm O2 in N2.** R2-1's premise does not hold, so the five current `10^-5` representations match the print and
no change is required. Every r1 item (10/10) still holds at the tip; all 37 CSV rows match; the ledger edit is correct
(migrator yields exactly 67 contexts). Acceptance checks pass. Verdict LAND.

Note to main: this is a reviewer disagreement on one degraded superscript glyph. Readings so far: r1 reviewer −5,
fixer solfix-AO −5 (440 dpi crop + 330 dpi render), its independent final check −5, r2 reviewer −6, this review −5
(native 350-ppi text layer, glyph-shape comparison below). Evidence image delivered alongside this file:
`REVIEW-kems-216-schneider-1981-nbsir-81-2252-r3-exponent-evidence.png`.

## R2-1 adjudication (printed p.13 / PDF 17 / index 16, ZrO2 Conductivity)

Sentence (page image): "Similar measurements were made in an atmosphere of 1.2 × 10^-? ppm O2 in N2 and the bulk
conductivity did not change." The pdftotext layer gives `1.2 X 10"^ ppm`, which is no help, so the image decides.

Method:
1. `pdftoppm -r 220 -png` of all 26 pages (read visually), plus `pdftoppm -f 17 -l 17 -r 600 -png` of PDF 17.
2. `pdfimages -png` of PDF 17, 22 and 13. The page is a JPX colour background (117 ppi) plus a **1-bit JBIG2 text mask at
   350 ppi** (2796×3838 px on PDF 17). The text mask is the highest-resolution source of the glyph, and resampling cannot
   blur it. I dumped the exponent glyph pixel by pixel. The descender of the "f" in "frequency" on the line above touches its top.
3. Reference glyphs from the same document's masks, located with `pdftotext -bbox`: body-size "5" in "25" (PDF 22, "first
   25 minutes"), body-size "6" in "7-6" (PDF 22), and the small superscript-size tick labels "10^-5" and "10^-6" on the
   Figure 1 ordinate (PDF 13, printed p.9). The prose exponent is ~31 px tall, which matches the small tick-label face (5: 29 px,
   6: 25 px) and not the body face (~42 px).

Findings on the native glyph:
- **Lower bowl is open at the lower left.** The left side of the bowl stops ~7 rows below the stem–bowl junction. A clean
  3-row gap follows, then a separate up-curled terminal joins the bottom stroke. This document's 5s show the same pattern:
  the tick-5 has a 6-row gap and terminal, and the body "25" has a 9-row gap and terminal. Every 6 examined has a
  **continuous closed** left side: body "7-6" and the tick-6.
- **Top stroke:** a short bar/arch with a small downward right-end serif (row 19, x21–25) over a left stem that drops to the
  bowl. This matches the body "5" (right-end serif under its top bar). The 6s instead have a smooth hook that descends
  diagonally into a closed bowl. The slight arch is partly from the overlapping "f" descender (x19–22, rows 0–15).
- No other glyph on the page reproduces it exactly, so there is no sign of a JBIG2 symbol substitution.

Conclusion: the glyph reads as **5**, i.e. **1.2 × 10^-5 ppm O2 in N2**. The extract's five occurrences are correct:
- `schneider_1980_zro2_air_low_po2.printed_qualification` (p.17/pub 13/index 16)
- `schneider_1980_zirconia_method_context` (atmospheres_as_printed and reason) (PDF 17)
- `schneider_1980_zro2_polarization.reason` (row PDF 18; cites PDF 17)
- `schneider_1980_conductivity_figure3.figure_caption_as_printed` conflict note (row PDF 21; cites PDF 17)
The migrated payload carries `10^-5 ppm` 5× and `10^-6 ppm` 0×. The source-internal inconsistency stays explicit, with no
value selected: Figure 3 caption **1.2 ppm** (PDF 21) and p.14 "near one part per million" (PDF 18). The Figure 1 ordinate
ticks 10^-6/10^-5/10^-4 atm (PDF 13) are separate and correctly carried.

Status: **R2-1 — NOT APPLIED, correctly. The finding is withdrawn on image evidence (the current extract matches the print).**
It is not counted as a mismatch.

Advisory, non-blocking: the glyph is degraded and readers have disagreed on it. Main may optionally add a one-line
legibility note to `schneider_1980_zro2_air_low_po2` ("superscript degraded; read as −5 against this document's 5/6
glyphs"). The value itself should not change.

## Every earlier item, NOT-FIXED stance

The extract YAML is byte-identical to 514e9811, so r2's per-item results carry over mechanically. I still re-checked
each item against the page images (all 26 read) and the YAML at the tip.

| Item (r1 review.md) | Status @ tip | Evidence (page image → YAML) |
|---|---|---|
| 1 Eleven tie-line equations | FIXED | PDF 9 eq.(1); PDF 10 eqs.(2)–(11). All 11 `equation_as_printed` strings (YAML l.1259–1429) match the image coefficient by coefficient, with rightward arrows and the implicit coefficients of CaSiO3 (7) and CaAl12O19 (10) preserved; mullite written `(3Al2O3·2SiO2)`. |
| 2 Table 3 key + parentheses | FIXED | PDF 11 key (C2S, Lc, CA6, Ge, Cor, Ksp, Kls, Ra=Ca3Si2O7=C3S2, Mu=3Al2O3·2SiO2, An, Wo; "( ) = phase barely detectable by x-ray diffraction") is carried. |
| 3 Tie-line context KAlSi3O8; split exclusions/plans | FIXED | PDF 9: "lower temperatures for the assemblages containing KAlSi3O8" (8 KAlSi3O8 hits in YAML). Exclusions are at PDF 10, minimum-melt and K2CaSiO4/K2Si2O5 plans at PDF 12. |
| 4 Conductivity introduction numbers | FIXED | PDF 15: near 1100 °C, above 1385 °C, ~14 % Fe, July–Sept. 1980; carried at index 14. |
| 5 Zirconia facts | FIXED | PDF 17–18: high-density oxygen-sensor tube; Pt contacts "do not appear to be porous"; April–June 1980 attachment; probes 1 and 4; 1–2 V/cm; above 1200 °C; ~60 s, reversed for the same time; Fe+2/Fe+3; yttria-stabilized. All carried. |
| 6 Corrosion facts | FIXED | PDF 22: 7-6 oxygen-rich / 10-6 fuel-rich; oxygen-propane ratio; 12.5 mm × 250 mm × 0.8 mm; four hours excluding heat-up/cool-down; 20 % K2SO4 / 80 % K2CO3; 25 min at 10 g/min; gas 1300 °C, two Pt/Pt-10%Rh thermocouples (centre, wall midpoint); cooling air; ~40 °C; epoxy; 10 mm from midpoint; metallographic, non-aqueous, vacuum desiccator. All carried. |
| 7 Coating facts | FIXED | PDF 23: arc plasma spray; NiCrAlY, MgAl2O4, MgO-doped ZrO2 on mild steel AISI 1015; 0.5 mm; 75 mm / 0.9 mm; 0.25 m with midpoint Pt/Pt-10%Rh. Carried. |
| 8 Context locators/splits | FIXED | r2's row-by-row table re-checked against page breaks: PDF 6/7 VP-4 continuation; PDF 15/16 conductivity split; PDF 17/18 zirconia split; PDF 22/23/24 corrosion/coatings. Holds. |
| 9 Figure descriptions | FIXED | PDF 20 labels 1727, 1545, 1393, 1265, 1155, 1060, 977, 903, 838, 779, 727 °C carried. PDF 21 ordinate f12(V); the printed stacked-fraction definition is carried as `(V12/d12)/(V23/d23) − 1`, which is algebraically identical to r1's reading `V12/(V23·d12/d23) − 1`, together with the probe voltage and distance definitions. No points digitized. |
| 10 Per-column origins / recipe | FIXED | PDF 6 recipe (minor constituents neglected, MgO→CaO, Fe2O3→Al2O3 equal weights, 66.9/33.1). Table 2 Rosebud column attributed to Pollina & Larsen ANL-77-21 (1977) (PDF 8). |
| r1 inventory extras | HOLD | PDF 13 tick-history caption; PDF 16 K-rejection/Ca, QR July–Sept. and October 1973, 500–1500 °C, one week, Δσ/Δt < 50 ppm/hour near 1000 °C, June 16–20 1980 session 07, 14 w/o Fe, ~1.8 eV / near 4.0 eV, factor of three; PDF 17 1972 reference; PDF 19 legend A/B/C 29/20/14 % Fe, D 4 % + 20 % K2SO4, April–June 1979 p.33; PDF 24 18 ↔ 700 °C, forming gas 5 % H2 / 95 % N2, 2 1/4 – 1 Mo Croloy; PDF 25 500 °C, 6×; PDF 26 425×. All carried. |
| r2 R2-1 | Withdrawn, see above | Print shows −5; no change required. |

## Every CSV row vs print (no sampling)

Read from the 220-dpi page images, digit by digit.

| Table | Rows | Result |
|---|---|---|
| Table 1, PDF 7 (p.3) | KCAS-VP-1…VP-4 (4 rows × mineral formula/wt % + 4 initial + 4 final) | All match. VP-1 14.8/27.5/22.2/35.5 → 1.6/31.8/25.7/40.9; VP-2 11.5/17.6/33.7/37.2 → 6.1/18.7/35.7/39.5; VP-3 12.5/28.0/13.5/46.0 → 1.8/31.4/15.2/51.6; VP-4 19.9/21.6/21.6/37.0 → 7.1/25.0/25.0/42.9; mineral wt % 49.8/16.5/16.5/17.2, 28.6/13.8/28.9/28.7, 42.0/58.0, 66.9/33.1; blanks are typed absences. |
| Table 2, PDF 8 (p.4) | 8 oxide rows | All match: SiO2 40.34/34.32/36.96; Al2O3 24.60/24.67/21.56; CaO 18.90/21.02/21.56; MgO 5.80/--/blank; Fe2O3 4.40/--/blank; K2O .53/20.00/19.92; Na2O .46/--/blank; SO3 .28/--/blank. |
| Table 3, PDF 11 (p.7) | 18 rows (reactions 1–7, 10, 11 × 2 durations) | All match, including temperatures (1250; 1050/950 for 10 and 11), durations (23/64, 16/24 ×4, 23/64 ×2, 22/64 ×2) and products. First-duration product cells are blank (typed absence). |
| Table I, PDF 15 (p.11) | 7 element rows | Si 20.3, Fe 3.9, Al 10.9, Mg 3.8, Ca 13.3, K 0.3, Na 0.4. All match. |

Totals: 37 rows; 103 numeric data cells plus 18 reaction identifiers; 25 absence cells, each a printed blank or dash.
**Mismatches 0.**

Pressure regression (PDF 7): `log P_K = 3.492 − 16153/T + .0903(wt% K2O) + .0351(wt% CaO)`, 30 % fit standard deviation,
~100 % maximum individual deviation, +20 % / +8 % per wt %. All match.

## Printed numbers not carried

I read all 26 pages. Pages 2 and 4 are blank (pixel stddev ~7–8); page 3 is contents. Every table, equation and
scientific numerical statement in r2's page inventory is carried at the tip (spot greps above, plus visual re-reads of
pages 1, 3, 5–12, 14–16, 18–26 and the PDF 13/17 masks). Figure coordinates are exempt and are not digitized.
**Printed numbers not carried: 0.**

## Collateral damage

- `git diff 514e9811..0826514` changes one line: ledger `context_rows 45 → 67`. Correct: the Green migrator yields exactly
  **67** contexts for this work. Nothing else changed: no CSV, provenance, raw or extract change, no context removed,
  no reader/schema/code change.
- `git diff --check` passes for both ba308952..tip and 514e9811..tip. `rg -n '/Users/|/private/'` over extract, ledger and
  tables finds no matches (exit 1).
- PDF sha256 `4287a212782666a8b827307c19de6d2311dda611ba8480b5df07895adfaaff68` matches the raw sidecar, the extract and
  all four table provenance sidecars (6 hits). The cover (PDF 1) confirms title, Schneider as project manager,
  Oct 1 – Dec 31 1980, NBS / Dept. of Commerce, and preparation for DOE; NBSIR 81-2252 is handwritten. Licence: the
  sidecar says "US Government work (as recorded by the fetcher from the landing page)". The PDF has only the
  sponsorship/liability disclaimer and no explicit licence grant, so this is unchanged from r1/r2 and is not a defect.

## Acceptance checks (Mac, Green @ 61ec839da)

Interpreter `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python`, `PYTHONPATH=/Users/simonrowland/ci-scratch/regolith-green-ro`,
`PYTHONDONTWRITEBYTECODE=1`.
1. From Green cwd: `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<absolute corpus extract path>)`, then
   `finalize()`. Result: works 1, benches 1, experiments 2, observations 0, contexts 67 for the work;
   `ValidationReport(issues=())`. **Hard issues 0 after finalize.** One expected queue entry (extract yielded no observations).
2. From Green cwd: `tools/validate_literature_extracts.py --check-fidelity-match <absolute corpus extract path>`. The
   validator was pointed at the corpus worktree by that positional absolute path, with no symlink or root patch.
   Result: **OK: 1 extract file(s) valid**, exit 0.
3. From the corpus worktree cwd: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 … -m pytest -c /dev/null -o addopts= -p no:cacheprovider -q tools/test_ledgers_valid.py`.
   Result: **674 passed**, exit 0.

## Non-blocking advisory

- PDF 15 prose: "An elemental analysis of this slag as received from Montana State University is given in Table 1."
  The Table I context says "independent laboratory; named laboratory not printed". That is literally true of the table
  heading, but the prose names the institution the slag (or analysis) came from. Main may add that sentence as provenance
  context later. It is not a number and does not block landing.
- The optional R2-1 legibility note above.

!COMPLETE: rev3-kems-216-schneider-1981-nbsir-81-2252 — LAND 082651452c3b878c412ebb7e7570a63a7a184213, pages read 26, rows checked 37, mismatches 0, printed numbers not carried 0
