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
