#!/usr/bin/env python3
"""Warm score of the fixed 10k-observation subset in serial and worker modes.

The controller runs this on a studio. It does not run on a laptop. The live
engine cache is opened read-only. The subset is the production score order:
comparison rows and diagnostics, sorted by observation id, first 10000.

The warmup faults the cache pages into the operating-system cache and is not
a result. A serial run and the requested --workers run then score the same
subset. Their residual bytes must match. CPU includes the profiler and sqlite
trace in the measured runs. Pipe counts are serialized payload bytes;
file opens count builtins/io.open calls and sqlite connections separately.
Use --limit 500 --allocation-profile to sample live allocations around residual
conversion on the serial pass; snapshots are taken every 25 residual lines.
Use --workers 1 --limit 500 --stage-profile for serial per-stage fault and CPU
attribution over the same every-20th slice, with cumulative cProfile for
candidate generation and residual construction.
Use --workers 1 --retention-profile for a full 10k run with RSS samples every
1000 observations and retained-allocation comparison from 2000 to 8000.

Usage, with the project interpreter:
  REGOLITH_ENGINE_CACHE=/path/to/cache.sqlite \\
    python3 scripts/probe_warm_score_sys.py --root . --workers 10
  REGOLITH_ENGINE_CACHE=/path/to/cache.sqlite \\
    python3 scripts/probe_warm_score_sys.py --root . --workers 10 --limit 500 --allocation-profile
  REGOLITH_ENGINE_CACHE=/path/to/cache.sqlite \\
    python3 scripts/probe_warm_score_sys.py --root . --workers 1 --limit 500 --stage-profile
  REGOLITH_ENGINE_CACHE=/path/to/cache.sqlite \\
    python3 scripts/probe_warm_score_sys.py --root . --workers 1 --retention-profile
"""

from __future__ import annotations

import argparse
import builtins
import cProfile
import hashlib
import io
import json
import os
import pstats
import resource
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path
from multiprocessing.connection import Connection
from multiprocessing.util import Finalize
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
SUBSET_LIMIT = 10_000
_RESULT_NAME = "probe-result.json"
_COUNTER_NAMES = (
    "files_opened",
    "stat_calls",
    "sqlite_connects",
    "sqlite_executes",
    "pipe_send_bytes",
    "pipe_recv_bytes",
)
_PROCESS_COUNTERS: dict[int, dict[str, int]] = {}
_COUNTERS_ACTIVE = False
_REAL_BUILTINS_OPEN = None
_REAL_IO_OPEN = None
_REAL_OS_STAT = None
_REAL_SQLITE_CONNECT = None
_REAL_SEND_BYTES = None
_REAL_RECV_BYTES = None


def _usage() -> dict[str, dict[str, float | int]]:
    self_usage = resource.getrusage(resource.RUSAGE_SELF)
    child_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    fields = ("utime", "stime", "minflt", "majflt", "nvcsw", "nivcsw")
    return {
        scope: {
            f"ru_{field}": getattr(usage, f"ru_{field}")
            for field in fields
        }
        for scope, usage in (("parent", self_usage), ("children", child_usage))
    }


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


def _process_counters() -> dict[str, int]:
    pid = os.getpid()
    return _PROCESS_COUNTERS.setdefault(pid, dict.fromkeys(_COUNTER_NAMES, 0))


def _count(name: str, amount: int = 1) -> None:
    if _COUNTERS_ACTIVE:
        counters = _process_counters()
        counters[name] += amount


def _install_process_counters() -> None:
    global _REAL_BUILTINS_OPEN, _REAL_IO_OPEN, _REAL_OS_STAT
    global _REAL_SQLITE_CONNECT, _REAL_SEND_BYTES, _REAL_RECV_BYTES
    if _REAL_BUILTINS_OPEN is None:
        _REAL_BUILTINS_OPEN = builtins.open

        def counted_builtin_open(*args, **kwargs):
            _count("files_opened")
            return _REAL_BUILTINS_OPEN(*args, **kwargs)

        builtins.open = counted_builtin_open
    if _REAL_IO_OPEN is None:
        _REAL_IO_OPEN = io.open

        def counted_io_open(*args, **kwargs):
            _count("files_opened")
            return _REAL_IO_OPEN(*args, **kwargs)

        io.open = counted_io_open
    if _REAL_OS_STAT is None:
        _REAL_OS_STAT = os.stat

        def counted_stat(*args, **kwargs):
            _count("stat_calls")
            return _REAL_OS_STAT(*args, **kwargs)

        os.stat = counted_stat
    if _REAL_SQLITE_CONNECT is None:
        _REAL_SQLITE_CONNECT = sqlite3.connect

        def counted_connect(*args, **kwargs):
            _count("sqlite_connects")
            connection = _REAL_SQLITE_CONNECT(*args, **kwargs)
            connection.set_trace_callback(
                lambda _statement: _count("sqlite_executes")
            )
            return connection

        sqlite3.connect = counted_connect
    if _REAL_SEND_BYTES is None or _REAL_RECV_BYTES is None:
        _REAL_SEND_BYTES = Connection.send_bytes
        _REAL_RECV_BYTES = Connection.recv_bytes

        def counted_send_bytes(connection, buffer, offset=0, size=None):
            length = memoryview(buffer).nbytes - offset if size is None else size
            _count("pipe_send_bytes", length)
            if size is None:
                return _REAL_SEND_BYTES(connection, buffer, offset)
            return _REAL_SEND_BYTES(connection, buffer, offset, size)

        def counted_recv_bytes(connection, maxlength=None):
            payload = _REAL_RECV_BYTES(connection, maxlength)
            _count("pipe_recv_bytes", len(payload))
            return payload

        Connection.send_bytes = counted_send_bytes
        Connection.recv_bytes = counted_recv_bytes


