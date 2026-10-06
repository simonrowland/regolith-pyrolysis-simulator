# STATUS: fix yakovlev-2017-cai-acidity (batch 7)

**From:** regolith-empirical seat (fix-b57)   **To:** regolith-main   **At:** 2026-10-05 ~21:16 ET
**Source:** Yakovlev, Ryazantsev and Shornikov (2017), *Geochemistry International* 55(3), 251–256, DOI 10.1134/S0016702917020070
**Branch:** `hunt/yakovlev-2017-cai-acidity` on `mac-studio-256-1:Repos/regolith-corpus.git`
**Review applied:** review-r2.md (FIX-FIRST at 5062c439: rows 11, mismatches 2, which contain 4 discrepancies, labelled required changes A–D)

- Start tip (verified with `git fetch`, matches the assignment): `5062c4392a01f77199c1d64477c4e0d7fd0ce8d6`
- **New tip, pushed to origin `hunt/yakovlev-2017-cai-acidity`: `00d351379fc55e46af1e116a77d493cdaa463676`**
  (one commit, "Restore printed Yakovlev 2017 quote wording and Introduction locator". Parent 5062c439. Explicit pathspecs. No trailers.)

items fixed 4/4, corrections 4, hard issues 0

**No confirm review follows ("main merges directly after this fix"), so I verified every item myself.** I re-read each of the 4 required
changes against fresh 220 dpi renders of the page images (crops, plus a 2× zoom of the "min" line), not against the text layer.
I also confirmed after migration that each corrected string is present in the migrated context payload and that each old string is gone.

## Per-item table

| r2 item | Location (page image) | What changed in the extract | Page evidence (220 dpi render) |
|---|---|---|---|
| A | p.253 (pdf idx 2), left column, sentence after Eq. (3); row `yakovlev_2017_directional_claims_and_data_qualifiers` | "…in melts but is their acceptor instead." → "…in melts but is instead their acceptor." | The print reads: "The minus sign at α_SiO2 shows that silicon oxide does not act as a donor of oxygen ions in melts but is instead their acceptor. According to dependences (2) and (3)…" |
| B | p.254 (pdf idx 3), right column, paragraph before Eq. (4); same row | "the activity (or in our situation, the partial pressure) of this component in the vapor." → "the activity (or in our situation, the partial pressure of this component) in the vapor." | The print reads: "The activity of a component in melt controls, in turn, the activity (or in our situation, the partial pres-/sure of this component) in the vapor." |
| C | p.254 (pdf idx 3), right column, paragraph after Eq. (4), "three parameters" quote; same row | "(2) the concentration of the oxide in the multicomponent melt" → "…the oxide **min** the multicomponent melt", as printed. I added `translation_note: "The English translation prints ‘the oxide min the multicomponent melt’; ‘min’ is plainly garbled (evidently ‘in’) and is retained verbatim."` This is the same treatment the extract already uses for "at a rare of" and "составов". | The print reads "(2) the concentration of the oxide min the multicomponent melt, and (3) interaction of the oxide in the melt." I confirmed "min" in a 2× zoom crop. |
| D | p.252 (pdf idx 1), left column; row `yakovlev_2017_numeric_background_context` | Quote "laboratory experiments on evaporation of forsterite (Davis et al., 1990) and other compositions составов": locator `section` changed from `Experimental Data on Evaporation of Melts` to `Introduction`. Quote text and translation_note are unchanged. | The sentence sits in the p.252 left column below Fig. 2, in the Introduction. That section runs on to the top of the right column, and the "EXPERIMENTAL DATA ON EVAPORATION OF MELTS" heading starts only partway down the right column. |

Ledger: I appended one sentence to the existing `stages.transcribed.note` describing fix round 2. It is descriptive only, and no stage, date or evidence path changed.
No numbers, rows, method classes, typed absences, figures or completeness entries changed. The full diff from 5062c439 is
2 files changed (`extracts/yakovlev-2017-cai-acidity.yaml` +5/−4, `ledger/yakovlev-2017-cai-acidity.yaml` +1/−1). `git diff --check` is clean.

**Advisories in r2 (not required for LAND): I did not apply them**, to keep the merge diff limited to the required changes. Two are verified true on the
page images, and main may route them later: the print uses double quotation marks around “liberation” (p.253) and “normal” (p.254), and the extract has ‘…’;
the "Because of this, CaO is the dominant donor…" quote starts at the foot of p.253. The other two (the Fig. 4 attribution pair and the
Shornikov 2008 citation on p.255) are as r2 describes. I did not change them.

## Acceptance (green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461)

- Green checkout: read-only `~/ci-scratch/regolith-green-ro`, HEAD `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`, clean (nothing written).
  Python `~/Repos/regolith-pyrolysis-simulator/.venv/bin/python`, `PYTHONPATH=<green-ro>`, `PYTHONDONTWRITEBYTECODE=1`, cwd green-ro.
  **`engines/engines.local.toml` EXISTS in green-ro** (1331 B, Oct 4, untracked/gitignored).
- Migrator: `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<abs sparse-worktree extract path>)`, then `finalize()`:
  **works 1, experiments 0, observations 0, context rows 11, hard issues 0.** The queue has 1 entry, the expected "extract yielded no observations" (typed absence; no printed tables).
  Payload check of the migrated context rows: present: "but is instead their acceptor", "the partial pressure of this component) in the vapor",
  "the oxide min the multicomponent melt", the new note "evidently ‘in’", and the составов quote with `section: Introduction`. Absent: all 3 old strings.
- `tools/validate_literature_extracts.py --check-fidelity-match <abs sparse-worktree extract path>` from green-ro: **OK: 1 extract file(s) valid**, exit 0.
- Corpus `tools/test_ledgers_valid.py` from the sparse worktree (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 -c /dev/null -o addopts= -p no:cacheprovider`): **688 passed**.
- YAML of the extract and ledger parses. `rg '/Users/|/private/'` over the extract, ledger and sidecar finds no matches.
  PDF sha256 `60dc9c6a76f982afc947d62a385e2f6ffa4d51102b7601ad6f7042b766e42c98` = sidecar = extract `corpus_sha256`.
- Not run: tools/build_index.py and tools/migrate_pilot_extracts.py. Nothing was merged into mirror main.

## Seat / disk

- Mac sparse worktree `~/Repos/regolith-corpus/worktrees/fix-b57-yakovlev-2017-cai-acidity` (`--no-checkout`, main's sparse recipe, about 46 MB).
  `df -g /Users` showed 79 GB free at start and 77 GB during the work. The worktree was removed after delivery, and the local branch `hunt/yakovlev-2017-cai-acidity` was kept (it tracks origin and is unmerged).
- Page renders were kept under `~/Repos/regolith-corpus/.ferry-tmp/fix-b57-yakovlev-2017-cai-acidity/` and removed after delivery.

!COMPLETE: fix-yakovlev-2017-cai-acidity — 00d351379fc55e46af1e116a77d493cdaa463676, items fixed 4/4, corrections 4, hard issues 0
