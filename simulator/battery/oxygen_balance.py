"""Shared engine-origin rules for solved oxygen-balance notices."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence

from scipy.optimize import brentq

from simulator.battery.enums import CellMaterial, Engine, NoticeKind
from simulator.battery.records import Located, Notice
from simulator.physical_constants import GAS_CONSTANT
from simulator.vacuum_oxygen_balance import (
    OxygenBalanceBracketBoundary,
    PressureLawChannelDiagnostic,
    PressureLawExponentMismatch,
    _OXYGEN_GAS_ALPHA,
    _OXYGEN_GAS_ALPHA_SOURCE,
    _VacuumOxygenBalance,
    _VacuumOxygenChannel,
    PressureEvaluation,
    RESIDUE_PRESSURE_LAW_REDOX_ACTIVE,
)

# Battery callers still import the shared channel types from this module.
__all__ = [
    "OxygenBalanceBracketBoundary",
    "_OXYGEN_GAS_ALPHA",
    "_OXYGEN_GAS_ALPHA_SOURCE",
    "_VacuumOxygenBalance",
    "_VacuumOxygenChannel",
]

IMCC_ENGINES: frozenset[Engine] = frozenset({Engine.OPENIMCC})
OXYGEN_BALANCE_EFFUSION_ENGINES: frozenset[Engine] = frozenset(
    {
        Engine.OPENIMCC,
        Engine.INTERNAL_ANALYTICAL,
        Engine.VAPOROCK,
        Engine.ALPHAMELTS,
    }
)
OXYGEN_BALANCE_NOTICE_PREFIX = "fo2_oxygen_balance_effusion_solved:"
_MODELLED_REACTIVE_CELL = frozenset({CellMaterial.W, CellMaterial.MO})


def _cell_material_class(
    materials: Sequence[Located[CellMaterial]] | None,
) -> str:
    if not materials:
        return "unknown"
    values: list[CellMaterial] = []
    for material in materials:
        if not material.state.is_value or not isinstance(material.state.value, CellMaterial):
            return "unknown"
        values.append(material.state.value)
    if any(
        material in {
            CellMaterial.W,
            CellMaterial.MO,
            CellMaterial.TA,
            CellMaterial.NB,
            CellMaterial.C_GRAPHITE,
            CellMaterial.RE,
        }
        for material in values
    ):
        return "reactive"
    if all(material in {CellMaterial.PT, CellMaterial.IR} for material in values):
        return "inert"
    return "not_inert"


def _uniform_modelled_reactive_cell(
    materials: Sequence[Located[CellMaterial]] | None,
) -> str | None:
    """W or Mo when every typed entry is that same modelled reservoir metal."""

    if not materials:
        return None
    values: list[CellMaterial] = []
    for material in materials:
        if not material.state.is_value or not isinstance(material.state.value, CellMaterial):
            return None
        values.append(material.state.value)
    if not values:
        return None
    metal = values[0]
    if metal not in _MODELLED_REACTIVE_CELL:
        return None
    if any(item is not metal for item in values):
        return None
    return metal.value


def _cell_oxygen_class(
    materials: Sequence[Located[CellMaterial]] | None,
) -> tuple[str, str | None]:
    """Return material class and the modeled W/Mo oxygen reservoir, if any."""

    material_class = _cell_material_class(materials)
    modelled = (
        _uniform_modelled_reactive_cell(materials)
        if material_class == "reactive"
        else None
    )
    return material_class, modelled


def _net_evaporation_pressure_Pa(pressure_Pa: float, background_pressure_Pa: float) -> float:
    """HKL driving pressure; zero background preserves the vacuum expression."""
    return max(0.0, pressure_Pa - background_pressure_Pa)


def _channel_parent_oxygen_demand(
    channel: _VacuumOxygenChannel,
    model_pressures: Mapping[str, float],
) -> float:
    """Return melt oxygen demand; Fe channels use this evaluation's valence."""
    # o_bar_M = sum_v x_v * v/2 is O atoms per metal atom. Fe(g) releases
    # o_bar and FeO(g) releases o_bar - 1. No q keeps formula stoichiometry.
    q = getattr(model_pressures, "fe3_over_sum_fe", None)
    if (
        q is None
        or channel.oxygen_per_metal_atom is None
        or channel.redox_metal_atoms <= 0.0
    ):
        return channel.parent_oxygen_demand
    return float(channel.oxygen_per_metal_atom(float(q))) * channel.redox_metal_atoms


