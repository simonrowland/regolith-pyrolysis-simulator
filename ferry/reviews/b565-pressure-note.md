# b-565 — pressure note vs stored Pa (1000×) — 2026-09-22

**Branch:** `empirical/b565-pressure-note-2026-09-22`  
**Base:** `origin/work-v064-green` @ `2e9e17c3d`  
**Tip:** `6136ca6d0` (`6136ca6d0395ff32e688af943ea9eeb41e0069d4`)  
**Worktree:** `/workspace/repos/wt/slot-b565`  
**ASK:** `/workspace/ferry-inbox/from-empirical/ASK-pdfs-b565.md`

## What was wrong

Grep on `origin/work-v064-green` `data/literature/works/*.yaml` for notes matching `10^-6 mbar` / `10-6 mbar` / `1e-6 mbar` / `10⁻⁶ mbar` **beside stored `0.1` Pa** found **two records in one work** (not two separate work files):

| File | Field | Note (kept) | Was | Fixed |
|------|-------|-------------|-----|-------|
| `works/6ae16616….yaml` + `extracts/kems-006-zhang-2021.yaml` | `total_pressure_Pa` | `Printed 10^-6 mbar; unit conversion only` | `0.1` Pa | `0.0001` Pa |
| same | `pumping.base_pressure_Pa` | `Printed about 10^-6 mbar; unit conversion only` | `0.1` Pa | `0.0001` Pa |

**Source:** `kems-006-zhang-2021` — Zhang et al. (2021), vacuum evaporation of Na, K and Rb from a basaltic silicate melt.

**Conversion:** `10^-6 mbar = 1e-4 Pa = 0.0001 Pa`. Stored `0.1 Pa = 1e-6 bar` — mbar confused with bar (1000×).

Other works with `10^-6 mbar` notes already store `0.0001` Pa correctly (e.g. Bischof Calphad/GCA 2023, Wilkerson Icarus 2023) and were left alone.

## Printed evidence (value side wrong; notes OK)

PDF: `/workspace/ferry-inbox/acquired/kems-006-zhang-2021.pdf` (SHA256 `c25927f3…4f251e`), §2.2 Vacuum evaporation experiments:

> After loading the sample into the furnace, the furnace was pumped down to about **10-6 mbar**.  
> …held at this temperature until the pressure inside the furnace dropped to **10-6 mbar**…

Notes already quote mbar correctly. No ambiguity — not typed ambiguous.

## Change

- `data/literature/extracts/kems-006-zhang-2021.yaml` — both pressure points `0.1` → `0.0001`
- `data/literature/works/6ae16616f5e2f8ee19ac5fce66cf1f8af1c7092013d4a4b1be22932f950ec2e4.yaml` — same (derived store kept in sync)

## Validation

`uv`/venv `tools/validate_literature_extracts.py data/literature/extracts/kems-006-zhang-2021.yaml` → `OK: 1 extract file(s) valid`

## Push

`origin/empirical/b565-pressure-note-2026-09-22` @ `6136ca6d0` (clean; two YAML files only; no PDF committed).
