"""Detached downstream thermal-train sizing diagnostics.

The module accepts recorded flow rates at its boundary, converts them once to
mol/s, and keeps all internal heat-flow arithmetic in J/mol and W.  It neither
reads nor writes AtomLedger and never mutates a simulator run.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from simulator.accounting.formulas import resolve_species_formula
from simulator.condensation import (
    antoine_dew_temperature_diagnostic,
    authoritative_condensation_temperature,
)
from simulator.condensation_routing import designated_stage_number
from simulator.config import DEFAULT_DATA_DIR
from simulator.physical_constants import (
    CELSIUS_TO_KELVIN_OFFSET,
    GAS_CONSTANT,
    STEFAN_BOLTZMANN,
)
from simulator.thermal_budget import latent_vaporization_kj_per_mol
from simulator.transport_regime import (
    carrier_mixture_mean_free_path,
    single_species_mean_free_path,
)


THERMAL_TRAIN_SCHEMA_VERSION = "thermal-train-v2"
THERMAL_TRAIN_REPORT_SCHEMA_VERSION = "thermal-train-report-v2"
DEFAULT_THERMAL_TRAIN_PARAMETERS_PATH = DEFAULT_DATA_DIR / "thermal_train_params.yaml"

SECONDS_PER_HOUR = 3600.0
KELVIN_OFFSET = CELSIUS_TO_KELVIN_OFFSET
HOT_RADIATOR_SPLIT_K = 1000.0
OXYGEN_MOLAR_MASS_KG_PER_MOL = resolve_species_formula("O2").molar_mass_kg_per_mol()
# NBSIR 77-859 gaseous O2 property tables near the 150 K compressor inlet;
# the design holds this first-order constant across the equal-stage ladder.
OXYGEN_GAMMA = 1.395
# NBSIR 77-859, gaseous oxygen specific gas constant.
OXYGEN_SPECIFIC_GAS_CONSTANT_J_PER_KG_K = 259.8

# NIST Chemistry WebBook SRD 69, oxygen phase-change data.
OXYGEN_TRIPLE_POINT_K = 54.361
OXYGEN_TRIPLE_POINT_PA = 146.33
OXYGEN_NORMAL_BOILING_POINT_K = 90.188
OXYGEN_NORMAL_BOILING_PRESSURE_PA = 101325.0
OXYGEN_FUSION_ENTHALPY_J_PER_MOL = 444.0
OXYGEN_VAPORIZATION_ENTHALPY_J_PER_MOL = 6820.0
# NBSIR 77-859, Table 3 and section 6.1, solid O2 at the triple point.
OXYGEN_SUBLIMATION_ENTHALPY_J_PER_MOL = 8199.5
# NBSIR 77-859 low-temperature vapor approximation, bounded 54.361-100 K.
OXYGEN_LOW_T_CP_J_PER_MOL_K = 29.10
# Roder, NBSIR 77-859 sections 6.2/6.4.  The gamma-solid correlation is
# refused below its 44 K program limit; its 54.359 K source endpoint is
# extended 0.002 K to the modern NIST triple point, inside the cited 0.01 K
# temperature uncertainty.  Pressure is normalized to the modern anchor.
OXYGEN_SOLID_VALID_MIN_K = 44.0
OXYGEN_SOLID_VALID_MAX_K = OXYGEN_TRIPLE_POINT_K
_OXYGEN_SOLID_CP_CAL_PER_MOL_K = (16.908081, -0.24181777, 0.0024809089)
_OXYGEN_SUBLIMATION_LOG_COEFFICIENTS = (-1096.562485, -2.025578307, 28.35976524)
MMHG_TO_PA = 101325.0 / 760.0
# NIST WebBook SRD 69, SiO(g) fundamental vibrational band near 1229.6 cm-1;
# theta_v = h*c*wavenumber/k = 1769.1 K.
SIO_VIBRATIONAL_TEMPERATURE_K = 1769.1

_SHOMATE_O2 = (
    # NIST WebBook SRD 69 Shomate coefficients, 100-700 K.
    (100.0, 700.0, (31.32234, -20.23531, 57.86644, -36.50624, -0.007374)),
    # NIST WebBook SRD 69 Shomate coefficients, 700-2000 K.
    (700.0, 2000.0, (30.03235, 8.772972, -3.988133, 0.788313, -0.741599)),
)
_MONATOMIC_SPECIES = frozenset({"Na", "K", "Mg", "Fe", "Ca", "Al", "Cr", "Mn", "Ti"})
_PARAMETER_NAMES = frozenset({
    "eta_2ndlaw", "eta_isen", "emissivity", "T_sink_night_K",
    "T_sink_day_K", "T_floor_K", "T_reject_K", "P_suction_Pa",
    "P_discharge_Pa", "n_compressor_stages", "T_frost_K", "T_storage_K",
    "frost_sticking_fraction", "cavern_capacity_kg",
    "cavern_thermal_mass_J_per_K", "dT_segment_K", "knudsen_locations",
})
_COLD_TRAIN_NAMES = frozenset({
    "runtime_enforcement", "rating", "orifice", "relief", "cycle", "endpoint",
})
_COLD_TRAIN_LIMIT_NAMES = frozenset({
    "compressor_mass_flow_limit_kg_hr", "refrigeration_freeze_rate_kg_hr",
})
_COLD_TRAIN_RATING_REFERENCE_NAMES = frozenset({"p_ref_Pa", "T_ref_K"})
_COLD_TRAIN_RATING_NAMES = _COLD_TRAIN_LIMIT_NAMES | _COLD_TRAIN_RATING_REFERENCE_NAMES
_COLD_TRAIN_ORIFICE_NAMES = frozenset({
    "discharge_coefficient", "upstream_pressure_Pa", "upstream_temperature_K",
    "back_pressure_Pa",
})
_COLD_TRAIN_RELIEF_NAMES = frozenset({"k_relief_kg_hr_Pa", "p_open_Pa", "vessel_rating_Pa"})
_COLD_TRAIN_CYCLE_NAMES = frozenset({"liquid_yield", "expander_bypass_fraction"})
_DISPLAY_PRICE_NAMES = frozenset({
    "radiator_cost_per_m2", "compressor_cost_per_kW", "cryo_cost_per_W",
    "o2_value_per_kg", "fixed_cost_per_hour",
    "o2_storage_keeping_cost_per_kg", "amortization_campaigns",
})
_KNUDSEN_LOCATIONS = frozenset({"throat", "duct"})


@dataclass(frozen=True)
class DisplayPrices:
    radiator_cost_per_m2: float
    compressor_cost_per_kW: float
    cryo_cost_per_W: float
    o2_value_per_kg: float
    fixed_cost_per_hour: float
    o2_storage_keeping_cost_per_kg: Mapping[str, float]
    amortization_campaigns: int

    def __post_init__(self) -> None:
        for name in (
            "radiator_cost_per_m2", "compressor_cost_per_kW", "cryo_cost_per_W",
            "o2_value_per_kg", "fixed_cost_per_hour",
        ):
            object.__setattr__(self, name, _finite_nonnegative(getattr(self, name), name))
        keeping = {
            str(key): _finite_nonnegative(value, f"o2_storage_keeping_cost_per_kg.{key}")
            for key, value in dict(self.o2_storage_keeping_cost_per_kg).items()
        }
        if set(keeping) != {"warm_overburden", "psr"}:
            raise ValueError("o2_storage_keeping_cost_per_kg must define warm_overburden and psr")
        object.__setattr__(self, "o2_storage_keeping_cost_per_kg", MappingProxyType(keeping))
        campaigns = _positive_integer(self.amortization_campaigns, "amortization_campaigns")
        object.__setattr__(self, "amortization_campaigns", campaigns)


@dataclass(frozen=True)
class NoColdTrain:
    reason: str = "not_configured"


@dataclass(frozen=True)
class FiniteCapacity:
    value_kg_hr: float
    p_ref_Pa: float | None = None
    T_ref_K: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value_kg_hr", _finite_positive(self.value_kg_hr, "value_kg_hr")
        )
        for name in _COLD_TRAIN_RATING_REFERENCE_NAMES:
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _finite_positive(value, name))


@dataclass(frozen=True)
class ColdTrainParameters:
    runtime_enforcement: bool
    rating: Mapping[str, float | None]
    orifice: Mapping[str, float]
    relief: Mapping[str, float]
    liquid_yield: float
    expander_bypass_fraction: float
    endpoint: str

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_enforcement, bool):
            raise TypeError("runtime_enforcement must be a boolean")
        rating = {
            name: None if self.rating.get(name) is None else _finite_positive(self.rating[name], name)
            for name in _COLD_TRAIN_RATING_NAMES
        }
        orifice = {name: _finite_positive(self.orifice[name], name) for name in _COLD_TRAIN_ORIFICE_NAMES}
        relief = {name: _finite_positive(self.relief[name], name) for name in _COLD_TRAIN_RELIEF_NAMES}
        y = _fraction(self.liquid_yield, "liquid_yield")
        x = _unit_interval(self.expander_bypass_fraction, "expander_bypass_fraction")
        if x + y > 1.0:
            raise ValueError("expander_bypass_fraction + liquid_yield must be <= 1")
        if self.endpoint != "liquefier_77K":
            raise ValueError("cold-train endpoint must be 'liquefier_77K'")
        if relief["p_open_Pa"] >= relief["vessel_rating_Pa"]:
            raise ValueError("relief p_open_Pa must be below vessel_rating_Pa")
        object.__setattr__(self, "rating", MappingProxyType(rating))
        object.__setattr__(self, "orifice", MappingProxyType(orifice))
        object.__setattr__(self, "relief", MappingProxyType(relief))
        object.__setattr__(self, "liquid_yield", y)
        object.__setattr__(self, "expander_bypass_fraction", x)


@dataclass(frozen=True)
class ThermalTrainParameters:
    eta_2ndlaw: float
    eta_isen: float
    emissivity: float
    T_sink_night_K: float
    T_sink_day_K: float
    T_floor_K: float
    T_reject_K: float
    P_suction_Pa: float
    P_discharge_Pa: float
    n_compressor_stages: int
    T_frost_K: float
    T_storage_K: float
    frost_sticking_fraction: float
    cavern_capacity_kg: float
    cavern_thermal_mass_J_per_K: float
    dT_segment_K: float
    knudsen_locations: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    cold_train: ColdTrainParameters | None = None
    display_prices: DisplayPrices | None = None

    def __post_init__(self) -> None:
        for name in ("eta_2ndlaw", "eta_isen", "emissivity"):
            object.__setattr__(self, name, _fraction(getattr(self, name), name))
        object.__setattr__(
            self,
            "frost_sticking_fraction",
            _unit_interval(self.frost_sticking_fraction, "frost_sticking_fraction"),
        )
        for name in (
            "T_sink_night_K", "T_sink_day_K", "T_floor_K", "T_reject_K",
            "P_suction_Pa", "P_discharge_Pa", "T_frost_K", "T_storage_K",
            "cavern_capacity_kg", "dT_segment_K",
        ):
            object.__setattr__(self, name, _finite_positive(getattr(self, name), name))
        object.__setattr__(
            self,
            "cavern_thermal_mass_J_per_K",
            _finite_nonnegative(self.cavern_thermal_mass_J_per_K, "cavern_thermal_mass_J_per_K"),
        )
        object.__setattr__(
            self,
            "n_compressor_stages",
            _positive_integer(self.n_compressor_stages, "n_compressor_stages"),
        )
        if self.P_discharge_Pa <= self.P_suction_Pa:
            raise ValueError("P_discharge_Pa must exceed P_suction_Pa")
        if self.T_frost_K > OXYGEN_TRIPLE_POINT_K:
            raise ValueError("T_frost_K must not exceed the O2 triple point")
        if self.T_storage_K > OXYGEN_TRIPLE_POINT_K:
            raise ValueError("T_storage_K must not exceed the O2 triple point")
        if set(self.knudsen_locations) != _KNUDSEN_LOCATIONS:
            raise ValueError("knudsen_locations must define throat and duct")
        locations: dict[str, Mapping[str, float]] = {}
        for location, raw in self.knudsen_locations.items():
            if set(raw) != {"L_m", "Kn_threshold"}:
                raise ValueError(f"knudsen location {location!r} has an invalid schema")
            locations[str(location)] = MappingProxyType({
                "L_m": _finite_positive(raw["L_m"], f"{location}.L_m"),
                "Kn_threshold": _finite_positive(raw["Kn_threshold"], f"{location}.Kn_threshold"),
            })
        object.__setattr__(self, "knudsen_locations", MappingProxyType(locations))


def load_thermal_train_parameters(path: str | Path | None = None) -> dict[str, Any]:
    source = Path(path) if path is not None else DEFAULT_THERMAL_TRAIN_PARAMETERS_PATH
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FileNotFoundError(f"thermal-train parameter config unreadable: {source}") from exc
    if not isinstance(payload, Mapping):
        raise TypeError(f"thermal-train parameter config must be a mapping: {source}")
    return normalize_thermal_train_parameters(payload, source=str(source))


def normalize_thermal_train_parameters(payload: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    _require_exact_keys(
        payload, {"schema_version", "parameters", "cold_train", "display_prices"},
        "thermal-train root",
    )
    if payload.get("schema_version") != THERMAL_TRAIN_SCHEMA_VERSION:
        raise ValueError(f"thermal-train schema_version must be {THERMAL_TRAIN_SCHEMA_VERSION!r}")
    raw_parameters = _required_mapping(payload, "parameters")
    raw_prices = _required_mapping(payload, "display_prices")
    raw_cold_train = _required_mapping(payload, "cold_train")
    _require_exact_keys(raw_parameters, _PARAMETER_NAMES, "thermal-train parameters")
    _require_exact_keys(raw_prices, _DISPLAY_PRICE_NAMES, "thermal-train display_prices")

    parameters: dict[str, Any] = {}
    for name in sorted(_PARAMETER_NAMES - {"knudsen_locations"}):
        entry = _validated_value_entry(raw_parameters[name], name, source_tag="assumption")
        parameters[name] = entry
    knudsen = _required_mapping(raw_parameters, "knudsen_locations")
    _require_exact_keys(knudsen, {"source_tag", "values"}, "knudsen_locations")
    if knudsen.get("source_tag") != "assumption":
        raise ValueError("knudsen_locations source_tag must be 'assumption'")
    locations = _required_mapping(knudsen, "values")
    _require_exact_keys(locations, _KNUDSEN_LOCATIONS, "knudsen_locations.values")
    normalized_locations: dict[str, dict[str, float]] = {}
    for location in sorted(_KNUDSEN_LOCATIONS):
        values = _required_mapping(locations, location)
        _require_exact_keys(values, {"L_m", "Kn_threshold"}, f"knudsen location {location}")
        normalized_locations[location] = {
            "L_m": _finite_positive(values["L_m"], f"{location}.L_m"),
            "Kn_threshold": _finite_positive(values["Kn_threshold"], f"{location}.Kn_threshold"),
        }
    parameters["knudsen_locations"] = {
        "source_tag": "assumption",
        "values": normalized_locations,
    }
    cold_train = _normalize_cold_train(raw_cold_train)

    prices: dict[str, Any] = {}
    for name in sorted(_DISPLAY_PRICE_NAMES - {"o2_storage_keeping_cost_per_kg"}):
        prices[name] = _validated_value_entry(
            raw_prices[name], name, source_tag="owner-ratified-2026-07-13"
        )
    keeping = _required_mapping(raw_prices, "o2_storage_keeping_cost_per_kg")
    _require_exact_keys(keeping, {"source_tag", "units", "values"}, "o2 storage keeping price")
    if keeping.get("source_tag") != "owner-ratified-2026-07-13":
        raise ValueError("display price source_tag must be owner-ratified-2026-07-13")
    keeping_values = _required_mapping(keeping, "values")
    _require_exact_keys(keeping_values, {"warm_overburden", "psr"}, "o2 storage keeping values")
    prices["o2_storage_keeping_cost_per_kg"] = {
        "source_tag": keeping["source_tag"],
        "units": str(keeping["units"]),
        "values": {
            key: _finite_nonnegative(value, f"o2_storage_keeping_cost_per_kg.{key}")
            for key, value in keeping_values.items()
        },
    }
    return {
        "schema_version": THERMAL_TRAIN_SCHEMA_VERSION,
        "parameters": parameters,
        "cold_train": cold_train,
        "display_prices": prices,
        "provenance": {"source": source},
    }


def thermal_train_parameters_from_mapping(
    payload: Mapping[str, Any] | None = None,
) -> ThermalTrainParameters:
    clean_payload = payload
    if payload is not None and "provenance" in payload:
        _require_exact_keys(
            payload,
            {"schema_version", "parameters", "cold_train", "display_prices", "provenance"},
            "normalized thermal-train root",
        )
        provenance = _required_mapping(payload, "provenance")
        _require_exact_keys(provenance, {"source"}, "thermal-train provenance")
        clean_payload = {
            key: payload[key]
            for key in ("schema_version", "parameters", "cold_train", "display_prices")
        }
    normalized = load_thermal_train_parameters() if clean_payload is None else normalize_thermal_train_parameters(
        clean_payload,
        source=str(payload.get("provenance", {}).get("source", "thermal-train payload"))
        if payload is not None and isinstance(payload.get("provenance"), Mapping)
        else "thermal-train payload",
    )
    raw = normalized["parameters"]
    values = {name: raw[name]["value"] for name in _PARAMETER_NAMES - {"knudsen_locations"}}
    price_entries = normalized["display_prices"]
    prices = DisplayPrices(
        **{
            name: price_entries[name]["value"]
            for name in _DISPLAY_PRICE_NAMES - {"o2_storage_keeping_cost_per_kg"}
        },
        o2_storage_keeping_cost_per_kg=price_entries["o2_storage_keeping_cost_per_kg"]["values"],
    )
    return ThermalTrainParameters(
        **values,
        knudsen_locations=raw["knudsen_locations"]["values"],
        cold_train=_cold_train_parameters(normalized["cold_train"]),
        display_prices=prices,
    )


def _normalize_cold_train(payload: Mapping[str, Any]) -> dict[str, Any]:
    _require_exact_keys(payload, _COLD_TRAIN_NAMES, "cold_train")
    rating = _required_mapping(payload, "rating")
    orifice = _required_mapping(payload, "orifice")
    relief = _required_mapping(payload, "relief")
    cycle = _required_mapping(payload, "cycle")
    _require_exact_keys(rating, _COLD_TRAIN_RATING_NAMES, "cold_train.rating")
    _require_exact_keys(orifice, _COLD_TRAIN_ORIFICE_NAMES, "cold_train.orifice")
    _require_exact_keys(relief, _COLD_TRAIN_RELIEF_NAMES, "cold_train.relief")
    _require_exact_keys(cycle, _COLD_TRAIN_CYCLE_NAMES, "cold_train.cycle")
    runtime_enforcement = _validated_assumption_entry(
        payload["runtime_enforcement"],
        "runtime_enforcement",
        boolean=True,
    )

    normalized_rating = {
        name: _validated_assumption_entry(rating[name], name, allow_none=True)
        for name in sorted(_COLD_TRAIN_RATING_NAMES)
    }
    normalized_orifice = {
        name: _validated_assumption_entry(orifice[name], name)
        for name in sorted(_COLD_TRAIN_ORIFICE_NAMES)
    }
    normalized_relief = {
        name: _validated_assumption_entry(relief[name], name)
        for name in sorted(_COLD_TRAIN_RELIEF_NAMES)
    }
    normalized_cycle = {
        name: _validated_assumption_entry(cycle[name], name)
        for name in sorted(_COLD_TRAIN_CYCLE_NAMES)
    }
    endpoint = _validated_assumption_entry(payload["endpoint"], "endpoint", enum=True)
    if endpoint["value"] != "liquefier_77K":
        raise ValueError("cold_train.endpoint supports only 'liquefier_77K'")
    x = float(normalized_cycle["expander_bypass_fraction"]["value"])
    y = float(normalized_cycle["liquid_yield"]["value"])
    if not 0.0 <= x <= 1.0 or not 0.0 < y <= 1.0 or x + y > 1.0:
        raise ValueError("cold_train cycle requires 0<=x<=1, 0<y<=1, and x+y<=1")
    if float(normalized_relief["p_open_Pa"]["value"]) >= float(
        normalized_relief["vessel_rating_Pa"]["value"]
    ):
        raise ValueError("relief p_open_Pa must be below vessel_rating_Pa")
    return {
        "runtime_enforcement": runtime_enforcement,
        "rating": normalized_rating,
        "orifice": normalized_orifice,
        "relief": normalized_relief,
        "cycle": normalized_cycle,
        "endpoint": endpoint,
    }


def _cold_train_parameters(payload: Mapping[str, Any]) -> ColdTrainParameters:
    return ColdTrainParameters(
        runtime_enforcement=payload["runtime_enforcement"]["value"],
        rating={name: payload["rating"][name]["value"] for name in _COLD_TRAIN_RATING_NAMES},
        orifice={name: payload["orifice"][name]["value"] for name in _COLD_TRAIN_ORIFICE_NAMES},
        relief={name: payload["relief"][name]["value"] for name in _COLD_TRAIN_RELIEF_NAMES},
        liquid_yield=payload["cycle"]["liquid_yield"]["value"],
        expander_bypass_fraction=payload["cycle"]["expander_bypass_fraction"]["value"],
        endpoint=str(payload["endpoint"]["value"]),
    )


def capacity_from_hardware(
    rating_params: Mapping[str, Any] | ColdTrainParameters | None,
) -> NoColdTrain | FiniteCapacity:
    """Reduce cold-end rating limits to the sole authoritative capacity C."""

    if rating_params is None:
        return NoColdTrain()
    rating = rating_params.rating if isinstance(rating_params, ColdTrainParameters) else rating_params
    limits: list[float] = []
    for name in _COLD_TRAIN_LIMIT_NAMES:
        raw = rating.get(name)
        if isinstance(raw, Mapping) and "value" in raw:
            raw = raw["value"]
        if raw is not None:
            limits.append(_finite_positive(raw, name))
    if not limits:
        return NoColdTrain("rating_limits_unset")
    references: dict[str, float | None] = {}
    for name in _COLD_TRAIN_RATING_REFERENCE_NAMES:
        raw = rating.get(name)
        if isinstance(raw, Mapping) and "value" in raw:
            raw = raw["value"]
        references[name] = None if raw is None else _finite_positive(raw, name)
    return FiniteCapacity(min(limits), **references)


def orifice_diameter_for_C(
    capacity_kg_hr: float,
    discharge_coefficient: float,
    upstream_pressure_Pa: float,
    upstream_temperature_K: float,
    *,
    back_pressure_Pa: float = 0.0,
) -> float:
    """Return choked metering-orifice diameter derived from authoritative C."""

    capacity = _finite_positive(capacity_kg_hr, "capacity_kg_hr") / SECONDS_PER_HOUR
    coefficient = _fraction(discharge_coefficient, "discharge_coefficient")
    pressure = _finite_positive(upstream_pressure_Pa, "upstream_pressure_Pa")
    temperature = _finite_positive(upstream_temperature_K, "upstream_temperature_K")
    back_pressure = _finite_nonnegative(back_pressure_Pa, "back_pressure_Pa")
    saturation_pressure = oxygen_saturation_pressure_pa(temperature)
    critical_ratio = (2.0 / (OXYGEN_GAMMA + 1.0)) ** (
        OXYGEN_GAMMA / (OXYGEN_GAMMA - 1.0)
    )
    if pressure >= 0.95 * saturation_pressure:
        raise ValueError("orifice inlet fails vapor gate: P0 must be < 0.95*P_sat(T0)")
    if back_pressure / pressure > critical_ratio:
        raise ValueError("orifice fails choked gate: P_back/P0 exceeds critical ratio")
    # Premise: m_dot=Cd*A*P0*sqrt(gamma/(Rs*T0))*phi.  Algebra gives
    # A=m_dot/(Cd*P0*sqrt(...)*phi), d=sqrt(4A/pi).  Units reduce to m2
    # then m.  Sanity anchor: Cd=.80, P0=45 kPa, T0=120 K and d=5 mm
    # carries 9.856 kg/hr O2 when P_back=21 kPa.
    phi = (2.0 / (OXYGEN_GAMMA + 1.0)) ** (
        (OXYGEN_GAMMA + 1.0) / (2.0 * (OXYGEN_GAMMA - 1.0))
    )
    mass_flux_factor = pressure * math.sqrt(
        OXYGEN_GAMMA / (OXYGEN_SPECIFIC_GAS_CONSTANT_J_PER_KG_K * temperature)
    ) * phi
    area_m2 = capacity / (coefficient * mass_flux_factor)
    return math.sqrt(4.0 * area_m2 / math.pi)


def claude_cycle_cold_end(
    capacity_kg_hr: float,
    *,
    liquid_yield: float,
    expander_bypass_fraction: float,
    pressure_low_Pa: float,
    pressure_high_Pa: float,
    inlet_temperature_K: float,
    reject_temperature_K: float,
    eta_isen: float,
    makeup_pressure_Pa: float | None = None,
) -> dict[str, Any]:
    """Seven-node first-order Claude-cycle state, mass, and energy ledger."""

    product_kg_hr = _finite_positive(capacity_kg_hr, "capacity_kg_hr")
    y = _fraction(liquid_yield, "liquid_yield")
    x = _unit_interval(expander_bypass_fraction, "expander_bypass_fraction")
    if x + y > 1.0:
        raise ValueError("expander_bypass_fraction + liquid_yield must be <= 1")
    if x == 0.0:
        raise ValueError("Claude-cycle liquefaction requires positive dry-expander flow")
    p_low = _finite_positive(pressure_low_Pa, "pressure_low_Pa")
    p_high = _finite_positive(pressure_high_Pa, "pressure_high_Pa")
    p_makeup = p_low if makeup_pressure_Pa is None else _finite_positive(
        makeup_pressure_Pa, "makeup_pressure_Pa"
    )
    if p_high <= p_low:
        raise ValueError("pressure_high_Pa must exceed pressure_low_Pa")
    t_in = _finite_positive(inlet_temperature_K, "inlet_temperature_K")
    t_reject = _finite_positive(reject_temperature_K, "reject_temperature_K")
    efficiency = _fraction(eta_isen, "eta_isen")
    circulation_kg_hr = product_kg_hr / y
    return_kg_hr = (1.0 - y) * circulation_kg_hr
    bypass_kg_hr = x * circulation_kg_hr
    separator_inlet_kg_hr = (1.0 - x) * circulation_kg_hr
    separator_vapor_kg_hr = (1.0 - x - y) * circulation_kg_hr
    cp_mass = oxygen_cp_shomate_j_per_mol_k(max(t_in, 100.0)) / OXYGEN_MOLAR_MASS_KG_PER_MOL
    sensible_J_mol = integrate_molar_sensible_enthalpy_j_per_mol(
        "O2", OXYGEN_NORMAL_BOILING_POINT_K, t_in,
        segment_K=10.0, allow_low_temperature_o2=True,
    ) if t_in > OXYGEN_NORMAL_BOILING_POINT_K else 0.0
    h1 = (sensible_J_mol + OXYGEN_VAPORIZATION_ENTHALPY_J_PER_MOL) / OXYGEN_MOLAR_MASS_KG_PER_MOL
    h_vapor_nbp = OXYGEN_VAPORIZATION_ENTHALPY_J_PER_MOL / OXYGEN_MOLAR_MASS_KG_PER_MOL
    h_liquid_77 = 0.0
    exponent = (OXYGEN_GAMMA - 1.0) / OXYGEN_GAMMA
    pressure_ratio = p_high / p_low
    h_radiator_out = h_vapor_nbp + cp_mass * (t_reject - OXYGEN_NORMAL_BOILING_POINT_K)
    separator_bath_temperature_K = 77.0
    h_separator_vapor = h_vapor_nbp + cp_mass * (
        separator_bath_temperature_K - OXYGEN_NORMAL_BOILING_POINT_K
    )
    cold_load_W = product_kg_hr / SECONDS_PER_HOUR * (h1 - h_liquid_77)

    # Separator premise: the declared 77 K product load Q_c removes the feed-to-liquid
    # enthalpy, while the vapor leaves on the same linear-cp datum. Solving
    # m_main*h5 = m_product*h_liquid + m_vapor*h_vapor + Q_c gives h5 [J/kg].
    h5 = (
        product_kg_hr * h_liquid_77
        + separator_vapor_kg_hr * h_separator_vapor
        + cold_load_W * SECONDS_PER_HOUR
    ) / separator_inlet_kg_hr
    t5 = OXYGEN_NORMAL_BOILING_POINT_K + (h5 - h_vapor_nbp) / cp_mass
    if t5 <= 0.0:
        raise ValueError("Claude-cycle high-pressure expander inlet temperature must be positive")
    turbine_specific_J_kg = (
        efficiency * cp_mass * t5 * (1.0 - pressure_ratio ** -exponent)
    )
    h6 = h5 - turbine_specific_J_kg
    if return_kg_hr > 0.0:
        h_return_in = (
            bypass_kg_hr * h6 + separator_vapor_kg_hr * h_separator_vapor
        ) / return_kg_hr
        # Recuperator premise: no wall heat or shaft work. Therefore
        # m_circ*(h_rad-h5) = m_return*(h_return_out-h_return_in), in watts
        # after either side is divided by 3600 s/hr.
        h_return_out = h_return_in + circulation_kg_hr / return_kg_hr * (
            h_radiator_out - h5
        )
    else:
        h_return_out = 0.0
        h_return_in = 0.0
    h3 = (
        product_kg_hr * h1 + return_kg_hr * h_return_out
    ) / circulation_kg_hr
    t3 = OXYGEN_NORMAL_BOILING_POINT_K + (h3 - h_vapor_nbp) / cp_mass
    if t3 <= 0.0:
        raise ValueError("Claude-cycle compressor suction temperature must be positive")
    compressor_specific_J_kg = (
        cp_mass * t3 * (pressure_ratio ** exponent - 1.0) / efficiency
    )
    h4 = h3 + compressor_specific_J_kg
    if min(h5, h6, h_return_in, h_return_out) < 0.0:
        raise ValueError("Claude-cycle state solution produced negative specific enthalpy")
    circulation_kg_s = circulation_kg_hr / SECONDS_PER_HOUR
    compressor_W = circulation_kg_s * compressor_specific_J_kg
    turbine_W = bypass_kg_hr / SECONDS_PER_HOUR * turbine_specific_J_kg
    net_work_W = compressor_W - turbine_W
    if net_work_W < 0.0:
        raise ValueError("Claude-cycle net shaft work must be non-negative")
    radiator_reject_W = circulation_kg_s * (h4 - h_radiator_out)
    separator_cooling_W = cold_load_W
    reject_load_W = radiator_reject_W + separator_cooling_W
    if min(radiator_reject_W, separator_cooling_W, reject_load_W) < 0.0:
        raise ValueError("Claude-cycle wall heat must be non-negative")
    mass_residuals = {
        "mixer_kg_hr": product_kg_hr + return_kg_hr - circulation_kg_hr,
        "separator_kg_hr": separator_inlet_kg_hr - product_kg_hr - separator_vapor_kg_hr,
        "return_kg_hr": separator_vapor_kg_hr + bypass_kg_hr - return_kg_hr,
    }
    device_residuals = {
        "mixer_W": (
            product_kg_hr * h1 + return_kg_hr * h_return_out
            - circulation_kg_hr * h3
        ) / SECONDS_PER_HOUR,
        "compressor_W": compressor_W - circulation_kg_s * (h4 - h3),
        "turbine_W": bypass_kg_hr / SECONDS_PER_HOUR * (h5 - h6) - turbine_W,
        "reject_radiator_W": circulation_kg_s * (h4 - h_radiator_out) - radiator_reject_W,
        "recuperator_W": (
            circulation_kg_hr * (h_radiator_out - h5)
            - return_kg_hr * (h_return_out - h_return_in)
        ) / SECONDS_PER_HOUR,
        "separator_W": (
            separator_inlet_kg_hr * h5
            - product_kg_hr * h_liquid_77
            - separator_vapor_kg_hr * h_separator_vapor
        ) / SECONDS_PER_HOUR - separator_cooling_W,
        "cold_return_mixer_W": (
            bypass_kg_hr * h6 + separator_vapor_kg_hr * h_separator_vapor
            - return_kg_hr * h_return_in
        ) / SECONDS_PER_HOUR,
    }
    device_scales = {
        "mixer_W": max(
            abs(product_kg_hr * h1 + return_kg_hr * h_return_out),
            abs(circulation_kg_hr * h3),
        ) / SECONDS_PER_HOUR,
        "compressor_W": max(abs(compressor_W), abs(circulation_kg_s * (h4 - h3))),
        "turbine_W": max(abs(turbine_W), abs(bypass_kg_hr / SECONDS_PER_HOUR * (h5 - h6))),
        "reject_radiator_W": max(
            abs(radiator_reject_W), abs(circulation_kg_s * (h4 - h_radiator_out))
        ),
        "recuperator_W": max(
            abs(circulation_kg_hr * (h_radiator_out - h5)),
            abs(return_kg_hr * (h_return_out - h_return_in)),
        ) / SECONDS_PER_HOUR,
        "separator_W": max(
            abs(separator_inlet_kg_hr * h5),
            abs(product_kg_hr * h_liquid_77 + separator_vapor_kg_hr * h_separator_vapor)
            + abs(separator_cooling_W * SECONDS_PER_HOUR),
        ) / SECONDS_PER_HOUR,
        "cold_return_mixer_W": max(
            abs(bypass_kg_hr * h6 + separator_vapor_kg_hr * h_separator_vapor),
            abs(return_kg_hr * h_return_in),
        ) / SECONDS_PER_HOUR,
    }
    device_relative_residuals = {
        name: value / max(device_scales[name], 1.0)
        for name, value in device_residuals.items()
    }
    plant_in_W = cold_load_W + compressor_W
    plant_out_W = reject_load_W + turbine_W
    plant_residual_W = plant_in_W - plant_out_W
    scale = max(abs(plant_in_W), abs(plant_out_W), 1.0)
    if abs(plant_residual_W) > 1e-6 * scale or any(
        abs(value) > 1e-6
        for value in device_relative_residuals.values()
    ):
        raise AssertionError("Claude-cycle energy ledger failed closure")
    nodes = [
        {"node": 1, "name": "furnace_overhead_makeup", "P_Pa": p_makeup, "T_K": t_in, "h_J_kg": h1, "mass_flow_kg_hr": product_kg_hr},
        {"node": 2, "name": "mixer", "P_Pa": p_low, "T_K": t3, "h_J_kg": h3, "mass_flow_kg_hr": circulation_kg_hr},
        {"node": 3, "name": "compressor_suction", "P_Pa": p_low, "T_K": t3, "h_J_kg": h3, "mass_flow_kg_hr": circulation_kg_hr},
        {"node": 4, "name": "compressor_discharge", "P_Pa": p_high, "T_K": OXYGEN_NORMAL_BOILING_POINT_K + (h4 - h_vapor_nbp) / cp_mass, "h_J_kg": h4, "mass_flow_kg_hr": circulation_kg_hr},
        {"node": 5, "name": "recuperator_hp_out", "P_Pa": p_high, "T_K": t5, "h_J_kg": h5, "mass_flow_kg_hr": circulation_kg_hr},
        {"node": 6, "name": "dry_expander_exhaust", "P_Pa": p_low, "T_K": OXYGEN_NORMAL_BOILING_POINT_K + (h6 - h_vapor_nbp) / cp_mass, "h_J_kg": h6, "mass_flow_kg_hr": bypass_kg_hr},
        {"node": 7, "name": "jt_exit_separator_inlet", "P_Pa": p_low, "T_K": t5, "h_J_kg": h5, "mass_flow_kg_hr": separator_inlet_kg_hr},
    ]
    edges = [
        {"from": 1, "to": 2, "device": "metering_orifice", "mass_flow_kg_hr": product_kg_hr},
        {"from": 2, "to": 3, "device": "mixer_outlet", "mass_flow_kg_hr": circulation_kg_hr},
        {"from": 3, "to": 4, "device": "compressor", "mass_flow_kg_hr": circulation_kg_hr},
        {"from": 4, "to": 5, "device": "reject_radiator_then_recuperator_hp", "mass_flow_kg_hr": circulation_kg_hr},
        {"from": 5, "to": 6, "device": "dry_work_expander", "mass_flow_kg_hr": bypass_kg_hr},
        {"from": 5, "to": 7, "device": "jt_valve", "mass_flow_kg_hr": separator_inlet_kg_hr},
        {"from": 7, "to": "product", "device": "separator_liquid", "mass_flow_kg_hr": product_kg_hr},
        {"from": 7, "to": 5, "device": "separator_vapor_cold_return", "mass_flow_kg_hr": separator_vapor_kg_hr},
        {"from": 6, "to": 5, "device": "expander_exhaust_cold_return", "mass_flow_kg_hr": bypass_kg_hr},
        {"from": 5, "to": 2, "device": "recuperator_cold_return", "mass_flow_kg_hr": return_kg_hr},
    ]
    return {
        "model": "claude_cycle_7_node_open_plant_v1",
        "endpoint": "liquefier_77K",
        "mass_basis": {
            "product_kg_hr": product_kg_hr,
            "circulation_kg_hr": circulation_kg_hr,
            "return_kg_hr": return_kg_hr,
            "expander_bypass_kg_hr": bypass_kg_hr,
            "separator_inlet_kg_hr": separator_inlet_kg_hr,
            "separator_vapor_kg_hr": separator_vapor_kg_hr,
            "liquid_yield": y,
            "expander_bypass_fraction": x,
        },
        "nodes": nodes,
        "edges": edges,
        "mass_residuals": mass_residuals,
        "energy": {
            "compressor_work_W": compressor_W,
            "turbine_work_out_W": turbine_W,
            "net_work_W": net_work_W,
            "cold_load_W": cold_load_W,
            "reject_load_W": reject_load_W,
            "radiator_reject_W": radiator_reject_W,
            "separator_cold_W": separator_cooling_W,
            "separator_bath_temperature_K": separator_bath_temperature_K,
            "W_comp_W": compressor_W,
            "W_turb_out_W": turbine_W,
            "W_net_W": net_work_W,
            "Q_c_W": cold_load_W,
            "Q_h_W": reject_load_W,
            "plant_energy_in_W": plant_in_W,
            "plant_energy_out_W": plant_out_W,
            "plant_residual_W": plant_residual_W,
            "plant_relative_residual": plant_residual_W / scale,
            "device_residuals_W": device_residuals,
            "device_relative_residuals": device_relative_residuals,
            "control_volume_identity": "h0 leaves only through wall heat or shaft work",
            "lox_77K_enthalpy_basis": "NBP latent plus gas sensible; 90.188-to-77 K liquid subcooling omitted because no sourced liquid-Cp anchor is configured",
        },
        "refrigeration_work_W": net_work_W,
    }


def mass_rate_kg_hr_to_molar_rate_mol_s(species: str, mass_rate_kg_hr: float) -> float:
    """Project one recorded kg/hr rate to the module's mol/s boundary."""

    rate = _finite_nonnegative(mass_rate_kg_hr, f"mass rate for {species}")
    molar_mass = resolve_species_formula(str(species)).molar_mass_kg_per_mol()
    # kg/hr / (kg/mol * s/hr) = mol/s.  This is the only mass-rate conversion
    # in the train; downstream enthalpy terms multiply this mol/s value by J/mol.
    return rate / (SECONDS_PER_HOUR * molar_mass)


