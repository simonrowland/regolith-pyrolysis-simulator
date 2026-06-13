"""Read-only accounting query facade for scoring and trace consumers."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from simulator.account_ids import (
    CHROMIUM_CONDENSED_OXIDE_ACCOUNT,
    OXYGEN_MELT_OFFGAS_ACCOUNT,
    OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
    OXYGEN_MRE_ANODE_ACCOUNT,
    OXYGEN_SPECIES,
    OXYGEN_STAGE0_ACCOUNT,
    OXYGEN_STORED_ACCOUNTS,
    OXYGEN_VENTED_ACCOUNTS,
)
from simulator.accounting.exceptions import AccountingError
from simulator.accounting.formulas import resolve_species_formula

OXYGEN_ACCOUNTING_TOLERANCE_KG = 1e-9
FREE_ANALYZER_OXYGEN_SPECIES = frozenset({OXYGEN_SPECIES, "O"})
OVERHEAD_VAPOR_ACCOUNTS = (
    "process.overhead_gas",
    "terminal.offgas",
)
CONDENSATION_TRAIN_ACCOUNT = "process.condensation_train"
WALL_DEPOSIT_SEGMENT_ACCOUNT_PREFIX = "process.wall_deposit_segment_"
FREE_ANALYZER_OXYGEN_ACCOUNTS = (
    *OVERHEAD_VAPOR_ACCOUNTS,
    *OXYGEN_STORED_ACCOUNTS,
    *OXYGEN_VENTED_ACCOUNTS,
)
TERMINAL_RUMP_ACCOUNTS = (
    "process.cleaned_melt",
    "terminal.slag",
)
TERMINAL_RUMP_REFRACTORY_OXIDES = frozenset({
    "CaO",
    "MgO",
    "Al2O3",
    "TiO2",
    "Cr2O3",
    "REE_oxides",
})
TERMINAL_RUMP_SILICATE_RESIDUAL = frozenset({"SiO2"})
TERMINAL_RUMP_UNEXTRACTED_METALS = frozenset({"Fe", "Ni", "Co", "Mn"})
TERMINAL_RUMP_CLASS_TOLERANCE_PCT = 5e-12


def _merge_masses(target: dict[str, float], values: Mapping[str, float]) -> None:
    for species, kg in values.items():
        amount = float(kg)
        if amount:
            target[species] = target.get(species, 0.0) + amount


class AccountingQueries:
    """Single read-side facade for simulator accounting/scoring queries."""

    def __init__(self, sim: Any):
        self.sim = sim
        self.ledger = sim.atom_ledger

    def product_ledger(self) -> dict[str, float]:
        products: dict[str, float] = {}
        for account in (
            "terminal.offgas",
            "terminal.stage0_salt_phase",
            "terminal.stage0_sulfide_matte",
            "terminal.drain_tap_material",
            CHROMIUM_CONDENSED_OXIDE_ACCOUNT,
            "process.metal_phase",
            "process.condensation_train",
            "process.overhead_gas",
        ):
            _merge_masses(
                products,
                {
                    species: kg
                    for species, kg in self.ledger.kg_by_account(account).items()
                    if species != OXYGEN_SPECIES
                },
            )
        _merge_masses(products, self.sim._unspent_additive_reagents_kg())
        return products

    def species_kg_by_accounts(
        self,
        accounts: tuple[str, ...] | list[str],
    ) -> dict[str, float]:
        species_kg: dict[str, float] = {}
        for account in accounts:
            _merge_masses(species_kg, self.ledger.kg_by_account(str(account)))
        return {
            species: kg
            for species, kg in sorted(species_kg.items())
            if kg > 0.0
        }

    def species_kg_by_account_pattern(self, account_pattern: str) -> dict[str, float]:
        pattern = str(account_pattern)
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            all_accounts = self.ledger.kg_by_account()
            species_kg: dict[str, float] = {}
            for account, values in all_accounts.items():
                if str(account).startswith(prefix):
                    _merge_masses(species_kg, values)
            return {
                species: kg
                for species, kg in sorted(species_kg.items())
                if kg > 0.0
            }
        return self.species_kg_by_accounts((pattern,))

    def unspent_additive_reagents_kg(self) -> dict[str, float]:
        getter = getattr(self.sim, "_unspent_additive_reagents_kg", None)
        if not callable(getter):
            raise AccountingError(
                "unspent additive reagent surface is unavailable"
            )
        values = getter()
        if not isinstance(values, Mapping):
            raise AccountingError(
                "unspent additive reagent surface is not a mapping"
            )
        return {
            str(species): float(kg)
            for species, kg in values.items()
            if float(kg) > 0.0
        }

    def terminal_rump_by_species(self) -> dict[str, float]:
        species_kg: dict[str, float] = {}
        for account in TERMINAL_RUMP_ACCOUNTS:
            _merge_masses(species_kg, self.ledger.kg_by_account(account))
        return {
            species: kg
            for species, kg in sorted(species_kg.items())
            if kg > 0.0
        }

    def terminal_rump_by_class(self) -> dict[str, float]:
        by_species = self.terminal_rump_by_species()
        by_class = {
            "refractory_oxides": 0.0,
            "silicate_residual": 0.0,
            "unextracted_metals": 0.0,
            "other": 0.0,
        }
        for species, kg in by_species.items():
            if species in TERMINAL_RUMP_REFRACTORY_OXIDES:
                category = "refractory_oxides"
            elif species in TERMINAL_RUMP_SILICATE_RESIDUAL:
                category = "silicate_residual"
            elif species in TERMINAL_RUMP_UNEXTRACTED_METALS:
                category = "unextracted_metals"
            else:
                category = "other"
            by_class[category] += kg

        total_kg = (
            self.ledger.total_kg_by_account("process.cleaned_melt")
            + self.ledger.total_kg_by_account("terminal.slag")
        )
        class_total_kg = sum(by_class.values())
        if total_kg > 0.0:
            error_pct = abs(class_total_kg - total_kg) / total_kg * 100.0
        else:
            error_pct = 0.0 if class_total_kg == 0.0 else math.inf
        if error_pct > TERMINAL_RUMP_CLASS_TOLERANCE_PCT:
            raise AccountingError(
                "terminal rump class mass does not match terminal rump total: "
                f"{class_total_kg:.15g} kg vs {total_kg:.15g} kg "
                f"({error_pct:.15g} pct)"
            )
        return by_class

    def oxygen_terminal_partition_kg(self) -> dict[str, float]:
        stored_by_source = {
            account: _ledger_o2_kg(self.ledger, account)
            for account in OXYGEN_STORED_ACCOUNTS
        }
        vented_by_source = {
            account: _ledger_o2_kg(self.ledger, account)
            for account in OXYGEN_VENTED_ACCOUNTS
        }
        stored_kg = sum(stored_by_source.values())
        vented_kg = sum(vented_by_source.values())
        return {
            "stored": stored_kg,
            "vented": vented_kg,
            "total": stored_kg + vented_kg,
            "stage0_stored": stored_by_source.get(OXYGEN_STAGE0_ACCOUNT, 0.0),
            "melt_offgas_stored": stored_by_source.get(
                OXYGEN_MELT_OFFGAS_ACCOUNT, 0.0),
            "mre_anode_stored": stored_by_source.get(
                OXYGEN_MRE_ANODE_ACCOUNT, 0.0),
            "melt_offgas_vented": vented_by_source.get(
                OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT, 0.0),
        }

    def condensation_totals_with_terminal_oxygen(self) -> dict[str, float]:
        totals = {
            species: float(kg)
            for species, kg in self.ledger.kg_by_account(
                "process.condensation_train").items()
            if kg > 1e-12
        }
        oxygen_partition = self.oxygen_terminal_partition_kg()
        melt_offgas_stored = oxygen_partition["melt_offgas_stored"]
        if melt_offgas_stored > OXYGEN_ACCOUNTING_TOLERANCE_KG:
            totals[OXYGEN_SPECIES] = melt_offgas_stored
        else:
            totals.pop(OXYGEN_SPECIES, None)
        return totals

    def lab_oxygen_atom_partition(self) -> dict[str, Any]:
        balances = self.ledger.mol_by_account()
        registry = self.ledger.registry
        total_oxygen_atom_mol = _oxygen_atom_mol_for_balances(
            balances,
            registry,
        )
        free_species_mol: dict[str, float] = {}
        free_species_oxygen_atom_mol: dict[str, float] = {}
        overhead_bound_species_oxygen_atom_mol: dict[str, float] = {}
        condensation_species_oxygen_atom_mol: dict[str, float] = {}
        wall_by_surface: dict[str, dict[str, Any]] = {}

        for account, species_mol in sorted(balances.items()):
            account_name = str(account)
            species_oxygen = _oxygen_atom_mol_by_species(
                species_mol,
                registry,
            )
            if not species_oxygen:
                continue

            if account_name.startswith(WALL_DEPOSIT_SEGMENT_ACCOUNT_PREFIX):
                surface = account_name.removeprefix(
                    WALL_DEPOSIT_SEGMENT_ACCOUNT_PREFIX
                )
                wall_by_surface[surface] = {
                    "account": account_name,
                    "oxygen_atom_mol": sum(species_oxygen.values()),
                    "species_oxygen_atom_mol": dict(sorted(
                        species_oxygen.items()
                    )),
                }
                continue

            if account_name == CONDENSATION_TRAIN_ACCOUNT:
                _merge_masses(
                    condensation_species_oxygen_atom_mol,
                    species_oxygen,
                )
                continue

            if account_name in OVERHEAD_VAPOR_ACCOUNTS:
                for species, oxygen_atom_mol in species_oxygen.items():
                    if species in FREE_ANALYZER_OXYGEN_SPECIES:
                        free_species_oxygen_atom_mol[species] = (
                            free_species_oxygen_atom_mol.get(species, 0.0)
                            + oxygen_atom_mol
                        )
                        free_species_mol[species] = (
                            free_species_mol.get(species, 0.0)
                            + float(species_mol.get(species, 0.0))
                        )
                    else:
                        overhead_bound_species_oxygen_atom_mol[species] = (
                            overhead_bound_species_oxygen_atom_mol.get(
                                species, 0.0
                            )
                            + oxygen_atom_mol
                        )
                continue

            if account_name in FREE_ANALYZER_OXYGEN_ACCOUNTS:
                for species, oxygen_atom_mol in species_oxygen.items():
                    if species not in FREE_ANALYZER_OXYGEN_SPECIES:
                        continue
                    free_species_oxygen_atom_mol[species] = (
                        free_species_oxygen_atom_mol.get(species, 0.0)
                        + oxygen_atom_mol
                    )
                    free_species_mol[species] = (
                        free_species_mol.get(species, 0.0)
                        + float(species_mol.get(species, 0.0))
                    )

        free_oxygen_atom_mol = sum(free_species_oxygen_atom_mol.values())
        overhead_bound_oxygen_atom_mol = sum(
            overhead_bound_species_oxygen_atom_mol.values()
        )
        condensation_oxygen_atom_mol = sum(
            condensation_species_oxygen_atom_mol.values()
        )
        wall_segment_oxygen_atom_mol = sum(
            surface["oxygen_atom_mol"]
            for surface in wall_by_surface.values()
        )
        allocated_oxygen_atom_mol = (
            free_oxygen_atom_mol
            + overhead_bound_oxygen_atom_mol
            + condensation_oxygen_atom_mol
            + wall_segment_oxygen_atom_mol
        )
        residual_unallocated_oxygen_atom_mol = (
            total_oxygen_atom_mol - allocated_oxygen_atom_mol
        )
        reported_total_oxygen_atom_mol = (
            allocated_oxygen_atom_mol
            + residual_unallocated_oxygen_atom_mol
        )
        if total_oxygen_atom_mol > 0.0:
            closure_error_pct = (
                abs(total_oxygen_atom_mol - reported_total_oxygen_atom_mol)
                / total_oxygen_atom_mol
                * 100.0
            )
        else:
            closure_error_pct = 0.0

        return {
            "total_oxygen_atom_mol": total_oxygen_atom_mol,
            "free_analyzer_visible": {
                "oxygen_atom_mol": free_oxygen_atom_mol,
                "species_mol": dict(sorted(
                    (species, amount)
                    for species, amount in free_species_mol.items()
                    if amount > 0.0
                )),
                "species_oxygen_atom_mol": dict(sorted(
                    free_species_oxygen_atom_mol.items()
                )),
            },
            "overhead_vapor_bound": {
                "oxygen_atom_mol": overhead_bound_oxygen_atom_mol,
                "species_oxygen_atom_mol": dict(sorted(
                    overhead_bound_species_oxygen_atom_mol.items()
                )),
            },
            "condensation_train": {
                "oxygen_atom_mol": condensation_oxygen_atom_mol,
                "species_oxygen_atom_mol": dict(sorted(
                    condensation_species_oxygen_atom_mol.items()
                )),
            },
            "wall_deposit_segment_by_surface": {
                "total_oxygen_atom_mol": wall_segment_oxygen_atom_mol,
                "surfaces": dict(sorted(wall_by_surface.items())),
            },
            "residual_unallocated_oxygen_atom_mol": (
                residual_unallocated_oxygen_atom_mol
            ),
            "closure": {
                "allocated_oxygen_atom_mol": allocated_oxygen_atom_mol,
                "reported_total_oxygen_atom_mol": (
                    reported_total_oxygen_atom_mol
                ),
                "error_pct": closure_error_pct,
            },
        }

    def rump_element_kg(self, element: str) -> float:
        species_names = self.sim._RUMP_ELEMENT_SPECIES.get(element, ())
        total = 0.0
        for account in ("process.cleaned_melt", "terminal.slag"):
            species_kg = self.ledger.kg_by_account(account)
            for species in species_names:
                total += max(0.0, float(species_kg.get(species, 0.0)))
        return total

    def actual_rump_elements_kg(self) -> dict[str, float]:
        return {
            element: kg
            for element in sorted(self.sim._RUMP_ELEMENT_SPECIES)
            if (
                (kg := self.rump_element_kg(element))
                > self.sim._RUMP_EXPECTATION_TOL_KG
            )
        }


def _ledger_o2_kg(ledger: Any, account: str) -> float:
    species_kg = ledger.kg_by_account(account)
    return max(0.0, float(species_kg.get(OXYGEN_SPECIES, 0.0)))


def _oxygen_atom_mol_for_balances(
    balances: Mapping[str, Mapping[str, float]],
    registry: Mapping[str, Any],
) -> float:
    return sum(
        sum(_oxygen_atom_mol_by_species(species_mol, registry).values())
        for species_mol in balances.values()
    )


def _oxygen_atom_mol_by_species(
    species_mol: Mapping[str, float],
    registry: Mapping[str, Any],
) -> dict[str, float]:
    result: dict[str, float] = {}
    for species, mol in species_mol.items():
        formula = resolve_species_formula(str(species), registry)
        oxygen_atoms = float(formula.atoms.get("O", 0.0))
        if oxygen_atoms <= 0.0:
            continue
        oxygen_atom_mol = float(mol) * oxygen_atoms
        if oxygen_atom_mol > 0.0:
            result[str(species)] = (
                result.get(str(species), 0.0) + oxygen_atom_mol
            )
    return dict(sorted(result.items()))


def stage_purity(train: Any) -> dict[int, dict[str, float]]:
    result: dict[int, dict[str, float]] = {}
    for stage in train.stages:
        total = stage.total_collected_kg()
        if total <= 0:
            continue
        purities = {}
        for species, kg in stage.collected_kg.items():
            purities[species] = (kg / total) * 100.0
        result[stage.stage_number] = purities
    return result


def condensation_stage_purity_pct(stage: Any, species: str) -> float:
    total = stage.total_collected_kg()
    if total <= 0:
        return 0.0
    return (stage.collected_kg.get(species, 0.0) / total) * 100.0


def wall_deposit_candidate_kg(
    model: Any,
    *,
    species: str,
    rate_kg_hr: float,
    T_cond_C: float,
    melt_temperature_C: float,
) -> float:
    return wall_deposit_candidate_for_surface_kg(
        model,
        species=species,
        rate_kg_hr=rate_kg_hr,
        T_cond_C=T_cond_C,
        melt_temperature_C=melt_temperature_C,
        wall_temperature_C=model.wall_temperature_C,
        surface_area_m2=model.wall_surface_area_m2,
    )


def wall_deposit_candidates_by_segment_kg(
    model: Any,
    *,
    species: str,
    rate_kg_hr: float,
    T_cond_C: float,
    melt_temperature_C: float,
    supply_by_segment_kg: Mapping[str, float],
) -> dict[str, float]:
    if rate_kg_hr <= 0.0 or not model.pipe_segments:
        return {}
    reachable_segments = model._mixed_temperature_wall_candidate_segments(species)
    if not reachable_segments:
        return {}
    temperatures = {
        float(segment.wall_temperature_C)
        for segment in reachable_segments
    }
    if len(temperatures) == 1:
        wall_temperature_C = next(iter(temperatures))
        conductance_weights = {
            segment.name: _wall_geometry_conductance_weight(segment)
            for segment in reachable_segments
        }
        reachable_surface_m2 = sum(conductance_weights.values())
        if reachable_surface_m2 <= 0.0:
            return {}
        total_candidate = wall_deposit_candidate_for_surface_kg(
            model,
            species=species,
            rate_kg_hr=rate_kg_hr,
            T_cond_C=T_cond_C,
            melt_temperature_C=melt_temperature_C,
            wall_temperature_C=wall_temperature_C,
            surface_area_m2=reachable_surface_m2,
        )
        from simulator.condensation import _allocate_total_by_weights

        candidates = _allocate_total_by_weights(
            total_candidate,
            conductance_weights,
        )
        for segment in reachable_segments:
            supply_kg = min(
                max(0.0, float(supply_by_segment_kg.get(
                    segment.name, rate_kg_hr))),
                rate_kg_hr,
            )
            if segment.name in candidates:
                candidates[segment.name] = min(
                    candidates[segment.name], supply_kg)
        return candidates

    candidates: dict[str, float] = {}
    for segment in reachable_segments:
        supply_kg = min(
            max(0.0, float(supply_by_segment_kg.get(
                segment.name, rate_kg_hr))),
            rate_kg_hr,
        )
        candidate = wall_deposit_candidate_for_surface_kg(
            model,
            species=species,
            rate_kg_hr=supply_kg,
            T_cond_C=T_cond_C,
            melt_temperature_C=melt_temperature_C,
            wall_temperature_C=segment.wall_temperature_C,
            surface_area_m2=_wall_geometry_conductance_weight(segment),
        )
        if candidate > 0.0:
            candidates[segment.name] = min(candidate, supply_kg)
    total = sum(candidates.values())
    if total > rate_kg_hr > 0.0:
        scale = rate_kg_hr / total
        candidates = {
            name: value * scale
            for name, value in candidates.items()
        }
    return candidates


def wall_deposit_candidate_for_surface_kg(
    model: Any,
    *,
    species: str,
    rate_kg_hr: float,
    T_cond_C: float,
    melt_temperature_C: float,
    wall_temperature_C: float,
    surface_area_m2: float,
) -> float:
    if rate_kg_hr <= 0.0 or surface_area_m2 <= 0.0:
        return 0.0

    from simulator.condensation import (
        _hkl_impingement_flux_mol_m2_s,
        _local_wall_species_pressure_pa,
        _series_resistance_deposition_flux_mol_m2_s,
        _wall_alpha_s,
    )

    alpha_s = _wall_alpha_s(species)
    if alpha_s <= 0.0:
        return 0.0

    P_local_pa = _local_wall_species_pressure_pa(
        species, melt_temperature_C, T_cond_C,
    )
    if P_local_pa <= 0.0:
        return 0.0

    T_ref_K = max(T_cond_C + 273.15, 1.0)
    reference_flux = _hkl_impingement_flux_mol_m2_s(
        species, P_local_pa, T_ref_K,
    )
    if reference_flux <= 0.0:
        return 0.0

    T_wall_K = max(float(wall_temperature_C) + 273.15, 1.0)
    T_gas_K = max(float(model.gas_temperature_C) + 273.15, 1.0)
    overhead_pressure_pa = float(model.overhead_pressure_mbar) * 100.0
    flux = _series_resistance_deposition_flux_mol_m2_s(
        species, P_local_pa, T_wall_K, alpha_s,
        pipe_diameter_m=model.pipe_diameter_m,
        stir_factor=model.stir_factor,
        radial_stir_factor=model.radial_stir_factor,
        regime_factor=model.regime_factor,
        T_gas_K=T_gas_K,
        overhead_pressure_pa=overhead_pressure_pa,
    )
    if flux <= 0.0:
        return 0.0

    residence_s = float(model.residence_time_s.get(0, 0.5))
    rate_s_inv = (
        flux / reference_flux
    ) * max(0.0, float(surface_area_m2))
    eta = 1.0 - math.exp(-max(0.0, residence_s * rate_s_inv))
    return max(0.0, min(rate_kg_hr, rate_kg_hr * eta))


def _wall_geometry_conductance_weight(segment: Any) -> float:
    """Named assumption: free-molecular view-factor/LOS area proxy.

    TODO(P0a): replace this proxy with the pinned aperture/tube conductance
    ladder once carrier/regime plumbing is available for lab surfaces.
    """

    area_m2 = max(0.0, float(getattr(segment, "surface_area_m2", 0.0)))
    view_factor = getattr(segment, "view_factor_from_melt", None)
    line_of_sight = getattr(segment, "line_of_sight_to_melt", None)
    if view_factor is None and line_of_sight is None:
        return area_m2
    if line_of_sight is False:
        return 0.0
    if view_factor is None:
        view_factor_value = 1.0
    else:
        try:
            view_factor_value = float(view_factor)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(view_factor_value):
            return 0.0
    return area_m2 * max(0.0, min(1.0, view_factor_value))
