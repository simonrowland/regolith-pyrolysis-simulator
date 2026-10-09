# Experiment sample composition inheritance sweep

Worktree: `/workspace/repos/wt/slot-z6` at `8089eadbfc856179668287e9c4e503e0e92c2682`.
`engines/engines.local.toml` exists: **False**.
Migrated 266 extracts with `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(path)` and one `finalize()`.

## Code path

- `migrate.py:8728-8784`, `_printed_composition_from_roots`, walks each supplied root to depth 14. It accepts keys whose vocabulary field is `sample.printed_composition`, keeps numeric children, and requires a locator. Its fingerprint is the sorted `(species, normalized decimal string)` map. More than one fingerprint returns `None`, except a unique fingerprint among `_CHARGE_PRINTED_COMPOSITION_NAMES` wins; exactly one fingerprint returns the first found map and locator.
- `migrate.py:8790-8799`, `_lab_roots`, supplies observation equipment and values. `migrate.py:9096-9152`, `sample_from_equipment`, calls the root walker and stores the result as `Sample.printed_composition`; for oxide maps it may also create `initial_composition`.
- `migrate.py:10290-10337`, `_ensure_experiment`, invokes `sample_from_equipment` for an extract observation's equipment/values; existing samples merge in `_merge_experiment_lab_params` at `migrate.py:8992-9032`. `_prefer_located` at `migrate.py:8847-8884` keeps equal numeric maps, but returns `None` for unequal value maps. The experiment registry path is separate: `_lift_extract_registries` reads declared `sample`/`charge` at `migrate.py:10739-10885` through `experiment_from_plain` / `_sample_from_plain` (`migrate.py:1611-1649, 1992-2043`).
- An observation-row map therefore becomes `experiment.sample.printed_composition` when one `_ensure_experiment` call sees exactly one accepted fingerprint (or a unique preferred charge fingerprint) in that observation's equipment/values roots, and the merged sample slot is empty or has an equal value. A later unequal row map clears the merged slot; a row with no map does not. This is a per-call fingerprint rule followed by merge behavior, not a corpus-wide count of all maps.
- `waypoints.py:716-734`, `normalized_composition`, chooses `observation.point_conditions` when that key exists and otherwise falls back to `experiment.sample`; it also normalizes the map at lines 745-908. An inherited selected route is `normalized_printed_composition`.
- `waypoints.py:953-975`, `identity_composition_waypoint`, exposes only a row identity `Composition` on mole-fraction or mol-inventory basis. `consumer_inputs.py:69-90, 124-125, 184-190` gathers the normalized waypoint and that separate identity waypoint. `bench.py:315-323` lets an observation route or identity composition override the sample route for melt-activity inputs; `bench.py:373-401` passes `normalized_composition` directly to engine-point requests and records its route.
- `waypoints.py:2018-2030` combines engine-point and melt-activity readiness. Validity does not call `normalized_composition`: `validity.py:902-917` uses observation identity composition, then point `composition`/`sample_composition`, then `experiment.sample.initial_composition`; the bulk-species gate uses that result at `validity.py:991-1035`. Scoring's candidate resolver uses identity/point composition (`score.py:2595-2657`, `3061-3125`); its validity checks call `run_validity_gates` (`score.py:1678-1684`). `comparison_candidates` (`score.py:4779-4790`) itself filters only measured evidence and admitted/pending status.

## Results

- Final sample printed compositions attributed to the extract root path or a declared experiment/sample: **570 experiments**; A: **566**, B: **4**. A includes experiments with no inheriting observations; the class rule is applied to all lifted experiments so the counts add to the total.
- Class B inheriting observations: **51** across **4 sources**; comparison candidates: **25**.
- Null hypothesis: **refuted** for the migrated extract store.

### Lifted experiments

