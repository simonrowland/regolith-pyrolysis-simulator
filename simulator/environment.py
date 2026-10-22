"""Total ambient pressure limits, declared cover gas, and numerical O2 guards."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET, GAS_CONSTANT
from simulator.scalar_boundary import is_declared_real_scalar

_OVERHEAD_EOS_GAS_CONSTANT_J_PER_MOL_K = 8.31446

DEFAULT_VACUUM_FLOOR_BAR = 1.0e-9
MOON_VACUUM_FLOOR_BAR = 1.3e-12
ASTEROID_VACUUM_FLOOR_BAR = 1.0e-14
MARS_DATUM_PRESSURE_BAR = 6.1e-3
MARS_OLYMPUS_PRESSURE_BAR = 7.2e-4

_BODY_ALIASES = {
    "luna": "moon",
    "lunar": "moon",
    "moon": "moon",
    "asteroid": "asteroid",
    "asteroidal": "asteroid",
    "deep_space": "asteroid",
    "deep-space": "asteroid",
    "space": "asteroid",
    "mars": "mars",
    "martian": "mars",
}

_BODY_VACUUM_FLOOR_BAR = {
    "moon": MOON_VACUUM_FLOOR_BAR,
    "asteroid": ASTEROID_VACUUM_FLOOR_BAR,
    "mars": MARS_DATUM_PRESSURE_BAR,
}


class CoverGasCompositionUnavailable(ValueError):
    """The declared cover gas does not specify a usable composition."""


def overhead_gas_temperature_K(
    explicit_temperature_K: object,
    request_temperature_C: float,
) -> float:
    """Resolve the provider's explicit headspace T or request-T fallback."""
    return max(
        0.0,
        float(
            explicit_temperature_K
            or request_temperature_C + CELSIUS_TO_KELVIN_OFFSET
        ),
    )


def ideal_gas_partial_pressures_bar(
    holdup_mol: Mapping[str, float],
    volume_m3: float,
    temperature_K: float,
) -> dict[str, float]:
    """Project finite ideal-gas inventories to partial pressures in bar.

    The six-significant-digit gas constant preserves the legacy provider's
    numeric result; the state module keeps its own copy for existing callers.
    Algebra: p_bar=nRT/V/1e5. R*T/V has J/m³ = Pa, then 1e5 Pa/bar converts
    units; zero V or T retains the legacy empty-result limit.
    """
    if volume_m3 <= 0.0 or temperature_K <= 0.0:
        return {}
    scale = (
        _OVERHEAD_EOS_GAS_CONSTANT_J_PER_MOL_K
        * temperature_K
        / (volume_m3 * 1.0e5)
    )
    return {
        str(species): max(0.0, float(mol)) * scale
        for species, mol in dict(holdup_mol or {}).items()
        if max(0.0, float(mol)) > 0.0
    }


def _molar_flow_partial_pressures(
    molar_flow_by_species: Mapping[str, float], total_molar_flow: float, pressure: float,
) -> dict[str, float]:
    """Dalton projection in the caller's pressure units (Pa or mbar).

    p_i = n_dot_i/sum(n_dot) * P. The molar-rate units cancel, leaving
    pressure units; one species takes P. Callers retain their zero shortcuts.
    Caller-specific species resolution and validation stay at the boundary.
    """
    return {
        species: molar_flow / total_molar_flow * pressure
        for species, molar_flow in molar_flow_by_species.items()
    }


def _headspace_pressure_bar(mol: float, volume_m3: float, temperature_K: float) -> float:
    """Legacy scalar EOS, after caller-specific zero/validity handling.

    p=nRT/V; J/m³ is Pa, divided by 1e5 Pa/bar. At n=0 pressure is
    zero. Keep the scalar multiplication order and historical R: factoring
    out RT/V as in the map projection changes binary64 rounding.
    """
    return mol * _OVERHEAD_EOS_GAS_CONSTANT_J_PER_MOL_K * temperature_K / (
        volume_m3 * 1.0e5
    )


