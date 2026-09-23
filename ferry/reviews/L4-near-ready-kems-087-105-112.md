# L4 — near-ready composition + oxygen + pressure (3-family cohort)

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/l4-near-ready-kems-087-105-112-2026-09-22`  
**Base:** `origin/work-v064-green` @ `2e9e17c3d`  
**Tip:** `b5774475083320210321535e9cc749d81885237c`  
**Worktree:** `/workspace/repos/wt/slot-11` (new; slots 01–10 occupied; L3 on slot-08)  
**Date:** 2026-09-22 ~23:05 ET (America/Toronto)

## Intent

Close printed waypoints on the next A3 3-family cohort (composition + oxygen +
pressure) for three KEMS sources. Printed evidence only; DERIVED stamp when
derived; never invent fO2 for vacuum KEMS; refuse figure-only compositions.

## PDF status

| source_id | extract filename | PDF |
|---|---|---|
| `kems-087-yamada-kato-1980` | `data/literature/extracts/kems-087-yamada-kato-1980.yaml` | **MISSING** — ASK `/workspace/ferry-inbox/ASK-pdfs-l4.md` |
| `kems-105-yamada-1983` | `data/literature/extracts/kems-105-yamada-1983.yaml` | **MISSING** — `yam1983.pdf` is a different paper (Yamaguchi/Imai/Goto Na2O–SiO2 EMF = source `yam1983`) |
| `kems-112-ichise-1989` | `data/literature/extracts/kems-112-ichise-1989.yaml` (**confirmed**) | `/workspace/batch-z/pdfs/kems-112-ichise-1989.pdf` |

## Before → After (live `_migrate_extract` / engine_point)

Probe = first `engine_point` consumer row per observation. Waypoint_ok counts
obs without that gap.

### kems-087-yamada-kato-1980

| | Before | After |
|---|---|---|
| obs | 16 | 16 (unchanged) |
| composition | 0/16 (`unsupported_print_form` string range) | **unchanged** |
| oxygen | 0/16 | **unchanged** — vacuum KEMS; no invent without PDF |
| pressure | 0/16 | **unchanged** |
| engine_point READY | **0/16** | **0/16** |

**Blockers:** PDF missing. Prior scout (slice-2) classifies composition table as
**NONE** (range + Fig. 8 analyses; one prose worked example 1.52 wt% P). Without
PDF, no printable landings. Leave GAP.

### kems-105-yamada-1983

| | Before | After |
|---|---|---|
| obs | 13 | 13 (unchanged) |
| composition | n/a (missing_bench) | **unchanged** |
| oxygen | n/a | **unchanged** |
| pressure | n/a | **unchanged** |
| engine_point READY | **0/13** | **0/13** |

**Blockers:** PDF missing. Extract has no `benches:` block; observations point
at numeric legacy experiment ids (`::1`…`::5(b)`) → `missing_bench` on all.
No invent of apparatus FK from “previous papers” without PDF. Leave GAP.

### kems-112-ichise-1989

| | Before | After |
|---|---|---|
| obs | 11 | **49** (Table 1/5 split into 40 per-heat rows) |
| composition selected | 0/11 (string / missing) | **40/49** `normalized_initial_composition` on Table 1+5 heats |
| oxygen selected | 0/11 | **0/49** — vacuum KEMS; **no fO2 invented** |
| pressure selected | 9/11 (series ~1e-4 Pa already printed) | **47/49** (copied onto per-heat charges) |
| temperature selected | 8/11 | **46/49** (1873 K activity T on charges) |
| engine_point READY | **0/11** | **0/49** (oxygen still open on every obs — honest) |

**Landed:** Table 1 Fe–Ta molar fractions (20 heats, X_Ta 0.048…0.828) and
Table 5 Fe–Nb molar fractions (20 heats, X_Nb 0.048…0.856) as experiments
`fe-ta-xta-*` / `fe-nb-xnb-*` with `mole_fraction` Composition (solute + Fe
complement, not renormalised). Per-heat `a_Fe` observation rows retargeted to
those experiments. Cell-chamber pressure ~1×10⁻⁴ Pa and activity T=1873 K
copied onto charges. Series experiment kept as label-only for Figs / Table 4/6
Gibbs–Duhem grids / geometry.

**Refused:** Table 3 EPMA phase-boundary at% Ta (not bulk charge). No fO2 /
buffer / pO2 printed for this vacuum KEMS.

**Remaining GAP:** oxygen_condition on all obs; composition still open on
series-linked rows (Table 4/6 model grids, metallography, figures, geometry);
2 obs still lack pressure (geometry / quote rows on legacy experiment id).

## Tests

- `tools/validate_literature_extracts.py` on kems-112: **OK**
- `tests/battery/test_waypoints.py` + `tests/battery/test_printed_fo2.py`: **68 passed** (`-o addopts=`)

Derived store **not** regenerated (extract-only; no battery_migrate store commit).

## Commits / push

Tip: `b5774475083320210321535e9cc749d81885237c`

```
b57744750 extracts: land Ichise 1989 Table 1/5 Fe-Ta and Fe-Nb mole fractions on per-heat experiments
```

Pushed normal to `origin/empirical/l4-near-ready-kems-087-105-112-2026-09-22`.

## READY summary

| source | READY before | READY after | Blockers |
|---|---|---|---|
| kems-087 | 0/16 | 0/16 | PDF missing; figure-only / range comps; vacuum O₂; no point P |
| kems-105 | 0/13 | 0/13 | PDF missing; missing_bench; no invent |
| kems-112 | 0/11 | 0/49 | composition+pressure closed on 40 heats; **oxygen not printed** (vacuum KEMS) |
