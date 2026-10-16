"""
Overhead Gas Model
===================

★ TIER 2: SCIENTIST-READABLE ★

Models the gas composition and pressure above the melt,
and the hot-duct transport implied by caller-supplied melt
pressure and evaporation flux.

This module does not derive pO₂ from turbine speed or route gas
through a condensation train. The caller supplies the melt pO₂
state and the post-routing evaporation flux. For the equilibrium:

    SiO₂(melt) → SiO(g) + ½O₂(g)

the equilibrium solver outside this module applies the pO₂
dependence; this module transports the resulting flux.

Pipe transport (isothermal compressible Poiseuille flow at mbar pressures):
    ṁ = π × d⁴ × M × (P₁² - P₂²) / (256 × η × L × R × T) [PIPE-1]

where:
    d = pipe inner diameter (m)
    M = mole-weighted gas molar mass (kg/mol)
    P₁, P₂ = upstream and downstream pressures (Pa)
    η = gas dynamic viscosity (Pa·s)
    L = pipe length (m)
    R = ideal-gas constant (Pa·m³/(mol·K))
    T = gas temperature (K)

Premise: steady isothermal ideal-gas flow conserves molar flow while
volumetric flow varies as 1/P. Integrating circular Poiseuille flow
therefore replaces the incompressible pressure difference with
(P₁²-P₂²)/2, giving the denominator 256. Unit check:
[m⁴·Pa²·kg/mol]/[(Pa·s)·m·(Pa·m³/(mol·K))·K] = kg/s.
Sanity check: d=0.12 m, L=1 m, T=1773.15 K, pure SiO, P₁=1000 Pa,
and P₂=0 gives 0.121878 kg/s; P₁=P₂ gives zero flow.

Feedback quantities exposed:
    [LOOP-1]  Backpressure: overhead partial pressures feed back as
              P_ambient in the HK equation (handled in core.py)
    [LOOP-3]  Transport saturation: evap rate / pipe conductance
              feeds back to throttle ΔT/dt (handled in core.py)

Turbine reporting fields are reset here for downstream accounting code to
populate; turbine speed/capacity is not an input to this model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Optional

from simulator.state import (
    GAS_CONSTANT,  # R: J/(mol·K)
    MOLAR_MASS,  # g/mol
    CondensationTrain,
    EvaporationFlux,
    MeltState,
    OverheadGas,
)
# Single-source the pipe-temperature default from condensation (canonical
# pipe-defaults home; cycle-safe — condensation doesn't import overhead) [BUG-052].
from simulator.condensation import DEFAULT_PIPE_TEMPERATURE_C  # °C — default pipe/liner temperature
from engines.builtin.overhead_bleed import (
    BuiltinOverheadBleedProvider,
    EffectiveTransportCapacity,
    controlled_flow_capacity,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET  # K — Celsius-to-Kelvin offset
from simulator.scalar_boundary import is_declared_real_scalar

O2_KG_PER_MOL = MOLAR_MASS['O2'] / 1000.0  # kg/mol — O2 molar mass; g/mol -> kg/mol

# 0.5.4 W7 (CW5 historical-audit closure, 2026-05-28): default
# mean molar mass for the pipe-conductance ideal-gas density when
# the live species mixture is unavailable (e.g., zero-flux warmup
# tick or a legacy test calling ``_pipe_conductance`` directly).
# 0.040 kg/mol matches the historical hardcoded value ("mix of SiO,
# Fe, Na vapors ~40 g/mol"), preserved as the fallback so the
# fail-closed path keeps the pre-W7 behaviour bit-identical. Real
# recipe runs span M_avg ≈ 0.023-0.046 kg/mol (alkali sweep early
# → SiO mid → O2 late) — that's a factor-of-2 in conductance, so
# the live mixture is the canonical source going forward.
DEFAULT_PIPE_M_AVG_KG_MOL = 0.040  # kg/mol — fallback mole-weighted pipe gas molar mass
# DERIVATION: premise — steady isothermal ideal-gas flow keeps molar flow
# constant while local volumetric flow varies as 1/P. Integrating circular
# Poiseuille along the pipe gives pV throughput
# π*d^4*(P1^2-P2^2)/(256*eta*L): the incompressible 128 denominator gains
# the factor 2 from P1^2-P2^2 = 2*p_mean*(P1-P2). Dividing by R*T and
# multiplying by molar mass M gives kg/s; units are
# [m^4*Pa^2*kg/mol]/[(Pa*s)*m*(Pa*m^3/(mol*K))*K] = kg/s. Worked check:
# d=0.08 m, L=2 m, T=1473.15 K, M(SiO)=0.0440845 kg/mol and m_dot=0.003
# kg/s require P1=426.45 Pa against vacuum; the forward law returns
# 0.003000 kg/s. Both capacity and pressure inversion use this one factor.
COMPRESSIBLE_POISEUILLE_DENOMINATOR = 256.0  # dimensionless — integrated mean-pressure factor
DEFAULT_INITIAL_THROAT_AREA_M2 = math.pi * 0.06**2  # m² — 12 cm diameter throat back-compat anchor
DEFAULT_CONDENSER_STAGE_AREA_RATIOS = {
    'fe_stage1': 4.0,
    'cr_stage2': 4.5,
    'sio_stage3': 6.0,
    'alkali_stage4': 5.0,
    'terminal': 2.0,
}  # dimensionless — physical area / throat area; downstream stages >= throat
DEFAULT_CONDENSER_STAGE_AREA_RATIO_SOURCES = {
    'fe_stage1': (
        'engineering-default: baffled Fe condenser presents multiple throat '
        'areas downstream of the constriction'
    ),
    'cr_stage2': (
        'engineering-default: removable Cr oxide cartridge sits between Fe '
        'condenser and SiO baffles with intermediate capture surface'
    ),
    'sio_stage3': (
        'engineering-default: removable SiO baffle cartridge needs the '
        'largest capture surface among named stages'
    ),
    'alkali_stage4': (
        'engineering-default: cyclone separator effective wall area exceeds '
        'throat but stays below SiO baffle cartridge'
    ),
    'terminal': (
        'engineering-default: terminal vent/capture area is not the '
        'constriction; keep >= throat for follow-on Kn diagnostics'
    ),
}  # provisional source notes for default condenser stage-area ratios
_STAGE_AREA_ALIASES = {
    1: 'fe_stage1',
    '1': 'fe_stage1',
    'stage1': 'fe_stage1',
    'stage_1': 'fe_stage1',
    'fe': 'fe_stage1',
    2: 'cr_stage2',
    '2': 'cr_stage2',
    'stage2': 'cr_stage2',
    'stage_2': 'cr_stage2',
    'cr': 'cr_stage2',
    'chromium': 'cr_stage2',
    3: 'sio_stage3',
    '3': 'sio_stage3',
    'stage3': 'sio_stage3',
    'stage_3': 'sio_stage3',
    'sio': 'sio_stage3',
    4: 'alkali_stage4',
    '4': 'alkali_stage4',
    'stage4': 'alkali_stage4',
    'stage_4': 'alkali_stage4',
    'alkali': 'alkali_stage4',
    7: 'terminal',
    '7': 'terminal',
    'stage7': 'terminal',
    'stage_7': 'terminal',
    'terminal': 'terminal',
}


class OverheadConfigurationError(ValueError):
    """Invalid overhead configuration input."""


def canonical_stage_area_key(stage: Any) -> str:
    """Canonical stage-area ratio key."""

    return _STAGE_AREA_ALIASES.get(
        stage, _STAGE_AREA_ALIASES.get(str(stage), str(stage)))


def _config_value(value: Any) -> Any:
    if isinstance(value, Mapping) and 'value' in value:
        return value.get('value')
    return value


def _required_positive_finite_float(
    value: Any,
    field_name: str,
    *,
    minimum: float = 0.0,
) -> float:
    try:
        raw = _config_value(value)
        if not is_declared_real_scalar(raw, allow_numeric_str=True):
            raise TypeError
        result = float(raw)
    except (TypeError, ValueError) as exc:
        raise OverheadConfigurationError(
            f'{field_name} must be a positive finite value'
        ) from exc
    if not math.isfinite(result) or result <= 0.0 or result < minimum:
        if minimum > 0.0:
            raise OverheadConfigurationError(
                f'{field_name} values must be finite and >= {minimum:g}'
            )
        raise OverheadConfigurationError(
            f'{field_name} must be a positive finite value'
        )
    return result


def _required_finite_float(value: Any, field_name: str) -> float:
    """Return a declared real scalar or refuse a non-finite runtime state."""

    try:
        raw = _config_value(value)
        if not is_declared_real_scalar(raw, allow_numeric_str=True):
            raise TypeError
        result = float(raw)
    except (TypeError, ValueError) as exc:
        raise OverheadConfigurationError(
            f'{field_name} must be finite'
        ) from exc
    if not math.isfinite(result):
        raise OverheadConfigurationError(f'{field_name} must be finite')
    return result


def _required_finite_nonnegative_float(value: Any, field_name: str) -> float:
    """Return a finite nonnegative scalar or refuse the runtime state."""

    result = _required_finite_float(value, field_name)
    if result < 0.0:
        raise OverheadConfigurationError(
            f'{field_name} values must be finite and >= 0'
        )
    return result


def validate_condenser_geometry_config(config: Mapping | None) -> dict[str, Any]:
    """Fail closed and canonicalize condenser geometry before model use."""

    if config is None:
        return {}
    if not isinstance(config, Mapping):
        raise OverheadConfigurationError('condenser_geometry must be a mapping')

    resolved = dict(config)
    if 'initial_throat_area_m2' in resolved:
        _required_positive_finite_float(
            resolved['initial_throat_area_m2'],
            'condenser_geometry.initial_throat_area_m2',
        )

    raw_ratios = resolved.get('stage_area_ratios', {})
    if not isinstance(raw_ratios, Mapping):
        raise OverheadConfigurationError(
            'condenser_geometry.stage_area_ratios must be a mapping'
        )
    ratios: dict[str, Any] = {}
    for raw_stage, raw_ratio in raw_ratios.items():
        stage = _stage_area_key(raw_stage)
        ratio = _required_positive_finite_float(
            raw_ratio,
            'condenser_geometry.stage_area_ratios',
            minimum=1.0,
        )
        ratios[stage] = ratio
    resolved['stage_area_ratios'] = ratios

    raw_sources = resolved.get('stage_area_ratio_sources')
    if raw_sources is not None:
        if not isinstance(raw_sources, Mapping):
            raise OverheadConfigurationError(
                'condenser_geometry.stage_area_ratio_sources must be a mapping'
            )
        resolved['stage_area_ratio_sources'] = {
            _stage_area_key(stage): source
            for stage, source in raw_sources.items()
        }
    return resolved


def _stage_area_key(stage: Any) -> str:
    return canonical_stage_area_key(stage)


def _stage_area_ratio_provenance_record(
    stage: str,
    source: Any,
    *,
    ratio: float,
) -> dict[str, Any]:
    """Normalize a stage-area source note into the local provenance shape."""

    if isinstance(source, Mapping):
        record = dict(source)
    else:
        source_text = str(source or '').strip()
        if not source_text:
            source_text = (
                'configuration: stage-area ratio override without explicit '
                'source; treat as provisional until geometry is certified'
            )
        source_class = source_text.split(':', 1)[0].strip() or 'configuration'
        is_provisional = (
            'engineering-default' in source_text.lower()
            or 'provisional' in source_text.lower()
            or source_class == 'configuration'
        )
        record = {
            'source': source_text,
            'source_class': source_class,
            'status': 'provisional' if is_provisional else 'sourced',
            'output_status': (
                'status_bearing' if is_provisional else 'sourced'
            ),
        }
    record.setdefault('stage', stage)
    record.setdefault('ratio', float(ratio))
    record.setdefault('usage', 'condensation_surface_area')
    record.setdefault(
        'message',
        'Condenser stage wall-deposit surface area follows the configured '
        'stage-area ratio; provisional geometry makes deposit/coating '
        'readouts status-bearing, not refused.',
    )
    return record


def _mean_molar_mass_kg_mol(
    species_kg: Optional[Mapping[str, float]],
    *,
    fallback_engagement_recorder: Optional[Callable[[], None]] = None,
    species_formula_registry: Optional[Mapping[str, Any]] = None,
) -> float:
    """Mole-weighted mean molar mass of a species mixture, kg/mol.

    Computed as ``Σ(kg_i) / Σ(kg_i / M_i)`` — the molar-fraction-
    weighted average molar mass, equivalent to ``1 / Σ(w_i / M_i)``
    where ``w_i`` is the mass fraction of species i. The ideal-gas
    density ``ρ = p M / (R T)`` uses this M_avg directly (M is the
    mole-weighted average molar mass for a gas mixture).

    Used by ``OverheadGasModel._pipe_conductance`` to derive the live
    pipe-conductance density from the actual gas composition (or
    incoming evaporation flux) instead of the legacy hardcoded
    ~0.040 kg/mol guess.

    Args:
        species_kg: mapping of species symbol → mass (kg). Per-tick
            evap flux ``species_kg_hr`` or per-tick gas inventory
            both shape correctly here — only the ratios matter, the
            time unit cancels. ``None`` or empty mapping → fall
            back to ``DEFAULT_PIPE_M_AVG_KG_MOL`` (preserves the
            pre-W7 behaviour for zero-flux warmup ticks and any
            legacy caller that didn't pass species).

    Returns:
        Mean molar mass in kg/mol, or the documented fallback when
        the mixture is empty / unresolvable. There is no fixed numeric
        domain: any finite positive molar mass resolved from ``MOLAR_MASS``
        or ``species_formula_registry`` is eligible. For sanity, pure He
        returns 0.004002602 kg/mol, while a registered 300 g/mol species
        returns 0.300 kg/mol. Species unresolved by both sources are skipped
        rather than contaminating the denominator. The result is always
        finite and positive.
    """
    if not species_kg:
        if fallback_engagement_recorder is not None:
            fallback_engagement_recorder()
        return DEFAULT_PIPE_M_AVG_KG_MOL
    total_kg = 0.0  # kg — summed species mass
    total_mol = 0.0  # mol — summed species amount
    for species, kg in species_kg.items():  # kg — species mass or mass-rate basis
        try:
            mass = float(kg)  # kg — finite species mass contribution
        except (TypeError, ValueError):
            continue
        if not math.isfinite(mass) or mass <= 0.0:
            continue
        molar_mass_g_mol = _species_molar_mass_g_mol(
            species, species_formula_registry
        )
        if molar_mass_g_mol is None or molar_mass_g_mol <= 0.0:
            # Unknown species — skip rather than poison the mean.
            # The fallback covers the "all unknown" degenerate case
            # via total_mol == 0 below.
            continue
        total_kg += mass
        total_mol += mass / (molar_mass_g_mol / 1000.0)  # mol — g/mol -> kg/mol
    if total_mol <= 0.0 or total_kg <= 0.0:
        if fallback_engagement_recorder is not None:
            fallback_engagement_recorder()
        return DEFAULT_PIPE_M_AVG_KG_MOL
    return total_kg / total_mol


def _species_molar_mass_g_mol(
    species: str,
    species_formula_registry: Optional[Mapping[str, Any]] = None,
) -> float | None:
    """Resolve legacy or catalog-generated gas molar mass in g/mol."""

    value = MOLAR_MASS.get(species)
    if value is None and species_formula_registry:
        formula = species_formula_registry.get(species)
        if formula is not None:
            if isinstance(formula, Mapping):
                value = formula.get("molar_mass_g_mol")
            else:
                value = getattr(formula, "molar_mass_g_mol", None)
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) and numeric > 0.0 else None


class OverheadGasModel:
    """
    Calculates gas composition and flow conditions above the melt.

    Updates the OverheadGas state each simulation hour based on
    evaporation flux, caller-supplied atmosphere settings, pipe geometry,
    and an optional effective transport-capacity result.
    """

    DEFAULT_HEADSPACE_CONFIG = {
        # 0.5.3 Phase A1 (2026-05-28): default-on global flip. Hard-vacuum
        # P_ambient=0 is no longer the default — every campaign now sees the
        # finite-headspace backpressure floor (per docs-private/goal-finite-
        # headspace-2026-05-21.md Q1 pinned decision). Synthetic O2 floor from
        # melt.pO2_mbar is re-applied below in _update_finite_headspace so a
        # controlled-O2 recipe setpoint is preserved across the holdup-derived
        # partial-pressure overwrite.
        'enabled': True,  # dimensionless bool — finite-headspace model switch
        'volume_m3': None,  # m³ — finite headspace gas volume override
        'temperature_model': 'melt',  # unitless — headspace temperature model selector
        'temperature_offset_K': None,  # K — headspace temperature offset from melt
        'bleed_model': 'poiseuille',  # unitless — finite-headspace bleed model selector
        'conductance_kg_s': None,  # kg/s — finite-headspace bleed mass-flow override
        'conductance_kg_s_per_bar': None,  # deprecated compatibility alias; kg/s, not per-bar
        'downstream_pressure_bar': None,  # bar — downstream/reference pressure override
        'liner_temperature_C': DEFAULT_PIPE_TEMPERATURE_C,  # °C — default liner/pipe wall temperature
        'pipe_segment_temperatures_C': None,  # °C — per-pipe-segment wall temperature schedule
    }

    def __init__(
        self,
        headspace_config: Optional[Mapping] = None,
        condenser_geometry_config: Optional[Mapping] = None,
        *,
        degraded_path_engagement_recorder: Optional[Callable[..., None]] = None,
        species_formula_registry: Optional[Mapping[str, Any]] = None,
    ):
        # Pipe geometry (default for 1-tonne batch)
        self.initial_throat_area_m2 = DEFAULT_INITIAL_THROAT_AREA_M2  # m² — configurable throat cross-section
        self.pipe_diameter_m = self.throat_diameter_m  # m — throat-equivalent inner diameter
        self.pipe_length_m = 1.0         # m — crucible-to-first-condenser pipe length
        self.stage_area_ratios = dict(DEFAULT_CONDENSER_STAGE_AREA_RATIOS)  # dimensionless — stage area / throat area
        self.stage_area_ratio_provenance_by_stage = {
            stage: _stage_area_ratio_provenance_record(
                stage,
                source,
                ratio=DEFAULT_CONDENSER_STAGE_AREA_RATIOS[stage],
            )
            for stage, source in DEFAULT_CONDENSER_STAGE_AREA_RATIO_SOURCES.items()
        }
        self._pipe_temperature_C = DEFAULT_PIPE_TEMPERATURE_C  # °C — active pipe/liner wall temperature
        self._liner_temperature_config: Any = DEFAULT_PIPE_TEMPERATURE_C  # °C or schedule — liner temperature config
        self._pipe_segment_temperature_config: Any = None  # °C or mapping — per-segment wall temperature config
        self._degraded_path_engagement_recorder = (
            degraded_path_engagement_recorder
        )
        self.species_formula_registry = species_formula_registry or {}
        self.configure_condenser_geometry(condenser_geometry_config or {})
        self.configure_headspace(headspace_config or {})

    def _record_pipe_m_avg_fallback_engagement(self) -> None:
        if self._degraded_path_engagement_recorder is not None:
            self._degraded_path_engagement_recorder(
                'pipe_m_avg_fallback',
                count=1,
            )

    @property
    def pipe_temperature_C(self) -> float:
        """Current liner temperature after applying the active recipe schedule."""

        return float(self._pipe_temperature_C)

    @pipe_temperature_C.setter
    def pipe_temperature_C(self, value: float) -> None:  # °C — requested pipe/liner wall temperature
        self._pipe_temperature_C = max(0.0, float(value))  # °C — clamped pipe/liner wall temperature
        self._liner_temperature_config = self._pipe_temperature_C  # °C — scalar liner temperature config
        self._pipe_segment_temperature_config = self._pipe_temperature_C  # °C — scalar segment temperature config

    @property
    def throat_diameter_m(self) -> float:
        """Diameter of a circular throat with ``initial_throat_area_m2``."""

        area = max(0.0, float(self.initial_throat_area_m2))  # m² — throat cross-section
        if area <= 0.0:
            return 0.0
        return math.sqrt(4.0 * area / math.pi)  # m — circular equivalent diameter

    def configure_condenser_geometry(self, config: Mapping) -> None:
        """Resolve throat area and per-stage area ratios from setpoints."""

        source = validate_condenser_geometry_config(config)
        throat_area = self._optional_positive_float(
            self._config_value(source.get('initial_throat_area_m2')),
            DEFAULT_INITIAL_THROAT_AREA_M2,
        )  # m² — user-exposed throat cross-section
        self.initial_throat_area_m2 = throat_area
        self.pipe_diameter_m = self.throat_diameter_m  # m — derived back-compat pipe diameter

        ratios = dict(DEFAULT_CONDENSER_STAGE_AREA_RATIOS)
        provenance = {
            stage: _stage_area_ratio_provenance_record(
                stage,
                DEFAULT_CONDENSER_STAGE_AREA_RATIO_SOURCES.get(stage),
                ratio=ratio,
            )
            for stage, ratio in ratios.items()
        }
        raw_ratios = source.get('stage_area_ratios', {})
        if isinstance(raw_ratios, Mapping):
            for raw_stage, raw_ratio in raw_ratios.items():
                stage = _stage_area_key(raw_stage)
                ratio = self._optional_positive_float(
                    self._config_value(raw_ratio),
                    ratios.get(stage, 1.0),
                )
                ratios[stage] = ratio
                default_ratio = DEFAULT_CONDENSER_STAGE_AREA_RATIOS.get(stage)
                if (
                    default_ratio is None
                    or not math.isclose(
                        ratio, default_ratio, rel_tol=1e-12, abs_tol=0.0)
                ):
                    provenance[stage] = _stage_area_ratio_provenance_record(
                        stage, None, ratio=ratio)
                elif stage not in provenance:
                    provenance[stage] = _stage_area_ratio_provenance_record(
                        stage, None, ratio=ratio)
                else:
                    provenance[stage] = dict(provenance[stage])
                    provenance[stage]['ratio'] = ratio
        raw_sources = source.get('stage_area_ratio_sources', {})
        if isinstance(raw_sources, Mapping):
            for raw_stage, raw_source in raw_sources.items():
                stage = _stage_area_key(raw_stage)
                provenance[stage] = _stage_area_ratio_provenance_record(
                    stage,
                    raw_source,
                    ratio=ratios.get(stage, 1.0),
                )
        self.stage_area_ratios = ratios
        self.stage_area_ratio_provenance_by_stage = provenance

    def stage_area_m2(self, stage: Any) -> float:
        """Physical area for a named condenser/volatiles stage."""

        key = _stage_area_key(stage)
        ratio = self.stage_area_ratios[key]
        return self.initial_throat_area_m2 * ratio  # m² — throat area × dimensionless ratio

    def stage_area_m2_by_stage(self) -> dict[str, float]:
        """All configured stage physical areas, keyed by stage label."""

        return {
            stage: self.stage_area_m2(stage)
            for stage in self.stage_area_ratios
        }

    def stage_area_geometry_provenance_notice(self) -> dict[str, Any]:
        """Provenance payload for stage-area geometry used by wall deposits."""

        records = {
            stage: dict(record)
            for stage, record in self.stage_area_ratio_provenance_by_stage.items()
        }
        status_bearing = {
            stage: record
            for stage, record in records.items()
            if str(record.get('output_status', '')).lower() == 'status_bearing'
            or str(record.get('status', '')).lower() in {'provisional', 'proxy'}
            or str(record.get('source_class', '')).lower() == 'engineering-default'
        }
        if not records:
            return {}
        return {
            'severity': 'warning' if status_bearing else 'info',
            'code': 'wall_deposit_surface_geometry_provenance',
            'source_class': (
                'engineering-default'
                if any(
                    str(record.get('source_class', '')).lower()
                    == 'engineering-default'
                    for record in status_bearing.values()
                )
                else 'configured_geometry'
            ),
            'status': 'provisional' if status_bearing else 'sourced',
            'output_status': (
                'status_bearing' if status_bearing else 'sourced_with_surface_proxy'
            ),
            'provisional': bool(status_bearing),
            'usage': [
                'stage_area_m2_by_stage',
                'PipeSegment.declared_area_m2',
                'PipeSegment.surface_area_m2',
                'wall_deposit_candidate_for_surface',
                'coating_lifespan',
            ],
            'stage_area_ratio_provenance_by_stage': records,
            'message': (
                'Condenser stage-area ratios include provisional/default '
                'geometry; wall-deposit and coating readouts are status-bearing '
                'until stage surface areas are certified.'
            ),
        }

    def configure_headspace(self, config: Mapping) -> None:
        merged = dict(self.DEFAULT_HEADSPACE_CONFIG)
        merged.update(dict(config or {}))
        enabled = merged.get('enabled', False)
        if not isinstance(enabled, bool):
            raise OverheadConfigurationError('headspace.enabled must be bool')
        self._finite_headspace_enabled = enabled  # dimensionless bool — finite-headspace switch
        self._headspace_volume_m3 = merged.get('volume_m3')  # m³ or None — finite headspace volume
        if self._headspace_volume_m3 is not None:
            self._headspace_volume_m3 = _required_positive_finite_float(
                self._headspace_volume_m3,
                'headspace.volume_m3',
            )
        self._temperature_model = str(merged.get('temperature_model') or 'melt')  # unitless — headspace temperature basis
        self._temperature_offset_K = merged.get('temperature_offset_K')  # K or None — headspace temperature offset
        self._bleed_model = str(merged.get('bleed_model') or 'poiseuille')  # unitless — bleed conductance model
        self._conductance_override = merged.get('conductance_kg_s')  # kg/s — finite-headspace bleed mass-flow override
        if self._conductance_override is None:
            self._conductance_override = merged.get('conductance_kg_s_per_bar')  # kg/s — deprecated compatibility alias
        self._downstream_pressure_override = merged.get('downstream_pressure_bar')  # bar or None — downstream pressure override
        self._liner_temperature_config = merged.get(  # °C or schedule — liner temperature config
            'liner_temperature_C',
            merged.get('pipe_temperature_C', DEFAULT_PIPE_TEMPERATURE_C),
        )
        self._pipe_segment_temperature_config = merged.get(  # °C or mapping — per-segment wall temperature config
            'pipe_segment_temperatures_C',
            self._liner_temperature_config,
        )
        self._pipe_temperature_C = self.resolve_pipe_temperature_C()  # °C — resolved active pipe/liner wall temperature

    def resolve_pipe_temperature_C(self, melt: Optional[MeltState] = None) -> float:
        """Resolve scalar or scheduled liner temperature for the current tick."""

        value = self._resolve_liner_temperature_value(  # °C — resolved liner wall temperature
            self._liner_temperature_config,
            melt,
        )
        self._pipe_temperature_C = max(0.0, float(value))  # °C — clamped pipe/liner wall temperature
        return self._pipe_temperature_C

    def resolve_pipe_segment_temperatures_C(
        self,
        segment_names: list[str] | tuple[str, ...],
        melt: Optional[MeltState] = None,
    ) -> dict[str, float]:
        """Resolve per-segment wall temperatures for the active recipe."""

        base_C = self.resolve_pipe_temperature_C(melt)  # °C — scalar fallback pipe/liner wall temperature
        config = self._pipe_segment_temperature_config  # °C or mapping — segment temperature config
        if config in (None, ''):
            return {str(name): base_C for name in segment_names}
        if isinstance(config, (int, float)) or not isinstance(config, Mapping):
            value = self._resolve_liner_temperature_value(config, melt)  # °C — scalar segment wall temperature
            return {str(name): max(0.0, float(value)) for name in segment_names}
        if 'segments' not in config:
            value = self._resolve_liner_temperature_value(config, melt)  # °C — mapping-derived wall temperature
            return {str(name): max(0.0, float(value)) for name in segment_names}

        default_config = config.get('default_C', base_C)  # °C or schedule — default segment temperature source
        default_C = self._resolve_liner_temperature_value(default_config, melt)  # °C — default segment wall temperature
        segments = config.get('segments', {}) or {}  # °C mapping — per-segment wall temperature configs
        if not isinstance(segments, Mapping):
            segments = {}  # °C mapping — empty per-segment wall temperature configs

        resolved: dict[str, float] = {}  # °C by segment — resolved wall temperatures
        for raw_name in segment_names:
            name = str(raw_name)
            segment_config = segments.get(name)  # °C or schedule — segment wall temperature source
            if segment_config is None:
                resolved[name] = max(0.0, float(default_C))  # °C — default segment wall temperature
            else:
                resolved[name] = max(  # °C — resolved segment wall temperature
                    0.0,
                    float(self._resolve_liner_temperature_value(
                        segment_config, melt)),
                )
        return resolved

    def estimate_transport_state(
        self,
        evap_flux: EvaporationFlux,
        melt: MeltState,
        p_downstream_bar: Optional[float] = None,
        effective_transport_capacity: Optional[EffectiveTransportCapacity] = None,
    ) -> dict[str, float]:
        """Estimate pipe pressure/capacity with the existing Poiseuille model."""

        conductance_temperature_C = _required_finite_float(
            melt.temperature_C,
            'melt.temperature_C',
        )  # °C — gas temperature used for pipe conductance
        if conductance_temperature_C + CELSIUS_TO_KELVIN_OFFSET <= 0.0:
            raise OverheadConfigurationError(
                'melt.temperature_C must be above absolute zero'
            )
        total_evap_kg_hr = _required_finite_nonnegative_float(
            evap_flux.total_kg_hr,
            'evap_flux.total_kg_hr',
        )  # kg/hr — total evaporation mass flow
        allowed_pressure_mbar = _required_finite_nonnegative_float(
            melt.p_total_mbar,
            'melt.p_total_mbar',
        )  # mbar — caller-supplied total-pressure ceiling
        pipe_temperature_C = self.resolve_pipe_temperature_C(melt)  # °C — active pipe/liner wall temperature
        allowed_pressure_Pa = max(allowed_pressure_mbar * 100.0, 1.0)  # Pa — allowed upstream pressure; mbar -> Pa with 1 Pa floor
        controlled_flow = effective_transport_capacity is not None
        downstream_pressure_Pa = (
            _required_finite_nonnegative_float(
                effective_transport_capacity.downstream_pressure_bar,
                'effective_transport_capacity.downstream_pressure_bar',
            ) * 1.0e5
            if controlled_flow
            else self._resolve_downstream_pressure(melt, p_downstream_bar) * 1.0e5
        )  # Pa — derived or explicit downstream/reference pressure; bar -> Pa
        # Preserve the existing gas-transport path: Poiseuille conductance has
        # historically used melt/gas temperature. The liner trajectory controls
        # wall deposition and Kn diagnostics without changing evaporation totals.
        # 0.5.4 W7 (CW5): pass the live evap-flux mixture so the
        # ideal-gas density in ``_pipe_conductance`` uses the actual
        # mole-weighted M_avg (~0.023-0.046 kg/mol across a recipe)
        # rather than the legacy 0.040 hardcoded magic number.
        # ``evap_flux.species_kg_hr`` is the steady-state pipe
        # composition; the time unit cancels in the mole-fraction
        # weighting.
        if controlled_flow:
            pipe_conductance_kg_hr = _required_finite_nonnegative_float(
                effective_transport_capacity.effective_capacity_kg_hr,
                'effective_transport_capacity.effective_capacity_kg_hr',
            )
            conductance = pipe_conductance_kg_hr / 3600.0
            vapor_pressure_mbar = allowed_pressure_Pa / 100.0
        else:
            conductance = self._pipe_conductance(  # kg/s — pipe mass-flow capacity at allowed upstream/downstream pressures
                allowed_pressure_Pa,
                conductance_temperature_C,
                p_downstream_Pa=downstream_pressure_Pa,
                species_kg_for_M_avg=evap_flux.species_kg_hr,  # kg/hr by species — composition basis for M_avg
            )
            pipe_conductance_kg_hr = conductance * 3600.0  # kg/hr — capacity at allowed pressure; kg/s -> kg/hr
        if not controlled_flow and conductance > 0.0:
            vapor_pressure_mbar = self._vapor_pressure_mbar_from_flux(  # mbar — steady-state throughput pressure, sqrt(Poiseuille balance)
                total_evap_kg_hr / 3600.0,  # kg/s — evaporation mass flow
                conductance_temperature_C,
                p_downstream_bar=downstream_pressure_Pa / 1.0e5,
                species_kg_for_M_avg=evap_flux.species_kg_hr,
            )
        elif not controlled_flow:
            # Premise: zero forward conductance is a closed-line condition,
            # not zero vapor pressure; evolved gas fills the upstream volume
            # until the allowed headspace pressure is reached and backpressure
            # suppresses the next tick's net HKL source.  Algebra is the closed
            # boundary P_vapor=P_up.  Unit check: Pa/100=mbar.  Limits: any
            # positive conductance uses the Poiseuille inversion above; at
            # P_down->P_up this full-upstream state makes the following live
            # flux (and therefore saturation) recover instead of relatching.
            vapor_pressure_mbar = allowed_pressure_Pa / 100.0
        pressure_mbar = max(vapor_pressure_mbar, allowed_pressure_mbar)  # mbar — reported total overhead pressure
        if controlled_flow:
            pipe_capacity_used_pct = (
                effective_transport_capacity.saturation * 100.0
            )
        elif total_evap_kg_hr <= 0.0:
            pipe_capacity_used_pct = 0.0
        elif pipe_conductance_kg_hr > 0.0:
            pipe_capacity_used_pct = (
                total_evap_kg_hr / pipe_conductance_kg_hr * 100.0
            )  # percent — load / capacity at allowed upstream pressure
        else:
            pipe_capacity_used_pct = math.inf
        # Premise: PIPE-1 mass flow is C * max(P_up^2 - P_down^2, 0) with C
        # independent of pressure. The value below is that capacity at the
        # allowed upstream pressure (the p_mean_Pa compatibility key in this
        # dict) and the resolved downstream pressure.
        # Algebra: treating the arithmetic mean as if it were P_up, with
        # P_down=0, substitutes P_up/2 and yields C*(P_up/2)^2 = C*P_up^2/4.
        # Unit check: C is kg/(s·Pa^2); times Pa^2 is kg/s. The number is a
        # mass-flow capacity, not a per-pressure coefficient (the
        # conductance_kg_s_per_bar alias is the same kg/s value).
        # Sanity: vacuum downstream, default geometry, 1500 °C, P_up=1000 Pa
        # vs 500 Pa returns exactly 4x.
        return {
            'pipe_temperature_C': pipe_temperature_C,  # °C — active pipe/liner wall temperature
            'conductance_temperature_C': conductance_temperature_C,  # °C — gas temperature used for conductance
            'p_mean_Pa': allowed_pressure_Pa,  # Pa — compatibility field; allowed upstream pressure
            'initial_throat_area_m2': self.initial_throat_area_m2,  # m² — user-configured throat cross-section
            'throat_diameter_m': self.pipe_diameter_m,  # m — circular-equivalent throat diameter
            'stage_area_m2_by_stage': self.stage_area_m2_by_stage(),  # m² by stage — throat area × ratio
            'stage_area_geometry_provenance_notice': (
                self.stage_area_geometry_provenance_notice()
            ),
            'conductance_kg_s': conductance,  # kg/s — PIPE-1 capacity at allowed upstream and downstream pressures, not at the arithmetic mean
            'conductance_kg_s_per_bar': conductance,  # kg/s — deprecated compatibility alias; not per-bar
            'pipe_conductance_kg_hr': pipe_conductance_kg_hr,  # kg/hr — pipe mass-flow capacity
            'pipe_capacity_used_pct': pipe_capacity_used_pct,  # percent — capacity used
            'vapor_pressure_mbar': vapor_pressure_mbar,  # mbar — steady-state vapor partial pressure
            'pressure_mbar': pressure_mbar,  # mbar — reported total overhead pressure
            'p_downstream_bar': downstream_pressure_Pa / 1.0e5,
            'transport_binding_cause': (
                effective_transport_capacity.binding_cause
                if controlled_flow
                else 'pipe'
            ),
        }

    def controlled_o2_transport_capacity(
        self,
        evap_flux: EvaporationFlux,
        melt: MeltState,
        *,
        cold_train_capacity,
        retained_holdup_kg: float = 0.0,
        dt_hr: float = 1.0,
    ) -> Optional[EffectiveTransportCapacity]:
        """Return the one-tick flow boundary for controlled-pO2 operation."""

        if self._downstream_pressure_override is not None:
            return None
        if getattr(melt.atmosphere, 'name', '') not in {
            'CONTROLLED_O2',
            'CONTROLLED_O2_FLOW',
            'O2_BACKPRESSURE',
        }:
            return None
        allowed_pressure_Pa = max(float(melt.p_total_mbar) * 100.0, 1.0)
        pipe_capacity_kg_hr = (
            max(0.0, float(self._conductance_override)) * 3600.0
            if self._conductance_override is not None
            else self._pipe_conductance(
                allowed_pressure_Pa,
                float(melt.temperature_C),
                p_downstream_Pa=0.0,
                species_kg_for_M_avg=evap_flux.species_kg_hr,
            ) * 3600.0
        )
        from simulator.thermal_train import FiniteCapacity, NoColdTrain

        equipment_capacity = (
            cold_train_capacity.value_kg_hr
            if isinstance(cold_train_capacity, FiniteCapacity)
            else None
        )
        equipment_capacity_required = not (
            isinstance(cold_train_capacity, NoColdTrain)
            and cold_train_capacity.reason == "runtime_enforcement_disabled"
        )
        return controlled_flow_capacity(
            pipe_capacity_kg_hr=pipe_capacity_kg_hr,
            equipment_capacity_kg_hr=equipment_capacity,
            evolved_flux_kg_hr=evap_flux.total_kg_hr,
            retained_holdup_kg=retained_holdup_kg,
            dt_hr=dt_hr,
            equipment_capacity_required=equipment_capacity_required,
            upstream_pressure_bar=allowed_pressure_Pa / 1.0e5,
        )

    def _resolve_liner_temperature_value(
        self,
        config: Any,
        melt: Optional[MeltState],
    ) -> float:
        if is_declared_real_scalar(config) and isinstance(config, (int, float)):
            return float(config)
        if not isinstance(config, Mapping):
            return DEFAULT_PIPE_TEMPERATURE_C

        default_C = self._optional_float(  # °C — default liner wall temperature
            config.get('default_C'),
            DEFAULT_PIPE_TEMPERATURE_C,
        )
        schedule = config.get('schedule', ())  # unitless sequence — liner temperature schedule
        if isinstance(schedule, Mapping):
            schedule = schedule.get('segments', ())  # unitless sequence — segment schedule entries
        if not isinstance(schedule, (list, tuple)):
            return default_C

        campaign_name = ''
        campaign_hour = 0.0  # hr — campaign-relative time
        absolute_hour = 0.0  # hr — absolute simulation time
        if melt is not None:
            campaign = getattr(melt, 'campaign', None)
            campaign_name = str(getattr(campaign, 'name', campaign) or '')
            campaign_hour = self._optional_float(  # hr — campaign-relative time
                getattr(melt, 'campaign_hour', 0.0), 0.0)
            absolute_hour = self._optional_float(getattr(melt, 'hour', 0.0), 0.0)  # hr — absolute simulation time

        selected = None
        for segment in schedule:
            if not isinstance(segment, Mapping):
                continue
            if not self._campaign_matches(segment.get('campaign'), campaign_name):
                continue
            hour_basis = str(segment.get('hour_basis') or 'campaign')
            hour = absolute_hour if hour_basis == 'absolute' else campaign_hour  # hr — selected schedule time basis
            start_hour = self._optional_float(  # hr — segment start time
                segment.get('from_campaign_hour', segment.get('start_hour')),
                0.0,
            )
            end_raw = segment.get('to_campaign_hour', segment.get('end_hour'))
            end_hour = None if end_raw is None else self._optional_float(end_raw, 0.0)  # hr or None — segment end time
            if hour < start_hour:
                continue
            if end_hour is not None and hour > end_hour:
                selected = segment
                continue
            return self._interpolate_liner_segment(segment, hour, start_hour, end_hour)

        if isinstance(selected, Mapping):
            return self._optional_float(
                selected.get('end_C', selected.get('start_C')),
                default_C,
            )
        return default_C

    @staticmethod
    def _optional_float(value: Any, default: float) -> float:
        try:
            if not is_declared_real_scalar(value, allow_numeric_str=True):
                raise TypeError
            result = float(value)
        except (TypeError, ValueError):
            return float(default)
        if not math.isfinite(result):
            return float(default)
        return result

    @staticmethod
    def _config_value(value: Any) -> Any:
        if isinstance(value, Mapping) and 'value' in value:
            return value.get('value')
        return value

    @classmethod
    def _optional_positive_float(cls, value: Any, default: float) -> float:
        result = cls._optional_float(value, default)
        if result <= 0.0:
            return float(default)
        return result

    @staticmethod
    def _campaign_matches(configured: Any, campaign_name: str) -> bool:
        aliases = {
            'C2A': {'C2A', 'C2A_continuous'},
            'C2A_STAGED': {'C2A_STAGED', 'C2A_staged'},
            'C3_K': {'C3_K', 'C3'},
            'C3_NA': {'C3_NA', 'C3'},
        }
        campaign_names = aliases.get(campaign_name, {campaign_name})
        if configured in (None, '', '*'):
            return True
        if isinstance(configured, str):
            return configured in campaign_names
        if isinstance(configured, (list, tuple, set)):
            return bool(campaign_names & {str(item) for item in configured})
        return False

    def _interpolate_liner_segment(
        self,
        segment: Mapping,
        hour: float,  # hr — schedule time on selected basis
        start_hour: float,  # hr — segment start time
        end_hour: Optional[float],  # hr or None — segment end time
    ) -> float:
        start_C = self._optional_float(  # °C — segment start wall temperature
            segment.get('start_C', segment.get('temperature_C')),
            DEFAULT_PIPE_TEMPERATURE_C,
        )
        end_C = self._optional_float(segment.get('end_C'), start_C)  # °C — segment end wall temperature
        if end_hour is None or end_hour <= start_hour:
            return end_C
        fraction = max(0.0, min(1.0, (hour - start_hour) / (end_hour - start_hour)))  # dimensionless — interpolation fraction
        return start_C + (end_C - start_C) * fraction

    def update(self, evap_flux: EvaporationFlux,
               melt: MeltState,
               train: CondensationTrain,
               actual_O2_kg_hr: float = 0.0,  # kg/hr — melt/offgas O2 mass flow
               actual_O2_mol_hr: Optional[float] = None,  # mol/hr — melt/offgas O2 molar flow
               mre_anode_O2_mol_hr: float = 0.0,  # mol/hr — MRE anode O2 flow
               overhead_holdup_mol: Optional[Mapping[str, float]] = None,  # mol by species — finite-headspace gas holdup
               existing_gas: Optional[OverheadGas] = None,
               headspace_volume_m3: Optional[float] = None,  # m³ — explicit finite headspace volume
               p_downstream_bar: Optional[float] = None,  # bar — explicit downstream/reference pressure
               bleed_conductance_kg_s: Optional[float] = None,  # kg/s — explicit bleed mass-flow capacity
               bleed_conductance_kg_s_per_bar: Optional[float] = None,  # deprecated compatibility alias; kg/s, not per-bar
               cold_train_capacity=None,
               transport_inlet_kg_hr: Optional[float] = None,
               transport_inlet_flux: Optional[EvaporationFlux] = None,
               effective_transport_capacity: Optional[EffectiveTransportCapacity] = None,
               ) -> OverheadGas:
        """
        Calculate overhead gas state for this hour.

        Args:
            evap_flux:     Current evaporation rates from the melt
            melt:          Current melt state (T, atmosphere, pO₂)
            train:         Legacy compatibility argument; not read. The caller
                           supplies the routed residual as ``evap_flux``.
            actual_O2_kg_hr: Melt/offgas O₂ produced this hour, kg.
            actual_O2_mol_hr: Same melt/offgas O₂ flow in mol/hr. If omitted,
                              it is projected from kg.
            mre_anode_O2_mol_hr: MRE anode O₂ flow in mol/hr. Recorded as a
                                 separate source bin and not counted as
                                 turbine throughput.
            transport_inlet_kg_hr: Full evolved mass flux entering the upstream
                                   transport duct. ``evap_flux`` remains the
                                   post-condensation composition basis.
            transport_inlet_flux: Full evolved species flux entering the
                                  upstream transport duct. Supplies the mixture
                                  basis for conductance and backpressure.
            cold_train_capacity: Legacy compatibility argument; not read.

        Returns:
            Updated OverheadGas with pressure, flow, and feedback data
        """
        gas = existing_gas if existing_gas is not None else OverheadGas()
        self._reset_gas(gas)
        O2_flow_kg_hr = max(0.0, actual_O2_kg_hr)
        O2_flow_mol_hr = (
            max(0.0, float(actual_O2_mol_hr))
            if actual_O2_mol_hr is not None
            else O2_flow_kg_hr / O2_KG_PER_MOL
        )
        gas.melt_offgas_O2_mol_hr = O2_flow_mol_hr
        gas.mre_anode_O2_mol_hr = max(0.0, float(mre_anode_O2_mol_hr))

        # ── Pipe conductance limit ──────────────────────── [PIPE-1]
        upstream_flux = (
            transport_inlet_flux
            if transport_inlet_flux is not None
            else evap_flux
        )
        transport_state = self.estimate_transport_state(
            upstream_flux,
            melt,
            p_downstream_bar=p_downstream_bar,
            effective_transport_capacity=effective_transport_capacity,
        )  # mixed units — pipe transport state
        conductance = transport_state['conductance_kg_s']  # kg/s — pipe mass-flow capacity
        gas.pipe_conductance_kg_hr = transport_state['pipe_conductance_kg_hr']  # kg/hr — pipe mass-flow capacity
        gas.initial_throat_area_m2 = transport_state['initial_throat_area_m2']  # m² — user-configured throat cross-section
        gas.throat_diameter_m = transport_state['throat_diameter_m']  # m — circular-equivalent throat diameter
        gas.stage_area_m2_by_stage = dict(transport_state['stage_area_m2_by_stage'])  # m² by stage — throat area × ratio
        gas.stage_area_geometry_provenance_notice = dict(
            transport_state['stage_area_geometry_provenance_notice'])
        finite_conductance = self._resolve_bleed_conductance(  # kg/s — finite-headspace bleed mass-flow capacity
            conductance,
            bleed_conductance_kg_s
            if bleed_conductance_kg_s is not None
            else bleed_conductance_kg_s_per_bar,
        )
        gas.bleed_conductance_kg_s = finite_conductance  # kg/s — finite-headspace bleed mass-flow capacity
        gas.bleed_conductance_kg_s_per_bar = finite_conductance  # kg/s — deprecated compatibility alias
        if effective_transport_capacity is None:
            inlet_load_kg_hr = (
                max(0.0, float(transport_inlet_kg_hr))
                if transport_inlet_kg_hr is not None
                else max(0.0, float(upstream_flux.total_kg_hr))
            )
            pipe_capacity_kg_hr = transport_state['pipe_conductance_kg_hr']
            if inlet_load_kg_hr <= 0.0:
                transport_state['pipe_capacity_used_pct'] = 0.0
            elif pipe_capacity_kg_hr > 0.0:
                transport_state['pipe_capacity_used_pct'] = (
                    inlet_load_kg_hr / pipe_capacity_kg_hr * 100.0
                )
            else:
                transport_state['pipe_capacity_used_pct'] = math.inf
        gas.p_downstream_bar = transport_state['p_downstream_bar']
        gas.headspace_volume_m3 = self._resolve_headspace_volume(  # m³ — finite headspace volume
            headspace_volume_m3)
        gas.headspace_temperature_K = self._headspace_temperature_K(melt)  # K — finite headspace gas temperature

        # ── Transport saturation ────────────────────────── [LOOP-3]
        # How much of the pipe capacity is being used.
        # >100% means evaporation exceeds transport → triggers ΔT/dt throttle.
        gas.transport_saturation_pct = transport_state['pipe_capacity_used_pct']  # percent — pipe capacity used
        gas.transport_binding_cause = transport_state['transport_binding_cause']

        gas.evap_exceeds_transport = gas.transport_saturation_pct > 100.0  # dimensionless bool — transport over-capacity flag

        if self._finite_headspace_enabled:
            self._update_finite_headspace(
                gas,
                melt,
                overhead_holdup_mol or {},
                actual_O2_kg_hr=actual_O2_kg_hr,  # kg/hr — melt/offgas O2 mass flow
                actual_O2_mol_hr=actual_O2_mol_hr,  # mol/hr — melt/offgas O2 molar flow
                mre_anode_O2_mol_hr=mre_anode_O2_mol_hr,  # mol/hr — MRE anode O2 flow
            )
            return gas

        # ── Overhead pressure ───────────────────────────────────────
        # Premise: the shared pipe law is F=C₀(P_up²-P_down²), where
        # C₀ has units kg/(s·Pa²). Algebra gives
        # P_vapor=sqrt(F/C₀+P_down²). Against vacuum, if F_allowed is the
        # capacity evaluated at P_allowed, then
        # P_vapor/P_allowed=sqrt(F/F_allowed), not F/F_allowed. Unit check:
        # (kg/s)/(kg/(s·Pa²))=Pa². Sanity: 25% of vacuum capacity requires
        # 50% of the allowed pressure; full capacity requires 100%.
        # Total pressure may include a non-condensable background gas
        # such as Mars CO2; product partial pressures should not inherit
        # that background pressure.
        # The transport duct is upstream of the condensation train, while the
        # reported product gas is downstream. Conductance/backpressure therefore
        # use ``upstream_flux`` above; serialized partials keep the residual flux
        # and its residual pressure scale so capture is not reported as product.
        report_transport_state = (
            transport_state
            if upstream_flux is evap_flux
            else self.estimate_transport_state(
                evap_flux,
                melt,
                p_downstream_bar=p_downstream_bar,
                effective_transport_capacity=effective_transport_capacity,
            )
        )
        gas.pressure_mbar = report_transport_state['pressure_mbar']  # mbar — reported downstream total pressure
        flows = self._molar_stream_from_mass_rates(evap_flux.species_kg_hr, self.species_formula_registry)
        # Downstream capture removes condensables, not upstream carrier flow.
        # Actual O2 telemetry already includes coproduct/dose/discharge here;
        # adding the reaction projector again would count production twice.
        upstream_flows = getattr(self, 'last_stream_mol_hr', {})
        has_live_inlet = bool(upstream_flows) and upstream_flux.total_kg_hr > 0.0
        if has_live_inlet:
            carrier = self.last_stream_boundary.get('carrier_species', '')
            for species, rate in upstream_flows.items():
                if species in {'O2', carrier}:
                    flows[species] = rate
        if O2_flow_mol_hr > 0.0:
            flows['O2'] = O2_flow_mol_hr
        if not has_live_inlet or not self.last_stream_boundary.get('carrier_species'):
            flows = self.flowing_stream_mol_hr(flows, melt, report_transport_state)
            gas.pressure_mbar = self.last_stream_boundary['pressure_mbar']
        gas.composition.update(self.species_partial_pressures(
            evap_flux, gas.pressure_mbar, self.species_formula_registry,
            molar_flow_by_species=flows,
        ))
        if not flows or sum(flows.values()) == 0.0:
            gas.pressure_mbar = 0.0
        return gas

    @staticmethod
    def _molar_stream_from_mass_rates(
        species_kg_hr: Mapping[str, float],
        species_formula_registry: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, float]:
        flows = {}
        for species, rate in species_kg_hr.items():
            mass = _species_molar_mass_g_mol(species, species_formula_registry)
            if mass is None or mass <= 0.0:
                raise OverheadConfigurationError(f'stream molar mass unavailable for {species!r}')
            # q=m_dot/M: (kg/hr)/(g/mol /1000)=mol/hr; zero mass gives zero q.
            flows[species] = _required_finite_nonnegative_float(rate, f'stream.{species}.kg_hr') / (mass / 1000.0)
        return flows

    @staticmethod
    def species_partial_pressures(
        evap_flux: EvaporationFlux,
        vapor_pressure_mbar: float,
        species_formula_registry: Optional[Mapping[str, Any]] = None,
        *,
        molar_flow_by_species: Optional[Mapping[str, float]] = None,
    ) -> dict[str, float]:
        """Dalton projection of the complete local molar stream, in mbar."""
        pressure = _required_finite_nonnegative_float(vapor_pressure_mbar, 'stream.pressure_mbar')
        flows = (dict(molar_flow_by_species) if molar_flow_by_species is not None else
                 OverheadGasModel._molar_stream_from_mass_rates(evap_flux.species_kg_hr, species_formula_registry))
        flows = {str(s): _required_finite_nonnegative_float(q, f'stream.{s}.mol_hr')
                 for s, q in flows.items()}
        total_molar_flow = sum(flows.values())
        # Premise: q includes emitted, carrier and dosed gas at this location.
        # Dalton: y_i=q_i/Q, p_i=y_i*P; (mol/hr)/(mol/hr)*mbar=mbar.
        # Sum(p)=P; adding carrier at fixed P lowers every unchanged partial.
        # A zero-flow vector has no composition, including at nonzero command.
        if total_molar_flow > 0.0:
            return {
                species: molar_flow / total_molar_flow * pressure
                for species, molar_flow in flows.items()
            }
        return {}

    def flowing_stream_mol_hr(
        self, source_mol_hr: Mapping[str, float], melt: MeltState,
        transport: Mapping[str, Any], *,
        holdup_mol: Optional[Mapping[str, float]] = None,
        effective_transport_capacity: Optional[EffectiveTransportCapacity] = None,
        headspace_volume_m3: Optional[float] = None,
    ) -> dict[str, float]:
        """Resolve the existing outlet boundary; no source flux or ledger edits."""
        self.stream_physics_notices = []
        pressure_mbar = float(transport['pressure_mbar'])
        temperature_K = float(transport['conductance_temperature_C']) + CELSIUS_TO_KELVIN_OFFSET
        flows = {str(s): float(q) for s, q in source_mol_hr.items()}
        inventory = dict(holdup_mol or {})
        if not inventory and effective_transport_capacity is not None and effective_transport_capacity.saturation > 1.0:
            self.stream_physics_notices.append({
                'reason': 'stream_source_exceeds_outlet_capacity', 'status': 'extrapolated',
                'authority_level': 'extrapolated', 'output_status': 'status_bearing',
                'boundary_saturation': effective_transport_capacity.saturation,
            })
        # A consuming reaction draws from the vessel before discharge. It is
        # not a negative outward gas flow. mol/hr * the one-hour tick = mol.
        for species, rate in list(flows.items()):
            if rate < 0.0:
                available = inventory.get(species, 0.0) + rate * 1.0
                if available < 0.0:
                    # The pre-commit source proposal can exceed available
                    # reactant (including proposals the ledger later rejects
                    # at its numerical floor). Preserve source flux and expose
                    # the deficit; an absent outward reactant is not negative
                    # gas. The source solve/commit still owns its disposition.
                    self.stream_physics_notices.append({
                        'reason': 'stream_reactant_supply_inconsistent', 'status': 'extrapolated',
                        'authority_level': 'extrapolated', 'output_status': 'status_bearing',
                        'species': species, 'signed_source_mol_hr': rate,
                        'available_inventory_mol': inventory.get(species, 0.0),
                        'reactant_deficit_mol': -available,
                    })
                inventory[species] = max(0.0, available)
                flows[species] = 0.0
        if inventory:
            # The existing bleed owner determines the part of prior inventory
            # discharged during this one-hour tick. Do not treat stored mol as
            # fresh mol/hr or add bubbler/redox counters already in that stock.
            masses = {s: _species_molar_mass_g_mol(s, self.species_formula_registry)
                      for s in inventory}
            if any(m is None or m <= 0 for m in masses.values()):
                raise OverheadConfigurationError('stream holdup molar mass unavailable')
            total_mol = sum(inventory.values())
            total_kg = sum(n * masses[s] / 1000.0 for s, n in inventory.items())
            source_kg_hr = 0.0
            for species, mol_hr in flows.items():
                mass = _species_molar_mass_g_mol(species, self.species_formula_registry)
                if mass is None or mass <= 0.0:
                    raise OverheadConfigurationError(f'stream molar mass unavailable for {species!r}')
                source_kg_hr += mol_hr * mass / 1000.0
            boundary = effective_transport_capacity or controlled_flow_capacity(
                pipe_capacity_kg_hr=transport['pipe_conductance_kg_hr'],
                equipment_capacity_kg_hr=None, equipment_capacity_required=False,
                evolved_flux_kg_hr=source_kg_hr, retained_holdup_kg=total_kg,
                upstream_pressure_bar=pressure_mbar / 1000.0,
            )
            # One outlet capacity serves fresh source plus prior discharge.
            # Preserve source flux (the coupled source solve is a later chunk)
            # and reserve it first: spare=max(0, swallowed-source) [kg/hr].
            # The existing bleed owner allocates that spare over prior stock.
            # Its effective-capacity branch also avoids applying P2 derating
            # twice to transport's already finite-downstream PIPE-1 capacity.
            prior_boundary = replace(boundary, swallowed_flux_kg_hr=max(
                0.0, boundary.swallowed_flux_kg_hr - source_kg_hr))
            if source_kg_hr > boundary.effective_capacity_kg_hr:
                self.stream_physics_notices.append({
                    'reason': 'stream_source_exceeds_outlet_capacity', 'status': 'extrapolated',
                    'authority_level': 'extrapolated', 'output_status': 'status_bearing',
                    'source_kg_hr': source_kg_hr, 'outlet_capacity_kg_hr': boundary.effective_capacity_kg_hr,
                })
            discharged = BuiltinOverheadBleedProvider._bled_species_mol(
                inventory, total_mol=total_mol, total_kg=total_kg,
                controls={'dt_hr': 1.0,
                          'bleed_conductance_kg_s': transport['conductance_kg_s'],
                          'p_total_bar': pressure_mbar / 1000.0,
                          'p_downstream_bar': transport['p_downstream_bar'],
                          'effective_transport_capacity': prior_boundary,
                          'force_drain_all': not self._finite_headspace_enabled},
            )
            for species, mol in discharged.items():
                flows[species] = flows.get(species, 0.0) + mol / 1.0  # mol / one-hour tick -> mol/hr
        flows = {s: _required_finite_nonnegative_float(q, f'stream.{s}.mol_hr')
                 for s, q in flows.items()}
        atmosphere = getattr(melt.atmosphere, 'name', '')
        carrier = str(getattr(melt, 'background_gas_species', '') or '')
        if atmosphere in {'PN2_SWEEP', 'ARGON_FLOW', 'CO2_BACKPRESSURE'}:
            carrier = carrier or {'PN2_SWEEP': 'N2', 'ARGON_FLOW': 'Ar',
                                  'CO2_BACKPRESSURE': 'CO2'}[atmosphere]
        else:
            carrier = ''
        self.last_stream_boundary = {
            'pressure_mbar': pressure_mbar, 'location': 'melt_to_train',
            'pressure_command_mbar': float(melt.p_total_mbar),
            'carrier_species': carrier,
            'provenance': 'emitted_plus_existing_bleed',
            'prior_inventory_discharge_dt_hr': 1.0,
        }
        if carrier:
            # Existing PIPE-1 gives mass capacity M_bar*Q. Divide by the same
            # M_bar: composition-independent viscosity cancels M_bar exactly.
            # (kg/s)*3600/(kg/mol)=mol/hr; equal endpoint pressures give Q=0.
            mixture = {s: q * _species_molar_mass_g_mol(s, self.species_formula_registry) / 1000.0
                       for s, q in flows.items() if q > 0.0}
            if not mixture:
                mixture = {carrier: _species_molar_mass_g_mol(carrier, self.species_formula_registry) / 1000.0}
            mean_mass = _mean_molar_mass_kg_mol(mixture, species_formula_registry=self.species_formula_registry)
            capacity = self._pipe_conductance(
                pressure_mbar * 100.0, temperature_K - CELSIUS_TO_KELVIN_OFFSET,
                p_downstream_Pa=float(transport['p_downstream_bar']) * 1.0e5,
                species_kg_for_M_avg=mixture,
            )
            throughput = capacity * 3600.0 / mean_mass
            # Steady outlet balance: q_carrier = Q_out - sum(q_noncarrier).
            # mol/hr throughout. At zero emitted flow the pure sweep survives.
            required_carrier = throughput - sum(q for s, q in flows.items() if s != carrier)
            self.last_stream_boundary.update(provenance='ideal_regulator_open_duct',
                                             required_carrier_out_mol_hr=required_carrier)
            if required_carrier >= 0.0:
                flows[carrier] = required_carrier
            else:
                flows[carrier] = 0.0
                self.stream_physics_notices.append({
                    'reason': 'stream_carrier_boundary_infeasible', 'status': 'extrapolated',
                    'required_carrier_out_mol_hr': required_carrier,
                    'authority_level': 'extrapolated', 'output_status': 'status_bearing',
                })
            if pressure_mbar > 0.0 and throughput > 0.0:
                # Diagnostic only, not a new choking/transport law. Continuity
                # gives u=(Q/3600)RT/(P A) [m/s]. For ideal diatomic N2 use
                # gamma=1.4 as an explicitly approximate property: a=sqrt(gamma RT/M).
                # Molar sonic ceiling follows isentropic continuity at Mach=1;
                # A P sqrt(gamma/(MRT))*[2/(gamma+1)]^((gamma+1)/(2(gamma-1)))
                # has mol/s units, tends to zero with P, and only flags PIPE-1.
                area = math.pi * self.pipe_diameter_m**2 / 4.0
                mass = _species_molar_mass_g_mol(carrier, self.species_formula_registry) / 1000.0
                gamma = 1.4
                speed = throughput / 3600.0 * GAS_CONSTANT * temperature_K / (pressure_mbar * 100.0 * area)
                sound = math.sqrt(gamma * GAS_CONSTANT * temperature_K / mass)
                sonic = (area * pressure_mbar * 100.0 * math.sqrt(gamma / (mass * GAS_CONSTANT * temperature_K))
                         * (2.0 / (gamma + 1.0))**((gamma + 1.0) / (2.0 * (gamma - 1.0))) * 3600.0)
                if speed > sound or throughput > sonic:
                    self.stream_physics_notices.append({
                        'reason': 'stream_throughput_out_of_domain', 'status': 'extrapolated',
                        'authority_level': 'extrapolated', 'output_status': 'status_bearing',
                        'provenance': 'ideal_regulator_open_duct',
                        'throughput_mol_hr': throughput, 'sonic_ceiling_mol_hr': sonic,
                        'mach': speed / sound, 'gamma_approximation': gamma, 'property_basis': carrier,
                    })
            # Finite-vessel makeup differs from outlet flow: n=pV/(RT),
            # q_in=q_out+(n_now-n_prev)/1hr [mol/hr]. This is an internal
            # regulator inventory, not a second AtomLedger material account.
            if self._finite_headspace_enabled:
                total = sum(flows.values())
                n_now = (flows[carrier] / total * pressure_mbar * 100.0
                         * self._resolve_headspace_volume(headspace_volume_m3)
                         / (GAS_CONSTANT * self._headspace_temperature_K(melt))) if total else 0.0
                tick = getattr(melt, 'hour', None)
                if getattr(self, '_stream_inventory_tick', None) != tick:
                    self._stream_inventory_start = dict(getattr(self, '_stream_carrier_inventory', {}))
                    self._stream_inventory_tick = tick
                previous = getattr(self, '_stream_inventory_start', {}).get(
                    carrier, float((holdup_mol or {}).get(carrier, 0.0)))
                makeup = flows[carrier] + (n_now - previous) / 1.0
                self.last_stream_boundary['carrier_inlet_makeup_mol_hr'] = makeup
                self.last_stream_boundary['carrier_inventory_change_mol'] = n_now - previous
                self._stream_carrier_inventory = {carrier: n_now}
                if makeup < 0.0:
                    self.stream_physics_notices.append({
                        'reason': 'stream_carrier_makeup_infeasible', 'status': 'extrapolated',
                        'authority_level': 'extrapolated', 'output_status': 'status_bearing',
                        'required_makeup_mol_hr': makeup,
                    })
        if not carrier and atmosphere not in {'CONTROLLED_O2', 'CONTROLLED_O2_FLOW', 'O2_BACKPRESSURE'}:
            # With no supplied carrier, a pressure command is a ceiling rather
            # than extra gas. Invert the existing outlet law using the complete
            # gas load: q_i*M_i is kg/hr, sum/3600 is kg/s. This determines
            # pressure for the given source; it does not recompute evaporation.
            mass_rates = {s: q * _species_molar_mass_g_mol(s, self.species_formula_registry) / 1000.0
                          for s, q in flows.items() if q > 0.0}
            pressure_mbar = self._vapor_pressure_mbar_from_flux(
                sum(mass_rates.values()) / 3600.0,
                temperature_K - CELSIUS_TO_KELVIN_OFFSET,
                p_downstream_bar=transport['p_downstream_bar'],
                species_kg_for_M_avg=mass_rates,
            )
            self.last_stream_boundary['pressure_mbar'] = pressure_mbar
        self.last_stream_mol_hr = dict(flows)
        if sum(flows.values()) == 0.0:
            self.last_stream_boundary['pressure_mbar'] = 0.0
            self.last_stream_boundary['status'] = 'zero_flow_no_composition'
        for notice in self.stream_physics_notices:
            notice.update(hour=getattr(melt, 'hour', None), campaign=getattr(melt.campaign, 'name', ''),
                          location='melt_to_train', pressure_mbar=pressure_mbar,
                          flows_mol_hr=dict(flows))
        return flows

    def _update_finite_headspace(self, gas: OverheadGas, melt: MeltState,
                                 overhead_holdup_mol: Mapping[str, float],
                                 *,
                                  actual_O2_kg_hr: float,  # kg/hr — melt/offgas O2 mass flow
                                  actual_O2_mol_hr: Optional[float],  # mol/hr — melt/offgas O2 molar flow
                                  mre_anode_O2_mol_hr: float) -> None:  # mol/hr — MRE anode O2 flow
        partials_bar = self._compute_partial_pressures(  # bar — species partial pressures
            overhead_holdup_mol,
            gas.headspace_volume_m3,
            gas.headspace_temperature_K,
        )
        gas.pressure_mbar = sum(partials_bar.values()) * 1000.0  # mbar — total pressure; bar -> mbar
        gas.composition.update({
            species: p_bar * 1000.0  # mbar — species partial pressure; bar -> mbar
            for species, p_bar in partials_bar.items()  # bar — species partial pressure
            if p_bar > 0.0
        })

        atmosphere_name = getattr(melt.atmosphere, 'name', '')
        if atmosphere_name == 'CO2_BACKPRESSURE' and melt.p_total_mbar > 0:
            gas.pressure_mbar = max(gas.pressure_mbar, melt.p_total_mbar)  # mbar — CO2 total pressure floor
            gas.composition['CO2'] = max(  # mbar — CO2 partial pressure
                gas.composition.get('CO2', 0.0), melt.p_total_mbar * 0.96)  # mbar — CO2 partial pressure; 0.96 mole fraction
        elif atmosphere_name in {'PN2_SWEEP', 'ARGON_FLOW'} and melt.p_total_mbar > 0:
            gas.pressure_mbar = max(gas.pressure_mbar, melt.p_total_mbar)  # mbar — sweep total pressure floor
            # Kr audit 2026-07-25: same stamped-carrier fix as the
            # no-headspace branch above.
            _sweep_key = (
                getattr(melt, 'background_gas_species', '')
                or ('Ar' if atmosphere_name == 'ARGON_FLOW' else 'N2')
            )
            gas.composition[_sweep_key] = max(
                gas.composition.get(_sweep_key, 0.0),
                max(0.0, melt.p_total_mbar - melt.pO2_mbar))  # mbar — sweep balance partial pressure

        # 0.5.3 Phase A1 (2026-05-28): commanded-pO2 floor mirror. The legacy
        # no-headspace branch (above) writes `gas.composition['O2'] = max(...,
        # melt.pO2_mbar)` so a recipe pO2 setpoint always survives. Without the
        # mirror here, the holdup-derived O2 partial would override the setpoint
        # for actively-controlled atmospheres. Only apply in O2-controlled modes:
        # an uncontrolled HARD_VACUUM / PN2_SWEEP run must NOT get a synthetic
        # O2 floor (matches the design intent at equilibrium.py:9-12).
        #
        # Phase A chunk-review P1 (codex 2026-05-28): also raise the reported
        # total pressure to at least the commanded pO2 floor. Pre-fix the
        # holdup-derived total could be 0.0 mbar while the O2 partial was
        # forced to e.g. 1.5 mbar — an impossible gas state (P_total < pO2).
        # The runner fixture ``ci_carbonaceous_chondrite_C2B_12h.json`` carried
        # ``P_total_bar=0`` with ``pO2_bar=0.0015`` as visible evidence. Fix
        # mirrors the CO2_BACKPRESSURE / PN2_SWEEP branches above: when the
        # commanded atmosphere demands a non-zero pO2, the reported total
        # must accommodate it.
        if atmosphere_name in {
            'CONTROLLED_O2',
            'CONTROLLED_O2_FLOW',
            'O2_BACKPRESSURE',
        }:
            if melt.pO2_mbar > 0.0:
                gas.composition['O2'] = max(  # mbar — controlled O2 partial pressure floor
                    gas.composition.get('O2', 0.0), melt.pO2_mbar)  # mbar — controlled O2 partial pressure floor
            gas.pressure_mbar = max(  # mbar — controlled total pressure floor
                gas.pressure_mbar, melt.pO2_mbar, melt.p_total_mbar)  # mbar — controlled total pressure floor
            background_species = str(
                getattr(melt, 'background_gas_species', '') or '').strip()
            if background_species and background_species.upper() != 'O2':
                background_fraction = float(  # dimensionless — background gas mole fraction
                    getattr(melt, 'background_gas_mole_fraction', 1.0) or 0.0)
                background_fraction = min(1.0, max(0.0, background_fraction))  # dimensionless — clamped mole fraction
                gas.composition[background_species] = max(  # mbar — background gas partial pressure
                    gas.composition.get(background_species, 0.0),
                    max(0.0, melt.p_total_mbar - melt.pO2_mbar)
                    * background_fraction,  # mbar — background gas partial pressure share
                )

    @staticmethod
    def _reset_gas(gas: OverheadGas) -> None:
        gas.pressure_mbar = 0.0  # mbar — reset total overhead pressure
        gas.composition.clear()
        gas.turbine_flow_kg_hr = 0.0  # kg/hr — reset O2 turbine mass flow
        gas.turbine_flow_mol_hr = 0.0  # mol/hr — reset O2 turbine molar flow
        gas.pipe_conductance_kg_hr = 50.0  # kg/hr — legacy reset pipe mass-flow capacity
        gas.turbine_limited = False  # dimensionless bool — reset turbine capacity flag
        gas.O2_vented_kg_hr = 0.0  # kg/hr — reset vented O2 mass flow
        gas.O2_vented_mol_hr = 0.0  # mol/hr — reset vented O2 molar flow
        gas.melt_offgas_O2_mol_hr = 0.0  # mol/hr — reset melt/offgas O2 source flow
        gas.mre_anode_O2_mol_hr = 0.0  # mol/hr — reset MRE anode O2 source flow
        gas.turbine_utilization_pct = 0.0  # percent — reset turbine utilization
        gas.turbine_shaft_power_kW = 0.0  # kW — reset turbine shaft power
        gas.evap_exceeds_transport = False  # dimensionless bool — reset transport over-capacity flag
        gas.transport_saturation_pct = 0.0  # percent — reset pipe capacity used
        gas.transport_binding_cause = 'pipe'
        gas.stage_area_geometry_provenance_notice.clear()

    @staticmethod
    def _compute_partial_pressures(holdup_mol: Mapping[str, float],  # mol by species — gas holdup
                                   volume_m3: float,  # m³ — gas volume
                                   temperature_K: float) -> dict[str, float]:  # K — gas temperature
        if volume_m3 <= 0.0 or temperature_K <= 0.0:
            return {}
        scale = GAS_CONSTANT * temperature_K / (volume_m3 * 1.0e5)  # bar/mol — ideal-gas pressure factor; Pa -> bar
        return {
            str(species): max(0.0, float(mol)) * scale  # bar — species partial pressure
            for species, mol in dict(holdup_mol or {}).items()  # mol — species gas holdup
            if max(0.0, float(mol)) > 0.0
        }

    def _resolve_headspace_volume(self, explicit: Optional[float]) -> float:  # m³ or None — explicit volume override
        if not self._finite_headspace_enabled:
            return 0.0
        if explicit is not None:
            return _required_positive_finite_float(
                explicit,
                'headspace.volume_m3',
            )  # m³ — explicit finite headspace volume
        if self._headspace_volume_m3 is not None:
            return _required_positive_finite_float(
                self._headspace_volume_m3,
                'headspace.volume_m3',
            )  # m³ — configured finite headspace volume
        raise OverheadConfigurationError(
            'finite headspace requires a positive configured volume_m3'
        )

    def _headspace_temperature_K(self, melt: MeltState) -> float:
        melt_T_K = float(melt.temperature_C) + CELSIUS_TO_KELVIN_OFFSET  # K — melt temperature; °C -> K
        offset = self._temperature_offset_K  # K or None — configured headspace temperature offset
        if offset is not None:
            return max(1.0, melt_T_K + float(offset))  # K — offset headspace temperature with 1 K floor
        if self._temperature_model == 'lumped':
            return max(1.0, melt_T_K - 100.0)  # K — lumped headspace 100 K below melt with 1 K floor
        return max(1.0, melt_T_K)  # K — melt-coupled headspace temperature with 1 K floor

    def _resolve_bleed_conductance(self, derived_kg_s: float,  # kg/s — derived mass-flow capacity
                                   explicit: Optional[float]) -> float:  # kg/s or None — explicit mass-flow override
        if explicit is not None:
            return max(0.0, float(explicit))  # kg/s — explicit bleed mass-flow capacity
        if self._conductance_override is not None:
            return max(0.0, float(self._conductance_override))  # kg/s — configured bleed mass-flow capacity
        if self._bleed_model == 'constant':
            return max(0.0, float(derived_kg_s))  # kg/s — constant bleed mass-flow capacity
        return max(0.0, float(derived_kg_s))  # kg/s — derived Poiseuille bleed mass-flow capacity

    def _resolve_downstream_pressure(self, melt: MeltState,
                                     explicit: Optional[float]) -> float:  # bar or None — explicit downstream pressure
        if explicit is not None:
            return self._finite_nonnegative_downstream_pressure_bar(
                explicit, 'p_downstream_bar')  # bar — explicit downstream/reference pressure
        if self._downstream_pressure_override is not None:
            return self._finite_nonnegative_downstream_pressure_bar(
                self._downstream_pressure_override,
                'overhead_headspace.downstream_pressure_bar',
            )  # bar — configured downstream/reference pressure
        atmosphere_name = getattr(melt.atmosphere, 'name', '')
        if atmosphere_name in {
            'CONTROLLED_O2',
            'CONTROLLED_O2_FLOW',
            'O2_BACKPRESSURE',
        }:
            # Without a live flux there is no controlled-flow pressure drop to
            # invert. The zero-flow diagnostic limit is P2=P1; runtime callers
            # receive the derived value from controlled_o2_transport_capacity.
            return max(0.0, float(melt.p_total_mbar) / 1000.0)
        return 0.0  # bar — vacuum downstream/reference pressure

    @staticmethod
    def _finite_nonnegative_downstream_pressure_bar(
        value: Any,
        field: str,
    ) -> float:
        try:
            pressure_bar = float(value)
        except (TypeError, ValueError) as exc:
            raise OverheadConfigurationError(
                f'{field} must be finite, got {value!r}'
            ) from exc
        if not math.isfinite(pressure_bar):
            raise OverheadConfigurationError(
                f'{field} must be finite, got {value!r}'
            )
        return max(0.0, pressure_bar)

    def _pipe_conductance(
        self,
        p_upstream_Pa: float,  # Pa — allowed upstream pipe pressure
        T_C: float,  # °C — pipe gas temperature
        *,
        p_downstream_Pa: float = 0.0,  # Pa — downstream/reference pressure
        species_kg_for_M_avg: Optional[Mapping[str, float]] = None,  # kg or kg/hr by species — M_avg basis
    ) -> float:
        """
        Compressible Poiseuille mass-flow capacity of the collection pipe.

        At millibar pressures and 1400+°C, the flow is in the
        viscous regime (Knudsen number Kn << 0.01).

        Args:
            p_upstream_Pa: Allowed upstream pressure in the pipe (Pa)
            T_C:       Pipe temperature (°C)
            species_kg_for_M_avg: optional mapping of species → mass
                for deriving the live mole-weighted M_avg. Pass the
                evap-flux species mass-rate to track real-recipe
                composition. ``None`` / empty falls back to
                ``DEFAULT_PIPE_M_AVG_KG_MOL`` (~0.040 kg/mol) which
                matches the historical hardcoded value.

        Returns:
            Conductance in kg/s (mass-flow capacity)

        0.5.4 W7 (CW5 historical-audit closure): the mass-conductance
        density used to derive a hardcoded ``M_avg = 0.040 kg/mol``
        "mix of SiO, Fe, Na vapors ~40 g/mol" magic number. Real
        recipes span ~0.023 (alkali sweep, Na ~23 g/mol) to ~0.046
        (Fe vapor, ~56 g/mol mixed with O2 ~32) — a factor-of-2 in
        conductance that pre-W7 was hidden behind the placeholder.
        The mole-weighted average is computed from the passed
        species mixture; legacy callers without the kwarg get the
        documented fallback.
        """
        d = self.pipe_diameter_m  # m — pipe inner diameter
        L = self.pipe_length_m  # m — pipe length

        # Direct-helper invalid-input contract: T_K, L, d, and both pressures
        # must be finite, with T_K/L/d positive. A NaN evades ordinary <=
        # comparisons, while infinite pressure makes the pressure-square law
        # report infinite capacity; either result is a meaningless scientific
        # verdict. Fail closed to 0.0 here (the public transport estimator
        # raises OverheadConfigurationError earlier). Negative pressure remains
        # the existing zero-pressure clamp; this preserves valid arithmetic.
        try:
            T_K = float(T_C) + CELSIUS_TO_KELVIN_OFFSET  # K — pipe gas temperature; °C -> K
            L = float(L)  # m — validated pipe length
            d = float(d)  # m — validated pipe inner diameter
            p_upstream_Pa = float(p_upstream_Pa)  # Pa — candidate upstream pressure
            p_downstream_Pa = float(p_downstream_Pa)  # Pa — candidate downstream pressure
        except (TypeError, ValueError):
            return 0.0
        if (
            not all(math.isfinite(value) for value in (
                T_K, L, d, p_upstream_Pa, p_downstream_Pa,
            ))
            or T_K <= 0.0
            or L <= 0.0
            or d <= 0.0
        ):
            return 0.0
        p_upstream_Pa = max(0.0, p_upstream_Pa)  # Pa — clamped upstream pressure
        p_downstream_Pa = max(0.0, p_downstream_Pa)  # Pa — clamped downstream pressure

        # Provisional N₂-like correlation: η=1.8e-5*(T/300 K)^0.7 Pa·s.
        # The temperature ratio and exponent are dimensionless. It recovers
        # 1.8e-5 Pa·s at 300 K and gives 6.243e-5 Pa·s at 1773.15 K
        # (1500 °C); the positive exponent makes viscosity increase with T.
        eta = self._gas_dynamic_viscosity_Pa_s(T_K)  # Pa·s — dynamic viscosity

        # BH-163 provenance: this closes the finite-headspace pipe-conductance
        # defect. The old path used a legacy constant M_avg = 0.040 kg/mol,
        # mis-weighting light species (pure Na off by ~1.74x); this now uses
        # the real evaporating species mix.
        M_avg = _mean_molar_mass_kg_mol(  # kg/mol — mole-weighted gas molar mass
            species_kg_for_M_avg,
            fallback_engagement_recorder=(
                self._record_pipe_m_avg_fallback_engagement
            ),
            species_formula_registry=self.species_formula_registry,
        )
        # F-112 derivation:
        # premise: compressible laminar pipe mass flow is proportional to
        # (P_up^2 - P_down^2), not P_up^2 unless the downstream reference is
        # vacuum.
        # algebra: capacity = C * max(P_up^2 - P_down^2, 0).
        # unit check: Pa^2 enters the Poiseuille numerator and the remaining
        # constants reduce the result to kg/s.
        # sanity: P_down=0 preserves the legacy vacuum result; P_down>=P_up
        # yields zero forward capacity instead of imaginary/negative flow.
        pressure_square_delta_Pa2 = max(
            0.0,
            p_upstream_Pa**2 - p_downstream_Pa**2,
        )  # Pa² — compressible pressure-square driving term
        numerator = math.pi * d**4 * M_avg * pressure_square_delta_Pa2
        denominator = (
            COMPRESSIBLE_POISEUILLE_DENOMINATOR
            * eta
            * L
            * GAS_CONSTANT
            * T_K
        )
        return numerator / denominator  # kg/s — capacity at allowed upstream pressure

    @staticmethod
    def _gas_dynamic_viscosity_Pa_s(T_K: float) -> float:
        return 1.8e-5 * (T_K / 300.0) ** 0.7  # Pa·s — 1.8e-5 Pa·s at 300 K, exponent dimensionless

    def _vapor_pressure_mbar_from_flux(
        self,
        total_evap_kg_s: float,  # kg/s — evaporation mass flow
        T_C: float,  # °C — pipe gas temperature
        *,
        p_downstream_bar: float = 0.0,  # bar — downstream/reference pressure
        species_kg_for_M_avg: Optional[Mapping[str, float]] = None,  # kg or kg/hr by species — M_avg basis
    ) -> float:
        """Invert the shared compressible Poiseuille law."""

        try:
            F_kg_s = float(total_evap_kg_s)  # kg/s — candidate vapor mass throughput
            T_K = float(T_C) + CELSIUS_TO_KELVIN_OFFSET  # K — pipe gas temperature
            d = float(self.pipe_diameter_m)  # m — throat-equivalent diameter
            L = float(self.pipe_length_m)  # m — throat/pipe length
            p_downstream_bar = float(p_downstream_bar)  # bar — candidate downstream pressure
        except (TypeError, ValueError):
            return 0.0
        if (
            not all(math.isfinite(value) for value in (
                F_kg_s, T_K, d, L, p_downstream_bar,
            ))
            or F_kg_s <= 0.0
        ):
            return 0.0
        F_kg_s = max(0.0, F_kg_s)  # kg/s — nonnegative vapor mass throughput
        M_avg = _mean_molar_mass_kg_mol(  # kg/mol — mole-weighted gas molar mass
            species_kg_for_M_avg,
            fallback_engagement_recorder=(
                self._record_pipe_m_avg_fallback_engagement
            ),
            species_formula_registry=self.species_formula_registry,
        )
        if (
            not math.isfinite(M_avg)
            or T_K <= 0.0
            or d <= 0.0
            or L <= 0.0
            or M_avg <= 0.0
        ):
            return 0.0
        eta = self._gas_dynamic_viscosity_Pa_s(T_K)  # Pa·s — dynamic viscosity
        numerator = (
            COMPRESSIBLE_POISEUILLE_DENOMINATOR
            * eta
            * L
            * GAS_CONSTANT
            * T_K
            * F_kg_s
        )
        denominator = math.pi * M_avg * d**4
        if denominator <= 0.0:
            return 0.0
        p_downstream_Pa = max(0.0, p_downstream_bar) * 1.0e5  # Pa — bar -> Pa
        # F-112 inverse derivation:
        # premise: F = C * (P_up^2 - P_down^2).
        # algebra: P_up = sqrt(F / C + P_down^2).
        # unit check: numerator/denominator is Pa^2, and P_down^2 is Pa^2.
        # sanity: zero downstream reproduces the legacy sqrt(F/C); non-zero
        # downstream raises the required upstream pressure for the same flow.
        pressure_Pa = math.sqrt(  # Pa — upstream pressure from pressure-square balance
            max(0.0, numerator / denominator + p_downstream_Pa**2)
        )
        return pressure_Pa / 100.0  # mbar — Pa -> mbar
