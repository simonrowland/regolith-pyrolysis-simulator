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


OPTIMIZE_COSTS_SCHEMA_VERSION = "optimize-costs-v1"
RECIPE_COST_PARAMETERS_KEY = "cost_parameters"
DEFAULT_COST_PARAMETERS_PATH = DEFAULT_DATA_DIR / "optimize_costs.yaml"
DEFAULT_ELECTRICAL_COST_PER_KWH = 10.0
DEFAULT_SOLAR_HEAT_COST_PER_KWH = 0.05
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
)
_SHUTTLE_PARAMETER = "shuttle_reagent_replacement_cost_per_kg"


@dataclass(frozen=True)
class CostParameters:
    electricity_cost_per_kWh: float
    furnace_resinter_cost_usd: float
    depreciation_expense_per_run: float
    generic_reagent_cost_per_kg: float
    shuttle_reagent_replacement_cost_per_kg: Mapping[str, float] = field(default_factory=dict)
    solar_heat_cost_per_kWh: float = DEFAULT_SOLAR_HEAT_COST_PER_KWH

    def __post_init__(self) -> None:
        for field_name in _REQUIRED_SCALAR_PARAMETERS:
            object.__setattr__(
                self,
                field_name,
                _finite_nonnegative(getattr(self, field_name), field_name),
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

    def reagent_cost_per_kg(self, species: str) -> float:
        if species in SHUTTLE_REAGENT_SPECIES:
            return self.shuttle_reagent_replacement_cost_per_kg[species]
        return self.generic_reagent_cost_per_kg


def load_cost_parameters(path: str | Path | None = None) -> dict[str, Any]:
    cost_path = Path(path) if path is not None else DEFAULT_COST_PARAMETERS_PATH
    try:
        payload = yaml.safe_load(cost_path.read_text(encoding="utf-8"))
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
    raw_parameters = _with_missing_solar_heat_default(raw_parameters)

    parameters: dict[str, Any] = {}
    for name in _REQUIRED_SCALAR_PARAMETERS:
        entry = _parameter_entry(raw_parameters, name)
        parameters[name] = {
            **{str(key): copy.deepcopy(value) for key, value in entry.items() if key != "value"},
            "value": _finite_nonnegative(entry.get("value"), name),
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
    parameters["electricity_cost_per_kWh"] = {
        "value": DEFAULT_ELECTRICAL_COST_PER_KWH,
        "units": "USD/kWh",
        "source_tag": ENERGY_COST_DEFAULT_SOURCE,
    }
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


def _parameter_entry(parameters: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    raw = parameters.get(name)
    if isinstance(raw, Mapping):
        return raw
    if raw is None:
        raise ValueError(f"cost parameter {name!r} is missing")
    return {"value": raw}


def _parameter_value(parameters: Mapping[str, Any], name: str) -> float:
    entry = _parameter_entry(parameters, name)
    return _finite_nonnegative(entry.get("value"), name)


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
