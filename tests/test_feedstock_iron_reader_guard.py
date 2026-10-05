from __future__ import annotations

import ast
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_SCANNED_ROOTS = ("simulator", "web", "tools", "scripts")
_IRON_OXIDES = {"FeO", "Fe2O3"}


def _literal_string(node: ast.AST) -> str | None:
    try:
        value = ast.literal_eval(node)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _composition_map_expression(
    node: ast.AST,
    composition_names: set[str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in composition_names
    if isinstance(node, ast.Attribute) and node.attr == "composition_wt_pct":
        return True
    if isinstance(node, ast.Subscript):
        if _literal_string(node.slice) == "composition_wt_pct":
            return isinstance(node.value, ast.Name)
        return _composition_map_expression(node.value, composition_names)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if (
            node.func.attr == "get"
            and node.args
            and _literal_string(node.args[0]) == "composition_wt_pct"
        ):
            return isinstance(node.func.value, ast.Name)
        return _composition_map_expression(node.func.value, composition_names)
    return False


def _iron_composition_reads(source: str) -> tuple[int, ...]:
    tree = ast.parse(source)
    composition_names = {"composition_wt_pct"}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if value is None or not _composition_map_expression(
                    value, composition_names
                ):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                added = {
                    target.id
                    for target in targets
                    if isinstance(target, ast.Name)
                } - composition_names
                if added:
                    composition_names.update(added)
                    changed = True

    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            base = node.value
            key = _literal_string(node.slice)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
        ):
            base = node.func.value
            key = _literal_string(node.args[0])
        else:
            continue
        if (
            key in _IRON_OXIDES
            and _composition_map_expression(base, composition_names)
        ):
            lines.append(node.lineno)
    return tuple(sorted(set(lines)))


def _production_iron_composition_reads() -> list[str]:
    findings: list[str] = []
    for root_name in _SCANNED_ROOTS:
        root = _ROOT / root_name
        for path in root.rglob("*.py"):
            if path == _ROOT / "simulator" / "feedstock_composition.py":
                continue
            source = path.read_text(encoding="utf-8")
            findings.extend(
                f"{path.relative_to(_ROOT)}:{line}"
                for line in _iron_composition_reads(source)
            )
    return findings


def test_raw_feedstock_iron_oxide_values_are_read_only_by_the_owner() -> None:
    assert _production_iron_composition_reads() == []


def test_guard_detects_subscript_and_get_reads_in_a_mutated_consumer() -> None:
    mutated_consumer = '''
def project(composition_wt_pct):
    feo = composition_wt_pct["FeO"]
    ferric = composition_wt_pct.get("Fe2O3", 0.0)
    return feo, ferric
'''

    assert _iron_composition_reads(mutated_consumer) == (3, 4)
