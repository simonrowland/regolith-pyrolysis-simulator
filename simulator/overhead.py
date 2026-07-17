"""
Overhead Gas Model
===================

★ TIER 2: SCIENTIST-READABLE ★

Models the gas composition and pressure above the melt,
the hot-duct transport, and the turbine flow rate that
controls upstream pO₂.

The turbine speed is the primary process control for SiO
suppression — the √pO₂ dependence in the equilibrium:

    SiO₂(melt) → SiO(g) + ½O₂(g)

gives strong suppression of SiO vapor pressure when
pO₂ is raised from the body/environment vacuum floor to ~1 mbar.

Pipe transport (isothermal compressible Poiseuille flow at mbar pressures):
    ṁ = π × d⁴ × M × (P₁² - P₂²) / (256 × η × L × R × T) [PIPE-1]

where:
    d = pipe inner diameter (m)
    p̄ = mean pressure (Pa)
    η = gas dynamic viscosity (Pa·s)
    L = pipe length (m)

Reference: 12 cm pipe handles 7-16 g/s SiO at 10 mbar.

Feedback loops modelled:
    [LOOP-1]  Backpressure: overhead partial pressures feed back as
              P_ambient in the HK equation (handled in core.py)
    [LOOP-2]  Turbine capacity: O₂ flow capped at turbine max;
              excess routed to terminal vacuum vent accounting
    [LOOP-3]  Transport saturation: evap rate / pipe conductance
              feeds back to throttle ΔT/dt (handled in core.py)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from simulator.core import (
    EvaporationFlux, MeltState, OverheadGas, CondensationTrain,
)
# Single-source the pipe-temperature default from condensation (canonical
# pipe-defaults home; cycle-safe — condensation doesn't import overhead) [BUG-052].
from simulator.condensation import DEFAULT_PIPE_TEMPERATURE_C  # °C — default pipe/liner temperature
from engines.builtin.overhead_bleed import (
    EffectiveTransportCapacity,
    controlled_flow_capacity,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET  # K — Celsius-to-Kelvin offset
from simulator.state import GAS_CONSTANT, MOLAR_MASS  # R: J/(mol·K); molar masses: g/mol

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
        result = float(_config_value(value))
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
        Mean molar mass in kg/mol, in the range ~0.018 (pure H2O)
        to ~0.197 (pure Au), or the documented fallback when the
        mixture is empty / unresolvable. Always finite + positive
        (no NaN / inf escape; species without an entry in
        ``MOLAR_MASS`` are skipped rather than contaminating the
        denominator).
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
        molar_mass_g_mol = MOLAR_MASS.get(species)  # g/mol — species molar mass
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


class OverheadGasModel:
    """
    Calculates gas composition and flow conditions above the melt.

    Updates the OverheadGas state each simulation hour based on
    evaporation flux, atmosphere settings, pipe geometry, and
    turbine capacity limits.
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
        self._finite_headspace_enabled = bool(merged.get('enabled', False))  # dimensionless bool — finite-headspace switch
        self._headspace_volume_m3 = merged.get('volume_m3')  # m³ or None — finite headspace volume
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

        pipe_temperature_C = self.resolve_pipe_temperature_C(melt)  # °C — active pipe/liner wall temperature
        total_evap_kg_hr = max(0.0, float(evap_flux.total_kg_hr))  # kg/hr — total evaporation mass flow
        allowed_pressure_Pa = max(float(melt.p_total_mbar) * 100.0, 1.0)  # Pa — allowed upstream pressure; mbar -> Pa with 1 Pa floor
        controlled_flow = effective_transport_capacity is not None
        downstream_pressure_Pa = (
            effective_transport_capacity.downstream_pressure_bar * 1.0e5
            if controlled_flow
            else self._resolve_downstream_pressure(melt, p_downstream_bar) * 1.0e5
        )  # Pa — derived or explicit downstream/reference pressure; bar -> Pa
        # Preserve the existing gas-transport path: Poiseuille conductance has
        # historically used melt/gas temperature. The liner trajectory controls
        # wall deposition and Kn diagnostics without changing evaporation totals.
        conductance_temperature_C = float(melt.temperature_C)  # °C — gas temperature used for pipe conductance
        # 0.5.4 W7 (CW5): pass the live evap-flux mixture so the
        # ideal-gas density in ``_pipe_conductance`` uses the actual
        # mole-weighted M_avg (~0.023-0.046 kg/mol across a recipe)
        # rather than the legacy 0.040 hardcoded magic number.
        # ``evap_flux.species_kg_hr`` is the steady-state pipe
        # composition; the time unit cancels in the mole-fraction
        # weighting.
        if controlled_flow:
            pipe_conductance_kg_hr = (
                effective_transport_capacity.effective_capacity_kg_hr
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
        pressure_mbar = max(vapor_pressure_mbar, float(melt.p_total_mbar))  # mbar — reported total overhead pressure
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
            'conductance_kg_s': conductance,  # kg/s — pipe mass-flow capacity at p_mean; NOT per-pressure
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
        if isinstance(config, (int, float)):
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
            train:         Condensation train (for gas routing)
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

        # Total evaporation rate → pressure buildup
        total_evap_kg_hr = evap_flux.total_kg_hr  # kg/hr — total evaporation mass flow

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
        # P_vapor ≈ (evap_rate / conductance) × characteristic pressure.
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
        vapor_pressure_mbar = report_transport_state['vapor_pressure_mbar']  # mbar (nominal) — downstream proxy pressure
        gas.pressure_mbar = report_transport_state['pressure_mbar']  # mbar — reported downstream total pressure

        # ── Product partial pressures (proportional to evaporation rates) ──
        if total_evap_kg_hr > 0:
            gas.composition.update(self.species_partial_pressures(
                evap_flux, vapor_pressure_mbar))

        # Controlled/background atmosphere partial pressures.
        if melt.pO2_mbar > 0.0:
            gas.composition['O2'] = max(  # mbar — controlled O2 partial pressure floor
                gas.composition.get('O2', 0.0), melt.pO2_mbar)  # mbar — controlled O2 partial pressure floor

        atmosphere_name = getattr(melt.atmosphere, 'name', '')
        if atmosphere_name == 'CO2_BACKPRESSURE' and melt.p_total_mbar > 0:
            gas.composition['CO2'] = max(  # mbar — CO2 partial pressure
                gas.composition.get('CO2', 0.0), melt.p_total_mbar * 0.96)  # mbar — CO2 partial pressure; 0.96 mole fraction
        elif atmosphere_name == 'PN2_SWEEP' and melt.p_total_mbar > 0:
            gas.composition['N2'] = max(
                gas.composition.get('N2', 0.0),
                max(0.0, melt.p_total_mbar - melt.pO2_mbar))  # mbar — N2 balance partial pressure
        elif atmosphere_name in {
            'CONTROLLED_O2',
            'CONTROLLED_O2_FLOW',
            'O2_BACKPRESSURE',
        } and melt.p_total_mbar > 0:
            gas.pressure_mbar = max(gas.pressure_mbar, melt.p_total_mbar)  # mbar — controlled total pressure floor
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

        partial_pressure_sum_mbar = sum(  # mbar — sum of nonnegative reported partial pressures
            max(0.0, float(partial_pressure))
            for partial_pressure in gas.composition.values()
        )
        # F-113 derivation:
        # premise: after controlled/background floors are added, reported
        # partial pressures are additive components of the same gas state.
        # algebra: P_total >= Σ_i p_i.
        # unit check: every p_i and P_total here is mbar.
        # sanity: when floors add nothing, this is a no-op; when floors would
        # exceed the prior max-floor total, the total rises instead of
        # advertising an impossible sum(partials) > total state.
        gas.pressure_mbar = max(gas.pressure_mbar, partial_pressure_sum_mbar)

        return gas

    @staticmethod
    def species_partial_pressures(
        evap_flux: EvaporationFlux,
        vapor_pressure_mbar: float,
    ) -> dict[str, float]:
        """Project a species mass-flow mixture onto mole-fraction partials."""
        total_evap_kg_hr = max(0.0, float(evap_flux.total_kg_hr))
        if total_evap_kg_hr <= 0.0:
            return {}
        molar_flow_by_species: dict[str, float] = {}
        for species, rate in evap_flux.species_kg_hr.items():
            molar_mass_g_mol = MOLAR_MASS.get(species)
            if molar_mass_g_mol is None or molar_mass_g_mol <= 0.0:
                continue
            molar_flow_by_species[species] = max(0.0, float(rate)) / (
                molar_mass_g_mol / 1000.0
            )
        total_molar_flow = sum(molar_flow_by_species.values())
        # F-316: y_i = n_dot_i / sum(n_dot), p_i = y_i P_vapor. Mass rates
        # become mol/hr through kg/(kg/mol); unknown-only mixtures retain the
        # legacy mass-fraction fallback instead of losing pressure entirely.
        if total_molar_flow > 0.0:
            return {
                species: molar_flow / total_molar_flow * vapor_pressure_mbar
                for species, molar_flow in molar_flow_by_species.items()
            }
        return {
            species: max(0.0, float(rate)) / total_evap_kg_hr
            * vapor_pressure_mbar
            for species, rate in evap_flux.species_kg_hr.items()
        }

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
        elif atmosphere_name == 'PN2_SWEEP' and melt.p_total_mbar > 0:
            gas.pressure_mbar = max(gas.pressure_mbar, melt.p_total_mbar)  # mbar — N2 total pressure floor
            gas.composition['N2'] = max(
                gas.composition.get('N2', 0.0),
                max(0.0, melt.p_total_mbar - melt.pO2_mbar))  # mbar — N2 balance partial pressure

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
        if explicit is not None:
            return max(0.0, float(explicit))  # m³ — explicit finite headspace volume
        if self._headspace_volume_m3 is not None:
            return max(0.0, float(self._headspace_volume_m3))  # m³ — configured finite headspace volume
        return 0.085  # m³ — default finite headspace volume

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

        # 0.5.4.1 A2 (0.5.4 post-push adversarial R2 P3): defensive
        # input guards. Pre-A2, T_K <= 0 (T_C <= -273.15) raised
        # ZeroDivisionError on the density divide AND a complex result
        # from the fractional exponent ``(T_K / 300.0) ** 0.7`` when
        # T_K is negative. Both are unreachable in valid recipes
        # (the pipe is always above ambient), but a numerical
        # instability or a bad test setup could poison the input;
        # fail-closed to 0.0 conductance rather than propagating a
        # complex / NaN / exception downstream. Also guard L, d <= 0
        # (degenerate pipe geometry; conductance is 0). p_upstream_Pa
        # < 0 is unphysical; clamp to 0.
        T_K = T_C + CELSIUS_TO_KELVIN_OFFSET  # K — pipe gas temperature; °C -> K
        if T_K <= 0.0 or L <= 0.0 or d <= 0.0:
            return 0.0
        p_upstream_Pa = max(0.0, float(p_upstream_Pa))  # Pa — clamped upstream pressure
        p_downstream_Pa = max(0.0, float(p_downstream_Pa))  # Pa — clamped downstream pressure

        # Dynamic viscosity of gas mixture (approximate as N₂-like)
        # η ≈ 4e-5 Pa·s at 1500°C (increases with T for gases)
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

        F_kg_s = max(0.0, float(total_evap_kg_s))  # kg/s — vapor mass throughput
        if F_kg_s <= 0.0:
            return 0.0
        T_K = T_C + CELSIUS_TO_KELVIN_OFFSET  # K — pipe gas temperature
        d = self.pipe_diameter_m  # m — throat-equivalent diameter
        L = self.pipe_length_m  # m — throat/pipe length
        M_avg = _mean_molar_mass_kg_mol(  # kg/mol — mole-weighted gas molar mass
            species_kg_for_M_avg,
            fallback_engagement_recorder=(
                self._record_pipe_m_avg_fallback_engagement
            ),
        )
        if T_K <= 0.0 or d <= 0.0 or L <= 0.0 or M_avg <= 0.0:
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
        p_downstream_Pa = max(0.0, float(p_downstream_bar)) * 1.0e5  # Pa — bar -> Pa
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
