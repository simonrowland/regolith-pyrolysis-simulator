"""Paths into the external regolith corpus checkout."""

from __future__ import annotations

import os
from pathlib import Path


def regolith_corpus_root() -> Path:
    """Return the configured corpus root or the user's standard checkout path."""
    configured_root = os.environ.get("REGOLITH_CORPUS_ROOT")
    if configured_root is not None:
        return Path(configured_root).expanduser().resolve()
    return Path.home() / "Repos/regolith-corpus"


def regolith_corpus_ctl_root() -> Path:
    """Use the configured checkout, or the legacy ctl checkout when unset."""
    if os.environ.get("REGOLITH_CORPUS_ROOT") is not None:
        return regolith_corpus_root()
    return Path.home() / "Repos/regolith-corpus-ctl"
