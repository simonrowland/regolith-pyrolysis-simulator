# Shornikov 1999 silica partial vaporization coefficients — extraction audit

Source: Shornikov, S. I., Archakov, I. Yu., and Shul'ts, M. M. (1999). Mass Spectrometric Study of Vaporization and Thermodynamic Properties of Silicon Dioxide. II. Determination of Partial Vaporization Coefficients of Silicon Dioxide. Russian Journal of General Chemistry, vol. 69, no. 2, pp. 187–196. Translated from Zhurnal Obshchei Khimii, vol. 69, no. 2, 1999, pp. 197–206. Received 6 April 1998. No DOI is printed. corpus sha256 3c520be818701c126c4df21c0f5685f1e6c3109fd8e06991378398b85f02a362.

Part I is shornikov-1998-silica-vaporization (RJGC 68(8), 1171–1177). Part III is shornikov-2000-silica-gas-equilibria (RJGC 70(3), 360–370). This paper is neither of those.

Scored observations: 15. Context rows: 35.

## Duplicate verdict

Not a duplicate. The title on published p. 187 is part II, determination of partial vaporization coefficients. No extract on this base stores these Table 4 coefficients.

## Standard state

These rows are evaporation coefficients and pressure-fit coefficients, not activities. Migration reads standard_state prose only for activity and activity_coefficient. No standard_state field is written.

Published p. 187: “the condensed and gas phases are denoted by brackets and parentheses, respectively.” The sample is pure [SiO2]. The paper does not say solid, liquid, or a polymorph. Part I prints the melting point of [SiO2] as 1996±5 K. Every this-work temperature in Tables 1–4 ends at or below 1983 K, which is outside the melting band 1991–2001 K and within 100 K of 1996 K. The SiO Table 4 interval 1700–1950 K also spans the temperature where tridymite and cristobalite change stability. This paper does not discuss that change, so no polymorph is stored. Expected migration phase: unknown, because the phase key is omitted.

## Table 4 — admitted this-work cells

Header α_i × 10^2. Stored alpha = printed/100. Fifteen cells: three vapor species and five columns. Columns (10), (15), (17), and (19) are the printed results of those equations. The recommended column is not their arithmetic mean, so it has no derived_from link.

| species | T, K | column | printed ×10^2 | alpha |
| --- | --- | --- | --- | --- |
| SiO | 1700–1950 | (10) | 2.01±0.45 | 0.0201 |
| SiO | 1700–1950 | (15) | 2.39±0.35 | 0.0239 |
| SiO | 1700–1950 | (17) | 2.25±0.25 | 0.0225 |
| SiO | 1700–1950 | (19) | 2.20±0.24 | 0.0220 |
| SiO | 1700–1950 | recommended value | 2.23±0.30 | 0.0223 |
| SiO2 | 1850–1950 | (10) | 1.56±0.52 | 0.0156 |
| SiO2 | 1850–1950 | (15) | 1.90±0.43 | 0.0190 |
| SiO2 | 1850–1950 | (17) | 1.64±0.35 | 0.0164 |
| SiO2 | 1850–1950 | (19) | 2.02±0.38 | 0.0202 |
| SiO2 | 1850–1950 | recommended value | 1.80±0.41 | 0.0180 |
| Si2O2 | 1850–1950 | (10) | 1.31±0.56 | 0.0131 |
| Si2O2 | 1850–1950 | (15) | 1.60±0.49 | 0.0160 |
| Si2O2 | 1850–1950 | (17) | 1.53±0.41 | 0.0153 |
| Si2O2 | 1850–1950 | (19) | 1.88±0.45 | 0.0188 |
| Si2O2 | 1850–1950 | recommended value | 1.62±0.47 | 0.0162 |

Refused Table 4 rows, literature, not measured in this paper:

