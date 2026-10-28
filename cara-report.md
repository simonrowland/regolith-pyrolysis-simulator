# Car A pre-assembly (cara-asm-1022)

Branch: `worktree/cara-asm-1022`
Base: offer 20 r9 `d8c8943430faf7f527c4bc005e71ec0a4757d998`
Merge tip: `c9a53bbf5ce3b2d8b0b34383d144af2a29eb5ba4`
Code tip after the stub fix: `f991f3b58f6f648f7814555ae9c695944b55bb99`
This report is the next commit, the child of that code tip. The dispatch line names the branch tip, which is the commit that adds this file.

No push. No new battery-store regeneration. No `data/literature/INDEX.*`. No new ack. The seat worktree was clean after every merge; migrate ran in scratch worktrees, so `git checkout -- data/` had nothing to restore in the seat.

Current assembly, on top of the sections below: phase-aliases stays at `13f6d3211` and is pending an engine fix. b-979 is absent. The bare-log converter is `845d4d214a01bc0f182bb8687724874eaf4972f6`. The d-262 census ran in this seat; derived files were restored before that commit. This report update is the child of `845d4d214`. The dispatch line names that child.

## Manifest

`cara-manifest.tsv` has ten rows. Only `o20res3` `96656cdd2f88efe158ba38590723fb2c676cab26` is already an ancestor of the base (ledger 2026-10-21 01:46:48 ror-o20res3 LAND). It was not merged again.

Merged, in order, one `git merge --no-ff` each:

| car | sha | merge |
| --- | --- | --- |
| stranded | `6de71974d08de39c4de0908db5d42fe3bec8ad3d` | `da60cfa82` |
| t-1444c2 | `87fa02e5a710100eb237ee90dc4623572cf7a150` | `773b6a6e7` |
| idrec2d | `5a0d47220d3796a7a646f9f2fc4afe4d7d6f6aff` | `6e18ace3c` |
| idrec3b | `f700d644612f7703a4e596f14df2ee3141481b65` | `87ac04740` |
| t-1338 | `8517ed6e0706c40b26d3789e92b3fe76bf2cf633` | `c9a53bbf5` |

Contained, not merged on their own: t-1444c2 guard `d0bbc4eb6`, ack `f1ddf0822`, idrec2a `9c39fb6af`, idrec2c `479a52f96`. No car was stopped. No manifest gap. No source-extract add/add.

## Conflicts and resolutions

### stranded (`da60cfa82`)

Code conflicts keep both sides.

- `simulator/battery/score.py`: mass-loss still returns before any species read (`_SPECIES_FORMULA_INDEPENDENT_QUANTITIES`). MAGEMin still refuses vapour-equilibrium quantities (`P_SAT`, `P_PARTIAL`, `P_REFERENCE`) before `open_battery_engine`. Those sets do not overlap. Melt-activity requests stay in `_activity_request_or_contract_refusal`, which forwards `fo2_mode` into `activity_request_for_engine`. A crystalline sample with a valid request is still not a melt observable. `compile_residual` passes `fo2_mode or Fo2Mode.SHARED_LADDER`.
- `tests/battery/test_redox_extract_scoring.py`: kept the `log10_fo2_to_pa` import.

Derived-store conflicts taken from the base (`git checkout --ours`, d-255):

- `data/battery/migration-report.md`
- `data/literature/extracts-v2/kems-015-hashimoto-1983.yaml`
- `data/literature/works/10.1016_j.asr.2026.02.003.yaml`
- `data/literature/works/10.1016_j.epsl.2017.11.032.yaml`
- `data/literature/works/10.1016_j.gca.2007.09.005.yaml`
- `data/literature/works/10.1016_j.gca.2023.08.027.yaml`
- `data/literature/works/10.2343_geochemj.17.111.yaml`
- `data/literature/works/10.5194_ejm-36-173-2024.yaml`
- `data/literature/works/10.6028_nbs.ir.81-2279.yaml`
- `data/literature/works/969f2b22817a66ec32c1f2d4408de8c89201ec1e1ef5202c351f41543ae17721.yaml`

Five source extracts both sides edited auto-merged with every added line kept. Clean-merged derived files from the car's own regen stayed (d-255): 200 `extracts-v2`, 128 `works`, 966 `observations-v2`, `data/battery/migration-queue.yaml`.

### t-1444c2 (`773b6a6e7`)

One conflict, `scripts/check_store_freshness.py`: both store-neutral hashes kept. `3b4e68626` is the base refused-role ack. `f1ddf0822` is this car's transition-temperature collision ack. `admitted_transition_points` (`validate.py`) and `test_identity_equal_transition_temperature_conflict_and_green_unknown` merged clean. No derived-store conflict. Code only (3 files).

### idrec2d (`6e18ace3c`)

Clean merge. Contains idrec2a and idrec2c. `migrate.py` keeps `molar_series_per` → `PerBasis.MOL_SPECIES`. Kelley-King page 101 entropy cells come in with their harvest records (4 compilation files). No derived-store conflict.

### idrec3b (`87ac04740`)

Clean merge. The base has not edited these extracts since o20res3. Gaillard hydrous compositions stay unknown. Adamkovicova cyclowollastonite spelling comes in as reviewed (3 extract files). No derived-store conflict.

### t-1338 (`c9a53bbf5`)

Three content conflicts, all in `simulator/battery/score.py`. No add/add.

- Kept the car's species-axis grammar in `score.py`: `parse_species_formula(formula, *, species=None)`, `_species_axis_matches_phase`, and `_is_raw_element_label(..., species=)`. Dropped the shadowed import of `composition.parse_species_formula`. `compilation_tier` imports the grammar from `score.py`.
- Both call sites (`predict_with_engine`, `compile_residual`) skip the parse for `_SPECIES_FORMULA_INDEPENDENT_QUANTITIES` and otherwise call `parse_species_formula(formula, species=identity.species)`.
- `composition.parse_species_formula` stays the `formula_composition` wrapper. `score.py` imports `composition_wt_pct` from that module, so moving the grammar there would cycle. Oxide wt% keys are bare formulas. `migrate.py`'s component check was not part of this car. That copy is the one left, and why.

No derived-store conflict. Five code/test files.

### Stub fix (`f991f3b58`)

The merged helper passes `fo2_mode` into `activity_request_for_engine`. Three MAGEMin activity stubs in `tests/battery/test_score.py` were `lambda *_args`, which rejects that keyword. They now take `**_kwargs` and return the same payload. Re-run: the six typed-refusal tests passed. No production change.

## Validation

YAML flow-scalar comma guard: `tests/test_yaml_flow_scalar_commas.py`, 17 passed in 29.04s.

`python scripts/battery_migrate.py` (exit 1 is the hard-issue return). Same `MigrationResult` the CLI prints, plus a per-source TSV.

| | rows_in | observations | works | experiments | queue | hard_issues |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| base `d8c894343` | 51641 | 128946 | 371 | 3863 | 88695 | 3278 |
| tip `c9a53bbf5` | 51641 | 128946 | 371 | 3863 | 82133 | 3223 |
| delta | 0 | 0 | 0 | 0 | -6562 | -55 |

The stub fix does not change migration. rows_in delta is 0 for every source. Observation delta nets to 0. Queue delta is -6562 across 130 sources (129 extracts down, one Kelley-King record up by 1). Hard-issue delta is one source.

### Hard-issue delta by source

| source | base | tip | delta |
| --- | ---: | ---: | ---: |
| `stolyarova-1996-cao-alumina-silica-kems` | 192 | 137 | -55 |

### Observation delta by source

| source | base | tip | delta | queue delta |
| --- | ---: | ---: | ---: | ---: |
| `data/literature/compilations/kelley-king-1961-usbm-b592/records/table-006-0003.json` | 5 | 4 | -1 | 0 |
| `data/literature/compilations/kelley-king-1961-usbm-b592/records/table-006-0004.json` | 2 | 3 | +1 | +1 |

### Per-source delta (tip minus base)

rows_in is 0 on every row below. 132 sources changed. The other sources are unchanged.

