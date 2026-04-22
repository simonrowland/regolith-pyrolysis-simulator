"""
Equipment Auto-Design Tool
============================

★ TIER 2: SCIENTIST-READABLE ★

Given a batch mass, feedstock, and peak campaign temperature,
automatically sizes all major refinery equipment:

    - Crucible (volume, diameter, freeboard)
    - Solar concentrator (aperture area, thermal power)
    - Collection pipe (diameter, conductance)
    - Condensation stages (volume, baffles, surface area)
    - Turbine-compressor (compression power, O₂ throughput)
    - Buffer tanks (O₂ accumulator volume)

The auto-design runs once when the user sets batch mass/feedstock.
The resulting PlantDesign is:
    1. Used as parameters for the simulation
    2. Displayed in the UI disclosure triangles
    3. Editable by the user (override any auto-calculated value)

Reference scale:
    100 m² concentrator → ~136 kW → appropriate for ~1 tonne batch
    12 cm pipe → 7-16 g/s SiO at 10 mbar pN₂

Key equations:
    Crucible volume:   V = m / ρ_melt, h = 1.5d, +20% freeboard    [EQ-1]
    Concentrator:      P = m×c_p×dT/dt + σ×ε×A×T⁴                   [EQ-2]
    Pipe conductance:  C = π×d⁴×p̄/(128×η×L)  [Poiseuille]          [EQ-3]
    Condenser sizing:  A = Q/(U×ΔT_lm)                              [EQ-4]
    Turbine power:     W = (γ/(γ-1))×nRT×[(p₂/p₁)^((γ-1)/γ) - 1]  [EQ-5]
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class CrucibleSpec:
    """Crucible dimensions."""
    volume_m3: float = 0.0
    diameter_m: float = 0.0
    height_m: float = 0.0
    melt_surface_area_m2: float = 0.0
    freeboard_pct: float = 20.0


@dataclass
class ConcentratorSpec:
    """Solar concentrator sizing."""
    aperture_m2: float = 0.0
    thermal_power_kW: float = 0.0
    peak_flux_kW_m2: float = 1.361  # Lunar insolation
    efficiency: float = 0.85


@dataclass
class PipeSpec:
    """Collection pipe sizing."""
    diameter_m: float = 0.12
    length_m: float = 1.0
    conductance_kg_s: float = 0.0
    max_transport_g_s: float = 0.0


@dataclass
class CondenserSpec:
    """Single condensation stage sizing."""
    stage_number: int = 0
    volume_m3: float = 0.0
    surface_area_m2: float = 0.0
    n_baffles: int = 0
    baffle_spacing_m: float = 0.0


@dataclass
class TurbineSpec:
    """
    Turbine-compressor sizing.

    The turbine compresses O₂ from overhead mbar pressure to ~3 bar
    for storage.  Max throughput is set by the available shaft power
    and the pressure ratio.  When O₂ production exceeds max_O2_flow_kg_hr,
    the excess is vented to lunar vacuum (physically reasonable —
    more O₂ than can be stored).
    """
    power_kW: float = 0.0              # Design-point shaft power
    O2_throughput_kg_hr: float = 0.0   # Design-point O₂ flow
    inlet_pressure_mbar: float = 1.0
    outlet_pressure_bar: float = 3.0
    isentropic_efficiency: float = 0.75

    # Capacity limits (set by size_turbine)
    max_O2_flow_kg_hr: float = 0.0     # Maximum O₂ the turbine can compress
    max_shaft_power_kW: float = 0.0    # Maximum shaft power available
    max_total_flow_kg_hr: float = 0.0  # Maximum total gas throughput


@dataclass
class VolatilesTrainSpec:
    """
    Volatiles condensation train sizing (Stage 0, active during C0).

    Separate from the metals train.  Handles CHNOPS, H₂O, S, CO₂,
    halides, perchlorates.  For volatile-heavy feedstocks (KREEP,
    highland), the C0 ramp must be throttled to stay within capacity.
    """
    max_throughput_kg_hr: float = 0.0  # Max offgas rate the train can handle
    design_point_kg_hr: float = 0.0    # Expected peak for mare basalt


@dataclass
class BufferTankSpec:
    """O₂ accumulator sizing."""
    volume_m3: float = 0.0
    pressure_bar: float = 3.0
    capacity_kg: float = 0.0


@dataclass
class PlantDesign:
    """Complete plant equipment specification."""
    batch_mass_kg: float = 0.0
    crucible: CrucibleSpec = field(default_factory=CrucibleSpec)
    concentrator: ConcentratorSpec = field(default_factory=ConcentratorSpec)
    pipe: PipeSpec = field(default_factory=PipeSpec)
    condensers: List[CondenserSpec] = field(default_factory=list)
    turbine: TurbineSpec = field(default_factory=TurbineSpec)
    volatiles_train: VolatilesTrainSpec = field(default_factory=VolatilesTrainSpec)
    buffer_tank: BufferTankSpec = field(default_factory=BufferTankSpec)


class EquipmentDesigner:
    """
    Auto-sizes the refinery equipment for a given batch.

    Given batch mass + feedstock + peak campaign temperature,
    calculates appropriate sizes for all major equipment.
    """

    # Melt physical properties
    MELT_DENSITY_KG_M3 = 2700.0     # Basaltic melt ~2500-2900
    MELT_CP_J_KG_K = 1200.0          # Heat capacity of silicate melt
    MELT_EMISSIVITY = 0.85            # Radiative emissivity

    def design_for_batch(self, mass_kg: float,
                          feedstock: dict,
                          peak_T_C: float = 1700.0) -> PlantDesign:
        """
        Generate a complete plant design for the given batch parameters.

        Args:
            mass_kg:    Batch mass (kg)
            feedstock:  Feedstock dict (from feedstocks.yaml)
            peak_T_C:   Maximum campaign temperature (°C)

        Returns:
            PlantDesign with all equipment specs
        """
        design = PlantDesign(batch_mass_kg=mass_kg)

        design.crucible = self.size_crucible(mass_kg)
        design.concentrator = self.size_solar_concentrator(
            mass_kg, peak_T_C)

        # Estimate peak evaporation rate from batch size
        # ~10 g/s per tonne at peak SiO window
        peak_evap_kg_s = mass_kg * 10e-3 / 1000.0
        design.pipe = self.size_collection_pipe(peak_evap_kg_s)

        design.turbine = self.size_turbine(mass_kg)
        design.volatiles_train = self.size_volatiles_train(mass_kg, feedstock)
        design.buffer_tank = self.size_buffer_tanks(mass_kg)

        return design

    def size_crucible(self, mass_kg: float) -> CrucibleSpec:
        """
        Size the crucible for the batch.

        Volume = mass / density                                [EQ-1]
        Geometry: height = 1.5 × diameter (cylindrical)
        Add 20% freeboard for bubbling/stirring
        """
        V_melt = mass_kg / self.MELT_DENSITY_KG_M3
        V_total = V_melt * 1.20  # 20% freeboard

        # Cylinder: V = π/4 × d² × h, with h = 1.5d
        # → V = π/4 × d² × 1.5d = 1.5π/4 × d³
        d = (V_total * 4.0 / (1.5 * math.pi)) ** (1.0/3.0)
        h = 1.5 * d

        return CrucibleSpec(
            volume_m3=V_total,
            diameter_m=d,
            height_m=h,
            melt_surface_area_m2=math.pi * (d/2)**2,
            freeboard_pct=20.0,
        )

    def size_solar_concentrator(self, mass_kg: float,
                                  peak_T_C: float) -> ConcentratorSpec:
        """
        Size the solar concentrator.

        Power needed:                                           [EQ-2]
            P = m × c_p × (dT/dt)_peak + σ × ε × A × T⁴
        where the first term is heating power and the second
        is radiative loss from the crucible surface.

        Lunar insolation: 1361 W/m²
        Concentrator efficiency: ~85%
        Reference: 100 m² → ~136 kW → ~1 tonne batch
        """
        T_K = peak_T_C + 273.15
        dT_dt = 50.0 / 3600.0  # 50 °C/hr → K/s

        # Heating power
        P_heat_W = mass_kg * self.MELT_CP_J_KG_K * dT_dt

        # Radiative loss from crucible surface
        # Estimate surface area from mass (rough cylinder)
        A_surface = 0.2 * (mass_kg / 1000.0) ** 0.67  # m²
        P_rad_W = (STEFAN_BOLTZMANN * self.MELT_EMISSIVITY
                    * A_surface * T_K**4)

        P_total_W = P_heat_W + P_rad_W
        P_total_kW = P_total_W / 1000.0

        # Concentrator aperture
        flux = 1.361  # kW/m² (lunar insolation)
        eff = 0.85
        aperture = P_total_kW / (flux * eff)

        return ConcentratorSpec(
            aperture_m2=aperture,
            thermal_power_kW=P_total_kW,
            peak_flux_kW_m2=flux,
            efficiency=eff,
        )

    def size_collection_pipe(self, peak_evap_rate_kg_s: float,
                              pressure_mbar: float = 10.0) -> PipeSpec:
        """
        Size the collection pipe.

        Poiseuille conductance:                                 [EQ-3]
            C = π × d⁴ × p̄ / (128 × η × L)

        At millibar pressures: viscous flow (Kn << 0.01).
        Require C ≥ peak_evap_rate / acceptable_pressure_drop.
        Reference: 12 cm pipe handles 7-16 g/s SiO at 10 mbar.
        """
        # Scale pipe diameter with batch size
        # Reference: 12 cm for 1 tonne
        scale = (peak_evap_rate_kg_s / 0.010) ** 0.25
        d = 0.12 * max(scale, 1.0)
        L = 1.0  # m (crucible to first condenser)

        # Calculate actual conductance
        p_Pa = pressure_mbar * 100.0
        T_K = 1773.15  # 1500°C pipe temperature
        eta = 1.8e-5 * (T_K / 300.0) ** 0.7

        C_vol = math.pi * d**4 * p_Pa / (128.0 * eta * L)
        M_avg = 0.040  # kg/mol
        rho = p_Pa * M_avg / (8.314 * T_K)
        C_mass = C_vol * rho

        return PipeSpec(
            diameter_m=d,
            length_m=L,
            conductance_kg_s=C_mass,
            max_transport_g_s=C_mass * 1000.0,
        )

    def size_turbine(self, mass_kg: float) -> TurbineSpec:
        """
        Size the turbine-compressor.

        Compression power:                                      [EQ-5]
            W = (γ/(γ-1)) × n × R × T × [(p₂/p₁)^((γ-1)/γ) - 1]

        Reference: 15-30 kWh per tonne O₂ for compression to ~3 bar.

        Max capacity is 1.5× the design-point O₂ rate.  Beyond this,
        the turbine shaft power is exceeded and excess O₂ must be vented.
        The max is set by the pressure differential and available power.
        """
        # Expected O₂ production rate (scale with batch)
        # ~400 kg O₂ per tonne regolith over ~100 hours
        O2_rate = mass_kg * 0.4 / 100.0  # kg/hr (design point)

        # Compression power (simplified)
        # From 1 mbar to 3 bar: large ratio, ~20 kWh/t O₂
        power_kW = O2_rate * 0.02  # 20 kWh/t = 0.02 kWh/kg

        # Max capacity = 1.5× design point
        # Beyond this, the turbine shaft power is exceeded
        max_O2 = O2_rate * 1.5
        max_shaft = max_O2 * 0.02  # kW at max throughput
        max_total = max_O2 / 0.3   # Total gas flow (O₂ is ~30%)

        return TurbineSpec(
            power_kW=power_kW,
            O2_throughput_kg_hr=O2_rate,
            max_O2_flow_kg_hr=max_O2,
            max_shaft_power_kW=max_shaft,
            max_total_flow_kg_hr=max_total,
        )

    def size_volatiles_train(self, mass_kg: float,
                              feedstock: dict) -> VolatilesTrainSpec:
        """
        Size the volatiles condensation train (Stage 0, C0 only).

        The volatiles train handles CHNOPS, H₂O, S, CO₂, halides.
        Capacity is set by the cold-trap surface area and gas throughput.

        For volatile-heavy feedstocks (KREEP, highland with high Na₂O + K₂O),
        the C0 ramp must be throttled to stay within this capacity.
        """
        # Estimate volatile content from feedstock composition
        comp = feedstock.get('composition_wt_pct', {})
        volatile_wt_pct = (
            comp.get('Na2O', 0.4) +
            comp.get('K2O', 0.1) +
            comp.get('H2O', 0.0) +
            comp.get('S', 0.0) +
            comp.get('Cl', 0.0)
        )
        volatile_mass_kg = mass_kg * volatile_wt_pct / 100.0

        # Design point: release all volatiles over ~10 hours of C0
        # (mare basalt: ~5 kg volatiles over 10 hr ≈ 0.5 kg/hr)
        design_rate = volatile_mass_kg / 10.0

        # Train capacity: 2× design point for mare (headroom)
        # This gives ~1.0 kg/hr for mare, ~5 kg/hr for KREEP
        max_rate = design_rate * 2.0

        return VolatilesTrainSpec(
            max_throughput_kg_hr=max(max_rate, 0.5),  # floor at 0.5 kg/hr
            design_point_kg_hr=design_rate,
        )

    def size_buffer_tanks(self, mass_kg: float) -> BufferTankSpec:
        """
        Size the O₂ accumulator.

        At ~3 bar and ~20°C, O₂ density ≈ 3.9 kg/m³.
        Size for peak 1-hour production × 5 safety margin.
        """
        O2_peak_kg_hr = mass_kg * 0.4 / 80.0  # optimistic rate
        capacity_kg = O2_peak_kg_hr * 5.0

        rho_O2 = 3.9  # kg/m³ at 3 bar, 20°C
        volume = capacity_kg / rho_O2

        return BufferTankSpec(
            volume_m3=volume,
            pressure_bar=3.0,
            capacity_kg=capacity_kg,
        )


# Import here to avoid circular dependency
STEFAN_BOLTZMANN = 5.670374e-8  # W/(m²·K⁴)
