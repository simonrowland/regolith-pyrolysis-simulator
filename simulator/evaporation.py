"""Evaporation and condensation-routing helpers for PyrolysisSimulator."""

from __future__ import annotations

import math

from simulator.state import (
    GAS_CONSTANT,
    MOLAR_MASS,
    STOICH_RATIOS,
    EvaporationFlux,
)


class EvaporationMixin:
    def _calculate_evaporation(self, equilibrium) -> EvaporationFlux:
        """
        Calculate evaporation flux using the Hertz-Knudsen-Langmuir equation.

        For each volatile species, the mass flux from the melt surface is:

            J_i = α_i × stir_factor × A_surface × (P_sat_i - P_ambient_i)
                  / √(2π × M_i × R × T)                            [HK-1]

        where:
            α_i         = evaporation coefficient (~0.1-1.0 for metals)
            stir_factor = 4-8× acceleration from induction stirring
            A_surface   = melt surface area (m²)
            P_sat_i     = saturation vapor pressure from equilibrium (Pa)
            P_ambient_i = partial pressure above the melt (Pa)
            M_i         = molar mass (kg/mol)
            R           = gas constant (J/mol·K)
            T           = temperature (K)

        The SiO suppression under pO₂ control is handled automatically:
        when pO₂ is elevated, the equilibrium vapor pressure of SiO
        drops by the factor √(pO₂), reducing the driving force.

        Returns:
            EvaporationFlux with species_kg_hr dict
        """
        T_K = self.melt.temperature_C + 273.15
        flux = EvaporationFlux()

        if T_K < 400:  # Below any significant evaporation
            return flux

        vapor_pressures = equilibrium.vapor_pressures_Pa
        alpha = 0.5  # Evaporation coefficient (conservative mean)

        metals_data = self.vapor_pressures.get('metals', {})
        oxide_vapors_data = self.vapor_pressures.get('oxide_vapors', {})

        for species, P_sat_Pa in vapor_pressures.items():
            if P_sat_Pa <= 0:
                continue

            # Get species data from metals or oxide_vapors section
            sp_data = metals_data.get(species, {})
            if not sp_data:
                sp_data = oxide_vapors_data.get(species, {})

            M_kg_mol = sp_data.get('molar_mass_g_mol',
                                    MOLAR_MASS.get(species, 50.0)) / 1000.0

            # Ambient partial pressure (Pa)                    [LOOP-1]
            # Uses the PREVIOUS hour's overhead partial pressures as
            # backpressure.  High evap → high overhead P → reduced driving
            # force → lower evap next hour (negative feedback, 1-hour lag).
            # Under hard vacuum (C0): overhead starts at ~0 but builds up
            # if evaporation outpaces transport.
            P_ambient_Pa = self.overhead.composition.get(species, 0.0) * 100.0  # mbar → Pa

            # For controlled-atmosphere species, also floor at the setpoint
            if species == 'O2' and self.melt.pO2_mbar > 0.001:
                P_ambient_Pa = max(P_ambient_Pa, self.melt.pO2_mbar * 100.0)

            # Hertz-Knudsen mass flux (kg/s per m²)        [HK-1]
            denominator = math.sqrt(2 * math.pi * M_kg_mol * GAS_CONSTANT * T_K)
            J_kg_s_m2 = alpha * (P_sat_Pa - P_ambient_Pa) / denominator

            if J_kg_s_m2 <= 0:
                continue

            # Total rate = flux × surface area × stirring × time
            rate_kg_hr = (J_kg_s_m2
                          * self.melt.melt_surface_area_m2
                          * self.melt.stir_factor
                          * 3600.0)  # s → hr

            # Don't evaporate more than is available in the melt
            parent_oxide = sp_data.get('parent_oxide', '')
            if parent_oxide:
                available_kg = self.melt.composition_kg.get(parent_oxide, 0.0)

                # For oxide vapors (e.g., SiO from SiO₂), use the
                # stoich_oxide_per_vapor ratio from the YAML
                oxide_per_vapor = sp_data.get('stoich_oxide_per_vapor')
                if oxide_per_vapor:
                    # kg_vapor_max = kg_oxide_available / oxide_per_vapor
                    max_vapor_kg = available_kg / oxide_per_vapor
                    rate_kg_hr = min(rate_kg_hr, max_vapor_kg)
                else:
                    # Metal species: use STOICH_RATIOS
                    stoich = STOICH_RATIOS.get(parent_oxide)
                    if stoich:
                        max_metal_kg = available_kg * stoich[0]
                        rate_kg_hr = min(rate_kg_hr, max_metal_kg)

            if rate_kg_hr > 1e-12:
                flux.species_kg_hr[species] = rate_kg_hr

        flux.update_totals()
        return flux

    def _route_to_condensation(self, evap_flux: EvaporationFlux):
        """
        Route evaporated species through the condensation train.

        Each species flows from Stage 0 (hot duct) downward through
        successive stages.  At each stage, a fraction condenses based
        on the condensation efficiency model:

            η = 1 - exp(-residence_time / τ_condensation)

        Species condense preferentially in stages where the stage
        temperature is well below the species' condensation temperature.

        The oxygen component of each evaporated metal oxide is
        released as O₂ and flows to the accumulator (Stage 6).
        """
        self.condensation_model.route(evap_flux, self.melt)

        # Track oxygen produced from evaporation
        # When a metal oxide evaporates and the metal condenses,
        # the oxygen is freed as O₂.
        metals_data = self.vapor_pressures.get('metals', {})
        oxide_vapors_data = self.vapor_pressures.get('oxide_vapors', {})

        for species, rate_kg_hr in evap_flux.species_kg_hr.items():
            sp_data = metals_data.get(species, {})
            if not sp_data:
                sp_data = oxide_vapors_data.get(species, {})

            parent_oxide = sp_data.get('parent_oxide', '')

            # For oxide vapors (e.g., SiO), use the YAML stoich
            O2_per_vapor = sp_data.get('stoich_O2_per_vapor')
            if O2_per_vapor:
                O2_kg = rate_kg_hr * O2_per_vapor
                self.oxygen_cumulative_kg += O2_kg
                stage6 = self.train.stages[6]
                stage6.collected_kg['O2'] = (
                    stage6.collected_kg.get('O2', 0.0) + O2_kg)
            elif parent_oxide:
                # Metal species: compute from STOICH_RATIOS
                stoich = STOICH_RATIOS.get(parent_oxide)
                if stoich:
                    kg_metal_per_kg_oxide, kg_O2_per_kg_oxide = stoich
                    if kg_metal_per_kg_oxide > 0:
                        O2_kg = rate_kg_hr * (kg_O2_per_kg_oxide
                                               / kg_metal_per_kg_oxide)
                        self.oxygen_cumulative_kg += O2_kg
                        stage6 = self.train.stages[6]
                        stage6.collected_kg['O2'] = (
                            stage6.collected_kg.get('O2', 0.0) + O2_kg)

    def _update_melt_composition(self, evap_flux: EvaporationFlux):
        """
        Subtract evaporated mass from the melt.

        When metal X evaporates from the melt, we remove the
        corresponding oxide (X₂O, XO, XO₂, etc.) from the
        melt composition.  The metal goes to the condensation
        train; the oxygen goes to the O₂ accumulator.

        For oxide vapors (e.g., SiO from SiO₂), the stoichiometry
        is different: kg_SiO₂_removed = kg_SiO × (M_SiO₂ / M_SiO).
        """
        metals_data = self.vapor_pressures.get('metals', {})
        oxide_vapors_data = self.vapor_pressures.get('oxide_vapors', {})

        for species, rate_kg_hr in evap_flux.species_kg_hr.items():
            sp_data = metals_data.get(species, {})
            if not sp_data:
                sp_data = oxide_vapors_data.get(species, {})

            parent_oxide = sp_data.get('parent_oxide', '')
            if not parent_oxide or parent_oxide not in self.melt.composition_kg:
                continue

            # For oxide vapors, use the YAML stoich ratio
            oxide_per_vapor = sp_data.get('stoich_oxide_per_vapor')
            if oxide_per_vapor:
                oxide_removed = rate_kg_hr * oxide_per_vapor
            else:
                # Metal species: use STOICH_RATIOS
                stoich = STOICH_RATIOS.get(parent_oxide)
                if stoich and stoich[0] > 0:
                    oxide_removed = rate_kg_hr / stoich[0]
                else:
                    continue

            current = self.melt.composition_kg[parent_oxide]
            self.melt.composition_kg[parent_oxide] = max(
                0.0, current - oxide_removed)

        self.melt.update_total_mass()
