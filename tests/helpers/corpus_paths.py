"""Paths into the external regolith-corpus checkout used by tests."""

from __future__ import annotations

import os
from pathlib import Path


def regolith_corpus_root() -> Path:
    """Return the configured corpus root or the user's standard checkout path."""
    default_root = Path.home() / "Repos/regolith-corpus"
    return Path(os.environ.get("REGOLITH_CORPUS_ROOT", default_root))
