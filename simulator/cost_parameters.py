"""Optimizer cost-parameter loading and canonicalization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
import copy
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from simulator.config import DEFAULT_DATA_DIR
from simulator.yaml_cache import load_cached_safe_yaml


OPTIMIZE_COSTS_SCHEMA_VERSION = "optimize-costs-v1"
RECIPE_COST_PARAMETERS_KEY = "cost_parameters"
DEFAULT_COST_PARAMETERS_PATH = DEFAULT_DATA_DIR / "optimize_costs.yaml"
DEFAULT_SOLAR_HEAT_COST_PER_KWH = 0.05
DEFAULT_FURNACE_LIFETIME_COST_MULTIPLIER = 500.0
DEFAULT_MIN_FOULING_PENALTY = 1.0
DEFAULT_IMPORT_DELIVERY_COST_PER_KG = 10000.0
DEFAULT_CARRIER_GAS_EARTH_PRICE_PER_KG = {"Ar": 0.0, "Kr": 290.0, "N2": 0.0}
DEFAULT_CARRIER_MAKEUP_LOSS_FRACTION_PER_RUN = 0.01
ENERGY_COST_DEFAULT_SOURCE = "owner-t7-two-price-energy-v1"
PAYLOAD_ABSENT_COST_PROVENANCE = (
    "canonical defaults; payload carried no cost parameters"
)
SHUTTLE_REAGENT_SPECIES = frozenset({"Na", "K", "Mg", "Ca"})
_REQUIRED_SCALAR_PARAMETERS = (
    "electricity_cost_per_kWh",
    "solar_heat_cost_per_kWh",
    "furnace_resinter_cost_usd",
    "depreciation_expense_per_run",
    "generic_reagent_cost_per_kg",
    "furnace_lifetime_cost_multiplier",
    "min_fouling_penalty",
    "import_delivery_cost_per_kg",
    "carrier_makeup_loss_fraction_per_run",
)
_SHUTTLE_PARAMETER = "shuttle_reagent_replacement_cost_per_kg"
_CARRIER_PRICE_PARAMETER = "carrier_gas_earth_price_per_kg"
_CARRIER_PRICE_SPECIES = frozenset({"Ar", "Kr", "N2"})


@dataclass(frozen=True)
class CostParameters:
    electricity_cost_per_kWh: float
    furnace_resinter_cost_usd: float
    depreciation_expense_per_run: float
    generic_reagent_cost_per_kg: float
    shuttle_reagent_replacement_cost_per_kg: Mapping[str, float] = field(default_factory=dict)
    solar_heat_cost_per_kWh: float = DEFAULT_SOLAR_HEAT_COST_PER_KWH
    furnace_lifetime_cost_multiplier: float = DEFAULT_FURNACE_LIFETIME_COST_MULTIPLIER
    min_fouling_penalty: float = DEFAULT_MIN_FOULING_PENALTY
    import_delivery_cost_per_kg: float = DEFAULT_IMPORT_DELIVERY_COST_PER_KG
    carrier_gas_earth_price_per_kg: Mapping[str, float] = field(
        default_factory=lambda: dict(DEFAULT_CARRIER_GAS_EARTH_PRICE_PER_KG)
    )
    carrier_makeup_loss_fraction_per_run: float = (
        DEFAULT_CARRIER_MAKEUP_LOSS_FRACTION_PER_RUN
    )

    def __post_init__(self) -> None:
        for field_name in _REQUIRED_SCALAR_PARAMETERS:
            object.__setattr__(
                self,
                field_name,
                _validated_scalar_parameter(getattr(self, field_name), field_name),
            )
        shuttle = {
            str(species): _finite_nonnegative(value, f"{_SHUTTLE_PARAMETER}.{species}")
            for species, value in dict(self.shuttle_reagent_replacement_cost_per_kg).items()
        }
        missing = SHUTTLE_REAGENT_SPECIES.difference(shuttle)
        extra = set(shuttle).difference(SHUTTLE_REAGENT_SPECIES)
        if missing or extra:
            raise ValueError(
                f"{_SHUTTLE_PARAMETER} must define exactly "
                f"{', '.join(sorted(SHUTTLE_REAGENT_SPECIES))}"
            )
        object.__setattr__(
            self,
            "shuttle_reagent_replacement_cost_per_kg",
            MappingProxyType(shuttle),
        )
        carrier_prices = {
            str(species): _finite_nonnegative(value, f"{_CARRIER_PRICE_PARAMETER}.{species}")
            for species, value in dict(self.carrier_gas_earth_price_per_kg).items()
        }
        extra_carrier_prices = set(carrier_prices).difference(_CARRIER_PRICE_SPECIES)
        if extra_carrier_prices:
            raise ValueError(
                f"{_CARRIER_PRICE_PARAMETER} supports only "
                f"{', '.join(sorted(_CARRIER_PRICE_SPECIES))}"
            )
        for species in _CARRIER_PRICE_SPECIES:
            carrier_prices.setdefault(
                species,
                DEFAULT_CARRIER_GAS_EARTH_PRICE_PER_KG[species],
            )
        object.__setattr__(
            self,
            "carrier_gas_earth_price_per_kg",
            MappingProxyType(carrier_prices),
        )

    def reagent_cost_per_kg(self, species: str) -> float:
        if species in SHUTTLE_REAGENT_SPECIES:
            return self.shuttle_reagent_replacement_cost_per_kg[species]
        return self.generic_reagent_cost_per_kg


def load_cost_parameters(path: str | Path | None = None) -> dict[str, Any]:
    cost_path = Path(path) if path is not None else DEFAULT_COST_PARAMETERS_PATH
    try:
        payload = load_cached_safe_yaml(cost_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FileNotFoundError(f"cost parameter config unreadable: {cost_path}") from exc
    if not isinstance(payload, Mapping):
        raise TypeError(f"cost parameter config must be a mapping: {cost_path}")
    is_default_path = cost_path.resolve() == DEFAULT_COST_PARAMETERS_PATH.resolve()
    if is_default_path:
        payload = _with_default_energy_costs(payload)
    source = (
        f"default:{ENERGY_COST_DEFAULT_SOURCE}; base_config:{cost_path}"
        if is_default_path
        else str(cost_path)
    )
    return normalize_cost_parameters(
        payload,
        source=source,
        defaults_applied=False,
    )


def default_cost_parameters_block() -> dict[str, Any]:
    return copy.deepcopy(load_cost_parameters())


def recipe_cost_parameters_from_payload(
    payload: Mapping[str, Any],
    *,
    source: str = "recipe payload",
) -> dict[str, Any]:
    if RECIPE_COST_PARAMETERS_KEY not in payload:
        defaults = load_cost_parameters()
        defaults["provenance"] = {
            **dict(defaults.get("provenance", {})),
            "recipe_source": source,
            "defaults_applied": True,
        }
        return defaults
    raw = payload[RECIPE_COST_PARAMETERS_KEY]
    if not isinstance(raw, Mapping):
        raise TypeError(f"{source} cost_parameters must be a mapping")
    return normalize_cost_parameters(
        raw,
        source=f"{source}::{RECIPE_COST_PARAMETERS_KEY}",
        defaults_applied=False,
    )


def normalize_cost_parameters(
    payload: Mapping[str, Any],
    *,
    source: str,
    defaults_applied: bool,
) -> dict[str, Any]:
    schema_version = str(payload.get("schema_version") or "")
    if schema_version != OPTIMIZE_COSTS_SCHEMA_VERSION:
        raise ValueError(
            "cost parameter schema_version must be "
            f"{OPTIMIZE_COSTS_SCHEMA_VERSION!r}"
        )
    raw_parameters = payload.get("parameters")
    if not isinstance(raw_parameters, Mapping):
        raise ValueError("cost parameter payload missing parameters mapping")
    raw_parameters = _with_missing_carrier_cost_defaults(
        _with_missing_solar_heat_default(raw_parameters)
    )

    parameters: dict[str, Any] = {}
    for name in _REQUIRED_SCALAR_PARAMETERS:
        entry = _parameter_entry(raw_parameters, name)
        parameters[name] = {
            **{str(key): copy.deepcopy(value) for key, value in entry.items() if key != "value"},
            "value": _validated_scalar_parameter(entry.get("value"), name),
        }

    shuttle_entry = _parameter_entry(raw_parameters, _SHUTTLE_PARAMETER)
    raw_values = shuttle_entry.get("values")
    if not isinstance(raw_values, Mapping):
        raise ValueError(f"{_SHUTTLE_PARAMETER} must define values")
    shuttle_values: dict[str, Any] = {}
    for species in sorted(SHUTTLE_REAGENT_SPECIES):
        if species not in raw_values:
            raise ValueError(f"{_SHUTTLE_PARAMETER} missing {species}")
        raw_species = raw_values[species]
        if isinstance(raw_species, Mapping):
            species_entry = dict(raw_species)
            value = species_entry.get("value")
        else:
            species_entry = {}
            value = raw_species
        shuttle_values[species] = {
            **{str(key): copy.deepcopy(item) for key, item in species_entry.items() if key != "value"},
            "value": _finite_nonnegative(value, f"{_SHUTTLE_PARAMETER}.{species}"),
        }
    extra = set(str(key) for key in raw_values).difference(SHUTTLE_REAGENT_SPECIES)
    if extra:
        raise ValueError(f"{_SHUTTLE_PARAMETER} has unsupported species: {sorted(extra)}")
    parameters[_SHUTTLE_PARAMETER] = {
        **{str(key): copy.deepcopy(value) for key, value in shuttle_entry.items() if key not in {"values"}},
        "values": shuttle_values,
    }

    carrier_price_entry = _parameter_entry(raw_parameters, _CARRIER_PRICE_PARAMETER)
    raw_carrier_prices = carrier_price_entry.get("values")
    if not isinstance(raw_carrier_prices, Mapping):
        raise ValueError(f"{_CARRIER_PRICE_PARAMETER} must define values")
    if set(str(key) for key in raw_carrier_prices) != _CARRIER_PRICE_SPECIES:
        raise ValueError(
            f"{_CARRIER_PRICE_PARAMETER} must define exactly "
            f"{', '.join(sorted(_CARRIER_PRICE_SPECIES))}"
        )
    carrier_price_values: dict[str, Any] = {}
    for species in sorted(_CARRIER_PRICE_SPECIES):
        raw_species = raw_carrier_prices[species]
        species_entry = dict(raw_species) if isinstance(raw_species, Mapping) else {}
        value = species_entry.get("value") if species_entry else raw_species
        carrier_price_values[species] = {
            **{str(key): copy.deepcopy(item) for key, item in species_entry.items() if key != "value"},
            "value": _finite_nonnegative(value, f"{_CARRIER_PRICE_PARAMETER}.{species}"),
        }
    parameters[_CARRIER_PRICE_PARAMETER] = {
        **{
            str(key): copy.deepcopy(value)
            for key, value in carrier_price_entry.items()
            if key != "values"
        },
        "values": carrier_price_values,
    }

    return {
        "schema_version": OPTIMIZE_COSTS_SCHEMA_VERSION,
        "parameters": parameters,
        "provenance": {
            "source": source,
            "defaults_applied": bool(defaults_applied),
        },
    }


def cost_parameter_values(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    block = default_cost_parameters_block() if payload is None else normalize_cost_parameters(
        payload,
        source=str(payload.get("provenance", {}).get("source", "cost parameter payload"))
        if isinstance(payload.get("provenance"), Mapping)
        else "cost parameter payload",
        defaults_applied=bool(
            payload.get("provenance", {}).get("defaults_applied", False)
        )
        if isinstance(payload.get("provenance"), Mapping)
        else False,
    )
    raw_parameters = block["parameters"]
    values: dict[str, Any] = {
        name: _parameter_value(raw_parameters, name)
        for name in _REQUIRED_SCALAR_PARAMETERS
    }
    shuttle = raw_parameters[_SHUTTLE_PARAMETER]["values"]
    values[_SHUTTLE_PARAMETER] = {
        species: _finite_nonnegative(entry["value"], f"{_SHUTTLE_PARAMETER}.{species}")
        for species, entry in sorted(shuttle.items())
    }
    carrier_prices = raw_parameters[_CARRIER_PRICE_PARAMETER]["values"]
    values[_CARRIER_PRICE_PARAMETER] = {
        species: _finite_nonnegative(entry["value"], f"{_CARRIER_PRICE_PARAMETER}.{species}")
        for species, entry in sorted(carrier_prices.items())
    }
    return {
        "schema_version": OPTIMIZE_COSTS_SCHEMA_VERSION,
        "parameters": values,
    }


def cost_parameters_digest(payload: Mapping[str, Any] | None = None) -> str:
    canonical = json.dumps(
        _canonical_json_ready(cost_parameter_values(payload)),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def cost_parameters_from_mapping(payload: Mapping[str, Any] | None = None) -> CostParameters:
    values = cost_parameter_values(payload)["parameters"]
    return CostParameters(
        electricity_cost_per_kWh=values["electricity_cost_per_kWh"],
        solar_heat_cost_per_kWh=values["solar_heat_cost_per_kWh"],
        furnace_resinter_cost_usd=values["furnace_resinter_cost_usd"],
        depreciation_expense_per_run=values["depreciation_expense_per_run"],
        generic_reagent_cost_per_kg=values["generic_reagent_cost_per_kg"],
        furnace_lifetime_cost_multiplier=values["furnace_lifetime_cost_multiplier"],
        min_fouling_penalty=values["min_fouling_penalty"],
        import_delivery_cost_per_kg=values["import_delivery_cost_per_kg"],
        carrier_gas_earth_price_per_kg=values[_CARRIER_PRICE_PARAMETER],
        carrier_makeup_loss_fraction_per_run=values[
            "carrier_makeup_loss_fraction_per_run"
        ],
        shuttle_reagent_replacement_cost_per_kg=values[_SHUTTLE_PARAMETER],
    )


def canonical_energy_cost_block(
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    block = default_cost_parameters_block() if payload is None else normalize_cost_parameters(
        payload,
        source=str(payload.get("provenance", {}).get("source", "cost parameter payload"))
        if isinstance(payload.get("provenance"), Mapping)
        else "cost parameter payload",
        defaults_applied=bool(payload.get("provenance", {}).get("defaults_applied", False))
        if isinstance(payload.get("provenance"), Mapping)
        else False,
    )
    values = cost_parameter_values(block)["parameters"]
    provenance = block.get("provenance")
    source = provenance.get("source") if isinstance(provenance, Mapping) else None
    if payload is None:
        source = PAYLOAD_ABSENT_COST_PROVENANCE
    return {
        "electrical_cost_per_kWh": values["electricity_cost_per_kWh"],
        "solar_heat_cost_per_kWh": values["solar_heat_cost_per_kWh"],
        "provenance": str(source or ENERGY_COST_DEFAULT_SOURCE),
    }


def _with_default_energy_costs(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(payload))
    parameters = result.get("parameters")
    if not isinstance(parameters, Mapping):
        return result
    parameters = copy.deepcopy(dict(parameters))
    parameters["solar_heat_cost_per_kWh"] = {
        "value": DEFAULT_SOLAR_HEAT_COST_PER_KWH,
        "units": "USD/kWh",
        "source_tag": ENERGY_COST_DEFAULT_SOURCE,
    }
    result["parameters"] = parameters
    return result


def _with_missing_solar_heat_default(
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(parameters)
    result.setdefault(
        "solar_heat_cost_per_kWh",
        {
            "value": DEFAULT_SOLAR_HEAT_COST_PER_KWH,
            "units": "USD/kWh",
            "source_tag": ENERGY_COST_DEFAULT_SOURCE,
        },
    )
    return result


def _with_missing_carrier_cost_defaults(
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep saved v1 recipe cost blocks usable as new carrier inputs land."""

    result = dict(parameters)
    result.setdefault(
        "import_delivery_cost_per_kg",
        {
            "value": DEFAULT_IMPORT_DELIVERY_COST_PER_KG,
            "units": "USD/kg",
            "rationale": "Owner-ratified import delivery cost, before Earth purchase price.",
            "source_tag": "owner ratified 2026-11-11 (d-389): imports at $10k/kg plus Earth cost",
        },
    )
    result.setdefault(
        _CARRIER_PRICE_PARAMETER,
        {
            "units": "USD/kg",
            "rationale": (
                "approximate European small-quantity price, Ullmann's (1999) via "
                "Wikipedia 'Prices of chemical elements'; USGS MCS 2026 reports "
                "rare-gas prices unavailable"
            ),
            "source_tag": (
                "FLAGGED dated estimate 2026-11-11: Ullmann's (1999) via Wikipedia "
                "'Prices of chemical elements'; USGS MCS 2026 reports rare-gas prices unavailable"
            ),
            "values": {
                "Ar": {
                    "value": DEFAULT_CARRIER_GAS_EARTH_PRICE_PER_KG["Ar"],
                    "source_tag": "earth_price_pending_immaterial_vs_delivery",
                },
                "Kr": {
                    "value": DEFAULT_CARRIER_GAS_EARTH_PRICE_PER_KG["Kr"],
                },
                "N2": {
                    "value": DEFAULT_CARRIER_GAS_EARTH_PRICE_PER_KG["N2"],
                    "source_tag": "earth_price_pending_immaterial_vs_delivery",
                },
            },
        },
    )
    carrier_price_entry = result.get(_CARRIER_PRICE_PARAMETER)
    if isinstance(carrier_price_entry, Mapping):
        raw_values = carrier_price_entry.get("values")
        if isinstance(raw_values, Mapping):
            values = dict(raw_values)
            for species in _CARRIER_PRICE_SPECIES:
                values.setdefault(
                    species,
                    {
                        "value": DEFAULT_CARRIER_GAS_EARTH_PRICE_PER_KG[species],
                        **(
                            {"source_tag": "earth_price_pending_immaterial_vs_delivery"}
                            if species in {"Ar", "N2"}
                            else {}
                        ),
                    },
                )
            result[_CARRIER_PRICE_PARAMETER] = {
                **dict(carrier_price_entry),
                "values": values,
            }
    result.setdefault(
        "carrier_makeup_loss_fraction_per_run",
        {
            "value": DEFAULT_CARRIER_MAKEUP_LOSS_FRACTION_PER_RUN,
            "units": "fraction/run",
            "rationale": (
                "Assumed fraction of recirculated carrier lost per run; visible design input pending an owner value."
            ),
            "source_tag": "FLAGGED: controller assumption pending owner number (d-389)",
        },
    )
    return result