def molar_rate_mol_s_to_mass_rate_kg_hr(species: str, molar_rate_mol_s: float) -> float:
    """Project an internal mol/s rate to a report-edge kg/hr value."""

    rate = _finite_nonnegative(molar_rate_mol_s, f"molar rate for {species}")
    return rate * SECONDS_PER_HOUR * resolve_species_formula(str(species)).molar_mass_kg_per_mol()


def oxygen_cp_shomate_j_per_mol_k(temperature_K: float) -> float:
    """Return NIST Shomate Cp for gaseous O2, refusing extrapolation."""

    temperature = _finite_positive(temperature_K, "temperature_K")
    for index, (low, high, coefficients) in enumerate(_SHOMATE_O2):
        if low <= temperature <= high and not (index == 1 and temperature == low):
            t = temperature / 1000.0
            a, b, c, d, e = coefficients
            return a + b * t + c * t ** 2 + d * t ** 3 + e / t ** 2
    raise ValueError("O2 Shomate Cp valid only from 100 K through 2000 K")


def oxygen_cp_j_per_mol_k(temperature_K: float, *, allow_low_temperature: bool = False) -> float:
    temperature = _finite_positive(temperature_K, "temperature_K")
    if OXYGEN_TRIPLE_POINT_K <= temperature < 100.0 and allow_low_temperature:
        return OXYGEN_LOW_T_CP_J_PER_MOL_K
    return oxygen_cp_shomate_j_per_mol_k(temperature)


