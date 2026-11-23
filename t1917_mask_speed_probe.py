#!/usr/bin/env python3
"""Warm whole-store scoring with the refusal mask on and off (t-1917).

No arguments. Run from the repository root::

    python3 t1917_mask_speed_probe.py

One cold pass and one warm pass for alphaMELTS and ThermoEngine, first
with the mask ignored and then with it on. Each pass walks the observation
store the way a score does. The scratch cache is private to this process.
Prints wall, process CPU, and the refusal re-solves (``not_stored``
refusal and exception counters) against mask hits.

The controller runs this. It calls the scorer, so it is not a laptop check.
"""

from __future__ import annotations

import os
import resource
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ENGINES = ("alphamelts", "thermoengine")


def _cpu_seconds() -> float:
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + children.ru_utime + children.ru_stime


def _bucket_sum(bucket: dict[str, int], prefix: str) -> int:
    return sum(int(value) for key, value in bucket.items() if key.startswith(prefix))


def _refusal_resolves(bucket: dict[str, int]) -> int:
    return _bucket_sum(bucket, "not_stored:refusal:") + _bucket_sum(
        bucket, "not_stored:exception:"
    )


@contextmanager
def _mask_enabled(enabled: bool) -> Iterator[None]:
    """Leave the mask alone, or skip lookup and learning for one pair of passes.

    The off side is this process only. It is how the probe compares on
    with off. A stored exact row is unchanged either way.
    """

    import simulator.engine_cache as engine_cache

    if enabled:
        yield
        return
    lookup = engine_cache._lookup_refusal_mask
    learn = engine_cache._try_learn_mask

    def lookup_off(*_args: Any, **_kwargs: Any) -> None:
        return None

    def learn_off(*_args: Any, **_kwargs: Any) -> None:
        return None

    engine_cache._lookup_refusal_mask = lookup_off
    engine_cache._try_learn_mask = learn_off
    try:
        yield
    finally:
        engine_cache._lookup_refusal_mask = lookup
        engine_cache._try_learn_mask = learn


def _score_once(context: Any, output: Path, cache_path: Path) -> tuple[dict[str, dict[str, int]], float, float]:
    from simulator.battery.enums import Engine
    from simulator.battery.score import _score_store_to_jsonl
    from simulator.engine_cache import _engine_cache_path_scope

    engines = tuple(Engine(name) for name in ENGINES)
    before_wall = time.perf_counter()
    before_cpu = _cpu_seconds()
    with _engine_cache_path_scope(cache_path):
        _count, metadata = _score_store_to_jsonl(
            context,
            output,
            root=ROOT,
            engines=engines,
            rail=None,
            work_id=None,
            limit=None,
            include_diagnostics=True,
            workers=1,
        )
    del _count
    counters = {
        str(engine): {str(key): int(value) for key, value in dict(bucket).items()}
        for engine, bucket in dict(metadata.engine_cache_counters or {}).items()
    }
    return counters, time.perf_counter() - before_wall, _cpu_seconds() - before_cpu


def _print_pass(mode: str, label: str, counters: dict[str, dict[str, int]], wall: float, cpu: float) -> None:
    parts = [
        f"T1917_MASK_SPEED mode={mode} pass={label}",
        f"wall_s={wall:.3f}",
        f"cpu_s={cpu:.3f}",
    ]
    for engine in ENGINES:
        bucket = counters.get(engine, {})
        parts.append(f"{engine}_refusal_resolves={_refusal_resolves(bucket)}")
        parts.append(f"{engine}_mask_hits={_bucket_sum(bucket, 'mask_hit:')}")
        parts.append(f"{engine}_hits={int(bucket.get('hits', 0))}")
        parts.append(f"{engine}_misses={int(bucket.get('misses', 0))}")
    print(" ".join(parts), flush=True)
    for engine in ENGINES:
        bucket = counters.get(engine, {})
        detail = {
            key: value
            for key, value in sorted(bucket.items())
            if key.startswith("not_stored:") or key.startswith("mask_")
        }
        print(
            f"T1917_MASK_SPEED_COUNTERS mode={mode} pass={label} engine={engine} {detail}",
            flush=True,
        )


def main() -> int:
    scratch_root = os.environ.get("TMPDIR")
    if not scratch_root:
        print("T1917_MASK_SPEED_NO_TMPDIR", file=sys.stderr)
        return 2
    os.environ.pop("REGOLITH_ENGINE_CACHE_MODE", None)
    os.environ["REGOLITH_ENGINE_CACHE_BOX"] = "laptop"

    from simulator.battery.score import load_score_context
    import simulator.engine_cache as engine_cache

    context = load_score_context(ROOT)
    if not context.observations:
        print("T1917_MASK_SPEED_EMPTY_STORE", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="t1917-mask-speed-", dir=scratch_root) as name:
        scratch = Path(name)
        for mode, enabled in (("off", False), ("on", True)):
            cache_path = scratch / f"{mode}.sqlite"
            engine_cache._close_connections()
            engine_cache._MEMORY_CACHE.clear()
            engine_cache._MASK_DENY.clear()
            with _mask_enabled(enabled):
                for label in ("cold", "warm"):
                    counters, wall, cpu = _score_once(
                        context, scratch / f"{mode}-{label}.jsonl", cache_path,
                    )
                    _print_pass(mode, label, counters, wall, cpu)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
