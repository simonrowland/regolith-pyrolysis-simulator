"""Read, write, and compare sorted identity manifests used by tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def load_named_manifest(path: Path) -> dict[str, Any]:
    """Load JSONL rows with exactly one sorted, unique identity per line."""
    rows: dict[str, Any] = {}
    previous = ""
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(record, dict) or set(record) != {"identity", "value"}:
            raise ValueError(
                f"{path}:{line_number}: expected exactly identity and value fields"
            )
        identity = record["identity"]
        if not isinstance(identity, str) or not identity:
            raise ValueError(f"{path}:{line_number}: identity must be a non-empty string")
        if identity <= previous:
            raise ValueError(
                f"{path}:{line_number}: identities must be unique and sorted; "
                f"got {identity!r} after {previous!r}"
            )
        rows[identity] = record["value"]
        previous = identity
    if not rows:
        raise ValueError(f"{path}: named manifest is empty")
    return rows


def write_named_manifest(path: Path, rows: Mapping[str, Any]) -> None:
    """Write sorted JSONL rows; callers must explicitly invoke their updater."""
    if not rows:
        raise ValueError(f"{path}: refusing to write an empty named manifest")
    identities = list(rows)
    if any(not isinstance(identity, str) or not identity for identity in identities):
        raise ValueError(f"{path}: identities must be non-empty strings")
    lines: list[str] = []
    for identity in sorted(identities):
        try:
            lines.append(
                json.dumps(
                    {"identity": identity, "value": rows[identity]},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}: row {identity!r} is not JSON serializable") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def assert_named_rows(
    actual: Mapping[str, Any], expected: Mapping[str, Any], *, label: str
) -> None:
    """Require the exact named rows and report added, removed, or changed IDs."""
    actual_ids = set(actual)
    expected_ids = set(expected)
    added = sorted(actual_ids - expected_ids)
    removed = sorted(expected_ids - actual_ids)
    changed = sorted(
        identity
        for identity in actual_ids & expected_ids
        if actual[identity] != expected[identity]
    )
    if added or removed or changed:
        details = []
        if added:
            details.append(f"added rows: {added}")
        if removed:
            details.append(f"removed rows: {removed}")
        if changed:
            details.append(f"changed rows: {changed}")
        raise AssertionError(
            f"{label} differs from its named manifest; " + "; ".join(details)
        )
