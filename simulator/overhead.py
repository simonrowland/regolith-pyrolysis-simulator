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
from typing import Mapping, Optional

from simulator.core import (
    EvaporationFlux, MeltState, OverheadGas, CondensationTrain,
)
from simulator.state import GAS_CONSTANT, MOLAR_MASS

O2_KG_PER_MOL = MOLAR_MASS['O2'] / 1000.0


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
    }

    def __init__(self, headspace_config: Optional[Mapping] = None):
        # Pipe geometry (default for 1-tonne batch)
        self.pipe_diameter_m = 0.12      # 12 cm
        self.pipe_length_m = 1.0         # crucible to first condenser
        self.pipe_temperature_C = 1500   # hot-walled
        self.configure_headspace(headspace_config or {})

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
        p_mean_Pa = max(melt.p_total_mbar * 100.0, 1.0)  # at least 1 Pa
        conductance = self._pipe_conductance(p_mean_Pa,
                                               melt.temperature_C)
        gas.pipe_conductance_kg_hr = conductance * 3600.0  # kg/s → kg/hr
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
        if gas.pipe_conductance_kg_hr > 0:
            gas.transport_saturation_pct = (
                total_evap_kg_hr / gas.pipe_conductance_kg_hr * 100.0)
        else:
            gas.transport_saturation_pct = 999.0 if total_evap_kg_hr > 0 else 0.0

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
        if conductance > 0:
            vapor_pressure_mbar = (
                (total_evap_kg_hr / 3600.0) / conductance * 10.0)
        else:
            vapor_pressure_mbar = 0.0

        gas.pressure_mbar = max(vapor_pressure_mbar, melt.p_total_mbar)

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
