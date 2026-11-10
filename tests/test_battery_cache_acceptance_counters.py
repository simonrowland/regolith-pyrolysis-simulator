from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from simulator.battery import score
from simulator.battery.score import SCORE_ENGINE_SET
from scripts import battery_cache_equivalence as gate


def _install_fake_slice(
    monkeypatch: Any,
    tmp_path: Path,
    *,
    mismatch: dict[str, bool],
) -> Path:
    spec_path = tmp_path / "slice.json"
    spec_path.write_text(
        json.dumps({"engines": [SCORE_ENGINE_SET[0].value], "workers": 2}),
        encoding="utf-8",
    )
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(gate, "load_score_context", lambda _root: object())
    monkeypatch.setattr(gate, "EngineCache", lambda _path: object())

    def run_slice(_context: object, output_path: Path, **_kwargs: Any):
        run_name = output_path.stem
        if run_name.startswith("cold"):
            counts = {
                SCORE_ENGINE_SET[0].value: {
                    "hits": 0,
                    "misses": 1,
                    "stores": 1,
                    "calls_by_kind": {"equilibrate_cell": 1},
                }
            }
        elif run_name in {"warm", "parallel-warm"}:
            counts = {
                SCORE_ENGINE_SET[0].value: {
                    "hits": 1,
                    "misses": 0,
                    "stores": 0,
                    "calls_by_kind": {},
                }
            }
        else:
            counts = {}
        rows = (
            ["different-row"]
            if mismatch["enabled"] and run_name == "cold"
            else ["same-row"]
        )
        return rows, counts

    monkeypatch.setattr(gate, "_run_slice", run_slice)
    return spec_path


def test_counter_table_is_printed_on_success_and_failure(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    mismatch = {"enabled": False}
    spec_path = _install_fake_slice(monkeypatch, tmp_path, mismatch=mismatch)

    assert gate.main([str(spec_path)]) == 0
    success = capsys.readouterr()
    success_lines = [
        line
        for line in success.out.splitlines()
        if line.startswith("engine_cache_counters run=")
    ]
    assert len(success_lines) == 6
    engine_id = SCORE_ENGINE_SET[0].value
    assert any(
        "engine_cache_counters run=parallel-warm" in line
        and f"{engine_id}:hits=1,misses=0,stores=0,calls_by_kind={{}}" in line
        for line in success_lines
    )

    mismatch["enabled"] = True
    assert gate.main([str(spec_path)]) == 1
    failure = capsys.readouterr()
    failure_lines = [
        line
        for line in failure.out.splitlines()
        if line.startswith("engine_cache_counters run=")
    ]
    assert len(failure_lines) == 6
    assert "canonical residual rows differ from the off run" in failure.err


def test_parallel_warm_worker_counters_are_summed_per_engine(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    class _Stream:
        def __init__(self, path: Path, **_kwargs: Any) -> None:
            self.partial_path = path.with_name(path.name + ".partial")
            self.partial_path.write_bytes(b'{"store_stamp":true}\n')
            self.count = 0
            self.band_values: dict = {}
            self.headline_band_values: dict = {}
            self.tier_band_values: dict = {}
            self.report_aggregate = None

        def append_payload_line(self, *_args: Any, **_kwargs: Any) -> None:
            self.count += 1

        def finalize(self, **_kwargs: Any) -> None:
            return None

        def close(self) -> None:
            return None

    context = object()
    refs = (object(), object())
    prepared = SimpleNamespace(
        context=context,
        engine_set=SCORE_ENGINE_SET,
        refs=refs,
    )
    monkeypatch.setattr(score, "_ResidualJsonlStream", _Stream)
    monkeypatch.setattr(score, "_prepare_score_run", lambda *_args, **_kwargs: prepared)
    monkeypatch.setattr(score, "_partition_score_refs", lambda *_args, **_kwargs: refs)

    def _parallel_map(tasks, _worker, **kwargs):
        assert kwargs["max_workers"] == 2
        results = []
        for index, engine, _refs, shard_path, _root in tasks:
            shard = Path(shard_path)
            shard.write_bytes(b'{"store_stamp":true}\n')
            shard.with_name(shard.name + ".order").write_text("", encoding="utf-8")
            results.append(
                (
                    str(shard),
                    0,
                    {
                        engine.value: {
                            "hits": 1,
                            "misses": 0,
                            "stores": 0,
                            "calls_by_kind": {},
                        }
                    },
                    None,
                )
            )
        assert len(results) == 2 * len(SCORE_ENGINE_SET)
        return results

    monkeypatch.setattr(score, "supervised_process_map", _parallel_map)
    _rows, metadata = score._score_store_to_jsonl(
        context,
        tmp_path / "parallel-warm.jsonl",
        workers=2,
        engine_cache=SimpleNamespace(db_path=tmp_path / "engine-cache.sqlite"),
    )

    assert metadata.engine_cache_counters is not None
    assert set(metadata.engine_cache_counters) == {
        engine.value for engine in SCORE_ENGINE_SET
    }
    for engine in SCORE_ENGINE_SET:
        counters = metadata.engine_cache_counters[engine.value]
        assert counters["hits"] == 2
        assert counters["misses"] == 0
        assert counters["stores"] == 0
        assert counters["calls_by_kind"] == {}
