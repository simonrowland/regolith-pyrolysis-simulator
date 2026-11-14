# Shornikov et al. (1997), diopside vaporization — extraction audit

## Scope and source reads

This repairs the existing Shornikov extract and completes its printed Tables 1–4. Input order was the MinerU table blocks and markdown, followed by `pdftotext-layout.txt`. The paper is born-digital: MinerU and pdftotext agree on the table digits, while the page renders settle row and column assignments and the flagged Table 3 cells. Table 1–3 layout was checked on physical PDF page 2 at 600 dpi. Table 4 was checked on physical PDF page 3 at 600 dpi because MinerU classified all value rows as headers and emitted no body rows.

The companion [cell audit](shornikov-1997-diopside-vaporization.cell-audit.csv) covers 144 entries: all numeric and dash cells in Tables 1–4, caption ranges, and four located numeric facts retained from the experimental description. Physical pages, printed table numbers, grid coordinates, cell text, stored values and uncertainty widths are recorded there. Table 3’s QA flags were reconciled against its page render. Table 4’s signed values and widths were transcribed from the render.

## Printed row and cell reconciliation

| Printed table | Printed rows | Numeric table cells | Printed dashes | Extraction result |
|---|---:|---:|---:|---|
| 1, page 2 | 9 species rows | 50 pressure cells and 6 temperature headings | 4 | All pressure rows retained. The two O rows remain distinct as O and O*. The four missing pressure cells remain dashes. |
| 2, page 2 | 7 fit rows | 14 A/B coefficient cells | 0 | All seven author-reduced pressure fits retain both coefficients and their printed widths. Their migrated pressure values remain unavailable because the printed form is a temperature relation, not a point. |
| 3, page 2 | 14 rows across two side-by-side blocks | 14 temperatures and 28 activities | 14 | All 18 Method I values and 10 values attributed to references 22, 23, 25, 26, 29 and 30 are retained. Every dash remains unreported. |
| 4, page 3 | 3 component rows | 6 value cells | 0 | All three enthalpies and three partial entropies retain their printed value and symmetric width. The three entropy observations remain typed-refused for the exact schema gap below. |

Tables 5–7 are thermodynamic compilations and comparisons outside this vapor-pressure and melt-activity extract. The source contains additional prose about congruent melting at 1665 ± 2 K; it is not a vaporization point and was not mixed into the tabulated observations.

The compare command output is preserved in [compare.csv](shornikov-1997-diopside-vaporization.compare.csv). Its 78 raw `mismatch` rows are all reconciled in [compare-resolution.csv](shornikov-1997-diopside-vaporization.compare-resolution.csv), which records the exact printed grid cell for each one. Table 1 stores pressure physically in atm after applying a different printed power-of-ten factor for each species row; the generic compare cannot apply those row-specific factors. Table 3 needs the correct left/right block, row and component column; its extract observations do not encode those grid coordinates. The render-confirmed cell audit resolves the row assignments and values. The corrected source locators produce zero compare locator errors and zero unverifiable locators.

## Target observables and engine readiness

The engine observables read from `simulator/battery/score.py::rail_for_quantity` and the adapters are `p_partial` for gas partial pressure and `activity` for oxide activity in liquid. SiO and SiO2 pressure rows route through the SiO evolution rail; the other gas species route through the vapor rail. Table 4’s typed partial molar enthalpy has no headline engine rail. Partial component entropy has no exact quantity token.

The readiness counts below distinguish typed identity axes from the validity gates. The latter remain authoritative and are reported even when a row has its required identity fields.

