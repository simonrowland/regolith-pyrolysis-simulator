"""Formal Langmuir / Knudsen analytical flux model.

KEMS is the un-enhanced baseline: ballistic gas transport (Kn >> 1, ``r_gas -> 0``)
and zero induction stirring (``stir=0``, no melt-side renewal). Equilibrium-mode
KEMS measures the thermodynamic driving force ``p_eq``; free-evaporation (Langmuir)
measurements pin the kinetic coefficient ``alpha`` at that same surface.

The furnace model layers validated transport effects on top of this baseline:

* overhead ``pN2`` adds continuum gas-side resistance ``r_gas`` through
  Chapman-Enskog / Sherwood mass transfer when ``Kn < 0.01``; outside that
  regime chamber transmission is represented by the evolved ``p_bulk``, and
* radial stirring can enhance continuum Sherwood transport. The melt term stays
  disabled until species/composition-dependent liquid-transfer inputs exist.

This module exposes the two limits and the three-resistance transition as clean
analytical functions. The series physics is delegated to
:func:`engines.builtin.evaporation_flux._series_resistance_evaporation_flux_kg_m2_s`
— no constants are re-derived here.

Vacuum oxygen atom balances also live here: d-142's monotone pressure-law
policy and the solid-congruence scan share this numerical owner while callers
retain their thermodynamics, channel provenance and notice policy.
"""

from __future__ import annotations

from simulator.transport_regime import (
    SurfaceFlow,
    SURFACE_GAS_CONSTANT_J_MOL_K as GAS_CONSTANT_J_MOL_K,
)

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from scipy.optimize import brentq
from simulator.physical_constants import GAS_CONSTANT

from engines.antoine import _antoine_log10_pressure
from engines.builtin.evaporation_flux import (
    SeriesEvaporationFlux,
    _series_resistance_evaporation_flux_kg_m2_s,
)
from engines.builtin.vapor_pressure import (
    FIT_TARGET_STANDARD_REACTION,
    VaporPressureComputationError,
    vapor_pressure_antoine_coefficients,
)
from simulator.alpha_kinetics import _alpha_s_evaluation, _load_evaporation_alpha_specs
from simulator.yaml_cache import load_cached_safe_yaml
_VAPOR_PRESSURE_GROUPS = ("metals", "oxide_vapors")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_VAPOR_PRESSURES_PATH = _REPO_ROOT / "data" / "vapor_pressures.yaml"
_VALIDATION_SIDECAR_PATH = (
    _REPO_ROOT / "data/literature/langmuir_knudsen_flux_validation.yaml"
)


@dataclass(frozen=True)
class LangmuirKnudsenDiagnostics:
    """Series flux with explicit limit diagnostics."""

    series: SeriesEvaporationFlux
    langmuir_flux_kg_s_m2: float
    knudsen_effusion_flux_kg_s_m2: float

    @property
    def flux_kg_s_m2(self) -> float:
        return self.series.flux_kg_s_m2

    @property
    def knudsen_number(self) -> float:
        return self.series.knudsen_number

    @property
    def r_interface(self) -> float:
        return self.series.r_interface

    @property
    def r_gas(self) -> float:
        return self.series.r_gas

    @property
    def r_melt(self) -> float:
        return self.series.r_melt

    @property
    def gas_resistance_weight(self) -> float:
        return self.series.gas_resistance_weight

    def as_dict(self) -> dict[str, float]:
        return {
            "flux_kg_s_m2": self.flux_kg_s_m2,
            "langmuir_flux_kg_s_m2": self.langmuir_flux_kg_s_m2,
            "knudsen_effusion_flux_kg_s_m2": self.knudsen_effusion_flux_kg_s_m2,
            "knudsen_number": self.knudsen_number,
            "Kn": self.knudsen_number,
            "r_interface": self.r_interface,
            "r_gas": self.r_gas,
            "r_melt": self.r_melt,
            "gas_resistance_weight": self.gas_resistance_weight,
            "k_hk_kg_s_m2_pa": self.series.k_hk_kg_s_m2_pa,
            "alpha_intrinsic": self.series.alpha_intrinsic,
            "alpha_effective": self.series.alpha_effective,
        }


