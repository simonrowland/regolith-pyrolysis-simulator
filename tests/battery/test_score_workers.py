from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import _thread
from types import SimpleNamespace

import pytest

from simulator.battery.enums import (
    QUANTITY_UNITS,
    AdmissionStatus,
    Engine,
    EvidenceClass,
    PerBasis,
    Phase,
)
from simulator.battery.records import Reaction, ReactionTerm, Species, State
from simulator.battery.score import (
    _partition_score_refs,
    _ResidualJsonlStream,
    _ResidualJsonlRows,
    ScoreContext,
    _score_store_to_jsonl,
    _write_headline_summary_from_accumulator_json,
    derive_store_stamp,
)
from tests.battery import factories as F


def _worker_fixture(experiment_count: int = 30) -> ScoreContext:
    work = F.work()
    experiments = {
        experiment_id: F.tabulation_experiment(
            experiment_id=experiment_id,
            work_id=work.work_id,
        )
        for experiment_id in (
            f"worker-exp-{index:02d}" for index in range(experiment_count)
        )
    }
    observations = {}
    origins = {}
    for experiment_id in experiments:
        for point in range(2):
            observation_id = f"{experiment_id}-point-{point}"
            observation = F.observation(
                observation_id,
                experiment_id,
                F.psat_identity("Na", T_K=Decimal(1100 + point)),
                Decimal(1000 + point),
                evidence=EvidenceClass.MEASURED_DIRECT,
                admission=AdmissionStatus.ADMITTED,
                source_id=work.work_id,
            )
            observations[observation_id] = observation
            origins[observation_id] = "gibbs_battery_residual_ledger.yaml"
    return ScoreContext(
        works={work.work_id: work},
        experiments=experiments,
        observations=observations,
        origins=origins,
        extract_review={},
    )


def _condensed_frame_diagnostic_fixture() -> tuple[ScoreContext, str]:
    from simulator.battery.generators.janaf import (
        janaf_condensed_element_gas_delta_fG_kJ_per_mol,
    )

    temperature = Decimal("1960")
    source_id = "shornikov-2011-cao-mgo-alumina-melts"
    experiment_id = "worker-condensed-frame"
    work = F.work()
    experiment = F.tabulation_experiment(
        experiment_id=experiment_id,
        work_id=work.work_id,
        T_K=temperature,
    )
    calcium_gas = Species("Ca", Phase.G)
    calcium_liquid = Species("Ca", Phase.L)
    diagnostic_identity = replace(
        F.oxide_identity(
            "Ca", Phase.G, T_K=temperature, metal_formula="Ca"
        ),
        per=State.of(PerBasis.MOL_SPECIES),
        reaction=State.of(
            Reaction(
                (
                    ReactionTerm(calcium_gas, Fraction(1)),
                    ReactionTerm(calcium_liquid, Fraction(-1)),
                )
            )
        ),
        formation_elements=State.of((("Ca", calcium_liquid),)),
    )
    diagnostic = replace(
        F.observation(
            "worker-condensed-frame-calcium-gas",
            experiment_id,
            diagnostic_identity,
            janaf_condensed_element_gas_delta_fG_kJ_per_mol(
                "Ca", temperature
            ),
            evidence=EvidenceClass.COMPILATION_ASSESSED,
            source_id=source_id,
        ),
        locator=F.loc(page=2, table="1"),
    )
    target_id = "worker-condensed-frame-calcium-oxide"
    target = replace(
        F.observation(
            target_id,
            experiment_id,
            F.cao_identity(Phase.L, T_K=temperature),
            Decimal("-500"),
            evidence=EvidenceClass.COMPILATION_ASSESSED,
            source_id=source_id,
        ),
        locator=F.loc(page=2, table="1"),
    )
    return (
        ScoreContext(
            works={work.work_id: work},
            experiments={experiment_id: experiment},
            observations={
                diagnostic.observation_id: diagnostic,
                target.observation_id: target,
            },
        ),
        target_id,
    )


