# Confirm review, round 2: ivanov-1975-lunar-vaporization

VERDICT ON COMMIT: FIX-FIRST bde1f3f68a410211614b5d5fd9ec8aad4dd1c0b0

rows checked 35, mismatches 2, printed numbers not carried 0

- Seat: regolith-empirical corpus review seat (batch2 section A), 2026-10-05 (ET).
- Commit under review: `bde1f3f68a410211614b5d5fd9ec8aad4dd1c0b0` on `hunt/ivanov-1975-lunar-vaporization`. Fetched from the mirror
  `mac-studio-256-1:Repos/regolith-corpus.git` (`git fetch origin hunt/ivanov-1975-lunar-vaporization`). `origin/hunt/...` resolved to exactly
  this sha, and so did the review worktree HEAD. Parent = `5e89c70a6190054d756ef9a10c41cc94de4e7a47` (the round-1 reviewed tip). The fix commit
  touches only `extracts/ivanov-1975-lunar-vaporization.yaml`, `tables/ivanov-1975-lunar-vaporization/t1.provenance.yaml`, and `t2.provenance.yaml`.
- Inputs: round-1 `review.md` (FIX-FIRST, 7 required changes), `extraction-report.md` (including "Fix round 1 — solfix-AN", treated as a claim to test),
  `extraction-brief.md`, `TEMPLATE-review.md`.
- Stance: I assumed every round-1 finding was NOT FIXED until the page image and the tip showed otherwise, and I checked the full fix diff
  (`git diff 5e89c70a bde1f3f6`) for collateral damage.

## Why FIX-FIRST

All seven round-1 findings are FIXED. All numerical content is exact: Table 1 has 118 cells and 20 dashes, Table 2 has 30 cells, and every other
printed number is present. The blocker is collateral damage: fix item 16 ("restored the printed wording ... 'basic composition' ... 'aluminium'")
rewrote two verbatim quotes that the parent commit had right. The page images print otherwise:

1. **PDF p. 5 / printed p. 1345, line 6 of the page** (extract: `ivanov_1975_directional_claims_pages1341_1349` → `statements`, quote beginning
   "these components (Na, K, Fe)"): the print reads "…significantly more volatile than silica in melts of **basaltic** composition." The tip has
   "melts of **basic** composition." Restore `basaltic` exactly as the parent 5e89c70a had it. In petrology "basic" and "basaltic" do not
   mean the same thing, so this is a meaning change, not just a style change.
