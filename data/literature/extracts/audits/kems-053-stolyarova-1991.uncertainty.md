# KEMS-053 Table Ia uncertainty recovery

The 44 Table Ia pressure points already carry the printed cell text and a numeric `sigma`. The table headers give the pressures in atm times `10^7` for Ca and `10^8` for CaO; the stored sigma values are the corresponding absolute widths in atm. Each point retains its exact `value ± width` cell under its `*_as_printed` field and its PDF page 2 / Table Ia point-condition locator. The observation locator now supplies `unit='atm'` to the existing uncertainty reader. Confidence level is not stated.

The migrated uncertainty field is selected from `values.points[].sigma` by `simulator/battery/migrate.py::Migrator._emit_exploded_point`. `simulator/battery/score.py::_uncertainty_unit` resolves the atm unit from the locator note, and `simulator/battery/score.py::_printed_uncertainty_band` resolves the row printed band. No pressure or uncertainty cell value changed, so the content-stable point IDs and migrated pressure values remain unchanged.

## Isolated migration and band check

The real migrator emitted 144 observations, 0 hard issues, and 6 unavailable observations. Table Ia contributed 44 partial-pressure points with printed uncertainties. No Table Ia point has an unknown quantity or phase. Across the full source migration, the existing baseline still includes 60 phase-unknown observations and one quantity-unknown observation; those rows are outside this 44-point uncertainty recovery.

The pinned OpenIMCC 66ec4a3 source tree hash was `308672269d6e6090023f4a86c4fc0d83c4c24688`. Ten Table Ia rows were rescored with OpenIMCC. Their residuals stayed the same; the final score still withholds a decision band on all ten, so it reports `no_band` and no band verdict. The direct row uncertainty parser now resolves bands in dimensionless log units. The score-level band remains withheld by existing source qualification flags; no scorer tuning was done.

| row key | residual before | residual after | score band / verdict before | score band / verdict after | row band before | row band after |
|---|---:|---:|---|---|---:|---:|
| `h=152b6c6f5a56` | -1.717938 | -1.717938 | absent / none | absent / none | 3.8965e-7 | 0.041393 |
| `h=1bfd4b1639bf` | -1.776752 | -1.776752 | absent / none | absent / none | 2.3615e-7 | 0.024613 |
| `h=282ee65d0821` | -1.175117 | -1.175117 | absent / none | absent / none | 3.4221e-7 | 0.036137 |
| `h=1ff3cb02588e` | -1.167244 | -1.167244 | absent / none | absent / none | 4.3558e-7 | 0.046543 |
| `h=5a0cf2c55968` | -0.743894 | -0.743894 | absent / none | absent / none | 4.2104e-7 | 0.044905 |
| `h=006b1878b9bb` | -3.480804 | -3.480804 | absent / none | absent / none | 3.1210e-7 | 0.032834 |
| `h=0c3c77576647` | -2.993733 | -2.993733 | absent / none | absent / none | 2.8273e-7 | 0.029636 |
| `h=0ced5fd54c2a` | -3.370037 | -3.370037 | absent / none | absent / none | 3.4566e-7 | 0.036517 |
| `h=0797219c1697` | -2.507672 | -2.507672 | absent / none | absent / none | 3.2348e-7 | 0.034080 |
| `h=3f09acb05b3a` | -3.361192 | -3.361192 | absent / none | absent / none | 4.4096e-7 | 0.047150 |

Baseline and after score rows with a final decision band: 0/10 → 0/10. The row bands shown above are direct printed-uncertainty resolutions; higher-level score qualification flags continue to suppress the final decision bands.

## Mutation evidence

Removing the Table Ia `sigma` value from the x(SiO2)=0.50 Ca complete-evaporation point in an isolated extract mutation made its row printed-band resolution disappear. Restoring the source field makes it resolve in atm and keeps the migrated pressure value unchanged. The focused regression test pins the four x(SiO2)=0.50 cells against the page 2 render and checks all 44 rows.
