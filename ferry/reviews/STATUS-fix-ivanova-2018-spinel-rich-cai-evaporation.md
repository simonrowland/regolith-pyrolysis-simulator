# STATUS: fix ivanova-2018-spinel-rich-cai-evaporation (batch4 B)

**From:** regolith-empirical corpus fix seat   **At:** 2026-10-05 ~21:05 ET
**Review applied:** review-r2.md (regolith-empirical round-2 confirm review, FIX-FIRST, 3 mismatches)

- **Start tip (verified):** `5c61b3e63224b67fead41d1c4b5497c6309d3bd9` (`git fetch origin hunt/ivanova-2018-spinel-rich-cai-evaporation` on mirror `mac-studio-256-1:Repos/regolith-corpus.git` resolved to exactly this sha)
- **New tip on mirror:** `213a87b3a31657fabb77e598e9bdd58df2809530` on `hunt/ivanova-2018-spinel-rich-cai-evaporation` (pushed to origin only; `git ls-remote` confirms; not merged to mirror main)
- **Items fixed: 3/3** (required changes). Recommended nits also applied: 4 of 4 (3 text corrections, 1 annotation).
- **Corrections: 7** (3 required + 4 recommended text corrections: ref [3] punctuation, ref [6] punctuation, affiliation line, CV3 sentence), plus 1 annotation note ("Futher"). One file changed: `extracts/ivanova-2018-spinel-rich-cai-evaporation.yaml` (+11/−8). CSV, provenance, sidecar, text unchanged.
- **Hard issues: 0** (migrator after finalize). Fidelity validator OK. `tools/test_ledgers_valid.py` 688 passed.

## Changes (each re-checked against the page images)

Pages rendered with `pdftoppm -r 300` (both pages, read in full) plus 600 dpi crops of p. 2 Discussion, p. 2 References, p. 1 affiliation block and p. 1 Introduction. I read all of them.

1. **p. 1 Table 1, context `ivanova_2018_table1_full_transcription`:** `values.method_class` `quoted_unattributed` → `measured_tabulated`. This is the authors' own table: Methods on p. 1 name the authors' own Chicago/Smithsonian analyses, and the Table 1 footnote says they calculated the losses. The `column_provenance` block stays as it was. It records the oxides as measured and the ratio and loss columns as author-reduced, and gives no loss formula. No `quoted_*` class is used for Table 1. Migrated context: `method_class: 'measured_tabulated'`.
2. **Claims container `ivanova_2018_qualifying_and_directional_statements`, locator:** the section value is now quoted: `section: "Introduction, Experimental and analytical methods, Results and Discussion"`. The container contains Methods quotes and the Table 1 footnote as well, so Methods is added to the list. I also added a container note: "the claims span pp. 1-2 (pdf_page_index 0-1). Each claim carries its own page locator." The stray `'Results and Discussion': None` key is gone from the migrated context (checked).
3. **p. 2 Discussion, sentence 2:** added as a located claim (p. 2 / pdf_page_index 1 / Discussion), inserted right after Discussion sentence 1. The text is verbatim from the 600 dpi crop and keeps the printed grammar: "The experiments confirms our thermodynamic calculations based on the theory of associated solutions and on experimental data for activities of oxides in the system CaO-MgO-FeO-Al2O3-TiO2-SiO2 [3,8]." Its note marks it as a comparative statement and as the stated model basis of the Figs. 2–3 calculated trends. Claims went from 25 to 26. `rg "associated solutions|confirms our"` now hits once.

Recommended (not counted in the review, applied, verified on the 600 dpi crops):
- References [3] and [6]: the comma after the volume is now a period, as printed: "LPS XLVIII. Abs. #1964." and "LPS XLVII. Abs. #2929.". [2] really is printed with a comma ("LPS XLVIII, Abs. #1363."), so it is unchanged.
- Affiliation context: the full printed line is now carried, including "Department of Mineral Sciences, National Museum of Natural History,". Units now read "source metadata as printed (affiliation superscript markers 1-3 omitted)". All five numerals are unchanged.
- CV3 context: the paraphrase is replaced by the verbatim p. 1 Introduction sentence, with a note that CV3 is printed twice.
- "Futher" claim: a locator note now says the printed spelling is the authors' typo, kept as printed. The quote text is unchanged.

No numbers were added, removed or changed. Table 1 (64 cells), the experiment facts, the typed absences and the 32 oxide observations are untouched. Nothing illegible was met, so no new typed absences were needed.

## Validation (green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461)
- Reader: `~/ci-scratch/regolith-green-ro` (detached at 61ec839da, used read-only, `git status --short` empty afterwards). Interpreter: `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python`. `engines/engines.local.toml` EXISTS in that checkout.
- Migrator: `Migrator(root=green, index={}, aliases={})`, then `_migrate_extract(<worktree extract>)`, then `finalize()`. Result: **hard issues 0**. 32 observations (all `measured_tabulated`), 8 context records, evidence fallthrough `{}`. There are 64 soft `identity_incomplete` issues (fO2 and sample mass not reported) and 1 queue entry (phase string not in the closed map). Both are unchanged from rounds 1 and 2.
- Fidelity validator: `tools/validate_literature_extracts.py --check-fidelity-match <extract>` gave `OK: 1 extract file(s) valid`, exit 0.
- Ledger tests: `tools/test_ledgers_valid.py` gave **688 passed**.
- `rg '/Users/|/private/'` over the extract, `tables/<sid>/` and the sidecar exits 1 (no absolute paths).
- Corpus worktree: sparse `~/Repos/regolith-corpus/worktrees/fix2-ivanova-2018-spinel-rich-cai-evaporation` (46 MB). It was removed after the push. No build_index or migrate_pilot script was run.
- Commit: explicit pathspec, author Simon Rowland, no AI/co-author trailers.

!COMPLETE: fix-ivanova-2018-spinel-rich-cai-evaporation — 213a87b3a31657fabb77e598e9bdd58df2809530, items fixed 3/3, corrections 7, hard issues 0
