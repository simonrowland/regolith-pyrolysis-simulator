# Stolyarova et al. (1996), Table 3 audit

Table 3 on published page 23 (PDF page 9) prints 30 composition rows. The two printed composition columns are `xA/xC` and `xS`; the activity columns are CaO in column 1, Al2O3 in columns 2–4, and SiO2 in column 5. The audit reconciled 210 body cells: 60 composition-axis cells, 137 numeric activities, and 13 dashes. Each row and column assignment was checked against the render. The full table image matched the MinerU HTML table and Tesseract text read; no disagreement remained. The CaO `0.853` at `xA/xC=0.15`, `xS=0.25` is printed as `0.853` and remains unchanged.

The existing extract already contains all five activity-column observations and all 137 numeric cells. No observation rows were added or modified. The focused test checks all 30 printed composition pairs, the 137-cell count, and rendered values from three activity columns.

Table 3’s caption omits temperature. The current-study 1933 K condition is printed in the Figure 1 caption on published page 21 and is carried by the existing rows. The printed composition axes are preserved; the normalized CaO-Al2O3-SiO2 mole fractions are derived per row from those axes. The source does not print total pressure, so no total-pressure condition is present.

The activities are measured_reduced: the authors derive activity from measured ion currents using equations (10)–(15), as the Table 3 caption states. The standard reference wording is the equation (13) definition on published page 19: “over the individual oxides (CaO, Al2O3, SiO2), respectively.” The paper does not name the physical phase or polymorph of those reference oxides. Existing structured standard-state phases are retained unchanged under the task’s keep-existing-rows instruction; those phases are explicitly marked as inferred in the extract.

| Cell group | Source agreement | Render check |
|---|---|---|
| Table 3 headers and column-to-species assignments | MinerU and Tesseract agree | PDF page 9 checked |
| All 30 `xA/xC` cells and 30 `xS` cells | MinerU and Tesseract agree | PDF page 9 checked |
| All 137 numeric activity cells and 13 dash cells | MinerU and Tesseract agree | PDF page 9 checked |
| CaO `0.853` at `xA/xC=0.15`, `xS=0.25` | Both reads give `0.853` | PDF page 9 checked; retained as printed |
| Standard reference wording | MinerU prose and Tesseract text agree | PDF page 5 checked at equation (13) |

QA pre-screen: the table is a scan, so the QA tool skips numeric agreement scoring against the empty pdftotext layer. The table block is 31 rows by 7 columns with no ragged rows or OCR trap flags. The independent Tesseract OCR and the page render provide the second read and cell/layout confirmation.
