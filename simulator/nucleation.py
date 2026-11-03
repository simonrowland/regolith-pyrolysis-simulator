"""Classical Becker-Doring homogeneous nucleation rate. One owner.

The rate is the classical nucleation theory expression for the number of
critical clusters formed per volume per time. It is a flag input for
condenser stages. It is not an aerosol capture efficiency.

Premise. A supersaturated vapour (saturation ratio S = p / P_sat(T) > 1)
has a critical droplet whose formation free energy is the CNT barrier.
Below that size the cluster is unstable. Becker and Doring give the
steady nucleation rate from the monomer collision rate onto that cluster.

Algebra, with the ideal-gas monomer density p/(kT):

    J = (p/kT)^2 * v_m * sqrt(2*sigma / (pi*m)) * exp(-dG*/kT)
    dG* = 16*pi*sigma^3*v_m^2 / (3 * (kT*ln S)^2)
    n* = 32*pi*sigma^3*v_m^2 / (3 * (kT*ln S)^3) = 2*dG* / (kT*ln S)

v_m = m/rho is the molecular volume in the liquid. m = M/N_A is the
molecular mass. sigma is the liquid-vapour surface tension.

Unit check. p/kT is m^-3, so (p/kT)^2 is m^-6. v_m is m^3. sigma is
N/m = kg/s^2 and m is kg, so sigma/m is s^-2 and the square root is
s^-1. The product is m^-3 s^-1. Fink's sigma is published in mN/m, so
the owner multiplies by 1e-3 before it enters this expression. dG*/kT
and n* are dimensionless.

Limiting cases. S <= 1 has no critical nucleus in this derivation, so
J = 0 (evaluated, not unevaluable). As S approaches 1 from above, ln S
approaches 0, dG* diverges, and J approaches 0. As S grows, the barrier
falls and J approaches the kinetic prefactor. A partial pressure of 0
is an empty vapour and J = 0. An exponent below -700 underflows the
real exponential; that rate is 0, not an error.

Sanity, not a fit. The same function at the cited water point (293.15 K,
the classical row quoted as 293 K) crosses J = 1 cm^-3 s^-1 = 1e6
m^-3 s^-1 near S = 3.3. Sodium at 523 K, with Fink liquid rho and sigma
and the Browning-Potter saturation pressure, crosses that rate near
S = 28. A worked density check at 523 K: tau = 1 - 523/2503.7 =
0.791108, tau^0.5 = 0.889443, rho = 219 + 275.32*tau + 511.58*tau^0.5
= 891.82 kg/m3.

Missing liquid density or surface tension is unevaluable and returns no
rate. The Na saturation pressure is the Browning-Potter function in
engines/builtin/sodium_saturation.py, not a second copy of that
equation. material_densities.yaml is a different owner (Assael linear
densities for stratification) and is not read here. Below the sodium
melting point the liquid correlation is still evaluated and flagged;
the cold-trap comparison needs that number, and the capillary picture
is already outside its domain when n* is of order one.

The stage flag fires when an evaluated J exceeds FOG_RATE_THRESHOLD_M3_S.
Equality does not fire.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from simulator.accounting.exceptions import UnknownSpeciesError
from simulator.accounting.formulas import parse_formula
from simulator.physical_constants import AVOGADRO, BOLTZMANN
from simulator.yaml_cache import load_cached_safe_yaml

_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "nucleation_liquid.yaml"

# 1 cm^-3 s^-1. The stage flag uses a strict greater-than against this.
FOG_RATE_THRESHOLD_M3_S = 1.0e6
NOTICE_FOG = "stage_vapour_fog_molecular_capture_not_valid"
NOTICE_UNEVALUABLE = "stage_fog_check_unevaluable"
ETA_ROLE_UPPER_BOUND = "upper_bound_not_valid_as_capture"

_UNDERFLOW_EXPONENT = -700.0
_CAPILLARY_CLUSTER_NOTICE = 10.0


class NucleationInputError(ValueError):
    """Temperature or partial pressure is not a usable number.

    A missing density, surface tension, or saturation pressure is an
    unevaluable result, not this error.
    """


@dataclass(frozen=True)
class NucleationRate:
    """One evaluation. ``rate_m3_s`` is None exactly when status is unevaluable."""

    status: str
    species: str
    temperature_K: float
    partial_pressure_Pa: float | None
    rate_m3_s: float | None
    saturation_pressure_Pa: float | None
    saturation_ratio: float | None
    saturation_authority: str | None
    critical_cluster_molecules: float | None
    reason: str | None
    property_notice: str | None
    saturation_notice: str | None

    def __post_init__(self) -> None:
        unevaluable = self.status == "unevaluable"
        if unevaluable != (self.rate_m3_s is None):
            raise NucleationInputError(
                "rate_m3_s is set exactly when the status is evaluated"
            )
        if self.status not in {"evaluated", "unevaluable"}:
            raise NucleationInputError(f"unknown nucleation status {self.status!r}")


def fog_capture_code(result: NucleationRate) -> str | None:
    """Notice code for one rate. None means the molecular eta is still the capture claim."""

    if result.status != "evaluated" or result.rate_m3_s is None:
        return NOTICE_UNEVALUABLE
    if result.rate_m3_s > FOG_RATE_THRESHOLD_M3_S:
        return NOTICE_FOG
    return None


@lru_cache(maxsize=1)
def nucleation_liquid_data() -> dict:
    with _DATA_PATH.open("r", encoding="utf-8") as handle:
        payload = load_cached_safe_yaml(handle.read()) or {}
    if not isinstance(payload, dict):
        raise ValueError("nucleation_liquid.yaml root must be a mapping")
    if int(payload.get("schema_version", 0)) != 1:
        raise ValueError("nucleation_liquid.yaml schema_version must be 1")
    species = payload.get("species")
    if not isinstance(species, dict):
        raise ValueError("nucleation_liquid.yaml species must be a mapping")
    return payload


@lru_cache(maxsize=1)
def _sodium_saturation_block() -> tuple[dict | None, str | None]:
    """Load the Na block through the saturation owner. Cached, not mutated."""

    from copy import deepcopy

    from engines.builtin.sodium_saturation import sodium_saturation_block
    from simulator.config import load_config_bundle
    from simulator.vapour_rail.catalog import vapor_pressure_legacy_view

    try:
        view = vapor_pressure_legacy_view(load_config_bundle().vapor_pressures)
        block = sodium_saturation_block(view["metals"]["Na"])
    except Exception as exc:
        return None, f"Na saturation owner unavailable: {type(exc).__name__}: {exc}"
    if not isinstance(block, Mapping):
        return None, "Na pure_component_saturation block is missing"
    return deepcopy(dict(block)), None


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _temperature_K(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NucleationInputError("temperature_K must be a finite number")
    temperature = float(value)
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise NucleationInputError("temperature_K must be finite and positive")
    return temperature


def _partial_pressure_Pa(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NucleationInputError("p_species_Pa must be a finite number")
    pressure = float(value)
    if not math.isfinite(pressure) or pressure < 0.0:
        raise NucleationInputError("p_species_Pa must be finite and non-negative")
    return pressure


def _unevaluable(
    species: str,
    temperature_K: float,
    partial_pressure_Pa: float | None,
    reason: str,
) -> NucleationRate:
    return NucleationRate(
        status="unevaluable",
        species=species,
        temperature_K=temperature_K,
        partial_pressure_Pa=partial_pressure_Pa,
        rate_m3_s=None,
        saturation_pressure_Pa=None,
        saturation_ratio=None,
        saturation_authority=None,
        critical_cluster_molecules=None,
        reason=reason,
        property_notice=None,
        saturation_notice=None,
    )


def _molecular_mass_kg(species: str, row: Mapping) -> tuple[float | None, str | None]:
    formula = row.get("formula")
    if not isinstance(formula, str) or not formula.strip():
        return None, "species row has no formula"
    try:
        parsed = parse_formula(formula, species=species)
        molar_mass_kg_per_mol = parsed.molar_mass_kg_per_mol()
    except (UnknownSpeciesError, ValueError, TypeError) as exc:
        return None, f"molar mass unavailable: {exc}"
    if not math.isfinite(molar_mass_kg_per_mol) or molar_mass_kg_per_mol <= 0.0:
        return None, "molar mass is not a finite positive number"
    return molar_mass_kg_per_mol / AVOGADRO, None


def _fink_liquid(
    row: Mapping,
    temperature_K: float,
) -> tuple[float | None, float | None, str | None, str | None]:
    """Return rho kg/m3, sigma N/m, property notice, or a refusal reason."""

    reference = _finite_number(row.get("reference_temperature_K"))
    if reference is None or reference <= 0.0:
        return None, None, None, "Fink reference temperature is not sourced"
    tau = 1.0 - temperature_K / reference
    if tau <= 0.0:
        return (
            None,
            None,
            None,
            "temperature is at or above the Fink reference temperature; "
            "the liquid correlation is not defined",
        )
    density = row.get("density")
    tension = row.get("surface_tension")
    if not isinstance(density, Mapping) or not isinstance(tension, Mapping):
        return None, None, None, "liquid density or surface tension is not sourced"
    intercept = _finite_number(density.get("a_kg_m3"))
    linear = _finite_number(density.get("b_kg_m3"))
    sqrt_term = _finite_number(density.get("c_kg_m3"))
    density_exponent = _finite_number(density.get("tau_exponent"))
    amplitude = _finite_number(tension.get("A_mN_per_m"))
    tension_exponent = _finite_number(tension.get("exponent"))
    if None in {
        intercept,
        linear,
        sqrt_term,
        density_exponent,
        amplitude,
        tension_exponent,
    }:
        return None, None, None, "liquid density or surface tension is not sourced"
    rho = (
        intercept
        + linear * tau
        + sqrt_term * (tau ** density_exponent)
    )
    # Published unit is mN/m. The rate expression needs N/m.
    sigma = amplitude * (tau ** tension_exponent) * 1.0e-3
    if (
        not math.isfinite(rho)
        or rho <= 0.0
        or not math.isfinite(sigma)
        or sigma <= 0.0
    ):
        return (
            None,
            None,
            None,
            "liquid density or surface tension is not a finite positive number",
        )
    notice = None
    melting = _finite_number(row.get("melting_temperature_K"))
    if melting is not None and temperature_K < melting:
        notice = (
            "Fink and Leibowitz liquid density and surface tension evaluated "
            f"below the melting point {melting:g} K. The classical capillary "
            "rate is outside the droplet approximation when the critical "
            "cluster is of order one molecule; the rate is still reported."
        )
    return rho, sigma, notice, None


def _iapws_sigma_N_per_m(
    tension: Mapping,
    temperature_K: float,
) -> tuple[float | None, str | None]:
    critical = _finite_number(tension.get("critical_temperature_K"))
    amplitude = _finite_number(tension.get("B_mN_per_m"))
    mu = _finite_number(tension.get("mu"))
    coefficient = _finite_number(tension.get("b"))
    if None in {critical, amplitude, mu, coefficient} or critical <= 0.0:
        return None, "liquid surface tension is not sourced"
    tau = 1.0 - temperature_K / critical
    if tau <= 0.0:
        return None, "temperature is at or above the surface-tension critical point"
    # IAPWS R1-76(2014): sigma = B * tau^mu * (1 + b*tau), in mN/m.
    sigma_mN = amplitude * (tau ** mu) * (1.0 + coefficient * tau)
    sigma = sigma_mN * 1.0e-3
    if not math.isfinite(sigma) or sigma <= 0.0:
        return None, "liquid surface tension is not a finite positive number"
    return sigma, None


def _sanity_point_liquid(
    row: Mapping,
    temperature_K: float,
) -> tuple[float | None, float | None, float | None, str | None, str | None]:
    """Cited (rho, sigma, P_sat) at one temperature. Other temperatures refuse."""

    sourced = _finite_number(row.get("temperature_K"))
    if sourced is None:
        return None, None, None, None, "sanity-point temperature is not sourced"
    if temperature_K != sourced:
        return (
            None,
            None,
            None,
            None,
            f"liquid properties for this species are sourced only at {sourced:g} K",
        )
    rho = _finite_number(row.get("density_kg_m3"))
    saturation = _finite_number(row.get("saturation_pressure_Pa"))
    tension = row.get("surface_tension")
    if (
        rho is None
        or rho <= 0.0
        or saturation is None
        or saturation <= 0.0
        or not isinstance(tension, Mapping)
    ):
        return None, None, None, None, "liquid density or surface tension is not sourced"
    sigma, reason = _iapws_sigma_N_per_m(tension, temperature_K)
    if reason is not None or sigma is None:
        return None, None, None, None, reason or "liquid surface tension is not sourced"
    return rho, sigma, saturation, None, None


def _liquid_state(
    species: str,
    row: Mapping,
    temperature_K: float,
) -> tuple[
    float | None,
    float | None,
    float | None,
    str | None,
    str | None,
    str | None,
    str | None,
]:
    """Return rho, sigma, P_sat, authority, property notice, saturation notice, reason."""

    model = row.get("model")
    if model == "fink_leibowitz_anl_re_95_2":
        rho, sigma, property_notice, reason = _fink_liquid(row, temperature_K)
        if reason is not None:
            return None, None, None, None, None, None, reason
        if row.get("saturation_owner") != "browning_potter":
            return (
                None, None, None, None, None, None,
                "no saturation owner is sourced for this species",
            )
        block, block_reason = _sodium_saturation_block()
        if block is None:
            return None, None, None, None, None, None, block_reason or "Na saturation owner unavailable"
        from engines.builtin.sodium_saturation import (
            SodiumSaturationError,
            sodium_saturation_pressure,
        )

        try:
            saturation = sodium_saturation_pressure(block, temperature_K)
        except SodiumSaturationError as exc:
            return None, None, None, None, None, None, str(exc)
        return (
            rho,
            sigma,
            saturation.pressure_pa,
            saturation.authority,
            property_notice,
            saturation.notice,
            None,
        )
    if model == "classical_sanity_point":
        rho, sigma, saturation, property_notice, reason = _sanity_point_liquid(
            row, temperature_K,
        )
        if reason is not None:
            return None, None, None, None, None, None, reason
        return rho, sigma, saturation, "cited_point", property_notice, None, None
    return (
        None, None, None, None, None, None,
        "no sourced surface tension or liquid density",
    )


def _with_capillary_notice(
    property_notice: str | None,
    critical_cluster: float | None,
) -> str | None:
    if critical_cluster is None or critical_cluster >= _CAPILLARY_CLUSTER_NOTICE:
        return property_notice
    sentence = (
        f"Critical cluster n* = {critical_cluster:.3g} is below "
        f"{_CAPILLARY_CLUSTER_NOTICE:g}, so the absolute rate is not a "
        "precise droplet count."
    )
    if property_notice:
        return f"{property_notice} {sentence}"
    return sentence


def nucleation_rate(
    species: str,
    temperature_K: float,
    p_species_Pa: float,
) -> NucleationRate:
    """Classical homogeneous nucleation rate at one temperature and partial pressure.

    The derivation, units, and the water and sodium sanity comparisons are
    in the module docstring. This function does not invent a density or a
    surface tension. A species without those sourced inputs is unevaluable.
    """

    if not isinstance(species, str) or not species.strip():
        raise NucleationInputError("species must be a non-empty string")
    temperature = _temperature_K(temperature_K)
    pressure = _partial_pressure_Pa(p_species_Pa)
    try:
        row = nucleation_liquid_data()["species"].get(species)
    except (OSError, ValueError, TypeError) as exc:
        return _unevaluable(species, temperature, pressure, f"nucleation data unavailable: {exc}")
    if not isinstance(row, Mapping):
        return _unevaluable(
            species,
            temperature,
            pressure,
            "no sourced surface tension or liquid density",
        )
    mass, mass_reason = _molecular_mass_kg(species, row)
    if mass_reason is not None or mass is None:
        return _unevaluable(
            species,
            temperature,
            pressure,
            mass_reason or "molar mass unavailable",
        )
    (
        rho,
        sigma,
        saturation_pressure,
        authority,
        property_notice,
        saturation_notice,
        reason,
    ) = _liquid_state(species, row, temperature)
    if reason is not None or rho is None or sigma is None or saturation_pressure is None:
        return _unevaluable(
            species,
            temperature,
            pressure,
            reason or "no sourced surface tension or liquid density",
        )
    if saturation_pressure <= 0.0 or not math.isfinite(saturation_pressure):
        return _unevaluable(
            species,
            temperature,
            pressure,
            "saturation pressure is not a finite positive number",
        )

    ratio = pressure / saturation_pressure
    if pressure == 0.0 or ratio <= 1.0:
        return NucleationRate(
            status="evaluated",
            species=species,
            temperature_K=temperature,
            partial_pressure_Pa=pressure,
            rate_m3_s=0.0,
            saturation_pressure_Pa=saturation_pressure,
            saturation_ratio=ratio,
            saturation_authority=authority,
            critical_cluster_molecules=None,
            reason=None,
            property_notice=property_notice,
            saturation_notice=saturation_notice,
        )

    vm = mass / rho
    ln_s = math.log(ratio)
    thermal = BOLTZMANN * temperature
    barrier = (
        16.0 * math.pi * sigma**3 * vm**2
        / (3.0 * (thermal * ln_s) ** 2)
    )
    critical_cluster = 2.0 * barrier / (thermal * ln_s)
    exponent = -barrier / thermal
    number_density = pressure / thermal
    prefactor = (
        number_density**2
        * vm
        * math.sqrt(2.0 * sigma / (math.pi * mass))
    )
    if exponent < _UNDERFLOW_EXPONENT:
        rate = 0.0
    else:
        try:
            rate = prefactor * math.exp(exponent)
        except OverflowError:
            rate = math.inf
    if not math.isfinite(rate) and rate != math.inf:
        return _unevaluable(
            species,
            temperature,
            pressure,
            "nucleation rate is not a finite number",
        )
    if rate < 0.0:
        return _unevaluable(
            species,
            temperature,
            pressure,
            "nucleation rate is not a finite number",
        )
    return NucleationRate(
        status="evaluated",
        species=species,
        temperature_K=temperature,
        partial_pressure_Pa=pressure,
        rate_m3_s=rate,
        saturation_pressure_Pa=saturation_pressure,
        saturation_ratio=ratio,
        saturation_authority=authority,
        critical_cluster_molecules=critical_cluster,
        reason=None,
        property_notice=_with_capillary_notice(property_notice, critical_cluster),
        saturation_notice=saturation_notice,
    )
