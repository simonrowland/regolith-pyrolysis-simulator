# Source audit: shornikov-2019-mgo-titania-melts

## Table-cell audit

Table 1 has 19 printed body rows and 22 numeric cells. The complete numeric spellings were present in both the MinerU cell layer and pdftotext-layout; the visible table on the 600 dpi PDF page 2 render confirms the row and column assignment. Six cells in the 1700–2030 K, 1700–1903 K and 1700–1930 K rows were entered by hand because the table tool's point-temperature mapping cannot carry interval temperatures. The other 16 Table 1 cells were emitted, then split into center values and their printed ± text because the emitter preserves ± as one value string. All 22 rows retain the full cell under `value_as_published` and `uncertainty`.

| Observation | CSV row/column | Compound | T as printed | Cell as printed | Reference column | Audit |
|---|---:|---|---|---|---|---|
| `shornikov_2019_mgo_titania_melts_p002_t01_r02_c04` | 2/4 | Mg2TiO4 | 298 | `-0.26±0.21` | Todd, 1952 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r03_c03` | 3/3 | Mg2TiO4 | 298 | `-5.69±0.33` | Kelley et al., 1954 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r04_c03` | 4/3 | Mg2TiO4 | 973 | `-1.40±0.73` | Wechsler and Navrotsky, 1984 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r05_c05` | 5/5 | Mg2TiO4 | 1573 | `-7.11±0.70` | Brezny and Muan, 1971 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r06_c05` | 6/5 | Mg2TiO4 | 1673 | `-8.09±1.26` | Evans and Muan, 1971 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r07_c03` | 7/3 | Mg2TiO4 | 1700-2030 | `-0.68±0.09` | Bale et al., 2016 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r07_c04` | 7/4 | Mg2TiO4 | 1700-2030 | `5.34±0.05` | Bale et al., 2016 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r08_c04` | 8/4 | MgTiO3 | 298 | `-2.30±0.21` | Shomate, 1946 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r09_c03` | 9/3 | MgTiO3 | 298 | `-13.26±0.46` | Kelley et al., 1954 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r10_c04` | 10/4 | MgTiO3 | 298 | `-1.39±0.15` | Robie et al., 1989 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r11_c03` | 11/3 | MgTiO3 | 973 | `-10.55±0.65` | Wechsler and Navrotsky, 1984 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r12_c05` | 12/5 | MgTiO3 | 1573 | `-10.04±2.09` | Brezny and Muan, 1971 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r13_c05` | 13/5 | MgTiO3 | 1673 | `-10.67±1.05` | Evans and Muan, 1971 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r14_c03` | 14/3 | MgTiO3 | 1700-1903 | `-10.26±0.06` | Bale et al., 2016 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r14_c04` | 14/4 | MgTiO3 | 1700-1903 | `0.91±0.03` | Bale et al., 2016 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r15_c04` | 15/4 | MgTi2O5 | 298 | `-0.23±0.28` | Todd, 1952 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r16_c03` | 16/3 | MgTi2O5 | 298 | `-6.22±0.60` | Kelley et al., 1954 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r17_c03` | 17/3 | MgTi2O5 | 973 | `-4.67±0.57` | Wechsler and Navrotsky, 1984 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r18_c05` | 18/5 | MgTi2O5 | 1573 | `-9.48±1.39` | Brezny and Muan, 1971 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r19_c05` | 19/5 | MgTi2O5 | 1673 | `-10.18±1.12` | Evans and Muan, 1971 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r20_c03` | 20/3 | MgTi2O5 | 1700-1930 | `-3.27±0.05` | Bale et al., 2016 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p002_t01_r20_c04` | 20/4 | MgTi2O5 | 1700-1930 | `4.24±0.03` | Bale et al., 2016 | MinerU + pdftotext agree; 600 dpi PDF p2 confirms row/column placement |

Table 2 has 12 body rows and 20 numeric cells. Each cell was emitted and both machine reads agree on the sign and digits; the 600 dpi PDF page 3 render confirms row/column placement.

| Observation | CSV row/column | Printed row label | Cell as printed | Audit |
|---|---:|---|---:|---|
| `shornikov_2019_mgo_titania_melts_p003_t02_r03_c02` | 3/2 | MgO | `-355.987` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r03_c04` | 3/4 | MgO | `-329.234` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r03_c06` | 3/6 | Mg | `-72.400` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r04_c02` | 4/2 | $Mg_{2}TiO_{4}$ | `-1300.488` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r04_c06` | 4/6 | Mg2 | `20.659` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r05_c02` | 5/2 | $MgTiO_{3}$ | `-932.105` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r05_c06` | 5/6 | MgO | `-120.207` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r06_c02` | 6/2 | $MgTi_{2}O_{5}$ | `-1496.736` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r06_c04` | 6/4 | $MgTi_{2}O_{5}$ | `-1511.848` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r06_c06` | 6/6 | Ti | `170.472` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r07_c02` | 7/2 | $TiO_{2}$ | `-551.661` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r07_c04` | 7/4 | $TiO_{2}$ | `-551.278` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r07_c06` | 7/6 | TiO | `-136.688` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r08_c06` | 8/6 | $TiO_{2}$ | `-334.779` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r09_c06` | 9/6 | $Ti_{2}O_{3}$ | `-641.289` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r10_c06` | 10/6 | $Ti_{2}O_{4}$ | `-794.803` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r11_c06` | 11/6 | O | `110.091` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r12_c06` | 12/6 | $O_{2}$ | `0.000` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r13_c06` | 13/6 | $O_{3}$ | `283.608` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |
| `shornikov_2019_mgo_titania_melts_p003_t02_r14_c06` | 14/6 | $O_{4}$ | `209.765` | MinerU + pdftotext agree; 600 dpi PDF p3 confirms row/column placement |

The current `compare` command only enumerates `values.points`, while `emit` creates scalar `values.value` rows. Its zero-row output is preserved separately. All Table 1 and Table 2 cells were audited against pdftotext and render placement above; no table cell spelling corrections were needed.

## Text and schema gaps

The 54.343 kJ/mol MgTi2O5 fusion enthalpy at 1930 K is quoted to Bale et al. (2016). The approximate MgTiO3 formation enthalpy `≈−856 kJ/mol` at 298 K is preserved as an approximate author estimate; its sentence has no explicit source attribution. The MgO and MgTiO3 relative vapor amounts remain typed refusals: the source prints an upper limit `not more than 0.1% of the Mg amount` over 2100–2400 K, but the schema has no token for this ratio. The `more than 10 kJ/mol` comparison of ΔGm near MgTiO3 concentration is also typed-refused because neither its mixing-energy-difference quantity nor exact temperature/composition is represented.

Table 1's values are formation enthalpy, entropy, and Gibbs energy from simple oxides. The scalar `delta_fH` / `delta_fG` tokens do not retain that reaction basis, and no formation-entropy token exists, so all 22 values remain typed refusals with their centers, uncertainties and printed basis preserved.

## Figure audit

PDF pages 2–4 were rendered at 600 dpi. Figure 1 is a phase diagram, not a plotted property marker series. Figures 2–4 identify their plotted series as calculations or works' determinations; the visible circles and triangles are points along model curves. The figure rule excludes numeric sampling of model curves, so no axes were calibrated and no figure pixel reads were recorded. Sixteen typed-unavailable curve records retain Fig. 2's eight component/temperature/source combinations, Fig. 3's four MgO–TiO2 mixing-energy combinations, and Fig. 4's four cited binary-system curves. The unavailable figure curves use unknown composition because each spans the x-axis. The focused test pins three figure curve records at literal temperatures and asserts their unavailable status; pixel reads are not present because the figure rules prohibit sampling these calculated curves.

## Isolated migration

The isolated `Migrator` run produced 63 observations: 37 point values, 25 unavailable values, and one categorical record; 22 rows are admitted and 41 are rejected. It reported zero registry issues and zero hard issues. Every row carries a `temperature_K` point condition with a locator. The nine rows without a point-temperature identity retain the printed interval for six Table 1 cells, the 2100–2400 K interval for two vapor-limit statements, and an unknown condition for the prose comparison whose temperature is unspecified. The fusion quote migrated as 54.343 kJ/mol at 1930 K; the approximate MgTiO3 estimate migrated as an approximate −856 kJ/mol point at 298 K. Table 1 entropy and mixing-energy differences have no quantity token; those gaps remain explicit. The source-local compare output reports zero matched and zero candidate rows because the command checks `values.points` while emit produced scalar `values.value` rows; the table audit above independently verifies all 42 numeric cells.

## Verification

The focused extract test passed (1 test), the YAML flow-scalar comma guard passed (17 tests), and the source-specific extract validator reported `OK: 1 extract file(s) valid`. A mutation check changed the pinned MgO value from `-355.987` to `-355.986`; the focused test failed at that literal pin, and the original extract was restored. The required `tests/test_literature_extracts.py` run reached its 300 s limit after reporting a failure at 24%. A fail-fast run stopped at `test_repo_extracts_validate_green`: two pre-existing `recalculation_table` type errors in `cottrell-2009-iron-xanes.yaml` (119 passed, 1 failed, 165.99 s). The failing extract is outside this source's changes.
