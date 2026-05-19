Start a goal:

Objective:
Add a pytest test in `tests/chemistry/test_benchmark_sublimation-kinetics-2023-minerals.py` that asserts
the simulator reproduces `expected.sublimation_rate_g_per_h_per_g_regolith.Na = 1.38` at `T_C=1200` within a factor-3 provisional tolerance from the triage caveat. The test
must load `docs-private/deep-research/literature/sublimation-kinetics-2023-minerals/benchmark-fixture.yaml`
and call provider `builtin-evaporation-flux` via `ChemistryIntent.EVAPORATION_FLUX` in the existing kernel registry.

Workspace:
/Users/simonrowland/Library/CloudStorage/Dropbox/Starship Mission Design/Regolith Processing/regolith-pyrolysis-simulator

Rules:
- Do not modify the fixture YAML.
- Do not modify simulator code outside `tests/`.
- Tolerance: factor-3 envelope for Na rate, `0.46 <= rate <= 4.14 g/h/g regolith`. Document in `notes.md` "Reproducibility blockers" if you have to adjust.
- Per-iteration gstack review (user directive 2026-05-18): after each iteration that changes the test, invoke `Skill(skill: "review", ...)` on the diff; address P0/P1 before next iteration.
- DO NOT call MCP from inside codex.
- Emit marker vocabulary (STATUS/RESULT/USER-NEED/...) in Final response.

Pinned benchmark:
- Paper id: `sublimation-kinetics-2023-minerals`.
- Numeric assertion: Na sublimation rate at 1200 C equals `1.38 g/h/g regolith`.
- Provider: `builtin-evaporation-flux`.
- Intent: `ChemistryIntent.EVAPORATION_FLUX`.
- Fixture source: `expected.sublimation_rate_g_per_h_per_g_regolith.Na = 1.38`; companion fixture values are Fe `0.08` and K `1.02`.
- Notes sensitivity: fixture has no explicit tolerance field; triage says Shaw 2023 needs a coarse factor-2 to factor-3 tolerance or a Tier C sintering/surface-area gate. Keep this red if the missing sintering correction dominates.

Acceptance criteria:
1. New test file `tests/chemistry/test_benchmark_sublimation-kinetics-2023-minerals.py` exists.
2. `pytest tests/chemistry/test_benchmark_sublimation-kinetics-2023-minerals.py -v` passes.
3. Existing suite stays green (738+ passing).
4. ONE commit: `Add benchmark test for sublimation-kinetics-2023-minerals (chunk #23/4)`.

Test gates:
- `pytest tests/chemistry/test_benchmark_sublimation-kinetics-2023-minerals.py -v` -> 1 passed (or N, if you parametrize).
- `pytest tests/ -q` -> 738+N passed.

If red:
- Document discrepancy in `docs-private/deep-research/literature/sublimation-kinetics-2023-minerals/notes.md` under "Reproducibility blockers".
- Do NOT silently relax tolerances.

Final response (marker prefixes):
- STATUS: ...
- RESULT: test_file=<path>; assertion=Na sublimation rate at 1200 C = 1.38 g/h/g regolith; tolerance=factor-3 [0.46, 4.14]; provider=builtin-evaporation-flux; status=<green|red>
- RESULT: commit=<hash + subject>
- Surprises
- Open findings
- COMPLETE: / BLOCKED: / USER-NEED:
