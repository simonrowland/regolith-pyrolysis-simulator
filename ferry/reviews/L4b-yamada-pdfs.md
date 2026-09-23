# L4b — Yamada PDF fill (kems-087 / kems-105)

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/l4b-yamada-pdfs-2026-09-22`  
**Base:** L4 tip `b5774475083320210321535e9cc749d81885237c` on `empirical/l4-near-ready-kems-087-105-112-2026-09-22` (Ichise landings kept)  
**Tip:** `8a3343690ce5076a53e08f9719fa0d7ca90d7b89`  
**Worktree:** `/workspace/repos/wt/slot-11`  
**Date:** 2026-09-22 ~23:20 ET (America/Toronto)

## Intent

L4 left kems-087 and kems-105 untouched because PDFs were missing. Ferry
`/workspace/ferry-inbox/pdfs-l3-l5/` now has both. Land **only** printed
composition / oxygen / pressure. Vacuum KEMS without printed fO2 → oxygen GAP.
Figure-only refuse. kems-112 already closed on L4 — touch only if PDF proves a
printed oxygen (it does not).

## PDFs

| source_id | Path | sha256 |
|---|---|---|
| `kems-087-yamada-kato-1980` | ferry `pdfs-l3-l5/kems-087-yamada-kato-1980.pdf` → local `raw/kems-087-yamada-kato-1980/` (gitignored `*.pdf`) | `66ba65a90fb60f4b3cd8d9aebf31b036b8e0de3465caee7c897ccd39f836a49a` |
| `kems-105-yamada-1983` | ferry `pdfs-l3-l5/kems-105-yamada-1983.pdf` → local `raw/kems-105-yamada-1983/` | `2c85d228997723d880339bc4d808254677163f807de65bda16a13b0dba7caf5b` (matches extract note) |
| `kems-112-ichise-1989` | already on L4 | unchanged |

Title/authors verified against renders (Yamada & Kato 1980 Trans. ISIJ 20:244–250;
Yamada & Kato 1983 Trans. ISIJ 23:51–55). Not the EMF `yam1983` paper.

## Before → After (live `_migrate_extract` / `engine_point_requests`)

Probe = first `engine_point` consumer row per observation. Waypoint_ok =
selected with that gap family absent. Before = L4 tip `b57744750`. After = this tip.

### kems-087-yamada-kato-1980

| | Before | After |
|---|---|---|
| obs | 16 | 16 |
| composition selected | 0/16 (`unsupported_print_form` string range) | **2/16** on `fep-1p52wt-fig9` (`normalized_composition` from printed 2.71 at%) |
| oxygen selected | 0/16 | **0/16** — vacuum KEMS; no invent |
| pressure selected | 0/16 | **0/16** — chamber total pressure unstated |
| temperature selected | 16/16 | 16/16 |
| engine_point READY | **0/16** | **0/16** |

**Landed:** Body prose for Fig. 9 sample — **1.52 wt% (2.71 at%) P** — as experiment
`fep-1p52wt-fig9` with `mole_fraction` Composition `P=0.0271`, `Fe=0.9729`
(printed at% → mole fraction; Fe complement; not renormalised; accompanying wt%
kept on `printed_composition` only). Retargeted
`yamada_kato_1980_P_plus_count_rate_1p52wt` and
`yamada_kato_1980_fig9_P_plus_peak_figure_only` onto that experiment. Series
`fep-kems-1600c-series` keeps the printed **0.7–3.2 wt% P (1.3–5.7 at%)** range
label only. Explicit `pressure_environment.total_pressure_Pa: unknown` on series
and discrete charge (P vapor-pressure estimate ~1×10⁻⁹ atm at 1 wt% P is sample
vapor pressure, **not** chamber total pressure).

**Refused:**
- Discrete charges from Fig. 4 / Fig. 5 / Fig. 8 axes (figure-only).
- Inventing fO2 from Al-deoxidation / “oxygen after melting <50 ppm” (dissolved O,
  not fO2_control / buffer / pO2).
- Chamber pressure from the 1×10⁻⁹ atm P vapor-pressure estimate.

### kems-105-yamada-1983

| | Before | After |
|---|---|---|
| obs | 13 | 13 |
| composition | n/a (`missing_evidence`; legacy numeric experiment ids; **13/13 missing_bench**) | 0/13 `unsupported_print_form` (series string label; bench+experiment present) |
| oxygen | n/a | **0/13** — vacuum KEMS; no invent |
| pressure | n/a | **0/13** — chamber pressure unstated |
| temperature selected | 10/13 | **13/13** |
| engine_point READY | **0/13** | **0/13** |

**Landed:** Bench `yamada-kato-rm6k` (apparatus as Yamada–Kato 1980; BeO cells for
Fe–P–Al/Ti, Al₂O₃ for others) and experiment `fep-i-1wtP-series` with printed
composition label **~1 wt% P + various solute i**. All 13 observations now point
at that experiment (closes missing_bench / missing_experiment).
`pressure_environment.total_pressure_Pa: unknown`. Temperature 1600 °C → 1873.15 K
on the series.

**Refused:**
- Structured `initial_composition` — no numeric X_i charge table; Figs 1–4 remain
  figure-only (do not invent X_i from axes). 1 wt% P alone is not a full ternary map
  and is not printed as at%/mole fraction here.
- fO2 from dissolved O in pure Fe (20–30 ppm) or Al/Be deoxidizer amounts.
- Numeric chamber pressure (“high vacuum” for Be deoxidation only).

### kems-112-ichise-1989

| | Before | After |
|---|---|---|
| obs | 49 | **49 (unchanged)** |
| composition selected | 40/49 | **40/49** |
| oxygen selected | 0/49 | **0/49** |
| pressure selected | 47/49 | **47/49** |
| engine_point READY | **0/49** | **0/49** |

**Untouched.** PDF has no printed fO2 / buffer / pO2 (vacuum KEMS). L4 Ichise
Table 1/5 mole-fraction + cell-chamber ~1×10⁻⁴ Pa landings retained.

## Tests

- `tools/validate_literature_extracts.py` on kems-087 / 105 / 112: **OK**
- `tests/battery/test_waypoints.py` + `tests/battery/test_printed_fo2.py`: **68 passed** (`-o addopts=`)

Derived store **not** regenerated (extract-only).

## Commits / push

```
8a3343690 extracts: L4b Yamada PDF fill — series pressure GAP note and L4b provenance
c49479211 extracts: close L4 printed composition on kems-087 and kems-105
b57744750 extracts: land Ichise 1989 Table 1/5 Fe-Ta and Fe-Nb mole fractions on per-heat experiments
```

Pushed to `origin/empirical/l4b-yamada-pdfs-2026-09-22`.

## READY summary

| source | READY before | READY after | Notes |
|---|---:|---:|---|
| kems-087 | 0/16 | **0/16** | composition closed on 2 Fig. 9-linked obs; O₂ + P still GAP |
| kems-105 | 0/13 | **0/13** | missing_bench closed; composition still string/unsupported; O₂ + P GAP |
| kems-112 | 0/49 | **0/49** | unchanged; oxygen not printed |

None fully READY for `engine_point` (expected: vacuum KEMS without printed fO2).
Lane is the honest READY path after the ferry PDFs arrived.