| source | d_obs | d_queue | d_hard |
| --- | ---: | ---: | ---: |
| `data/literature/compilations/kelley-king-1961-usbm-b592/records/table-006-0003.json` | -1 | 0 | 0 |
| `data/literature/compilations/kelley-king-1961-usbm-b592/records/table-006-0004.json` | 1 | 1 | 0 |
| `data/literature/extracts/2010zahnle-schaefer-fegley-cshperspect-ori-a004895-2019.yaml` | 0 | -2 | 0 |
| `data/literature/extracts/adamkovicova-1980-casio3-heat-of-fusion.yaml` | 0 | -23 | 0 |
| `data/literature/extracts/arxiv-1902-05005.yaml` | 0 | -39 | 0 |
| `data/literature/extracts/bencze-yazhenskikh-2016.yaml` | 0 | -69 | 0 |
| `data/literature/extracts/bennour-1996-ternary-cs-na-silicate.yaml` | 0 | -23 | 0 |
| `data/literature/extracts/berry-2006-chromium-composition.yaml` | 0 | -7 | 0 |
| `data/literature/extracts/berry-2018-morb-iron-redox.yaml` | 0 | -11 | 0 |
| `data/literature/extracts/berry-oneill-2004-chromium-xanes.yaml` | 0 | -5 | 0 |
| `data/literature/extracts/busemann-2000-phase-q-noble-gases.yaml` | 0 | -308 | 0 |
| `data/literature/extracts/cottrell-2009-iron-xanes.yaml` | 0 | -3 | 0 |
| `data/literature/extracts/deguzman-2026-simulant-physicochemical.yaml` | 0 | -24 | 0 |
| `data/literature/extracts/di-donato-granati-2000.yaml` | 0 | -95 | 0 |
| `data/literature/extracts/fdm879.yaml` | 0 | -3 | 0 |
| `data/literature/extracts/fegley-2016-rock-steam-solubility.yaml` | 0 | -75 | 0 |
| `data/literature/extracts/gaillard-2003-hydrous-silicic-melt-activity.yaml` | 0 | -178 | 0 |
| `data/literature/extracts/gca-366-210-berry-2024.yaml` | 0 | -2 | 0 |
| `data/literature/extracts/itoh-hino-banya-1997-mg-deoxidation.yaml` | 0 | -37 | 0 |
| `data/literature/extracts/ivtan-cro2-g.yaml` | 0 | -61 | 0 |
| `data/literature/extracts/jayasuriya-2004-iron-mossbauer.yaml` | 0 | -3 | 0 |
| `data/literature/extracts/johnston-1965-sodium-disilicate-redox.yaml` | 0 | -101 | 0 |
| `data/literature/extracts/jsc-a16-special-samples.yaml` | 0 | -16 | 0 |
| `data/literature/extracts/jsc-lunar-catalog-12023.yaml` | 0 | -5 | 0 |
| `data/literature/extracts/kassis-frischat-1981-silicate-glass-vapour.yaml` | 0 | -8 | 0 |
| `data/literature/extracts/kems-001-homma-1966.yaml` | 0 | -63 | 0 |
| `data/literature/extracts/kems-002-ohno-1967.yaml` | 0 | -30 | 0 |
| `data/literature/extracts/kems-003-pound-1972.yaml` | 0 | -6 | 0 |
| `data/literature/extracts/kems-005-fedkin-2006.yaml` | 0 | -18 | 0 |
| `data/literature/extracts/kems-006-zhang-2021.yaml` | 0 | -11 | 0 |
| `data/literature/extracts/kems-007-costa-2015.yaml` | 0 | -4 | 0 |
| `data/literature/extracts/kems-008-schaefer-fegley-2004.yaml` | 0 | -5 | 0 |
| `data/literature/extracts/kems-009-safarian-2013.yaml` | 0 | -2 | 0 |
| `data/literature/extracts/kems-010-richter-2007.yaml` | 0 | -96 | 0 |
| `data/literature/extracts/kems-011-wetzel-gail-2013.yaml` | 0 | -3 | 0 |
| `data/literature/extracts/kems-012-sossi-2019.yaml` | 0 | -53 | 0 |
| `data/literature/extracts/kems-014-drowart-2005.yaml` | 0 | -18 | 0 |
| `data/literature/extracts/kems-015-hashimoto-1983.yaml` | 0 | -13 | 0 |
| `data/literature/extracts/kems-016-stolyarova-1992.yaml` | 0 | -25 | 0 |
| `data/literature/extracts/kems-017-stolyarova-2013.yaml` | 0 | -1 | 0 |
| `data/literature/extracts/kems-018-stolyarova-2012.yaml` | 0 | -1 | 0 |
| `data/literature/extracts/kems-020-hastie-1981-nbsir.yaml` | 0 | -53 | 0 |
| `data/literature/extracts/kems-021-plante-1992-feo.yaml` | 0 | -8 | 0 |
| `data/literature/extracts/kems-022-demaria-1971.yaml` | 0 | -50 | 0 |
| `data/literature/extracts/kems-027-plante-hastie-1983.yaml` | 0 | -1 | 0 |
| `data/literature/extracts/kems-031-halwax-2024.yaml` | 0 | -9 | 0 |
| `data/literature/extracts/kems-032-copland-jacobson-2010.yaml` | 0 | -15 | 0 |
| `data/literature/extracts/kems-035-sauerborn-2005.yaml` | 0 | -21 | 0 |
| `data/literature/extracts/kems-036-sesko-2024.yaml` | 0 | -8 | 0 |
| `data/literature/extracts/kems-037-richter-2002.yaml` | 0 | -4 | 0 |
| `data/literature/extracts/kems-038-matchett-2006.yaml` | 0 | -8 | 0 |
| `data/literature/extracts/kems-040-stolyarova-2015.yaml` | 0 | -1 | 0 |
| `data/literature/extracts/kems-041-sossi-fegley-2018.yaml` | 0 | -31 | 0 |
| `data/literature/extracts/kems-045-sossi-2018-pnas-cr.yaml` | 0 | -32 | 0 |
| `data/literature/extracts/kems-046-van-limpt-2007.yaml` | 0 | -17 | 0 |
| `data/literature/extracts/kems-047-turkdogan-1984-isij.yaml` | 0 | -14 | 0 |
| `data/literature/extracts/kems-051-allibert-1981.yaml` | 0 | -28 | 0 |
| `data/literature/extracts/kems-053-stolyarova-1991.yaml` | 0 | -5 | 0 |
| `data/literature/extracts/kems-057-kambayashi-1985.yaml` | 0 | -85 | 0 |
| `data/literature/extracts/kems-058-ohara-1987.yaml` | 0 | -40 | 0 |
| `data/literature/extracts/kems-066-ichise-1977.yaml` | 0 | -5 | 0 |
| `data/literature/extracts/kems-067-yamada-1980.yaml` | 0 | -33 | 0 |
| `data/literature/extracts/kems-069-furukawa-1976.yaml` | 0 | -38 | 0 |
| `data/literature/extracts/kems-087-yamada-kato-1980.yaml` | 0 | -4 | 0 |
| `data/literature/extracts/kems-088-ichise-1975.yaml` | 0 | -11 | 0 |
| `data/literature/extracts/kems-093-piacente-1975.yaml` | 0 | -10 | 0 |
| `data/literature/extracts/kems-095-ueda-1986.yaml` | 0 | -9 | 0 |
| `data/literature/extracts/kems-097-ikeda-1978.yaml` | 0 | -9 | 0 |
| `data/literature/extracts/kems-103-fraser-1983.yaml` | 0 | -141 | 0 |
| `data/literature/extracts/kems-105-yamada-1983.yaml` | 0 | -25 | 0 |
| `data/literature/extracts/kems-111-ichise-1982.yaml` | 0 | -29 | 0 |
| `data/literature/extracts/kems-112-ichise-1989.yaml` | 0 | -42 | 0 |
| `data/literature/extracts/kems-116-nunoue-1987.yaml` | 0 | -18 | 0 |
| `data/literature/extracts/kems-118-yamamoto-1983.yaml` | 0 | -43 | 0 |
| `data/literature/extracts/kems-119-furukawa-1975.yaml` | 0 | -20 | 0 |
| `data/literature/extracts/kems-120-ueshima-1984.yaml` | 0 | -25 | 0 |
| `data/literature/extracts/kems-127-qi-1989.yaml` | 0 | -17 | 0 |
| `data/literature/extracts/kems-132-nakajima-2016.yaml` | 0 | -15 | 0 |
| `data/literature/extracts/kems-137-bischof-2023.yaml` | 0 | -6 | 0 |
| `data/literature/extracts/kems-139-jacobson-2024.yaml` | 0 | -29 | 0 |
| `data/literature/extracts/kems-140-heck-2025.yaml` | 0 | -111 | 0 |
| `data/literature/extracts/kems-184-behrens-1979.yaml` | 0 | -6 | 0 |
| `data/literature/extracts/kems-188-nanjo-1976.yaml` | 0 | -8 | 0 |
| `data/literature/extracts/kems-190-wu-1993.yaml` | 0 | -61 | 0 |
| `data/literature/extracts/kems-198-ichise-ueshima-1989.yaml` | 0 | -10 | 0 |
| `data/literature/extracts/kems-200-ueshima-1983.yaml` | 0 | -24 | 0 |
| `data/literature/extracts/kems-201-ichise-1986.yaml` | 0 | -17 | 0 |
| `data/literature/extracts/kems-ms2000-044.yaml` | 0 | -94 | 0 |
| `data/literature/extracts/kress-carmichael-1988-iron-oxidation-stoichiometry.yaml` | 0 | -126 | 0 |
| `data/literature/extracts/lamoreaux-hildenbrand-1984.yaml` | 0 | -8 | 0 |
| `data/literature/extracts/liu-2023-chromium-oxide-activity.yaml` | 0 | -36 | 0 |
| `data/literature/extracts/llnl-2021-cai-aerodynamic-levitation.yaml` | 0 | -4 | 0 |
| `data/literature/extracts/lpi-compendium-62231.yaml` | 0 | -1 | 0 |
| `data/literature/extracts/lpi-compendium-65701.yaml` | 0 | -47 | 0 |
| `data/literature/extracts/mendybaev-2021-cai-low-pressure-h2-evap.yaml` | 0 | -17 | 0 |
| `data/literature/extracts/mogul-2023-pioneer-venus-co2-icarus.yaml` | 0 | -5 | 0 |
| `data/literature/extracts/morita-1999-chromium-oxide-activity.yaml` | 0 | -105 | 0 |
| `data/literature/extracts/murchison-degassing-2023-springer.yaml` | 0 | -8 | 0 |
| `data/literature/extracts/murchison-hydropyrolysis-1990s-gca.yaml` | 0 | -38 | 0 |
| `data/literature/extracts/nagabayashi-hino-banya-1989-phosphorus.yaml` | 0 | -5 | 0 |
| `data/literature/extracts/nakamura-2022-science-ryugu-thermal.yaml` | 0 | -16 | 0 |
| `data/literature/extracts/nakano-hashimoto-2020-bubbles-to-chondrites-i.yaml` | 0 | -8 | 0 |
| `data/literature/extracts/nasa-tm-2024-simulant-guide.yaml` | 0 | -111 | 0 |
| `data/literature/extracts/no27-fegley-cameron-1987-epsl.yaml` | 0 | -93 | 0 |
| `data/literature/extracts/ntrs-19730008085.yaml` | 0 | -30 | 0 |
| `data/literature/extracts/ntrs-20250004626.yaml` | 0 | -15 | 0 |
| `data/literature/extracts/ohta-1996-cao-mgo-al2o3-activities.yaml` | 0 | -10 | 0 |
| `data/literature/extracts/robinot-2025-promes-review.yaml` | 0 | -11 | 0 |
| `data/literature/extracts/rusiecka-wood-2025-chlorine-nacl-hydrous-basaltic-melts.yaml` | 0 | -3 | 0 |
| `data/literature/extracts/sato-1976-lunar-fo2-apollo-17.yaml` | 0 | -6 | 0 |
| `data/literature/extracts/schaefer-fegley-2011-vaporization-earth.yaml` | 0 | -321 | 0 |
| `data/literature/extracts/shilov-1998-sno-zno-p2o5-vaporization.yaml` | 0 | -53 | 0 |
| `data/literature/extracts/slag-001-banya-1993.yaml` | 0 | -27 | 0 |
| `data/literature/extracts/smales-1971-lpsc-12022.yaml` | 0 | -2 | 0 |
| `data/literature/extracts/sossi-2020-cu-zn-isotope-evap-formalism.yaml` | 0 | -78 | 0 |
| `data/literature/extracts/steurer-1985-vapor-phase-pyrolysis.yaml` | 0 | -18 | 0 |
| `data/literature/extracts/steurer-1992-vapor-phase-pyrolysis.yaml` | 0 | -10 | 0 |
| `data/literature/extracts/street-2010-thermal-properties-simulants.yaml` | 0 | -7 | 0 |
| `data/literature/extracts/sublimation-kinetics-2023-minerals.yaml` | 0 | -3 | 0 |
| `data/literature/extracts/ta-shirai-2000-lpsc.yaml` | 0 | -35 | 0 |
| `data/literature/extracts/thomas-2022-chlorine-bonding-silicate-melts.yaml` | 0 | -86 | 0 |
| `data/literature/extracts/thomas-wood-2021-chlorine-silicate-melts.yaml` | 0 | -111 | 0 |
| `data/literature/extracts/tomioka-1991-nitride-capacity-cao-al2o3.yaml` | 0 | -30 | 0 |
| `data/literature/extracts/ts1985.yaml` | 0 | -4 | 0 |
| `data/literature/extracts/usgs-lunar-sourcebook-tab8-2.yaml` | 0 | -2185 | 0 |
| `data/literature/extracts/usgs-lunar-sourcebook-tab8-4.yaml` | 0 | -90 | 0 |
| `data/literature/extracts/van-kooten-2020-lunar-outgassing.yaml` | 0 | -14 | 0 |
| `data/literature/extracts/wadhwa-2008-redox-small-bodies.yaml` | 0 | -2 | 0 |
| `data/literature/extracts/wimpenny-2019-zn-isotope-evaporation-extreme-t.yaml` | 0 | -19 | 0 |
| `data/literature/extracts/yam1983.yaml` | 0 | -15 | 0 |
| `data/literature/extracts/zigo-1987-gehlenite-fusion.yaml` | 0 | -13 | 0 |
| `stolyarova-1996-cao-alumina-silica-kems` | 0 | 0 | -55 |

