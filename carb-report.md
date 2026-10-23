# Car B assembly report

Pinned assembly tip: `3986008f58e925ac7e81e42598055b916c565e68`

Branch: `worktree/carb-asm-1022`

Base: `d8c8943430faf7f527c4bc005e71ec0a4757d998`

Fifty reviewed cars are merged onto that base. The manifest commit is the pinned tip. No derived store is committed (d-255). `git diff d8c894343 HEAD -- data/battery data/literature/extracts-v2 data/literature/works data/literature/INDEX.* data/literature/SOURCE_STATUS.*` is empty. Dry-run migration output was discarded with `git checkout -- data/`.

## Manifest summary

Rule: a later ledger line wins when a car is named twice or a tip is superseded. Canonical order is ledger 1819, then the t-cars in resume rev 45, then F29–F38c, then offer 22. The offer-21 re-type step was not applied. The brief forbids changing extracted values.

Merged (50): F15, t-1410 (carries F16, replaces o21fixA), t-1367, F17, F18, F19, F20, t-1372, t-1374, shorn26b (carries w2 and F21), t1382neuman, F22, F23, t1382morris3, F24, t1382he3, F25, F26, F27, o21fixB2 (replaces both F28 tips), t-1358b, t-1358c, t-1358d, t-1359, t-1362, gate2d, t-1361, t-1363b, t-1353h, b-954, t-1366, F29, F30, F31, F32, F33, F34, F35b, F36b, F37b, F38c, r17a3, r17b4, L1, L1d, L2b, resox, t1415a2, t1415b2, berman88c. Full shas and result commits are in `carb-manifest.tsv`.

Skipped already on base: dg46 `7b67f2b68afdbd1f2e2662dbf0cf695cdeb722d2` (ancestor of d8c894343).

Held out:

- L3c `be4de18a771f2712fe08a867df98a1295a0ce043`: add/add on `data/literature/extracts/wang-2001-solar-melt-evaporation-residues.yaml`. Merge aborted. resox still merged after it, as the plan orders.
- kc91b `a4ef7adde4c4ec1f02331bcdd3bd938b23933d97`: t-1442, duplicates Kress-Carmichael 1991. Not merged.
- L4b `1dc54a96fe1143de8a8f0fe8d4bdd155c4910cc5`: t-1442, duplicates Mendybaev 2013. Not merged.

Excluded, later list omits them:

- t-1357b `69bab2e28dc286ece08a99f8bd2bdb7abf22b3cb`: lands after offer 21 on its own car.
- t-1380 `81f5a34e861c6e3cc76eb67fa3a5a2c87d13a100`: ledger 1698 accepted it into offer 21. Ledger 1819 and the brief omit it.
- t1317 `d5407cb3b7962f23bbe8412b3bfc87a7da9c4bc4`: not in the later lists.
- F13 `936f045e13d321224ec8b37d20c9ab739bb81782`: ledger 1819 starts at F15 on base F14 and does not name F13. F13 is not an ancestor of F14, F15, or this tip. The F15 merge message says ancestry brings F13. That claim is false. These extracts are absent here: pretorius-muan-1992, navrotsky-koryttseva-2023, van-westrenen-1999, smythe-2013, cohen-2006.

t-1374 is merged. Ledger 1819's semicolon list omits the name and does not retract ledger 1703, which accepted it into offer 21. The brief lists it.

Ledger 1470 labels sha `ddf175940` as ror-t1358c. That sha is the borisov capture merged as t-1358b. t-1358c is `c00db1723` (Aithala). Its parent WIP `33d42c220` stays in history.

## Conflicts and resolutions

Derived store paths were restored to the pre-merge side on every merge that touched them (d-255). Extract rows were not re-encoded. Code conflicts kept both sides' tests and rules.

- F15 `2544107c9`: QUANTITY_SOURCE_FIELDS kept the pre-merge superset (residue_ppm, oxide wt%, ISOTOPE_DELTA). The shornikov alumina test kept the pre-merge migrator import and F15's Quantity import plus composition-promotion assertions.
- t-1410 `0dc2df1da`: residue branch copies composition, total_pressure_Pa, sample_mass_kg, and duration_h onto the identity, then the pre-merge PHASE_ROW block still ingests printed atmosphere and printed_sum.
- F20 `23a54e9b3`: kept pre-merge subprocess and pytest imports and F20's corpus_root(). The two corpus lookups stay. Their defaults differ.
- shorn26b `d357e86db`: kept subprocess and pytest imports and shorn26b's ValueKind, Observation, and Iterable imports. Both helpers stay.
- F25 `f07058ca1`: kept both initializers (malformed_fraction_bound and density_refusal). Dropped F25's later composition-role stamp, which would also stamp datum_state and queue the row twice. The density converter stays.
- o21fixB2 `33f08227b`: kept malformed_fraction_bound. o21fixB2 declared neither local.
- t-1361 `0c77ddb11`: kept gate2d's `_composition_issues`. Did not revive `_composition_basis_issues` or `_formula_count`. Identity check is `_transition_mixture_identity_issues` (ledger 1510 and 1516). The species-misfile fixture gained basis_as_published `TiO2 equilibrium wt.%` so the row stays on the equilibrium-ratio reader. Migrated species stays SiO2.
- t-1363b `4cec870a9`: both test blocks kept. Names do not overlap.
- t-1353h `c13a5976e`: kept the soft locator-verification check and `_composition_issues`. shornikov-2018 sample.form stays condensed_solid. page is 1 because the hard check requires page = pdf_page_index + 1 (pdf index 0, published page 1447).
- b-954 `de71c1943`: kept both `import importlib.util` and `import json`.
- F32 `bfd5ecf5e`: kept both selection initializers.
- r17a3 `4679aa7d9`: `kems-012-sossi-2019.yaml` stayed at the pre-merge text. r17a3's locator hunks target the pre-split document and do not line up with the t-1363b Pb-channel rows. No printed value was rewritten.
- L1 `40d6a298c`: observation-level T_range_K on the Markova VI and V residual-melt rows stays off. Repair `042d48b56` removed those ranges. Restoring them would state the point-row temperatures twice.
- L2b `5fcb91c3b`: import union of AdmissionStatus, EvidenceClass, NoticeKind, Quantity, ValueKind, and check_all_fidelity_samples_match.
- resox `5344ed0d2`: emitter stays the pre-merge six-slot ordinal (point_stride 6: residue_ppm, SiO2, Al2O3, FeO, MgO, CaO). Appended oxides keep field-and-row ids. resox's QUANTITY_SOURCE_FIELDS names stay: TiO2, Cr2O3, MnO, Na2O, K2O, P2O5, Fe2O3, WO3. The unused stride constant was not kept. Both test blocks kept. Eleven residue tests passed at the merge.

## Store delta the train should regenerate

Dry-run `migrate(root, write=False)` on this seat and on a detached worktree at d8c894343. Hard issues are `validation.hard_issues`. Every issue path attributed to a source. Unattributed was 0 on both runs. Observation ids use the `source_id::local` prefix. Experiment and work issues follow the observation's experiment.

| | base | tip | delta |
|---|---:|---:|---:|
| rows_in | 51641 | 61229 | +9588 |
| observations | 128946 | 146379 | +17433 |
| hard_issues | 3278 | 6033 | +2755 |
| works | 371 | 604 | +233 |
| queue | 88695 | 97177 | +8482 |

169 sources change rows, observations, or hard issues. Hard issues change on 27 sources. Net hard +2755 = referential_integrity +2842 and conditional_field −87.

### Hard-issue delta by source

Axes are the last segment of the issue path. `conditional_field:derived_from` is "derived observation requires derived_from". `conditional_field:derivation` is "derived observation requires derivation". Those two are the b-767 model_derived-without-lineage check. `referential_integrity:derived_from` is a parent id that does not resolve. `referential_integrity:inputs` is a derivation input that does not resolve.

| delta | source | kinds | car | in that car's reviewed delta? |
|---:|---|---|---|---|
| +2787 | `borisov-2008-k-na-miscible-liquids.yaml` | referential_integrity:inputs +1357, referential_integrity:derived_from +1357, conditional_field:derived_from +73 | F18 `5c9fb4246` | No hard count on the LAND line. Ledger 1569 records 332 rows for borisov 2008, not these issues. |
| +147 | `neuman-2026-levitation-k-isotopes.yaml` | referential_integrity:inputs +49, referential_integrity:derived_from +49, conditional_field:derived_from +49 | shorn26b then t1382neuman | Not inside the reviewed +293 w2 figure. No separate neuman hard count in the ledger. |
| +84 | `shornikov-2026-wollastonite-kems.yaml` | conditional_field:derived_from +80, conditional_field:derivation +4 | shorn26b / w2 | Yes. The six w2 Shornikov files sum to +293, matching ledger 1790. |
| +60 | `metsoc-2019-6005.yaml` | conditional_field:derivation +30, conditional_field:derived_from +30 | shorn26b / w2 | Yes, part of the +293. |
| +60 | `shornikov-2008-zinc-phosphate-melts.yaml` | conditional_field:derivation +30, conditional_field:derived_from +30 | shorn26b / w2 | Yes, part of the +293. |
| +54 | `shornikov-1997-cao-al2o3.yaml` | conditional_field:derived_from +27, conditional_field:derivation +27 | t-1372 `9a3d42c71` | Yes. These four t-1372 files sum to +76, matching ledger 1638. |
| +50 | `shornikov-2014-sodium-germanate-melts.yaml` | conditional_field:derivation +25, conditional_field:derived_from +25 | shorn26b / w2 | Yes, part of the +293. |
| +36 | `borisov-2004-tio2-pd-ni-fe-solubilities.yaml` | referential_integrity:derived_from +12, conditional_field:derived_from +12, referential_integrity:inputs +12 | F18 `5c9fb4246` | No hard count on the LAND line. Ledger 1569 records 57 rows. |
| +30 | `shornikov-1999-silica-partial-vaporization-coefficients.yaml` | conditional_field:derived_from +15, conditional_field:derivation +15 | shorn26b / w2 | Yes, part of the +293. |
| +9 | `shornikov-2005-potassium-germanate.yaml` | conditional_field:derived_from +7, conditional_field:derivation +2 | shorn26b / w2 | Yes, part of the +293. 84+60+60+50+30+9 = 293. |
| +8 | `shornikov-2017-cai-mgo-sio-fugacity.yaml` | conditional_field:derived_from +4, conditional_field:derivation +4 | t-1372 `9a3d42c71` | Yes, part of the +76. |
| +8 | `shornikov-2015-cai-vaporization-coefficients.yaml` | conditional_field:derivation +4, conditional_field:derived_from +4 | t-1372 `9a3d42c71` | Yes, part of the +76. |
| +6 | `shornikova-2019-perovskite-thermodynamics.yaml` | conditional_field:derived_from +3, conditional_field:derivation +3 | t-1372 `9a3d42c71` | Yes, part of the +76. 54+8+8+6 = 76. |
| +6 | `he-2017-karrooite-ordering-heat-capacity.yaml` | referential_integrity:inputs +2, referential_integrity:derived_from +2, conditional_field:derived_from +2 | F24 and t1382he3 | No hard count in the ledger lines read. |
| +2 | `langmuir_knudsen_flux_validation.yaml` | referential_integrity:derived_from +2 | no extract merge in this assembly | Migrator change only. Not a reviewed extract delta. |
| -1 | `arxiv-2509-sio-flame-nucleation.yaml` | conditional_field:derivation -1 | no extract merge in this assembly | Migrator change only. |
| -2 | `kems-029-yakovlev-shornikov-2011.yaml` | conditional_field:derivation -1, conditional_field:derived_from -1 | F15 | Not stated as a hard budget. |
| -2 | `itoh-hino-banya-1998-spinel.yaml` | conditional_field:derivation -2 | o21fixB2 | Ledger 1790 records -2 itoh-hino-banya on the pre-reconciliation tip. Same magnitude. |
| -8 | `kems-026-markova-1984.yaml` | conditional_field:derivation -4, conditional_field:derived_from -4 | r17a3 and L1d | Not stated as a hard budget. L1 left the observation-level ranges off. |
| -8 | `kems-012-sossi-2019.yaml` | conditional_field:derived_from -8 | t-1363b | Not stated as a hard budget. r17a3 did not rewrite this file. |
| -9 | `kems-021-plante-1992-feo.yaml` | conditional_field:derived_from -9 | t-1353h and r17b4 | Not stated as a hard budget. |
| -16 | `kems-005-fedkin-2006.yaml` | conditional_field:derivation -4, conditional_field:derived_from -12 | t-1353h and r17a3 | Not stated as a hard budget. |
| -16 | `guo-2021-mgo-activity-cmas-slag.yaml` | conditional_field:derived_from -16 | r17b4 | Not stated as a hard budget. |
| -16 | `kems-010-richter-2007.yaml` | conditional_field:derivation -8, conditional_field:derived_from -8 | r17a3 | Not stated as a hard budget. |
| -24 | `kems-015-hashimoto-1983.yaml` | conditional_field:derivation -12, conditional_field:derived_from -12 | r17a3 | Not stated as a hard budget. |
| -55 | `stolyarova-1996-cao-alumina-silica-kems.yaml` | conditional_field:derived_from -55 | t-1353h and t1415a2 | Not stated as a hard budget. |
| -435 | `borisov-2018-compilation.yaml` | conditional_field:derived_from -435 | t-1410 `ff19b2adb` | Ledger 1816 records Mendybaev 0 hard on this car, not this file's lineage drop. |

