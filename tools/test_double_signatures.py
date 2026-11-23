#!/usr/bin/env python3
"""Find test doubles whose callable signature is narrower than production's."""

from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DoubleSite:
    path: Path
    line: int
    call: ast.Call
    target: ast.AST
    replacement: ast.AST


@dataclass(frozen=True)
class Census:
    scanned: int
    resolved: int
    skipped: tuple[str, ...]
    violations: tuple[str, ...]


def _callable_definition(
    node: ast.AST,
    call: ast.Call,
    parents: dict[ast.AST, ast.AST],
    tree: ast.Module,
    seen: frozenset[str] = frozenset(),
):
    if isinstance(node, ast.Lambda):
        return node
    if isinstance(node, ast.Name):
        if node.id in seen:
            return None
        scope: ast.AST | None = call
        while scope is not None:
            if isinstance(scope, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                declarations = []
                body = getattr(scope, "body", [])
                for item in ast.walk(ast.Module(body=body, type_ignores=[])):
                    if item is scope or isinstance(item, (ast.Module, ast.Lambda)):
                        continue
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        parent = parents.get(item)
                        if parent is not scope and not _within_direct_scope(item, scope, parents):
                            continue
                        if item.name == node.id:
                            declarations.append(item)
                    elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                        parent = parents.get(item)
                        if parent is not scope and not _within_direct_scope(item, scope, parents):
                            continue
                        targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                        if any(isinstance(target, ast.Name) and target.id == node.id for target in targets):
                            value = item.value
                            if isinstance(value, (ast.Lambda, ast.Name)):
                                declarations.append(value)
                if declarations:
                    declaration = max(declarations, key=lambda item: item.lineno)
                    if isinstance(declaration, (ast.Lambda, ast.Name)):
                        return _callable_definition(
                            declaration,
                            call,
                            parents,
                            tree,
                            seen | {node.id},
                        )
                    return declaration
            scope = parents.get(scope)
    return None


def _within_direct_scope(node: ast.AST, scope: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current = parents.get(node)
    while current is not None and current is not scope:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return False
        current = parents.get(current)
    return current is scope


def _aliases(tree: ast.Module) -> tuple[dict[str, str], dict[str, str]]:
    modules: dict[str, str] = {}
    objects: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                objects[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return modules, objects


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else None
    return None


def _sites(path: Path, tree: ast.Module) -> list[DoubleSite]:
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    modules, objects = _aliases(tree)
    patch_names = {"patch"}
    patch_object_names = {"patch.object"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "unittest.mock":
            for alias in node.names:
                if alias.name == "patch":
                    name = alias.asname or alias.name
                    patch_names.add(name)
                    patch_object_names.add(f"{name}.object")
        elif isinstance(node, ast.ImportFrom) and node.module == "unittest":
            for alias in node.names:
                if alias.name == "mock":
                    name = alias.asname or alias.name
                    patch_names.add(f"{name}.patch")
                    patch_object_names.add(f"{name}.patch.object")
        elif isinstance(node, ast.Import) and any(a.name == "unittest.mock" for a in node.names):
            for alias in node.names:
                if alias.name == "unittest.mock":
                    root = alias.asname or "unittest"
                    suffix = "" if alias.asname else ".mock"
                    patch_object_names.add(f"{root}{suffix}.patch.object")
                    patch_names.add(f"{root}{suffix}.patch")

    found: list[DoubleSite] = []
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        dotted = _dotted(call.func)
        target = replacement = None
        if dotted and dotted.endswith("monkeypatch.setattr"):
            if len(call.args) >= 3 and isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str):
                target, replacement = ast.Tuple(elts=[call.args[0], call.args[1]], ctx=ast.Load()), call.args[2]
            elif len(call.args) >= 2 and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                target, replacement = call.args[0], call.args[1]
        elif dotted in patch_object_names:
            if len(call.args) >= 2:
                target = ast.Tuple(elts=[call.args[0], call.args[1]], ctx=ast.Load())
                replacement = call.args[2] if len(call.args) >= 3 else None
        elif dotted in patch_names:
            if call.args:
                target = call.args[0]
                replacement = call.args[1] if len(call.args) >= 2 else None
        if replacement is None:
            replacement = next((kw.value for kw in call.keywords if kw.arg in {"new", "side_effect"}), None)
        if target is not None and replacement is not None and _callable_definition(replacement, call, parents, tree):
            found.append(DoubleSite(path, call.lineno, call, target, replacement))
    return found


def _resolve_alias(name: str, modules: dict[str, str], objects: dict[str, str]) -> str:
    first, dot, rest = name.partition(".")
    if first in objects:
        return objects[first] + (dot + rest if dot else "")
    if first in modules:
        return modules[first] + (dot + rest if dot else "")
    return name


def _production_signature(target: ast.AST, modules: dict[str, str], objects: dict[str, str]):
    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        dotted = target.value
    elif isinstance(target, ast.Tuple) and len(target.elts) == 2 and isinstance(target.elts[1], ast.Constant):
        base = _dotted(target.elts[0])
        if not base:
            return None
        dotted = f"{_resolve_alias(base, modules, objects)}.{target.elts[1].value}"
    else:
        base = _dotted(target)
        if not base:
            return None
        dotted = _resolve_alias(base, modules, objects)

    parts = dotted.split(".")
    for split in range(len(parts) - 1, 0, -1):
        try:
            value = importlib.import_module(".".join(parts[:split]))
        except Exception:
            continue
        try:
            for attribute in parts[split:]:
                value = getattr(value, attribute)
        except AttributeError:
            return None
        if callable(value):
            try:
                return inspect.signature(value)
            except (TypeError, ValueError):
                return None
        return None
    return None


def _replacement_signature(node: ast.AST) -> inspect.Signature | None:
    args = node.args if isinstance(node, ast.Lambda) else node.args if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
    if isinstance(node, ast.ClassDef):
        init = next((item for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__"), None)
        if init is None:
            return inspect.Signature()
        args = init.args
        remove_first = True
    else:
        remove_first = False
    if args is None:
        return None

    positional = list(args.posonlyargs) + list(args.args)
    defaults_start = len(positional) - len(args.defaults)
    params: list[inspect.Parameter] = []
    for index, arg in enumerate(positional):
        if remove_first and index == 0:
            continue
        kind = inspect.Parameter.POSITIONAL_ONLY if index < len(args.posonlyargs) else inspect.Parameter.POSITIONAL_OR_KEYWORD
        default_index = index - defaults_start
        default = inspect.Parameter.empty if default_index < 0 else None
        params.append(inspect.Parameter(arg.arg, kind, default=default))
    if args.vararg:
        params.append(inspect.Parameter(args.vararg.arg, inspect.Parameter.VAR_POSITIONAL))
    for arg, default_node in zip(args.kwonlyargs, args.kw_defaults):
        default = inspect.Parameter.empty if default_node is None else None
        params.append(inspect.Parameter(arg.arg, inspect.Parameter.KEYWORD_ONLY, default=default))
    if args.kwarg:
        params.append(inspect.Parameter(args.kwarg.arg, inspect.Parameter.VAR_KEYWORD))
    try:
        return inspect.Signature(params)
    except ValueError:
        return None


def _compatible(production: inspect.Signature, double: inspect.Signature) -> bool:
    production_params = list(production.parameters.values())
    double_params = list(double.parameters.values())
    accepts_var_pos = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in double_params)
    accepts_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in double_params)
    if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in production_params) and not accepts_var_pos:
        return False
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in production_params) and not accepts_var_kw:
        return False
    positional = [
        object() for p in production_params
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    keyword_only = {
        p.name: object() for p in production_params
        if p.kind is inspect.Parameter.KEYWORD_ONLY
    }
    try:
        double.bind(*positional, **keyword_only)
    except TypeError:
        return False
    return True


def scan_tests(root: Path) -> Census:
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    scanned = resolved = 0
    skipped: list[str] = []
    violations: list[str] = []
    for path in sorted(root.rglob("test_*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError) as exc:
            skipped.append(f"{path}:0: cannot parse: {exc}")
            continue
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        modules, objects = _aliases(tree)
        for site in _sites(path, tree):
            scanned += 1
            production = _production_signature(site.target, modules, objects)
            replacement_node = _callable_definition(site.replacement, site.call, parents, tree)
            double = _replacement_signature(replacement_node) if replacement_node else None
            if production is None or double is None:
                skipped.append(f"{path}:{site.line}: SKIPPED-UNRESOLVABLE")
                continue
            resolved += 1
            if not _compatible(production, double):
                violations.append(f"{path}:{site.line}: production {production}; double {double}")
    return Census(scanned, resolved, tuple(skipped), tuple(violations))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tests", type=Path, help="tests directory to scan")
    args = parser.parse_args(argv)
    census = scan_tests(args.tests)
    print(f"DOUBLES-SCANNED: {census.scanned}")
    print(f"RESOLVED: {census.resolved}")
    print(f"VIOLATIONS: {len(census.violations)}")
    print(f"SKIPPED-UNRESOLVABLE: {len(census.skipped)}")
    for item in census.violations:
        print(f"VIOLATION: {item}")
    for item in census.skipped:
        print(item)
    return 1 if census.violations else 0


if __name__ == "__main__":
    sys.exit(main())
