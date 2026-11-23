#!/usr/bin/env python3
"""Find test doubles whose callable signature is narrower than production's.

Compatibility rules:
1. Every production positional slot is accepted positionally or by ``*args``.
2. Every production keyword-capable name is accepted by name or by ``**kwargs``.
3. The double requires no argument production leaves optional.
4. A keyword production can pass alongside positional arguments is not bound
   to an already-filled positional slot by the double (including the
   ``*args``/``**kwargs`` collision).
5. Descriptor signatures come from ``inspect.signature(getattr(receiver,
   name))`` on the receiver surface being patched; receivers are never stripped
   by hand.
"""

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


def _assigned_constructor_call(
    name: str,
    at: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.Call | None:
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
                        declarations.append(value)
            if declarations:
                return max(declarations, key=lambda item: item.lineno)
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


def _imported_object(
    dotted: str,
    modules: dict[str, str],
    objects: dict[str, str],
):
    resolved = _resolve_alias(dotted, modules, objects)
    try:
        return importlib.import_module(resolved)
    except Exception:
        pass
    parts = resolved.split(".")
    for split in range(len(parts) - 1, 0, -1):
        try:
            value = importlib.import_module(".".join(parts[:split]))
            for attribute in parts[split:]:
                value = getattr(value, attribute)
            return value
        except Exception:
            continue
    return None


def _constructed_receiver(
    call: ast.Call,
    modules: dict[str, str],
    objects: dict[str, str],
):
    constructor = _imported_object(_dotted(call.func) or "", modules, objects)
    if not inspect.isclass(constructor):
        return None
    try:
        args = [ast.literal_eval(value) for value in call.args]
        kwargs = {
            keyword.arg: ast.literal_eval(keyword.value)
            for keyword in call.keywords
            if keyword.arg is not None
        }
        if len(kwargs) != len(call.keywords):
            return None
        return constructor(*args, **kwargs)
    except Exception:
        return None


def _receiver(
    node: ast.AST,
    at: ast.AST,
    parents: dict[ast.AST, ast.AST],
    modules: dict[str, str],
    objects: dict[str, str],
):
    if isinstance(node, ast.Name):
        constructor_call = _assigned_constructor_call(node.id, at, parents)
        if constructor_call is not None:
            receiver = _constructed_receiver(constructor_call, modules, objects)
            if receiver is not None:
                return receiver
    dotted = _dotted(node)
    return _imported_object(dotted, modules, objects) if dotted else None


def _signature_of(value) -> inspect.Signature | None:
    try:
        return inspect.signature(value)
    except (TypeError, ValueError):
        return None


def _production_signature(
    target: ast.AST,
    modules: dict[str, str],
    objects: dict[str, str],
    call: ast.Call,
    parents: dict[ast.AST, ast.AST],
):
    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        dotted = target.value
    elif isinstance(target, ast.Tuple) and len(target.elts) == 2 and isinstance(target.elts[1], ast.Constant):
        receiver = _receiver(target.elts[0], call, parents, modules, objects)
        if receiver is None:
            return None
        try:
            return _signature_of(getattr(receiver, target.elts[1].value))
        except Exception:
            return None
    else:
        base = _dotted(target)
        if not base:
            return None
        dotted = _resolve_alias(base, modules, objects)

    value = _imported_object(dotted, modules, objects)
    return _signature_of(value) if callable(value) else None


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


def _has_vararg(signature: inspect.Signature, kind: inspect._ParameterKind) -> bool:
    return any(parameter.kind is kind for parameter in signature.parameters.values())


def _positional_parameters(signature: inspect.Signature) -> list[inspect.Parameter]:
    return [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]


def _keyword_accepted(signature: inspect.Signature, name: str) -> bool:
    parameter = signature.parameters.get(name)
    return (
        parameter is not None
        and parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ) or _has_vararg(signature, inspect.Parameter.VAR_KEYWORD)


def _keyword_is_production_accepted(signature: inspect.Signature, name: str) -> bool:
    parameter = signature.parameters.get(name)
    if parameter is not None and parameter.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    ):
        return True
    return _has_vararg(signature, inspect.Parameter.VAR_KEYWORD)


