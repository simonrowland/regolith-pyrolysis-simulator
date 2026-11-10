from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    assert any(
        "engine_cache_counters run=parallel-warm" in line
        and "openimcc:hits=1,misses=0,stores=0,calls_by_kind={}" in line
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