| Class | Source ID | Experiment ID | Origin | Observations | Own row/identity composition | Inheriting only |
|---|---|---|---|---:|---:|---:|
| A | boulliung-2025-mercury-volatile-metals-magmatic | 10.1016/j.chemgeo.2025.123018::experiment::ssas-hg-volatile-series | experiment/sample declaration | 9 | 0 | 0 |
| A | britt-2019-asteroid-simulants | 10.1111/maps.13345::experiment::tga-ega-ci | experiment/sample declaration | 1 | 0 | 0 |
| A | britt-2019-asteroid-simulants | 10.1111/maps.13345::experiment::tga-ega-cm | experiment/sample declaration | 1 | 0 | 0 |
| A | britt-2019-asteroid-simulants | 10.1111/maps.13345::experiment::tga-ega-cr | experiment/sample declaration | 1 | 0 | 1 |
| A | britt-2019-asteroid-simulants | 10.1111/maps.13345::experiment::xrf-ci | experiment/sample declaration | 1 | 1 | 0 |
| A | britt-2019-asteroid-simulants | 10.1111/maps.13345::experiment::xrf-cm | experiment/sample declaration | 1 | 1 | 0 |
| A | britt-2019-asteroid-simulants | 10.1111/maps.13345::experiment::xrf-cr | experiment/sample declaration | 1 | 1 | 0 |
| A | busemann-2000-phase-q-noble-gases | 10.1111/j.1945-5100.2000.tb01485.x::experiment::chainpur_csse | experiment/sample declaration | 23 | 0 | 0 |
| A | busemann-2000-phase-q-noble-gases | 10.1111/j.1945-5100.2000.tb01485.x::experiment::cold_bokkeveld_csse | experiment/sample declaration | 32 | 0 | 0 |
| A | busemann-2000-phase-q-noble-gases | 10.1111/j.1945-5100.2000.tb01485.x::experiment::dimmitt_csse | experiment/sample declaration | 20 | 0 | 0 |
| A | busemann-2000-phase-q-noble-gases | 10.1111/j.1945-5100.2000.tb01485.x::experiment::grosnaja_csse | experiment/sample declaration | 22 | 0 | 0 |
| A | busemann-2000-phase-q-noble-gases | 10.1111/j.1945-5100.2000.tb01485.x::experiment::isna_csse | experiment/sample declaration | 24 | 0 | 0 |
| A | busemann-2000-phase-q-noble-gases | 10.1111/j.1945-5100.2000.tb01485.x::experiment::lance_csse | experiment/sample declaration | 12 | 0 | 0 |
| A | cardiff-2007-vacuum-pyrolysis-gsfc | 36a2bd0f7fed7a60838c49c676fa2f0e51024220b1e9310c9a25091d618b3fe2::experiment::psu-reduced-pressure-test | experiment/sample declaration | 1 | 0 | 0 |
| A | deguzman-2026-simulant-physicochemical | 974b5aba0b1aa5d339a5368973607339493726f4e4457d5a7793ba6101421824::experiment::bet-surface-area-series | experiment/sample declaration | 6 | 0 | 0 |
| A | deguzman-2026-simulant-physicochemical | 974b5aba0b1aa5d339a5368973607339493726f4e4457d5a7793ba6101421824::experiment::hydrogen-tpr-series | experiment/sample declaration | 1 | 0 | 0 |
| A | engelschion-2020-eac1a-simulant | 10.1038/s41598-020-62312-4::experiment::dsc-simulant-series | experiment/sample declaration | 1 | 0 | 0 |
| A | hendrix-2024-reactivity-reduced-simulants | 10.1111/maps.14228::experiment::jsc-1a-h2-reduction | experiment/sample declaration | 4 | 1 | 3 |
| A | hendrix-2024-reactivity-reduced-simulants | 10.1111/maps.14228::experiment::lhs-1-h2-reduction | experiment/sample declaration | 2 | 1 | 1 |
| A | hendrix-2024-reactivity-reduced-simulants | 10.1111/maps.14228::experiment::lms-1-h2-reduction | experiment/sample declaration | 2 | 1 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-ad-10-29 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-ad-12-15 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-ad-12-2 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-bk57-23 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-bk57-9 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-64-11 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-64-18 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-64-25 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-64-30 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-64-4 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-65-22 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-65-8 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-66-19 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-66-21 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-66-32 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-66-33 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-66-5 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-66-7 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-67-26 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-68-10 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-68-12 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-68-24 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-68-31 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-69-1 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-69-13 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-69-14 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-69-27 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-69-28 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-70-16 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-70-17 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-70-20 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-70-3 | experiment/sample declaration | 1 | 0 | 1 |
| A | holzheid-1997-feo-nio-coo-activity-metal-saturated | 10.1016/s0009-2541(97)00030-2::experiment::variable-mgo-v-70-6 | experiment/sample declaration | 1 | 0 | 1 |
| A | jgr-p-2024-mars-sam-clay-sulfate-ega | 10.1029/2024je008587::experiment::av | experiment/sample declaration | 5 | 0 | 0 |
| A | jgr-p-2024-mars-sam-clay-sulfate-ega | 10.1029/2024je008587::experiment::bd | experiment/sample declaration | 5 | 0 | 0 |
| A | jgr-p-2024-mars-sam-clay-sulfate-ega | 10.1029/2024je008587::experiment::ca1 | experiment/sample declaration | 5 | 0 | 0 |
| A | jgr-p-2024-mars-sam-clay-sulfate-ega | 10.1029/2024je008587::experiment::ca2 | experiment/sample declaration | 5 | 0 | 0 |
| A | jgr-p-2024-mars-sam-clay-sulfate-ega | 10.1029/2024je008587::experiment::mg1 | experiment/sample declaration | 5 | 0 | 0 |
| A | jgr-p-2024-mars-sam-clay-sulfate-ega | 10.1029/2024je008587::experiment::mg2 | experiment/sample declaration | 5 | 0 | 0 |
| A | jgr-p-2024-mars-sam-clay-sulfate-ega | 10.1029/2024je008587::experiment::nt1 | experiment/sample declaration | 5 | 0 | 0 |
| A | jgr-p-2024-mars-sam-clay-sulfate-ega | 10.1029/2024je008587::experiment::nt2 | experiment/sample declaration | 5 | 0 | 0 |
| A | jgr-p-2024-mars-sam-clay-sulfate-ega | 10.1029/2024je008587::experiment::pt | experiment/sample declaration | 5 | 0 | 0 |
| A | jgr-p-2024-mars-sam-clay-sulfate-ega | 10.1029/2024je008587::experiment::sam_blank | experiment/sample declaration | 0 | 0 | 0 |
| A | jgr-p-2024-mars-sam-clay-sulfate-ega | 10.1029/2024je008587::experiment::ze | experiment/sample declaration | 5 | 0 | 0 |
| A | kems-001-homma-1966 | 10.2320/jinstmet1952.30.6_515::experiment::fe-c-mn-6501-11 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-001-homma-1966 | 10.2320/jinstmet1952.30.6_515::experiment::fe-c-mn-6501-12 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-001-homma-1966 | 10.2320/jinstmet1952.30.6_515::experiment::fe-cu-6503-11 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-001-homma-1966 | 10.2320/jinstmet1952.30.6_515::experiment::fe-cu-6503-12 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-001-homma-1966 | 10.2320/jinstmet1952.30.6_515::experiment::fe-cu-6503-16 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-001-homma-1966 | 10.2320/jinstmet1952.30.6_515::experiment::fe-cu-6503-28 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-001-homma-1966 | 10.2320/jinstmet1952.30.6_515::experiment::fe-mn-6412-14 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-001-homma-1966 | 10.2320/jinstmet1952.30.6_515::experiment::fe-mn-6502-1 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-001-homma-1966 | 10.2320/jinstmet1952.30.6_515::experiment::fe-mn-6502-2 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-001-homma-1966 | 10.2320/jinstmet1952.30.6_515::experiment::fe-sn-6503-14 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-001-homma-1966 | 10.2320/jinstmet1952.30.6_515::experiment::fe-sn-6503-15 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-001-homma-1966 | 10.2320/jinstmet1952.30.6_515::experiment::fe-sn-6504-1 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-006-zhang-2021 | 6ae16616f5e2f8ee19ac5fce66cf1f8af1c7092013d4a4b1be22932f950ec2e4::experiment::basalt-bas1200c0h2 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-006-zhang-2021 | 6ae16616f5e2f8ee19ac5fce66cf1f8af1c7092013d4a4b1be22932f950ec2e4::experiment::basalt-bas1200c10h | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-006-zhang-2021 | 6ae16616f5e2f8ee19ac5fce66cf1f8af1c7092013d4a4b1be22932f950ec2e4::experiment::basalt-bas1200c1h | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-006-zhang-2021 | 6ae16616f5e2f8ee19ac5fce66cf1f8af1c7092013d4a4b1be22932f950ec2e4::experiment::basalt-bas1200c40h | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-006-zhang-2021 | 6ae16616f5e2f8ee19ac5fce66cf1f8af1c7092013d4a4b1be22932f950ec2e4::experiment::basalt-bas1200c4h | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-006-zhang-2021 | 6ae16616f5e2f8ee19ac5fce66cf1f8af1c7092013d4a4b1be22932f950ec2e4::experiment::basalt-bas1400c0m1 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-006-zhang-2021 | 6ae16616f5e2f8ee19ac5fce66cf1f8af1c7092013d4a4b1be22932f950ec2e4::experiment::basalt-bas1400c15m | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-006-zhang-2021 | 6ae16616f5e2f8ee19ac5fce66cf1f8af1c7092013d4a4b1be22932f950ec2e4::experiment::basalt-bas1400c30m | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-006-zhang-2021 | 6ae16616f5e2f8ee19ac5fce66cf1f8af1c7092013d4a4b1be22932f950ec2e4::experiment::basalt-bas1400c60m | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-006-zhang-2021 | 6ae16616f5e2f8ee19ac5fce66cf1f8af1c7092013d4a4b1be22932f950ec2e4::experiment::basalt-bas1400c90m | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-006-zhang-2021 | 6ae16616f5e2f8ee19ac5fce66cf1f8af1c7092013d4a4b1be22932f950ec2e4::kems-006-zhang-2021 | kems-006-zhang-2021::zhang_2021_table4_this_study_evaporation_coefficients / values.starting_glass_wt_pct | 5 | 4 | 0 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r-13 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r-14 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r-15 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r-16 | experiment/sample declaration | 3 | 1 | 2 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r-17 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r-6 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r2-13 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r2-14 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r2-15 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r2-17 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r2-18 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r2-19 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r2-20 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r2-21 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r2-7 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r2-8 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r2-9 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r3-12 | experiment/sample declaration | 3 | 1 | 2 |
| A | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::experiment::cai-r3-2 | experiment/sample declaration | 2 | 1 | 1 |
| B | kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::kems-010-richter-2007 | kems-010-richter-2007::richter_2007_mg_rate_series_geometry / values.composition_wt_pct | 15 | 1 | 14 |
| A | kems-011-wetzel-gail-2013 | 10.1051/0004-6361/201220803::experiment::sio-film-measurement-series | experiment/sample declaration | 17 | 0 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-1-3-16 | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-1-3-16b | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-15-07-16a | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-15-07-16b | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-2-3-16 | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-ps1 | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-ps2 | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-ps3 | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-ps4 | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-ps5 | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-ps6 | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-15-07-16c | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-15-07-16d | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-15-07-16g | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-15-07-16h | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16a | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16b | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16c | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16d | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16e | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16f | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16g | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16h | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-17-07-16a | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-17-07-16b | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-17-07-16c | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-18-07-16a | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-18-07-16c | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-3m-1-3-16 | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-3m-18-07-16b | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-3m-2-3-16 | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-3m-29-2-16 | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-3m-29-2-16b | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-c17-12-15a | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-c17-12-15b | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-c17-12-15c | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-c18-12-15 | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-p08-02-17b | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-p09-06-18a | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-p09-06-18b | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-p09-06-18c | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-p11-06-18a | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-012-sossi-2019 | 10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-p11-06-18b | experiment/sample declaration | 12 | 12 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c1 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c3-1 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c3-2 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c5-1 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c5-2 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c5-3 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c7 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c9-2 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17d2 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18b6-1 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18b6-2 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18b8-1 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18b8-2 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18b8-3 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c1-1 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c1-2 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c1-3 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c3-1 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c3-2 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c3-3 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c3-4 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c5-1 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c5-2 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-19b2 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-19b4 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-19b6 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-19b8 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-19c1 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-20b2 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-20b4 | experiment/sample declaration | 27 | 27 | 0 |
| A | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::experiment::hashimoto-1983-run-20b6 | experiment/sample declaration | 27 | 27 | 0 |
| B | kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | 31 | 12 | 19 |
| A | kems-020-hastie-1981-nbsir | 10.6028/nbs.ir.81-2279::experiment::hastie-1981-measured-soda-lime-glass | experiment/sample declaration | 3 | 0 | 3 |
| A | kems-020-hastie-1981-nbsir | 10.6028/nbs.ir.81-2279::experiment::hastie-1981-measured-soda-lime-glass-kms | experiment/sample declaration | 3 | 0 | 3 |
| A | kems-020-hastie-1981-nbsir | 10.6028/nbs.ir.81-2279::experiment::hastie-1981-measured-soda-lime-glass-tms | experiment/sample declaration | 5 | 0 | 5 |
| A | kems-020-hastie-1981-nbsir | 10.6028/nbs.ir.81-2279::experiment::hastie-1981-solgasmix-model-glass | experiment/sample declaration | 18 | 1 | 17 |
| A | kems-021-plante-1992-feo | 10.2355/isijinternational.32.1276::experiment::feo-mgo-sio2-series | experiment/sample declaration | 9 | 9 | 0 |
| A | kems-021-plante-1992-feo | 10.2355/isijinternational.32.1276::experiment::feo-mgo-sio2-xfeo-0p15 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-021-plante-1992-feo | 10.2355/isijinternational.32.1276::experiment::feo-mgo-sio2-xfeo-0p30 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-021-plante-1992-feo | 10.2355/isijinternational.32.1276::experiment::feo-mgo-sio2-xfeo-0p40 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-021-plante-1992-feo | 10.2355/isijinternational.32.1276::experiment::feo-mgo-sio2-xfeo-0p54 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-021-plante-1992-feo | 10.2355/isijinternational.32.1276::experiment::feo-mgo-sio2-xfeo-0p70 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-021-plante-1992-feo | 10.2355/isijinternational.32.1276::experiment::feo-mgo-sio2-xfeo-0p80 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-021-plante-1992-feo | 10.2355/isijinternational.32.1276::experiment::feo-mgo-sio2-xfeo-0p90 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-027-plante-hastie-1983 | 325ca7a754aefa98f00fefec10d3e89ae1a2d5cef23bc8c54b468afdf37924d0::experiment::kms-vacuum-glass-series | experiment/sample declaration | 7 | 0 | 0 |
| A | kems-027-plante-hastie-1983 | 325ca7a754aefa98f00fefec10d3e89ae1a2d5cef23bc8c54b468afdf37924d0::experiment::tms-n2-glass-series | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-031-halwax-2024 | 10.1007/s11663-024-02995-6::experiment::cao-series | experiment/sample declaration | 7 | 0 | 0 |
| A | kems-031-halwax-2024 | 10.1007/s11663-024-02995-6::experiment::mgo-series | experiment/sample declaration | 8 | 0 | 0 |
| A | kems-031-halwax-2024 | 10.1007/s11663-024-02995-6::experiment::nickel-calibration-series | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-035-sauerborn-2005 | d10e50ee6937a8dc68a31c6ffeda00081e85384b06ed4ebad2a1af7e912d8356::experiment::jsc1-ms1 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-035-sauerborn-2005 | d10e50ee6937a8dc68a31c6ffeda00081e85384b06ed4ebad2a1af7e912d8356::experiment::jsc1-ms2 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-035-sauerborn-2005 | d10e50ee6937a8dc68a31c6ffeda00081e85384b06ed4ebad2a1af7e912d8356::experiment::jsc1-ms4 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-035-sauerborn-2005 | d10e50ee6937a8dc68a31c6ffeda00081e85384b06ed4ebad2a1af7e912d8356::experiment::jsc1-ms5 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-035-sauerborn-2005 | d10e50ee6937a8dc68a31c6ffeda00081e85384b06ed4ebad2a1af7e912d8356::experiment::jsc1-solar-series | experiment/sample declaration | 34 | 2 | 32 |
| A | kems-035-sauerborn-2005 | d10e50ee6937a8dc68a31c6ffeda00081e85384b06ed4ebad2a1af7e912d8356::experiment::oxide-reference-series | experiment/sample declaration | 17 | 0 | 0 |
| A | kems-036-sesko-2024 | 10.1016/j.actaastro.2024.08.009::experiment::solar-exposure-12 | experiment/sample declaration | 4 | 0 | 4 |
| A | kems-036-sesko-2024 | 10.1016/j.actaastro.2024.08.009::kems-036-sesko-2024 | kems-036-sesko-2024::sesko_2024_table1_eac1_simulant_composition / values.major_oxide_wt_pct | 4 | 1 | 0 |
| A | kems-037-richter-2002 | 0c3d9db58c7c51a1c3cdff8509f9fb525a32352d1767ffc012d941abe775762e::experiment::b-113-evaporation | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-037-richter-2002 | 0c3d9db58c7c51a1c3cdff8509f9fb525a32352d1767ffc012d941abe775762e::experiment::b-133-evaporation | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-037-richter-2002 | 0c3d9db58c7c51a1c3cdff8509f9fb525a32352d1767ffc012d941abe775762e::experiment::bcai-evaporation | experiment/sample declaration | 2 | 0 | 2 |
| A | kems-039-wolf-2023-vaporock | f2e0decaf080185d6335e155731e9509598bd0aa2ce01d20c15398500c091e6d::experiment::lunar-basalt-12065 | experiment/sample declaration | 2 | 1 | 1 |
| A | kems-042-plante-1979 | 10.6028/nbs.sp.561v1::experiment::k2o-sio2-effusion-series | experiment/sample declaration | 383 | 324 | 59 |
| A | kems-044-robinot-2026 | 10.1016/j.asr.2026.02.003::experiment::solar-pyrolysis-series | experiment/sample declaration | 19 | 0 | 0 |
| A | kems-044-robinot-2026 | 10.1016/j.asr.2026.02.003::kems-044-robinot-2026 | kems-044-robinot-2026::robinot_2026_eac1_composition_quoted_sesko / values.major_oxide_wt_pct | 1 | 1 | 0 |
| A | kems-046-van-limpt-2007 | 10.6100/ir630685::experiment::borosilicate-glass-1 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-046-van-limpt-2007 | 10.6100/ir630685::experiment::borosilicate-glass-2 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-046-van-limpt-2007 | 10.6100/ir630685::experiment::borosilicate-glass-3 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-046-van-limpt-2007 | 10.6100/ir630685::experiment::float-glass-with-so3 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-046-van-limpt-2007 | 10.6100/ir630685::experiment::float-glass-without-so3 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-046-van-limpt-2007 | 10.6100/ir630685::experiment::sodium-disilicate-transpiration | experiment/sample declaration | 13 | 0 | 0 |
| A | kems-046-van-limpt-2007 | 10.6100/ir630685::experiment::tableware-glass-a | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-046-van-limpt-2007 | 10.6100/ir630685::experiment::tableware-glass-b | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-046-van-limpt-2007 | 10.6100/ir630685::experiment::tableware-glass-c | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-057-kambayashi-1985 | 10.2355/tetsutohagane1955.71.16_1911::experiment::feto-p2o5-sample-1 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-057-kambayashi-1985 | 10.2355/tetsutohagane1955.71.16_1911::experiment::feto-p2o5-sample-2 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-057-kambayashi-1985 | 10.2355/tetsutohagane1955.71.16_1911::experiment::feto-p2o5-sample-3 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-057-kambayashi-1985 | 10.2355/tetsutohagane1955.71.16_1911::experiment::feto-p2o5-sample-4 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-057-kambayashi-1985 | 10.2355/tetsutohagane1955.71.16_1911::experiment::feto-p2o5-sample-5 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-057-kambayashi-1985 | 10.2355/tetsutohagane1955.71.16_1911::experiment::feto-p2o5-sample-6 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-057-kambayashi-1985 | 10.2355/tetsutohagane1955.71.16_1911::experiment::feto-p2o5-sample-7 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-057-kambayashi-1985 | 10.2355/tetsutohagane1955.71.16_1911::experiment::feto-p2o5-sample-8 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-057-kambayashi-1985 | 10.2355/tetsutohagane1955.71.16_1911::experiment::pbo-p2o5-melt-1 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-057-kambayashi-1985 | 10.2355/tetsutohagane1955.71.16_1911::experiment::pbo-p2o5-melt-2 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-057-kambayashi-1985 | 10.2355/tetsutohagane1955.71.16_1911::experiment::pbo-p2o5-melt-3 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-1 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-10 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-11 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-12 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-13 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-14 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-15 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-16 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-17 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-18 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-19 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-2 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-3 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-4 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-5 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-6 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-7 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-8 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-058-ohara-1987 | 10.2355/tetsutohagane1955.73.10_1337::experiment::feto-p2o5-sample-9 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-066-ichise-1977 | 10.2355/tetsutohagane1955.63.3_417::experiment::fe-al-nal-0p0 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-066-ichise-1977 | 10.2355/tetsutohagane1955.63.3_417::experiment::fe-al-nal-0p1 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-066-ichise-1977 | 10.2355/tetsutohagane1955.63.3_417::experiment::fe-al-nal-0p2 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-066-ichise-1977 | 10.2355/tetsutohagane1955.63.3_417::experiment::fe-al-nal-0p3 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-066-ichise-1977 | 10.2355/tetsutohagane1955.63.3_417::experiment::fe-al-nal-0p368 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-066-ichise-1977 | 10.2355/tetsutohagane1955.63.3_417::experiment::fe-al-nal-0p406 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-066-ichise-1977 | 10.2355/tetsutohagane1955.63.3_417::experiment::fe-al-nal-0p5 | experiment/sample declaration | 3 | 0 | 0 |
| A | kems-066-ichise-1977 | 10.2355/tetsutohagane1955.63.3_417::experiment::fe-al-nal-0p6 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-066-ichise-1977 | 10.2355/tetsutohagane1955.63.3_417::experiment::fe-al-nal-0p7 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-066-ichise-1977 | 10.2355/tetsutohagane1955.63.3_417::experiment::fe-al-nal-0p8 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-066-ichise-1977 | 10.2355/tetsutohagane1955.63.3_417::experiment::fe-al-nal-0p9 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-066-ichise-1977 | 10.2355/tetsutohagane1955.63.3_417::experiment::fe-al-nal-1p0 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-087-yamada-kato-1980 | 10.2355/isijinternational1966.20.244::experiment::fep-1p52wt-fig9 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-087-yamada-kato-1980 | 10.2355/isijinternational1966.20.244::experiment::fep-kems-1600c-series | experiment/sample declaration | 17 | 0 | 0 |
| A | kems-088-ichise-1975 | 10.2355/isijinternational1966.15.115::experiment::fe-s-0p31-wtpct | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-088-ichise-1975 | 10.2355/isijinternational1966.15.115::experiment::fe-s-0p42-wtpct | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-088-ichise-1975 | 10.2355/isijinternational1966.15.115::experiment::fe-s-0p65-wtpct | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-088-ichise-1975 | 10.2355/isijinternational1966.15.115::experiment::fe-s-1p01-wtpct | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-088-ichise-1975 | 10.2355/isijinternational1966.15.115::experiment::fe-s-1p59-wtpct | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-088-ichise-1975 | 10.2355/isijinternational1966.15.115::experiment::fe-s-2p19-wtpct | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-088-ichise-1975 | 10.2355/isijinternational1966.15.115::experiment::fe-s-3p37-wtpct | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-088-ichise-1975 | 10.2355/isijinternational1966.15.115::experiment::fe-s-4p32-wtpct | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-095-ueda-1986 | 10.2320/jinstmet1952.50.12_1081::experiment::ti-co-nco-0p0 | experiment/sample declaration | 2 | 2 | 0 |
| A | kems-095-ueda-1986 | 10.2320/jinstmet1952.50.12_1081::experiment::ti-co-nco-0p1 | experiment/sample declaration | 2 | 2 | 0 |
| A | kems-095-ueda-1986 | 10.2320/jinstmet1952.50.12_1081::experiment::ti-co-nco-0p2 | experiment/sample declaration | 2 | 2 | 0 |
| A | kems-095-ueda-1986 | 10.2320/jinstmet1952.50.12_1081::experiment::ti-co-nco-0p3 | experiment/sample declaration | 2 | 2 | 0 |
| A | kems-095-ueda-1986 | 10.2320/jinstmet1952.50.12_1081::experiment::ti-co-nco-0p4 | experiment/sample declaration | 2 | 2 | 0 |
| A | kems-095-ueda-1986 | 10.2320/jinstmet1952.50.12_1081::experiment::ti-co-nco-0p5 | experiment/sample declaration | 2 | 2 | 0 |
| A | kems-095-ueda-1986 | 10.2320/jinstmet1952.50.12_1081::experiment::ti-co-nco-0p6 | experiment/sample declaration | 2 | 2 | 0 |
| A | kems-095-ueda-1986 | 10.2320/jinstmet1952.50.12_1081::experiment::ti-co-nco-0p7 | experiment/sample declaration | 2 | 2 | 0 |
| A | kems-095-ueda-1986 | 10.2320/jinstmet1952.50.12_1081::experiment::ti-co-nco-0p8 | experiment/sample declaration | 2 | 2 | 0 |
| A | kems-095-ueda-1986 | 10.2320/jinstmet1952.50.12_1081::experiment::ti-co-nco-0p9 | experiment/sample declaration | 2 | 2 | 0 |
| A | kems-095-ueda-1986 | 10.2320/jinstmet1952.50.12_1081::experiment::ti-co-nco-1p0 | experiment/sample declaration | 2 | 2 | 0 |
| A | kems-105-yamada-1983 | 10.2355/isijinternational1966.23.51::experiment::fep-i-1wtP-series | experiment/sample declaration | 35 | 0 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p048 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p091 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p094 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p139 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p144 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p192 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p234 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p242 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p298 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p320 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p370 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p384 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p388 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p394 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p502 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p548 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p596 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p671 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p760 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-nb-xnb-0p856 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-nb-kems-series | experiment/sample declaration | 45 | 0 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p048 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p054 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p094 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p152 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p153 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p156 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p239 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p244 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p255 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p297 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p319 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p404 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p406 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p436 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p444 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p606 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p727 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p775 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p810 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-112-ichise-1989 | 10.2355/isijinternational.29.843::experiment::fe-ta-xta-0p828 | experiment/sample declaration | 1 | 1 | 0 |
| A | kems-118-yamamoto-1983 | 10.2355/isijinternational1966.23.56::experiment::fe-sn-cu-series | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-118-yamamoto-1983 | 10.2355/isijinternational1966.23.56::experiment::fe-sn-nsn-0p010 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-118-yamamoto-1983 | 10.2355/isijinternational1966.23.56::experiment::fe-sn-nsn-0p020 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-118-yamamoto-1983 | 10.2355/isijinternational1966.23.56::experiment::fe-sn-nsn-0p030 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-118-yamamoto-1983 | 10.2355/isijinternational1966.23.56::experiment::fe-sn-nsn-0p040 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-118-yamamoto-1983 | 10.2355/isijinternational1966.23.56::experiment::fe-sn-nsn-0p050 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-118-yamamoto-1983 | 10.2355/isijinternational1966.23.56::experiment::fe-sn-nsn-0p060 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-118-yamamoto-1983 | 10.2355/isijinternational1966.23.56::experiment::fe-sn-nsn-0p070 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-118-yamamoto-1983 | 10.2355/isijinternational1966.23.56::experiment::fe-sn-nsn-0p080 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-118-yamamoto-1983 | 10.2355/isijinternational1966.23.56::experiment::fe-sn-nsn-0p089 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-118-yamamoto-1983 | 10.2355/isijinternational1966.23.56::experiment::fe-sn-nsn-0p100 | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-118-yamamoto-1983 | 10.2355/isijinternational1966.23.56::experiment::fe-sn-series | experiment/sample declaration | 40 | 0 | 0 |
| B | kems-137-bischof-2023 | 10.1016/j.gca.2023.08.027::experiment::an-di-1-low | kems-137-bischof-2023::bischof_2023_gao15_gamma_s2_1low_isotherm / equipment.starting_glass_wt_pct | 38 | 27 | 11 |
| A | kems-137-bischof-2023 | 10.1016/j.gca.2023.08.027::experiment::an-di-2-low | kems-137-bischof-2023::bischof_2023_gao15_gamma_s3_2low_polytherm / equipment.starting_glass_wt_pct | 54 | 54 | 0 |
| A | kems-137-bischof-2023 | 10.1016/j.gca.2023.08.027::experiment::an-di-3-high | kems-137-bischof-2023::bischof_2023_gao15_gamma_s4_3high_polytherm / equipment.starting_glass_wt_pct | 20 | 20 | 0 |
| A | kems-137-bischof-2023 | 10.1016/j.gca.2023.08.027::experiment::an-di-series | experiment/sample declaration | 50 | 30 | 20 |
| A | kems-138-bischof-2023 | 10.1016/j.calphad.2022.102507::experiment::ga2o3-series | experiment/sample declaration | 15 | 0 | 0 |
| A | kems-138-bischof-2023 | 10.1016/j.calphad.2022.102507::experiment::in2o3-series | experiment/sample declaration | 14 | 0 | 0 |
| A | kems-169-nakazawa-1976 | 10.2320/jinstmet1952.40.5_526::experiment::cd-zn-series | experiment/sample declaration | 5 | 0 | 0 |
| A | kems-188-nanjo-1976 | 10.2320/jinstmet1952.40.9_958::experiment::se-te-kems-series | experiment/sample declaration | 11 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-annealing-series | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-as-melt | experiment/sample declaration | 0 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-1 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-10 | experiment/sample declaration | 3 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-11 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-12 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-13 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-14 | experiment/sample declaration | 3 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-15 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-16 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-17 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-18 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-19 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-2 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-20 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-21 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-23 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-24 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-25 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-26 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-27 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-28 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-29 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-3 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-30 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-31 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-37 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-38 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-48 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-49 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-5 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-51 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-52 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-53 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-54 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-55 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-56 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-57 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-59 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-6 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-60 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-7 | experiment/sample declaration | 1 | 0 | 0 |
| A | kems-200-ueshima-1983 | 10.2355/tetsutohagane1955.69.6_556::experiment::ueshima-1983-sh-8 | experiment/sample declaration | 2 | 0 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::k2o-sio2-xsio2-0p500 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::k2o-sio2-xsio2-0p543 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::k2o-sio2-xsio2-0p591 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::k2o-sio2-xsio2-0p630 | experiment/sample declaration | 8 | 8 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::k2o-sio2-xsio2-0p674 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::k2o-sio2-xsio2-0p722 | experiment/sample declaration | 8 | 8 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::k2o-sio2-xsio2-0p770 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::k2o-sio2-xsio2-0p811 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::k2o-sio2-xsio2-0p848 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::k2o-sio2-xsio2-0p892 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::na2o-sio2-xsio2-0p349 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::na2o-sio2-xsio2-0p382 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::na2o-sio2-xsio2-0p405 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::na2o-sio2-xsio2-0p430 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::na2o-sio2-xsio2-0p477 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::na2o-sio2-xsio2-0p524 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::na2o-sio2-xsio2-0p573 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::na2o-sio2-xsio2-0p625 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::na2o-sio2-xsio2-0p671 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::na2o-sio2-xsio2-0p709 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::na2o-sio2-xsio2-0p753 | experiment/sample declaration | 4 | 4 | 0 |
| A | kems-ms2000-044 | 5fd1d72e3fb829f5b5c1b87be90482dd2d651a347320185683dc4dcd29ef70f6::experiment::na2o-sio2-xsio2-0p805 | experiment/sample declaration | 4 | 4 | 0 |
| B | llnl-2021-cai-aerodynamic-levitation | 9b5da9588d7a8a5c096c8f6ded983435c30a2876fdd6b5d84c73bada9f2f8973::llnl-2021-cai-aerodynamic-levitation | llnl-2021-cai-aerodynamic-levitation::ryerson_2022_CAI_starting_and_evaporated_composition_table1 / values.composition_wt_pct | 8 | 1 | 7 |
| A | lpsc-2024-bennu-pyrolysis-vandam | 8b79bdb9022f5df4da0a30d1c4bc731d1e2bf5895fbebf188091571818833877::experiment::standard-pyrolysis-600C | experiment/sample declaration | 0 | 0 | 0 |
| A | lpsc-2024-bennu-pyrolysis-vandam | 8b79bdb9022f5df4da0a30d1c4bc731d1e2bf5895fbebf188091571818833877::experiment::wet-chemistry-pyrolysis-250C | experiment/sample declaration | 1 | 0 | 0 |
| A | mendybaev-2017-fun-cai-lab-evaporation | 10.1016/j.gca.2016.08.034::experiment::func-5 | experiment/sample declaration | 1 | 1 | 0 |
| A | mendybaev-2017-fun-cai-lab-evaporation | 10.1016/j.gca.2016.08.034::mendybaev-2017-fun-cai-lab-evaporation | mendybaev-2017-fun-cai-lab-evaporation::mendybaev_2017_func_shared_starting_and_standard / values.starting_glass_wt_pct | 1 | 1 | 0 |
| A | mendybaev-2021-cai-low-pressure-h2-evap | 86c3ba1373c4e0104112a728521d068c32ce28f1fdcedc7b87aa8105a59e11d0::experiment::cai4b2-h2-2e-4-bar-series | experiment/sample declaration | 0 | 0 | 0 |
| A | mendybaev-2021-cai-low-pressure-h2-evap | 86c3ba1373c4e0104112a728521d068c32ce28f1fdcedc7b87aa8105a59e11d0::experiment::cai4b2-h2-2e-5-bar-series | experiment/sample declaration | 0 | 0 | 0 |
| A | mendybaev-2021-cai-low-pressure-h2-evap | 86c3ba1373c4e0104112a728521d068c32ce28f1fdcedc7b87aa8105a59e11d0::experiment::cai4b2-vacuum-series | experiment/sample declaration | 0 | 0 | 0 |
| A | murchison-degassing-2023-springer | 10.1134/s0038094623050064::experiment::murchison-isothermal-series | experiment/sample declaration | 26 | 0 | 0 |
| A | murchison-degassing-2023-springer | 10.1134/s0038094623050064::experiment::murchison-stepwise-series | experiment/sample declaration | 80 | 0 | 0 |
| A | murchison-hydropyrolysis-1990s-gca | 10.1016/j.gca.2003.08.019::experiment::murchison-h2-pyrolysis-series | experiment/sample declaration | 12 | 0 | 0 |
| A | nakano-hashimoto-2020-bubbles-to-chondrites-i | 10.1186/s40645-020-00354-0::experiment::allende-laser-boiling | experiment/sample declaration | 0 | 0 | 0 |
| A | nakano-hashimoto-2020-bubbles-to-chondrites-i | 10.1186/s40645-020-00354-0::experiment::laser-boiling-series | experiment/sample declaration | 14 | 0 | 0 |
| A | nakano-hashimoto-2020-bubbles-to-chondrites-i | 10.1186/s40645-020-00354-0::experiment::lsil-laser-boiling | experiment/sample declaration | 0 | 0 | 0 |
| A | norris-2017-earth-volatiles-nature | 10.1038/nature23645::experiment::volatile-loss-logfo2-neg11 | experiment/sample declaration | 2 | 0 | 2 |
| A | norris-2017-earth-volatiles-nature | 10.1038/nature23645::experiment::volatile-loss-logfo2-neg13 | experiment/sample declaration | 2 | 0 | 2 |
| A | norris-2017-earth-volatiles-nature | 10.1038/nature23645::experiment::volatile-loss-logfo2-neg7 | experiment/sample declaration | 2 | 1 | 1 |
| A | norris-2017-earth-volatiles-nature | 10.1038/nature23645::experiment::volatile-loss-logfo2-neg9 | experiment/sample declaration | 1 | 0 | 1 |
| A | ntrs-19650014783 | 85c75b4c3b2d782fbc74a644e1a4688eab5af5b0f0c40235bb77e1a94097d571::experiment::sodium-static-run-1 | experiment/sample declaration | 5 | 0 | 0 |
| A | ntrs-19650014783 | 85c75b4c3b2d782fbc74a644e1a4688eab5af5b0f0c40235bb77e1a94097d571::experiment::sodium-static-run-2 | experiment/sample declaration | 12 | 0 | 0 |
| A | ntrs-19650014783 | 85c75b4c3b2d782fbc74a644e1a4688eab5af5b0f0c40235bb77e1a94097d571::experiment::sodium-static-run-3 | experiment/sample declaration | 13 | 0 | 0 |
| A | ntrs-19650014783 | 85c75b4c3b2d782fbc74a644e1a4688eab5af5b0f0c40235bb77e1a94097d571::experiment::sodium-static-run-4 | experiment/sample declaration | 7 | 0 | 0 |
| A | ntrs-19650014783 | 85c75b4c3b2d782fbc74a644e1a4688eab5af5b0f0c40235bb77e1a94097d571::experiment::sodium-static-run-5 | experiment/sample declaration | 12 | 0 | 0 |
| A | pahlevan-2026-protolunar-volatile-outflows | 7a5c9c16910bb42a3ed78af0dd973cb95a6c1c2f332e0d415de6982bb5de8428::experiment::disk_volatile_atmosphere_3000K | experiment/sample declaration | 0 | 0 | 0 |
| A | pahlevan-2026-protolunar-volatile-outflows | 7a5c9c16910bb42a3ed78af0dd973cb95a6c1c2f332e0d415de6982bb5de8428::experiment::earth_volatile_atmosphere_5000K | experiment/sample declaration | 51 | 0 | 0 |
| A | pahlevan-2026-protolunar-volatile-outflows | 7a5c9c16910bb42a3ed78af0dd973cb95a6c1c2f332e0d415de6982bb5de8428::experiment::sodium_entrainment_3000K | experiment/sample declaration | 0 | 0 | 0 |
| A | reiss-2019-thermal-extraction-nulht2m | 10.1016/j.pss.2019.05.001::experiment::nulht2m-extraction-series | experiment/sample declaration | 8 | 0 | 0 |
| A | rusiecka-wood-2025-chlorine-nacl-hydrous-basaltic-melts | 10.1016/j.gca.2025.01.020::experiment::icb-1 | experiment/sample declaration | 1 | 0 | 0 |
| A | rusiecka-wood-2025-chlorine-nacl-hydrous-basaltic-melts | 10.1016/j.gca.2025.01.020::experiment::icb-10 | experiment/sample declaration | 1 | 0 | 0 |
| A | rusiecka-wood-2025-chlorine-nacl-hydrous-basaltic-melts | 10.1016/j.gca.2025.01.020::experiment::icb-2 | experiment/sample declaration | 1 | 0 | 0 |
| A | rusiecka-wood-2025-chlorine-nacl-hydrous-basaltic-melts | 10.1016/j.gca.2025.01.020::experiment::icb-3 | experiment/sample declaration | 1 | 0 | 0 |
| A | rusiecka-wood-2025-chlorine-nacl-hydrous-basaltic-melts | 10.1016/j.gca.2025.01.020::experiment::icb-4 | experiment/sample declaration | 1 | 0 | 0 |
| A | rusiecka-wood-2025-chlorine-nacl-hydrous-basaltic-melts | 10.1016/j.gca.2025.01.020::experiment::icb-5 | experiment/sample declaration | 1 | 0 | 0 |
| A | rusiecka-wood-2025-chlorine-nacl-hydrous-basaltic-melts | 10.1016/j.gca.2025.01.020::experiment::icb-6 | experiment/sample declaration | 1 | 0 | 0 |
| A | rusiecka-wood-2025-chlorine-nacl-hydrous-basaltic-melts | 10.1016/j.gca.2025.01.020::experiment::icb-7 | experiment/sample declaration | 1 | 0 | 0 |
| A | rusiecka-wood-2025-chlorine-nacl-hydrous-basaltic-melts | 10.1016/j.gca.2025.01.020::experiment::icb-8 | experiment/sample declaration | 1 | 0 | 0 |
| A | rusiecka-wood-2025-chlorine-nacl-hydrous-basaltic-melts | 10.1016/j.gca.2025.01.020::experiment::icb-9 | experiment/sample declaration | 1 | 0 | 0 |
| A | sf04-magma-companion-workbook | 10.1016/j.icarus.2003.08.023::experiment::sf04-tholeiite-workbook-grid | experiment/sample declaration | 56 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-01-08-18a | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-01-08-18b | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-01-08-18c | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-01-08-18d | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-02-08-18a | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-03-08-18b | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-10-04-17a | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-10-04-17a-r | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-19-06-18a | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-19-06-18b | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-19-06-18c | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-19-06-18d | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-20-06-18a | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-20-06-18b | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-21-06-18a | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-21-06-18b | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-21-06-18c | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-22-06-18b | experiment/sample declaration | 1 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-22-06-18d | experiment/sample declaration | 1 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-25-07-18a | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-26-12-18a | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-26-12-18b | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-26-12-18c | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-3-08-18a | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-31-07-18a | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-31-07-18b | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-31-07-18c | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-31-07-18d | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-4-04-17c | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-5-04-17a | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-5-04-17b | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-6-04-17a | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-6-04-17b | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-6-04-17c | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-7-04-17a | experiment/sample declaration | 2 | 0 | 0 |
| A | sossi-2020-cu-zn-isotope-evap-formalism | 10.1016/j.gca.2020.08.011::experiment::sossi-2020-p-7-04-17b | experiment/sample declaration | 2 | 0 | 0 |
| A | steurer-1985-vapor-phase-pyrolysis | df0238505301ce8823909c46c2187b3a6ba0fbd2420d9d5fecce946e681c3098::experiment::steurer-1985-model-mixture | experiment/sample declaration | 10 | 10 | 0 |
| A | sublimation-kinetics-2023-minerals | 10.3390/min13010079::experiment::apollo-12022-factsage | experiment/sample declaration | 2 | 1 | 1 |
| A | sublimation-kinetics-2023-minerals | 10.3390/min13010079::experiment::factsage-feo-proxy | experiment/sample declaration | 14 | 0 | 14 |
| A | sublimation-kinetics-2023-minerals | 10.3390/min13010079::experiment::lms1-sintering-series | experiment/sample declaration | 2 | 0 | 0 |
| A | ta-badro-2021 | 10.5802/crgeos.56::experiment::vp-series | ta-badro-2021::badro_2021_fit_26mg_24mg / values.laboratory_parameters.starting_composition.composition_wt_pct | 4 | 4 | 0 |
| A | ta-badro-2021 | 10.5802/crgeos.56::experiment::vp1 | ta-badro-2021::badro_2021_vp1_delta26mg / values.run.laboratory_parameters.starting_composition.composition_wt_pct | 4 | 4 | 0 |
| A | ta-badro-2021 | 10.5802/crgeos.56::experiment::vp2 | ta-badro-2021::badro_2021_vp2_delta26mg / values.run.laboratory_parameters.starting_composition.composition_wt_pct | 4 | 4 | 0 |
| A | ta-badro-2021 | 10.5802/crgeos.56::experiment::vp3 | ta-badro-2021::badro_2021_vp3_delta26mg / values.run.laboratory_parameters.starting_composition.composition_wt_pct | 4 | 4 | 0 |
| A | ta-badro-2021 | 10.5802/crgeos.56::experiment::vp4 | ta-badro-2021::badro_2021_vp4_delta26mg / values.run.laboratory_parameters.starting_composition.composition_wt_pct | 4 | 4 | 0 |
| A | ta-badro-2021 | 10.5802/crgeos.56::experiment::vp5 | ta-badro-2021::badro_2021_vp5_delta26mg / values.run.laboratory_parameters.starting_composition.composition_wt_pct | 4 | 4 | 0 |
| A | ta-badro-2021 | 10.5802/crgeos.56::experiment::vp6 | ta-badro-2021::badro_2021_vp6_delta26mg / values.run.laboratory_parameters.starting_composition.composition_wt_pct | 4 | 4 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1400c_1000hpa_0p5h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1400c_1000hpa_1h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1400c_1000hpa_2h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1400c_200hpa_0p5h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1400c_200hpa_1h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1400c_200hpa_2h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1400c_400hpa_0p5h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1400c_400hpa_1h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1400c_400hpa_2h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1600c_1000hpa_0p5h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1600c_1000hpa_1h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1600c_1000hpa_2h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1600c_200hpa_0p5h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1600c_200hpa_1h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1600c_200hpa_2h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1600c_400hpa_0p5h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1600c_400hpa_1h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t1_1600c_400hpa_2h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t2_1400c_0p2bar | experiment/sample declaration | 2 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t2_1400c_0p4bar | experiment/sample declaration | 2 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t2_1400c_1bar | experiment/sample declaration | 2 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t2_1500c_0p2bar | experiment/sample declaration | 2 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t2_1500c_0p4bar | experiment/sample declaration | 2 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t2_1500c_1bar | experiment/sample declaration | 2 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t2_1600c_0p2bar | experiment/sample declaration | 2 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t2_1600c_0p4bar | experiment/sample declaration | 2 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t2_1600c_1bar | experiment/sample declaration | 2 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t2_activity_1400c | experiment/sample declaration | 2 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t2_activity_1500c | experiment/sample declaration | 2 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t2_activity_1600c | experiment/sample declaration | 2 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t5_200hpa_1400c_1h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t5_200hpa_1600c_1h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t5_400hpa_1400c_1h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-dacko-conradt-low-p-transpiration | 6464b27a81073c0e1e340b1772e6520bb934e3aa294814333975c6ffe6bc8d1b::experiment::t5_400hpa_1600c_1h | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-flemetakis-2024 | 10.5194/ejm-36-173-2024::ta-flemetakis-2024 | ta-flemetakis-2024::flemetakis_2024_starting_glass_composition / values.composition_wt_pct | 72 | 1 | 0 |
| A | ta-mendybaev-2002-lpsc | 902510908415be31627b6455b1c2e313e9b13235dd467f9309dc5d4f7cc8425d::experiment::b133-vacuum-1800C-loop-series | experiment/sample declaration | 3 | 0 | 0 |
| A | ta-mendybaev-2002-lpsc | 902510908415be31627b6455b1c2e313e9b13235dd467f9309dc5d4f7cc8425d::experiment::b133-vacuum-multi-T-campaign | experiment/sample declaration | 3 | 0 | 0 |
| A | ta-mendybaev-2020-lpsc | 7ec3fe9fa2b6061b03cb4605140886a7244d4b260743cae7b2f7fc6336d49fe0::experiment::sio2-knudsen-ir-cell-1800C | experiment/sample declaration | 1 | 0 | 0 |
| A | ta-mendybaev-2020-lpsc | 7ec3fe9fa2b6061b03cb4605140886a7244d4b260743cae7b2f7fc6336d49fe0::experiment::sio2-langmuir-ir-loop-1800C | experiment/sample declaration | 2 | 0 | 0 |
| A | ta-shirai-2000-lpsc | c31f435ac3571abcf67819bf20c1353e15212bf08549e6c62bc389b6c5667bbf::experiment::shirai-fig1a-1300-pO2-1e-10 | experiment/sample declaration | 5 | 0 | 0 |
| A | ta-shirai-2000-lpsc | c31f435ac3571abcf67819bf20c1353e15212bf08549e6c62bc389b6c5667bbf::experiment::shirai-fig1a-1300-pO2-1e-8 | experiment/sample declaration | 9 | 0 | 0 |
| A | ta-shirai-2000-lpsc | c31f435ac3571abcf67819bf20c1353e15212bf08549e6c62bc389b6c5667bbf::experiment::shirai-fig1a-1300-pO2-1e-9 | experiment/sample declaration | 5 | 0 | 0 |
| A | ta-shirai-2000-lpsc | c31f435ac3571abcf67819bf20c1353e15212bf08549e6c62bc389b6c5667bbf::experiment::shirai-fig1b-1400-pO2-1e-10 | experiment/sample declaration | 6 | 0 | 0 |
| A | ta-shirai-2000-lpsc | c31f435ac3571abcf67819bf20c1353e15212bf08549e6c62bc389b6c5667bbf::experiment::shirai-fig1b-1400-pO2-1e-8 | experiment/sample declaration | 5 | 0 | 0 |
| A | ta-shirai-2000-lpsc | c31f435ac3571abcf67819bf20c1353e15212bf08549e6c62bc389b6c5667bbf::experiment::shirai-fig1b-1400-pO2-1e-9 | experiment/sample declaration | 5 | 0 | 0 |
| A | ta-shirai-2000-lpsc | c31f435ac3571abcf67819bf20c1353e15212bf08549e6c62bc389b6c5667bbf::experiment::shirai-flow-check-1400-pO2-1e-9 | experiment/sample declaration | 0 | 0 | 0 |
| A | ta-yamanaka-1997-metsoc | 0aeb508db97cd3a0df5fd8d5f0762c45b09654125b4d860c320067ae510543c5::experiment::yamanaka-1997-na-evaporation-series | experiment/sample declaration | 1 | 1 | 0 |
| A | thomas-wood-2021-chlorine-silicate-melts | 10.1016/j.gca.2020.11.018::experiment::table2_anhydrous_chlorine_series | experiment/sample declaration | 111 | 36 | 0 |
| A | ts1985 | 10.2355/tetsutohagane1955.71.7_815::experiment::na2o-sio2-table1-xna2o-0p50-t1200 | experiment/sample declaration | 1 | 1 | 0 |
| A | ts1985 | 10.2355/tetsutohagane1955.71.7_815::experiment::na2o-sio2-xna2o-0p40-t1100 | experiment/sample declaration | 1 | 1 | 0 |
| A | ts1985 | 10.2355/tetsutohagane1955.71.7_815::experiment::na2o-sio2-xna2o-0p40-t1200 | experiment/sample declaration | 1 | 1 | 0 |
| A | ts1985 | 10.2355/tetsutohagane1955.71.7_815::experiment::na2o-sio2-xna2o-0p40-t1300 | experiment/sample declaration | 1 | 1 | 0 |
| A | ts1985 | 10.2355/tetsutohagane1955.71.7_815::experiment::na2o-sio2-xna2o-0p45-t1100 | experiment/sample declaration | 1 | 1 | 0 |
| A | ts1985 | 10.2355/tetsutohagane1955.71.7_815::experiment::na2o-sio2-xna2o-0p45-t1200 | experiment/sample declaration | 1 | 1 | 0 |
| A | ts1985 | 10.2355/tetsutohagane1955.71.7_815::experiment::na2o-sio2-xna2o-0p45-t1300 | experiment/sample declaration | 1 | 1 | 0 |
| A | ts1985 | 10.2355/tetsutohagane1955.71.7_815::experiment::na2o-sio2-xna2o-0p50-t1100 | experiment/sample declaration | 1 | 1 | 0 |
| A | ts1985 | 10.2355/tetsutohagane1955.71.7_815::experiment::na2o-sio2-xna2o-0p50-t1200 | experiment/sample declaration | 1 | 1 | 0 |
| A | ts1985 | 10.2355/tetsutohagane1955.71.7_815::experiment::na2o-sio2-xna2o-0p50-t1300 | experiment/sample declaration | 1 | 1 | 0 |
| A | ts1985 | 10.2355/tetsutohagane1955.71.7_815::experiment::na2o-sio2-xna2o-0p55-t1100 | experiment/sample declaration | 1 | 1 | 0 |
| A | ts1985 | 10.2355/tetsutohagane1955.71.7_815::experiment::na2o-sio2-xna2o-0p55-t1200 | experiment/sample declaration | 1 | 1 | 0 |
| A | ts1985 | 10.2355/tetsutohagane1955.71.7_815::experiment::na2o-sio2-xna2o-0p55-t1300 | experiment/sample declaration | 1 | 1 | 0 |
| A | ueshima-1982-fe-mo-thermal | 10.2355/tetsutohagane1955.68.16_2569::experiment::femo-kems-series | experiment/sample declaration | 58 | 0 | 0 |
| A | ueshima-1982-fe-mo-thermal | 10.2355/tetsutohagane1955.68.16_2569::experiment::pure-metal-calibration-series | experiment/sample declaration | 3 | 0 | 0 |
| A | yam1983 | 10.2320/jinstmet1952.47.9_736::experiment::na2o-sio2-emf-series | experiment/sample declaration | 16 | 15 | 0 |
| A | yam1983 | 10.2320/jinstmet1952.47.9_736::experiment::na2o-sio2-xna2o-0p205 | experiment/sample declaration | 0 | 0 | 0 |
| A | yam1983 | 10.2320/jinstmet1952.47.9_736::experiment::na2o-sio2-xna2o-0p298 | experiment/sample declaration | 0 | 0 | 0 |
| A | yam1983 | 10.2320/jinstmet1952.47.9_736::experiment::na2o-sio2-xna2o-0p356 | experiment/sample declaration | 0 | 0 | 0 |
| A | yam1983 | 10.2320/jinstmet1952.47.9_736::experiment::na2o-sio2-xna2o-0p400 | experiment/sample declaration | 0 | 0 | 0 |
| A | yam1983 | 10.2320/jinstmet1952.47.9_736::experiment::na2o-sio2-xna2o-0p429 | experiment/sample declaration | 0 | 0 | 0 |
| A | yam1983 | 10.2320/jinstmet1952.47.9_736::experiment::na2o-sio2-xna2o-0p500 | experiment/sample declaration | 0 | 0 | 0 |
| A | yam1983 | 10.2320/jinstmet1952.47.9_736::experiment::na2o-sio2-xna2o-0p601 | experiment/sample declaration | 0 | 0 | 0 |

