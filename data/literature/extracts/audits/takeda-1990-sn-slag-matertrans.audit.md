# Takeda et al. (1990) extraction audit

## Target and extraction scope

The target observables are `activity_coefficient` for SnO(l) and `activity` for Sn(l), on the melt-activity rail (`simulator/battery/score.py::rail_for_quantity`, `simulator/battery/waypoints.py::MELT_ACTIVITY_OBSERVABLES`). Both quantity tokens are supported by `simulator/battery/migrate.py::QUANTITY_ALIASES`; required identity axes are determined by `simulator/battery/identity.py::profile_for`.

Table 1 has 14 SnO(l) activity-coefficient entries. Entries 1–10 and 12–14 are quoted comparisons from the named prior workers; entry 11 is the authors' own measured-reduced summary. The 1.0–7.0 endpoints are not paired to individual slag samples in the table, so no pointwise gamma rows were invented. The individual gamma values appear only as plotted points in Figure 6. The paper's Table 3–6 experimental rows and numeric method/results statements are retained as located context, not retyped as activity observations.

One separate Sn(l) activity observation records the prose quotation `aSn=0.8` attributed to Nunoue and Kato (12), with the two coexisting Sn-Fe liquid phase compositions and 1573 K. It uses the established `type: activity_coefficient` family with the exact `values.quantity: activity` token: the closed `tools/validate_literature_extracts.py::OBSERVATION_TYPES` does not define an `activity` family, while existing Zaitsev and Lopatin extracts use this same family/quantity pattern. The 1990 paper does not restate the cited method, carrier gas, pressure, or calibration window; those remain typed unknown. The row uses the pure-liquid Sn endmember reference indicated by Eq. (5), marked inferred because the quoted sentence does not name the convention. This is not a schema gap or a quantity substitution.

Every activity observation carries a typed standard state, temperature, composition, oxygen potential, total pressure, calibration-window state, and uncertainty confidence. Table 1's gamma header is γSnO(l); Eq. (5) assumes unit activities for SnO(l) and Sn(l). Table 1's mol% composition cells are retained exactly, including em dashes and ranges. The mixed-composition ranges do not specify a single activity point, and the Nagamori entry's `1473, 1573` temperatures are not paired to its composition endpoints; those identities remain unknown. The source prints no fO2/pO2 or total pressure for Table 1, no scalar composition for the range entries, and no calibration window. These are located typed absences. No absent pressure, temperature, composition, or oxygen condition was supplied from convention or from a neighbouring row.

## Cell audit

The PDF has no usable text layer (`pdftotext-layout.txt` is 9 bytes). The secondary read was the page-marked Tesseract OCR. MinerU conversion produced eight table blocks; its `p006-t08` details block is an invented numeric reading of Figure 4 and was excluded. The first nine Table 1 data rows were classified as header rows by MinerU, leaving only five body rows in its draft. The render check reconciled all 14 printed Table 1 rows (table rows 3–16). Table 6's two MinerU blocks were joined as one printed table; its 81 runs are tests 51–131, and merged FeT cells remain one printed total-iron value rather than being split into Fe2+ and Fe3+.

The audit CSV has 1,125 numeric source cells. Each listed cell was checked against a 600 dpi page render; the scan-specific QA reports that OCR numeric agreement is skipped for this source. The stored numeric cells outside printed tables are limited to the listed Figure 4 legend, table captions/footnotes, and identified prose statements.

| Printed source location | Printed data rows | Audited numeric cells |
|---|---:|---:|
| Table 1 | 14 | 79 |
| Table 2 | 5 | 14 |
| Table 3 | 11 | 99 |
| Table 4 | 29 | 203 |
| Table 5 | 6 | 41 |
| Table 6 | 81 | 653 |
| Figure 4 legend | 9 legend entries | 18 |
| Table 4 caption | shared caption values | 4 |
| Table 3 discussion | cited prose values | 10 |
| Table 5 footnote | 1 footnote | 2 |
| Table 4 discussion | silica-free slag bound | 1 |
| Nunoue and Kato quotation | prose activity value | 1 |
| **Total** | **146 table rows** | **1,125** |

Table 1's descending Koike range is preserved as printed (`1.4 to 0.9`); its numeric interval is stored in ascending order. Table 2's EMF ± cells and Tesseract's fused uncertainty digits were read from the render. Table 6's FeT merged cells, table layout, row order, and element headings were checked visually. The Figure 4 legend was rendered at 600 dpi; all nine marker/line-style to printed composition-range pairs are stored and pinned by the focused test. No figure-only data points were digitized. Figures 2, 3, 5, 6, 7, and 8 remain figure-read candidates.

The table QA flagged the scanned Table 1 and 2 drafts for multiple-number/LaTeX cells and noted OCR numeric comparison was skipped for scans. `mineru_table_draft.py compare` reports four mismatches: the three scalar Table 1 gamma values (1.8, 1.7, 0.9) and the Sn activity 0.8. The Table 1 rows are misclassified as draft headers; the Sn activity is a prose quotation rather than a table cell. The comparator cannot resolve the source's canonical liquid phase token `l` in its phase-column filter (`tools/mineru_table_draft.py::_phase_matches_column`). All four values were independently confirmed against the page renders, and the focused test pins the Table 1 values and quoted 0.8. The compare CSV is retained beside this report.

