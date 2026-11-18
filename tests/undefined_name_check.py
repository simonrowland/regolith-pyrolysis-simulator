"""Symtable-based undefined-name check shared by repository meta-tests.

This reports unbound global loads, but does not diagnose ``UnboundLocalError``
from a load before a later local assignment; symtable classifies that name as
local rather than global.
"""

from __future__ import annotations

import ast
import builtins
import symtable
from pathlib import Path

_IMPLICIT = {
    "__file__",
    "__name__",
    "__doc__",
    "__spec__",
    "__builtins__",
    "__annotations__",
    "__conditional_annotations__",
}
_ScopeKey = tuple[str, str, int]


def undefined_names(path: Path) -> tuple[tuple[str, int], ...]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    top = symtable.symtable(source, str(path), "exec")
    postponed_annotations = any(
        isinstance(statement, ast.ImportFrom)
        and statement.module == "__future__"
        and any(alias.name == "annotations" for alias in statement.names)
        for statement in tree.body
    )
    runtime_loads, comprehension_targets = _runtime_loads(
        tree, top, postponed_annotations
    )
    type_checking_imports, runtime_bindings = _scope_bindings(tree)
    module_scope = _scope_key(top)
    defined = {
        symbol.get_name()
        for symbol in top.get_symbols()
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()
    }
    defined.difference_update(
        type_checking_imports.get(module_scope, set())
        - runtime_bindings.get(module_scope, set())
    )
    defined.difference_update(
        comprehension_targets.get(module_scope, set())
        - runtime_bindings.get(module_scope, set())
    )
    missing: set[tuple[str, int]] = set()

    def walk(table: symtable.SymbolTable) -> None:
        table_loads = runtime_loads.get(_scope_key(table), {})
        if table.get_type() == "class":
            type_only_class_names = (
                type_checking_imports.get(_scope_key(table), set())
                - runtime_bindings.get(_scope_key(table), set())
                - defined
            )
            for name in type_only_class_names & table_loads.keys():
                missing.add((name, table_loads[name]))
        for symbol in table.get_symbols():
            name = symbol.get_name()
            is_unbound_global = symbol.is_global() or (
                table.get_type() == "module"
                and symbol.is_referenced()
                and not symbol.is_assigned()
                and not symbol.is_imported()
            )
            if (
                is_unbound_global
                and name not in defined
                and not hasattr(builtins, name)
                and name not in _IMPLICIT
                and name in table_loads
            ):
                missing.add((name, table_loads[name]))
        for child in table.get_children():
            walk(child)

    walk(top)
    return tuple(sorted(missing))


def _scope_key(table: symtable.SymbolTable) -> _ScopeKey:
    return (table.get_type(), table.get_name(), table.get_lineno())


def _runtime_loads(
    tree: ast.Module,
    top: symtable.SymbolTable,
    postponed_annotations: bool,
) -> tuple[dict[_ScopeKey, dict[str, int]], dict[_ScopeKey, set[str]]]:
    loads: dict[_ScopeKey, dict[str, int]] = {}
    comprehension_targets: dict[_ScopeKey, set[str]] = {}
    symbol_scopes: set[_ScopeKey] = set()
    variable_annotation_scopes: dict[_ScopeKey, _ScopeKey] = {}

    def collect_scopes(table: symtable.SymbolTable) -> None:
        symbol_scopes.add(_scope_key(table))
        for child in table.get_children():
            collect_scopes(child)

    collect_scopes(top)

    def annotation_scope(
        outer_scope: _ScopeKey,
        line: int,
        *,
        variable: bool = False,
    ) -> _ScopeKey:
        # Python 3.14 reports eager annotations in an annotation child table.
        if variable and outer_scope in variable_annotation_scopes:
            return variable_annotation_scopes[outer_scope]
        candidate = ("annotation", "__annotate__", line)
        result = candidate if candidate in symbol_scopes else outer_scope
        if variable:
            variable_annotation_scopes[outer_scope] = result
        return result

    def record(node: ast.Name, scope: _ScopeKey) -> None:
        if isinstance(node.ctx, ast.Load):
            scope_loads = loads.setdefault(scope, {})
            scope_loads[node.id] = min(
                scope_loads.get(node.id, node.lineno), node.lineno
            )

    def visit(node: ast.AST, scope: _ScopeKey) -> None:
        if isinstance(node, ast.Name):
            record(node, scope)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                visit(decorator, scope)
            for default in (*node.args.defaults, *node.args.kw_defaults):
                if default is not None:
                    visit(default, scope)
            if not postponed_annotations:
                arguments = [
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                ]
                if node.args.vararg is not None:
                    arguments.append(node.args.vararg)
                if node.args.kwarg is not None:
                    arguments.append(node.args.kwarg)
                for argument in arguments:
                    if argument.annotation is not None:
                        visit(argument.annotation, annotation_scope(scope, node.lineno))
                if node.returns is not None:
                    visit(node.returns, annotation_scope(scope, node.lineno))
            function_scope = ("function", node.name, node.lineno)
            for statement in node.body:
                visit(statement, function_scope)
            return
        if isinstance(node, ast.ClassDef):
            for decorator in node.decorator_list:
                visit(decorator, scope)
            for base in node.bases:
                visit(base, scope)
            for keyword in node.keywords:
                visit(keyword.value, scope)
            class_scope = ("class", node.name, node.lineno)
            for statement in node.body:
                visit(statement, class_scope)
            return
        if isinstance(node, ast.Lambda):
            for default in (*node.args.defaults, *node.args.kw_defaults):
                if default is not None:
                    visit(default, scope)
            visit(node.body, ("function", "lambda", node.lineno))
            return
        if isinstance(node, ast.AnnAssign):
            visit(node.target, scope)
            if not postponed_annotations and scope[0] != "function":
                visit(
                    node.annotation,
                    annotation_scope(scope, node.lineno, variable=True),
                )
            if node.value is not None:
                visit(node.value, scope)
            return
        if isinstance(node, ast.arg):
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            _visit_comprehension(
                node,
                scope,
                visit,
                symbol_scopes,
                comprehension_targets,
            )
            return
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, _scope_key(top))
    return loads, comprehension_targets


