from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simulator.battery.score import SCORE_ENGINE_SET
from scripts import battery_cache_equivalence as gate


def test_default_slice_uses_all_engines_and_all_rails(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec_path = tmp_path / "default-slice.json"
    spec_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(gate, "load_score_context", lambda _root: object())
    monkeypatch.setattr(gate, "EngineCache", lambda _path: object())
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
                "calls_by_kind": {
                    "equilibrate_cell": 0 if warm else 1,
                    "evaluate": 0,
                    "evaluate_redox": 0,
                },
            }
            for engine in SCORE_ENGINE_SET
        }
        return ["canonical-row"], counts

    monkeypatch.setattr(gate, "_run_slice", run_slice)

    assert gate.main([str(spec_path)]) == 0
    assert len(calls) == 4
    assert all(tuple(call["engines"]) == SCORE_ENGINE_SET for call in calls)
    assert all(call["rail"] is None for call in calls)
    assert all(call["limit"] == 334 for call in calls)
    assert [call["workers"] for call in calls] == [1, 1, 1, 3]