The borisov-2008 increase is not the b-767 missing-lineage check. 2714 of 2787 issues are dangling `derived_from` parents and dangling derivation inputs (1357 each). 73 are missing `derived_from`. F18's LAND line does not say this was already accepted as a hard budget.

### Per-source observation and row delta

Sorted by observation delta. The hard column is the attributed count.

| source | d rows | d obs | d hard |
|---|---:|---:|---:|
| `neuman-2026-levitation-k-isotopes.yaml` | +80 | +2307 | +147 |
| `rys-2007-alkali-oxide-thermodynamics.yaml` | +56 | +1001 | +0 |
| `cheng-2021-fe-ca-si-o-phase-equilibria.yaml` | +990 | +990 | +0 |
| `kress-carmichael-1991-iron-redox-calibration.yaml` | +881 | +881 | +0 |
| `gran-sichen-2012-molten12-ladle-slag-liquidus.yaml` | +219 | +876 | +0 |
| `klemme-2011-cobalt-nickel-titanate-carbonate-heat-capacity.yaml` | +14 | +827 | +0 |
| `chen-2019-sioch-cp-entropy-gibbs.yaml` | +33 | +760 | +0 |
| `sineva-2023-ni-sn-zn-slag-copper.yaml` | +696 | +696 | +0 |
| `wang-2001-solar-melt-evaporation-residues.yaml` | +38 | +538 | +0 |
| `inoue-1991-n-s-partition-cao-al2o3.yaml` | +446 | +446 | +0 |
| `hanson-jones-1998-am-cr-partitioning-olivine-liquid.yaml` | +98 | +400 | +0 |
| `tranell-2000-silica-activity-titanium.yaml` | +106 | +367 | +0 |
| `holzheid-lodders-2001-copper-solubility.yaml` | +321 | +321 | +0 |
| `shornikov-2000-titania-melts.yaml` | +141 | +301 | +0 |
| `shornikov-2026-wollastonite-kems.yaml` | +194 | +277 | +84 |
| `shornikov-2019-perovskite-evaporation-thermodynamics.yaml` | +263 | +263 | +0 |
| `xiao-2002-chromium-oxide-activities.yaml` | +261 | +261 | +0 |
| `berman-1988-internally-consistent-mineral-thermodynamic-data.yaml` | +219 | +219 | +0 |
| `chen-2022-mgo-sio2-tio2-phase-equilibria.yaml` | +198 | +198 | +0 |
| `lopatin-2023-na-barium-borate-kems.yaml` | +10 | +186 | +0 |
| `tyurnina-lopatin-stolyarova-2008-ba-borosilicate-kems.yaml` | +91 | +181 | +0 |
| `borisov-2009-alkali-activity-coefficients.yaml` | +169 | +169 | +0 |
| `yakovlev-2024-cai-vaporization.yaml` | +11 | +167 | +0 |
| `yakovlev-2017-cai-acidity.yaml` | +97 | +166 | +0 |
| `shornikov-2020-mgo-feo-melts.yaml` | +146 | +146 | +0 |
| `ozawa-2012-forsterite-vacuum-evaporation.yaml` | +5 | +134 | +0 |
| `shornikova-2019-perovskite-thermodynamics.yaml` | +119 | +119 | +6 |
| `zaitsev-1999-na2o-sio2-pccp.yaml` | +11 | +116 | +0 |
| `shornikov-2011-titania-melt-evaporation.yaml` | +51 | +114 | +0 |
| `zaitsev-2000-sodium-silicate-thermodynamics.yaml` | +7 | +110 | +0 |
| `kems-029-yakovlev-shornikov-2011.yaml` | +12 | +107 | -2 |
| `shornikov-2000-aluminosilicate-thermodynamics.yaml` | +6 | +105 | +0 |
| `jeon-lindberg-2023-mg-ca-zn-oxide-equilibria.yaml` | +102 | +102 | +0 |
| `florin-2025-cu-silicate-evaporation.yaml` | +4 | +99 | +0 |
| `mendybaev-2013-fun-cai-evaporation.yaml` | +2 | +96 | +0 |
| `shornikov-2003-mullite-thermodynamics.yaml` | +94 | +94 | +0 |
| `shornikov-2016-cao-mgo-silicate.yaml` | +50 | +85 | +0 |
| `saito-2021-fexo-cao-silica-activity.yaml` | +84 | +84 | +0 |
| `balabanova-lopatin-tyurnina-2025-sro-bao-al2o3-sio2-kems.yaml` | +83 | +83 | +0 |
| `kambayashi-kato-1984-mgo-sio2-1973k.yaml` | +77 | +77 | +0 |
| `lu-2026-mercury-sulfur-liquidus.yaml` | +77 | +77 | +0 |
| `shornikov-2020-cao-mgo-thermodynamics.yaml` | +77 | +77 | +0 |
| `borisov-2008-k-na-miscible-liquids.yaml` | +332 | +73 | +2787 |
| `borisov-behrens-holtz-2015-melt-composition-fe-redox.yaml` | +72 | +72 | +0 |
| `shornikov-2008-mgo-aluminosilicate-melts.yaml` | +70 | +70 | +0 |
| `kems-028-yakovlev-1984.yaml` | +8 | +69 | +0 |
| `jakobsson-tangstad-2015-ca-mg-activities.yaml` | +68 | +68 | +0 |
| `shornikov-2019-ca-feo-vapor.yaml` | +34 | +68 | +0 |
| `shornikov-2019-perovskite-evaporation-rjpc.yaml` | +6 | +68 | +0 |
| `itoh-1989-zno-solid-zinc-silicate.yaml` | +67 | +67 | +0 |
| `shornikov-2019-mgo-titania-melts.yaml` | +63 | +63 | +0 |
| `borisov-behrens-holtz-2013-ti-p-fe-redox.yaml` | +62 | +62 | +0 |
| `morishita-2004-sio2-partial-enthalpy.yaml` | +7 | +61 | +0 |
| `shornikov-2011-cao-mgo-alumina-melts.yaml` | +60 | +60 | +0 |
| `tyurnina-2023-sro-al2o3-sio2-vaporization.yaml` | +60 | +60 | +0 |
| `borisov-2017-network-modifiers-fe-redox.yaml` | +58 | +58 | +0 |
| `park-morita-2025-vanadium-activity.yaml` | +56 | +56 | +0 |
| `shornikov-2025-perovskite-condensation.yaml` | +54 | +54 | +0 |
| `tachibana-2002-enstatite-evaporation-kinetics.yaml` | +6 | +54 | +0 |
| `bissbort-2026-depolymerized-silicate-glass-transition-viscosity.yaml` | +53 | +53 | +0 |
| `shornikov-2015-cai-vaporization-coefficients.yaml` | +53 | +53 | +8 |
| `gaillard-2003-hydrogen-iron-redox-rate.yaml` | +52 | +52 | +0 |
| `lee-bruckner-1986-borate-redox-series.yaml` | +14 | +52 | +0 |
| `kemori-1980-oxygen-liquid-copper.yaml` | +31 | +51 | +0 |
| `kems-055-kambayashi-1983.yaml` | +50 | +50 | +0 |
| `shugurov-2026-ceo2-tio2-kems.yaml` | +50 | +50 | +0 |
| `shornikov-2003-aluminosilicate-solids.yaml` | +7 | +47 | +0 |
| `shornikov-2005-potassium-germanate.yaml` | +46 | +46 | +9 |
| `nekhoroshev-2024-cao-sio2-thermodynamic-reoptimization.yaml` | +45 | +45 | +0 |
| `leister-1999-fe-v-redox-2000c.yaml` | +5 | +44 | +0 |
| `tranell-1999-unsw-thesis.yaml` | +30 | +44 | +0 |
| `kems-214-hastie-1980-nbsir-80-2178.yaml` | +4 | +43 | +0 |
| `ferguson-nuth-2008-sio-vapor-pressure-evaporation-coefficient.yaml` | +1 | +42 | +0 |
| `zaitsev-1997-cas-caf2-melts.yaml` | +4 | +42 | +0 |
| `borisov-2004-karrooite-liquidus-redox.yaml` | +41 | +41 | +0 |
| `shornikov-2014-sodium-germanate-melts.yaml` | +40 | +40 | +50 |
| `wright-okamura-1977-makaopuhi-cooling.yaml` | +40 | +40 | +0 |
| `shornikov-1997-cao-al2o3.yaml` | +37 | +37 | +54 |
| `renggli-2022-te-evaporation.yaml` | +1 | +36 | +0 |
| `shi-2024-cr-activity-slag-polymerisation.yaml` | +36 | +36 | +0 |
| `shornikov-2003-silica-catitanoate-melts.yaml` | +36 | +36 | +0 |
| `lee-bruckner-1984-cr-mn-glass-redox.yaml` | +5 | +35 | +0 |
| `shornikov-2008-zinc-phosphate-melts.yaml` | +34 | +34 | +60 |
| `leister-1999-v-sodium-silicate-high-temperature.yaml` | +7 | +32 | +0 |
| `metsoc-2019-6005.yaml` | +30 | +30 | +60 |
| `shornikov-2017-spinel-thermodynamics.yaml` | +18 | +30 | +0 |
| `shornikov-2002-mullite-thermodynamics.yaml` | +29 | +29 | +0 |
| `shornikov-2025-ca-al-inclusion-thermodynamics.yaml` | +29 | +29 | +0 |
| `costa-2025-ree-cmas-glass-melt-thermochemistry.yaml` | +28 | +28 | +0 |
| `shornikov-2007-cao-aluminosilicate-melts.yaml` | +0 | +28 | +0 |
| `lu-2024-mgsio3-levitation.yaml` | +1 | +27 | +0 |
| `shornikov-2015-cai-mineral-thermodynamics.yaml` | +27 | +27 | +0 |
| `de-abreu-2024-li2o-sio2-thermochemistry.yaml` | +26 | +26 | +0 |
| `shornikov-2000-silica-gas-equilibria.yaml` | +26 | +26 | +0 |
| `matsuzaki-1997-feo-tio2-sio2-activities.yaml` | +25 | +25 | +0 |
| `majzlan-2021-magnesium-silicate-glass-cp.yaml` | +23 | +23 | +0 |
| `shornikov-1997-cao-alumina-vapor.yaml` | +8 | +23 | +0 |
| `volovetsky-2010-granitoid-iron-redox.yaml` | +22 | +22 | +0 |
| `liu-li-2025-se-te-sulfide-silicate.yaml` | +18 | +18 | +0 |
| `rzehak-2021-ti-isotope-redox-lunar-melts.yaml` | +3 | +18 | +0 |
| `shornikov-2024-iron-phosphate-melts.yaml` | +17 | +17 | +0 |
| `bouhifd-2007-basalt-thermochemistry.yaml` | +16 | +16 | +0 |
| `yokokawa-1984-alumina-carbon-volatilization.yaml` | +16 | +16 | +0 |
| `kems-012-sossi-2019.yaml` | +1 | +15 | -8 |
| `kems-213-schneider-1980-nbsir-80-2139.yaml` | +15 | +15 | +0 |
| `shornikov-1999-silica-partial-vaporization-coefficients.yaml` | +15 | +15 | +30 |
| `ehrt-2008-sn-redox-aluminosilicate.yaml` | +8 | +13 | +0 |
| `shornikov-2001-mgalumina-silica-vaporization.yaml` | +0 | +13 | +0 |
| `shornikov-2024-sodium-phosphate-melts.yaml` | +13 | +13 | +0 |
| `arutyunyan-2005-magnesium-silicate-activities.yaml` | +2 | +12 | +0 |
| `borisov-2004-tio2-pd-ni-fe-solubilities.yaml` | +57 | +12 | +36 |
| `ferkl-2026-waste-glass-thermal-redox.yaml` | +2 | +12 | +0 |
| `kawashima-1984-soda-flux-chemical-sensors.yaml` | +3 | +12 | +0 |
| `baborak-2023-alkali-silicate-levitation-evaporation.yaml` | +11 | +11 | +0 |
| `shornikov-2000-na-zno-phosphate-vaporization.yaml` | +11 | +11 | +0 |
| `itoh-1985-beta-alumina-na2o-emf.yaml` | +9 | +9 | +0 |
| `schreiber-1977-plsc8-redox-ti-zr-hf-cr-eu.yaml` | +8 | +8 | +0 |
| `schreiber-haskin-1976-plsc7-chromium-in-basalts.yaml` | +8 | +8 | +0 |
| `shornikov-2007-ca-aluminosilicate-activities.yaml` | +8 | +8 | +0 |
| `shornikov-2023-silica-phosphate-melts.yaml` | +7 | +7 | +0 |
| `jackson-2005-ferrous-glass-multispectroscopy.yaml` | +6 | +6 | +0 |
| `beerkens-2001-industrial-glass-evaporation.yaml` | +5 | +5 | +0 |
| `shornikov-2020-perovskite-chemical-fractionation.yaml` | +5 | +5 | +0 |
| `shornikov-2023-mgo-phosphate-melts.yaml` | +5 | +5 | +0 |
| `jin-2022-cl-cr-sulfur-glass-solubility.yaml` | +1 | +4 | +0 |
| `myers-jacobson-2015-volatile-metal-hydroxides-presentation.yaml` | +4 | +4 | +0 |
| `raberg-2007-salt-stability-na2s-thermochemistry-thesis.yaml` | +4 | +4 | +0 |
| `romero-serrano-1999-binary-ternary-silicates.yaml` | +4 | +4 | +0 |
| `shornikov-2002-aluminosilicate-solid-phases.yaml` | +3 | +4 | +0 |
| `shornikov-2017-cai-mgo-sio-fugacity.yaml` | +4 | +4 | +8 |
| `shornikov-2025-lithium-silicate-melts.yaml` | +4 | +4 | +0 |
| `sugie-2021-sioc-mixed-bond-calorimetry.yaml` | +4 | +4 | +0 |
| `folstad-2024-metals-cao-dissolution-cas.yaml` | +3 | +3 | +0 |
| `gribchenkova-2017-al2o3-mgo.yaml` | +3 | +3 | +0 |
| `rb86-capobianco-navrotsky-1982.yaml` | +3 | +3 | +0 |
| `schreiber-1977-tetravalent-cr-diopside.yaml` | +3 | +3 | +0 |
| `shornikov-2022-na2o-al2o3-melts.yaml` | +3 | +3 | +0 |
| `shornikov-2024-alkali-phosphate.yaml` | +3 | +3 | +0 |
| `yang-teng-seetharaman-2012-vanadium-evaporation.yaml` | +3 | +3 | +0 |
| `banya-2004-sulphide-capacity-cao-al2o3.yaml` | +2 | +2 | +0 |
| `boulliung-wood-2023-sulfur-solubility.yaml` | +2 | +2 | +0 |
| `connolly-2016-si-o-liquid-vapor-caloric-vdw.yaml` | +2 | +2 | +0 |
| `he-2017-karrooite-ordering-heat-capacity.yaml` | +2 | +2 | +6 |
| `leuthold-2023-basalt-partition-fo2.yaml` | +2 | +2 | +0 |
| `matousek-1998-silicate-vaporization-reactions.yaml` | +2 | +2 | +0 |
| `shornikov-2025-lithium-phosphate-melts.yaml` | +2 | +2 | +0 |
| `shyrokykh-2021-vanadium-vaporization.yaml` | +2 | +2 | +0 |
| `avarmaa-2022-cr-don-smelting.yaml` | +1 | +1 | +0 |
| `berry-2003-in-situ-cr-xanes-furnace.yaml` | +1 | +1 | +0 |
| `costa-2015-yb-silicate-silica-activity.yaml` | +1 | +1 | +0 |
| `matsumaru-1999-plasma-arc-silicate-evaporation.yaml` | +1 | +1 | +0 |
| `shornikov-2022-k2o-al2o3-melts.yaml` | +1 | +1 | +0 |
| `arxiv-2509-sio-flame-nucleation.yaml` | +0 | +0 | -1 |
| `borisov-2018-compilation.yaml` | +0 | +0 | -435 |
| `jeon-2025-metal-slag-refractory-thesis.yaml` | +330 | +0 | +0 |
| `jeon-lindberg-2025-mgo-refractory-slag-interface.yaml` | +203 | +0 | +0 |
| `kems-021-plante-1992-feo.yaml` | +0 | +0 | -9 |
| `langmuir_knudsen_flux_validation.yaml` | +0 | +0 | +2 |
| `oishi-1986-cu2o-cao-activities.yaml` | +48 | +0 | +0 |
| `stolyarova-1996-cao-alumina-silica-kems.yaml` | +0 | +0 | -55 |
| `zaitsev-2000-ms2000-045.yaml` | -1 | -1 | +0 |
| `kems-037-richter-2002.yaml` | -4 | -4 | +0 |
| `kems-026-markova-1984.yaml` | -6 | -6 | -8 |
| `guo-2021-mgo-activity-cmas-slag.yaml` | -1 | -8 | -16 |
| `itoh-hino-banya-1998-spinel.yaml` | -8 | -8 | -2 |
| `kems-010-richter-2007.yaml` | -10 | -10 | -16 |
| `kems-015-hashimoto-1983.yaml` | -15 | -15 | -24 |
| `kems-016-stolyarova-1992.yaml` | -9 | -22 | +0 |
| `kems-005-fedkin-2006.yaml` | -14 | -27 | -16 |

