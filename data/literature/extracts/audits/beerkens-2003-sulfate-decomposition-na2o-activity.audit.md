# Beerkens (2003) Na2O activity audit

The two activity fits are on absolute PDF page 5 (published p. 1897), §VI. The approximate float-glass scalar is on absolute PDF page 6 (published p. 1898), in the final paragraph of §VI before the §VII heading. MinerU markdown and the born-digital pdftotext layer agree on the fits and scalar. Renders of pages 5 and 6 confirm the expressions, `about 1.7 × 10^-8`, temperature 1673 K, and float-glass context. A page 4 render confirms the listed float-glass oxide composition (published p. 1896). The standard-state convention is on PDF page 1 (published p. 1893), §II. No flagged activity cell or machine-read disagreement required a render.

The fitted expressions are not scalar activity points. `QUANTITY_ALIASES["activity"]` (`simulator/battery/migrate.py:489`) supports the scalar a(Na2O) value. But the closed `OBSERVATION_TYPES` set (`tools/validate_literature_extracts.py:79-100`) has no activity-function observation type, and `TYPE_QUANTITY` (`simulator/battery/migrate.py:435-449`) has no such function type either. `PHASE_BASIS_TOKENS` (`simulator/battery/enums.py:663`) is for composition-basis rows, not activity equations. The nearby `Quantity.ACTIVITY_COEFFICIENT_TEMPERATURE_FIT` token (`simulator/battery/enums.py:74`) represents log-gamma fits, and its parser (`simulator/battery/migrate.py:10705-10714`) reads `A_printed` and `B_printed` coefficient rows, not a(Na2O). Following the context disposition for untyped fitted parameters, the two activity equations are retained as unscored context records with a schema-gap note and coefficients verbatim. The scalar is the authors' own reduction of measurement data, recorded as `measured_tabulated` per the controller ruling. The derivation uses Eq. (15), `a(Na2O) = Ka × Rc / (p(SO2) × p(O2)^0.5)`, with `Ka` from Eq. (3), `Ka = exp(-78616/T + 28.195)` (`T` in K). The printed inputs (`a(Na2SO4) = 1`, `p(SO2) = 0.666 bar`, and `p(O2) = 0.33 bar`) are derivation context and point conditions, not observations or parents. The pure-phase reference convention is printed on PDF p. 1 / published p. 1893, §II; the specific Na2O pure phase is not named and remains unknown.

| Stored numeric cell | Source locator | Confirmation |
| --- | --- | --- |
| Model-glass inverse-temperature coefficient `-43552` | PDF p. 5 / published p. 1897, §VI | MinerU + pdftotext agree; p. 5 render |
| Model-glass intercept `8.33` | PDF p. 5 / published p. 1897, §VI | MinerU + pdftotext agree; p. 5 render |
| Float-glass inverse-temperature coefficient `-46000` | PDF p. 5 / published p. 1897, §VI | MinerU + pdftotext agree; p. 5 render |
| Float-glass intercept `9.50` | PDF p. 5 / published p. 1897, §VI | MinerU + pdftotext agree; p. 5 render |
| Float-glass activity `about 1.7 × 10^-8` at 1673 K | PDF p. 6 / published p. 1898, §VI | MinerU + pdftotext agree; p. 6 render |
| Oxygen pressure input `0.33 bar` | PDF p. 6 / published p. 1898, §VI | pdftotext and p. 6 render; stored as `pO2_Pa` |
| Sulfur-dioxide pressure input `0.666 bar` | PDF p. 6 / published p. 1898, §VI | pdftotext and p. 6 render; stored as `pSO2_Pa` |
| Float-glass SiO2 `72.2 wt%` | PDF p. 4, §III(1) | pdftotext and page 4 render agree |
| Float-glass Na2O `13.4 wt%` | PDF p. 4, §III(1) | pdftotext and page 4 render agree |
| Float-glass CaO `9.5 wt%` | PDF p. 4, §III(1) | pdftotext and page 4 render agree |
| Standard-state convention | PDF p. 1 / published p. 1893, §II | Source text and page locator checked |

Table-tool pre-screen found two tables. Table I has five body rows and Table II has five body rows; MinerU's Table II block includes a spurious extra row, so the rendered layout was used to confirm the printed body count. Table I had six HTML rows by three columns; Table II had seven by four. Both blocks had 1.000 numeric agreement with pdftotext and zero flagged cells. Their contents are sulfur-solubility data, outside this activity extraction. No activity tables were present. Printed activity record count: one scalar plus two fitted expressions (three source records); Figure 5's points are excluded by the figure-only rule.

Figure-read candidate: Figure 5 compares present-study Na2O activity with literature curves/points. No figure values were digitized.
