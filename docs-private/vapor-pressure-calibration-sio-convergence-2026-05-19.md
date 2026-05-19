# §25-bis-SiO convergence report

Date: 2026-05-19

Gate: `pytest tests/chemistry/test_corpus_anchored_parity.py -q`.

Implementation: `tests/chemistry/corpus_fixtures.py::grid_25_sio_anchors()`
plus `test_grid_25_sio_cohort_passes_acceptance_gate`.

## Result

N/total pass at 1-decade tolerance: 1/25.

Status counts:

| status | count |
|---|---:|
| pass | 1 |
| model-spread-within-envelope | 9 |
| body-composition-spread | 3 |
| out-of-engine-T-range | 12 |
| convention-mismatch | 0 |
| blocked-on-missing-data | 0 |

Baseline constant: `GRID_25_SIO_PASS_BASELINE = 1`.
Body-composition envelope constant:
`GRID_25_SIO_BODY_COMPOSITION_ENVELOPE_DECADES = 3.5`.

The low pass count is not treated as a grid failure. It is the desired
diagnostic: the SiO corpus exposes the α(SiO) and high-T-range limitations that
were hidden by §25/v3's two SiO anchors.

## Per-anchor Status

| anchor | status | T_K | expected_Pa | observed_Pa | err_dec |
|---|---|---:|---:|---:|---:|
| `grid-25-sio:cj2015@1700K:SiO` | model-spread-within-envelope | 1700 | 4.320e-03 | 3.074e-04 | 1.15 |
| `grid-25-sio:cj2015@1800K:SiO` | model-spread-within-envelope | 1800 | 2.340e-02 | 8.972e-03 | 0.42 |
| `grid-25-sio:cj2015@1900K:SiO` | model-spread-within-envelope | 1900 | 1.060e-01 | 1.887e-01 | 0.25 |
| `grid-25-sio:cj2015@2000K:SiO` | model-spread-within-envelope | 2000 | 4.120e-01 | 3.006e+00 | 0.86 |
| `grid-25-sio:sof2018-mineru@1673K:SiO` | model-spread-within-envelope | 1673 | 1.230e-02 | 5.083e-04 | 1.38 |
| `grid-25-sio:sof2018-mineru@1773K:SiO` | model-spread-within-envelope | 1773 | 5.810e-02 | 1.656e-02 | 0.55 |
| `grid-25-sio:sof2018-mineru@1873K:SiO` | pass | 1873 | 2.820e-01 | 3.824e-01 | 0.13 |
| `grid-25-sio:sof2018-mineru@1973K:SiO` | model-spread-within-envelope | 1973 | 1.350e+00 | 6.598e+00 | 0.69 |
| `grid-25-sio:sf2004@1700K:SiO` | model-spread-within-envelope | 1700 | 1.660e-04 | 1.403e-03 | 0.93 |
| `grid-25-sio:sf2004@1900K:SiO` | model-spread-within-envelope | 1900 | 1.310e-02 | 9.041e-01 | 1.84 |
| `grid-25-sio:vf2013-moon@2000K:SiO` | body-composition-spread | 2000 | 8.000e-03 | 9.836e+00 | 3.09 |
| `grid-25-sio:vf2013-moon@2500K:SiO` | out-of-engine-T-range | 2500 | 8.000e+00 | - | - |
| `grid-25-sio:vf2013-moon@3000K:SiO` | out-of-engine-T-range | 3000 | 5.000e+03 | - | - |
| `grid-25-sio:vf2013-moon@3500K:SiO` | out-of-engine-T-range | 3500 | 1.000e+05 | - | - |
| `grid-25-sio:vf2013-moon@4000K:SiO` | out-of-engine-T-range | 4000 | 1.000e+06 | - | - |
| `grid-25-sio:vf2013-mars@2000K:SiO` | body-composition-spread | 2000 | 4.000e-01 | 1.218e+01 | 1.48 |
| `grid-25-sio:vf2013-mars@2500K:SiO` | out-of-engine-T-range | 2500 | 2.000e+01 | - | - |
| `grid-25-sio:vf2013-mars@3000K:SiO` | out-of-engine-T-range | 3000 | 4.000e+03 | - | - |
| `grid-25-sio:vf2013-mars@3500K:SiO` | out-of-engine-T-range | 3500 | 1.500e+05 | - | - |
| `grid-25-sio:vf2013-mars@4000K:SiO` | out-of-engine-T-range | 4000 | 1.300e+06 | - | - |
| `grid-25-sio:vf2013-bse@2000K:SiO` | body-composition-spread | 2000 | 2.000e-01 | 9.244e+00 | 1.66 |
| `grid-25-sio:vf2013-bse@2500K:SiO` | out-of-engine-T-range | 2500 | 2.000e+01 | - | - |
| `grid-25-sio:vf2013-bse@3000K:SiO` | out-of-engine-T-range | 3000 | 3.000e+03 | - | - |
| `grid-25-sio:vf2013-bse@3500K:SiO` | out-of-engine-T-range | 3500 | 1.000e+05 | - | - |
| `grid-25-sio:vf2013-bse@4000K:SiO` | out-of-engine-T-range | 4000 | 1.000e+06 | - | - |

