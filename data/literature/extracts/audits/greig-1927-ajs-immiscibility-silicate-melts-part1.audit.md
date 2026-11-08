# Greig (1927), Part I — CaO-SiO2 and MgO-SiO2 audit

## Scope and printed results

The PDF has no text layer. Transcription used the MinerU table blocks and markdown first, then the independent Tesseract `eng`, 300 dpi, `psm 6` layout. The source's own text calls the Table III values the temperature of equilibrium between cristobalite and two liquids. It restates CaO-SiO2 at 1698 °C and MgO-SiO2 at 1695 °C on page 30. The extract keeps the three existing observation IDs stable; the two binary equilibrium temperatures remain `transition_point` observations in printed °C.

The source does not print a numeric consolute temperature or the exact less-siliceous conjugate compositions in a table or prose. The less-siliceous compositions are described as extrapolations of the liquidus curve to the Table III temperature. Fig. 3 (MgO-SiO2, page 15) and Fig. 4 (CaO-SiO2, page 18) contain curve-only coordinates and are deferred. No curve values were read. For the more-siliceous liquids, page 16 prints a bound of over 98 wt% SiO2 for both binaries; Table V prints interpolation results at 1710 °C: 0.8 MgO / 99.2 SiO2 and 0.6 CaO / 99.4 SiO2. No Table V composition is printed for the 1744 °C index readings.

The source calls the Table III values “the temperature of the equilibrium,” not “monotectic.” They are the printed invariant temperatures for cristobalite plus two liquids and are carried under the task's requested binary equilibrium/monotectic role. No exact consolute point is asserted from the 26.5/73.5 CaO-SiO2 quenches: Table IV B prints 1700 °C, while page 18 prose says 1710 °C for two liquids and 1740 °C for one homogeneous liquid. Both values are preserved.

## Full-cell audit

### Table III, PDF page 14

The printed table has 11 run rows: 2–8 and 12–15. The binary outcome columns contain 18 populated charge/result cells: nine CaO-SiO2 and nine MgO-SiO2. Each is retained with the row's printed temperature and duration and the charge composition from its column heading. Run 6 has no MgO-SiO2 result cell; blank cells were not filled. The row/column layout and cell assignments were checked against the page render.

| Run | T (°C) | Time (min) | CaO-SiO2 charge → result | MgO-SiO2 charge → result |
|---:|---:|---:|---|---|
| 2 | 1703 | 20 | 15/85 → 2 glasses | 15/85 → 2 glasses |
| 3 | 1701 | 20 | 15/85 → 2 glasses | 15/85 → 2 glasses |
| 4 | 1697 | 15 | 15/85 → CaO-rich glass + cristobalite; not well sintered | 15/85 → 2 glasses |
| 5 | 1697 | 20 | 15/85 → as in run 4 | 15/85 → considerable cristobalite + 2 glasses |
| 6 | 1700 | 20 | 15/85 → 2 glasses | blank |
| 7 | 1700 | 20 | 5/95 → 2 glasses | 15/85 → 2 glasses |
| 8 | 1699 | 20 | 5/95 → 2 glasses | 15/85 → 2 glasses |
| 12 | 1698 | 20 | 15/85 → 2 glasses, not well separated | 15/85 → 2 glasses |
| 13 | 1696 | 20 | 15/85 → not sintered; cristobalite + CaO-rich glass | 15/85 → 2 glasses, not well separated |
| 14 | 1694 | 20 | blank | 15/85 → cristobalite + MgO-rich glass |

The table's equilibrium row prints 1698 °C for CaO-SiO2 and 1695 °C for MgO-SiO2. Neither has a printed uncertainty. The Tesseract read of run 4's temperature was `1607` and run 13's was `16096`; MinerU read 1697 and 1696. The page render confirms 1697 and 1696. These two corrections are explicitly pinned by the focused test.

The 18 binary run rows use the page 4 method's quench procedure and carry `preservation: not_stated`: the paper says charges were quenched and examined microscopically, but does not assert that glass preserved the high-temperature phases. For the numerical Table III cells, the two machine reads agree except the run 4 and run 13 temperatures listed above; those two are render-confirmed (b), and the remaining cells use both-read agreement with the table layout checked on the render (a).

### Table IV, PDF page 17

The printed Table IV contains 10 MgO-SiO2 runs in subtable A and 15 CaO-SiO2 runs in subtable B. All run rows are retained as outcome brackets, not fitted limb points. The compositions are wt% oxide pairs. Both machine reads agree on the numeric cells; the A/B row and column assignment was checked once against the render (a).

| Subtable A: MgO/SiO2 | T (°C) | min | Printed products |
|---|---:|---:|---|
| 2/98 | 1710 | 30 | 2 glasses |
| 15/85 | 1697 | 20 | 2 glasses |
| 15/85 | 1694 | 20 | Cristobalite + glass |
| 26/74 | 1694 | 30 | Cristobalite + glass |
| 26/74 | 1710 | 20 | 2 glasses |
| 30/70 | 1700 | 20 | 2 glasses |
| 31/69 | 1692 | 20 | Cristobalite + glass |
| 31/69 | 1696 | 15 | 1 glass |
| 32/68 | 1667 | 40 | Glass |
| 32/68 | 1654 | 40 | Cristobalite in fair amount + glass |

