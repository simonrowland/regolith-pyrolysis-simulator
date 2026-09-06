"""Corpus extracts/ is the source of record; the simulator tree must match it byte-for-byte."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
# The corpus checkout to compare against. REGOLITH_CORPUS_ROOT lets a gate point at a
# specific clone (the controller's, or a CI-provisioned one) instead of the default path.
CORPUS_ROOT = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))


@pytest.mark.skipif(not CORPUS_ROOT.is_dir(), reason="regolith-corpus checkout is absent")
def test_corpus_extracts_are_byte_identical_in_simulator_mirror() -> None:
    src_dir = CORPUS_ROOT / "extracts"
    dest_dir = REPO_ROOT / "data" / "literature" / "extracts"
    assert src_dir.is_dir(), f"corpus extracts directory missing: {src_dir}"
    assert dest_dir.is_dir(), f"simulator extracts directory missing: {dest_dir}"
    corpus_files = sorted(p for p in src_dir.glob("*.yaml") if not p.name.startswith("_"))
    assert corpus_files, "corpus extracts/ has no yaml files to mirror"
    for src in corpus_files:
        dest = dest_dir / src.name
        assert dest.is_file(), f"simulator mirror missing {src.name}"
        assert dest.read_bytes() == src.read_bytes(), f"simulator mirror diverged: {src.name}"
