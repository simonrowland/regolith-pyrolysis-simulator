"""Literature-grounded liquid-metal and alloy-density helpers.

This is intentionally a leaf module: sidecar data -> pure functions.  It does
not own simulator state and it does not reproduce the silicate-melt EOS.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL

_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "material_densities.yaml"


@lru_cache(maxsize=1)
def material_density_data() -> dict[str, Any]:
    with _DATA_PATH.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if int(payload.get("schema_version", 0)) != 1:
        raise ValueError("material_densities.yaml schema_version must be 1")
    return payload


def liquid_metal_density_kg_m3(species: str, temperature_K: float) -> float:
    """Return the cited linear liquid-density correlation for ``species``.

    Premise: the references regress liquid density at 0.1 MPa as a straight
    line from the melting point. Algebra: rho=rho_m-k*(T-Tm). Unit check:
    kg/m3 - (kg/m3/K)*K = kg/m3. Sanity: Fe gives about 6.98 g/cm3 near
    1600 C, matching the standard molten-iron range.
    """

    try:
        row = material_density_data()["liquid_metals"][str(species)]
    except KeyError as exc:
        raise KeyError(f"no liquid-metal density correlation for {species!r}") from exc
    T = float(temperature_K)
    if not math.isfinite(T) or T <= 0.0:
        raise ValueError("temperature_K must be finite and positive")
    rho_m = float(row["rho_at_melting_kg_m3"])
    slope = float(row["slope_kg_m3_K"])
    Tm = float(row["melting_temperature_K"])
    rho = rho_m - slope * (T - Tm)
    if not math.isfinite(rho) or rho <= 0.0:
        raise ValueError(f"non-positive liquid density for {species!r} at {T:g} K")
    return rho


def liquid_metal_density_provenance(
    species: str,
    temperature_K: float,
) -> dict[str, Any]:
    """Expose citation and whether the requested T is inside its fitted range."""

    try:
        row = material_density_data()["liquid_metals"][str(species)]
    except KeyError as exc:
        raise KeyError(f"no liquid-metal density correlation for {species!r}") from exc
    temperature = float(temperature_K)
    low, high = (float(value) for value in row["valid_range_K"])
    if temperature < low:
        status = "extrapolated_below_valid_range"
    elif temperature > high:
        status = "extrapolated_above_valid_range"
    else:
        status = "within_valid_range"
    return {
        "source": str(row["source"]),
        "valid_range_K": [low, high],
        "temperature_K": temperature,
        "status": status,
    }


def _clean_species_mol(species_mol: Mapping[str, float]) -> dict[str, float]:
    cleaned = {
        str(species): float(amount)
        for species, amount in dict(species_mol or {}).items()
        if float(amount) > 0.0
    }
    if not cleaned:
        raise ValueError("species_mol must contain a positive amount")
    return cleaned


def _ideal_alloy_molar_properties(
    species_mol: Mapping[str, float],
    temperature_K: float,
) -> tuple[float, float, dict[str, float]]:
    """Return ideal molar mass, molar volume, and mole fractions."""

    cleaned = _clean_species_mol(species_mol)
    total_mol = sum(cleaned.values())
    molar_mass_kg_mol = 0.0
    ideal_molar_volume_m3_mol = 0.0
    mole_fractions: dict[str, float] = {}
    for species, amount_mol in cleaned.items():
        if species not in ATOMIC_WEIGHTS_G_PER_MOL:
            raise KeyError(f"no atomic weight for alloy species {species!r}")
        x_i = amount_mol / total_mol
        mole_fractions[species] = x_i
        M_i = float(ATOMIC_WEIGHTS_G_PER_MOL[species]) / 1000.0
        rho_i = liquid_metal_density_kg_m3(species, temperature_K)
        molar_mass_kg_mol += x_i * M_i
        ideal_molar_volume_m3_mol += x_i * M_i / rho_i
    return molar_mass_kg_mol, ideal_molar_volume_m3_mol, mole_fractions


def _ideal_alloy_density_kg_m3(
    species_mol: Mapping[str, float],
    temperature_K: float,
) -> float:
    """Private ideal-volume reference used by source-grounding tests."""

    molar_mass, molar_volume, _ = _ideal_alloy_molar_properties(
        species_mol, temperature_K
    )
    return molar_mass / molar_volume


def _piecewise_linear_value(x: float, anchors: list[list[float]]) -> float:
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x <= x1:
            fraction = (x - x0) / (x1 - x0)
            return y0 + fraction * (y1 - y0)
    return float(anchors[-1][1])


def _fe_si_fractional_contraction(x_si: float) -> float:
    row = material_density_data()["fe_si_excess_volume"]
    return _piecewise_linear_value(
        float(x_si),
        [
            [float(value) for value in point]
            for point in row["fractional_contraction_anchors"]
        ],
    )


def _fe_si_source_molar_volume_m3_mol(
    x_si: float,
    temperature_K: float,
) -> float:
    """Mizuno et al. Fe-Si molar volume using one consistent endpoint basis."""

    row = material_density_data()["fe_si_excess_volume"]
    endpoints = row["endpoint_correlations"]
    endpoint_density: dict[str, float] = {}
    for species in ("Fe", "Si"):
        endpoint = endpoints[species]
        endpoint_density[species] = float(endpoint["rho_at_reference_kg_m3"]) - float(
            endpoint["slope_kg_m3_K"]
        ) * (float(temperature_K) - float(endpoint["reference_temperature_K"]))

    x_fe = 1.0 - x_si
    M_fe = float(ATOMIC_WEIGHTS_G_PER_MOL["Fe"]) / 1000.0
    M_si = float(ATOMIC_WEIGHTS_G_PER_MOL["Si"]) / 1000.0
    ideal_volume = (
        x_fe * M_fe / endpoint_density["Fe"]
        + x_si * M_si / endpoint_density["Si"]
    )
    contraction = _fe_si_fractional_contraction(x_si)
    # Mizuno defines V_E=V_M-sum(x_i*V_i). Therefore a positive fractional
    # contraction q=-V_E/V_ideal gives V_M=V_ideal*(1-q). Both q anchors are
    # dimensionless; the result remains m3/mol.
    return ideal_volume * (1.0 - contraction)


def alloy_density_kg_m3(
    species_mol: Mapping[str, float],
    temperature_K: float,
) -> float:
    """Return alloy density from molar-volume mixing plus measured Fe-Si V_E.

    Premise: volumes, not densities, are extensive. For mole fractions x_i,
    ideal molar volume is sum(x_i*M_i/rho_i), while molar mass is sum(x_i*M_i);
    therefore rho_mix=sum(x_i*M_i)/sum(x_i*M_i/rho_i). Unit check:
    (kg/mol)/(m3/mol)=kg/m3. Fe-Si uses Mizuno et al.'s internally consistent
    endpoint correlations and a piecewise representation of its measured
    composition fit; the correction is unconditional because it is physics,
    not a caller-selectable mode.
    """

    molar_mass_kg_mol, volume_m3_mol, mole_fractions = (
        _ideal_alloy_molar_properties(species_mol, temperature_K)
    )
    if {"Fe", "Si"}.issubset(mole_fractions):
        pair_fraction = mole_fractions["Fe"] + mole_fractions["Si"]
        x_si_pair = mole_fractions["Si"] / pair_fraction
        generic_pair_volume = sum(
            mole_fractions[species]
            * (float(ATOMIC_WEIGHTS_G_PER_MOL[species]) / 1000.0)
            / liquid_metal_density_kg_m3(species, temperature_K)
            for species in ("Fe", "Si")
        )
        source_pair_volume = pair_fraction * _fe_si_source_molar_volume_m3_mol(
            x_si_pair, temperature_K
        )
        volume_m3_mol += source_pair_volume - generic_pair_volume

    density = molar_mass_kg_mol / volume_m3_mol
    if not math.isfinite(density) or density <= 0.0:
        raise ValueError("computed alloy density is not finite and positive")
    return density


def alloy_density_uncertainty_relative_fraction(
    species_mol: Mapping[str, float],
) -> float:
    """Return the conservative largest cited 95% component uncertainty."""

    uncertainties = []
    for species in _clean_species_mol(species_mol):
        value = material_density_data()["liquid_metals"][species].get(
            "uncertainty_95_pct"
        )
        if value is not None:
            uncertainties.append(float(value) / 100.0)
    return max(uncertainties, default=0.0)


def resolve_melt_density_kg_m3(engine_density_kg_m3: float | None) -> tuple[float, str]:
    """Return engine density or a conspicuously labeled fallback tier."""

    try:
        density = float(engine_density_kg_m3) if engine_density_kg_m3 is not None else 0.0
    except (TypeError, ValueError):
        density = 0.0
    if math.isfinite(density) and density > 0.0:
        return density, "engine_liquid_eos"
    fallback = float(
        material_density_data()["diagnostic_assumptions"][
            "melt_density_fallback_kg_m3"
        ]
    )
    return fallback, "fallback_basaltic_melt_constant_engine_density_unavailable"


def buoyancy_verdict(
    alloy_density_kg_m3: float,
    melt_density_kg_m3: float,
    *,
    alloy_uncertainty_relative_fraction: float = 0.0,
) -> dict[str, float | str]:
    """Classify a droplet from its density contrast with an honesty band.

    Premise: buoyant force changes sign with rho_droplet-rho_melt. Algebra:
    delta_rho=rho_alloy-rho_melt; positive sinks, negative floats. Unit check:
    both terms and delta are kg/m3. Sanity: Fe is a clear sink in basaltic
    melt, while liquid Si near 1600 C lies close enough to the combined data
    uncertainty to be BUOYANCY-AMBIGUOUS rather than over-resolved.
    """

    rho_alloy = float(alloy_density_kg_m3)
    rho_melt = float(melt_density_kg_m3)
    assumptions = material_density_data()["diagnostic_assumptions"]
    melt_uncertainty = (
        float(assumptions["buoyancy_ambiguity_relative_fraction"]) * rho_melt
    )
    alloy_uncertainty = (
        float(alloy_uncertainty_relative_fraction) * rho_alloy
    )
    # Independent 95% density uncertainties combine by root-sum-square. The
    # absolute floor still protects cases whose sources omit uncertainty.
    threshold = max(
        float(assumptions["buoyancy_ambiguity_absolute_kg_m3"]),
        math.hypot(melt_uncertainty, alloy_uncertainty),
    )
    delta = rho_alloy - rho_melt
    if abs(delta) <= threshold:
        verdict = "BUOYANCY-AMBIGUOUS"
    elif delta > 0.0:
        verdict = "sink"
    else:
        verdict = "float"
    return {
        "alloy_density_kg_m3": rho_alloy,
        "melt_density_kg_m3": rho_melt,
        "delta_rho_kg_m3": delta,
        "ambiguity_threshold_kg_m3": threshold,
        "melt_density_uncertainty_kg_m3": melt_uncertainty,
        "alloy_density_uncertainty_kg_m3": alloy_uncertainty,
        "verdict": verdict,
    }
