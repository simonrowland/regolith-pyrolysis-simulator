"""Continuous wall-deposition rate diagnostics.

This module is pure: it neither reads nor writes the atom ledger. The
condensation route uses it to evaluate the same series-resistance flux that
feeds the existing mol-native wall-deposit transition; report code may then
project committed mol deltas into operator-facing rates.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from simulator.scalar_boundary import is_declared_real_scalar


@dataclass(frozen=True)
class WallDepositionFlux:
    """Decomposed gas-to-wall flux for one species and surface."""

    collision_mol_m2_s: float
    stuck_mol_m2_s: float
    reevaporated_mol_m2_s: float
    net_mol_m2_s: float
    wall_temperature_K: float

    def to_dict(self) -> dict[str, float]:
        return {
            "incident_mol_m2_s": self.collision_mol_m2_s,
            "stuck_mol_m2_s": self.stuck_mol_m2_s,
            "reevaporated_mol_m2_s": self.reevaporated_mol_m2_s,
            "net_mol_m2_s": self.net_mol_m2_s,
            "wall_temperature_K": self.wall_temperature_K,
        }


def continuous_wall_deposition_flux(
    *,
    bulk_pressure_pa: float,
    equilibrium_pressure_pa: float,
    collision_coefficient_mol_m2_s_pa: float,
    sticking_coefficient: float,
    gas_resistance_pa_m2_s_mol: float,
    wall_temperature_K: float,
    reevaporation_flux_mol_m2_s: float | None = None,
) -> WallDepositionFlux:
    """Return the experiment-validatable bidirectional deposition flux.

    Premise: gas-film transport and sticking-inclusive surface kinetics are
    sequential resistances. With ``k_s = alpha_c*k_HK`` and gas resistance
    ``R_g``, eliminating the unknown wall partial pressure gives

        J_net = (k_s*p_bulk - J_reevap) / (1 + k_s*R_g),
        J_reevap = measured_desorption_flux(T_wall, surface_state).

    If no independent reverse flux is supplied, the detailed-balance reference
    case uses ``J_reevap = k_s*p_eq``. That fallback does not assert that the
    condensation and evaporation coefficients are generally equal; measured
    Polanyi-Wigner or reverse-HKL flux belongs in ``reevaporation_flux``.

    Unit check: ``k_s`` is mol m^-2 s^-1 Pa^-1, so both numerator terms are
    mol m^-2 s^-1 and ``k_s*R_g`` is dimensionless.

    Limits: ``R_g -> 0`` recovers the surface HKL law; large ``R_g`` is
    transport-limited; ``alpha_c -> 0`` gives zero retention; and
    ``p_bulk == p_eq`` gives detailed-balance zero net flux.
    """

    values = {
        "bulk_pressure_pa": bulk_pressure_pa,
        "equilibrium_pressure_pa": equilibrium_pressure_pa,
        "collision_coefficient_mol_m2_s_pa": (
            collision_coefficient_mol_m2_s_pa
        ),
        "sticking_coefficient": sticking_coefficient,
        "gas_resistance_pa_m2_s_mol": gas_resistance_pa_m2_s_mol,
        "wall_temperature_K": wall_temperature_K,
    }
    if any(
        not is_declared_real_scalar(value, allow_numeric_str=True)
        for value in values.values()
    ):
        raise TypeError("wall-deposition rate input is missing")
    if any(not math.isfinite(float(value)) for value in values.values()):
        raise ValueError("wall-deposition rate inputs must be finite")
    if bulk_pressure_pa < 0.0 or equilibrium_pressure_pa < 0.0:
        raise ValueError("wall-deposition pressures must be non-negative")
    if collision_coefficient_mol_m2_s_pa <= 0.0:
        raise ValueError("collision coefficient must be positive")
    if sticking_coefficient < 0.0:
        raise ValueError("sticking coefficient must be non-negative")
    if gas_resistance_pa_m2_s_mol < 0.0:
        raise ValueError("gas resistance must be non-negative")
    if wall_temperature_K <= 0.0:
        raise ValueError("wall temperature must be positive")
    if (
        reevaporation_flux_mol_m2_s is not None
        and (
            not is_declared_real_scalar(
                reevaporation_flux_mol_m2_s,
                allow_numeric_str=True,
            )
            or not math.isfinite(float(reevaporation_flux_mol_m2_s))
            or reevaporation_flux_mol_m2_s < 0.0
        )
    ):
        raise ValueError("re-evaporation flux must be finite and non-negative")

    collision = collision_coefficient_mol_m2_s_pa * bulk_pressure_pa
    k_surface = sticking_coefficient * collision_coefficient_mol_m2_s_pa
    denominator = 1.0 + k_surface * gas_resistance_pa_m2_s_mol
    stuck = k_surface * bulk_pressure_pa / denominator
    reverse_flux = (
        k_surface * equilibrium_pressure_pa
        if reevaporation_flux_mol_m2_s is None
        else float(reevaporation_flux_mol_m2_s)
    )
    reevaporated = reverse_flux / denominator
    net = (k_surface * bulk_pressure_pa - reverse_flux) / denominator
    return WallDepositionFlux(
        collision_mol_m2_s=collision,
        stuck_mol_m2_s=stuck,
        reevaporated_mol_m2_s=reevaporated,
        net_mol_m2_s=net,
        wall_temperature_K=wall_temperature_K,
    )
