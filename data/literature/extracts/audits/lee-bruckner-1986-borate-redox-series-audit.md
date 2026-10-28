# Lee and Brückner (1986) extraction audit

## Duplicate check

The corpus PDF is titled “The electrochemical series of the 3d transition metal ions in alkali borate glasses,” by Jeong-Hoon Lee and Rolf Brückner, *Glastechnische Berichte* 59(9), 233–251 (1986). The corpus file has 19 physical pages. The nearby Lee–Brückner 1984 extract is a different paper (different title, year, issue, and tables); no 1986 title, DOI, author-year-title match, or matching source stem was found in the candidate extracts or review branches. No DOI is printed in the source PDF.

## Tables and typed values

Physical page 4 (published page 236), Table 1, contains redox fractions and glass densities, as well as single-ion concentrations and extinction coefficients. Candidate store quantities for the last two fields were `valence_ratio`, `ion_intensity`, and `density`; none describes molar concentration or extinction coefficient, so those printed quantities remain context with their original labels and units. The 30 reported redox values and 22 reported density values are typed. A dash is omitted as an unreported cell. The 18 Cr/Ti/Co redox and Cr/Ti density cells supported by MinerU were emitted from the table mapping. The other 34 typed cells were transcribed by hand after checking the 600 dpi page-4 render because MinerU had classified those row groups as headers. The test has an independent transcription of all 52 cells and checks both value and printed sample/addition.

Redox table entries are printed as percent of total element; the migrated values are fractions (for example, 4.5% is 0.045), with the published decimal-comma value retained in `value_as_published`. Each redox row carries its element species, base-glass molar composition, dopant amount as printed, and the 1000 °C air melting condition. Density values migrate in kg/m³ (for example, 2.042 g/cm³ is 2042 kg/m³); the printed number and g/cm³ unit remain in `value_as_published`. Density was measured by the Archimedes method at room temperature; the paper does not give a numeric temperature, so its temperature condition is unknown. Pressure is also unknown on the numeric axis. Density is a bulk property and its species formula is unknown; its identity carries the printed base-glass composition.

Physical page 7 (published page 239), Table 2, reports qualitative optical/ESR descriptors and g-values. Candidate quantities `valence_ratio`, `ion_intensity`, and `density` do not fit the reported descriptors or g-values; retained as context. Table 3 on the same page reports extinction per thickness and changes in individual-ion concentration; no existing quantity token fits either field, so its printed rows are retained as context. No table assets were registered.

The extract gate reports two SOFT capture findings for Tables 2 and 3 (0/5 and 2/85 numeric cells). Those values are retained in context because the printed descriptors, g-values, extinction coefficients, and concentration changes do not fit an existing quantity token. The gate reported no HARD findings; all 52 typed Table 1 points migrated as points.

## Figures and locators

Figures 1–15 are recorded as deferred figure records without numeric points. Their captions, panels, axes, species, phase, temperature text, series, source attribution, physical-page locators, and visible marker counts are retained. Figures 6, 7, 8, and 11 contain discrete markers; their marker counts were checked against 600 dpi physical-page renders (pages 10, 11, and 14). Figure 15 is a line plot without discrete point markers. Table 1 and shared preparation conditions were checked on physical pages 4 and 2 respectively; Tables 2 and 3 were checked on physical page 7. `page` is the one-based physical PDF page, with published folios stored separately.

The measured system is borate glass. It is not classified as silicate melt data.
