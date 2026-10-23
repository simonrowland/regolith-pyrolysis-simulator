"""File-backed acknowledgements for store-neutral input commits."""

from __future__ import annotations

import importlib.util
import subprocess
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


def _fake_log(*commits: str) -> str:
    return "".join(
        f"{commit}\0input change {index}\n\n{_STALE_INPUT}\n"
        for index, commit in enumerate(commits)
    )


def _fake_git(log: str, resolutions: dict[str, str]):
    def run(*args: str) -> str:
        if args[0] == "rev-parse":
            stem = args[-1].removesuffix("^{commit}")
            return resolutions[stem] + "\n"
        return log

    return run


def test_store_neutral_ack_file_exempts_stale_input_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    commit = "abcdef1" + "0" * 33
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / "abcdef1.txt").write_text(
        "Fixed-point migration produced no store diff.\n", encoding="utf-8"
    )
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(
        freshness, "_git", _fake_git(_fake_log(commit), {"abcdef1": commit})
    )

    assert freshness.stale_input_commits("HEAD", "store") == {}


def test_full_commit_id_ack_file_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    commit = "a" * 40
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / f"{commit}.txt").write_text("Fixed-point regen.\n", encoding="utf-8")
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(
        freshness, "_git", _fake_git(_fake_log(commit), {commit: commit})
    )

    assert freshness.stale_input_commits("HEAD", "store") == {}


def test_ambiguous_ack_prefix_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / "eb850d2.txt").write_text("Reason.\n", encoding="utf-8")
    monkeypatch.setattr(freshness, "ROOT", tmp_path)

    # Simulate git's response to an ambiguous short oid. Whether a real prefix
    # is ambiguous depends on which objects the local clone holds, so the test
    # must not depend on the repository's contents.
    def ambiguous_rev_parse(*args: str) -> str:
        if "rev-parse" in args:
            raise subprocess.CalledProcessError(
                128,
                ["git", *args],
                output="",
                stderr="error: short object ID eb850d2 is ambiguous\n"
                "fatal: Needed a single revision\n",
            )
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(freshness, "_git", ambiguous_rev_parse)
    with pytest.raises(subprocess.CalledProcessError) as error:
        freshness._store_neutral_ack_commits()
    assert "ambiguous" in (error.value.stderr or "")


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


def test_multiline_ack_body_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / "abcdefa.txt").write_text(
        "first reason line\n\nsecond reason line\n", encoding="utf-8"
    )
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
    (ack_dir / "037f97f.txt").write_text("Reason.\n", encoding="utf-8")
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    duplicate = "037f97ff6" + "a" * 31
    monkeypatch.setattr(
        freshness, "_git", _fake_git(_fake_log("a" * 40), {"037f97f": duplicate})
    )

    with pytest.raises(ValueError, match="duplicate store-neutral acknowledgement"):
        freshness.stale_input_commits("HEAD", "store")


def test_ack_for_one_commit_does_not_exempt_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    ack_dir = tmp_path / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    (ack_dir / "abcdef1.txt").write_text("Reason.\n", encoding="utf-8")
    acknowledged = "abcdef1" + "0" * 33
    stale_commit = "bbcdef2" + "0" * 33
    log = _fake_log(acknowledged, stale_commit)
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(
        freshness,
        "_git",
        _fake_git(log, {"abcdef1": acknowledged}),
    )

    stale = freshness.stale_input_commits("HEAD", "store")

    assert set(stale) == {f"{stale_commit[:9]} input change 1"}


def test_existing_store_neutral_frozenset_still_exempts_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freshness = _load_freshness()
    commit = "037f97ff6" + "a" * 31
    monkeypatch.setattr(freshness, "ROOT", tmp_path)
    monkeypatch.setattr(freshness, "_git", lambda *args: _fake_log(commit))

    assert freshness.stale_input_commits("HEAD", "store") == {}
