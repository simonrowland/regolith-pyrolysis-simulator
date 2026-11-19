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
_ScopeKey = tuple[str, int]


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
    scope_tables, annotation_tables = _scope_tables(tree, top)
    runtime, type_checking_guards = _scope_bindings(
        tree, postponed, scope_tables
    )
    loads, enclosing_functions, annotation_parents = _runtime_loads(
        tree,
        postponed,
        scope_tables,
        annotation_tables,
        type_checking_guards,
    )
    module = ("module", id(tree))
    defined = runtime.get(module, set())
    missing: set[tuple[str, int]] = set()

    def has_global_binding(name: str) -> bool:
        return (
            name in defined
            or hasattr(builtins, name)
            or name in _IMPLICIT
        )

    def closure_unresolved(name: str, scope: _ScopeKey) -> bool:
        for enclosing in reversed(enclosing_functions.get(scope, ())):
            outer = scope_tables.get(enclosing)
            if outer is None or name not in outer.get_identifiers():
                continue
            if outer.lookup(name).is_local():
                return name not in runtime.get(enclosing, set())
        return not has_global_binding(name)

    def unresolved(name: str, scope: _ScopeKey) -> bool:
        kind = scope[0]
        table = scope_tables.get(scope)
        parent = annotation_parents.get(scope)
        if kind == "module":
            return not has_global_binding(name)
        if kind == "comprehension":
            # A comprehension's outermost iterable was recorded in its
            # surrounding scope. Its body closes over functions, never a
            # class namespace, even when the interpreter inlines it.
            return closure_unresolved(name, scope)
        if kind == "annotation" and parent is not None:
            return (
                name not in runtime.get(parent, set())
                and not has_global_binding(name)
            )
        if table is not None and table.get_type() == "class":
            symbol = table.lookup(name) if name in table.get_identifiers() else None
            if symbol is not None and symbol.is_local():
                return (
                    name not in runtime.get(scope, set())
                    and not has_global_binding(name)
                )
            if symbol is not None and symbol.is_free():
                return closure_unresolved(name, scope)
            return not has_global_binding(name)
        if table is not None and table.get_type() == "function":
            symbol = table.lookup(name) if name in table.get_identifiers() else None
            if symbol is not None and symbol.is_local():
                return name not in runtime.get(scope, set())
            if symbol is not None and symbol.is_free():
                return closure_unresolved(name, scope)
        return not has_global_binding(name)

    for scope, scope_loads in loads.items():
        for name, line in scope_loads.items():
            if unresolved(name, scope):
                missing.add((name, line))
    return tuple(sorted(missing))


def _scope_table_base(table: symtable.SymbolTable) -> tuple[str, str, int]:
    kind = table.get_type()
    return getattr(kind, "value", kind), table.get_name(), table.get_lineno()


def _ast_scope_base(node: ast.AST) -> tuple[str, str, int] | None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "function", node.name, node.lineno
    if isinstance(node, ast.ClassDef):
        return "class", node.name, node.lineno
    if isinstance(node, ast.Lambda):
        return "function", "lambda", node.lineno
    labels = {
        ast.ListComp: "listcomp",
        ast.SetComp: "setcomp",
        ast.DictComp: "dictcomp",
        ast.GeneratorExp: "genexpr",
    }
    label = labels.get(type(node))
    return ("function", label, node.lineno) if label else None


def _ast_scope_key(node: ast.AST) -> _ScopeKey:
    base = _ast_scope_base(node)
    if isinstance(node, ast.ClassDef):
        kind = "class"
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        kind = "function"
    else:
        kind = "comprehension"
    if base is None:
        raise TypeError(f"not a scope node: {type(node).__name__}")
    return kind, id(node)


