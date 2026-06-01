from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, replace
import math
import multiprocessing
import queue
import sqlite3
import threading

import pytest

from simulator.optimize.evalspec import EvalSpec, cache_key, current_code_version
from simulator.optimize.evaluate import FailureCategory, RunReference, ScoredResult
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.results_store import ResultStore, SCHEMA_VERSION


def _base_spec(**overrides: object) -> EvalSpec:
    data = {
        "recipe_id": "recipe-id",
        "feedstock_recipe_digest": "feedstock-recipe-digest",
        "feedstock_id": "lunar_mare_low_ti",
        "profile_id": "oxygen-yield-v1",
        "fidelity": "fast",
        "code_version": current_code_version(),
        "data_digests": {
            "setpoints": "setpoints-digest",
            "feedstocks": "feedstock-digest",
            "vapor_pressures": "vapor-digest",
            "profile": "profile-digest",
        },
        "chemistry_kernel": {
            "engine": "builtin",
            "allow_builtin_fallback": False,
            "pressure_Pa": 0.001,
        },
        "campaign": "C0",
        "hours": 24,
        "mass_kg": 1000.0,
        "additives_kg": {"CaO": 1.5},
        "track": "pyrolysis",
        "backend_name": "stub",
        "runtime_campaign_overrides": {"C0": {"hold_time_h": 1.0}},
    }
    data.update(overrides)
    return EvalSpec(**data)


def _margin(
    gate: str = "delivered_stream_purity",
    feasible: bool = True,
    *,
    margin: float | None = None,
    observed: float | None = None,
) -> GateMargin:
    return GateMargin(
        gate=gate,
        feasible=feasible,
        margin=margin if margin is not None else (0.25 if feasible else -0.25),
        threshold=ThresholdSpec(
            id=f"{gate}-threshold",
            value=0.95,
            units="fraction",
            source="profile",
            source_ref="test profile",
        ),
        observed=observed if observed is not None else (0.98 if feasible else 0.90),
        detail="test margin",
    )


def _objectives(oxygen: float = 10.0, energy: float = 2.0) -> ObjectiveVector:
    return ObjectiveVector(
        (
            ObjectiveValue("oxygen_kg", "maximize", oxygen, "kg", ordinal=0),
            ObjectiveValue("energy_kWh", "minimize", energy, "kWh", ordinal=1),
        )
    )


def _scored(
    spec: EvalSpec,
    *,
    candidate_id: str = "candidate-a",
    oxygen: float = 10.0,
    energy: float = 2.0,
    objectives: ObjectiveVector | None = None,
    margins: Mapping[str, GateMargin] | None = None,
    result_blob: dict[str, object] | None = None,
) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=objectives or _objectives(oxygen, energy),
        feasibility_margins=margins or {"delivered_stream_purity": _margin()},
        failing_gates=(),
        run_reference=RunReference(
            status="ok",
            trace=result_blob or {"hours": [{"hour": 1, "oxygen_kg": oxygen}]},
            product_summary={"oxygen_kg": oxygen},
        ),
        notes=("stored",),
    )


def _eval_spec_payload(spec: EvalSpec) -> dict[str, object]:
    return {field.name: _plain(getattr(spec, field.name)) for field in fields(EvalSpec)}


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _process_store_writer(
    db_path: str,
    spec_payload: dict[str, object],
    start: multiprocessing.synchronize.Event,
    errors: multiprocessing.queues.Queue,
    offset: int,
    count: int,
) -> None:
    try:
        spec = EvalSpec(**spec_payload)
        store = ResultStore(
            db_path,
            current_code_version=spec.code_version,
            current_data_digests=spec.data_digests,
            busy_timeout_ms=10000,
        )
        start.wait(10)
        for idx in range(count):
            recipe_idx = offset + idx
            next_spec = replace(spec, recipe_id=f"process-{recipe_idx}")
            store.store(
                next_spec,
                _scored(
                    next_spec,
                    candidate_id=f"process-{recipe_idx}",
                    oxygen=float(recipe_idx),
                ),
                created_at=f"tp{recipe_idx}",
            )
    except BaseException as exc:  # pragma: no cover - asserted in parent process
        errors.put(repr(exc))


