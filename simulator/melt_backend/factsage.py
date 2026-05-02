"""
FactSAGE / ChemApp Backend (Stub)
===================================

Stub implementation for FactSAGE thermodynamic calculations
via the ChemApp Python interface.

FactSAGE requires a commercial license.  This stub checks
for ChemApp availability and returns empty results.
Full implementation deferred until a FactSAGE license is available.
"""

from __future__ import annotations

from typing import Dict, List

from simulator.melt_backend.base import MeltBackend, EquilibriumResult


class FactSAGEBackend(MeltBackend):
    """
    FactSAGE/ChemApp thermodynamic backend (stub).

    Will be implemented when a FactSAGE license is available.
    For now, checks if ChemApp is installed and reports status.
    """

    def __init__(self):
        self._available = False

    def initialize(self, config: dict) -> bool:
        try:
            import ChemApp  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False
        return self._available

    def is_available(self) -> bool:
        return self._available

    def get_vapor_species(self) -> List[str]:
        return ['Na', 'K', 'Fe', 'Mg', 'Ca', 'SiO']

    def equilibrate(self, temperature_C: float,
                    composition_kg: Dict[str, float],
                    fO2_log: float = -9.0,
                    pressure_bar: float = 1e-6) -> EquilibriumResult:
        # Stub — returns empty result
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
        )
