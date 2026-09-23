# N11 NEW EXTRACT — NIST NBSIR + NTRS O2-compat (BACKLOG 4)

**Branch:** `empirical/n11-nist-ntrs-2026-09-22`
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`
**Tip SHA:** `e81eab4d94f70fc6b314cd3e7547f614456a8d1c`
**Worktree:** `/workspace/repos/wt/slot-12`
**Date:** 2026-09-22 (America/Toronto, EDT)
**PDFs:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/new-pdfs/` (private; not committed)

## Identify first

| Corpus dir | PDF p1 identity | Sidecar citation | Match? |
| --- | --- | --- | --- |
| `nist-nbsir-77-859` | H.M. Roder, *The Thermodynamic Properties of Slush Hydrogen and Oxygen*, NBSIR 77-859, Nov 1977, NBS Cryogenics / NASA-JSC | `The thermodynamic properties of slush hydrogen and oxygen` | Yes |
| `ntrs-20100041337` | Forsyth, Maes, Stoltzfus, Bachelier, *Promoted Ignition and Burning Tests of Stainless Steel in Flowing and Nonflowing Oxygen*, ASTM STP 1454 (2003); NASA JSC WSTF | OCR garbage `Source of Acqui siti on` | **Yes for PDF identity** — sidecar citation is truncated OCR from the acquisition stamp; extract citation rebuilt from PDF p1 |
| `ntrs-20240012816` | Jonathan Tylka, *Promoted Combustion Behavior of 316 Stainless Steel and 6061 Aluminum Alloys in Elevated Pressure Nitrox*, 10/23/24 | Tylka / ASTM G04 Prague / NTRS 20240012816 | Yes |

## STEP 0

| Paper | author/`rg` | DOI | PDF ↔ sidecar | Prior extract? |
| --- | --- | --- | --- | --- |
| `nist-nbsir-77-859` | No Roder / slush-hydrogen extract | none (NBSIR) | SHA-256 match `e209245d…bdd065` | No (SOURCE_STATUS inbox) |
| `ntrs-20100041337` | No Forsyth / Stoltzfus / Program 96-1 extract | none on this PDF (ASTM STP) | SHA-256 match `c84dad73…227793` | No |
| `ntrs-20240012816` | No Tylka / Nitrox-promoted-combustion extract | none (presentation) | SHA-256 match `f6ac9c1e…2ecf` | No |

All three STEP 0 PASS → extract.

## Validate

```
OK: 3 extract file(s) valid
OK: 3 extract file(s) valid   # --check-fidelity-match
```

0 new errors. Validator: `/workspace/repos/regolith-pyrolysis-simulator/.venv/bin/python tools/validate_literature_extracts.py`.

## Per-paper status

### 1. `nist-nbsir-77-859` — LANDED

**Extract:** `data/literature/extracts/nist-nbsir-77-859.yaml`

**Scored:** `transition_point` reference values the author adopts for the slush programs (secondary compilation — not a new lab campaign):

| Species | Quantity | Printed | Locator |
| --- | --- | --- | --- |
| H₂ | Triple point | 13.800 K, 0.0695 atm | §3.2 / pub p5 |
| H₂ | ΔHfus at TP | 117.277 J/mol (28.03 cal/mol); ~1% unc | §3.3 / pub p6 |
| O₂ | Triple point + ΔHfus | 54.359 K, 0.0014451 atm; 106.3 cal/mol (444.8 J/mol) | §6.1 / pub p13 |

**Context:** program I/O (T + quality → P, ρ, H, S, U); upper limit ~5000 psia (34 MN/m²); O₂ solid–liquid described as “best estimate.”

**Not extracted:** melting-line / solid-vapor fit coefficients; solid-density polynomials; Appendix A/B FORTRAN listings; full property grids returned by the programs.

**DERIVED:** Pa companions only (`P_Pa = P_atm × 101325`).

### 2. `ntrs-20100041337` — LANDED (context-only; amendment noted)

**Extract:** `data/literature/extracts/ntrs-20100041337.yaml`

No closed observation type fits metal promoted-ignition **burn length / threshold pressure** (oxygen-compatibility engineering, not vapour-rail). Printed tables landed in **context** (d-032 / N7 off-rail pattern). Amendment request recorded for a future `flammability_threshold` type.

**Context highlights (printed):**

| Block | Key printed numbers |
| --- | --- |
| Apparatus | WSTF flowing chamber to 4500 psia (31 MPa); ASTM G124-derived counterflow; ≥99.5% O₂; 1.0 in self-sustained definition |
| Table 1 Phase I thresholds (1.0 in min burn) | 1/8″ 316H <700 psi; 1/8″ 316L 400 psi; 1/4″ CF8M 800 psi; 1/2″ 316L **1200 psi (8.3 MPa)** |
| Table 2 Phase II ½″ 316L rods | 500 psia / 50 ft/s → complete burn **4.81 in (12.22 cm)**; higher V or P → fewer ignitions / no burn |
| Table 5 supplementary | self-sustained down to **350 psi (2.4 MPa)** at 29 ft/s; flowing can cut threshold by **~70%** vs nonflowing 1200 psi |
| Sch. 80 / XXS pipe | minimal / no sustained burn (summarised) |

**Not digitised:** Figs 4–9 photographs/plots (table/prose numbers used).

### 3. `ntrs-20240012816` — LANDED (context-only; amendment noted)

**Extract:** `data/literature/extracts/ntrs-20240012816.yaml`

ASTM G04 presentation (15 slides). Same closed-type gap → context.

| Alloy / condition | Printed result |
| --- | --- |
| 6061 Al @ 25% O₂, 69 MPa | 10 tests; no sustained combustion |
| 316 SS @ 25% O₂, 69 MPa | 10 tests; no sustained combustion |
| 6061 Al @ 50% O₂ | ~10% reaction rate ~15 MPa; NASA non-flammable at **~11 MPa** |
| 316 SS @ 50% O₂ | ~10% reaction rate ~43 MPa; NASA non-flammable at **~33 MPa** |
| Conclusions | both not flammable at 69 MPa / 25% O₂; Al @ 11 MPa / 50%; SS @ 33 MPa / 50% (bal. N₂) |

**Method context:** 25% / 50% O₂ margins vs >23% breathing air / >46% NBL Nitrox; G124 ~3 mm rods; 3POD → 10-replicate validation; Pyrofuse/Ti promoter.

**Not digitised:** slides 10 & 12 3POD scatter plots (figure-only).

## Refusals

| Paper | Reason |
| --- | --- |
| *(none refused)* | All three STEP 0 PASS; all three LANDED |
| Content parked in context (not scored) | Forsyth burn-length / threshold tables; Tylka Nitrox non-flammable pressures — no closed observation type |

## Commit

```
e81eab4d9 extracts: N11 Roder 1977 slush H2/O2 NBSIR + Forsyth 2003 / Tylka 2024 O2-compat
```

3 extract YAML files added. Private PDFs not committed. Pushed to `origin/empirical/n11-nist-ntrs-2026-09-22`.

## Optional follow-ups (not done)

- Owner ruling / schema amendment for `flammability_threshold` (or similar) so Forsyth/Tylka measured burn / non-flammable pressures can be scored
- migrate + readiness / engine_point for the new `source_id`s once I1 is green (likely low vapour-rail relevance)
- Remap SOURCE_STATUS inbox → extracted for the three corpus ids
- Fix sidecar citation for `ntrs-20100041337` (OCR garbage → Forsyth et al. 2003 STP 1454)
