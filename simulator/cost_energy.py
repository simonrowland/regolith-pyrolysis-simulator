"""Cost-energy helper functions and owner-ratify placeholder coefficients."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.cost_parameters import (
    DEFAULT_ELECTRICAL_COST_PER_KWH,
    ENERGY_COST_DEFAULT_SOURCE,
)


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


ELECTRICAL_USD_PER_KWH = OwnerRatifyCostParameter(
    name="electrical_usd_per_kWh",
    value=DEFAULT_ELECTRICAL_COST_PER_KWH,
    units="USD/kWh",
    source_tag=ENERGY_COST_DEFAULT_SOURCE,
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


def project_owner_ratify_money(cost: Any) -> float:
    return (
        float(getattr(cost, "electrical_kWh", 0.0)) * ELECTRICAL_USD_PER_KWH.value
        + float(getattr(cost, "thermal_flux_h", 0.0)) * THERMAL_USD_PER_FLUX_H.value
        + float(getattr(cost, "furnace_h", 0.0)) * FURNACE_USD_PER_H.value
        + float(getattr(cost, "launch_penalty_kg", 0.0)) * LAUNCH_USD_PER_KG.value
        + float(getattr(cost, "external_reagent_kg", 0.0)) * REAGENT_USD_PER_KG.value
    )