def _solve_vacuum_oxygen_balance(
    pressure_model: Callable[[float], Mapping[str, float]],
    channels: Sequence[_VacuumOxygenChannel],
    *,
    temperature_K: float,
    background_pressure_Pa: float = 0.0,
    bracket_log10_pO2_bar: tuple[float, float] = (-30.0, 0.0),
    on_accepted_evaluation: Callable[[float], None] | None = None,
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
    if not math.isfinite(background_pressure_Pa) or background_pressure_Pa < 0.0:
        raise ValueError("background_pressure_Pa must be finite and nonnegative")
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
        # Kress has d ln r/d ln pO2=c=0.196 for r=X_Fe2O3/X_FeO and
        # q=2r/(1+2r), hence dq/d ln pO2=c*q*(1-q). In the dilute-Fe limit
        # total melt moles are constant to O(x_Fe), X_FeO is proportional to
        # 1-q, and d ln a_FeO/d ln pO2=-c*q.
        # (A) For FeO(l)=Fe(g)+1/2 O2, p_Fe=K*a_FeO*pO2**-1/2, so the
        # redox-active Fe pressure-law exponent is -1/2-c*q.
        # (B) The demand-weighted Fe(g) term has slope
        # d ln[(1+q/2)*p_Fe]/d ln pO2 = -1/2-c*q+c*q*(1-q)/(2+q)
        # = -1/2-c*q*(1+2*q)/(2+q), first order -1/2-0.098*q. It is
        # negative throughout q in [0,1]: q=0 gives -1/2 and q=1 gives
        # -1/2-c. FeO(g) adds the residual term -q*J_FeO/2 because its
        # oxygen atom leaves with the gas while o_bar=1+q/2 is demanded.
        # These are distinct slopes: (A) is the diagnostic expectation;
        # (B) bounds the Fe(g) demand term in the root residual.
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

    def molar_flux(channel: _VacuumOxygenChannel, pressure: float) -> float:
        # Only the flux uses the bulk-partial ceiling. Keep equilibrium Peq
        # untouched for the power-law validation and thermodynamic root axis.
        drive = (_net_evaporation_pressure_Pa(pressure, background_pressure_Pa)
                 if background_pressure_Pa else pressure)
        return channel.alpha * drive / math.sqrt(
            2.0 * math.pi * GAS_CONSTANT * temperature * channel.molar_mass_kg_per_mol
        )

    def evaluate(
        logp: float, model_pressures: Mapping[str, float] | None = None,
    ) -> tuple[
        float,
        dict[str, float],
        float,
        float,
        float | None,
        dict[str, float],
        float | None,
    ]:
        if model_pressures is None:
            model_pressures = pressure_model(logp)
        if not isinstance(model_pressures, Mapping):
            raise ValueError("pressure model must return a species-to-Pa mapping")
        pressures: dict[str, float] = {}
        demands: dict[str, float] = {}
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
            demand = _channel_parent_oxygen_demand(channel, model_pressures)
            if not math.isfinite(demand) or demand < 0.0:
                raise ValueError(
                    f"parent oxygen demand is invalid for {channel.species!r}"
                )
            demands[channel.species] = demand
            flux = molar_flux(channel, pressure)
            oxygen_flux += channel.oxygen_atoms * flux
            parent_flux += demand * flux
        activity = getattr(model_pressures, "fe_activity", None)
        fe_activity = None if activity is None else float(activity)
        fe_q = getattr(model_pressures, "fe3_over_sum_fe", None)
        fe_q = None if fe_q is None else float(fe_q)
        return oxygen_flux - parent_flux, pressures, oxygen_flux, parent_flux, fe_activity, demands, fe_q

    low_model_pressures = pressure_model(low)
    low_evaluation = evaluate(low, low_model_pressures)
    f_low = low_evaluation[0]
    f_high = evaluate(high)[0]
    low_demands = low_evaluation[5]
    bracket_boundary: OxygenBalanceBracketBoundary | None = None
    parent_channels = [channel for channel in active_channels if channel.parent_oxygen_demand > 0.0]
    zero_oxygen_boundary = background_pressure_Pa == 0.0 and all(
        channel.oxygen_atoms >= low_demands[channel.species]
        and channel.pO2_exponent >= 0.0
        and (
            channel.oxygen_atoms == low_demands[channel.species]
            or channel.pO2_exponent > 0.0
        )
        for channel in parent_channels
    ) and all(
        channel.pO2_exponent > 0.0
        for channel in active_channels if channel.parent_oxygen_demand == 0.0
    )
    if zero_oxygen_boundary:
        # Without an oxygen supplier the root is pO2=0. Neutral oxide gases
        # retain their pressures; oxygen-consuming gases and free O/O2 vanish.
        # Validate every exponent at a finite anchor before taking the limit.
        root = low
    elif f_low == 0.0:
        root = low
        bracket_boundary = "lower"
    elif f_high == 0.0:
        root = high
        bracket_boundary = "upper"
    elif f_low > 0.0 and f_high > 0.0:
        # The residual is oxygen flux minus parent-oxygen demand and increases
        # with pO2. Positive values at both endpoints put the root below the
        # bracket (for example, a root at -40 gives positive residuals at -30
        # and 0), so only this same-sign case holds the lower floor.
        root = low
        bracket_boundary = "lower"
    elif f_low < 0.0 and f_high < 0.0:
        raise ValueError("vacuum oxygen balance has no root in the declared pO2 bracket")
    else:
        root = brentq(
            lambda value: evaluate(value)[0],
            low,
            high,
            xtol=1.0e-12,
            rtol=1.0e-12,
            maxiter=200,
        )

    (
        residual,
        pressures,
        oxygen_flux,
        parent_flux,
        _root_activity,
        root_demands,
        root_q,
    ) = evaluate(root)
    pressure_law_diagnostics: list[PressureLawChannelDiagnostic] = []
    fixed_activities = bool(getattr(pressure_model, "declares_fixed_activities", False))
    redox_active = bool(getattr(pressure_model, "declares_redox_active", False))
    if bracket_boundary != "lower":
        probe_low = max(low, root - 0.1)
        probe_high = root + 0.1
        lower_model_pressures = pressure_model(probe_low)
        upper_model_pressures = pressure_model(probe_high)
        _low_residual, lower_pressures, _low_oxygen, _low_parent, _activity_low, _low_demands, _q_low = evaluate(
            probe_low, lower_model_pressures
        )
        _high_residual, upper_pressures, _high_oxygen, _high_parent, _activity_high, _high_demands, _q_high = evaluate(
            probe_high, upper_model_pressures
        )
        for channel in active_channels:
            pressure = pressures[channel.species]
            if pressure <= 1.0e-300:
                continue
            lower_pressure = lower_pressures[channel.species]
            upper_pressure = upper_pressures[channel.species]
            if lower_pressure <= 0.0 or upper_pressure <= 0.0:
                lower_omissions = getattr(
                    lower_model_pressures, "below_threshold_omissions", {}
                )
                upper_omissions = getattr(
                    upper_model_pressures, "below_threshold_omissions", {}
                )
                lower_omission_proven = (
                    isinstance(lower_omissions, Mapping)
                    and channel.species in lower_omissions
                )
                upper_omission_proven = (
                    isinstance(upper_omissions, Mapping)
                    and channel.species in upper_omissions
                )
                if (
                    (lower_pressure > 0.0 or lower_omission_proven)
                    and (upper_pressure > 0.0 or upper_omission_proven)
                ):
                    continue
                raise ValueError(
                    f"pressure law for {channel.species!r} is not positive around the root"
                )
            observed_exponent = (
                math.log10(upper_pressure) - math.log10(lower_pressure)
            ) / (probe_high - probe_low)
            if fixed_activities or not redox_active:
                # Fixed-activity laws and undeclared callables retain the
                # supplied stoichiometric exponent as a hard contract.
                if abs(observed_exponent - channel.pO2_exponent) > 1.0e-6:
                    raise PressureLawExponentMismatch(
                        f"pressure law exponent mismatch for {channel.species!r}: "
                        f"declared {channel.pO2_exponent}, observed {observed_exponent}"
                    )
                continue
            expected_exponent = channel.pO2_exponent
            if channel.oxygen_per_metal_atom is not None and root_q is not None:
                from simulator.fe_redox import KRESS91_LN_FO2_COEFFICIENT

                expected_exponent -= KRESS91_LN_FO2_COEFFICIENT * root_q
            pressure_law_diagnostics.append(
                PressureLawChannelDiagnostic(
                    species=channel.species,
                    declared=channel.pO2_exponent,
                    observed=observed_exponent,
                    expected=expected_exponent,
                    reason=RESIDUE_PRESSURE_LAW_REDOX_ACTIVE,
                )
            )
            flags = getattr(pressure_model, "engine_flags", None)
            if isinstance(flags, set):
                flags.add(RESIDUE_PRESSURE_LAW_REDOX_ACTIVE)
    if on_accepted_evaluation is not None:
        on_accepted_evaluation(root)

    if zero_oxygen_boundary:
        boundary_pressures = PressureEvaluation(
            {
                channel.species: (
                    0.0 if channel.pO2_exponent > 0.0 else pressures[channel.species]
                )
                for channel in active_channels
            },
            fe3_over_sum_fe=getattr(low_model_pressures, "fe3_over_sum_fe", None),
            fe_activity=getattr(low_model_pressures, "fe_activity", None),
            speciation_source=getattr(low_model_pressures, "speciation_source", None),
            below_threshold_omissions=getattr(
                low_model_pressures, "below_threshold_omissions", None
            ),
        )
        (
            residual,
            pressures,
            oxygen_flux,
            parent_flux,
            _activity,
            root_demands,
            _q,
        ) = evaluate(root, boundary_pressures)
    relative_residual = abs(residual) / max(oxygen_flux, parent_flux, 1.0e-300)
    fluxes = {
        channel.species: molar_flux(channel, pressures[channel.species])
        for channel in active_channels
    }
    return _VacuumOxygenBalance(
        pO2_bar=0.0 if zero_oxygen_boundary else 10.0**root,
        bracket_boundary=bracket_boundary,
        pressures_Pa=pressures,
        channel_fluxes_mol_m2_s=fluxes,
        oxygen_atom_flux_mol_m2_s=oxygen_flux,
        parent_oxygen_demand_flux_mol_m2_s=parent_flux,
        relative_residual=relative_residual,
        alpha_sources={
            channel.species: channel.alpha_source for channel in active_channels
        },
        pressure_law_diagnostics=tuple(pressure_law_diagnostics),
        parent_oxygen_demand_at_root=root_demands,
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
