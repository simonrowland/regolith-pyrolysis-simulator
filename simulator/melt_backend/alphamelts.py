"""
AlphaMELTS Backend
==================

Wraps alphaMELTS for thermodynamic equilibrium calculations via:

1. Python API (preferred): petthermotools -> alphaMELTS for Python
2. Subprocess fallback: write .melts files, run binary, parse stdout/tables

PetThermoTools 0.4.5 schema verified from installed source:

* import package: ``petthermotools``; distribution: ``petthermotools``.
* compiled MELTS payload is the separate ``meltsdynamic.MELTSdynamic`` loader.
* single equilibrium entry point is ``equilibrate_MELTS(...)``; it returns
  ``(Results, Affinity)`` where ``Results`` contains ``Conditions``, phase
  composition tables, and ``<phase>_prop`` tables.
* ``fO2_offset`` is a delta from ``fO2_buffer``. The simulator's absolute
  ``fO2_log`` is not passed as an offset.
* MELTS/ThermoEngine chemical-potential output must be converted to
  thermodynamic activity as ``a_i = exp((mu_i - mu_i0) / RT)``. Activity is
  absent when the live path does not supply both ``mu`` and ``mu0``.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from simulator.accounting.formulas import resolve_species_formula
from simulator.melt_backend.base import MeltBackend, EquilibriumResult
from simulator.melt_backend.liquidus import (
    LiquidusSolidusResult,
    find_liquidus_solidus_by_fraction,
)


ALPHAMELTS_LIQUIDUS_SEED_TEMPERATURE_C = 800.0
MELTS_OXIDE_BASIS = (
    'SiO2', 'TiO2', 'Al2O3', 'FeO', 'Fe2O3', 'MgO', 'CaO',
    'Na2O', 'K2O', 'Cr2O3', 'MnO', 'P2O5', 'NiO', 'CoO',
)
MELTS_MAJOR_OXIDES = set(MELTS_OXIDE_BASIS)
MELTS_OXIDE_ALIASES = {
    oxide.lower(): oxide
    for oxide in MELTS_OXIDE_BASIS
}
MELTS_OXIDE_ALIASES.update({
    'feo_total': 'FeO_total',
    'feot': 'FeO_total',
    'feototal': 'FeO_total',
    'feo_tot': 'FeO_total',
})
FE_REDOX_BUFFERS = {'QFM', 'NNO', 'IW', 'HM'}
FE_REDOX_BUFFER_ALIASES = {'FMQ': 'QFM'}
FE3_TO_FEOT_FACTOR = 0.8998
PETTHERMOTOOLS_NON_PHASE_KEYS = {
    'All', 'Mass', 'Volume', 'rho', 'Conditions', 'Input', 'Affinity',
    'Activities', 'activities', 'activity_coefficients',
    'chemical_potentials', 'chem_potentials', 'mu', 'mu_oxides',
    'oxide_mu', 'standard_chemical_potentials', 'pure_chemical_potentials',
    'reference_chemical_potentials', 'mu0', 'mu0_oxides', 'oxide_mu0',
}
GAS_CONSTANT_J_PER_MOL_K = 8.31446261815324


def activity_from_chem_potential(mu: float, mu0: float, T_K: float) -> float:
    """Convert chemical potential to pure-endmember-referenced activity."""
    mu_val = float(mu)
    mu0_val = float(mu0)
    T_val = float(T_K)
    if not (
        math.isfinite(mu_val)
        and math.isfinite(mu0_val)
        and math.isfinite(T_val)
    ):
        raise ValueError('chemical potentials and temperature must be finite')
    if T_val <= 0.0:
        raise ValueError('temperature must be positive')
    return math.exp((mu_val - mu0_val) / (GAS_CONSTANT_J_PER_MOL_K * T_val))


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
        self._pet_module = None
        self._pet_melts = None
        self._pet_import_error: Optional[ImportError] = None
        self._pet_payload_preloaded = False
        self._engine_version: Optional[str] = None
        self._vaporock_available = False
        self._redox_buffer: Optional[str] = None
        self._fo2_offset: Optional[float] = None
        self._fe3fet_ratio: Optional[float] = None
        self._model = 'MELTSv1.0.2'
        self._timeout_s = 20.0
        self._last_normalization_warnings: List[str] = []
        self._vapor_pressure_table: Optional[dict] = None

    def initialize(self, config: dict) -> bool:
        """
        Detect available alphaMELTS interfaces.

        Checks in order:
        1. PetThermoTools Python package
        2. alphaMELTS binary in engines/alphamelts/
        3. alphaMELTS on system PATH
        """
        config = self._alphamelts_config(config)
        self._redox_buffer = self._normalize_redox_buffer(
            config.get('fO2_buffer', config.get('redox_buffer')))
        self._fo2_offset = self._optional_float(config.get('fO2_offset'))
        self._fe3fet_ratio = self._normalize_fe3fet_ratio(
            config.get('Fe3Fet_Liq', config.get('fe3fet_ratio')))
        self._model = str(config.get('model', self._model))
        self._timeout_s = float(config.get('timeout_s', self._timeout_s))
        require_petthermotools = bool(
            config.get('require_petthermotools')
            or str(config.get('mode', '')).lower() == 'python_api'
        )

        # Try PetThermoTools
        try:
            self._pet_module = self._import_petthermotools()
            self._engine_version = self.get_engine_version()
            self._preload_petthermotools_payload(self._pet_module)
            self._pet_available = True
            self._mode = 'python_api'
        except ImportError:
            self._pet_available = False
            self._pet_module = None
            self._pet_melts = None
            self._pet_payload_preloaded = False
            self._pet_import_error = ImportError(
                'PetThermoTools Python path unavailable: '
                'petthermotools and meltsdynamic must both import'
            )
            if require_petthermotools:
                raise self._pet_import_error

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

    def _alphamelts_config(self, config: dict) -> dict:
        if not isinstance(config, Mapping):
            return {}
        nested = config.get('alphamelts')
        if isinstance(nested, Mapping):
            merged = dict(config)
            merged.update(nested)
            return merged
        return dict(config)

    def _optional_float(self, value) -> Optional[float]:
        if value is None or value == '':
            return None
        return float(value)

    def _normalize_redox_buffer(self, value) -> Optional[str]:
        if value is None or value == '':
            return None
        buffer = str(value).strip().upper()
        buffer = FE_REDOX_BUFFER_ALIASES.get(buffer, buffer)
        if buffer not in FE_REDOX_BUFFERS:
            raise ValueError(f'unsupported AlphaMELTS redox buffer: {value}')
        return buffer

    def _normalize_fe3fet_ratio(self, value) -> Optional[float]:
        ratio = self._optional_float(value)
        if ratio is None:
            return None
        if not 0.0 <= ratio <= 1.0:
            raise ValueError('Fe3Fet ratio must be in [0, 1]')
        return ratio

    def _import_petthermotools(self):
        try:
            return importlib.import_module('petthermotools')
        except ImportError:
            return importlib.import_module('PetThermoTools')

    def _preload_petthermotools_payload(self, module) -> None:
        loader = getattr(module, 'MELTSdynamic', None)
        if loader is None:
            try:
                meltsdynamic = importlib.import_module('meltsdynamic')
                loader = getattr(meltsdynamic, 'MELTSdynamic', None)
            except ImportError as exc:
                raise ImportError(
                    'PetThermoTools compiled MELTS payload missing: '
                    'cannot import meltsdynamic.MELTSdynamic'
                ) from exc
        if loader is None:
            raise ImportError(
                'PetThermoTools compiled MELTS payload missing: '
                'MELTSdynamic loader not found'
            )
        self._pet_melts = loader(self._melts_model_code())
        self._pet_payload_preloaded = True

    def _melts_model_code(self) -> int:
        if self._model == 'pMELTS':
            return 2
        if self._model == 'MELTSv1.1.0':
            return 3
        if self._model == 'MELTSv1.2.0':
            return 4
        return 1

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

    def get_engine_version(self) -> str:
        if self._engine_version:
            return self._engine_version
        if self._pet_module is not None:
            version = getattr(self._pet_module, '__version__', None)
            if version:
                self._engine_version = f'petthermotools {version}'
                return self._engine_version
        for package_name in ('petthermotools', 'PetThermoTools'):
            try:
                version = importlib.metadata.version(package_name)
                self._engine_version = f'{package_name} {version}'
                return self._engine_version
            except importlib.metadata.PackageNotFoundError:
                continue
        if self._binary_path is not None or self._engine_path is not None:
            binary = self._binary_path or self._engine_path
            try:
                result = subprocess.run(
                    [str(binary), '--version'],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                text = (result.stdout or result.stderr).strip().splitlines()
                if result.returncode == 0 and text:
                    self._engine_version = text[0]
                    return self._engine_version
            except (OSError, subprocess.TimeoutExpired):
                pass
        return 'unavailable'

    def capabilities(self) -> Dict[str, object]:
        caps = super().capabilities()
        caps['engine_version'] = self.get_engine_version()
        return caps

    def ledger_account_policies(self) -> tuple[Any, ...]:
        return ()

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
            unsupported = self._unsupported_accounts(composition_mol_by_account)
            if unsupported:
                return self._domain_gate_result(
                    temperature_C,
                    pressure_bar,
                    fO2_log,
                    [
                        'unsupported ledger accounts present: '
                        + ', '.join(
                            f'{account}={species}'
                            for account, species in sorted(unsupported.items())
                        )
                    ],
                )
            composition_mol = {}
            for species, mol in composition_mol_by_account.get(
                'process.cleaned_melt', {}
            ).items():
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

        raw_comp_wt = self._composition_kg_to_wt_pct(composition_kg)
        if not raw_comp_wt:
            # No melt oxides supplied - the engine has nothing valid to act on.
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status='out_of_domain',
            )
        domain_rejection = self._domain_gate(
            raw_comp_wt,
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
        )
        if domain_rejection is not None:
            return domain_rejection
        comp_wt = self._normalize_composition_to_melts_basis(raw_comp_wt)
        warnings = list(self._last_normalization_warnings)

        if self._mode == 'python_api':
            return self._equilibrate_python(
                temperature_C, comp_wt, fO2_log, pressure_bar, warnings)
        elif self._mode == 'subprocess':
            return self._equilibrate_subprocess(
                temperature_C, comp_wt, fO2_log, pressure_bar, warnings)
        else:
            # No PetThermoTools, no binary -- the engine is not present.
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                warnings=warnings,
                status='unavailable',
            )

    def find_liquidus_solidus(self,
                              composition_kg: Optional[Dict[str, float]] = None,
                              fO2_log: float = -9.0,
                              pressure_bar: float = 1e-6,
                              *,
                              composition_mol: Optional[Dict[str, float]] = None,
                              composition_mol_by_account: Optional[
                                  Mapping[str, Mapping[str, float]]
                              ] = None,
                              species_formula_registry: Optional[
                                  Mapping[str, object]
                              ] = None,
                              min_T_C: float = 400.0,
                              max_T_C: float = 2200.0,
                              scan_step_C: float = 50.0,
                              tolerance_C: float = 2.0,
                              ) -> LiquidusSolidusResult:
        """Find solidus/liquidus when the PetThermoTools findLiq path is live."""
        if self._mode != 'python_api':
            return LiquidusSolidusResult(
                status='unavailable',
                warnings=(
                    'AlphaMELTS liquidus finder requires PetThermoTools '
                    'python_api mode',
                ),
            )

        comp_wt_result = self._composition_for_liquidus_finder(
            composition_kg=composition_kg,
            composition_mol=composition_mol,
            composition_mol_by_account=composition_mol_by_account,
            species_formula_registry=species_formula_registry,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            min_T_C=min_T_C,
        )
        if isinstance(comp_wt_result, LiquidusSolidusResult):
            return comp_wt_result
        comp_wt = comp_wt_result

        ptt_liquidus_C, ptt_warnings = self._find_petthermotools_liquidus_C(
            comp_wt,
            pressure_bar=pressure_bar,
            seed_T_C=max(min_T_C, ALPHAMELTS_LIQUIDUS_SEED_TEMPERATURE_C),
        )
        if ptt_liquidus_C is None:
            return LiquidusSolidusResult(
                status='unavailable',
                warnings=(
                    *ptt_warnings,
                    'AlphaMELTS liquidus finder unavailable: '
                    'PetThermoTools findLiq did not return a temperature',
                ),
            )

        sample_warnings: list[str] = []

        def sample_fraction(temperature_C: float) -> float:
            result = self.equilibrate(
                float(temperature_C),
                composition_kg=composition_kg,
                fO2_log=fO2_log,
                pressure_bar=pressure_bar,
                composition_mol=composition_mol,
                composition_mol_by_account=composition_mol_by_account,
                species_formula_registry=species_formula_registry,
            )
            if result.status != 'ok':
                warning = '; '.join(result.warnings) or result.status
                raise RuntimeError(warning)
            for warning in result.warnings:
                if warning not in sample_warnings:
                    sample_warnings.append(warning)
            return float(result.liquid_fraction)

        result = find_liquidus_solidus_by_fraction(
            sample_fraction,
            min_T_C=min_T_C,
            max_T_C=max_T_C,
            scan_step_C=scan_step_C,
            tolerance_C=tolerance_C,
        )
        warnings_out = [*ptt_warnings, *result.warnings, *sample_warnings[:6]]
        if (
            result.status == 'ok'
            and result.liquidus_T_C is not None
            and abs(result.liquidus_T_C - ptt_liquidus_C) > max(5.0, tolerance_C * 2.0)
        ):
            warnings_out.append(
                'PetThermoTools findLiq differs from frac_M bisection: '
                f'findLiq={ptt_liquidus_C:.3f} C, '
                f'bisection={result.liquidus_T_C:.3f} C'
            )
        return LiquidusSolidusResult(
            liquidus_T_C=result.liquidus_T_C,
            liquidus_T_K=result.liquidus_T_K,
            solidus_T_C=result.solidus_T_C,
            status=result.status,
            warnings=tuple(warnings_out),
            samples=result.samples,
            iterations=result.iterations,
        )

    def _unsupported_accounts(
        self,
        composition_mol_by_account: Mapping[str, Mapping[str, float]],
    ) -> Dict[str, List[str]]:
        unsupported = {
            str(account): sorted(
                str(species)
                for species, mol in (species_mol or {}).items()
                if float(mol) > 0.0
            )
            for account, species_mol in composition_mol_by_account.items()
            if str(account) != 'process.cleaned_melt'
        }
        return {account: species for account, species in unsupported.items()
                if species}

    def _composition_kg_to_wt_pct(self, composition_kg: Mapping[str, float]) -> dict:
        total = sum(float(value) for value in composition_kg.values()
                    if float(value) > 0.0)
        if total <= 0.0:
            return {}
        return {
            species: float(value) / total * 100.0
            for species, value in composition_kg.items()
            if float(value) > 0.0
        }

    def _domain_gate(self, comp_wt: Mapping[str, float], *,
                     temperature_C: float,
                     pressure_bar: float,
                     fO2_log: float) -> Optional[EquilibriumResult]:
        canonical_wt: Dict[str, float] = {}
        non_oxides: List[str] = []
        for raw_name, raw_wt in comp_wt.items():
            wt = float(raw_wt)
            if wt <= 0.0:
                continue
            oxide = self._canonical_oxide_name(raw_name)
            if oxide is None:
                if self._is_non_oxide_species_name(raw_name):
                    non_oxides.append(str(raw_name))
                continue
            canonical_wt[oxide] = canonical_wt.get(oxide, 0.0) + wt

        sio2_pct = canonical_wt.get('SiO2', 0.0)
        major_pct = sum(canonical_wt.values())
        reasons: List[str] = []
        if not 30.0 <= sio2_pct <= 80.0:
            reasons.append(f'SiO2 {sio2_pct:.3f} wt% outside [30, 80]')
        if major_pct <= 95.0:
            reasons.append(f'major oxide sum {major_pct:.3f} wt% <= 95')
        if non_oxides:
            reasons.append(
                'non-oxide species present: ' + ', '.join(sorted(non_oxides)))
        if not reasons:
            return None
        return self._domain_gate_result(
            temperature_C, pressure_bar, fO2_log, reasons)

    def _domain_gate_result(self, temperature_C: float, pressure_bar: float,
                            fO2_log: float,
                            reasons: List[str]) -> EquilibriumResult:
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            warnings=['DomainGate rejected: ' + '; '.join(reasons)],
            status='out_of_domain',
        )

    def _is_non_oxide_species_name(self, name: str) -> bool:
        text = str(name).strip()
        elements = re.findall(r'[A-Z][a-z]?', text)
        if not elements:
            return True
        if 'O' not in elements:
            return True
        return any(element in {'Cl', 'F', 'Br', 'I', 'S'} for element in elements)

    def _canonical_oxide_name(self, name: str) -> Optional[str]:
        key = str(name).strip()
        if key.endswith('_Liq'):
            key = key[:-4]
        return MELTS_OXIDE_ALIASES.get(key.lower())

    def _normalize_composition_to_melts_basis(self, comp_wt: dict) -> dict:
        self._last_normalization_warnings = []
        normalized_basis = {oxide: 0.0 for oxide in MELTS_OXIDE_BASIS}
        feo_total = 0.0

        for raw_name, raw_wt in comp_wt.items():
            wt = float(raw_wt)
            if wt <= 0.0:
                continue
            oxide = self._canonical_oxide_name(raw_name)
            if oxide is None:
                self._last_normalization_warnings.append(
                    f'Dropped non-MELTS component {raw_name}')
                continue
            if oxide == 'FeO_total':
                feo_total += wt
            else:
                normalized_basis[oxide] += wt

        if feo_total > 0.0:
            if self._fe3fet_ratio is not None:
                normalized_basis['FeO'] += feo_total * (1.0 - self._fe3fet_ratio)
                normalized_basis['Fe2O3'] += (
                    feo_total * self._fe3fet_ratio / FE3_TO_FEOT_FACTOR
                )
            elif self._redox_buffer is not None:
                normalized_basis['FeO'] += feo_total
                self._last_normalization_warnings.append(
                    f'FeO_total treated as FeOt with {self._redox_buffer} '
                    'redox buffer; no FeO/Fe2O3 auto-split applied'
                )
            else:
                raise ValueError('FeO_total requires explicit redox policy')

        total = sum(normalized_basis.values())
        if total <= 0.0:
            raise ValueError('AlphaMELTS composition has no MELTS-basis oxides')
        return {
            oxide: normalized_basis[oxide] / total * 100.0
            for oxide in MELTS_OXIDE_BASIS
        }

    # ------------------------------------------------------------------
    # Python API mode (PetThermoTools)
    # ------------------------------------------------------------------

    def _equilibrate_python(self, temperature_C, comp_wt,
                             fO2_log, pressure_bar, warnings=None):
        """
        Use PetThermoTools for equilibrium calculation.

        PetThermoTools wraps alphaMELTS for Python, providing
        phase assemblage, liquid composition, and activity data.
        """
        try:
            ptt = self._require_petthermotools_runtime()
            ptt_comp = self._to_petthermotools_liq_comp(comp_wt)
            results = ptt.equilibrate_MELTS(
                Model=self._model,
                P_bar=max(pressure_bar, 1e-6),
                T_C=temperature_C,
                comp=ptt_comp,
                fO2_buffer=self._redox_buffer,
                fO2_offset=self._fo2_offset,
                melts=self._pet_melts,
            )
            eq = self._parse_petthermotools_result(
                results,
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                comp_wt=comp_wt,
                warnings=warnings,
            )

            # Vapor pressures via VapoRock if available
            if self._vaporock_available:
                eq.vapor_pressures_Pa = self._get_vaporock_pressures(
                    temperature_C, comp_wt, fO2_log)
            else:
                # Use activity × pure-component Antoine only when the
                # chemical-potential convention supplied real activities.
                eq.vapor_pressures_Pa = self._activities_times_antoine(
                    temperature_C, eq.activity_coefficients, comp_wt)

            return eq

        except ImportError:
            self._mode = None
            raise
        except Exception as e:
            self._mode = None
            raise RuntimeError(f'AlphaMELTS Python equilibrium failed: {e}') from e

    def _require_petthermotools_runtime(self):
        if not self._pet_payload_preloaded or self._pet_melts is None:
            raise ImportError(
                'PetThermoTools runtime not preloaded; call initialize() '
                'so meltsdynamic loads outside the request hot path'
            )
        if self._pet_module is None:
            raise ImportError('PetThermoTools module not initialized')
        return self._pet_module

    def _to_petthermotools_liq_comp(self, comp_wt: Mapping[str, float]) -> dict:
        feot = float(comp_wt.get('FeO', 0.0)) + (
            FE3_TO_FEOT_FACTOR * float(comp_wt.get('Fe2O3', 0.0))
        )
        if feot > 0.0:
            fe3fet = (
                FE3_TO_FEOT_FACTOR * float(comp_wt.get('Fe2O3', 0.0)) / feot
            )
        else:
            fe3fet = self._fe3fet_ratio or 0.0
        return {
            'SiO2_Liq': float(comp_wt.get('SiO2', 0.0)),
            'TiO2_Liq': float(comp_wt.get('TiO2', 0.0)),
            'Al2O3_Liq': float(comp_wt.get('Al2O3', 0.0)),
            'Cr2O3_Liq': float(comp_wt.get('Cr2O3', 0.0)),
            'FeOt_Liq': feot,
            'MnO_Liq': float(comp_wt.get('MnO', 0.0)),
            'MgO_Liq': float(comp_wt.get('MgO', 0.0)),
            'CaO_Liq': float(comp_wt.get('CaO', 0.0)),
            'Na2O_Liq': float(comp_wt.get('Na2O', 0.0)),
            'K2O_Liq': float(comp_wt.get('K2O', 0.0)),
            'P2O5_Liq': float(comp_wt.get('P2O5', 0.0)),
            'H2O_Liq': 0.0,
            'CO2_Liq': 0.0,
            'Fe3Fet_Liq': max(0.0, min(1.0, fe3fet)),
        }

    def _composition_for_liquidus_finder(
        self,
        *,
        composition_kg: Optional[Dict[str, float]],
        composition_mol: Optional[Dict[str, float]],
        composition_mol_by_account: Optional[Mapping[str, Mapping[str, float]]],
        species_formula_registry: Optional[Mapping[str, object]],
        pressure_bar: float,
        fO2_log: float,
        min_T_C: float,
    ) -> dict | LiquidusSolidusResult:
        if composition_mol_by_account is not None:
            unsupported = self._unsupported_accounts(composition_mol_by_account)
            if unsupported:
                return LiquidusSolidusResult(
                    status='out_of_domain',
                    warnings=(
                        'unsupported ledger accounts present: '
                        + ', '.join(
                            f'{account}={species}'
                            for account, species in sorted(unsupported.items())
                        ),
                    ),
                )
            composition_mol = {}
            for species, mol in composition_mol_by_account.get(
                'process.cleaned_melt', {}
            ).items():
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

        raw_comp_wt = self._composition_kg_to_wt_pct(composition_kg)
        if not raw_comp_wt:
            return LiquidusSolidusResult(
                status='out_of_domain',
                warnings=('AlphaMELTS liquidus finder received no melt oxides',),
            )
        domain_rejection = self._domain_gate(
            raw_comp_wt,
            temperature_C=min_T_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
        )
        if domain_rejection is not None:
            return LiquidusSolidusResult(
                status='out_of_domain',
                warnings=tuple(domain_rejection.warnings),
            )
        try:
            return self._normalize_composition_to_melts_basis(raw_comp_wt)
        except ValueError as exc:
            return LiquidusSolidusResult(
                status='out_of_domain',
                warnings=(f'AlphaMELTS composition rejected: {exc}',),
            )

    def _find_petthermotools_liquidus_C(
        self,
        comp_wt: Mapping[str, float],
        *,
        pressure_bar: float,
        seed_T_C: float,
    ) -> tuple[Optional[float], tuple[str, ...]]:
        try:
            ptt = self._require_petthermotools_runtime()
        except ImportError as exc:
            return None, (f'PetThermoTools runtime unavailable: {exc}',)
        ptt_comp = self._to_petthermotools_liq_comp(comp_wt)
        find_liq_melts = getattr(ptt, 'findLiq_MELTS', None)
        find_liq = getattr(ptt, 'findLiq', None)
        try:
            if callable(find_liq_melts):
                raw = find_liq_melts(
                    P_bar=max(pressure_bar, 1e-6),
                    Model=self._model,
                    T_C_init=float(seed_T_C),
                    comp=ptt_comp,
                    melts=self._pet_melts,
                    fO2_buffer=self._redox_buffer,
                    fO2_offset=self._fo2_offset,
                    Step=50.0,
                )
            elif callable(find_liq):
                raw = find_liq(
                    None,
                    0,
                    Model=self._model,
                    P_bar=max(pressure_bar, 1e-6),
                    T_initial_C=float(seed_T_C),
                    comp=ptt_comp,
                    fO2_buffer=self._redox_buffer,
                    fO2_offset=self._fo2_offset,
                )
            else:
                return None, ('PetThermoTools findLiq API not found',)
        except Exception as exc:  # noqa: BLE001 - optional engine boundary
            return None, (f'PetThermoTools findLiq failed: {exc}',)
        return self._extract_temperature_C(raw), ()

    def _extract_temperature_C(self, raw: Any) -> Optional[float]:
        if raw is None:
            return None
        if self._is_number(raw):
            return float(raw)
        if isinstance(raw, Mapping):
            for key in (
                'T_Liq',
                'T_liq',
                'T_liquidus_C',
                'liquidus_T_C',
                'Liquidus',
                'liquidus',
                'T_C',
                'T',
            ):
                if key in raw and self._is_number(raw[key]):
                    return float(raw[key])
            for value in raw.values():
                found = self._extract_temperature_C(value)
                if found is not None:
                    return found
        if isinstance(raw, (tuple, list)):
            for value in raw:
                found = self._extract_temperature_C(value)
                if found is not None:
                    return found
        for key in (
            'T_Liq',
            'T_liq',
            'T_liquidus_C',
            'liquidus_T_C',
            'Liquidus',
            'liquidus',
            'T_C',
            'T',
        ):
            value = getattr(raw, key, None)
            if value is not None and self._is_number(value):
                return float(value)
        return None

    # ------------------------------------------------------------------
    # Subprocess mode
    # ------------------------------------------------------------------

    def _equilibrate_subprocess(self, temperature_C, comp_wt,
                                 fO2_log, pressure_bar, warnings=None):
        """
        Run alphaMELTS binary via subprocess.

        Writes a .melts input file, runs the binary, and parses
        the *_tbl.txt output files for phase data.

        Slower (~1-3s per call) but reliable.
        """
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
                total_input_kg=100.0,
                warnings=warnings,
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
                                   total_input_kg: float,
                                   warnings=None) -> EquilibriumResult:
        if not re.search(r'<> Stable .+ assemblage achieved\.', output):
            raise RuntimeError(
                'AlphaMELTS subprocess produced no stable assemblage verdict'
            )

        # The binary ran to completion with a stable assemblage - status 'ok'.
        eq = EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            warnings=list(warnings or []),
            status='ok',
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

    def _parse_petthermotools_result(self, results, *, temperature_C: float,
                                     pressure_bar: float, fO2_log: float,
                                     comp_wt: dict, warnings=None
                                     ) -> EquilibriumResult:
        # PetThermoTools returned a result object - status 'ok'.
        eq = EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            warnings=list(warnings or []),
            status='ok',
        )
        run_result = self._select_petthermotools_run(results)
        conditions = self._first_row_mapping(run_result.get('Conditions', {}))
        total_mass = self._first_number(conditions, ('mass', 'Mass'))

        phase_masses: Dict[str, float] = {}
        for key, value in run_result.items():
            if not self._is_petthermotools_phase_key(key, value):
                continue
            phase = str(key)
            prop = self._first_row_mapping(run_result.get(f'{phase}_prop', {}))
            mass_g = self._first_number(prop, ('mass', 'Mass'))
            if mass_g is None:
                row = self._first_row_mapping(value)
                mass_g = self._first_number(row, ('mass', 'Mass'))
            if mass_g is not None and mass_g > 0.0:
                phase_name = phase[:-1] if phase.endswith('_Liq') else phase
                if phase_name not in eq.phases_present:
                    eq.phases_present.append(phase_name)
                phase_masses[phase_name] = mass_g

        if phase_masses:
            eq.phase_masses_kg = {
                phase: mass_g / 1000.0
                for phase, mass_g in phase_masses.items()
            }
        liquid_key = self._select_liquid_phase_key(run_result)
        if liquid_key is not None:
            liquid_row = self._first_row_mapping(run_result.get(liquid_key, {}))
            eq.liquid_composition_wt_pct = (
                self._extract_liquid_composition(liquid_row) or dict(comp_wt)
            )
            liquid_mass = phase_masses.get(liquid_key)
            if liquid_mass is None:
                liquid_mass = phase_masses.get(
                    liquid_key[:-1] if liquid_key.endswith('_Liq') else liquid_key)
            if liquid_mass is not None and total_mass and total_mass > 0.0:
                eq.liquid_fraction = max(0.0, min(1.0, liquid_mass / total_mass))
        else:
            eq.liquid_composition_wt_pct = dict(comp_wt)

        eq.activity_coefficients = self._extract_activities_from_chemical_potentials(
            run_result,
            temperature_C=temperature_C,
        )
        if not eq.activity_coefficients:
            eq.warnings.append(
                'PetThermoTools chemical potentials absent; '
                'activity-scaled Antoine fallback skipped'
            )
        return eq

    def _select_petthermotools_run(self, results) -> dict:
        if isinstance(results, tuple) and results:
            results = results[0]
        if not isinstance(results, dict):
            return {'All': results}
        if any(key in results for key in ('Conditions', 'Mass', 'Input')):
            return results
        for value in results.values():
            if isinstance(value, tuple) and value:
                value = value[0]
            if isinstance(value, dict):
                return value
        return results

    def _first_row_mapping(self, table) -> dict:
        if table is None:
            return {}
        if isinstance(table, Mapping):
            return dict(table)
        try:
            if hasattr(table, 'empty') and bool(table.empty):
                return {}
            if hasattr(table, 'iloc'):
                row = table.iloc[0]
                if hasattr(row, 'to_dict'):
                    return dict(row.to_dict())
            if hasattr(table, 'to_dict'):
                value = table.to_dict()
                if isinstance(value, Mapping):
                    if all(isinstance(v, Mapping) for v in value.values()):
                        return {
                            key: next(iter(v.values()))
                            for key, v in value.items()
                            if v
                        }
                    return dict(value)
        except (IndexError, KeyError, TypeError, ValueError):
            return {}
        return {}

    def _first_number(self, values: Mapping[str, object],
                      keys: tuple[str, ...]) -> Optional[float]:
        for key in keys:
            if key in values and self._is_number(values[key]):
                return float(values[key])
        return None

    def _is_number(self, value) -> bool:
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    def _is_petthermotools_phase_key(self, key: str, value) -> bool:
        name = str(key)
        if name in PETTHERMOTOOLS_NON_PHASE_KEYS:
            return False
        if name.endswith('_prop') or name.endswith('_keys'):
            return False
        if hasattr(value, 'empty'):
            return not bool(value.empty)
        if isinstance(value, Mapping):
            return bool(value)
        return False

    def _select_liquid_phase_key(self, results: Mapping[str, object]) -> Optional[str]:
        for key in results:
            name = str(key)
            if name.lower().startswith('liquid') and not name.endswith('_prop'):
                return name
        return None

    def _extract_liquid_composition(self, row: Mapping[str, object]) -> dict:
        composition = {}
        for key, value in row.items():
            if not self._is_number(value):
                continue
            # _canonical_oxide_name strips a trailing '_Liq' internally.
            oxide = self._canonical_oxide_name(str(key))
            if oxide == 'FeO_total':
                oxide = 'FeO'
            if oxide in MELTS_OXIDE_BASIS:
                composition[oxide] = float(value)
        return composition

    def _extract_activities_from_chemical_potentials(
        self,
        results: Mapping[str, object],
        *,
        temperature_C: float,
    ) -> dict:
        mu = self._extract_potential_mapping(
            results,
            (
                'chemical_potentials',
                'chem_potentials',
                'mu',
                'mu_oxides',
                'oxide_mu',
            ),
            ('_mu', '_chemical_potential'),
        )
        mu0 = self._extract_potential_mapping(
            results,
            (
                'standard_chemical_potentials',
                'pure_chemical_potentials',
                'reference_chemical_potentials',
                'mu0',
                'mu0_oxides',
                'oxide_mu0',
            ),
            ('_mu0', '_pure_mu', '_standard_mu', '_reference_mu'),
        )
        if not mu or not mu0:
            return {}
        T_K = float(temperature_C) + 273.15
        activities: Dict[str, float] = {}
        for species, mu_i in mu.items():
            if species not in mu0:
                continue
            try:
                activity = activity_from_chem_potential(mu_i, mu0[species], T_K)
            except (OverflowError, ValueError):
                continue
            if activity > 0.0 and math.isfinite(activity):
                activities[str(species)] = activity
        return activities

    def _extract_potential_mapping(
        self,
        results: Mapping[str, object],
        keys: tuple[str, ...],
        suffixes: tuple[str, ...],
    ) -> dict:
        for key in keys:
            if key in results:
                row = self._first_row_mapping(results[key])
                mapped = {
                    str(species): float(value)
                    for species, value in row.items()
                    if self._is_number(value)
                }
                if mapped:
                    return mapped
        potentials: Dict[str, float] = {}
        for key, value in results.items():
            name = str(key)
            if not name.endswith('_prop'):
                continue
            row = self._first_row_mapping(value)
            for prop_name, prop_value in row.items():
                if not self._is_number(prop_value):
                    continue
                prop = str(prop_name)
                prop_lower = prop.lower()
                for suffix in suffixes:
                    if prop_lower.endswith(suffix):
                        species = prop[: -len(suffix)]
                        if species:
                            potentials[species] = float(prop_value)
                        break
        return potentials

    def decompression_path(self, T_C: float, P_start_bar: float,
                           P_end_bar: float, dp_bar: float, *,
                           composition_kg: Optional[Dict[str, float]] = None,
                           composition_mol: Optional[Dict[str, float]] = None,
                           composition_mol_by_account: Optional[
                               Mapping[str, Mapping[str, float]]
                           ] = None,
                           species_formula_registry: Optional[
                               Mapping[str, object]
                           ] = None,
                           fO2_log: float = -9.0) -> list[EquilibriumResult]:
        if composition_mol_by_account is not None:
            unsupported = self._unsupported_accounts(composition_mol_by_account)
            if unsupported:
                return [
                    self._domain_gate_result(
                        T_C,
                        P_start_bar,
                        fO2_log,
                        [
                            'unsupported ledger accounts present: '
                            + ', '.join(
                                f'{account}={species}'
                                for account, species in sorted(unsupported.items())
                            )
                        ],
                    )
                ]
            composition_mol = dict(
                composition_mol_by_account.get('process.cleaned_melt', {}))
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
        if composition_kg is None:
            raise ValueError('decompression_path requires composition input')

        raw_comp_wt = self._composition_kg_to_wt_pct(composition_kg)
        domain_rejection = self._domain_gate(
            raw_comp_wt,
            temperature_C=T_C,
            pressure_bar=P_start_bar,
            fO2_log=fO2_log,
        )
        if domain_rejection is not None:
            return [domain_rejection]
        comp_wt = self._normalize_composition_to_melts_basis(raw_comp_wt)
        ptt = self._require_petthermotools_runtime()
        ptt_comp = self._to_petthermotools_liq_comp(comp_wt)
        if not hasattr(ptt, 'isothermal_decompression'):
            raise AttributeError(
                'PetThermoTools isothermal_decompression API not found'
            )
        results = ptt.isothermal_decompression(
            Model=self._model,
            bulk=ptt_comp,
            T_C=T_C,
            P_start_bar=P_start_bar,
            P_end_bar=P_end_bar,
            dp_bar=dp_bar,
            fO2_buffer=self._redox_buffer,
            fO2_offset=self._fo2_offset,
            multi_processing=False,
        )
        if isinstance(results, Mapping) and all(
            isinstance(value, Mapping) for value in results.values()
        ):
            runs = list(results.values())
        else:
            runs = [results]
        return [
            self._parse_petthermotools_result(
                run,
                temperature_C=T_C,
                pressure_bar=P_start_bar,
                fO2_log=fO2_log,
                comp_wt=comp_wt,
                warnings=self._last_normalization_warnings,
            )
            for run in runs
        ]

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
                                    _comp_wt: dict) -> Dict[str, float]:
        """
        Compute vapor pressures as thermodynamic activity × pure-component P(T).

        Fallback when VapoRock is not available. Uses Antoine equation
        parameters from vapor_pressures.yaml. Activities must already be
        pure-endmember-referenced values from
        ``activity_from_chem_potential(mu, mu0, T_K)``.

        P_i = a_i × P_pure_i(T)

        If activities are unavailable, no pressure is emitted.
        """
        if not activities:
            return {}
        table = self._load_vapor_pressure_table()
        if not table:
            return {}
        T_K = float(T_C) + 273.15
        pressures: Dict[str, float] = {}
        for species, raw_activity in activities.items():
            if not self._is_number(raw_activity):
                continue
            spec = table.get(str(species))
            if not spec:
                continue
            coeffs = spec.get('antoine') or {}
            if not all(key in coeffs for key in ('A', 'B', 'C')):
                continue
            activity_i = float(raw_activity)
            p_pure_i = 10.0 ** (
                float(coeffs['A']) - float(coeffs['B']) / (T_K + float(coeffs['C']))
            )
            p_i = activity_i * p_pure_i
            if p_i > 0.0 and math.isfinite(p_i):
                pressures[str(species)] = p_i
        return pressures

    def _load_vapor_pressure_table(self) -> dict:
        if self._vapor_pressure_table is not None:
            return self._vapor_pressure_table
        import yaml

        path = (
            Path(__file__).parent.parent.parent
            / 'data'
            / 'vapor_pressures.yaml'
        )
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        table = {}
        for group in ('metals', 'oxide_vapors'):
            for species, spec in (data.get(group) or {}).items():
                table[str(species)] = dict(spec or {})
        self._vapor_pressure_table = table
        return table

    def _oxide_mole_fractions(self, comp_wt: Mapping[str, float]) -> Dict[str, float]:
        moles: Dict[str, float] = {}
        for oxide, wt in comp_wt.items():
            if not self._is_number(wt) or float(wt) <= 0.0:
                continue
            try:
                molar_mass = resolve_species_formula(
                    oxide, None).molar_mass_kg_per_mol()
            except Exception:
                continue
            if molar_mass > 0.0:
                moles[str(oxide)] = float(wt) / molar_mass
        total_moles = sum(moles.values())
        if total_moles <= 0.0:
            return {}
        return {
            oxide: mol / total_moles
            for oxide, mol in moles.items()
        }
