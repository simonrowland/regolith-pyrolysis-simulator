# Shornikov 2019 RJPC extraction audit

Source: Shornikov, “Mass-Spectrometric Study of Evaporation of Perovskite,” Russian Journal of Physical Chemistry A 93(6), 1024–1031, DOI `10.1134/S0036024419060293`. The corpus PDF SHA-256 is recorded in the extract.

## Table comparison

| RJPC table | Printed rows | Numeric cells | Disposition | Render/audit evidence |
| --- | ---: | ---: | --- | --- |
| 1 | 8 | — | Rows 2, 5, and 8 cross-reference existing Banon, Lopatin and Semenov, and Zhang context. Rows 1, 3, 4, 6, and 7 are retained as attributed context. | Published p. 1025, PDF p. 2; 600-dpi render confirms row and column assignments. |
| 2 | 13 | 76 | All 76 pressures match the existing TMS series at the same temperatures. Seven species cross-reference the existing observation IDs; no pressures are duplicated. | Published p. 1029, PDF p. 6; all 13 row assignments checked on the 600-dpi render. |
| 3 | 13 | 68 values, 10 dashes | Atomic-oxygen model outputs are retained as printed strings in `O.context[]`, with all dash cells preserved. They are not scored observations. | Published p. 1029, PDF p. 6; all cell assignments checked on the 600-dpi render. |
| 4 | 8 | 16 coefficient cells | The fitted log-pressure coefficients have no extract token. All eight rows are typed refusals with the printed ranges and coefficients retained; no pressure values are reconstructed. | Published p. 1029, PDF p. 6; the 600-dpi render confirms the rows, signs, uncertainty marks, and the printed `39 002` spacing for TiO. |
| 5 | 10 | 40 compared fields | All ten reaction-thermochemistry rows match the existing TMS context and are cross-referenced. Minus signs are visible in both renders; OCR dash-glyph differences are not value differences. | RJPC p. 1030, PDF p. 7, and TMS p. 262, PDF p. 10; both checked at 600 dpi. |

The table emitter produced 144 intermediate observations (76 shared Table 2 cells and 68 Table 3 cells), skipped 25 dashes, and the MinerU compare output matched all 144 numeric cells. The committed CSV is [shornikov-2019-perovskite-evaporation-rjpc.mineru-compare.csv](shornikov-2019-perovskite-evaporation-rjpc.mineru-compare.csv). It is an audit artifact; the emitted shared pressures and model-derived values are not copied into `observations[]`.

The committed table mapping is [shornikov-2019-perovskite-evaporation-rjpc.mapping.yaml](../../../../tests/tools/fixtures/mineru-table-emit/shornikov-2019-perovskite-evaporation-rjpc.mapping.yaml); it maps the duplicate-pressure and model-table transcription intermediates.

Table 2 comparison against the existing extract, by matching temperature and species:

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

The new extract keeps the RJPC laser ion-current ratio and the 4890 K estimate as attributed context, the 4730 ± 100 K surface-temperature estimate, the 4000–6000 K comparable-experiment range, and the pyrometer and residual-pressure statements. Shared apparatus, preparation, ionization, appearance-energy, molybdenum-cell ratio, pressure-error, and melting-temperature context points to the existing TMS context IDs. Table 1 rows 3, 4, 6, and 7 retain their distinct cited study summaries.

The RJPC ion-current ratio at 2182 K is the one observed disagreement: RJPC p. 1027 prints `100 : 0.06 : 0.07 : 54 : 100 : 0.12 : 0.73 : 0.02`; the TMS chapter p. 257 prints `80 : 0.04 : 0.1 : 75 : 100 : 0.02 : 40 : 0.3` in the same species order. Both readings are in the extract with their locators and a controller-decision flag. The TMS extract was not changed.

Figure 1 has four panel records and Figure 2 has one. Their captions, identities, axes, series attribution, and visual marker counts are recorded in context. The figure pages (RJPC PDF pp. 3–4) were rendered at 600 dpi. No plotted values were digitized; every record says `digitisation: deferred to a figure pass`.

## Migration disposition

The new source intentionally has zero scoreable observations: the measured Table 2 series already exist in the TMS extract, while the new numeric tables are model outputs or unsupported fit coefficients. The isolated real-migrator run must therefore report zero observations and zero hard issues; the eight fit records remain context-only typed refusals.