def solid_oxygen_cp_j_per_mol_k(temperature_K: float) -> float:
    """NBSIR 77-859 section 6.4 gamma-solid O2 heat capacity."""

    temperature = _finite_positive(temperature_K, "temperature_K")
    if not OXYGEN_SOLID_VALID_MIN_K <= temperature <= OXYGEN_SOLID_VALID_MAX_K:
        raise ValueError(
            "solid O2 Cp valid only from 44 K through the O2 triple point"
        )
    a, b, c = _OXYGEN_SOLID_CP_CAL_PER_MOL_K
    return (a + b * temperature + c * temperature ** 2) * 4.184


def vapor_cp_j_per_mol_k(species: str, temperature_K: float) -> float:
    temperature = _finite_positive(temperature_K, "temperature_K")
    if species == "O2":
        return oxygen_cp_shomate_j_per_mol_k(temperature)
    if species in _MONATOMIC_SPECIES:
        return 2.5 * GAS_CONSTANT
    if species == "SiO":
        x = SIO_VIBRATIONAL_TEMPERATURE_K / temperature
        vibrational = GAS_CONSTANT * x ** 2 * math.exp(x) / math.expm1(x) ** 2
        return 3.5 * GAS_CONSTANT + vibrational
    raise KeyError(f"no vapor Cp model for {species!r}")