def _process_store_reader(
    db_path: str,
    spec_payload: dict[str, object],
    start: multiprocessing.synchronize.Event,
    errors: multiprocessing.queues.Queue,
    iterations: int,
) -> None:
    try:
        spec = EvalSpec(**spec_payload)
        store = ResultStore(
            db_path,
            current_code_version=spec.code_version,
            current_data_digests=spec.data_digests,
            busy_timeout_ms=10000,
        )
        start.wait(10)
        for _ in range(iterations):
            store.query(spec.feedstock_id, profile_id=spec.profile_id, fidelity=spec.fidelity)
    except BaseException as exc:  # pragma: no cover - asserted in parent process
        errors.put(repr(exc))


def _infeasible(spec: EvalSpec) -> ScoredResult:
    return ScoredResult(
        candidate_id="candidate-bad",
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=False,
        failure_category=FailureCategory.INFEASIBLE_RECIPE,
        feasibility_margins={"delivered_stream_purity": _margin(feasible=False)},
        failing_gates=("delivered_stream_purity",),
        run_reference=RunReference(
            status="ok",
            trace={"hours": [{"hour": 1, "oxygen_kg": 0.0}]},
            product_summary={"oxygen_kg": 0.0},
        ),
    )


def test_round_trip_lossless_lookup(tmp_path) -> None:
    spec = _base_spec()
    scored = _scored(spec, result_blob={"hours": [{"hour": 1}], "status": "ok"})
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )

    store.store(spec, scored, created_at="2026-05-31T00:00:00Z")

    loaded = store.lookup(spec)
    assert loaded == scored
    assert loaded is not None
    assert loaded.run_reference is not None
    assert loaded.run_reference.trace == {"hours": [{"hour": 1}], "status": "ok"}
    assert loaded.run_reference.product_summary == {"oxygen_kg": 10.0}


def test_store_rejects_nonfinite_margin_numbers(tmp_path) -> None:
    spec = _base_spec()
    store = ResultStore(tmp_path / "results.sqlite")
    scored = _scored(
        spec,
        margins={"delivered_stream_purity": _margin(margin=math.nan)},
    )

    with pytest.raises(ValueError, match="delivered_stream_purity.margin is non-finite"):
        store.store(spec, scored, created_at="2026-01-01T00:00:00Z")

    assert store.lookup(spec) is None


def test_lookup_miss_returns_none(tmp_path) -> None:
    store = ResultStore(tmp_path / "results.sqlite")
    assert store.lookup(_base_spec()) is None


def test_idempotent_upsert_latest_wins(tmp_path) -> None:
    spec = _base_spec()
    store = ResultStore(tmp_path / "results.sqlite")
    store.store(spec, _scored(spec, candidate_id="old", oxygen=1.0), created_at="t1")
    store.store(spec, _scored(spec, candidate_id="new", oxygen=2.0), created_at="t2")

    with sqlite3.connect(tmp_path / "results.sqlite") as conn:
        row_count = conn.execute("SELECT count(*) FROM results").fetchone()[0]

    loaded = store.lookup(spec)
    assert row_count == 1
    assert loaded is not None
    assert loaded.candidate_id == "new"
    assert loaded.objectives is not None
    assert loaded.objectives.as_mapping()["oxygen_kg"] == 2.0


