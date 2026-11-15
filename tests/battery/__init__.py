"""Standalone schema v2.1 tests. Not wired into calibration_battery."""

from __future__ import annotations

import ast
from copy import deepcopy
import importlib.util
from pathlib import Path

_BUILDER = None


def _literature_index_builder(root: Path):
    global _BUILDER
    if _BUILDER is None:
        path = root / "data" / "literature" / "build_index.py"
        spec = importlib.util.spec_from_file_location(
            "literature_index_store_summary", path
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _BUILDER = module
    return _BUILDER


def load_observation_store_summary(root: Path) -> dict[str, dict]:
    return _literature_index_builder(root).load_observation_store_summary(root)


def compilation_shard_observation_count(
    root: Path, paths: list[Path], obs_dir: Path
) -> int:
    """Serve compilation observation_id counts from the derived summary.

    Size-matched against the live files. Does not open shard payloads.
    """
    summary = load_observation_store_summary(root)
    total = 0
    for path in paths:
        rel = path.relative_to(obs_dir).as_posix()
        cached = summary.get(rel)
        assert cached is not None, f"observation_store_summary missing {rel}"
        assert cached["size"] == path.stat().st_size, rel
        total += cached["observation_id_count"]
    return total


def compilation_generator_dispatch(
    source: str,
    *,
    predicate: str,
    generator_module: str,
    generator_name: str,
    source_path_keyword: bool = False,
) -> None:
    """Guard a source's wiring to its generator-backed compilation lift."""
    tree = ast.parse(source)

    def dispatch_nodes(candidate: ast.Module) -> tuple[ast.If, ast.Call] | None:
        migrator = next(
            (
                node
                for node in candidate.body
                if isinstance(node, ast.ClassDef) and node.name == "Migrator"
            ),
            None,
        )
        if migrator is None:
            return None
        migrate = next(
            (
                node
                for node in migrator.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "_migrate_compilation_file"
            ),
            None,
        )
        if migrate is None:
            return None
        branches = [
            node
            for node in ast.walk(migrate)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Call)
            and isinstance(node.test.func, ast.Name)
            and node.test.func.id == predicate
        ]
        if len(branches) != 1:
            return None
        branch = branches[0]
        imports = [
            alias
            for node in ast.walk(branch)
            if isinstance(node, ast.ImportFrom) and node.module == generator_module
            for alias in node.names
            if alias.name == generator_name
        ]
        calls = [
            node
            for node in ast.walk(branch)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr == "_lift_generator_backed_compilation"
        ]
        if len(imports) != 1 or len(calls) != 1:
            return None
        return branch, calls[0]

    def matches(candidate: ast.Module) -> bool:
        result = dispatch_nodes(candidate)
        if result is None:
            return False
        _, call = result
        expected_args = ("work", "rel", "count", "doc", generator_name)
        if len(call.args) != len(expected_args) or any(
            not isinstance(arg, ast.Name) or arg.id != expected
            for arg, expected in zip(call.args, expected_args)
        ):
            return False
        if source_path_keyword:
            return (
                len(call.keywords) == 1
                and call.keywords[0].arg == "source_path"
                and isinstance(call.keywords[0].value, ast.Name)
                and call.keywords[0].value.id == "rel"
            )
        return not call.keywords

    assert matches(tree), (
        f"{predicate} must pass {generator_module}.{generator_name} and rel "
        "to _lift_generator_backed_compilation"
    )

    # Exercise the two wiring failures this guard prevents against AST mutants.
    wrong_generator = deepcopy(tree)
    result = dispatch_nodes(wrong_generator)
    assert result is not None
    _, call = result
    call.args[4] = ast.Name(id="wrong_generator", ctx=ast.Load())
    assert not matches(wrong_generator), f"{predicate} accepted a wrong generator"

    lost_path = deepcopy(tree)
    result = dispatch_nodes(lost_path)
    assert result is not None
    _, call = result
    call.args[1] = ast.Constant(value=None)
    if source_path_keyword:
        call.keywords.clear()
    assert not matches(lost_path), f"{predicate} accepted a lost source path"