def hertz_knudsen_k_kg_s_m2_pa(T_surface_K: float, molar_mass_kg_mol: float) -> float:
    """Hertz-Knudsen kinetic coefficient ``k_HK`` in kg/(m^2*Pa*s).

    Premise: multiply molar Langmuir flux by molar mass. Algebra:
    ``M / sqrt(2*pi*M*R*T) = sqrt(M/(2*pi*R*T))``. Unit check: the
    coefficient has units ``s/m``, equivalent to ``kg/(m^2*Pa*s)`` because
    ``Pa = kg/(m*s^2)``. Limiting case: multiplying by ``alpha * delta_p``
    recovers the authoritative builtin mass-flux expression exactly.
    """
    return math.sqrt(
        molar_mass_kg_mol
        / (2.0 * math.pi * GAS_CONSTANT_J_MOL_K * T_surface_K)
    )


def knudsen_effusion_molar_flux(
    T_surface_K: float,
    p_eq_pa: float,
    *,
    molar_mass_kg_mol: float,
    p_bulk_pa: float = 0.0,
) -> float:
    """Ideal equilibrium-effusion flux in mol/(m^2*s).

    Premise: equilibrium Maxwellian vapor reaches a unit-area ideal Knudsen
    orifice. Algebra: ``n*v_bar/4`` with ``n=p/(R*T)`` reduces to
    ``J_K=(p_eq-p_bulk)/sqrt(2*pi*M*R*T)``. Unit check:
    ``Pa/sqrt((kg/mol)*(J/mol)) = mol/(m^2*s)``. Limiting case: an ideal
    Clausing factor of one and ``p_bulk -> 0`` gives the equilibrium KEMS
    reference flux; apparatus-specific orifice corrections belong to the
    measurement reduction, not this surface-flux model.
    """
    delta_p = max(0.0, float(p_eq_pa) - float(p_bulk_pa))
    denominator = math.sqrt(
        2.0
        * math.pi
        * molar_mass_kg_mol
        * GAS_CONSTANT_J_MOL_K
        * T_surface_K
    )
    return delta_p / denominator


def langmuir_molar_flux(
    T_surface_K: float,
    p_eq_pa: float,
    p_bulk_pa: float,
    alpha: float,
    *,
    molar_mass_kg_mol: float,
) -> float:
    """Free-evaporation flux in mol/(m^2*s).

    Premise: only fraction ``alpha`` of equilibrium surface crossings escapes.
    Algebra: ``J_L=alpha*J_K`` gives the requested
    ``alpha*(p_eq-p_bulk)/sqrt(2*pi*M*R*T)``. Units remain mol/(m^2*s).
    Limiting cases: ``alpha -> 1`` recovers equilibrium effusion and
    ``alpha -> 0`` stops free evaporation.
    """
    if alpha <= 0.0:
        return 0.0
    return float(alpha) * knudsen_effusion_molar_flux(
        T_surface_K,
        p_eq_pa,
        molar_mass_kg_mol=molar_mass_kg_mol,
        p_bulk_pa=p_bulk_pa,
    )


def langmuir_flux(
    species: str,
    T_surface_K: float,
    p_eq_pa: float,
    p_bulk_pa: float,
    alpha: float,
    *,
    molar_mass_kg_mol: float,
) -> float:
    """Free-evaporation (Langmuir) surface flux in kg/(m^2*s).

    ``J = alpha * max(0, p_eq - p_bulk) * sqrt(M / (2*pi*R*T))``

    This is the KEMS un-enhanced baseline: ballistic gas (``r_gas -> 0``) with
    ``stir=0`` melt renewal off. The evaporation coefficient ``alpha`` is the
    kinetic pin measured in open-sweep / mass-loss experiments.
    """
    _ = species  # species label retained for call-site readability / logging
    molar_flux = langmuir_molar_flux(
        T_surface_K,
        p_eq_pa,
        p_bulk_pa,
        alpha,
        molar_mass_kg_mol=molar_mass_kg_mol,
    )
    return molar_flux * molar_mass_kg_mol