def _worker_test_predict(
    engine,
    observation,
    *,
    crash_observation_id: str | None = None,
    delay_seconds: float = 0.0,
    **_kwargs,
):
    if observation.observation_id == crash_observation_id:
        os._exit(17)
    if delay_seconds:
        time.sleep(delay_seconds)
    from simulator.battery.enums import Authority, ExecutionState
    from simulator.battery.identity import quantity_token
    from simulator.battery.records import Execution
    from simulator.battery.score import EnginePrediction
    from simulator.engine_cache import _record_engine_cache_counters

    _record_engine_cache_counters(
        {engine.value: {"hits": 1, "misses": 0, "stores": 0}}
    )
    return EnginePrediction(
        engine=engine,
        channel=engine.value,
        execution=Execution(
            state=ExecutionState.PRODUCED,
            call_evidence="test:worker_predict",
        ),
        value=Decimal("1"),
        unit=QUANTITY_UNITS[quantity_token(observation.identity)],
        authority=Authority.CERTIFIED,
        coefficient_sources=("test",),
        lineage_complete=True,
        identity=observation.identity,
    )


def _score_rows_and_summary(
    context: ScoreContext, path: Path, workers: int | None
) -> tuple[bytes, bytes]:
    options = {} if workers is None else {"workers": workers}
    written, metadata = _score_store_to_jsonl(
        context,
        path,
        engines=(Engine.INTERNAL_ANALYTICAL, Engine.VAPOROCK),
        **options,
    )
    assert written > 0
    partial_path = path.with_name(path.name + ".partial")
    rows = _ResidualJsonlRows(partial_path, metadata=metadata)
    assert sum(1 for _ in rows) == written
    summary_path = path.with_suffix(".summary.json")
    assert metadata.aggregate is not None
    _write_headline_summary_from_accumulator_json(
        metadata.aggregate,
        summary_path,
        store_stamp=derive_store_stamp(),
    )
    return partial_path.read_bytes(), summary_path.read_bytes()


def test_serial_scoring_fixture_has_stable_canonical_rows(tmp_path: Path) -> None:
    rows, _summary = _score_rows_and_summary(
        _worker_fixture(), tmp_path / "serial.jsonl", workers=None
    )
    canonical_rows = b"\n".join(rows.splitlines()[1:])
    assert hashlib.sha256(canonical_rows).hexdigest() == (
        "d0be8fc51724d79f41e6df08c43f628c7d3de860ba9f6bfff89423a264c94de3"
    )


def test_parallel_scoring_matches_serial_rows_and_summary(tmp_path: Path) -> None:
    context = _worker_fixture()
    serial_rows, serial_summary = _score_rows_and_summary(
        context, tmp_path / "serial.jsonl", workers=1
    )
    parallel_rows, parallel_summary = _score_rows_and_summary(
        context, tmp_path / "parallel.jsonl", workers=3
    )
    assert parallel_rows == serial_rows
    assert parallel_summary == serial_summary


def test_parallel_score_shards_reuse_parent_store_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.score as score_mod

    context = _worker_fixture(experiment_count=12)
    parent_stamp = derive_store_stamp(score_mod.REPO_ROOT)
    derive_calls = 0

    def changing_store_stamp(_root: Path | None = None) -> dict[str, object]:
        nonlocal derive_calls
        derive_calls += 1
        if derive_calls == 1:
            return parent_stamp
        return {
            **parent_stamp,
            "revision": f"{parent_stamp['revision']}x",
        }

    def run_workers_in_process(tasks, worker, *, initializer, initargs, **_kwargs):
        initializer(*initargs)
        return [worker(task) for task in tasks]

    monkeypatch.setattr(score_mod, "derive_store_stamp", changing_store_stamp)
    monkeypatch.setattr(score_mod, "supervised_process_map", run_workers_in_process)

    path = tmp_path / "changing-store-stamp.jsonl"
    written, _metadata = _score_store_to_jsonl(
        context,
        path,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        predict=_worker_test_predict,
        workers=2,
    )

    assert written > 0
    assert derive_calls == 1
    header = path.with_name(path.name + ".partial").read_bytes().splitlines()[0]
    assert json.loads(header) == parent_stamp


def test_parallel_workers_inherit_prepared_run_and_reference_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.score as score_mod
    from simulator.battery import score_run
    from simulator.battery.score_run import _PreparedScoreRun

    context = _worker_fixture(experiment_count=4)
    captured: dict[str, object] = {}

    def run_workers_in_process(tasks, worker, *, initializer, initargs, mp_context, **_kwargs):
        captured["tasks"] = tasks
        captured["initargs"] = initargs
        captured["method"] = mp_context.get_start_method()
        captured["inherited"] = score_run._INHERITED_SCORE_RUN is not None
        initializer(*initargs)
        return [worker(task) for task in tasks]

    monkeypatch.setattr(score_run, "_score_fork_blockers", lambda **_kwargs: ())
    monkeypatch.setattr(score_mod, "supervised_process_map", run_workers_in_process)
    written, _metadata = _score_store_to_jsonl(
        context,
        tmp_path / "inherited-prepared.jsonl",
        engines=(Engine.INTERNAL_ANALYTICAL,),
        predict=_worker_test_predict,
        workers=2,
    )

    initargs = captured["initargs"]
    tasks = captured["tasks"]
    assert written > 0
    assert captured["method"] == "fork"
    assert captured["inherited"] is True
    assert isinstance(initargs, tuple)
    assert initargs
    assert all(not isinstance(arg, _PreparedScoreRun) for arg in initargs)
    assert initargs[-2] is None
    assert isinstance(initargs[-1], dict)
    assert tasks
    assert all(isinstance(ref_id, str) for task in tasks for ref_id in task[2])
    assert score_run._INHERITED_SCORE_RUN is None


