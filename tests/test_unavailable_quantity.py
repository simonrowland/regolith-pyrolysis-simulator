"""G6: unavailable quantities are falsy and presence tests inspect status."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from simulator.cost_energy import (
    UnavailableQuantity,
    as_json_ready,
    is_unavailable_quantity,
    json_safe_number,
    unavailable_quantity,
    unavailable_reason_of,
)


QUANTITY_FIELDS = frozenset(
    {
        "pumping_electrical_energy_kWh",
        "pumping_electrical_cost_usd",
        "electrical_energy_kWh",
        "electrical_cost_usd",
        "total_cost_usd",
        "pumping_electrical_kWh",
        "auxiliary_electrical_kWh",
        "owner_ratify_money_projection",
    }
)
SAFE_CALL_NAMES = frozenset(
    {
        "is_unavailable_quantity",
        "unavailable_reason_of",
        "isinstance",
        "isUnavailableQuantity",
        "unavailableText",
        "hasNumber",
        "finiteNumber",
        "formatUnavailable",
        "numericValue",
        "formatLeaf",
    }
)
ROOT = Path(__file__).resolve().parent.parent


def test_json_safe_number_replaces_nan_and_inf_with_unavailable() -> None:
    import math

    finite = json_safe_number(1.25, reason="invalid-offgas-rate", units="mol/s")
    assert finite == 1.25
    nan = json_safe_number(math.nan, reason="invalid-offgas-rate", units="mol/s")
    inf = json_safe_number(math.inf, reason="invalid-offgas-rate", units="m^3/s")
    assert is_unavailable_quantity(nan)
    assert is_unavailable_quantity(inf)
    assert nan["value"] is None
    assert unavailable_reason_of(nan) == "invalid-offgas-rate"
    json.dumps({"nan": nan, "inf": inf, "finite": finite}, allow_nan=False)
    dumped = json.dumps(
        as_json_ready(nan), sort_keys=True, allow_nan=False, indent=2
    )
    assert json.loads(dumped)["status"] == "unavailable"
    # RunArtifactStore.save uses indent=2 + sort_keys; that Python encoder
    # path treats a falsy dict subclass as {}.
    assert json.dumps(nan, sort_keys=True, allow_nan=False, indent=2) == "{}"


def test_unavailable_quantity_is_falsy_and_structurally_unmistakable() -> None:
    value = unavailable_quantity(reason="missing-o2-vented-flow", units="kWh")
    assert type(value) is UnavailableQuantity
    assert isinstance(value, dict)
    assert is_unavailable_quantity(value)
    assert unavailable_reason_of(value) == "missing-o2-vented-flow"
    assert value["value"] is None
    assert value["units"] == "kWh"
    assert bool(value) is False
    assert not value
    entered = False
    if value:
        entered = True
    assert entered is False
    assert json.loads(json.dumps(value)) == {
        "status": "unavailable",
        "reason": "missing-o2-vented-flow",
        "value": None,
        "units": "kWh",
    }
    nested = json.loads(json.dumps({"leaf": value}))
    assert nested["leaf"]["status"] == "unavailable"
    loaded = json.loads(json.dumps(value))
    assert type(loaded) is dict
    assert bool(loaded) is True
    assert is_unavailable_quantity(loaded)


def test_unavailable_quantity_truthiness_mutation_fails_then_restores(monkeypatch) -> None:
    from simulator import cost_energy as cost_energy_mod

    original = cost_energy_mod.unavailable_quantity

    def mutated(*, reason: str, units: str) -> dict:
        return {
            "status": "unavailable",
            "reason": reason,
            "value": None,
            "units": units,
        }

    monkeypatch.setattr(cost_energy_mod, "unavailable_quantity", mutated)
    with pytest.raises(AssertionError):
        produced = cost_energy_mod.unavailable_quantity(reason="x", units="kWh")
        assert bool(produced) is False
        assert type(produced) is UnavailableQuantity
    monkeypatch.setattr(cost_energy_mod, "unavailable_quantity", original)
    restored = cost_energy_mod.unavailable_quantity(reason="x", units="kWh")
    assert bool(restored) is False
    assert type(restored) is UnavailableQuantity
    assert is_unavailable_quantity(restored)
    assert json.loads(json.dumps(restored))["status"] == "unavailable"


def _loaded_quantity_field(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name) and node.id in QUANTITY_FIELDS:
        return node.id
    if isinstance(node, ast.Attribute) and node.attr in QUANTITY_FIELDS:
        return node.attr
    if isinstance(node, ast.Subscript):
        sl = node.slice
        if isinstance(sl, ast.Constant) and sl.value in QUANTITY_FIELDS:
            return sl.value
    return None


def _call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _bare_truthiness_field(node: ast.AST) -> str | None:
    if _call_name(node) in SAFE_CALL_NAMES:
        return None
    if isinstance(node, ast.Compare):
        return None
    return _loaded_quantity_field(node)


def _python_truthiness_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        operands: list[ast.AST] = []
        if isinstance(node, (ast.If, ast.While, ast.IfExp)):
            operands.append(node.test)
        elif isinstance(node, ast.Assert):
            operands.append(node.test)
        elif isinstance(node, ast.BoolOp):
            operands.extend(node.values)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            operands.append(node.operand)
        else:
            continue
        for operand in operands:
            field = _bare_truthiness_field(operand)
            if field is None:
                continue
            hits.append(f"{path.relative_to(ROOT)}:{node.lineno} truthiness on {field}")
    return hits


def _js_truthiness_violations(path: Path) -> list[str]:
    hits: list[str] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        if not any(field in line for field in QUANTITY_FIELDS):
            continue
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        if any(name in stripped for name in SAFE_CALL_NAMES):
            continue
        if "status" in stripped and "unavailable" in stripped:
            continue
        for field in QUANTITY_FIELDS:
            if field not in stripped:
                continue
            patterns = (
                f"if ({field}",
                f"if (!{field}",
                f"{field} ||",
                f"|| {field}",
                f"{field} &&",
                f"&& {field}",
                f"{field} ?",
                f"!{field}",
            )
            if any(pattern in stripped for pattern in patterns):
                hits.append(
                    f"{path.relative_to(ROOT)}:{lineno} JS truthiness on {field}"
                )
            dotted = f".{field}"
            if dotted in stripped and any(
                token in stripped
                for token in (f"{dotted} ||", f"{dotted} &&", f"{dotted} ?", f"!{dotted}")
            ):
                hits.append(
                    f"{path.relative_to(ROOT)}:{lineno} JS truthiness on {field}"
                )
    return hits


def test_quantity_presence_tests_inspect_status_not_truthiness() -> None:
    """AST walk of simulator/, web/, scripts/: a refusal must not look present."""

    violations: list[str] = []
    for root_name in ("simulator", "web", "scripts"):
        root = ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix == ".py":
                violations.extend(_python_truthiness_violations(path))
            elif path.suffix == ".js":
                violations.extend(_js_truthiness_violations(path))
    assert violations == []
