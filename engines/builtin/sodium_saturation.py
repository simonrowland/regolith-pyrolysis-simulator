"""Pure-sodium saturation pressure. One owner.

Liquid, T >= T_m. Fink & Leibowitz, ANL/RE-95/2 (1995) §1.2 Eq. (1),
the Browning & Potter equation for liquid sodium in equilibrium with its vapor:

    ln(P/MPa) = a - b/T - c ln T
    P_Pa = 1e6 * exp(ln(P/MPa))

Coefficients, the certified band 864-2499 K, T_m, and the fusion enthalpy
are read from the species block in data/vapor_pressures.yaml. Outside that
band the same equation is evaluated and flagged. Fink's own comparison is
part of the notice: extrapolation to 400 K differs by 24% from the LMFBR
safety-analysis equation.

Solid, T < T_m. The handbook equation is the liquid curve. The solid
pressure follows from the Clausius-Clapeyron relation between the two
condensed phases at the same temperature, with the fusion enthalpy taken
from the same handbook.

Premise: along a saturation curve, d ln P / d(1/T) = -ΔH/R. Subtracting
the liquid and solid curves and holding ΔH_fus constant gives

    ln(P_s/P_l) = -(ΔH_fus/R) (1/T - 1/T_m)

Algebra: P_s = P_l * exp(that). Units: ΔH_fus is J/mol, R is J/(mol K),
T is K, so the exponent is dimensionless and P_s has the unit of P_l.
The handbook Table 1.1-4 (CODATA, Cordfunke & Konings) prints the
transition at 371 K as H(liquid) - H(solid, 298.15 K) = 4752 J/mol and
H(solid) - H(solid, 298.15 K) = 2154 J/mol, so ΔH_fus = 2598 J/mol.
The branch temperature stored with the coefficients is 370.98 K.

Sanity: the ratio is 1 at T_m. At 313.15 K with ΔH_fus = 2598 J/mol,
(ΔH_fus/R)(1/313.15 - 1/370.98) is about 0.155 and the ratio is about 0.86.

The solid branch is always extrapolated: T_m is below 864 K, so the liquid
base is already outside the certified band, and the fusion correction is
applied. One flag names both. A non-positive temperature is invalid input.
A non-positive underflow is an error, never a silent 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from simulator.physical_constants import GAS_CONSTANT

_EQUATION = "browning_potter"


class SodiumSaturationError(ValueError):
    """The sodium saturation block or the temperature cannot be evaluated."""


@dataclass(frozen=True)
class SodiumSaturation:
    pressure_pa: float
    authority: str
    phase: str
    reservoir_phase: str
    certified_range_K: tuple[float, float]
    melting_temperature_K: float
    notice: str | None


def sodium_saturation_block(row: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if not isinstance(row, Mapping):
        return None
    block = row.get("pure_component_saturation")
    if isinstance(block, Mapping):
        return block
    return None


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SodiumSaturationError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise SodiumSaturationError(f"{field} must be a finite number")
    return number


def sodium_saturation_pressure(
    block: Mapping[str, object],
    temperature_K: float,
) -> SodiumSaturation:
    """Return pure-Na saturation pressure at ``temperature_K``."""

    if block.get("equation") != _EQUATION:
        raise SodiumSaturationError(
            f"unsupported sodium saturation equation {block.get('equation')!r}"
        )
    if isinstance(temperature_K, bool) or isinstance(temperature_K, str):
        raise SodiumSaturationError("temperature_K must be a finite number")
    try:
        temperature = float(temperature_K)
    except (TypeError, ValueError):
        raise SodiumSaturationError("temperature_K must be a finite number") from None
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise SodiumSaturationError("temperature_K must be finite and positive")

    coefficients = block.get("ln_p_mpa")
    if not isinstance(coefficients, Mapping):
        raise SodiumSaturationError("ln_p_mpa is required")
    intercept = _finite(coefficients.get("a"), "ln_p_mpa.a")
    slope = _finite(coefficients.get("b"), "ln_p_mpa.b")
    log_coefficient = _finite(coefficients.get("c"), "ln_p_mpa.c")
    certified_raw = block.get("certified_range_K")
    if (
        not isinstance(certified_raw, (list, tuple))
        or isinstance(certified_raw, (str, bytes))
        or len(certified_raw) != 2
    ):
        raise SodiumSaturationError("certified_range_K must be [low, high]")
    certified = (
        _finite(certified_raw[0], "certified_range_K[0]"),
        _finite(certified_raw[1], "certified_range_K[1]"),
    )
    if certified[1] <= certified[0]:
        raise SodiumSaturationError("certified_range_K must be increasing")
    melting = _finite(block.get("melting_temperature_K"), "melting_temperature_K")
    if melting <= 0.0:
        raise SodiumSaturationError("melting_temperature_K must be positive")
    fusion = _finite(block.get("fusion_enthalpy_J_per_mol"), "fusion_enthalpy_J_per_mol")
    if fusion <= 0.0:
        raise SodiumSaturationError("fusion_enthalpy_J_per_mol must be positive")

    # Browning-Potter liquid pressure. P is MPa inside the logarithm.
    ln_p_mpa = intercept - slope / temperature - log_coefficient * math.log(temperature)
    liquid_pa = 1.0e6 * math.exp(ln_p_mpa)
    if temperature < melting:
        # ln(P_s/P_l) = -(ΔH_fus/R) (1/T - 1/T_m); see the module comment.
        exponent = -(fusion / GAS_CONSTANT) * (1.0 / temperature - 1.0 / melting)
        pressure_pa = liquid_pa * math.exp(exponent)
        phase = "solid"
        reservoir_phase = "cr"
    else:
        pressure_pa = liquid_pa
        phase = "liquid"
        reservoir_phase = "l"
    if not math.isfinite(pressure_pa) or pressure_pa <= 0.0:
        raise SodiumSaturationError(
            "sodium saturation pressure is not a finite positive number"
        )

    in_certified_band = certified[0] <= temperature <= certified[1] and temperature >= melting
    if in_certified_band:
        return SodiumSaturation(
            pressure_pa=pressure_pa,
            authority="certified",
            phase=phase,
            reservoir_phase=reservoir_phase,
            certified_range_K=certified,
            melting_temperature_K=melting,
            notice=None,
        )

    reasons = [
        "Browning-Potter liquid equation evaluated outside its certified band "
        f"[{certified[0]:g}, {certified[1]:g}] K"
    ]
    if temperature < melting:
        reasons.append(
            "solid branch corrects that liquid pressure by the handbook fusion "
            "enthalpy: ln(P_s/P_l) = -(dH_fus/R)(1/T - 1/T_m)"
        )
    caution = block.get("extrapolation_caution")
    notice = "; ".join(reasons)
    if isinstance(caution, str) and caution.strip():
        notice = f"{notice}. {caution.strip()}"
    return SodiumSaturation(
        pressure_pa=pressure_pa,
        authority="extrapolated",
        phase=phase,
        reservoir_phase=reservoir_phase,
        certified_range_K=certified,
        melting_temperature_K=melting,
        notice=notice,
    )


def sodium_saturation_temperature_K(
    block: Mapping[str, object],
    pressure_pa: float,
) -> float:
    """Temperature where this same saturation function equals ``pressure_pa``.

    Bisection of ``sodium_saturation_pressure``. There is no second equation.
    The liquid curve increases with temperature, and the solid correction
    does too, so the composite pressure is monotone on the bracket
    [200 K, 3000 K]. Eighty bisections put the root well inside a millikelvin.
    """

    if isinstance(pressure_pa, bool) or isinstance(pressure_pa, str):
        raise SodiumSaturationError("pressure_pa must be a finite number")
    try:
        target = float(pressure_pa)
    except (TypeError, ValueError):
        raise SodiumSaturationError("pressure_pa must be a finite number") from None
    if not math.isfinite(target) or target <= 0.0:
        raise SodiumSaturationError("pressure_pa must be finite and positive")
    low = 200.0
    high = 3000.0
    low_pressure = sodium_saturation_pressure(block, low).pressure_pa
    high_pressure = sodium_saturation_pressure(block, high).pressure_pa
    if target < low_pressure or target > high_pressure:
        raise SodiumSaturationError(
            "pressure_pa is outside the saturation curve on [200, 3000] K"
        )
    for _step in range(80):
        mid = 0.5 * (low + high)
        if sodium_saturation_pressure(block, mid).pressure_pa < target:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)
