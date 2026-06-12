"""Runtime guardrails for non-loadable feedstock catalog entries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class BlockedFeedstockError(ValueError):
    """Raised when a catalog entry is present but marked non-loadable."""


def blocked_feedstock_status(feedstock: Mapping[str, Any] | None) -> str:
    if not isinstance(feedstock, Mapping):
        return ""
    status = feedstock.get("status")
    if not isinstance(status, str):
        return ""
    status = status.strip()
    return status if status.startswith("blocked_") else ""


def is_blocked_feedstock(feedstock: Mapping[str, Any] | None) -> bool:
    return bool(blocked_feedstock_status(feedstock))


def assert_feedstock_loadable(feedstock_id: str, feedstock: Mapping[str, Any]) -> None:
    status = blocked_feedstock_status(feedstock)
    if not status:
        return
    reason = str(feedstock.get("blocked_reason") or "missing loadable composition")
    raise BlockedFeedstockError(
        f"feedstock {feedstock_id!r} is blocked ({status}): {reason}"
    )


def loadable_feedstocks(
    feedstocks: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    return {
        str(feedstock_id): feedstock
        for feedstock_id, feedstock in feedstocks.items()
        if not is_blocked_feedstock(feedstock)
    }
