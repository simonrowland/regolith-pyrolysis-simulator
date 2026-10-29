#!/usr/bin/env python3
"""Generate exact measured runner moves and their JSON class summary.

--out <stem>.jsonl[.gz] writes rows plus <stem>.json; --out <stem>.json
writes the summary plus <stem>.jsonl. --base/--tip accept revisions or producer
directories. No fixtures are written and no physics values are invented.

--class-map is JSON: {"offer":"id", "classes":{"cause-id":{"cause":"why",
"owner_decision":"d-id", "summary":"consequences"}}, "rules":[{"cause":"cause-id"}]}.
Rules match in order, with optional artifact, path_prefix, old_kind/new_kind
(lists of the owner's token kinds). Every measured row must match a rule.
The class map supplies attribution, never old/new values or approval authority.
"""
import argparse
import gzip
import hashlib
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.golden_sections import (
    COVERAGE_EXCEPTION, json_bytes, load_json, make_manifest, runner_payloads, source_id,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--tip", required=True)
    parser.add_argument("--class-map", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(COVERAGE_EXCEPTION)
    output = args.out
    if output.name.endswith((".jsonl", ".jsonl.gz")):
        summary = output.with_name(output.name.removesuffix(".gz").removesuffix(".jsonl") + ".json")
    elif output.suffix == ".json":
        summary, output = output, output.with_suffix(".jsonl")
    else:
        parser.error("--out must end in .json, .jsonl or .jsonl.gz")
    try:
        old, new = runner_payloads(args.base), runner_payloads(args.tip)
        manifest, raw = make_manifest(old, new, load_json(args.class_map.read_bytes()),
                                      base_green=source_id(args.base, old), expected_diff=output.name)
        if output.suffix == ".gz":
            raw = gzip.compress(raw, mtime=0)
        summary.write_bytes(json_bytes(manifest))
        output.write_bytes(raw)
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for path in (summary, output):
        print(f"SHA256 {hashlib.sha256(path.read_bytes()).hexdigest()} {path}")
    print("CLASSES " + str({k: c["count"] for k, c in manifest["classes"].items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
