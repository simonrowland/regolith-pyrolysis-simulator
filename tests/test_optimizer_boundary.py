"""R-F7 data-layer boundary guard.

The recipe optimizer must NOT depend on the legacy single-user UI store
(`simulator.persistence`) or the legacy `simulator.mass_balance` module. The
optimizer's scoring surface is the R-F3 `simulator.trace.PhysicsTrace` +
`simulator.accounting` queries; its run cache is the (Phase-O)
`simulator.results_store`. This guard fails if any `simulator/optimize/*.py`
imports a forbidden legacy module, so the boundary is enforced for Phase-O code,
not merely documented.
"""
from __future__ import annotations

import ast
from pathlib import Path

OPTIMIZE_DIR = Path(__file__).resolve().parents[1] / "simulator" / "optimize"
FORBIDDEN_MODULES = {"simulator.mass_balance", "simulator.persistence"}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            # Also catch `from simulator import mass_balance`: ast records
            # module="simulator" + name="mass_balance"; the forbidden module is
            # module + "." + name.
            for alias in node.names:
                modules.add(f"{node.module}.{alias.name}")
    return modules


def test_optimizer_does_not_import_legacy_mass_balance_or_persistence() -> None:
    offenders: dict[str, list[str]] = {}
    for py in sorted(OPTIMIZE_DIR.rglob("*.py")):
        bad = sorted(_imported_modules(py) & FORBIDDEN_MODULES)
        if bad:
            offenders[py.name] = bad

    assert not offenders, (
        f"optimize/ modules import forbidden legacy modules: {offenders}. "
        "Use the R-F3 PhysicsTrace / accounting.queries scoring surface and the "
        "(Phase-O) results_store.py, not legacy mass_balance/persistence."
    )


def test_boundary_guard_detects_from_package_import_form(tmp_path) -> None:
    # The guard must catch BOTH `import simulator.mass_balance` AND
    # `from simulator import mass_balance` (ast: module="simulator",
    # name="mass_balance"). Exit-review P3 (2026-05-30).
    snippet = tmp_path / "snip.py"
    snippet.write_text(
        "from simulator import mass_balance\nimport simulator.persistence\n"
    )
    detected = _imported_modules(snippet)
    assert "simulator.mass_balance" in detected
    assert "simulator.persistence" in detected
