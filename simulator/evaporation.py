"""Evaporation and condensation-routing helpers for PyrolysisSimulator."""

from __future__ import annotations

import math
from collections import defaultdict

from simulator.accounting import AccountingError, resolve_species_formula
from simulator.state import (
    GAS_CONSTANT,
    MOLAR_MASS,
    OXIDE_TO_METAL,
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
            if not parent_oxide:
                raise AccountingError(
                    f"vapor species {species!r} requires parent_oxide "
                    "metadata before evaporation flux can be emitted"
                )
            available_kg = self.melt.composition_kg.get(parent_oxide, 0.0)
            stoich = self._evaporation_stoich(species, sp_data)
            max_product_kg = available_kg / stoich['oxide_per_product_kg']
            rate_kg_hr = min(rate_kg_hr, max_product_kg)

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
        released as O2 and credited to terminal oxygen storage.
        """
        route_result = self.condensation_model.route(
            evap_flux, self.melt)

        metals_data = self.vapor_pressures.get('metals', {})
        oxide_vapors_data = self.vapor_pressures.get('oxide_vapors', {})

        for species, rate_kg_hr in evap_flux.species_kg_hr.items():
            sp_data = metals_data.get(species, {})
            if not sp_data:
                sp_data = oxide_vapors_data.get(species, {})

            credited_condensed_kg = self._credit_evaporation_transition(
                species,
                rate_kg_hr,
                route_result.remaining_by_species.get(species, 0.0),
                sp_data,
            )
            product_projection = self._condensed_products_kg(
                species, credited_condensed_kg, sp_data)
            self._project_condensed_stage_collection(
                route_result, species, credited_condensed_kg,
                product_projection)

        self._sync_oxygen_kg_counters()

    def _credit_evaporation_transition(
        self,
        species: str,
        rate_kg_hr: float,
        remaining_kg_hr: float,
        sp_data: dict,
    ) -> float:
        stoich = self._evaporation_stoich(species, sp_data)
        if stoich is None:
            return 0.0

        parent_oxide = stoich['parent_oxide']
        oxide_removed = rate_kg_hr * stoich['oxide_per_product_kg']
        product_kg = rate_kg_hr
        O2_kg = rate_kg_hr * stoich['O2_per_product_kg']
        product_species = species

        if oxide_removed <= 1e-12:
            return 0.0

        available_kg = self.atom_ledger.kg_by_account(
            'process.cleaned_melt').get(parent_oxide, 0.0)
        if available_kg <= 1e-12:
            return 0.0

        scale = min(1.0, available_kg / oxide_removed)
        oxide_removed *= scale
        product_kg *= scale
        O2_kg *= scale
        if remaining_kg_hr < -1e-12 or remaining_kg_hr > rate_kg_hr + 1e-12:
            raise AccountingError(
                f"condensation route for {species!r} returned "
                "unphysical remaining vapor mass"
            )
        remaining_kg = max(0.0, remaining_kg_hr) * scale
        if remaining_kg > product_kg + 1e-12:
            raise AccountingError(
                f"condensation route for {species!r} exceeds credited vapor"
            )
        condensed_kg = max(0.0, product_kg - remaining_kg)
        condensed_product_mol, condensed_products_kg = (
            self._condensed_products_for_vapor(
                product_species, condensed_kg, sp_data)
        )

        credits = []
        if condensed_kg > 1e-12:
            if condensed_product_mol:
                credits.append(self.atom_ledger.credit_mol(
                    'process.condensation_train',
                    condensed_product_mol,
                    source=f'{species} condensation',
                ))
            else:
                credits.append(self.atom_ledger.credit(
                    'process.condensation_train',
                    condensed_products_kg,
                    source=f'{species} condensation',
                ))
        if remaining_kg > 1e-12:
            credits.append(self.atom_ledger.credit(
                'process.overhead_gas',
                {product_species: remaining_kg},
                source=f'{species} uncondensed vapor',
            ))
        if O2_kg > 1e-12:
            credits.append(self.atom_ledger.credit(
                'terminal.oxygen_melt_offgas_stored',
                {'O2': O2_kg},
                source=f'{species} oxygen coproduct',
            ))
        if not credits:
            return 0.0

        self.atom_ledger.transfer(
            f'evaporate_{species}_{self.melt.hour}',
            debits=(self.atom_ledger.debit(
                'process.cleaned_melt',
                {parent_oxide: oxide_removed},
                source=f'{species} evaporation',
            ),),
            credits=tuple(credits),
            reason='evaporation and gas-train routing',
        )
        if O2_kg > 1e-12:
            self._melt_offgas_O2_kg_this_hr += O2_kg
        return condensed_kg

    def _condensed_products_for_vapor(
        self, species: str, condensed_kg: float, sp_data: dict
    ):
        products_mol_per_mol = self._condensation_product_mol_ratios(
            species, sp_data)
        if products_mol_per_mol is None:
            return None, {species: condensed_kg} if condensed_kg > 0.0 else {}

        vapor_formula = resolve_species_formula(
            species, self.species_formula_registry)
        vapor_mol = condensed_kg / vapor_formula.molar_mass_kg_per_mol()
        product_mol = {
            product: ratio * vapor_mol
            for product, ratio in products_mol_per_mol.items()
            if ratio * vapor_mol > 0.0
        }
        return product_mol, self._species_mol_to_kg(product_mol)

    def _condensed_products_kg(
        self, species: str, condensed_kg: float, sp_data: dict
    ) -> dict:
        _product_mol, product_kg = self._condensed_products_for_vapor(
            species, condensed_kg, sp_data)
        return product_kg

    def _condensation_product_mol_ratios(
        self, species: str, sp_data: dict
    ):
        ratios = sp_data.get('condensation_products_mol_per_mol_vapor')
        if ratios is None:
            declared = str(sp_data.get('condensation_product', '')).lower()
            if 'disproportion' in declared:
                raise AccountingError(
                    f"vapor species {species!r} declares condensation "
                    "disproportionation but lacks "
                    "condensation_products_mol_per_mol_vapor metadata"
                )
            return None
        if not isinstance(ratios, dict) or not ratios:
            raise AccountingError(
                f"vapor species {species!r} condensation products must be "
                "a non-empty mapping"
            )

        clean = {}
        for product, raw_ratio in ratios.items():
            ratio = float(raw_ratio)
            if ratio <= 0.0 or not math.isfinite(ratio):
                raise AccountingError(
                    f"vapor species {species!r} condensation product "
                    f"{product!r} requires a positive mol ratio"
                )
            clean[str(product)] = ratio
        self._validate_condensation_products_atoms(species, clean)
        return clean

    def _validate_condensation_products_atoms(
        self, vapor_species: str, products_mol_per_mol: dict
    ) -> None:
        debit_atoms = resolve_species_formula(
            vapor_species, self.species_formula_registry).atom_moles(1.0)
        credit_atoms = defaultdict(float)
        for product, mol in products_mol_per_mol.items():
            formula = resolve_species_formula(
                product, self.species_formula_registry)
            for element, moles in formula.atom_moles(mol).items():
                credit_atoms[element] += moles

        for element in set(debit_atoms) | set(credit_atoms):
            debit = debit_atoms.get(element, 0.0)
            credit = credit_atoms.get(element, 0.0)
            if not math.isclose(debit, credit, rel_tol=1e-9, abs_tol=1e-12):
                raise AccountingError(
                    f"vapor species {vapor_species!r} condensation products "
                    f"do not conserve {element} atoms"
                )

    def _species_mol_to_kg(self, species_mol: dict) -> dict:
        converted = {}
        for species, mol in species_mol.items():
            formula = resolve_species_formula(
                species, self.species_formula_registry)
            kg = float(mol) * formula.molar_mass_kg_per_mol()
            if kg > 0.0:
                converted[species] = kg
        return converted

    def _evaporation_stoich(self, species: str, sp_data: dict):
        parent_oxide = sp_data.get('parent_oxide', '')
        if not parent_oxide:
            raise AccountingError(
                f"vapor species {species!r} requires parent_oxide "
                "metadata before ledger routing"
            )

        has_oxide = sp_data.get('stoich_oxide_per_vapor') is not None
        has_o2 = sp_data.get('stoich_O2_per_vapor') is not None
        if has_oxide or has_o2:
            missing = []
            if not has_oxide:
                missing.append('stoich_oxide_per_vapor')
            if not has_o2:
                missing.append('stoich_O2_per_vapor')
            if missing:
                raise AccountingError(
                    f"vapor species {species!r} from {parent_oxide!r} "
                    f"missing explicit stoich metadata: {', '.join(missing)}"
                )
            oxide_per_product = float(sp_data['stoich_oxide_per_vapor'])
            O2_per_product = float(sp_data['stoich_O2_per_vapor'])
            if oxide_per_product <= 0.0 or O2_per_product < 0.0:
                raise AccountingError(
                    f"vapor species {species!r} from {parent_oxide!r} "
                    "requires positive stoich_oxide_per_vapor and "
                    "non-negative stoich_O2_per_vapor"
                )
            if not math.isclose(
                oxide_per_product,
                1.0 + O2_per_product,
                rel_tol=1e-6,
                abs_tol=1e-9,
            ):
                raise AccountingError(
                    f"vapor species {species!r} from {parent_oxide!r} "
                    "stoich metadata must conserve mass: "
                    "stoich_oxide_per_vapor must equal "
                    "1 + stoich_O2_per_vapor"
                )
            self._validate_evaporation_stoich_atoms(
                parent_oxide,
                species,
                oxide_per_product,
                O2_per_product,
            )
            return {
                'parent_oxide': parent_oxide,
                'oxide_per_product_kg': oxide_per_product,
                'O2_per_product_kg': O2_per_product,
            }

        implied = OXIDE_TO_METAL.get(parent_oxide, ('', 0, 0))[0]
        if species != implied:
            raise AccountingError(
                f"vapor species {species!r} from {parent_oxide!r} requires "
                "explicit stoich_oxide_per_vapor and stoich_O2_per_vapor; "
                f"STOICH_RATIOS fallback only applies to elemental "
                f"{implied!r}"
            )
        fallback = STOICH_RATIOS.get(parent_oxide)
        if not fallback or fallback[0] <= 0:
            raise AccountingError(
                f"vapor species {species!r} from {parent_oxide!r} has no "
                "valid elemental stoich fallback"
            )
        kg_product_per_kg_oxide, kg_O2_per_kg_oxide = fallback
        return {
            'parent_oxide': parent_oxide,
            'oxide_per_product_kg': 1.0 / kg_product_per_kg_oxide,
            'O2_per_product_kg': (
                kg_O2_per_kg_oxide / kg_product_per_kg_oxide),
        }

    def _validate_evaporation_stoich_atoms(
        self,
        parent_oxide: str,
        product_species: str,
        oxide_per_product_kg: float,
        O2_per_product_kg: float,
    ) -> None:
        debit_atoms = self._atom_moles_for_kg(
            parent_oxide, oxide_per_product_kg)
        credit_atoms = defaultdict(float)
        product_atoms = self._atom_moles_for_kg(product_species, 1.0)
        for element, moles in product_atoms.items():
            credit_atoms[element] += moles
        oxygen_atoms = self._atom_moles_for_kg('O2', O2_per_product_kg)
        for element, moles in oxygen_atoms.items():
            credit_atoms[element] += moles

        for element in set(debit_atoms) | set(credit_atoms):
            debit = debit_atoms.get(element, 0.0)
            credit = credit_atoms.get(element, 0.0)
            if not math.isclose(debit, credit, rel_tol=1e-6, abs_tol=1e-9):
                raise AccountingError(
                    f"vapor species {product_species!r} from "
                    f"{parent_oxide!r} stoich metadata does not conserve "
                    f"{element} atoms"
                )

    def _atom_moles_for_kg(self, species: str, kg: float) -> dict:
        if kg <= 0.0:
            return {}
        formula = resolve_species_formula(
            species, self.species_formula_registry)
        species_moles = float(kg) / formula.molar_mass_kg_per_mol()
        return formula.atom_moles(species_moles)

    def _project_condensed_stage_collection(
        self, route_result, species: str, credited_condensed_kg: float,
        product_kg_by_species: dict | None = None,
    ) -> None:
        if credited_condensed_kg <= 1e-12:
            return
        product_kg_by_species = product_kg_by_species or {
            species: credited_condensed_kg}
        intended_condensed_kg = route_result.condensed_for_species(species)
        if intended_condensed_kg <= 1e-12:
            return
        scale = credited_condensed_kg / intended_condensed_kg
        product_scale = {
            product: kg / credited_condensed_kg
            for product, kg in product_kg_by_species.items()
            if kg > 1e-12
        }
        stages_by_number = {
            stage.stage_number: stage for stage in self.train.stages
        }
        for stage_number, stage_species in (
            route_result.condensed_by_stage_species.items()
        ):
            projected_kg = stage_species.get(species, 0.0) * scale
            if projected_kg <= 1e-12:
                continue
            stage = stages_by_number.get(stage_number)
            if stage is None:
                continue
            for product, product_fraction in product_scale.items():
                stage_product_kg = projected_kg * product_fraction
                if stage_product_kg <= 1e-12:
                    continue
                stage.collected_kg.update({
                    product: (
                        stage.collected_kg.get(product, 0.0)
                        + stage_product_kg)
                })

    def _update_melt_composition(self, evap_flux: EvaporationFlux):
        """Project the cleaned-melt account onto MeltState kg fields."""
        self._project_cleaned_melt_from_atom_ledger()