def knudsen_effusion_flux(
    species: str,
    T_surface_K: float,
    p_eq_pa: float,
    *,
    molar_mass_kg_mol: float,
    p_bulk_pa: float = 0.0,
    alpha: float = 1.0,
) -> float:
    """Equilibrium-effusion limit flux in kg/(m^2*s).

    A Knudsen-effusion mass-spectrometry (KEMS) cell holds vapor near ``p_eq``
    and effuses through a small orifice in free-molecular flow. With the
    intrinsic coefficient at unity this reduces to ``J = p_eq * k_HK`` — KEMS
    measures ``p_eq`` (thermodynamics). Free-evaporation (Langmuir) measurements
    at the same surface instead report ``alpha * p_eq`` (kinetics).
    """
    _ = species
    molar_flux = knudsen_effusion_molar_flux(
        T_surface_K,
        p_eq_pa,
        molar_mass_kg_mol=molar_mass_kg_mol,
        p_bulk_pa=p_bulk_pa,
    )
    return float(alpha) * molar_flux * molar_mass_kg_mol


def series_flux(
    species: str,
    p_eq_pa: float,
    p_bulk_pa: float,
    T_surface_K: float,
    molar_mass_kg_mol: float,
    alpha: float,
    *,
    knudsen_number: float | None = None,
    pipe_diameter_m: float = 0.12,
    overhead_pressure_pa: float = 0.0,
    axial_stir_factor: float = 0.0,
    radial_stir_factor: float = 1.0,
    carrier_gas: str = "N2",
    T_gas_K: float | None = None,
    melt_resistance_enabled: bool = False,
    gas_resistance_enabled: bool = True,
    melt_surface_area_m2: float | None = None,
    surface_flow: SurfaceFlow | None = None,
) -> LangmuirKnudsenDiagnostics:
    """Full three-resistance flux with explicit limit diagnostics.

    Premise: interface, gas-film, and melt-renewal conductances can act in
    series only when each is expressed against the same pressure potential.
    Algebra stays delegated to the authoritative helper: ``J=delta_p /
    (r_interface+r_gas+r_melt)``.
    Every resistance has units ``m^2*Pa*s/kg``, so the result is kg/(m^2*s).
    Limiting cases: free-molecular vacuum makes ``r_gas`` vanish, reducing to
    :func:`langmuir_flux` while the ungrounded melt term is disabled;
    ``Kn < 0.01`` enables the continuum gas film.
    """
    series = _series_resistance_evaporation_flux_kg_m2_s(
        species=species,
        P_eq_pa=p_eq_pa,
        P_bulk_pa=p_bulk_pa,
        T_surface_K=T_surface_K,
        molar_mass_kg_mol=molar_mass_kg_mol,
        alpha_i=alpha,
        melt_surface_area_m2=melt_surface_area_m2,
        surface_flow=surface_flow,
        knudsen_number=knudsen_number,
        pipe_diameter_m=pipe_diameter_m,
        overhead_pressure_pa=overhead_pressure_pa,
        axial_stir_factor=axial_stir_factor,
        radial_stir_factor=radial_stir_factor,
        carrier_gas=carrier_gas,
        T_gas_K=T_gas_K,
        melt_resistance_enabled=melt_resistance_enabled,
        gas_resistance_enabled=gas_resistance_enabled,
    )
    langmuir = langmuir_flux(
        species,
        T_surface_K,
        p_eq_pa,
        p_bulk_pa,
        alpha,
        molar_mass_kg_mol=molar_mass_kg_mol,
    )
    knudsen = knudsen_effusion_flux(
        species,
        T_surface_K,
        p_eq_pa,
        molar_mass_kg_mol=molar_mass_kg_mol,
        p_bulk_pa=p_bulk_pa,
        alpha=1.0,
    )
    return LangmuirKnudsenDiagnostics(
        series=series,
        langmuir_flux_kg_s_m2=langmuir,
        knudsen_effusion_flux_kg_s_m2=knudsen,
    )


@lru_cache(maxsize=1)
def _vapor_pressure_data() -> dict[str, Any]:
    payload = load_cached_safe_yaml(_VAPOR_PRESSURES_PATH.read_text()) or {}
    from simulator.vapour_rail.catalog import vapor_pressure_legacy_view

    return vapor_pressure_legacy_view(payload)


def _species_row(species: str) -> dict[str, Any]:
    data = _vapor_pressure_data()
    for group_name in _VAPOR_PRESSURE_GROUPS:
        row = (data.get(group_name) or {}).get(species)
        if isinstance(row, dict):
            return row
    raise KeyError(f"no vapor_pressures.yaml row for species {species!r}")


