"""Shared signature checks for monkeypatch doubles and their ratchet."""

from __future__ import annotations

import ast
import importlib
import inspect
import subprocess
from pathlib import Path
from typing import Any


_MISSING = object()


def _signature(value: Any) -> inspect.Signature:
    if isinstance(value, staticmethod):
        value = value.__func__
    return inspect.signature(value)


def _is_python_callable(value: Any) -> bool:
    if isinstance(value, staticmethod):
        value = value.__func__
    return inspect.isfunction(value) or inspect.ismethod(value)


def signature_mismatch(
    production: Any,
    replacement: Any,
    *,
    positional_receiver: bool = False,
) -> tuple[str | None, bool]:
    """Return an incompatibility reason and whether signature lookup was skipped."""
    if not _is_python_callable(production) or not callable(replacement):
        return None, False
    try:
        production_signature = _signature(production)
        replacement_signature = _signature(replacement)
    except (TypeError, ValueError):
        return None, True
    return signature_mismatch_for_signatures(
        production_signature,
        replacement_signature,
        positional_receiver=positional_receiver,
    ), False


def signature_mismatch_for_signatures(
    production: inspect.Signature,
    replacement: inspect.Signature,
    *,
    positional_receiver: bool = False,
) -> str | None:
    """Check calls the production function permits, including positional and named use.

    A double must accept each call shape its production callable allows. This
    derives the check cases from the production signature rather than copying
    its current parameter count into each test double.
    """
    production_params = tuple(production.parameters.values())
    if (
        positional_receiver
        and production_params
        and production_params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    ):
        production_params = (
            production_params[0].replace(kind=inspect.Parameter.POSITIONAL_ONLY),
            *production_params[1:],
        )
        production = production.replace(parameters=production_params)
    replacement_params = tuple(replacement.parameters.values())
    replacement_kinds = {parameter.kind for parameter in replacement_params}
    production_has_varargs = any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL
        for parameter in production_params
    )
    production_has_varkw = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in production_params
    )
    if production_has_varargs and inspect.Parameter.VAR_POSITIONAL not in replacement_kinds:
        return "production accepts *args but the double does not"
    if production_has_varkw and inspect.Parameter.VAR_KEYWORD not in replacement_kinds:
        return "production accepts **kwargs but the double does not"

    positional = tuple(
        parameter
        for parameter in production_params
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    keyword_only = tuple(
        parameter
        for parameter in production_params
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    )
    required_posonly_count = sum(
        parameter.default is inspect.Parameter.empty
        and parameter.kind is inspect.Parameter.POSITIONAL_ONLY
        for parameter in positional
    )
    required_keywords = tuple(
        parameter
        for parameter in (*positional, *keyword_only)
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    )
    marker = object()
    cases: list[tuple[tuple[Any, ...], dict[str, Any], str]] = []

    def add(args: tuple[Any, ...], kwargs: dict[str, Any], label: str) -> None:
        candidate = (args, kwargs, label)
        if candidate not in cases:
            cases.append(candidate)

    # Try each legal positional prefix: production permits its positional-or-
    # keyword parameters both by position and by name, and doubles must too.
    for prefix in range(required_posonly_count, len(positional) + 1):
        args = (marker,) * prefix
        kwargs = {
            parameter.name: marker
            for parameter in (*positional[prefix:], *keyword_only)
            if parameter.kind is not inspect.Parameter.POSITIONAL_ONLY
        }
        add(args, kwargs, f"{prefix} positional argument(s) and remaining names")

    minimum_args = (marker,) * required_posonly_count
    minimum_kwargs = {parameter.name: marker for parameter in required_keywords}
    add(minimum_args, minimum_kwargs, "required parameters only")

    if production_has_varargs:
        required_keyword_only = {
            parameter.name: marker
            for parameter in keyword_only
            if parameter.default is inspect.Parameter.empty
        }
        add(
            (marker,) * (len(positional) + 1),
            required_keyword_only,
            "one variadic positional argument",
        )
    if production_has_varkw:
        probe_name = "_signature_guard_probe"
        production_names = {parameter.name for parameter in production_params}
        while probe_name in production_names:
            probe_name += "_"
        add(
            minimum_args,
            {**minimum_kwargs, probe_name: marker},
            "one variadic keyword argument",
        )

    for args, kwargs, label in cases:
        try:
            replacement.bind(*args, **kwargs)
        except TypeError as error:
            return f"cannot bind production call ({label}): {error}"
    return None


def signature_target_name(value: Any) -> str:
    if isinstance(value, staticmethod):
        value = value.__func__
    if inspect.ismethod(value):
        value = value.__func__
    return f"{value.__module__}.{value.__qualname__}"


def resolve_import_path(path: str) -> Any:
    """Import the longest module prefix, then resolve its remaining attributes."""
    parts = path.split(".")
    for split in range(len(parts), 0, -1):
        try:
            value: Any = importlib.import_module(".".join(parts[:split]))
        except ModuleNotFoundError as error:
            if error.name != ".".join(parts[:split]):
                raise
            continue
        for part in parts[split:]:
            value = getattr(value, part)
        return value
    raise ModuleNotFoundError(path)


def _ast_signature(node: ast.Lambda | ast.FunctionDef | ast.AsyncFunctionDef) -> inspect.Signature:
    arguments = node.args
    positional_nodes = [*arguments.posonlyargs, *arguments.args]
    defaults = [_MISSING] * (len(positional_nodes) - len(arguments.defaults))
    defaults.extend(arguments.defaults)
    parameters: list[inspect.Parameter] = []
    for index, argument in enumerate(arguments.posonlyargs):
        parameters.append(
            inspect.Parameter(
                argument.arg,
                inspect.Parameter.POSITIONAL_ONLY,
                default=inspect.Parameter.empty if defaults[index] is _MISSING else marker_default,
            )
        )
    offset = len(arguments.posonlyargs)
    for index, argument in enumerate(arguments.args, start=offset):
        parameters.append(
            inspect.Parameter(
                argument.arg,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=inspect.Parameter.empty if defaults[index] is _MISSING else marker_default,
            )
        )
    if arguments.vararg is not None:
        parameters.append(
            inspect.Parameter(arguments.vararg.arg, inspect.Parameter.VAR_POSITIONAL)
        )
    for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        parameters.append(
            inspect.Parameter(
                argument.arg,
                inspect.Parameter.KEYWORD_ONLY,
                default=inspect.Parameter.empty if default is None else marker_default,
            )
        )
    if arguments.kwarg is not None:
        parameters.append(
            inspect.Parameter(arguments.kwarg.arg, inspect.Parameter.VAR_KEYWORD)
        )
    return inspect.Signature(parameters)


marker_default = object()


def _import_bindings(tree: ast.Module) -> dict[str, tuple[str, str | None]]:
    bindings: dict[str, tuple[str, str | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bindings[alias.asname] = (alias.name, None)
                else:
                    root = alias.name.split(".", 1)[0]
                    bindings[root] = (root, None)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                bindings[alias.asname or alias.name] = (node.module, alias.name)
    return bindings


def _resolve_ast_expression(
    node: ast.AST,
    bindings: dict[str, tuple[str, str | None]],
) -> Any:
    if isinstance(node, ast.Name) and node.id in bindings:
        module_path, member = bindings[node.id]
        module = importlib.import_module(module_path)
        if member is None:
            return module
        try:
            return getattr(module, member)
        except AttributeError:
            return importlib.import_module(f"{module_path}.{member}")
    if isinstance(node, ast.Attribute):
        return getattr(_resolve_ast_expression(node.value, bindings), node.attr)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return resolve_import_path(node.value)
    raise LookupError("target expression is not a statically imported name")


def scan_test_double_mismatches(tests_root: Path) -> tuple[dict[str, str], int]:
    repo_root = tests_root.parent
    findings: dict[str, str] = {}
    skips = 0
    tracked_tests = subprocess.run(
        ["git", "ls-files", "tests"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    test_paths = sorted(
        repo_root / relative
        for relative in tracked_tests
        if Path(relative).name.startswith("test_") and relative.endswith(".py")
    )
    for path in test_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(repo_root).as_posix()
        bindings = _import_bindings(tree)
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        functions: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.setdefault(node.name, []).append(node)
        for test in ast.walk(tree):
            if not isinstance(test, (ast.FunctionDef, ast.AsyncFunctionDef)) or not test.name.startswith("test_"):
                continue
            test_functions = {
                node.name: node
                for node in ast.walk(test)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            local_types: dict[str, type] = {}

            def inferred_type(expression: ast.AST | None) -> type | None:
                if expression is None:
                    return None
                if isinstance(expression, ast.Call):
                    resolved = None
                    try:
                        resolved = _resolve_ast_expression(expression.func, bindings)
                    except (ImportError, AttributeError, LookupError, ModuleNotFoundError, TypeError, ValueError):
                        if isinstance(expression.func, ast.Name):
                            local_function = test_functions.get(expression.func.id)
                            candidates = functions.get(expression.func.id, [])
                            function = local_function or (candidates[-1] if candidates else None)
                            if function is not None:
                                return inferred_type(function.returns)
                    return resolved if isinstance(resolved, type) else None
                if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
                    try:
                        parsed = ast.parse(expression.value, mode="eval").body
                    except SyntaxError:
                        return None
                    return inferred_type(parsed)
                try:
                    resolved = _resolve_ast_expression(expression, bindings)
                except (ImportError, AttributeError, LookupError, ModuleNotFoundError):
                    return None
                return resolved if isinstance(resolved, type) else None

            for node in ast.walk(test):
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    known_type = inferred_type(node.annotation)
                    if known_type is not None:
                        local_types[node.target.id] = known_type
                elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    known_type = inferred_type(node.value)
                    if known_type is not None:
                        for target_node in node.targets:
                            if isinstance(target_node, ast.Name):
                                local_types[target_node.id] = known_type
            local_lambdas = {
                target.id: node.value
                for node in ast.walk(test)
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                and isinstance(node.value, ast.Lambda)
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                if isinstance(target, ast.Name)
            }
            classes = []
            parent = parents.get(test)
            while parent is not None:
                if isinstance(parent, ast.ClassDef):
                    classes.append(parent.name)
                parent = parents.get(parent)
            classes.reverse()
            for call in ast.walk(test):
                if not (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "setattr"
                    and call.args
                ):
                    continue
                keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
                target_expr = call.args[0]
                try:
                    if isinstance(target_expr, ast.Constant) and isinstance(target_expr.value, str):
                        target, attribute, production = resolve_target_string(target_expr.value)
                        replacement_node = call.args[1] if len(call.args) > 1 else keywords.get("value")
                        production_signature = None
                        positional_receiver = has_positional_method_receiver(target, attribute)
                    else:
                        instance_type = (
                            local_types.get(target_expr.id)
                            if isinstance(target_expr, ast.Name)
                            else None
                        )
                        target = instance_type or _resolve_ast_expression(target_expr, bindings)
                        name_node = call.args[1] if len(call.args) > 1 else keywords.get("name")
                        replacement_node = call.args[2] if len(call.args) > 2 else keywords.get("value")
                        if not isinstance(name_node, ast.Constant) or not isinstance(name_node.value, str):
                            continue
                        attribute = name_node.value
                        production = getattr(target, attribute)
                        positional_receiver = has_positional_method_receiver(target, attribute)
                        production_signature = None
                        if instance_type is not None:
                            descriptor = inspect.getattr_static(target, attribute)
                            production_signature = _signature(production)
                            if inspect.isfunction(descriptor):
                                parameters = tuple(production_signature.parameters.values())
                                production_signature = production_signature.replace(
                                    parameters=parameters[1:]
                                )
                            positional_receiver = False
                    if isinstance(replacement_node, ast.Lambda):
                        replacement_signature = _ast_signature(replacement_node)
                    elif isinstance(replacement_node, ast.Name):
                        function = test_functions.get(replacement_node.id)
                        if function is None:
                            candidates = functions.get(replacement_node.id, [])
                            function = candidates[-1] if candidates else None
                        if function is not None:
                            replacement_signature = _ast_signature(function)
                        elif replacement_node.id in local_lambdas:
                            replacement_signature = _ast_signature(local_lambdas[replacement_node.id])
                        elif replacement_node.id in bindings:
                            replacement = _resolve_ast_expression(replacement_node, bindings)
                            replacement_signature = _signature(replacement)
                        else:
                            continue
                    else:
                        continue
                    if not _is_python_callable(production):
                        continue
                    positional_receiver = has_positional_method_receiver(target, attribute)
                    if production_signature is None:
                        production_signature = _signature(production)
                    reason = signature_mismatch_for_signatures(
                        production_signature,
                        replacement_signature,
                        positional_receiver=positional_receiver,
                    )
                    if reason is not None:
                        nodeid = "::".join([relative, *classes, test.name, signature_target_name(production)])
                        findings[nodeid] = f"{production_signature} <- {replacement_signature}: {reason}"
                except (ImportError, AttributeError, LookupError, ModuleNotFoundError, TypeError, ValueError):
                    skips += 1
    return findings, skips


def read_allowlist(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, reason = line.partition(" # ")
        if not separator or not reason.strip():
            raise AssertionError(f"{path}:{line_number} needs `path::test::target # reason`")
        if key in entries:
            raise AssertionError(f"{path}:{line_number} repeats {key}")
        entries[key] = reason.strip()
    return entries


def allowlist_key_for_node(nodeid: str, production: Any) -> str:
    base_nodeid = nodeid.split("[", 1)[0]
    return f"{base_nodeid}::{signature_target_name(production)}"


def resolve_target_string(path: str) -> tuple[Any, str, Any]:
    target_path, _, attribute = path.rpartition(".")
    if not target_path:
        raise LookupError(f"not a dotted monkeypatch target: {path}")
    target = resolve_import_path(target_path)
    return target, attribute, getattr(target, attribute)


def has_positional_method_receiver(target: Any, name: str) -> bool:
    if not isinstance(target, type):
        return False
    try:
        descriptor = inspect.getattr_static(target, name)
    except AttributeError:
        return False
    return inspect.isfunction(descriptor)