def _write_worker_counters(output_dir: str, pid: int) -> None:
    path = Path(output_dir) / f"worker-{pid}.json"
    payload = json.dumps(_PROCESS_COUNTERS.get(pid, dict.fromkeys(_COUNTER_NAMES, 0)))
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, payload.encode("utf-8"))
    finally:
        os.close(descriptor)


def _instrument_score_worker(initializer, output_dir: str, *initargs) -> None:
    global _COUNTERS_ACTIVE
    _install_process_counters()
    _COUNTERS_ACTIVE = True
    pid = os.getpid()
    _PROCESS_COUNTERS[pid] = dict.fromkeys(_COUNTER_NAMES, 0)
    if initializer is not None:
        initializer(*initargs)
    Finalize(None, _write_worker_counters, args=(output_dir, pid), exitpriority=0)


def _install_worker_probe(output_dir: Path) -> None:
    import simulator.battery.score as score

    real_map = score.supervised_process_map
    real_shutdown = ProcessPoolExecutor.shutdown
    counters_dir = output_dir / "worker-counters"
    counters_dir.mkdir(parents=True, exist_ok=True)

    def counting_map(tasks, worker, **kwargs):
        initializer = kwargs.get("initializer")
        initargs = tuple(kwargs.get("initargs") or ())
        kwargs["initializer"] = _instrument_score_worker
        kwargs["initargs"] = (initializer, str(counters_dir), *initargs)
        return real_map(tasks, worker, **kwargs)

    def waiting_shutdown(executor, wait=False, cancel_futures=False):
        # Wait so RUSAGE_CHILDREN and worker-finalizer counters include the pool.
        return real_shutdown(executor, wait=True, cancel_futures=cancel_futures)

    score.supervised_process_map = counting_map
    ProcessPoolExecutor.shutdown = waiting_shutdown


def _read_worker_counters(directory: Path) -> list[dict[str, int]]:
    return [
        {"pid": int(path.stem.split("-")[-1]), **json.loads(path.read_text())}
        for path in sorted(directory.glob("worker-*.json"))
    ]


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


def _profile_top_cumulative(
    profile: cProfile.Profile, count: int = 20
) -> list[dict[str, object]]:
    stats = pstats.Stats(profile)
    ordered = sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)
    return [
        {
            "function": f"{name}:{Path(filename).name}:{line}",
            "calls": values[0],
            "tottime_s": round(values[2], 6),
            "cumtime_s": round(values[3], 6),
        }
        for (filename, line, name), values in ordered[:count]
    ]


def _print_profile(profile: cProfile.Profile) -> None:
    stats = pstats.Stats(profile)
    print("cProfile cumtime", flush=True)
    stats.sort_stats("cumtime").print_stats(30)
    print("cProfile tottime", flush=True)
    stats.sort_stats("tottime").print_stats(30)


