"""Cost-energy helper functions and owner-ratify placeholder coefficients."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.cost_parameters import (
    DEFAULT_ELECTRICAL_COST_PER_KWH,
    load_cost_parameters,
)

UNAVAILABLE_STATUS = "unavailable"


@dataclass(frozen=True)
class OwnerRatifyCostParameter:
    name: str
    value: float
    units: str
    source_tag: str
    ticket: str

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": float(self.value),
            "units": self.units,
            "source_tag": self.source_tag,
            "ticket": self.ticket,
            "status": "owner-ratify-placeholder",
        }


# Owner D35 (2026-09-05): one electricity quantity, using the YAML's EIA citation
# verbatim. Supersedes 10.0 USD/kWh, tagged owner-ratify-placeholder /
# owner-t7-two-price-energy-v1, with the cited 0.15 USD/kWh bootstrap rate.
ELECTRICAL_USD_PER_KWH = OwnerRatifyCostParameter(
    name="electrical_usd_per_kWh",
    value=DEFAULT_ELECTRICAL_COST_PER_KWH,
    units="USD/kWh",
    source_tag=load_cost_parameters()["parameters"]["electricity_cost_per_kWh"]["source_tag"],
    ticket="COST-PARAM-ELECTRICAL-KWH",
)
THERMAL_USD_PER_FLUX_H = OwnerRatifyCostParameter(
    name="thermal_usd_per_flux_h",
    value=1.0,
    units="USD/(K*h)",
    source_tag="owner-ratify-placeholder:cost-ledger-2026-06-28",
    ticket="COST-PARAM-THERMAL-FLUX-H",
)
FURNACE_USD_PER_H = OwnerRatifyCostParameter(
    name="furnace_usd_per_h",
    value=10.0,
    units="USD/h",
    source_tag="owner-ratify-placeholder:cost-ledger-2026-06-28",
    ticket="COST-PARAM-FURNACE-HOUR",
)
LAUNCH_USD_PER_KG = OwnerRatifyCostParameter(
    name="launch_usd_per_kg",
    value=10000.0,
    units="USD/kg",
    source_tag="owner-ratify-placeholder:cost-ledger-2026-06-28",
    ticket="COST-PARAM-LAUNCH-KG",
)
REAGENT_USD_PER_KG = OwnerRatifyCostParameter(
    name="reagent_usd_per_kg",
    value=100.0,
    units="USD/kg",
    source_tag="owner-ratify-placeholder:cost-ledger-2026-06-28",
    ticket="COST-PARAM-REAGENT-KG",
)


def owner_ratify_cost_placeholders() -> tuple[OwnerRatifyCostParameter, ...]:
    return (
        ELECTRICAL_USD_PER_KWH,
        THERMAL_USD_PER_FLUX_H,
        FURNACE_USD_PER_H,
        LAUNCH_USD_PER_KG,
        REAGENT_USD_PER_KG,
    )


def furnace_thermal_flux_hours(temperature_C: float, duration_h: float) -> float:
    temperature_K = float(temperature_C) + CELSIUS_TO_KELVIN_OFFSET
    duration = float(duration_h)
    if (
        not math.isfinite(temperature_K)
        or not math.isfinite(duration)
        or temperature_K < 0.0
        or duration < 0.0
    ):
        raise ValueError("temperature_C and duration_h must be finite non-negative inputs")
    return temperature_K * duration


class UnavailableQuantity(dict):
    """Typed unavailable energy or money. Never a priced 0.0.

    Falsy so a presence test that does not inspect ``status`` cannot treat a
    refusal as a present value. Distinct dict subclass so the in-process
    object is unmistakable; JSON-loaded copies are plain dicts and are
    recognized via ``status``. ``json.dumps`` keeps the four keys (CPython
    3.12 C encoder uses size, not truthiness).
    """

    def __bool__(self) -> bool:
        return False


def unavailable_quantity(*, reason: str, units: str) -> UnavailableQuantity:
    """Typed unavailable energy or money. Never a priced 0.0."""

    return UnavailableQuantity(
        {
            "status": UNAVAILABLE_STATUS,
            "reason": str(reason or "unspecified"),
            "value": None,
            "units": str(units),
        }
    )


def is_unavailable_quantity(value: Any) -> bool:
    return isinstance(value, Mapping) and str(value.get("status", "")) == UNAVAILABLE_STATUS


def unavailable_reason_of(value: Any, default: str = "unspecified") -> str:
    if is_unavailable_quantity(value):
        reason = value.get("reason")
        if reason is not None and str(reason).strip():
            return str(reason)
    if default is not None and str(default).strip():
        return str(default)
    return "unspecified"


def project_owner_ratify_money(cost: Any) -> float:
    if is_unavailable_quantity(cost):
        raise TypeError(
            "cannot project money from an unavailable quantity: "
            f"{unavailable_reason_of(cost)}"
        )
    electrical = getattr(cost, "electrical_kWh", 0.0)
    if is_unavailable_quantity(electrical):
        raise TypeError(
            "cannot project money from unavailable electrical_kWh: "
            f"{unavailable_reason_of(electrical)}"
        )
    return (
        float(electrical) * ELECTRICAL_USD_PER_KWH.value
        + float(getattr(cost, "thermal_flux_h", 0.0)) * THERMAL_USD_PER_FLUX_H.value
        + float(getattr(cost, "furnace_h", 0.0)) * FURNACE_USD_PER_H.value
        + float(getattr(cost, "launch_penalty_kg", 0.0)) * LAUNCH_USD_PER_KG.value
        + float(getattr(cost, "external_reagent_kg", 0.0)) * REAGENT_USD_PER_KG.value
    )