| Observable | Rows | Typed-axis ready | Missing inputs or gate result |
|---|---:|---:|---|
| Table 1 `p_partial` points | 50 | 0 | `identity.total_pressure_Pa`: 50 not published; `identity.fO2_Pa`: 44 unknown; `identity.composition`: 12 unknown for MoO2/MoO3 cell-vapor rows. The six Ca points have located source oxygen, but remain typed-axis incomplete because total pressure is not printed. Their per-engine scorer outcomes are recorded separately below. |
| Table 2 `p_partial` fits | 7 | 0 | The source prints seven A/B temperature relations, not seven numeric pressure points. The migrator emits seven unavailable values, preserving the full printed coefficients in the source extract. |
| Table 3 `activity` points | 28 | 6 | The six Method I MgO points have composition, temperature, and typed pure-solid MgO reference state. Twelve Method I CaO/SiO2 points lack a stated endmember phase for the pure-oxide reference. The ten cited values lack per-run composition, fO2, and activity reference state. All 18 measured Method I points fail the existing KEMS pressure-sum and geometry gates; the 10 quoted rows bypass measured-apparatus gates but still lack required identity inputs. |
| Table 4 partial molar enthalpy | 3 | 0 | `rail_for_quantity` returns no engine rail. The rows also have a temperature range rather than a point and lack required standard-pressure, reference-state, total-pressure and per-basis identity axes. |
| Table 4 partial component entropy | 3 | 0 | Typed-refused: no exact schema token for the printed quantity. |

Table 1 contains no chamber/system total pressure. The extract records it as not published and does not add an assumed pressure. The nominal initial charge is CaO:MgO:SiO2 = 1:1:2 on a mol-inventory formula basis, linked to the Table 1 sample by its caption and the experiment record. The Experimental section reports Al2O3 and Fe2O3 impurities whose sum is at most 0.4 mol%; their split is unresolved. No post-vaporization composition, run-order index, mass loss, conversion or fractional depletion is reported. Each absence is located. The paper states that vaporization mainly proceeds through gas-phase pure CaO, MgO and SiO2 species; the extract preserves that wording without assigning an unstated congruent/incongruent label.

The 70.0 ± 0.2 eV ionizing energy and the not-less-than-1:400 orifice/sample area ratio are preserved from the experimental text. The temperature sensor is not published; the calibration is referred to source [27]. The paper does not report individual ion-current ratios or their numeric reduction inputs. Oxygen partial pressure is author-reduced from the cited gas equilibria and ion-current ratios. The source-linked oxygen relation is attached only to the six Ca pressure points as requested.

## Same-cell oxygen linkage and scorer applicability

Each Ca pressure point links to the author-reduced pO2 point at the same printed Table 1 temperature through the existing `source_oxygen_condition` path. The pO2 observations retain their IDs, and the six Ca parent IDs remain stable. The focused migrated-record check confirms that all six linked Pa values equal the same-temperature pO2 records, with no interpolation. `simulator.battery.score::_oxygen_input_request` resolves all six rows as `PO2_COMMANDED` for each of the six engines (36 row/engine requests). This is oxygen-input applicability; the actual `compile_residual` results are below.

The scorer was run through `simulator.battery.score::compile_residual` on the six migrated Ca points, using the finalized isolated migration, all migrated source observations for validity-gate input, and the engine runtime available in this seat. “Predicted” counts match/mismatch outcomes; “no_band” counts numeric residuals without a comparison band; refused counts non-numeric refused results.

| Engine | Predicted (match/mismatch) | No-band numeric | Refused | Refusal reason |
|---|---:|---:|---:|---|
| VapoRock | 0 | 6 | 0 | — |
| AlphaMELTS | 0 | 0 | 6 | `attempted_unavailable`: AlphaMELTS runtime unavailable |
| ThermoEngine | 0 | 6 | 0 | — |
| MAGEMin | 0 | 0 | 6 | `unsupported`: engine does not expose the `vapour species` observable for `Ca` `p_partial` |
| OpenIMCC | 0 | 6 | 0 | — |
| INTERNAL_ANALYTICAL | 0 | 6 | 0 | — |

Four engines therefore produced six numeric no-band residuals each, with no pressure-sum refusal. AlphaMELTS was refused because its provider is unavailable in this seat, and MAGEMin was refused because its adapter does not expose this observable. The scorer was not changed.

OpenIMCC scoring used the declared `66ec4a3` source pin; the imported Python source tree hash was `308672269d6e`.

## Observation ID changes

