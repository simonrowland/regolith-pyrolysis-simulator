# STATUS: fix ivanov-1975-lunar-vaporization (batch5 section B)

- **From:** regolith-empirical fix seat (fix-b57), 2026-10-05 ~21:15 ET
- **Branch:** `hunt/ivanov-1975-lunar-vaporization` on the mirror `mac-studio-256-1:Repos/regolith-corpus.git`
- **Started from:** `bde1f3f68a410211614b5d5fd9ec8aad4dd1c0b0`. Before I started, `git fetch origin hunt/ivanov-1975-lunar-vaporization` resolved to exactly this sha.
- **New tip (pushed, re-fetched and verified on origin):** `fc74c91948f1fb887ca9de4807be3cb874b26746` (parent `bde1f3f68a410211614b5d5fd9ec8aad4dd1c0b0`)
- **Review applied:** `review-r2.md` (round-2 confirm review: FIX-FIRST, 2 mismatches, both quote-wording regressions)
- **Items fixed 2/2** (plus item 3, "keep 'estimations'", checked and kept). **Corrections: 2** (two words in two quotes). **Hard issues: 0.**
- **No confirm review follows; main merges directly after this fix.** I checked every item myself against fresh page images and did not rely on the review's or the report's wording.

## Per-item table

| review-r2 item | What changed at fc74c919 | Page evidence (my own 250 dpi renders, full page plus crop) |
|---|---|---|
| 1. Context `ivanov_1975_directional_claims_pages1341_1349`, quote at page 5 / published 1345 | "…more volatile than silica in melts of **basic** composition." → "…melts of **basaltic** composition." (`extracts/…yaml`, line 827) | PDF p. 5 / printed p. 1345, lines 5–6: "these components (Na, K, Fe) are significantly more volatile than silica in melts of basaltic composition." Read on the image. |
| 2. Same context, quote at page 3 / published 1343 | "calcium, **aluminium**, and titanium" → "calcium, **aluminum**, and titanium" (line 807) | PDF p. 3 / printed p. 1343, paragraph "The impossibility of calculating the minerals…", 6th line: "concentrations of calcium, aluminum, and titanium, in comparison (where possible)". Read on the image. |
| 3. Keep "estimations" (p. 1343) | Unchanged: "the estimations of silica loss so obtained are undoubtedly low…" | PDF p. 3 / printed p. 1343: "It should be noted that the estimations of silica loss so obtained are undoubtedly low since as a rule (en + fs) > wo". Read on the image. Correct as is. |

Further checks:
- Both restored quotes are now byte-identical to the parent `5e89c70a`, the commit review-r2 said had them right. I compared them programmatically (`quote in parent quotes == True` for both).
- `rg 'aluminium|basic composition'` over the extract, `tables/ivanov-1975-lunar-vaporization/` and the ledger finds 0 matches, so neither wrong reading survives anywhere in the corpus files for this sid.
- The diff `bde1f3f6..fc74c919` changes 1 file, +2/−2 lines, only these two words. No structure, number, locator or other quote changed. `git diff --check` is clean.
- The extraction report lives in main's package, not in the corpus repo, so I did not re-issue it. Its quote list still has "aluminium" (p. 1343) and "basic composition" (p. 1345). If main re-issues the report, those should read "aluminum" and "basaltic composition".
- The raw PDF's sha256 is `245b9a65f430f5ed24f990aa22451bf0821c8f12cac8b51b2dcc49cfd7e6bbf9`, which matches the sidecar and the ledger. It has 10 pages, and I rendered all 10 with `pdftoppm -r 250 -png`.

## Validation (green `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`)

- Environment: Simon-MacBookPro-M5. Interpreter `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python`, with `PYTHONPATH` set to `~/ci-scratch/regolith-green-ro` (detached at exactly 61ec839da, `git status` clean; I reused it read-only) and `PYTHONDONTWRITEBYTECODE=1`.
- **`engines/engines.local.toml` EXISTS** in that green checkout. It is untracked, and the readers here do not use it.
- Corpus: sparse fix worktree `~/Repos/regolith-corpus/worktrees/fix-b57-ivanov-1975-lunar-vaporization`, on branch `hunt/ivanov-1975-lunar-vaporization` tracking origin. It used about 46 MB and was removed after delivery.
- Migrator: `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<extract>)` then `finalize()`, run from the corpus worktree. Result: **hard issues 0**, validation hard issues `[]`, 1 Work, 0 Benches, 0 Experiments, 0 Observations, **20 contexts**, the same as at bde1f3f6. The one queue entry is the expected extract-level typed absence "extract yielded no observations". I also checked the migrated context payload: it contains "calcium, aluminum, and titanium", "melts of basaltic composition." and "estimations of silica loss", and contains neither "aluminium" nor "basic composition".
- `tools/validate_literature_extracts.py --check-fidelity-match <absolute corpus extract path>`, run from the green checkout: **OK: 1 extract file(s) valid**.
- `tools/test_ledgers_valid.py` in the corpus worktree (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -c /dev/null -o addopts= -p no:cacheprovider --rootdir=.`): **661 passed**.
- I did not run build_index.py or migrate_pilot_extracts.py. I did not merge into mirror main. The commit used an explicit pathspec and has no trailers.

!COMPLETE: fix-ivanov-1975-lunar-vaporization — tip fc74c91948f1fb887ca9de4807be3cb874b26746, items fixed 2/2, corrections 2, hard issues 0