def _install_allocation_probe(score, *, sample_every: int = 25) -> dict[str, object]:
    import tracemalloc

    real_residual_to_plain = score.residual_to_plain
    real_dumps_residual_line = score.dumps_residual_line
    site_bytes: dict[str, int] = {}
    site_blocks: dict[str, int] = {}
    state = {
        "calls": 0,
        "samples": 0,
        "plain_calls": 0,
        "plain_peak_bytes_sum": 0,
        "plain_peak_bytes_max": 0,
        "dump_peak_bytes_sum": 0,
        "dump_peak_bytes_max": 0,
        "serialized_chars": 0,
        "serialized_chars_max": 0,
        "capture_plain": False,
    }

    def record_snapshot_diff(before, after) -> None:
        for stat in after.compare_to(before, "lineno"):
            if stat.size_diff <= 0:
                continue
            frame = stat.traceback[-1]
            filename = Path(frame.filename)
            if filename.name == Path(__file__).name:
                continue
            try:
                site = f"{filename.relative_to(ROOT)}:{frame.lineno}"
            except ValueError:
                site = f"{filename}:{frame.lineno}"
            site_bytes[site] = site_bytes.get(site, 0) + stat.size_diff
            site_blocks[site] = site_blocks.get(site, 0) + max(0, stat.count_diff)

    def measured_residual_to_plain(residual, candidate=None):
        if not state["capture_plain"]:
            return real_residual_to_plain(residual, candidate)
        import tracemalloc

        before = tracemalloc.take_snapshot()
        current_before, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        payload = real_residual_to_plain(residual, candidate)
        _current_after, peak_after = tracemalloc.get_traced_memory()
        after = tracemalloc.take_snapshot()
        record_snapshot_diff(before, after)
        peak_bytes = max(0, peak_after - current_before)
        state["plain_calls"] += 1
        state["plain_peak_bytes_sum"] += peak_bytes
        state["plain_peak_bytes_max"] = max(
            state["plain_peak_bytes_max"], peak_bytes
        )
        return payload

    def measured_dumps_residual_line(residual, candidate=None):
        state["calls"] += 1
        if state["calls"] % sample_every:
            return real_dumps_residual_line(residual, candidate)
        import tracemalloc

        state["samples"] += 1
        before = tracemalloc.take_snapshot()
        current_before, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        state["capture_plain"] = True
        try:
            serialized = real_dumps_residual_line(residual, candidate)
        finally:
            state["capture_plain"] = False
        _current_after, peak_after = tracemalloc.get_traced_memory()
        after = tracemalloc.take_snapshot()
        record_snapshot_diff(before, after)
        peak_bytes = max(0, peak_after - current_before)
        state["dump_peak_bytes_sum"] += peak_bytes
        state["dump_peak_bytes_max"] = max(
            state["dump_peak_bytes_max"], peak_bytes
        )
        serialized_chars = len(serialized)
        state["serialized_chars"] += serialized_chars
        state["serialized_chars_max"] = max(
            state["serialized_chars_max"], serialized_chars
        )
        return serialized

    score.residual_to_plain = measured_residual_to_plain
    score.dumps_residual_line = measured_dumps_residual_line
    return {
        "sample_every": sample_every,
        "state": state,
        "site_bytes": site_bytes,
        "site_blocks": site_blocks,
    }


def _allocation_profile_result(profile: dict[str, object]) -> dict[str, object]:
    state = profile["state"]
    site_bytes = profile["site_bytes"]
    site_blocks = profile["site_blocks"]
    assert isinstance(state, dict)
    assert isinstance(site_bytes, dict)
    assert isinstance(site_blocks, dict)
    top_sites = [
        {
            "site": site,
            "positive_live_bytes": size_bytes,
            "positive_live_blocks": site_blocks[site],
        }
        for site, size_bytes in sorted(
            site_bytes.items(), key=lambda item: item[1], reverse=True
        )[:10]
    ]
    return {
        "sample_every": profile["sample_every"],
        "serialization_calls": state["calls"],
        "sampled_calls": state["samples"],
        "residual_to_plain_calls_sampled": state["plain_calls"],
        "plain_peak_bytes_sum": state["plain_peak_bytes_sum"],
        "plain_peak_bytes_max": state["plain_peak_bytes_max"],
        "dump_peak_bytes_sum": state["dump_peak_bytes_sum"],
        "dump_peak_bytes_max": state["dump_peak_bytes_max"],
        "serialized_chars_sum": state["serialized_chars"],
        "serialized_chars_max": state["serialized_chars_max"],
        "top_sites": top_sites,
    }


