"""Cost-energy helper functions and cost coefficient provenance."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.cost_parameters import (
    DEFAULT_ELECTRICAL_COST_PER_KWH,
    _finite_nonnegative,
    load_cost_parameters,
)

UNAVAILABLE_STATUS = "unavailable"
OWNER_RATIFY_PLACEHOLDER_STATUS = "owner-ratify-placeholder"
OWNER_RATIFIED_STATUS = "owner-ratified"


@dataclass(frozen=True)
class OwnerRatifyCostParameter:
    name: str
    value: float
    units: str
    source_tag: str
    ticket: str
    status: str = OWNER_RATIFY_PLACEHOLDER_STATUS

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": float(self.value),
            "units": self.units,
            "source_tag": self.source_tag,
            "ticket": self.ticket,
            "status": self.status,
        }


# Owner D35 (2026-09-05): ratified the YAML's EIA-cited 0.15 USD/kWh electricity
# rate, superseding the 10.0 USD/kWh owner-ratify placeholder.
_COST_PARAMETER_ENTRIES = load_cost_parameters()["parameters"]
_CARRIER_GAS_PRICE_ENTRY = _COST_PARAMETER_ENTRIES["carrier_gas_earth_price_per_kg"]
_CARRIER_MAKEUP_LOSS_SOURCE_TAG = _COST_PARAMETER_ENTRIES[
    "carrier_makeup_loss_fraction_per_run"
]["source_tag"]
_MARS_LOCAL_CARRIER_SPECIES = frozenset({"Ar", "CO2", "N2"})
ELECTRICAL_USD_PER_KWH = OwnerRatifyCostParameter(
    name="electrical_usd_per_kWh",
    value=DEFAULT_ELECTRICAL_COST_PER_KWH,
    units="USD/kWh",
    source_tag=_COST_PARAMETER_ENTRIES["electricity_cost_per_kWh"]["source_tag"],
    ticket="COST-PARAM-ELECTRICAL-KWH",
    status=OWNER_RATIFIED_STATUS,
)
IMPORT_DELIVERY_USD_PER_KG = OwnerRatifyCostParameter(
    name="import_delivery_cost_per_kg",
    value=_COST_PARAMETER_ENTRIES["import_delivery_cost_per_kg"]["value"],
    units=_COST_PARAMETER_ENTRIES["import_delivery_cost_per_kg"]["units"],
    source_tag=_COST_PARAMETER_ENTRIES["import_delivery_cost_per_kg"]["source_tag"],
    ticket="COST-PARAM-IMPORT-DELIVERY-KG",
    status=OWNER_RATIFIED_STATUS,
)
CARRIER_GAS_KR_EARTH_USD_PER_KG = OwnerRatifyCostParameter(
    name="carrier_gas_earth_price_per_kg.Kr",
    value=_COST_PARAMETER_ENTRIES["carrier_gas_earth_price_per_kg"]["values"]["Kr"]["value"],
    units=_COST_PARAMETER_ENTRIES["carrier_gas_earth_price_per_kg"]["units"],
    source_tag=_COST_PARAMETER_ENTRIES["carrier_gas_earth_price_per_kg"]["source_tag"],
    ticket="COST-PARAM-CARRIER-KR-EARTH-KG",
)
CARRIER_MAKEUP_LOSS_FRACTION_PER_RUN = OwnerRatifyCostParameter(
    name="carrier_makeup_loss_fraction_per_run",
    value=_COST_PARAMETER_ENTRIES["carrier_makeup_loss_fraction_per_run"]["value"],
    units=_COST_PARAMETER_ENTRIES["carrier_makeup_loss_fraction_per_run"]["units"],
    source_tag=_COST_PARAMETER_ENTRIES["carrier_makeup_loss_fraction_per_run"]["source_tag"],
    ticket="COST-PARAM-CARRIER-MAKEUP-LOSS-RUN",
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
    return tuple(
        parameter
        for parameter in (
            ELECTRICAL_USD_PER_KWH,
            IMPORT_DELIVERY_USD_PER_KG,
            CARRIER_GAS_KR_EARTH_USD_PER_KG,
            CARRIER_MAKEUP_LOSS_FRACTION_PER_RUN,
            THERMAL_USD_PER_FLUX_H,
            FURNACE_USD_PER_H,
            LAUNCH_USD_PER_KG,
            REAGENT_USD_PER_KG,
        )
        if parameter.status == OWNER_RATIFY_PLACEHOLDER_STATUS
    )


def carrier_gas_run_cost(
    *,
    carrier_mass_used_kg: float,
    carrier_species: str,
    carrier_body: str = "",
    refrigeration_recycle_energy_kWh: float,
    carrier_gas_earth_price_per_kg: Mapping[str, float],
    import_delivery_cost_per_kg: float,
    makeup_loss_fraction_per_run: float,
    electricity_cost_per_kWh: float,
) -> dict[str, Any]:
    """Price lost carrier makeup and electrical recycle duty for one run."""

    mass_kg = _finite_nonnegative(carrier_mass_used_kg, "carrier_mass_used_kg")
    recycle_kWh = _finite_nonnegative(
        refrigeration_recycle_energy_kWh,
        "refrigeration_recycle_energy_kWh",
    )
    delivery = _finite_nonnegative(
        import_delivery_cost_per_kg,
        "import_delivery_cost_per_kg",
    )
    loss_fraction = _finite_nonnegative(
        makeup_loss_fraction_per_run,
        "makeup_loss_fraction_per_run",
    )
    if loss_fraction > 1.0:
        raise ValueError("makeup_loss_fraction_per_run must be at most 1.0")
    electricity = _finite_nonnegative(
        electricity_cost_per_kWh,
        "electricity_cost_per_kWh",
    )
    species = str(carrier_species or "").strip()
    body = str(carrier_body or "").strip()
    prices = {
        str(key): _finite_nonnegative(value, f"carrier_gas_earth_price_per_kg.{key}")
        for key, value in dict(carrier_gas_earth_price_per_kg).items()
    }

    # Premise: only the per-run makeup loss is bought; the remaining carrier is
    # recycled and pays refrigeration electricity. Algebra: (kg * fraction) *
    # (USD/kg + USD/kg) + kWh * USD/kWh = USD/run. On the stated 44 kg Kr hero,
    # 44 * 0.01 = 0.44 kg and 0.44 * (290 + 10,000) = $4,527.60 before power.
    # Units close to USD/run; at zero carrier mass both terms vanish, while at
    # zero loss only the recirculation electricity term remains.
    makeup_kg = mass_kg * loss_fraction
    recirculated_kg = mass_kg - makeup_kg
    mars_local_source = (
        body.casefold() == "mars" and species in _MARS_LOCAL_CARRIER_SPECIES
    )
    earth_price = None if mars_local_source else prices.get(species)
    has_import_price = earth_price is not None
    body_unknown_assumed_import = not body and has_import_price
    earth_price_pending = (
        has_import_price
        and species in {"Ar", "N2"}
        and float(earth_price) == 0.0
    )
    makeup_cost_usd = (
        makeup_kg * (float(earth_price) + delivery)
        if has_import_price
        else 0.0
    )
    recycle_cost_usd = recycle_kWh * electricity
    price_metadata = _CARRIER_GAS_PRICE_ENTRY["values"].get(species, {})
    configured_earth_price = price_metadata.get("value")
    earth_price_source_tag = (
        (
            price_metadata.get("source_tag")
            or _CARRIER_GAS_PRICE_ENTRY["source_tag"]
        )
        if has_import_price
        and configured_earth_price is not None
        and float(earth_price) == float(configured_earth_price)
        else None
    )
    configured_loss_fraction = _COST_PARAMETER_ENTRIES[
        "carrier_makeup_loss_fraction_per_run"
    ]["value"]
    makeup_loss_source_tag = (
        str(_CARRIER_MAKEUP_LOSS_SOURCE_TAG)
        if loss_fraction == float(configured_loss_fraction)
        else None
    )
    todo = None
    if mars_local_source:
        todo = {
            "code": "carrier_local_source_price_pending",
            "species": species,
            "body": body,
            "detail": (
                f"TODO: model local {species} sourcing on Mars; "
                "no Earth import price is charged."
            ),
        }
    elif not has_import_price:
        todo = {
            "code": "carrier_source_price_pending",
            "species": species,
            "body": body or None,
            "detail": (
                "Earth purchase and delivery prices are configured only for N2, Ar, "
                "and Kr; no import price is charged for this carrier."
            ),
        }
    evidence_flags = []
    if earth_price_source_tag:
        evidence_flags.append(str(earth_price_source_tag))
    if body_unknown_assumed_import:
        evidence_flags.append("carrier_body_unknown_assumed_import")
    if earth_price_pending:
        evidence_flags.append("earth_price_pending_immaterial_vs_delivery")
    if makeup_loss_source_tag:
        evidence_flags.append(makeup_loss_source_tag)
    if todo is not None:
        evidence_flags.append(str(todo["code"]))
    evidence_flags = list(dict.fromkeys(evidence_flags))
    return {
        "carrier_species": species,
        "carrier_body": body or None,
        "carrier_mass_used_kg": mass_kg,
        "makeup_loss_fraction_per_run": loss_fraction,
        "makeup_mass_kg": makeup_kg,
        "recirculated_mass_kg": recirculated_kg,
        "earth_price_per_kg": earth_price,
        "earth_price_source_tag": earth_price_source_tag,
        "earth_price_pending": earth_price_pending,
        "import_delivery_cost_per_kg": delivery if has_import_price else None,
        "makeup_cost_usd": makeup_cost_usd,
        "refrigeration_recycle_energy_kWh": recycle_kWh,
        "recycle_electricity_cost_usd": recycle_cost_usd,
        "carrier_cost_usd": makeup_cost_usd + recycle_cost_usd,
        "pricing_status": "priced" if has_import_price else "price_pending",
        "typed_todo": todo,
        "makeup_loss_source_tag": makeup_loss_source_tag,
        "evidence_flags": evidence_flags,
    }


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

    Distinct dict subclass so the in-process object is unmistakable.
    JSON-loaded copies are plain dicts. Presence is decided only by
    ``is_unavailable_quantity`` (``status == unavailable``). Do not
    override ``__bool__``: CPython's indented / streaming JSON encoder
    truth-tests mappings and would persist a falsy subclass as ``{}``.
    """


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


def json_safe_number(value: Any, *, reason: str, units: str) -> Any:
    """Finite float, or a typed unavailable object. Never a bare NaN/Inf."""

    if isinstance(value, bool) or value is None:
        return unavailable_quantity(reason=reason, units=units)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return unavailable_quantity(reason=reason, units=units)
    if math.isfinite(number):
        return float(number)
    return unavailable_quantity(reason=reason, units=units)


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
