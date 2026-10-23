# Itoh et al. (1985) extraction audit

## Duplicate check

The PDF's first page identifies “Measurement of Na2O Activity in Beta-Alumina by EMF Method,” Transactions of the Japan Institute of Metals 26(5), 353–361; DOI 10.2320/matertrans1960.26.353. `pdfinfo` reports nine pages and the first-page text gives the same title and journal. No DOI, title, or same-author/year match exists in the current extracts, works, or index. The same query returned no matches across the available `origin/review/w*` refs. The PDF is not a paper about slag melts: its measured phases are the solid (alpha+beta)- and (beta+beta-double-prime)-alumina assemblages.

## Table dispositions

| Table | Printed content | Candidate tokens | Disposition |
|---|---|---|---|
| 1, physical PDF page 6 / folio 358 | Nine Na2O activities at 1000 K; seven values from cited works and two from this work | `activity` fits; `delta_fG` does not fit formation from oxides | All nine activities are typed under Na2O, phase `cr`, with the printed assemblage retained in `values.phase_as_printed`. Their reference is Raoultian pure crystalline Na2O, inferred from equations (6) and (14) and the printed 1000 K condition; JANAF Na-014 gives Tm=1405.200 K. The composition remains unknown. The seven cited values are `quoted_attributed`; this work's values are `measured_reduced` from Eq. (6), with the unprinted Figure 3 numeric inputs declared. The seven alpha-alumina reaction Gibbs values are retained in context with the reaction; the beta-alumina dash is retained as a blank, not a zero. |
| 2, physical PDF page 7 / folio 359 | 48 equilibrium SO2, SO3 and CO2 pressure ratios at eight temperatures and two alumina assemblages | `p_partial` exists, but these are calculated outputs from Eqs. (7), (8), (16), (17), and JANAF data (29) | Context-only model output; the extract carries every printed value and condition. Its inputs are in the untracked `.agent-scratch/F28-tables/itoh-1985-beta-alumina-na2o-emf/table-2-inputs.csv`; the output transcription is in `table-2-values.csv`. Both are awaiting a corpus table asset per controller ruling t-1382. |

The two activity-versus-temperature least-squares equations are model fits and remain in context. The body prints ranges of 874–1055 K and 823–1073 K, while the abstract gives 874–1054 K and 823–1075 K; both printed versions are noted with the body equations. The printed cell-EMF fit, Gibbs-energy equations (12)–(15), and sodium-vapor-pressure equations (19)–(20) are retained in context without converting them into additional observations. The plotted results in Figures 3–9 are deferred without numeric points. The extraction did not digitize figures.

## Cell audit

The born-digital text layer contains captions but omits the body cells in both tables. MinerU supplied the cell drafts. Each cell below was checked against a 600 dpi render: Table 1's nine activity cells, seven reaction-Gibbs cells and two em dashes; Table 2's eight temperatures and 48 pressure-ratio cells (56 numeric cells total). The render confirmed the two Table 2 values stacked under each temperature and the alpha+beta / beta+beta-double-prime column groups. The MinerU compare output is committed as `itoh-1985-beta-alumina-na2o-emf-mineru-compare.csv`; all nine typed activity values matched its page/column candidates. The two mapped present-work activity rows were emitted by the table tool; seven quoted values were hand-added to preserve their distinct attributions. Table 2 had no numeric observation mapping because the values are calculated and their derivation inputs have no registered table asset.

| Physical page | Table / cells checked | Read and render disposition |
|---|---|---|
| 6 | Table 1: activity column rows (3), (6), (22), (23), (24)(25), (26), This work, (25), This work | MinerU digits matched the 600 dpi page render. The table's merged phase labels apply to rows 1–7 and 8–9 respectively. |
| 6 | Table 1: reaction Gibbs column rows (3), (6), (22), (23), (24)(25), (26), This work, then two em dashes | All seven numeric values and both printed em dashes confirmed on the 600 dpi page render. |
| 7 | Table 2: 500, 600, 700, 800, 900, 1000, 1100, 1200 K; SO2, SO3, CO2 values for both alumina assemblages | All 56 numeric cells confirmed on the 600 dpi page render, including the paired rows under each temperature. No dash or unreported value was converted to zero. |

## Figure records

Figures 3–9 have context records with captions, physical locators, typed quantity descriptions, phase/temperature/axes and series. Figure 9 is filed under Na because it shows sodium vapor pressure. Plot points remain deferred to a figure pass; no values were digitized.