## Pytest

Command: `.venv` python, `PYTHONPATH` the tree under test, `pytest -n 0 --timeout=300 -q tests/battery tests/test_literature_extracts*.py tests/test_absolute_path_ratchet.py`.

Base `d8c894343`: 9 failed, 6408 passed, 4 skipped, 7331.44s.
Tip `c9a53bbf5`: 25 failed, 6582 passed, 4 skipped, 7310.94s.

The nine base failures are not new, except one that the tip no longer fails: `test_internal_analytical_observation_uses_core_vapor_pressure_intent`. The other eight still fail at the tip (LiquidMelts `NameError` on three vaporock census tests, three migrate-census timeouts over 300s, the OpenIMCC Sossi/Aithala hash pin, and the Sossi worker-start transport test). OpenIMCC was the venv install. This lane did not prepend the e975edd pin.

### NEW failures at `c9a53bbf5`

Seventeen node ids. Six were the `fo2_mode` TypeError and pass after `f991f3b58` (isolated re-run, 52s, with the tests below).

Still red, recorded and not fixed:

- `test_r16_store_regen.py::test_committed_store_has_no_stale_migrate_input_commits` and `test_r17_glass_store_regen.py::test_committed_store_has_no_stale_migrate_input_commits`. Expected under d-255. r17 names unacknowledged inputs `746fdc345`, `5a0d47220`, `c7df095bf`, `08a0ac23e`, `f700d6446`, `9f466810a`, `9c39fb6af`, `8517ed6e0`, `c91ba407c`, `479a52f96`.
- `test_migrate_r7.py::test_h5_whole_store_numeric_cell_claims_match_sources[compilations-kelley-king-1961-usbm-b592.yaml]`. The committed observations claim `entropy_other_sources` count 1 for `table-006-0003`; the harvest record now has 0. Same cell as the observation delta above. Gate regen, not a pin edit.
- `test_fo2_effusion_pin.py::test_costa_and_stolyarova_effusion_numbers_match_6f90106c6`. `internal-analytical` equilibrate hits the 5s hard timeout. Reproduced in isolation after the full suite.
- `test_fo2_ladder.py::test_steurer_effusion_row_has_a_numeric_internal_residual`. Same 5s timeout in the full suite. Passed alone in the same re-run as the stub tests (7 passed, and this was one of them). Load during the paired suite, not a remaining product failure.
- `test_score.py::test_score_store_attempts_all_owned_handle_closes`. `assert 1 == 2`. The Na `P_SAT` row is vapour equilibrium, so MAGEMin returns `engine_missing_observable` before `open_battery_engine`. Only VAPOROCK is owned. Both the base and stranded copies of this test still expect two opens. The refusal is the stranded rule that was kept. The count was not edited.
- `test_migrate_r7.py::test_g2_plante_source_points_and_comparison_fence`. A defaulted Plante row now has `fO2_Pa` `0.005747761950`. Both sides' test says a defaulted row's fO2 is absent or not a value.
- `test_t1243_feo_reference.py::test_every_iron_saturated_feo_row_is_scored_or_named`. A Holzheid Table 2 row at 1677 K now has a numeric dex residual. Both sides' test expects `numeric is None` and `IDENTITY_UNKNOWN`.
- `test_score.py::test_real_stolyarova_cell_passes_mo_gate_and_scores_with_notice`. The Mo notice and the unparseable-uncertainty refusal are present. `flagged_stratum_payloads` is empty, so `strata[0]` raises IndexError. Both sides expect stratum `cell-material-inferred`.
- `test_sweep_gas.py::test_existing_single_species_migrates_byte_identically`. Payload length 4476. The base pin is 4444. Every car's own pin is 12809. The combined serializer is the base shape plus 32 bytes, not the cars' payload. The structured N2 / nominal / analytical checks passed. The length pin was not moved.
- `test_migrate_benches.py::test_legacy_equipment_extract_output_has_no_empty_bench_key`. Digest `3b9705c38dc3…` against the pin `69d1ac13c09d…`, which is the same pin on the base and on stranded. The behaviour asserts above the digest passed.

These last five are integration results the two sides do not settle by themselves. They are recorded, not guessed.

### Mutation

This lane did not run a mutation campaign. The cars were already reviewed LAND.

## Store delta the train should expect

Regenerating at the gate, relative to a regeneration of offer 20 r9:

- rows_in 51641, observations 128946, works 371, experiments 3863, all unchanged
- queue 88695 → 82133 (-6562)
- hard_issues 3278 → 3223 (-55), all of it `stolyarova-1996-cao-alumina-silica-kems` 192 → 137
- observations move only inside Kelley-King table 6: record 0003 loses one, record 0004 gains one

The committed tree already carries stranded's clean-merged regen and the base side of every derived-store conflict. This lane did not commit another regeneration. The fix round below does not change migration, so this prediction still stands.

## Fix round

Controller review of `355fce660` required the four combination reds. One commit per code change. The Kelley-King claim has no source-file change.

### 1. Kelley-King table-006-0003 entropy_other_sources

The harvest record and the manifest correction agree. `table-006-0003` (Al(g)) has an empty printed token for `entropy_other_sources`. The OCR token `-76±10` was realigned onto `table-006-0004` (Al+++(aq)). The migrator census for 0003 is `entropy_other_sources: 0 numeric cells`. The committed observations-v2 reason still says 1. That sentence is the only difference from the regenerated census.

Splicing the regenerated 0 into a copy of `compilations-kelley-king-1961-usbm-b592.yaml` and running `_count_claim_audit` gave judged 16231, which is `_EXPECTED_COUNT_CLAIMS` for that file, and `bad` empty. The copy was deleted. The claim lives only in the derived store. It was not committed. The seat test stays red until the gate regen. The test predicate was not changed.

