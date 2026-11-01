"""Shared file helpers for compilation harvesters.

Burcat and NASA Glenn each carried the same byte-hash, copy, and JSON
writer. FastChem uses this module instead of a third copy.
``scripts/cache_convert.py`` and ``scripts/sso_r_validation_map.py`` keep
their own helpers: those functions take different arguments.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def resolve_existing(candidates: list[Path], label: str) -> Path:
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"{label} not found; looked at: " + ", ".join(str(path) for path in candidates)
    )


def copy_if_needed(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
