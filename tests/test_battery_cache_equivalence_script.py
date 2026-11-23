from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from simulator.battery.score import SCORE_ENGINE_SET
from simulator.engine_cache import _engine_cache_path_scope
from scripts import battery_cache_equivalence as gate
from scripts import battery_score

pytestmark = pytest.mark.engine_cache_off


@pytest.mark.parametrize(
    ("scoped_path", "flag_path", "expected_path"),
    [
        ("scoped.sqlite", None, "scoped.sqlite"),
        (None, None, None),
        (None, "explicit.sqlite", "explicit.sqlite"),
    ],
)
def test_scorer_resolves_engine_cache_scope(
    monkeypatch: pytest.MonkeyPatch,
    scoped_path: str | None,
    flag_path: str | None,
    expected_path: str | None,
) -> None:
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", "ambient.sqlite")

    def record_path(_root, _engines, *, workers, engine_cache_path):
        assert engine_cache_path == (Path(expected_path) if expected_path else None)
        return 0

    monkeypatch.setattr(battery_score, "_run_measured_headline", record_path)
    args = ["--measured-headline"]
    if flag_path:
        args.extend(["--engine-cache", flag_path])
    with _engine_cache_path_scope(scoped_path):
        assert battery_score.main(args) == 0


def test_canonical_rows_ignore_traceback_text_but_preserve_the_failure():
    payload = {
        "prediction": 1.25,
        "provenance": {
            "traceback": "worker-specific stack text",
            "elapsed_s": 2.0,
            "process_cpu_seconds": 1.0,
            "status": "refusal",
            "refusal_reason": "engine_timeout",
        },
    }
    assert gate._without_cache_counters(payload) == {
        "prediction": 1.25,
        "provenance": {"status": "refusal", "refusal_reason": "engine_timeout"},
    }


def test_scorer_prints_cache_failure_counters(capsys: pytest.CaptureFixture[str]) -> None:
    summary = battery_score._engine_cache_summary(
        Path("engine.sqlite"),
        local_counters={
            "alphamelts": {
                "hits": 0, "misses": 682, "stores": 0,
                "read_error": 1, "store_error": 2, "conflicts": 0,
                "uncacheable:list": 3,
            },
        },
    )

    assert summary["by_engine"]["alphamelts"] == {"hits": 0, "misses": 682, "stores": 0}
    assert capsys.readouterr().err.splitlines() == [
        "engine cache alphamelts: read_error=1 store_error=2 uncacheable:list=3",
    ]


def test_default_slice_uses_all_engines_and_all_rails(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec_path = tmp_path / "default-slice.json"
    spec_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(gate, "load_score_context", lambda _root: object())
    calls: list[dict[str, Any]] = []

    def run_slice(
        _context: object,
        output_path: Path,
        **kwargs: Any,
    ) -> tuple[list[str], dict[str, dict[str, Any]]]:
        calls.append(kwargs)
        name = output_path.stem
        warm = name in {"warm", "parallel-warm"}
        counts = {
            engine.value: {
                "misses": 0 if warm else 1,
                "hits": 1 if warm else 0,
                "stores": 0 if warm else 1,
            }
            for engine in SCORE_ENGINE_SET
        }
        return ["canonical-row"], counts

    monkeypatch.setattr(gate, "_run_slice", run_slice)

    assert gate.main([str(spec_path)]) == 0
    assert len(calls) == 6
    assert all(tuple(call["engines"]) == SCORE_ENGINE_SET for call in calls)
    assert all(call["rail"] is None for call in calls)
    assert all(call["limit"] == 334 for call in calls)
    assert [call["workers"] for call in calls] == [1, 3, 1, 3, 1, 3]


def test_run_slice_returns_summed_worker_cache_counters(
    tmp_path: Path, monkeypatch: Any
) -> None:
    summed = {
        "openimcc": {
            "hits": 7,
            "misses": 0,
            "stores": 0,
        }
    }
    monkeypatch.setattr(
        gate,
        "_score_store_to_jsonl",
        lambda *_args, **_kwargs: (
            0,
            SimpleNamespace(engine_cache_counters=summed),
        ),
    )
    monkeypatch.setattr(gate, "_canonical_rows", lambda _path: ["canonical-row"])

    rows, counts = gate._run_slice(
        object(),
        tmp_path / "warm.jsonl",
        root=tmp_path,
        engines=(),
        rail=None,
        work=None,
        limit=1,
        workers=3,
        cache_path=tmp_path / "engine-cache.sqlite",
    )

    assert rows == ["canonical-row"]
    assert counts == summed