class _StageProbe:
    """Collect per-call page faults and CPU around probe-only stage hooks."""

    def __init__(self) -> None:
        self.active = False
        self.rows: dict[str, dict[str, float | int]] = {}
        self.profiles = {
            "candidate_generation": cProfile.Profile(),
            "residual_construction": cProfile.Profile(),
        }
        self.active_profile: cProfile.Profile | None = None

    def measure(self, stage: str, function, *args, **kwargs):
        if not self.active:
            return function(*args, **kwargs)
        profile = self.profiles.get(stage)
        previous_profile = self.active_profile
        switched_profile = profile is not None and profile is not previous_profile
        if switched_profile:
            if previous_profile is not None:
                previous_profile.disable()
            assert profile is not None
            profile.enable()
            self.active_profile = profile
        started = time.process_time()
        faults_before = resource.getrusage(resource.RUSAGE_SELF).ru_minflt
        try:
            return function(*args, **kwargs)
        finally:
            if switched_profile:
                assert profile is not None
                profile.disable()
                if previous_profile is not None:
                    previous_profile.enable()
                self.active_profile = previous_profile
            cpu_s = time.process_time() - started
            faults = (
                resource.getrusage(resource.RUSAGE_SELF).ru_minflt
                - faults_before
            )
            row = self.rows.setdefault(
                stage, {"calls": 0, "ru_minflt": 0, "cpu_s": 0.0}
            )
            row["calls"] += 1
            row["ru_minflt"] += faults
            row["cpu_s"] += cpu_s

    def wrap(self, stage: str, function):
        def measured(*args, **kwargs):
            return self.measure(stage, function, *args, **kwargs)

        return measured

    def result(self, observations: int) -> dict[str, object]:
        return {
            "stages": {
                stage: {
                    **values,
                    "ru_minflt_per_observation": values["ru_minflt"] / observations,
                    "cpu_s_per_observation": values["cpu_s"] / observations,
                }
                for stage, values in sorted(self.rows.items())
            },
            "cumulative_profiles": {
                stage: _profile_top_cumulative(profile)
                for stage, profile in self.profiles.items()
            },
        }


def _install_stage_probe(score) -> _StageProbe:
    """Measure scoring stages by monkeypatching their existing call seams."""
    import simulator.battery.compilation_tier as compilation_tier
    import simulator.engine_cache as engine_cache

    probe = _StageProbe()

    for name in (
        "_compilation_engines_for_scoring",
        "_series_point_cell",
    ):
        function = getattr(compilation_tier, name)
        setattr(
            compilation_tier,
            name,
            probe.wrap(
                "candidate_generation"
                if name == "_compilation_engines_for_scoring"
                else "compilation_tier.series_point_cell",
                function,
            ),
        )

    real_slots = compilation_tier._compilation_series_slots

    def measured_slots(*args, **kwargs):
        slots = real_slots(*args, **kwargs)
        if slots is None or not probe.active:
            return slots
        iterator = iter(slots)

        def measured_generate():
            while True:
                try:
                    yield probe.measure(
                        "compilation_tier.generate", next, iterator
                    )
                except StopIteration:
                    return

        return measured_generate()

    compilation_tier._compilation_series_slots = measured_slots

    for name in (
        "_score_predict_with_engine",
        "_hashimoto_residue_prediction",
        "_sossi_residue_prediction",
        "_knudsen_residue_prediction",
        "candidate_observation",
    ):
        function = getattr(score, name)
        setattr(score, name, probe.wrap("candidate_generation", function))

    score._compile_residual_with_lineage = probe.wrap(
        "residual_construction", score._compile_residual_with_lineage
    )
    score.hk_ratio_alpha_predictor_refusal = probe.wrap(
        "residual_construction.hk_alpha_source_scan",
        score.hk_ratio_alpha_predictor_refusal,
    )
    engine_cache.cached_engine_call = probe.wrap(
        "engine_cache.lookup_and_decode", engine_cache.cached_engine_call
    )
    score.residual_to_plain = probe.wrap("conversion", score.residual_to_plain)
    score._ResidualJsonlStream.append = probe.wrap(
        "writing", score._ResidualJsonlStream.append
    )
    return probe


def _ru_maxrss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def _current_rss_bytes() -> int:
    resident_kib = subprocess.check_output(
        ["ps", "-o", "rss=", "-p", str(os.getpid())], text=True
    ).strip()
    return int(resident_kib) * 1024


class _RetentionProbe:
    """Sample retained process memory at outer observation boundaries."""

    def __init__(self) -> None:
        self.active = False
        self.reference_ids: set[str] = set()
        self.seen_ids: set[str] = set()
        self.samples: list[dict[str, int]] = []
        self.snapshot_2000 = None
        self.retained_diff: dict[str, object] | None = None

    def start(self, references) -> None:
        import tracemalloc

        self.reference_ids = {
            observation.observation_id for observation in references
        }
        tracemalloc.start(10)
        self.active = True

    def row_start(self, observation) -> None:
        if (
            not self.active
            or observation.observation_id not in self.reference_ids
            or observation.observation_id in self.seen_ids
        ):
            return
        completed = len(self.seen_ids)
        if completed and completed % 1000 == 0:
            self.sample(completed)
        self.seen_ids.add(observation.observation_id)

    def sample(self, observations: int) -> None:
        import tracemalloc

        if self.samples and self.samples[-1]["observations"] == observations:
            return
        snapshot = None
        if observations in {2000, 8000}:
            snapshot = tracemalloc.take_snapshot()
        traced_current, _traced_peak = tracemalloc.get_traced_memory()
        self.samples.append(
            {
                "observations": observations,
                "rss_bytes": _current_rss_bytes(),
                "ru_maxrss_bytes": _ru_maxrss_bytes(),
                "traced_current_bytes": traced_current,
            }
        )
        if observations == 2000:
            self.snapshot_2000 = snapshot
        elif observations == 8000 and self.snapshot_2000 is not None:
            assert snapshot is not None
            positive_diffs = [
                stat
                for stat in snapshot.compare_to(self.snapshot_2000, "traceback")
                if stat.size_diff > 0
            ]
            positive_diffs.sort(key=lambda stat: stat.size_diff, reverse=True)
            self.retained_diff = {
                "positive_size_diff_bytes": sum(
                    stat.size_diff for stat in positive_diffs
                ),
                "top_tracebacks": [
                    {
                        "size_diff_bytes": stat.size_diff,
                        "count_diff": stat.count_diff,
                        "traceback": stat.traceback.format(),
                    }
                    for stat in positive_diffs[:10]
                ],
            }
            self.snapshot_2000 = None

    def finish(self, observations: int) -> dict[str, object]:
        if observations:
            self.sample(observations)
        self.active = False
        return {
            "ru_maxrss_bytes": _ru_maxrss_bytes(),
            "samples": self.samples,
            "retained_2000_to_8000": self.retained_diff,
        }


