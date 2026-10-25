# Matsuzaki et al. (1998) extraction audit

## Scope and source

The extract stores the experimental FetO activities and their activity coefficients from Tables 1–3. The tables contain 64 sample rows: Table 1 has 20 rows (samples 26–45, published page 1147); Table 2 has 22 rows (samples 46–67, page 1147); Table 3 has 22 rows (samples 68–89, page 1148). Each sample contributes one activity and one activity-coefficient observation, for 128 observations total.

The extract was checked against MinerU and pdftotext machine text. The pdftotext layer is OCR, not an exact born-digital text layer. All 704 cells in Tables 1–3 were transcribed from rendered pages 1–2 and compared with the MinerU values; the rendered and MinerU strings agree for all 704 cells. The PDF SHA-256 is `37c68d874f43ff27114eaa7661761e0131e04b40fb3e0204ec5efe38e5763939`.

## Table and cell audit

| Table | PDF page | Published page | Printed rows | QA digit agreement with pdftotext | QA flags / mismatches | Disposition |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| 1 | 1 | 1147 | 20 | 0.909 | 79 comma/dot decimal flags; 20 mismatches | Stored; all 220 row cells checked on render |
| 2 | 1 | 1147 | 22 | 0.905 | 78 comma/dot decimal flags; 23 mismatches | Stored; all 242 row cells checked on render |
| 3 | 2 | 1148 | 22 | 0.901 | 54 comma/dot decimal flags; 24 mismatches | Stored; all 242 row cells checked on render |
| 4 | 3 | 1149 | 17 | 0.969 | 17 LaTeX flags; 2 mismatches | Render layout checked; interaction-energy table is outside the requested activity extraction |

The audit CSV records all 704 cells in Tables 1–3 (64 rows × 11 columns) with the MinerU spelling, render transcription, verification method (`b_render` for every cell), and whether the cell is stored. Row and column alignment was checked against each table render. The printed Fe3+/Fe2+ ratio column is included in the audit but not stored as an observation because this extraction covers activities, activity coefficients, compositions, and their experimental conditions. The other ten columns are represented in the extract or its row conditions.

The table-tool compare report has 128/128 matched cells and no unmatched values. For that comparison only, a scratch projection expanded the extract phase token `l` to `liquid`; the comparator's phase filter accepts `liquid` but rejects the schema's `l` token. Values, locators, row identifiers, and the committed extract were unchanged by this comparison-only normalization.

## Recorded experimental conditions and interpretation

- The experimental section reports 1673 K, a PID temperature control within ±1 K, a 10 h equilibration, and an H2/CO2 mixture controlling the oxygen potential. The gas flow was controlled, but no numerical flow rate or row-specific H2/CO2 ratio is printed.
- Each table row prints `log(P_O2/atm)`. It is stored as an absolute `log10(atm)` condition and reaches the migrated identity as fO2 in Pa through the existing oxygen-log conversion path.
- The source does not state total gas pressure. Each extract row declares total pressure unknown; migration leaves identity total pressure unknown. No ambient or standard total pressure is assumed.
- The crucible is reported as iron, i.d. 24 mm, o.d. 30 mm, and height 100 mm. The authors also state that the crucible was pure iron and use unit activity for solid iron.
- The slag was quenched in water after equilibrium. The source does not say whether quenching preserves the high-temperature state. The measured phase is therefore stored as liquid at 1673 K with quench state `not_stated`.
- The printed standard state is pure molten iron oxide in equilibrium with pure solid iron; the typed component is FetO(l).
- The authors identify the Table 1–3 values as experimental activities from gas-slag chemical equilibration. Activities are classified `measured_direct`. The printed activity coefficients are reduced from the corresponding activity and X_FetO row values; each gamma observation links to its activity observation and is classified `measured_reduced`. The paper's regular-solution contours and fitted interaction parameters are not substituted for these experimental values.
- Tables 1 and 2 compositions migrate to identity mole-fraction compositions from the printed mass-percent maps (84 observations). Table 3 prints AlO1.5; the closed mass-percent composition mapper has no token for that printed component. Those 44 observations retain the printed composition map and a located typed-unknown composition condition, with the schema gap named; their activity and gamma values, temperature, and fO2 still migrate.

## Isolated migration

| Representative row | Composition identity | Total pressure identity | fO2 identity | Temperature identity |
| --- | --- | --- | --- | --- |
| Table 1, sample 26, activity 0.36 | FeO 0.4742647069383984102215445333; Fe2O3 0.01134504540326722330496242863; TiO2 0.1535531125576476763530372022; SiO2 0.3061644558631359537290577073; CaO 0.05467267923755073639139812848 | unknown; source does not print total pressure | 0.000001975680040704839424107844447 Pa (printed log P_O2/atm = -10.71) | 1673 K |
| Table 2, sample 56, gamma 0.80 | FeO 0.4008071350303695161509129937; Fe2O3 0.05036513893867298666818043891; TiO2 0.2166916229116175338877320735; SiO2 0.2163259109790499709325898407; MgO 0.1158101921402899923605846534 | unknown; source does not print total pressure | 0.000001975680040704839424107844447 Pa (printed log P_O2/atm = -10.71) | 1673 K |
| Table 3, sample 89, gamma 0.67 | typed unknown: AlO1.5 composition-token schema gap | unknown; source does not print total pressure | 0.000001975680040704839424107844447 Pa (printed log P_O2/atm = -10.71) | 1673 K |

The real migrator was run on this extract alone: 128 observations, 1 experiment, 1 work, 0 hard issues, 0 unavailable values, and 0 registry issues. Identity axes across the 128 rows: temperature mapped on 128/128, absolute fO2 mapped on 128/128, total pressure unknown on 128/128, and composition mapped on 84/128 (the remaining 44 are the Table 3 AlO1.5 schema gap).

## Regression and validation evidence

- The focused extraction test passes (2 tests). It pins three values to their rendered page/table/sample locators, checks the 64-row table count, validates the observation-level gamma lineage, and checks migrated conditions and unknown total pressure on all 128 observations.
- `tests/test_yaml_flow_scalar_commas.py` passes (17 tests).
- The standalone literature validator passes with `--check-fidelity-match` for this extract (`OK: 1 extract file(s) valid`).
- A mutation changing the rendered Table 1 sample 26 activity from 0.36 to 0.35 made the focused render-pin test fail; the source value was restored and the focused test passes again.
- The repository-wide `tests/test_literature_extracts.py` run was interrupted after 329 seconds: 126 passed, and it reported one failure in the independent `test_docs_private_reference_in_nested_prose_is_refused_but_path_is_allowed` fixture test. The failure concerns the validator's handling of `extraction.provenance_path` in a minimal fixture and does not load this extract. The repository-wide suite did not complete.

## Excluded printed material

Table 4 reports regular-solution interaction parameters, not additional measured activities. Figures 1–3 show estimated/model activity contours and data presentation; any values read from those figures are figure-read candidates and were not digitized.