def _required_parameters_compatible(
    production: inspect.Signature,
    double: inspect.Signature,
) -> bool:
    production_parameters = list(production.parameters.values())
    double_parameters = list(double.parameters.values())
    production_keyword = {
        parameter.name: parameter
        for parameter in production_parameters
        if parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    production_positional_only_required = 0
    for parameter in production_parameters:
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY and parameter.default is inspect.Parameter.empty:
            production_positional_only_required += 1
        elif parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            break

    for index, parameter in enumerate(double_parameters):
        if parameter.default is not inspect.Parameter.empty or parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            required_production = production_keyword.get(parameter.name)
            if not (
                required_production is not None
                and required_production.kind is inspect.Parameter.KEYWORD_ONLY
                and required_production.default is inspect.Parameter.empty
            ):
                return False
            continue
        positional_index = sum(
            earlier.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            for earlier in double_parameters[:index]
        )
        if positional_index < production_positional_only_required:
            continue
        required_production = production_keyword.get(parameter.name)
        if required_production is None or required_production.default is not inspect.Parameter.empty:
            return False
        if required_production.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD:
            production_index = _positional_parameters(production).index(required_production)
            if positional_index > production_index:
                return False
    return True


def _has_positional_keyword_collision(
    production: inspect.Signature,
    double: inspect.Signature,
) -> bool:
    production_positional = _positional_parameters(production)
    double_positional = _positional_parameters(double)
    production_positional_only_required = 0
    for parameter in production.parameters.values():
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY and parameter.default is inspect.Parameter.empty:
            production_positional_only_required += 1
        elif parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            break
    has_production_varargs = _has_vararg(production, inspect.Parameter.VAR_POSITIONAL)
    maximum_positional = float("inf") if has_production_varargs else len(production_positional)
    production_keywords = {
        parameter.name: index
        for index, parameter in enumerate(production_positional)
        if parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    }

    for double_index, parameter in enumerate(double_positional):
        if parameter.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD:
            continue
        if not _keyword_is_production_accepted(production, parameter.name):
            continue
        minimum_positional = max(double_index + 1, production_positional_only_required)
        if minimum_positional > maximum_positional:
            continue
        production_index = production_keywords.get(parameter.name)
        if production_index is not None and minimum_positional > production_index:
            continue
        return True
    return False


def _compatible(production: inspect.Signature, double: inspect.Signature) -> bool:
    production_params = list(production.parameters.values())
    double_params = list(double.parameters.values())
    production_positional = _positional_parameters(production)
    double_positional = _positional_parameters(double)
    if len(production_positional) > len(double_positional) and not _has_vararg(double, inspect.Parameter.VAR_POSITIONAL):
        return False
    if _has_vararg(production, inspect.Parameter.VAR_POSITIONAL) and not _has_vararg(double, inspect.Parameter.VAR_POSITIONAL):
        return False
    if _has_vararg(production, inspect.Parameter.VAR_KEYWORD) and not _has_vararg(double, inspect.Parameter.VAR_KEYWORD):
        return False
    for parameter in production_params:
        if parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            if not _keyword_accepted(double, parameter.name):
                return False
    if not _required_parameters_compatible(production, double):
        return False
    return not _has_positional_keyword_collision(production, double)


def _replacement_signature_for_site(
    node: ast.AST,
    site: DoubleSite,
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
    modules: dict[str, str],
    objects: dict[str, str],
) -> inspect.Signature | None:
    definition = _callable_definition(node, site.call, parents, tree)
    if definition is not None:
        return _replacement_signature(definition)
    if isinstance(node, ast.Attribute):
        receiver = _receiver(node.value, site.call, parents, modules, objects)
        if receiver is None:
            return None
        try:
            return _signature_of(getattr(receiver, node.attr))
        except Exception:
            return None
    if isinstance(node, ast.Name):
        value = _imported_object(_dotted(node) or "", modules, objects)
        return _signature_of(value) if callable(value) else None
    if isinstance(node, ast.Call):
        value = _constructed_receiver(node, modules, objects)
        return _signature_of(value) if callable(value) else None
    return None


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
            double = (
                _replacement_signature_for_site(site.replacement, site, tree, parents, modules, objects)
                if site.replacement is not None
                else None
            )
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