def oxygen_saturation_pressure_pa(temperature_K: float) -> float:
    """Return a phase-aware O2 saturation pressure joined at the triple point."""

    temperature = _finite_positive(temperature_K, "temperature_K")
    if temperature <= OXYGEN_TRIPLE_POINT_K:
        if temperature < OXYGEN_SOLID_VALID_MIN_K:
            raise ValueError(
                "solid O2 sublimation pressure valid only from 44 K through the triple point"
            )
        a, b, c = _OXYGEN_SUBLIMATION_LOG_COEFFICIENTS

        def source_pressure(value_K: float) -> float:
            return math.exp(a / value_K + b * math.log(value_K) + c) * MMHG_TO_PA

        # Roder's correlation uses the 1977 54.359 K / 0.0014451 atm
        # fixed point.  Multiplying by one constant preserves its measured
        # shape while making the modern NIST triple-point join exact.
        return source_pressure(temperature) * (
            OXYGEN_TRIPLE_POINT_PA / source_pressure(OXYGEN_TRIPLE_POINT_K)
        )
    # A two-anchor Clausius-Clapeyron diagnostic is used for the liquid branch:
    # solve ΔH_eff/R = ln(P_b/P_tp)/(1/T_tp-1/T_b), then anchor at P_tp.
    # It is deliberately separate from the tagged 90.188 K ΔHvap datum.
    effective_enthalpy = GAS_CONSTANT * math.log(
        OXYGEN_NORMAL_BOILING_PRESSURE_PA / OXYGEN_TRIPLE_POINT_PA
    ) / (1.0 / OXYGEN_TRIPLE_POINT_K - 1.0 / OXYGEN_NORMAL_BOILING_POINT_K)
    exponent = -effective_enthalpy / GAS_CONSTANT * (
        1.0 / temperature - 1.0 / OXYGEN_TRIPLE_POINT_K
    )
    return OXYGEN_TRIPLE_POINT_PA * math.exp(exponent)


def oxygen_deposition_gate(partial_pressure_pa: float, wall_temperature_K: float) -> dict[str, Any]:
    partial_pressure = _finite_nonnegative(partial_pressure_pa, "partial_pressure_pa")
    saturation_pressure = oxygen_saturation_pressure_pa(wall_temperature_K)
    return {
        "partial_pressure_Pa": partial_pressure,
        "sublimation_pressure_Pa": saturation_pressure,
        "frost_forms": partial_pressure > saturation_pressure,
        "criterion": "p_O2 > P_sub(T_wall)",
    }


def integrate_molar_sensible_enthalpy_j_per_mol(
    species: str,
    temperature_low_K: float,
    temperature_high_K: float,
    *,
    segment_K: float,
    allow_low_temperature_o2: bool = False,
) -> float:
    low = _finite_positive(temperature_low_K, "temperature_low_K")
    high = _finite_positive(temperature_high_K, "temperature_high_K")
    step = _finite_positive(segment_K, "segment_K")
    if high < low:
        low, high = high, low
    if high == low:
        return 0.0
    total = 0.0
    cursor = low
    while cursor < high:
        upper = min(high, cursor + step)
        if species == "O2":
            for boundary in (100.0, 700.0, 2000.0):
                if cursor < boundary < upper:
                    upper = boundary
                    break
        midpoint = (cursor + upper) / 2.0
        cp = (
            oxygen_cp_j_per_mol_k(midpoint, allow_low_temperature=True)
            if species == "O2" and allow_low_temperature_o2
            else vapor_cp_j_per_mol_k(species, midpoint)
        )
        total += cp * (upper - cursor)
        cursor = upper
    return total


def integrate_solid_oxygen_enthalpy_j_per_mol(
    temperature_low_K: float,
    temperature_high_K: float,
    *,
    segment_K: float,
) -> float:
    low = _finite_positive(temperature_low_K, "temperature_low_K")
    high = _finite_positive(temperature_high_K, "temperature_high_K")
    step = _finite_positive(segment_K, "segment_K")
    if high < low:
        low, high = high, low
    if not OXYGEN_SOLID_VALID_MIN_K <= low <= high <= OXYGEN_SOLID_VALID_MAX_K:
        raise ValueError(
            "solid O2 sensible integral valid only from 44 K through the triple point"
        )
    total = 0.0
    cursor = low
    while cursor < high:
        upper = min(high, cursor + step)
        midpoint = (cursor + upper) / 2.0
        total += solid_oxygen_cp_j_per_mol_k(midpoint) * (upper - cursor)
        cursor = upper
    return total


