# Nagahara (1993) full-cell audit

## Scope and source reads

- Source: `nagahara-1993-sio2-evaporation-equilibrium-vacuum`, PDF pages 1–2 (published pages 1045–1046).
- Machine source order: MinerU markdown and content list. The pdftotext file contains only the ADS bibcode and form feeds, so it supplied no numeric cells.
- `tools/mineru_table_draft.py convert` found 0 tables. `tools.mineru_table_qa` found 0 tables and 2 chart blocks whose `<details>` tables are invented chart readings. No table mapping or compare file applies.
- The full two-page PDF render was checked for every numeric value retained below and for the row/legend assignment. Figure 1's marker legend was re-rendered at 600 dpi and checked marker by marker. Both charts are kept as `figure_only`; no plotted point values were transcribed. The MinerU `<details>` point tables were ignored.
- Context count: 9 records (1 setup, 6 prose rate findings, 2 figure records); printed table row count: 0 because the abstract has no table.

## Numeric-cell audit

| Stored cell(s) | Machine read | Locator | Render verification |
|---|---|---|---|
| `experimental_temperature_C`, `vacuum_chamber_base_pressure_bar_bounds_as_printed`, `sample_mass_as_printed` | `1600`, `1700`; `10^-11`–`10^-9 bar`; about `10mg` | PDF p. 1 / published p. 1045, experimental-procedure paragraph | Confirmed on page render; base pressure retained only as chamber base pressure, not promoted to every row. |
| `rate_factor` (C-capsule / Mo-capsule equilibrium) | about `3.5` | PDF p. 1 / published p. 1045, results paragraph | Confirmed on page render; C-capsule and Mo-capsule assignments match the prose. |
| `rate_factor`, `abstract_comparison_as_printed` (vacuum / equilibrium) | about `0.02`; about two orders of magnitude smaller | PDF p. 1 / published p. 1045, abstract and results paragraph | Both statements confirmed on page render; stored as distinct printed forms, without deriving one from the other. |
| `rate_factor_range`, `temperature_C_range` (reference [6]) | `0.011-0.015`; `1560-1685°C` | PDF p. 1 / published p. 1045, results paragraph and reference [6] | Confirmed on page render; attributed to Hashimoto (1990), not Nagahara's measurement. |
| `hydrogen_pressure_bar` and `rate_relation_as_printed` at low H2 | `10^-7 bar`; nearly same as vacuum / as small as free evaporation | PDF p. 1 abstract and p. 2 results paragraph / published pp. 1045–1046 | Confirmed on both page renders; this record is `gas_system: H2` and remains separate from the vacuum record. |
| `hydrogen_pressure_bar`, `rate_factor_range`, abstract relation at high H2 | `10^-5 bar`; about `3-4` times larger; larger than equilibrium | PDF p. 1 abstract and p. 2 results paragraph / published pp. 1045–1046 | Confirmed on both page renders. The detailed sentence does not name the comparator for `3-4`; the extract leaves that comparator unassigned. |
| Figure 1 legend condition values | `1600°C`, `1700°C`, `10^-7 bar hydrogen gas`, `10^-5 bar hydrogen gas` | PDF p. 2 / published p. 1046, Fig. 1 caption | Confirmed against the rendered legend; only series conditions are retained, not plotted data. |
| Figure 1 marker-to-condition pairing | solid circle, cross, diamond, circle, square, plus, triangle mapped to the seven printed conditions | PDF p. 2 / published p. 1046, Fig. 1 caption | Re-rendered at 600 dpi; every marker and its following condition were checked against the caption. The focused fidelity sample pins the complete printed legend list. |
| Figure 2 printed slopes | `1`; `1/2` | PDF p. 2 / published p. 1046, Fig. 2 caption and preceding paragraph | Confirmed on page render. These are printed summary slopes; graph points remain undigitized. |

The prose also states that hydrogen-gas evaporation is more effective than at higher total pressure, gives the possible SiO2-H2 reaction, says the evaporated fraction falls below equilibrium increasingly with time, and states that the hydrogen rate is neither the equilibrium rate nor the vacuum rate nor between them. These qualitative statements are retained verbatim in the context row; no total-pressure condition is inferred from them.

## Typing and migration

The prose reports relative evaporation-rate comparisons, not absolute mass-per-time values. `Quantity.EVAPORATION_RATE` and `Quantity.MASS_LOSS_RATE` in `simulator/battery/enums.py` are absolute rates; `Quantity.EVAPORATION_COEFFICIENT_ALPHA` is not a rate ratio. `simulator/battery/migrate.py::QUANTITY_ALIASES` maps `rate_series` to `MASS_LOSS_RATE`, while `tools/validate_literature_extracts.py::OBSERVATION_TYPES` is closed and has no relative-rate comparison type. No exact quantity token exists, so the printed comparisons are typed-refused context, not assigned a near-miss quantity. Their underlying point inputs are only plotted and are excluded by the task; the authors' reduced ratios therefore carry `quoted_attributed` evidence as required by d-110. The Hashimoto ratio is independently marked as quoted attribution.

The same schema gap appears in `data/literature/extracts/richter-2008-cai-like-liquids-lpsc-abstract.yaml`, which keeps a quoted relative evaporation-rate comparison in context with no quantity token. That precedent supports retaining Nagahara's prose comparisons as context rather than binding them to an absolute-rate token.

The source gives 1600°C and 1700°C as experiment temperatures and a vacuum-chamber base-pressure range. It does not give a numeric capsule total pressure or a total pressure for the hydrogen rows. The H2 comparison records each printed hydrogen pressure separately as `gas_system: H2`; the vacuum comparison is separately marked `gas_system: vacuum`. No fO2 value or composition is inferred. The H2 factor's comparator stays unassigned because the detailed sentence does not state one.

The focused test runs the real migrator on an isolated copy and asserts 0 observations, 0 hard issues, 0 unavailable values, and 0 quantity-unknown or phase-unknown observations; all 9 contextual records survive migration. The expected generated-store delta is 0 observations and 9 context records; no store regeneration was committed.