def _scope_tables(
    tree: ast.Module, top: symtable.SymbolTable
) -> tuple[dict[_ScopeKey, symtable.SymbolTable], dict[int, list[symtable.SymbolTable]]]:
    by_base: dict[tuple[str, str, int], list[symtable.SymbolTable]] = {}
    annotation_tables: dict[int, list[symtable.SymbolTable]] = {}

    def collect_tables(table: symtable.SymbolTable) -> None:
        base = _scope_table_base(table)
        by_base.setdefault(base, []).append(table)
        if base[:2] == ("annotation", "__annotate__"):
            annotation_tables.setdefault(base[2], []).append(table)
        for child in table.get_children():
            collect_tables(child)

    collect_tables(top)

    nodes_by_base: dict[tuple[str, str, int], list[ast.AST]] = {}

    def collect_nodes(node: ast.AST) -> None:
        base = _ast_scope_base(node)
        if base is not None:
            nodes_by_base.setdefault(base, []).append(node)
        for child in ast.iter_child_nodes(node):
            collect_nodes(child)

    collect_nodes(tree)
    tables_by_scope = {("module", id(tree)): top}
    for base, nodes in nodes_by_base.items():
        nodes.sort(key=lambda item: (item.lineno, item.col_offset))
        for node, table in zip(nodes, by_base.get(base, ())):
            tables_by_scope[_ast_scope_key(node)] = table
    return tables_by_scope, annotation_tables


def _runtime_loads(
    tree: ast.Module,
    postponed: bool,
    scope_tables: dict[_ScopeKey, symtable.SymbolTable],
    annotation_tables: dict[int, list[symtable.SymbolTable]],
    type_checking_guards: set[int],
) -> tuple[
    dict[_ScopeKey, dict[str, int]],
    dict[_ScopeKey, tuple[_ScopeKey, ...]],
    dict[_ScopeKey, _ScopeKey],
]:
    loads: dict[_ScopeKey, dict[str, int]] = {}
    variable_annotations: dict[_ScopeKey, _ScopeKey] = {}
    scope_functions: dict[_ScopeKey, tuple[_ScopeKey, ...]] = {}
    annotation_parents: dict[_ScopeKey, _ScopeKey] = {}
    module = ("module", id(tree))

    def annotation_scope(
        parent: _ScopeKey,
        owner: ast.AST,
        enclosing_functions: tuple[_ScopeKey, ...],
        variable: bool = False,
    ) -> _ScopeKey:
        if variable and parent in variable_annotations:
            return variable_annotations[parent]
        candidates = annotation_tables.get(owner.lineno, [])
        if candidates:
            table = candidates.pop(0)
            key = ("annotation", id(table) if variable else id(owner))
            scope_tables[key] = table
            annotation_parents[key] = parent
            scope_functions[key] = enclosing_functions
            result = key
        else:
            result = parent
        if variable:
            variable_annotations[parent] = result
        return result

    def visit(
        node: ast.AST,
        scope: _ScopeKey,
        local: frozenset[str] = frozenset(),
        enclosing: tuple[_ScopeKey, ...] = (),
    ) -> None:
        scope_functions.setdefault(scope, enclosing)
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load) and node.id not in local:
                names = loads.setdefault(scope, {})
                names[node.id] = min(names.get(node.id, node.lineno), node.lineno)
            return
        if isinstance(node, ast.AugAssign):
            visit(node.target, scope, local, enclosing)
            if isinstance(node.target, ast.Name) and node.target.id not in local:
                names = loads.setdefault(scope, {})
                names[node.target.id] = min(
                    names.get(node.target.id, node.target.lineno), node.target.lineno
                )
            visit(node.value, scope, local, enclosing)
            return
        if isinstance(node, ast.If):
            visit(node.test, scope, local, enclosing)
            if id(node) not in type_checking_guards:
                for statement in node.body:
                    visit(statement, scope, local, enclosing)
            for statement in node.orelse:
                visit(statement, scope, local, enclosing)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for expr in (*node.decorator_list, *node.args.defaults, *node.args.kw_defaults):
                if expr is not None:
                    visit(expr, scope, local, enclosing)
            if not postponed:
                args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                args.extend(arg for arg in (node.args.vararg, node.args.kwarg) if arg)
                annotation_scope_key = annotation_scope(
                    scope, node, enclosing
                )
                for arg in args:
                    if arg.annotation:
                        visit(arg.annotation, annotation_scope_key, local, enclosing)
                if node.returns:
                    visit(node.returns, annotation_scope_key, local, enclosing)
            child = _ast_scope_key(node)
            child_enclosing = (*enclosing, child)
            scope_functions.setdefault(child, enclosing)
            for statement in node.body:
                visit(statement, child, enclosing=child_enclosing)
            return
        if isinstance(node, ast.ClassDef):
            for expr in (*node.decorator_list, *node.bases, *(kw.value for kw in node.keywords)):
                visit(expr, scope, local, enclosing)
            child = _ast_scope_key(node)
            scope_functions.setdefault(child, enclosing)
            for statement in node.body:
                visit(statement, child, enclosing=enclosing)
            return
        if isinstance(node, ast.Lambda):
            for expr in (*node.args.defaults, *node.args.kw_defaults):
                if expr is not None:
                    visit(expr, scope, local, enclosing)
            child = _ast_scope_key(node)
            child_enclosing = (*enclosing, child)
            scope_functions.setdefault(child, enclosing)
            visit(node.body, child, local, child_enclosing)
            return
        if isinstance(node, ast.AnnAssign):
            visit(node.target, scope, local, enclosing)
            if not postponed and scope[0] not in {"function", "comprehension"}:
                annotation_key = annotation_scope(
                    scope, node, enclosing, variable=True
                )
                visit(node.annotation, annotation_key, local, enclosing)
            if node.value is not None:
                visit(node.value, scope, local, enclosing)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            if not node.generators:
                return
            first = node.generators[0]
            visit(first.iter, scope, local, enclosing)
            inner = _ast_scope_key(node)
            scope_functions.setdefault(inner, enclosing)
            bound = set(local)
            for index, generator in enumerate(node.generators):
                if index:
                    visit(generator.iter, inner, frozenset(bound), enclosing)
                visit(generator.target, inner, frozenset(bound), enclosing)
                bound.update(
                    name.id for name in ast.walk(generator.target)
                    if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store)
                )
                for condition in generator.ifs:
                    visit(condition, inner, frozenset(bound), enclosing)
            values = (node.key, node.value) if isinstance(node, ast.DictComp) else (node.elt,)
            for value in values:
                visit(value, inner, frozenset(bound), enclosing)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, scope, local, enclosing)

    scope_functions[module] = ()
    visit(tree, module)
    return loads, scope_functions, annotation_parents


