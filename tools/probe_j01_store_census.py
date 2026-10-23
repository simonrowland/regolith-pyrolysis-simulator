#!/usr/bin/env python3
"""Time the j01 store census and compare its canonical output across revisions."""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import importlib.util
import json
import os
import pstats
import statistics
import sys
import time
from pathlib import Path


REPO_ROOT = Path(
    os.environ.get("J01_CENSUS_REPO_ROOT", Path(__file__).resolve().parents[1])
).resolve()
TEST_MODULE_PATH = REPO_ROOT / "tests" / "battery" / "test_migrate.py"
sys.path.insert(0, str(REPO_ROOT))
spec = importlib.util.spec_from_file_location("j01_census_test_migrate", TEST_MODULE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load {TEST_MODULE_PATH}")
test_migrate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = test_migrate
spec.loader.exec_module(test_migrate)


def census_bytes() -> bytes:
    extracts = REPO_ROOT / "data" / "literature" / "extracts"
    extracts_v2 = REPO_ROOT / "data" / "literature" / "extracts-v2"
    result = test_migrate._series_census(extracts, extracts_v2)
    return json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    refs = parser.add_mutually_exclusive_group()
    refs.add_argument("--save-reference", type=Path)
    refs.add_argument("--compare-reference", type=Path)
    parser.add_argument("--profile", action="store_true", help="profile one extra census call")
    args = parser.parse_args()

    reference = args.compare_reference.read_bytes() if args.compare_reference else None
    outputs: list[bytes] = []
    cpu_seconds: list[float] = []
    for repeat in range(1, 6):
        started = time.process_time()
        output = census_bytes()
        cpu = time.process_time() - started
        cpu_seconds.append(cpu)
        outputs.append(output)
        if reference is not None and output != reference:
            raise SystemExit(f"repeat {repeat}: census output differs from reference")
        if repeat > 1 and output != outputs[0]:
            raise SystemExit(f"repeat {repeat}: census output differs from repeat 1")
        print(f"repeat {repeat}: process_cpu_seconds={cpu:.3f}")

    if args.save_reference:
        args.save_reference.write_bytes(outputs[0])
    digest = hashlib.sha256(outputs[0]).hexdigest()
    median = statistics.median(cpu_seconds)
    mad = statistics.median(abs(cpu - median) for cpu in cpu_seconds)
    print(
        f"output_bytes={len(outputs[0])} sha256={digest} "
        f"median_process_cpu_seconds={median:.3f} "
        f"mad_seconds={mad:.3f} range_seconds="
        f"{min(cpu_seconds):.3f}-{max(cpu_seconds):.3f}"
    )
    if args.compare_reference:
        print(f"byte_identical_to={args.compare_reference}")
    elif args.save_reference:
        print(f"reference_written={args.save_reference}")

    if args.profile:
        profile = cProfile.Profile()
        profile.enable()
        profiled_output = census_bytes()
        profile.disable()
        if profiled_output != outputs[0]:
            raise SystemExit("profiled census output differs from timed output")
        print("cProfile cumulative top 30 (one additional run):")
        pstats.Stats(profile).strip_dirs().sort_stats("cumulative").print_stats(30)


if __name__ == "__main__":
    main()
