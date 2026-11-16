"""Paths into the external regolith-corpus checkout used by tests."""

from __future__ import annotations

from collections.abc import Iterable
import glob
from pathlib import Path

import pytest

from simulator.corpus_paths import regolith_corpus_root


def require_corpus_files(paths: Iterable[Path]) -> None:
    """Skip with the first missing corpus file (or file glob) in test order."""
    for path in paths:
        path = Path(path)
        if glob.has_magic(str(path)):
            present = any(
                Path(match).is_file()
                for match in glob.glob(str(path), recursive=True)
            )
        else:
            present = path.is_file()
        if not present:
            pytest.skip(f"corpus input is missing: {path}")