def thermal_train_section_ownership(
    crossing_temperature_K: float,
    inlet_temperature_K: float,
    floor_temperature_K: float,
    *,
    split_temperature_K: float = HOT_RADIATOR_SPLIT_K,
) -> dict[str, Any]:
    crossing = _finite_positive(crossing_temperature_K, "crossing_temperature_K")
    inlet = _finite_positive(inlet_temperature_K, "inlet_temperature_K")
    floor = _finite_positive(floor_temperature_K, "floor_temperature_K")
    split = _finite_positive(split_temperature_K, "split_temperature_K")
    mid_inlet = min(inlet, split)

    def positive_intersection(
        first: tuple[float, float], second: tuple[float, float]
    ) -> tuple[float, float] | None:
        lower = max(first[0], second[0])
        upper = min(first[1], second[1])
        return (lower, upper) if upper > lower else None

    # Vapor exists only on [crossing, inlet].  Intersecting that path with
    # each physical section prevents a latent boundary convention from also
    # inventing (or dropping) sensible duty in the neighboring section.
    vapor_interval = (crossing, inlet)
    hot_sensible_interval = positive_intersection(vapor_interval, (split, inlet))
    mid_sensible_interval = positive_intersection(vapor_interval, (floor, mid_inlet))

    latent_owner = None
    exclusion_reason = None
    if crossing > inlet:
        exclusion_reason = "authoritative_condensation_temperature_above_train_inlet"
    elif crossing < floor:
        exclusion_reason = "authoritative_condensation_temperature_below_train_floor"
    elif crossing >= split:
        latent_owner = "hot"
    else:
        latent_owner = "mid"

    return {
        "latent_owner": latent_owner,
        "exclusion_reason": exclusion_reason,
        "hot_sensible_interval_K": hot_sensible_interval,
        "mid_sensible_interval_K": mid_sensible_interval,
    }


def segmented_radiator_area_m2(
    molar_rates_mol_s: Mapping[str, float],
    *,
    temperature_in_K: float,
    temperature_out_K: float,
    sink_temperature_K: float,
    emissivity: float,
    segment_K: float,
    latent_crossings_K: Mapping[str, float] | None = None,
    sink_margin_K: float = 0.0,
) -> dict[str, Any]:
    temperature_in = _finite_positive(temperature_in_K, "temperature_in_K")
    temperature_out = _finite_positive(temperature_out_K, "temperature_out_K")
    sink = _finite_nonnegative(sink_temperature_K, "sink_temperature_K")
    epsilon = _fraction(emissivity, "emissivity")
    step = _finite_positive(segment_K, "segment_K")
    margin = _finite_nonnegative(sink_margin_K, "sink_margin_K")
    if temperature_in < temperature_out:
        raise ValueError("radiator temperature_in_K must be >= temperature_out_K")
    rates = {str(species): _finite_nonnegative(value, f"molar rate for {species}") for species, value in molar_rates_mol_s.items()}
    if temperature_out <= sink + margin:
        active_lift_W = sum(
            rate * integrate_molar_sensible_enthalpy_j_per_mol(
                species, temperature_out, temperature_in, segment_K=step
            )
            for species, rate in rates.items()
        )
        return {
            "status": "passive_refused",
            "reason": "target_not_above_effective_sink_margin",
            "area_m2": None,
            "sensible_load_W": active_lift_W,
            "latent_load_W": 0.0,
            "active_lift_W": active_lift_W,
        }

    sensible_area = 0.0
    sensible_load = 0.0
    # Every discontinuity is an exact grid edge.  Midpoint quadrature may
    # approximate smooth Cp/flux variation, but must never smear a phase
    # crossing or a Shomate coefficient boundary across a whole segment.
    boundaries = {temperature_out, temperature_in}
    cursor = temperature_out
    while cursor < temperature_in:
        cursor = min(temperature_in, cursor + step)
        boundaries.add(cursor)
    for boundary in (100.0, 700.0, 2000.0):
        if temperature_out < boundary < temperature_in:
            boundaries.add(boundary)
    for crossing in (latent_crossings_K or {}).values():
        value = float(crossing)
        if temperature_out < value < temperature_in:
            boundaries.add(value)
    ordered = sorted(boundaries)
    for cursor, upper in zip(ordered, ordered[1:]):
        midpoint = (cursor + upper) / 2.0
        radiative_flux = epsilon * STEFAN_BOLTZMANN * (midpoint ** 4 - sink ** 4)
        for species, rate in rates.items():
            crossing = (latent_crossings_K or {}).get(species)
            if crossing is not None and midpoint < float(crossing):
                continue
            # Premise: one segment removes n_dot*Cp*dT.  Algebra and units:
            # (mol/s)*(J/mol/K)*K = W, then A = W/(W/m2).  For 1 kg/hr Na
            # across 0.01 K at 1000 K this gives 2.51 mW and 4.43e-8 m2.
            load = rate * vapor_cp_j_per_mol_k(species, midpoint) * (upper - cursor)
            sensible_load += load
            sensible_area += load / radiative_flux

    latent_area = 0.0
    latent_load = 0.0
    crossing_rows: dict[str, dict[str, float]] = {}
    for species, crossing in sorted((latent_crossings_K or {}).items()):
        if species not in rates or rates[species] <= 0.0:
            continue
        crossing_temperature = _finite_positive(crossing, f"latent crossing for {species}")
        if not temperature_out <= crossing_temperature <= temperature_in:
            continue
        latent = latent_vaporization_kj_per_mol(species) * 1000.0
        # n_dot[mol/s]*DeltaH[J/mol] = W; dividing by the radiative wall flux
        # gives m2.  At 1 kg/hr Na the sourced latent term is about 1.177 kW.
        load = rates[species] * latent
        flux = epsilon * STEFAN_BOLTZMANN * (crossing_temperature ** 4 - sink ** 4)
        area = load / flux
        latent_load += load
        latent_area += area
        crossing_rows[species] = {
            "temperature_K": crossing_temperature,
            "load_W": load,
            "area_m2": area,
        }
    return {
        "status": "sized",
        "area_m2": sensible_area + latent_area,
        "sensible_area_m2": sensible_area,
        "latent_area_m2": latent_area,
        "sensible_load_W": sensible_load,
        "latent_load_W": latent_load,
        "active_lift_W": 0.0,
        "latent_crossings": crossing_rows,
    }


def intercooled_compression(
    oxygen_molar_rate_mol_s: float,
    *,
    pressure_suction_Pa: float,
    pressure_discharge_Pa: float,
    stages: int,
    inlet_temperature_K: float,
    eta_isen: float,
) -> dict[str, Any]:
    rate = _finite_nonnegative(oxygen_molar_rate_mol_s, "oxygen_molar_rate_mol_s")
    suction = _finite_positive(pressure_suction_Pa, "pressure_suction_Pa")
    discharge = _finite_positive(pressure_discharge_Pa, "pressure_discharge_Pa")
    count = _positive_integer(stages, "stages")
    temperature = _finite_positive(inlet_temperature_K, "inlet_temperature_K")
    efficiency = _fraction(eta_isen, "eta_isen")
    if discharge <= suction:
        raise ValueError("pressure_discharge_Pa must exceed pressure_suction_Pa")
    pressure_ratio = discharge / suction
    stage_ratio = pressure_ratio ** (1.0 / count)
    cp = oxygen_cp_shomate_j_per_mol_k(temperature)
    ideal_stage_rise_J_mol = cp * temperature * (
        stage_ratio ** ((OXYGEN_GAMMA - 1.0) / OXYGEN_GAMMA) - 1.0
    )
    # Each ideal stage enthalpy rise is rejected by the intercooler.  Shaft
    # input divides that rise by eta, so Q_intercool,total = W_shaft*eta.
    shaft_W = count * rate * ideal_stage_rise_J_mol / efficiency
    intercooler_W = count * rate * ideal_stage_rise_J_mol
    return {
        "pressure_ratio_total": pressure_ratio,
        "pressure_ratio_per_stage": stage_ratio,
        "compressor_shaft_W": shaft_W,
        "intercooler_reject_W": intercooler_W,
        "intercooler_reject_W_per_stage": intercooler_W / count,
        "stage_count": count,
        "eta_isen": efficiency,
    }


def cryogenic_tail(
    oxygen_molar_rate_mol_s: float,
    *,
    temperature_floor_K: float,
    temperature_frost_K: float,
    temperature_reject_K: float,
    eta_2ndlaw: float,
    segment_K: float,
) -> dict[str, Any]:
    rate = _finite_nonnegative(oxygen_molar_rate_mol_s, "oxygen_molar_rate_mol_s")
    floor = _finite_positive(temperature_floor_K, "temperature_floor_K")
    frost = _finite_positive(temperature_frost_K, "temperature_frost_K")
    reject = _finite_positive(temperature_reject_K, "temperature_reject_K")
    efficiency = _fraction(eta_2ndlaw, "eta_2ndlaw")
    if frost < OXYGEN_TRIPLE_POINT_K:
        raise ValueError(
            f"temperature_frost_K must be at least the O2 triple point "
            f"({OXYGEN_TRIPLE_POINT_K} K) for the gaseous sensible-heat integral"
        )
    if not frost < floor < reject:
        raise ValueError("cryo temperatures must satisfy T_frost < T_floor < T_reject")
    sensible_J_mol = integrate_molar_sensible_enthalpy_j_per_mol(
        "O2", frost, floor, segment_K=segment_K, allow_low_temperature_o2=True
    )
    # Cold load is n_dot[mol/s]*(integral Cp dT + DeltaH_sub)[J/mol] = W.
    # A refrigerator lifting the full load from T_f has Carnot work
    # Qc*(Th/Tf-1); eta_2ndlaw divides that ideal performance.
    cold_load_W = rate * (sensible_J_mol + OXYGEN_SUBLIMATION_ENTHALPY_J_PER_MOL)
    work_W = cold_load_W * (reject / frost - 1.0) / efficiency
    return {
        "cold_load_W": cold_load_W,
        "sensible_load_W": rate * sensible_J_mol,
        "sublimation_load_W": rate * OXYGEN_SUBLIMATION_ENTHALPY_J_PER_MOL,
        "refrigeration_work_W": work_W,
        "reject_load_W": cold_load_W + work_W,
        "model": "full_cold_load_lifted_from_T_frost_upper_bound",
    }


def cavern_regeneration_energy_J(
    oxygen_batch_mol: float,
    *,
    storage_temperature_K: float,
    cavern_thermal_mass_J_per_K: float,
    segment_K: float,
) -> dict[str, float]:
    amount = _finite_nonnegative(oxygen_batch_mol, "oxygen_batch_mol")
    storage = _finite_positive(storage_temperature_K, "storage_temperature_K")
    thermal_mass = _finite_nonnegative(cavern_thermal_mass_J_per_K, "cavern_thermal_mass_J_per_K")
    step = _finite_positive(segment_K, "segment_K")
    if storage > OXYGEN_TRIPLE_POINT_K:
        raise ValueError("storage_temperature_K must not exceed the O2 triple point")
    sensible_per_mol = integrate_solid_oxygen_enthalpy_j_per_mol(
        storage,
        OXYGEN_TRIPLE_POINT_K,
        segment_K=step,
    )
    # Final state is liquid at the triple point: n*(integral Cp_s dT + H_fus),
    # not H_sub-H_fus.  J/mol*mol and J/K*K both close in joules; at T_tp the
    # sensible and wall terms vanish, leaving exactly n*444 J/mol.
    phase_J = amount * OXYGEN_FUSION_ENTHALPY_J_PER_MOL
    sensible_J = amount * sensible_per_mol
    wall_J = thermal_mass * (OXYGEN_TRIPLE_POINT_K - storage)
    return {
        "oxygen_sensible_J": sensible_J,
        "oxygen_fusion_J": phase_J,
        "cavern_walls_J": wall_J,
        "total_J": sensible_J + phase_J + wall_J,
        "integration_segment_K": step,
    }