def test_query_exact_selector_and_version_scoped(tmp_path) -> None:
    current = _base_spec(recipe_id="current-recipe")
    wrong_profile = replace(current, recipe_id="wrong-profile", profile_id="other")
    wrong_fidelity = replace(current, recipe_id="wrong-fidelity", fidelity="full")
    stale_code = replace(current, recipe_id="stale-code", code_version="old-version")
    stale_data = replace(
        current,
        recipe_id="stale-data",
        data_digests={**current.data_digests, "profile": "old-profile-digest"},
    )
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=current.code_version,
        current_data_digests=current.data_digests,
    )

    for idx, spec in enumerate(
        (current, wrong_profile, wrong_fidelity, stale_code, stale_data)
    ):
        store.store(
            spec,
            _scored(spec, candidate_id=f"candidate-{idx}", oxygen=float(idx + 1)),
            created_at=f"t{idx}",
        )

    results = store.query(
        current.feedstock_id,
        profile_id=current.profile_id,
        fidelity=current.fidelity,
    )

    assert [result.candidate_id for result in results] == ["candidate-0"]
    assert store.lookup(stale_code) is not None


def test_selector_reads_require_explicit_scope_and_never_infer_current(tmp_path) -> None:
    stale = _base_spec(code_version="v1")
    current = replace(stale, recipe_id="current-recipe", code_version="v2")
    unscoped = ResultStore(tmp_path / "results.sqlite")
    unscoped.store(stale, _scored(stale, candidate_id="stale"), created_at="t1")

    with pytest.raises(ValueError, match="current code_version"):
        unscoped.query(stale.feedstock_id)
    with pytest.raises(ValueError, match="current code_version"):
        unscoped.best(stale.feedstock_id)

    scoped = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=current.code_version,
        current_data_digests=current.data_digests,
    )

    assert scoped.query(current.feedstock_id) == []
    assert scoped.best(current.feedstock_id) is None
    assert scoped.lookup(stale) is not None


def test_best_returns_best_feasible_with_deterministic_tie_break(tmp_path) -> None:
    spec_a = _base_spec(recipe_id="recipe-a")
    spec_b = replace(spec_a, recipe_id="recipe-b")
    spec_c = replace(spec_a, recipe_id="recipe-c")
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=spec_a.code_version,
        current_data_digests=spec_a.data_digests,
    )
    store.store(spec_a, _scored(spec_a, candidate_id="a", oxygen=5.0), created_at="t1")
    store.store(spec_b, _scored(spec_b, candidate_id="b", oxygen=7.0), created_at="t2")
    store.store(spec_c, _infeasible(spec_c), created_at="t3")

    best = store.best(spec_a.feedstock_id, objective_metric="oxygen_kg")

    assert best is not None
    assert best.candidate_id == "b"


def test_best_defaults_to_profile_primary_and_honors_direction(tmp_path) -> None:
    spec_a = _base_spec(recipe_id="recipe-a")
    spec_b = replace(spec_a, recipe_id="recipe-b")
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=spec_a.code_version,
        current_data_digests=spec_a.data_digests,
    )
    store.store(
        spec_a,
        _scored(
            spec_a,
            candidate_id="low-oxygen-low-energy",
            objectives=ObjectiveVector(
                (
                    ObjectiveValue("energy_kWh", "minimize", 1.0, "kWh", ordinal=1),
                    ObjectiveValue("oxygen_kg", "maximize", 5.0, "kg", ordinal=0),
                )
            ),
        ),
        created_at="t1",
    )
    store.store(
        spec_b,
        _scored(
            spec_b,
            candidate_id="high-oxygen-high-energy",
            objectives=ObjectiveVector(
                (
                    ObjectiveValue("energy_kWh", "minimize", 9.0, "kWh", ordinal=1),
                    ObjectiveValue("oxygen_kg", "maximize", 8.0, "kg", ordinal=0),
                )
            ),
        ),
        created_at="t2",
    )

    default_best = store.best(spec_a.feedstock_id)
    explicit_energy_best = store.best(spec_a.feedstock_id, objective_metric="energy_kWh")

    assert default_best is not None
    assert default_best.candidate_id == "high-oxygen-high-energy"
    assert explicit_energy_best is not None
    assert explicit_energy_best.candidate_id == "low-oxygen-low-energy"


