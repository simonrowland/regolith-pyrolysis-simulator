# Liu & Li (2025) extraction audit

Source: `liu-li-2025-se-te-sulfide-silicate`, a 12-page born-digital PDF. DOI `10.1016/j.epsl.2025.119277`; duplicate checks for DOI, full title, and author-year found no earlier extract in the local data or available `review/w*` refs. The main-text PDF and sidecar are present; supplementary files are not present in the source directory. The article uses e-locator 119277 and prints no journal folio, so `published_page` is omitted.

## Table disposition

| Printed table | Disposition | Candidate tokens and reason |
|---|---|---|
| Table 1, physical PDF page 3, 18 experimental runs | Mixed: `typed` and `context-no-token` | `equilibrium_phase_composition` fits the 18 measured S concentrations in quenched silicate melt, so each is typed under species S. The calculated sulfide-liquid/silicate-melt DSe, DTe, DS and DCu values have no matching store quantity token and remain in context with their 1σ values. `liquidus_composition`, `residue_component_composition`, `activity`, `activity_coefficient`, `interaction_parameter`, `valence_ratio`, and `fugacity` do not fit these partition coefficients. Complete run-specific major-oxide melt compositions are in absent supplementary Tables S2 and S3; none are reconstructed. |

All 18 S-in-silicate-melt values and their printed 1σ uncertainties are `measured_direct equilibrium_phase_composition` observations. They are filed under species S and phase glass because Table 1 identifies Sil as quenched silicate melt. The paper says experiments were quenched by turning off electrical power to the graphite heaters, but does not state whether quenching preserves sulfur content; each observation records preservation as `not_stated`. The method for the Table 1 S column is not separately stated. Uncertainty confidence is not stated.

Every S observation binds its Table 1 run ID, printed starting materials, products, temperature, pressure, duration, capsule and ΔFMQ condition. The run-specific final melt composition remains unknown because it is only supplied in absent supplementary Tables S2 and S3. Bounds `< −1.6` stay strings in the printed ΔFMQ condition; DO1's `~1.5` stays approximate text. No absolute fO2 is computed. The prose's aggregate major-oxide ranges remain context because they do not give a complete composition for any identified run.

The table-draft tool was attempted and refused with `no MinerU content-list JSON found for liu-li-2025-se-te-sulfide-silicate`. No MinerU mapping or emission was possible. Table 1 was transcribed from `pdftotext-layout.txt` and checked against the 600 dpi render of physical PDF page 3. The focused test contains a separate full-grid transcription of all 18 rows and 18 columns, compares every cell across the typed observations and context, and proves both a grid-cell mutation and an extract-cell mutation fail.

## Figure disposition

All nine main-text figures are recorded as figure records with no numeric points and `digitisation: deferred to a figure pass`. Figures 1–3 report phase-partition coefficients or ratios for which no store quantity token exists. Figures 4–9 show model results, elemental concentrations or bulk element ratios without a fitting scalar quantity token. Their captions, phases, axes, temperature fields, series, attribution, physical-page locators and visible marker counts were checked on 600 dpi renders of pages 4–11. Marker counts are visual counts; coincident symbols may overlap.

No activity series is printed, so there is no activity reference state to record. No figure values are digitised. The expected battery-store delta is 18 observations; no battery-store regeneration is committed.

## Locator and migration evidence

- Physical pages 2–11 use `page = pdf_page_index + 1`; no journal folio is printed.
- Page-specific `pdftotext -f N -l N -layout` reads and the focused source test confirm the quench statement on page 2, Table 1 on page 3, figure captions on pages 4–11, and the supplementary-data availability statement on page 11.
- The focused test pins 18×18 Table 1 cells to an independently transcribed render grid, asserts at least three literal measured values, mutates one expected-grid cell and one extract cell (both fail), checks migrated species S and every row's temperature, pressure and ΔFMQ, and requires zero migration hard issues.
- The source test uses `source_page_text` and skips cleanly when the corpus is absent.