def species_molar_mass_kg_mol(species: str) -> float:
    row = _species_row(species)
    molar_mass_g_mol = float(row["molar_mass_g_mol"])
    return molar_mass_g_mol / 1000.0


def pseudo_antoine_p_eq_pa(species: str, T_K: float) -> float:
    """Evaluate the builtin pseudo-Antoine ``p_eq`` row used by the provider."""
    row = _species_row(species)
    if str(row.get("fit_target", "") or "") == FIT_TARGET_STANDARD_REACTION:
        raise ValueError(
            f"{species} uses a standard_reaction_term; raw pseudo-Antoine "
            "evaluation would omit melt activity and pO2 context. Use the "
            "builtin vapor-pressure provider for an effective P_eq."
        )
    antoine, _ = vapor_pressure_antoine_coefficients(row, temperature_K=T_K)
    try:
        A, B, C = (float(antoine[key]) for key in ("A", "B", "C"))
    except KeyError as exc:
        raise VaporPressureComputationError(
            "missing_antoine_coefficient: "
            f"species={species} coefficient={exc.args[0]}"
        ) from exc
    except (TypeError, ValueError, OverflowError) as exc:
        raise VaporPressureComputationError(
            f"invalid_antoine_coefficient: species={species}"
        ) from exc

    for name, value in (("A", A), ("B", B), ("C", C)):
        if not math.isfinite(value):
            raise VaporPressureComputationError(
                "nonfinite_antoine_coefficient: "
                f"species={species} coefficient={name} value={value!r}"
            )

    try:
        valid_temperature = math.isfinite(T_K) and T_K > 0.0
    except (TypeError, ValueError, OverflowError) as exc:
        raise VaporPressureComputationError(
            f"invalid_antoine_temperature: species={species} T_K={T_K!r}"
        ) from exc
    if not valid_temperature:
        raise VaporPressureComputationError(
            f"invalid_antoine_temperature: species={species} T_K={T_K!r}"
        )

    log10_p = _antoine_log10_pressure(A, B, C, T_K)
    return 10.0 ** log10_p


def grounded_alpha(species: str, T_K: float) -> tuple[float, dict[str, Any]]:
    """Return YAML-backed alpha using the same ``alpha_s`` path as the provider."""
    alpha_by_species = _load_evaporation_alpha_specs(_vapor_pressure_data())
    alpha_spec = alpha_by_species[species]
    value, evaluation = _alpha_s_evaluation(species, T_K, alpha_spec)
    return value, dict(evaluation)


@dataclass(frozen=True)
class BaselineValidationRow:
    measurement_id: str
    species: str
    regime: str
    T_K: float
    modeled_langmuir_to_effusion_ratio: float
    measured_ratio_center: float
    measured_ratio_low: float
    measured_ratio_high: float
    relative_error_percent: float
    allowed_error_percent: float
    source: str

    def as_dict(self) -> dict[str, float | str]:
        return {
            "measurement_id": self.measurement_id,
            "species": self.species,
            "regime": self.regime,
            "T_K": self.T_K,
            "modeled_langmuir_to_effusion_ratio": (
                self.modeled_langmuir_to_effusion_ratio
            ),
            "measured_ratio_center": self.measured_ratio_center,
            "measured_ratio_low": self.measured_ratio_low,
            "measured_ratio_high": self.measured_ratio_high,
            "relative_error_percent": self.relative_error_percent,
            "allowed_error_percent": self.allowed_error_percent,
            "source": self.source,
        }


def _baseline_validation_rows() -> list[dict[str, Any]]:
    payload = load_cached_safe_yaml(_VALIDATION_SIDECAR_PATH.read_text()) or {}
    measurements = payload.get("measurements") or {}
    return [
        dict(row, measurement_id=measurement_id)
        for measurement_id, row in measurements.items()
    ]


