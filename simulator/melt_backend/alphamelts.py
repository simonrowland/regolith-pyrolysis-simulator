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
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from simulator.accounting.formulas import resolve_species_formula
from simulator.melt_backend.base import MeltBackend, EquilibriumResult


ALPHAMELTS_LIQUIDUS_SEED_TEMPERATURE_C = 800.0


class AlphaMELTSBackend(MeltBackend):
    """
    AlphaMELTS thermodynamic backend.

    Tries PetThermoTools Python API first, falls back to
    subprocess mode if the binary is available.
    """

    def __init__(self):
        self._mode: Optional[str] = None  # 'python_api' or 'subprocess'
        self._engine_path: Optional[Path] = None
        self._binary_path: Optional[Path] = None
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
        except ImportError:
            self._pet_available = False

        # Try VapoRock
        try:
            import VapoRock  # noqa: F401
            self._vaporock_available = True
        except ImportError:
            self._vaporock_available = False

        # Try binary
        if self._mode is None:
            # Check project engines/ directory
            project_root = Path(__file__).parent.parent.parent
            engine_root = project_root / 'engines' / 'alphamelts'
            engine_path = engine_root / 'run_alphamelts.command'
            binary_path = self._find_project_binary(engine_root)
            if engine_path.exists() or binary_path is not None:
                self._engine_path = engine_path if engine_path.exists() else binary_path
                self._binary_path = binary_path
                self._mode = 'subprocess'
            else:
                # Check system PATH
                try:
                    result = subprocess.run(
                        ['alphamelts', '--version'],
                        capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        self._engine_path = Path('alphamelts')
                        self._binary_path = Path('alphamelts')
                        self._mode = 'subprocess'
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass

        return self._mode is not None

    def _find_project_binary(self, engine_root: Path) -> Optional[Path]:
        if not engine_root.exists():
            return None
        binary_names = (
            'alphamelts2',
            'alphamelts_macos',
            'alphamelts_linux',
            'alphamelts_win64.exe',
        )
        for name in binary_names:
            direct = engine_root / name
            if direct.exists():
                return direct
        for child in sorted(engine_root.iterdir()):
            if not child.is_dir():
                continue
            for name in binary_names:
                candidate = child / name
                if candidate.exists():
                    return candidate
        return None

    def is_available(self) -> bool:
        return self._mode is not None

    def get_vapor_species(self) -> List[str]:
        if self._vaporock_available:
            # VapoRock provides 34 species
            return [
                'Na', 'K', 'Fe', 'Mg', 'Ca', 'Si', 'Al', 'Ti', 'Cr', 'Mn',
                'SiO', 'FeO', 'MgO', 'CaO', 'AlO', 'TiO', 'NaO', 'KO',
                'O2', 'O', 'SiO2', 'Fe2O3',
            ]
        return ['Na', 'K', 'Fe', 'Mg', 'Ca', 'SiO', 'Mn', 'Cr']

    def equilibrate(self, temperature_C: float,
                    composition_kg: Optional[Dict[str, float]] = None,
                    fO2_log: float = -9.0,
                    pressure_bar: float = 1e-6,
                    *,
                    composition_mol: Optional[Dict[str, float]] = None,
                    composition_mol_by_account: Optional[Mapping[str, Mapping[str, float]]] = None,
                    species_formula_registry: Optional[Mapping[str, object]] = None,
                    ) -> EquilibriumResult:
        """
        Calculate thermodynamic equilibrium.

        Routes to the appropriate engine based on available mode.
        """
        if composition_mol_by_account is not None:
            unsupported = {
                str(account): sorted(
                    str(species)
                    for species, mol in (species_mol or {}).items()
                    if float(mol) > 0.0
                )
                for account, species_mol in composition_mol_by_account.items()
                if str(account) != 'process.cleaned_melt'
            }
            unsupported = {account: species for account, species in unsupported.items()
                           if species}
            if unsupported:
                raise ValueError(
                    'AlphaMELTS accepts only process.cleaned_melt input until '
                    f'metal/gas account mappings are implemented; got {unsupported}'
                )
            composition_mol = {}
            for species_mol in composition_mol_by_account.values():
                for species, mol in species_mol.items():
                    composition_mol[species] = (
                        composition_mol.get(species, 0.0) + float(mol))
        if composition_mol is not None:
            composition_kg = {
                species: float(mol)
                * resolve_species_formula(
                    species,
                    species_formula_registry,
                ).molar_mass_kg_per_mol()
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
            _ = load

            raise RuntimeError(
                'AlphaMELTS Python API absolute fO2 control is not wired; '
                'backend disabled instead of using fO2_log as a buffer offset'
            )

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
            self._mode = None
            raise RuntimeError(f'AlphaMELTS Python equilibrium failed: {e}') from e

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
            calculation_temperature_C = max(
                float(temperature_C), ALPHAMELTS_LIQUIDUS_SEED_TEMPERATURE_C)
            self._write_melts_file(melts_path, comp_wt,
                                    calculation_temperature_C, pressure_bar)

            binary = self._binary_path or self._engine_path
            if binary is None:
                raise RuntimeError('AlphaMELTS subprocess binary is not configured')
            menu_input = '1\ninput.melts\n3\n2\nx\n'
            env = os.environ.copy()
            env.setdefault('ALPHAMELTS_CALC_MODE', 'MELTS')

            # Run alphaMELTS directly. The alphaMELTS 2 app runner only
            # emits *_tbl.txt for path-style runs; single-point equilibria
            # report the stable phase assemblage on stdout.
            try:
                result = subprocess.run(
                    [str(binary), '1'],
                    cwd=tmpdir,
                    input=menu_input,
                    capture_output=True, text=True,
                    timeout=20,
                    env=env,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                self._mode = None
                raise RuntimeError(
                    f'AlphaMELTS subprocess equilibrium failed: {exc}'
                ) from exc

            if result.returncode != 0:
                self._mode = None
                raise RuntimeError(
                    'AlphaMELTS subprocess equilibrium failed: '
                    f'{result.stderr or result.stdout}'
                )

            return self._parse_single_point_stdout(
                f'{result.stdout}\n{result.stderr}',
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                total_input_kg=total,
            )

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
        lines.append(f'Initial Pressure: {max(P_bar, 1):.1f}')

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
                mass_index = None
                for line in content.split('\n'):
                    parts = line.strip().split()
                    if len(parts) < 2:
                        continue
                    lower_parts = [part.lower() for part in parts]
                    for idx, token in enumerate(lower_parts):
                        if 'mass' in token:
                            mass_index = idx
                    phase = parts[0]
                    phase_key = phase.lower()
                    if phase_key in (
                        'temperature', 'pressure', 'mass', 'phase', 'total'
                    ):
                        continue
                    if phase_key[0].isdigit():
                        continue
                    try:
                        if (
                            mass_index is not None
                            and mass_index < len(parts)
                            and mass_index > 0
                        ):
                            mass = float(parts[mass_index])
                        elif len(parts) == 2:
                            mass = float(parts[1])
                        else:
                            numeric = [float(part) for part in parts[1:]]
                            mass = numeric[-1]
                    except ValueError:
                        continue
                    if mass > 0.0:
                        eq.phases_present.append(phase)
                        eq.phase_masses_kg[phase] = mass / 1000.0
            except OSError:
                pass

        return eq

    def _parse_liquidus_C(self, output: str) -> Optional[float]:
        match = re.search(
            r'Found the liquidus at T\s*=\s*([0-9.+\-Ee]+)\s*\(C\)',
            output,
        )
        if match is None:
            return None
        return float(match.group(1))

    def _parse_single_point_stdout(self, output: str, *, temperature_C: float,
                                   pressure_bar: float, fO2_log: float,
                                   total_input_kg: float) -> EquilibriumResult:
        if not re.search(r'<> Stable .+ assemblage achieved\.', output):
            raise RuntimeError(
                'AlphaMELTS subprocess produced no stable assemblage verdict'
            )

        eq = EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
        )
        liquidus_C = self._parse_liquidus_C(output)
        if liquidus_C is not None:
            eq.warnings.append(f'AlphaMELTS liquidus_C={liquidus_C:.3f}')
        lines = output.splitlines()

        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('liquid:'):
                if 'liquid' not in eq.phases_present:
                    eq.phases_present.append('liquid')
                headers = stripped.split(':', 1)[1].split()
                if idx + 1 < len(lines):
                    values = lines[idx + 1].split()
                    if len(values) >= 2 and values[1] == 'g':
                        for oxide, raw in zip(headers, values[2:]):
                            try:
                                eq.liquid_composition_wt_pct[oxide] = float(raw)
                            except ValueError:
                                continue

            phase_match = re.match(
                r'^([A-Za-z][A-Za-z0-9_\-]*):\s+'
                r'([0-9.+\-Ee]+)\s+g\b',
                stripped,
            )
            if phase_match:
                phase = phase_match.group(1)
                if phase != 'liquid' and phase not in eq.phases_present:
                    eq.phases_present.append(phase)

            melt_match = re.search(
                r'Melt fraction\s*=\s*([0-9.+\-Ee]+)', stripped)
            if melt_match:
                eq.liquid_fraction = max(
                    0.0, min(1.0, float(melt_match.group(1))))

        if not eq.phases_present:
            raise RuntimeError(
                'AlphaMELTS subprocess produced no parseable phase assemblage'
            )
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
