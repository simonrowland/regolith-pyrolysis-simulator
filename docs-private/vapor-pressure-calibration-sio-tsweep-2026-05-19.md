# §25-bis-SiO T-sweep grid spec

Date: 2026-05-19

Purpose: add a dedicated SiO(g) partial-pressure benchmark grid because SiO
evolution from silicate melt is the central furnace-fouling claim for the
solar-thermal pyrolysis recipe.

Scope: benchmark coverage only. No engine edits, no coefficient edits, no YAML
fixture edits.

## Loader Surface

Code surface: `tests/chemistry/corpus_fixtures.py::grid_25_sio_anchors()`.

Grid-cell id shape: `grid-25-sio:<paper-tag>@<T>K:SiO`.

The loader reads existing corpus fixture anchors through
`load_all_corpus_anchors()` and re-wraps selected SiO rows as a curated
§25-bis cohort. The test keeps the same 1-decade pass tolerance as §25 v3; it
adds status classification for literature-scale model spread and documented
engine temperature range gaps.

## Grid Shape

Total anchors: 25.

Paper breakdown:

| paper tag | corpus paper | anchors | T coverage K | melt class |
|---|---|---:|---|---|
| `cj2015` | Costa & Jacobson 2015 olivine KEMS | 4 | 1700, 1800, 1900, 2000 | forsterite Fo93Fa7 olivine |
| `sof2018-mineru` | Sossi & Fegley 2018 lunar 12022, MinerU Fig 3 redigitization | 4 | 1673, 1773, 1873, 1973 | lunar mare basalt 12022 |
| `sf2004` | Schaefer & Fegley 2004 Io tholeiite, Table 9 partials | 2 | 1700, 1900 | Io tholeiite |
| `vf2013-moon` | Visscher & Fegley 2013 Moon | 5 | 2000, 2500, 3000, 3500, 4000 | bulk silicate Moon |
| `vf2013-mars` | Visscher & Fegley 2013 Mars | 5 | 2000, 2500, 3000, 3500, 4000 | bulk silicate Mars |
| `vf2013-bse` | Visscher & Fegley 2013 BSE | 5 | 2000, 2500, 3000, 3500, 4000 | bulk silicate Earth proxy |

T coverage: 1673-4000 K.

## Anchor Provenance

| grid id | P_SiO Pa | corpus provenance |
|---|---:|---|
| `grid-25-sio:cj2015@1700K:SiO` | 4.320e-03 | CJ2015 Ir-cell Clausius-Clapeyron equation, A=7.83, B=22.43, A_sigma=0.13 |
| `grid-25-sio:cj2015@1800K:SiO` | 2.340e-02 | CJ2015 Ir-cell Clausius-Clapeyron equation |
| `grid-25-sio:cj2015@1900K:SiO` | 1.060e-01 | CJ2015 Ir-cell Clausius-Clapeyron equation |
| `grid-25-sio:cj2015@2000K:SiO` | 4.120e-01 | CJ2015 Ir-cell Clausius-Clapeyron equation |
| `grid-25-sio:sof2018-mineru@1673K:SiO` | 1.230e-02 | SoF2018 MinerU Fig 3 JPEG local SiO pixels |
| `grid-25-sio:sof2018-mineru@1773K:SiO` | 5.810e-02 | SoF2018 MinerU Fig 3 JPEG local SiO pixels |
| `grid-25-sio:sof2018-mineru@1873K:SiO` | 2.820e-01 | SoF2018 MinerU Fig 3 JPEG local SiO pixels |
| `grid-25-sio:sof2018-mineru@1973K:SiO` | 1.350e+00 | SoF2018 MinerU Fig 3 JPEG local SiO pixels |
| `grid-25-sio:sf2004@1700K:SiO` | 1.660e-04 | SF2004 Table 9 row SiO, 1700 K |
| `grid-25-sio:sf2004@1900K:SiO` | 1.310e-02 | SF2004 Table 9 row SiO, Hertz-Knudsen back-solve; Table 8/x_SiO check agrees within 0.1 decade |
| `grid-25-sio:vf2013-moon@2000K:SiO` | 8.000e-03 | VF2013 Fig 2b Moon × Fig 3a Moon |
| `grid-25-sio:vf2013-moon@2500K:SiO` | 8.000e+00 | VF2013 Fig 2b Moon × Fig 3a Moon, Na-SiO crossover region |
| `grid-25-sio:vf2013-moon@3000K:SiO` | 5.000e+03 | VF2013 Fig 2b Moon × Fig 3a Moon |
| `grid-25-sio:vf2013-moon@3500K:SiO` | 1.000e+05 | VF2013 Fig 2b Moon × Fig 3a Moon, SiO-dominated |
| `grid-25-sio:vf2013-moon@4000K:SiO` | 1.000e+06 | VF2013 Fig 2b Moon × Fig 3a Moon |
| `grid-25-sio:vf2013-mars@2000K:SiO` | 4.000e-01 | VF2013 Fig 2c Mars × Fig 3a Mars |
| `grid-25-sio:vf2013-mars@2500K:SiO` | 2.000e+01 | VF2013 Fig 2c Mars × Fig 3a Mars |
| `grid-25-sio:vf2013-mars@3000K:SiO` | 4.000e+03 | VF2013 Fig 2c Mars × Fig 3a Mars, transition region |
| `grid-25-sio:vf2013-mars@3500K:SiO` | 1.500e+05 | VF2013 Fig 2c Mars × Fig 3a Mars, SiO-dominated |
| `grid-25-sio:vf2013-mars@4000K:SiO` | 1.300e+06 | VF2013 Fig 2c Mars × Fig 3a Mars |
| `grid-25-sio:vf2013-bse@2000K:SiO` | 2.000e-01 | VF2013 Fig 2a BSE × Fig 3a BSE |
| `grid-25-sio:vf2013-bse@2500K:SiO` | 2.000e+01 | VF2013 Fig 2a BSE × Fig 3a BSE |
| `grid-25-sio:vf2013-bse@3000K:SiO` | 3.000e+03 | VF2013 Fig 2a BSE × Fig 3a BSE |
| `grid-25-sio:vf2013-bse@3500K:SiO` | 1.000e+05 | VF2013 Fig 2a BSE × Fig 3a BSE |
| `grid-25-sio:vf2013-bse@4000K:SiO` | 1.000e+06 | VF2013 Fig 2a BSE × Fig 3a BSE |

