"""Structural checks for store-neutral freshness acknowledgements."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ACK_DIR = REPO_ROOT / "scripts" / "store_neutral_acks"


def _load_freshness():
    path = REPO_ROOT / "scripts" / "check_store_freshness.py"
    spec = importlib.util.spec_from_file_location("check_store_freshness", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_store_neutral_ack_files_match_freshness_set() -> None:
    entries = list(ACK_DIR.iterdir())
    stray = [
        path.name
        for path in entries
        if not path.is_file() or path.suffix != ".md"
    ]
    assert not stray, f"unexpected files in {ACK_DIR}: {stray}"

    files = list(ACK_DIR.glob("*.md"))
    invalid_names = [
        path.name
        for path in files
        if re.fullmatch(r"[0-9a-f]{9}\.md", path.name) is None
    ]
    assert not invalid_names, (
        f"ack filenames must be 9 lowercase hex chars: {invalid_names}"
    )

    empty_bodies = [
        path.name
        for path in files
        if not path.read_text(encoding="utf-8").strip()
    ]
    assert not empty_bodies, f"ack bodies must be non-empty: {empty_bodies}"

    freshness = _load_freshness()
    file_stems = frozenset(path.stem for path in files)
    assert freshness.STORE_NEUTRAL_INPUT_COMMITS == file_stems
