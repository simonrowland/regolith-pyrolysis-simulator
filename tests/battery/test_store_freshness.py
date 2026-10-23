"""Report modes for the store freshness gate and offer pre-gate checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_freshness():
    path = REPO_ROOT / "scripts" / "check_store_freshness.py"
    spec = importlib.util.spec_from_file_location("check_store_freshness", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_stale(freshness, monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_git(*args: str) -> str:
        if args[:2] == ("rev-parse", "HEAD"):
            return "head\n"
        if args[:3] == ("log", "-1", "--format=%H"):
            return "store\n"
        if "--date=short" in args:
            return "store9 2026-10-22 store regen\n"
        raise AssertionError(args)

    def fake_stale(head: str, last_store: str):
        calls.append((head, last_store))
        return {"abc123456 change migrate input": ["simulator/battery/migrate.py"]}

    monkeypatch.setattr(freshness, "_git", fake_git)
    monkeypatch.setattr(freshness, "stale_input_commits", fake_stale)
    monkeypatch.setattr(freshness, "_untraced_extracts", lambda head: [])
    return calls


def test_default_stale_report_keeps_gate_defect_text_and_exit_code(
    monkeypatch, capsys
) -> None:
    freshness = _load_freshness()
    calls = _make_stale(freshness, monkeypatch)

    assert freshness.main([]) == 1

    assert calls == [("head", "store")]
    assert capsys.readouterr().out == (
        "STALE: store last touched at store9 2026-10-22 store regen\n"
        "1 later commit(s) touched migrate inputs without a store regen:\n"
        "  abc123456 change migrate input\n"
        "    simulator/battery/migrate.py\n"
        "regenerate: .venv/bin/python scripts/battery_migrate.py && "
        "data/literature/build_index.py --write-store-summary\n"
    )


def test_pre_gate_report_awaits_train_regen_with_same_checks_and_exit_code(
    monkeypatch, capsys
) -> None:
    freshness = _load_freshness()
    calls = _make_stale(freshness, monkeypatch)

    assert freshness.main(["--pre-gate"]) == 1

    assert calls == [("head", "store")]
    assert capsys.readouterr().out == (
        "STALE: store last touched at store9 2026-10-22 store regen\n"
        "1 later commit(s) touched migrate inputs without a store regen:\n"
        "  abc123456 change migrate input\n"
        "    simulator/battery/migrate.py\n"
        "d-255: listed migrate-input commits await the train's gate regen; "
        "this STALE result is expected before the gate and a defect after it.\n"
    )
