"""Shared availability decision for wall-deposit figures."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
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
    # Refused supply uses the activity-1 free-evaporation ceiling at the
    # hour's pO2: J_max = P_sat(T) * sqrt(M / (2*pi*R*T)), capped by the
    # element inventory. Pa * s/m = kg/(m2*s); area * duration gives kg.
    # This is flagged numeric supply, even when its bound is effectively
    # 0 kg at 313 K. Without a complete booking, refusal stays unavailable.
    bookings = authority_payload.get("wall_supply_bound_bookings") or ()
    booked_species = {
        str(item["species"])
        for item in bookings
        if isinstance(item, Mapping) and item.get("species")
        and all(
            isinstance(item.get(key), (int, float))
            and not isinstance(item[key], bool)
            and math.isfinite(item[key]) and item[key] >= 0.0
            for key in ("supply_upper_bound_kg", "deposit_kg")
        )
    }
    decision_authority = _authority_without_booked_refusals(
        authority_payload, booked_species,
    )
    cumulative_pressure_refused = _has_pressure_refusal(decision_authority)
    pressure_refused = cumulative_pressure_refused or _has_pressure_refusal(
        explicit_authority
    )
    # d-367: source-carrier uncertainty cannot erase a booked positive
    # deposit. It does prevent certifying an empty wall without a supply
    # bound. Wall-pressure and explicit measurement refusals still apply.
    carrier_refused = (
        not has_positive_deposit
        and bool(decision_authority.get("refused_carrier_species"))
    )
    if explicit_refusal or pressure_refused or carrier_refused:
        # An authority payload with pressure refusals may already aggregate
        # earlier campaigns; the explicit refusal can describe only the latest.
        refusal_authority = (
            authority_payload
            if cumulative_pressure_refused or carrier_refused
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


def _authority_without_booked_refusals(
    authority: Mapping[str, Any],
    booked_species: set[str],
) -> dict[str, Any]:
    """Keep only unbooked species in the internal availability decision.

    The published authority keeps the refusal. This copy is only the
    availability input, so a booked supply cannot refuse the whole figure.
    """

    payload = _mapping(authority)
    if not booked_species:
        return payload
    carriers = payload.get("refused_carrier_species") or ()
    payload["refused_carrier_species"] = [
        str(species) for species in carriers if str(species) not in booked_species
    ]
    raw_species = payload.get("wall_saturation_pressure_refused_species")
    if isinstance(raw_species, str):
        remaining = [] if raw_species in booked_species else [raw_species]
    elif isinstance(raw_species, (list, tuple, set, frozenset)):
        remaining = [str(item) for item in raw_species if str(item) not in booked_species]
    else:
        remaining = []
    records = payload.get("wall_saturation_pressure_refusals_by_species")
    if isinstance(records, Mapping):
        kept_records = {
            str(species): value
            for species, value in records.items()
            if str(species) not in booked_species
        }
    else:
        kept_records = {}
    events = payload.get("wall_saturation_pressure_refusal_events")
    if isinstance(events, list):
        payload["wall_saturation_pressure_refusal_events"] = [
            event
            for event in events
            if not (
                isinstance(event, Mapping)
                and str(event.get("species", "")) in booked_species
            )
        ]
    still_refused = bool(remaining or kept_records)
    if still_refused:
        payload["wall_saturation_pressure_refused_species"] = remaining
        payload["wall_saturation_pressure_refusals_by_species"] = kept_records
        payload["status_bearing_refusal_count"] = len(set(remaining) | set(kept_records))
        return payload
    payload.pop("wall_saturation_pressure_refused_species", None)
    payload.pop("wall_saturation_pressure_refusals_by_species", None)
    payload["status_bearing_refusal_count"] = 0
    codes = payload.get("codes")
    if isinstance(codes, list):
        payload["codes"] = [
            code for code in codes if code != WALL_SATURATION_PRESSURE_REFUSED_CODE
        ]
    if payload.get("code") == WALL_SATURATION_PRESSURE_REFUSED_CODE:
        remaining_codes = payload.get("codes")
        payload["code"] = (
            remaining_codes[0]
            if isinstance(remaining_codes, list) and remaining_codes
            else "wall_deposit_supply_bound"
        )
    return payload
