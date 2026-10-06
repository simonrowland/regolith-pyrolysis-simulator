# STATUS: fix ivanova-2018-spinel-rich-cai-evaporation, round 3

**From:** regolith-empirical (corpus fix seat)   **To:** regolith-main   **At:** 2026-10-05 ~21:50 ET
**Branch:** `hunt/ivanova-2018-spinel-rich-cai-evaporation` on mirror `mac-studio-256-1:Repos/regolith-corpus.git`
**Started from:** `213a87b3a31657fabb77e598e9bdd58df2809530` (origin tip verified equal before work)
**New tip:** `189c1830818fa3031146b8db40d4c51924f70dbe` (pushed to hunt/<sid> only; `git ls-remote` confirms; not merged into mirror main)
**Review applied:** review-r3.md (grok round 3, FIX-FIRST, 1 mismatch)

items fixed 1/1, corrections 2 (1 required by review-r3 + 1 found during self-verification), hard issues 0

## Per-item table

| Review item | Change | Page evidence |
|---|---|---|
| r3 #1: p. 2 Results, ~1.1/~1.5 sentence, "yet different" must read as printed | Quote now reads "...intersect in Fig. 3), yet differnent CaO/Al2O3." Added a locator note, parallel to the "Futher" note, saying 'differnent' is the printed spelling and is kept. The earlier "different evaporation trajectories" ('dif-ferent' across a line break) is unchanged because the print spells it correctly. | pdftoppm 600 dpi crop of p. 2, right column, read: "intersect in Fig. 3), yet differnent CaO/Al₂O₃. Taken together..." Text layer: the word `differnent` at x 419.75–459.24 pt, y 187.27 pt (10 glyphs). The prior line on the same crop ends "Note also the dif-" / "ferent evaporation trajectories". |
| Self-verification extra (not raised by r3): p. 1 Table 1 footnote quote had an added terminal period | Quote now ends "...before evaporation at 1900 °C" with no period. The locator note records that the printed footnote has no terminal period. | pdftoppm 600 dpi crop of p. 1 under Table 1, read: "...ing composition of the samples before evaporation at 1900 °C", then a gap, then "Bulk compositions of experimental run products of". Text layer: the last word is `°C` at x 508.0–516.5 pt, y 552.9 pt, with no following glyph. |

Diff 213a87b3..189c1830: only `extracts/ivanova-2018-spinel-rich-cai-evaporation.yaml`, +4/−4 (two quote lines and two locator lines). The commit does not touch the CSV, provenance, sidecar, ledger, text, observations, Table 1 digits, conditions, benches or figure records.

## Round-2 fixes re-verified at the new tip (each one re-checked; none assumed)

| r2 item | Re-check at 189c1830 | Holds |
|---|---|---|
| 1. Table 1 full-table context class = this-work, `column_provenance` kept, no `quoted_*` | YAML `values.method_class: measured_tabulated`, and `column_provenance` is present (oxides measured/tabulated; ratio and losses author-reduced, no formula inferred). After migrate+finalize, the table context is `measured_tabulated` and all 32 observations are `measured_tabulated`. All 64 context cells were re-read against a fresh 600 dpi crop of p. 1 Table 1 and every one matches. | YES |
| 2. Claims container locator: one quoted `section` string, no stray key | The parsed and migrated locator has `section: 'Introduction, Experimental and analytical methods, Results and Discussion'` plus `note`, with no stray key. 26 claims, each with its own page locator. | YES |
| 3. p. 2 Discussion sentence 2 (associated solutions / oxide activities) verbatim | Fresh 600 dpi crop of p. 2: "The experiments confirms our thermodynamic calculations based on the theory of associated solutions and on experimental data for activities of oxides in the system CaO-MgO-FeO-Al₂O₃-TiO₂-SiO₂ [3,8]." The extract matches, including "confirms" and "[3,8]". | YES |

The round-1 items listed in r3 (approximate ~10^-6 torr, units, figures, "Futher", 5aN-3 quench, affiliation/CV3/references) were not touched by this commit.

## Whole-extract quote sweep (self-verification)
Every `quote`/`text` string in the extract (28) was compared with the `pdftotext` text after normalizing whitespace, line-break hyphens, quote marks and the Symbol dash. All 28 match except:
- the footnote period, now fixed (above);
- line-break artifacts where the print has a real hyphen at a line end (5aN-like, MgO-rich, Al2O3-TiO2, acidity-basicity);
- superscript affiliation markers, omitted as the units say.

The single quotes around 'Christmas tree' and '5aN-3', where the print has double quotes, are the normalization already accepted in rounds 1–3. They were left unchanged.

## Validation (green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461)
Reader: `/Users/simonrowland/ci-scratch/regolith-green-ro`, detached at 61ec839da, run read-only with `.venv/bin/python` from `~/Repos/regolith-pyrolysis-simulator` and `PYTHONPATH` set to that checkout. **`engines/engines.local.toml` EXISTS in that checkout.** The green checkout was clean after the runs.
- Migrator: `Migrator(root=<green>, index={}, aliases={})._migrate_extract(<extract>)` then `finalize()`. **Hard issues 0**, registry issues 0. 64 soft `identity_incomplete` issues. 32 observations, all `measured_tabulated`. 8 context records. 1 queue entry. This is the same soft/queue picture r3 reported. The migrated claims show "yet differnent CaO/Al2O3." and "...at 1900 °C" with no period.
- Fidelity validator: `tools/validate_literature_extracts.py --check-fidelity-match <extract>` printed `OK: 1 extract file(s) valid` and exited 0.
- Ledgers: `pytest -q -c /dev/null -o addopts= -p no:cacheprovider --rootdir=. tools/test_ledgers_valid.py` from the corpus worktree gave **688 passed**.
- `rg '/Users/|/private/'` on the extract found no matches. `tools/build_index.py` and `tools/migrate_pilot_extracts.py` were NOT run.

## Explicit self-verification statement
Main merges directly with no confirm review, so I checked the result myself against freshly rendered page images (pdftoppm 300 dpi full pages plus 600 dpi crops, all actually read):
- The single review-r3 item is fixed and matches the print.
- All three round-2 fixes still hold at tip 189c1830.
- All 64 Table 1 cells still match the page.
- One extra fidelity miss turned up during the sweep (an added period at the end of the Table 1 footnote quote). It was corrected against the image.
- No other divergence remains in the quoted prose.
- No value was guessed and no extract data was invented.

Worktree: sparse `~/Repos/regolith-corpus/worktrees/fix3-ivanova-2018-spinel-rich-cai-evaporation`, removed after delivery.
