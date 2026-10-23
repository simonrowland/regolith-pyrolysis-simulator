# Car A pre-assembly (cara-asm-1022)

Branch: `worktree/cara-asm-1022`
Base: offer 20 r9 `d8c8943430faf7f527c4bc005e71ec0a4757d998`
Merge tip: `c9a53bbf5ce3b2d8b0b34383d144af2a29eb5ba4`
Code tip after the stub fix: `f991f3b58f6f648f7814555ae9c695944b55bb99`
This report is the next commit, the child of that code tip. The dispatch line names the branch tip, which is the commit that adds this file.

No push. No new battery-store regeneration. No `data/literature/INDEX.*`. No new ack. The seat worktree was clean after every merge; migrate ran in scratch worktrees, so `git checkout -- data/` had nothing to restore in the seat.

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

The committed tree already carries stranded's clean-merged regen and the base side of every derived-store conflict. This lane did not commit another regeneration.

!COMPLETE: cara-asm-1022 — f991f3b58f6f648f7814555ae9c695944b55bb99
