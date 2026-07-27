from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import pytest


_HINTS_PATH = Path(__file__).with_name("tests") / "xdist_loadgroup_durations.json"


class _GroupedItem(Protocol):
    nodeid: str

    def iter_markers(self, name: str): ...


def _load_duration_hints(path: Path = _HINTS_PATH) -> dict[str, float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    hints = payload["durations_seconds"]
    if not isinstance(hints, dict):
        raise TypeError(f"{path}: durations_seconds must be an object")
    parsed = {str(group): float(seconds) for group, seconds in hints.items()}
    if any(seconds < 0 for seconds in parsed.values()):
        raise ValueError(f"{path}: duration hints must be non-negative")
    return parsed


LOADGROUP_DURATION_HINTS_SECONDS = _load_duration_hints()


def xdist_group_name(item: _GroupedItem) -> str | None:
    names: set[str] = set()
    for mark in item.iter_markers("xdist_group"):
        name = mark.args[0] if mark.args else mark.kwargs.get("name", "default")
        names.add(str(name))
    return "_".join(sorted(names)) if names else None


def order_items_longest_group_first(
    items: list[_GroupedItem],
    duration_hints: dict[str, float] = LOADGROUP_DURATION_HINTS_SECONDS,
) -> None:
    items.sort(
        key=lambda item: -duration_hints.get(xdist_group_name(item) or "", 0.0)
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    order_items_longest_group_first(items)
