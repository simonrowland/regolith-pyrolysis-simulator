# F2 — swallowed failures + masked exit / non-determinism (V2 + V6)

**Repo:** regolith-pyrolysis-simulator
**Base:** `origin/review/r6-r8-fix` @ `1f8df6cbe`
**Branch:** `empirical/f2-swallowed-masked-2026-09-22`
**Tip:** `3b7aa96418c261196cc3bc60cd33d725bac1dc8e`
**Worktree:** `/workspace/repos/wt/slot-06`
**Inputs (CONFIRMED only):** `ferry-inbox/verify/V2-s12-swallowed.md`, `ferry-inbox/verify/V6-s11-s17.md`

## Scope

Implemented the **confirmed P1** items from V2 (S12) and V6 (S11+S17). Zero confirmed P0. Demoted/FP sites (dead MELTS helper, shadow init-pass, MAGEMin composition P2, sulfsat Fe3Fet P2, S11/S17 P2/P3) were **not** landed.

## Commits (one per root cause)

| root | commit | subject |
|---|---|---|
| **A** wall Knudsen | `ab647f398` | wall: refuse Knudsen regime failures instead of inventing 1.0 |
| **B** formula resolve | `1ea8acc43` | chemistry: refuse unresolvable formula mass instead of inventing zero |
| **C** account projection | `e4bc0729e` | stage0: surface ledger account projection failures on the diagnostic |
| **N1** float sinks | `7731663a0` | battery: keep Decimal grain through bench payloads and DEX residuals |
| **N2** JSON canonical | `911024867` | bench_generate: write canonical JSON with sorted keys and counters |
| **N3** wall-clock | `d5d67a235` | compilations: omit wall-clock stamps from harvest sidecars |
| **E1** shell errexit | `3b7aa9641` | scripts: fail closed on collect/refresh instead of masking exit status |

## Tests + mutation proofs

`tests/ferry_f2/` — **28 passed** (`pytest -q tests/ferry_f2/`):

- **A:** healthy viscous factor; None pressure / bogus carrier refuse; mutation reintroduces swallow → 1.0
- **B:** TE/stage0/AlphaMELTS/optimize refuse holes; mutation old omit keeps partial SiO2 map
- **C:** projection failures listed or raised; mutation old continue omits silently
- **N1:** DEX Decimal grain ≠ float log10; `_dec_payload` string grain
- **N2:** source asserts sort_keys=True + sorted counters
- **N3:** harvest tools free of datetime.now/date.today; SGTE default generated_at=None
- **E1:** collect exits nonzero on zero studies; refresh refuses missing/non-git; both scripts set -euo pipefail

## Pathspecs (explicit)

```
engines/alphamelts/provider.py
engines/alphamelts/thermoengine.py
patches/scripts/enginepatch.sh
scripts/bench_generate.py
scripts/collect_recipe_db.sh
simulator/accounting/stage0_inventory.py
simulator/battery/generators/bench.py
simulator/battery/score.py
simulator/chemistry/sgte_unary.py
simulator/optimize/evaluate.py
simulator/wall_deposition.py
tests/ferry_f2/__init__.py
tests/ferry_f2/test_f2_a_wall_knudsen_refuse.py
tests/ferry_f2/test_f2_b_alphamelts_composition_refuse.py
tests/ferry_f2/test_f2_b_formula_resolve_refuse.py
tests/ferry_f2/test_f2_c_stage0_account_projection.py
tests/ferry_f2/test_f2_e1_shell_errexit.py
tests/ferry_f2/test_f2_n1_float_sinks.py
tests/ferry_f2/test_f2_n2_json_canonical.py
tests/ferry_f2/test_f2_n3_no_wall_clock.py
tools/build_janaf_compilation_manifest.py
tools/harvest_burcat_compilation.py
tools/harvest_janaf_compilation.py
tools/harvest_nasa_glenn_compilation.py
tools/harvest_sgte_unary_compilation.py
```

## Not in this landing

V2 FP/dead/P2: MELTS `_oxide_mole_fractions` (uncalled), MAGEMin/VapoRock init-pass, MAGEMin composition shadow, sulfsat Fe3Fet placeholder.
V6 P2/P3: migrate dump_yaml sort, Composition oxide order, queue/observation dump sorts, enginepatch verify residual, epoch_grind ioreg pipefail, pack empty-guard.

READY: /workspace/ferry-inbox/reviews/F2-swallowed-masked.md
