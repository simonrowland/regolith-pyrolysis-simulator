#!/usr/bin/env python3
"""Denylist-safe cleanup for legacy grind output directories."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


ALLOWLIST_PATTERNS: tuple[str, ...] = (
    "cache-grind-collected",
    "dose-exploration-*",
    "grind-c6-*",
    "grind-smoke",
    "recipe-db-collected",
    "regolith-cache-archive-*",
)
PROTECTED_REPO_NAMES = {".git", ".venv", "docs-private"}
REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    matched_pattern: str


@dataclass(frozen=True)
class CleanupRefusal:
    path: Path
    reason: str


@dataclass(frozen=True)
class CleanupPlan:
    candidates: tuple[CleanupCandidate, ...]
    refusals: tuple[CleanupRefusal, ...]


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def matched_allowlist_pattern(path: Path) -> str | None:
    name = path.name
    for pattern in ALLOWLIST_PATTERNS:
        if fnmatch.fnmatchcase(name, pattern):
            return pattern
    return None


def protected_paths(repo_root: Path | None = None) -> tuple[Path, ...]:
    root = (repo_root or REPO_ROOT).resolve(strict=False)
    return (
        root,
        root / ".git",
        root / ".venv",
        root / "docs-private",
    )


def denylist_reason(path: Path, *, repo_root: Path | None = None) -> str | None:
    raw_name = path.name
    if raw_name.startswith("."):
        return "home dotfile/dotdir"
    if raw_name in PROTECTED_REPO_NAMES:
        return "repo protected directory"
    if path.is_symlink():
        return "symlink"
    if not path.is_dir():
        return "not a directory"

    resolved = path.resolve(strict=False)
    for protected in protected_paths(repo_root):
        protected_resolved = protected.resolve(strict=False)
        if resolved == protected_resolved or _is_relative_to(resolved, protected_resolved):
            return f"protected path: {protected}"
    nested_reason = nested_protected_reason(path)
    if nested_reason is not None:
        return nested_reason
    return None


def nested_protected_reason(path: Path) -> str | None:
    for root, dirnames, filenames in os.walk(path, topdown=True, followlinks=False):
        protected_names = PROTECTED_REPO_NAMES.intersection(dirnames).union(filenames)
        if protected_names:
            protected = sorted(protected_names)[0]
            return f"contains protected path: {Path(root) / protected}"
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if not (Path(root) / dirname).is_symlink()
        ]
    return None


def build_cleanup_plan(
    search_roots: Sequence[Path | str],
    *,
    repo_root: Path | None = None,
) -> CleanupPlan:
    candidates: list[CleanupCandidate] = []
    refusals: list[CleanupRefusal] = []

    for search_root_value in search_roots:
        search_root = Path(search_root_value).expanduser()
        if not search_root.exists():
            refusals.append(CleanupRefusal(search_root, "search root does not exist"))
            continue
        if not search_root.is_dir():
            refusals.append(CleanupRefusal(search_root, "search root is not a directory"))
            continue

        for child in sorted(search_root.iterdir(), key=lambda item: item.name):
            matched_pattern = matched_allowlist_pattern(child)
            if matched_pattern is None:
                continue
            reason = denylist_reason(child, repo_root=repo_root)
            if reason is not None:
                refusals.append(CleanupRefusal(child, reason))
                continue
            candidates.append(CleanupCandidate(child, matched_pattern))

    return CleanupPlan(tuple(candidates), tuple(refusals))


def delete_candidate(candidate: CleanupCandidate) -> None:
    reason = denylist_reason(candidate.path)
    if reason is not None:
        raise RuntimeError(f"refusing to delete {candidate.path}: {reason}")
    if matched_allowlist_pattern(candidate.path) is None:
        raise RuntimeError(f"refusing to delete non-allowlisted path: {candidate.path}")
    shutil.rmtree(candidate.path)


def _remote_args(args: argparse.Namespace) -> list[str]:
    remote: list[str] = []
    for root in args.search_root or []:
        remote.extend(["--search-root", str(root)])
    if args.yes:
        remote.append("--yes")
    if args.json:
        remote.append("--json")
    return remote


def run_remote(args: argparse.Namespace) -> int:
    script = Path(__file__).read_text(encoding="utf-8")
    completed = subprocess.run(
        ["ssh", args.host, "python3", "-", *_remote_args(args)],
        input=script,
        text=True,
        check=False,
    )
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find and optionally delete legacy grind output directories.",
    )
    parser.add_argument(
        "--search-root",
        action="append",
        type=Path,
        default=None,
        help="directory whose immediate children should be scanned; default: HOME",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="delete selected directories; without this flag the command is dry-run only",
    )
    parser.add_argument(
        "--host",
        help="ssh alias on which to run the same cleanup command",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print a machine-readable cleanup plan",
    )
    return parser


def _plan_payload(plan: CleanupPlan, *, dry_run: bool) -> dict[str, object]:
    return {
        "dry_run": dry_run,
        "allowlist_patterns": list(ALLOWLIST_PATTERNS),
        "candidates": [
            {"path": str(candidate.path), "matched_pattern": candidate.matched_pattern}
            for candidate in plan.candidates
        ],
        "refusals": [
            {"path": str(refusal.path), "reason": refusal.reason}
            for refusal in plan.refusals
        ],
    }


def print_plan(plan: CleanupPlan, *, dry_run: bool) -> None:
    mode = "DRY-RUN" if dry_run else "DELETE"
    print(f"grind-cleanup: mode={mode}")
    print("allowlist: " + ", ".join(ALLOWLIST_PATTERNS))
    if plan.candidates:
        action = "WOULD_DELETE" if dry_run else "DELETE"
        for candidate in plan.candidates:
            print(f"{action} {candidate.path} pattern={candidate.matched_pattern}")
    else:
        print("no allowed grind output dirs found")
    for refusal in plan.refusals:
        print(f"REFUSE {refusal.path} reason={refusal.reason}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.host:
        return run_remote(args)

    search_roots = args.search_root or [Path.home()]
    plan = build_cleanup_plan(search_roots)
    dry_run = not args.yes

    if args.json:
        print(json.dumps(_plan_payload(plan, dry_run=dry_run), indent=2, sort_keys=True))
    else:
        print_plan(plan, dry_run=dry_run)

    if plan.refusals:
        return 2
    if dry_run:
        return 0

    for candidate in plan.candidates:
        delete_candidate(candidate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
