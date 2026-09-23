# VZ3 — independent verify Z/b565 P0 fixes (Burcat NO key + Zhang-2021 mbar)

**Date:** 2026-09-23 ~00:15 ET (America/Toronto)  
**Seat:** VZ3 (BACKLOG 5) — verifier, **not** the fixer  
**Null hypothesis:** claimed fixes are wrong or incomplete → OVERTURN  
**Calibration:** P0 = wrong number / wrong identity that can reach a result/score/ledger today.

| Claim | Tip(s) | Verdict |
| --- | --- | --- |
| Burcat YAML `'NO':` was `False` | `empirical/z10-lange-burcat-holzheid-2026-09-22` @ `a8769e268` | **CONFIRM** |
| Zhang-2021 `0.1` Pa → `0.0001` Pa for printed `10^-6 mbar` | `empirical/b565-pressure-note-2026-09-22` @ `6136ca6d0` **and** `empirical/b565-mbar-pa-2026-09-22` @ `29783e70c` | **CONFIRM** (both remotes; identical blobs) |

**I2 eligibility:** both confirmed → fold.

Evidence scratch: `/workspace/ferry-inbox/reviews/_vz3_audit/`  
Worktrees freed after verify: `slot-b565`, `slot-06` → `origin/work-v064-green` @ `2e9e17c3d`.

---

## 1. Burcat NO key — CONFIRM

