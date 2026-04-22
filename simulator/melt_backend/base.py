"""
Melt Backend — Abstract Interface & Data Classes
=================================================

Defines the abstract MeltBackend interface and EquilibriumResult
that all thermodynamic backends (AlphaMELTS, FactSAGE, stub) must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class EquilibriumResult:
    """
    Result of a thermodynamic equilibrium calculation.

    Returned by MeltBackend.equilibrate() with phase assemblage,
    activity coefficients, and vapor pressures at the given
    temperature and composition.
    """
    temperature_C: float = 0.0
    pressure_bar: float = 0.0

    # Phase assemblage
    phases_present: List[str] = field(default_factory=list)
    phase_masses_kg: Dict[str, float] = field(default_factory=dict)
    phase_compositions: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # Liquid state
    liquid_fraction: float = 1.0
    liquid_composition_wt_pct: Dict[str, float] = field(default_factory=dict)
    liquid_viscosity_Pa_s: float = 5.0  # Typical basaltic melt

    # Vapor pressures (Pa) for each volatile species
    vapor_pressures_Pa: Dict[str, float] = field(default_factory=dict)

    # Activity coefficients in the melt
    activity_coefficients: Dict[str, float] = field(default_factory=dict)

    # Oxygen fugacity
    fO2_log: float = -9.0  # log10(fO2 / 1 bar)


class MeltBackend(ABC):
    """
    Abstract interface for thermodynamic melt calculations.

    Implementations wrap different thermodynamic engines:
    - AlphaMELTS (via PetThermoTools or subprocess)
    - FactSAGE (via ChemApp)
    - StubBackend (Antoine vapor pressures, no phase equilibrium)
    """

    @abstractmethod
    def initialize(self, config: dict) -> bool:
        """
        Initialize the backend with configuration.

        Returns True if the backend is ready to use.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is installed and functional."""

    @abstractmethod
    def equilibrate(self, temperature_C: float,
                    composition_kg: Dict[str, float],
                    fO2_log: float = -9.0,
                    pressure_bar: float = 1e-6) -> EquilibriumResult:
        """
        Calculate thermodynamic equilibrium at given conditions.

        Args:
            temperature_C:   Melt temperature (°C)
            composition_kg:  Oxide masses in the melt (kg)
            fO2_log:         log10(oxygen fugacity / 1 bar)
            pressure_bar:    Total pressure (bar)

        Returns:
            EquilibriumResult with phases, activities, vapor pressures
        """

    @abstractmethod
    def get_vapor_species(self) -> List[str]:
        """Return list of vapor species this backend can calculate."""


class StubBackend(MeltBackend):
    """
    Minimal stub backend for development and testing.

    Returns empty equilibrium results.  The simulator's
    _stub_equilibrium() method handles Antoine-equation
    vapor pressures independently of this class.
    """

    def initialize(self, config: dict) -> bool:
        return True

    def is_available(self) -> bool:
        return False  # Signals core.py to use its own stub logic

    def equilibrate(self, temperature_C, composition_kg,
                    fO2_log=-9.0, pressure_bar=1e-6):
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
        )

    def get_vapor_species(self):
        return ['Na', 'K', 'Fe', 'Mg', 'Ca', 'SiO']
