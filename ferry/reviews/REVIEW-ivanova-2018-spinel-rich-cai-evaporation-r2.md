# Ivanova 2018 (spinel-rich CAI evaporation): confirm review, round 2

VERDICT ON COMMIT: FIX-FIRST (reviewed tip 5c61b3e63224b67fead41d1c4b5497c6309d3bd9)

**Reviewer:** regolith-empirical corpus review seat (batch2 section A), 2026-10-05 ~20:40 ET.
**Tip under review:** `5c61b3e63224b67fead41d1c4b5497c6309d3bd9` on `hunt/ivanova-2018-spinel-rich-cai-evaporation`. I ran `git fetch origin hunt/ivanova-2018-spinel-rich-cai-evaporation` against mirror `mac-studio-256-1:Repos/regolith-corpus.git`, and `origin/hunt/...` resolved to exactly `5c61b3e63224b67fead41d1c4b5497c6309d3bd9`. The tip has one commit after the round-1 tip `83cd828fc0714f71775f2da055bd4a92c289234f`. That commit changes only `extracts/ivanova-2018-spinel-rich-cai-evaporation.yaml` (+59/−7). The CSV, provenance, sidecar and text are unchanged since round 1.
**Stance:** I treated every round-1 finding as NOT FIXED until the page image and the tip showed otherwise. I also looked for collateral damage from the fix.

rows checked 8, mismatches 3, printed numbers not carried 0

## Required changes (page locators)

1. **p. 1, Table 1, full-table context `ivanova_2018_table1_full_transcription`: wrong top-level `method_class` (collateral damage from the fix to round-1 finding 3).** The fix changed `values.method_class` from `measured_tabulated` to `quoted_unattributed`. In the green reader, `quoted_unattributed` is the class that `quoted_from_other_workers`, `quoted_literature`, `quoted_prior_work` and `literature_psat_not_this_work` all resolve to (`simulator/battery/migrate.py` l. 301–306 at 61ec839da). The label therefore says Table 1 is other workers' data. It is this paper's own table: the oxide analyses were measured by these authors (p. 1 Methods: "Texture and chemical composition of the evaporation residues were studied at the University of Chicago … and at the Smithsonian Institution …"), and the authors computed the ratio and loss columns themselves (p. 1 Table 1 footnote: "% Mg and % Si lost have been calculated relative to 5aN-3 (0 min run) …"). Round 1 asked to keep the 32 oxide observations measured and to label only the ratio and loss columns as author-reduced. The new `column_provenance` block does that correctly in prose, but the machine-readable class now contradicts it for all nine columns. Required: use a this-work class for the context. Restoring `measured_tabulated` and keeping the new `column_provenance` block (which states that the ratio and loss columns are author-calculated) is the minimal change. Do not use any `quoted_*` class for Table 1.
2. **p. 1–2, qualifying-claims context `ivanova_2018_qualifying_and_directional_statements`: comma-split flow-mapping locator (missed in round 1, still present).** Line 306 of the tip reads `locator: {page: 1, published_page: 1, pdf_page_index: 0, section: Introduction, Results and Discussion}`. Because the value is unquoted, YAML parses it as `section: Introduction` plus a stray key `"Results and Discussion": null`. The bad key survives into the migrated context (`'section': 'Introduction', 'Results and Discussion': None`). This is the comma-split flow-mapping defect the brief bans by name. Required: quote the value (e.g. `section: "Introduction, Results and Discussion"`), or use block style. The container locator also gives only page 1 / index 0 although the claims run onto p. 2. Each claim has its own correct locator, so a container note that the claims span pp. 1–2 would be enough.
3. **p. 2, Discussion, sentence 2: model-background / qualifying sentence not carried (round-1 instruction NOT FIXED).** Round 1 said, in its activity/claims section: "The paper's explicit p. 2 associated-solution/activity-model basis is not carried in those claims; add that sentence as located model-background context." The tip still lacks it: `rg -n -i "associated solutions|confirms our"` on the extract exits 1. The printed sentence (p. 2, Discussion, read from the page image) is: "The experiments confirms our thermodynamic calculations based on the theory of associated solutions and on experimental data for activities of oxides in the system CaO-MgO-FeO-Al2O3-TiO2-SiO2 [3,8]." Required: add it verbatim, keeping the printed grammar ("confirms"), as a located claim (p. 2 / index 1, Discussion). It is both a comparative statement (experiments versus calculation) and the stated basis of the model curves in Figs. 2–3. The fixer's report says "all seven findings are addressed". That is true of the seven numbered items only; this instruction was missed.