Finalized isolated migrations compare 81 observations at `d5e6b8dc216bd601ca080d47dd1a53e10168aa6e` with 91 at this extract. The net increase is ten quoted Table 3 activity points. In addition, 38 old observation IDs are replaced by 48 new IDs; 43 existing IDs remain identical, including all six pO2 IDs. The replacement pairs below are exact. Added `value_as_published` spellings enter the point payload hashed by `simulator/battery/migrate.py::_exploded_point_id`, changing the hashes for the Table 1 pressure points whose printed spelling was newly retained and the Table 4 enthalpy points. The six Ca points also contain the linked source oxygen condition. The three Table 4 entropy records changed from parent records to exploded points so each printed uncertainty remains attached to its numeric value.

| Old observation ID | New observation ID |
|---|---|
| `shornikov-1997-diopside-vaporization::shornikov_1997_ca_pressure_table1::T=1566.00:h=a90dcc455f24` | `shornikov-1997-diopside-vaporization::shornikov_1997_ca_pressure_table1::T=1566.00:h=76f9f3d4b5a5` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_ca_pressure_table1::T=1640.00:h=2b64e459539e` | `shornikov-1997-diopside-vaporization::shornikov_1997_ca_pressure_table1::T=1640.00:h=a9d8648c25ba` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_ca_pressure_table1::T=1688.00:h=840501588eb4` | `shornikov-1997-diopside-vaporization::shornikov_1997_ca_pressure_table1::T=1688.00:h=88a2d38a7cc9` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_ca_pressure_table1::T=1743.00:h=4003db48a3e5` | `shornikov-1997-diopside-vaporization::shornikov_1997_ca_pressure_table1::T=1743.00:h=2dbf0bb943d5` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_ca_pressure_table1::T=1778.00:h=80fa0634cb03` | `shornikov-1997-diopside-vaporization::shornikov_1997_ca_pressure_table1::T=1778.00:h=a774506d7968` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_ca_pressure_table1::T=1862.00:h=f7c5d2a5c9a5` | `shornikov-1997-diopside-vaporization::shornikov_1997_ca_pressure_table1::T=1862.00:h=6a9b028ddadb` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_cao_partial_enthalpy_table4::points:h=afb500bd6e40` | `shornikov-1997-diopside-vaporization::shornikov_1997_cao_partial_enthalpy_table4::points:h=a9bca8190a42` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_cao_partial_entropy_table4` | `shornikov-1997-diopside-vaporization::shornikov_1997_cao_partial_entropy_table4::points:h=6c9a068f6111` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_mg_pressure_table1::T=1566.00:h=865b6ca1995f` | `shornikov-1997-diopside-vaporization::shornikov_1997_mg_pressure_table1::T=1566.00:h=8867777e463d` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_mg_pressure_table1::T=1640.00:h=22ab1a9e712d` | `shornikov-1997-diopside-vaporization::shornikov_1997_mg_pressure_table1::T=1640.00:h=eaf82720e0fa` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_mg_pressure_table1::T=1688.00:h=3461d5e0c7ac` | `shornikov-1997-diopside-vaporization::shornikov_1997_mg_pressure_table1::T=1688.00:h=216c77473a71` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_mg_pressure_table1::T=1743.00:h=ebcff1409bc2` | `shornikov-1997-diopside-vaporization::shornikov_1997_mg_pressure_table1::T=1743.00:h=ee7cd82cf166` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_mg_pressure_table1::T=1778.00:h=b61c762f5640` | `shornikov-1997-diopside-vaporization::shornikov_1997_mg_pressure_table1::T=1778.00:h=283921497710` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_mg_pressure_table1::T=1862.00:h=2661d97b01aa` | `shornikov-1997-diopside-vaporization::shornikov_1997_mg_pressure_table1::T=1862.00:h=18f9a832b3e8` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_mgo_partial_enthalpy_table4::points:h=96113f5e44e7` | `shornikov-1997-diopside-vaporization::shornikov_1997_mgo_partial_enthalpy_table4::points:h=f4fe6470c3bf` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_mgo_partial_entropy_table4` | `shornikov-1997-diopside-vaporization::shornikov_1997_mgo_partial_entropy_table4::points:h=776765fa4634` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_alternate_pressure_table1::T=1688.00:h=69b2369d70d1` | `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_alternate_pressure_table1::T=1688.00:h=3ab4f1e714d5` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_alternate_pressure_table1::T=1743.00:h=acc4db904a5a` | `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_alternate_pressure_table1::T=1743.00:h=034337b84cb2` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_alternate_pressure_table1::T=1778.00:h=71a80e965362` | `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_alternate_pressure_table1::T=1778.00:h=a15c98e03f43` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_alternate_pressure_table1::T=1862.00:h=6a6f5c5b2d06` | `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_alternate_pressure_table1::T=1862.00:h=50b843ae3ff3` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_pressure_table1::T=1566.00:h=5e70a1468284` | `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_pressure_table1::T=1566.00:h=b210fad6da02` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_pressure_table1::T=1640.00:h=733f582b29fd` | `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_pressure_table1::T=1640.00:h=926bacf29561` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_pressure_table1::T=1688.00:h=a780cd091105` | `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_pressure_table1::T=1688.00:h=107237abb8f3` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_pressure_table1::T=1743.00:h=4cd8bf15793f` | `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_pressure_table1::T=1743.00:h=3340cb43e2ad` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_pressure_table1::T=1778.00:h=504322b36832` | `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_pressure_table1::T=1778.00:h=f2032453ee53` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_pressure_table1::T=1862.00:h=a4653e772b7e` | `shornikov-1997-diopside-vaporization::shornikov_1997_o_atomic_pressure_table1::T=1862.00:h=b05f8195ea61` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_sio2_partial_enthalpy_table4::points:h=73dd446c23c8` | `shornikov-1997-diopside-vaporization::shornikov_1997_sio2_partial_enthalpy_table4::points:h=28e7e46d8da8` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_sio2_partial_entropy_table4` | `shornikov-1997-diopside-vaporization::shornikov_1997_sio2_partial_entropy_table4::points:h=91e921b526e2` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_sio2_pressure_table1::T=1688.00:h=ad8999830424` | `shornikov-1997-diopside-vaporization::shornikov_1997_sio2_pressure_table1::T=1688.00:h=e5c3a57d561d` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_sio2_pressure_table1::T=1743.00:h=593a2fa9b2af` | `shornikov-1997-diopside-vaporization::shornikov_1997_sio2_pressure_table1::T=1743.00:h=a63badb7f543` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_sio2_pressure_table1::T=1778.00:h=165be609493b` | `shornikov-1997-diopside-vaporization::shornikov_1997_sio2_pressure_table1::T=1778.00:h=a6f574a56419` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_sio2_pressure_table1::T=1862.00:h=101ccba283a1` | `shornikov-1997-diopside-vaporization::shornikov_1997_sio2_pressure_table1::T=1862.00:h=fc4ea0dbe333` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_sio_pressure_table1::T=1566.00:h=ea1c2b992c89` | `shornikov-1997-diopside-vaporization::shornikov_1997_sio_pressure_table1::T=1566.00:h=b12606a24eac` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_sio_pressure_table1::T=1640.00:h=95ad2c728020` | `shornikov-1997-diopside-vaporization::shornikov_1997_sio_pressure_table1::T=1640.00:h=ea5bec189980` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_sio_pressure_table1::T=1688.00:h=5613a174e66b` | `shornikov-1997-diopside-vaporization::shornikov_1997_sio_pressure_table1::T=1688.00:h=da8cf36e70e1` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_sio_pressure_table1::T=1743.00:h=09febc58bf10` | `shornikov-1997-diopside-vaporization::shornikov_1997_sio_pressure_table1::T=1743.00:h=30f3f28a6518` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_sio_pressure_table1::T=1778.00:h=792e7655860f` | `shornikov-1997-diopside-vaporization::shornikov_1997_sio_pressure_table1::T=1778.00:h=80ecdf51a449` |
| `shornikov-1997-diopside-vaporization::shornikov_1997_sio_pressure_table1::T=1862.00:h=8f84f6834d08` | `shornikov-1997-diopside-vaporization::shornikov_1997_sio_pressure_table1::T=1862.00:h=33e4086f81d2` |

