# Filsinger and Bourrie (1990) extraction audit

## Source coverage and row reconciliation

The seven-page scanned article has no text layer. The primary reads were MinerU table blocks and the tesseract OCR transcription; the supplied `pdftotext-layout.txt` is the scan OCR, not a born-digital layer. The printed table counts reconcile as Table I: 6 reagent rows, Table II: 10 reaction runs, Table III: 8 fitted rows covering six reaction/vent series. Table I is retained as contextual reagent characterization. Table II has one typed-refusal observation per run. Table III has one typed-refusal observation per fitted row, retaining A, B, E_s and Z together. One additional in-text measured rate is retained from page 3.

Drafts and the comparison output are under `notes/tables/filsinger-1990-silica-silicon-carbothermic-kinetics/` in the worker scratch area. The tool's Table II and III drafts flagged merged cells and row/column uncertainty, so their data were transcribed from machine text and each table's complete row/column layout was checked against renders. Table I was also checked against its page render because MinerU treated its body rows as headers. Renders used PDF pages 2, 4, and 7 at 300 dpi; the ambiguous Table II final-residue cell was rechecked at 600 dpi. The page 3 prose rate and Figure 6's printed series legend were checked at 600 dpi on pages 3 and 6.

## Full-cell audit

Each entry below gives the printed cell coordinates and the resolved reading. `(b)` means render checked; no table cell was accepted solely from a single OCR read. Table II draft coordinates include the two header rows, so printed body row 1 is draft row 3. Table III likewise starts its body at draft row 3.

### Table I, PDF page 2, printed table I — six reagent rows

| Printed row | Cell readings retained | Audit |
| --- | --- | --- |
| Si | c1 `Si`; c2 `Hemlock Semiconductor Corp., Hemlock, MI; IVO190, lot S-03759`; c3 `Si` | (b) p2 render; supplier and lot are textual identifiers |
| SiC | c1 `SiC`; c2 `Cerac Pure, Milwaukee, WI; S-1058, lot 1587; 7 μm average`; c3 `α-SiC` | (b) p2 render; lot `1587` and size `7 μm` retained as printed |
| SiC: β form | c1 `SiC: β form`; c2 `Produced via SiO + 2C (see Table II)`; c3 `75% β-SiC; 2% C (graphite); remainder α-SiC` | (b) p2 render; both printed percentages checked |
| SiO | c1 `SiO`; c2 `Cerac Pure, Milwaukee, WI; S-1064, lot 1858; 325 mesh`; c3 `No crystalline phases detected` | (b) p2 render; lot and mesh retained as printed |
| SiO2 | c1 `SiO2`; c2 `Cerac Pure, Milwaukee, WI; S-1061, lot 2957; 325 mesh`; c3 `α-quartz` | (b) p2 render; lot and mesh retained as printed |
| C: graphite | c1 `C: graphite`; c2 `Cerac Pure, Milwaukee, WI; G-1059, lot 480; 325 mesh`; c3 `Graphite` | (b) p2 render; lot and mesh retained as printed |

### Table II, PDF page 4, printed table II — 10 reaction runs

| Printed row (draft row) | c2 ratio; c3 onset °C; c4 intermediate; c5 intermediate °C; c6 final residue wt%; c7 final XRD | Audit |
| --- | --- | --- |
| 1 (3) | `1/1`; `1300`; `11% β-SiC; 5% SiO2`; `1720`; `<11`; `None` | (b) p4 render; `<11` confirmed at 600 dpi |
| 2 (4) | `1/2`; `1300`; `27% β-SiC; 5% SiO2; 3% C`; `1730`; `30`; `97% β-SiC; 2% Si` | (b) p4 render |
| 3 (5) | `1/3`; `1400`; blank; blank; `42`; `73% β-SiC; 12% C` | (b) p4 render; empty cells remain empty |
| 4 (6) | `1/1`; `1500`; blank; blank; `29`; `42% Si; 36% β-SiC` | (b) p4 render |
| 5 (7) | `1/2`; `1550`; blank; blank; `50`; `71% β-SiC; 29% Si` | (b) p4 render |
| 6 (8) | `2/1`; `1520`; blank; blank; `8`; blank | (b) p4 render; XRD cell is blank |
| 7 (9) | `1/1`; `1500`; blank; blank; `72`; `92% β-SiC; 4% Si` | (b) p4 render |
| 8 (10) | `2.8/1`; `1500`; `20% Si; 8% TaSi2; 4% β-SiC`; `1800`; `22`; `>90% Si` | (b) p4 render |
| 9 (11) | `1/1`; `1470`; `22% β-SiC; 3% SiC`; `1720`; `42`; `70% β-SiC; 19% Si` | (b) p4 render |
| 10 (12) | `1/2`; `1450`; blank; blank; `40`; `75% SiC; β:α/3:1; 2% C` | (b) p4 render; slash retained as printed |