def _headspace_moles_at_pressure_pa(
    pressure_pa: float, volume_m3: float, temperature_K: float,
) -> float:
    """Inverse legacy EOS: PV/RT, Pa m³/(J/mol) = mol; P=0 gives n=0."""
    return pressure_pa * volume_m3 / (
        _OVERHEAD_EOS_GAS_CONSTANT_J_PER_MOL_K * temperature_K
    )


def _capacity_partial_pressures_pa(
    holdup_mol: Mapping[str, float], volume_m3: float, temperature_K: float,
) -> dict[str, float]:
    """Picard EOS with its existing full-precision R and multiplication order.

    nRT/V has units J/m³ = Pa. Unlike the legacy bar map, zero species
    remain present for the fixed-point vector; zero inventory gives zero P.
    Inputs have already passed the capacity solver's admission checks.
    """
    return {
        species: mol * GAS_CONSTANT * temperature_K / volume_m3
        for species, mol in holdup_mol.items()
    }


def controlled_background_partial_pressure(
    total_pressure: float, oxygen_partial_pressure: float, mole_fraction: float,
) -> float:
    """Background share of the non-commanded-O2 pressure, in input units.

    Dalton: P=P_O2+P_background, hence p_i=y_i*max(P-P_O2,0).
    y_i is dimensionless. At P=P_O2 no background remains; y_i=1 fills
    the entire residual. Preserve the existing boundary fraction clamp.
    """
    fraction = min(1.0, max(0.0, float(mole_fraction)))
    return max(0.0, total_pressure - oxygen_partial_pressure) * fraction


def cover_gas_partial_pressures(
    atmosphere: str, total_pressure: float,
) -> dict[str, float]:
    """Project the existing atmosphere declaration to known gas partials.

    Output pressure units match the input (bar or mbar).

    A percentage specifies that species only; the unreported balance is not
    invented as oxygen. Air explicitly denotes the 0.21 oxygen mole fraction.
    """
    pressure = float(total_pressure)
    if not math.isfinite(pressure) or pressure < 0.0:
        raise ValueError("cover-gas total_pressure must be finite and >= 0")
    if pressure == 0.0:
        return {}
    token = str(atmosphere).strip().upper().replace(" ", "")
    if token == "AIR":
        return {"O2": pressure * 0.21, "N2": pressure * 0.79}
    if token in {"CO2", "O2", "N2", "AR", "KR", "HE"}:
        species = {"AR": "Ar", "KR": "Kr", "HE": "He"}.get(token, token)
        return {species: pressure}
    if token.endswith("%CO2"):
        try:
            fraction = float(token[:-4]) / 100.0
        except ValueError:
            fraction = math.nan
        if math.isfinite(fraction) and 0.0 < fraction <= 1.0:
            # Ideal gas: p_i = y_i P. y_i is dimensionless, so bar stays bar;
            # y=1 gives pure gas and y->0 gives no partial of this species.
            return {"CO2": pressure * fraction}
    raise CoverGasCompositionUnavailable(
        f"cover-gas composition missing or unknown in atmosphere={atmosphere!r}"
    )


def normalize_body_name(body: object) -> str:
    """Return the canonical body token, or empty string for unknown/missing."""

    if body is None:
        return ""
    token = str(body).strip().lower().replace(" ", "_")
    if not token:
        return ""
    return _BODY_ALIASES.get(token, token)


def _positive_finite_bar(value: object, *, field: str) -> float:
    try:
        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise TypeError
        pressure_bar = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric, got {value!r}") from exc
    if not math.isfinite(pressure_bar) or pressure_bar <= 0.0:
        raise ValueError(f"{field} must be finite and > 0, got {value!r}")
    return pressure_bar