The source map is duplicated in structured form in [mapping.yaml](shornikov-1997-diopside-vaporization.mapping.yaml). No generated store files are committed. The expected source-store delta is +10 observations net: 38 prior IDs are removed and 48 IDs are added, including 38 replacements and the ten quoted activity points. All six pO2 IDs remain unchanged.

## Uncertainty, schema gap, and ID reference audit

Isolated migration reports 63 observations with printed uncertainty and confidence `not_stated`, and 28 observations with no printed uncertainty. Table 1 pressure widths and Table 2 coefficient widths retain their source spellings; Table 4 symmetric widths remain at their printed magnitudes with confidence unstated. The temperature-heading widths remain separately stored as condition uncertainty. The migration has exactly three quantity-unknown rows, the three partial component entropies, and no phase-unknown observations.

Those three entropy rows remain typed-refused because the exact quantity and observation-type sets do not represent partial component entropy: `simulator/battery/enums.py::Quantity.S` is absolute entropy; `simulator/battery/migrate.py::QUANTITY_ALIASES` and `simulator/battery/migrate.py::TYPE_QUANTITY` have no exact token; `tools/validate_literature_extracts.py::OBSERVATION_TYPES` has no exact typed observation. The source values and widths are preserved without using a near-match token.

Each of the 38 old IDs was checked with `git grep` across tests and data. Outside this audit and its structured mapping, references are confined to the generated baseline files `data/literature/extracts-v2/shornikov-1997-diopside-vaporization.yaml` and `data/battery/migration-queue.yaml`, plus historical rows in `tests/tools/t1353_store_regen_impact.csv`. These generated/historical snapshots are left untouched under the no-store-regen instruction; no other test, live extract or golden references an old ID.

