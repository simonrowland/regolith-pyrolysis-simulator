"""Shared typed contract for the silent-zero class (b-149).

Predicate: any path where an absent input yields a zero/empty/default result
indistinguishable from a *computed* zero (or unit default).

Doctrine has three categories; this module does not convert zeros into
refusals. It records a typed ``zero_because`` so consumers can see which
category produced the silence:

1. missing input → eventually REFUSE (typed, visible)
2. out-of-domain physics → COMPUTE AND MARK (status-bearing)
3. proven zero → keep the zero, but say *why* it is proven

Instrument-first (binding for b-149): emit the note, leave numeric behaviour
unchanged, measure blast radius before any refusal rebaseline.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, MutableMapping, MutableSequence, Sequence


class ZeroBecause(str, Enum):
    """Closed enum of silent-zero / silent-default causes.

    Keep the set small and stable. New causes need an explicit review that the
    existing tags cannot express the physics.
    """

    PROVEN_EMPTY_INVENTORY = "proven_empty_inventory"
    PROVEN_BELOW_THRESHOLD = "proven_below_threshold"
    MISSING_COEFFICIENT = "missing_coefficient"
    MISSING_THERMO = "missing_thermo"
    MISSING_ACTIVITY = "missing_activity"
    REFUSED_UPSTREAM = "refused_upstream"
    OUT_OF_DOMAIN_MARKED = "out_of_domain_marked"
    UNPARSEABLE_SPEC = "unparseable_spec"
    KERNEL_OK_EMPTY = "kernel_ok_empty"
    IMPLICIT_UNIT_ACTIVITY = "implicit_unit_activity"
    AMBIGUOUS_EMPTY_TERMINUS = "ambiguous_empty_terminus"


# Doctrine category integers (1/2/3) used on every note.
CATEGORY_REFUSE = 1
CATEGORY_MARK = 2
CATEGORY_PROVEN_ZERO = 3

SCHEMA_V1 = "silent_zero.v1"


@dataclass(frozen=True)
class SilentZeroNote:
    """One typed silent-zero / silent-default observation."""

    zero_because: ZeroBecause
    site: str
    species: str | None = None
    field: str | None = None
    detail: str | None = None
    doctrine_category: int = CATEGORY_REFUSE

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "zero_because": self.zero_because.value,
            "site": self.site,
            "doctrine_category": int(self.doctrine_category),
        }
        if self.species is not None:
            payload["species"] = str(self.species)
        if self.field is not None:
            payload["field"] = str(self.field)
        if self.detail is not None:
            payload["detail"] = str(self.detail)
        return payload


def make_note(
    zero_because: ZeroBecause | str,
    *,
    site: str,
    species: str | None = None,
    field: str | None = None,
    detail: str | None = None,
    doctrine_category: int = CATEGORY_REFUSE,
) -> SilentZeroNote:
    """Build a typed note; ``zero_because`` may be the enum or its value."""

    if isinstance(zero_because, ZeroBecause):
        reason = zero_because
    else:
        reason = ZeroBecause(str(zero_because))
    category = int(doctrine_category)
    if category not in (CATEGORY_REFUSE, CATEGORY_MARK, CATEGORY_PROVEN_ZERO):
        raise ValueError(
            f"doctrine_category must be 1, 2, or 3; got {category!r}"
        )
    return SilentZeroNote(
        zero_because=reason,
        site=str(site),
        species=None if species is None else str(species),
        field=None if field is None else str(field),
        detail=None if detail is None else str(detail),
        doctrine_category=category,
    )


def note_dict(
    zero_because: ZeroBecause | str,
    *,
    site: str,
    species: str | None = None,
    field: str | None = None,
    detail: str | None = None,
    doctrine_category: int = CATEGORY_REFUSE,
) -> dict[str, Any]:
    """Convenience: ``make_note(...).as_dict()``."""

    return make_note(
        zero_because,
        site=site,
        species=species,
        field=field,
        detail=detail,
        doctrine_category=doctrine_category,
    ).as_dict()


def append_note(
    sink: MutableSequence[Any] | None,
    zero_because: ZeroBecause | str,
    *,
    site: str,
    species: str | None = None,
    field: str | None = None,
    detail: str | None = None,
    doctrine_category: int = CATEGORY_REFUSE,
) -> dict[str, Any]:
    """Append a note dict to *sink* (if provided) and return the dict."""

    payload = note_dict(
        zero_because,
        site=site,
        species=species,
        field=field,
        detail=detail,
        doctrine_category=doctrine_category,
    )
    if sink is not None:
        sink.append(payload)
    return payload


def merge_notes_into_mapping(
    target: MutableMapping[str, Any],
    notes: Sequence[Mapping[str, Any] | SilentZeroNote],
    *,
    key: str = "silent_zero_notes",
) -> list[dict[str, Any]]:
    """Extend ``target[key]`` with *notes*; return the full list under *key*."""

    existing_raw = target.get(key)
    merged: list[dict[str, Any]] = []
    if isinstance(existing_raw, Sequence) and not isinstance(
        existing_raw, (str, bytes)
    ):
        for item in existing_raw:
            if isinstance(item, SilentZeroNote):
                merged.append(item.as_dict())
            elif isinstance(item, Mapping):
                merged.append(dict(item))
    for item in notes:
        if isinstance(item, SilentZeroNote):
            merged.append(item.as_dict())
        elif isinstance(item, Mapping):
            merged.append(dict(item))
    target[key] = merged
    return merged


def record_on_host(
    host: Any,
    zero_because: ZeroBecause | str,
    *,
    site: str,
    species: str | None = None,
    field: str | None = None,
    detail: str | None = None,
    doctrine_category: int = CATEGORY_REFUSE,
) -> dict[str, Any]:
    """Record a note on a simulator-like host (``_silent_zero_notes`` list)."""

    payload = note_dict(
        zero_because,
        site=site,
        species=species,
        field=field,
        detail=detail,
        doctrine_category=doctrine_category,
    )
    if host is None:
        return payload
    bucket = getattr(host, "_silent_zero_notes", None)
    if bucket is None or not isinstance(bucket, list):
        bucket = []
        try:
            setattr(host, "_silent_zero_notes", bucket)
        except (AttributeError, TypeError):
            return payload
    bucket.append(payload)
    return payload


def notes_payload(
    notes: Iterable[Mapping[str, Any] | SilentZeroNote] | None,
) -> dict[str, Any]:
    """Serialize a notes collection for snapshot / diagnostic surfaces."""

    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    by_category: dict[str, int] = {"1": 0, "2": 0, "3": 0}
    for raw in notes or ():
        if isinstance(raw, SilentZeroNote):
            item = raw.as_dict()
        elif isinstance(raw, Mapping):
            item = dict(raw)
        else:
            continue
        items.append(item)
        reason = str(item.get("zero_because") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
        cat = str(item.get("doctrine_category") or "")
        if cat in by_category:
            by_category[cat] += 1
    return {
        "schema": SCHEMA_V1,
        "count": len(items),
        "counts_by_reason": dict(sorted(counts.items())),
        "counts_by_doctrine_category": by_category,
        "notes": items,
    }


def silent_zero_diagnostic(host: Any) -> dict[str, Any]:
    """Snapshot/diagnostic entry point for a simulator-like host."""

    notes = getattr(host, "_silent_zero_notes", None)
    if notes is None:
        notes = ()
    return notes_payload(notes)


__all__ = [
    "CATEGORY_MARK",
    "CATEGORY_PROVEN_ZERO",
    "CATEGORY_REFUSE",
    "SCHEMA_V1",
    "SilentZeroNote",
    "ZeroBecause",
    "append_note",
    "make_note",
    "merge_notes_into_mapping",
    "note_dict",
    "notes_payload",
    "record_on_host",
    "silent_zero_diagnostic",
]
