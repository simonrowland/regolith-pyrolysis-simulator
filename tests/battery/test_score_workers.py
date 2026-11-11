from __future__ import annotations

from decimal import Decimal
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import time

from simulator.battery.enums import AdmissionStatus, Engine, EvidenceClass
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


def _worker_test_predict(
    engine,
    observation,
    *,
    delay_seconds: float = 0.0,
    hang_observation_id: str | None = None,
    hang_seconds: float = 0.0,
    crash_observation_id: str | None = None,
    **_kwargs,
):
    if observation.observation_id == crash_observation_id:
        os._exit(17)
    if observation.observation_id == hang_observation_id:
        time.sleep(hang_seconds)
    elif delay_seconds:
        time.sleep(delay_seconds)
    from simulator.battery.enums import Authority, ExecutionState
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
        unit="Pa",
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


def test_parallel_score_shards_use_progress_instead_of_total_wall_deadline(
    tmp_path: Path, monkeypatch
) -> None:
    import simulator.battery.score as score_module

    monkeypatch.setattr(
        score_module, "_SCORE_SHARD_IDLE_TIMEOUT_SECONDS", 3.0, raising=False
    )
    context = _worker_fixture(experiment_count=16)
    started = time.monotonic()
    written, metadata = _score_store_to_jsonl(
        context,
        tmp_path / "slow-but-progressing.jsonl",
        engines=(Engine.VAPOROCK,),
        predict=partial(_worker_test_predict, delay_seconds=0.25),
        workers=2,
    )

    assert time.monotonic() - started > 3.0
    assert written > 0
    assert metadata.shard_failures == ()


def test_parallel_score_hung_shard_becomes_refusals_and_keeps_other_shards(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import simulator.battery.score as score_module

    monkeypatch.setattr(
        score_module, "_SCORE_SHARD_IDLE_TIMEOUT_SECONDS", 3.0, raising=False
    )
    context = _worker_fixture(experiment_count=8)
    chunks = _partition_score_refs(tuple(context.observations.values()), workers=2)
    failed_chunk_index = next(index for index, chunk in enumerate(chunks) if chunk)
    failed_observation = chunks[failed_chunk_index][0].observation_id
    healthy_chunk = chunks[1 - failed_chunk_index]
    assert healthy_chunk
    written, metadata = _score_store_to_jsonl(
        context,
        tmp_path / "hung-shard.jsonl",
        engines=(Engine.VAPOROCK,),
        predict=partial(
            _worker_test_predict,
            hang_observation_id=failed_observation,
            hang_seconds=10.0,
        ),
        workers=2,
    )

    rows = [
        json.loads(line)
        for line in (tmp_path / "hung-shard.jsonl.partial")
        .read_text(encoding="utf-8")
        .splitlines()[1:]
    ]
    failed_ids = {
        observation.observation_id for observation in chunks[failed_chunk_index]
    }
    healthy_ids = {observation.observation_id for observation in healthy_chunk}
    failed_rows = [row for row in rows if row["reference"] in failed_ids]
    healthy_rows = [row for row in rows if row["reference"] in healthy_ids]
    assert written == len(rows)
    assert failed_rows and healthy_rows
    assert all(
        row["execution"]["state"] == "attempted_unavailable"
        and row["refusal"]["reason"] == "attempted_unavailable"
        and row["refusal"]["detail"]["reason"] == "score_shard_failed"
        for row in failed_rows
    )
    assert any(row["execution"]["state"] == "produced" for row in healthy_rows)
    assert metadata.shard_failures
    assert metadata.engine_cache_counters[Engine.VAPOROCK.value]["hits"] > 0
    assert "score run notice" in capsys.readouterr().err


def test_parallel_score_crashed_shard_becomes_refusals_and_keeps_other_shards(
    tmp_path: Path, capsys
) -> None:
    context = _worker_fixture(experiment_count=8)
    chunks = _partition_score_refs(tuple(context.observations.values()), workers=2)
    failed_chunk_index = next(index for index, chunk in enumerate(chunks) if chunk)
    failed_observation = chunks[failed_chunk_index][0].observation_id
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

    rows = [
        json.loads(line)
        for line in (tmp_path / "crashed-shard.jsonl.partial")
        .read_text(encoding="utf-8")
        .splitlines()[1:]
    ]
    failed_ids = {
        observation.observation_id for observation in chunks[failed_chunk_index]
    }
    healthy_ids = {observation.observation_id for observation in healthy_chunk}
    failed_rows = [row for row in rows if row["reference"] in failed_ids]
    healthy_rows = [row for row in rows if row["reference"] in healthy_ids]
    assert written == len(rows)
    assert failed_rows and healthy_rows
    assert all(
        row["execution"]["state"] == "attempted_unavailable"
        and row["refusal"]["detail"]["reason"] == "score_shard_failed"
        for row in failed_rows
    )
    assert any(row["execution"]["state"] == "produced" for row in healthy_rows)
    assert metadata.shard_failures
    assert metadata.engine_cache_counters[Engine.VAPOROCK.value]["hits"] > 0
    assert "score run notice" in capsys.readouterr().err


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


def test_jsonl_sort_preserves_append_order_for_tied_rows(
    tmp_path: Path, monkeypatch
) -> None:
    class _Aggregate:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def add(self, _payload):
            return None

    monkeypatch.setattr("simulator.battery.score._ScorePayloadAccumulator", _Aggregate)
    stream = _ResidualJsonlStream(tmp_path / "tied.jsonl")
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
    class _Aggregate:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def add(self, _payload):
            return None

    monkeypatch.setattr("simulator.battery.score._ScorePayloadAccumulator", _Aggregate)
    stream = _ResidualJsonlStream(tmp_path / "sharded-ties.jsonl")
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