No generated store files are committed. The expected source-store delta is +10 observations net: 38 old IDs removed and 48 new IDs added (38 replacements and ten quoted Table 3 activity points). All six pO2 IDs remain unchanged.

The full scratch regeneration emitted 186,654 observations. Its migration report has 1,685 hard issues, equal to the checked-in green report. `validate_migrated_store` returned `forbidden_shape_count: 0` with shape counts `conditional_field:derivation=249` and `conditional_field:derived_from=1191`, matching the checked-in store-validation record. The regenerated store content hash is `594a7e7ce686dffaec2961e6b1cc430b82889716fbee49cfdc8ee3e6b7b1befb` (the checked-in baseline is `64b1334af71411ff2eb33743800a5f13d616ea9cacdfb10745820aa8b86d6748`). The scratch j01 census found 12,591 numeric series points, 1,010 unavailable points, and zero mismatches. Shornikov contributes no j01 series rows because its migrated values use `points`, not `series`.

## Verification

- Focused extract test: 8 passed locally in the initial round; the fix-round Studio gate passed all 9 tests. The new context regression calls `validate_extract_document` with `check_fidelity_match=True` and returns zero errors. The migration mutation harness killed the missing-link, wrong-pressure and cross-temperature mutations.
- Flow-scalar comma guard: 17 passed locally in the initial round and 17 passed again after the fix (127.79 seconds).
- Repository literature extraction suite: the initial local run had 935 passes and three 300-second timeouts (`test_repo_extracts_validate_green`, `test_coverage_pending_stubs_not_found`, `test_pilot_extract_count_exact_and_merge_smoke`). The direct validator CLI also reached its 300-second local cap without a summary (`UNVERIFIED-local`). On the fix-round Studio rerun, node-ID syntax was rejected by the command profile (exit 69), and the `-k` form was rejected as an unsupported flag (exit 69). The accepted full named-file request produced no result after 20 minutes and was canceled. The three timeout nodes remain `UNVERIFIED-local`.
- Scratch regenerated `validate_store`: passed; zero forbidden shapes and baseline-equal shape counts as reported above. Migration hard-issue total 1,685 equals the checked-in green migration report.
- Scratch regenerated j01 series census: 12,591 numeric, 1,010 unavailable, zero mismatches. The named `test_j01_store_census_series_numeric_matches_declared_field` pytest node reached its 300-second cap without a summary (`UNVERIFIED-local`).
- `git diff d5e6b8dc216bd601ca080d47dd1a53e10168aa6e -- simulator/ tools/`: empty.

The review-fix round adds schema-validation coverage for the six Ca readiness contexts and reruns only targeted tests. Per the controller review, it does not rerun the full scratch migration or j01 locally; the previous scratch results above are retained as prior-round evidence. The full literature-suite timeout nodes remain unverified after both the local timeout and unavailable targeted Studio result.
