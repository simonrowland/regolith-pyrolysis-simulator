Start a goal:

Objective:
Add a pytest test in `tests/chemistry/test_benchmark_costa-jacobson-2015-olivine-kems.py` that asserts
the simulator reproduces `expected.vaporization_coefficients.SiO` at `T_K=1800`: `alpha = 0.030` within `alpha_absolute_uncertainty = 0.002`. The test
must load `docs-private/deep-research/literature/costa-jacobson-2015-olivine-kems/benchmark-fixture.yaml`
and call provider `builtin-evaporation-flux` via `ChemistryIntent.EVAPORATION_FLUX` in the existing kernel registry.

Workspace:
/Users/simonrowland/Library/CloudStorage/Dropbox/Starship Mission Design/Regolith Processing/regolith-pyrolysis-simulator

Rules:
- Do not modify the fixture YAML.
- Do not modify simulator code outside `tests/`.
- Tolerance: `alpha_absolute_uncertainty = 0.002`. Document in `notes.md` "Reproducibility blockers" if you have to adjust.
- Per-iteration gstack review (user directive 2026-05-18): after each iteration that changes the test, invoke `Skill(skill: "review", ...)` on the diff; address P0/P1 before next iteration.
- DO NOT call MCP from inside codex.
- Emit marker vocabulary (STATUS/RESULT/USER-NEED/...) in Final response.

Pinned benchmark:
- Paper id: `costa-jacobson-2015-olivine-kems`.
- Numeric assertion: SiO vaporization coefficient alpha at 1800 K equals `0.030`.
- Provider: `builtin-evaporation-flux`.
- Intent: `ChemistryIntent.EVAPORATION_FLUX`.
- Fixture source: `expected.vaporization_coefficients.SiO`, entry `{T_K: 1800, alpha: 0.030, alpha_absolute_uncertainty: 0.002}`.
- Notes sensitivity: Mo Knudsen cells react with olivine above melting; keep this as an alpha/flux surface check, not a canonical Ir-cell vapor-pressure check.

Acceptance criteria:
1. New test file `tests/chemistry/test_benchmark_costa-jacobson-2015-olivine-kems.py` exists.
2. `pytest tests/chemistry/test_benchmark_costa-jacobson-2015-olivine-kems.py -v` passes.
3. Existing suite stays green (738+ passing).
4. ONE commit: `Add benchmark test for costa-jacobson-2015-olivine-kems (chunk #23/1)`.

Test gates:
- `pytest tests/chemistry/test_benchmark_costa-jacobson-2015-olivine-kems.py -v` -> 1 passed (or N, if you parametrize).
- `pytest tests/ -q` -> 738+N passed.

If red:
- Document discrepancy in `docs-private/deep-research/literature/costa-jacobson-2015-olivine-kems/notes.md` under "Reproducibility blockers".
- Do NOT silently relax tolerances.

Final response (marker prefixes):
- STATUS: ...
- RESULT: test_file=<path>; assertion=SiO alpha at 1800 K = 0.030; tolerance=0.002 absolute; provider=builtin-evaporation-flux; status=<green|red>
- RESULT: commit=<hash + subject>
- Surprises
- Open findings
- COMPLETE: / BLOCKED: / USER-NEED:
