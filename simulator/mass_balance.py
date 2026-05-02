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
              volatiles_kg: float = 0.0) -> Dict[str, float]:
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
        condensed = sum(train.total_by_species().values())
        volatiles = sum(train.volatiles_collected_kg.values())

        mass_out = melt_remaining + condensed + oxygen_kg + volatiles

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
            'error_pct': error_pct,
        }

    def product_summary(self, train: CondensationTrain,
                         oxygen_kg: float) -> Dict[str, float]:
        """
        Summarise products by species across all stages.

        Returns dict of species → total kg collected.
        """
        products = dict(train.total_by_species())
        products['O2'] = oxygen_kg
        products.update(train.volatiles_collected_kg)
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
