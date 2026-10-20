"""Shared availability decision for wall-deposit figures."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal


WALL_SATURATION_PRESSURE_REFUSED_CODE = "wall_deposit_saturation_pressure_refused"
WALL_DEPOSIT_MEASUREMENT_UNAVAILABLE_CODE = "wall_deposit_measurement_unavailable"


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
    pressure_refused = _has_pressure_refusal(authority_payload)
    if explicit_refusal or pressure_refused:
        refusal_authority = _mapping(explicit_refusal.get("authority")) or authority_payload
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
        return WallDepositAvailability(
            status="refused",
            refusal={
                "reason": (
                    _string(explicit_refusal.get("reason"))
                    or _string(refusal_authority.get("message"))
                    or _nested_reason(refusal_authority)
                    or "wall saturation pressure refusal"
                ),
                "authority": refusal_authority,
                "count": _count(explicit_refusal, refusal_authority),
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
    for value in (
        explicit_refusal.get("count"),
        authority.get("status_bearing_refusal_count"),
    ):
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
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
