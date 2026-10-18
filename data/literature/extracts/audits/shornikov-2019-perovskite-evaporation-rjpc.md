# Shornikov 2019 RJPC extraction audit

Source: Shornikov, “Mass-Spectrometric Study of Evaporation of Perovskite,” Russian Journal of Physical Chemistry A 93(6), 1024–1031, DOI `10.1134/S0036024419060293`. The corpus PDF SHA-256 is recorded in the extract.

## Table comparison

| RJPC table | Printed rows | Numeric cells | Disposition | Render/audit evidence |
| --- | ---: | ---: | --- | --- |
| 1 | 8 | — | Rows 2, 5, and 8 cross-reference existing Banon, Lopatin and Semenov, and Zhang context. Rows 1, 3, 4, 6, and 7 remain attributed context. | Published p. 1025, PDF p. 2; 600-dpi render confirms row and column assignments. |
| 2 | 13 | 76 | All pressures match the existing TMS series at the same temperatures. Seven species cross-reference the existing observation IDs; no pressures are duplicated. | Published p. 1029, PDF p. 6; all 13 row assignments checked on the 600-dpi render. The audit CSV carries these 76 printed cells and the TMS observation/point used for each match. |
| 3 | 13 | 68 values, 10 dashes | The 13 equation (22), (23) values are a `measured_tabulated` O partial-pressure observation. Equations (24)–(28) are five `quoted_attributed` O partial-pressure observations; dash cells are omitted. | Published p. 1029, PDF p. 6; the 600-dpi render checked the values and dash positions. |
| 4 | 8 | 16 coefficient cells | The fitted log-pressure coefficients have no extract token. All eight rows remain typed refusals; no pressure values are reconstructed. | Published p. 1029, PDF p. 6; the render confirms the rows, signs, uncertainty marks, and printed `39 002` spacing for TiO. |
| 5 | 10 | 40 compared fields | All ten reaction-thermochemistry rows match the existing TMS context and are cross-referenced. Minus signs are visible in both renders; OCR dash-glyph differences are not value differences. | RJPC p. 1030, PDF p. 7, and TMS p. 262, PDF p. 10; both checked at 600 dpi. |

The Table 3 emit mapping was removed because its columns have mixed evidence classes; the mapper cannot assign the measured class to (22), (23) and quoted-attributed class to (24)–(28) in one table mapping. Table 3 was manually transcribed from the checked table. The retained mapping covers Table 2 as an emitter comparison only.

The committed [MinerU compare audit](shornikov-2019-perovskite-evaporation-rjpc.mineru-compare.csv) has 68 Table 3 matches produced by comparing the current extract to the draft index, plus 76 Table 2 cross-reference rows. The rows are marked separately so the test can compare every shared printed pressure and temperature against the values in the existing TMS extract. The Table 2 comparison covers:

| Existing observation ID | RJPC points | Result |
| --- | ---: | --- |
| `shornikov_2019_t1_p_ca` | 13 | exact numeric match |
| `shornikov_2019_t1_p_cao` | 10 | exact numeric match |
| `shornikov_2019_t1_p_ti` | 8 | exact numeric match |
| `shornikov_2019_t1_p_tio` | 13 | exact numeric match |
| `shornikov_2019_t1_p_tio2` | 13 | exact numeric match |
| `shornikov_2019_t1_p_o2` | 11 | exact numeric match |
| `shornikov_2019_t1_p_catio3` | 8 | exact numeric match |

## Text and figure records

The new extract keeps the RJPC laser ion-current ratio and 4890 K estimate as attributed context, the 4730 ± 100 K surface-temperature estimate, the 4000–6000 K comparable-experiment range, and the pyrometer and residual-pressure statements. Shared apparatus, preparation, ionization, appearance-energy, molybdenum-cell ratio, pressure-error, and melting-temperature context points to the existing TMS context IDs. Table 1 rows 3, 4, 6, and 7 retain their distinct cited-study summaries.

The RJPC ion-current ratio at 2182 K is the one observed disagreement: RJPC p. 1027 prints `100 : 0.06 : 0.07 : 54 : 100 : 0.12 : 0.73 : 0.02`; the TMS chapter p. 257 prints `80 : 0.04 : 0.1 : 75 : 100 : 0.02 : 40 : 0.3` in the same species order. Both readings are in the extract with their locators and a controller-decision flag. The TMS extract was not changed.

Figure 1 has four panel records and Figure 2 has one. Their captions, identities, axes, series attribution, and visual marker counts are recorded in context. The figure pages (RJPC PDF pp. 3–4) were rendered at 600 dpi. No plotted values were digitized; every record says `digitisation: deferred to a figure pass`.

## Migration disposition

The extract records six source observations: one measured 13-point O series and five quoted-attributed O series containing 55 non-dash cells. The isolated real-migrator expands these to 68 battery observation rows with zero hard issues. Table 2 and Table 5 add no duplicate rows. Table 4 remains eight context-only typed refusals. No store regeneration is committed.
