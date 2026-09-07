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

NASA Glenn / CEA `thermo.inp` now lives at `./nasa-glenn/` (complete 2111-record
harvest). The 1615-species extract `../extracts/nasa-cea-thermo.yaml` is left in
place this round and is superseded by that compilation.

Burcat / Ruscic Third Millennium (`BURCAT.THR.txt`) now lives at `./burcat/`
(complete 3446-record harvest: 3407 NASA-7 polynomials + 39 comment-only CAS
stanzas). See `burcat/README.md`.

## Status of what we actually hold

`../extracts/janaf-4th.yaml` is **not JANAF**. It is a `review_status: draft` manual
transcription of a few phosphorus carriers, pointing at `janaf.nist.gov` table URLs. It
remains a battery extract (b-481 / t-850 structural exclusion) and was **not** deleted
in this compilation ingest.

The NIST-JANAF 4th edition compilation now lives here:

`janaf/` — 1655 feedstock-element tables (380 HTML-era 2026-08-27 + 1275 NIST `.txt`
2026-09-06). Loader: `simulator/reference_data/janaf.py`. This compilation produces
**no** battery scoring rows. JANAF itself has no tables for 26 feedstock elements
(Ag As Au Bi Cd Ce Dy Er Eu Gd Ge In La Lu Nd Pt Sb Sc Se Sm Sn Te Th U Y Yb).

`sgte-unary/` is a complete ingest of the SGTE free pure-elements database unary50.tdb v5.0
(Thermo-Calc TDB: ELEMENT / FUNCTION / PHASE / PARAMETER G, TC, BM, BMAGN). Licence quote
and coverage are in `sgte-unary/README.md`. Compilations still produce no scoring rows.

`hemingway-haas-robinson-1982-usgs-b1544/` is a complete transcription of USGS Bulletin 1544
(Hemingway, Haas & Robinson 1982): TABLE 1 plus every per-substance T-grid. Public-domain
US government work. Loader refuses any T not on the printed grid. No battery scoring rows.

`nasa-glenn/` is the complete CEA `thermo.inp` compilation (products + reactants,
ions and condensed phases included). See `nasa-glenn/README.md`.

`burcat/` is the complete Third Millennium NASA-7 polynomial compilation
(ions, condensed phases, and comment-only CAS notes included). See
`burcat/README.md`.

| Compilation | Local status |
|---|---|
| [Robie & Hemingway 1995, USGS Bulletin 2131](robie-hemingway-1995-usgs-b2131/) | Whole-bulletin numeric-table census (1,277 records); native OCR records. Pass 2 transcribed the 111 pass-1 OCR/layout gaps from MinerU tables plus page images. Public domain; reference input only, never battery-scored. |
