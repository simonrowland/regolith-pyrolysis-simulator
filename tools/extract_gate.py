#!/usr/bin/env python3
"""Run deterministic mechanical checks for one literature extract."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from contextlib import contextmanager
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from typing import Iterator

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _tree_modules(
    tree: Path,
) -> Iterator[tuple[ModuleType, ModuleType, ModuleType, object]]:
    """Load migration, validation, and YAML helpers exclusively from ``tree``."""
    required = (
        tree / "simulator/battery/migrate.py",
        tree / "simulator/yaml_cache.py",
        tree / "tools/validate_literature_extracts.py",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ImportError("tree is missing required modules: " + ", ".join(missing))

    original_path = list(sys.path)
    original_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "simulator"
        or name.startswith("simulator.")
        or name == "tools"
        or name.startswith("tools.")
    }
    for name in original_modules:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(tree))
    try:
        migrate = importlib.import_module("simulator.battery.migrate")
        validator = importlib.import_module("tools.validate_literature_extracts")
        yaml_cache = importlib.import_module("simulator.yaml_cache")
        for module in (migrate, validator, yaml_cache):
            if not Path(module.__file__).resolve().is_relative_to(tree):
                raise ImportError(
                    f"{module.__name__} resolved outside --tree: {module.__file__}"
                )
        source_issues = getattr(yaml_cache, "yaml_mapping_source_issues", None)
        if source_issues is None:
            source_issues = _runner_yaml_source_issues()

        gate_path = REPO_ROOT / "simulator/battery/extract_gate.py"
        spec = importlib.util.spec_from_file_location(
            "_regolith_extract_gate_runtime", gate_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load extract gate implementation at {gate_path}")
        gate = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = gate
        try:
            spec.loader.exec_module(gate)
        finally:
            sys.modules.pop(spec.name, None)
        if gate.migrate is not migrate:
            raise ImportError("extract gate did not bind the migrator from --tree")
        yield gate, validator, yaml_cache, source_issues
    finally:
        sys.path[:] = original_path
        for name in tuple(sys.modules):
            if (
                name == "simulator"
                or name.startswith("simulator.")
                or name == "tools"
                or name.startswith("tools.")
            ):
                sys.modules.pop(name, None)
        sys.modules.update(original_modules)


def _runner_yaml_source_issues():
    """Use this gate version's YAML source guard on trees predating the helper."""
    source = REPO_ROOT / "simulator/yaml_cache.py"
    spec = importlib.util.spec_from_file_location(
        "_regolith_yaml_source_guard", source
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load YAML source guard from {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    checker = getattr(module, "yaml_mapping_source_issues", None)
    if checker is None:
        raise ImportError(f"YAML source guard is missing from {source}")
    return checker


def _tree_head(path: Path) -> tuple[Path, str]:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"cannot resolve git tree at {path}: {result.stderr.strip()}")
    root, head = result.stdout.splitlines()
    return Path(root).resolve(), head


def _run_one(
    path: Path,
    corpus: Path,
    pre_policy_ids: set[str],
    *,
    gate: ModuleType,
    validator: ModuleType,
    yaml_cache: ModuleType,
    yaml_source_issues,
) -> bool:
    path = path.resolve()
    try:
        raw = path.read_text(encoding="utf-8")
        doc = yaml_cache.load_cached_safe_yaml(raw)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        print(f"HARD yaml-parse: {type(exc).__name__}: {exc}")
        return True

    errors = validator.validate_extract_document(
        doc,
        path=path,
        expected_source_id=path.stem,
        pre_policy_ids=pre_policy_ids,
    )
    if isinstance(doc, Mapping):
        errors.extend(validator.check_all_fidelity_samples_match(doc, label=path.stem))
    yaml_issues = yaml_source_issues(raw)
    report = gate.evaluate_extract(
        path,
        doc,
        corpus=corpus,
        validator_errors=errors,
        yaml_issues=yaml_issues,
    )

    for finding in report.findings:
        print(f"{finding.severity} {finding.check}: {finding.detail}")
    kinds = ", ".join(
        f"{kind}={count}" for kind, count in report.value_kinds.items()
    ) or "none"
    print(
        f"MIGRATION observations by ValueKind: {kinds}; "
        f"hard issues={report.migration_hard_issues}"
    )
    refusals = ", ".join(
        f"{reason}={count}" for reason, count in report.refusal_reasons.items()
    ) or "none"
    print(f"MIGRATION refusal reasons: {refusals}")
    for table in report.tables:
        print(
            f"TABLE {table.table}: {table.percent:.1f}% "
            f"({table.extract_values}/{table.numeric_cells} numeric values)"
        )
    if not report.findings:
        print("PASS: no failed checks")
    return report.hard_failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extract", nargs="?", type=Path, help="one literature extract")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--ids",
        nargs="+",
        metavar="SOURCE_ID",
        help="audit these extract filename stems from the repository",
    )
    selection.add_argument(
        "--changed-since",
        metavar="REF",
        help="audit extract YAML files changed between REF and HEAD",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(os.environ["REGOLITH_CORPUS"])
        if os.environ.get("REGOLITH_CORPUS")
        else None,
        help="corpus root containing raw/<source_id>/ocr-mineru (or set REGOLITH_CORPUS)",
    )
    parser.add_argument(
        "--tree",
        type=Path,
        help="code tree to audit (default: git toplevel of the extract or this tool)",
    )
    args = parser.parse_args(argv)
    if args.corpus is None:
        parser.error("--corpus or REGOLITH_CORPUS is required")

    if args.extract and (args.ids or args.changed_since):
        parser.error("provide either an extract or one audit mode, not both")

    tree_hint = args.tree
    if tree_hint is None:
        tree_hint = args.extract.resolve().parent if args.extract else REPO_ROOT
    try:
        tree, head = _tree_head(tree_hint)
    except (OSError, ValueError) as exc:
        print(f"HARD tree-import: {exc}")
        return 1
    print(f"TREE {tree} HEAD {head}")

    if args.ids:
        extracts = tree / "data" / "literature" / "extracts"
        paths = []
        for source_id in args.ids:
            candidate = Path(source_id)
            stem = (
                candidate.stem
                if candidate.suffix.lower() in {".yaml", ".yml"}
                else source_id
            )
            paths.append(extracts / f"{stem}.yaml")
    elif args.changed_since:
        result = subprocess.run(
            [
                "git",
                "diff",
                "--name-only",
                f"{args.changed_since}...HEAD",
                "--",
                "data/literature/extracts",
            ],
            cwd=tree,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            print(f"HARD changed-since: {result.stderr.strip()}")
            return 1
        paths = [
            tree / relative
            for relative in result.stdout.splitlines()
            if Path(relative).suffix.lower() in {".yaml", ".yml"}
            and not Path(relative).name.startswith("_")
            and (tree / relative).is_file()
        ]
    elif args.extract:
        paths = [args.extract]
    else:
        parser.error("provide an extract, --ids, or --changed-since")

    module_context = _tree_modules(tree)
    try:
        gate, validator, yaml_cache, yaml_source_issues = module_context.__enter__()
    except (ImportError, ModuleNotFoundError, AttributeError) as exc:
        print(f"HARD tree-import: {type(exc).__name__}: {exc}")
        return 1

    try:
        hard_failed = False
        pre_policy_ids = validator.pre_policy_source_ids() if paths else set()
        for path in paths:
            if len(paths) > 1 or args.ids or args.changed_since:
                print(f"EXTRACT {path}")
            if not path.is_file():
                print(f"HARD extract: file not found: {path}")
                hard_failed = True
                continue
            hard_failed = _run_one(
                path,
                args.corpus,
                pre_policy_ids,
                gate=gate,
                validator=validator,
                yaml_cache=yaml_cache,
                yaml_source_issues=yaml_source_issues,
            ) or hard_failed
        if not paths:
            print("PASS: no changed extract YAML files")
        return int(hard_failed)
    finally:
        module_context.__exit__(None, None, None)


if __name__ == "__main__":
    raise SystemExit(main())
