"""Corpus extracts/ is the source of record; the simulator tree must match it byte-for-byte."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.helpers.corpus_paths import regolith_corpus_root

REPO_ROOT = Path(__file__).resolve().parents[1]


def _require_corpus_root() -> Path:
    """Return the corpus checkout. A missing checkout fails closed.

    REGOLITH_CORPUS_ROOT points a gate at a specific clone. The studio gate
    collects these tests and used to skip them when that checkout was absent,
    so mirror divergence never failed a landing.
    """
    root = regolith_corpus_root()
    assert root.is_dir(), (
        "regolith-corpus checkout is absent: "
        f"{root}. Set REGOLITH_CORPUS_ROOT to a checkout. "
        "Absence fails closed; it is not a skip."
    )
    return root


def test_corpus_extracts_are_byte_identical_in_simulator_mirror() -> None:
    src_dir = _require_corpus_root() / "extracts"
    dest_dir = REPO_ROOT / "data" / "literature" / "extracts"
    assert src_dir.is_dir(), f"corpus extracts directory missing: {src_dir}"
    assert dest_dir.is_dir(), f"simulator extracts directory missing: {dest_dir}"
    corpus_files = sorted(p for p in src_dir.glob("*.yaml") if not p.name.startswith("_"))
    assert corpus_files, "corpus extracts/ has no yaml files to mirror"
    for src in corpus_files:
        dest = dest_dir / src.name
        assert dest.is_file(), f"simulator mirror missing {src.name}"
        assert dest.read_bytes() == src.read_bytes(), f"simulator mirror diverged: {src.name}"


def test_simulator_extracts_are_byte_identical_in_corpus_mirror() -> None:
    src_dir = REPO_ROOT / "data" / "literature" / "extracts"
    dest_dir = _require_corpus_root() / "extracts"
    assert src_dir.is_dir(), f"simulator extracts directory missing: {src_dir}"
    assert dest_dir.is_dir(), f"corpus extracts directory missing: {dest_dir}"
    simulator_files = sorted(p for p in src_dir.glob("*.yaml") if not p.name.startswith("_"))
    assert simulator_files, "simulator extracts/ has no yaml files to mirror"
    for src in simulator_files:
        dest = dest_dir / src.name
        assert dest.is_file(), f"corpus missing {src.name}"
        assert dest.read_bytes() == src.read_bytes(), f"corpus diverged: {src.name}"


def test_absent_corpus_checkout_fails_closed(monkeypatch, tmp_path: Path) -> None:
    """A missing regolith-corpus checkout is a failure, not a skip.

    The studio gate collects the two mirror tests and skips them when the
    checkout is absent, so divergence never fails the landing.
    """
    for fn in (
        test_corpus_extracts_are_byte_identical_in_simulator_mirror,
        test_simulator_extracts_are_byte_identical_in_corpus_mirror,
    ):
        marks = getattr(fn, "pytestmark", [])
        if not isinstance(marks, list):
            marks = [marks]
        for mark in marks:
            reason = mark.kwargs.get("reason", "") if mark.name == "skipif" else ""
            assert "checkout is absent" not in str(reason), (
                f"{fn.__name__} skips when the corpus checkout is absent"
            )
    missing = tmp_path / "absent-corpus"
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(missing))
    with pytest.raises(AssertionError, match="regolith-corpus checkout is absent"):
        _require_corpus_root()
