"""
Mass Balance Tracker
=====================

Tracks inputs (regolith + additives) vs outputs (metals + O₂ +
glass + slag) for conservation checking.  Warns if discrepancy
exceeds 0.1%.

Also tracks product purity per stream and impurity partitioning
(e.g., 0.5% V co-depositing with Fe, Cr in the Fe condenser).

Ferry V / V1-S13: this helper is *not* the hour atom-ledger gate
(``FLOW_MASS_ACCOUNTS`` / ``_flow_mass_out_kg``). It must still not
omit inventory mass that ProcessInventory carries (sulfate feed,
inert melt) or invent a false stage-0 delta, and must keep volatiles
disjoint from stage ``collected_kg``.
"""

from __future__ import annotations

from typing import Dict, Mapping

from simulator.accounting.queries import stage_purity as query_stage_purity
from simulator.core import (
    BatchRecord, CondensationTrain, MeltState, OXIDE_SPECIES,
    ProcessInventory,
)

ZERO_INPUT_BASIS_BREACH = "zero_input_basis_breach"


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
              additive_inventory_kg: Dict[str, float] = None,
              wall_deposit_kg: float = 0.0) -> Dict[str, object]:
        """
        Check mass conservation.

        Returns dict with:
            mass_in:      Total input (kg)
            mass_out:     Total accountable output (kg)
            melt_remaining: Mass still in crucible (kg)
            condensed:    Total in condensation train (kg)
            oxygen:       O₂ produced (kg)
            volatiles:    Volatiles collected (kg) disjoint from stages
            error_pct:    Discrepancy as % of input; ``None`` when output
                          exists on a zero input basis
            error_category: Named category for non-percentable breaches.
                            Empty zero-in/zero-out systems stay closed at 0.0.
        """
        mass_in = self.input_mass_kg + self.additive_mass_kg
        melt_remaining = melt.total_mass_kg
        train_totals = train.total_by_species()
        condensed = sum(
            kg for species, kg in train_totals.items()
            if species != 'O2')
        # Volatiles are disjoint from stage collected_kg: only count species
        # not already present in stage totals (avoids double-book).
        volatiles = 0.0
        for species, kg in train.volatiles_collected_kg.items():
            if species in train_totals and float(train_totals.get(species, 0.0)) != 0.0:
                continue
            volatiles += float(kg)
        additive_inventory = sum((additive_inventory_kg or {}).values())
        stage0_products = 0.0
        drain_tap = 0.0
        residual = 0.0
        terminal_slag = 0.0
        cation_sulfate_feed = 0.0
        inert_melt = 0.0
        stage0_delta = 0.0
        if inventory is not None:
            stage0_products = sum(inventory.stage0_products_kg.values())
            drain_tap = sum(inventory.drain_tap_kg.values())
            residual = inventory.residual_mass_kg()
            terminal_slag = sum(inventory.terminal_slag_components_kg.values())
            cation_sulfate_feed = sum(
                (getattr(inventory, 'cation_sulfate_feed_kg', {}) or {}).values()
            )
            inert_melt = sum(
                (getattr(inventory, 'inert_melt_components_kg', {}) or {}).values()
            )
            stage0_delta = float(
                getattr(inventory, 'stage0_mass_balance_delta_kg', 0.0) or 0.0
            )

        wall = max(0.0, float(wall_deposit_kg or 0.0))

        mass_out = (
            melt_remaining + condensed + oxygen_kg + volatiles
            + stage0_products + drain_tap + residual + terminal_slag
            + additive_inventory
            + cation_sulfate_feed + inert_melt + wall
        )

        error_pct = 0.0
        error_category = ""
        if mass_in > 0:
            error_pct = abs(mass_in - mass_out) / mass_in * 100.0
        elif mass_out > 0:
            error_pct = None
            error_category = ZERO_INPUT_BASIS_BREACH

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
            'cation_sulfate_feed': cation_sulfate_feed,
            'inert_melt': inert_melt,
            'wall_deposit': wall,
            'stage0_mass_balance_delta': stage0_delta,
            'additive_inventory': additive_inventory,
            'error_pct': error_pct,
            'error_category': error_category,
        }

    def product_summary(self, train: CondensationTrain,
                         oxygen_kg: float) -> Dict[str, float]:
        """
        Summarise products by species across all stages.

        Stage ``collected_kg`` and ``volatiles_collected_kg`` are kept
        disjoint: a species already present in stage totals is not added
        again from the volatiles map (Ferry V / V1-S13 double-book fix).
        """
        products = {
            species: kg for species, kg in train.total_by_species().items()
            if species != 'O2'
        }
        products['O2'] = oxygen_kg
        for species, kg in train.volatiles_collected_kg.items():
            if species in products and float(products.get(species, 0.0)) != 0.0:
                continue
            products[species] = products.get(species, 0.0) + kg
        return products

    def stage_purity(self, train: CondensationTrain) -> Dict[int, Dict[str, float]]:
        """
        Calculate purity of each condensation stage's product.

        Returns dict of stage_number → {species: purity_pct}.
        """
        return query_stage_purity(train)
