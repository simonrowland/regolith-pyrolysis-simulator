from __future__ import annotations

import ast
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_SCANNED_ROOTS = ("simulator", "web", "tools", "scripts")
_IRON_MAP_KEYS = {
    "composition_wt_pct": {"FeO", "Fe2O3", "Fe", "Fe0", "Fe_metal"},
    "elemental_composition_wt_pct": {"Fe"},
}


def _literal_string(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _module_level_nodes(tree: ast.AST):
    stack = [tree]
    while stack:
        node = stack.pop()
        if node is not tree and isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _string_constants(tree: ast.AST) -> dict[str, str]:
    constants: dict[str, str] = {}
    assignments = [
        node
        for node in _module_level_nodes(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    for _ in range(len(assignments) + 1):
        changed = False
        for node in assignments:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                resolved = value.value
            elif isinstance(value, ast.Name):
                resolved = constants.get(value.id)
            else:
                continue
            if resolved is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in constants:
                    constants[target.id] = resolved
                    changed = True
        if not changed:
            break
    return constants


def _composition_map_expression(
    node: ast.AST,
    map_field: str,
    composition_names: set[str],
    constants: dict[str, str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in composition_names
    if isinstance(node, ast.BoolOp):
        return any(
            _composition_map_expression(
                value, map_field, composition_names, constants
            )
            for value in node.values
        )
    if isinstance(node, ast.IfExp):
        return _composition_map_expression(
            node.body, map_field, composition_names, constants
        ) or _composition_map_expression(
            node.orelse, map_field, composition_names, constants
        )
    if isinstance(node, ast.NamedExpr):
        return _composition_map_expression(
            node.value, map_field, composition_names, constants
        )
    if isinstance(node, ast.Attribute):
        return _composition_map_expression(
            node.value, map_field, composition_names, constants
        )
    if isinstance(node, ast.Subscript):
        if _literal_string(node.slice, constants) == map_field:
            return True
        return _composition_map_expression(
            node.value, map_field, composition_names, constants
        )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if (
            node.func.attr == "get"
            and node.args
            and _literal_string(node.args[0], constants) == map_field
        ):
            return True
        return _composition_map_expression(
            node.func.value, map_field, composition_names, constants
        )
    return False


def _function_parameters(tree: ast.AST) -> dict[str, list[tuple[str, ...]]]:
    functions: dict[str, list[tuple[str, ...]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        arguments = node.args
        parameters = tuple(
            argument.arg
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
            )
        )
        functions.setdefault(node.name, []).append(parameters)
    return functions


def _composition_aliases(
    tree: ast.AST,
    map_field: str,
    constants: dict[str, str],
    functions: dict[str, list[tuple[str, ...]]],
) -> set[str]:
    aliases = {map_field}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            else:
                targets = []
                value = None
            if value is not None and _composition_map_expression(
                value, map_field, aliases, constants
            ):
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in aliases:
                        aliases.add(target.id)
                        changed = True

            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
            ):
                continue
            for parameters in functions.get(node.func.id, ()):
                passed = set()
                for index, argument in enumerate(node.args):
                    if index < len(parameters) and _composition_map_expression(
                        argument, map_field, aliases, constants
                    ):
                        passed.add(parameters[index])
                for keyword in node.keywords:
                    if (
                        keyword.arg in parameters
                        and _composition_map_expression(
                            keyword.value, map_field, aliases, constants
                        )
                    ):
                        passed.add(keyword.arg)
                for parameter in passed - aliases:
                    aliases.add(parameter)
                    changed = True
    return aliases


def _iron_composition_reads(source: str) -> tuple[int, ...]:
    tree = ast.parse(source)
    constants = _string_constants(tree)
    functions = _function_parameters(tree)
    aliases = {
        map_field: _composition_aliases(
            tree, map_field, constants, functions
        )
        for map_field in _IRON_MAP_KEYS
    }

    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            base = node.value
            key = _literal_string(node.slice, constants)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
        ):
            base = node.func.value
            key = _literal_string(node.args[0], constants)
        else:
            continue
        if any(
            key in _IRON_MAP_KEYS[map_field]
            and _composition_map_expression(
                base, map_field, aliases[map_field], constants
            )
            for map_field in _IRON_MAP_KEYS
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


def test_raw_feedstock_iron_values_are_read_only_by_the_owner() -> None:
    assert _production_iron_composition_reads() == []


@pytest.mark.parametrize(
    "mutated_consumer",
    [
        "def project(fs):\n    c = fs.get(\"composition_wt_pct\", {}) or {}\n    return c.get(\"FeO\")\n",
        "FEO = \"FeO\"\ndef project(fs):\n    c = fs[\"composition_wt_pct\"]\n    return c.get(FEO)\n",
        "COMP = \"composition_wt_pct\"\ndef project(fs):\n    return fs[COMP][\"FeO\"]\n",
        "def project(feedstocks, feedstock_id):\n    return feedstocks[feedstock_id][\"composition_wt_pct\"].get(\"Fe2O3\")\n",
        "def consume(comp):\n    return comp.get(\"Fe2O3\")\n\nconsume(fs[\"composition_wt_pct\"])\n",
        "def project(fs):\n    return fs[\"elemental_composition_wt_pct\"].get(\"Fe\")\n",
    ],
)
def test_guard_detects_mutated_raw_iron_reads(mutated_consumer: str) -> None:
    assert _iron_composition_reads(mutated_consumer)


def test_guard_detects_subscript_and_get_reads_in_a_mutated_consumer() -> None:
    mutated_consumer = (
        "def project(composition_wt_pct):\n"
        "    feo = composition_wt_pct[\"FeO\"]\n"
        "    ferric = composition_wt_pct.get(\"Fe2O3\", 0.0)\n"
        "    return feo, ferric\n"
    )

    assert _iron_composition_reads(mutated_consumer) == (2, 3)
