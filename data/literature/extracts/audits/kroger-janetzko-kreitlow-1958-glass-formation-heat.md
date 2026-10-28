# Kröger, Janetzko and Kreitlow (1958): extraction audit

The scanned article has no text layer. The first machine read was MinerU table HTML/markdown; the independent second read was the specified Tesseract layout OCR. Both machine reads agree on the stored numeric cells. The source pages were rendered to verify table row and column placement at every distinct locator and to settle the conflicts recorded below. The table-to-extract map is in the adjacent `.mapping.yaml`; it uses printed table labels and absolute PDF pages.

`mineru_table_draft.py compare` was run on the full extract and converted MinerU draft. It reported 118 `unverifiable` locator records and no match/mismatch records because the scanned MinerU blocks do not preserve printed caption evidence (`cannot verify table label ...`); this is not a numeric disagreement. The complete output is committed in the adjacent `.mineru-compare.csv`. Every used table/page label was checked against the physical render, and cell values/layout were checked against both machine reads and the page renders as detailed below.

## Coverage and transcription

The extract stores 35 observations: 16 detailed direct calorimetry means; 2 direct-calorimetry corrections for reducing conditions; 5 component dissolution heats; 5 glass dissolution heats used by solution calorimetry; and 7 authors' solution-calorimetry glass-formation results. Composition vectors are taken from the matching analyzed-glass row in Tables 3, 8b, 9, 10, 11 and 12a. The stored means and summaries retain their printed units and precision. Printed widths are preserved with confidence level `not_stated`.

| Printed source cells | PDF page / locator | Cell audit |
| --- | --- | --- |
| Fourcault Detag 123.6 ± 0.8; Delog oxidising 125.6 ± 1.8; Libbey-Owens oxidising 111.8 ± 1.4 cal/g | 222, prose in section 2 | MinerU and Tesseract agree; render confirms row labels, decimal digits and ± values. These values are prose, not Table 2; Table 2 is raw-material analysis. |
| Delog reducing 137.86; Libbey-Owens reducing 124.28 cal/g | 222, prose and equation (3) correction | Both OCR reads agree; render confirms the printed values. They are retained as `measured_reduced`, linked to their direct measurement parents. |
| Table 3 analyzed composition for the three Tafelglas rows | 222, Table 3 | Both OCR reads agree; render confirms all row-to-composition assignments and aggregate `R2O3` columns. |
| Feldspar −425.1 ± 0.9 and corrected −455.4; dolomite −98.0 ± 0.1; sulfate +24.68 / +3.5 kcal/mol; sulfite −16.08 / −2.03 kcal/mol / corrected −24.24; sulfide −315.0 ± 1.1 / −24.57 kcal/mol | 223, Table 4 | Both OCR reads agree on signs and digits; render confirms component and column assignments. Five observation rows retain the separately printed cells. |
| Detag −583.7 ± 0.4; Delog −586.8 ± 0.4; Libbey-Owens −581.2 ± 0.9 cal/g dissolution heats | 223, Table 5 | Both OCR reads agree; render confirms the three glass labels, negative signs and uncertainty values. These are dissolution inputs, not formation heats. |
| Table 6b auxiliary formation values: Detag +120.30; Delog +172.18 raw / +132.60 corrected; Libbey-Owens +148.03 raw / +116.70 corrected kcal/kg | 223, Table 6b | Both OCR reads agree; render confirms each row and column. Values are retained on their corresponding solution-calorimetry observations. |
| Table 7 Tafelglas direct and solution-calorimetry summaries, including reducing direct Delog 13786 and Libbey-Owens 12428 kcal/100 kg | 224, Table 7 | Both OCR reads agree; render confirms the direct/solution columns and oxidising/reducing attribution. |
| Brown 126.60 ± 1.6; chrom-green 124.40 ± 1.2; green 113.80 ± 0.9; white 126.8 ± 1.5 cal/g | 224, Table 8a | Both OCR reads agree; render confirms all four rows, means and uncertainties. |
| Brown, chrom-green, green and white analyzed compositions | 224, Table 8b | Both OCR reads agree; render confirms row labels and composition columns; chrom-green Cr2O3 is 0.29 mass percent. |
| Rohr I 130.2 ± 1.7; Rohr II 121.1 ± 3.1; Rohr I dissolution −566.6 ± 1.0 cal/g | 225, Table 9 | Both OCR reads agree; render confirms signs and row assignment. |
| Speiser 109.6 ± 2.8 cal/g and composition | 225, Table 10 | Both OCR reads agree; render confirms mean, uncertainty and glass composition. |
| Geräteglas 107.0 ± 2.4; Kristallglas 123.7 ± 1.8; Bleikristallglas 90.3 ± 2.9 cal/g | 226, Table 11 | Both OCR reads agree; render confirms values and heading order. |
| Sulfatglas I 209.5 ± 3.8; II 175.8 ± 4.3; III 203.5 ± 3.3 cal/g | 226, Table 12a | Both OCR reads agree; render confirms values and heading order. |
| Table 16 direct summaries for 18 direct rows and solution summaries for 7 solution results | 228, Table 16 | Both OCR reads agree; render confirms each row and column. “Aus Bildungswärmen” is a calculated comparison column and is excluded from measured data. |

