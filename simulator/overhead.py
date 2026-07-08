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

Pipe conductance (Poiseuille viscous flow at mbar pressures):
    C = π × d⁴ × p̄ / (128 × η × L)                       [PIPE-1]

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
from typing import Any, Mapping, Optional

from simulator.core import (
    EvaporationFlux, MeltState, OverheadGas, CondensationTrain,
)
# Single-source the pipe-temperature default from condensation (canonical
# pipe-defaults home; cycle-safe — condensation doesn't import overhead) [BUG-052].
from simulator.condensation import DEFAULT_PIPE_TEMPERATURE_C  # °C — default pipe/liner temperature
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


def _mean_molar_mass_kg_mol(
    species_kg: Optional[Mapping[str, float]],
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
        'conductance_kg_s_per_bar': None,  # MISNOMER: kg/s mass-flow capacity override, not per-bar
        'downstream_pressure_bar': None,  # bar — downstream/reference pressure override
        'liner_temperature_C': DEFAULT_PIPE_TEMPERATURE_C,  # °C — default liner/pipe wall temperature
        'pipe_segment_temperatures_C': None,  # °C — per-pipe-segment wall temperature schedule
    }

    def __init__(self, headspace_config: Optional[Mapping] = None):
        # Pipe geometry (default for 1-tonne batch)
        self.pipe_diameter_m = 0.12      # m — pipe inner diameter default (12 cm)
        self.pipe_length_m = 1.0         # m — crucible-to-first-condenser pipe length
        self._pipe_temperature_C = DEFAULT_PIPE_TEMPERATURE_C  # °C — active pipe/liner wall temperature
        self._liner_temperature_config: Any = DEFAULT_PIPE_TEMPERATURE_C  # °C or schedule — liner temperature config
        self._pipe_segment_temperature_config: Any = None  # °C or mapping — per-segment wall temperature config
        self.configure_headspace(headspace_config or {})

    @property
    def pipe_temperature_C(self) -> float:
        """Current liner temperature after applying the active recipe schedule."""

        return float(self._pipe_temperature_C)

    @pipe_temperature_C.setter
    def pipe_temperature_C(self, value: float) -> None:  # °C — requested pipe/liner wall temperature
        self._pipe_temperature_C = max(0.0, float(value))  # °C — clamped pipe/liner wall temperature
        self._liner_temperature_config = self._pipe_temperature_C  # °C — scalar liner temperature config
        self._pipe_segment_temperature_config = self._pipe_temperature_C  # °C — scalar segment temperature config

    def configure_headspace(self, config: Mapping) -> None:
        merged = dict(self.DEFAULT_HEADSPACE_CONFIG)
        merged.update(dict(config or {}))
        self._finite_headspace_enabled = bool(merged.get('enabled', False))  # dimensionless bool — finite-headspace switch
        self._headspace_volume_m3 = merged.get('volume_m3')  # m³ or None — finite headspace volume
        self._temperature_model = str(merged.get('temperature_model') or 'melt')  # unitless — headspace temperature basis
        self._temperature_offset_K = merged.get('temperature_offset_K')  # K or None — headspace temperature offset
        self._bleed_model = str(merged.get('bleed_model') or 'poiseuille')  # unitless — bleed conductance model
        self._conductance_override = merged.get('conductance_kg_s_per_bar')  # kg/s — legacy-named mass-flow override
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
    ) -> dict[str, float]:
        """Estimate pipe pressure/capacity with the existing Poiseuille model."""

        pipe_temperature_C = self.resolve_pipe_temperature_C(melt)  # °C — active pipe/liner wall temperature
        total_evap_kg_hr = max(0.0, float(evap_flux.total_kg_hr))  # kg/hr — total evaporation mass flow
        p_mean_Pa = max(float(melt.p_total_mbar) * 100.0, 1.0)  # Pa — total pressure; mbar -> Pa with 1 Pa floor
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
        conductance = self._pipe_conductance(  # kg/s — pipe mass-flow CAPACITY at p_mean (Poiseuille × ideal-gas ρ); ∝ p̄². NOT per-pressure.
            p_mean_Pa,
            conductance_temperature_C,
            species_kg_for_M_avg=evap_flux.species_kg_hr,  # kg/hr by species — composition basis for M_avg
        )
        pipe_conductance_kg_hr = conductance * 3600.0  # kg/hr — pipe capacity; kg/s -> kg/hr
        if pipe_conductance_kg_hr > 0.0:
            pipe_capacity_used_pct = (  # percent — evaporation flow divided by pipe mass-flow capacity
                total_evap_kg_hr / pipe_conductance_kg_hr * 100.0  # dimensionless -> percent
            )
        else:
            pipe_capacity_used_pct = 999.0 if total_evap_kg_hr > 0.0 else 0.0  # percent — saturated sentinel or zero-load
        if conductance > 0.0:
            vapor_pressure_mbar = (total_evap_kg_hr / 3600.0) / conductance * 10.0  # mbar (nominal) — coarse proxy: kg/hr -> kg/s, flux/flow-capacity ×10; NOT a rigorous partial pressure (see BH-063).
        else:
            vapor_pressure_mbar = 0.0  # mbar (nominal) — zero proxy pressure when pipe capacity is zero
        pressure_mbar = max(vapor_pressure_mbar, float(melt.p_total_mbar))  # mbar — reported total overhead pressure
        return {
            'pipe_temperature_C': pipe_temperature_C,  # °C — active pipe/liner wall temperature
            'conductance_temperature_C': conductance_temperature_C,  # °C — gas temperature used for conductance
            'p_mean_Pa': p_mean_Pa,  # Pa — mean pipe pressure
            'conductance_kg_s_per_bar': conductance,  # MISNOMER: value is kg/s (mass flow), not per-bar; name kept for API compat, to be corrected with BH-063.
            'pipe_conductance_kg_hr': pipe_conductance_kg_hr,  # kg/hr — pipe mass-flow capacity
            'pipe_capacity_used_pct': pipe_capacity_used_pct,  # percent — capacity used
            'vapor_pressure_mbar': vapor_pressure_mbar,  # mbar (nominal) — proxy pressure, not rigorous partial pressure
            'pressure_mbar': pressure_mbar,  # mbar — reported total overhead pressure
        }

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
               turbine_spec=None,
               actual_O2_kg_hr: float = 0.0,  # kg/hr — melt/offgas O2 mass flow
               actual_O2_mol_hr: Optional[float] = None,  # mol/hr — melt/offgas O2 molar flow
               mre_anode_O2_mol_hr: float = 0.0,  # mol/hr — MRE anode O2 flow
               overhead_holdup_mol: Optional[Mapping[str, float]] = None,  # mol by species — finite-headspace gas holdup
               existing_gas: Optional[OverheadGas] = None,
               headspace_volume_m3: Optional[float] = None,  # m³ — explicit finite headspace volume
               p_downstream_bar: Optional[float] = None,  # bar — explicit downstream/reference pressure
               bleed_conductance_kg_s_per_bar: Optional[float] = None  # MISNOMER: kg/s mass flow, not per-bar
               ) -> OverheadGas:
        """
        Calculate overhead gas state for this hour.

        Args:
            evap_flux:     Current evaporation rates from the melt
            melt:          Current melt state (T, atmosphere, pO₂)
            train:         Condensation train (for gas routing)
            turbine_spec:  TurbineSpec from equipment auto-design (optional).
                           If provided, enforces turbine max O₂ flow and
                           computes venting, shaft power, and transport
                           saturation metrics.
            actual_O2_kg_hr: Melt/offgas O₂ produced this hour, kg.
            actual_O2_mol_hr: Same melt/offgas O₂ flow in mol/hr. If omitted,
                              it is projected from kg.
            mre_anode_O2_mol_hr: MRE anode O₂ flow in mol/hr. Recorded as a
                                 separate source bin and not counted as
                                 turbine throughput.

        Returns:
            Updated OverheadGas with pressure, flow, and feedback data
        """
        gas = existing_gas if existing_gas is not None else OverheadGas()
        self._reset_gas(gas)

        # Total evaporation rate → pressure buildup
        total_evap_kg_hr = evap_flux.total_kg_hr  # kg/hr — total evaporation mass flow

        # ── Pipe conductance limit ──────────────────────── [PIPE-1]
        transport_state = self.estimate_transport_state(evap_flux, melt)  # mixed units — pipe transport state
        conductance = transport_state['conductance_kg_s_per_bar']  # kg/s — MISNOMER field value is mass flow, not per-bar
        gas.pipe_conductance_kg_hr = transport_state['pipe_conductance_kg_hr']  # kg/hr — pipe mass-flow capacity
        finite_conductance = self._resolve_bleed_conductance(  # kg/s — finite-headspace bleed mass-flow capacity
            conductance,
            bleed_conductance_kg_s_per_bar,
        )
        gas.bleed_conductance_kg_s_per_bar = finite_conductance  # MISNOMER: kg/s mass flow, not per-bar
        gas.p_downstream_bar = self._resolve_downstream_pressure(  # bar — downstream/reference pressure
            melt, p_downstream_bar)
        gas.headspace_volume_m3 = self._resolve_headspace_volume(  # m³ — finite headspace volume
            headspace_volume_m3)
        gas.headspace_temperature_K = self._headspace_temperature_K(melt)  # K — finite headspace gas temperature

        # ── Transport saturation ────────────────────────── [LOOP-3]
        # How much of the pipe capacity is being used.
        # >100% means evaporation exceeds transport → triggers ΔT/dt throttle.
        gas.transport_saturation_pct = transport_state['pipe_capacity_used_pct']  # percent — pipe capacity used

        gas.evap_exceeds_transport = gas.transport_saturation_pct > 100.0  # dimensionless bool — transport over-capacity flag

        if self._finite_headspace_enabled:
            self._update_finite_headspace(
                gas,
                melt,
                overhead_holdup_mol or {},
                actual_O2_kg_hr=actual_O2_kg_hr,  # kg/hr — melt/offgas O2 mass flow
                actual_O2_mol_hr=actual_O2_mol_hr,  # mol/hr — melt/offgas O2 molar flow
                mre_anode_O2_mol_hr=mre_anode_O2_mol_hr,  # mol/hr — MRE anode O2 flow
                turbine_spec=turbine_spec,
            )
            return gas

        # ── Overhead pressure ───────────────────────────────────────
        # P_vapor ≈ (evap_rate / conductance) × characteristic pressure.
        # Total pressure may include a non-condensable background gas
        # such as Mars CO2; product partial pressures should not inherit
        # that background pressure.
        vapor_pressure_mbar = transport_state['vapor_pressure_mbar']  # mbar (nominal) — proxy pressure, not rigorous partial pressure
        gas.pressure_mbar = transport_state['pressure_mbar']  # mbar — reported total overhead pressure

        # ── Product partial pressures (proportional to evaporation rates) ──
        if total_evap_kg_hr > 0:
            for sp, rate in evap_flux.species_kg_hr.items():  # kg/hr — species evaporation mass flow
                gas.composition[sp] = (rate / total_evap_kg_hr  # mbar — species proxy partial pressure
                                        * vapor_pressure_mbar)  # mbar — species proxy partial pressure

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

        # ── Turbine flow + capacity enforcement ─────────── [LOOP-2]
        O2_flow_kg_hr = max(0.0, actual_O2_kg_hr)  # kg/hr — melt/offgas O2 mass flow
        O2_flow_mol_hr = (  # mol/hr — melt/offgas O2 molar flow
            max(0.0, float(actual_O2_mol_hr))
            if actual_O2_mol_hr is not None
            else O2_flow_kg_hr / O2_KG_PER_MOL  # kg/hr / kg/mol -> mol/hr
        )
        gas.melt_offgas_O2_mol_hr = O2_flow_mol_hr  # mol/hr — melt/offgas O2 source flow
        gas.mre_anode_O2_mol_hr = max(0.0, float(mre_anode_O2_mol_hr))  # mol/hr — MRE anode O2 source flow
        gas.turbine_flow_kg_hr = O2_flow_kg_hr  # kg/hr — O2 turbine mass flow before capacity cap
        gas.turbine_flow_mol_hr = O2_flow_mol_hr  # mol/hr — O2 turbine molar flow before capacity cap

        if turbine_spec is not None and turbine_spec.max_O2_flow_kg_hr > 0:
            max_O2 = turbine_spec.max_O2_flow_kg_hr  # kg/hr — turbine O2 mass-flow capacity

            # Turbine utilization
            gas.turbine_utilization_pct = (
                O2_flow_kg_hr / max_O2 * 100.0) if max_O2 > 0 else 0.0  # percent — dimensionless utilization -> percent

            if O2_flow_kg_hr > max_O2:
                # Turbine is overloaded: cap compressed flow and vent excess.
                gas.turbine_limited = True  # dimensionless bool — turbine capacity exceeded
                gas.O2_vented_kg_hr = O2_flow_kg_hr - max_O2  # kg/hr — O2 flow above turbine capacity
                gas.O2_vented_mol_hr = gas.O2_vented_kg_hr / O2_KG_PER_MOL  # mol/hr — kg/hr / kg/mol
                gas.turbine_flow_kg_hr = max_O2  # kg/hr — capped compressed O2 mass flow
                gas.turbine_flow_mol_hr = max_O2 / O2_KG_PER_MOL  # mol/hr — kg/hr / kg/mol
            else:
                gas.turbine_limited = False  # dimensionless bool — turbine capacity not exceeded
                gas.O2_vented_kg_hr = 0.0  # kg/hr — no vented O2 mass flow
                gas.O2_vented_mol_hr = 0.0  # mol/hr — no vented O2 molar flow

            # ── Shaft power calculation ─────────────────────── [EQ-5]
            # W = (γ/(γ-1)) × ṁ × R_specific × T × [(p₂/p₁)^((γ-1)/γ) - 1] / η
            # Simplified: ~0.02 kWh/kg O₂ from 1 mbar to 3 bar
            # Actual shaft power scales with the capped flow
            gas.turbine_shaft_power_kW = gas.turbine_flow_kg_hr * 0.02  # kW — kg/hr × 0.02 kWh/kg

        else:
            # No turbine spec — no capacity enforcement
            gas.turbine_utilization_pct = 0.0  # percent — no turbine capacity basis
            gas.turbine_limited = False  # dimensionless bool — no turbine capacity basis
            gas.O2_vented_kg_hr = 0.0  # kg/hr — no vented O2 mass flow
            gas.O2_vented_mol_hr = 0.0  # mol/hr — no vented O2 molar flow
            gas.turbine_shaft_power_kW = O2_flow_kg_hr * 0.02  # kW — kg/hr × 0.02 kWh/kg

        return gas

    def _update_finite_headspace(self, gas: OverheadGas, melt: MeltState,
                                 overhead_holdup_mol: Mapping[str, float],
                                 *,
                                 actual_O2_kg_hr: float,  # kg/hr — melt/offgas O2 mass flow
                                 actual_O2_mol_hr: Optional[float],  # mol/hr — melt/offgas O2 molar flow
                                 mre_anode_O2_mol_hr: float,  # mol/hr — MRE anode O2 flow
                                 turbine_spec) -> None:
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

        self._update_turbine_fields(
            gas,
            turbine_spec=turbine_spec,
            actual_O2_kg_hr=actual_O2_kg_hr,  # kg/hr — melt/offgas O2 mass flow
            actual_O2_mol_hr=actual_O2_mol_hr,  # mol/hr — melt/offgas O2 molar flow
            mre_anode_O2_mol_hr=mre_anode_O2_mol_hr,  # mol/hr — MRE anode O2 flow
        )

    def _update_turbine_fields(self, gas: OverheadGas, *, turbine_spec,
                               actual_O2_kg_hr: float,  # kg/hr — melt/offgas O2 mass flow
                               actual_O2_mol_hr: Optional[float],  # mol/hr — melt/offgas O2 molar flow
                               mre_anode_O2_mol_hr: float) -> None:  # mol/hr — MRE anode O2 flow
        O2_flow_kg_hr = max(0.0, actual_O2_kg_hr)  # kg/hr — melt/offgas O2 mass flow
        O2_flow_mol_hr = (  # mol/hr — melt/offgas O2 molar flow
            max(0.0, float(actual_O2_mol_hr))
            if actual_O2_mol_hr is not None
            else O2_flow_kg_hr / O2_KG_PER_MOL  # kg/hr / kg/mol -> mol/hr
        )
        gas.melt_offgas_O2_mol_hr = O2_flow_mol_hr  # mol/hr — melt/offgas O2 source flow
        gas.mre_anode_O2_mol_hr = max(0.0, float(mre_anode_O2_mol_hr))  # mol/hr — MRE anode O2 source flow
        gas.turbine_flow_kg_hr = O2_flow_kg_hr  # kg/hr — O2 turbine mass flow before capacity cap
        gas.turbine_flow_mol_hr = O2_flow_mol_hr  # mol/hr — O2 turbine molar flow before capacity cap

        if turbine_spec is not None and turbine_spec.max_O2_flow_kg_hr > 0:
            max_O2 = turbine_spec.max_O2_flow_kg_hr  # kg/hr — turbine O2 mass-flow capacity
            gas.turbine_utilization_pct = (
                O2_flow_kg_hr / max_O2 * 100.0) if max_O2 > 0 else 0.0  # percent — dimensionless utilization -> percent
            if O2_flow_kg_hr > max_O2:
                gas.turbine_limited = True  # dimensionless bool — turbine capacity exceeded
                gas.O2_vented_kg_hr = O2_flow_kg_hr - max_O2  # kg/hr — O2 flow above turbine capacity
                gas.O2_vented_mol_hr = gas.O2_vented_kg_hr / O2_KG_PER_MOL  # mol/hr — kg/hr / kg/mol
                gas.turbine_flow_kg_hr = max_O2  # kg/hr — capped compressed O2 mass flow
                gas.turbine_flow_mol_hr = max_O2 / O2_KG_PER_MOL  # mol/hr — kg/hr / kg/mol
            else:
                gas.turbine_limited = False  # dimensionless bool — turbine capacity not exceeded
                gas.O2_vented_kg_hr = 0.0  # kg/hr — no vented O2 mass flow
                gas.O2_vented_mol_hr = 0.0  # mol/hr — no vented O2 molar flow
            gas.turbine_shaft_power_kW = gas.turbine_flow_kg_hr * 0.02  # kW — kg/hr × 0.02 kWh/kg
        else:
            gas.turbine_utilization_pct = 0.0  # percent — no turbine capacity basis
            gas.turbine_limited = False  # dimensionless bool — no turbine capacity basis
            gas.O2_vented_kg_hr = 0.0  # kg/hr — no vented O2 mass flow
            gas.O2_vented_mol_hr = 0.0  # mol/hr — no vented O2 molar flow
            gas.turbine_shaft_power_kW = O2_flow_kg_hr * 0.02  # kW — kg/hr × 0.02 kWh/kg

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
            return max(0.0, float(explicit))  # bar — explicit downstream/reference pressure
        if self._downstream_pressure_override is not None:
            return max(0.0, float(self._downstream_pressure_override))  # bar — configured downstream/reference pressure
        atmosphere_name = getattr(melt.atmosphere, 'name', '')
        if atmosphere_name in {
            'CONTROLLED_O2',
            'CONTROLLED_O2_FLOW',
            'O2_BACKPRESSURE',
        }:
            return max(0.0, float(melt.pO2_mbar) / 1000.0)  # bar — O2 setpoint; mbar -> bar
        return 0.0  # bar — vacuum downstream/reference pressure

    def _pipe_conductance(
        self,
        p_mean_Pa: float,  # Pa — mean pipe pressure
        T_C: float,  # °C — pipe gas temperature
        *,
        species_kg_for_M_avg: Optional[Mapping[str, float]] = None,  # kg or kg/hr by species — M_avg basis
    ) -> float:
        """
        Poiseuille conductance of the collection pipe.

        C = π × d⁴ × p̄ / (128 × η × L)                   [PIPE-1]

        At millibar pressures and 1400+°C, the flow is in the
        viscous regime (Knudsen number Kn << 0.01).

        Args:
            p_mean_Pa: Mean pressure in the pipe (Pa)
            T_C:       Pipe temperature (°C)
            species_kg_for_M_avg: optional mapping of species → mass
                for deriving the live mole-weighted M_avg. Pass the
                evap-flux species mass-rate to track real-recipe
                composition. ``None`` / empty falls back to
                ``DEFAULT_PIPE_M_AVG_KG_MOL`` (~0.040 kg/mol) which
                matches the historical hardcoded value.

        Returns:
            Conductance in kg/s (mass flow per unit pressure drop)

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
        # (degenerate pipe geometry; conductance is 0). p_mean_Pa
        # < 0 is unphysical; clamp to 0.
        T_K = T_C + CELSIUS_TO_KELVIN_OFFSET  # K — pipe gas temperature; °C -> K
        if T_K <= 0.0 or L <= 0.0 or d <= 0.0:
            return 0.0
        p_mean_Pa = max(0.0, float(p_mean_Pa))  # Pa — clamped mean pipe pressure

        # Dynamic viscosity of gas mixture (approximate as N₂-like)
        # η ≈ 4e-5 Pa·s at 1500°C (increases with T for gases)
        eta = 1.8e-5 * (T_K / 300.0) ** 0.7  # Pa·s — dynamic viscosity; 1.8e-5 Pa·s at 300 K, exponent dimensionless

        # Volumetric conductance (m³/s)
        C_vol = math.pi * d**4 * p_mean_Pa / (128.0 * eta * L)  # m³/s — volumetric conductance; 128 dimensionless

        # Convert to mass conductance (kg/s)
        # Using ideal gas: ρ = p × M / (R × T)
        M_avg = _mean_molar_mass_kg_mol(species_kg_for_M_avg)  # kg/mol — mole-weighted gas molar mass
        rho = p_mean_Pa * M_avg / (8.314 * T_K)  # kg/m³ — ideal-gas density; 8.314 J/(mol·K)

        return C_vol * rho  # kg/s — pipe mass-flow capacity at p_mean