def thermal_train_overflow_kg_hr(cold_inlet_kg_hr: float, rated_capacity_kg_hr: float) -> float:
    inlet = _finite_nonnegative(cold_inlet_kg_hr, "cold_inlet_kg_hr")
    capacity = _finite_nonnegative(rated_capacity_kg_hr, "rated_capacity_kg_hr")
    return max(0.0, inlet - capacity)


def knudsen_anchor(
    *,
    location: str,
    pressure_pa: float,
    temperature_K: float,
    characteristic_length_m: float,
    threshold: float,
    carrier_mole_fractions: Mapping[str, float] | None = None,
    test_species: str = "O2",
) -> dict[str, Any]:
    if location == "cavern":
        result = single_species_mean_free_path(
            "O2", pressure_pa, temperature_K, characteristic_length_m
        )
        basis = "pure_O2_post_separator"
    else:
        if not carrier_mole_fractions:
            raise ValueError("furnace/throat Knudsen anchors require carrier_mole_fractions")
        result = carrier_mixture_mean_free_path(
            test_species,
            carrier_mole_fractions,
            pressure_pa,
            temperature_K,
            characteristic_length_m,
        )
        basis = "mixture"
    selected_threshold = _finite_positive(threshold, "threshold")
    return {
        "location": location,
        "basis": basis,
        "lambda_m": result.lambda_m,
        "Kn": result.knudsen_number,
        "regime": result.regime,
        "threshold": selected_threshold,
        "threshold_interpretation": "minimum_Kn_for_rarefaction",
        "rarefaction_threshold_met": result.knudsen_number >= selected_threshold,
        "formula_id": result.formula_id,
    }


def condensation_stage_windows_K(setpoints: Mapping[str, Any]) -> dict[str, tuple[float, float]]:
    train = setpoints.get("condensation_train")
    if not isinstance(train, Mapping):
        return {}
    metals = train.get("metals_train")
    if not isinstance(metals, Mapping):
        return {}
    windows: dict[str, tuple[float, float]] = {}
    for species in ("Fe", "Cr", "CrO2", "Mn", "SiO", "Na", "K", "Mg"):
        stage_number = designated_stage_number(species)
        stage_key = {
            1: "stage_1_fe_condenser",
            2: "stage_2_cr_oxide_harvest",
            3: "stage_3_sio_zone",
            4: "stage_4_alkali_mg_cyclone",
        }.get(stage_number)
        stage = metals.get(stage_key) if stage_key else None
        band = stage.get("temp_range_C") if isinstance(stage, Mapping) else None
        if isinstance(band, Sequence) and not isinstance(band, (str, bytes)) and len(band) == 2:
            low = _finite_positive(float(band[0]) + KELVIN_OFFSET, f"{species} stage low K")
            high = _finite_positive(float(band[1]) + KELVIN_OFFSET, f"{species} stage high K")
            if high >= low:
                windows[species] = (low, high)
    return windows


