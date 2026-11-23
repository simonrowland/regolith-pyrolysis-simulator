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
    replacement: ast.AST | None


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


def _assigned_constructor(
    name: str,
    at: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.AST | None:
    scope: ast.AST | None = at
    while scope is not None:
        if isinstance(scope, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)):
            declarations = []
            for item in ast.walk(ast.Module(body=getattr(scope, "body", []), type_ignores=[])):
                if not isinstance(item, (ast.Assign, ast.AnnAssign)):
                    continue
                if not _within_direct_scope(item, scope, parents):
                    continue
                targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                    value = item.value
                    if isinstance(value, ast.Call):
                        declarations.append(value.func)
            if declarations:
                return max(declarations, key=lambda item: item.lineno)
        scope = parents.get(scope)
    return None


def _local_method(
    node: ast.AST,
    at: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> tuple[ast.AST | None, bool]:
    """Resolve a method on a locally constructed object; bool means bound."""
    if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
        return None, False
    constructor = _assigned_constructor(node.value.id, at, parents)
    if constructor is None:
        return None, False
    cls = _callable_definition(constructor, at, parents, ast.Module(body=[], type_ignores=[]))
    if isinstance(cls, ast.ClassDef):
        method = next((item for item in cls.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == node.attr), None)
        return method, _method_has_receiver(method)
    return None, False


def _method_has_receiver(method: ast.AST | None) -> bool:
    if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for decorator in method.decorator_list:
        name = _dotted(decorator.func) if isinstance(decorator, ast.Call) else _dotted(decorator)
        if name in {"classmethod", "staticmethod"}:
            return False
    return True


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
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if (
                isinstance(value, ast.Call)
                and _dotted(value.func) in {"pytest.importorskip", "importorskip"}
                and value.args
                and isinstance(value.args[0], ast.Constant)
                and isinstance(value.args[0].value, str)
            ):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        modules[target.id] = value.args[0].value
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
        if target is not None and isinstance(replacement, (ast.Lambda, ast.Name, ast.Attribute, ast.Call)):
            found.append(DoubleSite(path, call.lineno, call, target, replacement))
    return found


def _resolve_alias(name: str, modules: dict[str, str], objects: dict[str, str]) -> str:
    first, dot, rest = name.partition(".")
    if first in objects:
        return objects[first] + (dot + rest if dot else "")
    if first in modules:
        return modules[first] + (dot + rest if dot else "")
    return name


def _production_signature(
    target: ast.AST,
    modules: dict[str, str],
    objects: dict[str, str],
    call: ast.Call,
    parents: dict[ast.AST, ast.AST],
):
    bound_target = False
    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        dotted = target.value
    elif isinstance(target, ast.Tuple) and len(target.elts) == 2 and isinstance(target.elts[1], ast.Constant):
        base = _dotted(target.elts[0])
        if isinstance(target.elts[0], ast.Name):
            constructor = _assigned_constructor(target.elts[0].id, call, parents)
            if constructor is not None and _is_class_constructor(
                constructor, call, parents, modules, objects
            ):
                base = _dotted(constructor)
                bound_target = True
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
                signature = inspect.signature(value)
                if bound_target and inspect.isfunction(value):
                    parameters = list(signature.parameters.values())
                    if parameters:
                        signature = signature.replace(parameters=parameters[1:])
                return signature
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
    positional_only = [p for p in production_params if p.kind is inspect.Parameter.POSITIONAL_ONLY]
    positional_or_keyword = [p for p in production_params if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
    keyword_only_params = [p for p in production_params if p.kind is inspect.Parameter.KEYWORD_ONLY]
    optional = {p.name for p in production_params if p.default is not inspect.Parameter.empty and p.kind is not inspect.Parameter.VAR_POSITIONAL and p.kind is not inspect.Parameter.VAR_KEYWORD}
    shapes: list[tuple[list[object], dict[str, object]]] = []

    def shape(omitted: frozenset[str], positional_count: int) -> None:
        args = [object() for param in positional_only if param.name not in omitted]
        kwargs = {}
        for index, param in enumerate(positional_or_keyword):
            if param.name in omitted:
                continue
            if index < positional_count and not any(p.name in omitted for p in positional_or_keyword[:index]):
                args.append(object())
            else:
                kwargs[param.name] = object()
        for param in keyword_only_params:
            if param.name not in omitted:
                kwargs[param.name] = object()
        shapes.append((args, kwargs))

    # Probe the minimal valid call, every individual optional omission, and
    # every positional/keyword split for positional-or-keyword parameters.
    shape(frozenset(optional), 0)
    for name in optional:
        shape(frozenset({name}), 0)
    for positional_count in range(len(positional_or_keyword) + 1):
        shape(frozenset(), positional_count)
    if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in production_params) and any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in production_params
    ):
        double_positional = [
            p for p in double_params
            if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        ]
        for index, param in enumerate(double_positional):
            shapes.append(([object() for _ in range(index + 1)], {param.name: object()}))
    # Required positional-only parameters cannot be omitted; make sure the
    # constructed minimal call still supplies them positionally.
    for args, kwargs in shapes:
        try:
            production.bind(*args, **kwargs)
            double.bind(*args, **kwargs)
        except TypeError:
            return False
    return True


def _is_class_constructor(
    constructor: ast.AST,
    at: ast.AST,
    parents: dict[ast.AST, ast.AST],
    modules: dict[str, str],
    objects: dict[str, str],
) -> bool:
    tree = ast.Module(body=[], type_ignores=[])
    local = _callable_definition(constructor, at, parents, tree)
    if isinstance(local, ast.ClassDef):
        return True
    dotted = _dotted(constructor)
    if not dotted:
        return False
    dotted = _resolve_alias(dotted, modules, objects)
    parts = dotted.split(".")
    for split in range(len(parts) - 1, 0, -1):
        try:
            value = importlib.import_module(".".join(parts[:split]))
            for attribute in parts[split:]:
                value = getattr(value, attribute)
        except Exception:
            continue
        return inspect.isclass(value)
    return False


def scan_tests(root: Path) -> Census:
    repository_root = Path(__file__).resolve().parents[1]
    root = root.resolve()
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
            production = _production_signature(site.target, modules, objects, site.call, parents)
            replacement_node = _callable_definition(site.replacement, site.call, parents, tree) if site.replacement is not None else None
            bound_method = False
            if replacement_node is None and site.replacement is not None:
                replacement_node, bound_method = _local_method(site.replacement, site.call, parents)
            double = _replacement_signature(replacement_node) if replacement_node else None
            if double is not None and bound_method:
                parameters = list(double.parameters.values())
                if parameters:
                    double = double.replace(parameters=parameters[1:])
            if production is None or double is None:
                skipped.append(f"{path}:{site.line}: SKIPPED-UNRESOLVABLE")
                continue
            resolved += 1
            if not _compatible(production, double):
                try:
                    relative = path.relative_to(repository_root).as_posix()
                except ValueError:
                    relative = path.relative_to(root).as_posix()
                test_name = _test_name(site.call, parents)
                target = ast.unparse(site.target)
                violations.append(f"{relative}::{test_name}::{target}")
    return Census(scanned, resolved, tuple(skipped), tuple(violations))


def _test_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    scope = parents.get(node)
    while scope is not None:
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) and scope.name.startswith("test_"):
            return scope.name
        scope = parents.get(scope)
    return "<module>"


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
