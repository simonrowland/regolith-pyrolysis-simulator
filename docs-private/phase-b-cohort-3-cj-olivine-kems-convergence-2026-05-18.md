# Phase B Cohort 3 - CJ2015 Olivine KEMS Convergence

Date: 2026-05-18

Scope: extend the corpus-anchored parity framework with Costa-Jacobson 2015 Fo93Fa7 olivine KEMS anchors for `VAPOR_PRESSURE` and `EVAPORATION_FLUX`.

## Framework Extension

- Loader: `tests/chemistry/corpus_fixtures.py::load_all_cj_olivine_kems_anchors`.
- Test: `tests/chemistry/test_corpus_anchored_parity.py::test_cj_olivine_kems_cohort`.
- Anchors emitted: 66.
- Shape:
  - `VAPOR_PRESSURE`, derived C-C partial pressures: 36 anchors.
  - `EVAPORATION_FLUX`, alpha(Fe+) scaling: 15 anchors.
  - `EVAPORATION_FLUX`, alpha(SiO+) scaling: 15 anchors.
- Status counts:
  - `pass`: 22.
  - `model-spread-within-envelope`: 29.
  - `convention-mismatch`: 15.
  - `out-of-engine-range`: 0.
  - `bug-suspected`: 0.

Current local CJ2015 fixture does not carry `expected.intents_exercised`; the loader uses that field when present and backfills intent routing from the C-C and alpha blocks for this older fixture shape. Synthetic fixture auto-extension remains independent.

## Convergence Notes

- VapoRock covers the Fo93Fa7 oxide composition and emits Mg, Fe, and SiO surfaces for 1700-2000 K.
- Ir-cell Fe at 1700 K is the only direct pressure `pass` at the canonical Ir basis.
- Mg and SiO pressure residuals mostly sit inside the documented Ir/Mo/Re cell spread. The widest residuals are low-T Mo/Re SiO, where C-C cell choice dominates.
- SiO+ alpha anchors pass because `EVAPORATION_FLUX` scales linearly with the supplied alpha.
- Fe+ alpha anchors are marked `convention-mismatch`: CJ2015 reports the Fe+ ion signal; the simulator flux surface is neutral Fe(g).

## Per-Anchor Status