def _peak_partial_pressures_pa(
    overhead_state_series: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    peaks: dict[str, float] = {}
    for state in overhead_state_series:
        composition = state.get("composition_mbar", {})
        if not isinstance(composition, Mapping):
            continue
        for species, value in composition.items():
            try:
                pressure = _finite_nonnegative(value, f"{species} partial pressure mbar") * 100.0
            except (TypeError, ValueError):
                continue
            peaks[str(species)] = max(peaks.get(str(species), 0.0), pressure)
    return peaks


def _knudsen_report(
    overhead_state_series: Sequence[Mapping[str, Any]],
    params: ThermalTrainParameters,
) -> dict[str, Any]:
    rows: dict[str, list[dict[str, Any]]] = {"throat": [], "duct": []}
    refusals: list[dict[str, Any]] = []
    for index, state in enumerate(overhead_state_series):
        try:
            pressure_pa = _finite_positive(state.get("pressure_Pa"), "overhead pressure")
            temperature_K = _finite_positive(state.get("temperature_K"), "overhead temperature")
            composition = state.get("composition_mbar", {})
            if not isinstance(composition, Mapping):
                raise ValueError("overhead composition unavailable")
            positive = {
                str(species): _finite_nonnegative(value, f"{species} partial mbar")
                for species, value in composition.items()
                if float(value) > 0.0
            }
            total = sum(positive.values())
            if total <= 0.0:
                raise ValueError("overhead composition has no positive partial pressures")
            fractions = {species: value / total for species, value in positive.items()}
            test_species = "O2" if "O2" in fractions else max(fractions, key=fractions.get)
            throat_recorded_m = float(state.get("throat_diameter_m") or 0.0)
            for location in ("throat", "duct"):
                config = params.knudsen_locations[location]
                length = throat_recorded_m if location == "throat" and throat_recorded_m > 0.0 else config["L_m"]
                rows[location].append(
                    knudsen_anchor(
                        location=location,
                        pressure_pa=pressure_pa,
                        temperature_K=temperature_K,
                        characteristic_length_m=length,
                        threshold=config["Kn_threshold"],
                        carrier_mole_fractions=fractions,
                        test_species=test_species,
                    )
                )
        except (KeyError, TypeError, ValueError) as exc:
            refusals.append({"snapshot_index": index, "reason": str(exc)})
    anchors: dict[str, Any] = {}
    for location, candidates in rows.items():
        if candidates:
            anchors[location] = min(candidates, key=lambda row: float(row["Kn"]))
        else:
            anchors[location] = {"status": "inputs_required"}
    anchors["cavern"] = {
        "status": "inputs_required",
        "reason": "cavern pressure and characteristic state are not recorded",
    }
    return {
        "status": "computed_with_refusals" if refusals and any(rows.values()) else (
            "computed" if any(rows.values()) else "inputs_required"
        ),
        "anchors": anchors,
        "refusals": refusals,
    }


def report_from_recorded_series(
    hot_species_kg_hr_series: Sequence[Mapping[str, float]],
    oxygen_mol_hr_series: Sequence[float],
    temperature_K_series: Sequence[float],
    *,
    setpoints: Mapping[str, Any],
    observed_transport_saturation_pct: Sequence[float] = (),
    observed_o2_vented_kg_hr: Sequence[float] = (),
    overhead_state_series: Sequence[Mapping[str, Any]] = (),
    parameters: ThermalTrainParameters | None = None,
    expected_refractory_trace_species: Sequence[str] = (),
) -> dict[str, Any]:
    params = parameters or thermal_train_parameters_from_mapping()
    trace_authority = frozenset(str(species) for species in expected_refractory_trace_species)
    unsupported_trace_authority = trace_authority - {"SiO", "CrO2"}
    if unsupported_trace_authority:
        raise ValueError(
            "expected_refractory_trace_species may contain only SiO and CrO2"
        )
    snapshot_count = max(len(hot_species_kg_hr_series), len(oxygen_mol_hr_series))
    if snapshot_count == 0:
        return empty_thermal_train_report("no_run_history")
    windows = condensation_stage_windows_K(setpoints)
    peak_hot_molar_by_species: dict[str, float] = {}
    hot_molar_rows: list[dict[str, float]] = []
    entry_refusals: dict[str, str] = {}
    for row in hot_species_kg_hr_series:
        row_molar: dict[str, float] = {}
        for species, raw_rate in row.items():
            name = str(species)
            try:
                rate = mass_rate_kg_hr_to_molar_rate_mol_s(name, raw_rate)
            except (KeyError, TypeError, ValueError) as exc:
                entry_refusals[name] = exc.args[0] if isinstance(exc, KeyError) else str(exc)
                continue
            row_molar[name] = rate
            peak_hot_molar_by_species[name] = max(
                peak_hot_molar_by_species.get(name, 0.0), rate
            )
        hot_molar_rows.append(row_molar)
    peak_hot_by_species = {
        species: molar_rate_mol_s_to_mass_rate_kg_hr(species, rate)
        for species, rate in peak_hot_molar_by_species.items()
    }
    hot_molar_rates: dict[str, float] = {}
    excluded: dict[str, dict[str, Any]] = {}
    excluded_molar_rates: dict[str, float] = {}
    excluded_has_latent: dict[str, bool] = {}
    crossings: dict[str, float] = {}
    crossing_authority: dict[str, dict[str, Any]] = {}
    for species, reason in sorted(entry_refusals.items()):
        # Invalid entries cannot enter mol-native aggregation.  Preserve their
        # recorded peak only as a fail-closed report projection.
        unconverted_peak_kg_hr = max(
            (
                _finite_nonnegative(row[species], f"recorded {species} kg/hr")
                for row in hot_species_kg_hr_series
                if species in row
            ),
            default=0.0,
        )
        peak_hot_by_species[species] = unconverted_peak_kg_hr
        excluded[species] = {
            "peak_kg_hr": unconverted_peak_kg_hr,
            "reason": reason,
            "heat_load_W": None,
            "heat_load_status": "unavailable_invalid_entry_rate",
            "exclusion_class": "major_heat_carrier_report_incomplete",
        }
    for species, molar_rate in sorted(peak_hot_molar_by_species.items()):
        if molar_rate <= 0.0:
            continue
        peak_rate = peak_hot_by_species[species]
        try:
            vapor_cp_j_per_mol_k(species, max(HOT_RADIATOR_SPLIT_K, 1200.0))
        except (KeyError, TypeError, ValueError) as exc:
            excluded[species] = {
                "peak_kg_hr": peak_rate,
                "reason": exc.args[0] if isinstance(exc, KeyError) else str(exc),
                "heat_load_W": None,
                "heat_load_status": "unavailable",
                "exclusion_class": (
                    "known_refractory_trace_enthalpy_gap"
                    if species in trace_authority
                    else "major_heat_carrier_report_incomplete"
                ),
            }
            continue
        try:
            latent_vaporization_kj_per_mol(species)
        except KeyError as exc:
            excluded[species] = {
                "peak_kg_hr": peak_rate,
                "reason": exc.args[0],
                "heat_load_W": None,
                "heat_load_status": "lower_bound_missing_latent",
                "exclusion_class": (
                    "known_refractory_trace_enthalpy_gap"
                    if species in trace_authority
                    else "major_heat_carrier_report_incomplete"
                ),
            }
            excluded_molar_rates[species] = molar_rate
            excluded_has_latent[species] = False
            continue
        window = windows.get(species)
        try:
            authority = authoritative_condensation_temperature(species, setpoints=setpoints)
            crossing = float(authority["temperature_C"]) + KELVIN_OFFSET
        except (KeyError, TypeError, ValueError) as exc:
            excluded[species] = {
                "peak_kg_hr": peak_rate,
                "reason": str(exc),
                "heat_load_W": None,
                "heat_load_status": "missing_authoritative_condensation_temperature",
                "exclusion_class": "major_heat_carrier_report_incomplete",
            }
            excluded_molar_rates[species] = molar_rate
            excluded_has_latent[species] = True
            continue
        if window is None or not window[0] <= crossing <= window[1]:
            excluded[species] = {
                "peak_kg_hr": peak_rate,
                "reason": "authoritative_condensation_temperature_outside_stage_window",
                "heat_load_W": None,
                "heat_load_status": "conservative_full_path",
                "exclusion_class": "major_heat_carrier_report_incomplete",
                "chosen_temperature_K": crossing,
                "stage_window_K": list(window) if window is not None else None,
            }
            excluded_molar_rates[species] = molar_rate
            excluded_has_latent[species] = True
            continue
        hot_molar_rates[species] = molar_rate
        crossings[species] = crossing
        crossing_authority[species] = {
            "temperature_K": crossing,
            "source": authority["source"],
            "authority": authority["authority"],
            "stage_window_K": list(window),
        }

    inlet_temperature = max(
        [float(temperature) for temperature in temperature_K_series if math.isfinite(float(temperature))]
        or [HOT_RADIATOR_SPLIT_K]
    )
    inlet_temperature = float(inlet_temperature)
    mid_inlet_temperature = min(inlet_temperature, HOT_RADIATOR_SPLIT_K)
    assignments = {
        species: thermal_train_section_ownership(
            crossing,
            inlet_temperature,
            params.T_floor_K,
        )
        for species, crossing in crossings.items()
    }
    hot_crossings = {
        species: crossings[species]
        for species, assignment in assignments.items()
        if assignment["latent_owner"] == "hot"
    }
    mid_crossings = {
        species: crossings[species]
        for species, assignment in assignments.items()
        if assignment["latent_owner"] == "mid"
    }
    hot_species = {
        species: hot_molar_rates[species]
        for species, assignment in assignments.items()
        if assignment["hot_sensible_interval_K"] is not None
        or assignment["latent_owner"] == "hot"
    }
    mid_species = {
        species: hot_molar_rates[species]
        for species, assignment in assignments.items()
        if assignment["mid_sensible_interval_K"] is not None
        or assignment["latent_owner"] == "mid"
    }
    section_routed_exclusions: set[str] = set()
    for species, assignment in assignments.items():
        reason = assignment["exclusion_reason"]
        if reason is None:
            continue
        excluded[species] = {
            "peak_kg_hr": peak_hot_by_species[species],
            "reason": reason,
            "heat_load_W": None,
            "heat_load_status": "latent_outside_train_temperature_path",
            "exclusion_class": "major_heat_carrier_report_incomplete",
            "chosen_temperature_K": crossings[species],
        }
        excluded_molar_rates[species] = hot_molar_rates[species]
        excluded_has_latent[species] = True
        section_routed_exclusions.add(species)

    for species, rate in excluded_molar_rates.items():
        sensible = 0.0 if species in section_routed_exclusions else (
            rate * integrate_molar_sensible_enthalpy_j_per_mol(
                species,
                params.T_floor_K,
                inlet_temperature,
                segment_K=params.dT_segment_K,
            )
        )
        latent = (
            rate * latent_vaporization_kj_per_mol(species) * 1000.0
            if excluded_has_latent[species]
            else 0.0
        )
        excluded[species]["heat_load_W"] = sensible + latent

    oxygen_molar_rates = [
        _finite_nonnegative(value, "melt-offgas O2 mol/hr") / SECONDS_PER_HOUR
        for value in oxygen_mol_hr_series
    ]
    peak_o2_mol_s = max(oxygen_molar_rates, default=0.0)
    peak_o2_mol_hr = peak_o2_mol_s * SECONDS_PER_HOUR
    peak_o2_kg_hr = molar_rate_mol_s_to_mass_rate_kg_hr("O2", peak_o2_mol_s)
    # Melt-offgas O2 co-flows through S-A before the separator, then enters S-B
    # at 1000 K.  Adding it only as sensible load avoids inventing a hot-stage
    # condensation crossing or charging O2 latent heat in S-A.
    hot_section_species = dict(hot_species)
    if peak_o2_mol_s > 0.0 and inlet_temperature > HOT_RADIATOR_SPLIT_K:
        hot_section_species["O2"] = hot_section_species.get("O2", 0.0) + peak_o2_mol_s
    hot_section = segmented_radiator_area_m2(
        hot_section_species,
        temperature_in_K=inlet_temperature,
        temperature_out_K=HOT_RADIATOR_SPLIT_K,
        sink_temperature_K=params.T_sink_night_K,
        emissivity=params.emissivity,
        segment_K=params.dT_segment_K,
        latent_crossings_K=hot_crossings,
    ) if hot_section_species and inlet_temperature >= HOT_RADIATOR_SPLIT_K else _zero_radiator_section()
    mid_section = segmented_radiator_area_m2(
        mid_species,
        temperature_in_K=mid_inlet_temperature,
        temperature_out_K=params.T_floor_K,
        sink_temperature_K=params.T_sink_night_K,
        emissivity=params.emissivity,
        segment_K=params.dT_segment_K,
        latent_crossings_K=mid_crossings,
    ) if mid_species and mid_inlet_temperature >= params.T_floor_K else _zero_radiator_section()
    hot_section["sizing_basis"] = "per_species_maxima_conservative"
    mid_section["sizing_basis"] = "per_species_maxima_conservative"

    # S-B begins after the hot separator.  Melt-to-split enthalpy is upstream
    # and is charged once to S-A, not again to the passive O2 row.
    o2_inlet_K = max(HOT_RADIATOR_SPLIT_K, params.T_floor_K)
    o2_night = segmented_radiator_area_m2(
        {"O2": peak_o2_mol_s},
        temperature_in_K=o2_inlet_K,
        temperature_out_K=params.T_floor_K,
        sink_temperature_K=params.T_sink_night_K,
        emissivity=params.emissivity,
        segment_K=params.dT_segment_K,
        sink_margin_K=params.dT_segment_K,
    ) if peak_o2_mol_s > 0.0 and o2_inlet_K > params.T_floor_K else _zero_radiator_section()
    o2_day = segmented_radiator_area_m2(
        {"O2": peak_o2_mol_s},
        temperature_in_K=o2_inlet_K,
        temperature_out_K=params.T_floor_K,
        sink_temperature_K=params.T_sink_day_K,
        emissivity=params.emissivity,
        segment_K=params.dT_segment_K,
        sink_margin_K=params.dT_segment_K,
    ) if peak_o2_mol_s > 0.0 and o2_inlet_K > params.T_floor_K else {
        **_zero_radiator_section(),
        "status": "passive_refused",
        "reason": "target_not_above_effective_sink_margin",
    }
    o2_night["inlet_temperature_K"] = o2_inlet_K
    o2_night["inlet_basis"] = "post_separator_S_B"
    o2_day["inlet_temperature_K"] = o2_inlet_K
    o2_day["inlet_basis"] = "post_separator_S_B"
    capacity_result = capacity_from_hardware(params.cold_train)
    if isinstance(capacity_result, FiniteCapacity) and params.cold_train is not None:
        capacity_kg_hr = capacity_result.value_kg_hr
        cold_end = claude_cycle_cold_end(
            capacity_kg_hr,
            liquid_yield=params.cold_train.liquid_yield,
            expander_bypass_fraction=params.cold_train.expander_bypass_fraction,
            pressure_low_Pa=params.P_suction_Pa,
            pressure_high_Pa=params.P_discharge_Pa,
            inlet_temperature_K=params.cold_train.orifice["upstream_temperature_K"],
            reject_temperature_K=params.T_reject_K,
            eta_isen=params.eta_isen,
            makeup_pressure_Pa=params.cold_train.orifice["upstream_pressure_Pa"],
        )
        orifice_diameter_m = orifice_diameter_for_C(
            capacity_kg_hr,
            params.cold_train.orifice["discharge_coefficient"],
            params.cold_train.orifice["upstream_pressure_Pa"],
            params.cold_train.orifice["upstream_temperature_K"],
            back_pressure_Pa=params.cold_train.orifice["back_pressure_Pa"],
        )
        cold_end["metering_orifice"] = {
            "diameter_m": orifice_diameter_m,
            "capacity_authority": "derived_from_C_never_source_of_C",
            **dict(params.cold_train.orifice),
        }
        cold_end["relief"] = {
            **dict(params.cold_train.relief),
            "law": "mass_flow_kg_hr=k_relief_kg_hr_Pa*max(P_Pa-p_open_Pa,0)",
        }
        cold_end["sizing_path"] = "night_path_only"
    else:
        capacity_kg_hr = 0.0
        cold_end = {
            "status": "not_configured",
            "reason": capacity_result.reason,
            "refrigeration_work_W": 0.0,
            "energy": {"reject_load_W": 0.0, "net_work_W": 0.0},
        }
    compression = {
        "model": "claude_cycle_circulation_compressor",
        "compressor_shaft_W": cold_end.get("energy", {}).get("compressor_work_W", 0.0),
        "intercooler_reject_W": 0.0,
    }
    cryo = {
        **cold_end,
        "cold_load_W": cold_end.get("energy", {}).get("cold_load_W", 0.0),
        "reject_load_W": cold_end.get("energy", {}).get("reject_load_W", 0.0),
    }
    reject_radiator = _isothermal_radiator(
        cryo["reject_load_W"], params.T_reject_K, params.T_sink_night_K, params.emissivity
    )
    # One HourSnapshot represents one elapsed hour, so summing mol/hr rows
    # gives batch mol and snapshot_count is the report's run-hours basis.
    batch_o2_mol = sum(oxygen_molar_rates) * SECONDS_PER_HOUR
    deposition_gate = oxygen_deposition_gate(params.P_discharge_Pa, params.T_frost_K)
    if deposition_gate["frost_forms"]:
        captured_batch_mol = min(
            batch_o2_mol * params.frost_sticking_fraction,
            params.cavern_capacity_kg / OXYGEN_MOLAR_MASS_KG_PER_MOL,
        )
        cavern = cavern_regeneration_energy_J(
            captured_batch_mol,
            storage_temperature_K=params.T_storage_K,
            cavern_thermal_mass_J_per_K=params.cavern_thermal_mass_J_per_K,
            segment_K=params.dT_segment_K,
        )
        capture_status = {"status": "captured", "reason": None}
    else:
        captured_batch_mol = 0.0
        cavern = {
            "status": "not_invoked",
            "reason": "deposition_gate_not_met",
            "oxygen_sensible_J": 0.0,
            "oxygen_fusion_J": 0.0,
            "cavern_walls_J": 0.0,
            "total_J": 0.0,
            "integration_segment_K": params.dT_segment_K,
        }
        capture_status = {"status": "refused", "reason": "deposition_gate_not_met"}
    captured_batch_kg = captured_batch_mol * OXYGEN_MOLAR_MASS_KG_PER_MOL
    overflow = thermal_train_overflow_kg_hr(peak_o2_kg_hr, capacity_kg_hr)
    # Equal-stage intercooling rejects over the T_floor-to-T_reject band;
    # midpoint sizing avoids pretending the whole surface radiates at 300 K.
    intercooler_radiator_temperature_K = (params.T_floor_K + params.T_reject_K) / 2.0
    intercooler_radiator = _isothermal_radiator(
        compression["intercooler_reject_W"],
        intercooler_radiator_temperature_K,
        params.T_sink_night_K,
        params.emissivity,
    )
    intercooler_radiator["sizing_temperature_K"] = intercooler_radiator_temperature_K
    display_costs = _display_cost_report(
        params,
        hot_section=hot_section,
        mid_section=mid_section,
        o2_radiator=o2_night,
        intercooler_radiator=intercooler_radiator,
        reject_radiator=reject_radiator,
        compression=compression,
        cryo=cryo,
        captured_batch_kg=captured_batch_kg,
        run_hours=snapshot_count,
    )
    partial_pressures = _peak_partial_pressures_pa(overhead_state_series)
    condensation_crossings: dict[str, dict[str, Any]] = {}
    for species, authority in sorted(crossing_authority.items()):
        condensation_crossings[species] = {
            **authority,
            "antoine_dew_diagnostic": antoine_dew_temperature_diagnostic(
                species,
                partial_pressures.get(species, 0.0),
            ),
        }
    closes = not excluded
    known_trace = sorted(
        species
        for species, row in excluded.items()
        if row.get("exclusion_class") == "known_refractory_trace_enthalpy_gap"
    )
    major_excluded = sorted(set(excluded) - set(known_trace))
    closure_status = (
        "closed"
        if closes
        else (
            "excluded_major_heat_carrier_report_incomplete"
            if major_excluded
            else "excluded_refractory_trace_expected"
        )
    )
    return copy.deepcopy({
        "schema_version": THERMAL_TRAIN_REPORT_SCHEMA_VERSION,
        "status": "closed" if closes else "incomplete",
        "train_closes_for_run": closes,
        "closure_status": closure_status,
        "excluded_refractory_trace_species": known_trace,
        "excluded_major_heat_carrier_species": major_excluded,
        "snapshot_count": snapshot_count,
        "run_hours": snapshot_count,
        "run_hours_basis": "one_hour_per_snapshot",
        "peaks": {
            # Project each mol-native concurrent row only at the report edge.
            # Unmapped species are excluded fail-closed, never retained as a
            # parallel kg/hr aggregation path.
            "hot_total_vapor_kg_hr": max(
                (
                    sum(
                        molar_rate_mol_s_to_mass_rate_kg_hr(species, rate)
                        for species, rate in row.items()
                    )
                    for row in hot_molar_rows
                ),
                default=0.0,
            ),
            "hot_total_vapor_basis": "maximum_concurrent_snapshot_total",
            "hot_total_vapor_projection_status": (
                "partial_unconverted_species_excluded"
                if entry_refusals
                else "complete"
            ),
            "hot_species_kg_hr": dict(sorted(peak_hot_by_species.items())),
            "hot_species_basis": "per_species_design_maxima_not_concurrent_sum",
            "cold_o2_mol_hr": peak_o2_mol_hr,
            "cold_o2_kg_hr": peak_o2_kg_hr,
        },
        "condensation_crossings": condensation_crossings,
        "sections": {
            "hot_radiator": hot_section,
            "mid_radiator": mid_section,
            "o2_passive_radiator_night": o2_night,
            "o2_passive_radiator_day": o2_day,
            "compressor": compression,
            "intercooler_radiator": intercooler_radiator,
            "cryo_tail": cryo,
            "cold_end_cycle": cold_end,
            "reject_radiator": reject_radiator,
            "cavern_regeneration": cavern,
        },
        "capacity": {
            "basis": "hardware_rating_minimum",
            "rated_cold_train_kg_hr": capacity_kg_hr,
            "rating_reference": {
                "p_ref_Pa": capacity_result.p_ref_Pa
                if isinstance(capacity_result, FiniteCapacity)
                else None,
                "T_ref_K": capacity_result.T_ref_K
                if isinstance(capacity_result, FiniteCapacity)
                else None,
                "authority": "diagnostic_scalar_rating_reference_not_capacity_source",
            },
            "thermal_train_overflow_kg_hr": overflow,
            "cavern_capacity_kg": params.cavern_capacity_kg,
            "captured_batch_kg": captured_batch_kg,
            "capture_shortfall_kg": max(
                0.0,
                batch_o2_mol * OXYGEN_MOLAR_MASS_KG_PER_MOL - captured_batch_kg,
            ),
            "frost_sticking_fraction": params.frost_sticking_fraction,
            "deposition_gate": deposition_gate,
            "capture_status": capture_status,
        },
        "excluded_species": dict(sorted(excluded.items())),
        "excluded_species_nonzero": bool(excluded),
        "observed_upstream_state": {
            "transport_saturation_peak_pct": max(observed_transport_saturation_pct, default=0.0),
            "O2_vented_peak_kg_hr": max(observed_o2_vented_kg_hr, default=0.0),
            "note": "observed legacy upstream diagnostics; not thermal-train overflow",
        },
        "display_costs": display_costs,
        "knudsen_anchors": _knudsen_report(overhead_state_series, params),
        "footnotes": [
            "Condensed-film sensible heat below each crossing is neglected relative to latent heat.",
            "Cold-end work uses the seven-node Claude cycle; dry-expander shaft credit is explicit.",
            "Cryogenic sizing is the night-path-only case; day-operation strategy remains deferred.",
            "Melt-offgas O2 sensible duty is charged through S-A to 1000 K, then enters S-B at that post-separator datum.",
            "SiO/CrO2 condenser enthalpy is not sourced in Phase 1a; exclusions are classified without reusing melt-side reaction enthalpy.",
            "Cold-end capacity C is the minimum of compressor mass-flow and refrigeration freeze-rate ratings; the orifice is derived from C.",
            "All kg and USD values are report-edge projections; internal enthalpy arithmetic is mol/s and W.",
            "Hot and mid radiator sections conservatively sum per-species design maxima; the displayed hot peak is a concurrent snapshot maximum.",
        ],
    })


def empty_thermal_train_report(reason: str) -> dict[str, Any]:
    return {
        "schema_version": THERMAL_TRAIN_REPORT_SCHEMA_VERSION,
        "status": "no_data",
        "reason": str(reason),
        "train_closes_for_run": False,
        "snapshot_count": 0,
        "excluded_species": {},
        "excluded_species_nonzero": False,
    }


def _zero_radiator_section() -> dict[str, Any]:
    return {
        "status": "sized",
        "area_m2": 0.0,
        "sensible_area_m2": 0.0,
        "latent_area_m2": 0.0,
        "sensible_load_W": 0.0,
        "latent_load_W": 0.0,
        "active_lift_W": 0.0,
        "latent_crossings": {},
    }


def _isothermal_radiator(load_W: float, temperature_K: float, sink_K: float, emissivity: float) -> dict[str, float]:
    load = _finite_nonnegative(load_W, "load_W")
    flux = _fraction(emissivity, "emissivity") * STEFAN_BOLTZMANN * (temperature_K ** 4 - sink_K ** 4)
    if flux <= 0.0:
        raise ValueError("reject radiator temperature must exceed sink temperature")
    return {"load_W": load, "area_m2": load / flux}


def _display_cost_report(
    params: ThermalTrainParameters,
    *,
    hot_section: Mapping[str, Any],
    mid_section: Mapping[str, Any],
    o2_radiator: Mapping[str, Any],
    intercooler_radiator: Mapping[str, Any],
    reject_radiator: Mapping[str, Any],
    compression: Mapping[str, Any],
    cryo: Mapping[str, Any],
    captured_batch_kg: float,
    run_hours: int,
) -> dict[str, Any]:
    prices = params.display_prices
    if prices is None:
        return {"status": "unavailable"}
    radiator_area = sum(
        float(section.get("area_m2") or 0.0)
        for section in (
            hot_section,
            mid_section,
            o2_radiator,
            intercooler_radiator,
            reject_radiator,
        )
    )
    radiator_usd = radiator_area * prices.radiator_cost_per_m2
    compressor_usd = float(compression["compressor_shaft_W"]) / 1000.0 * prices.compressor_cost_per_kW
    cryo_usd = float(cryo["cold_load_W"]) * prices.cryo_cost_per_W
    installed = radiator_usd + compressor_usd + cryo_usd
    return {
        "status": "display_only_not_optimizer_objective",
        "source_tag": "owner-ratified-2026-07-13",
        "radiator_installed_usd": radiator_usd,
        "compressor_installed_usd": compressor_usd,
        "cryo_installed_usd": cryo_usd,
        "cryo_installed_basis": "cold_load_W_not_net_shaft_work",
        "total_installed_usd": installed,
        "amortized_per_campaign_usd": installed / prices.amortization_campaigns,
        "fixed_run_cost_usd": run_hours * prices.fixed_cost_per_hour,
        "captured_o2_display_value_usd": captured_batch_kg * prices.o2_value_per_kg,
        "storage_keeping_warm_overburden_usd": captured_batch_kg * prices.o2_storage_keeping_cost_per_kg["warm_overburden"],
        "storage_keeping_psr_usd": captured_batch_kg * prices.o2_storage_keeping_cost_per_kg["psr"],
    }


def _validated_value_entry(raw: Any, name: str, *, source_tag: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"{name} must be a mapping")
    _require_exact_keys(raw, {"value", "units", "source_tag"}, name)
    if raw.get("source_tag") != source_tag:
        raise ValueError(f"{name} source_tag must be {source_tag!r}")
    units = str(raw.get("units") or "")
    if not units:
        raise ValueError(f"{name} units are required")
    return {
        "value": _finite_nonnegative(raw.get("value"), name),
        "units": units,
        "source_tag": source_tag,
    }


def _validated_assumption_entry(
    raw: Any,
    name: str,
    *,
    allow_none: bool = False,
    enum: bool = False,
    boolean: bool = False,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"{name} must be a mapping")
    required = {"value", "units", "source_tag", "assumption_class", "range", "provenance"}
    _require_exact_keys(raw, required, name)
    if raw.get("source_tag") != "assumption":
        raise ValueError(f"{name} source_tag must be 'assumption'")
    if not str(raw.get("assumption_class") or ""):
        raise ValueError(f"{name} assumption_class is required")
    if not str(raw.get("provenance") or ""):
        raise ValueError(f"{name} provenance is required")
    if not str(raw.get("units") or ""):
        raise ValueError(f"{name} units are required")
    bounds = raw.get("range")
    if not isinstance(bounds, Sequence) or isinstance(bounds, (str, bytes)):
        raise ValueError(f"{name} range must be a sequence")
    if not enum and not boolean and len(bounds) != 2:
        raise ValueError(f"{name} range must contain exactly two bounds")
    if enum and not bounds:
        raise ValueError(f"{name} enum range may not be empty")
    value = raw.get("value")
    if boolean:
        if not isinstance(value, bool):
            raise TypeError(f"{name} must be a boolean")
        if list(bounds) != [False, True]:
            raise ValueError(f"{name} boolean range must be [false, true]")
    elif enum:
        if value not in bounds:
            raise ValueError(f"{name} value must be listed in its range")
    elif value is not None:
        number = _finite_nonnegative(value, name)
        low = _finite_nonnegative(bounds[0], f"{name}.range[0]")
        high = _finite_positive(bounds[1], f"{name}.range[1]")
        if not low <= number <= high:
            raise ValueError(f"{name} value must lie inside its declared range")
        value = number
    elif not allow_none:
        raise ValueError(f"{name} value may not be null")
    return {
        "value": value,
        "units": str(raw["units"]),
        "source_tag": "assumption",
        "assumption_class": str(raw["assumption_class"]),
        "range": list(bounds),
        "provenance": str(raw["provenance"]),
    }


def _required_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a mapping")
    return value


def _require_exact_keys(payload: Mapping[str, Any], expected: set[str] | frozenset[str], label: str) -> None:
    actual = {str(key) for key in payload}
    if actual != set(expected):
        raise ValueError(f"{label} keys must be exactly {sorted(expected)}; got {sorted(actual)}")


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


def _finite_positive(value: Any, name: str) -> float:
    number = _finite_nonnegative(value, name)
    if number <= 0.0:
        raise ValueError(f"{name} must be positive")
    return number


def _fraction(value: Any, name: str) -> float:
    number = _finite_nonnegative(value, name)
    if number > 1.0 or number == 0.0:
        raise ValueError(f"{name} must be in (0, 1]")
    return number


def _unit_interval(value: Any, name: str) -> float:
    number = _finite_nonnegative(value, name)
    if number > 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return number


def _positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be an integer") from exc
    if number <= 0 or float(value) != number:
        raise ValueError(f"{name} must be a positive integer")
    return number