def _visit_comprehension(
    node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    outer_scope: _ScopeKey,
    visit,
    symbol_scopes: set[_ScopeKey],
    comprehension_targets: dict[_ScopeKey, set[str]],
) -> None:
    if not node.generators:
        return
    first_generator = node.generators[0]
    visit(first_generator.iter, outer_scope)
    names = {
        ast.ListComp: "listcomp",
        ast.SetComp: "setcomp",
        ast.DictComp: "dictcomp",
        ast.GeneratorExp: "genexpr",
    }
    child_scope = ("function", names[type(node)], node.lineno)
    inner_scope = child_scope if child_scope in symbol_scopes else outer_scope
    if inner_scope == outer_scope:
        targets = comprehension_targets.setdefault(outer_scope, set())
        for generator in node.generators:
            targets.update(
                target.id
                for target in ast.walk(generator.target)
                if isinstance(target, ast.Name)
                and isinstance(target.ctx, ast.Store)
            )
    for index, generator in enumerate(node.generators):
        if index:
            visit(generator.iter, inner_scope)
        visit(generator.target, inner_scope)
        for condition in generator.ifs:
            visit(condition, inner_scope)
    if isinstance(node, ast.DictComp):
        visit(node.key, inner_scope)
        visit(node.value, inner_scope)
    else:
        visit(node.elt, inner_scope)


def _scope_bindings(
    tree: ast.Module,
) -> tuple[dict[_ScopeKey, set[str]], dict[_ScopeKey, set[str]]]:
    typing_modules = {"typing"}
    type_checking_names = {"TYPE_CHECKING"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "typing":
                    typing_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "typing":
            for alias in node.names:
                if alias.name == "TYPE_CHECKING":
                    type_checking_names.add(alias.asname or alias.name)

    type_only: dict[_ScopeKey, set[str]] = {}
    runtime: dict[_ScopeKey, set[str]] = {}

    def import_bindings(node: ast.Import | ast.ImportFrom) -> set[str]:
        bindings: set[str] = set()
        for alias in node.names:
            if alias.name == "*":
                continue
            if alias.asname:
                bindings.add(alias.asname)
            elif isinstance(node, ast.Import):
                bindings.add(alias.name.split(".", 1)[0])
            else:
                bindings.add(alias.name)
        return bindings

    def is_type_checking(test: ast.expr) -> bool:
        if isinstance(test, ast.Name):
            return test.id in type_checking_names
        return (
            isinstance(test, ast.Attribute)
            and test.attr == "TYPE_CHECKING"
            and isinstance(test.value, ast.Name)
            and test.value.id in typing_modules
        )

    def bind_names(
        scope: _ScopeKey,
        names: set[str],
        under_type_checking: bool,
    ) -> None:
        destination = type_only if under_type_checking else runtime
        destination.setdefault(scope, set()).update(names)

    def visit_statements(
        statements: list[ast.stmt],
        scope: _ScopeKey,
        under_type_checking: bool,
    ) -> None:
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bind_names(scope, {statement.name}, under_type_checking)
                continue
            if isinstance(statement, ast.ClassDef):
                bind_names(scope, {statement.name}, under_type_checking)
                visit_statements(
                    statement.body,
                    ("class", statement.name, statement.lineno),
                    under_type_checking,
                )
                continue
            if isinstance(statement, ast.If):
                visit_statements(
                    statement.body,
                    scope,
                    under_type_checking or is_type_checking(statement.test),
                )
                visit_statements(statement.orelse, scope, under_type_checking)
                continue
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                bind_names(scope, import_bindings(statement), under_type_checking)
                continue
            if isinstance(statement, ast.Assign):
                bind_names(
                    scope,
                    {
                        target.id
                        for assignment_target in statement.targets
                        for target in ast.walk(assignment_target)
                        if isinstance(target, ast.Name)
                        and isinstance(target.ctx, ast.Store)
                    },
                    under_type_checking,
                )
            elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
                bind_names(
                    scope,
                    {
                        target.id
                        for target in ast.walk(statement.target)
                        if isinstance(target, ast.Name)
                        and isinstance(target.ctx, ast.Store)
                    },
                    under_type_checking,
                )
            for child in ast.iter_child_nodes(statement):
                if isinstance(child, ast.stmt):
                    visit_statements([child], scope, under_type_checking)
                elif isinstance(child, ast.ExceptHandler):
                    visit_statements(child.body, scope, under_type_checking)
                elif isinstance(child, ast.match_case):
                    visit_statements(child.body, scope, under_type_checking)

    visit_statements(tree.body, ("module", "top", 0), False)
    return type_only, runtime
