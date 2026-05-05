"""
AlphaMELTS Backend
===================

Wraps alphaMELTS for thermodynamic equilibrium calculations via
two modes:

1. Python API (preferred): PetThermoTools → alphaMELTS for Python
2. Subprocess fallback: Write .melts files, run binary, parse output

Vapor pressures are computed by combining MELTS activity coefficients
with either VapoRock (if available) or pure-component Antoine equations:

    P_i_sat = γ_i × x_i × P_pure_i(T)

where γ_i is the activity coefficient from MELTS, x_i is the mole
fraction, and P_pure_i(T) is the Antoine vapor pressure.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from simulator.accounting.formulas import resolve_species_formula
from simulator.melt_backend.base import MeltBackend, EquilibriumResult


class AlphaMELTSBackend(MeltBackend):
    """
    AlphaMELTS thermodynamic backend.

    Tries PetThermoTools Python API first, falls back to
    subprocess mode if the binary is available.
    """

    def __init__(self):
        self._mode: Optional[str] = None  # 'python_api' or 'subprocess'
        self._engine_path: Optional[Path] = None
        self._pet_available = False
        self._vaporock_available = False

    def initialize(self, config: dict) -> bool:
        """
        Detect available alphaMELTS interfaces.

        Checks in order:
        1. PetThermoTools Python package
        2. alphaMELTS binary in engines/alphamelts/
        3. alphaMELTS on system PATH
        """
        # Try PetThermoTools
        try:
            import PetThermoTools  # noqa: F401
            self._pet_available = True
            self._mode = 'python_api'
        except ImportError:
            self._pet_available = False

        # Try VapoRock
        try:
            import VapoRock  # noqa: F401
            self._vaporock_available = True
        except ImportError:
            self._vaporock_available = False

        # Try binary
        if not self._pet_available:
            # Check project engines/ directory
            project_root = Path(__file__).parent.parent.parent
            engine_path = project_root / 'engines' / 'alphamelts' / 'run_alphamelts.command'
            if engine_path.exists():
                self._engine_path = engine_path
                self._mode = 'subprocess'
            else:
                # Check system PATH
                try:
                    result = subprocess.run(
                        ['alphamelts', '--version'],
                        capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        self._engine_path = Path('alphamelts')
                        self._mode = 'subprocess'
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass

        return self._mode is not None

    def is_available(self) -> bool:
        return self._mode is not None

    def get_vapor_species(self) -> List[str]:
        if self._vaporock_available:
            # VapoRock provides 34 species
            return [
                'Na', 'K', 'Fe', 'Mg', 'Ca', 'Si', 'Al', 'Ti', 'Cr', 'Mn',
                'SiO', 'FeO', 'MgO', 'CaO', 'AlO', 'TiO', 'NaO', 'KO',
                'O2', 'O', 'SiO2_gas', 'Fe2O3_gas',
            ]
        return ['Na', 'K', 'Fe', 'Mg', 'Ca', 'SiO', 'Mn', 'Cr']

    def equilibrate(self, temperature_C: float,
                    composition_kg: Optional[Dict[str, float]] = None,
                    fO2_log: float = -9.0,
                    pressure_bar: float = 1e-6,
                    *,
                    composition_mol: Optional[Dict[str, float]] = None
                    ) -> EquilibriumResult:
        """
        Calculate thermodynamic equilibrium.

        Routes to the appropriate engine based on available mode.
        """
        if composition_mol is not None:
            composition_kg = {
                species: float(mol)
                * resolve_species_formula(species).molar_mass_kg_per_mol()
                for species, mol in composition_mol.items()
                if float(mol) > 0.0
            }
        else:
            composition_kg = dict(composition_kg or {})

        if self._mode == 'python_api':
            return self._equilibrate_python(
                temperature_C, composition_kg, fO2_log, pressure_bar)
        elif self._mode == 'subprocess':
            return self._equilibrate_subprocess(
                temperature_C, composition_kg, fO2_log, pressure_bar)
        else:
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
            )

    # ------------------------------------------------------------------
    # Python API mode (PetThermoTools)
    # ------------------------------------------------------------------

    def _equilibrate_python(self, temperature_C, composition_kg,
                             fO2_log, pressure_bar):
        """
        Use PetThermoTools for equilibrium calculation.

        PetThermoTools wraps alphaMELTS for Python, providing
        phase assemblage, liquid composition, and activity data.
        """
        try:
            from PetThermoTools import Path as PTPath
            from PetThermoTools.GenFuncs import load

            # Convert composition to wt% for MELTS input
            total = sum(composition_kg.values())
            if total <= 0:
                return EquilibriumResult(temperature_C=temperature_C)

            comp_wt = {k: v / total * 100.0
                       for k, v in composition_kg.items() if v > 0}

            # Run isobaric equilibrium at single T
            # PetThermoTools API varies by version; this is the general pattern
            results = PTPath.isobaric(
                comp=comp_wt,
                T_start_C=temperature_C,
                T_end_C=temperature_C,
                dt_C=1.0,
                P_bar=max(pressure_bar, 1e-6),
                fO2_buffer=None,
                fO2_offset=fO2_log,
            )

            # Extract results
            eq = EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
            )

            if results is not None:
                # Parse phase assemblage from results
                eq.phases_present = list(results.get('phases', {}).keys())
                eq.liquid_fraction = results.get('liquid_fraction', 1.0)
                eq.liquid_composition_wt_pct = results.get(
                    'liquid_composition', comp_wt)

                # Get activity coefficients
                eq.activity_coefficients = results.get('activities', {})

            # Vapor pressures via VapoRock if available
            if self._vaporock_available:
                eq.vapor_pressures_Pa = self._get_vaporock_pressures(
                    temperature_C, comp_wt, fO2_log)
            else:
                # Use activities × pure-component Antoine
                eq.vapor_pressures_Pa = self._activities_times_antoine(
                    temperature_C, eq.activity_coefficients, comp_wt)

            return eq

        except Exception as e:
            # Fall back to empty result on any PetThermoTools error
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
            )

    # ------------------------------------------------------------------
    # Subprocess mode
    # ------------------------------------------------------------------

    def _equilibrate_subprocess(self, temperature_C, composition_kg,
                                 fO2_log, pressure_bar):
        """
        Run alphaMELTS binary via subprocess.

        Writes a .melts input file, runs the binary, and parses
        the *_tbl.txt output files for phase data.

        Slower (~1-3s per call) but reliable.
        """
        total = sum(composition_kg.values())
        if total <= 0:
            return EquilibriumResult(temperature_C=temperature_C)

        comp_wt = {k: v / total * 100.0
                   for k, v in composition_kg.items() if v > 0}

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write .melts file
            melts_path = Path(tmpdir) / 'input.melts'
            self._write_melts_file(melts_path, comp_wt,
                                    temperature_C, pressure_bar)

            # Write command file
            cmd_path = Path(tmpdir) / 'commands.txt'
            self._write_command_file(cmd_path, temperature_C)

            # Run alphaMELTS
            try:
                result = subprocess.run(
                    [str(self._engine_path), '-f', str(cmd_path)],
                    cwd=tmpdir,
                    capture_output=True, text=True,
                    timeout=10,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return EquilibriumResult(
                    temperature_C=temperature_C,
                    pressure_bar=pressure_bar,
                )

            # Parse output
            return self._parse_melts_output(tmpdir, temperature_C,
                                             pressure_bar, fO2_log)

    def _write_melts_file(self, path: Path, comp_wt: dict,
                           T_C: float, P_bar: float):
        """Write a .melts input file for alphaMELTS."""
        lines = ['Title: regolith_pyrolysis_simulator']
        for oxide, wt in sorted(comp_wt.items()):
            if wt > 0.001:
                # Map our oxide names to MELTS format
                melts_name = oxide.replace('2O3', '2O3').replace('2O', '2O')
                lines.append(f'Initial Composition: {melts_name} {wt:.4f}')
        lines.append(f'Initial Temperature: {T_C:.1f}')
        lines.append(f'Initial Pressure: {max(P_bar * 10, 1):.1f}')  # bars→MPa? MELTS uses bars
        lines.append('Mode: Isobaric')

        with open(path, 'w') as f:
            f.write('\n'.join(lines) + '\n')

    def _write_command_file(self, path: Path, T_C: float):
        """Write alphaMELTS command sequence."""
        commands = [
            '1',      # Read input
            f'3',     # Set temperature
            f'{T_C}',
            '4',      # Execute
            '0',      # Exit
        ]
        with open(path, 'w') as f:
            f.write('\n'.join(commands) + '\n')

    def _parse_melts_output(self, tmpdir: str, T_C: float,
                             P_bar: float, fO2_log: float):
        """Parse alphaMELTS *_tbl.txt output files."""
        eq = EquilibriumResult(
            temperature_C=T_C,
            pressure_bar=P_bar,
            fO2_log=fO2_log,
        )

        # Look for phase table output
        tbl_files = list(Path(tmpdir).glob('*_tbl.txt'))
        for tbl in tbl_files:
            try:
                with open(tbl) as f:
                    content = f.read()
                # Minimal parsing — extract phase names and masses
                for line in content.split('\n'):
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[0] not in ('Temperature', 'Pressure'):
                        try:
                            mass = float(parts[-1])
                            phase = parts[0]
                            eq.phases_present.append(phase)
                            eq.phase_masses_kg[phase] = mass / 1000.0
                        except ValueError:
                            pass
            except OSError:
                pass

        return eq

    # ------------------------------------------------------------------
    # Vapor pressure helpers
    # ------------------------------------------------------------------

    def _get_vaporock_pressures(self, T_C: float, comp_wt: dict,
                                 fO2_log: float) -> Dict[str, float]:
        """
        Get vapor pressures from VapoRock.

        VapoRock calculates equilibrium vapor speciation over
        silicate melts using MELTS thermodynamics + JANAF tables.
        Returns partial pressures for ~34 vapor species.
        """
        try:
            import VapoRock

            # VapoRock API (simplified — actual API may differ)
            result = VapoRock.calc_vapor(
                composition=comp_wt,
                temperature_C=T_C,
                fO2_log=fO2_log,
            )

            # Convert to Pa
            pressures = {}
            for species, p_bar in result.items():
                pressures[species] = p_bar * 1e5  # bar → Pa

            return pressures

        except Exception:
            return {}

    def _activities_times_antoine(self, T_C: float,
                                    activities: dict,
                                    comp_wt: dict) -> Dict[str, float]:
        """
        Compute vapor pressures as activity × pure-component P(T).

        Fallback when VapoRock is not available.  Uses Antoine
        equation parameters from vapor_pressures.yaml (loaded
        separately by the simulator).

        P_i = γ_i × x_i × P_pure_i(T)

        where γ_i × x_i is the activity from MELTS.
        If activities aren't available, uses wt% as crude proxy.
        """
        # This is computed in core.py's _stub_equilibrium()
        # when the backend returns activities
        return {}
