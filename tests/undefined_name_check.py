"""Symtable-based undefined-name check shared by repository meta-tests.

It does not detect UnboundLocalError from a load before a later local assignment.
TYPE_CHECKING aliases, negated guards, and else-branch semantics are out of scope.
"""

from __future__ import annotations

import ast
import builtins
import symtable
from pathlib import Path

_IMPLICIT = {"__file__", "__name__", "__doc__", "__spec__", "__builtins__",
             "__annotations__", "__conditional_annotations__"}
_ScopeKey = tuple[str, str, int]


def undefined_names(path: Path) -> tuple[tuple[str, int], ...]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    top = symtable.symtable(source, str(path), "exec")
    postponed = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )
    loads, augmented_loads = _runtime_loads(tree, top, postponed)
    runtime, type_only, runtime_without_augassign = _scope_bindings(
        tree, top, postponed
    )
    module = _scope_key(top)
    defined = runtime.get(module, set())
    missing: set[tuple[str, int]] = set()

    def walk(
        table: symtable.SymbolTable,
        class_only: set[str] | None = None,
        enclosing_functions: tuple[_ScopeKey, ...] = (),
    ) -> None:
        scope = _scope_key(table)
        scope_loads = loads.get(scope, {})
        scope_augmented_loads = augmented_loads.get(scope, {})
        if table.get_type() == "class":
            class_only = type_only.get(scope, set()) - runtime.get(scope, set()) - defined
        elif table.get_type() != "annotation":
            class_only = None
        missing.update(
            (name, scope_loads[name])
            for name in (class_only or set()) & scope_loads.keys()
        )
        for symbol in table.get_symbols():
            name = symbol.get_name()
            global_load = symbol.is_global() or table.get_type() == "module"
            if (
                global_load and name not in defined and not hasattr(builtins, name)
                and name not in _IMPLICIT and name in scope_loads
            ):
                missing.add((name, scope_loads[name]))
            if (
                global_load and name in scope_augmented_loads
                and name not in runtime_without_augassign.get(module, set())
                and not hasattr(builtins, name) and name not in _IMPLICIT
            ):
                missing.add((name, scope_augmented_loads[name]))
            if symbol.is_free() and name in scope_loads:
                for enclosing in reversed(enclosing_functions):
                    if name not in runtime.get(enclosing, set()) and name not in type_only.get(
                        enclosing, set()
                    ):
                        continue
                    if (
                        name in type_only.get(enclosing, set())
                        and name not in runtime.get(enclosing, set())
                    ):
                        missing.add((name, scope_loads[name]))
                    break
        for child in table.get_children():
            walk(
                child,
                class_only
                if table.get_type() == "class" and child.get_type() == "annotation"
                else None,
                enclosing_functions
                + ((scope,) if table.get_type() == "function" else ()),
            )

    walk(top)
    return tuple(sorted(missing))


def _scope_key(table: symtable.SymbolTable) -> _ScopeKey:
    return table.get_type(), table.get_name(), table.get_lineno()


