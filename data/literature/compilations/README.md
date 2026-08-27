# Compilations and reference databases — NOT validation sources

★ **Everything in this directory serves a different purpose from `../extracts/`, and the
distinction is load-bearing. Do not merge them.**

| | `../extracts/` | `./` (here) |
|---|---|---|
| contains | EXPERIMENTAL MEASUREMENTS | ASSESSED / COMPILED FUNCTIONS |
| examples | KEMS partial pressures, Langmuir evaporation rates, activity measurements | JANAF, IVTANTHERMO, Barin, Landolt-Börnstein, SGTE, NIST WebBook |
| role | the engine is VALIDATED AGAINST these | the engine CONSUMES these as reference data |
| in the battery | produce SCORING rows | produce **no scoring rows** — they refuse as `gibbs_table_not_runtime_observable` |

## Why this directory exists

Reference compilations were being stored in `../extracts/` alongside measurement papers, so
the battery tried to score them and they refused. `janaf-4th` shows in the coverage table as
4 observations / 0 comparable (`pointer_or_anchor_without_numeric_points`). That is a category
error, not a data problem: **a table of Gibbs energies is not an experiment and cannot validate
anything.** Validating the engine against a compilation the engine already consumes is circular.

## The rule

- A source that reports **what someone measured, under stated conditions** → `../extracts/`.
- A source that reports **assessed, smoothed or recommended thermodynamic functions** → here.
- A compilation that reprints someone else's measurements WITH conditions → the measurements
  belong in `../extracts/` credited to the ORIGINAL source, with the compilation recorded as
  the access path. Do not cite the compilation as if it were the measurement.

## What belongs here (migration pending)

Currently misfiled in `../extracts/`: `janaf-4th`, `nist-webbook`, `nasa-cea-thermo`,
`lamoreaux-hildenbrand-1984`.

★ **Do not move them without checking consumers first.** At least one is wired by path:
`simulator/chemistry/offgas_fo2.py:109` reads `data/literature/extracts/nasa-cea-thermo.yaml`
directly. Migration is a code change, not a file move.

## Status of what we actually hold

`janaf-4th.yaml` is **not JANAF**. It is a `review_status: draft` manual transcription of a few
phosphorus carriers, pointing at `janaf.nist.gov` table URLs. The full NIST-JANAF 4th edition is
~1,800 species tables and is freely available. Harvesting it properly is an open task.
