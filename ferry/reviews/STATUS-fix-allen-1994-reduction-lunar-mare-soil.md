# STATUS: fix allen-1994-reduction-lunar-mare-soil (corpus batch 11, section B)

**From:** regolith-empirical corpus FIX seat   **To:** regolith-main   **At:** 2026-10-05 ~22:35 ET
Review applied: our first review `REVIEW-allen-1994-reduction-lunar-mare-soil.md` (= package review.md; rows checked 119, mismatches 5,
printed numbers not carried 0; FIX-FIRST). A grok confirm follows (main runs it).

- Start tip: `83f01066e60facb4b4e22d07db81075bb2f6d5f6` (`git fetch origin hunt/allen-1994-reduction-lunar-mare-soil` = FETCH_HEAD, matched).
- **New tip on the mirror: `b61a775241f63c35249e3df8794d0fbc663b1fdd`** on `hunt/allen-1994-reduction-lunar-mare-soil`
  (push `83f01066..b61a7752`, fast-forward; re-fetched, origin = local HEAD; descendant of the start tip). Not merged into mirror main.
- One commit, author Simon Rowland, explicit pathspec `extracts/allen-1994-reduction-lunar-mare-soil.yaml` only (+26/-19); no AI trailers.
  Ledger, sidecar, raw/ untouched. No tables/ (the source has none).
- **Items fixed 8/8 required (M1, M2, M3, M4, M5, O1, O2, O3) + nit N1; corrections count 23 field edits (refs 4, quoted_from 1, 75061 form 1, phase labels 5, M4 3, M5 3, O1 1, O2 2, O3 2, N1 1) + 3 new locator notes** (see table). Hard issues 0.

## Setup
Sparse Mac worktree `~/Repos/regolith-corpus/worktrees/fix-allen-1994-reduction-lunar-mare-soil` (`git worktree add --no-checkout -B hunt/<sid>`
tracking origin; sparse `/*`, `!/raw/*/*`, `!/text/*/*`, `/raw/*/sidecar.yaml`, `/raw/<sid>/*`, `/text/<sid>/*`; 46 MB; `git add --sparse`).
`df -g /Users` at start: 60 GB available. tools/build_index.py and tools/migrate_pilot_extracts.py not run.
Green: read-only `~/ci-scratch/regolith-green-ro` at 61ec839da3ba288c5df4a80f6d3ef142bd8ab461; Python `~/Repos/regolith-pyrolysis-simulator/.venv/bin/python`;
`engines/engines.local.toml` EXISTS in the green clone.
PDF sha256 2d49c7a0dd8a24b8f8823e32883a6988242b3bb4f86e135e37769dcc3e5b7bdb (= sidecar). Both pages re-rendered with `pdftoppm -r 250`
(2119×2750) and read as four overlapping full-width strips each, to re-check every item below against the image.

## Per-item table (review item -> what changed -> page evidence)
| item | finding | change in extract | page evidence (re-read on the 250 dpi image) |
|---|---|---|---|
| M1 | ref (1) written "PLPSC 5, 843" | `cited_references_as_printed[0]` → `'(1) Heiken and McKay, 1974, PLSC5, 843'`. Same pass, refs (2)–(4) set to exact printed punctuation: `'(2) Allen et al., 1993, Icarus, 104, 291'`, `'(3) Gibson et al., 1994, submitted JGR'`, `'(4) Hawke et al., 1990, PLPSC20, 249'` (trailing periods not printed, dropped) | p24 References: "(1) Heiken and McKay, 1974, *PLSC5*, 843 (2) Allen et al., 1993, *Icarus*, *104*, 291 (3) Gibson et al., 1994, submitted *JGR* (4) Hawke et al., 1990, *PLPSC20*, 249" |
| M2 | `quoted_from` attributed the comparison to "Allen et al. (1994), reference (3)" | `quoted_from: 'Gibson et al., 1994, submitted JGR (reference (3))'` | p23 last paragraph "...crushed lunar basalt (3)"; p24 ref (3) = Gibson et al., 1994, submitted JGR |
| M3 | "Apollo 17" added to 75061 (not printed for it) | 75061 `sample.form` → `Lunar mare soil sample 75061; high-Ti mare soil; used as received`, with locator note quoting the printed wording and stating Apollo 17 provenance is printed only for 74220; 5 phase labels `Apollo 17 lunar mare soil 75061` → `lunar mare soil 75061` (mare weight-loss, mare alpha-Fe, Fig. 1, Fig. 2, mare qualifying statements). `rg "Apollo 17"` now hits only 74220 records and that note | p23 Lunar Samples "Sample 75061 is a high-Ti mare soil ..."; Results-Mare Soil "Lunar mare soil 75061"; "Sample 74220 is the Apollo 17 "orange soil,"" |
| M4 | mare alpha-Fe/oxygen-equivalent record `measured_direct` | `method_class: measured_reduced`; added `derivation_relation_as_printed` (verbatim two sentences) and `derivation_inputs_as_printed: VSM alpha-iron abundance relative to the starting-sample Fe2+ (starting Fe2+ content not printed)`. Numbers unchanged [47, 54], [1.9, 2.2] | p23 Results-Mare Soil: "The VSM data indicated that 47-54% of the Fe²⁺ in the starting sample was reduced to alpha iron metal. This was equivalent to the loss of 1.9-2.2% of the starting sample weight as oxygen." |
| M5 | glass alpha-Fe/oxygen-equivalent record `measured_direct` | same re-class + relation/inputs; numbers unchanged [32, 68], [1.7, 3.3] | p24 Results-Pyroclastic Glass: "The VSM data indicated that 32-68% of the Fe²⁺ in the starting sample was reduced to alpha iron, with the percentage increasing with temperature. This was equivalent to the loss of 1.7-3.3% of the starting sample weight as oxygen." |
| O1 | end-member sentence not carried | appended verbatim to `allen_1994_mare_soil_qualifying_statements`: `'They also provide an extreme "end member" for studies of reduction as a cause of lunar soil maturation.'` | p23 final paragraph, second sentence |
| O2 | method lineage "(2)" not carried | `allen_1994_method_and_characterization_context.values` gains `method_lineage_as_printed: 'The samples were reduced in a vertical tube furnace, with weight change continually monitored, as in previous tests with lunar simulants (2).'` and `method_lineage_attribution: '(2) Allen et al., 1993, Icarus, 104, 291'` | p23 Experimental/Analytical first sentence; p24 ref (2) |
| O3 | "which comprise the bulk of this sample" dropped; citation (1) not recorded | both `printed_composition` values now verbatim from the Lunar Samples paragraph, including "(1)": 75061 `'Sample 75061 is a high-Ti mare soil which contains pyroxene, plagioclase, ilmenite, minor olivine, agglutinates, and a trace of glass (1).'`; 74220 `'Sample 74220 is the Apollo 17 "orange soil," an essentially pure pyroclastic glass deposit (1). Orange spheres, which comprise the bulk of this sample, are completely glassy. Black spheres are the same glass partly recrystallized to olivine, ilmenite, and spinel.'`; each locator note records the citation (1) Heiken and McKay, 1974, PLSC5, 843 | p23 Lunar Samples paragraph (whole) |
| N1 (nit) | mare `time_course_as_printed` not verbatim | → `Sample weights decreased rapidly for the first 20 minutes and then fell more slowly, remaining essentially constant after the first hour.` | p23 Results-Mare Soil, second sentence |
| N2 (nit) | methods context `method_class: measured_direct` | left unchanged (discretionary; there is no closer closed-vocabulary token for a methods description, and changing it is not required) | — |
| sidecar N94-35398 (optional) | — | not changed (optional; archival stamp, not source data) | p23 header stamp |

No printed value was added, removed or altered in any numeric field: all 20 numeric context scalars are identical to 83f01066.
No new values invented; the "starting Fe2+ content not printed" phrase is a typed statement of absence (checked: pp. 23–24 print no starting FeO/Fe²⁺ content).

## Acceptance (green 61ec839da, run on the Mac)
- `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<worktree>/extracts/allen-1994-reduction-lunar-mare-soil.yaml)` then `finalize()`
  → 0 observations, 2 experiments, 1 bench, 13 context rows; **hard issues after finalize: 0** (total issues 0). One queue entry "extract yielded
  no observations" (expected, context-only extract; unchanged from the reviewed tip; its source_path is the runtime worktree path, not stored in the extract).
- Payload survival (serialised `works`/`context_by_work`/experiments/benches): both alpha-iron records carry `method_class: measured_reduced`,
  numeric ranges [47, 54]/[1.9, 2.2] and [32, 68]/[1.7, 3.3] as numbers, and both derivation strings; `method_lineage_as_printed`,
  "Gibson et al., 1994, submitted JGR", "PLSC5", "end member", "comprise the bulk" all present; "Apollo 17 lunar mare soil" count 0.
- `evidence_for()`: measured_direct, measured_reduced, figure_only, quoted_attributed → all resolve, none unknown. The two measured_reduced rows are
  context rows (d-032), not Observations, so the observation-level derived_from requirement does not apply and the migrator/validator raised nothing.
- Fidelity validator: `PYTHONPATH=~/ci-scratch/regolith-green-ro python tools/validate_literature_extracts.py --check-fidelity-match --show-warnings <worktree>/extracts/allen-1994-reduction-lunar-mare-soil.yaml`
  (run from the green clone, extract path passed explicitly) → `OK: 1 extract file(s) valid`, exit 0.
- Corpus worktree `python -m pytest -c /dev/null -q -p no:cacheprovider tools/test_ledgers_valid.py` → **687 passed**.
- `rg "/Users/|/private/"` over extract, ledger, sidecar: no hits.

## AMENDMENTS PROPOSED
- Context rows have no structured `derivation` home (relation/inputs/derived_from are Observation-level). The M4/M5 reduced status is carried as
  `method_class: measured_reduced` plus located `derivation_relation_as_printed` / `derivation_inputs_as_printed` strings; a schema home for context-row
  derivations would let these be checked mechanically.

## Housekeeping
Worktree `~/Repos/regolith-corpus/worktrees/fix-allen-1994-reduction-lunar-mare-soil` removed after push (`git worktree remove` + prune; `git worktree list` has no allen entry; `df -g /Users` after: 59 GB available). Only this sid's worktree and
branch were touched. No Mac listen pools armed.

!COMPLETE: fix-allen-1994-reduction-lunar-mare-soil — b61a775241f63c35249e3df8794d0fbc663b1fdd, items fixed 8/8 (+N1), hard issues 0, validator OK, ledgers 687 passed; awaiting grok confirm