def _parameter_entry(parameters: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    raw = parameters.get(name)
    if isinstance(raw, Mapping):
        return raw
    if raw is None:
        raise ValueError(f"cost parameter {name!r} is missing")
    return {"value": raw}


def _parameter_value(parameters: Mapping[str, Any], name: str) -> float:
    entry = _parameter_entry(parameters, name)
    return _validated_scalar_parameter(entry.get("value"), name)


def _validated_scalar_parameter(value: Any, name: str) -> float:
    if name != "min_fouling_penalty":
        number = _finite_nonnegative(value, name)
        if name == "carrier_makeup_loss_fraction_per_run" and number > 1.0:
            raise ValueError(f"{name} must be at most 1.0")
        return number
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be numeric") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError("min_fouling_penalty must be positive")
    return number


def _finite_nonnegative(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be numeric") from exc
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")
    return number


def _canonical_json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("cost parameter digest rejects NaN and infinity")
        return value
    if isinstance(value, (list, tuple)):
        return [_canonical_json_ready(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _canonical_json_ready(value[key]) for key in sorted(value)}
    raise TypeError(f"unsupported cost parameter digest value: {type(value).__name__}")


# Read the cited YAML after the loader is defined; electricity has no Python override.
DEFAULT_ELECTRICAL_COST_PER_KWH = cost_parameters_from_mapping().electricity_cost_per_kWh