**Recommended, not counted** (all numerals verified correct; these are wording/fidelity nits in blocks labelled "as printed"):
- p. 2 References: `[3] … LPS XLVIII, Abs. #1964.` and `[6] … LPS XLVII, Abs. #2929.` Print has a period, not a comma, after the volume: "LPS XLVIII. Abs. #1964." and "LPS XLVII. Abs. #2929." (600 dpi crop and PDF text layer). The other nine entries match the print exactly.
- p. 1 affiliation context: the units say "source metadata as printed", but the text leaves out "Department of Mineral Sciences, National Museum of Natural History," before "Smithsonian Institution". All five numerals are present and correct (Kosygin St. 19; Moscow 119991; meteorite2000@mail.ru; Washington, DC 20560; Chicago, IL 60637). Either carry the full line or change the units to "abridged".
- p. 1 CV3 context: the `text` is a paraphrase, not the printed sentence ("We recently presented results on thermodynamic modeling of evaporation of CAIs with different compositions from CV3 chondrites, and compared their evaporation trends with the bulk compositions of CAIs from CV3 and CH-CB chondrites [2]."). Both CV3 occurrences are carried. Quoting the sentence verbatim would be cleaner.

## Round-1 findings, one by one (NOT-FIXED stance)

| # | Round-1 finding | Evidence at tip `5c61b3e6` / page image | Status |
|---|---|---|---|
| 1 | p. 1 Methods: `~10^-6 torr` needs `approximate: true` | Tip l. 63 `{kind: point, point: '0.00013332236842105264', approximate: true}`. Migrated `total_pressure_Pa.state.value.approximate` = `True`. Print (p. 1 Methods, 300 dpi): "at total pressure ~10^-6 torr". | FIXED |
| 2 | p. 1 Table 1: units "ratios and losses in percent" | Context units now "wt.% oxide composition; time in min; dimensionless CaO/Al2O3 mass ratio; Mg and Si losses in percent". "Mass ratio" is supported: all eight printed ratios equal the rounded CaO wt%/Al2O3 wt% quotients (0.333→0.33, 0.325→0.33, 0.338→0.34, 0.333→0.33, 0.295→0.29, 0.327→0.33, 0.299→0.30, 0.304→0.30). | FIXED |
| 3 | p. 1 Table 1 footnote: per-column provenance; oxides measured, ratio/losses author-reduced; lineage; no invented formula | `column_provenance` added. It states the oxides are measured/tabulated, the ratio and losses author-reduced/tabulated, and the losses relative to 5aN-3 (0 min) and the residue rows in `ivanova_2018_table1_residue_composition`. It explicitly says no loss formula is published, and none is invented. The 32 scored observations still migrate as `measured_tabulated` (32/32). **But** the context's top-level class became `quoted_unattributed`, a new provenance error (required change 1). | PARTIAL (collateral regression) |
| 4 | Figs. 2–3 mislocated to p. 1/index 0 | Three contexts now: `ivanova_2018_figure_1` p. 1/index 0, `_figure_2` p. 2/index 1, `_figure_3` p. 2/index 1. I checked the figure text against the images. Fig. 1 labels "5aN-8 10 min at 1900ºC" and "5aN-7 45 min at 1900ºC", and both scale bars "500 μm" (600 dpi crops), match. Fig. 2 caption "Experimental (lines) and calculated (symbols) compositional trends … 5aN, CAI4 and CAIB … compare to CH-CB CAIs" and the "wt %" ternary axis match the paraphrase. Fig. 3 labels "CaO/Al2O3=0.3", "=1.2", "=0.8" and the molar axes match. The caption "1900 °C in vacuum … contained 1 wt% more SiO2 and 0.5 wt% less Al2O3" is carried with the correct p. 2 locator. Nothing was digitised. | FIXED |
| 5 | p. 2 Discussion: "Futher" should be "Further" | **Disputed item; ruling below.** The extract keeps "Futher", which matches the print. | NO CHANGE REQUIRED (round-1 premise wrong) |
| 6 | p. 1 Methods: quench typed as unknown although 5aN-3's quench is printed | `thermal_schedule.cooling_or_quench` = value "5aN-3 was quenched as soon as the furnace reached 1900 °C; cooling details for the other runs are not published." The schema types this field as `Located[str]` (`records.py` l. 928), and it migrates as a value. Print: "One run ("5aN-3") was quenched as soon as the furnace temperature have reached 1900°C)". The note and the value no longer contradict each other. | FIXED |
| 7 | 47 uncarried metadata numerals (affiliation 5, CV3 ×2, references 40) | All present: the affiliation context carries 19, 119991, meteorite2000, 20560 and 60637. The CV3 context carries both occurrences. The reference context carries all 11 entries. I checked every numeral against the 600 dpi References crop: [1] 2014, 1, 139–179; [2] 2017, XLVIII, 1363; [3] 2017, XLVIII, 1964; [4] 2013, 123, 368–384; [5] 2007, 71, 5544–5564; [6] 2016, XLVII, 2929; [7] 2014, XLV, 2782; [8] 2015, XVI, 281–284; [9] 2016, XLVII, 2315; [10] 2017, 55, 3, 251–256; [11] 2009, 73, 4963–4997. 40/40 match. Wording nits are listed under "Recommended". | FIXED |
| — | Round-1 body instruction: add the p. 2 associated-solution/activity-model sentence | Absent (rg exit 1). | NOT FIXED (required change 3) |