def validate_against_baseline(
    *,
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> list[BaselineValidationRow]:
    """Compare the ballistic model to measured Langmuir/effusion flux ratios.

    The normalized ratio is the measured evaporation coefficient. It preserves
    direct Langmuir-versus-Knudsen evidence without inventing missing aperture
    geometry or hardcoding model-generated absolute flux. Runtime alpha comes
    independently from ``vapor_pressures.yaml``; observations and their stated
    error bars come only from the literature sidecar. No coefficient is fitted.
    """
    validation_rows = (
        list(rows) if rows is not None else _baseline_validation_rows()
    )
    results: list[BaselineValidationRow] = []
    for entry in validation_rows:
        species = str(entry["species"])
        temperature_range = [
            float(value) for value in entry["temperature_range_k"]
        ]
        T_K = sum(temperature_range) / len(temperature_range)
        alpha, _ = grounded_alpha(species, T_K)
        molar_mass = species_molar_mass_kg_mol(species)
        equilibrium_effusion = knudsen_effusion_molar_flux(
            T_K,
            1.0,
            molar_mass_kg_mol=molar_mass,
        )
        free_evaporation = langmuir_molar_flux(
            T_K,
            1.0,
            0.0,
            alpha,
            molar_mass_kg_mol=molar_mass,
        )
        modeled = free_evaporation / equilibrium_effusion
        measured = entry["measured_langmuir_to_effusion_flux_ratio"]
        if "range" in measured:
            low, high = (float(value) for value in measured["range"])
            center = (low + high) / 2.0
        else:
            center = float(measured["value"])
            uncertainty = float(measured["uncertainty_absolute"])
            low, high = center - uncertainty, center + uncertainty
        relative_error = (modeled - center) / center * 100.0
        allowed_error = max(center - low, high - center) / center * 100.0
        source = entry.get("source") or {}
        results.append(
            BaselineValidationRow(
                measurement_id=str(entry.get("measurement_id") or ""),
                species=species,
                regime=str(entry["regime"]),
                T_K=T_K,
                modeled_langmuir_to_effusion_ratio=modeled,
                measured_ratio_center=center,
                measured_ratio_low=low,
                measured_ratio_high=high,
                relative_error_percent=relative_error,
                allowed_error_percent=allowed_error,
                source=str(source.get("citation") or ""),
            )
        )
    return results


__all__ = (
    "BaselineValidationRow",
    "LangmuirKnudsenDiagnostics",
    "grounded_alpha",
    "hertz_knudsen_k_kg_s_m2_pa",
    "knudsen_effusion_flux",
    "knudsen_effusion_molar_flux",
    "langmuir_flux",
    "langmuir_molar_flux",
    "pseudo_antoine_p_eq_pa",
    "series_flux",
    "species_molar_mass_kg_mol",
    "validate_against_baseline",
)


OxygenBalanceBracketBoundary = Literal["lower", "upper"]


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
    bracket_boundary: OxygenBalanceBracketBoundary | None
    pressures_Pa: Mapping[str, float]
    channel_fluxes_mol_m2_s: Mapping[str, float]
    oxygen_atom_flux_mol_m2_s: float
    parent_oxygen_demand_flux_mol_m2_s: float
    relative_residual: float
    alpha_sources: Mapping[str, str]


def _net_evaporation_pressure_Pa(pressure_Pa: float, background_pressure_Pa: float) -> float:
    """HKL driving pressure; zero background preserves the vacuum expression."""
    return max(0.0, pressure_Pa - background_pressure_Pa)


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
    ) -> tuple[float, dict[str, float], float, float]:
        if model_pressures is None:
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
            flux = molar_flux(channel, pressure)
            oxygen_flux += channel.oxygen_atoms * flux
            parent_flux += channel.parent_oxygen_demand * flux
        return oxygen_flux - parent_flux, pressures, oxygen_flux, parent_flux

    f_low = evaluate(low)[0]
    f_high = evaluate(high)[0]
    bracket_boundary: OxygenBalanceBracketBoundary | None = None
    parent_channels = [channel for channel in active_channels if channel.parent_oxygen_demand > 0.0]
    zero_oxygen_boundary = background_pressure_Pa == 0.0 and all(
        channel.oxygen_atoms >= channel.parent_oxygen_demand and channel.pO2_exponent >= 0.0
        and (channel.oxygen_atoms == channel.parent_oxygen_demand or channel.pO2_exponent > 0.0)
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

    residual, pressures, oxygen_flux, parent_flux = evaluate(root)
    if bracket_boundary != "lower":
        probe_low = max(low, root - 0.1)
        probe_high = root + 0.1
        lower_pressures = evaluate(probe_low)[1]
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
            ) / (probe_high - probe_low)
            if abs(observed_exponent - channel.pO2_exponent) > 1.0e-6:
                raise ValueError(
                    f"pressure law exponent mismatch for {channel.species!r}: "
                    f"declared {channel.pO2_exponent}, observed {observed_exponent}"
                )
    if on_accepted_evaluation is not None:
        on_accepted_evaluation(root)

    if zero_oxygen_boundary:
        residual, pressures, oxygen_flux, parent_flux = evaluate(root, {
            channel.species: (
                0.0 if channel.pO2_exponent > 0.0 else pressures[channel.species]
            )
            for channel in active_channels
        })
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
    )


