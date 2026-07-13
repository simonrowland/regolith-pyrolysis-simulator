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


THERMAL_TRAIN_SCHEMA_VERSION = "thermal-train-v1"
THERMAL_TRAIN_REPORT_SCHEMA_VERSION = "thermal-train-report-v1"
DEFAULT_THERMAL_TRAIN_PARAMETERS_PATH = DEFAULT_DATA_DIR / "thermal_train_params.yaml"

SECONDS_PER_HOUR = 3600.0
KELVIN_OFFSET = CELSIUS_TO_KELVIN_OFFSET
HOT_RADIATOR_SPLIT_K = 1000.0
OXYGEN_MOLAR_MASS_KG_PER_MOL = resolve_species_formula("O2").molar_mass_kg_per_mol()
# NBSIR 77-859 gaseous O2 property tables near the 150 K compressor inlet;
# the design holds this first-order constant across the equal-stage ladder.
OXYGEN_GAMMA = 1.395

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
    _require_exact_keys(payload, {"schema_version", "parameters", "display_prices"}, "thermal-train root")
    if payload.get("schema_version") != THERMAL_TRAIN_SCHEMA_VERSION:
        raise ValueError(f"thermal-train schema_version must be {THERMAL_TRAIN_SCHEMA_VERSION!r}")
    raw_parameters = _required_mapping(payload, "parameters")
    raw_prices = _required_mapping(payload, "display_prices")
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
        "display_prices": prices,
        "provenance": {"source": source},
    }


def thermal_train_parameters_from_mapping(
    payload: Mapping[str, Any] | None = None,
) -> ThermalTrainParameters:
    clean_payload = (
        {key: payload[key] for key in ("schema_version", "parameters", "display_prices")}
        if payload is not None and "provenance" in payload
        else payload
    )
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
        display_prices=prices,
    )


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
    rated_cold_train_kg_hr: float | None = None,
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
    compression = intercooled_compression(
        peak_o2_mol_s,
        pressure_suction_Pa=params.P_suction_Pa,
        pressure_discharge_Pa=params.P_discharge_Pa,
        stages=params.n_compressor_stages,
        inlet_temperature_K=params.T_floor_K,
        eta_isen=params.eta_isen,
    )
    cryo = cryogenic_tail(
        peak_o2_mol_s,
        temperature_floor_K=params.T_floor_K,
        temperature_frost_K=params.T_frost_K,
        temperature_reject_K=params.T_reject_K,
        eta_2ndlaw=params.eta_2ndlaw,
        segment_K=params.dT_segment_K,
    )
    cryo["sizing_path"] = "night_path_only"
    reject_radiator = _isothermal_radiator(cryo["reject_load_W"], params.T_reject_K, params.T_sink_night_K, params.emissivity)
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
    capacity_kg_hr = (
        peak_o2_kg_hr
        if rated_cold_train_kg_hr is None
        else _finite_nonnegative(rated_cold_train_kg_hr, "rated_cold_train_kg_hr")
    )
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
            "reject_radiator": reject_radiator,
            "cavern_regeneration": cavern,
        },
        "capacity": {
            "basis": (
                "observed_peak_design_capacity"
                if rated_cold_train_kg_hr is None
                else "declared_rated_capacity"
            ),
            "rated_cold_train_kg_hr": capacity_kg_hr,
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
            "Cryogenic work is an upper-bound generic refrigerator surrogate with no direct-O2 turbine credit.",
            "Cryogenic sizing is the night-path-only case; day-operation strategy remains deferred.",
            "Melt-offgas O2 sensible duty is charged through S-A to 1000 K, then enters S-B at that post-separator datum.",
            "SiO/CrO2 condenser enthalpy is not sourced in Phase 1a; exclusions are classified without reusing melt-side reaction enthalpy.",
            "Default capacity equals the observed peak by design, so overflow is zero unless a declared rated capacity is supplied.",
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
    cryo_usd = float(cryo["refrigeration_work_W"]) * prices.cryo_cost_per_W
    installed = radiator_usd + compressor_usd + cryo_usd
    return {
        "status": "display_only_not_optimizer_objective",
        "source_tag": "owner-ratified-2026-07-13",
        "radiator_installed_usd": radiator_usd,
        "compressor_installed_usd": compressor_usd,
        "cryo_installed_usd": cryo_usd,
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