### 2. Holzheid Table 2 at 1677 K — `c3f91611b`

The extract files are unchanged from offer 20 r9. Row AD 10 prints `log_fO2: -10.31`. The PDF column header is `log fO2` with no unit. The extract's `log_fO2_scale_and_control` says the scale was not published. Stranded's bare-log converter maps an empty unit to bar: `fO2_Pa = 1e5 * 10^-10.31 = 0.000004897788193684461959103085614` Pa, which is the migrated value. The stub copies that identity, so the old `fO2_Pa`-only `IDENTITY_UNKNOWN` does not fire.

Of the 96 FeO rows, 57 now have a dex residual and `DECISION_RULE_MISSING` / `unparseable_printed_uncertainty` (Table 2 prints no per-row gamma uncertainty). 15 still refuse on `composition`. 24 still refuse on `species.phase`. `tests/battery/test_t1243_feo_reference.py` passed (the file, 4 tests, inside the 52-test run below).

### 3. Stolyarova cell-material stratum — `861da8c49`

`_key_for_fo2_mode` appends `::fo2_mode=shared_ladder` when a notice carries that mode. `flagged_stratum_payloads` treated the last `::` field as the engine, so the row was filtered out and `strata[0]` raised IndexError. The cell-material notice itself was still on the residual. The suffix is stripped before the four-field split and before every production read of an engine from a residual key. `test_real_stolyarova_cell_passes_mo_gate_and_scores_with_notice` passed.

`tests/battery/test_score.py` and `tests/battery/test_compilation_tier.py` together: 4 failed, 429 passed, 603.30s. The four failures are the pre-existing handle-close `1 == 2` and the three LiquidMelts `NameError`s. They were not edited.

### 4. Sweep-gas payload — `85e25758b`

Base payload 4444 bytes, sha256 `bffc8e9c562bb69d558d776abf9204b2c22d2c81ca5d9064afc3f7c5a675da4c`. Tip payload 4476 bytes, sha256 `f7d3bb2458192701fc7479d3a8a216cfd60e930a602b21aace50cca7fa3b2401`. The only additions are `"references":[]` on `pressure_environment.total_pressure_Pa.inference` (`atm_to_Pa`) and on `sample.initial_composition.inference` (wt% to mole fraction). Empty `parameters` lists were already serialized, so an empty `references` list is the same rule. No printed value changed. The pin moved to 4476 and that hash. `tests/battery/test_sweep_gas.py` passed.

The targeted run of the t1243 file, the sweep-gas file, the Stolyarova cell test, and the two flagged-stratum classifier tests was 52 passed in 23.79s.

### Effusion timeouts, re-run and not edited

Serial foreground re-run, each test in its own process:

- `test_steurer_effusion_row_has_a_numeric_internal_residual`: failed in 37.42s. `equilibrate exceeded hard timeout of 5s`.
- `test_costa_and_stolyarova_effusion_numbers_match_6f90106c6`: failed in 42.91s. Same 5s timeout.

An earlier isolated re-run had passed Steurer. This one did not. Neither test was edited.

### Still red, outside this fix round

r16/r17 freshness, the handle-close count, the Plante defaulted-row fO2 fence, the legacy bench digest, LiquidMelts, the OpenIMCC hash pin, the Sossi worker-start failure, and the migrate-census timeouts. The store prediction above is unchanged.

## Train merges on the fix tip

Controller instruction `final-merge.md`, on top of `68d3876b8`. Two of the three requested merges are in. The third is stopped.

### famcar-r3 `8910347f64` — merge `3986fa63e`

Clean merge onto `68d3876b8`. Merge-base is offer 20 r9 `d8c894343`. Seven paths changed on both sides. The auto-merge keeps both hunks.

- `SCHEMA.md`, `tools/validate_literature_extracts.py`, `tests/test_literature_extracts.py`: `chloride_capacity` plus the Muan 1 atm inference test. Existing type checks stay.
- `docs/running-from-shell.md`: pinned-engine install section. Existing shell notes stay.
- `scripts/check_store_freshness.py`: ack-directory receipts beside `STORE_NEUTRAL_INPUT_COMMITS`. The frozenset is unchanged.
- `simulator/battery/migrate.py`: `kbar_to_Pa` (1 kbar = 100 MPa), V2O5 and V2O3 components, and row-level `total_pressure_Pa` for phase composition ahead of the printed experiment pressure. The experiment fallback remains.
- `tests/battery/test_migrate.py`: kbar pin and the printed Fe3/Fe2 census order. Existing migrate tests stay.

`INDEX.yaml` and `INDEX.md` come from famcar-r3, the only side that changed them. No other derived-store file conflicted.

### phase-aliases `28dd1e2fc` — merge `13f6d3211`

Three content conflicts. Each keeps both sides. No derived-store path changed against the famcar tip, so the laptop INDEX stays.

- `simulator/battery/identity.py`: `_identity_equal` keeps `ignore_fO2` and gains `knudsen_residue_extent`.
- `simulator/battery/score.py`: `_predict_fe_redox` is the stack's rewrite (Kress91 helpers, OpenIMCC `evaluate_redox`, MELTS ferric split). The identity stamp at all three `fO2_Pa` sites, including the new MELTS site, is `log10_fo2_to_pa`. `compile_residual` tries the Knudsen extent comparison first and the intrinsic-mode `ignore_fO2` comparison next. The score loop still requests a Knudsen residue prediction and still records `intrinsic_redox_activity`.
- `tests/battery/test_redox_extract_scoring.py`: MELTS ratio helpers and `log10_fo2_to_pa` both stay. Both are called.

Auto-merged without a dropped hunk: Antoine phase boundaries, freshness receipts, Ellingham and VapoRock gas basis, sample-source phase labels on partial pressure, canonical element allotropes, battery-cell payload fields, and the gas-suffix and Stolyarova phase tests. The fo2_mode key parser and the melt-activity `**_kwargs` stubs stay.

### b-979 `9fb179254` — stopped, not committed

Superseded as the current state by Pending engine fixes below. This section is the first abort.

`git merge --no-ff` conflicted in `data/literature/extracts/SCHEMA.md`, `simulator/battery/records.py`, and `simulator/battery/validate.py`. The merge was aborted. The tip at that moment was `13f6d3211`.

The two sides contradict on `model_derived` plus `raw_inputs: not_printed`.

- Car A has `d5455b9e1` (2026-10-19, "Allow located raw inputs for model-derived evidence"). A model-derived row may declare `not_printed` when the method has a nonempty value, an equation, a physical page, and at least one named reference. That declaration supplies lineage, so `derived_from` is not required, and it contradicts a present `derived_from`. Tests: `test_model_derived_unprinted_inputs_with_located_method_passes`, `test_production_migration_accepts_model_derived_raw_input_method`.
- b-979 has `e830456b8` and `3e59430b3` (2026-10-21). `not_printed` is invalid on model-derived evidence. Model-derived rows use `printed_unresolved`, which is located and flagged and does not supply lineage. Tests: `test_model_derived_rejects_raw_inputs_not_printed`, `test_production_migration_rejects_model_derived_raw_input_declaration`.

b-979's branch does not contain `d5455b9e1`, so the later date is not an override of the located-method rule. `printed_unresolved` is its own status and is not on car A. The schema sentences disagree about whether model-derived `not_printed` is valid. Steer seq 10 then removed b-979 from the branch, so that choice is not in this tree. The census below is the tree without b-979.

## Pending engine fixes

Steer seq 10. The train bounced both of engine's car-A items. Engine is fixing both. The fixed versions will be merged on top later. This lane did not merge either fix.

### phase-aliases `28dd1e2fc` — kept, pending engine fixes

Merge `13f6d3211` stays on the branch. It is an ancestor of `845d4d214`. Pending: Antoine A=None refusal, and Stolyarova 23 versus 14.

### b-979 `9fb179254` — absent, pending engine fixes

`9fb179254` is not an ancestor of `845d4d214`. The first merge was aborted; that conflict is the section above. A later ruling finished a merge at `51283fa6b4ccea73e18b645102add7b94916e19e` (parents `4a0a5fa88` and `9fb179254`). Steer seq 10 then bounced b-979 (Shornikov 2008 x5). That merge was unpushed and only on this branch, so it was removed with `git reset --hard 4a0a5fa8852f569f85a4b7417305dfb5cd665ad6`. The raw-input resolution that lived only in `51283fa6b` is absent. Pending: Shornikov 2008 x5.

## d-262 bare-log converter — `845d4d214`

Own commit on `4a0a5fa88`. No extracted value changed. The commit contains no derived store and no ack.

A unitless printed log fO2 (empty unit string) is accepted as log10(fO2 / 1 bar) only when it is plausible as an absolute log fO2 at the row's run temperature. The notice text is exactly `fO2 reference state not printed; 1 bar convention assumed (1 atm alternative: +0.0057 dex)`. Unknown run temperature, or a value outside the band, stays identity-unknown with reason `fO2 reference scale not printed (absolute vs buffer-relative)`.

The centre is `feo_iw_log10_fO2_bar` in `simulator/fe_redox` (`a_feo=1.0`), the production FeO-IW buffer named by `_battery_hero_delta_iw`. The closed half-width 6 is the ruling's plausibility window. This function adds no buffer coefficient. Explicit `log10(bar)`, `log10(atm)`, `log10`, `log`, and `dex` keep their existing frames. The arithmetic leaf `log10_fo2_to_pa` is unchanged. Notice kind is the existing `NoticeKind.INPUT_OMITTED`.