def test_dirty_process_pickles_the_prepared_run_once_and_spawns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pickle

    import simulator.battery.score as score_mod
    from simulator.battery import score_run

    context = _worker_fixture(experiment_count=4)
    captured: dict[str, object] = {}
    dumped: list[str] = []
    original_dump = pickle.dump

    def counting_dump(value, handle, *args, **kwargs):
        dumped.append(type(value).__name__)
        return original_dump(value, handle, *args, **kwargs)

    def run_workers_in_process(tasks, worker, *, initializer, initargs, mp_context, **_kwargs):
        captured["initargs"] = initargs
        captured["method"] = mp_context.get_start_method()
        captured["inherited"] = score_run._INHERITED_SCORE_RUN
        initializer(*initargs)
        loaded_ids = tuple(score_run._SCORE_WORKER_REFS)
        captured["loaded_ids"] = loaded_ids
        return [worker(task) for task in tasks]

    monkeypatch.setattr(
        score_run,
        "_score_fork_blockers",
        lambda **_kwargs: ("threads:score-fork-blocker",),
    )
    monkeypatch.setattr(pickle, "dump", counting_dump)
    monkeypatch.setattr(score_mod, "supervised_process_map", run_workers_in_process)
    written, _metadata = _score_store_to_jsonl(
        context,
        tmp_path / "spawn-prepared.jsonl",
        engines=(Engine.INTERNAL_ANALYTICAL,),
        predict=_worker_test_predict,
        workers=2,
    )

    initargs = captured["initargs"]
    assert written > 0
    assert captured["method"] == "spawn"
    assert captured["inherited"] is None
    assert dumped == ["_PreparedScoreRun"]
    assert isinstance(initargs, tuple)
    assert isinstance(initargs[-2], str)
    assert isinstance(initargs[-1], dict)
    assert captured["loaded_ids"]
    assert score_run._INHERITED_SCORE_RUN is None


def test_score_fork_blockers_name_threads_modules_and_cache_connections() -> None:
    import sys
    import types

    from simulator import engine_cache
    from simulator.battery.score_run import _score_fork_blockers

    release = threading.Event()
    started = threading.Event()

    def _hold() -> None:
        started.set()
        release.wait(5)

    thread = threading.Thread(target=_hold, name="score-fork-blocker")
    thread.start()
    assert started.wait(5)
    try:
        reasons = _score_fork_blockers()
    finally:
        release.set()
        thread.join(5)
    assert any(
        reason.startswith("threads:") and "score-fork-blocker" in reason
        for reason in reasons
    )

    inserted_module = "rubicon.objc" not in sys.modules
    if inserted_module:
        sys.modules["rubicon.objc"] = types.ModuleType("rubicon.objc")
    try:
        reasons = _score_fork_blockers()
    finally:
        if inserted_module:
            del sys.modules["rubicon.objc"]
    assert any(
        reason.startswith("modules:") and "rubicon.objc" in reason
        for reason in reasons
    )

    before = _score_fork_blockers()
    connection = sqlite3.connect(":memory:")
    engine_cache._CONNECTIONS[("score-fork-blocker", False)] = connection
    try:
        after = _score_fork_blockers()
    finally:
        del engine_cache._CONNECTIONS[("score-fork-blocker", False)]
        connection.close()

    def _connection_count(reasons: tuple[str, ...]) -> int:
        for reason in reasons:
            if reason.startswith("cache_connections:"):
                return int(reason.removeprefix("cache_connections:"))
        return 0

    assert _connection_count(after) == _connection_count(before) + 1
    assert _score_fork_blockers(residual_sqlite_open=True)[-1] == (
        "residual sqlite is open"
    )


