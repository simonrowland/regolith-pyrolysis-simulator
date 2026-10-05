# STATUS: t-1139 Build A FOCUSED CONFIRM DELIVERED (regolith-empirical → regolith-physics)
Date: 2026-10-05 ~18:40 ET. Full review: `from-empirical/REVIEW-t1139-build-a-confirm-2026-10-05.md`.
Sha reviewed: **0c80eef0c6bd561a6fbd30156c3168e982a31408** (a11d8f6ff..0c80eef0c, 11 commits).

**Verdict: REVISE at 0c80eef0c.** New + residual: **0×P0, 3×P1, 1×P2, 11×P3** (new 0/2/0/5, residual 0/1/1/6).
- Items: P1-2 **PARTIAL** (NASA fixed: SiO residual −1.7 kJ/mol, was +241.8. Burcat still takes the gas reference from crystal Tm: Fe(g) +182 kJ/mol at 1600 K). P1-3 (32070d527 + 77d9d978f), P1-4, P2-1, P2-3, P2-4, P2-5, P2-6, P3-5 and pin-first 304b080c7 are all **FIXED**.
- Live pins: identical. All pressures and legacy_view are bit-identical to a11d8f6ff, and the G1 rows are unchanged.
- New P1 #1: b5cee4ee4 renamed `_interpolate_formation_gibbs`, which breaks the live `binary_pot_battery._cell_oxide_thermodynamics` (W/Mo reactive cell) with an ImportError.
- New P1 #2: the t622 `candidate_compiler_sha256` is stale after the catalog.py edits, so the test is red on every platform.
- d-062: Q1 YES (small residual copies), Q2 YES (stoich still in the legacy projection), Q3 NO, Q4 PARTIAL (no pin on the second consumer of the interpolator), Q5 NO.
- Rebased tip 3c56d0351: patch-identical except janaf.py, whose conflict resolution keeps the old name. 523d0af3f fixes both new P1s plus the empty-condensed Burcat case. The Burcat crystal-ends-at-Tm residual is still open there. Not a verdict of record on 3c56d0351.
- ASK regolith-main: run G2 on the landing sha, including `test_openimcc_battery_engine.py -k reactive_cell` with the openimcc pin installed.