| Subtable B: CaO/SiO2 | T (°C) | min | Printed products |
|---|---:|---:|---|
| 2/98 | 1710 | 30 | 2 glasses |
| 5/95 | 1699 | 20 | 2 glasses |
| 10/90 | 1700 | 20 | 2 glasses |
| 15/85 | 1697 | 20 | Cristobalite + glass |
| 15/85 | 1700 | 20 | 2 glasses |
| 20/80 | 1702 | 20 | 2 glasses |
| 26.5/73.5 | 1700 | 30 | 2 glasses |
| 26.5/73.5 | 1740 | 20 | 1 glass |
| 28/72 | 1693 | 40 | Cristobalite + glass |
| 28/72 | 1700 | 20 | 1 glass |
| 31.8/68.2 | 1650 | 30 | All glass |
| 31.8/68.2 | 1627 | 30 | Rare cristobalite in glass |
| 35/65 | 1459 | 30 | Cristobalite + glass |
| 35/65 | 1471 | 30 | Cristobalite + glass |
| 35/65 | 1480 | 30 | Scattering of cristobalite in glass; liquidus about 5 °C higher |

Table IV's row and column assignment was checked against the page render. The subtable A/B labels are printed below the Table IV caption. The page 18 prose/table discrepancy for 26.5/73.5 is retained, not averaged or resolved.

Each Table IV run row also records the page 4 quench method as `not_stated` for preservation. The products are the printed post-quench brackets.

### Table V and prose, PDF pages 16–19 and 44

| System | 1710 °C index | 1744 °C index | Reference index and printed reference composition | Composition printed by interpolation at 1710 °C |
|---|---:|---:|---|---|
| MgO-SiO2 | 1.461 | 1.462 | 1.580¹; reference glass 40.15 MgO / 59.85 SiO2 | 0.8 MgO / 99.2 SiO2 |
| CaO-SiO2 | 1.460+ | 1.462− | 1.589²; reference glass 37.0 CaO / 63.0 SiO2 | 0.6 CaO / 99.4 SiO2 |

The table also prints the silica glass refractive index 1.4584. The reference footnotes identify Larsen for the MgO glass and Bowen for the CaO glass. The CaO index has plus/minus direction marks; they were preserved. MinerU QA flagged two fused reference-footnote digits and LaTeX-like cells on page 19 and skipped numeric comparison because the source is scanned. The page render confirms 1.580¹, 1.589², 1.460+, and 1.462−. The focused test pins these rendered cells. The page 18 prose states silica content from Table V is accurate to “better than 0.5 per cent”; each interpolated composition retains that bound with confidence level `not_stated` and the exact phrase in `value_as_published`.

Page 16 states the more-siliceous MgO-SiO2 and CaO-SiO2 liquids contain over 98 per cent SiO2. The context records `> 98` for both systems and notes that the statement is a bound, not an exact composition or numeric-temperature tie-line point. Page 16 describes the less-siliceous limb as an extrapolation to the Table III equilibrium horizontal; its numeric value is not printed. Figure 3 and Figure 4 readings remain deferred.

The page 20 prose records a 5 CaO / 95 SiO2 **starting mixture** melted on a Pt-Rh ribbon and forming two liquids; the extract marks it `composition_role: initial_charge_only`. The Figure 12 caption (page 26) reports cristobalite formed during quenching, so this row carries `asserted_altered`. The discussion on pages 43–44 describes the resulting ribbon droplet's analyzed liquid phase as about 27.5 CaO / 72.5 SiO2, gives a ribbon temperature of about 1725 °C and a liquidus of 1698 °C, and says chilling was imperfect with cristobalite forming. The source therefore gives a starting-mixture composition and a distinct liquid-phase composition, not contradictory values for the same material. The approximate composition remains a prose context record, marked as the quenched glass phase and not as a conjugate endpoint.

The page 44 ten-gram comparison is a separate **starting charge** at 37 CaO / 63 SiO2, completely quenched by plunging under water. Its 1700 °C value is stated as a viscosity-comparison condition, not its quench setpoint. The approximate 27.5 / 72.5 ribbon-droplet liquid composition, its about 1725 °C ribbon temperature, 1698 °C liquidus, and imperfect quench remain separately recorded.

## Schema and lineage disposition

Table III and Table IV run outcomes are stored as context because `phase_equilibrium_run_products` is not in the closed `tools/validate_literature_extracts.py::OBSERVATION_TYPES` set. Their numeric temperature, duration, charge composition, and printed microscopic products remain available as brackets. The binary equilibrium temperatures already have the exact `transition_point` type and `transition_temperature` quantity.

