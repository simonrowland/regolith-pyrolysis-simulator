# Itoh and Yokokawa (1984) extraction audit

## Duplicate check

The source PDF has six physical pages. Its first page identifies “Thermodynamic Activity of Na2O in Na2O-SiO2-Al2O3 Melt,” *Transactions of the Japan Institute of Metals* 25(12), 879–884; DOI 10.2320/matertrans1960.25.879. Searches by DOI, title, author and year found no matching extract or work, including the available `origin/review/w*` refs. The 1985 Itoh paper in this family is distinct: it reports solid beta-alumina assemblage activities at 1000 K; this paper reports NAS melt measurements at higher temperatures against a 40Na2O·60SiO2 melt. No value was merged or duplicated.

## Table dispositions

| Table | Printed contents | Candidate tokens | Disposition |
|---|---|---|---|
| 1, physical page 2 / folio 880 | Cell EMF readings at multiple temperatures for NAS compositions, against 40Na2O·60SiO2 | `activity` does not fit a voltage; `partial_molar_enthalpy` does not fit a voltage. `emf_cell` is not a `Quantity` token; it is only an ion-intensity-ratio denominator kind. | Context-only because there is no measured cell-voltage quantity token. The two table-tool drafts with distinct row groups are preserved in `tables_as_printed`; their exact CSV inputs are also in untracked `.agent-scratch/F28-tables/itoh-yokokawa-1984-sodium-activity-nas/`. No EMF was converted to activity. |
| 2, physical page 3 / folio 881 | 28 rows of relative partial-molar ΔG, ΔH and ΔS at 1400 K, relative to 40Na2O·60SiO2 | `partial_molar_enthalpy` fits ΔH in isolation, but Table 2 is reduced from Table 1's untyped EMF inputs, so no table asset can support its derivation. `delta_fG` is element-formation Gibbs energy and does not fit relative ΔG. `S` is absolute entropy and does not fit relative ΔS. | All 28 rows, including the two rows with blank composition cells, are retained in context. Derived-property lineage is pending a corpus table asset (controller, t-1382); input tables are retained in the CSVs above. |

The activity/basicity values are plotted in Figure 1, not printed in a table. Figures 1–5 are deferred figure records with no numeric values. Figure 1 mixes this work and cited binary-melt series; figures 2–3 show relative partial-molar properties; Figure 4 shows iso-expansivity lines; Figure 5 compares optical and thermodynamic basicity. Marker counts are explicitly marked not enumerated because this pass did not digitize the plots.

## Render checks and locators

The Table 2 28-row composition/ΔH transcription was checked against a 600 dpi render of physical page 3; values and signs, including the zero reference row, agree. The two blank composition rows remain unknown. Physical pages 4–6 were rendered at 600 dpi to identify figure captions and page locators. Figure locators use physical pages 4, 4, 4, 5 and 6, with folios 882, 882, 882, 883 and 884. Table 1 came from the primary MinerU table drafts `p002-t01` and `p002-t02`; `p002-t04` and `p002-t05` are duplicate OCR copies of those groups and were not counted again.

## Capture-floor note

This is a context-only extract with zero numeric observations. Table 1 has no quantity token for EMF. Table 2 is derived from those untyped readings, and the activities are only plotted in figures, which are deferred by the family instruction. No measured table was parked in context despite a matching value token.
