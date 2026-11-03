# Nagahara and Ozawa (1996) extraction audit

## Table 1: full-cell audit

Printed count: 45 experiment rows. MinerU draft `p004-t01` contains the complete table on physical PDF page 4 (published page 1448); MinerU page index 3 is zero-based within chunk `p003-p004`. Table number is the printed Table 1, not the draft ordinal.

The QA pre-screen compared 505 numeric cells and found 120 MinerU/pdftotext digit disagreements. I rendered physical PDF page 4 at 600 dpi and checked each populated numeric cell in columns 1, 2, 4–12, and 14, for all 45 body rows, resolving every disagreement against the render. The row and column assignment was checked visually across the full grid. Table-draft compare reports 137 matched values, with zero no-match, mismatch, presence-only, mapping-needed, locator-error, or unverifiable results; the row-level output is committed in the companion CSV. Parentheses in column 14 are the printed standard errors; `*` in column 13 is the printed Opt marker. Species assignment is forsterite (Mg2SiO4) throughout.

All listed cells use audit method (b): page render. Cell coordinates are physical PDF page 4 / draft row / printed Table 1 column, including each numeric run identifier in column 1.

| draft row | run | P (bar) | Gas | t (h) | a0 (cm) | b0 (cm) | c0 (cm) | w1 (g) | w2 (g) | Loss (g) | f (%) | J (g cm-2 s-1) | Opt | Optimized J | render check |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | 754 | 2.63E-09 | no | 4 | 0.32 | 0.18 | 0.09 | 0.01470 | 0.01314 | 0.00156 | 11 | 5.92E-07 | * | 9.84E-07 | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 3 | 753 | 3.95E-09 | no | 14 | 0.22 | 0.22 | 0.09 | 0.01052 | 0.00500 | 0.00552 | 52 | 9.01E-07 | * | (2.51E-07) | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 4 | 752 | 3.16E-09 | no | 24 | 0.22 | 0.20 | 0.09 | 0.01074 | 0.00303 | 0.00771 | 72 | 8.19E-07 | * | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 5 | 755 | 5.26E-09 | no | 28 | 0.28 | 0.16 | 0.11 | 0.01438 | 0.00249 | 0.01189 | 83 | 1.03E-06 | * | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 6 | 769 | 2.00E-07 | H2 | 8 | 0.23 | 0.21 | 0.10 | 0.01601 | 0.01254 | 0.00347 | 22 | 6.87E-07 | * | 1.00E-06 | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 7 | 770 | 2.00E-07 | H2 | 12 | 0.41 | 0.12 | 0.12 | 0.01693 | 0.01101 | 0.00592 | 35 | 7.40E-07 | * | (2.42E-07) | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 8 | 768 | 2.00E-07 | H2 | 18 | 0.29 | 0.22 | 0.13 | 0.01535 | 0.00564 | 0.00971 | 63 | 1.08E-06 | * | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 9 | 767 | 2.00E-07 | H2 | 24 | 0.34 | 0.18 | 0.08 | 0.01214 | 0.00220 | 0.00994 | 82 | 9.39E-07 | * | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 10 | 771 | 2.00E-06 | H2 | 4 | 0.37 | 0.17 | 0.15 | 0.03398 | 0.03116 | 0.00282 | 8 | 6.49E-07 | * | 1.02E-06 | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 11 | 772 | 2.00E-06 | H2 | 6 | 0.43 | 0.15 | 0.15 | 0.02072 | 0.01618 | 0.00454 | 22 | 9.80E-07 | * | (2.89E-07) | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 12 | 773 | 2.00E-06 | H2 | 20 | 0.19 | 0.15 | 0.15 | 0.01005 | 0.00355 | 0.00650 | 65 | 9.48E-07 | * | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 13 | 806 | 3.95E-06 | H2 | 2 | 0.23 | 0.12 | 0.10 | 0.00802 | 0.00605 | 0.00197 | 25 | 2.55E-06 | * | 2.84E-06 | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 14 | 800 | 3.95E-06 | H2 | 4 | 0.24 | 0.19 | 0.11 | 0.01338 | 0.00727 | 0.00611 | 46 | 3.08E-06 | * | (8.07E-07) | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 15 | 797 | 3.95E-06 | H2 | 6 | 0.36 | 0.24 | 0.16 | 0.03590 | 0.02642 | 0.00948 | 26 | 1.52E-06 | * | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 16 | 795 | 3.95E-06 | H2 | 10 | 0.47 | 0.23 | 0.16 | 0.05322 | 0.01752 | 0.03570 | 67 | 3.10E-06 | * | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 17 | 790 | 4.47E-06 | H2 | 9 | 0.34 | 0.32 | 0.10 | 0.03085 | 0.00979 | 0.02106 | 68 | 5.11E-06 | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 18 | 789 | 4.47E-06 | H2 | 15 | 0.39 | 0.23 | 0.11 | 0.02371 | 0.00000 | 0.02371 | 100 | — | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11 |
| 19 | 787 | 7.89E-06 | H2 | 10 | 0.34 | 0.28 | 0.14 | 0.03313 | 0.00837 | 0.02476 | 75 | 2.45E-06 | * | 2.51E-06 | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 20 | 788 | 7.89E-06 | H2 | 15 | 0.38 | 0.23 | 0.14 | 0.03691 | 0.00765 | 0.02926 | 79 | 2.37E-06 | * | (1.52E-07) | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 21 | 765 | 2.00E-05 | H2 | 3 | 0.28 | 0.20 | 0.13 | 0.01605 | 0.01019 | 0.00586 | 37 | 3.35E-06 | * | 4.23E-06 | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 22 | 756 | 2.00E-05 | H2 | 4 | 0.34 | 0.20 | 0.11 | 0.02023 | 0.01011 | 0.01012 | 50 | 3.75E-06 | * | (7.04E-07) | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 23 | 766 | 2.00E-05 | H2 | 5 | 0.37 | 0.20 | 0.11 | 0.01609 | 0.00615 | 0.00994 | 62 | 3.58E-06 | * | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 24 | 764 | 2.00E-05 | H2 | 7 | 0.30 | 0.20 | 0.08 | 0.01468 | 0.00131 | 0.01337 | 91 | 3.99E-06 | * | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 25 | 780 | 2.00E-05 | H2 | 9 | 0.35 | 0.20 | 0.14 | 0.02466 | 0.00965 | 0.01501 | 61 | 2.41E-06 | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 26 | 781 | 2.00E-05 | H2 | 10 | 0.43 | 0.23 | 0.11 | 0.02945 | 0.00772 | 0.02173 | 74 | 2.62E-06 | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 27 | 783 | 2.00E-05 | H2 | 15 | 0.33 | 0.21 | 0.10 | 0.01797 | 0.00000 | 0.01797 | 100 | — | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11 |
| 28 | 786 | 2.00E-05 | H2 | 10 | 0.32 | 0.19 | 0.11 | 0.01556 | 0.00000 | 0.01556 | 100 | — | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11 |
| 29 | 805 | 4.00E-05 | H2 | 0.33 | 0.30 | 0.27 | 0.08 | 0.01354 | 0.00638 | 0.00716 | 53 | 3.76E-05 | * | 4.09E-05 | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 30 | 799 | 4.00E-05 | H2 | 0.5 | 0.20 | 0.17 | 0.15 | 0.02093 | 0.00068 | 0.02025 | 97 | 5.15E-05 | * | (1.86E-05) | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 31 | 791 | 4.00E-05 | H2 | 0.75 | 0.34 | 0.23 | 0.14 | 0.03993 | 0.02587 | 0.01406 | 35 | 1.71E-05 | * | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 32 | 792 | 4.00E-05 | H2 | 4 | 0.49 | 0.25 | 0.23 | 0.02267 | 0.00000 | 0.02267 | 100 | — | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11 |
| 33 | 777 | 8.60E-05 | H2 | 0.25 | 0.36 | 0.18 | 0.14 | 0.02844 | 0.02180 | 0.00664 | 23 | 2.89E-05 | * | 2.67E-05 | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 34 | 776 | 8.60E-05 | H2 | 0.5 | 0.18 | 0.18 | 0.10 | 0.00565 | 0.00202 | 0.00363 | 64 | 2.95E-05 | * | (2.53E-06) | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12, c14 |
| 35 | 775 | 8.60E-05 | H2 | 1 | 0.21 | 0.16 | 0.10 | 0.01056 | 0.00194 | 0.00862 | 82 | 2.63E-05 | * | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 36 | 778 | 8.60E-05 | H2 | 2 | 0.31 | 0.25 | 0.10 | 0.02027 | 0.00015 | 0.02012 | 99 | 2.07E-05 | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 37 | 774 | 8.60E-05 | H2 | 4 | 0.21 | 0.15 | 0.10 | 0.00623 | 0.00000 | 0.00623 | 100 | — | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11 |
| 38 | 785 | 2.20E-04 | H2 | 0.33 | 0.43 | 0.18 | 0.10 | 0.02292 | 0.01018 | 0.01274 | 56 | 5.02E-05 | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 39 | 779 | 2.20E-04 | H2 | 0.5 | 0.38 | 0.17 | 0.15 | 0.02677 | 0.01527 | 0.01150 | 43 | 3.82E-05 | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 40 | 784 | 2.20E-04 | H2 | 0.75 | 0.36 | 0.16 | 0.14 | 0.02269 | 0.00599 | 0.01670 | 74 | 3.68E-05 | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 41 | 782 | 2.20E-04 | H2 | 1 | 0.36 | 0.25 | 0.10 | 0.02264 | 0.00190 | 0.02074 | 92 | 3.34E-05 | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 42 | 793 | 6.32E-04 | H2 | 0.33 | 0.26 | 0.17 | 0.15 | 0.02566 | 0.01272 | 0.01294 | 50 | 5.45E-05 | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 43 | 804 | 6.32E-04 | H2 | 0.33 | 0.60 | 0.28 | 0.07 | 0.02324 | 0.00588 | 0.01736 | 75 | 5.39E-05 | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 44 | 794 | 6.32E-04 | H2 | 0.75 | 0.43 | 0.26 | 0.09 | 0.03096 | 0.00000 | 0.03096 | 100 | — | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11 |
| 45 | 796 | 1.21E-03 | H2 | 0.25 | 0.42 | 0.10 | 0.08 | 0.01848 | 0.00912 | 0.00936 | 51 | 5.23E-05 | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12 |
| 46 | 798 | 1.21E-03 | H2 | 0.58 | 0.21 | 0.20 | 0.11 | 0.01801 | 0.00210 | 0.01591 | 95 | 6.43E-05 | — | — | p.4 render: c1, c2, c4, c5, c6, c7, c8, c9, c10, c11, c12; printed 95% mass loss and weights implying 88.34% disagree; both retained as printed. |

