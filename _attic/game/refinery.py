"""
Refinery Manager — Multi-Line Game State
==========================================

Manages 10-15 furnace lines running simultaneously, each
operating as an independent PyrolysisSimulator instance.
Lines share a common inventory of additives and products.

The game ticks all lines forward by 1 hour each step.
Lines run autonomously until they reach a decision point,
at which time the operator is alerted.
"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional

from simulator.core import (
    PyrolysisSimulator, CampaignPhase, DecisionPoint, HourSnapshot,
)
from simulator.melt_backend.base import StubBackend


class SharedInventory:
    """
    Shared resource pool across all furnace lines.

    Tracks additives (Na, K, Mg, Ca, C) and products
    (metals, O₂, glass) with thread-safe access.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.additives: Dict[str, float] = {
            'Na': 0.0,    # kg
            'K': 0.0,
            'Mg': 0.0,
            'Ca': 0.0,
            'C': 0.0,
        }
        self.products: Dict[str, float] = {}
        self.oxygen_kg: float = 0.0
        self.energy_consumed_kWh: float = 0.0

    def withdraw(self, species: str, amount_kg: float) -> float:
        """
        Withdraw additive from inventory.
        Returns the amount actually withdrawn (may be less if insufficient).
        """
        with self._lock:
            available = self.additives.get(species, 0.0)
            taken = min(amount_kg, available)
            self.additives[species] = available - taken
            return taken

    def deposit(self, species: str, amount_kg: float):
        """Add product or recovered additive to inventory."""
        with self._lock:
            if species in self.additives:
                self.additives[species] = (
                    self.additives.get(species, 0.0) + amount_kg)
            else:
                self.products[species] = (
                    self.products.get(species, 0.0) + amount_kg)

    def deposit_oxygen(self, amount_kg: float):
        with self._lock:
            self.oxygen_kg += amount_kg

    def add_energy(self, kWh: float):
        with self._lock:
            self.energy_consumed_kWh += kWh

    def snapshot(self) -> dict:
        """Thread-safe copy of current inventory."""
        with self._lock:
            return {
                'Na': self.additives.get('Na', 0.0),
                'K': self.additives.get('K', 0.0),
                'Mg': self.additives.get('Mg', 0.0),
                'O2': self.oxygen_kg,
                'energy_kWh': self.energy_consumed_kWh,
                'products': dict(self.products),
            }


class RefineryManager:
    """
    Manages multiple furnace lines for the operator game.

    Each line is an independent PyrolysisSimulator.
    The manager steps all lines forward together and
    tracks which lines need operator decisions.
    """

    def __init__(self, setpoints: dict, feedstocks: dict,
                 vapor_pressures: dict, num_lines: int = 15):
        self.setpoints = setpoints
        self.feedstocks = feedstocks
        self.vapor_pressures = vapor_pressures
        self.num_lines = num_lines

        self.lines: Dict[str, PyrolysisSimulator] = {}
        self.inventory = SharedInventory()
        self.game_hour: int = 0

    def add_line(self, line_id: str, feedstock_key: str,
                 mass_kg: float = 1000.0):
        """
        Add a furnace line with the given feedstock.

        Creates a new PyrolysisSimulator instance with a stub
        backend and loads the batch.
        """
        backend = StubBackend()
        backend.initialize({})

        sim = PyrolysisSimulator(
            backend, self.setpoints, self.feedstocks, self.vapor_pressures)
        sim.load_batch(feedstock_key, mass_kg)
        sim.start_campaign(CampaignPhase.C0)

        self.lines[line_id] = sim

    def step_all(self) -> Dict[str, dict]:
        """
        Advance all active lines by 1 hour.

        Returns dict of line_id → snapshot summary for UI updates.
        """
        self.game_hour += 1
        results = {}

        for line_id, sim in self.lines.items():
            if sim.is_complete() or sim.paused_for_decision:
                # Return current state without advancing
                results[line_id] = self._line_summary(line_id, sim)
                continue

            snapshot = sim.step()

            # Update shared inventory with any products
            train_totals = sim.train.total_by_species()
            # Deposit O₂
            self.inventory.deposit_oxygen(
                snapshot.oxygen_produced_kg - self.inventory.oxygen_kg)

            results[line_id] = self._line_summary(line_id, sim)

        return results

    def _line_summary(self, line_id: str, sim: PyrolysisSimulator) -> dict:
        """Build a compact summary dict for the UI."""
        status = 'idle'
        if sim.paused_for_decision:
            status = 'decision'
        elif sim.is_complete():
            status = 'complete'
        elif sim.melt.campaign != CampaignPhase.IDLE:
            status = 'running'

        return {
            'line_id': line_id,
            'status': status,
            'temperature_C': sim.melt.temperature_C,
            'campaign': sim.melt.campaign.name,
            'hour': sim.melt.hour,
            'melt_mass_kg': sim.melt.total_mass_kg,
            'feedstock': sim.record.feedstock_label,
        }

    def get_decisions_pending(self) -> List[Dict]:
        """Return all lines that need operator decisions."""
        pending = []
        for line_id, sim in self.lines.items():
            if sim.paused_for_decision and sim.pending_decision:
                d = sim.pending_decision
                pending.append({
                    'line_id': line_id,
                    'type': d.decision_type.name,
                    'options': d.options,
                    'recommendation': d.recommendation,
                    'context': d.context,
                })
        return pending

    def apply_decision(self, line_id: str, choice: str):
        """Apply an operator decision to a specific line."""
        sim = self.lines.get(line_id)
        if sim and sim.pending_decision:
            sim.apply_decision(sim.pending_decision.decision_type, choice)

    def harvest_products(self, line_id: str) -> Dict[str, float]:
        """
        Harvest products from a completed or stage-ended line.
        Deposits into shared inventory.
        """
        sim = self.lines.get(line_id)
        if sim is None:
            return {}

        products = dict(sim.train.total_by_species())

        # Deposit into shared inventory
        for species, kg in products.items():
            self.inventory.deposit(species, kg)

        self.inventory.deposit_oxygen(sim.oxygen_cumulative_kg)
        self.inventory.add_energy(sim.energy_cumulative_kWh)

        return products
