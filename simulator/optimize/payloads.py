"""Optimizer-owned payloads shared with the web presentation layer."""

from typing import Any

from simulator.ceramic_classifier import CeramicServiceTemperature


def _ceramic_service_temp_payload(
    service_temp: CeramicServiceTemperature,
) -> dict[str, Any]:
    usable = service_temp.usable_service_C
    kind = service_temp.kind
    if usable is not None:
        display = f"Usable service: {usable:g} C"
        usable_service = True
    elif service_temp.value_C is not None:
        display = f"{kind}: {service_temp.value_C:g} C; not a usable service rating"
        usable_service = False
    else:
        display = f"{kind}; not a usable service rating"
        usable_service = False
    return {
        "value_C": _round_or_none(service_temp.value_C),
        "kind": kind,
        "usable_service_C": _round_or_none(usable),
        "usable_service": usable_service,
        "display": display,
        "note": service_temp.note,
    }


def _round_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 3)