## α(SiO) Literature Compilation

| source | T band K | α(SiO) relevance | range |
|---|---:|---|---:|
| CJ2015 olivine KEMS, Fig SiO+ points | 1700-1800 | direct SiO vaporization coefficient rows in fixture | 0.003-0.036 |
| SF2004 Table 10, SiO2 liquid from Hashimoto 1990 | 2173-2373 | SiO(g) kinetic correction over SiO2(liq) | 0.038-0.048 |
| SF2004 Table 10, SiO2 solid from Nagai et al. 1973 | 1823-1983 | lower-T solid SiO2 reference, not the melt-grid default | 0.022 +/- 0.008 |

Compiled melt-relevant α(SiO) range surfaced by this grid: 0.003-0.048.

Interpretation: α is far below 1. A provider path that effectively assumes
α=1 can produce multi-factor SiO residuals. The §25-bis test treats those as
`model-spread-within-envelope` when the residual stays inside the documented
literature-scale envelope; it does not widen the 1-decade pass tolerance.

## T-band Coverage

| T band K | anchors | papers/classes | coverage note |
|---:|---:|---|---|
| 1673-1800 | 5 | SoF2018 lunar, CJ2015 olivine, SF2004 tholeiite | low end of current direct SiO corpus |
| 1801-2000 | 8 | CJ2015, SoF2018, SF2004, VF2013 | strongest cross-paper overlap |
| 2001-2400 | 0 direct P_species anchors in this fixture-backed cohort | SF2004 has Table 7 total-vapor equation, but fixture marks it total pressure, not SiO partial pressure | explicit future extraction gap |
| 2401-2700 | 3 | VF2013 Moon/Mars/BSE | dissociation/crossover band is sparse and above current VapoRock range |
| 2701-4000 | 9 | VF2013 Moon/Mars/BSE | high-T corpus extension; classified out-of-engine-T-range for current VapoRock gate |

## Cross-paper Rows

| T neighborhood | comparison | status |
|---|---|---|
| 1673-1700 K | SoF2018 lunar vs CJ2015 olivine vs SF2004 tholeiite | three distinct melt classes at the low-T SiO onset |
| 1873-1900 K | SoF2018 lunar vs CJ2015 olivine vs SF2004 tholeiite | best direct cross-paper consistency band |
| 1973-2000 K | SoF2018 lunar vs CJ2015 olivine vs VF2013 bulk bodies | bridge into VF2013 body-scale curves |
| 2500-4000 K | VF2013 Moon/Mars/BSE only | high-T extension; engine range gap, not data gap |

## Deliberate Non-use

SF2004 Table 7 gives total vapor pressure equations for model compositions.
The existing fixture records that surface as total vapor pressure, while the
§25-bis cohort is P_species(SiO) only. The loader therefore uses SF2004 Table 9
SiO partial-pressure rows at 1700 and 1900 K, and records 2001-2400 K as a
future extraction gap instead of treating total vapor pressure as SiO pressure.

SoF2018 has two fixture digitizations of the same Fig 3 curve at overlapping
temperatures. The loader uses the structured MinerU redigitization only; the
older Fig 3 rows remain corpus provenance but are not independent grid cells.

VF2013 body compositions are read from the fixture's body-specific
`bulk_silicate_compositions` rows. Trace `ZnO` is dropped at grid-load time
because the simulator species catalog does not declare ZnO and the trace oxide
is not part of the SiO benchmark variable under test.