2. **PDF p. 3 / printed p. 1343, the paragraph beginning "The impossibility of calculating the minerals"** (same context, quote beginning "They are
   also characterized by much lower alkali concentrations"): the print reads "much higher concentrations of calcium, **aluminum**, and titanium."
   The tip has "**aluminium**". Restore `aluminum` exactly as the parent had it.

The third wording change in item 16, "estimates" → "estimations" (PDF p. 3 / 1343, "It should be noted that the estimations of silica loss so
obtained are undoubtedly low"), is **correct** against the image and must stay. Fixing the two items above is a two-word change on the
directional-claims context, with no change to structure or numbers. A confirm round after that change can be very short.

The fixer's own report repeats both wrong readings in its quote list (report lines quoting p. 1343 "aluminium" and p. 1345 "basic
composition"). If the report is revised, correct those too.

## Round-1 findings: disposition (NOT-FIXED stance)

| # | Round-1 required change | Evidence at bde1f3f6 (and page image) | Disposition |
|---|---|---|---|
| 1 | Carry the five sample-f parameters, p. 8 / 1348 | New context `ivanov_1975_sample_f_comparison_parameters`, page 8 / published 1348 / index 7, `sample_id: f`, `glass_group: 4`, `compared_glass_group: 6`: `ol/(ol+px)` 0.3; `(FeO+MnO)/(FeO+MnO+MgO)` 0.36; `(or+ab)/(or+ab+an)` 0.007; anorthite content of plagioclase 99.3; `ilmenite_content_percent` 4.93. The image (parenthesized equations after "sample 'f' has parameters close to that for glasses of Group 6") reads 0.3; 0.36; 0.007; 99.3; 4.93%. The oxide formula FeO+MnO is kept as printed, and no Table 2 statistics were substituted. The context survives migration intact. | FIXED |
| 2 | "about half of the glasses analyzed", p. 5 / 1345 | source_qualifications page 5 / 1345: "(they amount to about half of the glasses analyzed) … approximate verbal context, not an exact measured percentage." The image reads "(they amount to about half of the glasses analyzed)". No 50% observation was created. | FIXED |
| 3 | Locators coherent; convention stated | `extraction.locator_convention` is stated, and both provenance files state the same convention. Programmatic check of all 76 `page`/`published_page` mappings: `published_page − 1340 == page` and `pdf_page_index == page − 1` everywhere, with 0 violations. Maturity sentence: page 1 / 1341, and the short duplicate was removed (image p. 1 confirms). Mare/volatility/masking/olivine ratio and the 4 expected variations: page 5 / 1345 (image confirms). Rarity/masking passage: page 4 / 1344 with a continuation note to 1345 (image: begins at the foot of 1344, ends "forms (i.e. replacement of pyroxenes by olivines)" at the top of 1345). Group-6 vs Group-4, sample f, Apollo-16 30 samples / ≥3.5%, Conrad 34/nine, Dowty 34.8%, and the limited-use conclusion are all at page 8 / 1348. The wrongly located p. 1345 duplicates were removed and the fuller p. 1348 copies kept. The p. 1349 quotes are at page 9. Table 1 is at page 4 / idx 3 / 1344, Table 2 at page 7 / idx 6 / 1347, and Figure 1 at page 6 / idx 5 / 1346, in both contexts and fidelity_samples. | FIXED |
| 4 | 34.8% bound to the interstitial phase; nine ⊂ Conrad's 34 lithic fragments | numeric_context p. 8 / 1348: "Dowty et al. (1973) found in Luna 20 fines a lithic fragment with an overall SiO2 deficit; its interstitial phase has a very low SiO2 content of 34.8%. This is not a whole-fragment analysis." Conrad: "assigned 34 lithic fragments to the spinel-troctolite group; nine of those fragments … separate from the 30 Ridley et al. (1973) Apollo 16 gabbro-anorthosite glass samples." The "Nine samples" quote has the same scope. The image p. 1348 matches: "contains an interstitial phase with a very low SiO2 content (34.8%)"; "Conrad et al. (1973) assigned 34 lithic fragments to the group of spinel troctolites. Nine samples from these…". | FIXED |
| 5 | `wo = en + fs` construction; loss relation and inputs; 10.58 locator; model description | The a–d normative `model` text now gives the p. 1343 construction ("calcium remaining after plagioclase formation sets pyroxene through wollastonite and wo = en + fs (p. 1343)… Table 1 and its footnote … (p. 1344)"). The equality and the separate caution `en + fs > wo` are both in source_qualifications p. 3 / 1343. Loss context: relation `100 × SiO2(added) / [SiO2(found) + SiO2(added)] (Table 1 footnote, p. 1344)`. `derived_from` and `derivation.inputs` now include the a–d normative contexts, which carry `added_SiO2_wt_percent` 4.45 / 2.88 / 1.47 / 0.78, alongside the a–d chemistry. Every parent resolves. The 10.58% example is now its own context at page 4 / 1344, Table 1 footnote, relation `100 × 4.45 / (37.6 + 4.45) = 10.58%`, parents sample-a chemistry plus normative. The false Table-1 tag and the "page 1346" formula text on the loss context are gone. The image p. 1344 footnote matches, and my recomputation gives 10.5826…%. | FIXED |
| 6 | Table 2 method attribution | Both group summaries: `method_class: model_derived`, `model:` "Authors calculated normative compositions from Chao et al. (1972), Tables 4, 5, 6 (p. 1345), and summarized … ranges and averages in Table 2 (p. 1347)", plus `primary_data_attribution: Chao et al. (1972), Tables 4, 5, 6; full group input populations are external.` No internal parents were invented. The t2 provenance says the same. The six oxide contexts are still `quoted_attributed`, and the normative and loss contexts are `model_derived`. The image p. 1345 reads "We have calculated the normative composition of these glasses (Chao et al., 1972, Tables 4, 5, 6)" and the Table 2 caption reads "(primary data are from Chao et al., 1972)". | FIXED |
| 7 | Brown et al. (1971), p. 39, fragment 104EO931HG | numeric_context p. 3 / 1343: "A cited Brown et al. (1971, p. 39, fragment 104EO931HG) comparison gives silica loss from 0% to more than 30%", with a note that the fragment identifier completes on p. 1344. The duplicate range in the p. 1346 loss context was removed. The image p. 1343 ends "(Brown et al., 1971, p. 39, fragment" and p. 1344 resumes "104EO931HG) for particles from different samples." | FIXED |
| inv. | Round-1 inventory note: Group 5 and 7 names | source_qualifications p. 5 / 1345: basalts (4), feldspathic peridotites (5), spinel troctolites (6), peridotites (7). The image matches. | FIXED |
| inv. | Round-1 author-report overstatement (sample b wo ≠ en+fs) | The report withdraws its "no internal contradiction" claim. Printed 8.61 / 6.41 / 2.47 are unchanged in the tip. | FIXED (report) |

## Collateral-damage audit of the fix diff

I read every hunk of `git diff 5e89c70a bde1f3f6` against the page images:

- Page-field renumbering (3→4, 5→6, 6→7, 7→8, 8→9; quote moves 1344→1345, 1345→1348): all correct against the images, with 0 violations
  programmatically.
- Removed keys `sample_a_example_percent`, `attribution_for_comparison`, `printed_comparison_range` from the loss context. These were moved to the new
  footnote-example context and the Brown numeric_context, so no value was lost. The migrated context count went from 18 to 20.
- Removed duplicates: the short maturity copy and the three wrongly located p. 1345 copies. The fuller p. 1348 copies, including "etc.", were kept,
  so no statement was lost.
- Figure 1: added `printed_silica_loss_scale_percent: 5` and `legend_group_numbers: [4, 5, 6, 7]`. Both match the image (the "5% / −SiO2" scale
  bar at the An corner; legend Chao's Gr. 4/5/6/7). Still figure_only, with no coordinates digitized.
- Provenance files: convention line added, and the t2 notes now describe the author model summary. Correct.
- Quote wording: "estimates"→"estimations" is correct. **"basaltic"→"basic" and "aluminum"→"aluminium" are regressions** (see above; these
  are the 2 mismatches).
- No numerical leaf changed in any Table 1 or Table 2 context (programmatic comparison below).

## Table audit (all rows, no sampling)

I re-checked Table 1 (PDF p. 4 / 1344) and Table 2 (PDF p. 7 / 1347, rotated) digit by digit against crops of fresh 250 dpi renders, then compared
them programmatically CSV → extract:

- Table 1: 25 CSV rows (2 ID rows plus 23 value rows), with 118 numeric cells matching the print and the extract maps (0 mismatches). There are 20
  printed dashes (Na2O a–e; K2O b, f; P2O5 a, b; added SiO2 e, f; or b, f; ab a–e; ap a, b). Each one is an absent key, never zero. The vectors
  match round 1's per-row table, which I re-confirmed against the image: SiO2 37.6/39.0/41.0/40.0/41.7/43.8 … ap —/—/0.02/0.02/0.07/0.06. The
  Chao IDs are read as J7/C1/I11/19/I1/O11. The scan's 1/l glyph ambiguity is unchanged and bounded, as in round 1.
- Table 2: 10 CSV rows and 30 statistics (min/max/avg) matching the print and the extract (0 mismatches). Sample counts are 22 and 5. Group-6
  anorthite 100/100/100 expands the single printed 100 range.
- Fidelity samples (5): the values equal the context payloads, and the locators carry the corrected pages 4/4/4/7/7.

rows checked = 35 table rows (25 + 10). Separately, I checked 34 directional quotes, 9 numeric_context, and 8 source_qualification statements
against the images. The 2 mismatches are the two quote regressions. Numeric mismatches are 0.

## Printed numbers carried (full page reread)

Every scientific number on pp. 1341–1350 is carried with its correct binding: >30% (1341); volatility order and about 1400 °C (1342); wo = en+fs,
(en+fs)>wo, 0% to >30%, Brown p. 39 / 104EO931HG (1343–1344); Table 1 and its footnote formula, plus 10.58% (1344); >200 particles, eight groups,
Groups 4–7, about half, Chao Tables 4/5/6, two of five (a, b) (1345); 10.6/6.9/3.5/1.9%, Fe/(Fe+Mg) ≈ 0.3, 5% scale (1346); Table 2 (1347);
sample-f 0.3/0.36/0.007/99.3/4.93%, 30 samples / ≥3.5%, 34.8%, 34/nine (1348). Pp. 1349–1350 have conclusions and references only (bibliographic
numbers are not observations, as in round 1). **Printed numbers not carried: 0.**

## Non-blocking observations (no change required for LAND)

- The sample e/f normative `model` still says "Standard CIPW normative calculation". Table 1 heads the whole block "with added SiO2" and prints
  dashes for e/f additions, so the wording is a harmless inference. A neutral alternative would be "authors' normative calculation; no SiO2 addition printed".
- The p. 1342 volcanic-glass quote silently drops the two "(Heiken et al., 1974)" parentheticals without an ellipsis. This predates the fix; round 1
  did not flag it.
- The Table 1 added-SiO2 header prints "%", and the extract writes "wt.%". This predates the fix and is consistent with the footnote formula's use of
  SiO2(found) wt.%.

## Experiment facts, absences, citation, hashes, paths

The source still has no experiment, bench, apparatus, calibration, pressure, ionization or activity rows: `experiments: []`, `observations: []`.
The absences are bounded as in round 1 and were re-read against the full ten pages. The fix invented nothing.
`shasum -a 256` of the raw PDF gives `245b9a65f430f5ed24f990aa22451bf0821c8f12cac8b51b2dcc49cfd7e6bbf9`, which matches the sidecar and the extract.
The PDF is 167384 bytes and 10 pages. Citation and licence are unchanged and still correct ("not recorded — verify before any redistribution").
`rg -n '/Users/|/private/'` over the three changed files found no matches. `git diff --check 5e89c70a bde1f3f6` is clean.

## Validation (green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461)

- Environment: Simon-MacBookPro-M5. Interpreter `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python`, with `PYTHONPATH` set
  to a clean detached checkout at exactly 61ec839da (`~/ci-scratch/regolith-green-ro`, `git status` clean) and `PYTHONDONTWRITEBYTECODE=1`.
  The corpus is a sparse review worktree detached at bde1f3f6 (`~/Repos/regolith-corpus/worktrees/rev2-ivanov-1975-lunar-vaporization`).
- **`engines/engines.local.toml` EXISTS** in that green checkout. It is untracked and gitignored (`.gitignore:105`), and the readers here do not use it.
- Migrator: `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<corpus extract>)` then `finalize()`. Result: **hard issues 0**,
  validation issues `()`, 1 Work, 0 Benches, 0 Experiments, 0 Observations, **20 contexts**. The single queue entry is the expected extract-level typed absence
  "extract yielded no observations", identical for the parent commit. The sample-f context payload survives migration verbatim.
- `tools/validate_literature_extracts.py --check-fidelity-match <corpus extract path>`, run from the green checkout with the explicit corpus YAML
  argument: **OK: 1 extract file(s) valid**.
- Corpus worktree `tools/test_ledgers_valid.py` (`pytest -c /dev/null -o addopts= -p no:cacheprovider --rootdir=.`,
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`): **661 passed**.
- Passing these gates does not catch the two quote-wording regressions. Only the page images do.

Pages: all 10 rendered with `pdftoppm -r 250 -png` (1907 × 2750 px) and read, with 250 dpi crops of Table 1, Table 2 (rotated), the p. 1343,
1345, 1346 and 1348 passages, and the Figure 1 scale. I did not run build_index.py or migrate_pilot_extracts.py, changed no tracked file, and
merged nothing.

## Required changes (FIX-FIRST)

1. `extracts/ivanov-1975-lunar-vaporization.yaml`, context `ivanov_1975_directional_claims_pages1341_1349`, quote at page 5 / published 1345:
   change "melts of basic composition." to "melts of basaltic composition." (PDF p. 5 / printed p. 1345, 6th text line).
2. Same context, quote at page 3 / published 1343: change "calcium, aluminium, and titanium" to "calcium, aluminum, and titanium" (PDF p. 3 /
   printed p. 1343, paragraph "The impossibility of calculating…", 6th line of that paragraph).
3. Keep "estimations" (p. 1343) as it is. Correct the same two words in the extraction report's quote list if the report is re-issued.

!COMPLETE: rev2-ivanov-1975-lunar-vaporization — FIX-FIRST, pages read 10, rows checked 35, mismatches 2, printed numbers not carried 0