### Page 3 prose rate — one measured point

The discussion of reaction (j) reports a rate constant of `49.2 × 10⁻⁴ s⁻¹` at `1725°C` for the BN crucible, within 2% of the average for Ta crucibles. The equation, condition, exponent, and comparison were checked against the 600 dpi PDF page 3 render. The reported ratio is 2.8 mol SiO to 1 mol SiC. The Ta average is not printed as a separate value. The measured rate is retained as a typed refusal because the schema has no exact kinetic-rate quantity token; its numerical inputs are not printed.

### Table III, PDF page 7, printed table III — 8 fitted rows

| Printed row (draft row) | c1 reaction; c2 vent; c3 T (K); c4 A; c5 B; c6 E_s (kJ/mol); c7 Z (s^-1) | Audit |
| --- | --- | --- |
| 1 (3) | `SiO2 + 3C → SiC + 2CO`; `Open`; `1733–1933`; `-4.68`; `18.82`; `390`; `108` | (b) p7 render; baseline digits stored as printed; interpretation is uncertain |
| 2 (4) | `2SiO2 + SiC → 3SiO + CO`; `Open`; `1833–2033`; `-4.18`; `17.55`; `348`; `106` | (b) p7 render; baseline digits stored as printed; interpretation is uncertain |
| 3 (5) | `SiO + 2C → SiC + CO`; `0.4 mm`; `1733–1933`; `-5.69`; `23.76`; `472`; `1010` | (b) p7 render; baseline digits stored as printed; interpretation is uncertain |
| 4 (6) | `(2.8)SiO + SiC → 2Si + CO`; `0.4 mm`; `1963–2073`; `-6.10`; `24.42`; `505`; `1010` | (b) p7 render; tesseract comma in A resolved as decimal point; baseline digits stored as printed |
| 5 (7) | same reaction; `Loose lid`; `1998–2073`; `-6.99`; `25.10`; `505`; `1011` | (b) p7 render; blank reaction cell carries the preceding reaction; baseline digits stored as printed |
| 6 (8) | `SiO(s) → SiO(g)`; `0.4 mm`; `1833–2033`; `-4.06`; `13.79`; `340`; `106` | (b) p7 render; baseline digits stored as printed; interpretation is uncertain |
| 7 (9) | same reaction; `Open`; `1683–1833`; `-3.68`; `14.34`; `305`; `106` | (b) p7 render; blank reaction cell carries the preceding reaction; baseline digits stored as printed |
| 8 (10) | `SiO2 + Si → 2SiO`; `Open`; `1683–1883`; `-4.18`; `16.95`; `348`; `107` | (b) p7 render; baseline digits stored as printed; interpretation is uncertain |

All six reaction families are associated with their row equations. The ratios in Table III are the printed stoichiometric reactant proportions; Table II ratios are retained as printed. The Table II final-residue measurement is stored as residue wt%, not complemented into a mass-loss percentage. The Z cells are baseline digit strings on the page. Each observation has a separate note that a `10^n` reading with n set on the baseline is likely but uncertain; that interpretation is not stored as printed data.

## Conditions and evidence

