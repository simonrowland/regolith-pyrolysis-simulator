"""Profile one warm single-process score of 30000 observations.

No arguments. The import root is the current working directory. The
controller runs this on a studio. It calls battery_score.main with
--workers 1 and --limit 30000 and the scorer's own engine-cache path.
--limit writes a subset under data/battery/subsets/. That file is not
the published residuals.jsonl.

Reports process CPU inside and outside the engine adapters, and CPU per
selected observation outside the engines. Load average is printed at
the start and the end.
"""

from __future__ import annotations

import cProfile
import importlib.util
import io
import os
import platform
import pstats
import re
import sys
import time
from pathlib import Path

_ENGINE_MARKS = (
    "melt_backend",
    "/engines/",
    "openimcc",
    "vaporock",
    "magemin",
    "alphamelts",
    "thermoengine",
    "engine_cache.py",
)
_REFERENCES = re.compile(r"references=(\d+)")


def _emit(text: str) -> None:
    print(text, flush=True)


def _engine_filename(filename: str) -> bool:
    folded = filename.replace("\\", "/").casefold()
    return any(mark in folded for mark in _ENGINE_MARKS)


def _load_battery_score():
    path = Path.cwd() / "scripts" / "battery_score.py"
    spec = importlib.util.spec_from_file_location("battery_score_t1928", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    root = Path.cwd()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    _emit(
        "host={host} python={python} platform={platform} loadavg_start={loadavg}".format(
            host=platform.node(),
            python=platform.python_version(),
            platform=platform.platform(),
            loadavg=",".join(f"{value:.2f}" for value in os.getloadavg()),
        )
    )
    from simulator.engine_cache import _configured_engine_cache_path

    cache_path = _configured_engine_cache_path()
    _emit(f"engine_cache={cache_path}")
    _emit("subset=true published_residuals=false limit=30000 workers=1")
    battery_score = _load_battery_score()
    profiler = cProfile.Profile()
    captured = io.StringIO()
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    stdout = sys.stdout
    sys.stdout = captured
    exit_code = 1
    try:
        profiler.enable()
        exit_code = battery_score.main(["--workers", "1", "--limit", "30000"])
    finally:
        profiler.disable()
        sys.stdout = stdout
    wall_s = time.perf_counter() - wall_start
    cpu_s = time.process_time() - cpu_start
    score_stdout = captured.getvalue()
    _emit("score_stdout<<")
    sys.stdout.write(score_stdout)
    if score_stdout and not score_stdout.endswith("\n"):
        sys.stdout.write("\n")
    _emit("score_stdout>>")
    matched = _REFERENCES.search(score_stdout)
    references = int(matched.group(1)) if matched else None
    stats = pstats.Stats(profiler)
    total = 0.0
    engine = 0.0
    rows: list[tuple[float, int, str]] = []
    for (filename, line, name), value in stats.stats.items():
        tottime = float(value[2])
        calls = int(value[1])
        total += tottime
        label = f"{filename}:{line}:{name}"
        if _engine_filename(str(filename)):
            engine += tottime
        else:
            rows.append((tottime, calls, label))
    outside = total - engine
    rows.sort(reverse=True)
    per_observation = "n/a" if not references else f"{outside / references:.6f}"
    _emit(f"exit={exit_code}")
    _emit(f"wall_s={wall_s:.3f}")
    _emit(f"process_cpu_s={cpu_s:.3f}")
    _emit(f"profile_tottime_s={total:.3f}")
    _emit(f"engine_tottime_s={engine:.3f}")
    _emit(f"outside_engine_tottime_s={outside:.3f}")
    _emit(f"references={references if references is not None else 'missing'}")
    _emit(f"outside_engine_cpu_per_observation_s={per_observation}")
    _emit("top_outside")
    for tottime, calls, label in rows[:20]:
        _emit(f"  {tottime:.3f} calls={calls} {label}")
    scratch = os.environ.get("TMPDIR")
    if scratch:
        profile_path = Path(scratch) / "t1928-warm-score.prof"
        profiler.dump_stats(str(profile_path))
        _emit(f"profile_path={profile_path}")
    _emit(
        "loadavg_end={}".format(
            ",".join(f"{value:.2f}" for value in os.getloadavg())
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