```python
_BARE_LOG_BAR_NOTICE = (
    "fO2 reference state not printed; 1 bar convention assumed "
    "(1 atm alternative: +0.0057 dex)"
)
_BARE_LOG_SCALE_UNKNOWN = (
    "fO2 reference scale not printed (absolute vs buffer-relative)"
)


def _bare_log_convention(
    log10_fO2: Decimal, temperature_K: Decimal | None
) -> tuple[State, str | None]:
    """Unitless printed log fO2, accepted only as a plausible absolute.

    Premise: fugacity carries a standard state. 1 atm versus 1 bar is
    log10(1.01325) = 0.0057 dex, so a unitless number is not yet a pressure.
    The 1 bar convention is applied only when the value is plausible as an
    absolute log fO2 at the row's run temperature: inside [IW-6, IW+6].
    The centre is ``feo_iw_log10_fO2_bar``, the production FeO-IW buffer.
    This function adds no buffer coefficients. Unknown temperature, or a
    value outside that band, stays identity-unknown. The returned sentence
    is the notice when the convention is applied, and None when it is not.
    """

    if (
        temperature_K is None
        or not temperature_K.is_finite()
        or temperature_K <= 0
    ):
        return State.unknown(_BARE_LOG_SCALE_UNKNOWN), None
    from simulator.fe_redox import Kress91InvalidControls, feo_iw_log10_fO2_bar

    try:
        centre = feo_iw_log10_fO2_bar(float(temperature_K), a_feo=1.0)
        iw = Decimal(str(centre))
    except (Kress91InvalidControls, OverflowError, ValueError):
        return State.unknown(_BARE_LOG_SCALE_UNKNOWN), None
    if not iw.is_finite():
        return State.unknown(_BARE_LOG_SCALE_UNKNOWN), None
    # Closed plausibility window around the buffer owner. Not a buffer fit.
    half_width = Decimal(6)
    if log10_fO2 < iw - half_width or log10_fO2 > iw + half_width:
        return State.unknown(_BARE_LOG_SCALE_UNKNOWN), None
    return (
        State.of(_log_fO2_to_pa(log10_fO2, _BAR_PA)),
        _BARE_LOG_BAR_NOTICE,
    )
```

### Tests

Foreground, `-n 0 --timeout=300`, `tests/battery/test_printed_oxygen_identity.py` and `tests/battery/test_t1243_feo_reference.py`. 21 passed in 10.20s. Exit 0.

- `test_unitless_holzheid_ad10_log_is_one_bar_with_notice`: bare `log_fO2: -10.31` at 1677 K lands as `_log_fO2_to_pa(Decimal("-10.31"))` with the notice. `identity_equal` of the row to itself does not name `fO2_Pa`.
- `test_unitless_log_outside_iw_band_stays_unknown`: bare `-1.0` at 1573 K is identity-unknown with the scale reason, and `fO2_Pa` is in the identity-equal fields.
- `test_unitless_log_without_temperature_stays_unknown`: bare `-10.31` with no `T_K` is identity-unknown with the same reason.
- `test_every_iron_saturated_feo_row_is_scored_or_named`: `scored == 57`, composition 15, `species.phase` 24, extrapolated 32. Two FeO rows print `-10.31` at 1677 K. Both carry the notice. Table 2 AD 10 scores (`ad10_scored == 1`). Table 3(b) AD 10 stays identity-unknown on composition.

NEW failures in that run: none. The wider battery suite was not re-run for this commit. The known reds recorded above (r16/r17 freshness, handle-close `1 == 2`, Plante defaulted-row fO2, legacy bench digest, LiquidMelts, OpenIMCC hash, Sossi worker, migrate-census timeouts, effusion 5s timeouts) were left as they were.

## Migrator census at the d-262 tree versus `d8c894343`

`python scripts/battery_migrate.py` on the working tree that `845d4d214` records (car A, famcar `3986fa63e`, aliases `13f6d3211`, the converter; b-979 absent). Exit 1, which is the hard-issue return. Started 2026-10-23T07:40:29Z, finished 2026-10-23T08:07:46Z.

| | rows_in | observations | works | experiments | queue | hard_issues |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| base `d8c894343` | 51641 | 128946 | 371 | 3863 | 88695 | 3278 |
| tip | 72789 | 151944 | 440 | 5606 | 103532 | 3223 |
| delta | +21148 | +22998 | +69 | +1743 | +14837 | -55 |

This supersedes the store prediction recorded at tip `c9a53bbf5`. That census was before famcar and the aliases merge. Derived files written by this run were restored (`git checkout -- data/` and removal of the untracked migrator outputs). `845d4d214` contains none of them. The train regenerates at its gate. Expected store delta is the table above.

Holzheid is absent from the per-source delta. The converter did not change that source's row, observation, queue, or hard-issue counts.

### Hard-issue delta by source

| source | base | tip | delta |
| --- | ---: | ---: | ---: |
| `stolyarova-1996-cao-alumina-silica-kems` | 192 | 137 | -55 |

1 source has a hard-issue delta. 56 sources have a rows_in delta. 58 sources have an observation delta. 184 sources have a queue delta. 201 sources changed on at least one of those four counts. Kelley-King table 6 is unchanged from the earlier census: record 0003 loses one observation, record 0004 gains one.

### Per-source delta (d-262 tree minus `d8c894343`)

