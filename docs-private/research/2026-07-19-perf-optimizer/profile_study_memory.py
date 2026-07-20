from __future__ import annotations

import gc
import json
from pathlib import Path
import sys
import time
import tracemalloc

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.optimize.cli import main as optimizer_main


def main() -> int:
    collections = [0, 0, 0]

    def collect(phase: str, info: dict[str, int]) -> None:
        if phase == "stop":
            collections[info["generation"]] += 1

    gc.callbacks.append(collect)
    tracemalloc.start()
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    try:
        status = optimizer_main(sys.argv[1:])
    finally:
        wall_s = time.perf_counter() - wall_start
        cpu_s = time.process_time() - cpu_start
        current, peak = tracemalloc.get_traced_memory()
        gc.callbacks.remove(collect)
        print(
            "PROFILE_MEMORY "
            + json.dumps(
                {
                    "wall_s": wall_s,
                    "parent_cpu_s": cpu_s,
                    "parent_cpu_pct": 100.0 * cpu_s / wall_s,
                    "traced_current_bytes": current,
                    "traced_peak_bytes": peak,
                    "gc_collections": collections,
                },
                sort_keys=True,
            )
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