def _runtime_loads(
    tree: ast.Module, top: symtable.SymbolTable, postponed: bool
) -> tuple[dict[_ScopeKey, dict[str, int]], dict[_ScopeKey, dict[str, int]]]:
    loads: dict[_ScopeKey, dict[str, int]] = {}
    augmented_loads: dict[_ScopeKey, dict[str, int]] = {}
    tables: set[_ScopeKey] = set()
    variable_annotations: dict[_ScopeKey, _ScopeKey] = {}

    def collect(table: symtable.SymbolTable) -> None:
        tables.add(_scope_key(table))
        for child in table.get_children():
            collect(child)

    collect(top)

    def annotation_scope(scope: _ScopeKey, line: int, variable: bool = False) -> _ScopeKey:
        candidate = ("annotation", "__annotate__", line)
        result = candidate if candidate in tables else scope
        return variable_annotations.setdefault(scope, result) if variable else result

    def visit(node: ast.AST, scope: _ScopeKey, local: frozenset[str] = frozenset()) -> None:
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load) and node.id not in local:
                names = loads.setdefault(scope, {})
                names[node.id] = min(names.get(node.id, node.lineno), node.lineno)
            return
        if isinstance(node, ast.AugAssign):
            visit(node.target, scope, local)
            if isinstance(node.target, ast.Name) and node.target.id not in local:
                names = loads.setdefault(scope, {})
                names[node.target.id] = min(
                    names.get(node.target.id, node.target.lineno), node.target.lineno
                )
                names = augmented_loads.setdefault(scope, {})
                names[node.target.id] = min(
                    names.get(node.target.id, node.target.lineno), node.target.lineno
                )
            visit(node.value, scope, local)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for expr in (*node.decorator_list, *node.args.defaults, *node.args.kw_defaults):
                if expr is not None:
                    visit(expr, scope, local)
            if not postponed:
                args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                args.extend(arg for arg in (node.args.vararg, node.args.kwarg) if arg)
                annotation_scope_key = annotation_scope(scope, node.lineno)
                for arg in args:
                    if arg.annotation:
                        visit(arg.annotation, annotation_scope_key, local)
                if node.returns:
                    visit(node.returns, annotation_scope_key, local)
            child = ("function", node.name, node.lineno)
            for statement in node.body:
                visit(statement, child)
            return
        if isinstance(node, ast.ClassDef):
            for expr in (*node.decorator_list, *node.bases, *(kw.value for kw in node.keywords)):
                visit(expr, scope, local)
            child = ("class", node.name, node.lineno)
            for statement in node.body:
                visit(statement, child)
            return
        if isinstance(node, ast.Lambda):
            for expr in (*node.args.defaults, *node.args.kw_defaults):
                if expr is not None:
                    visit(expr, scope, local)
            visit(node.body, ("function", "lambda", node.lineno), local)
            return
        if isinstance(node, ast.AnnAssign):
            visit(node.target, scope, local)
            if not postponed and scope[0] != "function":
                visit(node.annotation, annotation_scope(scope, node.lineno, True), local)
            if node.value is not None:
                visit(node.value, scope, local)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            if not node.generators:
                return
            first = node.generators[0]
            visit(first.iter, scope, local)
            labels = {ast.ListComp: "listcomp", ast.SetComp: "setcomp",
                      ast.DictComp: "dictcomp", ast.GeneratorExp: "genexpr"}
            candidate = ("function", labels[type(node)], node.lineno)
            inner = candidate if candidate in tables else scope
            bound = set(local)
            for index, generator in enumerate(node.generators):
                if index:
                    visit(generator.iter, inner, frozenset(bound))
                visit(generator.target, inner, frozenset(bound))
                bound.update(
                    name.id for name in ast.walk(generator.target)
                    if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store)
                )
                for condition in generator.ifs:
                    visit(condition, inner, frozenset(bound))
            values = (node.key, node.value) if isinstance(node, ast.DictComp) else (node.elt,)
            for value in values:
                visit(value, inner, frozenset(bound))
            return
        for child in ast.iter_child_nodes(node):
            visit(child, scope, local)

    visit(tree, _scope_key(top))
    return loads, augmented_loads