def _install_retention_probe(score) -> _RetentionProbe:
    probe = _RetentionProbe()
    real_duplicate_detail = score._duplicate_observation_refusal_detail

    def measured_duplicate_detail(context, observation):
        probe.row_start(observation)
        return real_duplicate_detail(context, observation)

    score._duplicate_observation_refusal_detail = measured_duplicate_detail
    return probe


def _run_child(args: argparse.Namespace) -> int:
    sampled_profile = args.allocation_profile or args.stage_profile
    if sampled_profile and SUBSET_LIMIT % args.limit:
        print(
            "profile limit must divide the fixed subset size",
            file=sys.stderr,
        )
        return 2
    sample_stride = (
        SUBSET_LIMIT // args.limit if sampled_profile else 1
    )
    scoring_limit = SUBSET_LIMIT if sampled_profile else args.limit
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
    _install_process_counters()
    _install_worker_probe(output_dir)
    from simulator.battery.score import SCORE_ENGINE_SET, _score_store_to_jsonl, load_score_context
    import simulator.battery.score as score

    recorded: dict[str, tuple[str, ...]] = {}
    stage_probe = _install_stage_probe(score) if args.stage_profile else None
    retention_probe = (
        _install_retention_probe(score) if args.retention_profile else None
    )
    real_prepare = score._prepare_score_run

    def recording_prepare(*prepare_args, **prepare_kwargs):
        prepared = real_prepare(*prepare_args, **prepare_kwargs)
        if sampled_profile:
            prepared = replace(
                prepared, refs=prepared.refs[::sample_stride]
            )
        recorded["ids"] = tuple(observation.observation_id for observation in prepared.refs)
        if stage_probe is not None:
            stage_probe.active = True
        if retention_probe is not None:
            retention_probe.start(prepared.refs)
        return prepared

    score._prepare_score_run = recording_prepare
    print(
        f"probe workers={args.workers} profile={args.profile} "
        f"stage_profile={args.stage_profile} "
        f"retention_profile={args.retention_profile} limit={args.limit} "
        f"cache={cache_path}",
        flush=True,
    )
    print("loading score context", flush=True)
    context = load_score_context(args.root)
    residuals_path = output_dir / "residuals.jsonl"
    global _COUNTERS_ACTIVE
    _PROCESS_COUNTERS[os.getpid()] = dict.fromkeys(_COUNTER_NAMES, 0)
    _COUNTERS_ACTIVE = True
    allocation_probe = None
    if args.allocation_profile and args.workers == 1:
        import tracemalloc

        tracemalloc.start(1)
        allocation_probe = _install_allocation_probe(score)
    retention_metrics = None
    profile = cProfile.Profile()
    started = _usage()
    wall_started = time.perf_counter()
    if args.profile:
        profile.enable()
    try:
        written, metadata = _score_store_to_jsonl(
            context,
            residuals_path,
            root=args.root,
            engines=SCORE_ENGINE_SET,
            include_diagnostics=True,
            measured_only=False,
            workers=args.workers,
            limit=scoring_limit,
        )
        if retention_probe is not None:
            retention_metrics = retention_probe.finish(len(recorded["ids"]))
    finally:
        if stage_probe is not None:
            stage_probe.active = False
        if args.profile:
            profile.disable()
        _COUNTERS_ACTIVE = False
    allocation_profile = None
    if allocation_probe is not None:
        import tracemalloc

        allocation_profile = _allocation_profile_result(allocation_probe)
        tracemalloc.stop()
    if retention_probe is not None:
        import tracemalloc

        tracemalloc.stop()
    elapsed = _usage()
    parent_counts = dict(_PROCESS_COUNTERS.get(os.getpid(), {}))
    worker_metrics = _read_worker_counters(output_dir / "worker-counters")
    if args.workers > 1 and not worker_metrics:
        print("worker process counters were not collected", file=sys.stderr)
        return 2
    wall_s = time.perf_counter() - wall_started
    usage_delta = {
        scope: {
            name: value - started[scope][name]
            for name, value in counters.items()
        }
        for scope, counters in elapsed.items()
    }
    user_s = usage_delta["parent"]["ru_utime"]
    sys_s = usage_delta["parent"]["ru_stime"]
    child_user_s = usage_delta["children"]["ru_utime"]
    child_sys_s = usage_delta["children"]["ru_stime"]
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
    observations = len(ids)
    total_cpu = user_s + sys_s + child_user_s + child_sys_s
    stage_metrics = (
        None if stage_probe is None else stage_probe.result(observations)
    )
    subset_digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    worker_pipe_sent = sum(item["pipe_send_bytes"] for item in worker_metrics)
    worker_pipe_received = sum(item["pipe_recv_bytes"] for item in worker_metrics)
    worker_pipe_bytes = parent_counts["pipe_send_bytes"] + worker_pipe_sent
    payload = {
        "workers": args.workers,
        "allocation_profile": allocation_profile,
        "stage_metrics": stage_metrics,
        "retention_profile": retention_metrics,
        "observations": observations,
        "residuals": written,
        "wall_s": wall_s,
        "parent_user_cpu_s": user_s,
        "parent_sys_cpu_s": sys_s,
        "child_user_cpu_s": child_user_s,
        "child_sys_cpu_s": child_sys_s,
        "parent_ru_minflt": usage_delta["parent"]["ru_minflt"],
        "parent_ru_majflt": usage_delta["parent"]["ru_majflt"],
        "parent_ru_nvcsw": usage_delta["parent"]["ru_nvcsw"],
        "parent_ru_nivcsw": usage_delta["parent"]["ru_nivcsw"],
        "children_ru_minflt": usage_delta["children"]["ru_minflt"],
        "children_ru_majflt": usage_delta["children"]["ru_majflt"],
        "children_ru_nvcsw": usage_delta["children"]["ru_nvcsw"],
        "children_ru_nivcsw": usage_delta["children"]["ru_nivcsw"],
        "cpu_per_observation_user_s": (user_s + child_user_s) / observations,
        "cpu_per_observation_sys_s": (sys_s + child_sys_s) / observations,
        "cpu_per_observation_total_s": total_cpu / observations,
        "parent_sqlite_connects": parent_counts["sqlite_connects"],
        "parent_sqlite_executes": parent_counts["sqlite_executes"],
        "worker_sqlite_connects": sum(
            item["sqlite_connects"] for item in worker_metrics
        ),
        "worker_sqlite_executes": sum(
            item["sqlite_executes"] for item in worker_metrics
        ),
        "parent_pipe_send_bytes": parent_counts["pipe_send_bytes"],
        "worker_ipc_payload_bytes_sent": worker_pipe_bytes,
        "parent_pipe_recv_bytes": parent_counts["pipe_recv_bytes"],
        "worker_pipe_recv_bytes": worker_pipe_received,
        "worker_python_file_opens": sum(
            item["files_opened"] for item in worker_metrics
        ),
        "worker_stat_calls": sum(item["stat_calls"] for item in worker_metrics),
        "worker_counters": worker_metrics,
        "cache_hits": hits,
        "cache_misses": misses,
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
        f"workers={args.workers} observations={observations} residuals={written} "
        f"wall_s={wall_s:.3f} parent_user_cpu_s={user_s:.3f} "
        f"parent_sys_cpu_s={sys_s:.3f} children_user_cpu_s={child_user_s:.3f} "
        f"children_sys_cpu_s={child_sys_s:.3f} "
        f"parent_ru_minflt={payload['parent_ru_minflt']} "
        f"parent_ru_majflt={payload['parent_ru_majflt']} "
        f"parent_ru_nvcsw={payload['parent_ru_nvcsw']} "
        f"parent_ru_nivcsw={payload['parent_ru_nivcsw']} "
        f"children_ru_minflt={payload['children_ru_minflt']} "
        f"children_ru_majflt={payload['children_ru_majflt']} "
        f"children_ru_nvcsw={payload['children_ru_nvcsw']} "
        f"children_ru_nivcsw={payload['children_ru_nivcsw']} "
        f"cpu_per_observation_user_s={payload['cpu_per_observation_user_s']:.6f} "
        f"cpu_per_observation_sys_s={payload['cpu_per_observation_sys_s']:.6f} "
        f"cpu_per_observation_total_s={payload['cpu_per_observation_total_s']:.6f}",
        flush=True,
    )
    print(
        f"parent_sqlite_connects={payload['parent_sqlite_connects']} "
        f"parent_sqlite_executes={payload['parent_sqlite_executes']} "
        f"worker_sqlite_connects={payload['worker_sqlite_connects']} "
        f"worker_sqlite_executes={payload['worker_sqlite_executes']} "
        f"worker_ipc_payload_bytes_sent={payload['worker_ipc_payload_bytes_sent']} "
        f"worker_python_file_opens={payload['worker_python_file_opens']} "
        f"worker_stat_calls={payload['worker_stat_calls']} "
        f"cache_hits={hits} cache_misses={misses}",
        flush=True,
    )
    for worker_metric in worker_metrics:
        print(
            "worker "
            f"pid={worker_metric['pid']} "
            f"pipe_send_bytes={worker_metric['pipe_send_bytes']} "
            f"pipe_recv_bytes={worker_metric['pipe_recv_bytes']} "
            f"files_opened={worker_metric['files_opened']} "
            f"stat_calls={worker_metric['stat_calls']} "
            f"sqlite_connects={worker_metric['sqlite_connects']} "
            f"sqlite_executes={worker_metric['sqlite_executes']}",
            flush=True,
        )
    print(
        f"subset_sha256={subset_digest} first={ids[0]} last={ids[-1]} "
        f"residual_sha256={payload['residual_sha256']}",
        flush=True,
    )
    if allocation_profile is not None:
        print(
            "allocation profile "
            f"sampled_calls={allocation_profile['sampled_calls']} "
            f"plain_peak_bytes_max={allocation_profile['plain_peak_bytes_max']} "
            f"dump_peak_bytes_max={allocation_profile['dump_peak_bytes_max']} "
            f"serialized_chars_max={allocation_profile['serialized_chars_max']}",
            flush=True,
        )
        for item in allocation_profile["top_sites"]:
            print(
                "allocation site "
                f"bytes={item['positive_live_bytes']} "
                f"blocks={item['positive_live_blocks']} "
                f"{item['site']}",
                flush=True,
            )
    if stage_metrics is not None:
        for name, values in stage_metrics["stages"].items():
            print(
                f"stage={name} calls={values['calls']} "
                f"ru_minflt={values['ru_minflt']} "
                f"cpu_s={values['cpu_s']:.6f} "
                f"ru_minflt_per_observation="
                f"{values['ru_minflt_per_observation']:.6f} "
                f"cpu_s_per_observation="
                f"{values['cpu_s_per_observation']:.6f} "
                "attribution=inclusive",
                flush=True,
            )
        for name, rows in stage_metrics["cumulative_profiles"].items():
            print(f"stage cProfile cumulative stage={name}", flush=True)
            for row in rows:
                print(
                    f"  function={row['function']} calls={row['calls']} "
                    f"tottime_s={row['tottime_s']:.6f} "
                    f"cumtime_s={row['cumtime_s']:.6f}",
                    flush=True,
                )
    if retention_metrics is not None:
        for sample in retention_metrics["samples"]:
            print(
                f"memory observations={sample['observations']} "
                f"rss_bytes={sample['rss_bytes']} "
                f"ru_maxrss_bytes={sample['ru_maxrss_bytes']} "
                f"traced_current_bytes={sample['traced_current_bytes']}",
                flush=True,
            )
        retained = retention_metrics["retained_2000_to_8000"]
        if retained is not None:
            print(
                "retained traced bytes 2000_to_8000="
                f"{retained['positive_size_diff_bytes']}",
                flush=True,
            )
            for row in retained["top_tracebacks"]:
                print(
                    f"retained allocation size_diff_bytes={row['size_diff_bytes']} "
                    f"count_diff={row['count_diff']} "
                    f"traceback={' | '.join(row['traceback'])}",
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
    focused_profile = args.stage_profile or args.retention_profile
    phases = (
        ("warmup", 1, False),
        ("serial", 1, not focused_profile),
    ) + (() if focused_profile else (("workers", args.workers, True),))
    results: dict[str, dict[str, object]] = {}
    for name, workers, profile in phases:
        output_dir = run_dir / name
        phase_limit = (
            SUBSET_LIMIT
            if (
                args.allocation_profile or args.stage_profile or args.retention_profile
            ) and name == "warmup"
            else args.limit
        )
        code = _spawn(
            [
                "--phase",
                "child",
                "--workers",
                str(workers),
                "--limit",
                str(phase_limit),
                "--root",
                str(args.root),
                "--output-dir",
                str(output_dir),
                *(["--profile"] if profile else []),
                *(
                    ["--allocation-profile"]
                    if args.allocation_profile and name != "warmup"
                    else []
                ),
                *(
                    ["--stage-profile"]
                    if args.stage_profile and name == "serial"
                    else []
                ),
                *(
                    ["--retention-profile"]
                    if args.retention_profile and name == "serial"
                    else []
                ),
            ],
            env,
        )
        if code != 0:
            print(f"{name} exited {code}", file=sys.stderr)
            return code
        results[name] = _load_result(output_dir / _RESULT_NAME)
    if focused_profile:
        serial = results["serial"]
        print(
            f"warm-score-sys focused profile observations={serial['observations']} "
            f"parent_sys_cpu_s={serial['parent_sys_cpu_s']} "
            f"parent_ru_minflt={serial['parent_ru_minflt']} "
            f"residual_sha256={serial['residual_sha256']}",
            flush=True,
        )
        return 0
    serial = results["serial"]
    parallel = results["workers"]
    if serial["subset_sha256"] != parallel["subset_sha256"]:
        print("serial and worker runs scored different observation subsets", file=sys.stderr)
        return 2
    identical = serial["residual_sha256"] == parallel["residual_sha256"]
    difference = "identical"
    if not identical:
        difference = _first_difference(
            Path(str(serial["residual_path"])),
            Path(str(parallel["residual_path"])),
        )
    observations = int(serial["observations"])
    print("warm-score-sys comparison", flush=True)
    print(f"observations={observations} subset_sha256={serial['subset_sha256']}", flush=True)
    for name, result in (("serial", serial), ("workers", parallel)):
        print(
            f"{name} workers={result['workers']} "
            f"parent_user_cpu_s={float(result['parent_user_cpu_s']):.3f} "
            f"parent_sys_cpu_s={float(result['parent_sys_cpu_s']):.3f} "
            f"children_user_cpu_s={float(result['child_user_cpu_s']):.3f} "
            f"children_sys_cpu_s={float(result['child_sys_cpu_s']):.3f} "
            f"parent_ru_minflt={result['parent_ru_minflt']} "
            f"parent_ru_majflt={result['parent_ru_majflt']} "
            f"parent_ru_nvcsw={result['parent_ru_nvcsw']} "
            f"parent_ru_nivcsw={result['parent_ru_nivcsw']} "
            f"children_ru_minflt={result['children_ru_minflt']} "
            f"children_ru_majflt={result['children_ru_majflt']} "
            f"children_ru_nvcsw={result['children_ru_nvcsw']} "
            f"children_ru_nivcsw={result['children_ru_nivcsw']} "
            f"worker_ipc_payload_bytes_sent={result['worker_ipc_payload_bytes_sent']} "
            f"worker_python_file_opens={result['worker_python_file_opens']} "
            f"worker_stat_calls={result['worker_stat_calls']} "
            f"worker_sqlite_connects={result['worker_sqlite_connects']} "
            f"worker_sqlite_executes={result['worker_sqlite_executes']}",
            flush=True,
        )
    print(f"residual_bytes_identical={identical} {difference}", flush=True)
    if not identical:
        return 2
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("run", "child"), default="run")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--allocation-profile", action="store_true")
    parser.add_argument("--stage-profile", action="store_true")
    parser.add_argument("--retention-profile", action="store_true")
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
    if args.workers < 1:
        print("--workers must be at least 1", file=sys.stderr)
        return 2
    if (args.stage_profile or args.retention_profile) and args.workers != 1:
        print("--stage-profile and --retention-profile require --workers 1", file=sys.stderr)
        return 2
    if args.stage_profile and args.retention_profile:
        print("--stage-profile and --retention-profile are separate runs", file=sys.stderr)
        return 2
    if args.allocation_profile and args.retention_profile:
        print("--allocation-profile and --retention-profile are separate runs", file=sys.stderr)
        return 2
    if args.retention_profile and args.limit != SUBSET_LIMIT:
        print("--retention-profile requires the full 10000 observation run", file=sys.stderr)
        return 2
    if args.phase == "child":
        if args.output_dir is None:
            print("--output-dir is required for a child phase", file=sys.stderr)
            return 2
        return _run_child(args)
    if args.limit != SUBSET_LIMIT and not (
        args.allocation_profile or args.stage_profile
    ):
        print(
            f"warning: measuring {args.limit} observations, not the fixed {SUBSET_LIMIT}",
            file=sys.stderr,
        )
    if args.allocation_profile or args.stage_profile or args.retention_profile:
        print(
            (
                "retention profile covers the full fixed 10000 observations"
                if args.retention_profile
                else f"profile samples {args.limit} rows every "
                f"{SUBSET_LIMIT // args.limit}th row from the fixed {SUBSET_LIMIT}"
            ),
            flush=True,
        )
    return _run_parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