Table V's `equilibrium_phase_composition` quantity exists (`simulator/battery/enums.py::Quantity.EQUILIBRIUM_PHASE_COMPOSITION`) and is used for comparable data in `park-2021-vanadium-thermodynamics-thesis.yaml`. The four printed Table V composition cells are now observations of the measured glass phase with `measured_reduced` evidence: each component comes from the authors' printed interpolation of refractive index as linear with composition. The printed refractive-index readings and attributed reference-glass values remain in located context rows; each scored composition row cites its corresponding context row as derivation input. The existing `simulator/battery/migrate.py::lineage_parents_from_source` path resolves those qualified context references, and `simulator/battery/validate.py::_validate_observation` permits context records as parents. The composition rows retain the page 18 "better than 0.5 per cent" bound with confidence level `not_stated`; the refractive-index inputs are not misrepresented as scored refractive-index quantities.

The page 44 composition remains a context record: the author calls it “about” and describes the analyzed liquid phase in a quenched ribbon-droplet example, not a liquid-liquid conjugate endpoint. The less-siliceous compositions and numeric consolute point are not table/text values and remain deferred to Figs. 3 and 4.

## Isolated migration

The real migrator emitted 7 observations, 0 hard issues, 0 unavailable values, and 0 queued items. The migration returned no quantity-unknown or phase-unknown observations. The three original transition observations are preserved, and the four Table V component values migrate through `equilibrium_phase_composition`. Migrated uncertainty kinds are `printed: 5` and `none: 2`; confidence counts are `not_stated: 5` and absent on two rows. Each of the four Table V observations retains the printed `[-0.5, 0.5] wt%` width with confidence unstated, and the original melting-point observation retains its symmetric ±5 °C width with confidence unstated.

For the three migrated transition-temperature observations, the identity profile reports composition not applicable, total pressure unknown, fO2 not applicable, and temperature not applicable. Their measured temperature values are 1986.15 K, 1971.15 K, and 1968.15 K after migration from the printed 1713 °C, 1698 °C, and 1695 °C. For the four Table V composition observations, the identity composition is populated from the printed binary composition, temperature is 1983.15 K from the printed 1710 °C, and total pressure and fO2 remain unknown. The focused isolated-migration test checks these four axes; it does not add assumed atmospheric conditions.

Representative migrated identity axes from the isolated run:

| Observation | Composition identity | Total pressure | fO2 | Temperature |
|---|---|---|---|---:|
| `greig1927_t5_mgo_siliceous_glass` | MgO 0.01187932489735118912866695045 / SiO2 0.9881206751026488108713330495 mole fraction | unknown | unknown | 1983.15 K |
| `greig1927_t5_mgo_siliceous_glass_MgO` | MgO 0.01187932489735118912866695045 / SiO2 0.9881206751026488108713330495 mole fraction | unknown | unknown | 1983.15 K |
| `greig1927_t5_cao_siliceous_glass` | CaO 0.006425870638110853568968368582 / SiO2 0.9935741293618891464310316315 mole fraction | unknown | unknown | 1983.15 K |

The focused test pins the Table III render corrections, Table IV run bracket, all four migrated Table V composition values and the rendered index cells, the page 16 composition bounds, the initial-charge and analyzed-phase roles on pages 20 and 44, the three unchanged transition IDs and values, uncertainty counts, unknown pressure/fO2 conditions, and context-parent lineage. It also asserts the printed counts: 18 binary Table III cells, 10 Table IV A rows, and 15 Table IV B rows.

`mineru_table_draft.py compare` output is committed beside this audit. It reports 96 locator errors and 13 unverifiable locators (0 matched, no-match, mismatch, or presence-only cell comparisons); the errors chiefly reflect the caption recognizer seeing only the base table labels for subtables (`II A/B`, `IV A/B/C`, and `VII A–E`). It does not emit cell comparisons for these scalar/context records, so the cell audit above is the evidence for their numeric values. The row and column assignments were checked against the renders as stated by table; flagged Table III and Table V cells are individually listed above. MinerU QA marked 14 tables as scan tables; p14 and p17 have no digit-agreement mismatch flags, and p19 was render-checked for fused footnotes and index marks.

## Review fix-round verification

The focused Greig migration test passes and pins the four component values; changing the MgO-system SiO2 input from `99.2` to `99.8` makes it fail, while the unchanged source passes. `tests/test_yaml_flow_scalar_commas.py` passes (17 tests). The repo-wide `tests/test_literature_extracts.py` selection hit the 300-second local process cap before completing, including a second split attempt; record that suite as `UNVERIFIED-local`. The requested `tests/battery/test_migrate.py -k j01` run hit the same cap in `test_j01_store_census_series_numeric_matches_declared_field`; the other J01 cases pass (9 passed when that census case is excluded), so record the census case as `UNVERIFIED-local`.

Scratch full-store regeneration reports 186,648 observations, 784 works, 7,000 experiments, queue 124,419, and 1,685 hard issues, equal to the green baseline hard-issue count. `scripts/battery_validate_store.py --root <scratch>` exits 0 with forbidden-shape count 0; scratch store SHA-256 is `fa2b9173b08595a7e3496337e3b7182bc81b31d5411c61b63dab12fc10f924ff`. Relative to green, the candidate store delta is +4 observations and -6 queued items, with hard issues unchanged. No generated store files are part of the commit.
