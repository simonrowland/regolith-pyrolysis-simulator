# Hertl and Pultz (1967) extraction audit

## Scope and source reading

The target article spans physical PDF pages 1–4, published pages 378–381. MinerU's markdown and content list contain no target-article text or tables. The scanned-page `pdftotext-layout.txt` OCR was used as the next read, and every stored numeric cell was checked against the PDF render. Pages 1–4 were inspected at 200 dpi; pages 2 and 3 were also inspected at 600 dpi for figure labels and machine-text disagreements. The cell-by-cell record is in `hertl-1967-solid-sio-disproportionation-vaporization.cells.csv`.

The article has zero printed data tables and zero printed table rows. Figs. 1–6 are figure-read candidates; captions, printed conditions, prose summaries, and the Fig. 1 and Fig. 5 marker legends are recorded, but no plotted coordinates are digitized. No numerical SiO vapour pressure is printed. The converter index contains zero MinerU tables; `hertl-1967-solid-sio-disproportionation-vaporization.compare.txt` records the corresponding zero-match comparison.

The render resolves two OCR errors: the disproportionation muffle is 18 by 2 in. ID (the text layer reads 13 by 2), and the Fig. 5 cross series is 1330°C with the filled-circle endpoint at 1070°C (3600 sec). The superscript 5 and 7 after the quoted 55 and 95 kcal/mole values are reference markers, not decimal digits.

## Extracted observations and evidence

Five scalar observations retain the printed values: 72.5 kcal/mole for this work; the approximate quoted range 55⁵–95⁷ kcal/mole; the attributed 76 and 77 kcal/mole values; and the Fig. 6 apparent activation energy 21.0 kcal/mole, also stated as 21 kcal/mole in the page 381 discussion. The 76 value is attributed to Gel'd and Kochnev (reference 1), the 77 value to Schäfer and Hörnle (reference 3), and the approximate range endpoints to references 5 and 7.

The authors calculate 72.5 from the Fig. 4 slope, and report 21.0 from the Fig. 6 slope using values from Fig. 5. The input coordinates are not stored because the figures remain undigitized. Following the fit-output rule, these fitted results are `quoted_attributed`; the cited literature values are also `quoted_attributed`. No model-derived parents are asserted.

The source explicitly says its vaporization experiments cannot distinguish `SiO(s) = SiO(g)` from `Si(s) + SiO2(s) = 2SiO(g)`. Therefore the vaporization-enthalpy rows use explicit descriptive quantity labels and remain quantity-unknown, instead of binding to a named-reaction token. The closed candidates are `simulator/battery/enums.py::Quantity.ENTHALPY_OF_VAPORIZATION_2ND_LAW` and `simulator/battery/enums.py::Quantity.ENTHALPY_OF_VAPORIZATION_3RD_LAW`; the related ruling is documented in `powell-1971-alkali-sulfate-metasilicate-vapour.audit.md`. The apparent activation-energy row has no exact token in `simulator/battery/enums.py::Quantity` or `simulator/battery/migrate.py::QUANTITY_ALIASES`, and no corresponding observation type in `tools/validate_literature_extracts.py::OBSERVATION_TYPES`. Its explicit descriptive label prevents the `rate_series` fallback to `mass_loss_rate` in `simulator/battery/migrate.py::TYPE_QUANTITY`.

## Conditions and apparatus

Temperature ranges are preserved from the abstract: vaporization 1005–1475°C and disproportionation 1000–1440°C. The apparatus records the 36 in. by 1½ or 2 in. ID vaporization muffle, the 18 by 2 in. ID disproportionation muffle, the 11.90 cm² alumina tray, the furnace thermocouple, the evacuated/argon sequence, and the gasket-sealed orifice used to move the charge. The source gives no orifice diameter or Clausing factor. Its evacuation wording, “at least 10 to 75 μ,” is retained without interpreting or converting it.

Conditions are not promoted between figures. The Fig. 1 caption separately states the 1200°C evacuated and 1474°C/1 atm argon runs; Fig. 3 states 1427°C and 1 atm argon; Fig. 5 states 1 atm argon. The Fig. 6 caption does not state pressure. Scalar thermochemistry rows have unknown temperature and total pressure where the source provides no single fitted temperature or numeric pressure; no ambient, vacuum pressure, fO2, or assumed 1 atm condition is added.

## Migration and verification

Isolated migration contains 5 observations, 0 hard issues, and 5 unavailable values. All five are quantity-unknown for the explicit schema gaps above; their SiO species phase maps to `condensed_solid` / `Phase.CR`. The temperature and total-pressure identity axes remain unknown, and the unprinted pressure key is absent from migrated point conditions. No fO2 condition is added. Three migrated identity-axis examples are printed by the focused test.

`tools/mineru_table_draft.py compare` reports `matched=0 no-match=0 mismatch=0 presence-only=0 needs_mapping=0 locator-error=0 unverifiable=0 total=0`, consistent with the zero-table inventory. This comparison does not substitute for cell review; the render evidence is recorded per cell in the CSV. The focused test pins the printed values, both legend mappings, the row/context counts, locators from physical pages 2–4, and a mutation that makes a pinned value fail fidelity.

Local checks: the targeted literature-extract validator reported `OK: 1 extract file(s) valid`; the Hertl focused test passed (2 tests), and `tests/test_yaml_flow_scalar_commas.py` passed (17 tests). `tests/test_literature_extracts.py` was divided into local `-k` groups: 886 tests passed, including all 633 fidelity-sample cases. The remaining two tests, `test_pilot_extract_count_exact_and_merge_smoke` and `test_repo_extracts_validate_green`, each exceeded the required 300-second cap when run alone and remain **UNVERIFIED-local**. No test failure was reported in the completed groups.

Expected generated observation-store delta on regeneration: **+5 observations**, all quantity-unknown typed refusals. Generated stores were not regenerated or committed.
