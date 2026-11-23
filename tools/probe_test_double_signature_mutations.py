#!/usr/bin/env python3
"""Run checker-level probes that restore each t-1788 resolver defect."""

from __future__ import annotations

import inspect
import sys
import tempfile
import textwrap
import types
from pathlib import Path

from tools import test_double_signatures as checker


def load_mutant(name: str, replacements: tuple[tuple[str, str], ...]):
    source = Path(checker.__file__).read_text(encoding="utf-8")
    for current, mutated in replacements:
        assert source.count(current) == 1, current
        source = source.replace(current, mutated, 1)
    module_name = f"_test_double_signature_mutant_{name}"
    module = types.ModuleType(module_name)
    module.__file__ = checker.__file__
    sys.modules[module_name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def scan_fixture(module, filename: str, source: str):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / filename).write_text(textwrap.dedent(source), encoding="utf-8")
        return module.scan_tests(root)


def probe_variadic_collision() -> None:
    production = inspect.signature(lambda *args, **kwargs: None)
    double = inspect.signature(lambda value=None, *args, **kwargs: None)
    assert not checker._compatible(production, double)
    mutant = load_mutant(
        "variadic",
        ((
            "    if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in production_params) and any(\n"
            "        p.kind is inspect.Parameter.VAR_KEYWORD for p in production_params\n"
            "    ):\n"
            "        double_positional = [\n"
            "            p for p in double_params\n"
            "            if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD\n"
            "        ]\n"
            "        for index, param in enumerate(double_positional):\n"
            "            shapes.append(([object() for _ in range(index + 1)], {param.name: object()}))\n",
            "",
        ),),
    )
    assert mutant._compatible(production, double)
    print("PASS mutation: variadic argument collision is detected")


def importorskip_fixture() -> str:
    return """
        import pytest
        module_alias = pytest.importorskip('collections')
        def replacement(iterable=(), /, **kwargs): return None
        def test_patch(monkeypatch):
            monkeypatch.setattr(module_alias, 'Counter', replacement)
    """


def probe_class_only_constructor_inference() -> None:
    census = scan_fixture(checker, "test_alias.py", importorskip_fixture())
    assert census.scanned == census.resolved == 1
    assert not census.skipped

    mutant = load_mutant(
        "class_constructor",
        ((
            "            if constructor is not None and _is_class_constructor(\n"
            "                constructor, call, parents, modules, objects\n"
            "            ):\n",
            "            if constructor is not None:\n",
        ),),
    )
    mutated = scan_fixture(mutant, "test_alias.py", importorskip_fixture())
    assert mutated.resolved == 0 and len(mutated.skipped) == 1
    print("PASS mutation: non-class importorskip callee is not a constructor")


def probe_importorskip_alias() -> None:
    alias_block = (
        "        elif isinstance(node, (ast.Assign, ast.AnnAssign)):\n"
        "            value = node.value\n"
        "            if (\n"
        "                isinstance(value, ast.Call)\n"
        "                and _dotted(value.func) in {\"pytest.importorskip\", \"importorskip\"}\n"
        "                and value.args\n"
        "                and isinstance(value.args[0], ast.Constant)\n"
        "                and isinstance(value.args[0].value, str)\n"
        "            ):\n"
        "                targets = node.targets if isinstance(node, ast.Assign) else [node.target]\n"
        "                for target in targets:\n"
        "                    if isinstance(target, ast.Name):\n"
        "                        modules[target.id] = value.args[0].value\n"
    )
    mutant = load_mutant("importorskip_alias", ((alias_block, ""),))
    mutated = scan_fixture(mutant, "test_alias.py", importorskip_fixture())
    assert mutated.resolved == 0 and len(mutated.skipped) == 1
    print("PASS mutation: pytest.importorskip module alias is resolved")


def classmethod_fixture() -> str:
    return """
        from collections import UserDict
        def replacement(value=None): return None
        def test_patch(monkeypatch):
            mapping = UserDict()
            monkeypatch.setattr(mapping, 'fromkeys', replacement)
    """


def probe_bound_classmethod() -> None:
    census = scan_fixture(checker, "test_classmethod.py", classmethod_fixture())
    assert census.resolved == 1 and len(census.violations) == 1
    mutant = load_mutant(
        "classmethod",
        ((
            "                if bound_target and inspect.isfunction(value):\n",
            "                if bound_target:\n",
        ),),
    )
    mutated = scan_fixture(mutant, "test_classmethod.py", classmethod_fixture())
    assert mutated.resolved == 1 and not mutated.violations
    print("PASS mutation: already-bound classmethod keeps its first parameter")


if __name__ == "__main__":
    probe_variadic_collision()
    probe_class_only_constructor_inference()
    probe_importorskip_alias()
    probe_bound_classmethod()