## Ruling on the disputed item (round-1 finding 5)

**I uphold the fixer's call. The print reads "Futher", not "Further", and the extract is right to keep it.** Evidence:
- p. 2, right column, Discussion, last paragraph. In the 300 dpi full-page render and a 600 dpi crop (`pdftoppm -r 600 -f 2 -x 2400 -y 4100 -W 2400 -H 600`), the line reads "tions. Futher isotopic investigations may help to re-" / "solve this problem." The letters F-u-t-h-e-r are clearly separated; no "r" follows the "u".
- The PDF's embedded text layer agrees: `pdftotext -layout` gives "tions. Futher isotopic investigations may help to re-".
- The corpus OCR lead `text/ivanova-2018-spinel-rich-cai-evaporation/pdftotext-layout.txt` matches.
The round-1 reviewer silently corrected the authors' typo. Under the image-first contract, the transcription must keep the printed spelling. No change is required to the extract or the extraction report. Optionally, a note on the claim could flag "Futher" as the authors' typo so that later reviewers do not raise it again.

## Collateral-damage sweep of the fix commit

- **Table digits:** I re-read every Table 1 cell on a fresh 600 dpi crop of p. 1. CSV, `composition_series.values.series` and `context[table].values.rows` were compared as Decimals: **64/64 equal** across all three copies. Each row against print (time, MgO, Al2O3, SiO2, CaO, CaO/Al2O3, %Mg lost, %Si lost), all eight cells matching in every row:
  - 5aN-3: 0, 16.02, 37.72, 33.71, 12.56, 0.33, 0.0, 0.0
  - 5aN-6: 5, 15.77, 43.07, 27.17, 14.00, 0.33, 13.8, 29.4
  - 5aN-8: 10, 14.46, 46.07, 23.91, 15.56, 0.34, 26.1, 41.9
  - 5aN-5: 15, 12.69, 50.59, 19.87, 16.85, 0.33, 41.0, 56.0
  - 5aN-2: 15, 11.43, 57.63, 13.95, 16.99, 0.29, 53.3, 72.9
  - 5aN-4: 25, 6.49, 63.62, 9.11, 20.79, 0.33, 76.0, 84.0
  - 5aN-1: 30, 0.01, 76.88, 0.09, 23.02, 0.30, 100.0, 99.9
  - 5aN-7: 45, 0.00, 76.66, 0.05, 23.29, 0.30, 100.0, 99.9
- **Context count:** the extract went from 3 to 8 context records: table, Figs. 1/2/3, claims, affiliation, CV3, references. All eight migrate. The old combined `ivanova_2018_figures_1_2_3` id is gone, and nothing else in the extract referred to it.
- **Class regression:** see required change 1. This is the only collateral damage I found. The fix did not touch the oxide observation series (`measured_tabulated`) or the starting composition (16.02/37.72/33.71/12.56 wt%).
- **The 25 existing claims:** the fix did not change them. I re-read them against both pages and they still match, including "Futher". Required change 2 (the stray locator key) dates from round 1 but was not caught then.
- **Experiment facts and typed absences:** unchanged, apart from findings 1 and 6. I re-checked them against p. 1 Methods: vacuum furnace; 1900 °C = 2173.15 K; ~10^-6 torr; no sweep gas; no cell/liner, orifice, sensor, uncertainty, calibration or purity printed; detectors are TESCAN LYRA3 FIB/FESEM + Oxford AZtec, FEI NOVA NanoSEM 600 (EDS) and JEOL 8530F Hyperprobe (WDS). All still consistent with the print.
- Advisory, not counted: the scored series block (`units: "...; loss in percent"`, `method_class: measured_tabulated`) still carries the ratio and loss keys next to the oxides. Only the four oxides become scored observations, so the migrated evidence is correct. A `loss_basis` note is already present.