Nine sources lose observations. The files still exist. The drops come from the reviewed cars' own edits, not from a conflict that discarded both sides: kems-005 (−27 obs, t-1353h and r17a3), kems-016 (−22, r17b4), kems-037 (−4, r17a3), kems-015 (−15, r17a3), kems-010 (−10, r17a3), itoh-hino-banya-1998 (−8, o21fixB2), guo-2021 (−8, r17b4), kems-026-markova (−6, r17a3 and L1d), zaitsev-2000 (−1, r17b4).

## Validator

Base: `OK: 369 extract file(s) valid`, exit 0.

Tip: exit 1, two errors, both absent on the base tree:

- `shornikov-2000-titania-melts.yaml`: fidelity_samples required when observations are present
- `yakovlev-2017-cai-acidity.yaml`: fidelity_samples required when observations are present

Both files arrived through F20 `23a54e9b3` and were updated by t-1374 `a733c7e04` (worker `t1374-fig-t1`). Neither source_id is in `_fidelity_pre_policy_allowlist.yaml`.

### Required fidelity change (not applied)

The requirement was added by `99d2b1553163342c6d7d360de99552caf435cb76` (2026-08-03, per-extract fidelity gate). Policy `ENFORCED_FOR_NEW` in `data/literature/extracts/SCHEMA.md` and `_fidelity_pre_policy_allowlist.yaml`: an extract with observations needs a non-empty `fidelity_samples` list unless its source_id is an active pre-policy id. The value must be an independent literal, not a YAML alias, equal to the stored field under type-strict match, including int versus float and quoted versus bare scalars. A path on a new extract must sit under `species.<id>.(observations|context)[<id>].(values|equipment)`. A structured sample needs `species`, `observation_id` and/or `observable`, `value` or `draft_value`, and exactly one of `locator` or `source_locator`. `field` reads one key of the observation's `values` mapping. It does not walk into `values.points`.

No data was edited. One gate-satisfying pin per file, copied from values already stored, is below. The policy still calls the pin reviewer-verified against the page. This report does not supply that check.

shornikov-2000, path form, because the printed activity sits inside `values.points` and `field` cannot address it:

```yaml
fidelity_samples:
  - path: species.SiO2.observations[shornikov_2000_f2c_SiO2_2100k].values.points[0].activity
    value: '0.01300'
    locator:
      page: 3
      published_page: 1471
      pdf_page_index: 2
      figure: '2'
      panel: c
```

yakovlev-2017, structured form. `log10_p` is a direct `values` key and is stored as a string:

```yaml
fidelity_samples:
  - species: SiO2
    observation_id: yakovlev_2017_fig2_SiO2_1_01
    observable: partial_pressure
    field: log10_p
    value: '-6.195'
    locator:
      page: 2
      published_page: 252
      pdf_page_index: 1
      figure: '2'
      series: '1'
```

## Tests

Foreground, one tree at a time, `.venv` Python, `PYTHONPATH` set to that tree, `-n 0 --timeout=300 -q -p no:cacheprovider`. Matplotlib cache and OpenMPI/PRTE temp dirs were set as briefed. `tests/battery` was split into four filename chunks. Base chunks omitted 11 test files that do not exist at d8c894343. Those files were run on the tip. `tests/test_literature_extracts.py` and `tests/test_absolute_path_ratchet.py` ran as their own chunk.

Base failures: 15. Tip failures: 78. New (tip only): 66. Present on base and absent on the tip: 3. `test_validate_corpus_zero_hard_issues_on_migrated_store` timed out at 300 s on the base run and passed on the tip. The two Stolyarova 1991 phase-field tests failed on the base and passed on the tip.

r16 and r17 store-freshness failures are the d-255 expectation. The committed store was not regenerated. They are listed with the other new failures and were not fixed.

### New failures