### Class B inheriting observations

Each description below comes from the extract YAML only; no PDFs were opened. `system_as_printed` and `notes` are shown when present; the row's own `phase`, `material`/`system_class`, `quote`, or usage text is included when those are the available descriptions. `different_material` is `yes` only when extract text/row labels identify incompatible systems, `no` when they identify the same material/system, and otherwise `cannot tell from the extract`.

| Source | Experiment | Origin observation / row | Inheriting observation | Quantity | Species | Evidence | system_as_printed / notes | Different material? | Comparison candidate? |
|---|---|---|---|---|---|---|---|---|---|
| kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::kems-010-richter-2007 | kems-010-richter-2007::richter_2007_mg_rate_series_geometry / values.composition_wt_pct | kems-010-richter-2007::richter_2007_al_non_loss_until_mg_exhausted | unknown | Al | measured_direct | system_as_printed: (not supplied); notes: (not supplied); phase: silicate_melt_Type_B_CAI_like; material/system: silicate_melt | no | no |
| kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::kems-010-richter-2007 | kems-010-richter-2007::richter_2007_mg_rate_series_geometry / values.composition_wt_pct | kems-010-richter-2007::richter_2007_al_quoted_non_loss_bound | unknown | Al | measured_direct | system_as_printed: (not supplied); notes: (not supplied); material/system: silicate_melt; extract quote: Calcium and aluminum are sufficiently refractory that they do not experience any significant loss by evaporation until virtually all of the magnesium has evaporated. | cannot tell from the extract | yes |
| kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::kems-010-richter-2007 | kems-010-richter-2007::richter_2007_mg_rate_series_geometry / values.composition_wt_pct | kems-010-richter-2007::richter_2007_ca_non_loss_until_mg_exhausted | unknown | Ca | measured_direct | system_as_printed: (not supplied); notes: (not supplied); phase: silicate_melt_Type_B_CAI_like; material/system: silicate_melt | no | no |
| kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::kems-010-richter-2007 | kems-010-richter-2007::richter_2007_mg_rate_series_geometry / values.composition_wt_pct | kems-010-richter-2007::richter_2007_ca_quoted_non_loss_bound | unknown | Ca | measured_direct | system_as_printed: (not supplied); notes: (not supplied); material/system: silicate_melt; extract quote: Calcium and aluminum are sufficiently refractory that they do not experience any significant loss by evaporation until virtually all of the magnesium has evaporated. | cannot tell from the extract | yes |
| kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::kems-010-richter-2007 | kems-010-richter-2007::richter_2007_mg_rate_series_geometry / values.composition_wt_pct | kems-010-richter-2007::richter_2007_mg_cai_langmuir_alpha_arrhenius | evaporation_coefficient_alpha | Mg | unknown | system_as_printed: (not supplied); notes: (not supplied); phase: silicate_melt_Type_B_CAI_like; material/system: Type_B_CAI_like_liquid | no | no |
| kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::kems-010-richter-2007 | kems-010-richter-2007::richter_2007_mg_rate_series_geometry / values.composition_wt_pct | kems-010-richter-2007::richter_2007_mg_quoted_fits | unknown | Mg | model_derived | system_as_printed: (not supplied); notes: (not supplied); extract quote: The combined vacuum and finite hydrogen pressure data are fit by the functional form γ = γ0e−E/RT with γ0 = 25.3, E = 92 ± 37 kJ mol−1 for γSi; γ0 = 143, E = 121 ± 53 kJ mol−1 for γMg. All uncertainties and error envelopes are 2σ | cannot tell from the extract | no |
| kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::kems-010-richter-2007 | kems-010-richter-2007::richter_2007_mg_rate_series_geometry / values.composition_wt_pct | kems-010-richter-2007::richter_2007_sio_cai_langmuir_alpha_arrhenius | evaporation_coefficient_alpha | SiO | unknown | system_as_printed: (not supplied); notes: (not supplied); phase: silicate_melt_Type_B_CAI_like; material/system: Type_B_CAI_like_liquid | no | no |
| kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::kems-010-richter-2007 | kems-010-richter-2007::richter_2007_mg_rate_series_geometry / values.composition_wt_pct | kems-010-richter-2007::richter_2007_sio_quoted_fits | unknown | SiO | model_derived | system_as_printed: (not supplied); notes: (not supplied); extract quote: The combined vacuum and finite hydrogen pressure data are fit by the functional form γ = γ0e−E/RT with γ0 = 25.3, E = 92 ± 37 kJ mol−1 for γSi; γ0 = 143, E = 121 ± 53 kJ mol−1 for γMg. All uncertainties and error envelopes are 2σ | cannot tell from the extract | no |
| kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::kems-010-richter-2007 | kems-010-richter-2007::richter_2007_mg_rate_series_geometry / values.composition_wt_pct | kems-010-richter-2007::richter_2007_table2_3537_1 | unknown | Mg | model_derived | system_as_printed: (not supplied); notes: Kinetic isotope fractionation alpha, not an HKL evaporation coefficient. Table estimates concern natural CAIs, not these laboratory evaporation residues.; extract quote: Sample \| Type \| FMg (‰ amu−1) \| % Mg evap. α = 0.97978 \| % Mg evap. α = 0.991: 3537-1 \| B1 \| 3.26 \| 14.9 \| 30.4 | yes | no |
| kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::kems-010-richter-2007 | kems-010-richter-2007::richter_2007_mg_rate_series_geometry / values.composition_wt_pct | kems-010-richter-2007::richter_2007_table2_e107 | unknown | Mg | model_derived | system_as_printed: (not supplied); notes: Kinetic isotope fractionation alpha, not an HKL evaporation coefficient. Table estimates concern natural CAIs, not these laboratory evaporation residues.; extract quote: Sample \| Type \| FMg (‰ amu−1) \| % Mg evap. α = 0.97978 \| % Mg evap. α = 0.991: E107 \| B2 \| 1.99 \| 9.4 \| 19.9 | yes | no |
| kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::kems-010-richter-2007 | kems-010-richter-2007::richter_2007_mg_rate_series_geometry / values.composition_wt_pct | kems-010-richter-2007::richter_2007_table2_f2_ts65 | unknown | Mg | model_derived | system_as_printed: (not supplied); notes: Kinetic isotope fractionation alpha, not an HKL evaporation coefficient. Table estimates concern natural CAIs, not these laboratory evaporation residues.; extract quote: Sample \| Type \| FMg (‰ amu−1) \| % Mg evap. α = 0.97978 \| % Mg evap. α = 0.991: F2 (TS65) \| B2 \| 6.12 \| 26.0 \| 49.2 | yes | no |
| kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::kems-010-richter-2007 | kems-010-richter-2007::richter_2007_mg_rate_series_geometry / values.composition_wt_pct | kems-010-richter-2007::richter_2007_table2_golfball | unknown | Mg | model_derived | system_as_printed: (not supplied); notes: Kinetic isotope fractionation alpha, not an HKL evaporation coefficient. Table estimates concern natural CAIs, not these laboratory evaporation residues.; extract quote: Sample \| Type \| FMg (‰ amu−1) \| % Mg evap. α = 0.97978 \| % Mg evap. α = 0.991: Golfball \| B \| 1.61 \| 7.7 \| 16.4 | yes | no |
| kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::kems-010-richter-2007 | kems-010-richter-2007::richter_2007_mg_rate_series_geometry / values.composition_wt_pct | kems-010-richter-2007::richter_2007_table2_ts33 | unknown | Mg | model_derived | system_as_printed: (not supplied); notes: Kinetic isotope fractionation alpha, not an HKL evaporation coefficient. Table estimates concern natural CAIs, not these laboratory evaporation residues.; extract quote: Sample \| Type \| FMg (‰ amu−1) \| % Mg evap. α = 0.97978 \| % Mg evap. α = 0.991: TS33 \| B1 \| 4.01 \| 18.0 \| 35.9 | yes | no |
| kems-010-richter-2007 | 10.1016/j.gca.2007.09.005::kems-010-richter-2007 | kems-010-richter-2007::richter_2007_mg_rate_series_geometry / values.composition_wt_pct | kems-010-richter-2007::richter_2007_table2_ts34 | unknown | Mg | model_derived | system_as_printed: (not supplied); notes: Kinetic isotope fractionation alpha, not an HKL evaporation coefficient. Table estimates concern natural CAIs, not these laboratory evaporation residues.; extract quote: Sample \| Type \| FMg (‰ amu−1) \| % Mg evap. α = 0.97978 \| % Mg evap. α = 0.991: TS34 \| B1 \| 5.63 \| 24.2 \| 46.4 | yes | no |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_alpha_hertz_knudsen_assumed_unity_ratio_quoted | unknown | Fe | model_derived | system_as_printed: (not supplied); notes: (not supplied); phase: silicate_melt_FCMAS_solar_abundance; extract quote: If αi/αj is assumed to be unity in eqn. (16), the ratio is 0.36 (at 1,600°C) | no | no |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_cao_al2o3_residue_enrichment | unknown | Ca | measured_direct | system_as_printed: (not supplied); notes: Residue enrichment is composition-path evidence, not an absolute evaporation rate or an activity coefficient.; phase: silicate_melt_FCMAS_solar_abundance; material/system: silicate_melt | no | yes |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_derived_feo_activation_energy_quoted | unknown | Fe | model_derived | system_as_printed: (not supplied); notes: (not supplied); phase: silicate_melt_FCMAS_solar_abundance; material/system: silicate_melt; extract quote: The activation energy of FeO vaporization lies in the range 93.7-107.9 kcal/mol, and shows a trend to increase with lowering molar concentration of FeO. For SiO2 and MgO, the values 131.6 and 123.6 kcal/mol are derived, respectively. | no | no |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_derived_mgo_activation_energy | unknown | Mg | model_derived | system_as_printed: (not supplied); notes: (not supplied); phase: silicate_melt_FCMAS_solar_abundance; material/system: silicate_melt | no | no |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_derived_mgo_activation_energy_quoted | unknown | Mg | model_derived | system_as_printed: (not supplied); notes: (not supplied); phase: silicate_melt_FCMAS_solar_abundance; material/system: silicate_melt; extract quote: The activation energy of FeO vaporization lies in the range 93.7-107.9 kcal/mol, and shows a trend to increase with lowering molar concentration of FeO. For SiO2 and MgO, the values 131.6 and 123.6 kcal/mol are derived, respectively. | no | no |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_derived_sio2_activation_energy | unknown | SiO | model_derived | system_as_printed: (not supplied); notes: (not supplied); phase: silicate_melt_FCMAS_solar_abundance; material/system: silicate_melt | no | no |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_derived_sio2_activation_energy_quoted | unknown | SiO | model_derived | system_as_printed: (not supplied); notes: (not supplied); phase: silicate_melt_FCMAS_solar_abundance; material/system: silicate_melt; extract quote: The activation energy of FeO vaporization lies in the range 93.7-107.9 kcal/mol, and shows a trend to increase with lowering molar concentration of FeO. For SiO2 and MgO, the values 131.6 and 123.6 kcal/mol are derived, respectively. | no | no |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_fcmas_qualitative_volatility_order | unknown | Fe | measured_direct | system_as_printed: (not supplied); notes: The source's gross sequence supports relative extraction order only. Mg and Si change relative behavior with evaporation stage, so their adjacency is typed as one tier rather than a fixed pairwise rate ranking.; phase: silicate_melt_FCMAS_solar_abundance; material/system: silicate_melt | no | yes |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_mg_fcmas_free_evap_geometry | unknown | Mg | unknown | system_as_printed: (not supplied); notes: (not supplied); phase: silicate_melt_FCMAS_solar_abundance; material/system: FeO-MgO-SiO2-CaO-Al2O3 | no | no |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_mg_geometry_class_b1 | unknown | Mg | measured_direct | system_as_printed: (not supplied); notes: Primary free-evap experiment behind Fedkin Fe/Mg/SiO α. Spherule free surface evolves with mass loss; starting preform given; per-run free-surface series not tabulated as numeric m2 series — ABSENT_NOT_NUMERIC for Motzfeldt (not a Knudsen cell). No area is estimated from the preform dimensions.; phase: silicate_melt_FCMAS; material/system: silicate_melt | no | yes |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_sio_fcmas_free_evap_geometry | unknown | SiO | unknown | system_as_printed: (not supplied); notes: (not supplied); phase: silicate_melt_FCMAS_solar_abundance; material/system: FeO-MgO-SiO2-CaO-Al2O3 | no | no |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_sio_geometry_class_b1 | unknown | SiO | measured_direct | system_as_printed: (not supplied); notes: Primary free-evap experiment behind Fedkin Fe/Mg/SiO α. Spherule free surface evolves with mass loss; starting preform given; per-run free-surface series not tabulated as numeric m2 series — ABSENT_NOT_NUMERIC for Motzfeldt (not a Knudsen cell). No area is estimated from the preform dimensions.; phase: silicate_melt_FCMAS; material/system: silicate_melt | no | yes |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_stage_iv_cao_relative_volatility | unknown | Ca | measured_direct | system_as_printed: (not supplied); notes: The one-third statement constrains CaO relative to SiO2 only; it is not promoted to an absolute CaO or SiO2 evaporation rate.; phase: silicate_melt_FCMAS_solar_abundance_stage_IV; material/system: silicate_melt | no | yes |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_table1_starting_composition | unknown | Fe | measured_direct | system_as_printed: (not supplied); notes: Row (1) Ave. is regarded as the starting composition in the experiments. Full 7-sample EPMA grid in tables/kems-015-hashimoto-1983/t1.csv. T_range is the experimental grid, not a measurement T of the starting powder.; phase: silicate_melt_FCMAS_solar_abundance; material/system: silicate_melt | no | yes |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_table6_feo_vaporization_enthalpies_quoted | unknown | Fe | model_derived | system_as_printed: (not supplied); notes: Full 20-row table in tables/kems-015-hashimoto-1983/t6.csv. These are JANAF thermodynamic values, not measured evaporation rates. No kcal-to-kJ conversion.; phase: liquid_pure_oxides_JANAF; material/system: pure_oxide_liquids; extract quote: no,reactant,products,delta_H_kcal_mol,delta_H_over_z_kcal_mol 1,Fe,Fe,89.243,89.243 2,FeO,Fe + 1/2 O2,150.637,100.425 3,FeO,Fe + O,211.657,105.829 4,FeO,FeO,109.476,109.476 5,Fe2O3,2Fe + 3/2 O2,352.832,100.809 6,Fe2O3,2Fe + 3O,535.892,107.178 7,Fe3O4,3Fe + 2 O2,506.610,101.322 8,Fe3O4,3Fe + 4O,750.690,107.241 9,Fe2O3,2FeO + 1/2 O2,270.510,108.204 10,Fe2O3,2FeO + O,331.530,110.510 11,Fe3O4,3FeO + 1/2 O2,383.127,109.465 12,Fe3O4,3FeO + O,444.147,111.037 13,SiO2,SiO + 1/2 O2,184.198,122.799 14,SiO2,SiO + O,245.218,122.609 15,SiO2,SiO2,137.071,137.071 16,SiO2,Si + O2,317.250,158.625 17,SiO2,Si + 2 | yes | no |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_table6_mgo_vaporization_enthalpies | unknown | Mg | compilation_assessed | system_as_printed: (not supplied); notes: (not supplied); phase: liquid_pure_oxides_JANAF | yes | no |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_table6_mgo_vaporization_enthalpies_quoted | unknown | Mg | model_derived | system_as_printed: (not supplied); notes: (not supplied); phase: liquid_pure_oxides_JANAF; extract quote: no,reactant,products,delta_H_kcal_mol,delta_H_over_z_kcal_mol 1,Fe,Fe,89.243,89.243 2,FeO,Fe + 1/2 O2,150.637,100.425 3,FeO,Fe + O,211.657,105.829 4,FeO,FeO,109.476,109.476 5,Fe2O3,2Fe + 3/2 O2,352.832,100.809 6,Fe2O3,2Fe + 3O,535.892,107.178 7,Fe3O4,3Fe + 2 O2,506.610,101.322 8,Fe3O4,3Fe + 4O,750.690,107.241 9,Fe2O3,2FeO + 1/2 O2,270.510,108.204 10,Fe2O3,2FeO + O,331.530,110.510 11,Fe3O4,3FeO + 1/2 O2,383.127,109.465 12,Fe3O4,3FeO + O,444.147,111.037 13,SiO2,SiO + 1/2 O2,184.198,122.799 14,SiO2,SiO + O,245.218,122.609 15,SiO2,SiO2,137.071,137.071 16,SiO2,Si + O2,317.250,158.625 17,SiO2,Si + 2 | yes | no |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_table6_sio2_vaporization_enthalpies | unknown | SiO | compilation_assessed | system_as_printed: (not supplied); notes: (not supplied); phase: liquid_pure_oxides_JANAF | yes | no |
| kems-015-hashimoto-1983 | 10.2343/geochemj.17.111::kems-015-hashimoto-1983 | kems-015-hashimoto-1983::hashimoto_1983_fe_fcmas_free_evap_geometry / values.composition_wt_pct | kems-015-hashimoto-1983::hashimoto_1983_table6_sio2_vaporization_enthalpies_quoted | unknown | SiO | model_derived | system_as_printed: (not supplied); notes: (not supplied); phase: liquid_pure_oxides_JANAF; extract quote: no,reactant,products,delta_H_kcal_mol,delta_H_over_z_kcal_mol 1,Fe,Fe,89.243,89.243 2,FeO,Fe + 1/2 O2,150.637,100.425 3,FeO,Fe + O,211.657,105.829 4,FeO,FeO,109.476,109.476 5,Fe2O3,2Fe + 3/2 O2,352.832,100.809 6,Fe2O3,2Fe + 3O,535.892,107.178 7,Fe3O4,3Fe + 2 O2,506.610,101.322 8,Fe3O4,3Fe + 4O,750.690,107.241 9,Fe2O3,2FeO + 1/2 O2,270.510,108.204 10,Fe2O3,2FeO + O,331.530,110.510 11,Fe3O4,3FeO + 1/2 O2,383.127,109.465 12,Fe3O4,3FeO + O,444.147,111.037 13,SiO2,SiO + 1/2 O2,184.198,122.799 14,SiO2,SiO + O,245.218,122.609 15,SiO2,SiO2,137.071,137.071 16,SiO2,Si + O2,317.250,158.625 17,SiO2,Si + 2 | yes | no |
| kems-137-bischof-2023 | 10.1016/j.gca.2023.08.027::experiment::an-di-1-low | kems-137-bischof-2023::bischof_2023_gao15_gamma_s2_1low_isotherm / equipment.starting_glass_wt_pct | kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1586.4:h=d7dddfc9ba1e | p_partial | Ga | measured_direct | system_as_printed: (not supplied); notes: (not supplied); phase: anorthite_diopside_eutectic_silicate_melt | no | yes |
| kems-137-bischof-2023 | 10.1016/j.gca.2023.08.027::experiment::an-di-1-low | kems-137-bischof-2023::bischof_2023_gao15_gamma_s2_1low_isotherm / equipment.starting_glass_wt_pct | kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1598.0:h=2cd4bd7cfe96 | p_partial | Ga | measured_direct | system_as_printed: (not supplied); notes: (not supplied); phase: anorthite_diopside_eutectic_silicate_melt | no | yes |
| kems-137-bischof-2023 | 10.1016/j.gca.2023.08.027::experiment::an-di-1-low | kems-137-bischof-2023::bischof_2023_gao15_gamma_s2_1low_isotherm / equipment.starting_glass_wt_pct | kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1608.6:h=b9dae2d4d4f2 | p_partial | Ga | measured_direct | system_as_printed: (not supplied); notes: (not supplied); phase: anorthite_diopside_eutectic_silicate_melt | no | yes |
| kems-137-bischof-2023 | 10.1016/j.gca.2023.08.027::experiment::an-di-1-low | kems-137-bischof-2023::bischof_2023_gao15_gamma_s2_1low_isotherm / equipment.starting_glass_wt_pct | kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1619.2:h=d2f65843657e | p_partial | Ga | measured_direct | system_as_printed: (not supplied); notes: (not supplied); phase: anorthite_diopside_eutectic_silicate_melt | no | yes |
| kems-137-bischof-2023 | 10.1016/j.gca.2023.08.027::experiment::an-di-1-low | kems-137-bischof-2023::bischof_2023_gao15_gamma_s2_1low_isotherm / equipment.starting_glass_wt_pct | kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1630.8:h=32f9208ca45e | p_partial | Ga | measured_direct | system_as_printed: (not supplied); notes: (not supplied); phase: anorthite_diopside_eutectic_silicate_melt | no | yes |
| kems-137-bischof-2023 | 10.1016/j.gca.2023.08.027::experiment::an-di-1-low | kems-137-bischof-2023::bischof_2023_gao15_gamma_s2_1low_isotherm / equipment.starting_glass_wt_pct | kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1642.5:h=3d5e1b4780de | p_partial | Ga | measured_direct | system_as_printed: (not supplied); notes: (not supplied); phase: anorthite_diopside_eutectic_silicate_melt | no | yes |
| kems-137-bischof-2023 | 10.1016/j.gca.2023.08.027::experiment::an-di-1-low | kems-137-bischof-2023::bischof_2023_gao15_gamma_s2_1low_isotherm / equipment.starting_glass_wt_pct | kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1653.0:h=917fb8de71ee | p_partial | Ga | measured_direct | system_as_printed: (not supplied); notes: (not supplied); phase: anorthite_diopside_eutectic_silicate_melt | no | yes |
| kems-137-bischof-2023 | 10.1016/j.gca.2023.08.027::experiment::an-di-1-low | kems-137-bischof-2023::bischof_2023_gao15_gamma_s2_1low_isotherm / equipment.starting_glass_wt_pct | kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1664.7:h=8dd7cd739c2c | p_partial | Ga | measured_direct | system_as_printed: (not supplied); notes: (not supplied); phase: anorthite_diopside_eutectic_silicate_melt | no | yes |
| kems-137-bischof-2023 | 10.1016/j.gca.2023.08.027::experiment::an-di-1-low | kems-137-bischof-2023::bischof_2023_gao15_gamma_s2_1low_isotherm / equipment.starting_glass_wt_pct | kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1674.2:h=937c91dd605a | p_partial | Ga | measured_direct | system_as_printed: (not supplied); notes: (not supplied); phase: anorthite_diopside_eutectic_silicate_melt | no | yes |
| kems-137-bischof-2023 | 10.1016/j.gca.2023.08.027::experiment::an-di-1-low | kems-137-bischof-2023::bischof_2023_gao15_gamma_s2_1low_isotherm / equipment.starting_glass_wt_pct | kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1684.8:h=56d20ce145ed | p_partial | Ga | measured_direct | system_as_printed: (not supplied); notes: (not supplied); phase: anorthite_diopside_eutectic_silicate_melt | no | yes |
| kems-137-bischof-2023 | 10.1016/j.gca.2023.08.027::experiment::an-di-1-low | kems-137-bischof-2023::bischof_2023_gao15_gamma_s2_1low_isotherm / equipment.starting_glass_wt_pct | kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1695.4:h=eec90a0eba81 | p_partial | Ga | measured_direct | system_as_printed: (not supplied); notes: (not supplied); phase: anorthite_diopside_eutectic_silicate_melt | no | yes |
| llnl-2021-cai-aerodynamic-levitation | 9b5da9588d7a8a5c096c8f6ded983435c30a2876fdd6b5d84c73bada9f2f8973::llnl-2021-cai-aerodynamic-levitation | llnl-2021-cai-aerodynamic-levitation::ryerson_2022_CAI_starting_and_evaporated_composition_table1 / values.composition_wt_pct | llnl-2021-cai-aerodynamic-levitation::ryerson_2022_aerodynamic_transport_conditions | unknown | B_type_CAI | measured_direct | system_as_printed: (not supplied); notes: Values labelled assumed, typical, order-of-magnitude, or calculated remain distinct from direct measurements.; phase: glass | no | yes |
| llnl-2021-cai-aerodynamic-levitation | 9b5da9588d7a8a5c096c8f6ded983435c30a2876fdd6b5d84c73bada9f2f8973::llnl-2021-cai-aerodynamic-levitation | llnl-2021-cai-aerodynamic-levitation::ryerson_2022_CAI_starting_and_evaporated_composition_table1 / values.composition_wt_pct | llnl-2021-cai-aerodynamic-levitation::ryerson_2022_fractionation_factors_table2::rows:h=3449c34e85e3 | unknown | B_type_CAI | measured_reduced | system_as_printed: (not supplied); notes: (not supplied); phase: glass | no | yes |
| llnl-2021-cai-aerodynamic-levitation | 9b5da9588d7a8a5c096c8f6ded983435c30a2876fdd6b5d84c73bada9f2f8973::llnl-2021-cai-aerodynamic-levitation | llnl-2021-cai-aerodynamic-levitation::ryerson_2022_CAI_starting_and_evaporated_composition_table1 / values.composition_wt_pct | llnl-2021-cai-aerodynamic-levitation::ryerson_2022_fractionation_factors_table2::rows:h=37c913db6e7b | unknown | B_type_CAI | measured_reduced | system_as_printed: (not supplied); notes: (not supplied); phase: glass | no | yes |
| llnl-2021-cai-aerodynamic-levitation | 9b5da9588d7a8a5c096c8f6ded983435c30a2876fdd6b5d84c73bada9f2f8973::llnl-2021-cai-aerodynamic-levitation | llnl-2021-cai-aerodynamic-levitation::ryerson_2022_CAI_starting_and_evaporated_composition_table1 / values.composition_wt_pct | llnl-2021-cai-aerodynamic-levitation::ryerson_2022_fractionation_factors_table2::rows:h=93ba22baeed9 | unknown | B_type_CAI | measured_reduced | system_as_printed: (not supplied); notes: (not supplied); phase: glass | no | yes |
| llnl-2021-cai-aerodynamic-levitation | 9b5da9588d7a8a5c096c8f6ded983435c30a2876fdd6b5d84c73bada9f2f8973::llnl-2021-cai-aerodynamic-levitation | llnl-2021-cai-aerodynamic-levitation::ryerson_2022_CAI_starting_and_evaporated_composition_table1 / values.composition_wt_pct | llnl-2021-cai-aerodynamic-levitation::ryerson_2022_fractionation_factors_table2::rows:h=e710ee1b2aba | unknown | B_type_CAI | measured_reduced | system_as_printed: (not supplied); notes: (not supplied); phase: glass | no | yes |
| llnl-2021-cai-aerodynamic-levitation | 9b5da9588d7a8a5c096c8f6ded983435c30a2876fdd6b5d84c73bada9f2f8973::llnl-2021-cai-aerodynamic-levitation | llnl-2021-cai-aerodynamic-levitation::ryerson_2022_CAI_starting_and_evaporated_composition_table1 / values.composition_wt_pct | llnl-2021-cai-aerodynamic-levitation::ryerson_2022_isotope_ratios_table1 | unknown | B_type_CAI | measured_direct | system_as_printed: (not supplied); notes: (not supplied); phase: glass | no | yes |
| llnl-2021-cai-aerodynamic-levitation | 9b5da9588d7a8a5c096c8f6ded983435c30a2876fdd6b5d84c73bada9f2f8973::llnl-2021-cai-aerodynamic-levitation | llnl-2021-cai-aerodynamic-levitation::ryerson_2022_CAI_starting_and_evaporated_composition_table1 / values.composition_wt_pct | llnl-2021-cai-aerodynamic-levitation::ryerson_2022_modified_HKL_saturation_factor | unknown | B_type_CAI | model_derived | system_as_printed: (not supplied); notes: p_i and p_i,eq were not measured; saturation factors were constrained from isotope data using Eq. 19.; phase: glass | no | no |