The paper reports a common TGA heating rate of `20°C/min`, helium with `<0.5 ppm` oxygen, and average helium flow of `3 mm/s`. `70 kPa` is explicitly the estimated pressure used to tamp the sample into the crucible, not chamber pressure. The paper states analyses above `1600°C` were at atmospheric pressure; Table II's final residue is at `2000°C`, and Table III rows 4–5 lie wholly above that threshold. No numeric chamber pressure is printed. Other Table III fit ranges are below or cross the threshold, so atmospheric pressure is not copied to those rows. No CO/Ar carrier mixture or numeric fO2 is reported; reaction-produced CO is not treated as the carrier gas. The total-pressure identity remains unknown.

For migration, the printed reactant mole ratios are carried both verbatim and through the existing `point_conditions.composition` path as `mol_inventory`; they identify the solid charge and do not imply a melt. Table II temperature is the printed final-residue condition, `2000°C`. Table III retains its printed fit interval as `T_range_K`; it has no single fit temperature. The conditions have locators on the table cells or on the common-method prose that supplies them.

Table III's constants are the authors' own reduction of their TGA measurements, so all eight rows are `measured_reduced`. The authors derive the first-order rate constant from isothermal TGA weight responses (equations 5–6) and fit the Arrhenius relation (equation 7). The numerical inputs occur only as plots in Figures 3, 4, and 6 and are not printed as numeric input tables. In line with the controller ruling, each fit declares `derivation.raw_inputs` as `not_printed`, located at PDF page 6, Figures 3, 4, and 6. No figure coordinates are digitized.

The exact quantity gap is documented on the rows: `simulator/battery/enums.py::Quantity.LOG10_K_REACTION` is equilibrium log K and `simulator/battery/enums.py::Quantity.MASS_LOSS_RATE` is mass/time; neither is a kinetic rate constant or Arrhenius A/B, E_s, or Z quantity. `simulator/battery/migrate.py::TYPE_QUANTITY` defaults `rate_series` to `MASS_LOSS_RATE`, and the closed `tools/validate_literature_extracts.py::OBSERVATION_TYPES` set offers no more exact kinetic observation type. `simulator/battery/enums.py::Quantity.MASS_LOSS_FRACTION` is not an exact match for final-residue wt%. All 19 migrated observations are therefore typed-refused with quantity unknown; no near-miss quantity is used. Phase is mapped as solid residue (`cr`) or gas product (`gas`).

Figures 3, 4, and 6 are retained as undigitized `figure_only` context. Figure 6's rendered legend has eight labeled series, A–H; the test binds all eight printed labels and checks a rendered label against a fidelity sample. Figures 1, 2, and 5 are noted as figure-read candidates only; their curves were not digitized.

## Validation record

The focused test checks the 10 and 8 printed row counts, all Table II result cells, all Table III fit fields, rendered fidelity samples, Figure 6 labels, and mutation failure. It runs direct extract validation and the real isolated migrator, asserting 19 observations, 0 hard issues, 19 unavailable typed refusals, no phase-unknown rows, and quantity-unknown exactly on those 19 schema-gap records. It also pins the as-charged composition, temperature, helium condition, and absent numeric total pressure for every row. The focused test passed (4 tests), and `tests/test_yaml_flow_scalar_commas.py` passed (17 tests).

The repository-wide extract suite is `UNVERIFIED-local` as a complete invocation: its bounded run excluding `test_repo_extracts_validate_green` ended with a keyboard interrupt after reporting 158 passed and 1 deselected. The excluded repository-wide validator then passed in a separate bounded run (1 passed); the full suite did not return cleanly as one invocation.

The MinerU draft comparison CSV is committed beside this report: 17 numeric candidates matched, with no mismatch or locator error. The nonnumeric `<11` Table II value has no decimal candidate and was resolved directly against the 600 dpi render. The Table III Z column was render-checked; its baseline digit strings are stored as printed, with the possible power-of-ten reading documented only as uncertain row notes. No changes were made to the migrator or tools.