## Locator page proofs

Page proofs were grepped from the Tesseract text one physical page at a time; its page marker is `=== PDF page N ===`. The PDF index is zero-based, and the published page is shown for each proof. These strings establish page placement; the cell values themselves were settled against the renders and recorded per cell in the audit CSV.

| Locator group | Physical PDF page | PDF index | Published page | Page-grep proof string |
|---|---:|---:|---:|---|
| Experimental method, charge, and argon conditions | 1 | 0 | 793 | `After holding the sample for 4 to 8 ks` |
| Table 1 entries and quoted composition ranges | 2 | 1 | 794 | `1.4 to 0.9 0 to 60` |
| Figure 1 and Table 1 caption | 2 | 1 | 794 | `Table 1 Reported values of the activity coefficient` |
| Table 2, SnO thermal analysis, and Eq. (5) standard state | 3 | 2 | 795 | `activities of unity for SnO() and Sn(1)` |
| Table 3 rows and Table 4 experimental caption | 4 | 3 | 796 | `A5 1573, —11.40 2.0` |
| Quoted Sn activity and coexisting metal phases | 4 | 3 | 796 | `tivity of Sn is 0.8 at this miscibility gap` |
| Table 5 footnote and Table 6 rows | 5 | 4 | 797 | `A-S51 —11.30** 2.4` |
| Figure 4 caption and marker legend | 6 | 5 | 798 | `Fig. 4 Effect of oxygen partial pressure on tin content` |
| Figures 6–7 and unplotted gamma inputs | 7 | 6 | 799 | `Fig. 6 Activity coefficient of SnO(1) as a function` |
| Figures 8–10 and conclusions | 8 | 7 | 800 | `Fig. 8 Magnesia content in slag` |
| Figure 8 and bibliography entry for cited reference (12) | 8 | 7 | 800 | `(12) S. Nunoue and E. Kato: Tetu to Hagané, 73 (1987), 868.` |

The focused test independently locates and pins Table 1, Table 2, Table 3, Table 6, Figure 4, Figure 6, the quoted activity paragraph, and the reference (12) bibliography entry by their page-marked OCR strings; the corresponding expected numeric cells were verified on the render.

## Engine readiness

| Observable | Rows | Engine-ready | Typed refusal and missing inputs |
|---|---:|---:|---|
| SnO `activity_coefficient` | 14 | 0 | `identity.composition`: 14 rows lack one composition paired to the gamma value; `identity.fO2_Pa`: 14 rows have no printed oxygen potential; `identity.temperature_K`: the Nagamori row has two unpaired temperatures. All 14 calibration windows are unreported. Total pressure is recorded as not published; it is not required by this profile. |
| Sn `activity` | 1 | 0 | `identity.composition`: the source gives two coexisting phase compositions, not one bulk composition for the activity point; `identity.fO2_Pa`: not published. Temperature and standard state are present. Calibration window and total pressure are not published. |

Identity axes after isolated migration:

| Observation | composition | total_pressure_Pa | fO2_Pa | temperature_K |
|---|---|---|---|---|
| Table 1 gamma 01 | unknown | unknown | unknown | value |
| Table 1 gamma 12 | unknown | unknown | unknown | unknown |
| Quoted Sn activity | unknown | unknown | unknown | value |

The melt-activity profiles require composition, fO2, `per`, reference state, and temperature. All 15 rows migrate `per` as `dimensionless`, consistent with the printed dimensionless activity and coefficient units. Every unreported pressure remains unknown; no 1 atm, vacuum, or standard pressure was added. There is no distribution-coefficient value printed as an observation. The source's numeric `Sn`/`Fe` slag/metal cells and oxygen potentials in Tables 3–6 are fully retained as context with experiment links, row/cell locators, and units; they do not form pointwise SnO activity coefficients in the printed tables.

## Migration and uncertainty

The isolated real-migrator run emitted 15 observations with 0 hard issues and 0 unavailable values. Migrated values are 11 intervals and 4 points; no observation migrated with unknown quantity or phase. Migrated uncertainty counts are 15 `printed`, all with confidence `not_stated`; the paper gives no activity-value uncertainty. Table 2's EMF widths are retained with their source cells in the table context, not incorrectly attached to an activity observation. The migration test checks these counts and the composition, pressure, fO2, and temperature axes above.

The row evidence choices follow the printed provenance: 13 quoted Table 1 entries and the cited Sn activity are `quoted_attributed`; the authors' own Table 1 range is `measured_reduced`, with unprinted individual plotted gamma values located at Figure 6. The authors' equation-based results are not reclassified as quoted. No distribution coefficient row or model-derived output is printed.

## Expected store delta

The source extract now carries 15 observations (14 SnO activity coefficients plus the quoted Sn activity), 204 context records, and 3 fidelity samples. Expected derived-store delta is **+15 observations**, with no battery-store regeneration in this change.
