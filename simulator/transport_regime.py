"""Pinned VPR-P0a transport-regime formulas.

This module is intentionally isolated from the live condensation/evaporation
paths. It holds only reference transport formulas and fail-closed validity
guards for the vacuum-pyrolysis reproduction work.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


GAS_CONSTANT_J_MOL_K = 8.31446261815324
BOLTZMANN_CONSTANT_J_K = 1.380649e-23

VISCOUS_KNUDSEN_MAX = 0.01
FREE_MOLECULAR_KNUDSEN_MIN = 10.0
LONG_TUBE_L_OVER_D_MIN = 10.0

FORMULA_FREE_MOLECULAR_APERTURE = "free_molecular_aperture_conductance"
FORMULA_FREE_MOLECULAR_TUBE = "free_molecular_tube_clausing_conductance"
FORMULA_BESKOK_KARNIADAKIS_CIVAN = (
    "beskok_karniadakis_civan_transitional_conductance"
)
FORMULA_SINGLE_SPECIES_MFP = "single_species_hard_sphere_mean_free_path"
FORMULA_MIXTURE_MFP = "carrier_mixture_hard_sphere_mean_free_path"

COLLISION_DIAMETER_SOURCE = "Poling et al., Lennard-Jones sigma"

COLLISION_DIAMETERS_M: Mapping[str, float] = MappingProxyType(
    {
        "N2": 3.798e-10,
        "Ar": 3.542e-10,
        "O2": 3.467e-10,
        "CO": 3.690e-10,
        "CO2": 3.941e-10,
        "H2": 2.827e-10,
        "H2O": 2.641e-10,
    }
)

MOLAR_MASSES_KG_PER_MOL: Mapping[str, float] = MappingProxyType(
    {
        "N2": 0.0280134,
        "Ar": 0.039948,
        "O2": 0.031998,
        "CO": 0.0280101,
        "CO2": 0.0440095,
        "H2": 0.00201588,
        "H2O": 0.01801528,
    }
)


class KnudsenRegime(str, Enum):
    VISCOUS = "viscous"
    TRANSITIONAL = "transitional"
    FREE_MOLECULAR = "free_molecular"


class TransportRegimeRefusal(ValueError):
    """Named fail-closed refusal for out-of-validity transport inputs."""

    def __init__(self, category: str, detail: str | None = None) -> None:
        self.category = category
        self.reason = category
        message = category if detail is None else f"{category}: {detail}"
        super().__init__(message)


@dataclass(frozen=True)
class CarrierCollision:
    species: str
    mole_fraction: float
    collision_diameter_m: float
    molar_mass_kg_mol: float


@dataclass(frozen=True)
class MeanFreePathResult:
    lambda_m: float
    knudsen_number: float
    regime: KnudsenRegime
    formula_id: str
    test_species: str
    carriers: tuple[CarrierCollision, ...]
    collision_diameter_source: str


def _refuse(category: str, detail: str | None = None) -> None:
    raise TransportRegimeRefusal(category, detail)


def _require_positive(value: float, *, name: str, category: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0.0:
        _refuse(category, f"{name} must be finite and > 0")
    return value


def _require_nonnegative(value: float, *, name: str, category: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0.0:
        _refuse(category, f"{name} must be finite and >= 0")
    return value


def _require_collision_diameter(species: str) -> float:
    try:
        return COLLISION_DIAMETERS_M[species]
    except KeyError:
        _refuse(
            "uncertified_collision_diameter",
            f"no P0a collision diameter certified for {species!r}",
        )


def _require_molar_mass(species: str) -> float:
    try:
        return MOLAR_MASSES_KG_PER_MOL[species]
    except KeyError:
        _refuse(
            "uncertified_molar_mass",
            f"no P0a molar mass available for {species!r}",
        )


def _require_free_molecular_knudsen(
    knudsen_number: float, *, category: str
) -> float:
    knudsen_number = float(knudsen_number)
    if not math.isfinite(knudsen_number) or knudsen_number < 0.0:
        _refuse("invalid_knudsen_number", "Kn must be finite and >= 0")
    if knudsen_number < FREE_MOLECULAR_KNUDSEN_MIN:
        _refuse(
            category,
            f"Kn_D must be >= {FREE_MOLECULAR_KNUDSEN_MIN:g}",
        )
    return knudsen_number


def _require_transitional_knudsen(
    knudsen_number: float,
    *,
    allow_near_viscous_cross_check: bool = False,
) -> float:
    knudsen_number = float(knudsen_number)
    if not math.isfinite(knudsen_number) or knudsen_number <= 0.0:
        _refuse("invalid_knudsen_number", "Kn must be finite and > 0")
    lower_ok = knudsen_number >= VISCOUS_KNUDSEN_MAX
    near_viscous_ok = (
        allow_near_viscous_cross_check
        and 0.0 < knudsen_number < VISCOUS_KNUDSEN_MAX
    )
    if not (lower_ok or near_viscous_ok) or (
        knudsen_number >= FREE_MOLECULAR_KNUDSEN_MIN
    ):
        _refuse(
            "transitional_correlation_out_of_range",
            "BK/Civan correlation is strict over 0.01 <= Kn_D < 10; "
            "near-viscous values require explicit cross-check mode",
        )
    return knudsen_number


def classify_knudsen_regime(knudsen_number: float) -> KnudsenRegime:
    knudsen_number = float(knudsen_number)
    if math.isnan(knudsen_number) or knudsen_number < 0.0:
        _refuse("invalid_knudsen_number", "Kn must not be NaN or negative")
    if math.isinf(knudsen_number):
        return KnudsenRegime.FREE_MOLECULAR
    if knudsen_number < VISCOUS_KNUDSEN_MAX:
        return KnudsenRegime.VISCOUS
    if knudsen_number < FREE_MOLECULAR_KNUDSEN_MIN:
        return KnudsenRegime.TRANSITIONAL
    return KnudsenRegime.FREE_MOLECULAR


def mean_molecular_speed_m_s(
    temperature_K: float,
    molar_mass_kg_mol: float,
) -> float:
    temperature_K = _require_positive(
        temperature_K,
        name="temperature_K",
        category="invalid_temperature",
    )
    molar_mass_kg_mol = _require_positive(
        molar_mass_kg_mol,
        name="molar_mass_kg_mol",
        category="invalid_molar_mass",
    )
    return math.sqrt(
        8.0
        * GAS_CONSTANT_J_MOL_K
        * temperature_K
        / (math.pi * molar_mass_kg_mol)
    )


def molecular_aperture_conductance_m3_s(
    open_area_m2: float,
    temperature_K: float,
    molar_mass_kg_mol: float,
    *,
    knudsen_number: float,
) -> float:
    open_area_m2 = _require_positive(
        open_area_m2,
        name="open_area_m2",
        category="invalid_geometry",
    )
    _require_free_molecular_knudsen(
        knudsen_number,
        category="aperture_requires_free_molecular",
    )
    return 0.25 * open_area_m2 * mean_molecular_speed_m_s(
        temperature_K,
        molar_mass_kg_mol,
    )


def throughput_pa_m3_s(
    conductance_m3_s: float,
    pressure_delta_pa: float,
) -> float:
    conductance_m3_s = _require_nonnegative(
        conductance_m3_s,
        name="conductance_m3_s",
        category="invalid_conductance",
    )
    pressure_delta_pa = _require_nonnegative(
        pressure_delta_pa,
        name="pressure_delta_pa",
        category="invalid_pressure_delta",
    )
    return conductance_m3_s * pressure_delta_pa


def long_tube_clausing_transmission(
    diameter_m: float,
    length_m: float,
) -> float:
    diameter_m = _require_positive(
        diameter_m,
        name="diameter_m",
        category="invalid_geometry",
    )
    length_m = _require_positive(
        length_m,
        name="length_m",
        category="invalid_geometry",
    )
    length_over_diameter = length_m / diameter_m
    if length_over_diameter < LONG_TUBE_L_OVER_D_MIN:
        _refuse(
            "clausing_long_tube_asymptote_out_of_range",
            f"L/D must be >= {LONG_TUBE_L_OVER_D_MIN:g}",
        )
    return 4.0 * diameter_m / (3.0 * length_m)


def molecular_tube_conductance_m3_s(
    diameter_m: float,
    length_m: float,
    temperature_K: float,
    molar_mass_kg_mol: float,
    *,
    transmission_probability: float,
    knudsen_number: float,
) -> float:
    diameter_m = _require_positive(
        diameter_m,
        name="diameter_m",
        category="invalid_geometry",
    )
    _require_positive(
        length_m,
        name="length_m",
        category="invalid_geometry",
    )
    transmission_probability = _require_positive(
        transmission_probability,
        name="transmission_probability",
        category="invalid_transmission_probability",
    )
    if transmission_probability > 1.0:
        _refuse(
            "invalid_transmission_probability",
            "transmission_probability must be <= 1",
        )
    _require_free_molecular_knudsen(
        knudsen_number,
        category="tube_requires_free_molecular",
    )
    area_m2 = math.pi * diameter_m ** 2 / 4.0
    return (
        0.25
        * area_m2
        * mean_molecular_speed_m_s(temperature_K, molar_mass_kg_mol)
        * transmission_probability
    )


def long_tube_molecular_conductance_m3_s(
    diameter_m: float,
    length_m: float,
    temperature_K: float,
    molar_mass_kg_mol: float,
    *,
    knudsen_number: float,
) -> float:
    transmission_probability = long_tube_clausing_transmission(
        diameter_m,
        length_m,
    )
    return molecular_tube_conductance_m3_s(
        diameter_m,
        length_m,
        temperature_K,
        molar_mass_kg_mol,
        transmission_probability=transmission_probability,
        knudsen_number=knudsen_number,
    )


def dynamic_viscosity_sutherland_pa_s(
    temperature_K: float,
    *,
    eta0_pa_s: float = 17.81e-6,
    reference_temperature_K: float = 300.55,
    sutherland_temperature_K: float = 111.0,
) -> float:
    temperature_K = _require_positive(
        temperature_K,
        name="temperature_K",
        category="invalid_temperature",
    )
    eta0_pa_s = _require_positive(
        eta0_pa_s,
        name="eta0_pa_s",
        category="invalid_viscosity_model",
    )
    reference_temperature_K = _require_positive(
        reference_temperature_K,
        name="reference_temperature_K",
        category="invalid_viscosity_model",
    )
    sutherland_temperature_K = _require_positive(
        sutherland_temperature_K,
        name="sutherland_temperature_K",
        category="invalid_viscosity_model",
    )
    return (
        eta0_pa_s
        * (temperature_K / reference_temperature_K) ** 1.5
        * (reference_temperature_K + sutherland_temperature_K)
        / (temperature_K + sutherland_temperature_K)
    )


def beskok_karniadakis_civan_alpha(
    knudsen_number: float,
    *,
    allow_near_viscous_cross_check: bool = False,
) -> float:
    knudsen_number = _require_transitional_knudsen(
        knudsen_number,
        allow_near_viscous_cross_check=allow_near_viscous_cross_check,
    )
    return 1.358 / (1.0 + 0.170 * knudsen_number ** (-0.4348))


def beskok_karniadakis_rarefaction_factor(
    knudsen_number: float,
    *,
    allow_near_viscous_cross_check: bool = False,
) -> float:
    knudsen_number = _require_transitional_knudsen(
        knudsen_number,
        allow_near_viscous_cross_check=allow_near_viscous_cross_check,
    )
    alpha = 1.358 / (1.0 + 0.170 * knudsen_number ** (-0.4348))
    slip_coefficient_b = -1.0
    return (1.0 + alpha * knudsen_number) * (
        1.0 + 4.0 * knudsen_number / (1.0 - slip_coefficient_b * knudsen_number)
    )


def poiseuille_conductance_m3_s(
    diameter_m: float,
    length_m: float,
    mean_pressure_pa: float,
    dynamic_viscosity_pa_s: float,
) -> float:
    diameter_m = _require_positive(
        diameter_m,
        name="diameter_m",
        category="invalid_geometry",
    )
    length_m = _require_positive(
        length_m,
        name="length_m",
        category="invalid_geometry",
    )
    mean_pressure_pa = _require_positive(
        mean_pressure_pa,
        name="mean_pressure_pa",
        category="invalid_pressure",
    )
    dynamic_viscosity_pa_s = _require_positive(
        dynamic_viscosity_pa_s,
        name="dynamic_viscosity_pa_s",
        category="invalid_dynamic_viscosity",
    )
    radius_m = diameter_m / 2.0
    return (
        math.pi
        * radius_m ** 4
        * mean_pressure_pa
        / (8.0 * dynamic_viscosity_pa_s * length_m)
    )


def beskok_karniadakis_civan_conductance_m3_s(
    diameter_m: float,
    length_m: float,
    mean_pressure_pa: float,
    dynamic_viscosity_pa_s: float,
    *,
    knudsen_number: float,
    allow_near_viscous_cross_check: bool = False,
) -> float:
    return poiseuille_conductance_m3_s(
        diameter_m,
        length_m,
        mean_pressure_pa,
        dynamic_viscosity_pa_s,
    ) * beskok_karniadakis_rarefaction_factor(
        knudsen_number,
        allow_near_viscous_cross_check=allow_near_viscous_cross_check,
    )


def single_species_mean_free_path_m(
    pressure_pa: float,
    temperature_K: float,
    collision_diameter_m: float,
) -> float:
    pressure_pa = _require_positive(
        pressure_pa,
        name="pressure_pa",
        category="invalid_pressure",
    )
    temperature_K = _require_positive(
        temperature_K,
        name="temperature_K",
        category="invalid_temperature",
    )
    collision_diameter_m = _require_positive(
        collision_diameter_m,
        name="collision_diameter_m",
        category="invalid_collision_diameter",
    )
    denominator = (
        math.sqrt(2.0)
        * math.pi
        * collision_diameter_m ** 2
        * pressure_pa
    )
    return BOLTZMANN_CONSTANT_J_K * temperature_K / denominator


def single_species_mean_free_path(
    carrier_species: str,
    pressure_pa: float,
    temperature_K: float,
    characteristic_length_m: float,
) -> MeanFreePathResult:
    characteristic_length_m = _require_positive(
        characteristic_length_m,
        name="characteristic_length_m",
        category="invalid_characteristic_length",
    )
    sigma_m = _require_collision_diameter(carrier_species)
    molar_mass_kg_mol = _require_molar_mass(carrier_species)
    lambda_m = single_species_mean_free_path_m(
        pressure_pa,
        temperature_K,
        sigma_m,
    )
    knudsen_number = lambda_m / characteristic_length_m
    return MeanFreePathResult(
        lambda_m=lambda_m,
        knudsen_number=knudsen_number,
        regime=classify_knudsen_regime(knudsen_number),
        formula_id=FORMULA_SINGLE_SPECIES_MFP,
        test_species=carrier_species,
        carriers=(
            CarrierCollision(
                species=carrier_species,
                mole_fraction=1.0,
                collision_diameter_m=sigma_m,
                molar_mass_kg_mol=molar_mass_kg_mol,
            ),
        ),
        collision_diameter_source=COLLISION_DIAMETER_SOURCE,
    )


def mixture_mean_free_path_m(
    test_species: str,
    carrier_mole_fractions: Mapping[str, float],
    pressure_pa: float,
    temperature_K: float,
) -> float:
    if not carrier_mole_fractions:
        _refuse(
            "missing_carrier_state",
            "carrier mole fractions are required",
        )
    pressure_pa = _require_positive(
        pressure_pa,
        name="pressure_pa",
        category="invalid_pressure",
    )
    temperature_K = _require_positive(
        temperature_K,
        name="temperature_K",
        category="invalid_temperature",
    )
    sigma_i = _require_collision_diameter(test_species)
    molar_mass_i = _require_molar_mass(test_species)
    fraction_sum = 0.0
    denominator_sum = 0.0
    for carrier_species, raw_fraction in carrier_mole_fractions.items():
        mole_fraction = float(raw_fraction)
        if not math.isfinite(mole_fraction) or mole_fraction <= 0.0:
            _refuse(
                "invalid_carrier_mole_fraction",
                f"{carrier_species!r} mole fraction must be finite and > 0",
            )
        sigma_j = _require_collision_diameter(carrier_species)
        molar_mass_j = _require_molar_mass(carrier_species)
        sigma_ij = (sigma_i + sigma_j) / 2.0
        denominator_sum += (
            mole_fraction
            * math.pi
            * sigma_ij ** 2
            * math.sqrt(1.0 + molar_mass_i / molar_mass_j)
        )
        fraction_sum += mole_fraction
    if not math.isclose(fraction_sum, 1.0, rel_tol=0.0, abs_tol=1e-9):
        _refuse(
            "carrier_mole_fractions_not_normalized",
            "carrier mole fractions must sum to 1.0",
        )
    return BOLTZMANN_CONSTANT_J_K * temperature_K / (
        pressure_pa * denominator_sum
    )


def carrier_mixture_mean_free_path(
    test_species: str,
    carrier_mole_fractions: Mapping[str, float],
    pressure_pa: float,
    temperature_K: float,
    characteristic_length_m: float,
) -> MeanFreePathResult:
    characteristic_length_m = _require_positive(
        characteristic_length_m,
        name="characteristic_length_m",
        category="invalid_characteristic_length",
    )
    lambda_m = mixture_mean_free_path_m(
        test_species,
        carrier_mole_fractions,
        pressure_pa,
        temperature_K,
    )
    carriers = tuple(
        CarrierCollision(
            species=species,
            mole_fraction=float(fraction),
            collision_diameter_m=_require_collision_diameter(species),
            molar_mass_kg_mol=_require_molar_mass(species),
        )
        for species, fraction in carrier_mole_fractions.items()
    )
    knudsen_number = lambda_m / characteristic_length_m
    return MeanFreePathResult(
        lambda_m=lambda_m,
        knudsen_number=knudsen_number,
        regime=classify_knudsen_regime(knudsen_number),
        formula_id=FORMULA_MIXTURE_MFP,
        test_species=test_species,
        carriers=carriers,
        collision_diameter_source=COLLISION_DIAMETER_SOURCE,
    )
