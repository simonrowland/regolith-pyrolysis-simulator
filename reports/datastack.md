# Data offer stack integration (work in progress)

Status: paused after A, B, C, and the first D commit. A controller ruling resolved the A/G Yakovlev overlap. The next D commit conflicts in a shared test file; per the code-conflict rule, the cherry-pick was aborted and integration is paused for a controller ruling. The worktree remains on `worktree/w1103datastack`.

## Reviewed-range inventory

All 56 named tips are present in the clone. The ranges after `origin/work-v064-green` contain 219 commits: 15 SC-456 prerequisite commits are already ancestors of the seat, and 204 new candidate commits remain. `git cherry` marks every non-ancestor candidate as new (`+`); none are patch-equivalent skips. A reverse file-patch check against the base worktree also failed for every candidate, so none of those deltas is already in the base tree. No candidate commits have been cherry-picked.

| Stack item | Tip(s) | New candidate commits | Skipped as present | Picked so far | Conflict / status |
|---|---|---:|---|---|---|
| A — SC-456 restack | `cb76c0423` | 100 | 15 ancestors already on base; derived HTML commit `c3360f70aec6c80c29d495a31785cab7112803e8` skipped for regeneration | 99 | picked; Yakovlev overlap ruled (see below) |
| B — batch3 | `78d5c0ec6`, `1affbf202`, `9c9dca42f`, `bd5c94202`, `c1ccfbf83`, `477b39e76`, `9d1629ede`, `c6281dfe7`, `c906ce056`, `725e54f83`, `fc3d9c476`, `c59b02e61`, `67171dd47`, `9fbe8a420`, `79a5dced5`, `18e5c4c38`, `61c5b9f64`, `8c4738532` | 44 | none | 44 | picked; no conflicts |
| C — rp1 | `611d48838`, `85f6b5ffa`, `c8f1d9511`, `94c916112`, `3774d4f3c` | 7 | none | 7 | picked; no conflicts |
| D — rp2 | `19894bf78`, `e04042747`, `e392d70d1`, `dc052a1ea`, `ad7ab429d`, `1dc46ae88` | 9 | none | 1 (`19894bf78`) | stopped on code/test conflict in `e04042747` |
| E — rp3 | `ee2709321`, `b1d6f481c`, `9dac82e1b`, `ed019f54f`, `a16303eda`, `b8cf721ac` | 11 | none | none | not reached |
| F — rp4 | `1c8a3df3a`, `515642bb7`, `75baad8b6`, `c0c6938d9`, `f448bf371`, `8ce59b132`, `fb022cc07`, `f58cc4093`, `4b2b5bf8d` | 17 | none | none | not reached |
| G — rp5 | `818f65e76`, `9552b14ef`, `d2ae74a61`, `403865e4d`, `9d657af94`, `f519568dc` | 7 | none | none | not reached; A/G Yakovlev overlap ruled |
| H — rp6 | `dc3a2cb2e`, `95e74aeec`, `8dd4cbee7`, `862491634`, `b09ab0b64` | 9 | none | none | not reached |

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

The controller ruled that the three observations retain `method_class: model_derived`; use yak24's per-observation derivation text and append its reference-activity sources (Shornikov et al. 1996, Bale et al. 2016 (FactSage), Shornikov 2017). Keep both distinct `values.model` and `values.derived_from` fields. This resolution is not yet applied because item G has not been reached.

## Current code conflict requiring a controller ruling

The D item `kf35` commit `e04042747f789b727a03b9bb81f06320dbac6b81` conflicts in `tests/test_literature_extracts.py` after `ty08` (`19894bf783997c910c4fa603e7375d572607869a`) was applied. The incoming side adds `test_kroger_fingas_1935_alkali_silicate_equilibria_method_and_temperatures`; the existing side from the SC-456 restack adds the `_SC456_SOURCE_IDS` parametrized migration-test block. Their patch hunks overlap in this shared test file. The extract data change for Kroger-Fingas was not retained after aborting the conflicted cherry-pick. No code conflict was resolved or test code merged. Per the brief, awaiting instruction before proceeding.

## Verification status

No validation, migration, scoring, test, reference generation, or store regeneration has been run. The required source-by-source admitted/refused counts, focused test results, and expected store delta remain pending the conflict ruling and stack integration. Store regeneration was not run; expected store delta is pending the full stack and migration/score evidence.