## Other numeric cells and conditions

| Printed value / claim | Locator | Confirmation |
| --- | --- | --- |
| 1700°C; W-Re thermocouple; temperature “controlled in ±2°C”; accuracy checked at Au and Pt melting points | PDF p.2, published p.1446; Experimental techniques | Rendered at 600 dpi. Temperature is the run setpoint for all Table 1 rows. |
| Microbalance error ±0.02 mg; caliper initial-dimension average error ±0.1 mm | PDF p.2, published p.1446; Experimental techniques | Rendered at 600 dpi; both ± symbols confirmed. These are method precision notes, not row uncertainties. |
| Initial sample is a forsterite parallelepiped about 1×2×3 mm and about 20 mg; all surfaces assumed exposed though sample leans against graphite capsule wall | PDF p.2, published p.1446 | Rendered at 600 dpi; the geometry assumption and limitation are preserved. |
| H2 introduced as needed and flow controlled to maintain total pressure; no per-run H2 flow or partial pressure is printed | PDF p.2, published p.1446 | Rendered at 600 dpi; pH2 remains unknown and is not substituted by total P. |
| J_exp^Fo = 1.72 P_tot^1.19 + 9.87 × 10^-7, Ptot in bar; data above 10^-4 bar excluded | PDF p.5, published p.1449, Eq. 2 | Rendered at 600 dpi; coefficient, exponent, sign, variable and fit domain confirmed. The equation has no unit suffix; the output unit is recorded separately. Stored as typed refusal because no exact mass-flux quantity exists. |
| Alpha 0.06 in vacuum; 0.2 at 10^-4 bar | PDF p.8, published p.1452, prose below Figure 7 | Rendered at 600 dpi. The source term is “condensation coefficient”; the printed definition is experimental evaporation rate divided by maximum evaporation rate (J_exp^Fo/J_vac^Fo; Fig. 6). Both numeric endpoints and this wording are stored. |

