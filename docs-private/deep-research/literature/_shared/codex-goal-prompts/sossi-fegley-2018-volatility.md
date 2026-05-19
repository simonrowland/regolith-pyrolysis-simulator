Start a goal:

Objective:
Add a pytest test in `tests/chemistry/test_benchmark_sossi-fegley-2018-volatility.py` that asserts
the simulator reproduces `expected.activity_coefficient_envelopes.KO0.5` at `T_K=1573`: `gamma_min = 6.3e-5`, `gamma_max = 7.1e-4` within the fixture envelope. The test
must load `docs-private/deep-research/literature/sossi-fegley-2018-volatility/benchmark-fixture.yaml`
and call provider `builtin-overhead-gas-equilibrium` via `ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM` in the existing kernel registry.

Workspace:
/Users/simonrowland/Library/CloudStorage/Dropbox/Starship Mission Design/Regolith Processing/regolith-pyrolysis-simulator

Rules:
- Do not modify the fixture YAML.
- Do not modify simulator code outside `tests/`.
- Tolerance: inclusive fixture envelope `6.3e-5 <= gamma(KO0.5) <= 7.1e-4`. Document in `notes.md` "Reproducibility blockers" if you have to adjust.
- Per-iteration gstack review (user directive 2026-05-18): after each iteration that changes the test, invoke `Skill(skill: "review", ...)` on the diff; address P0/P1 before next iteration.
- DO NOT call MCP from inside codex.
- Emit marker vocabulary (STATUS/RESULT/USER-NEED/...) in Final response.

Pinned benchmark:
- Paper id: `sossi-fegley-2018-volatility`.
- Numeric assertion: `gamma(KO0.5)` at 1573 K falls within `[6.3e-5, 7.1e-4]`.
- Provider: `builtin-overhead-gas-equilibrium`.
- Intent: `ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM`.
- Fixture source: `expected.activity_coefficient_envelopes.KO0.5`, `{T_K_min: 1573, T_K_max: 1573, gamma_min: 6.3e-5, gamma_max: 7.1e-4, melt_class: "KFCAS w/ KAlSiO4 saturation"}`.
- Notes sensitivity: current overhead gas logic has gamma=1 proxy surfaces; a red result likely means the provider lacks a non-ideal activity coefficient surface rather than the fixture being bad.

Acceptance criteria:
1. New test file `tests/chemistry/test_benchmark_sossi-fegley-2018-volatility.py` exists.
2. `pytest tests/chemistry/test_benchmark_sossi-fegley-2018-volatility.py -v` passes.
3. Existing suite stays green (738+ passing).
4. ONE commit: `Add benchmark test for sossi-fegley-2018-volatility (chunk #23/3)`.

Test gates:
- `pytest tests/chemistry/test_benchmark_sossi-fegley-2018-volatility.py -v` -> 1 passed (or N, if you parametrize).
- `pytest tests/ -q` -> 738+N passed.

If red:
- Document discrepancy in `docs-private/deep-research/literature/sossi-fegley-2018-volatility/notes.md` under "Reproducibility blockers".
- Do NOT silently relax tolerances.

Final response (marker prefixes):
- STATUS: ...
- RESULT: test_file=<path>; assertion=gamma(KO0.5, 1573 K) in [6.3e-5, 7.1e-4]; tolerance=inclusive fixture envelope; provider=builtin-overhead-gas-equilibrium; status=<green|red>
- RESULT: commit=<hash + subject>
- Surprises
- Open findings
- COMPLETE: / BLOCKED: / USER-NEED:
