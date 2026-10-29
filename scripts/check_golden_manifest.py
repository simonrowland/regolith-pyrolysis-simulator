#!/usr/bin/env python3
"""Check exact runner D == composed M. This does not grant value approval.

Pass each offer's JSON summary (or <stem>.jsonl[.gz], with <stem>.json
alongside it) in merge order. SHA256 lines cover summary and exact row bytes.
Required-engine admission and digest-bound approval lookup are train gates.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.golden_sections import COVERAGE_EXCEPTION, compare_manifests, load_manifest, runner_payloads


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--tip", required=True)
    parser.add_argument("--manifest", required=True, action="append", type=Path)
    args = parser.parse_args()
    print(COVERAGE_EXCEPTION)
    try:
        offers = []
        for path in args.manifest:
            manifest, rows, identities = load_manifest(path)
            for name, digest in identities.items():
                print(f"SHA256 {digest} {name}")
            offers.append((manifest, rows))
        report = compare_manifests(runner_payloads(args.base), runner_payloads(args.tip), offers)
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for kind in ("UNDECLARED", "MISSING", "WRONG-OLD", "OVERLAP", "CANCELLED"):
        entries = report[kind]
        for entry in entries[:20]:
            print(f"{kind} {json.dumps(entry, allow_nan=False)}")
        print(f"{kind}: count={len(entries)} shown={min(20, len(entries))}")
    print("DECLARED-CLASSES " + json.dumps(report["classes"]))
    print("D-CLASSES " + json.dumps(report["observed_classes"]))
    print("CHAINS " + json.dumps(report["chains"][:20]))
    print(f"CHAINS: count={len(report['chains'])} shown={min(20, len(report['chains']))}")
    print(f"D={report['observed_count']} E={report['expected_count']} exact={'yes' if report['ok'] else 'no'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