## Residual Story

The grid does not widen the 1-decade pass tolerance. Anchors outside the
1-decade gate are classified rather than forced to pass.

Main pattern:

- 9 anchors land in `model-spread-within-envelope`.
- The three VF2013 body-scale anchors at 2000 K are classified separately as
  `body-composition-spread` (1.48-3.09 decades). They are not counted as
  α-driven model spread.
- SF2004 tholeiite at 1900 K is 1.84 decades high.
- SoF2018 uses only the MinerU redigitization of Fig 3; the older overlapping
  Fig 3 rows are kept out of the grid so duplicate digitizations do not become
  independent acceptance cells.
- CJ2015 residuals are mixed-sign across 1700-2000 K, so α is not a fitted
  one-knob correction in this test. The α compilation still explains why an
  α=1-style surface should be treated as an upper-envelope diagnostic, not as
  a tuned acceptance failure.

α(SiO) compilation:

| source | range |
|---|---:|
| CJ2015 SiO+ vaporization coefficient points | 0.003-0.036 |
| SF2004 Table 10 SiO2(liq) kinetic correction | 0.038-0.048 |

Combined melt-relevant range: 0.003-0.048.

Interpretation: the engine-side α correction goal is now ready to scope, but
this chunk intentionally does not make that correction.

## Coverage Gaps

| gap | why it matters | next action |
|---|---|---|
| 1200-1673 K low-T expansion | condenser warm-up and sub-liquidus onset are not covered by direct SiO P_species anchors | add low-T anchors only from papers with numeric P_species rows |
| 2001-2400 K direct P_species gap | SF2004 Table 7 covers total vapor pressure, not SiO partial pressure in the current fixture | extract or derive an explicit SiO partial-pressure sweep before adding grid cells |
| 2400-2700 K sparse band | likely dissociation/crossover onset and above current VapoRock range | add direct P_species anchors or flag as engine-range extension work |
| 2500-4000 K engine range | VF2013 data exists, but current VapoRock gate documents 2400 K max | keep `out-of-engine-T-range` until engine authority expands |

## Acceptance Gate

The gate passes if:

1. Anchor count remains exactly 25.
2. No anchor is `blocked-on-missing-data`, `missing_species`, `skipped`, or
   `bug-suspected`.
3. Pass count remains at least `GRID_25_SIO_PASS_BASELINE = 1`.
4. Non-passing in-range anchors stay classified as
   `model-spread-within-envelope` or `body-composition-spread`, not hidden by
   tolerance changes.

This makes §25-bis-SiO a benchmark coverage extension and an engine-side α
proposal trigger, not an engine/coefficient sweep.