| source | d_rows | d_obs | d_queue | d_hard | base_obs | tip_obs | base_hard | tip_hard |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `data/literature/compilations/kelley-king-1961-usbm-b592/records/table-006-0003.json` | 0 | -1 | 0 | 0 | 5 | 4 | 0 | 0 |
| `data/literature/compilations/kelley-king-1961-usbm-b592/records/table-006-0004.json` | 0 | 1 | 1 | 0 | 2 | 3 | 0 | 0 |
| `data/literature/extracts/2010zahnle-schaefer-fegley-cshperspect-ori-a004895-2019.yaml` | 0 | 0 | -2 | 0 | 9 | 9 | 0 | 0 |
| `data/literature/extracts/abdul-2023-clinker-thesis.yaml` | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `data/literature/extracts/abdullah-2013-redox-acid-base-soda-lime-silicate-thesis.yaml` | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `data/literature/extracts/adamkovicova-1980-casio3-heat-of-fusion.yaml` | 0 | 0 | -23 | 0 | 47 | 47 | 0 | 0 |
| `data/literature/extracts/albertsson-2013-cr-slag-thesis.yaml` | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `data/literature/extracts/arxiv-1902-05005.yaml` | 0 | 0 | -39 | 0 | 41 | 41 | 0 | 0 |
| `data/literature/extracts/babaian-2024-fe-mg-si-air-equilibria.yaml` | 165 | 165 | 165 | 0 | 0 | 165 | 0 | 0 |
| `data/literature/extracts/babaian-2026-fe-mg-si-reassessment.yaml` | 213 | 213 | 213 | 0 | 0 | 213 | 0 | 0 |
| `data/literature/extracts/barcza-1979-mn-slag-metal-equilibrium.yaml` | 12 | 12 | 4 | 0 | 0 | 12 | 0 | 0 |
| `data/literature/extracts/bencze-yazhenskikh-2016.yaml` | 0 | 0 | -69 | 0 | 70 | 70 | 0 | 0 |
| `data/literature/extracts/bennour-1996-ternary-cs-na-silicate.yaml` | 0 | 0 | -23 | 0 | 27 | 27 | 0 | 0 |
| `data/literature/extracts/berry-2006-chromium-composition.yaml` | 0 | 0 | -7 | 0 | 96 | 96 | 0 | 0 |
| `data/literature/extracts/berry-2018-morb-iron-redox.yaml` | 0 | 0 | -11 | 0 | 58 | 58 | 0 | 0 |
| `data/literature/extracts/berry-oneill-2004-chromium-xanes.yaml` | 0 | 0 | -5 | 0 | 145 | 145 | 0 | 0 |
| `data/literature/extracts/bonnand-bruand-matzen-et-al-2020-redox-chromium-isotope-magnesiochromite.yaml` | 98 | 98 | 14 | 0 | 0 | 98 | 0 | 0 |
| `data/literature/extracts/borisov-borisovskiy-2024-ti-zircon.yaml` | 198 | 198 | 198 | 0 | 0 | 198 | 0 | 0 |
| `data/literature/extracts/borisov-veksler-2021-k-fe-supplement.yaml` | 10092 | 10092 | 11447 | 0 | 0 | 10092 | 0 | 0 |
| `data/literature/extracts/brillo-2025-al-ti-thermophysical.yaml` | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `data/literature/extracts/busemann-2000-phase-q-noble-gases.yaml` | 0 | 0 | -308 | 0 | 308 | 308 | 0 | 0 |
| `data/literature/extracts/cacciatore-zajacz-2026-v-sc-oxybarometer-supplement.yaml` | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `data/literature/extracts/cacciatore-zajacz-2026-v-sc-oxybarometer.yaml` | 682 | 682 | 682 | 0 | 0 | 682 | 0 | 0 |
| `data/literature/extracts/cottrell-2009-iron-xanes.yaml` | 0 | 0 | -3 | 0 | 41 | 41 | 0 | 0 |
| `data/literature/extracts/dachs-geiger-2006-pyrope-grossular.yaml` | 23 | 23 | 29 | 0 | 0 | 23 | 0 | 0 |
| `data/literature/extracts/day-2020-trinitite-volatility.yaml` | 6 | 33 | 6 | 0 | 0 | 33 | 0 | 0 |
| `data/literature/extracts/decapitani-kirschen-1998-excess-function.yaml` | 90 | 90 | 0 | 0 | 0 | 90 | 0 | 0 |
| `data/literature/extracts/deguzman-2026-simulant-physicochemical.yaml` | 0 | 0 | -24 | 0 | 30 | 30 | 0 | 0 |
| `data/literature/extracts/di-donato-granati-2000.yaml` | 0 | 0 | -95 | 0 | 95 | 95 | 0 | 0 |
| `data/literature/extracts/dohmen-2003-coupled-kinetics.yaml` | 2 | 2 | 0 | 0 | 0 | 2 | 0 | 0 |
| `data/literature/extracts/farah-1998-vanadium-silicate-melts-thesis.yaml` | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `data/literature/extracts/farmer-oneill-2023-mgo-zno-miscibility.yaml` | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `data/literature/extracts/fdm879.yaml` | 0 | 0 | -3 | 0 | 8 | 8 | 0 | 0 |
| `data/literature/extracts/fegley-2016-rock-steam-solubility.yaml` | 0 | 0 | -75 | 0 | 75 | 75 | 0 | 0 |
| `data/literature/extracts/feng-2025-fe-si-ca-v-ti-phase-diagrams.yaml` | 204 | 204 | 0 | 0 | 0 | 204 | 0 | 0 |
| `data/literature/extracts/gaillard-2003-hydrous-silicic-melt-activity.yaml` | 0 | 0 | -178 | 0 | 260 | 260 | 0 | 0 |
| `data/literature/extracts/gca-366-210-berry-2024.yaml` | 0 | 0 | -2 | 0 | 50 | 50 | 0 | 0 |
| `data/literature/extracts/georges-2000-potassium-evaporation-thesis.yaml` | 630 | 990 | 630 | 0 | 0 | 990 | 0 | 0 |
| `data/literature/extracts/gorojovsky-wood-2026-sulfur-crustal.yaml` | 983 | 983 | 993 | 0 | 0 | 983 | 0 | 0 |
| `data/literature/extracts/haskin-1990-lunar-molten-regolith-electrolysis-nasa-report.yaml` | 38 | 38 | 38 | 0 | 0 | 38 | 0 | 0 |
| `data/literature/extracts/inoue-suito-1982-vanadium-mgo-saturated-slag.yaml` | 549 | 549 | 0 | 0 | 0 | 549 | 0 | 0 |
| `data/literature/extracts/itoh-hino-banya-1997-mg-deoxidation.yaml` | 0 | 0 | -37 | 0 | 39 | 39 | 0 | 0 |
| `data/literature/extracts/ivtan-cro2-g.yaml` | 0 | 0 | -61 | 0 | 64 | 64 | 0 | 0 |
| `data/literature/extracts/janz-1979-nsrds61-part2-molten-salt-properties.yaml` | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `data/literature/extracts/jayasuriya-2004-iron-mossbauer.yaml` | 0 | 0 | -3 | 0 | 38 | 38 | 0 | 0 |
| `data/literature/extracts/johnston-1965-sodium-disilicate-redox.yaml` | 0 | 0 | -101 | 0 | 148 | 148 | 0 | 0 |
| `data/literature/extracts/jsc-a16-special-samples.yaml` | 0 | 0 | -16 | 0 | 18 | 18 | 0 | 0 |
| `data/literature/extracts/jsc-lunar-catalog-12023.yaml` | 0 | 0 | -5 | 0 | 16 | 16 | 0 | 0 |
| `data/literature/extracts/kassis-frischat-1981-silicate-glass-vapour.yaml` | 0 | 0 | -8 | 0 | 16 | 16 | 0 | 0 |
| `data/literature/extracts/kems-001-homma-1966.yaml` | 0 | 0 | -63 | 0 | 72 | 72 | 0 | 0 |
| `data/literature/extracts/kems-002-ohno-1967.yaml` | 0 | 0 | -30 | 0 | 25 | 25 | 0 | 0 |
| `data/literature/extracts/kems-003-pound-1972.yaml` | 0 | 0 | -6 | 0 | 18 | 18 | 0 | 0 |
| `data/literature/extracts/kems-005-fedkin-2006.yaml` | 0 | 0 | -18 | 0 | 27 | 27 | 0 | 0 |
| `data/literature/extracts/kems-006-zhang-2021.yaml` | 0 | 0 | -11 | 0 | 15 | 15 | 0 | 0 |
| `data/literature/extracts/kems-007-costa-2015.yaml` | 0 | 0 | -4 | 0 | 113 | 113 | 0 | 0 |
| `data/literature/extracts/kems-008-schaefer-fegley-2004.yaml` | 0 | 0 | -5 | 0 | 78 | 78 | 0 | 0 |
| `data/literature/extracts/kems-009-safarian-2013.yaml` | 0 | 0 | -2 | 0 | 2 | 2 | 0 | 0 |
| `data/literature/extracts/kems-010-richter-2007.yaml` | 0 | 0 | -96 | 0 | 360 | 360 | 0 | 0 |
| `data/literature/extracts/kems-011-wetzel-gail-2013.yaml` | 0 | 0 | -3 | 0 | 17 | 17 | 0 | 0 |
| `data/literature/extracts/kems-012-sossi-2019.yaml` | 0 | 0 | -53 | 0 | 612 | 612 | 0 | 0 |
| `data/literature/extracts/kems-014-drowart-2005.yaml` | 0 | 0 | -18 | 0 | 17 | 17 | 0 | 0 |
| `data/literature/extracts/kems-015-hashimoto-1983.yaml` | 0 | 0 | -13 | 0 | 681 | 681 | 0 | 0 |
| `data/literature/extracts/kems-016-stolyarova-1992.yaml` | 0 | 0 | -25 | 0 | 22 | 22 | 0 | 0 |
| `data/literature/extracts/kems-017-stolyarova-2013.yaml` | 0 | 0 | -1 | 0 | 11 | 11 | 0 | 0 |
| `data/literature/extracts/kems-018-stolyarova-2012.yaml` | 0 | 0 | -1 | 0 | 1 | 1 | 0 | 0 |
| `data/literature/extracts/kems-020-hastie-1981-nbsir.yaml` | 0 | 0 | -53 | 0 | 112 | 112 | 0 | 0 |
| `data/literature/extracts/kems-021-plante-1992-feo.yaml` | 0 | 0 | -8 | 0 | 25 | 25 | 0 | 0 |
| `data/literature/extracts/kems-022-demaria-1971.yaml` | 0 | 0 | -50 | 0 | 117 | 117 | 0 | 0 |
| `data/literature/extracts/kems-027-plante-hastie-1983.yaml` | 0 | 0 | -1 | 0 | 48 | 48 | 0 | 0 |
| `data/literature/extracts/kems-031-halwax-2024.yaml` | 0 | 0 | -9 | 0 | 30 | 30 | 0 | 0 |
| `data/literature/extracts/kems-032-copland-jacobson-2010.yaml` | 0 | 0 | -15 | 0 | 13 | 13 | 0 | 0 |
| `data/literature/extracts/kems-035-sauerborn-2005.yaml` | 0 | 0 | -21 | 0 | 74 | 74 | 0 | 0 |
| `data/literature/extracts/kems-036-sesko-2024.yaml` | 0 | 0 | -8 | 0 | 8 | 8 | 0 | 0 |
| `data/literature/extracts/kems-037-richter-2002.yaml` | 0 | 0 | -4 | 0 | 4 | 4 | 0 | 0 |
| `data/literature/extracts/kems-038-matchett-2006.yaml` | 0 | 0 | -8 | 0 | 16 | 16 | 0 | 0 |
| `data/literature/extracts/kems-040-stolyarova-2015.yaml` | 0 | 0 | -1 | 0 | 2 | 2 | 0 | 0 |
| `data/literature/extracts/kems-041-sossi-fegley-2018.yaml` | 0 | 0 | -31 | 0 | 90 | 90 | 0 | 0 |
| `data/literature/extracts/kems-045-sossi-2018-pnas-cr.yaml` | 0 | 0 | -32 | 0 | 57 | 57 | 0 | 0 |
| `data/literature/extracts/kems-046-van-limpt-2007.yaml` | 0 | 0 | -17 | 0 | 24 | 24 | 0 | 0 |
| `data/literature/extracts/kems-047-turkdogan-1984-isij.yaml` | 0 | 0 | -14 | 0 | 14 | 14 | 0 | 0 |
| `data/literature/extracts/kems-051-allibert-1981.yaml` | 0 | 0 | -28 | 0 | 99 | 99 | 0 | 0 |
| `data/literature/extracts/kems-053-stolyarova-1991.yaml` | 0 | 0 | -5 | 0 | 144 | 144 | 0 | 0 |
| `data/literature/extracts/kems-057-kambayashi-1985.yaml` | 0 | 0 | -85 | 0 | 145 | 145 | 0 | 0 |
| `data/literature/extracts/kems-058-ohara-1987.yaml` | 0 | 0 | -40 | 0 | 110 | 110 | 0 | 0 |
| `data/literature/extracts/kems-066-ichise-1977.yaml` | 0 | 0 | -5 | 0 | 49 | 49 | 0 | 0 |
| `data/literature/extracts/kems-067-yamada-1980.yaml` | 0 | 0 | -33 | 0 | 48 | 48 | 0 | 0 |
| `data/literature/extracts/kems-069-furukawa-1976.yaml` | 0 | 0 | -38 | 0 | 52 | 52 | 0 | 0 |
| `data/literature/extracts/kems-087-yamada-kato-1980.yaml` | 0 | 0 | -4 | 0 | 19 | 19 | 0 | 0 |
| `data/literature/extracts/kems-088-ichise-1975.yaml` | 0 | 0 | -11 | 0 | 25 | 25 | 0 | 0 |
| `data/literature/extracts/kems-093-piacente-1975.yaml` | 0 | 0 | -10 | 0 | 40 | 40 | 0 | 0 |
| `data/literature/extracts/kems-095-ueda-1986.yaml` | 0 | 0 | -9 | 0 | 46 | 46 | 0 | 0 |
| `data/literature/extracts/kems-097-ikeda-1978.yaml` | 0 | 0 | -9 | 0 | 24 | 24 | 0 | 0 |
| `data/literature/extracts/kems-103-fraser-1983.yaml` | 0 | 0 | -141 | 0 | 142 | 142 | 0 | 0 |
| `data/literature/extracts/kems-105-yamada-1983.yaml` | 0 | 0 | -25 | 0 | 35 | 35 | 0 | 0 |
| `data/literature/extracts/kems-111-ichise-1982.yaml` | 0 | 0 | -29 | 0 | 64 | 64 | 0 | 0 |
| `data/literature/extracts/kems-112-ichise-1989.yaml` | 0 | 0 | -42 | 0 | 87 | 87 | 0 | 0 |
| `data/literature/extracts/kems-116-nunoue-1987.yaml` | 0 | 0 | -18 | 0 | 40 | 40 | 0 | 0 |
| `data/literature/extracts/kems-118-yamamoto-1983.yaml` | 0 | 0 | -43 | 0 | 50 | 50 | 0 | 0 |
| `data/literature/extracts/kems-119-furukawa-1975.yaml` | 0 | 0 | -20 | 0 | 38 | 38 | 0 | 0 |
| `data/literature/extracts/kems-120-ueshima-1984.yaml` | 0 | 0 | -25 | 0 | 30 | 30 | 0 | 0 |
| `data/literature/extracts/kems-127-qi-1989.yaml` | 0 | 0 | -17 | 0 | 26 | 26 | 0 | 0 |
| `data/literature/extracts/kems-132-nakajima-2016.yaml` | 0 | 0 | -15 | 0 | 23 | 23 | 0 | 0 |
| `data/literature/extracts/kems-137-bischof-2023.yaml` | 0 | 0 | -6 | 0 | 164 | 164 | 0 | 0 |
| `data/literature/extracts/kems-139-jacobson-2024.yaml` | 0 | 0 | -29 | 0 | 35 | 35 | 0 | 0 |
| `data/literature/extracts/kems-140-heck-2025.yaml` | 0 | 0 | -111 | 0 | 148 | 148 | 0 | 0 |
| `data/literature/extracts/kems-184-behrens-1979.yaml` | 0 | 0 | -6 | 0 | 49 | 49 | 0 | 0 |
| `data/literature/extracts/kems-188-nanjo-1976.yaml` | 0 | 0 | -8 | 0 | 12 | 12 | 0 | 0 |
| `data/literature/extracts/kems-190-wu-1993.yaml` | 0 | 0 | -61 | 0 | 63 | 63 | 0 | 0 |
| `data/literature/extracts/kems-198-ichise-ueshima-1989.yaml` | 0 | 0 | -10 | 0 | 23 | 23 | 0 | 0 |
| `data/literature/extracts/kems-200-ueshima-1983.yaml` | 0 | 0 | -24 | 0 | 32 | 32 | 0 | 0 |
| `data/literature/extracts/kems-201-ichise-1986.yaml` | 0 | 0 | -17 | 0 | 27 | 27 | 0 | 0 |
| `data/literature/extracts/kems-ms2000-044.yaml` | 0 | 0 | -94 | 0 | 140 | 140 | 0 | 0 |
| `data/literature/extracts/khedim-2008-chromia-solubility-thesis.yaml` | 25 | 25 | 0 | 0 | 0 | 25 | 0 | 0 |
| `data/literature/extracts/khedim-abdelouhab-2011-cr2o3-soda-silicate-kinetics.yaml` | 1 | 1 | 1 | 0 | 0 | 1 | 0 | 0 |
| `data/literature/extracts/khedim-katrina-podor-panteix-rapin-vilasi-2010-cr-soda-lime-silicate.yaml` | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `data/literature/extracts/khedim-podor-panteix-2010-binary-soda-silicate-cr-solubility.yaml` | 80 | 80 | 0 | 0 | 0 | 80 | 0 | 0 |
| `data/literature/extracts/kim-2014-carbon-fe-v-mo-ni.yaml` | 233 | 233 | 0 | 0 | 0 | 233 | 0 | 0 |
| `data/literature/extracts/kim-2018-sulfur-fe-v-mo-ni.yaml` | 208 | 208 | 9 | 0 | 0 | 208 | 0 | 0 |
| `data/literature/extracts/kirschen-1998-silicate-miscibility-thesis.yaml` | 21 | 21 | 0 | 0 | 0 | 21 | 0 | 0 |
| `data/literature/extracts/kirschen-1999-si-ti-al-miscibility.yaml` | 51 | 51 | 8 | 0 | 0 | 51 | 0 | 0 |
| `data/literature/extracts/kirschen-decapitani-1999-liquid-miscibility.yaml` | 576 | 576 | 110 | 0 | 0 | 576 | 0 | 0 |
| `data/literature/extracts/konar-2018-alkali-cmas-thesis.yaml` | 25 | 25 | 64 | 0 | 0 | 25 | 0 | 0 |
| `data/literature/extracts/kress-carmichael-1988-iron-oxidation-stoichiometry.yaml` | 0 | 0 | -126 | 0 | 127 | 127 | 0 | 0 |
| `data/literature/extracts/kropf-2009-alkalis-chondrules-thesis.yaml` | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `data/literature/extracts/lamoreaux-hildenbrand-1984.yaml` | 0 | 0 | -8 | 0 | 20 | 20 | 0 | 0 |
| `data/literature/extracts/leister-ehrt-2000-polyvalent-redox.yaml` | 3 | 14 | 3 | 0 | 0 | 14 | 0 | 0 |
| `data/literature/extracts/liu-2023-chromium-oxide-activity.yaml` | 0 | 0 | -36 | 0 | 72 | 72 | 0 | 0 |
| `data/literature/extracts/liu-2024-cao-al2o3-vox-phase.yaml` | 72 | 72 | 5 | 0 | 0 | 72 | 0 | 0 |
| `data/literature/extracts/llnl-2021-cai-aerodynamic-levitation.yaml` | 0 | 0 | -4 | 0 | 8 | 8 | 0 | 0 |
| `data/literature/extracts/lopatin-2021-ce-ti-zr-kems-ru.yaml` | 13 | 13 | 19 | 0 | 0 | 13 | 0 | 0 |
| `data/literature/extracts/lopatin-shugurov-tyurnina-2022-sro-al2o3-kems.yaml` | 13 | 213 | 0 | 0 | 0 | 213 | 0 | 0 |
| `data/literature/extracts/lpi-compendium-62231.yaml` | 0 | 0 | -1 | 0 | 1 | 1 | 0 | 0 |
| `data/literature/extracts/lpi-compendium-65701.yaml` | 0 | 0 | -47 | 0 | 50 | 50 | 0 | 0 |
| `data/literature/extracts/matzen-2017-ni-olivine-composition.yaml` | 180 | 180 | 180 | 0 | 0 | 180 | 0 | 0 |
| `data/literature/extracts/mendybaev-2021-cai-low-pressure-h2-evap.yaml` | 0 | 0 | -17 | 0 | 22 | 22 | 0 | 0 |
| `data/literature/extracts/miao-li-yan-2023-fe-v-spinel-gibbs.yaml` | 39 | 39 | 3 | 0 | 0 | 39 | 0 | 0 |
| `data/literature/extracts/mogul-2023-pioneer-venus-co2-icarus.yaml` | 0 | 0 | -5 | 0 | 6 | 6 | 0 | 0 |
| `data/literature/extracts/morita-1999-chromium-oxide-activity.yaml` | 0 | 0 | -105 | 0 | 210 | 210 | 0 | 0 |
| `data/literature/extracts/muan-1959-manganese-silica-air.yaml` | 57 | 57 | 7 | 0 | 0 | 57 | 0 | 0 |
| `data/literature/extracts/murchison-degassing-2023-springer.yaml` | 0 | 0 | -8 | 0 | 115 | 115 | 0 | 0 |
| `data/literature/extracts/murchison-hydropyrolysis-1990s-gca.yaml` | 0 | 0 | -38 | 0 | 42 | 42 | 0 | 0 |
| `data/literature/extracts/nagabayashi-hino-banya-1989-phosphorus.yaml` | 0 | 0 | -5 | 0 | 14 | 14 | 0 | 0 |
| `data/literature/extracts/nakamura-2022-science-ryugu-thermal.yaml` | 0 | 0 | -16 | 0 | 18 | 18 | 0 | 0 |
| `data/literature/extracts/nakano-hashimoto-2020-bubbles-to-chondrites-i.yaml` | 0 | 0 | -8 | 0 | 23 | 23 | 0 | 0 |
| `data/literature/extracts/nam-2025-cmas-vanadium-thesis.yaml` | 66 | 66 | 29 | 0 | 0 | 66 | 0 | 0 |
| `data/literature/extracts/nasa-tm-2024-simulant-guide.yaml` | 0 | 0 | -111 | 0 | 113 | 113 | 0 | 0 |
| `data/literature/extracts/neubert-2025-vanadium-slag-evaporation.yaml` | 148 | 148 | 148 | 0 | 0 | 148 | 0 | 0 |
| `data/literature/extracts/no27-fegley-cameron-1987-epsl.yaml` | 0 | 0 | -93 | 0 | 97 | 97 | 0 | 0 |
| `data/literature/extracts/ntrs-19730008085.yaml` | 0 | 0 | -30 | 0 | 30 | 30 | 0 | 0 |
| `data/literature/extracts/ntrs-20250004626.yaml` | 0 | 0 | -15 | 0 | 15 | 15 | 0 | 0 |
| `data/literature/extracts/ohta-1996-cao-mgo-al2o3-activities.yaml` | 0 | 0 | -10 | 0 | 20 | 20 | 0 | 0 |
| `data/literature/extracts/panda-2019-ten-oxide-thermodynamics.yaml` | 2 | 2 | 2 | 0 | 0 | 2 | 0 | 0 |
| `data/literature/extracts/park-2021-vanadium-thermodynamics-thesis.yaml` | 14 | 14 | 0 | 0 | 0 | 14 | 0 | 0 |
| `data/literature/extracts/park-min-2004-carbide-capacity-fluoride-soda-slags.yaml` | 111 | 111 | 0 | 0 | 0 | 111 | 0 | 0 |
| `data/literature/extracts/qi-1987-fe-c-v-interaction.yaml` | 71 | 71 | 19 | 0 | 0 | 71 | 0 | 0 |
| `data/literature/extracts/robinot-2025-promes-review.yaml` | 0 | 0 | -11 | 0 | 39 | 39 | 0 | 0 |
| `data/literature/extracts/rusiecka-2026-chlorine-data.yaml` | 1911 | 1911 | 3822 | 0 | 0 | 1911 | 0 | 0 |
| `data/literature/extracts/rusiecka-wood-2025-chlorine-nacl-hydrous-basaltic-melts.yaml` | 0 | 0 | -3 | 0 | 13 | 13 | 0 | 0 |
| `data/literature/extracts/sato-1976-lunar-fo2-apollo-17.yaml` | 0 | 0 | -6 | 0 | 115 | 115 | 0 | 0 |
| `data/literature/extracts/schaefer-fegley-2011-vaporization-earth.yaml` | 0 | 0 | -321 | 0 | 321 | 321 | 0 | 0 |
| `data/literature/extracts/shilov-1998-sno-zno-p2o5-vaporization.yaml` | 0 | 0 | -53 | 0 | 92 | 92 | 0 | 0 |
| `data/literature/extracts/shilov-2025-sro-al2o3-sio2-kems-supplement.yaml` | 42 | 42 | 0 | 0 | 0 | 42 | 0 | 0 |
| `data/literature/extracts/shilov-2026-sro-la2o3-kems.yaml` | 9 | 29 | 9 | 0 | 0 | 29 | 0 | 0 |
| `data/literature/extracts/shugurov-2024-gaseous-cerates-si.yaml` | 6 | 126 | 6 | 0 | 0 | 126 | 0 | 0 |
| `data/literature/extracts/slag-001-banya-1993.yaml` | 0 | 0 | -27 | 0 | 67 | 67 | 0 | 0 |
| `data/literature/extracts/smales-1971-lpsc-12022.yaml` | 0 | 0 | -2 | 0 | 2 | 2 | 0 | 0 |
| `data/literature/extracts/sossi-2020-cu-zn-isotope-evap-formalism.yaml` | 0 | 0 | -78 | 0 | 101 | 101 | 0 | 0 |
| `data/literature/extracts/steurer-1985-vapor-phase-pyrolysis.yaml` | 0 | 0 | -18 | 0 | 29 | 29 | 0 | 0 |
| `data/literature/extracts/steurer-1992-vapor-phase-pyrolysis.yaml` | 0 | 0 | -10 | 0 | 11 | 11 | 0 | 0 |
| `data/literature/extracts/stolyarova-2021-ti-al-kems-ru.yaml` | 205 | 205 | 177 | 0 | 0 | 205 | 0 | 0 |
| `data/literature/extracts/stolyarova-vorozhtcov-lopatin-2023-sro-al2o3-kems-supplement.yaml` | 190 | 1037 | 6 | 0 | 0 | 1037 | 0 | 0 |
| `data/literature/extracts/street-2010-thermal-properties-simulants.yaml` | 0 | 0 | -7 | 0 | 13 | 13 | 0 | 0 |
| `data/literature/extracts/su-2024-lunar-volatiles-thesis.yaml` | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `data/literature/extracts/sublimation-kinetics-2023-minerals.yaml` | 0 | 0 | -3 | 0 | 57 | 57 | 0 | 0 |
| `data/literature/extracts/sugawara-akaogi-2003-ni-co-fusion.yaml` | 18 | 18 | 18 | 0 | 0 | 18 | 0 | 0 |
| `data/literature/extracts/ta-shirai-2000-lpsc.yaml` | 0 | 0 | -35 | 0 | 46 | 46 | 0 | 0 |
| `data/literature/extracts/terashima-2026-sro-slag-activity-preprint.yaml` | 119 | 119 | 37 | 0 | 0 | 119 | 0 | 0 |
| `data/literature/extracts/thomas-2022-chlorine-bonding-silicate-melts.yaml` | 0 | 0 | -86 | 0 | 87 | 87 | 0 | 0 |
| `data/literature/extracts/thomas-wood-2021-chlorine-silicate-melts.yaml` | 0 | 0 | -111 | 0 | 111 | 111 | 0 | 0 |
| `data/literature/extracts/tomioka-1991-nitride-capacity-cao-al2o3.yaml` | 0 | 0 | -30 | 0 | 37 | 37 | 0 | 0 |
| `data/literature/extracts/ts1985.yaml` | 0 | 0 | -4 | 0 | 19 | 19 | 0 | 0 |
| `data/literature/extracts/usgs-lunar-sourcebook-tab8-2.yaml` | 0 | 0 | -2185 | 0 | 2185 | 2185 | 0 | 0 |
| `data/literature/extracts/usgs-lunar-sourcebook-tab8-4.yaml` | 0 | 0 | -90 | 0 | 90 | 90 | 0 | 0 |
| `data/literature/extracts/van-kooten-2020-lunar-outgassing.yaml` | 0 | 0 | -14 | 0 | 15 | 15 | 0 | 0 |
| `data/literature/extracts/vickerfalt-2020-mgo-v2o3.yaml` | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `data/literature/extracts/vickerfalt-2024-al-si-v-phase-diagram.yaml` | 39 | 39 | 0 | 0 | 0 | 39 | 0 | 0 |
| `data/literature/extracts/vickerfalt-2024-autogenous-slag-thesis.yaml` | 150 | 150 | 0 | 0 | 0 | 150 | 0 | 0 |
| `data/literature/extracts/wadhwa-2008-redox-small-bodies.yaml` | 0 | 0 | -2 | 0 | 2 | 2 | 0 | 0 |
| `data/literature/extracts/wang-2017-cao-sio2-vox-tables-s1-s32.yaml` | 516 | 516 | 516 | 0 | 0 | 516 | 0 | 0 |
| `data/literature/extracts/wimpenny-2019-zn-isotope-evaporation-extreme-t.yaml` | 0 | 0 | -19 | 0 | 60 | 60 | 0 | 0 |
| `data/literature/extracts/woermann-1969-mg-fe-ti-oxides.yaml` | 17 | 17 | 3 | 0 | 0 | 17 | 0 | 0 |
| `data/literature/extracts/xiao-reuter-holappa-2000-chromium-slag-oxidation-review.yaml` | 84 | 84 | 0 | 0 | 0 | 84 | 0 | 0 |
| `data/literature/extracts/xu-2026-excess-oxygen-magma.yaml` | 1462 | 1462 | 1463 | 0 | 0 | 1462 | 0 | 0 |
| `data/literature/extracts/yam1983.yaml` | 0 | 0 | -15 | 0 | 16 | 16 | 0 | 0 |
| `data/literature/extracts/yan-wang-deng-wu-dong-2020-cao-sio2-vox-gibbs-energy.yaml` | 230 | 230 | 250 | 0 | 0 | 230 | 0 | 0 |
| `data/literature/extracts/yu-2003-k-na-evaporation-chondrule-melts.yaml` | 1 | 32 | 1 | 0 | 0 | 32 | 0 | 0 |
| `data/literature/extracts/zagrtdenov-2020-chromite-magnesiochromite-solubility.yaml` | 132 | 132 | 29 | 0 | 0 | 132 | 0 | 0 |
| `data/literature/extracts/zagrtdenov-2025-chromite-magnesiochromite-dataset.yaml` | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| `data/literature/extracts/zhang-2024-alkali-evaporation-ti-thesis.yaml` | 23 | 257 | 23 | 0 | 0 | 257 | 0 | 0 |
| `data/literature/extracts/zigo-1987-gehlenite-fusion.yaml` | 0 | 0 | -13 | 0 | 27 | 27 | 0 | 0 |
| `stolyarova-1996-cao-alumina-silica-kems` | 0 | 0 | 0 | -55 | 0 | 0 | 192 | 137 |