| anchor | intent | status | err_dec | alpha_abs_err |
|---|---|---|---:|---:|
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1700K:p(Mg) | VAPOR_PRESSURE | model-spread-within-envelope | 0.82 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1800K:p(Mg) | VAPOR_PRESSURE | model-spread-within-envelope | 0.91 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1900K:p(Mg) | VAPOR_PRESSURE | model-spread-within-envelope | 1.00 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@2000K:p(Mg) | VAPOR_PRESSURE | model-spread-within-envelope | 1.08 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1700K:p(SiO) | VAPOR_PRESSURE | model-spread-within-envelope | 1.15 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1800K:p(SiO) | VAPOR_PRESSURE | model-spread-within-envelope | 0.42 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1900K:p(SiO) | VAPOR_PRESSURE | model-spread-within-envelope | 0.25 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@2000K:p(SiO) | VAPOR_PRESSURE | model-spread-within-envelope | 0.86 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1700K:p(Fe) | VAPOR_PRESSURE | pass | 0.14 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1800K:p(Fe) | VAPOR_PRESSURE | model-spread-within-envelope | 0.54 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1900K:p(Fe) | VAPOR_PRESSURE | model-spread-within-envelope | 0.90 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@2000K:p(Fe) | VAPOR_PRESSURE | model-spread-within-envelope | 1.24 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1700K:p(Mg) | VAPOR_PRESSURE | model-spread-within-envelope | 1.01 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1800K:p(Mg) | VAPOR_PRESSURE | model-spread-within-envelope | 0.61 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1900K:p(Mg) | VAPOR_PRESSURE | model-spread-within-envelope | 0.23 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@2000K:p(Mg) | VAPOR_PRESSURE | pass | 0.11 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1700K:p(SiO) | VAPOR_PRESSURE | model-spread-within-envelope | 2.27 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1800K:p(SiO) | VAPOR_PRESSURE | model-spread-within-envelope | 1.44 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1900K:p(SiO) | VAPOR_PRESSURE | model-spread-within-envelope | 0.68 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@2000K:p(SiO) | VAPOR_PRESSURE | pass | 0.02 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1700K:p(Fe) | VAPOR_PRESSURE | model-spread-within-envelope | 1.24 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1800K:p(Fe) | VAPOR_PRESSURE | model-spread-within-envelope | 0.59 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1900K:p(Fe) | VAPOR_PRESSURE | pass | 0.01 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@2000K:p(Fe) | VAPOR_PRESSURE | model-spread-within-envelope | 0.56 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1700K:p(Mg) | VAPOR_PRESSURE | model-spread-within-envelope | 1.42 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1800K:p(Mg) | VAPOR_PRESSURE | model-spread-within-envelope | 0.97 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1900K:p(Mg) | VAPOR_PRESSURE | pass | 0.56 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@2000K:p(Mg) | VAPOR_PRESSURE | pass | 0.18 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1700K:p(SiO) | VAPOR_PRESSURE | model-spread-within-envelope | 2.60 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1800K:p(SiO) | VAPOR_PRESSURE | model-spread-within-envelope | 1.92 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1900K:p(SiO) | VAPOR_PRESSURE | model-spread-within-envelope | 1.30 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@2000K:p(SiO) | VAPOR_PRESSURE | model-spread-within-envelope | 0.73 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1700K:p(Fe) | VAPOR_PRESSURE | model-spread-within-envelope | 1.91 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1800K:p(Fe) | VAPOR_PRESSURE | model-spread-within-envelope | 1.35 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1900K:p(Fe) | VAPOR_PRESSURE | model-spread-within-envelope | 0.84 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@2000K:p(Fe) | VAPOR_PRESSURE | pass | 0.37 | - |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1700K:alpha(Fe+) | EVAPORATION_FLUX | convention-mismatch | - | 3.47e-18 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1700K:alpha(Fe+) | EVAPORATION_FLUX | convention-mismatch | - | 3.47e-18 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1700K:alpha(Fe+) | EVAPORATION_FLUX | convention-mismatch | - | 0.00e+00 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1722K:alpha(Fe+) | EVAPORATION_FLUX | convention-mismatch | - | 0.00e+00 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1722K:alpha(Fe+) | EVAPORATION_FLUX | convention-mismatch | - | 0.00e+00 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1722K:alpha(Fe+) | EVAPORATION_FLUX | convention-mismatch | - | 0.00e+00 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1753K:alpha(Fe+) | EVAPORATION_FLUX | convention-mismatch | - | 0.00e+00 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1753K:alpha(Fe+) | EVAPORATION_FLUX | convention-mismatch | - | 3.47e-18 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1753K:alpha(Fe+) | EVAPORATION_FLUX | convention-mismatch | - | 0.00e+00 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1794K:alpha(Fe+) | EVAPORATION_FLUX | convention-mismatch | - | 0.00e+00 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1794K:alpha(Fe+) | EVAPORATION_FLUX | convention-mismatch | - | 0.00e+00 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1794K:alpha(Fe+) | EVAPORATION_FLUX | convention-mismatch | - | 1.73e-18 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1800K:alpha(Fe+) | EVAPORATION_FLUX | convention-mismatch | - | 0.00e+00 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1800K:alpha(Fe+) | EVAPORATION_FLUX | convention-mismatch | - | 1.73e-18 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1800K:alpha(Fe+) | EVAPORATION_FLUX | convention-mismatch | - | 0.00e+00 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1700K:alpha(SiO+) | EVAPORATION_FLUX | pass | - | 0.00e+00 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1700K:alpha(SiO+) | EVAPORATION_FLUX | pass | - | 4.34e-19 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1700K:alpha(SiO+) | EVAPORATION_FLUX | pass | - | 8.67e-19 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1722K:alpha(SiO+) | EVAPORATION_FLUX | pass | - | 3.47e-18 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1722K:alpha(SiO+) | EVAPORATION_FLUX | pass | - | 3.47e-18 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1722K:alpha(SiO+) | EVAPORATION_FLUX | pass | - | 3.47e-18 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1753K:alpha(SiO+) | EVAPORATION_FLUX | pass | - | 0.00e+00 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1753K:alpha(SiO+) | EVAPORATION_FLUX | pass | - | 6.94e-18 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1753K:alpha(SiO+) | EVAPORATION_FLUX | pass | - | 0.00e+00 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1794K:alpha(SiO+) | EVAPORATION_FLUX | pass | - | 3.47e-18 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1794K:alpha(SiO+) | EVAPORATION_FLUX | pass | - | 6.94e-18 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1794K:alpha(SiO+) | EVAPORATION_FLUX | pass | - | 3.47e-18 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Ir@1800K:alpha(SiO+) | EVAPORATION_FLUX | pass | - | 0.00e+00 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Mo@1800K:alpha(SiO+) | EVAPORATION_FLUX | pass | - | 0.00e+00 |
| costa-jacobson-2015-olivine-kems:costa_2015_fo93fa7_olivine:Re@1800K:alpha(SiO+) | EVAPORATION_FLUX | pass | - | 0.00e+00 |

## Framework Auto-Extension Check

`test_loader_auto_extends_to_new_fixture` still validates the Phase A contract. The CJ loader itself is block-driven: a future fixture with `clausius_clapeyron_equations`, `vaporization_coefficients`, feedstock composition, and `intents_exercised` will emit the same coupled pressure/alpha surface without adding paper-specific test code.

## Open Findings

- Cohort 2 cross-impact: no overlap. SF2004 Table 8 atomic-ratio anchors stay owned by `OVERHEAD_GAS_EQUILIBRIUM`; CJ2015 olivine anchors are emitted by separate C-C/alpha blocks and do not reclassify any §25 grid anchors.
- VapoRock coverage gap: no hard coverage gap for Fo93Fa7 at 1700-2000 K. The current gap is calibration/model spread against Ir/Mo/Re KEMS cell equations, especially low-T SiO.
- Fe+ convention: treat Fe+ to Fe(g) as `convention-mismatch` for now. It is not a provider surface gap because neutral Fe flux is available, but the framework should not imply the engine predicts KEMS ion-current alpha directly.