## Printed-number inventory (completeness)

Every printed number outside figure plot areas is carried:
- Table 1: 64 cells.
- Methods: 1900 °C; 0–45 min; ~10^-6 torr; CaO/Al2O3 ~0.3.
- Introduction: ~0.3; "several million years" (no number printed).
- Results: CaO/Al2O3 = 1.2; ~1.1 and ~1.5.
- Fig. 1 labels: 10 and 45 min, 1900 °C, 500 μm ×2.
- Fig. 3 labels 0.3/1.2/0.8 and the caption's 1900 °C, 1 wt% and 0.5 wt%.
- Header: LPI 2083, abstract 1965, 2018, 49th.
- Affiliation numerals (5), CV3 ×2, and 40 reference numerals.

Excluded as identifiers or figure-only, as in round 1: author superscripts, inline citation labels, formula subscripts, instrument model numbers (carried anyway in the detector string), and Fig. 2/3 axis ticks and plotted points. **Printed numbers not carried: 0.**

## Pages, citation, sha, paths

- Rendered both pages with `pdftoppm -r 300 -png` and read the images in full, plus 600 dpi crops of Table 1, Fig. 1 (top and bottom), the p. 2 Discussion ending and the References. Pages read: 2.
- PDF sha256 `8a0f1ed3c4073a5d4e28813a4abf40136be29cba7a8dc6c7fe5e7d2ad763ca4f`, size 225796 bytes. Sidecar and extract `corpus_sha256` match. The citation matches the print (49th LPSC 2018, LPI Contrib. No. 2083, abstract 1965). Licence/access are `owner-supplied copy`/`owner-supplied` (unchanged; none invented).
- `rg -n '/Users/|/private/'` over the extract, `tables/<sid>/` and the sidecar exits 1 (no absolute paths).

## Validation (green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461)

- Reader checkout: `~/ci-scratch/regolith-green-ro` on the Mac, detached at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`, used read-only (`PYTHONDONTWRITEBYTECODE=1`; `git status --short` empty afterwards). Interpreter `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python`, `PYTHONPATH` = that checkout. **`engines/engines.local.toml` EXISTS in this checkout.**
- Corpus: sparse worktree `~/Repos/regolith-corpus/worktrees/rev2-ivanova-2018-spinel-rich-cai-evaporation`, detached at the tip (46 MB), created with SEAT-COMMON's sparse recipe. No build_index or migrate_pilot script was run.
- Migrator: ran `Migrator(root=Path.cwd(), index={}, aliases={})` from the green checkout, then `_migrate_extract(<absolute corpus extract path>)`, then `finalize()`.
  - Result: **hard issues 0**; 32 observations, all `measured_tabulated`; 8 context records; evidence fallthrough `{}`.
  - 64 soft `identity_incomplete` issues (sample mass and fO2 not reported), as in round 1.
  - 1 queue entry: the phase string "quenched CAI-like evaporation residues" is not in the closed phase map, also unchanged from round 1.
- Fidelity validator: `tools/validate_literature_extracts.py --check-fidelity-match <absolute path to the corpus worktree's extracts/ivanova-2018-spinel-rich-cai-evaporation.yaml>`, run from the green checkout. Output: `OK: 1 extract file(s) valid`, exit 0.
- Ledger tests, from the corpus worktree: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 … -m pytest -q -c /dev/null -o addopts= -p no:cacheprovider --rootdir=. tools/test_ledgers_valid.py`. Result: **688 passed**, exit 0. The worktree stayed clean.
- The validators pass but do not catch required changes 1–3: a semantically wrong class name, a stray YAML key, and a missing claim.

!COMPLETE: rev-ivanova-2018-spinel-rich-cai-evaporation-r2 — FIX-FIRST, pages read 2, rows checked 8, mismatches 3, printed numbers not carried 0
