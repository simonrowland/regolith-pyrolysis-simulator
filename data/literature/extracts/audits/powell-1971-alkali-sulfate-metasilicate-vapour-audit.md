# Powell (1971) extraction audit — pending pressure quantity ruling

## Source and scope

Source: Powell, David Guy (1971), *A thermodynamic study of the sulphates and related compounds of alkali and alkaline earth metals at high temperatures*, PhD thesis, University of St Andrews. Source ID: `powell-1971-alkali-sulfate-metasilicate-vapour`.

The machine-text inventory identifies printed Tables 1–13 as the thesis's primary vapour-pressure and thermochemistry tables. The MinerU table draft detects 39 table blocks overall; the remaining blocks include apparatus/method tables, literature-survey values, chart-derived blocks, and appendix tables. Their relevance and printed captions still require the full inventory before extraction.

## Unresolved quantity decision

The source calls the Knudsen weight-loss output in Tables 1–5 and 9–10 `P'` and describes it as a vapour pressure calculated using an assumed vapour-phase molecular weight corresponding to the salt. It explicitly cautions that the assumed molecular weight may be incorrect when more than one gas species effuses. Torsion Tables 7, 8, and 11 report `P` without identifying individual gas species. These are therefore not printed species-specific partial pressures.

The current schema has `Quantity.P_SAT = "p_sat"` and `Quantity.P_PARTIAL = "p_partial"` in `simulator/battery/enums.py:64-67`; there is no total mixed-vapour pressure quantity. The migration code says its tabulated T/P path cannot distinguish p_sat from p_partial by row type (`simulator/battery/migrate.py:6194`) and the p_sat identity path names a gas species (`simulator/battery/migrate.py:5386-5387`).

Please rule whether these unresolved total vapour-pressure measurements should be represented as `p_sat` of the named condensed salt (with the gas species unresolved in notes), or left quantity-unknown using a descriptive non-token label and a schema-gap note. No pressure rows have been stored pending this decision.

## Machine-read screen

`tools/mineru_table_draft.py convert` produced the 39-block draft index. `tools.mineru_table_qa.py` flagged numerous comma-decimal, numeric-mismatch, and LaTeX cells; flagged values require page-render review before any numeric cell is committed. Tables 7, 8, and 11 have clean structured MinerU tables in the source content lists. The other pressure tables include OCR disagreements that require cell-level resolution.

## Not yet complete

No extract rows, focused test, migration, complete table inventory, table mapping, or compare output have been committed. The full-cell audit, required tests, and expected battery-store delta remain pending the quantity ruling and completion of the transcription.