Table 13 is not a measurement table: it calculates a comparison from assumed component reactions and is excluded. The article's figure-only claims were not digitized.

Two incompatible printed summaries are intentionally preserved. Table 8a prints green bottle glass as 113.80 ± 0.9 cal/g, while Table 16 gives 10700 kcal/100 kg; the detailed measured cell and the summary remain side-by-side. For reducing Libbey-Owens glass, the prose and Table 7 give 124.28 cal/g and 12428 kcal/100 kg, while Table 16 gives 12420 kcal/100 kg. Neither discrepancy is normalized or silently corrected.

## Conditions, reaction and evidence

The article defines the measured process as `Gemenge = Glas + Gas` and describes the heat as referenced to 20°C. Each observation carries `T_C: 20` and a located `temperature_C` point condition. The analyzed glass composition is attached from its printed row where available. No total pressure is printed for these experiments; none is added. For glass observations, the source gives no quench/preservation statement, so `quench_state: not_stated` is recorded with the measurement page. No measurement is presented as in-situ liquid glass.

The direct calorimetry means are `measured_direct`. The two carbon-reaction corrections are `measured_reduced` and link to the corresponding oxidising measurement. Component and glass dissolution heats are measured inputs. The solution-calorimetry results are the authors' own tabulated measurements (`measured_tabulated`); their reducing-condition values are `measured_reduced` from their oxidising solution rows. Their lineage lists stored inputs only. In particular, the older paper is not used as a `derived_from` parent.

The 1956 paper's direct 1:1:6 batch result is 117.8 ± 2.5 cal/g (11780 kcal/100 kg), for the distinct printed glass composition 12.97:11.73:75.30 mass percent Na2O:CaO:SiO2. It is not duplicated here. The 1958 article cites Kröger and Janetzko, Glastechnische Berichte 29 (1956), 393–400 as reference [2] for prior quartz, soda and limestone solution heats; those auxiliary values are not repeated as measured glass-formation rows. The old calculated “aus Bildungswärmen” comparison is likewise not admitted as a measurement.

## Schema decision

`tools/validate_literature_extracts.py:96-99 OBSERVATION_TYPES` admits `gibbs_table`, so the measured rows use that observation type. `simulator/battery/migrate.py:442 TYPE_QUANTITY` assigns `gibbs_table` a default `delta_fG`; the exact-key set `QUANTITY_ALIASES` at line 460 has no token for batch-to-glass-plus-gas reaction heat. Therefore formation rows declare the descriptive non-token `heat_of_glass_formation_from_batch`, which the migrator refuses as quantity-unknown rather than misclassifying as `delta_fG`. `simulator/battery/enums.py:89 Quantity.DELTA_FH` is not a valid substitute: it means formation from the elements, while the printed reaction begins from a technical batch. The separate measured dissolution inputs use `heat_of_solution_as_printed`, also a non-token; no exact solution-heat quantity exists in the migration aliases. `Quantity.ENTHALPY_OF_FUSION` (enums.py:92) is not a substitute because these are dissolution heats, not fusion heats. No near-miss quantity token is assigned.

There is no phase gap: `simulator/battery/migrate.py:399 PHASE_MAP` recognizes `glass`, and `simulator/battery/enums.py:322` includes `PhaseRole.GLASS`. Each glass is stored as the measured glass phase. Tabelle 12a's glass composition entries are printed in kilograms and remain plain printed maps, without a percent or mole-fraction basis; they are not converted to a different quantity or used to claim a phase-composition observation.

The isolated migration yields 35 observations, 0 hard issues, 35 unavailable typed refusals and exactly 35 quantity-unknown observations; no phase is unknown. Every observation retains its 20°C point condition with the section 8 locator; printed glass compositions and the not-stated quench state reach each applicable observation. Pressure remains absent. The expected battery-store delta is 35 refused quantity-unknown observations and 0 admitted scored observations. No store regeneration was committed.
