"""
FactSAGE / ChemApp Backend
==========================

Optional adapter for FactSAGE thermodynamic databases through the
ChemApp for Python API.  The dependency is deliberately imported only
inside ``initialize()`` so the simulator remains usable without a
FactSAGE/ChemApp license or local data file.

The simulator-facing boundary is mol-native.  Kg is accepted only as an
external projection helper for callers outside the AtomLedger kernel.
"""

from __future__ import annotations

import importlib
import math
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from simulator.accounting.exceptions import AccountingError
from simulator.accounting.formulas import resolve_species_formula
from simulator.melt_backend.base import (
    EquilibriumResult,
    MeltBackend,
    normalize_backend_capabilities,
)
from simulator.state import OXIDE_SPECIES


SIMULATOR_OXIDES = tuple(OXIDE_SPECIES)

DEFAULT_COMPONENT_MAP = {oxide: oxide for oxide in SIMULATOR_OXIDES}

DEFAULT_SPECIES_MAP = {
    'Na': 'Na',
    'K': 'K',
    'Fe': 'Fe',
    'Mg': 'Mg',
    'Ca': 'Ca',
    'SiO': 'SiO',
}

DEFAULT_PHASE_MAP = {
    'liquid': ('LIQUID', 'SLAG', 'MELT'),
    'gas': ('GAS', 'VAPOR'),
}

SUPPORTED_AMOUNT_UNITS = {
    'kg',
    'kilogram',
    'kilograms',
    'g',
    'gram',
    'grams',
    'tonne',
    'tonnes',
    'metric_ton',
    'mol',
    'mole',
    'moles',
}


