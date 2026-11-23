#!/usr/bin/env python3
"""Warm single-process score of a fixed 10k-observation subset.

The controller runs this on a studio. It does not run on a laptop. The live
engine cache is opened read-only. The subset is the production score order:
comparison rows and diagnostics, sorted by observation id, first 10000.

Three processes run that score. The warmup faults the cache pages into the
operating-system cache and is not a result. The next process disables the
lookup mmap and page-cache pragmas, which is the measured before. The last
process uses them, which is the measured after. Residual bytes must match.

Each measured process prints user and system CPU, a cProfile, sqlite
connect and execute counts, and bytes handed to score workers. Workers stay
at 1, so worker bytes are the pickle size of a worker dispatch if one happens.

CPU includes the profiler and the sqlite trace, on both measured processes.
System time is the comparison. Per-observation CPU is that time divided by
the subset size.

Usage, with the project interpreter:
  REGOLITH_ENGINE_CACHE=/path/to/cache.sqlite \\
    python3 scripts/probe_warm_score_sys.py --root .
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import os
import pstats
import resource
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
SUBSET_LIMIT = 10_000
_RESULT_NAME = "probe-result.json"


def _cpu() -> tuple[float, float, float, float]:
    self_usage = resource.getrusage(resource.RUSAGE_SELF)
    child_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (
        self_usage.ru_utime,
        self_usage.ru_stime,
        child_usage.ru_utime,
        child_usage.ru_stime,
    )


def _cache_identity(path: Path) -> dict[str, int]:
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        data_version = int(connection.execute("PRAGMA data_version").fetchone()[0])
    return {
        "bytes": path.stat().st_size,
        "page_count": page_count,
        "data_version": data_version,
    }


def _install_sqlite_counter() -> dict[str, int]:
    counted = {"connect": 0, "execute": 0}
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        counted["connect"] += 1
        connection = real_connect(*args, **kwargs)

        def trace(_statement: str) -> None:
            counted["execute"] += 1

        connection.set_trace_callback(trace)
        return connection

    sqlite3.connect = connect
    return counted


def _install_worker_byte_counter() -> dict[str, int]:
    """Count pickle bytes a worker dispatch would send. Workers=1 sends none."""

    import pickle

    import simulator.battery.score as score
    import simulator.battery.score_run as score_run

    counted = {"bytes": 0, "calls": 0}
    real_map = score.supervised_process_map

    def counting_map(tasks, worker, **kwargs):
        protocol = pickle.HIGHEST_PROTOCOL
        for task in tasks:
            counted["bytes"] += len(pickle.dumps(task, protocol=protocol))
            counted["calls"] += 1
        for item in kwargs.get("initargs") or ():
            counted["bytes"] += len(pickle.dumps(item, protocol=protocol))
        return real_map(tasks, worker, **kwargs)

    def counting_dump(obj, file, protocol=None, **kwargs):
        protocol_used = pickle.HIGHEST_PROTOCOL if protocol is None else protocol
        payload = pickle.dumps(obj, protocol=protocol_used)
        counted["bytes"] += len(payload)
        counted["calls"] += 1
        file.write(payload)
        return None

    score.supervised_process_map = counting_map
    score_run.pickle.dump = counting_dump
    return counted


def _sum_named(counters: object, name: str) -> int:
    if not isinstance(counters, dict):
        return 0
    total = 0
    for values in counters.values():
        if isinstance(values, dict):
            total += int(values.get(name, 0) or 0)
    return total


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _profile_top(profile: cProfile.Profile, count: int = 15) -> list[dict[str, object]]:
    stats = pstats.Stats(profile)
    ordered = sorted(stats.stats.items(), key=lambda item: item[1][2], reverse=True)
    rows = []
    for (filename, line, name), (calls, _total_calls, tottime, cumtime, _callers) in ordered[:count]:
        rows.append(
            {
                "function": f"{name}:{Path(filename).name}:{line}",
                "calls": calls,
                "tottime_s": round(tottime, 6),
                "cumtime_s": round(cumtime, 6),
            }
        )
    return rows


def _print_profile(profile: cProfile.Profile) -> None:
    stats = pstats.Stats(profile)
    print("cProfile cumtime", flush=True)
    stats.sort_stats("cumtime").print_stats(30)
    print("cProfile tottime", flush=True)
    stats.sort_stats("tottime").print_stats(30)


def _lookup_pragmas() -> dict[str, int | None]:
    from simulator import engine_cache

    found: dict[str, int | None] = {"mmap_size": None, "cache_size": None}
    for connection in engine_cache._CONNECTIONS.values():
        found["mmap_size"] = int(connection.execute("PRAGMA mmap_size").fetchone()[0])
        found["cache_size"] = int(connection.execute("PRAGMA cache_size").fetchone()[0])
        break
    return found


def _run_child(args: argparse.Namespace) -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    cache_path = Path(os.path.expanduser(os.environ["REGOLITH_ENGINE_CACHE"]))
    if os.environ.get("REGOLITH_ENGINE_CACHE_MODE") != "readonly":
        print("probe requires REGOLITH_ENGINE_CACHE_MODE=readonly", file=sys.stderr)
        return 2
    if not cache_path.is_file():
        print(f"engine cache is not a file: {cache_path}", file=sys.stderr)
        return 2
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    before = _cache_identity(cache_path)
    sqlite_counts = _install_sqlite_counter()
    worker_bytes = _install_worker_byte_counter()
    from simulator import engine_cache
    from simulator.battery.score import SCORE_ENGINE_SET, _score_store_to_jsonl, load_score_context
    import simulator.battery.score as score

    if args.pager == "default":
        engine_cache._apply_lookup_pragmas = lambda _connection: None
    recorded: dict[str, tuple[str, ...]] = {}
    real_prepare = score._prepare_score_run

    def recording_prepare(*prepare_args, **prepare_kwargs):
        prepared = real_prepare(*prepare_args, **prepare_kwargs)
        recorded["ids"] = tuple(observation.observation_id for observation in prepared.refs)
        return prepared

    score._prepare_score_run = recording_prepare
    print(
        f"probe phase={args.pager} profile={args.profile} limit={args.limit} "
        f"cache={cache_path}",
        flush=True,
    )
    print("loading score context", flush=True)
    context = load_score_context(args.root)
    residuals_path = output_dir / "residuals.jsonl"
    for key in sqlite_counts:
        sqlite_counts[key] = 0
    worker_bytes["bytes"] = 0
    worker_bytes["calls"] = 0
    profile = cProfile.Profile()
    started = _cpu()
    wall_started = time.perf_counter()
    if args.profile:
        profile.enable()
    try:
        written, metadata = _score_store_to_jsonl(
            context,
            residuals_path,
            root=args.root,
            engines=SCORE_ENGINE_SET,
            limit=args.limit,
            include_diagnostics=True,
            measured_only=False,
            workers=1,
        )
    finally:
        if args.profile:
            profile.disable()
    elapsed = _cpu()
    wall_s = time.perf_counter() - wall_started
    user_s, sys_s, child_user_s, child_sys_s = (
        after - before_value for after, before_value in zip(elapsed, started)
    )
    ids = recorded.get("ids")
    if not ids or len(ids) != args.limit:
        print(
            f"subset size is {0 if not ids else len(ids)}, required {args.limit}",
            file=sys.stderr,
        )
        return 2
    partial = residuals_path.with_name(residuals_path.name + ".partial")
    if not partial.is_file():
        print(f"missing residual file {partial}", file=sys.stderr)
        return 2
    hits = _sum_named(metadata.engine_cache_counters, "hits")
    misses = _sum_named(metadata.engine_cache_counters, "misses")
    if hits <= 0:
        print("engine cache recorded no hits; the score was not warm", file=sys.stderr)
        return 2
    after = _cache_identity(cache_path)
    if (
        after["bytes"] != before["bytes"]
        or after["page_count"] != before["page_count"]
        or after["data_version"] != before["data_version"]
    ):
        print(f"read-only cache changed: before={before} after={after}", file=sys.stderr)
        return 2
    pragmas = _lookup_pragmas()
    if args.pager == "production" and not pragmas["mmap_size"]:
        print("production lookup connection did not enable mmap", file=sys.stderr)
        return 2
    observations = len(ids)
    total_cpu = user_s + sys_s + child_user_s + child_sys_s
    subset_digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    payload = {
        "pager": args.pager,
        "observations": observations,
        "residuals": written,
        "wall_s": wall_s,
        "user_cpu_s": user_s,
        "sys_cpu_s": sys_s,
        "child_user_cpu_s": child_user_s,
        "child_sys_cpu_s": child_sys_s,
        "cpu_per_observation_user_s": (user_s + child_user_s) / observations,
        "cpu_per_observation_sys_s": (sys_s + child_sys_s) / observations,
        "cpu_per_observation_total_s": total_cpu / observations,
        "sqlite_connects": sqlite_counts["connect"],
        "sqlite_executes": sqlite_counts["execute"],
        "worker_ipc_bytes": worker_bytes["bytes"],
        "worker_ipc_calls": worker_bytes["calls"],
        "cache_hits": hits,
        "cache_misses": misses,
        "mmap_size": pragmas["mmap_size"],
        "cache_size": pragmas["cache_size"],
        "residual_sha256": _file_sha256(partial),
        "residual_bytes": partial.stat().st_size,
        "residual_path": str(partial),
        "subset_sha256": subset_digest,
        "subset_first": ids[0],
        "subset_last": ids[-1],
        "cache_path": str(cache_path),
        "cache_bytes_before": before["bytes"],
        "cache_bytes_after": after["bytes"],
        "cache_page_count_before": before["page_count"],
        "cache_page_count_after": after["page_count"],
        "cache_data_version_before": before["data_version"],
        "cache_data_version_after": after["data_version"],
    }
    if args.profile:
        payload["profile_top_tottime"] = _profile_top(profile)
        profile.dump_stats(str(output_dir / "score.pstats"))
        _print_profile(profile)
    (output_dir / _RESULT_NAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"pager={args.pager} observations={observations} residuals={written} "
        f"wall_s={wall_s:.3f} user_cpu_s={user_s + child_user_s:.3f} "
        f"sys_cpu_s={sys_s + child_sys_s:.3f} "
        f"cpu_per_observation_user_s={payload['cpu_per_observation_user_s']:.6f} "
        f"cpu_per_observation_sys_s={payload['cpu_per_observation_sys_s']:.6f} "
        f"cpu_per_observation_total_s={payload['cpu_per_observation_total_s']:.6f}",
        flush=True,
    )
    print(
        f"sqlite_connects={payload['sqlite_connects']} "
        f"sqlite_executes={payload['sqlite_executes']} "
        f"worker_ipc_bytes={payload['worker_ipc_bytes']} "
        f"cache_hits={hits} cache_misses={misses} "
        f"mmap_size={pragmas['mmap_size']} cache_size={pragmas['cache_size']}",
        flush=True,
    )
    print(
        f"subset_sha256={subset_digest} first={ids[0]} last={ids[-1]} "
        f"residual_sha256={payload['residual_sha256']}",
        flush=True,
    )
    return 0


def _first_difference(left: Path, right: Path) -> str:
    with left.open("rb") as left_handle, right.open("rb") as right_handle:
        for number, pair in enumerate(zip(left_handle, right_handle), start=1):
            if pair[0] != pair[1]:
                return f"line {number} differs"
        extra_left = left_handle.readline()
        extra_right = right_handle.readline()
    if extra_left or extra_right:
        return "files differ in length"
    return "no line difference"


def _load_result(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _spawn(phase_args: list[str], env: dict[str, str]) -> int:
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *phase_args],
        env=env,
        check=False,
    )
    return completed.returncode


def _run_parent(args: argparse.Namespace) -> int:
    cache_value = (
        str(args.engine_cache.expanduser())
        if args.engine_cache is not None
        else os.environ.get("REGOLITH_ENGINE_CACHE", "")
    )
    cache_path = Path(os.path.expanduser(cache_value)) if cache_value else None
    if cache_path is None or not cache_path.is_file():
        print(
            "set REGOLITH_ENGINE_CACHE or --engine-cache to the live cache file",
            file=sys.stderr,
        )
        return 2
    scratch_root = os.environ.get("TMPDIR")
    if not scratch_root:
        print("TMPDIR is required for probe output", file=sys.stderr)
        return 2
    scratch = Path(scratch_root) / "warm-score-sys"
    run_dir = scratch / (os.environ.get("GOALFLIGHT_DISPATCH_ID") or "local")
    env = os.environ.copy()
    env["REGOLITH_ENGINE_CACHE"] = str(cache_path)
    env["REGOLITH_ENGINE_CACHE_MODE"] = "readonly"
    phases = (
        ("warmup", "production", False),
        ("before", "default", True),
        ("after", "production", True),
    )
    results: dict[str, dict[str, object]] = {}
    for name, pager, profile in phases:
        output_dir = run_dir / name
        code = _spawn(
            [
                "--phase",
                "child",
                "--pager",
                pager,
                "--limit",
                str(args.limit),
                "--root",
                str(args.root),
                "--output-dir",
                str(output_dir),
                *(["--profile"] if profile else []),
            ],
            env,
        )
        if code != 0:
            print(f"{name} exited {code}", file=sys.stderr)
            return code
        results[name] = _load_result(output_dir / _RESULT_NAME)
    before = results["before"]
    after = results["after"]
    if before["subset_sha256"] != after["subset_sha256"]:
        print("before and after scored different observation subsets", file=sys.stderr)
        return 2
    identical = before["residual_sha256"] == after["residual_sha256"]
    difference = "identical"
    if not identical:
        difference = _first_difference(
            Path(str(before["residual_path"])),
            Path(str(after["residual_path"])),
        )
    observations = int(before["observations"])
    print("warm-score-sys comparison", flush=True)
    print(f"observations={observations} subset_sha256={before['subset_sha256']}", flush=True)
    print(
        "before "
        f"user_per_obs_s={float(before['cpu_per_observation_user_s']):.6f} "
        f"sys_per_obs_s={float(before['cpu_per_observation_sys_s']):.6f} "
        f"total_per_obs_s={float(before['cpu_per_observation_total_s']):.6f} "
        f"sqlite_connects={before['sqlite_connects']} "
        f"sqlite_executes={before['sqlite_executes']} "
        f"worker_ipc_bytes={before['worker_ipc_bytes']} "
        f"mmap_size={before['mmap_size']} cache_size={before['cache_size']}",
        flush=True,
    )
    print(
        "after "
        f"user_per_obs_s={float(after['cpu_per_observation_user_s']):.6f} "
        f"sys_per_obs_s={float(after['cpu_per_observation_sys_s']):.6f} "
        f"total_per_obs_s={float(after['cpu_per_observation_total_s']):.6f} "
        f"sqlite_connects={after['sqlite_connects']} "
        f"sqlite_executes={after['sqlite_executes']} "
        f"worker_ipc_bytes={after['worker_ipc_bytes']} "
        f"mmap_size={after['mmap_size']} cache_size={after['cache_size']}",
        flush=True,
    )
    print(f"residual_bytes_identical={identical} {difference}", flush=True)
    if not identical:
        return 2
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("run", "child"), default="run")
    parser.add_argument("--pager", choices=("default", "production"), default="production")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--limit", type=int, default=SUBSET_LIMIT)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--engine-cache", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.limit < 1:
        print("--limit must be at least 1", file=sys.stderr)
        return 2
    if args.phase == "child":
        if args.output_dir is None:
            print("--output-dir is required for a child phase", file=sys.stderr)
            return 2
        return _run_child(args)
    if args.limit != SUBSET_LIMIT:
        print(
            f"warning: measuring {args.limit} observations, not the fixed {SUBSET_LIMIT}",
            file=sys.stderr,
        )
    return _run_parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