- `tests/battery/test_costa_2025_table3.py::test_costa_table3_migrates_composition_temperature_and_source_conditions` — KeyError: 'total_pressure_Pa'
- `tests/battery/test_kems_background_not_identity.py::test_langmuir_free_evaporation_still_inherits_experiment_total` — AssertionError: expected at least one langmuir observation to inherit experiment total
- `tests/battery/test_migrate.py::test_borisov_combined_iron_composition_migrates_without_a_redox_split` — KeyError: 'composition_mol'
- `tests/battery/test_migrate.py::test_demaria_1973_table1_temperature_and_conditions_reach_every_row` — assert (ValidationIs..._fraction'"),) == ()
- `tests/battery/test_migrate.py::test_j01_fedkin_alpha_series_not_mass_loss_rate` — assert 0 == 12
- `tests/battery/test_migrate.py::test_j01_store_census_series_numeric_matches_declared_field` — AssertionError: assert 10 == 22
- `tests/battery/test_migrate.py::test_k01_value_constructions_live_inside_the_boundary` — AssertionError: Value constructed in _named_fraction_bound_selection:6011 outside the boundary
- `tests/battery/test_migrate.py::test_k04_census_goes_red_when_stored_alpha_is_corrupted` — AssertionError: census must go red when stored alpha points are corrupted
- `tests/battery/test_migrate.py::test_plante_1992_table2_activities_migrate_with_table1_ancestry` — AssertionError: assert 3 == 1278
- `tests/battery/test_migrate.py::test_stolyarova_1996_printed_compositions_reach_migrated_observations` — AssertionError: assert 6 in {20, 23}
- `tests/battery/test_migrate.py::test_stolyarova_table3_137_content_ids_and_reference_states_are_stable` — AssertionError: assert '979291d9c0a3...5b9493f15bf97' == '1c09862823fb...ba7a32d564259'
- `tests/battery/test_migrate_composition.py::test_markova_table2_migrates_both_quantities_and_printed_charge` — AssertionError: assert 'T_range_K' in 'no single temperature_K mapped from source'
- `tests/battery/test_migrate_evaporation_extracts.py::test_sossi_fitted_outputs_are_context_and_run_measurements_keep_conditions` — assert (ValidationIs...esolve"), ...) == ()
- `tests/battery/test_migrate_r7.py::test_g1_whole_store_absence_claims_match_sources[borisov-2018-compilation.yaml]` — AssertionError: borisov-2018-compilation::borisov_2018_table2_row_155
- `tests/battery/test_migrate_r7.py::test_g1_whole_store_absence_claims_match_sources[guo-2021-mgo-activity-cmas-slag.yaml]` — AssertionError: guo-2021-mgo-activity-cmas-slag::guo_2021_table6_mg_activity_coefficients_1873k::T=1873:h=055156f0bcdd
- `tests/battery/test_migrate_r7.py::test_g1_whole_store_absence_claims_match_sources[itoh-hino-banya-1998-spinel.yaml]` — AssertionError: itoh-hino-banya-1998-spinel::itoh_1998_assumed_Ca_Mg_interaction
- `tests/battery/test_migrate_r7.py::test_g1_whole_store_absence_claims_match_sources[kems-005-fedkin-2006.yaml]` — AssertionError: kems-005-fedkin-2006::fedkin_2006_fe_class_b1
- `tests/battery/test_migrate_r7.py::test_g1_whole_store_absence_claims_match_sources[kems-010-richter-2007.yaml]` — AssertionError: kems-010-richter-2007::richter_2007_mg_cai_langmuir_alpha_arrhenius
- `tests/battery/test_migrate_r7.py::test_g1_whole_store_absence_claims_match_sources[kems-015-hashimoto-1983.yaml]` — AssertionError: kems-015-hashimoto-1983::hashimoto_1983_alpha_hertz_knudsen_assumed_unity_ratio
- `tests/battery/test_migrate_r7.py::test_g1_whole_store_absence_claims_match_sources[kems-016-stolyarova-1992.yaml]` — AssertionError: kems-016-stolyarova-1992::stolyarova_1992_al_cas_vapor_ions_and_gibbs
- `tests/battery/test_migrate_r7.py::test_g1_whole_store_absence_claims_match_sources[kems-026-markova-1984.yaml]` — AssertionError: kems-026-markova-1984::markova_1984_K_dissipation_1500C_reduced_lunar_mass_model
- `tests/battery/test_migrate_r7.py::test_g1_whole_store_absence_claims_match_sources[kems-029-yakovlev-shornikov-2011.yaml]` — AssertionError: kems-029-yakovlev-shornikov-2011::yakovlev_shornikov_2011_fig1_murchison_psat_figure_only
- `tests/battery/test_migrate_r7.py::test_g1_whole_store_absence_claims_match_sources[kems-037-richter-2002.yaml]` — AssertionError: kems-037-richter-2002::richter_2002_mg_cai_langmuir_gamma
- `tests/battery/test_migrate_r7.py::test_g1_whole_store_absence_claims_match_sources[zaitsev-2000-ms2000-045.yaml]` — AssertionError: zaitsev-2000-ms2000-045::zaitsev_2000_table1_measured_ion_signals
- `tests/battery/test_migrate_r7.py::test_g4_boundary_mutants[constructor = Value\n    return constructor(None)]` — AssertionError: Value constructed in _named_fraction_bound_selection:6011 outside the boundary
- `tests/battery/test_migrate_r7.py::test_g4_boundary_mutants[constructor: object = Value\n    return constructor(None)]` — AssertionError: Value constructed in _named_fraction_bound_selection:6011 outside the boundary
- `tests/battery/test_migrate_r7.py::test_g4_boundary_mutants[return Value(None)]` — AssertionError: Value constructed in _named_fraction_bound_selection:6011 outside the boundary
- `tests/battery/test_migrate_r7.py::test_g4_boundary_mutants[return getattr(module, "Value")(None)]` — AssertionError: Value constructed in _named_fraction_bound_selection:6011 outside the boundary
- `tests/battery/test_migrate_r7.py::test_g4_boundary_mutants[return globals()("Value")(None)]` — AssertionError: Value constructed in _named_fraction_bound_selection:6011 outside the boundary
- `tests/battery/test_migrate_r7.py::test_g4_boundary_mutants[return globals().get("Value")(None)]` — AssertionError: Value constructed in _named_fraction_bound_selection:6011 outside the boundary
- `tests/battery/test_migrate_r7.py::test_g4_boundary_mutants[return globals()["Value"](None)]` — AssertionError: Value constructed in _named_fraction_bound_selection:6011 outside the boundary
- `tests/battery/test_migrate_r7.py::test_g4_boundary_mutants[return globals()["select_declared_source"](None, None, {})]` — AssertionError: Value constructed in _named_fraction_bound_selection:6011 outside the boundary
- `tests/battery/test_migrate_r7.py::test_g4_boundary_mutants[return select_declared_source(None, None, {})]` — AssertionError: Value constructed in _named_fraction_bound_selection:6011 outside the boundary
- `tests/battery/test_migrate_r7.py::test_g4_boundary_mutants[selector = select_declared_source\n    return selector(None, None, {})]` — AssertionError: Value constructed in _named_fraction_bound_selection:6011 outside the boundary
- `tests/battery/test_migrate_r7.py::test_g4_boundary_mutants[selector: object = select_declared_source\n    return selector(None, None, {})]` — AssertionError: Value constructed in _named_fraction_bound_selection:6011 outside the boundary
- `tests/battery/test_property_no_count.py::test_m15_property_no_test_asserts_a_corpus_census` — AssertionError: assert ['test_rys_20...3: pinned 60'] == []
- `tests/battery/test_r16_store_regen.py::test_committed_store_has_no_stale_migrate_input_commits` — AssertionError: store is STALE w.r.t. migrate inputs; regenerate with scripts/battery_migrate.py + build_index --write-store-summary:
- `tests/battery/test_r17_glass_store_regen.py::test_committed_store_has_no_stale_migrate_input_commits` — AssertionError: unacknowledged post-regen migrate inputs: {'ef77c9427', '72c25f447', '8e77f5460', 'b5c0a34dc', '5cc4d1102', '1eb761019', 'dca6d8010', 'e7d7949c4', 'b652ade0f', '015f56d4e', 'edadc426d', 'd599bd273', '4408b15a9', '9c3a1151d', '5e97c09cd', 'c8eb9a33a', '5a2cf6aa1', '99c610d47', '8d35cc4f8', '8c99455f4', '397f4c1e3', '929c07240', 'be20e42c7', '39f0367c2', 'ecc33e39f', '527b0d3e7', '7469d7b12', 'eee2ee1dd', '042d48b56', 'd3285901c', 'cb00b3e05', '00982fcd1', '606c8e99e', 'bf6da9cee', 'c909e608a', 'f5d31a577', '415023f24', '3de591b81', 'a54ae31e7', '9b6331d33', 'e0aa58904', '7609ebc86', 'd2143313b', 'bccc180e2', 'cd7fbe3da', '9c32d932d', 'f13736762', '27b9aa10f', '013926f10', '060d40748', '0eae2fa55', 'fc8308604', 'c7415973e', 'aa5c9712f', '7cdedfc7c', '80b20edf5', '527252210', '383f86909', 'a4e2816c9', '513ae4504', '8ee563652', 'ec63836b3', 'e88da0eee', '24618c025', 'e7d122e00', 'cc797a1a3', 'b03f20773', '1868a25dc', '4ee22964a', '7d1865616', 'f461876b6', 'df584a4bc', 'bad62ef4d', 'b4307d11c', 'ecdf9683c', '3c9f5b4f6', '1c4e5bb25', '4d4524698', '4b3b00271', 'c9f2e2892', 'f7a29715d', '889f34cf3', '705ffec5a', 'b0e51b93c', '981be1b14', '22a0d0945', '66c64a309', '3de37e78b', '28e90a79f', 'ca846f131', '4ef1f0e2b', 'fd6fed7a4', 'a65ebd0ff', '6ff12a7b0', '6b6825f9f', '1a328f291', '060b59f9d', '5219fec8c', 'aeb360df3', '83f425b68', '84d9b20cb', '27609ab53', '26ff9e44e', '2342c5815', '63589952a', '6e8684aec', '8fd5246a2', '04ac4a05e', 'c96e746fc', 'd7f3ebd47', 'ca49a2e50', '7878671ef', '9b1226945', '21ee1779b', 'af85e3d9c', '41ee1b47c', '9a7f961b0', '41f0616b6', 'f9d033085', 'b0031a8f3', 'a98b1383c', 'fd7d63f0b', '99395d6f2', 'f6f3f0b9a', '8ce88dea7', 'a8bb4f71d', 'ff7bdadfd', '165d6e8ee', '6c09d957d', '16d9ec0a2', 'a3c3afc21', 'e332992a7', 'a01cffbca', '6ddcb1413', '2751617f2', '851565a57', '033da8457', 'a367fad6c', '76f780a76', '81eb49b52', '6837edda2', '04e03ba08', '3b8559c05', '7aa96e88b', 'c0edfa426', '1bafcac44', '0c740e142', '41c5ed9f3', 'dbf7c91d0', '4cd36a852', 'e66b15cfa', '654655b99', '10ce53fcc', 'bdade0631', 'c4c44cb76', '0a3217efd', 'effe6de02', '74cb01aca', '55d8dc5f8', '9305a068d', 'c97847f5c', 'f8178f91a', '3f6f94f96', 'ea5827d9b', '1f16ec7ed', '09797168d', '0d97e4df2', 'b0c2f87ea', 'd1c691ccc', 'ef69dffa1', 'b357b44ca', 'bd38be7fe', '6028d1b5a', 'f7bb180fd', '3f948a82d', 'f10dab307', '99443ebf3', 'f85245179', 'ae976b5c8', '61e6e61dd', '887c8f365', 'f2e9b34ee', '3d822594e', 'ce43ca290', '9d6aaa2a0', '683511598', '5e497a2ed', 'ea66120a5', 'fbdb60242', '8f262a497', 'a9dabb02e', 'a58e1ea74', '9c17e2e25', '39eb9813d', 'f5d636158', '3f00512eb', '2e965e11e', 'ff60e6d16', '53e6a7310', '5f1963ef3', '59ec9ffb9', '1e9bb3b38', '8d9705dbb', '5affb08f9', '1191c1691', 'e1c07ca8c', 'd71b448c6', '995a7c22c', '94e3f405d', '1c528f800', '3c8e869e5', '6bd2d938f', 'd792eda2b', 'd2d6c8cf3', 'c8ead2efb', '4999adbbf', '3043cef8e', '48b3251f8', '116145be3', '442d1461d', '1b7188d6a', 'c9c78c175', '5f6ba3ed4', '0af2c9718', '5566c936d', '5750bde5f', '7e7a5f5d9', '86aee2bbb', 'e0223fe4c', 'dd6800d1e', '378ce40a9', 'fbedf7403', 'e8d88761e', 'ff19b2adb', '308ac9db6', '02bc8e04a', 'b046d5472', '3375a1e93', '4c6317c4a', 'eb01e2fed', '6b2d5f185', '66b6589c7', 'e04b47fa1', 'a2bd0997b', '45cea9498', 'cedc4cd98', 'acf058b16', '54f7b398f', 'b20a0c5ff', 'b973d9277', '7bc7ab788', 'ed9e5dbd4', 'dc5e3e0ee', 'e7e3983da', '4000d10b6', 'e2d2c075d', 'c8be639af', '43d8ddd41', '278bf8435', '5b2cedccf', '128fe2428', '5f019dd46', '7f8ddb24e', '98402838f', '2fa50d670', '1131b4b23', '98c7bc761', '6c08d584c', '277f38bda', '9c0693a37', '5e1e5da56', '1b16d99a6', '9e93f8893', '63e2844b1', 'c0bfc46a9', '7999fa9dc', 'e4bc14e99', 'ecf35b2c6', '00590dd7a', '828a1e7a9', '477a8c0a8', '1ca8328d4', '72a1d6095', '42797dfa8', '92060ee60', '29fcafa1e', 'f7b510b18', '3a7f6a71f', '2edfdb425', '21d1ee8d7', '9cd3b4724', '7638d15b7', 'e2b33bcf9', 'dd1b5968c', 'fc70099e7', 'ebd17fe71', 'b8dda1e3a', 'e444f5925', '788b498ac', 'f1f611d2d', '4745ae480', '070b22925', '07d0feef1', '39a848730', 'f2fa98747', 'eb85083f1', 'bbc0a4a72', 'eec464e34', '8af1797ae', '00bd467ec', '4f4b948e7', 'ae4a2b986', '599606689', '605d62065', 'dfc707cb7', 'acebc70e6', '0a9f0c29b', '2f68fef78', '56a313867', '64625117f', '73ee57902', 'b96aef6ed', 'db3b76f35', '4cb21eea5', '80bfbff17', 'b69aa646c', '6eec9a074', '51105547e', 'b153dd994', 'f536bb98a', '57b8bcea1', 'd6f06dc42', 'bf54c61d4', 'c9d209ef7', 'c263c7938', '577e28a11', '22dab268e', '47f863f17', '4b7dace28', '3f59be899', 'c8efeabdc', '41bce7c42', 'ed2b1e76d', 'da7d9fc2b', 'fd7ed23cc', '16d8eb240', '6baa72d02', '4ed098ceb', '678d31a78', '170c79389', '2be45bef7', '70b50d413', '133f17ae6', '9314b6774', '982d1bfe5', 'c390c0005', '1060e7370', '2f342093c', 'f80fe3559', '8b18e7625', '034054685', '870e62c10', '1476ee470', '8de76ab40', '892dccc2e', '1b6372045', '2cb08370d', '1c24025f3', 'f9894e760', '03e607910', '653f797aa', '70c717d5f', '8932be515', 'dfc501b37', '513805471', 'fcd00e9d1', 'b2bbdfe35', '636298879', '1b3b67fd4', '7f7eb31eb', 'd53162118', '097ad758b', '3deb27971', '8366acaed', '2fa6d0dc1', '904c3996b', '3ad936ca8', 'a2a7492b1', 'f066c5b4d', 'ec80e15df', 'd4c39b370', '99d1eaa1f', '5ba171b62', '536a744eb', '95de7ba9b', '7de87a143', '7248e7421', '5c247e952', 'ef7da56a5', '5c9fb4246', 'd1eabbfba', 'f66dd6c36', 'dba5b9568', 'd5aa61a94', 'e86bfbf6f', 'c1231bfd3', '624e4c9e9', '97bb6e841', '176451cfb', '8134009c7', 'd1e5cf54a', '98fc3fc10', '3c62a3d4f', 'bec82179e', '68bd7a426', '9061ef654', '1303b4ef1', '7baaf334b', '34006af9b', '9d334b1d1', '08320d26e', '47919bb97', '3756c7c8b', 'fdeaa93e5', 'efe20a228', '7a90bab6a', 'a3773d765', '45fa6fdae', 'f7d6901b7', '57e5c9c40', '820f1f589', '06edc8a1b', '78421dd3d', '91d560cdc', '7297361d6', '51d060552', '8d29ffa58', '79d61a1cb', 'c766b89b8', 'a7ca35ef1', '125f1fd09', 'ce4baa01a', '2cba3708a', '59db0aa64', 'df017e9d3', 'ffd940954', '42e9bc763', 'f3082e8c4', '1683f1756', '47376a466', '0730604ef', 'c30991d8d', 'df501a90c', 'fe4527aa8', '7ea8c54f9', 'f03435cc2', 'd4694c469', '63259dc6c', '9888d9caa', '4219027d7', 'dcd0f6916', '08bab10a9', '4e07bd649', '5d5af9dbe', '1a0c9572d', '1c4e65ad0', '7203cbe7c', '4a4f43cd7', 'b15131f6e', '65b6b8af6', 'ce0ef0e4b', '1cfb9bb40', 'eb7a98b59', 'eead470ca', '53bd8803f', 'bec35c779', '24596c08d', 'cd7966564', '8845f7217', '2455216ab', 'c1d148f85', '7a42c5a24', '5ab1f987f', '2338113e2', 'd1ce23baa', 'cdad0b3cd', '0a7436790', 'a0059f942', '5339c1879', 'bfb611d85', 'be2c96e44', '0a538d369', '1fa1c5779', '78543cbc6', '8134beffa', '9a3d42c71', 'b855826d0', '70207d0e4', '1df9f6af9', '45fe44875', '6dc6e6e42', '986d009ea', 'bd14cbff9', '74db58f6e', 'c763c444f', '83403c983', '194cd569c', 'd6ac01b1a', '27dc9099f', 'dabb1f9e7', '4ccfc2608', 'b6ef9136c', '6c1f09eb1', '69c8bc966', '6e837bc10', 'bfa9062c5', '68273bee2', '2d6dee882', '312c2f676', '600a140b5', 'a29ec9175', '2a70cd9f2', '2f21ef45d', '35143ad80', '15fdf298c', '1d523f7f6', '0a1c9ab4c', 'df3d3891c', '90b8f2c53', '64ce4638e', '4e0819f6d', 'af51f5485', '09777603d', '4095d0614', 'a27c80220', 'f74b69996', '3a31f3a43', '82affc0bf', '9dd7fe832', 'a60ebf140', 'fd0d6b473', 'd67b5d6f7', 'cbbdf574c', '2900498e8', '73eb2cedc', '33d42c220', '9f74d7e74', '38201bbbe', '45765e41f', '38bf11bf2', '6fbf36a1d', 'a2879a99a', 'b40e8d55b', '2648aaa7e', '9899789bf', '6201621b8', '9d01692d2', 'f32336651', 'd95541e16', '21dcf7953', 'c69eda064', '9b0ed5639', '79bd75879', '55c32b2ff', '3dab93c3c', 'b787a4588', '3f2d69181', '450c0f4c8', '38af44de2', '1175b49fc', '56fbddded', '0f0fef6a9', '829e5d0bd', 'd09f436be', 'b92777609', '5b6b50b21', 'aa6edc791', '2fb263985', '8044ad462', 'b72b8ac16', '5026c1f31', '14c50501f', 'cf81f4ba6', '7f0d9f339', '33b11a6ef', '7cf3ab415', '6c93b6218', '95c875ec6', 'a4e801c39', '9f3049640', 'dce4722e7', '18923febb', 'c00756e38', '9f716b0b1', 'f6b72676c', '7c28e14c4', '1d3c83e0f', '2fdcaf7d8', 'c4398089d', '967558512', '59287c9e3', '313c3908a', 'ece851beb', '359a62fbd', 'd157906e6', '5bcec692e', '696341f87', 'ab64ae340', 'cdd3ef0fe', '8e40315e9', '3890e28c1', 'f7a7d9d05', '118a8ecf4', '74a808058', 'fc7c11533', '27adf715e', '97a65be97', 'c00db1723', '7fb43772b', 'ddf175940', 'da2680c99', '6bce684b1', 'bd632d2cd', '192582717', 'a937ba48c', 'be0c2b614', '8974c3082', 'dec6cfe40', '87f672f19', 'aaa86ca6a', '410135382', '2c26a6104', '0f4e598e4', 'd6d2b1744', '6838b0120', '4abcfe352'}
- `tests/battery/test_redox_extract_scoring.py::test_borisov_2018_rows_reach_redox_scoring_population` — KeyError: 'inference'
- `tests/battery/test_redox_extract_scoring.py::test_borisov_redox_headline_splits_calibration_overlap_from_accuracy` — AssertionError: assert {'independent...rce_accuracy'} == {'calibration...rce_accuracy'}
- `tests/battery/test_residue_oxide_green_parity.py::test_printed_residue_migrations_match_green_a822c275f` — subprocess.CalledProcessError: Command '['/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python', '/Users/simonrowland/Repos/regolith-pyrolysis-simulator/worktrees/s-26/tests/battery/test_residue_oxide_green_parity.py']' returned non-zero exit status 1.
- `tests/battery/test_rys_2007_thermal_effects.py::test_rys_tables_7_and_9_thermal_cells_migrate_with_composition_and_locators` — KeyError: 'total_pressure_Pa'
- `tests/battery/test_score.py::test_admitted_model_derived_rows_emit_residuals_per_imcc_engine` — AssertionError: assert set() == {'kems-053-st...bdb53a0', ...}
- `tests/battery/test_source_oxygen.py::test_migrated_printed_fo2_source_is_excluded_by_scorer[local-id-Pa]` — KeyError: 'match'
- `tests/battery/test_source_oxygen.py::test_migrated_printed_fo2_source_is_excluded_by_scorer[local-id-atm]` — KeyError: 'match'
- `tests/battery/test_source_oxygen.py::test_migrated_printed_fo2_source_is_excluded_by_scorer[qualified-id-Pa]` — KeyError: 'match'
- `tests/battery/test_source_oxygen.py::test_migrated_printed_fo2_source_is_excluded_by_scorer[qualified-id-atm]` — KeyError: 'match'
- `tests/battery/test_sweep_gas.py::test_model_carrier_keeps_printed_sossi_furnace_condition[sossi_2019_k_open_furnace_alpha_e_context-sossi_2019_k_open_furnace_alpha_e_context]` — AssertionError: no context row for kems-012-sossi-2019::sossi_2019_k_open_furnace_alpha_e_context
- `tests/battery/test_sweep_gas.py::test_model_carrier_keeps_printed_sossi_furnace_condition[sossi_2019_na_alpha_e_authors_adopted_unity-sossi_2019_na_alpha_e_authors_adopted_unity]` — AssertionError: no context row for kems-012-sossi-2019::sossi_2019_na_alpha_e_authors_adopted_unity
- `tests/battery/test_sweep_gas.py::test_model_carrier_keeps_printed_sossi_furnace_condition[sossi_2019_na_alpha_e_authors_adopted_unity_quoted_20260906-sossi_2019_na_alpha_e_authors_adopted_unity]` — AssertionError: no context row for kems-012-sossi-2019::sossi_2019_na_alpha_e_authors_adopted_unity
- `tests/battery/test_sweep_gas.py::test_model_carrier_keeps_printed_sossi_furnace_condition[sossi_2019_na_pure_system_LH_table5-sossi_2019_na_pure_system_LH_table5]` — AssertionError: no context row for kems-012-sossi-2019::sossi_2019_na_pure_system_LH_table5
- `tests/battery/test_sweep_gas.py::test_model_carrier_keeps_printed_sossi_furnace_condition[sossi_2019_na_pure_system_LH_table5_quoted_20260906-sossi_2019_na_pure_system_LH_table5]` — AssertionError: no context row for kems-012-sossi-2019::sossi_2019_na_pure_system_LH_table5
- `tests/battery/test_sweep_gas.py::test_richter_h2_pressure_note_does_not_demote_its_point` — assert 0 == 1
- `tests/battery/test_sweep_gas.py::test_sossi_log_k_star_does_not_use_furnace_pressure[sossi_2019_na_logKstar_table3-sossi_2019_na_logKstar_table3]` — AssertionError: no context row for kems-012-sossi-2019::sossi_2019_na_logKstar_table3
- `tests/battery/test_sweep_gas.py::test_sossi_log_k_star_does_not_use_furnace_pressure[sossi_2019_na_logKstar_table3_quoted_20260906-sossi_2019_na_logKstar_table3]` — AssertionError: no context row for kems-012-sossi-2019::sossi_2019_na_logKstar_table3
- `tests/battery/test_sweep_gas.py::test_sossi_printed_one_atmosphere_still_inherits` — AssertionError: no context row for kems-012-sossi-2019::sossi_2019_k_class_b1
- `tests/battery/test_t1225_melt_activity_methods.py::test_printed_quench_equilibration_reaches_melt_activity_rows[kume-2000-cao-activities.yaml-observation_selectors0-555-1]` — AssertionError: assert 2 == 555
- `tests/battery/test_t1225_melt_activity_methods.py::test_printed_quench_equilibration_reaches_melt_activity_rows[ohta-1996-cao-mgo-al2o3-activities.yaml-observation_selectors1-983-1]` — AssertionError: assert 1 == 983
- `tests/battery/test_t1225_melt_activity_methods.py::test_printed_quench_equilibration_reaches_melt_activity_rows[seki-2011-ca-o-solubility-fe-cao-al2o3.yaml-observation_selectors2-1370-2]` — AssertionError: assert 2 == 1370
- `tests/test_absolute_path_ratchet.py::test_path_value_keys_match_the_extract_tree` — AssertionError: path-carrying keys outside PATH_VALUE_KEYS: ['audit_file', 'audit_files', 'calibration_audit', 'data_csv', 'input_csv', 'input_csvs', 'inputs_csv']
- `tests/test_literature_extracts.py::test_coverage_pending_stubs_not_found` — SystemExit: extract validation failed:
- `tests/test_literature_extracts.py::test_every_extract_has_fidelity_sample` — AssertionError: shornikov-2000-titania-melts.yaml missing fidelity_samples
- `tests/test_literature_extracts.py::test_markova_1984_apparatus_inputs_migrate_from_the_printed_method` — AssertionError: assert 0 == 8
- `tests/test_literature_extracts.py::test_pilot_extract_count_exact_and_merge_smoke` — SystemExit: extract validation failed:
- `tests/test_literature_extracts.py::test_repo_extracts_validate_green` — AssertionError: /Users/simonrowland/Repos/regolith-pyrolysis-simulator/worktrees/s-26/data/literature/extracts/shornikov-2000-titania-melts.yaml: fidelity_samples required when observations are present (t-510 ENFORCED_FOR_NEW: ≥1 reviewer-verified located sample; pre-policy pilot extracts may be allowlisted)
- `tests/test_literature_extracts.py::test_sossi_na_inference_and_ceiling_never_recreate_alpha_range` — KeyError: 'context'

Eleven test files exist only on the tip: test_anand_2024, test_arias_2025, test_chen_2019, test_costa_2025_table3, test_lontchi_2024, test_migrate_evaporation_extracts, test_raberg_2007, test_residue_oxide_green_parity, test_rys_2007_thermal_effects, test_sugie_2021, test_transition_composition_identity. Failures in costa, migrate_evaporation, residue_oxide_green_parity, and rys are in the list above. The other seven passed.

The Markova `T_range_K` failure is new versus d8c894343. It is the L1 resolution: the observation-level ranges stay off, so the assertion that looks for `T_range_K` in the unknown-temperature reason fails. Point rows still carry temperatures.

!COMPLETE: carb-asm-1022 — 3986008f58e925ac7e81e42598055b916c565e68

## Fix round

`carb-classify.tsv` classifies the 66 new failures against a scratch regen of `2535acb42` (migrate write, then `build_index.py --write-store-summary`). All 66 were red before that regen. Eleven g1 absence-claim tests went green: they were reading the stale committed store. The other 55 stayed red. Nothing from that scratch tree was committed.

Seat fixes, one commit per cause:

- `a5ba8a6ac` routes `_named_fraction_bound_selection` and `_printed_scalar_selection` through `_value_from_plain`. `allowed_value` is unchanged.
- `cf65c323f` gives the Borisov 2008 wt% phase rows `phase_label` and `basis` so the existing `derived_from` parents are emitted. Lineage lists were not deleted. An isolated migrate of that file is 332 observations and 0 hard issues; it had been 73 observations and 2787 hard issues.
- `5f44bd3ee` restores Sossi `species.Na.context` dropped by merge `4679aa7d9`, keeps the K measurement rows, and copies parent lineage onto quoted model successors.
- `2f6a9a927` registers `audit_file`, `audit_files`, and `calibration_audit`, and drops dangling `.agent-scratch` CSV values.
- `caaffbbbe` adds one reviewer-checked fidelity sample each for Shornikov 2000 Fig. 2(c) (`log10_activity` `-1.886`) and Yakovlev 2017 Fig. 2 (`log10_p` `-6.195`).
- `63524113c` reads printed folios from `published_page`. t-1353 stores the physical PDF page on `locator.page`. Stolyarova Table 3 reference states are unchanged; the content-id pin moved with the page field.

Scratch regen of `63524113c`: rows_in=61218, observations=146627, works=604, experiments=4560, queue=96862, hard_issues=3194. Against the pre-fix regen (61229 / 146379 / 97177 / 6033) that is rows −11, observations +248, queue −315, hard −2839. Migrate exited 1 because hard_issues is non-zero. The index write exited 0. The regenerated store was not committed.

Re-run of the 66 on that regen: 44 pass, 22 still red.

The passes are the 11 stale-store g1 tests, the k01 boundary and its 11 mutants, the Sossi context tests, the path-key ratchet, the four fidelity gates, and the locator-folio pins.

Still red on this regen:

- r16 and r17 freshness. Left red. d-255: the committed store is stale against migrate inputs, and a dirty regen does not move the commit those tests read.
- Costa 2025 and Rys 2007: `point_conditions` has no `total_pressure_Pa`. Identity pressure on the Rys rows is already unknown.
- Langmuir free evaporation: no observation inherited the experiment total (`assert []`).
- Borisov 2018 combined iron: `KeyError: composition_mol` after the row count check.
- Demaria 1973: one hard issue, an initial-composition conflict between sample-catalog proxies (sample 12073 versus the Table I Na rows).
- j01 Fedkin alpha: `assert 0 == 12` (no alpha series rows found).
- j01 census: Chen 2019 Table 5 cp points store a numeric value whose declared quantity has no source field.
- k04: corrupting stored alpha does not turn the census red (`assert 0 == 9`).
- Markova Table 2: the two zero mass-loss rows are the printed T=0 initial charge. `temperature_K` stays unknown with reason `no single temperature_K mapped from source`. The test looks for `T_range_K` in that reason. That is the L1 resolution already recorded above: observation-level ranges stay off, and point rows still carry temperatures. I did not assign a temperature. If the unknown reason should name the printed range, that is a wording judgement on top of that L1 choice.
- Markova apparatus: `assert 0 == 8`. The migrated validation report has no hard issues. The printed method leaves sample mass and total pressure unknown (`IDENTITY_INCOMPLETE`), which is not a hard issue. I did not invent a pressure or a mass.
- m15 census pin: `test_rys_2007_thermal_effects.py:53` compares a table length with the literal 60, which is in the forbidden census set.
- Borisov 2018 redox: `KeyError: inference`, and the headline set is missing `calibration_source_overlap_fit_fidelity`.
- Residue-oxide green parity: the child process exits 1. The parent assertion does not include the child's stderr.
- Score residuals: `assert 0 == 26` admitted model-derived rows emit no IMCC residuals.
- Source oxygen, four parameter cases: `KeyError: match`.
- Richter H2: `assert 0 == 1`. The Sossi sweep cases in the same file pass.

These remaining reds were not patched. Each one still needs its own cause, and several would be a physics or scoring judgement rather than a locator or lineage repair.

## Prep merges

After the fix-round regen, five reviewed car-B prep commits were merged with `git merge --no-ff`, in order:

- `6b0378d4ff4706bf3d9790413cffac23e6e4748f` t-1442 Kress-Carmichael dedup `8cc62e193`.
- `329658d6e153e1ba2dae913efc8fed3f40bdb511` t-1442 Mendybaev dedup `8a3d0ce7c`.
- `9c71a44438a484172b5ae4498cf7e1e3ff2ccbe0` F31 re-type `0f98dfab6`. The merge also brought the Evans 2009, Jin 2022, and Saini 2023 extracts that sit on that tip's ancestry.
- `4e30031f7beeafb56171f71b565fdf558a08ab92` F35 Taniguchi re-type `d9368f82a`.
- `a95fa19909a8c1af910a42a55e0864cc8dbce88e` F37 Henao/Van Ende re-type `3e54199c8`.

The first four merged clean. F37 conflicted only in `henao-2001-ni-cr-mn-co-cu-slag-distribution.yaml`, three context rows. The resolution keeps the retyped remainder `printed_rows_csv` (blanks, bounds, and calculated activity columns) and leaves out the three `input_csv` values under `.agent-scratch/F37-tables/`. Those pointers are the dangling scratch paths the path-key commit removed. No derived store file was in the conflict.

Foreground re-run on this seat, one file at a time, of the tests those merges touched plus `tests/test_literature_extracts.py` and the path ratchet:

- `test_kress_carmichael_1991_iron_redox_extract.py`: 4 passed.
- `test_mendybaev_2013_fun_cai_extract.py`: 6 passed.
- `test_evans_2009_sulfur_mn_ni_w_xanes_extract.py`: 1 passed.
- `test_jin_2022_cl_cr_sulfur_glass.py`: 1 passed.
- `test_saini_2023_sulfur_vanadium_borosilicate_extract.py`: 1 passed.
- `test_taniguchi_1997_feo_cas_extract.py`: 2 passed.
- `tests/battery/test_phase_composition_rows.py`: 13 passed.
- `test_henao_2001_ni_cr_mn_co_cu_slag_distribution_extract.py`: 2 passed.
- `test_van_ende_2008_mn_cr_slag_steel_extract.py`: 2 passed.
- `test_absolute_path_ratchet.py`: 247 passed.
- `test_literature_extracts.py`: 738 passed, 1 failed.

No new failure. The one red is `test_markova_1984_apparatus_inputs_migrate_from_the_printed_method` (`assert 0 == 8`), already recorded above. r16 and r17 were left red and were not re-run. None of the merge resolutions needed a physics judgement. The open judgements remain the Markova Table 2 unknown-temperature wording and the Markova apparatus sample mass and total pressure.

## Fix round 2

Scratch regen of `f936e0e85` (migrate write, then `build_index.py --write-store-summary`). Migrate exited 1 because hard_issues is non-zero. The index write exited 0. The store was not committed. `1a9aa1fb5` changes only `tests/battery/test_migrate.py` and `tests/battery/test_score.py`, so that regen is the final tip's migrate. The re-run below copied those two test files onto the regen tree.

Regen versus `d8c894343`: rows 51641→62021 (+10380), observations 128946→148022 (+19076), works 371→604 (+233), experiments 3863→4697 (+834), queue 88695→97181 (+8486), hard issues 3278→3194 (−84).

Hard issues are stored by kind, not by source. The kind census:

| kind | d8c894343 | tip | delta |
|---|---:|---:|---:|
| `conditional_field:derived_from` | 2131 | 1824 | −307 |
| `conditional_field:derivation` | 1143 | 1238 | +95 |
| `referential_integrity:derived_from` | 0 | 65 | +65 |
| `referential_integrity:inputs` | 0 | 63 | +63 |
| `invalid_source:initial_composition` | 4 | 4 | 0 |

The base report omitted the two referential rows. Their appearance on the tip, with the derived_from and derivation moves, is the −84. The report keeps the first 50 hard-issue paths and does not give a per-source hard total.

Observation delta is per extract. 166 extracts changed observations (sum +19076). 100 further extracts changed queue with the same observation count. Compilations did not change.

| source | obs d8c894343 | obs tip | obs delta | rows delta |
|---|---:|---:|---:|---:|
| `neuman-2026-levitation-k-isotopes` | 0 | 2307 | +2307 | +80 |
| `rys-2007-alkali-oxide-thermodynamics` | 0 | 1001 | +1001 | +56 |
| `cheng-2021-fe-ca-si-o-phase-equilibria` | 0 | 990 | +990 | +990 |
| `kress-carmichael-1991-iron-redox-calibration` | 0 | 881 | +881 | +881 |
| `gran-sichen-2012-molten12-ladle-slag-liquidus` | 0 | 876 | +876 | +219 |
| `klemme-2011-cobalt-nickel-titanate-carbonate-heat-capacity` | 0 | 827 | +827 | +14 |
| `chen-2019-sioch-cp-entropy-gibbs` | 0 | 760 | +760 | +33 |
| `sineva-2023-ni-sn-zn-slag-copper` | 0 | 696 | +696 | +696 |
| `wang-2001-solar-melt-evaporation-residues` | 0 | 538 | +538 | +38 |
| `inoue-1991-n-s-partition-cao-al2o3` | 0 | 446 | +446 | +446 |
| `henao-2001-ni-cr-mn-co-cu-slag-distribution` | 0 | 431 | +431 | +431 |
| `hanson-jones-1998-am-cr-partitioning-olivine-liquid` | 0 | 400 | +400 | +98 |
| `evans-2009-sulfur-mn-ni-w-xanes` | 0 | 381 | +381 | +24 |
| `tranell-2000-silica-activity-titanium` | 0 | 367 | +367 | +106 |
| `borisov-2008-k-na-miscible-liquids` | 0 | 332 | +332 | +332 |
| `holzheid-lodders-2001-copper-solubility` | 0 | 321 | +321 | +321 |
| `shornikov-2000-titania-melts` | 0 | 301 | +301 | +141 |
| `shornikov-2026-wollastonite-kems` | 0 | 277 | +277 | +194 |
| `shornikov-2019-perovskite-evaporation-thermodynamics` | 0 | 263 | +263 | +263 |
| `xiao-2002-chromium-oxide-activities` | 0 | 261 | +261 | +261 |
| `berman-1988-internally-consistent-mineral-thermodynamic-data` | 0 | 219 | +219 | +219 |
| `chen-2022-mgo-sio2-tio2-phase-equilibria` | 0 | 198 | +198 | +198 |
| `jin-2022-cl-cr-sulfur-glass-solubility` | 0 | 198 | +198 | +13 |
| `van-ende-2008-mn-cr-slag-steel` | 0 | 189 | +189 | +189 |
| `lopatin-2023-na-barium-borate-kems` | 0 | 186 | +186 | +10 |
| `tyurnina-lopatin-stolyarova-2008-ba-borosilicate-kems` | 0 | 181 | +181 | +91 |
| `borisov-2009-alkali-activity-coefficients` | 0 | 169 | +169 | +169 |
| `yakovlev-2024-cai-vaporization` | 0 | 167 | +167 | +11 |
| `yakovlev-2017-cai-acidity` | 0 | 166 | +166 | +97 |
| `shornikov-2020-mgo-feo-melts` | 0 | 146 | +146 | +146 |
| `taniguchi-1997-feo-cas` | 0 | 141 | +141 | +141 |
| `ozawa-2012-forsterite-vacuum-evaporation` | 0 | 134 | +134 | +5 |
| `shornikova-2019-perovskite-thermodynamics` | 0 | 119 | +119 | +119 |
| `zaitsev-1999-na2o-sio2-pccp` | 0 | 116 | +116 | +11 |
| `shornikov-2011-titania-melt-evaporation` | 0 | 114 | +114 | +51 |
| `zaitsev-2000-sodium-silicate-thermodynamics` | 0 | 110 | +110 | +7 |
| `kems-029-yakovlev-shornikov-2011` | 15 | 122 | +107 | +12 |
| `shornikov-2000-aluminosilicate-thermodynamics` | 0 | 105 | +105 | +6 |
| `jeon-lindberg-2023-mg-ca-zn-oxide-equilibria` | 0 | 102 | +102 | +102 |
| `florin-2025-cu-silicate-evaporation` | 0 | 99 | +99 | +4 |
| `mendybaev-2013-fun-cai-evaporation` | 0 | 96 | +96 | +2 |
| `shornikov-2003-mullite-thermodynamics` | 0 | 94 | +94 | +94 |
| `shornikov-2016-cao-mgo-silicate` | 0 | 85 | +85 | +50 |
| `saito-2021-fexo-cao-silica-activity` | 0 | 84 | +84 | +84 |
| `balabanova-lopatin-tyurnina-2025-sro-bao-al2o3-sio2-kems` | 0 | 83 | +83 | +83 |
| `kambayashi-kato-1984-mgo-sio2-1973k` | 0 | 77 | +77 | +77 |
| `lu-2026-mercury-sulfur-liquidus` | 0 | 77 | +77 | +77 |
| `shornikov-2020-cao-mgo-thermodynamics` | 0 | 77 | +77 | +77 |
| `borisov-behrens-holtz-2015-melt-composition-fe-redox` | 0 | 72 | +72 | +72 |
| `shornikov-2008-mgo-aluminosilicate-melts` | 0 | 70 | +70 | +70 |
| `kems-028-yakovlev-1984` | 29 | 98 | +69 | +8 |
| `jakobsson-tangstad-2015-ca-mg-activities` | 0 | 68 | +68 | +68 |
| `shornikov-2019-ca-feo-vapor` | 0 | 68 | +68 | +34 |
| `shornikov-2019-perovskite-evaporation-rjpc` | 0 | 68 | +68 | +6 |
| `itoh-1989-zno-solid-zinc-silicate` | 0 | 67 | +67 | +67 |
| `shornikov-2019-mgo-titania-melts` | 0 | 63 | +63 | +63 |
| `borisov-behrens-holtz-2013-ti-p-fe-redox` | 0 | 62 | +62 | +62 |
| `morishita-2004-sio2-partial-enthalpy` | 0 | 61 | +61 | +7 |
| `shornikov-2011-cao-mgo-alumina-melts` | 0 | 60 | +60 | +60 |
| `tyurnina-2023-sro-al2o3-sio2-vaporization` | 0 | 60 | +60 | +60 |
| `saini-2023-sulfur-vanadium-borosilicate-structure` | 0 | 59 | +59 | +6 |
| `borisov-2017-network-modifiers-fe-redox` | 0 | 58 | +58 | +58 |
| `park-morita-2025-vanadium-activity` | 0 | 56 | +56 | +56 |
| `shornikov-2025-perovskite-condensation` | 0 | 54 | +54 | +54 |
| `tachibana-2002-enstatite-evaporation-kinetics` | 0 | 54 | +54 | +6 |
| `bissbort-2026-depolymerized-silicate-glass-transition-viscosity` | 0 | 53 | +53 | +53 |
| `shornikov-2015-cai-vaporization-coefficients` | 0 | 53 | +53 | +53 |
| `gaillard-2003-hydrogen-iron-redox-rate` | 0 | 52 | +52 | +52 |
| `lee-bruckner-1986-borate-redox-series` | 0 | 52 | +52 | +14 |
| `kemori-1980-oxygen-liquid-copper` | 0 | 51 | +51 | +31 |
| `kems-055-kambayashi-1983` | 0 | 50 | +50 | +50 |
| `shugurov-2026-ceo2-tio2-kems` | 0 | 50 | +50 | +50 |
| `shornikov-2003-aluminosilicate-solids` | 0 | 47 | +47 | +7 |
| `shornikov-2005-potassium-germanate` | 0 | 46 | +46 | +46 |
| `nekhoroshev-2024-cao-sio2-thermodynamic-reoptimization` | 0 | 45 | +45 | +45 |
| `leister-1999-fe-v-redox-2000c` | 0 | 44 | +44 | +5 |
| `tranell-1999-unsw-thesis` | 0 | 44 | +44 | +30 |
| `kems-214-hastie-1980-nbsir-80-2178` | 0 | 43 | +43 | +4 |
| `ferguson-nuth-2008-sio-vapor-pressure-evaporation-coefficient` | 0 | 42 | +42 | +1 |
| `zaitsev-1997-cas-caf2-melts` | 0 | 42 | +42 | +4 |
| `borisov-2004-karrooite-liquidus-redox` | 0 | 41 | +41 | +41 |
| `shornikov-2014-sodium-germanate-melts` | 0 | 40 | +40 | +40 |
| `wright-okamura-1977-makaopuhi-cooling` | 0 | 40 | +40 | +40 |
| `shornikov-1997-cao-al2o3` | 0 | 37 | +37 | +37 |
| `renggli-2022-te-evaporation` | 0 | 36 | +36 | +1 |
| `shi-2024-cr-activity-slag-polymerisation` | 0 | 36 | +36 | +36 |
| `shornikov-2003-silica-catitanoate-melts` | 0 | 36 | +36 | +36 |
| `lee-bruckner-1984-cr-mn-glass-redox` | 0 | 35 | +35 | +5 |
| `shornikov-2008-zinc-phosphate-melts` | 0 | 34 | +34 | +34 |
| `leister-1999-v-sodium-silicate-high-temperature` | 0 | 32 | +32 | +7 |
| `metsoc-2019-6005` | 3 | 33 | +30 | +30 |
| `shornikov-2017-spinel-thermodynamics` | 0 | 30 | +30 | +18 |
| `shornikov-2002-mullite-thermodynamics` | 0 | 29 | +29 | +29 |
| `shornikov-2025-ca-al-inclusion-thermodynamics` | 0 | 29 | +29 | +29 |
| `costa-2025-ree-cmas-glass-melt-thermochemistry` | 0 | 28 | +28 | +28 |
| `shornikov-2007-cao-aluminosilicate-melts` | 6 | 34 | +28 | +0 |
| `lu-2024-mgsio3-levitation` | 0 | 27 | +27 | +1 |
| `shornikov-2015-cai-mineral-thermodynamics` | 0 | 27 | +27 | +27 |
| `de-abreu-2024-li2o-sio2-thermochemistry` | 0 | 26 | +26 | +26 |
| `shornikov-2000-silica-gas-equilibria` | 0 | 26 | +26 | +26 |
| `matsuzaki-1997-feo-tio2-sio2-activities` | 0 | 25 | +25 | +25 |
| `majzlan-2021-magnesium-silicate-glass-cp` | 0 | 23 | +23 | +23 |
| `shornikov-1997-cao-alumina-vapor` | 51 | 74 | +23 | +8 |
| `volovetsky-2010-granitoid-iron-redox` | 0 | 22 | +22 | +22 |
| `liu-li-2025-se-te-sulfide-silicate` | 0 | 18 | +18 | +18 |
| `rzehak-2021-ti-isotope-redox-lunar-melts` | 0 | 18 | +18 | +3 |
| `shornikov-2024-iron-phosphate-melts` | 0 | 17 | +17 | +17 |
| `bouhifd-2007-basalt-thermochemistry` | 0 | 16 | +16 | +16 |
| `yokokawa-1984-alumina-carbon-volatilization` | 0 | 16 | +16 | +16 |
| `kems-213-schneider-1980-nbsir-80-2139` | 0 | 15 | +15 | +15 |
| `shornikov-1999-silica-partial-vaporization-coefficients` | 0 | 15 | +15 | +15 |
| `ehrt-2008-sn-redox-aluminosilicate` | 0 | 13 | +13 | +8 |
| `shornikov-2001-mgalumina-silica-vaporization` | 14 | 27 | +13 | +0 |
| `shornikov-2024-sodium-phosphate-melts` | 0 | 13 | +13 | +13 |
| `arutyunyan-2005-magnesium-silicate-activities` | 0 | 12 | +12 | +2 |
| `borisov-2004-tio2-pd-ni-fe-solubilities` | 0 | 12 | +12 | +57 |
| `ferkl-2026-waste-glass-thermal-redox` | 0 | 12 | +12 | +2 |
| `kawashima-1984-soda-flux-chemical-sensors` | 0 | 12 | +12 | +3 |
| `baborak-2023-alkali-silicate-levitation-evaporation` | 0 | 11 | +11 | +11 |
| `shornikov-2000-na-zno-phosphate-vaporization` | 0 | 11 | +11 | +11 |
| `itoh-1985-beta-alumina-na2o-emf` | 0 | 9 | +9 | +9 |
| `schreiber-1977-plsc8-redox-ti-zr-hf-cr-eu` | 0 | 8 | +8 | +8 |
| `schreiber-haskin-1976-plsc7-chromium-in-basalts` | 0 | 8 | +8 | +8 |
| `shornikov-2007-ca-aluminosilicate-activities` | 0 | 8 | +8 | +8 |
| `shornikov-2023-silica-phosphate-melts` | 0 | 7 | +7 | +7 |
| `jackson-2005-ferrous-glass-multispectroscopy` | 0 | 6 | +6 | +6 |
| `beerkens-2001-industrial-glass-evaporation` | 0 | 5 | +5 | +5 |
| `shornikov-2020-perovskite-chemical-fractionation` | 0 | 5 | +5 | +5 |
| `shornikov-2023-mgo-phosphate-melts` | 0 | 5 | +5 | +5 |
| `kems-012-sossi-2019` | 612 | 616 | +4 | -10 |
| `myers-jacobson-2015-volatile-metal-hydroxides-presentation` | 0 | 4 | +4 | +4 |
| `raberg-2007-salt-stability-na2s-thermochemistry-thesis` | 0 | 4 | +4 | +4 |
| `romero-serrano-1999-binary-ternary-silicates` | 0 | 4 | +4 | +4 |
| `shornikov-2002-aluminosilicate-solid-phases` | 0 | 4 | +4 | +3 |
| `shornikov-2017-cai-mgo-sio-fugacity` | 0 | 4 | +4 | +4 |
| `shornikov-2025-lithium-silicate-melts` | 0 | 4 | +4 | +4 |
| `sugie-2021-sioc-mixed-bond-calorimetry` | 0 | 4 | +4 | +4 |
| `folstad-2024-metals-cao-dissolution-cas` | 0 | 3 | +3 | +3 |
| `gribchenkova-2017-al2o3-mgo` | 0 | 3 | +3 | +3 |
| `rb86-capobianco-navrotsky-1982` | 0 | 3 | +3 | +3 |
| `schreiber-1977-tetravalent-cr-diopside` | 0 | 3 | +3 | +3 |
| `shornikov-2022-na2o-al2o3-melts` | 0 | 3 | +3 | +3 |
| `shornikov-2024-alkali-phosphate` | 0 | 3 | +3 | +3 |
| `yang-teng-seetharaman-2012-vanadium-evaporation` | 0 | 3 | +3 | +3 |
| `banya-2004-sulphide-capacity-cao-al2o3` | 0 | 2 | +2 | +2 |
| `boulliung-wood-2023-sulfur-solubility` | 0 | 2 | +2 | +2 |
| `connolly-2016-si-o-liquid-vapor-caloric-vdw` | 0 | 2 | +2 | +2 |
| `he-2017-karrooite-ordering-heat-capacity` | 0 | 2 | +2 | +2 |
| `leuthold-2023-basalt-partition-fo2` | 0 | 2 | +2 | +2 |
| `matousek-1998-silicate-vaporization-reactions` | 0 | 2 | +2 | +2 |
| `shornikov-2025-lithium-phosphate-melts` | 0 | 2 | +2 | +2 |
| `shyrokykh-2021-vanadium-vaporization` | 0 | 2 | +2 | +2 |
| `avarmaa-2022-cr-don-smelting` | 0 | 1 | +1 | +1 |
| `berry-2003-in-situ-cr-xanes-furnace` | 0 | 1 | +1 | +1 |
| `costa-2015-yb-silicate-silica-activity` | 0 | 1 | +1 | +1 |
| `matsumaru-1999-plasma-arc-silicate-evaporation` | 0 | 1 | +1 | +1 |
| `shornikov-2022-k2o-al2o3-melts` | 0 | 1 | +1 | +1 |
| `zaitsev-2000-ms2000-045` | 33 | 32 | -1 | -1 |
| `kems-037-richter-2002` | 4 | 0 | -4 | -4 |
| `kems-026-markova-1984` | 147 | 141 | -6 | -6 |
| `guo-2021-mgo-activity-cmas-slag` | 16 | 8 | -8 | -1 |
| `itoh-hino-banya-1998-spinel` | 10 | 2 | -8 | -8 |
| `kems-010-richter-2007` | 360 | 350 | -10 | -10 |
| `kems-015-hashimoto-1983` | 681 | 666 | -15 | -15 |
| `kems-016-stolyarova-1992` | 22 | 0 | -22 | -9 |
| `kems-005-fedkin-2006` | 27 | 0 | -27 | -14 |

Seat commits after `18e34f680`, one cause each. All of these update tests. No predicate was weakened and no lineage list was deleted.

- `736bfb939` (a) Costa 2025 and Rys 2007. `2ee0a7c72` (first on the seat in F15 `2544107c9`) drops unprinted `total_pressure_Pa` from point conditions. Those sources do not print a total pressure (`tag: unknown`). The heat-of-fusion identity pressure is `not_applicable`, so the assertion is `pressure is None or not pressure.is_value`.
- `1fc9431ea` (a) m15. The Rys Table 7 census compares the migration to the printed point list instead of the literal 60.
- `94c45fb17` (a) Richter 2002. `0af2c9718` (merge r17a3 `4679aa7d9`) leaves the 24 context cells at 1.87e-4 bar H2 as points of 18.7 Pa. The experiment pressure stays unknown.
- `d170a39b8` (a) Stolyarova calculated oxygen columns are quoted (`4e0819f6d`, `3de591b81`, merge `394c37f47`). Quoted counts 26 and 55.
- `481831b91` (a) same cause, the model-derived residual slice. Those ids are absent from the slice, so both lengths are 0.
- `a93afbc80` (a) Borisov 2018 composition. `1175b49fc` adds article Table 1 (31 rows, no `composition_mol`). Spreadsheet rows 155–185 are Table 1. The 404 quoted Table 2 rows still carry `composition_mol`.
- `c7f47eb17` (a) Fedkin Table 3 alphas are context (`7baaf334b`, merge `4679aa7d9`). Langmuir free evaporation does not inherit the chamber pressure. k04 corrupts Shirai 2000's six stored alpha points.
- `2517e318e` (a) alpha census. `5227cb93b` had left the 12 owner Fedkin cells in the count (34→22). After `7baaf334b` they are not series points. The regen count is Shirai 6 plus Zhang 2021 Table 4's 4, so the pin is 10.
- `90ec0181a` (a) Chen 2019 Cp printed `J/mol/K` (`07d0feef1`, merge F30 `0b8214859`) is stored as the printed joule cell.
- `0e2b96af2` (a) Klemme 2011 prints `J/mol K` (`cdad0b3cd`, merge F24 `2bb15e2b0`). `_convert_compilation_amount` drops whitespace, so the census oracle uses the same heat tokens.
- `5809dfade` (a) DeMaria 1973. `829ce3701` and `243fb508c` record one `initial_composition_conflict` between samples 10017 and 12073. The 61 Table I rows still migrate.
- `149153728` (a) Shornikov 2020 Table 3 oxygen. `45cea9498` (merge `76c04d3fa`) types those rows `measured_reduced` with a Hertz–Knudsen derivation and no observation inputs, so each row is the selected source-oxygen condition. The exclusion lists `derived_from` and does not set `match` (`9c40ad728`). The sample Ca row still scores.
- `f936e0e85` (a) residue-oxide green parity. The child stderr was `ImportError: cannot import name phase_for_sample_form`. `e47e33186` adds that name and `81256c01a` imports it; neither is in green pin `a822c275f`. The child binds the worktree package, then installs the green migrate module. `real_total` stays 0. Pytest passed (11.89s on the final copy).
- `b3a63eb0d` (a) Kress–Carmichael Table 5 (`8cc62e193`, merge `6b0378d4f`) prints Fe2O3 and FeO wt% and stores `fe3_fe2_ratio_from_oxide_wt_pct`. The census oracle now calls that function.
- `7cc5662e8` (a) isotope census. `82e127173` stores exactly one `<quantity>_*` column. Neuman 2026 (`2751617f2`, merge `d357e86db`) prints `isotope_delta_delta41K_NIST_SRM_3141a_permil` and `isotope_delta_Delta41K_sample_minus_starting_permil` on separate series. Two columns on one row stay unselected. The oracle matches `select_declared_source` for the canonical field, for that Neuman name, and for two columns.
- `61b58d093` (a) F22 merge `1b5b56761`. Ozawa 2012 (`45765e41f`) adds 42 evaporation-rate, 86 mass-loss fraction, and 6 isotope cells. Wang 2001 (`d2143313b`) adds 90 isotope cells and 432 residue cells.
- `5e3d8f9ae` (a) Neuman 2026 adds 96 isotope cells and 2160 residue cells. Isotope pin 214 (Sossi 22, Wang 90, Ozawa 6, Neuman 96).
- `632f2e085` (a) F15 merge `2544107c9` residue cells: Mendybaev 2013 96 (`4e07bd649`), Florin 2025 50 (`0f4e598e4`), Renggli 2022 36 (`7a90bab6a`), Lu 2024 27 (`21d1ee8d7`).
- `d66957d6d` (a) Yakovlev 2017 residue 34 (`fc7c11533`, merge F20 `23a54e9b3`). Residue pin 4353.
- `1a9aa1fb5` (a) series-census total. mismatches stays 0. Asserted quantities sum to 5535. The other 2716 cells are cp 1629, S 279, activity 316, delta_fG 148, delta_fH 152, log10_Kf 78, fe3_fe2_ratio 51, valence_ratio 45, and mass_loss_areal_density 18. The pin moves 3136→8251.
- `a94f2ba00` (a) Sossi Table 4 gamma successors. `dc7eafe24` adds them and `5f44bd3ee` copies the parent derivation, so they migrate as model_derived: 26 Sossi plus 24 ms2000, 50 in all. Union 91→113 and headline 55→77 by that same 22.

Re-run of the 66 previously-new failures on that regen, split by file, with the final tests: 60 passed, 6 failed.

Passed: Costa, Langmuir (`test_kems_background_not_identity`), the evaporation extract, property count, residue parity, Rys (including m15), four source-oxygen cases, nine sweep-gas cases (including Richter H2), 22 `test_migrate_r7` cases, the previously-red path-ratchet node (passed in 66.28s), all nine `test_migrate.py` nodes (Borisov composition, DeMaria, Fedkin alpha, j01 census, k01, k04, Plante, both Stolyarova pins), the score residual node, five of the six literature-extract nodes, and the three current t1225 nodes. The saved t1225 ids still named the old parameter tuple (`…-555-1`). The collected ids are `…-2-1-555`, `…-1-0-983`, and `…-2-1-1370`, and those three passed. The locator split already expects physical `page` and printed `published_page` separately.

Still red:

- r16 and r17, `test_committed_store_has_no_stale_migrate_input_commits`. Left red. d-255: the committed store is stale against migrate inputs. The regen store is dirty and was not committed, so it does not move the commit those tests read.
- (c) Markova Table 2, `tests/battery/test_migrate_composition.py:2563`. The two zero mass-loss rows stay unknown with reason `no single temperature_K mapped from source`. The test requires `'T_range_K'` in that reason. L1 `40d6a298c` and repair `042d48b56` keep observation-level ranges off. Question: should that unknown-temperature reason name `T_range_K`? I did not assign a temperature.
- (c) Markova apparatus, `tests/test_literature_extracts.py:2256`. `len(hard_issues)` is 0; the test expects 8. The validation issues on the migrated file are `IDENTITY_INCOMPLETE`, and the failure text shows `required axis sample_mass_kg is unknown`. Question: should those apparatus rows gain a printed sample mass or a total pressure? I did not invent either.
- (c) Borisov 2018 redox population, `tests/battery/test_redox_extract_scoring.py:398`, `assert None is not None` on the glass-fixture residual numeric. `score.py` was not edited. The refusal measured on this seat is `identity_mismatch` on `fO2_Pa`: the scorer rebuilds the pressure as `Decimal(str(100000.0 * 10.0 ** float(fO2_log)))`, and a quoted row differs from the stored Decimal by about 4.8e-12 Pa. Question: should an fO2_Pa difference of about 4.8e-12 Pa refuse the residual as an identity mismatch? I did not loosen identity equality and I did not change the float cast.
- (c) Borisov 2018 redox headline, `tests/battery/test_redox_extract_scoring.py:540`. The population set is only `independent_source_accuracy`. The test also expects `calibration_source_overlap_fit_fidelity`. Question: should that calibration-overlap population be emitted when the fO2 identity mismatch leaves the numeric headline counts at zero? I did not add the record.

No (b) regression turned up in this round. The open items above are the ones I am not deciding.

