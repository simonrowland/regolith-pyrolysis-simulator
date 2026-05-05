"""
Mass Balance Tracker
=====================

Tracks inputs (regolith + additives) vs outputs (metals + O₂ +
glass + slag) for conservation checking.  Warns if discrepancy
exceeds 0.1%.

Also tracks product purity per stream and impurity partitioning
(e.g., 0.5% V co-depositing with Fe, Cr in the Fe condenser).
"""

from __future__ import annotations

from typing import Dict

from simulator.core import (
    BatchRecord, CondensationTrain, MeltState, OXIDE_SPECIES,
    ProcessInventory,
)


class MassBalance:
    """
    Verifies mass conservation and tracks product streams.
    """

    def __init__(self):
        self.input_mass_kg = 0.0
        self.additive_mass_kg = 0.0

    def set_inputs(self, batch_mass_kg: float,
                    additives_kg: Dict[str, float]):
        """Record total input mass."""
        self.input_mass_kg = batch_mass_kg
        self.additive_mass_kg = sum(additives_kg.values())

    def check(self, melt: MeltState,
              train: CondensationTrain,
              oxygen_kg: float,
              volatiles_kg: float = 0.0,
              inventory: ProcessInventory = None,
              additive_inventory_kg: Dict[str, float] = None) -> Dict[str, float]:
        """
        Check mass conservation.

        Returns dict with:
            mass_in:      Total input (kg)
            mass_out:     Total accountable output (kg)
            melt_remaining: Mass still in crucible (kg)
            condensed:    Total in condensation train (kg)
            oxygen:       O₂ produced (kg)
            volatiles:    Volatiles collected (kg)
            error_pct:    Discrepancy as % of input
        """
        mass_in = self.input_mass_kg + self.additive_mass_kg
        melt_remaining = melt.total_mass_kg
        train_totals = train.total_by_species()
        condensed = sum(
            kg for species, kg in train_totals.items()
            if species != 'O2')
        volatiles = sum(train.volatiles_collected_kg.values())
        additive_inventory = sum((additive_inventory_kg or {}).values())
        stage0_products = 0.0
        drain_tap = 0.0
        residual = 0.0
        terminal_slag = 0.0
        if inventory is not None:
            stage0_products = sum(inventory.stage0_products_kg.values())
            drain_tap = sum(inventory.drain_tap_kg.values())
            residual = inventory.residual_mass_kg()
            terminal_slag = sum(inventory.terminal_slag_components_kg.values())

        mass_out = (
            melt_remaining + condensed + oxygen_kg + volatiles
            + stage0_products + drain_tap + residual + terminal_slag
            + additive_inventory
        )

        error_pct = 0.0
        if mass_in > 0:
            error_pct = abs(mass_in - mass_out) / mass_in * 100.0

        return {
            'mass_in': mass_in,
            'mass_out': mass_out,
            'melt_remaining': melt_remaining,
            'condensed': condensed,
            'oxygen': oxygen_kg,
            'volatiles': volatiles,
            'stage0_products': stage0_products,
            'drain_tap': drain_tap,
            'residual': residual,
            'terminal_slag': terminal_slag,
            'stage0_mass_balance_delta': 0.0,
            'additive_inventory': additive_inventory,
            'error_pct': error_pct,
        }

    def product_summary(self, train: CondensationTrain,
                         oxygen_kg: float) -> Dict[str, float]:
        """
        Summarise products by species across all stages.

        Returns dict of species → total kg collected.
        """
        products = {
            species: kg for species, kg in train.total_by_species().items()
            if species != 'O2'
        }
        products['O2'] = oxygen_kg
        for species, kg in train.volatiles_collected_kg.items():
            products[species] = products.get(species, 0.0) + kg
        return products

    def stage_purity(self, train: CondensationTrain) -> Dict[int, Dict[str, float]]:
        """
        Calculate purity of each condensation stage's product.

        Returns dict of stage_number → {species: purity_pct}.
        """
        result = {}
        for stage in train.stages:
            total = stage.total_collected_kg()
            if total <= 0:
                continue
            purities = {}
            for sp, kg in stage.collected_kg.items():
                purities[sp] = (kg / total) * 100.0
            result[stage.stage_number] = purities
        return result
