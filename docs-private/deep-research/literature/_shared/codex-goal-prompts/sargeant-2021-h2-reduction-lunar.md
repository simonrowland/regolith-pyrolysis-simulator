Start a goal:

Objective:
Add a pytest test in `tests/chemistry/test_benchmark_sargeant-2021-h2-reduction-lunar.py` that asserts
the simulator reproduces `expected.measured_O2_yield_kg_per_kg_initial.apollo_10084 = 0.0094` at 1000 C, 4 hr, 420 mbar H2 within strict fixture tolerance `0.0 kg/kg` unless a source-backed tolerance is documented first. The test
must load `docs-private/deep-research/literature/_adjacent-isru/sargeant-2021-h2-reduction-lunar/benchmark-fixture.yaml`
and call provider `builtin-stage0-pretreatment` via `ChemistryIntent.STAGE0_PRETREATMENT` in the existing kernel registry.

Workspace:
/Users/simonrowland/Library/CloudStorage/Dropbox/Starship Mission Design/Regolith Processing/regolith-pyrolysis-simulator

Rules:
- Do not modify the fixture YAML.
- Do not modify simulator code outside `tests/`.
- Tolerance: `0.0 kg/kg` until the fixture gains a source-backed measurement tolerance. Document in `notes.md` "Reproducibility blockers" if you have to adjust.
- Per-iteration gstack review (user directive 2026-05-18): after each iteration that changes the test, invoke `Skill(skill: "review", ...)` on the diff; address P0/P1 before next iteration.
- DO NOT call MCP from inside codex.
- Emit marker vocabulary (STATUS/RESULT/USER-NEED/...) in Final response.

Pinned benchmark:
- Paper id: `sargeant-2021-h2-reduction-lunar`.
- Numeric assertion: Apollo 10084 measured O2 yield equals `0.0094 kg/kg initial` (`0.94 wt%`).
- Provider: `builtin-stage0-pretreatment`.
- Intent: `ChemistryIntent.STAGE0_PRETREATMENT`.
- Fixture source: `expected.measured_O2_yield_kg_per_kg_initial.apollo_10084 = 0.0094`.
- Notes sensitivity: fixture lives under `_adjacent-isru/`, not the main literature directory, and is marked a stub. Stage 0 cleanup may not be an H2-reduction yield surface; if no provider output maps to O2 yield, document that as the blocker rather than bending the test.

Acceptance criteria:
1. New test file `tests/chemistry/test_benchmark_sargeant-2021-h2-reduction-lunar.py` exists.
2. `pytest tests/chemistry/test_benchmark_sargeant-2021-h2-reduction-lunar.py -v` passes.
3. Existing suite stays green (738+ passing).
4. ONE commit: `Add benchmark test for sargeant-2021-h2-reduction-lunar (chunk #23/5)`.

Test gates:
- `pytest tests/chemistry/test_benchmark_sargeant-2021-h2-reduction-lunar.py -v` -> 1 passed (or N, if you parametrize).
- `pytest tests/ -q` -> 738+N passed.

If red:
- Document discrepancy in `docs-private/deep-research/literature/_adjacent-isru/sargeant-2021-h2-reduction-lunar/notes.md` under "Reproducibility blockers".
- Do NOT silently relax tolerances.

Final response (marker prefixes):
- STATUS: ...
- RESULT: test_file=<path>; assertion=O2 yield Apollo 10084 = 0.0094 kg/kg initial; tolerance=0.0 kg/kg pending source-backed fixture tolerance; provider=builtin-stage0-pretreatment; status=<green|red>
- RESULT: commit=<hash + subject>
- Surprises
- Open findings
- COMPLETE: / BLOCKED: / USER-NEED:
