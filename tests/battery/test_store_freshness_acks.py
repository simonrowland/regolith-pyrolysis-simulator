"""File-backed acknowledgements for store-neutral input commits."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_STALE_INPUT = "data/literature/extracts/example.yaml"


def _load_freshness():
    path = REPO_ROOT / "scripts" / "check_store_freshness.py"
    spec = importlib.util.spec_from_file_location("check_store_freshness", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_log(commit: str) -> str:
    return f"{commit}\0input change\n\n{_STALE_INPUT}\n"


def test_store_neutral_ack_file_exempts_stale_input_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    commit = "abcdef1234567890" * 2 + "abcdef12"
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / "abcdef1.txt").write_text(
        "Fixed-point migration produced no store diff.\n", encoding="utf-8"
    )
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(freshness, "_git", lambda *args: _fake_log(commit))

    assert freshness.stale_input_commits("HEAD", "store") == {}


def test_malformed_store_neutral_ack_name_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / "not-a-sha.txt").write_text("Reason.\n", encoding="utf-8")
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(freshness, "_git", lambda *args: _fake_log("a" * 40))

    with pytest.raises(ValueError, match="malformed store-neutral ack file"):
        freshness.stale_input_commits("HEAD", "store")


@pytest.mark.parametrize("entry_name", ["README.md", "abcdefg.md", "abcdefg.TXT"])
def test_ack_directory_strays_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry_name: str
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / entry_name).write_text("Reason.\n", encoding="utf-8")
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(freshness, "_git", lambda *args: _fake_log("a" * 40))

    with pytest.raises(ValueError, match="malformed store-neutral ack file"):
        freshness.stale_input_commits("HEAD", "store")


def test_ack_directory_subdirectories_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    (ack_dir / "abcdefa.txt").mkdir(parents=True)
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(freshness, "_git", lambda *args: _fake_log("a" * 40))

    with pytest.raises(ValueError, match="malformed store-neutral ack file"):
        freshness.stale_input_commits("HEAD", "store")


def test_empty_ack_body_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / "abcdefa.txt").write_text(" \n", encoding="utf-8")
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(freshness, "_git", lambda *args: _fake_log("a" * 40))

    with pytest.raises(ValueError, match="one reason line"):
        freshness.stale_input_commits("HEAD", "store")


def test_ack_cannot_duplicate_frozenset_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / "037f97ff6.txt").write_text("Reason.\n", encoding="utf-8")
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(freshness, "_git", lambda *args: _fake_log("a" * 40))

    with pytest.raises(ValueError, match="duplicate store-neutral acknowledgement"):
        freshness.stale_input_commits("HEAD", "store")


def test_existing_store_neutral_frozenset_still_exempts_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    commit = "037f97ff6" + "a" * 31
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(freshness, "_git", lambda *args: _fake_log(commit))

    assert freshness.stale_input_commits("HEAD", "store") == {}
