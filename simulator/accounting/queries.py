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

OXYGEN_ACCOUNTING_TOLERANCE_KG = 1e-9
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
        reachable_surface_m2 = sum(
            max(0.0, float(segment.surface_area_m2))
            for segment in reachable_segments
        )
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
            {
                segment.name: segment.surface_area_m2
                for segment in reachable_segments
            },
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
            surface_area_m2=segment.surface_area_m2,
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