## Conditions and evidence decisions

- Table pressure P is the reported total pressure, calibrated for ionization-gauge sensitivity. In H2 runs the authors correct nominal gauge readings by a factor of two; values in Table 1 are already corrected. Gas is recorded as printed (`H2` or `no`, the latter explicitly described as vacuum). No H2 partial pressure, fO2, or numeric sample surface area is supplied. Table dimensions, duration, gas and pressure are carried to every observation in located point conditions; area remains unknown.
- The measured phase is solid forsterite (`cr`) at 1700°C. The source specifies the crystal sample; it makes no quench claim because the experiment reports evaporation of the crystal rather than a quenched melt.
- Initial/final masses are direct balance readings, but the schema has no exact paired before/after sample-mass observation quantity. They are preserved in typed-refused rows. Table mass loss in grams and f are author calculations from these weights; because no typed parent observation can migrate, they use `quoted_attributed` as reported author results. J is calculated from f and surface area; its mass/(area·time) dimension is not represented by a supported quantity, so it remains typed-refused with the printed value retained.
- Schema-gap checks: `simulator/battery/enums.py::Quantity.MASS_LOSS_AREAL_DENSITY` is area-normalized mass only; `simulator/battery/enums.py::Quantity.MASS_LOSS_RATE` and `simulator/battery/enums.py::Quantity.EVAPORATION_RATE` are mass/time; `simulator/battery/enums.py::Quantity.WALL_DEPOSIT_MASS` is deposited mass, not mass removed from the sample. None is mass/(area·time). `simulator/battery/migrate.py::QUANTITY_ALIASES` and `simulator/battery/migrate.py::TYPE_QUANTITY` provide no exact paired sample-mass or absolute-gram-loss quantity. `tools/validate_literature_extracts.py::OBSERVATION_TYPES` admits the generic `mass_loss` observation type but does not define the paired before/after mass quantity; the typed quantity remains unknown. Existing `Tsuchiyama (1998)` data use areal mass-loss density, which is not J. Descriptive non-token quantity labels prevent `rate_series` fallback to `mass_loss_rate`.
- The 8 Table 1 optimized rates are model-derived fits; their lineage is exactly the Table 1 J rows marked `Opt` in each pressure group. Each printed parent row is stored even though its flux quantity is typed-refused. Table 1 parenthetical optimizer errors are retained as printed widths; confidence level is not stated. Eq. 2 is model-derived from the 8 stored optimized rates.
- The two endpoints are stored as printed with quote attribution, source term, ratio definition, and PDF p.8 locators. Per controller ruling, they remain quantity-unknown/unavailable because `simulator/battery/migrate.py::_quantity_contradiction` rejects a source-described condensation coefficient as an evaporation-coefficient alpha. The numeric Fig. 6 maximum-rate inputs are not stored, so the reported endpoint calculations remain `quoted_attributed` under the missing-input rule. A separate controller ticket covers whether this printed J_exp^Fo/J_vac^Fo coefficient maps to alpha; this extract does not change the schema or gate.
- Figure-read candidates deferred: plotted points in Figures 2, 3 and 7; theoretical maximum-rate curve in Figure 6; Figure 5 calculated equilibrium gas compositions. No figure coordinates are extracted.

