"""Symtable-based undefined-name check shared by repository meta-tests."""

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


def undefined_names(path: Path) -> tuple[tuple[str, int], ...]:
    source = path.read_text(encoding="utf-8")
    top = symtable.symtable(source, str(path), "exec")
    tree = ast.parse(source, filename=str(path))
    load_lines: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            load_lines.setdefault(node.id, node.lineno)
    defined = {
        symbol.get_name()
        for symbol in top.get_symbols()
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()
    }
    missing: dict[str, int] = {}

    def walk(table: symtable.SymbolTable) -> None:
        for symbol in table.get_symbols():
            name = symbol.get_name()
            if (
                symbol.is_global()
                or (
                    table.get_type() == "module"
                    and symbol.is_referenced()
                    and not symbol.is_assigned()
                    and not symbol.is_imported()
                )
            ) and name not in defined and not hasattr(builtins, name) and name not in _IMPLICIT:
                missing.setdefault(name, load_lines.get(name, 1))
        for child in table.get_children():
            walk(child)

    walk(top)
    return tuple(sorted((name, line) for name, line in missing.items()))