def _solve_congruent_oxygen_balance(
    flux_state: Callable[[float], tuple[Mapping[str, float], float]],
    *,
    gas_names: Sequence[str],
    oxygen_atoms: Mapping[str, float],
    cation_species: frozenset[str],
    condensed_oxygen_count: float,
    scan_min: float,
    scan_max: float,
    material: str,
    temperature: float,
    error_type: type[ValueError],
) -> tuple[float, int, float]:
    """Solid congruence policy, preserving its historical summation and scan.

    The solid activity bounds and thermodynamic state come from the caller.
    This differs from the d-142 monotone power-law policy; substituting that
    residual would change rounding and lose the solid unique-root check.
    """
    def oxygen_residual(oxygen_log: float) -> float:
        fluxes, formula_flux = flux_state(oxygen_log)
        # Premise: one formula unit releases its stoichiometric O atoms.
        # Subtract cation-vapor O and compare the remainder with O + 2 O2.
        # All terms are mol O/(m2 s); their scaled difference is dimensionless
        # and vanishes when condensed oxygen equals the full vapor atom flux.
        oxygen_from_condensed = condensed_oxygen_count * formula_flux
        oxygen_in_cation_vapor = sum(
            oxygen_atoms[name] * fluxes[name]
            for name in gas_names
            if name in cation_species
        )
        oxygen_excess = oxygen_from_condensed - oxygen_in_cation_vapor
        oxygen_effusion = fluxes["O"] + 2.0 * fluxes["O2"]
        scale = abs(oxygen_excess) + abs(oxygen_effusion) + 1.0e-300
        return 2.0 * (oxygen_excess - oxygen_effusion) / scale

    scan_points = [
        scan_min
        + index * (scan_max - scan_min) / 720.0
        for index in range(721)
    ]
    brackets: list[tuple[float, float]] = []
    exact_roots: list[float] = []
    previous_x: float | None = None
    previous_value: float | None = None
    for point in scan_points:
        try:
            value = oxygen_residual(point)
        except error_type:
            previous_x = None
            previous_value = None
            continue
        if not math.isfinite(value):
            previous_x = None
            previous_value = None
            continue
        if abs(value) <= 1.0e-14:
            exact_roots.append(point)
            previous_x = None
            previous_value = None
            continue
        if previous_value is not None and value * previous_value < 0.0:
            brackets.append((previous_x, point))
        previous_x = point
        previous_value = value
    candidate_roots = list(exact_roots)
    for bracket in brackets:
        try:
            candidate_roots.append(
                brentq(
                    oxygen_residual,
                    bracket[0],
                    bracket[1],
                    xtol=1.0e-12,
                    rtol=1.0e-12,
                    maxiter=200,
                )
            )
        except (RuntimeError, ValueError) as exc:
            # Fail loud with typed refusal — never return a bracket mid-point
            # or last residual evaluation (SC-109 completion-from-absence).
            raise error_type(
                f"congruent pO2 root solver failed for {material} at "
                f"{temperature:g} K (bracket={bracket[0]:.6g}..{bracket[1]:.6g}): "
                f"{exc}"
            ) from exc
    unique_roots: list[float] = []
    for root in sorted(candidate_roots):
        if not unique_roots or abs(root - unique_roots[-1]) > 1.0e-8:
            unique_roots.append(root)
    if len(unique_roots) != 1:
        raise error_type(
            f"congruent solve found {len(unique_roots)} physical pO2 roots for "
            f"{material} at {temperature:g} K"
        )
    oxygen_log = unique_roots[0]
    self_buffered_root_count = len(unique_roots)
    residual_norm = abs(oxygen_residual(oxygen_log))
    return oxygen_log, self_buffered_root_count, residual_norm
