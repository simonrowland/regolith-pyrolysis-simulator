#!/usr/bin/env python3
"""Store an existing runner payload as a report-viewer run artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.accounting.run_artifact import build_run_artifact
from web.run_store import RunArtifactStore


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill a runner JSON payload into the web run store.",
    )
    parser.add_argument("payload", type=Path, help="Runner payload JSON path")
    parser.add_argument("run_id", help="Run ID used by /api/runs/<run-id>")
    parser.add_argument("--name", default=None, help="Display name for the run")
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=ROOT / "instance" / "runs",
        help="Run store directory (default: instance/runs)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        with args.payload.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("runner payload must be a JSON object")
        artifact = build_run_artifact(payload, run_id=args.run_id, name=args.name)
        store = RunArtifactStore(args.runs_dir)
        store.save(args.run_id, artifact)
        summary = next(
            row for row in store.list_runs() if row["run_id"] == args.run_id
        )
    except (FileExistsError, OSError, json.JSONDecodeError, ValueError) as exc:
        parser.exit(1, f"backfill_run_artifact.py: error: {exc}\n")

    print(json.dumps({"stored_run_id": args.run_id, "summary": summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