def _scope_bindings(
    tree: ast.Module, top: symtable.SymbolTable, postponed: bool
) -> tuple[
    dict[_ScopeKey, set[str]],
    dict[_ScopeKey, set[str]],
    dict[_ScopeKey, set[str]],
]:
    module = _scope_key(top)
    runtime: dict[_ScopeKey, set[str]] = {}
    type_only: dict[_ScopeKey, set[str]] = {}
    runtime_without_augassign: dict[_ScopeKey, set[str]] = {}
    tables: dict[_ScopeKey, symtable.SymbolTable] = {}

    def collect_tables(table: symtable.SymbolTable) -> None:
        tables[_scope_key(table)] = table
        for child in table.get_children():
            collect_tables(child)

    collect_tables(top)

    def scan_imports(nodes: list[ast.AST]) -> set[str]:
        imports: set[str] = set()

        def visit(node: ast.AST) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                return
            if isinstance(node, ast.Import):
                if any(alias.name == "typing" and alias.asname is None for alias in node.names):
                    imports.add("typing")
            elif isinstance(node, ast.ImportFrom) and node.module == "typing":
                if any(
                    alias.name == "TYPE_CHECKING" and alias.asname is None
                    for alias in node.names
                ):
                    imports.add("TYPE_CHECKING")
            for child in ast.iter_child_nodes(node):
                visit(child)

        for node in nodes:
            visit(node)
        return imports

    def scan(nodes: list[ast.AST], scope: _ScopeKey, function: bool = False,
             arguments: tuple[ast.arg, ...] = (), outer_imports: set[str] | None = None) -> None:
        imports = scan_imports(nodes)
        outer_imports = outer_imports or set()
        table = tables.get(scope)

        def is_guard(test: ast.expr) -> bool:
            def imported(name: str) -> bool:
                if name in imports:
                    return True
                if table is None or name not in table.get_identifiers():
                    return False
                symbol = table.lookup(name)
                return name in outer_imports and (symbol.is_global() or symbol.is_free())

            return (
                imported("TYPE_CHECKING") and isinstance(test, ast.Name)
                and test.id == "TYPE_CHECKING"
            ) or (
                imported("typing") and isinstance(test, ast.Attribute)
                and test.attr == "TYPE_CHECKING" and isinstance(test.value, ast.Name)
                and test.value.id == "typing"
            )

        class Bindings(ast.NodeVisitor):
            def __init__(self) -> None:
                self.runtime: set[str] = {arg.arg for arg in arguments}
                self.runtime_without_augassign: set[str] = set(self.runtime)
                self.type_only: set[str] = set()
                self.globals: set[str] = set()
                self.nonlocals: set[str] = set()
                self.guarded = False

            def bind(
                self, name: str, imported: bool = False, augmented: bool = False
            ) -> None:
                (self.type_only if self.guarded and imported else self.runtime).add(name)
                if not augmented and not (self.guarded and imported):
                    self.runtime_without_augassign.add(name)

            def visit_Name(self, node: ast.Name) -> None:
                if isinstance(node.ctx, ast.Store):
                    self.bind(node.id)

            def visit_AugAssign(self, node: ast.AugAssign) -> None:
                if isinstance(node.target, ast.Name):
                    self.bind(node.target.id, augmented=True)
                else:
                    self.visit(node.target)
                self.visit(node.value)

            def visit_Import(self, node: ast.Import) -> None:
                for alias in node.names:
                    self.bind(alias.asname or alias.name.split(".", 1)[0], True)

            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
                if node.module != "__future__":
                    for alias in node.names:
                        if alias.name != "*":
                            self.bind(alias.asname or alias.name, True)

            def visit_If(self, node: ast.If) -> None:
                self.visit(node.test)
                previous = self.guarded
                self.guarded |= is_guard(node.test)
                for statement in node.body:
                    self.visit(statement)
                self.guarded = previous
                for statement in node.orelse:
                    self.visit(statement)

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self.bind(node.name)
                header = [*node.decorator_list, *node.args.defaults, *node.args.kw_defaults]
                args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                args.extend(arg for arg in (node.args.vararg, node.args.kwarg) if arg)
                if not postponed:
                    header.extend(arg.annotation for arg in args if arg.annotation)
                    if node.returns:
                        header.append(node.returns)
                for expr in header:
                    if expr is not None:
                        self.visit(expr)
                inherited = outer_imports | imports if scope[0] != "class" else outer_imports
                scan(node.body, ("function", node.name, node.lineno), True, tuple(args), inherited)

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self.bind(node.name)
                expressions = (
                    *node.decorator_list, *node.bases, *(kw.value for kw in node.keywords)
                )
                for expr in expressions:
                    self.visit(expr)
                inherited = outer_imports | imports if scope[0] != "class" else outer_imports
                scan(node.body, ("class", node.name, node.lineno), outer_imports=inherited)

            def visit_Lambda(self, node: ast.Lambda) -> None:
                for expr in (*node.args.defaults, *node.args.kw_defaults):
                    if expr is not None:
                        self.visit(expr)
                args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                args.extend(arg for arg in (node.args.vararg, node.args.kwarg) if arg)
                inherited = outer_imports | imports if scope[0] != "class" else outer_imports
                scan(
                    [node.body], ("function", "lambda", node.lineno), True,
                    tuple(args), inherited,
                )

            def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
                if node.value is not None:
                    self.visit(node.target)
                if not postponed and not function:
                    self.visit(node.annotation)
                if node.value is not None:
                    self.visit(node.value)

            def visit_ListComp(self, node: ast.ListComp) -> None:
                for generator in node.generators:
                    self.visit(generator.iter)
                    for condition in generator.ifs:
                        self.visit(condition)
                values = (node.key, node.value) if isinstance(node, ast.DictComp) else (node.elt,)
                for value in values:
                    self.visit(value)

            visit_SetComp = visit_ListComp
            visit_DictComp = visit_ListComp
            visit_GeneratorExp = visit_ListComp

            def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
                if node.name:
                    self.bind(node.name)
                self.generic_visit(node)

            def visit_MatchAs(self, node: ast.MatchAs) -> None:
                if node.name:
                    self.bind(node.name)
                self.generic_visit(node)

            def visit_MatchStar(self, node: ast.MatchStar) -> None:
                if node.name:
                    self.bind(node.name)

            def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
                if node.rest:
                    self.bind(node.rest)
                self.generic_visit(node)

            def visit_Global(self, node: ast.Global) -> None:
                self.globals.update(node.names)

            def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
                self.nonlocals.update(node.names)

        collector = Bindings()
        for node in nodes:
            collector.visit(node)
        global_runtime = collector.runtime & collector.globals
        global_runtime_without_augassign = (
            collector.runtime_without_augassign & collector.globals
        )
        global_type_only = collector.type_only & collector.globals
        for names, destination in (
            (collector.runtime, runtime),
            (collector.type_only, type_only),
            (collector.runtime_without_augassign, runtime_without_augassign),
        ):
            names.difference_update(collector.nonlocals | collector.globals)
            destination.setdefault(scope, set()).update(names)
        runtime.setdefault(module, set()).update(global_runtime)
        type_only.setdefault(module, set()).update(global_type_only)
        runtime_without_augassign.setdefault(module, set()).update(
            global_runtime_without_augassign
        )

    scan(tree.body, module)
    return runtime, type_only, runtime_without_augassign
