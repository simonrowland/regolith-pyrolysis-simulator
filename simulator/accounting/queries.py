"""Read-only accounting query facade for scoring and trace consumers."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from simulator.account_ids import (
    CHROMIUM_CONDENSED_OXIDE_ACCOUNT,
    OXYGEN_CAPTURED_ACCOUNTS,
    OXYGEN_MELT_OFFGAS_ACCOUNT,
    OXYGEN_MELT_OFFGAS_CAPTURED_ACCOUNT,
    OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
    OXYGEN_MRE_ANODE_ACCOUNT,
    OXYGEN_SPECIES,
    OXYGEN_STAGE0_ACCOUNT,
    OXYGEN_STORED_ACCOUNTS,
    OXYGEN_VENTED_ACCOUNTS,
)
from simulator.accounting.exceptions import AccountingError
from simulator.accounting.formulas import (
    ATOMIC_WEIGHTS_G_PER_MOL,
    resolve_species_formula,
)

OXYGEN_ACCOUNTING_TOLERANCE_KG = 1e-9
FREE_ANALYZER_OXYGEN_SPECIES = frozenset({OXYGEN_SPECIES, "O"})
OVERHEAD_VAPOR_ACCOUNTS = (
    "process.overhead_gas",
    "terminal.offgas",
)
NEAR_MELT_SOURCE_ACCOUNT = "process.overhead_gas"
TERMINAL_ESCAPE_ACCOUNT = "terminal.offgas"
CONDENSATION_TRAIN_ACCOUNT = "process.condensation_train"
WALL_DEPOSIT_SEGMENT_ACCOUNT_PREFIX = "process.wall_deposit_segment_"
FREE_ANALYZER_OXYGEN_ACCOUNTS = (
    *OVERHEAD_VAPOR_ACCOUNTS,
    *OXYGEN_STORED_ACCOUNTS,
    *OXYGEN_VENTED_ACCOUNTS,
    *OXYGEN_CAPTURED_ACCOUNTS,
)
PLUME_PRODUCT_SIO2_SPECIES = "SiO2"
PLUME_SOURCE_SIO_SPECIES = "SiO"
FROZEN_SIO_SOURCE_VAPOR_CEILING_MOL = 0.013617600827
MAJOR_METAL_OXIDE_SOURCE_VAPOR_CEILINGS_MOL = {
    PLUME_SOURCE_SIO_SPECIES: FROZEN_SIO_SOURCE_VAPOR_CEILING_MOL,
    "Na2O": 0.0,
    "K2O": 0.0,
    "FeO": 0.0,
    "MgO": 0.0,
    "CaO": 0.0,
    "Al2O3": 0.0,
    "TiO2": 0.0,
    "CrO2": 0.0,
}
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
STAGE0_MELT_REDOX_ACCOUNT = "process.cleaned_melt"
STAGE0_O2_SOURCE_ACCOUNT_PREFIXES = (
    "process.stage0_",
    "reservoir.stage0_",
)
STAGE0_FOULANT_GROUPS = (
    "trapped_gasses",
    "refractory_carbon",
    "other_mineral_contaminant",
)
STAGE0_FOULANT_PARTITION_FIELDS = (
    "escaped_kg",
    "retained_kg",
    "wall_deposit_kg",
    "rump_kg",
    "burned_kg",
)
STAGE0_FOULANT_CLOSURE_TOLERANCE_KG = 1e-9
STAGE0_FOULANT_CLOSURE_REL_TOL = 1e-9
ROBINOT_EXP1_ANALYZER_VISIBLE_O2_KG = 35.0e-6
ROBINOT_EXP2_ANALYZER_VISIBLE_O2_KG = 39.229e-6
ROBINOT_RAW_FAITHFUL_SOURCE_SIDE_O2_KG = 0.881913e-3
ROBINOT_LITERATURE_ALPHA_TOP_AREA_O2_KG = 0.656204e-3
ROBINOT_LITERATURE_ALPHA_AREA_FACTOR_BAND = (18.25, 19.04)
ROBINOT_REPRODUCIBILITY_FLOOR_FRACTION = 0.11
ROBINOT_O2_NORMALIZATION_NOTE = (
    "25.20x compares the raw faithful source-side O2 potential "
    "(0.881913 g O2-equivalent) to Robinot exp. 1 analyzer-visible "
    "O2 (35 mg). 18.75x compares the literature-alpha/top-area "
    "forward prediction (0.656204 g O2-equivalent) to the same "
    "35 mg paper quantity; it is a different normalization, not a "
    "closure fit."
)
REACTION_FAMILY_PARTITION_CARBON = "partition_carbon"
REACTION_FAMILY_VOLATILIZATION = "volatilization"
REACTION_FAMILY_SULFATE_DECOMP = "sulfate_decomp"
REACTION_FAMILY_SILICATE_DISPLACEMENT = "silicate_displacement"
REACTION_FAMILY_CARBONATE_DECOMPOSITION = "carbonate_decomposition"
REACTION_FAMILY_INERT_TO_RUMP = "inert_to_rump"
REACTION_FAMILY_PERCHLORATE = "perchlorate"


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
            "terminal.stage0_chloride_salt_phase",
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
        consumed_getter = getattr(self.sim, "_consumed_additive_reagents_kg", None)
        if callable(consumed_getter):
            _merge_masses(products, consumed_getter())
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

    def stage0_foulant_partition_by_group(self) -> dict[str, dict[str, Any]]:
        """Read-only Stage-0 foulant fate rollup by reporting group."""
        groups = _empty_stage0_foulant_partition_groups()
        registry = _stage0_foulant_registry(self.sim)
        diagnostics = _coalesced_stage0_foulant_diagnostics(
            getattr(self.sim, "_stage0_foulant_diagnostics", ()) or ()
        )
        expected_source_debit_kg = _stage0_authoritative_foulant_source_debit_kg(
            self.sim,
            diagnostics,
        )

        for diagnostic in diagnostics:
            if not isinstance(diagnostic, Mapping):
                continue
            rows = _stage0_foulant_partition_rows(diagnostic, registry)
            for row in rows:
                _add_stage0_foulant_partition_row(groups, row)

        return _finalize_stage0_foulant_partition_groups(
            groups,
            expected_source_debit_kg,
        )

    def stage0_foulant_hourly_by_group(
        self,
        snapshot: Any,
    ) -> dict[str, dict[str, Any]]:
        """Read one snapshot's reset-per-hour foulant deltas by group."""
        groups = _empty_stage0_foulant_hourly_groups()
        registry = _stage0_foulant_registry(self.sim)

        by_group = getattr(snapshot, "by_group", None)
        if isinstance(by_group, Mapping) and by_group:
            for group, events in by_group.items():
                if group not in STAGE0_FOULANT_GROUPS:
                    if _stage0_hourly_events_positive_mass_kg(events) > 0.0:
                        raise AccountingError(
                            "Stage-0 foulant hourly event has unknown "
                            f"group {group!r} with positive mass"
                        )
                    continue
                for event in _stage0_hourly_event_mappings(events):
                    _add_stage0_foulant_hourly_event(
                        groups,
                        str(group),
                        event,
                    )
            return _finalize_stage0_foulant_hourly_groups(groups)

        evap = getattr(snapshot, "evap_flux", None)
        species_kg_hr = getattr(evap, "species_kg_hr", {}) or {}
        if isinstance(species_kg_hr, Mapping):
            for species, kg in species_kg_hr.items():
                amount = _stage0_positive_float(kg)
                if amount <= 0.0:
                    continue
                group = _stage0_group_for_carrier(
                    str(species),
                    "",
                    registry,
                )
                groups[group]["escaped_kg"] += amount

        wall_delta = getattr(
            snapshot,
            "wall_deposit_by_segment_species_delta",
            {},
        ) or {}
        if isinstance(wall_delta, Mapping):
            for key, kg in wall_delta.items():
                if not isinstance(key, tuple) or len(key) != 2:
                    continue
                amount = _stage0_positive_float(kg)
                if amount <= 0.0:
                    continue
                species = str(key[1])
                group = _stage0_group_for_carrier(species, "", registry)
                groups[group]["wall_deposit_kg"] += amount

        return _finalize_stage0_foulant_hourly_groups(groups)

    def oxygen_terminal_partition_kg(self) -> dict[str, float]:
        stored_by_source = {
            account: _ledger_o2_kg(self.ledger, account)
            for account in OXYGEN_STORED_ACCOUNTS
        }
        vented_by_source = {
            account: _ledger_o2_kg(self.ledger, account)
            for account in OXYGEN_VENTED_ACCOUNTS
        }
        captured_by_source = {
            account: _ledger_o2_kg(self.ledger, account)
            for account in OXYGEN_CAPTURED_ACCOUNTS
        }
        stored_kg = sum(stored_by_source.values())
        vented_kg = sum(vented_by_source.values())
        captured_kg = sum(captured_by_source.values())
        stage0_o2_vented = vented_by_source.get(
            OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT, 0.0)
        stage0_o2_recovered = stored_by_source.get(OXYGEN_STAGE0_ACCOUNT, 0.0)
        stage0_o2_bound = self._stage0_o2_bound_into_melt_redox_kg(
            stage0_o2_vented,
            stage0_o2_recovered,
        )
        return {
            "stored": stored_kg,
            "vented": vented_kg,
            "captured": captured_kg,
            "total": stored_kg + vented_kg + captured_kg,
            "stage0_stored": stage0_o2_recovered,
            "melt_offgas_stored": stored_by_source.get(
                OXYGEN_MELT_OFFGAS_ACCOUNT, 0.0),
            "mre_anode_stored": stored_by_source.get(
                OXYGEN_MRE_ANODE_ACCOUNT, 0.0),
            "melt_offgas_vented": vented_by_source.get(
                OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT, 0.0),
            "melt_offgas_captured": captured_by_source.get(
                OXYGEN_MELT_OFFGAS_CAPTURED_ACCOUNT, 0.0),
            "stage0_o2_vented_with_offgas": stage0_o2_vented,
            "stage0_o2_recovered_stored": stage0_o2_recovered,
            "stage0_o2_bound_into_melt_redox": stage0_o2_bound,
        }

    def _stage0_o2_bound_into_melt_redox_kg(
        self,
        stage0_o2_vented_kg: float,
        stage0_o2_recovered_kg: float,
    ) -> float:
        redox_o2_kg = 0.0
        terminal_o2_accounts = frozenset(
            (
                *OXYGEN_STORED_ACCOUNTS,
                *OXYGEN_VENTED_ACCOUNTS,
                *OXYGEN_CAPTURED_ACCOUNTS,
            )
        )
        for transition in getattr(self.ledger, "transitions", ()):
            if not str(getattr(transition, "name", "")).startswith("stage0_"):
                continue
            credits = tuple(getattr(transition, "credits", ()))
            if not any(
                getattr(lot, "account", "") == STAGE0_MELT_REDOX_ACCOUNT
                for lot in credits
            ):
                continue
            for lot in getattr(transition, "debits", ()):
                account = str(getattr(lot, "account", ""))
                if account in terminal_o2_accounts:
                    continue
                if not account.startswith(STAGE0_O2_SOURCE_ACCOUNT_PREFIXES):
                    continue
                species_kg = getattr(lot, "species_kg", {})
                redox_o2_kg += float(species_kg.get(OXYGEN_SPECIES, 0.0))

        consumed_kg = (
            float(stage0_o2_vented_kg)
            + float(stage0_o2_recovered_kg)
            + redox_o2_kg
        )
        bound_kg = (
            consumed_kg
            - float(stage0_o2_vented_kg)
            - float(stage0_o2_recovered_kg)
        )
        if bound_kg <= OXYGEN_ACCOUNTING_TOLERANCE_KG:
            return 0.0
        return bound_kg

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
        terminal_oxygen_partition_kg = self.oxygen_terminal_partition_kg()

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
            "robinot_o2_error_budget": _robinot_o2_error_budget(
                free_oxygen_atom_mol=free_oxygen_atom_mol,
                terminal_oxygen_partition_kg=terminal_oxygen_partition_kg,
                condensation_oxygen_atom_mol=condensation_oxygen_atom_mol,
                wall_segment_oxygen_atom_mol=wall_segment_oxygen_atom_mol,
            ),
        }

    def lab_plume_product_partition(self) -> dict[str, Any]:
        """Position-resolved plume-product diagnostic for Channel A falsification.

        Near-melt reads ``process.overhead_gas`` only (pre-condensation source
        proxy). ``terminal.offgas`` is downstream escape (``terminal_escape``),
        not near-melt. Outlet ``plume_product_proxy`` is condensation-train
        SiO2 — a route-product proxy, not direct O2-consuming ledger proof.
        """
        balances = self.ledger.mol_by_account()
        registry = self.ledger.registry

        near_melt_species_mol = dict(
            balances.get(NEAR_MELT_SOURCE_ACCOUNT, {}) or {}
        )
        terminal_escape_species_mol = dict(
            balances.get(TERMINAL_ESCAPE_ACCOUNT, {}) or {}
        )
        outlet_species_mol = dict(
            balances.get(CONDENSATION_TRAIN_ACCOUNT, {}) or {}
        )

        near_melt = _plume_position_reading(
            near_melt_species_mol,
            registry,
        )
        terminal_escape = _plume_position_reading(
            terminal_escape_species_mol,
            registry,
        )
        outlet = _plume_position_reading(
            outlet_species_mol,
            registry,
        )

        plume_extent_mol = float(
            outlet["plume_product_proxy"]["species_mol"]
        )
        near_melt_sio_mol = float(
            near_melt["sio"]["species_mol"]
        )
        sio_source_proxy_mol = near_melt_sio_mol + plume_extent_mol
        near_melt_o2_mol = float(
            near_melt["free_analyzer_oxygen"]["species_mol"].get(
                OXYGEN_SPECIES, 0.0
            )
        )
        predicted_outlet_o2_mol = (
            near_melt_o2_mol - 0.5 * plume_extent_mol
        )
        stoichiometric_o2_deficit_mol = max(0.0, -predicted_outlet_o2_mol)
        observed_outlet_o2_mol = float(
            outlet["free_analyzer_oxygen"]["species_mol"].get(
                OXYGEN_SPECIES, 0.0
            )
        )

        ceiling_offenders: list[str] = []
        for species, ceiling_mol in sorted(
            MAJOR_METAL_OXIDE_SOURCE_VAPOR_CEILINGS_MOL.items()
        ):
            if species == PLUME_SOURCE_SIO_SPECIES:
                source_mol = sio_source_proxy_mol
            else:
                source_mol = float(
                    near_melt_species_mol.get(species, 0.0) or 0.0
                )
            if source_mol > ceiling_mol + 1e-15:
                ceiling_offenders.append(species)

        return {
            "schema": "rec_w1_02_qms_oes_position_resolved.v1",
            "near_melt": {
                "account": NEAR_MELT_SOURCE_ACCOUNT,
                **near_melt,
            },
            "terminal_escape": {
                "account": TERMINAL_ESCAPE_ACCOUNT,
                **terminal_escape,
            },
            "outlet": {
                "account": CONDENSATION_TRAIN_ACCOUNT,
                **outlet,
            },
            "discriminant": {
                "stoichiometry": (
                    "SiO + 0.5 O2 -> SiO2(plume_product_proxy)"
                ),
                "plume_extent_mol": plume_extent_mol,
                "near_melt_o2_mol": near_melt_o2_mol,
                "predicted_outlet_o2_mol": predicted_outlet_o2_mol,
                "stoichiometric_o2_deficit_mol": stoichiometric_o2_deficit_mol,
                "observed_outlet_o2_mol": observed_outlet_o2_mol,
                "predicted_minus_observed_outlet_o2_mol": (
                    predicted_outlet_o2_mol - observed_outlet_o2_mol
                ),
            },
            "ceiling_breach": {
                "breached": bool(ceiling_offenders),
                "offending_species": ceiling_offenders,
                "sio_source_proxy_mol": sio_source_proxy_mol,
                "frozen_sio_ceiling_mol": (
                    FROZEN_SIO_SOURCE_VAPOR_CEILING_MOL
                ),
                "major_metal_oxide_ceilings_mol": dict(
                    MAJOR_METAL_OXIDE_SOURCE_VAPOR_CEILINGS_MOL
                ),
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


def _ratio_or_none(numerator: float, denominator: float) -> float | None:
    if denominator <= 0.0:
        return None
    return float(numerator) / float(denominator)


def _oxygen_atom_mol_to_o2_equivalent_kg(oxygen_atom_mol: float) -> float:
    return float(oxygen_atom_mol) * ATOMIC_WEIGHTS_G_PER_MOL["O"] / 1000.0


def _robinot_o2_error_budget(
    *,
    free_oxygen_atom_mol: float,
    terminal_oxygen_partition_kg: Mapping[str, float],
    condensation_oxygen_atom_mol: float,
    wall_segment_oxygen_atom_mol: float,
) -> dict[str, Any]:
    model_free_o2_equivalent_kg = _oxygen_atom_mol_to_o2_equivalent_kg(
        free_oxygen_atom_mol
    )
    condensation_o2_equivalent_kg = _oxygen_atom_mol_to_o2_equivalent_kg(
        condensation_oxygen_atom_mol
    )
    wall_o2_equivalent_kg = _oxygen_atom_mol_to_o2_equivalent_kg(
        wall_segment_oxygen_atom_mol
    )
    literature_factor = _ratio_or_none(
        ROBINOT_LITERATURE_ALPHA_TOP_AREA_O2_KG,
        ROBINOT_EXP1_ANALYZER_VISIBLE_O2_KG,
    )
    raw_factor = _ratio_or_none(
        ROBINOT_RAW_FAITHFUL_SOURCE_SIDE_O2_KG,
        ROBINOT_EXP1_ANALYZER_VISIBLE_O2_KG,
    )
    missing_literature_o2_kg = (
        ROBINOT_LITERATURE_ALPHA_TOP_AREA_O2_KG
        - ROBINOT_EXP1_ANALYZER_VISIBLE_O2_KG
    )

    return {
        "schema": "robinot_o2_error_budget.v1",
        "status": "WARN_ONLY",
        "golden_neutral": True,
        "comparison_target": {
            "paper": "Robinot et al. 2026 exp. 1 analyzer-visible O2",
            "exp1_analyzer_visible_o2_kg": (
                ROBINOT_EXP1_ANALYZER_VISIBLE_O2_KG
            ),
            "exp2_analyzer_visible_o2_kg": (
                ROBINOT_EXP2_ANALYZER_VISIBLE_O2_KG
            ),
            "reproducibility_floor_fraction": (
                ROBINOT_REPRODUCIBILITY_FLOOR_FRACTION
            ),
            "source": (
                "docs/lab-validation-whitepaper.md section 4.1 and "
                "section 5 E3"
            ),
        },
        "model_runtime": {
            "free_analyzer_visible_oxygen_atom_mol": (
                float(free_oxygen_atom_mol)
            ),
            "free_analyzer_visible_o2_equivalent_kg": (
                model_free_o2_equivalent_kg
            ),
            "factor_vs_robinot_exp1": _ratio_or_none(
                model_free_o2_equivalent_kg,
                ROBINOT_EXP1_ANALYZER_VISIBLE_O2_KG,
            ),
            "terminal_oxygen_partition_kg": dict(
                sorted(terminal_oxygen_partition_kg.items())
            ),
            "bound_oxygen_context_kg": {
                "condensation_train_o2_equivalent": (
                    condensation_o2_equivalent_kg
                ),
                "wall_deposit_o2_equivalent": wall_o2_equivalent_kg,
            },
        },
        "published_normalizations": {
            "raw_faithful_source_side_potential": {
                "o2_equivalent_kg": (
                    ROBINOT_RAW_FAITHFUL_SOURCE_SIDE_O2_KG
                ),
                "factor_vs_exp1": raw_factor,
                "bucket": "source-side O2 potential before sink allocation",
                "source": (
                    "docs/lab-validation-whitepaper.md section 5 "
                    "Raw faithful model row"
                ),
            },
            "literature_alpha_top_area_source_side_potential": {
                "o2_equivalent_kg": (
                    ROBINOT_LITERATURE_ALPHA_TOP_AREA_O2_KG
                ),
                "factor_vs_exp1": literature_factor,
                "factor_band_vs_exp1": list(
                    ROBINOT_LITERATURE_ALPHA_AREA_FACTOR_BAND
                ),
                "bucket": (
                    "source-side/free-O2 potential after literature-alpha "
                    "and top-area forward prediction"
                ),
                "source": (
                    "docs/lab-validation-whitepaper.md section 4.1 and "
                    "section 5 Literature-alpha/top-area row"
                ),
            },
            "normalization_note": ROBINOT_O2_NORMALIZATION_NOTE,
        },
        "budget_terms": {
            "plume_oxidation": {
                "source": (
                    "docs/lab-validation-whitepaper.md M3 / four-channel "
                    "allocation"
                ),
                "direction": (
                    "LOWER_SIM_O2: consumes free O2/O into plume products "
                    "before the analyzer"
                ),
                "magnitude": {
                    "kind": "unquantified",
                    "reason": (
                        "requires position-resolved gas sampling near melt, "
                        "pre-filter, and outlet"
                    ),
                },
            },
            "deposit_gettering": {
                "source": (
                    "docs/lab-validation-whitepaper.md M3 / per-surface "
                    "deposition rows"
                ),
                "direction": (
                    "LOWER_SIM_O2: binds oxygen into deposits instead of "
                    "outlet free O2"
                ),
                "magnitude": {
                    "kind": "unquantified",
                    "runtime_bound_oxygen_context_kg": (
                        condensation_o2_equivalent_kg
                        + wall_o2_equivalent_kg
                    ),
                    "reason": (
                        "deposit oxygen fraction and in-run vs post-run "
                        "oxidation state are not measured"
                    ),
                },
            },
            "melt_redox_retention": {
                "source": (
                    "Stage-0 melt-redox bin (O0 terminal partition) as a "
                    "PROXY for M3 melt-redox-retention; Robinot "
                    "residual-glass-redox allocation is unmeasured"
                ),
                "direction": (
                    "LOWER_SIM_O2: retains oxygen in melt redox instead "
                    "of terminal free O2"
                ),
                "magnitude": {
                    "kind": "runtime_accounted",
                    "stage0_o2_bound_into_melt_redox_kg": float(
                        terminal_oxygen_partition_kg.get(
                            "stage0_o2_bound_into_melt_redox", 0.0
                        )
                        or 0.0
                    ),
                    "reason": (
                        "current ledger can expose this bin, but Robinot "
                        "allocation remains unmeasured"
                    ),
                },
            },
            "post_run_air_oxidation": {
                "source": (
                    "docs/lab-validation-whitepaper.md M3 / E5 oxidation "
                    "state caveat"
                ),
                "direction": (
                    "NO_IN_RUN_CLOSURE: can raise recovered-deposit oxygen "
                    "after venting, but cannot lower in-run analyzer O2"
                ),
                "magnitude": {
                    "kind": "unquantified",
                    "reason": (
                        "requires air-isolated deposit oxidation-state data"
                    ),
                },
            },
            "analyzer_flow_baseline": {
                "source": (
                    "docs/lab-validation-whitepaper.md E3 O2 ppm x flow "
                    "integration"
                ),
                "direction": (
                    "RAISE_PAPER_O2 if the trace missed peaks, baseline, "
                    "or flow; LOWER_PAPER_O2 if ppm/flow was overestimated"
                ),
                "magnitude": {
                    "kind": "quantified_anchor",
                    "exp1_o2_kg": ROBINOT_EXP1_ANALYZER_VISIBLE_O2_KG,
                    "exp2_o2_kg": ROBINOT_EXP2_ANALYZER_VISIBLE_O2_KG,
                    "reproducibility_floor_fraction": (
                        ROBINOT_REPRODUCIBILITY_FLOOR_FRACTION
                    ),
                    "status": (
                        "whitepaper E3 killed as a primary 18.75x-25.20x "
                        "closure mechanism"
                    ),
                },
            },
        },
        "unexplained_residual": {
            "kind": "explicit_unexplained",
            "central_missing_free_o2_equivalent_kg": (
                missing_literature_o2_kg
            ),
            "full_prediction_factor_vs_exp1": literature_factor,
            "residual_factor_vs_exp1": _ratio_or_none(
                missing_literature_o2_kg, ROBINOT_EXP1_ANALYZER_VISIBLE_O2_KG
            ),
            "statement": (
                "Allocation of the missing O2 among plume oxidation, "
                "deposit gettering, melt-redox retention, and post-run "
                "air oxidation is not yet measured; this diagnostic does "
                "not tune or close the gap."
            ),
        },
    }


def _empty_stage0_foulant_partition_groups() -> dict[str, dict[str, Any]]:
    return {
        group: {
            "escaped_kg": 0.0,
            "retained_kg": 0.0,
            "wall_deposit_kg": 0.0,
            "rump_kg": 0.0,
            "burned_kg": 0.0,
            "_source_kg": 0.0,
            "_reaction_family_totals_kg": {},
            "_residual_intervals": [],
        }
        for group in STAGE0_FOULANT_GROUPS
    }


def _empty_stage0_foulant_hourly_groups() -> dict[str, dict[str, Any]]:
    return {
        group: {
            "escaped_kg": 0.0,
            "retained_kg": 0.0,
            "wall_deposit_kg": 0.0,
            "rump_kg": 0.0,
            "burned_kg": 0.0,
            "_residual_intervals": [],
        }
        for group in STAGE0_FOULANT_GROUPS
    }


def _stage0_foulant_registry(sim: Any) -> Any | None:
    getter = getattr(sim, "_load_foulant_registry_cached", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        return None


def _stage0_positive_float(value: Any) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(amount) or amount <= 0.0:
        return 0.0
    return amount


def _stage0_positive_mass_sum(values: Any) -> float:
    if not isinstance(values, Mapping):
        return 0.0
    total = 0.0
    for species, value in values.items():
        amount = _stage0_optional_float(value)
        if amount is None:
            continue
        if amount < -STAGE0_FOULANT_CLOSURE_TOLERANCE_KG:
            raise AccountingError(
                f"Stage-0 foulant product mass is negative for {species!r}: "
                f"{amount:.15g} kg"
            )
        if amount > 0.0:
            total += amount
    return total


def _stage0_optional_float(value: Any) -> float | None:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amount):
        return None
    return amount


def _stage0_fraction(value: Any) -> float:
    amount = _stage0_optional_float(value)
    if amount is None:
        return 0.0
    return amount


def _stage0_group_for_carrier(
    carrier: str,
    reaction_family: str,
    registry: Any | None,
) -> str:
    if registry is not None:
        alias_to_carrier = getattr(registry, "alias_to_carrier", {}) or {}
        carriers = getattr(registry, "carriers", {}) or {}
        key = alias_to_carrier.get(carrier) or alias_to_carrier.get(
            carrier.lower()
        )
        entry = carriers.get(key) if key is not None else None
        group = getattr(entry, "group", None)
        if group in STAGE0_FOULANT_GROUPS:
            return str(group)
    if reaction_family == REACTION_FAMILY_PARTITION_CARBON:
        return "refractory_carbon"
    return "other_mineral_contaminant"


def _stage0_normalized_component_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _stage0_raw_inventory_components_kg(sim: Any) -> dict[str, float] | None:
    inventory = getattr(sim, "inventory", None)
    raw_components = getattr(inventory, "raw_components_kg", None)
    if not isinstance(raw_components, Mapping):
        return None
    result: dict[str, float] = {}
    for component, value in raw_components.items():
        amount = _stage0_positive_float(value)
        if amount > 0.0:
            result[str(component)] = amount
    return result


def _stage0_species_element_mass_fraction(
    species: str,
    element: str,
    formula_registry: Mapping[str, Any] | None,
) -> float:
    formula = resolve_species_formula(species, formula_registry)
    element_count = float(getattr(formula, "elements", {}).get(element, 0.0))
    if element_count <= 0.0:
        raise AccountingError(
            f"Stage-0 foulant carrier {species!r} contains no {element}"
        )
    element_formula = resolve_species_formula(element, formula_registry)
    element_mass = element_count * element_formula.molar_mass_kg_per_mol()
    carrier_mass = formula.molar_mass_kg_per_mol()
    if carrier_mass <= 0.0:
        raise AccountingError(
            f"Stage-0 foulant carrier {species!r} has invalid molar mass"
        )
    return element_mass / carrier_mass


def _stage0_authoritative_foulant_source_debit_kg(
    sim: Any,
    diagnostics: Any,
) -> float | None:
    raw_components = _stage0_raw_inventory_components_kg(sim)
    if raw_components is None:
        return None
    raw_key_by_normalized = {
        _stage0_normalized_component_key(component): component
        for component in raw_components
    }
    consumed_raw_keys: set[str] = set()
    expected_kg = 0.0
    chloride_split_totals: dict[str, float] = {}
    formula_registry = getattr(sim, "species_formula_registry", None)

    def consume_raw_source(*candidates: Any) -> bool:
        nonlocal expected_kg
        for candidate in candidates:
            key = _stage0_normalized_component_key(candidate)
            if not key:
                continue
            raw_key = raw_key_by_normalized.get(key)
            if raw_key is None:
                continue
            if raw_key not in consumed_raw_keys:
                expected_kg += raw_components[raw_key]
                consumed_raw_keys.add(raw_key)
            return True
        return False

    def raw_component_kg(*candidates: Any) -> tuple[str, float] | None:
        for candidate in candidates:
            key = _stage0_normalized_component_key(candidate)
            if not key:
                continue
            raw_key = raw_key_by_normalized.get(key)
            if raw_key is not None:
                return raw_key, raw_components[raw_key]
        return None

    for diagnostic in diagnostics:
        if not isinstance(diagnostic, Mapping):
            continue
        family = str(diagnostic.get("reaction_family", ""))
        carrier = str(
            diagnostic.get("carrier")
            or diagnostic.get("species")
            or diagnostic.get("source_component")
            or ""
        )
        source_component = diagnostic.get("source_component")

        if (
            family == REACTION_FAMILY_VOLATILIZATION
            and _stage0_normalized_component_key(
                diagnostic.get("feed_basis")
            ) == "elemental_cl"
        ):
            source = raw_component_kg(source_component, "Cl")
            if source is None:
                raise AccountingError(
                    "Stage-0 foulant diagnostic has no raw Cl source "
                    f"for carrier {carrier!r}"
                )
            raw_key, raw_cl_kg = source
            split = _stage0_fraction(diagnostic.get("chloride_split_fraction"))
            if split <= 0.0:
                source_cl_kg = _stage0_positive_float(
                    diagnostic.get("source_cl_kg")
                )
                if raw_cl_kg > 0.0 and source_cl_kg > 0.0:
                    split = source_cl_kg / raw_cl_kg
            if split <= 0.0:
                raise AccountingError(
                    "Stage-0 foulant chloride diagnostic has no positive "
                    f"split for carrier {carrier!r}"
                )
            if split > 1.0 + STAGE0_FOULANT_CLOSURE_REL_TOL:
                raise AccountingError(
                    "Stage-0 foulant chloride split exceeds source inventory: "
                    f"{split:.15g} for carrier {carrier!r}"
                )
            chloride_split_totals[raw_key] = (
                chloride_split_totals.get(raw_key, 0.0) + split
            )
            cl_fraction = _stage0_species_element_mass_fraction(
                carrier,
                "Cl",
                formula_registry,
            )
            expected_kg += raw_cl_kg * split / cl_fraction
            continue

        if family in {
            REACTION_FAMILY_CARBONATE_DECOMPOSITION,
            REACTION_FAMILY_SILICATE_DISPLACEMENT,
        }:
            matched = consume_raw_source(
                source_component,
                carrier,
                diagnostic.get("species"),
                "carbonate_salts",
            )
        elif family == REACTION_FAMILY_SULFATE_DECOMP:
            matched = consume_raw_source(
                source_component,
                carrier,
                diagnostic.get("species"),
                "SO3",
                "sulfate",
                "sulfates",
            )
        elif family == REACTION_FAMILY_PERCHLORATE:
            matched = consume_raw_source(source_component, "ClO4", carrier)
        elif family == REACTION_FAMILY_PARTITION_CARBON:
            matched = consume_raw_source(
                source_component,
                carrier,
                diagnostic.get("species"),
                "carbonaceous_organic",
                "organics",
                "hydrocarbons",
            )
        elif family in {
            REACTION_FAMILY_VOLATILIZATION,
            REACTION_FAMILY_INERT_TO_RUMP,
        }:
            matched = consume_raw_source(
                source_component,
                carrier,
                diagnostic.get("species"),
            )
        else:
            matched = True

        if not matched:
            raise AccountingError(
                "Stage-0 foulant diagnostic has no raw inventory source: "
                f"reaction_family={family!r}, carrier={carrier!r}"
            )

    for raw_key, split_total in chloride_split_totals.items():
        if split_total > 1.0 + STAGE0_FOULANT_CLOSURE_REL_TOL:
            raise AccountingError(
                "Stage-0 foulant chloride diagnostics overdraw raw source "
                f"{raw_key!r}: split total {split_total:.15g}"
            )

    return expected_kg


def _coalesced_stage0_foulant_diagnostics(
    diagnostics: Any,
) -> list[Mapping[str, Any]]:
    coalesced: list[Mapping[str, Any]] = []
    sulfate: dict[tuple[str, float], dict[str, Any]] = {}
    seen_non_sulfate: set[tuple[str, str, float, str]] = set()
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, Mapping):
            continue
        family = str(diagnostic.get("reaction_family", ""))
        carrier = str(diagnostic.get("carrier") or diagnostic.get("species") or "")
        feed_kg = _stage0_positive_float(diagnostic.get("feed_kg"))
        if family != REACTION_FAMILY_SULFATE_DECOMP:
            source_component = str(diagnostic.get("source_component") or "")
            key = (family, carrier, feed_kg, source_component)
            if feed_kg > 0.0 and key in seen_non_sulfate:
                raise AccountingError(
                    "duplicate Stage-0 foulant diagnostic source debit for "
                    f"reaction_family={family!r}, carrier={carrier!r}, "
                    f"feed_kg={feed_kg:.15g}, "
                    f"source_component={source_component!r}"
                )
            seen_non_sulfate.add(key)
            coalesced.append(diagnostic)
            continue
        key = (carrier, feed_kg)
        row = sulfate.setdefault(
            key,
            {
                **dict(diagnostic),
                "_retained_fraction_product": 1.0,
                "_phase_rows": [],
            },
        )
        extent = _stage0_fraction(diagnostic.get("extent"))
        row["_retained_fraction_product"] *= 1.0 - extent
        row["_phase_rows"].append(dict(diagnostic))
    for row in sulfate.values():
        retained = float(row.pop("_retained_fraction_product"))
        row["extent"] = 1.0 - retained
        row["phase_rows"] = tuple(row.pop("_phase_rows"))
        coalesced.append(row)
    return coalesced


def _stage0_foulant_partition_rows(
    diagnostic: Mapping[str, Any],
    registry: Any | None,
) -> list[dict[str, Any]]:
    family = str(diagnostic.get("reaction_family", ""))
    carrier = str(
        diagnostic.get("carrier")
        or diagnostic.get("species")
        or diagnostic.get("source_component")
        or ""
    )
    if family == REACTION_FAMILY_PARTITION_CARBON:
        return _stage0_partition_carbon_rows(diagnostic)

    feed_kg = _stage0_positive_float(diagnostic.get("feed_kg"))
    if feed_kg <= 0.0:
        return []
    group = _stage0_group_for_carrier(carrier, family, registry)

    if family == REACTION_FAMILY_VOLATILIZATION:
        escaped = feed_kg * _stage0_fraction(
            diagnostic.get("cumulative_escaped_frac")
        )
        retained = feed_kg * _stage0_fraction(
            diagnostic.get("cumulative_retained_frac")
        )
        wall = feed_kg * _stage0_fraction(diagnostic.get("wall_deposit_frac"))
        if wall > escaped and math.isclose(
            wall,
            escaped,
            rel_tol=STAGE0_FOULANT_CLOSURE_REL_TOL,
            abs_tol=STAGE0_FOULANT_CLOSURE_TOLERANCE_KG,
        ):
            wall = escaped
        escaped_nonwall = escaped - wall
        return [_stage0_partition_row(
            group,
            family,
            feed_kg,
            escaped_kg=escaped_nonwall,
            retained_kg=retained,
            wall_deposit_kg=wall,
        )]

    if family in {
        REACTION_FAMILY_SULFATE_DECOMP,
        REACTION_FAMILY_CARBONATE_DECOMPOSITION,
    }:
        extent = _stage0_fraction(diagnostic.get("extent"))
        return [_stage0_partition_row(
            group,
            family,
            feed_kg,
            escaped_kg=feed_kg * extent,
            retained_kg=feed_kg * (1.0 - extent),
        )]

    if family == REACTION_FAMILY_SILICATE_DISPLACEMENT:
        extent = _stage0_fraction(diagnostic.get("extent"))
        return [_stage0_partition_row(
            group,
            family,
            feed_kg,
            retained_kg=feed_kg * (1.0 - extent),
            rump_kg=feed_kg * extent,
        )]

    if family == REACTION_FAMILY_PERCHLORATE:
        escaped_kg = (
            _stage0_positive_mass_sum(diagnostic.get("salt_products_kg"))
            + _stage0_positive_mass_sum(diagnostic.get("oxygen_products_kg"))
        )
        retained_kg = _stage0_positive_mass_sum(
            diagnostic.get("retained_products_kg")
        )
        allocated_kg = escaped_kg + retained_kg
        if not math.isclose(
            allocated_kg,
            feed_kg,
            rel_tol=STAGE0_FOULANT_CLOSURE_REL_TOL,
            abs_tol=STAGE0_FOULANT_CLOSURE_TOLERANCE_KG,
        ):
            raise AccountingError(
                "Stage-0 perchlorate foulant mass does not close: "
                f"{allocated_kg:.15g} kg products vs {feed_kg:.15g} kg feed"
            )
        rows: list[dict[str, Any]] = []
        if escaped_kg > 0.0:
            rows.append(_stage0_partition_row(
                "trapped_gasses",
                family,
                escaped_kg,
                escaped_kg=escaped_kg,
            ))
        if retained_kg > 0.0:
            rows.append(_stage0_partition_row(
                "other_mineral_contaminant",
                family,
                retained_kg,
                retained_kg=retained_kg,
            ))
        return rows

    if family == REACTION_FAMILY_INERT_TO_RUMP:
        rump_frac = _stage0_fraction(diagnostic.get("rump_frac", 1.0))
        return [_stage0_partition_row(
            group,
            family,
            feed_kg,
            retained_kg=feed_kg * (1.0 - rump_frac),
            rump_kg=feed_kg * rump_frac,
        )]

    raise AccountingError(
        f"unknown Stage-0 foulant reaction_family {family!r} "
        f"for carrier {carrier!r}"
    )


def _stage0_partition_carbon_rows(
    diagnostic: Mapping[str, Any],
) -> list[dict[str, Any]]:
    feed_kg = _stage0_positive_float(diagnostic.get("feed_kg"))
    declared_c_mol = _stage0_positive_float(diagnostic.get("declared_c_mol"))
    if feed_kg <= 0.0 or declared_c_mol <= 0.0:
        return []

    rows: list[dict[str, Any]] = []
    assigned_kg = 0.0

    labile_kg = _stage0_carbon_split_kg(
        diagnostic.get("labile_mol"),
        declared_c_mol,
        feed_kg,
    )
    if labile_kg > 0.0:
        labile_extent = _stage0_fraction(diagnostic.get("labile_extent"))
        assigned_kg += labile_kg
        rows.append(_stage0_partition_row(
            "trapped_gasses",
            REACTION_FAMILY_PARTITION_CARBON,
            labile_kg,
            retained_kg=labile_kg * (1.0 - labile_extent),
            burned_kg=labile_kg * labile_extent,
        ))

    refractory_kg = _stage0_carbon_split_kg(
        diagnostic.get("refractory_mol"),
        declared_c_mol,
        feed_kg,
    )
    if refractory_kg > 0.0:
        assigned_kg += refractory_kg
        interval = dict(diagnostic.get("refractory_interval") or {})
        low = _stage0_fraction(interval.get("low"))
        high = _stage0_fraction(interval.get("high", 1.0))
        retained_kg = refractory_kg * high
        burned_kg = refractory_kg - retained_kg
        rows.append(_stage0_partition_row(
            "refractory_carbon",
            REACTION_FAMILY_PARTITION_CARBON,
            refractory_kg,
            retained_kg=retained_kg,
            burned_kg=burned_kg,
            residual_interval={
                "low_kg": refractory_kg * low,
                "high_kg": refractory_kg * high,
                "reason": interval.get("reason"),
            },
        ))

    carbonate_kg = _stage0_carbon_split_kg(
        diagnostic.get("carbonate_mol"),
        declared_c_mol,
        feed_kg,
    )
    if carbonate_kg > 0.0:
        assigned_kg += carbonate_kg
        rows.append(_stage0_partition_row(
            "other_mineral_contaminant",
            REACTION_FAMILY_PARTITION_CARBON,
            carbonate_kg,
            rump_kg=carbonate_kg,
        ))

    if assigned_kg > feed_kg + STAGE0_FOULANT_CLOSURE_TOLERANCE_KG:
        raise AccountingError(
            "Stage-0 carbon partition source mass exceeds feed_kg: "
            f"{assigned_kg:.15g} kg assigned vs {feed_kg:.15g} kg feed"
        )
    if assigned_kg > feed_kg:
        assigned_kg = feed_kg

    unassigned_kg = feed_kg - assigned_kg
    if unassigned_kg > STAGE0_FOULANT_CLOSURE_TOLERANCE_KG:
        rows.append(_stage0_partition_row(
            "other_mineral_contaminant",
            REACTION_FAMILY_PARTITION_CARBON,
            unassigned_kg,
            retained_kg=unassigned_kg,
            residual_interval={
                "low_kg": 0.0,
                "high_kg": unassigned_kg,
                "reason": "carbon_partition_not_speciated",
            },
        ))
    return rows


def _stage0_carbon_split_kg(
    split_mol: Any,
    declared_c_mol: float,
    feed_kg: float,
) -> float:
    amount = _stage0_optional_float(split_mol)
    if amount is None or amount <= 0.0:
        return 0.0
    return feed_kg * amount / declared_c_mol


def _stage0_partition_row(
    group: str,
    reaction_family: str,
    source_kg: float,
    *,
    escaped_kg: float = 0.0,
    retained_kg: float = 0.0,
    wall_deposit_kg: float = 0.0,
    rump_kg: float = 0.0,
    burned_kg: float = 0.0,
    residual_interval: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    values = {
        "source_kg": float(source_kg),
        "escaped_kg": float(escaped_kg),
        "retained_kg": float(retained_kg),
        "wall_deposit_kg": float(wall_deposit_kg),
        "rump_kg": float(rump_kg),
        "burned_kg": float(burned_kg),
    }
    for field, value in values.items():
        if value < -STAGE0_FOULANT_CLOSURE_TOLERANCE_KG:
            raise AccountingError(
                f"Stage-0 foulant {field} is negative for group {group!r}: "
                f"{value:.15g} kg"
            )
        if value < 0.0:
            values[field] = 0.0
    return {
        "group": group,
        "reaction_family": str(reaction_family),
        "source_kg": values["source_kg"],
        "escaped_kg": values["escaped_kg"],
        "retained_kg": values["retained_kg"],
        "wall_deposit_kg": values["wall_deposit_kg"],
        "rump_kg": values["rump_kg"],
        "burned_kg": values["burned_kg"],
        "residual_interval": (
            dict(residual_interval) if residual_interval is not None else None
        ),
    }


def _add_stage0_foulant_partition_row(
    groups: dict[str, dict[str, Any]],
    row: Mapping[str, Any],
) -> None:
    group = str(row.get("group", ""))
    if group not in groups:
        raise AccountingError(f"unknown Stage-0 foulant group {group!r}")
    payload = groups[group]
    source_kg = float(row.get("source_kg", 0.0) or 0.0)
    payload["_source_kg"] += source_kg
    family = str(row.get("reaction_family", ""))
    family_totals = payload["_reaction_family_totals_kg"]
    family_totals[family] = family_totals.get(family, 0.0) + source_kg
    for field in STAGE0_FOULANT_PARTITION_FIELDS:
        payload[field] += float(row.get(field, 0.0) or 0.0)
    interval = row.get("residual_interval")
    if isinstance(interval, Mapping):
        payload["_residual_intervals"].append(dict(interval))


def _finalize_stage0_foulant_partition_groups(
    groups: dict[str, dict[str, Any]],
    expected_source_debit_kg: float | None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    total_source_debited_kg = 0.0
    for group, payload in groups.items():
        source_kg = float(payload["_source_kg"])
        total_source_debited_kg += source_kg
        allocated_kg = sum(float(payload[field]) for field in STAGE0_FOULANT_PARTITION_FIELDS)
        error_kg = allocated_kg - source_kg
        if not math.isclose(
            allocated_kg,
            source_kg,
            rel_tol=STAGE0_FOULANT_CLOSURE_REL_TOL,
            abs_tol=STAGE0_FOULANT_CLOSURE_TOLERANCE_KG,
        ):
            raise AccountingError(
                f"Stage-0 foulant group {group!r} mass does not close: "
                f"{allocated_kg:.15g} kg allocated vs "
                f"{source_kg:.15g} kg debited"
            )
        error_pct = (
            abs(error_kg) / source_kg * 100.0 if source_kg > 0.0 else 0.0
        )
        result[group] = {
            "escaped_kg": float(payload["escaped_kg"]),
            "retained_kg": float(payload["retained_kg"]),
            "wall_deposit_kg": float(payload["wall_deposit_kg"]),
            "rump_kg": float(payload["rump_kg"]),
            "burned_kg": float(payload["burned_kg"]),
            "residual_interval": _combine_stage0_residual_intervals(
                payload["_residual_intervals"]
            ),
            "reaction_family_totals_kg": dict(sorted(
                payload["_reaction_family_totals_kg"].items()
            )),
            "closure": {
                "source_debited_kg": source_kg,
                "allocated_kg": allocated_kg,
                "error_kg": error_kg,
                "error_pct": error_pct,
            },
        }
    if expected_source_debit_kg is not None and not math.isclose(
        total_source_debited_kg,
        expected_source_debit_kg,
        rel_tol=STAGE0_FOULANT_CLOSURE_REL_TOL,
        abs_tol=STAGE0_FOULANT_CLOSURE_TOLERANCE_KG,
    ):
        raise AccountingError(
            "Stage-0 foulant global source debit does not match feed debit: "
            f"{total_source_debited_kg:.15g} kg debited vs "
            f"{expected_source_debit_kg:.15g} kg feed"
        )
    return result


def _stage0_hourly_events_positive_mass_kg(events: Any) -> float:
    total = 0.0
    for event in _stage0_hourly_event_mappings(events):
        total += _stage0_hourly_event_positive_mass_kg(event)
    return total


def _stage0_hourly_event_mappings(
    events: Any,
) -> tuple[Mapping[str, Any], ...]:
    if isinstance(events, Mapping):
        return (events,)
    return tuple(event for event in events or () if isinstance(event, Mapping))


def _stage0_hourly_event_positive_mass_kg(event: Mapping[str, Any]) -> float:
    total = sum(
        _stage0_positive_float(event.get(field))
        for field in STAGE0_FOULANT_PARTITION_FIELDS
    )
    total += _stage0_positive_float(event.get("amount_kg"))
    total += _stage0_positive_float(event.get("decomposed_kg"))
    total += _stage0_positive_float(event.get("evolved_kg_hr"))
    return total


def _add_stage0_foulant_hourly_event(
    groups: dict[str, dict[str, Any]],
    group: str,
    event: Mapping[str, Any],
) -> None:
    payload = groups[group]
    disposition = str(event.get("disposition", ""))
    amount_kg = _stage0_positive_float(event.get("amount_kg"))
    decomposed_kg = _stage0_positive_float(event.get("decomposed_kg"))
    evolved_kg = _stage0_positive_float(event.get("evolved_kg_hr"))
    explicit_fields = {
        field: _stage0_positive_float(event.get(field))
        for field in STAGE0_FOULANT_PARTITION_FIELDS
    }
    explicit_total_kg = sum(explicit_fields.values())
    feed_kg = _stage0_positive_float(event.get("feed_kg"))
    channel_count = sum((
        explicit_total_kg > 0.0,
        disposition != "" and amount_kg > 0.0,
        decomposed_kg > 0.0,
        evolved_kg > 0.0,
    ))
    if channel_count > 1:
        raise AccountingError(
            "Stage-0 foulant hourly event has multiple positive mass "
            "channels"
        )
    if explicit_total_kg > 0.0:
        _validate_stage0_hourly_feed_closure(feed_kg, explicit_total_kg)
        for field, amount in explicit_fields.items():
            payload[field] += amount
    elif disposition in {"escaped", "evolved"} and amount_kg > 0.0:
        _validate_stage0_hourly_feed_closure(feed_kg, amount_kg)
        payload["escaped_kg"] += amount_kg
    elif disposition == "rump" and amount_kg > 0.0:
        _validate_stage0_hourly_feed_closure(feed_kg, amount_kg)
        payload["rump_kg"] += amount_kg
    elif disposition in {"residual", "carbonate_residual"} and amount_kg > 0.0:
        _validate_stage0_hourly_feed_closure(feed_kg, amount_kg)
        payload["retained_kg"] += amount_kg
    elif decomposed_kg > 0.0:
        _validate_stage0_hourly_feed_closure(feed_kg, decomposed_kg)
        payload["escaped_kg"] += decomposed_kg
    elif evolved_kg > 0.0:
        _validate_stage0_hourly_feed_closure(feed_kg, evolved_kg)
        payload["escaped_kg"] += evolved_kg
    interval = event.get("residual_interval")
    if isinstance(interval, Mapping):
        payload["_residual_intervals"].append(dict(interval))


def _validate_stage0_hourly_feed_closure(
    feed_kg: float,
    allocated_kg: float,
) -> None:
    if feed_kg <= 0.0:
        return
    if not math.isclose(
        allocated_kg,
        feed_kg,
        rel_tol=STAGE0_FOULANT_CLOSURE_REL_TOL,
        abs_tol=STAGE0_FOULANT_CLOSURE_TOLERANCE_KG,
    ):
        raise AccountingError(
            "Stage-0 foulant hourly event mass does not close: "
            f"{allocated_kg:.15g} kg allocated vs {feed_kg:.15g} kg feed"
        )


def _finalize_stage0_foulant_hourly_groups(
    groups: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        group: {
            "escaped_kg": float(payload["escaped_kg"]),
            "retained_kg": float(payload["retained_kg"]),
            "wall_deposit_kg": float(payload["wall_deposit_kg"]),
            "rump_kg": float(payload["rump_kg"]),
            "burned_kg": float(payload["burned_kg"]),
            "residual_interval": _combine_stage0_residual_intervals(
                payload["_residual_intervals"]
            ),
        }
        for group, payload in groups.items()
    }


def _combine_stage0_residual_intervals(
    intervals: list[Mapping[str, Any]],
) -> dict[str, Any] | None:
    if not intervals:
        return None
    low = sum(float(item.get("low_kg", 0.0) or 0.0) for item in intervals)
    high = sum(float(item.get("high_kg", 0.0) or 0.0) for item in intervals)
    reasons = sorted({
        str(item.get("reason"))
        for item in intervals
        if item.get("reason") is not None
    })
    return {
        "low_kg": low,
        "high_kg": high,
        "reasons": reasons,
    }


def _oxygen_atom_mol_for_balances(
    balances: Mapping[str, Mapping[str, float]],
    registry: Mapping[str, Any],
) -> float:
    return sum(
        sum(_oxygen_atom_mol_by_species(species_mol, registry).values())
        for species_mol in balances.values()
    )


def _merge_species_mol_from_accounts(
    balances: Mapping[str, Mapping[str, float]],
    accounts: tuple[str, ...] | list[str],
) -> dict[str, float]:
    merged: dict[str, float] = {}
    for account in accounts:
        for species, mol in (balances.get(str(account), {}) or {}).items():
            amount = float(mol)
            if amount:
                merged[str(species)] = merged.get(str(species), 0.0) + amount
    return dict(sorted(merged.items()))


def _plume_position_reading(
    species_mol: Mapping[str, float],
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    free_species_mol: dict[str, float] = {}
    for species, mol in species_mol.items():
        if species not in FREE_ANALYZER_OXYGEN_SPECIES:
            continue
        amount = float(mol)
        if amount > 0.0:
            free_species_mol[str(species)] = amount

    sio_mol = float(species_mol.get(PLUME_SOURCE_SIO_SPECIES, 0.0) or 0.0)
    plume_sio2_mol = float(
        species_mol.get(PLUME_PRODUCT_SIO2_SPECIES, 0.0) or 0.0
    )
    oxygen_by_species = _oxygen_atom_mol_by_species(species_mol, registry)

    free_oxygen_atom_mol = sum(
        oxygen_by_species.get(species, 0.0)
        for species in FREE_ANALYZER_OXYGEN_SPECIES
        if species in species_mol
    )
    sio_oxygen_atom_mol = oxygen_by_species.get(PLUME_SOURCE_SIO_SPECIES, 0.0)
    plume_sio2_oxygen_atom_mol = oxygen_by_species.get(
        PLUME_PRODUCT_SIO2_SPECIES, 0.0
    )

    return {
        "free_analyzer_oxygen": {
            "species_mol": dict(sorted(free_species_mol.items())),
            "oxygen_atom_mol": free_oxygen_atom_mol,
        },
        "sio": {
            "species_mol": sio_mol,
            "oxygen_atom_mol": sio_oxygen_atom_mol,
        },
        "plume_product_proxy": {
            "species": PLUME_PRODUCT_SIO2_SPECIES,
            "provenance": (
                "condensation_train_route_product_proxy"
            ),
            "species_mol": plume_sio2_mol,
            "oxygen_atom_mol": plume_sio2_oxygen_atom_mol,
        },
    }


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

    alpha_s = _wall_alpha_s(species, getattr(model, "materials", None))
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
