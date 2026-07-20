from __future__ import annotations

import cProfile
from pathlib import Path
import sys

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.optimize.evaluate import evaluate
from simulator.optimize.recipe import RecipePatch
import simulator.melt_backend.thermoengine as thermoengine_backend


def main() -> int:
    thermoengine_backend.THERMOENGINE_WARM_CALL_TIMEOUT_S = 120.0
    profile = yaml.safe_load(
        (REPO_ROOT / "data/optimize_profiles/lunar_mare_low_ti.yaml").read_text()
    )
    profile["feedstock"] = "lunar_highland"
    profile["profile_id"] = "perf-real-engine-lunar-highland"
    profile["fidelities"]["high"] = {
        "backend_name": "thermoengine",
        "hours": 1,
    }
    profiler = cProfile.Profile()
    try:
        result = profiler.runcall(
            evaluate,
            RecipePatch({}),
            profile["feedstock"],
            "high",
            profile=profile,
            candidate_id="real-engine-profile",
        )
    finally:
        profiler.dump_stats(sys.argv[1])
    print(result.candidate_id, result.feasible, result.cache_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
