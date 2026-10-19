#!/usr/bin/env python3
"""Run deterministic mechanical checks for one literature extract."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import os
from pathlib import Path
import subprocess
import sys

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.battery.extract_gate import evaluate_extract
from simulator.yaml_cache import load_cached_safe_yaml, yaml_mapping_source_issues
from tools import validate_literature_extracts as validator


def _run_one(path: Path, corpus: Path, pre_policy_ids: set[str]) -> bool:
    path = path.resolve()
    try:
        raw = path.read_text(encoding="utf-8")
        doc = load_cached_safe_yaml(raw)
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
    yaml_issues = yaml_mapping_source_issues(raw)
    report = evaluate_extract(
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
    args = parser.parse_args(argv)
    if args.corpus is None:
        parser.error("--corpus or REGOLITH_CORPUS is required")

    if args.extract and (args.ids or args.changed_since):
        parser.error("provide either an extract or one audit mode, not both")

    if args.ids:
        extracts = REPO_ROOT / "data" / "literature" / "extracts"
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
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            print(f"HARD changed-since: {result.stderr.strip()}")
            return 1
        paths = [
            REPO_ROOT / relative
            for relative in result.stdout.splitlines()
            if Path(relative).suffix.lower() in {".yaml", ".yml"}
            and not Path(relative).name.startswith("_")
            and (REPO_ROOT / relative).is_file()
        ]
    elif args.extract:
        paths = [args.extract]
    else:
        parser.error("provide an extract, --ids, or --changed-since")

    hard_failed = False
    pre_policy_ids = validator.pre_policy_source_ids() if paths else set()
    for path in paths:
        if len(paths) > 1 or args.ids or args.changed_since:
            print(f"EXTRACT {path}")
        if not path.is_file():
            print(f"HARD extract: file not found: {path}")
            hard_failed = True
            continue
        hard_failed = _run_one(path, args.corpus, pre_policy_ids) or hard_failed
    if not paths:
        print("PASS: no changed extract YAML files")
    return int(hard_failed)


if __name__ == "__main__":
    raise SystemExit(main())