- (SiO) 1692–1739 K, reference [15]: (10) 5.0±0.9, (15) 3.2±0.9, recommended 3.2±0.9. Columns (17) and (19) are empty.
- (SiO) 1800–1950 K, reference [16]: (10) 5.6±4.9, (15) 5.6±4.2, recommended 2.2±0.8. Columns (17) and (19) are empty.
- (SiO) 1800–1950 K, reference [16]: (15) 2.1±1.6, recommended 2.2±0.8. The other equation columns are empty.

The [16] recommended cell 2.2±0.8 × 10^{-2} is the comparison quoted on published p. 189, (2.2±0.8)×10^{-2} at 1820–1960 K. It is not re-stored.

## Tables 1–3 — context only

This-work rows are context of type experiment_series. The quantity token partial_pressure names the relation. The stored numbers are A, B or the printed −a(i), −b(i), not a pressure. Literature rows of Tables 1 and 3 are refused and are not stored.

Table 1 this-work (SiO), Mo, published p. 189: 1623–1930 K, n=31, f=1.12×10^{-3}, A=27880±612, B=10.84±0.15; 1616–1947 K, n=24, f=2.39×10^{-3}, A=27240±420, B=10.45±0.24; 1650–1970 K, n=31, f=3.35×10^{-3}, A=26932±820, B=10.27±0.27; 1690–1947 K, n=32, f=3.75×10^{-1}, A=26452±860, B=8.00±0.48.

Table 2, all eight rows, this work, Mo, published p. 190. (SiO2) then (Si2O2), four f values each. The open-cell f is 3.75×10^{-1}, equal to the printed cell Clausing coefficient 0.375 when s_or = S_c.

Table 3 this-work Mo rows, published p. 193. Printed columns are −a(i) and −b(i). SiO at 1700, 1750, 1800, 1850, 1900, and 1950 K; (SiO2) and (Si2O2) at 1850, 1900, and 1950 K. Ditto marks continue This work.

## Figures

No figure value is stored. MinerU chart tables were not used.

| figure | decision | evidence |
| --- | --- | --- |
| 1, series 1–6 | refused | Straight lines, no markers. Series 6 is the Table 1 this-work fit. Series 1–5 cite [29], [15], [16], and [6]. |
| 2a, 2b, 2c | deferred | This work's markers sit on the Table 1 and Table 2 lines. No labelled-tick calibration was stored, and no marker count was invented. No point pressure. |
| 3 | refused | Silver calibration against Paule and Mandel. Not an oxide measurement. |
| 4a series 1–10 | refused | Effusion results of [15] and [16], as the caption states. |
| 4a series 11–16, 4b, 4c | deferred | This work's p_ef/p_eq markers. Their least-squares constants are Table 3. Markers were not read. |
| 5, series 1–5 | refused | Equation (8) applied to Nagai et al. [16]. Not this work. |

Fig. 2's MinerU caption prints series 4 as f = 3.75×10^{-3}. The table cell and equation (11), with the printed open-cell Clausing coefficient 0.375, give 3.75×10^{-1}. The caption exponent was not stored.

## Render checks

pdftoppm -r 600 of PDF pages 3, 4, 7, and 8. Running headers read 189, 190, 193, and 194, so published page = 186 + PDF page. The journal line on PDF page 1 reads pp. 187–196.

Tesseract of the 600 dpi table bands agrees with MinerU on every this-work digit that tesseract did not fuse. Fused cells, kept as MinerU and not edited: Table 3 SiO 1800 K, OCR “2243” and “4,626”, MinerU 22±3 and 4.626±0.008; Table 3 SiO 1850 K, OCR “28244”, MinerU 28±4. Table 3 SiO 1700 K, OCR “§+3”, MinerU 8±3. Tesseract drops the minus in 10^{-n} and garbles species glyphs. Species labels and exponents are the MinerU text.

## What was not stored

Literature rows of Tables 1, 3, and 4. Empty equation cells. Point pressures read off Figs. 1–5. Equilibrium pressures computed from 10^{b(i)}. A parent link from a recommended alpha to the equation columns or to Table 3.