def _recorded_start_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    import simulator.battery.score as score_mod

    captured: dict[str, object] = {}

    def run_workers_in_process(
        tasks, worker, *, initializer, initargs, mp_context, **_kwargs
    ):
        captured["method"] = mp_context.get_start_method()
        initializer(*initargs)
        return [worker(task) for task in tasks]

    monkeypatch.setattr(score_mod, "supervised_process_map", run_workers_in_process)
    written, _metadata = _score_store_to_jsonl(
        _worker_fixture(experiment_count=2),
        tmp_path / "fork-block.jsonl",
        engines=(Engine.INTERNAL_ANALYTICAL,),
        predict=_worker_test_predict,
        workers=2,
    )
    assert written > 0
    method = captured["method"]
    assert isinstance(method, str)
    return method


def test_raw_interpreter_thread_refuses_fork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A thread started outside threading still makes os.fork warn."""

    from simulator.battery.score_run import _score_fork_blockers

    hold = _thread.allocate_lock()
    hold.acquire()
    started = _thread.allocate_lock()
    started.acquire()

    def _hold() -> None:
        started.release()
        hold.acquire()
        hold.release()

    before_idents = {thread.ident for thread in threading.enumerate()}
    _thread.start_new_thread(_hold, ())
    assert started.acquire(timeout=5)
    try:
        enum_idents = {thread.ident for thread in threading.enumerate()}
        frame_idents = set(sys._current_frames())
        assert enum_idents == before_idents
        assert frame_idents - enum_idents
        assert len(frame_idents) > 1
        thread_reasons = [
            reason
            for reason in _score_fork_blockers()
            if reason.startswith("threads:")
        ]
        assert thread_reasons
        count = int(thread_reasons[0].rsplit("count:", 1)[1])
        assert count >= len(frame_idents)
        assert count > 1
        assert _recorded_start_method(tmp_path, monkeypatch) == "spawn"
    finally:
        hold.release()


def test_phaseobjc_loader_marks_runtime_and_spawns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.engine_local_config as config
    from simulator.battery.score_run import _score_fork_blockers

    monkeypatch.setattr(config, "_THERMOENGINE_PHASEOBJC_LOADED", False)
    loaded: list[str] = []
    real_cdll = config.ctypes.CDLL

    def fake_cdll(path, *args, **kwargs):
        if str(path).endswith("libphaseobjc.dylib"):
            loaded.append(str(path))
            return object()
        return real_cdll(path, *args, **kwargs)

    monkeypatch.setattr(config.ctypes, "CDLL", fake_cdll)
    monkeypatch.setattr(
        config,
        "_thermoengine_phaseobjc_library_path",
        lambda: Path("/configured/libphaseobjc.dylib"),
    )
    loaded_path = config._load_thermoengine_phaseobjc_library()
    assert loaded_path == Path("/configured/libphaseobjc.dylib")
    assert loaded == ["/configured/libphaseobjc.dylib"]
    assert config.thermoengine_phaseobjc_runtime_loaded() is True
    assert "native_runtime:thermoengine_phaseobjc" in _score_fork_blockers()
    assert _recorded_start_method(tmp_path, monkeypatch) == "spawn"


def test_failed_phaseobjc_load_does_not_mark_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.engine_local_config as config
    from simulator.battery.score_run import _score_fork_blockers

    monkeypatch.setattr(config, "_THERMOENGINE_PHASEOBJC_LOADED", False)
    real_cdll = config.ctypes.CDLL

    def boom(path, *args, **kwargs):
        if str(path).endswith("libphaseobjc.dylib"):
            raise OSError("dlopen failed")
        return real_cdll(path, *args, **kwargs)

    monkeypatch.setattr(config.ctypes, "CDLL", boom)
    monkeypatch.setattr(
        config,
        "_thermoengine_phaseobjc_library_path",
        lambda: Path("/missing/libphaseobjc.dylib"),
    )
    with pytest.raises(ImportError, match="could not be loaded"):
        config._load_thermoengine_phaseobjc_library()
    assert config.thermoengine_phaseobjc_runtime_loaded() is False
    assert "native_runtime:thermoengine_phaseobjc" not in _score_fork_blockers()


def _fresh_python(script: str) -> subprocess.CompletedProcess[str]:
    """Run a script in a new interpreter. Pytest's threads must not count."""

    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root), env.get("PYTHONPATH", "")]
    ).removesuffix(os.pathsep)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_fresh_interpreter_has_no_fork_blocker() -> None:
    """A new score process still has nothing that makes fork unsafe."""

    result = _fresh_python(
        """
import socket
from simulator.battery.score_run import _score_fork_blockers
reasons = _score_fork_blockers()
print("HOST", socket.gethostname())
print("BLOCKERS", len(reasons))
for reason in reasons:
    print("REASON", reason)
"""
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "BLOCKERS 0" in result.stdout, output


def test_real_phaseobjc_cdll_blocks_fork() -> None:
    """The production loader maps libphaseobjc with ctypes.CDLL."""

    result = _fresh_python(
        """
from simulator.engine_local_config import (
    _load_thermoengine_phaseobjc_library,
    thermoengine_phaseobjc_runtime_loaded,
)
from simulator.battery.score_run import _score_fork_blockers
_load_thermoengine_phaseobjc_library()
print("FLAG", int(thermoengine_phaseobjc_runtime_loaded()))
for reason in _score_fork_blockers():
    print("REASON", reason)
"""
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "FLAG 1" in result.stdout
    assert "REASON native_runtime:thermoengine_phaseobjc" in result.stdout


def test_mach_probe_failure_does_not_abort_fork_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ctypes stand-in with no library handle must not raise."""

    import ctypes

    from simulator.battery.score_run import _score_fork_blockers

    def broken_cdll(path, *args, **kwargs):
        return object()

    monkeypatch.setattr(ctypes, "CDLL", broken_cdll)
    assert isinstance(_score_fork_blockers(), tuple)


def test_open_engine_cache_sqlite_spawns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from simulator import engine_cache
    from simulator.battery.score_run import _score_fork_blockers

    connection = sqlite3.connect(":memory:")
    engine_cache._CONNECTIONS[("score-fork-open", False)] = connection
    try:
        assert any(
            reason.startswith("cache_connections:")
            for reason in _score_fork_blockers()
        )
        assert _recorded_start_method(tmp_path, monkeypatch) == "spawn"
    finally:
        del engine_cache._CONNECTIONS[("score-fork-open", False)]
        connection.close()


def test_open_residual_order_sqlite_spawns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.score as score_mod
    from simulator.battery import score_run

    original = score_mod._ResidualJsonlStream
    seen: dict[str, object] = {}

    class _OpenDuringForkCheck(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._probe = sqlite3.connect(":memory:")
            self._database = self._probe

        def _ensure_order_index(self):
            if self._database is getattr(self, "_probe", None):
                self._probe.close()
                self._probe = None
                self._database = None
            super()._ensure_order_index()

    real_blockers = score_run._score_fork_blockers

    def recording_blockers(**kwargs):
        seen.update(kwargs)
        return real_blockers(**kwargs)

    monkeypatch.setattr(score_mod, "_ResidualJsonlStream", _OpenDuringForkCheck)
    monkeypatch.setattr(score_run, "_score_fork_blockers", recording_blockers)
    assert _recorded_start_method(tmp_path, monkeypatch) == "spawn"
    assert seen.get("residual_sqlite_open") is True


@pytest.mark.parametrize("workers", (1, 2), ids=("serial", "worker"))
def test_condensed_frame_diagnostics_reach_serial_and_worker_scoring(
    tmp_path: Path, workers: int
) -> None:
    context, target_id = _condensed_frame_diagnostic_fixture()
    path = tmp_path / f"condensed-frame-{workers}.jsonl"

    written, _metadata = _score_store_to_jsonl(
        context,
        path,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        predict=_worker_test_predict,
        workers=workers,
    )

    assert written == 2
    rows = [
        json.loads(line)
        for line in path.with_name(path.name + ".partial")
        .read_text(encoding="utf-8")
        .splitlines()[1:]
    ]
    target = next(row for row in rows if row["reference"] == target_id)
    assert any(
        notice["reason"] == "condensed_element_reference_from_janaf"
        for notice in target["notices"]
    )


def test_parallel_score_slow_shard_completes_without_wall_timeout(
    tmp_path: Path, monkeypatch
) -> None:
    import simulator.battery.score as score_module

    original_map = score_module.supervised_process_map
    timeout_values = []

    def record_timeout(*args, **kwargs):
        timeout_values.append(kwargs.get("timeout_seconds"))
        return original_map(*args, **kwargs)

    monkeypatch.setattr(score_module, "supervised_process_map", record_timeout)
    written, metadata = _score_store_to_jsonl(
        _worker_fixture(experiment_count=1),
        tmp_path / "slow-shard.jsonl",
        engines=(Engine.VAPOROCK,),
        predict=partial(_worker_test_predict, delay_seconds=0.2),
        workers=2,
    )

    rows = [
        json.loads(line)
        for line in (tmp_path / "slow-shard.jsonl.partial")
        .read_text(encoding="utf-8")
        .splitlines()[1:]
    ]
    assert timeout_values == [None]
    assert written == 2
    assert all(row["execution"]["state"] == "produced" for row in rows)
    assert metadata.shard_failures == ()


def test_score_cli_publishes_partial_crash_run_with_success_exit(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import simulator.battery.score as score_module
    from scripts import battery_score

    context = _worker_fixture(experiment_count=8)
    chunks = _partition_score_refs(tuple(context.observations.values()), workers=2)
    failed_chunk = next(chunk for chunk in chunks if len(chunk) >= 4)
    failed_observation_id = failed_chunk[len(failed_chunk) // 2].observation_id
    root = tmp_path / "cli-root"
    battery_path = root / "data" / "battery"
    battery_path.mkdir(parents=True)
    (battery_path / "migration-report.md").write_text(
        "rows in: 16\nrecords out (observations): 16\nworks: 1\n"
        "experiments: 8\nqueue size: 0\nhard issues: 0\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "data"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture store",
        ],
        check=True,
    )
    monkeypatch.setattr(battery_score, "load_score_context", lambda root=None, *, sources=None: context)
    monkeypatch.setattr(battery_score, "load_legacy_score_rows", lambda _root: [])

    def write_empty_comparisons(_context, path, **_kwargs):
        Path(path).write_text("", encoding="utf-8")

    monkeypatch.setattr(
        battery_score,
        "_write_compilation_comparisons_jsonl",
        write_empty_comparisons,
    )
    original_score = battery_score._score_store_to_jsonl

    def score_with_crash(store_context, path, **kwargs):
        return original_score(
            store_context,
            path,
            predict=partial(
                _worker_test_predict,
                crash_observation_id=failed_observation_id,
            ),
            **kwargs,
        )

    monkeypatch.setattr(battery_score, "_score_store_to_jsonl", score_with_crash)
    result = battery_score.main(
        [
            "--root",
            str(root),
            "--engines",
            Engine.VAPOROCK.value,
            "--workers",
            "2",
        ]
    )

    assert result == 0
    assert "score run notice" in capsys.readouterr().err
    filters = battery_score._scoring_filter_record(
        engines=(Engine.VAPOROCK,),
        rail=None,
        work_id=None,
        limit=None,
        species_formulas=(),
    )
    subset = battery_score._is_subset_run(filters)
    assert subset
    output_directory = battery_score._scoring_output_directory(
        root, filters, subset=subset
    )
    residual_lines = (output_directory / "residuals.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    rows = [json.loads(line) for line in residual_lines[1:]]
    assert len(rows) == 16
    assert any(row["execution"]["state"] == "produced" for row in rows)
    assert any(
        row["execution"]["state"] == "attempted_unavailable"
        and row["refusal"]["detail"]["reason"] == "score_shard_failed"
        for row in rows
    )
    summary = json.loads(
        (output_directory / "score-summary.json").read_text(encoding="utf-8")
    )
    assert summary["subset"] is True
    assert len(summary["score_shard_failures"]) == 1
    assert summary["engine_cache"]["by_engine"][Engine.VAPOROCK.value]["hits"] > 0


def test_parallel_score_crash_preserves_completed_rows_and_publishes_failure(
    tmp_path: Path, capsys
) -> None:
    context = _worker_fixture(experiment_count=8)
    chunks = _partition_score_refs(tuple(context.observations.values()), workers=2)
    failed_chunk_index = next(
        index for index, chunk in enumerate(chunks) if len(chunk) >= 4
    )
    failed_chunk = chunks[failed_chunk_index]
    crash_index = len(failed_chunk) // 2
    failed_observation = failed_chunk[crash_index].observation_id
    healthy_chunk = chunks[1 - failed_chunk_index]
    assert healthy_chunk
    written, metadata = _score_store_to_jsonl(
        context,
        tmp_path / "crashed-shard.jsonl",
        engines=(Engine.VAPOROCK,),
        predict=partial(
            _worker_test_predict,
            crash_observation_id=failed_observation,
        ),
        workers=2,
    )

    residual_bytes = (tmp_path / "crashed-shard.jsonl.partial").read_bytes()
    assert residual_bytes.endswith(b"\n")
    rows = [
        json.loads(line)
        for line in residual_bytes.decode("utf-8").splitlines()[1:]
    ]
    assert len({row["reference"] for row in rows}) == len(rows)
    completed_ids = {
        observation.observation_id for observation in failed_chunk[:crash_index]
    }
    failed_ids = {
        observation.observation_id for observation in failed_chunk[crash_index:]
    }
    healthy_ids = {observation.observation_id for observation in healthy_chunk}
    completed_rows = [row for row in rows if row["reference"] in completed_ids]
    failed_rows = [row for row in rows if row["reference"] in failed_ids]
    healthy_rows = [row for row in rows if row["reference"] in healthy_ids]
    assert written == len(rows)
    assert completed_rows and failed_rows and healthy_rows
    assert all(row["execution"]["state"] == "produced" for row in completed_rows)
    assert all(
        row["execution"]["state"] == "attempted_unavailable"
        and row["refusal"]["detail"]["reason"] == "score_shard_failed"
        for row in failed_rows
    )
    assert any(row["execution"]["state"] == "produced" for row in healthy_rows)
    assert len(failed_rows) == len(failed_ids)
    assert len(metadata.shard_failures) == 1
    assert metadata.engine_cache_counters[Engine.VAPOROCK.value]["hits"] > 0
    assert "score run notice" in capsys.readouterr().err

    from simulator.battery.score import (
        _ResidualJsonlRows,
        _render_score_report_from_payloads_legacy,
        _write_headline_summary_from_accumulator_json,
        derive_store_stamp,
    )

    summary_path = tmp_path / "score-summary.json"
    _write_headline_summary_from_accumulator_json(
        metadata.aggregate,
        summary_path,
        store_stamp=derive_store_stamp(),
        shard_failures=metadata.shard_failures,
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    failure = summary["score_shard_failures"][0]
    assert failure["chunk"] == failed_chunk_index
    assert failure["engine"] == Engine.VAPOROCK.value
    assert failure["refused_rows"] == len(failed_ids)
    assert "BrokenProcessPool" in failure["reason"]

    report = _render_score_report_from_payloads_legacy(
        _ResidualJsonlRows(
            tmp_path / "crashed-shard.jsonl.partial", metadata=metadata
        ),
        context=context,
        engines=(Engine.VAPOROCK,),
        store_stamp=derive_store_stamp(),
        _aggregate=metadata.aggregate,
        shard_failures=metadata.shard_failures,
    )
    assert "## Score shard failures" in report
    assert f"{failed_chunk_index}" in report
    assert "BrokenProcessPool" in report
    assert str(len(failed_ids)) in report


def test_failed_shard_recovery_does_not_add_intrinsic_sibling_to_completed_activity(
    tmp_path: Path,
) -> None:
    work = F.work()
    experiment_id = "completed-activity-crash-exp"
    experiment = F.tabulation_experiment(
        experiment_id=experiment_id,
        work_id=work.work_id,
    )
    activity = F.observation(
        "a-completed-activity",
        experiment_id,
        F.activity_identity(formula="FeO"),
        Decimal("0.5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        admission=AdmissionStatus.ADMITTED,
        source_id=work.work_id,
    )
    crash = F.observation(
        "z-crash-after-activity",
        experiment_id,
        F.psat_identity("Na", T_K=Decimal("1100")),
        Decimal("1000"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        admission=AdmissionStatus.ADMITTED,
        source_id=work.work_id,
    )
    context = ScoreContext(
        works={work.work_id: work},
        experiments={experiment_id: experiment},
        observations={activity.observation_id: activity, crash.observation_id: crash},
        origins={
            activity.observation_id: "gibbs_battery_residual_ledger.yaml",
            crash.observation_id: "gibbs_battery_residual_ledger.yaml",
        },
        extract_review={},
    )

    written, _metadata = _score_store_to_jsonl(
        context,
        tmp_path / "completed-activity.jsonl",
        engines=(Engine.INTERNAL_ANALYTICAL,),
        predict=partial(
            _worker_test_predict,
            crash_observation_id=crash.observation_id,
        ),
        workers=2,
    )

    rows = [
        json.loads(line)
        for line in (tmp_path / "completed-activity.jsonl.partial")
        .read_text(encoding="utf-8")
        .splitlines()[1:]
    ]
    activity_rows = [
        row for row in rows if row["reference"] == activity.observation_id
    ]
    assert written == len(rows)
    assert len(activity_rows) == 1
    assert activity_rows[0]["fo2_mode"] == "shared_ladder"
    assert activity_rows[0]["execution"]["state"] == "produced"


def test_group_partition_keeps_series_and_sossi_cohort_together() -> None:
    from simulator.battery.score import _partition_score_refs

    work = F.work()
    series = tuple(
        F.observation(
            f"series-{point}",
            "series-experiment",
            F.psat_identity("Na"),
            Decimal(1000 + point),
            source_id=work.work_id,
        )
        for point in range(2)
    )
    sossi = tuple(
        F.observation(
            f"sossi-{run}",
            f"sossi::{run}",
            F.psat_identity("Mn"),
            Decimal(1000),
            source_id="kems-012-sossi-2019",
        )
        for run in range(43)
    )
    refs = (*series, *sossi)
    chunks = _partition_score_refs(refs, workers=3)
    owner_by_id = {
        observation.observation_id: index
        for index, chunk in enumerate(chunks)
        for observation in chunk
    }
    assert len({owner_by_id[point.observation_id] for point in series}) == 1
    assert len({owner_by_id[point.observation_id] for point in sossi}) == 1


class _SortAggregate:
    def __init__(self, *args, **kwargs) -> None:
        self.tier_accumulator = SimpleNamespace(selected=set())

    def add(self, _payload):
        return None


def test_jsonl_sort_preserves_append_order_for_tied_rows(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("simulator.battery.score._ScorePayloadAccumulator", _SortAggregate)
    stream = _ResidualJsonlStream(tmp_path / "tied.jsonl")
    stream.selected_reference_ids.update({"observation-1", "selected-without-row"})
    try:
        payloads = [
            {
                "rail": "liquid",
                "reference": "observation-1",
                "key": "same-key",
                "candidate_observation": {
                    "engine": {"name": Engine.VAPOROCK.value}
                },
            },
            {
                "rail": "liquid",
                "reference": "observation-1",
                "key": "same-key",
                "candidate_request": {"engine": Engine.INTERNAL_ANALYTICAL.value},
            },
        ]
        for payload in payloads:
            stream.append_payload_line(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            )
        stream.finalize(
            context=_worker_fixture(),
            engines=(Engine.INTERNAL_ANALYTICAL, Engine.VAPOROCK),
        )
        assert stream.report_aggregate.tier_accumulator.selected == {
            "observation-1", "selected-without-row"
        }
        rows = [
            json.loads(line)
            for line in stream.partial_path.read_text(encoding="utf-8").splitlines()[1:]
        ]
    finally:
        stream.close()

    engine_names = [
        row["candidate_request"]["engine"]
        if "candidate_request" in row
        else row["candidate_observation"]["engine"]["name"]
        for row in rows
    ]
    assert engine_names == [
        Engine.VAPOROCK.value,
        Engine.INTERNAL_ANALYTICAL.value,
    ]


def test_jsonl_sort_restores_serial_order_after_engine_shard_merge(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("simulator.battery.score._ScorePayloadAccumulator", _SortAggregate)
    stream = _ResidualJsonlStream(tmp_path / "sharded-ties.jsonl")
    stream.selected_reference_ids.update({"observation-1", "selected-without-row"})
    try:
        merged_shard_rows = (
            ("point-0", Engine.INTERNAL_ANALYTICAL.value, ("observation-1", 0, 0, 0)),
            ("point-1", Engine.INTERNAL_ANALYTICAL.value, ("observation-1", 1, 0, 0)),
            ("point-0", Engine.VAPOROCK.value, ("observation-1", 0, 1, 0)),
            ("point-1", Engine.VAPOROCK.value, ("observation-1", 1, 1, 0)),
        )
        for point, engine, serial_order in merged_shard_rows:
            payload = {
                "rail": "liquid",
                "reference": "observation-1",
                "key": "same-key",
                "point": point,
                "candidate_request": {"engine": engine},
            }
            stream.append_payload_line(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
                sort_order=serial_order,
            )
        stream.finalize(
            context=_worker_fixture(),
            engines=(Engine.INTERNAL_ANALYTICAL, Engine.VAPOROCK),
        )
        assert stream.report_aggregate.tier_accumulator.selected == {
            "observation-1", "selected-without-row"
        }
        rows = [
            json.loads(line)
            for line in stream.partial_path.read_text(encoding="utf-8").splitlines()[1:]
        ]
    finally:
        stream.close()

    assert [(row["point"], row["candidate_request"]["engine"]) for row in rows] == [
        ("point-0", Engine.INTERNAL_ANALYTICAL.value),
        ("point-0", Engine.VAPOROCK.value),
        ("point-1", Engine.INTERNAL_ANALYTICAL.value),
        ("point-1", Engine.VAPOROCK.value),
    ]
