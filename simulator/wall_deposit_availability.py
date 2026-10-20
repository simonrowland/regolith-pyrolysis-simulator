"""Shared availability decision for wall-deposit figures."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal


WALL_SATURATION_PRESSURE_REFUSED_CODE = "wall_deposit_saturation_pressure_refused"
WALL_DEPOSIT_MEASUREMENT_UNAVAILABLE_CODE = "wall_deposit_measurement_unavailable"
WALL_SATURATION_PRESSURE_REFUSAL_EVENTS = (
    "wall_saturation_pressure_refusal_events"
)


@dataclass(frozen=True)
class WallDepositAvailability:
    status: Literal["available", "refused", "measured_empty"]
    refusal: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if (self.status == "refused") != (self.refusal is not None):
            raise ValueError("a refused wall-deposit figure requires a refusal record")


def wall_deposit_figure_availability(
    *,
    has_positive_deposit: bool,
    authority: Mapping[str, Any] | None = None,
    refusal: Mapping[str, Any] | None = None,
    measured: bool = True,
) -> WallDepositAvailability:
    """Classify a figure using its consumer's existing numeric projection."""

    authority_payload = _mapping(authority)
    explicit_refusal = _mapping(refusal)
    explicit_authority = _mapping(explicit_refusal.get("authority"))
    cumulative_pressure_refused = _has_pressure_refusal(authority_payload)
    pressure_refused = cumulative_pressure_refused or _has_pressure_refusal(
        explicit_authority
    )
    if explicit_refusal or pressure_refused:
        # An authority payload with pressure refusals may already aggregate
        # earlier campaigns; the explicit refusal can describe only the latest.
        refusal_authority = (
            authority_payload
            if cumulative_pressure_refused
            else explicit_authority or authority_payload
        )
        if not refusal_authority:
            refusal_authority = {
                "code": (
                    WALL_SATURATION_PRESSURE_REFUSED_CODE
                    if pressure_refused
                    else WALL_DEPOSIT_MEASUREMENT_UNAVAILABLE_CODE
                ),
                "output_status": "status_bearing" if pressure_refused else "unavailable",
                "authoritative_for_resinter": False,
            }
        reasons = wall_deposit_refusal_events(refusal_authority)
        explicit_reasons = explicit_refusal.get("reasons")
        if isinstance(explicit_reasons, (list, tuple)):
            supplied_reasons = [
                dict(reason)
                for reason in explicit_reasons
                if isinstance(reason, Mapping)
            ]
            if len(supplied_reasons) > len(reasons):
                reasons = supplied_reasons
        return WallDepositAvailability(
            status="refused",
            refusal={
                "reason": (
                    _string(refusal_authority.get("message"))
                    or _string(explicit_refusal.get("reason"))
                    or _nested_reason(refusal_authority)
                    or "wall saturation pressure refusal"
                ),
                "authority": refusal_authority,
                "count": _count(explicit_refusal, refusal_authority),
                "refusal_events": len(reasons),
                "reasons": reasons,
            },
        )
    if not measured:
        return WallDepositAvailability(
            status="refused",
            refusal={
                "reason": "wall-deposit measurement is unavailable",
                "authority": {
                    "code": WALL_DEPOSIT_MEASUREMENT_UNAVAILABLE_CODE,
                    "output_status": "unavailable",
                    "authoritative_for_resinter": False,
                },
                "count": 0,
            },
        )
    return WallDepositAvailability(
        status="available" if has_positive_deposit else "measured_empty"
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def wall_deposit_refusal_events(
    authority: Mapping[str, Any] | None,
    *,
    campaign: int | None = None,
) -> list[dict[str, Any]]:
    """Return refusal event records, retaining each campaign's source record."""

    payload = _mapping(authority)
    raw_events = payload.get(WALL_SATURATION_PRESSURE_REFUSAL_EVENTS)
    if isinstance(raw_events, (list, tuple)):
        return [
            dict(event)
            for event in raw_events
            if isinstance(event, Mapping)
        ]

    records = payload.get("wall_saturation_pressure_refusals_by_species")
    if not isinstance(records, Mapping):
        return []
    events: list[dict[str, Any]] = []
    for species, by_segment in sorted(
        records.items(),
        key=lambda item: str(item[0]),
    ):
        if not isinstance(by_segment, Mapping):
            continue
        for segment, record in sorted(
            by_segment.items(),
            key=lambda item: str(item[0]),
        ):
            if not isinstance(record, Mapping):
                continue
            events.append(
                {
                    "campaign": campaign,
                    "species": str(species),
                    "segment": str(segment),
                    "reason": _string(record.get("reason")),
                    "authority": dict(record),
                }
            )
    return events


def _has_pressure_refusal(authority: Mapping[str, Any]) -> bool:
    code = WALL_SATURATION_PRESSURE_REFUSED_CODE
    codes = authority.get("codes", ())
    refused_species = authority.get("wall_saturation_pressure_refused_species")
    refusal_records = authority.get("wall_saturation_pressure_refusals_by_species")
    count = authority.get("status_bearing_refusal_count")
    return (
        authority.get("code") == code
        or isinstance(codes, (list, tuple, set, frozenset)) and code in codes
        or isinstance(refused_species, (list, tuple, set, frozenset))
        and bool(refused_species)
        or isinstance(refusal_records, Mapping) and bool(refusal_records)
        or isinstance(count, int) and not isinstance(count, bool) and count > 0
    )


def _count(
    explicit_refusal: Mapping[str, Any],
    authority: Mapping[str, Any],
) -> int:
    counts = [
        authority.get("status_bearing_refusal_count"),
        explicit_refusal.get("count"),
    ]
    counts = [
        value
        for value in counts
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    ]
    if counts:
        return max(counts)
    species = authority.get("wall_saturation_pressure_refused_species")
    if isinstance(species, (list, tuple, set, frozenset)):
        return len(species)
    records = authority.get("wall_saturation_pressure_refusals_by_species")
    return len(records) if isinstance(records, Mapping) else 0


def _nested_reason(authority: Mapping[str, Any]) -> str | None:
    records = authority.get("wall_saturation_pressure_refusals_by_species")
    if not isinstance(records, Mapping):
        return None
    for segment_records in records.values():
        if not isinstance(segment_records, Mapping):
            continue
        for record in segment_records.values():
            if isinstance(record, Mapping):
                reason = _string(record.get("reason"))
                if reason:
                    return reason
    return None


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