def test_unknown_objective_sense_raises_at_construction_and_deserialization(tmp_path) -> None:
    spec = _base_spec()
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )
    with pytest.raises(ValueError, match="objective sense"):
        ObjectiveValue("oxygen_kg", "sideways", 1.0, "kg", ordinal=0)

    store.store(spec, _scored(spec), created_at="t1")
    with sqlite3.connect(tmp_path / "results.sqlite") as conn:
        conn.execute(
            "UPDATE results SET objectives = ? WHERE cache_key = ?",
            (
                '[{"metric":"oxygen_kg","sense":"sideways","value":1.0,"units":"kg","ordinal":0}]',
                cache_key(spec),
            ),
        )

    with pytest.raises(ValueError, match="objective sense"):
        store.lookup(spec)

    with sqlite3.connect(tmp_path / "results.sqlite") as conn:
        conn.execute(
            "UPDATE results SET objectives = ? WHERE cache_key = ?",
            (
                '[{"metric":"oxygen_kg","value":1.0,"units":"kg","ordinal":0}]',
                cache_key(spec),
            ),
        )

    with pytest.raises(ValueError, match="objective sense"):
        store.lookup(spec)


def test_wal_busy_timeout_supports_concurrent_reader_and_writer(tmp_path) -> None:
    spec = _base_spec(recipe_id="seed")
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
        busy_timeout_ms=2000,
    )
    store.store(spec, _scored(spec, candidate_id="seed"), created_at="t0")
    errors: list[BaseException] = []
    start = threading.Event()

    def writer() -> None:
        try:
            start.wait()
            for idx in range(20):
                next_spec = replace(spec, recipe_id=f"writer-{idx}")
                store.store(
                    next_spec,
                    _scored(next_spec, candidate_id=f"writer-{idx}", oxygen=idx),
                    created_at=f"tw{idx}",
                )
        except BaseException as exc:  # pragma: no cover - asserted in parent thread
            errors.append(exc)

    def reader() -> None:
        try:
            start.wait()
            for _ in range(40):
                store.query(spec.feedstock_id, profile_id=spec.profile_id, fidelity=spec.fidelity)
        except BaseException as exc:  # pragma: no cover - asserted in parent thread
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert store.best(spec.feedstock_id, objective_metric="oxygen_kg") is not None


def test_cross_process_writers_and_reader_are_serialized_without_lost_rows(tmp_path) -> None:
    spec = _base_spec(recipe_id="seed")
    path = tmp_path / "results.sqlite"
    ResultStore(
        path,
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
        busy_timeout_ms=10000,
    ).initialize()
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    errors = ctx.Queue()
    writers = [
        ctx.Process(
            target=_process_store_writer,
            args=(str(path), _eval_spec_payload(spec), start, errors, worker_idx * 10, 6),
        )
        for worker_idx in range(3)
    ]
    reader = ctx.Process(
        target=_process_store_reader,
        args=(str(path), _eval_spec_payload(spec), start, errors, 20),
    )
    processes = [*writers, reader]

    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=30)

    failures: list[str] = []
    for process in processes:
        if process.exitcode != 0:
            failures.append(f"{process.name} exit={process.exitcode}")
    while True:
        try:
            failures.append(errors.get_nowait())
        except queue.Empty:
            break

    store = ResultStore(
        path,
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )
    rows = store.query(spec.feedstock_id, profile_id=spec.profile_id, fidelity=spec.fidelity)

    assert failures == []
    assert {row.candidate_id for row in rows} == {
        f"process-{idx}"
        for worker_idx in range(3)
        for idx in range(worker_idx * 10, worker_idx * 10 + 6)
    }


def test_schema_version_stamped_and_migration_smoke(tmp_path) -> None:
    path = tmp_path / "results.sqlite"
    store = ResultStore(path)
    assert store.schema_version == SCHEMA_VERSION

    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE store_meta SET value = '0' WHERE key = 'schema_version'"
        )

    migrated = ResultStore(path)
    assert migrated.schema_version == SCHEMA_VERSION
