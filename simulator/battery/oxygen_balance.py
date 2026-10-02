"""Shared engine-origin rules for solved oxygen-balance notices."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from scipy.optimize import brentq

from simulator.battery.enums import Engine, NoticeKind
from simulator.battery.records import Notice
from simulator.physical_constants import GAS_CONSTANT

IMCC_ENGINES: frozenset[Engine] = frozenset({Engine.OPENIMCC})
OXYGEN_BALANCE_EFFUSION_ENGINES: frozenset[Engine] = frozenset(
    {Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL}
)
OXYGEN_BALANCE_NOTICE_PREFIX = "fo2_oxygen_balance_effusion_solved:"
_OXYGEN_GAS_ALPHA = 1.0
_OXYGEN_GAS_ALPHA_SOURCE = (
    "declared common-unity sensitivity alpha=1.0 for O/O2 free-molecular gas escape; "
    "absolute oxygen-gas alpha is not measured"
)


@dataclass(frozen=True)
class _VacuumOxygenChannel:
    """One active gas channel participating in a vacuum oxygen balance."""

    species: str
    molar_mass_kg_per_mol: float
    oxygen_atoms: float
    parent_oxygen_demand: float
    pO2_exponent: float
    alpha: float
    alpha_source: str
    active: bool = True


@dataclass(frozen=True)
class _VacuumOxygenBalance:
    pO2_bar: float
    pressures_Pa: Mapping[str, float]
    channel_fluxes_mol_m2_s: Mapping[str, float]
    oxygen_atom_flux_mol_m2_s: float
    parent_oxygen_demand_flux_mol_m2_s: float
    relative_residual: float
    alpha_sources: Mapping[str, str]


def _solve_vacuum_oxygen_balance(
    pressure_model: Callable[[float], Mapping[str, float]],
    channels: Sequence[_VacuumOxygenChannel],
    *,
    temperature_K: float,
    bracket_log10_pO2_bar: tuple[float, float] = (-30.0, 0.0),
) -> _VacuumOxygenBalance:
    """Solve an alpha-weighted, engine-supplied vacuum pressure model.

    Premise: in free evaporation, the outgoing oxygen atoms carried by gas
    channels must equal the oxygen removed with their parent oxides. For
    channel ``i`` the vacuum Hertz-Knudsen molar flux is
    ``J_i = alpha_i * p_i / sqrt(2*pi*M_i*R*T)``; the root is therefore
    ``sum_i (nO_i - dO_i) * alpha_i * p_i / sqrt(M_i) = 0``. Pressure-model
    inputs and outputs are Pa (the independent root coordinate is
    ``log10(pO2 / bar)``), ``M_i`` is kg/mol, and ``T`` is K. The omitted
    ``1/sqrt(2*pi*R*T)`` is common to every term and is restored when this
    function reports mol m^-2 s^-1 fluxes. With one channel, a nonzero
    oxygen difference cannot close at positive pressure; a zero difference
    makes that channel neutral and leaves no pO2 constraint. If every active
    alpha is multiplied by the same positive factor, it multiplies both
    sides equally and cancels from the root. Different channel alphas do not
    cancel and must be applied before solving.

    ``pressure_model`` must be the pressure law for the engine being evaluated.
    Channel alpha and provenance come from that evaluation's runtime catalog;
    oxygen-gas channels may carry an explicitly declared oxygen-gas alpha.
    Dormant/reporting-only channels are omitted by setting ``active=False``.
    """

    temperature = float(temperature_K)
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise ValueError("temperature_K must be finite and positive")
    low, high = bracket_log10_pO2_bar
    if not (math.isfinite(low) and math.isfinite(high) and low < high <= 0.0):
        raise ValueError("pO2 bracket must be finite, increasing, and at most 1 bar")

    active_channels: list[_VacuumOxygenChannel] = []
    seen: set[str] = set()
    strictly_monotone = False
    for channel in channels:
        if channel.species in seen:
            raise ValueError(f"duplicate oxygen-balance channel {channel.species!r}")
        seen.add(channel.species)
        if not channel.active:
            continue
        if not isinstance(channel.alpha_source, str) or not channel.alpha_source.strip():
            raise ValueError(f"alpha provenance is required for {channel.species!r}")
        values = (
            channel.molar_mass_kg_per_mol,
            channel.oxygen_atoms,
            channel.parent_oxygen_demand,
            channel.pO2_exponent,
            channel.alpha,
        )
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError(f"non-finite oxygen-channel data for {channel.species!r}")
        if channel.molar_mass_kg_per_mol <= 0.0:
            raise ValueError(f"molar mass must be positive for {channel.species!r}")
        if channel.oxygen_atoms < 0.0 or channel.parent_oxygen_demand < 0.0:
            raise ValueError(f"oxygen counts must be non-negative for {channel.species!r}")
        if channel.alpha < 0.0:
            raise ValueError(f"alpha must be non-negative for {channel.species!r}")
        if channel.alpha == 0.0:
            continue
        signed_weight = (
            channel.oxygen_atoms - channel.parent_oxygen_demand
        ) / math.sqrt(channel.molar_mass_kg_per_mol)
        slope_sign = signed_weight * channel.pO2_exponent
        if slope_sign < 0.0:
            raise ValueError(
                f"oxygen-balance flux is non-monotone for {channel.species!r}"
            )
        strictly_monotone = strictly_monotone or slope_sign > 0.0
        active_channels.append(channel)
    if not active_channels or not strictly_monotone:
        raise ValueError("oxygen-balance flux is not strictly monotone for active channels")

    def evaluate(logp: float) -> tuple[float, dict[str, float], float, float]:
        model_pressures = pressure_model(logp)
        if not isinstance(model_pressures, Mapping):
            raise ValueError("pressure model must return a species-to-Pa mapping")
        pressures: dict[str, float] = {}
        oxygen_flux = 0.0
        parent_flux = 0.0
        for channel in active_channels:
            try:
                pressure = float(model_pressures[channel.species])
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    f"pressure model is missing a valid {channel.species!r} pressure"
                ) from exc
            if not math.isfinite(pressure) or pressure < 0.0:
                raise ValueError(
                    f"pressure model returned invalid {channel.species!r} pressure"
                )
            pressures[channel.species] = pressure
            molar_flux = channel.alpha * pressure / math.sqrt(
                2.0 * math.pi * GAS_CONSTANT * temperature * channel.molar_mass_kg_per_mol
            )
            oxygen_flux += channel.oxygen_atoms * molar_flux
            parent_flux += channel.parent_oxygen_demand * molar_flux
        return oxygen_flux - parent_flux, pressures, oxygen_flux, parent_flux

    f_low = evaluate(low)[0]
    f_high = evaluate(high)[0]
    if f_low == 0.0:
        root = low
    elif f_high == 0.0:
        root = high
    elif f_low * f_high > 0.0:
        raise ValueError(
            "vacuum oxygen balance has no root in the declared pO2 bracket"
        )
    else:
        root = brentq(
            lambda value: evaluate(value)[0],
            low,
            high,
            xtol=1.0e-12,
            rtol=1.0e-12,
            maxiter=200,
        )

    residual, pressures, oxygen_flux, parent_flux = evaluate(root)
    lower_pressures = evaluate(root - 0.1)[1]
    upper_pressures = evaluate(root + 0.1)[1]
    for channel in active_channels:
        pressure = pressures[channel.species]
        if pressure <= 1.0e-300:
            continue
        lower_pressure = lower_pressures[channel.species]
        upper_pressure = upper_pressures[channel.species]
        if lower_pressure <= 0.0 or upper_pressure <= 0.0:
            raise ValueError(
                f"pressure law for {channel.species!r} is not positive around the root"
            )
        observed_exponent = (
            math.log10(upper_pressure) - math.log10(lower_pressure)
        ) / 0.2
        if abs(observed_exponent - channel.pO2_exponent) > 1.0e-6:
            raise ValueError(
                f"pressure law exponent mismatch for {channel.species!r}: "
                f"declared {channel.pO2_exponent}, observed {observed_exponent}"
            )

    relative_residual = abs(residual) / max(oxygen_flux, parent_flux, 1.0e-300)
    fluxes = {
        channel.species: channel.alpha * pressures[channel.species]
        / math.sqrt(
            2.0 * math.pi * GAS_CONSTANT * temperature * channel.molar_mass_kg_per_mol
        )
        for channel in active_channels
    }
    return _VacuumOxygenBalance(
        pO2_bar=10.0**root,
        pressures_Pa=pressures,
        channel_fluxes_mol_m2_s=fluxes,
        oxygen_atom_flux_mol_m2_s=oxygen_flux,
        parent_oxygen_demand_flux_mol_m2_s=parent_flux,
        relative_residual=relative_residual,
        alpha_sources={
            channel.species: channel.alpha_source for channel in active_channels
        },
    )


def has_own_engine_solved_oxygen_balance(
    engine: Engine | None,
    notices: Sequence[Notice],
) -> bool:
    return engine in OXYGEN_BALANCE_EFFUSION_ENGINES and any(
        notice.kind is NoticeKind.SOURCE_DISAGREEMENT
        and notice.origin == f"engine:{engine.value}"
        and notice.reason.startswith(OXYGEN_BALANCE_NOTICE_PREFIX)
        for notice in notices
    )
