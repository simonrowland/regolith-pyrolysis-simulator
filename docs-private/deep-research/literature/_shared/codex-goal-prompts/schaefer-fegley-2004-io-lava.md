Start a goal:

Objective:
Add a pytest test in `tests/chemistry/test_benchmark_schaefer-fegley-2004-io-lava.py` that asserts
the simulator reproduces `expected.total_vapor_pressure_by_composition_Pa.Tholeiites.value_Pa = 7.72` at `T_K=1900` within `tolerance_decades = 0.05`. The test
must load `docs-private/deep-research/literature/schaefer-fegley-2004-io-lava/benchmark-fixture.yaml`
and call provider `vaporock` via `ChemistryIntent.VAPOR_PRESSURE` in the existing kernel registry.

Workspace:
/Users/simonrowland/Library/CloudStorage/Dropbox/Starship Mission Design/Regolith Processing/regolith-pyrolysis-simulator

Rules:
- Do not modify the fixture YAML.
- Do not modify simulator code outside `tests/`.
- Tolerance: `tolerance_decades = 0.05`. Document in `notes.md` "Reproducibility blockers" if you have to adjust.
- Per-iteration gstack review (user directive 2026-05-18): after each iteration that changes the test, invoke `Skill(skill: "review", ...)` on the diff; address P0/P1 before next iteration.
- DO NOT call MCP from inside codex.
- Emit marker vocabulary (STATUS/RESULT/USER-NEED/...) in Final response.

Pinned benchmark:
- Paper id: `schaefer-fegley-2004-io-lava`.
- Numeric assertion: tholeiite total vapor pressure at 1900 K equals `7.72 Pa`.
- Provider: `vaporock`.
- Intent: `ChemistryIntent.VAPOR_PRESSURE`.
- Fixture source: `expected.total_vapor_pressure_by_composition_Pa.Tholeiites`, `{value_Pa: 7.72, range_Pa: [5.52, 7.72], A: 4.719, B: -16761, tolerance_decades: 0.05}`.
- Notes sensitivity: Table 7 is total vapor pressure, not SiO partial pressure. Keep the test pinned to total pressure to avoid mixing it with Table 8 atomic ratios or Table 9 HK back-solves.

Acceptance criteria:
1. New test file `tests/chemistry/test_benchmark_schaefer-fegley-2004-io-lava.py` exists.
2. `pytest tests/chemistry/test_benchmark_schaefer-fegley-2004-io-lava.py -v` passes.
3. Existing suite stays green (738+ passing).
4. ONE commit: `Add benchmark test for schaefer-fegley-2004-io-lava (chunk #23/2)`.

Test gates:
- `pytest tests/chemistry/test_benchmark_schaefer-fegley-2004-io-lava.py -v` -> 1 passed (or N, if you parametrize).
- `pytest tests/ -q` -> 738+N passed.

If red:
- Document discrepancy in `docs-private/deep-research/literature/schaefer-fegley-2004-io-lava/notes.md` under "Reproducibility blockers".
- Do NOT silently relax tolerances.

Final response (marker prefixes):
- STATUS: ...
- RESULT: test_file=<path>; assertion=P_total(tholeiite, 1900 K) = 7.72 Pa; tolerance=0.05 decades; provider=vaporock; status=<green|red>
- RESULT: commit=<hash + subject>
- Surprises
- Open findings
- COMPLETE: / BLOCKED: / USER-NEED:
