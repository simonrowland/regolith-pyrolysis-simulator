# Data offer stack integration (work in progress)

Status: paused before cherry-picks because two reviewed items assign conflicting values to the same extract cells. The worktree remains on `worktree/w1103datastack`, based at `6512e8b9de88efbbaf12c315cc45034b7c5b7025` before this report commit.

## Reviewed-range inventory

All 56 named tips are present in the clone. The ranges after `origin/work-v064-green` contain 219 commits: 15 SC-456 prerequisite commits are already ancestors of the seat, and 204 new candidate commits remain. `git cherry` marks every non-ancestor candidate as new (`+`); none are patch-equivalent skips. A reverse file-patch check against the base worktree also failed for every candidate, so none of those deltas is already in the base tree. No candidate commits have been cherry-picked.

| Stack item | Tip(s) | New candidate commits | Skipped as present | Picked so far | Conflict / status |
|---|---|---:|---|---|---|
| A — SC-456 restack | `cb76c0423` | 100 | 15 ancestors already in code stack 3 | none | blocked by overlap with `yak24` |
| B — batch3 | 18 tips listed in the task | 44 | none | none | not reached |
| C — rp1 | 5 tips listed in the task | 7 | none | none | not reached |
| D — rp2 | 6 tips listed in the task | 9 | none | none | not reached |
| E — rp3 | 6 tips listed in the task | 11 | none | none | not reached |
| F — rp4 | 9 tips listed in the task | 17 | none | none | not reached |
| G — rp5 | 6 tips listed in the task | 7 | none | none | `yak24` overlaps A |
| H — rp6 | 5 tips listed in the task | 9 | none | none | not reached |

The 15 SC-456 commits skipped as ancestors are `1ac6c2e2a0b5e248cac66ea5f3c0c1aa22888661`, `4c89cf87897b29bc0158c1659569c2cb4b4e661b`, `1d574a444926f2eacf3cae0e7cd5bad62bccf845`, `17186a3177a7ee9470db4c29be8083e0a15cdecc`, `64464c08cf819922494eb925195d1905f199659a`, `8df361e62b8555c0a721595867c10168c7dcb148`, `ae3474bc6a94f019dd47020c37b8e5274c1f08a9`, `e67a32f664005e0c5cbd501a202bb1b959d6c239`, `b994b4d52ac46c7b8ee63f0f67be70eb741a28d8`, `7a96f079c99c3a02c0aa89bde9be89158edb37bd`, `0a9ce33d80a9d6e58c0f946803b3f008a376f179`, `6096a1e282379a1cc2f0a3ca2de30889e2c14290`, `20d0c11e7b0a319b8f8cec7c0bcc2a3b26ba84cd`, `8fb92637dc8859d33865c36ba510a41301dd3a1c`, and `0a79182969a52b722a6315d687d665e4829ab1b4`.

## Conflict requiring a controller ruling

The only cross-item extract-path overlap found is SC-456 item A and `yak24` in item G, both touching `data/literature/extracts/yakovlev-2024-cai-vaporization.yaml`:

- SC-456 commit `756a1f9e7e42ddb4b0317ad6f888cbf9bd96eaef` sets `values.method_class` to `model_derived` and adds a structured `values.derivation` describing Lewis relations (1)–(3), with raw inputs at Fig. 2B and the cited pure-compound references.
- `yak24` commit `9d657af9469dbea696ac433b9917d044025cd193` sets the same `values.method_class` cells to `measured_reduced` and adds a different structured `values.derivation` for Fig. 3A, with `derived_from` inputs from the Fig. 2B oxide activities and the statement that the pure-compound reference activity values are cited but not reproduced.

Conflicting observation cells:

| Observation | `values.method_class` conflict | SC-456 derivation relation | `yak24` derivation relation |
|---|---|---|---|
| `yakovlev_2024_fig3a_mgal2o4_figure_only` | `model_derived` vs `measured_reduced` | Lewis relations (1)–(3) calculate spinel, akermanite, and gehlenite activities from residual-melt oxide activities and corresponding pure-compound melt activities. | Authors calculate MgAl2O4 from residual-melt MgO and Al2O3 activities with Lewis relation (1), then plot it in Fig. 3A; the pure-compound reference activity values are cited but not reproduced. |
| `yakovlev_2024_fig3a_ca2mgsi2o7_figure_only` | `model_derived` vs `measured_reduced` | Same SC-456 relation above. | Authors calculate Ca2MgSi2O7 from residual-melt CaO, MgO, and SiO2 activities with Lewis relation (2), then plot it in Fig. 3A; the pure-compound reference activity values are cited but not reproduced. |
| `yakovlev_2024_fig3a_ca2al2sio7_figure_only` | `model_derived` vs `measured_reduced` | Same SC-456 relation above. | Authors calculate Ca2Al2SiO7 from residual-melt CaO, Al2O3, and SiO2 activities with Lewis relation (3), then plot it in Fig. 3A; the pure-compound reference activity values are cited but not reproduced. |

The two commits also add distinct `values.model` and `values.derived_from` fields. Both add a `values.derivation` key, so applying both verbatim would repeat a YAML mapping key. No value or schema has been chosen or changed; awaiting instruction on which method class and derivation payload to retain for each observation.

## Verification status

No validation, migration, scoring, test, reference generation, or store regeneration has been run. The required source-by-source admitted/refused counts, focused test results, and expected store delta remain pending the conflict ruling and stack integration.
