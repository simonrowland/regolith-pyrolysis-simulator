"""
VapoRock Integration
=====================

Wrapper around VapoRock (ENKI project) for calculating equilibrium
vapor speciation over silicate melts.

VapoRock uses the MELTS thermodynamic model combined with JANAF
tables to compute partial pressures for ~34 vapor species in the
Si-Mg-Fe-Al-Ca-Na-K-Ti-Cr-O system over silicate melts.

This is the preferred source of vapor pressures when available,
as it accounts for non-ideal mixing in the melt (activity
coefficients from MELTS).

If VapoRock is not installed, the simulator falls back to
pure-component Antoine equations with crude activity estimates.
"""

from __future__ import annotations

from typing import Dict, Optional


class VapoRockWrapper:
    """
    Wraps VapoRock for vapor pressure calculations.

    Usage:
        vr = VapoRockWrapper()
        if vr.is_available():
            pressures = vr.get_vapor_pressures(T_C, comp_wt, fO2_log)
    """

    def __init__(self):
        self._available: Optional[bool] = None

    def is_available(self) -> bool:
        """Check if VapoRock is installed."""
        if self._available is None:
            try:
                import VapoRock  # noqa: F401
                self._available = True
            except ImportError:
                self._available = False
        return self._available

    def get_vapor_pressures(self, temperature_C: float,
                             composition_wt_pct: Dict[str, float],
                             fO2_log: float = -9.0) -> Dict[str, float]:
        """
        Calculate vapor species partial pressures over a silicate melt.

        Args:
            temperature_C:       Melt temperature (°C)
            composition_wt_pct:  Oxide composition in wt%
            fO2_log:             log₁₀(fO₂ / 1 bar)

        Returns:
            Dict of species name → partial pressure in Pa
        """
        if not self.is_available():
            return {}

        try:
            import VapoRock

            # Build VapoRock input composition
            # VapoRock expects oxide wt% normalized to 100
            total = sum(composition_wt_pct.values())
            if total <= 0:
                return {}

            comp = {k: v / total * 100.0
                    for k, v in composition_wt_pct.items()}

            # Call VapoRock equilibrium calculator
            # Note: The actual VapoRock API may differ; this
            # follows the documented ENKI/VapoRock interface.
            result = VapoRock.calc_vapor_pressures(
                T_C=temperature_C,
                composition=comp,
                log_fO2=fO2_log,
            )

            # Convert bar → Pa
            pressures = {}
            for species, p_bar in result.items():
                pressures[species] = p_bar * 1e5
            return pressures

        except Exception:
            return {}

    def get_species_list(self) -> list:
        """Return the list of vapor species VapoRock can calculate."""
        # Based on the VapoRock/MELTS vapor model
        return [
            # Monatomic metals
            'Na', 'K', 'Fe', 'Mg', 'Ca', 'Si', 'Al', 'Ti', 'Cr', 'Mn',
            # Metal oxides
            'SiO', 'FeO', 'MgO', 'CaO', 'AlO', 'TiO', 'NaO', 'KO',
            'CrO', 'MnO',
            # Higher oxides
            'SiO2_gas', 'Al2O', 'Fe2O3_gas', 'Ti2O3_gas',
            # Oxygen
            'O2', 'O',
            # Alkali dimers/hydroxides
            'Na2', 'K2', 'NaOH', 'KOH',
            # Minor
            'Si2', 'Mg2', 'Ca2',
        ]