class FactSAGEBackend(MeltBackend):
    """
    FactSAGE/ChemApp thermodynamic backend.

    The adapter uses the documented ``chemapp.friendly`` API when
    available.  All ChemApp calls are kept behind private helpers so the
    simulator can import this module without ChemApp installed.
    """

    _chemapp_lock = threading.RLock()

    def __init__(self):
        self._available = False
        self._chemapp: Optional[Any] = None
        self._thermo: Optional[Any] = None
        self._equilibrium: Optional[Any] = None
        self._units: Optional[Any] = None
        self._config: Dict[str, Any] = {}
        self._component_map: Dict[str, Any] = dict(DEFAULT_COMPONENT_MAP)
        self._species_map: Dict[str, str] = dict(DEFAULT_SPECIES_MAP)
        self._phase_roles: Dict[str, str] = {}
        self._datafile_path: Optional[Path] = None
        self._timeout_s: Optional[float] = None
        self._amount_unit = 'mol'
        self._capabilities = normalize_backend_capabilities()
        self._warnings: List[str] = []
        self._last_error: Optional[str] = None
        self._build_phase_roles(DEFAULT_PHASE_MAP)

    @property
    def warnings(self) -> List[str]:
        """Warnings from initialization or the most recent calculation."""
        return list(self._warnings)

    @property
    def last_error(self) -> Optional[str]:
        """Last backend error string, if any."""
        return self._last_error

    def initialize(self, config: dict) -> bool:
        """
        Initialize ChemApp and load the configured thermodynamic data file.

        Required local state is intentionally explicit.  A ChemApp module
        without a configured data file is not a usable backend, so this
        returns ``False`` and lets the simulator fall back to its built-in
        thermodynamic approximation.
        """
        with self._chemapp_lock:
            return self._initialize_locked(config)

    def _initialize_locked(self, config: dict) -> bool:
        self._available = False
        self._last_error = None
        self._warnings = []
        self._config = dict(config or {})
        try:
            self._capabilities = self._capabilities_from_config(self._config)
        except ValueError as exc:
            self._last_error = str(exc)
            self._warn(str(exc))
            return False
        self._component_map = self._merged_component_map(self._config)
        self._species_map = self._merged_species_map(self._config)
        self._phase_roles = {}
        self._build_phase_roles(DEFAULT_PHASE_MAP)
        self._build_phase_roles(self._config.get('phase_map') or {})
        self._timeout_s = self._coerce_optional_float(
            self._config.get('timeout_s'))
        self._amount_unit = str(
            self._config.get('amount_unit')
            or self._config.get('chemapp_amount_unit')
            or 'mol'
        ).lower()
        if self._amount_unit not in SUPPORTED_AMOUNT_UNITS:
            self._warn(
                f'FactSAGE amount unit {self._amount_unit!r} is not supported; '
                'configure mol, kg, g, or tonne'
            )
            return False

        module = self._load_chemapp_module(self._config)
        if module is None:
            return False

        self._chemapp = module
        self._bind_chemapp_api(module)

        datafile_path = self._resolve_datafile_path(self._config)
        if datafile_path is None:
            self._warn('FactSAGE data file not configured')
            return False
        if not datafile_path.exists():
            self._warn(f'FactSAGE data file not found: {datafile_path}')
            return False

        try:
            self._load_datafile(datafile_path)
            self._configure_units()
        except Exception as exc:
            self._record_error('FactSAGE data file load failed', exc)
            return False

        self._datafile_path = datafile_path
        self._available = True
        return True

    def is_available(self) -> bool:
        return self._available

    def get_vapor_species(self) -> List[str]:
        return list(self._species_map.keys())

    def capabilities(self) -> Dict[str, bool]:
        return dict(self._capabilities)

    def equilibrate(self, temperature_C: float,
                    composition_kg: Optional[Dict[str, float]] = None,
                    fO2_log: float = -9.0,
                    pressure_bar: float = 1e-6,
                    *,
                    composition_mol: Optional[Dict[str, float]] = None
                    ) -> EquilibriumResult:
        if not self.is_available():
            return self._empty_result(temperature_C, pressure_bar, fO2_log)

        self._warnings = []
        try:
            with self._chemapp_lock:
                formula_amounts, component_amounts = self._convert_composition(
                    composition_mol=composition_mol,
                    composition_kg=composition_kg,
                )
                self._set_incoming_amounts(formula_amounts, component_amounts)
                self._set_conditions(temperature_C, pressure_bar, fO2_log)
                raw_result = self._run_equilibrium()
                return self._parse_result(
                    raw_result, temperature_C, pressure_bar, fO2_log)
        except AccountingError:
            raise
        except (RuntimeError, ValueError, OSError, ArithmeticError) as exc:
            self._available = False
            self._record_error('FactSAGE equilibrium failed', exc)
            raise RuntimeError(self._last_error) from exc

    # ------------------------------------------------------------------
    # ChemApp boundary
    # ------------------------------------------------------------------

    def _load_chemapp_module(self, config: Mapping[str, Any]) -> Optional[Any]:
        requested = config.get('chemapp_module')
        candidates: Tuple[str, ...]
        if requested:
            candidates = (str(requested),)
        else:
            candidates = ('chemapp.friendly', 'ChemApp')

        errors = []
        for name in candidates:
            try:
                return importlib.import_module(name)
            except ImportError as exc:
                errors.append(f'{name}: {exc}')

        self._last_error = '; '.join(errors) if errors else None
        self._warn('ChemApp Python module not available')
        return None

    def _bind_chemapp_api(self, module: Any) -> None:
        self._thermo = self._first_attr(module, 'ThermochemicalSystem')
        self._equilibrium = self._first_attr(module, 'EquilibriumCalculation')
        self._units = self._first_attr(module, 'Units')

        if self._thermo is None and hasattr(module, 'friendly'):
            friendly = getattr(module, 'friendly')
            self._thermo = self._first_attr(friendly, 'ThermochemicalSystem')
            self._equilibrium = self._first_attr(friendly, 'EquilibriumCalculation')
            self._units = self._first_attr(friendly, 'Units')

    def _load_datafile(self, datafile_path: Path) -> None:
        if self._thermo is not None and hasattr(self._thermo, 'load'):
            self._thermo.load(str(datafile_path))
            return

        if hasattr(self._chemapp, 'load_data_file'):
            self._chemapp.load_data_file(str(datafile_path))
            return

        raise RuntimeError(
            'ChemApp module does not expose ThermochemicalSystem.load()')

    def _configure_units(self) -> None:
        if self._units is None or not hasattr(self._units, 'set'):
            raise RuntimeError(
                'ChemApp API has no Units.set(); cannot configure mol amount unit'
            )

        kwargs = {}
        pressure_unit = self._enum_member('PressureUnit', 'bar')
        temperature_unit = self._enum_member('TemperatureUnit', 'K')
        amount_unit = self._enum_member('AmountUnit', self._amount_unit)
        energy_unit = self._enum_member('EnergyUnit', 'J')

        if pressure_unit is not None:
            kwargs['P'] = pressure_unit
        if temperature_unit is not None:
            kwargs['T'] = temperature_unit
        if amount_unit is None:
            raise RuntimeError(
                f'ChemApp AmountUnit has no {self._amount_unit!r} member'
            )
        kwargs['A'] = amount_unit
        if energy_unit is not None:
            kwargs['E'] = energy_unit

        if kwargs:
            self._units.set(**kwargs)

    def _set_conditions(self, temperature_C: float, pressure_bar: float,
                        fO2_log: float) -> None:
        eq = self._require_equilibrium_api()
        temperature_K = temperature_C + 273.15
        pressure = max(float(pressure_bar), 1e-20)

        eq.set_eq_T(temperature_K)
        eq.set_eq_P(pressure)

        if not self._config.get('control_fO2', True):
            return

        oxygen_phase = str(self._config.get('oxygen_phase', 'GAS'))
        oxygen_species = str(self._config.get('oxygen_species', 'O2'))
        oxygen_fugacity_bar = 10.0 ** float(fO2_log)

        # ChemApp documents AC for gas constituents as fugacity in the
        # active pressure unit, so this is the closest direct fO2 control.
        if hasattr(eq, 'set_eq_AC_pc'):
            try:
                eq.set_eq_AC_pc(oxygen_phase, oxygen_species,
                                oxygen_fugacity_bar)
            except Exception as exc:
                raise RuntimeError(
                    'FactSAGE fO2 control unavailable for '
                    f'{oxygen_phase}/{oxygen_species}: {exc}'
                ) from exc
        else:
            raise RuntimeError('ChemApp API has no set_eq_AC_pc() fO2 control')

    def _set_incoming_amounts(self, formula_amounts: Dict[str, float],
                              component_amounts: Dict[str, float]) -> None:
        eq = self._require_equilibrium_api()
        all_formula_names, all_component_names = self._incoming_amount_names()

        if formula_amounts or all_formula_names:
            if not hasattr(eq, 'set_IA_cfs'):
                raise RuntimeError('ChemApp API has no set_IA_cfs()')
            names = sorted(set(all_formula_names) | set(formula_amounts))
            values = [formula_amounts.get(name, 0.0) for name in names]
            eq.set_IA_cfs(names, values)

        if component_amounts or all_component_names:
            if not hasattr(eq, 'set_IA_sc'):
                raise RuntimeError('ChemApp API has no set_IA_sc()')
            for component in sorted(set(all_component_names) | set(component_amounts)):
                eq.set_IA_sc(component, component_amounts.get(component, 0.0))

    def _incoming_amount_names(self) -> Tuple[List[str], List[str]]:
        formula_names: set[str] = set()
        component_names: set[str] = set()
        for mapping in self._component_map.values():
            if mapping is None:
                continue
            if isinstance(mapping, Mapping):
                component_names.update(str(component) for component in mapping)
            else:
                formula_names.add(str(mapping))
        return sorted(formula_names), sorted(component_names)

    def _run_equilibrium(self) -> Any:
        eq = self._require_equilibrium_api()

        if self._timeout_s is not None:
            self._warn(
                'timeout_s accepted but not hard-enforced for in-process ChemApp')

        try:
            result = eq.calculate_eq(return_result=True)
        except TypeError:
            result = eq.calculate_eq()

        if result is None and hasattr(eq, 'get_result_object'):
            result = eq.get_result_object()
        if result is None:
            raise RuntimeError('ChemApp returned no equilibrium result')
        return result

    # ------------------------------------------------------------------
    # Input conversion
    # ------------------------------------------------------------------

    def _convert_composition(
        self,
        *,
        composition_mol: Optional[Dict[str, float]] = None,
        composition_kg: Optional[Dict[str, float]] = None,
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        formula_amounts: Dict[str, float] = {}
        component_amounts: Dict[str, float] = {}
        source = composition_mol if composition_mol is not None else composition_kg
        source_is_mol = composition_mol is not None

        for oxide, raw_amount in (source or {}).items():
            raw_value = float(raw_amount)
            if raw_value <= 0:
                continue
            amount = (
                self._configured_amount_from_mol(str(oxide), raw_value)
                if source_is_mol
                else self._configured_amount_from_kg(str(oxide), raw_value)
            )

            mapping = self._component_map.get(oxide)
            if mapping is None:
                raise ValueError(
                    f'No FactSAGE component mapping for oxide {oxide!r}')

            if isinstance(mapping, Mapping):
                for component, fraction in mapping.items():
                    component_amounts[str(component)] = (
                        component_amounts.get(str(component), 0.0)
                        + amount * float(fraction)
                    )
            else:
                target = str(mapping)
                formula_amounts[target] = (
                    formula_amounts.get(target, 0.0) + amount)

        if not formula_amounts and not component_amounts:
            raise ValueError('No positive melt composition supplied')

        return formula_amounts, component_amounts

    # ------------------------------------------------------------------
    # Result parsing
    # ------------------------------------------------------------------

    def _parse_result(self, raw_result: Any, temperature_C: float,
                      pressure_bar: float,
                      fO2_log: float) -> EquilibriumResult:
        eq = EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
        )

        phases = self._extract_phases(raw_result)
        total_mass_kg = 0.0
        liquid_mass_kg = 0.0
        liquid_components: Dict[str, float] = {}

        for phase_name, phase_state in phases:
            species_mol, species_kg = self._phase_species_amounts(phase_state)
            phase_mass_kg = (
                sum(species_kg.values())
                if species_kg
                else self._phase_amount_kg(phase_state)
            )
            if phase_mass_kg <= 0:
                continue

            total_mass_kg += phase_mass_kg
            eq.phases_present.append(phase_name)
            eq.phase_masses_kg[phase_name] = phase_mass_kg
            if species_mol:
                eq.phase_species_mol[phase_name] = species_mol
                eq.phase_species_kg[phase_name] = species_kg

            composition = self._phase_composition_wt_pct(
                phase_state, phase_mass_kg, species_kg)
            if composition:
                eq.phase_compositions[phase_name] = composition

            if self._phase_role(phase_name) == 'liquid':
                liquid_mass_kg += phase_mass_kg
                for species, wt_pct in composition.items():
                    liquid_components[species] = (
                        liquid_components.get(species, 0.0)
                        + wt_pct * phase_mass_kg / 100.0
                    )

            eq.activity_coefficients.update(
                self._activities_for_phase(phase_name, phase_state))

        if total_mass_kg > 0:
            eq.liquid_fraction = liquid_mass_kg / total_mass_kg
        if liquid_mass_kg > 0 and liquid_components:
            eq.liquid_composition_wt_pct = {
                species: mass / liquid_mass_kg * 100.0
                for species, mass in liquid_components.items()
                if mass > 0
            }
        else:
            direct_liquid = self._get_mapping(
                raw_result, 'liquid_composition_wt_pct')
            if direct_liquid:
                eq.liquid_composition_wt_pct = {
                    str(k): float(v) for k, v in direct_liquid.items()
                }

        eq.vapor_pressures_Pa = self._extract_vapor_pressures(
            raw_result, phases)
        eq.warnings = list(self._warnings)

        direct_phases = self._get_mapping(raw_result, 'phases')
        if direct_phases and not eq.phase_masses_kg:
            for phase_name, amount in direct_phases.items():
                amount_kg = self._amount_to_kg(float(amount))
                if amount_kg <= 0:
                    continue
                eq.phases_present.append(str(phase_name))
                eq.phase_masses_kg[str(phase_name)] = amount_kg

        return eq

    def _extract_phases(self, raw_result: Any) -> List[Tuple[str, Any]]:
        phase_map = self._get_mapping(raw_result, 'phs')
        if phase_map:
            return [(str(name), state) for name, state in phase_map.items()]

        direct = self._get_mapping(raw_result, 'phases')
        if direct:
            return [(str(name), {'A': amount}) for name, amount in direct.items()]

        return []

    def _phase_amount_kg(self, phase_state: Any) -> float:
        amount = self._get_number(phase_state, 'A')
        if amount is None and isinstance(phase_state, (int, float)):
            amount = float(phase_state)
        if amount is None:
            return 0.0
        return self._amount_to_kg(amount)

    def _phase_composition_wt_pct(
        self,
        phase_state: Any,
        phase_mass_kg: float,
        species_kg: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        direct = self._get_mapping(phase_state, 'composition_wt_pct')
        if direct:
            return {str(k): float(v) for k, v in direct.items()}

        if species_kg:
            total = phase_mass_kg if phase_mass_kg > 0 else sum(species_kg.values())
            if total <= 0:
                return {}
            return {
                species: mass / total * 100.0
                for species, mass in species_kg.items()
                if mass > 0
            }

        constituents = self._get_mapping(phase_state, 'pcs')
        if not constituents:
            return {}

        masses: Dict[str, float] = {}
        for name, state in constituents.items():
            amount = self._get_number(state, 'A')
            if amount is None:
                continue
            amount_kg = self._amount_to_kg(amount)
            if amount_kg > 0:
                display_name = (
                    self._simulator_component_for_backend_name(str(name))
                    or str(name)
                )
                masses[display_name] = (
                    masses.get(display_name, 0.0) + amount_kg)

        total = phase_mass_kg if phase_mass_kg > 0 else sum(masses.values())
        if total <= 0:
            return {}

        return {
            species: mass / total * 100.0
            for species, mass in masses.items()
            if mass > 0
        }

    def _phase_species_amounts(
        self, phase_state: Any
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        direct_mol = self._get_mapping(phase_state, 'species_mol')
        if direct_mol:
            species_mol = {
                str(species): float(mol)
                for species, mol in direct_mol.items()
                if float(mol) > 0.0
            }
            return species_mol, {
                species: self._species_mol_to_kg(species, mol)
                for species, mol in species_mol.items()
            }

        constituents = self._get_mapping(phase_state, 'pcs')
        if not constituents:
            return {}, {}

        species_mol: Dict[str, float] = {}
        species_kg: Dict[str, float] = {}
        for name, state in constituents.items():
            amount = self._get_number(state, 'A')
            if amount is None:
                continue
            simulator_species = self._simulator_name_for_backend_constituent(
                str(name))
            if simulator_species is None:
                raise RuntimeError(
                    'FactSAGE constituent has no explicit simulator mapping: '
                    f'{name!r}'
                )
            mol, kg = self._amount_to_species_mol_kg(
                simulator_species, amount)
            if mol <= 0.0 or kg <= 0.0:
                continue
            species_mol[simulator_species] = (
                species_mol.get(simulator_species, 0.0) + mol)
            species_kg[simulator_species] = (
                species_kg.get(simulator_species, 0.0) + kg)
        return dict(sorted(species_mol.items())), dict(sorted(species_kg.items()))

    def _activities_for_phase(self, phase_name: str,
                              phase_state: Any) -> Dict[str, float]:
        activities: Dict[str, float] = {}
        constituents = self._get_mapping(phase_state, 'pcs')
        if not constituents:
            return activities

        for constituent_name, state in constituents.items():
            value = self._get_number(
                state, 'AC', 'activity', 'activity_coefficient', 'fugacity')
            if value is None:
                continue
            name = str(constituent_name)
            activities[name] = value
            simulator_name = self._simulator_species_for_backend_name(name)
            if simulator_name is not None:
                activities[simulator_name] = value

        return activities

    def _extract_vapor_pressures(
        self, raw_result: Any, phases: List[Tuple[str, Any]]
    ) -> Dict[str, float]:
        direct = self._get_mapping(raw_result, 'vapor_pressures_Pa')
        if direct:
            return self._filter_direct_vapor_pressures(direct)

        gas_phases = [
            phase for phase_name, phase in phases
            if self._phase_role(phase_name) == 'gas'
        ]
        pressures: Dict[str, float] = {}
        missing: List[str] = []

        for simulator_species in self.get_vapor_species():
            backend_names = self._backend_species_aliases(simulator_species)
            pressure = self._find_gas_species_pressure(gas_phases, backend_names)
            if pressure is None:
                missing.append(simulator_species)
                continue
            if pressure > 0:
                pressures[simulator_species] = pressure

        if missing:
            self._warn(
                'FactSAGE vapor species unavailable: ' + ', '.join(missing))

        return pressures

    def _filter_direct_vapor_pressures(
        self, raw_pressures: Mapping[str, Any]
    ) -> Dict[str, float]:
        pressures: Dict[str, float] = {}
        missing: List[str] = []

        for simulator_species in self.get_vapor_species():
            candidates = [simulator_species]
            candidates.extend(self._backend_species_aliases(simulator_species))
            value = None
            for name in candidates:
                if name in raw_pressures:
                    value = float(raw_pressures[name])
                    break
            if value is None:
                missing.append(simulator_species)
            elif value > 0:
                pressures[simulator_species] = value

        if missing:
            self._warn(
                'FactSAGE vapor species unavailable: ' + ', '.join(missing))

        return pressures

    def _find_gas_species_pressure(
        self, gas_phases: List[Any], backend_names: Iterable[str]
    ) -> Optional[float]:
        backend_lookup = {self._normal_name(name) for name in backend_names}

        for phase in gas_phases:
            constituents = self._get_mapping(phase, 'pcs')
            if not constituents:
                continue
            for constituent_name, state in constituents.items():
                if self._normal_name(str(constituent_name)) not in backend_lookup:
                    continue

                pressure_pa = self._get_number(
                    state, 'vapor_pressure_Pa', 'pressure_Pa', 'P_Pa')
                if pressure_pa is not None:
                    return pressure_pa

                pressure_bar = self._get_number(
                    state, 'vapor_pressure_bar', 'pressure_bar', 'P_bar')
                if pressure_bar is not None:
                    return pressure_bar * 1e5

                fugacity_bar = self._get_number(
                    state, 'AC', 'fugacity', 'activity')
                if fugacity_bar is not None:
                    return fugacity_bar * 1e5

        return None

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _merged_component_map(self, config: Mapping[str, Any]) -> Dict[str, Any]:
        merged = dict(DEFAULT_COMPONENT_MAP)
        merged.update(config.get('component_map') or {})
        return merged

    def _merged_species_map(self, config: Mapping[str, Any]) -> Dict[str, str]:
        merged = dict(DEFAULT_SPECIES_MAP)
        merged.update(config.get('species_map') or {})
        return {str(k): str(v) for k, v in merged.items()}

    def _capabilities_from_config(
        self, config: Mapping[str, Any]
    ) -> Dict[str, bool]:
        if 'capabilities' in config:
            raw = config.get('capabilities')
        else:
            raw = config.get('capability_profile')
        if raw in ('silicate_melt_only', 'melt_only'):
            raw = ['silicate_melt']
        if raw == 'whole_regolith':
            raise ValueError(
                'whole_regolith is not a valid FactSAGE capability; '
                'declare specific export capabilities instead')
        return normalize_backend_capabilities(raw)

    def _build_phase_roles(self, phase_map: Mapping[str, Any]) -> None:
        for key, value in phase_map.items():
            key_s = str(key)
            if isinstance(value, (list, tuple, set)):
                role = key_s.lower()
                for phase_name in value:
                    self._phase_roles[self._normal_name(str(phase_name))] = role
            else:
                value_s = str(value)
                if value_s.lower() in ('liquid', 'gas', 'solid', 'metal'):
                    self._phase_roles[self._normal_name(key_s)] = value_s.lower()
                else:
                    self._phase_roles[self._normal_name(value_s)] = key_s.lower()

    def _resolve_datafile_path(self, config: Mapping[str, Any]) -> Optional[Path]:
        raw_path = (
            config.get('datafile_path')
            or config.get('database_path')
            or config.get('factsage_datafile')
            or config.get('data_file')
        )
        if not raw_path:
            return None
        return Path(str(raw_path)).expanduser()

    def _backend_species_aliases(self, simulator_species: str) -> List[str]:
        configured = self._species_map.get(simulator_species, simulator_species)
        names = {
            simulator_species,
            configured,
            f'{configured}(g)',
            f'{configured}(G)',
            f'{simulator_species}(g)',
            f'{simulator_species}(G)',
        }
        return [name for name in names if name]

    def _simulator_species_for_backend_name(
        self, backend_name: str
    ) -> Optional[str]:
        target = self._normal_name(backend_name)
        for simulator_species in self.get_vapor_species():
            aliases = self._backend_species_aliases(simulator_species)
            if target in {self._normal_name(alias) for alias in aliases}:
                return simulator_species
        return None

    def _simulator_component_for_backend_name(
        self, backend_name: str
    ) -> Optional[str]:
        target = self._normal_name(backend_name)
        for simulator_oxide, mapping in self._component_map.items():
            if isinstance(mapping, Mapping):
                continue
            if self._normal_name(str(mapping)) == target:
                return simulator_oxide
        return None

    def _simulator_name_for_backend_constituent(
        self, backend_name: str
    ) -> Optional[str]:
        return (
            self._simulator_component_for_backend_name(backend_name)
            or self._simulator_species_for_backend_name(backend_name)
        )

    def _phase_role(self, phase_name: str) -> Optional[str]:
        normalized = self._normal_name(phase_name)
        if normalized in self._phase_roles:
            return self._phase_roles[normalized]
        if 'liquid' in normalized or 'melt' in normalized or 'slag' in normalized:
            return 'liquid'
        if 'gas' in normalized or 'vapor' in normalized:
            return 'gas'
        return None

    # ------------------------------------------------------------------
    # Generic object helpers
    # ------------------------------------------------------------------

    def _require_equilibrium_api(self) -> Any:
        if self._equilibrium is None:
            raise RuntimeError(
                'ChemApp module does not expose EquilibriumCalculation')
        return self._equilibrium

    def _enum_member(self, enum_name: str, member_name: str) -> Optional[Any]:
        enum = self._first_attr(self._chemapp, enum_name)
        if enum is None:
            return None
        return getattr(enum, member_name, None)

    @staticmethod
    def _first_attr(obj: Any, name: str) -> Any:
        if obj is None:
            return None
        return getattr(obj, name, None)

    @staticmethod
    def _get_mapping(obj: Any, name: str) -> Dict[str, Any]:
        if isinstance(obj, Mapping):
            value = obj.get(name)
        else:
            value = getattr(obj, name, None)
        if isinstance(value, Mapping):
            return dict(value)
        return {}

    @staticmethod
    def _get_number(obj: Any, *names: str) -> Optional[float]:
        for name in names:
            if isinstance(obj, Mapping):
                value = obj.get(name)
            else:
                value = getattr(obj, name, None)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def _amount_to_kg(self, amount: float) -> float:
        if self._amount_unit in ('kg', 'kilogram', 'kilograms'):
            return amount
        if self._amount_unit in ('g', 'gram', 'grams'):
            return amount / 1000.0
        if self._amount_unit in ('tonne', 'tonnes', 'metric_ton'):
            return amount * 1000.0
        if self._amount_unit in ('mol', 'mole', 'moles'):
            raise RuntimeError(
                'FactSAGE phase total in mol cannot be converted to kg '
                'without species-resolved phase constituents'
            )
        raise RuntimeError(
            f'FactSAGE amount unit {self._amount_unit!r} is not supported; '
            'configure mol, kg, g, or tonne'
        )

    def _amount_to_species_mol_kg(
        self, species: str, amount: float
    ) -> Tuple[float, float]:
        amount_value = float(amount)
        formula = resolve_species_formula(species)
        kg_per_mol = formula.molar_mass_kg_per_mol()
        if self._amount_unit in ('mol', 'mole', 'moles'):
            mol = amount_value
            return mol, mol * kg_per_mol
        kg = self._amount_to_kg(amount_value)
        return kg / kg_per_mol, kg

    def _configured_amount_from_mol(self, species: str, mol: float) -> float:
        mol_value = float(mol)
        if self._amount_unit in ('mol', 'mole', 'moles'):
            return mol_value
        kg = self._species_mol_to_kg(species, mol_value)
        return self._configured_amount_from_kg(species, kg)

    def _configured_amount_from_kg(self, species: str, kg: float) -> float:
        kg_value = float(kg)
        if self._amount_unit in ('kg', 'kilogram', 'kilograms'):
            return kg_value
        if self._amount_unit in ('g', 'gram', 'grams'):
            return kg_value * 1000.0
        if self._amount_unit in ('tonne', 'tonnes', 'metric_ton'):
            return kg_value / 1000.0
        if self._amount_unit in ('mol', 'mole', 'moles'):
            formula = resolve_species_formula(species)
            return kg_value / formula.molar_mass_kg_per_mol()
        raise RuntimeError(
            f'FactSAGE amount unit {self._amount_unit!r} is not supported; '
            'configure mol, kg, g, or tonne'
        )

    @staticmethod
    def _species_mol_to_kg(species: str, mol: float) -> float:
        formula = resolve_species_formula(species)
        return float(mol) * formula.molar_mass_kg_per_mol()

    @staticmethod
    def _normal_name(name: str) -> str:
        return ''.join(ch for ch in name.lower() if ch.isalnum())

    @staticmethod
    def _coerce_optional_float(value: Any) -> Optional[float]:
        if value is None or value == '':
            return None
        return float(value)

    def _record_error(self, message: str, exc: Exception) -> None:
        self._last_error = f'{message}: {exc}'
        self._warn(self._last_error)

    def _warn(self, message: str) -> None:
        if message not in self._warnings:
            self._warnings.append(message)

    @staticmethod
    def _empty_result(temperature_C: float, pressure_bar: float,
                      fO2_log: float) -> EquilibriumResult:
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
        )
