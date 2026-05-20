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

gives >300× suppression of SiO vapor pressure when
pO₂ is raised from hard vacuum (~1e-9 bar) to ~1 mbar.

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
from simulator.state import GAS_CONSTANT, MOLAR_MASS

O2_KG_PER_MOL = MOLAR_MASS['O2'] / 1000.0
DEFAULT_PIPE_TEMPERATURE_C = 1500.0


class OverheadGasModel:
    """
    Calculates gas composition and flow conditions above the melt.

    Updates the OverheadGas state each simulation hour based on
    evaporation flux, atmosphere settings, pipe geometry, and
    turbine capacity limits.
    """

    DEFAULT_HEADSPACE_CONFIG = {
        'enabled': False,
        'volume_m3': None,
        'temperature_model': 'melt',
        'temperature_offset_K': None,
        'bleed_model': 'poiseuille',
        'conductance_kg_s_per_bar': None,
        'downstream_pressure_bar': None,
        'liner_temperature_C': DEFAULT_PIPE_TEMPERATURE_C,
    }

    def __init__(self, headspace_config: Optional[Mapping] = None):
        # Pipe geometry (default for 1-tonne batch)
        self.pipe_diameter_m = 0.12      # 12 cm
        self.pipe_length_m = 1.0         # crucible to first condenser
        self._pipe_temperature_C = DEFAULT_PIPE_TEMPERATURE_C
        self._liner_temperature_config: Any = DEFAULT_PIPE_TEMPERATURE_C
        self.configure_headspace(headspace_config or {})

    @property
    def pipe_temperature_C(self) -> float:
        """Current liner temperature after applying the active recipe schedule."""

        return float(self._pipe_temperature_C)

    @pipe_temperature_C.setter
    def pipe_temperature_C(self, value: float) -> None:
        self._pipe_temperature_C = max(0.0, float(value))
        self._liner_temperature_config = self._pipe_temperature_C

    def configure_headspace(self, config: Mapping) -> None:
        merged = dict(self.DEFAULT_HEADSPACE_CONFIG)
        merged.update(dict(config or {}))
        self._finite_headspace_enabled = bool(merged.get('enabled', False))
        self._headspace_volume_m3 = merged.get('volume_m3')
        self._temperature_model = str(merged.get('temperature_model') or 'melt')
        self._temperature_offset_K = merged.get('temperature_offset_K')
        self._bleed_model = str(merged.get('bleed_model') or 'poiseuille')
        self._conductance_override = merged.get('conductance_kg_s_per_bar')
        self._downstream_pressure_override = merged.get('downstream_pressure_bar')
        self._liner_temperature_config = merged.get(
            'liner_temperature_C',
            merged.get('pipe_temperature_C', DEFAULT_PIPE_TEMPERATURE_C),
        )
        self._pipe_temperature_C = self.resolve_pipe_temperature_C()

    def resolve_pipe_temperature_C(self, melt: Optional[MeltState] = None) -> float:
        """Resolve scalar or scheduled liner temperature for the current tick."""

        value = self._resolve_liner_temperature_value(
            self._liner_temperature_config,
            melt,
        )
        self._pipe_temperature_C = max(0.0, float(value))
        return self._pipe_temperature_C

    def estimate_transport_state(
        self,
        evap_flux: EvaporationFlux,
        melt: MeltState,
    ) -> dict[str, float]:
        """Estimate pipe pressure/capacity with the existing Poiseuille model."""

        pipe_temperature_C = self.resolve_pipe_temperature_C(melt)
        total_evap_kg_hr = max(0.0, float(evap_flux.total_kg_hr))
        p_mean_Pa = max(float(melt.p_total_mbar) * 100.0, 1.0)
        # Preserve the existing gas-transport path: Poiseuille conductance has
        # historically used melt/gas temperature. The liner trajectory controls
        # wall deposition and Kn diagnostics without changing evaporation totals.
        conductance_temperature_C = float(melt.temperature_C)
        conductance = self._pipe_conductance(p_mean_Pa, conductance_temperature_C)
        pipe_conductance_kg_hr = conductance * 3600.0
        if pipe_conductance_kg_hr > 0.0:
            pipe_capacity_used_pct = (
                total_evap_kg_hr / pipe_conductance_kg_hr * 100.0
            )
        else:
            pipe_capacity_used_pct = 999.0 if total_evap_kg_hr > 0.0 else 0.0
        if conductance > 0.0:
            vapor_pressure_mbar = (total_evap_kg_hr / 3600.0) / conductance * 10.0
        else:
            vapor_pressure_mbar = 0.0
        pressure_mbar = max(vapor_pressure_mbar, float(melt.p_total_mbar))
        return {
            'pipe_temperature_C': pipe_temperature_C,
            'conductance_temperature_C': conductance_temperature_C,
            'p_mean_Pa': p_mean_Pa,
            'conductance_kg_s_per_bar': conductance,
            'pipe_conductance_kg_hr': pipe_conductance_kg_hr,
            'pipe_capacity_used_pct': pipe_capacity_used_pct,
            'vapor_pressure_mbar': vapor_pressure_mbar,
            'pressure_mbar': pressure_mbar,
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

        default_C = self._optional_float(
            config.get('default_C'),
            DEFAULT_PIPE_TEMPERATURE_C,
        )
        schedule = config.get('schedule', ())
        if isinstance(schedule, Mapping):
            schedule = schedule.get('segments', ())
        if not isinstance(schedule, (list, tuple)):
            return default_C

        campaign_name = ''
        campaign_hour = 0.0
        absolute_hour = 0.0
        if melt is not None:
            campaign = getattr(melt, 'campaign', None)
            campaign_name = str(getattr(campaign, 'name', campaign) or '')
            campaign_hour = self._optional_float(
                getattr(melt, 'campaign_hour', 0.0), 0.0)
            absolute_hour = self._optional_float(getattr(melt, 'hour', 0.0), 0.0)

        selected = None
        for segment in schedule:
            if not isinstance(segment, Mapping):
                continue
            if not self._campaign_matches(segment.get('campaign'), campaign_name):
                continue
            hour_basis = str(segment.get('hour_basis') or 'campaign')
            hour = absolute_hour if hour_basis == 'absolute' else campaign_hour
            start_hour = self._optional_float(
                segment.get('from_campaign_hour', segment.get('start_hour')),
                0.0,
            )
            end_raw = segment.get('to_campaign_hour', segment.get('end_hour'))
            end_hour = None if end_raw is None else self._optional_float(end_raw, 0.0)
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
        if configured in (None, '', '*'):
            return True
        if isinstance(configured, str):
            return configured == campaign_name
        if isinstance(configured, (list, tuple, set)):
            return campaign_name in {str(item) for item in configured}
        return False

    def _interpolate_liner_segment(
        self,
        segment: Mapping,
        hour: float,
        start_hour: float,
        end_hour: Optional[float],
    ) -> float:
        start_C = self._optional_float(
            segment.get('start_C', segment.get('temperature_C')),
            DEFAULT_PIPE_TEMPERATURE_C,
        )
        end_C = self._optional_float(segment.get('end_C'), start_C)
        if end_hour is None or end_hour <= start_hour:
            return end_C
        fraction = max(0.0, min(1.0, (hour - start_hour) / (end_hour - start_hour)))
        return start_C + (end_C - start_C) * fraction

    def update(self, evap_flux: EvaporationFlux,
               melt: MeltState,
               train: CondensationTrain,
               turbine_spec=None,
               actual_O2_kg_hr: float = 0.0,
               actual_O2_mol_hr: Optional[float] = None,
               mre_anode_O2_mol_hr: float = 0.0,
               overhead_holdup_mol: Optional[Mapping[str, float]] = None,
               existing_gas: Optional[OverheadGas] = None,
               headspace_volume_m3: Optional[float] = None,
               p_downstream_bar: Optional[float] = None,
               bleed_conductance_kg_s_per_bar: Optional[float] = None
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
        total_evap_kg_hr = evap_flux.total_kg_hr

        # ── Pipe conductance limit ──────────────────────── [PIPE-1]
        transport_state = self.estimate_transport_state(evap_flux, melt)
        conductance = transport_state['conductance_kg_s_per_bar']
        gas.pipe_conductance_kg_hr = transport_state['pipe_conductance_kg_hr']
        finite_conductance = self._resolve_bleed_conductance(
            conductance,
            bleed_conductance_kg_s_per_bar,
        )
        gas.bleed_conductance_kg_s_per_bar = finite_conductance
        gas.p_downstream_bar = self._resolve_downstream_pressure(
            melt, p_downstream_bar)
        gas.headspace_volume_m3 = self._resolve_headspace_volume(
            headspace_volume_m3)
        gas.headspace_temperature_K = self._headspace_temperature_K(melt)

        # ── Transport saturation ────────────────────────── [LOOP-3]
        # How much of the pipe capacity is being used.
        # >100% means evaporation exceeds transport → triggers ΔT/dt throttle.
        gas.transport_saturation_pct = transport_state['pipe_capacity_used_pct']

        gas.evap_exceeds_transport = gas.transport_saturation_pct > 100.0

        if self._finite_headspace_enabled:
            self._update_finite_headspace(
                gas,
                melt,
                overhead_holdup_mol or {},
                actual_O2_kg_hr=actual_O2_kg_hr,
                actual_O2_mol_hr=actual_O2_mol_hr,
                mre_anode_O2_mol_hr=mre_anode_O2_mol_hr,
                turbine_spec=turbine_spec,
            )
            return gas

        # ── Overhead pressure ───────────────────────────────────────
        # P_vapor ≈ (evap_rate / conductance) × characteristic pressure.
        # Total pressure may include a non-condensable background gas
        # such as Mars CO2; product partial pressures should not inherit
        # that background pressure.
        vapor_pressure_mbar = transport_state['vapor_pressure_mbar']
        gas.pressure_mbar = transport_state['pressure_mbar']

        # ── Product partial pressures (proportional to evaporation rates) ──
        if total_evap_kg_hr > 0:
            for sp, rate in evap_flux.species_kg_hr.items():
                gas.composition[sp] = (rate / total_evap_kg_hr
                                        * vapor_pressure_mbar)

        # Controlled/background atmosphere partial pressures.
        if melt.pO2_mbar > 0.001:
            gas.composition['O2'] = max(
                gas.composition.get('O2', 0.0), melt.pO2_mbar)

        atmosphere_name = getattr(melt.atmosphere, 'name', '')
        if atmosphere_name == 'CO2_BACKPRESSURE' and melt.p_total_mbar > 0:
            gas.composition['CO2'] = max(
                gas.composition.get('CO2', 0.0), melt.p_total_mbar * 0.96)
        elif atmosphere_name == 'PN2_SWEEP' and melt.p_total_mbar > 0:
            gas.composition['N2'] = max(
                gas.composition.get('N2', 0.0),
                max(0.0, melt.p_total_mbar - melt.pO2_mbar))

        # ── Turbine flow + capacity enforcement ─────────── [LOOP-2]
        O2_flow_kg_hr = max(0.0, actual_O2_kg_hr)
        O2_flow_mol_hr = (
            max(0.0, float(actual_O2_mol_hr))
            if actual_O2_mol_hr is not None
            else O2_flow_kg_hr / O2_KG_PER_MOL
        )
        gas.melt_offgas_O2_mol_hr = O2_flow_mol_hr
        gas.mre_anode_O2_mol_hr = max(0.0, float(mre_anode_O2_mol_hr))
        gas.turbine_flow_kg_hr = O2_flow_kg_hr
        gas.turbine_flow_mol_hr = O2_flow_mol_hr

        if turbine_spec is not None and turbine_spec.max_O2_flow_kg_hr > 0:
            max_O2 = turbine_spec.max_O2_flow_kg_hr

            # Turbine utilization
            gas.turbine_utilization_pct = (
                O2_flow_kg_hr / max_O2 * 100.0) if max_O2 > 0 else 0.0

            if O2_flow_kg_hr > max_O2:
                # Turbine is overloaded: cap compressed flow and vent excess.
                gas.turbine_limited = True
                gas.O2_vented_kg_hr = O2_flow_kg_hr - max_O2
                gas.O2_vented_mol_hr = gas.O2_vented_kg_hr / O2_KG_PER_MOL
                gas.turbine_flow_kg_hr = max_O2  # Only this much gets compressed
                gas.turbine_flow_mol_hr = max_O2 / O2_KG_PER_MOL
            else:
                gas.turbine_limited = False
                gas.O2_vented_kg_hr = 0.0
                gas.O2_vented_mol_hr = 0.0

            # ── Shaft power calculation ─────────────────────── [EQ-5]
            # W = (γ/(γ-1)) × ṁ × R_specific × T × [(p₂/p₁)^((γ-1)/γ) - 1] / η
            # Simplified: ~0.02 kWh/kg O₂ from 1 mbar to 3 bar
            # Actual shaft power scales with the capped flow
            gas.turbine_shaft_power_kW = gas.turbine_flow_kg_hr * 0.02  # kW (≈kWh/hr)

        else:
            # No turbine spec — no capacity enforcement
            gas.turbine_utilization_pct = 0.0
            gas.turbine_limited = False
            gas.O2_vented_kg_hr = 0.0
            gas.O2_vented_mol_hr = 0.0
            gas.turbine_shaft_power_kW = O2_flow_kg_hr * 0.02

        return gas

    def _update_finite_headspace(self, gas: OverheadGas, melt: MeltState,
                                 overhead_holdup_mol: Mapping[str, float],
                                 *,
                                 actual_O2_kg_hr: float,
                                 actual_O2_mol_hr: Optional[float],
                                 mre_anode_O2_mol_hr: float,
                                 turbine_spec) -> None:
        partials_bar = self._compute_partial_pressures(
            overhead_holdup_mol,
            gas.headspace_volume_m3,
            gas.headspace_temperature_K,
        )
        gas.pressure_mbar = sum(partials_bar.values()) * 1000.0
        gas.composition.update({
            species: p_bar * 1000.0
            for species, p_bar in partials_bar.items()
            if p_bar > 0.0
        })

        atmosphere_name = getattr(melt.atmosphere, 'name', '')
        if atmosphere_name == 'CO2_BACKPRESSURE' and melt.p_total_mbar > 0:
            gas.pressure_mbar = max(gas.pressure_mbar, melt.p_total_mbar)
            gas.composition['CO2'] = max(
                gas.composition.get('CO2', 0.0), melt.p_total_mbar * 0.96)
        elif atmosphere_name == 'PN2_SWEEP' and melt.p_total_mbar > 0:
            gas.pressure_mbar = max(gas.pressure_mbar, melt.p_total_mbar)
            gas.composition['N2'] = max(
                gas.composition.get('N2', 0.0),
                max(0.0, melt.p_total_mbar - melt.pO2_mbar))

        self._update_turbine_fields(
            gas,
            turbine_spec=turbine_spec,
            actual_O2_kg_hr=actual_O2_kg_hr,
            actual_O2_mol_hr=actual_O2_mol_hr,
            mre_anode_O2_mol_hr=mre_anode_O2_mol_hr,
        )

    def _update_turbine_fields(self, gas: OverheadGas, *, turbine_spec,
                               actual_O2_kg_hr: float,
                               actual_O2_mol_hr: Optional[float],
                               mre_anode_O2_mol_hr: float) -> None:
        O2_flow_kg_hr = max(0.0, actual_O2_kg_hr)
        O2_flow_mol_hr = (
            max(0.0, float(actual_O2_mol_hr))
            if actual_O2_mol_hr is not None
            else O2_flow_kg_hr / O2_KG_PER_MOL
        )
        gas.melt_offgas_O2_mol_hr = O2_flow_mol_hr
        gas.mre_anode_O2_mol_hr = max(0.0, float(mre_anode_O2_mol_hr))
        gas.turbine_flow_kg_hr = O2_flow_kg_hr
        gas.turbine_flow_mol_hr = O2_flow_mol_hr

        if turbine_spec is not None and turbine_spec.max_O2_flow_kg_hr > 0:
            max_O2 = turbine_spec.max_O2_flow_kg_hr
            gas.turbine_utilization_pct = (
                O2_flow_kg_hr / max_O2 * 100.0) if max_O2 > 0 else 0.0
            if O2_flow_kg_hr > max_O2:
                gas.turbine_limited = True
                gas.O2_vented_kg_hr = O2_flow_kg_hr - max_O2
                gas.O2_vented_mol_hr = gas.O2_vented_kg_hr / O2_KG_PER_MOL
                gas.turbine_flow_kg_hr = max_O2
                gas.turbine_flow_mol_hr = max_O2 / O2_KG_PER_MOL
            else:
                gas.turbine_limited = False
                gas.O2_vented_kg_hr = 0.0
                gas.O2_vented_mol_hr = 0.0
            gas.turbine_shaft_power_kW = gas.turbine_flow_kg_hr * 0.02
        else:
            gas.turbine_utilization_pct = 0.0
            gas.turbine_limited = False
            gas.O2_vented_kg_hr = 0.0
            gas.O2_vented_mol_hr = 0.0
            gas.turbine_shaft_power_kW = O2_flow_kg_hr * 0.02

    @staticmethod
    def _reset_gas(gas: OverheadGas) -> None:
        gas.pressure_mbar = 0.0
        gas.composition.clear()
        gas.turbine_flow_kg_hr = 0.0
        gas.turbine_flow_mol_hr = 0.0
        gas.pipe_conductance_kg_hr = 50.0
        gas.turbine_limited = False
        gas.O2_vented_kg_hr = 0.0
        gas.O2_vented_mol_hr = 0.0
        gas.melt_offgas_O2_mol_hr = 0.0
        gas.mre_anode_O2_mol_hr = 0.0
        gas.turbine_utilization_pct = 0.0
        gas.turbine_shaft_power_kW = 0.0
        gas.evap_exceeds_transport = False
        gas.transport_saturation_pct = 0.0

    @staticmethod
    def _compute_partial_pressures(holdup_mol: Mapping[str, float],
                                   volume_m3: float,
                                   temperature_K: float) -> dict[str, float]:
        if volume_m3 <= 0.0 or temperature_K <= 0.0:
            return {}
        scale = GAS_CONSTANT * temperature_K / (volume_m3 * 1.0e5)
        return {
            str(species): max(0.0, float(mol)) * scale
            for species, mol in dict(holdup_mol or {}).items()
            if max(0.0, float(mol)) > 0.0
        }

    def _resolve_headspace_volume(self, explicit: Optional[float]) -> float:
        if explicit is not None:
            return max(0.0, float(explicit))
        if self._headspace_volume_m3 is not None:
            return max(0.0, float(self._headspace_volume_m3))
        return 0.085

    def _headspace_temperature_K(self, melt: MeltState) -> float:
        melt_T_K = float(melt.temperature_C) + 273.15
        offset = self._temperature_offset_K
        if offset is not None:
            return max(1.0, melt_T_K + float(offset))
        if self._temperature_model == 'lumped':
            return max(1.0, melt_T_K - 100.0)
        return max(1.0, melt_T_K)

    def _resolve_bleed_conductance(self, derived_kg_s: float,
                                   explicit: Optional[float]) -> float:
        if explicit is not None:
            return max(0.0, float(explicit))
        if self._conductance_override is not None:
            return max(0.0, float(self._conductance_override))
        if self._bleed_model == 'constant':
            return max(0.0, float(derived_kg_s))
        return max(0.0, float(derived_kg_s))

    def _resolve_downstream_pressure(self, melt: MeltState,
                                     explicit: Optional[float]) -> float:
        if explicit is not None:
            return max(0.0, float(explicit))
        if self._downstream_pressure_override is not None:
            return max(0.0, float(self._downstream_pressure_override))
        atmosphere_name = getattr(melt.atmosphere, 'name', '')
        if atmosphere_name in {
            'CONTROLLED_O2',
            'CONTROLLED_O2_FLOW',
            'O2_BACKPRESSURE',
        }:
            return max(0.0, float(melt.pO2_mbar) / 1000.0)
        return 0.0

    def _pipe_conductance(self, p_mean_Pa: float,
                           T_C: float) -> float:
        """
        Poiseuille conductance of the collection pipe.

        C = π × d⁴ × p̄ / (128 × η × L)                   [PIPE-1]

        At millibar pressures and 1400+°C, the flow is in the
        viscous regime (Knudsen number Kn << 0.01).

        Args:
            p_mean_Pa: Mean pressure in the pipe (Pa)
            T_C:       Pipe temperature (°C)

        Returns:
            Conductance in kg/s (mass flow per unit pressure drop)
        """
        d = self.pipe_diameter_m
        L = self.pipe_length_m

        # Dynamic viscosity of gas mixture (approximate as N₂-like)
        # η ≈ 4e-5 Pa·s at 1500°C (increases with T for gases)
        T_K = T_C + 273.15
        eta = 1.8e-5 * (T_K / 300.0) ** 0.7  # Sutherland approximation

        # Volumetric conductance (m³/s)
        C_vol = math.pi * d**4 * p_mean_Pa / (128.0 * eta * L)

        # Convert to mass conductance (kg/s)
        # Using ideal gas: ρ = p × M / (R × T)
        M_avg = 0.040  # kg/mol (mix of SiO, Fe, Na vapors ~40 g/mol)
        rho = p_mean_Pa * M_avg / (8.314 * T_K)

        return C_vol * rho