### Rows that would lose the sample fallback under the proposed rule

- `kems-010-richter-2007`: 14 observations — `kems-010-richter-2007::richter_2007_al_non_loss_until_mg_exhausted`, `kems-010-richter-2007::richter_2007_al_quoted_non_loss_bound`, `kems-010-richter-2007::richter_2007_ca_non_loss_until_mg_exhausted`, `kems-010-richter-2007::richter_2007_ca_quoted_non_loss_bound`, `kems-010-richter-2007::richter_2007_mg_cai_langmuir_alpha_arrhenius`, `kems-010-richter-2007::richter_2007_mg_quoted_fits`, `kems-010-richter-2007::richter_2007_sio_cai_langmuir_alpha_arrhenius`, `kems-010-richter-2007::richter_2007_sio_quoted_fits`, `kems-010-richter-2007::richter_2007_table2_3537_1`, `kems-010-richter-2007::richter_2007_table2_e107`, `kems-010-richter-2007::richter_2007_table2_f2_ts65`, `kems-010-richter-2007::richter_2007_table2_golfball`, `kems-010-richter-2007::richter_2007_table2_ts33`, `kems-010-richter-2007::richter_2007_table2_ts34`
- `kems-015-hashimoto-1983`: 19 observations — `kems-015-hashimoto-1983::hashimoto_1983_alpha_hertz_knudsen_assumed_unity_ratio_quoted`, `kems-015-hashimoto-1983::hashimoto_1983_cao_al2o3_residue_enrichment`, `kems-015-hashimoto-1983::hashimoto_1983_derived_feo_activation_energy_quoted`, `kems-015-hashimoto-1983::hashimoto_1983_derived_mgo_activation_energy`, `kems-015-hashimoto-1983::hashimoto_1983_derived_mgo_activation_energy_quoted`, `kems-015-hashimoto-1983::hashimoto_1983_derived_sio2_activation_energy`, `kems-015-hashimoto-1983::hashimoto_1983_derived_sio2_activation_energy_quoted`, `kems-015-hashimoto-1983::hashimoto_1983_fcmas_qualitative_volatility_order`, `kems-015-hashimoto-1983::hashimoto_1983_mg_fcmas_free_evap_geometry`, `kems-015-hashimoto-1983::hashimoto_1983_mg_geometry_class_b1`, `kems-015-hashimoto-1983::hashimoto_1983_sio_fcmas_free_evap_geometry`, `kems-015-hashimoto-1983::hashimoto_1983_sio_geometry_class_b1`, `kems-015-hashimoto-1983::hashimoto_1983_stage_iv_cao_relative_volatility`, `kems-015-hashimoto-1983::hashimoto_1983_table1_starting_composition`, `kems-015-hashimoto-1983::hashimoto_1983_table6_feo_vaporization_enthalpies_quoted`, `kems-015-hashimoto-1983::hashimoto_1983_table6_mgo_vaporization_enthalpies`, `kems-015-hashimoto-1983::hashimoto_1983_table6_mgo_vaporization_enthalpies_quoted`, `kems-015-hashimoto-1983::hashimoto_1983_table6_sio2_vaporization_enthalpies`, `kems-015-hashimoto-1983::hashimoto_1983_table6_sio2_vaporization_enthalpies_quoted`
- `kems-137-bischof-2023`: 11 observations — `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1586.4:h=d7dddfc9ba1e`, `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1598.0:h=2cd4bd7cfe96`, `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1608.6:h=b9dae2d4d4f2`, `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1619.2:h=d2f65843657e`, `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1630.8:h=32f9208ca45e`, `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1642.5:h=3d5e1b4780de`, `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1653.0:h=917fb8de71ee`, `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1664.7:h=8dd7cd739c2c`, `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1674.2:h=937c91dd605a`, `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1684.8:h=56d20ce145ed`, `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1695.4:h=eec90a0eba81`
- `llnl-2021-cai-aerodynamic-levitation`: 7 observations — `llnl-2021-cai-aerodynamic-levitation::ryerson_2022_aerodynamic_transport_conditions`, `llnl-2021-cai-aerodynamic-levitation::ryerson_2022_fractionation_factors_table2::rows:h=3449c34e85e3`, `llnl-2021-cai-aerodynamic-levitation::ryerson_2022_fractionation_factors_table2::rows:h=37c913db6e7b`, `llnl-2021-cai-aerodynamic-levitation::ryerson_2022_fractionation_factors_table2::rows:h=93ba22baeed9`, `llnl-2021-cai-aerodynamic-levitation::ryerson_2022_fractionation_factors_table2::rows:h=e710ee1b2aba`, `llnl-2021-cai-aerodynamic-levitation::ryerson_2022_isotope_ratios_table1`, `llnl-2021-cai-aerodynamic-levitation::ryerson_2022_modified_HKL_saturation_factor`

Smallest reader rule: retain a sample-level fallback only for an explicit experiment/sample declaration or when the same map is present on every observation row in that experiment; if it is present on a strict subset, keep it local to those rows. This would remove the inherited sample route only from the listed Class B rows; their own row-local/identity maps are unaffected.

## Artifacts

- `/workspace/b718-sweep/class-b-rows.csv` — Class B inherited rows in the requested column order.
- `/workspace/b718-sweep/sweep.py` — instrumentation script used for this sweep.