def vacuum_floor_bar_for_body(body: object) -> float:
    """Resolve the total-pressure vacuum/ambient limit for a body token.

    Grounding:
    - Moon: owner-reviewed nanotorr correction sets 1.3e-12 bar; NASA
      describes the lunar atmosphere as a tenuous exosphere.
    - Asteroid/deep space: owner-reviewed correction sets 1e-14 bar for
      airless small-body/deep-space processing.
    - Mars: pump-limited ambient, with 610 Pa datum and 72 Pa Olympus summit
      reference carried for CF-9 feasibility work.

    Missing or unknown bodies intentionally preserve the historical 1e-9 bar
    floor so old recipes stay golden-neutral until feedstock bodies are
    populated explicitly.
    """

    canonical = normalize_body_name(body)
    return _BODY_VACUUM_FLOOR_BAR.get(canonical, DEFAULT_VACUUM_FLOOR_BAR)


def numerical_pressure_floor_bar_for_body(body: object) -> float:
    """Pressure resolution for admission; Mars ambient belongs to pump costing.

    Airless-body floors already describe numerical vacuum resolution. Mars's
    ambient is a pumping load, so retain the historical numerical resolution
    there instead of forbidding the modelled subambient pressure choices.
    """
    if normalize_body_name(body) == "mars":
        return DEFAULT_VACUUM_FLOOR_BAR
    return vacuum_floor_bar_for_body(body)


def vacuum_floor_bar_for_environment(
    *,
    body: object = None,
    ambient_pressure_bar: object = None,
) -> float:
    """Resolve the vacuum floor from explicit body plus optional ambient."""

    canonical = normalize_body_name(body)
    if canonical == "mars" and ambient_pressure_bar is not None:
        return _positive_finite_bar(
            ambient_pressure_bar,
            field="ambient_pressure_bar",
        )
    return vacuum_floor_bar_for_body(canonical)


def oxygen_numerical_floor_bar_for_body(body: object) -> float:
    """Division/log guard, never an ambient oxygen measurement.

    Preserve the historical airless-body resolution. Mars's CO2 total pressure
    is not an oxygen lower bound; use the historical numerical guard there.
    The Moon's 1.3e-12 bar and asteroid's 1e-14 bar remain numerical guards,
    not claims that their exospheres are pure oxygen.
    """
    canonical = normalize_body_name(body)
    if canonical == "mars":
        return DEFAULT_VACUUM_FLOOR_BAR
    return vacuum_floor_bar_for_body(canonical)


def vacuum_floor_log10_for_environment(
    *,
    body: object = None,
    ambient_pressure_bar: object = None,
) -> float:
    return math.log10(
        vacuum_floor_bar_for_environment(
            body=body,
            ambient_pressure_bar=ambient_pressure_bar,
        )
    )


def feedstock_body(feedstock: Mapping[str, Any]) -> str:
    """Read an explicit body hook from a feedstock entry.

    No key-name inference here: missing/unknown body must default to the
    historical floor instead of silently reclassifying existing goldens.
    """

    environment = feedstock.get("environment", {}) or {}
    if not isinstance(environment, Mapping):
        environment = {}
    return normalize_body_name(
        feedstock.get("body")
        or environment.get("body")
        or feedstock.get("planetary_body")
        or environment.get("planetary_body")
    )


def feedstock_surface_pressure_mbar(feedstock: Mapping[str, Any]) -> float:
    environment = feedstock.get('environment', {}) or {}
    surface_pressure = feedstock.get('surface_pressure_mbar')
    environment_surface_pressure = environment.get('surface_pressure_mbar')

    def _validated_surface_pressure(value: Any, field: str) -> float:
        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise TypeError(f"{field} must be numeric")
        pressure_mbar = float(value)
        if not math.isfinite(pressure_mbar) or pressure_mbar < 0.0:
            raise ValueError(f"{field} must be finite and nonnegative")
        return pressure_mbar

    validated_surface_pressure = None
    if surface_pressure not in (None, ''):
        validated_surface_pressure = _validated_surface_pressure(
            surface_pressure,
            'surface_pressure_mbar',
        )
    validated_environment_pressure = None
    if environment_surface_pressure not in (None, ''):
        validated_environment_pressure = _validated_surface_pressure(
            environment_surface_pressure,
            'environment.surface_pressure_mbar',
        )
    return float(
        validated_surface_pressure if validated_surface_pressure is not None
        else validated_environment_pressure if validated_environment_pressure is not None
        else 0.0
    )