## Migration and scope

Isolated real migration: 191 observations; 0 hard issues; 146 unavailable; 146 quantity-unknown; 0 phase-unknown. The 146 quantity-unknown rows are 45 paired sample-weight records, 45 absolute-gram-loss records, 45 areal-flux records, 8 optimized areal-flux records, Eq. 2, and the 2 alpha endpoints rejected by the semantic guard. For the four specified identity axes, composition is unknown because no oxide mixture is printed, total pressure is bound from Table 1 for the three checked runs, fO2 is unknown because no numerical value is reported, and temperature is bound to 1973.15 K from printed 1700°C. The kinetic-yield profile also requires exposure, sweep gas, and sample mass; those remain identity-unknown while their printed gas, dimensions, and run-specific masses are retained in located point conditions and source values.

No store regeneration was run. Expected generated-store delta on review acceptance: 191 observation rows, 8 fit experiment records, and one source work record; no battery files committed.

## Verification

- The focused extract test passes: 45 Table 1 rows, six render-pinned numeric values across PDF pages 4 and 8, post-migration locator pins on pages 4, 5 and 8, migrated f = 0.11 for run 754, exact unavailable/quantity-unknown IDs, zero hard issues, and mutation detection for a changed render-pinned value.
- `tests/test_yaml_flow_scalar_commas.py`: 17 passed.
- `tests/test_literature_extracts.py`: `UNVERIFIED-local` as a complete suite. A 128-test non-fidelity shard passed before the repo-wide validator; selected focused migration and fidelity checks also passed. The repo-wide scan did not finish within the required 300-second local call cap.