**Remote:** `origin/empirical/z10-lange-burcat-holzheid-2026-09-22` = `a8769e268d74bf8ae5bb9f90ba86af0f9130981c`  
**Parent:** `2e9e17c3d` (same as `origin/work-v064-green` base for this wave)  
**PDF:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/audit-pdfs/burcat-third-millennium.pdf`  
**Printed Table 1** (pub p.13; text harvest `_z10_audit/burcat/p1-30.txt` / `_vz3_audit/burcat-p1-30.txt`):

| Species (printed) | ΔfH298 (kJ/mol) | unc |
| --- | ---: | ---: |
| **NO(g)** | **91.097** | **± 0.084** |
| N(g) | 472.459 | ± 0.044 |
| NO2(g) | 34.025 | ± 0.085 |

### What was wrong (reproduced on parent)

Tools load extracts with `yaml.safe_load` (PyYAML / YAML 1.1). Unquoted `NO:` is the boolean **False**.

| Layer | Parent (`a8769e268^`) | Tip (`a8769e268`) |
| --- | --- | --- |
| `extracts/burcat-third-millennium.yaml` species key | raw `NO:` → **bool `False`** (no str `'NO'`) | raw `'NO':` → **str `'NO'`** |
| Observation under that key | `burcat_2005_table1_NO_gas`, values still `species_as_printed: NO(g)`, `91.097 ± 0.084` | same values, now under correct key |
| `extracts-v2/...` species.formula for that obs | **`'False'`** (string) | **`'NO'`** |

Independent parse (this seat, `uv run` + PyYAML):

- `yaml.safe_load('NO')` → `False`
- Parent dict: `False in species` True; `'NO' in species` False; sole False-block id = `burcat_2005_table1_NO_gas`
- Tip dict: `'NO' in species` True; no bool keys; values `91.097` / `0.084` / `NO(g)` **match printed Table 1**
- Bare `N:` is **not** a YAML 1.1 bool under this loader (`yaml.safe_load('N')` → `'N'`); only `NO` needed quoting. No N/NO collision after the fix.

### Why P0 (not cosmetic)

v2 `identity.species.formula` was the string **`False`**. Any consumer that keys / displays / matches on formula (migrate, fidelity pins, compilation joins) sees a wrong species identity for nitric oxide while ΔfH stays attached to that broken identity. Quoting the key + correcting v2 formula restores `NO`.

### Scope note (not an overturn)

Commit `a8769e268` also carries Holzheid Table 3 edits (NiO T_range / s.d., FeO MgO s.d., page locators). Those are **out of VZ3** (covered under other VZ seats for Holzheid). This seat only re-checked the Burcat NO hunks; they are self-contained and correct.

**Verdict: CONFIRM** — fix is necessary and sufficient for the Burcat NO P0.

---

## 2. Zhang-2021 mbar / b-565 — CONFIRM (both remotes)

### Remotes / tips

| Branch | Tip SHA | Remote |
| --- | --- | --- |
| `empirical/b565-pressure-note-2026-09-22` | `6136ca6d0395ff32e688af943ea9eeb41e0069d4` | `origin` yes |
| `empirical/b565-mbar-pa-2026-09-22` | `29783e70cd4048f810430e033dae9eb056c7b555` | `origin` yes |

**Tree identity:** `git diff 6136ca6d0 29783e70c` = empty. Same blobs:

- `data/literature/extracts/kems-006-zhang-2021.yaml` → `daee3388b…`
- `data/literature/works/6ae16616….yaml` → `e8881dabc…`

Sibling commits off `2e9e17c3d` (message on `29783e70c` adds a grep-scope sentence only). **Either tip is fine for I2**; prefer one and drop the duplicate when folding.

### Printed evidence (value side was wrong; notes already OK)

PDF: `/workspace/ferry-inbox/acquired/kems-006-zhang-2021.pdf` (also `from-main-Z-…/pdfs/`). §2.2 Vacuum evaporation experiments:

> After loading the sample into the furnace, the furnace was pumped down to about **10-6 mbar**.  
> …held at this temperature until the pressure inside the furnace dropped to **10-6 mbar**…

(snippet: `_vz3_audit/zhang-s2.2-pressure.txt`)

### Conversion (recomputed)

| Relation | Value |
| --- | --- |
| 1 mbar | 100 Pa |
| **10⁻⁶ mbar** | **10⁻⁶ × 100 = 1×10⁻⁴ Pa = 0.0001 Pa** |
| Wrong store `0.1` Pa | = 10⁻⁶ **bar** (mbar↔bar confusion; **1000×** high) |

Notes already said `Printed 10^-6 mbar; unit conversion only` / `Printed about 10^-6 mbar…` — unit label in the note was right; stored Pa was wrong.

### Tip state (both branches)

| Field | Parent | Tip |
| --- | ---: | ---: |
| `experiments[basalt-vacuum-evaporation-series].pressure_environment.total_pressure_Pa` | `0.1` | **`0.0001`** |
| `….pumping.base_pressure_Pa` (approximate) | `0.1` | **`0.0001`** |
| Derived work `6ae16616…` same two points | `0.1` | **`0.0001`** |

Locator notes unchanged (correct).

### Peer check (grep scope)

Works/extracts with `10^-6 mbar` / `10-6 mbar` / `1e-6 mbar` notes that already store **`0.0001` Pa** (left alone, correctly):

- Wilkerson Icarus 2023 (`10.1016_j.icarus.2023.115577`) base_pressure_Pa `0.0001`
- Bischof GCA 2023 (`10.1016_j.gca.2023.08.027`) total_pressure_Pa `0.0001`
- Bischof Calphad 2022 (`10.1016_j.calphad.2022.102507`) total_pressure_Pa `0.0001`

No other `0.1` Pa beside a `10^-6 mbar` note found on these tips. Fix scope is exactly Zhang / kems-006.

**Verdict: CONFIRM** — printed mbar is unambiguous; 0.0001 Pa is the only correct conversion; both remotes land the same fix.

---

## Summary for I2

| Item | Verdict | Land tip |
| --- | --- | --- |
| Burcat NO key + v2 formula | **CONFIRM** | `a8769e268` on `empirical/z10-lange-burcat-holzheid-2026-09-22` (Burcat hunks only for this seat; Holzheid hunks need their own VZ) |
| Zhang-2021 / b-565 mbar→Pa | **CONFIRM** | Either `6136ca6d0` **or** `29783e70c` (identical trees); pick one |

**OVERTURN count: 0.**  
**False-positive claimed fixes: 0.**

Worktrees: `slot-b565` and `slot-06` reset to `origin/work-v064-green` @ `2e9e17c3d` (free). Z10 worktree `slot-y11-n11` left untouched (shared Holzheid tip).
