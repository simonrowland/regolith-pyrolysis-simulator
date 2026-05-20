# Wolf 2022 VapoRock Anchor Convergence

Date: 2026-05-20

Chunk: `wolf-anchor`

Gate:
`PYTHONPATH=$REPO $VENV -m pytest tests/chemistry/test_vaporock_wolf2022_parity.py -q`

Full gate:
`PYTHONPATH=$REPO $VENV -m pytest tests/ -q`

## Source

Paper: Wolf et al. 2022, "VapoRock: Thermodynamics of Vaporized Silicate
Melts", arXiv 2208.09582v2 / published model paper.

Anchor source: BSE JANAF model output used for manuscript Fig. 4:
`latex_src/data/bse_logP_JANAF.csv`.

Composition source: manuscript Table 1, BSE column.

These anchors are not figure-digitized. They come from the staged paper source
CSV, whose vapor species columns are log10(partial pressure / bar). Fixture
values are converted to Pa by `10 ** log10_bar * 1e5`.

## fO2 Convention

Paper source convention:
`O2(g)` column = log10(pO2/bar) = log10(fO2/bar) for the imposed oxygen
fugacity curve.

Adapter mapping:
`O2(g)` source value -> `VapoRockBackend.equilibrate(..., fO2_log=value)`.

The source CSV also has a `dIW` column duplicating the raw log10(fO2/bar) value.
The manuscript text describes the same curve as approximately Delta IW +4.5 at
1500 K to +1 at 3000 K. The parity cohort uses the raw log10(fO2/bar), not the
Delta IW text.

## Result

Adapter reproduces paper: true.

Species comparisons: 40/40 pass at 0.3-decade tolerance.

Worst residual: 0.0078 decades, `TiO2_gas` at `bse_janaf_1500K`.

No adapter bug surfaced. The old failure mode this guards against
(fO2-regime conflation or bar/Pa scaling) would move O2 or major vapor species
by orders of magnitude; this cohort is within rounding-level drift from the
paper source CSV.

## Per-Anchor Status

| anchor | T_K | fO2_log10_bar | pass | worst species | worst err dec | status |
|---|---:|---:|---:|---|---:|---|
| `bse_janaf_1500K` | 1500 | -7.078856 | 10/10 | `TiO2_gas` | 0.0078 | pass |
| `bse_janaf_1900K` | 1900 | -4.898136 | 10/10 | `TiO2_gas` | 0.0061 | pass |
| `bse_janaf_2500K` | 2500 | -2.857966 | 10/10 | `TiO2_gas` | 0.0046 | pass |
| `bse_janaf_3000K` | 3000 | -1.568574 | 10/10 | `SiO2_gas` | 0.0050 | pass |

## Test Gates

Targeted cohort:
`5 passed, 7 warnings in 2.10s`.

Full suite:
`765 passed, 101 skipped, 8 warnings in 93.71s`.

Local setup note: this worktree initially lacked the gitignored private corpus
fixtures required by the existing §25/§25-bis tests. Restoring the staged
private corpus into the worktree as ignored test data made the full gate match
the intended corpus-backed environment.

## Open Findings

None.

No `USER-NEED` from this chunk.
