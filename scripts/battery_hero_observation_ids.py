#!/usr/bin/env python3
"""Write observation IDs loaded from the battery store at a Git commit.

The baseline mirrors ``load_migrated_store``: top-level ``*.yaml`` files and
one-level YAML shards in both store directories, read with ``git ls-tree`` and
``git show`` without making a checkout.
"""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

import yaml


STORE_PATHS = (
    "data/literature/extracts-v2",
    "data/literature/observations-v2",
)


def _store_yaml_paths(repo: Path, commit: str) -> list[str]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "ls-tree",
            "-r",
            "-z",
            "--name-only",
            commit,
            "--",
            *STORE_PATHS,
        ],
        check=True,
        capture_output=True,
    )
    paths: list[str] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8")
        for store in STORE_PATHS:
            prefix = store + "/"
            if not path.startswith(prefix):
                continue
            parts = PurePosixPath(path[len(prefix):]).parts
            if len(parts) == 1 and parts[0].endswith(".yaml"):
                paths.append(path)
            elif (
                len(parts) == 2
                and parts[1].endswith(".yaml")
                and not parts[0].startswith((".", "_"))
                and not parts[0].endswith("-reports")
            ):
                paths.append(path)
            break
    return sorted(paths)


def observation_ids_at_commit(repo: Path, commit: str) -> set[str]:
    ids: set[str] = set()
    for path in _store_yaml_paths(repo, commit):
        document = subprocess.run(
            ["git", "-C", str(repo), "show", f"{commit}:{path}"],
            check=True,
            capture_output=True,
        ).stdout
        payload = yaml.safe_load(document)
        observations = payload.get("observations") if isinstance(payload, Mapping) else None
        if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
            continue
        for observation in observations:
            if isinstance(observation, Mapping) and observation.get("observation_id"):
                ids.add(str(observation["observation_id"]))
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ids = observation_ids_at_commit(args.repo, args.commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(f"{observation_id}\n" for observation_id in sorted(ids)), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