def _scope_bindings(
    tree: ast.Module,
    postponed: bool,
    tables: dict[_ScopeKey, symtable.SymbolTable],
) -> tuple[dict[_ScopeKey, set[str]], set[int]]:
    module = ("module", id(tree))
    runtime: dict[_ScopeKey, set[str]] = {}
    type_checking_guards: set[int] = set()

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
                self.globals: set[str] = set()
                self.nonlocals: set[str] = set()
                self.guarded = False

            def bind(self, name: str) -> None:
                if not self.guarded:
                    self.runtime.add(name)

            def visit_Name(self, node: ast.Name) -> None:
                if isinstance(node.ctx, ast.Store):
                    self.bind(node.id)

            def visit_AugAssign(self, node: ast.AugAssign) -> None:
                self.visit(node.value)

            def visit_Import(self, node: ast.Import) -> None:
                for alias in node.names:
                    self.bind(alias.asname or alias.name.split(".", 1)[0])

            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
                if node.module != "__future__":
                    for alias in node.names:
                        if alias.name != "*":
                            self.bind(alias.asname or alias.name)

            def visit_If(self, node: ast.If) -> None:
                self.visit(node.test)
                previous = self.guarded
                guarded = is_guard(node.test)
                if guarded:
                    type_checking_guards.add(id(node))
                self.guarded |= guarded
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
                scan(node.body, _ast_scope_key(node), True, tuple(args), inherited)

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self.bind(node.name)
                expressions = (
                    *node.decorator_list, *node.bases, *(kw.value for kw in node.keywords)
                )
                for expr in expressions:
                    self.visit(expr)
                inherited = outer_imports | imports if scope[0] != "class" else outer_imports
                scan(node.body, _ast_scope_key(node), outer_imports=inherited)

            def visit_Lambda(self, node: ast.Lambda) -> None:
                for expr in (*node.args.defaults, *node.args.kw_defaults):
                    if expr is not None:
                        self.visit(expr)
                args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                args.extend(arg for arg in (node.args.vararg, node.args.kwarg) if arg)
                inherited = outer_imports | imports if scope[0] != "class" else outer_imports
                scan(
                    [node.body], _ast_scope_key(node), True,
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
        collector.runtime.difference_update(collector.nonlocals | collector.globals)
        runtime.setdefault(scope, set()).update(collector.runtime)
        runtime.setdefault(module, set()).update(global_runtime)

    scan(tree.body, module)
    return runtime, type_checking_guards
