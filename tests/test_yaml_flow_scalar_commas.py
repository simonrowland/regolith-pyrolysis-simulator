"""Regression guard for SC-289 YAML flow-scalar comma truncation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from simulator.battery.migrate import discover_extracts, load_yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACTS = REPO_ROOT / "data" / "literature" / "extracts"

# This is an OCR continuation deliberately kept as a null key. It is not a
# comma-split flow fragment: its source line contains no comma.
ALLOWED_INTENTIONAL_NULL_KEYS = {
    (
        "kems-035-sauerborn-2005.yaml",
        (
            "species",
            "O2",
            "observations",
            0,
            "values",
            "SiO2_mass_loss_locator",
            "1 Gew.%",
        ),
    ),
}


def _prose_like_key(key: object) -> bool:
    return isinstance(key, str) and any(char.isspace() for char in key)


def _walk_null_prose(value: Any, path: tuple[object, ...] = ()):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + (key,)
            if child is None and _prose_like_key(key):
                yield child_path
            yield from _walk_null_prose(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_null_prose(child, path + (index,))


def _format_path(path: tuple[object, ...]) -> str:
    result = ""
    for part in path:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += f".{part}" if result else str(part)
    return result


def test_battery_loader_rejects_comma_split_prose_keys():
    """Load every source through the battery loader and reject silent splits.

    Null hypothesis: suppose my change is wrong in the way that matters most
    — what shows it? A source line with a comma-split prose fragment would load
    as a null mapping key and fail this whole-corpus guard.
    """

    offenders = []
    paths = discover_extracts(EXTRACTS)
    assert paths
    for path in paths:
        document = load_yaml(path)
        for key_path in _walk_null_prose(document):
            identity = (path.name, key_path)
            if identity not in ALLOWED_INTENTIONAL_NULL_KEYS:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{_format_path(key_path)}"
                )
    assert offenders == [], "comma-split prose keys remain:\n" + "\n".join(offenders)
