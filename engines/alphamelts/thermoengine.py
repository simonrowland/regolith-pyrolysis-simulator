"""ThermoEngine transport for the AlphaMELTS provider.

This module is a transport selector only.  ThermoEngine stays behind the
existing :class:`AlphaMELTSProvider`; it does not own an intent and it never
emits a ledger transition.
"""

from __future__ import annotations

import math
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from simulator.accounting.formulas import resolve_species_formula
from simulator.engine_local_config import (
    cache_version_for,
    setup_thermoengine_dylib_path,
    warn_legacy_once,
)


ActivityConverter = Callable[[float, float, float], float]


_MODEL_TO_THERMOENGINE = {
    'MELTSv1.0.2': ('1.0.2', 'v1.0'),
    'MELTSv1.1.0': ('1.1.0', 'v1.1'),
    'MELTSv1.2.0': ('1.2.0', 'v1.2'),
    'pMELTS': ('5.6.1', 'pMELTS'),
}


@dataclass(frozen=True)
class ThermoEnginePayload:
    """Transport payload ready for ``AlphaMELTSBackend`` emission."""

    phases_present: tuple[str, ...] = ()
    phase_masses_kg: Mapping[str, float] = field(default_factory=dict)
    liquid_fraction: float = 0.0
    liquid_composition_wt_pct: Mapping[str, float] = field(default_factory=dict)
    activity_coefficients: Mapping[str, float] = field(default_factory=dict)
    fe_redox_split: Mapping[str, float] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


class ThermoEngineTransport:
    """ENKI ThermoEngine MELTS transport for one AlphaMELTS backend."""

    def __init__(
        self,
        *,
        model_name: str = 'MELTSv1.0.2',
        activity_converter: ActivityConverter,
    ) -> None:
        self._model_name = str(model_name or 'MELTSv1.0.2')
        self._activity_converter = activity_converter
        self._thermoengine = None
        self._equilibrate = None
        self._model = None
        self._chem = None
        self._liq_phase = None
        self._melts_version = '1.0.2'
        self._liq_model = 'v1.0'
        self.engine_version = 'thermoengine unavailable'
        self._health_cache: dict[str, tuple[bool, str]] = {}

    def initialize(self) -> bool:
        setup_thermoengine_dylib_path()
        import thermoengine
        from thermoengine import chem, equilibrate, model

        melts_version, liq_model = _MODEL_TO_THERMOENGINE.get(
            self._model_name,
            _MODEL_TO_THERMOENGINE['MELTSv1.0.2'],
        )
        database = model.Database(
            database='Berman',
            liq_mod=liq_model,
            calib=True,
        )
        liq_phase = database.get_phase('Liq')
        # Construct once at initialization so missing Objective-C/C payloads
        # fail before the adapter advertises thermoengine mode.
        equilibrate.MELTSmodel(version=melts_version)

        self._thermoengine = thermoengine
        self._equilibrate = equilibrate
        self._model = model
        self._chem = chem
        self._liq_phase = liq_phase
        self._melts_version = melts_version
        self._liq_model = liq_model
        config_version = cache_version_for('thermoengine')
        if config_version is not None:
            self.engine_version = config_version
        else:
            module_path = getattr(thermoengine, '__file__', 'unknown')
            self.engine_version = (
                f'thermoengine MELTS {melts_version} '
                f'(liq_mod {liq_model}; {module_path})'
            )
            warn_legacy_once(
                'thermoengine',
                'engines.local.toml absent; using legacy ThermoEngine '
                'path-based identity for cache comparison',
            )
        return True

    def health_check(self, *, timeout_s: float = 8.0) -> tuple[bool, str]:
        timeout = max(1.0, float(timeout_s))
        cache_key = f'{self._model_name}:{timeout:.3f}'
        cached = self._health_cache.get(cache_key)
        if cached is not None:
            return cached

        code = f"""
from engines.alphamelts.thermoengine import ThermoEngineTransport

def activity_from_mu(_mu, _mu0, _temperature_K):
    return 1.0

transport = ThermoEngineTransport(
    model_name={self._model_name!r},
    activity_converter=activity_from_mu,
)
transport.initialize()
payload = transport.equilibrate(
    temperature_C=1200.0,
    pressure_bar=1.0,
    comp_wt={{
        'SiO2': 49.0,
        'TiO2': 1.5,
        'Al2O3': 14.0,
        'FeO': 10.0,
        'Fe2O3': 1.0,
        'MgO': 9.0,
        'CaO': 11.0,
        'Na2O': 2.5,
        'K2O': 0.8,
        'Cr2O3': 0.2,
        'MnO': 0.2,
        'P2O5': 0.3,
    }},
)
if not payload.phases_present:
    raise RuntimeError('ThermoEngine smoke equilibrium returned no phases')
print('ok')
"""
        try:
            result = subprocess.run(
                [sys.executable, '-c', code],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            health = (
                False,
                f'ThermoEngine smoke equilibrium timed out after '
                f'{timeout:.1f}s',
            )
        except OSError as exc:
            health = (False, f'ThermoEngine smoke equilibrium failed: {exc}')
        else:
            if result.returncode == 0:
                health = (True, 'ThermoEngine smoke equilibrium completed')
            else:
                detail = (result.stderr or result.stdout or '').strip()
                if detail:
                    detail = detail.splitlines()[-1]
                health = (
                    False,
                    'ThermoEngine smoke equilibrium failed'
                    + (f': {detail}' if detail else ''),
                )
        self._health_cache[cache_key] = health
        return health

    def equilibrate(
        self,
        *,
        temperature_C: float,
        pressure_bar: float,
        comp_wt: Mapping[str, float],
        warnings: tuple[str, ...] = (),
    ) -> ThermoEnginePayload:
        if self._equilibrate is None or self._liq_phase is None:
            raise ImportError('ThermoEngine transport not initialized')

        melts = self._equilibrate.MELTSmodel(version=self._melts_version)
        oxide_names = tuple(str(name) for name in melts.get_oxide_names())
        bulk_wt = {
            oxide: float(comp_wt.get(oxide, 0.0) or 0.0)
            for oxide in oxide_names
            if float(comp_wt.get(oxide, 0.0) or 0.0) > 0.0
        }
        if not bulk_wt:
            raise ValueError('ThermoEngine composition has no MELTS oxides')

        melts.set_bulk_composition(bulk_wt)
        pressure_mpa = max(float(pressure_bar) / 10.0, 1.0e-7)
        runs = melts.equilibrate_tp(
            float(temperature_C),
            pressure_mpa,
            initialize=True,
        )
        if not runs:
            return ThermoEnginePayload(
                warnings=tuple(warnings) + (
                    'ThermoEngine returned no equilibration result',
                ),
            )

        status, _T_C, _P_MPa, root = runs[0]
        status_text = str(status)
        if 'success' not in status_text.lower():
            return ThermoEnginePayload(
                warnings=tuple(warnings) + (
                    f'ThermoEngine equilibrium status: {status_text}',
                ),
            )

        phases = tuple(str(phase) for phase in melts.get_list_of_phases_in_assemblage(root))
        phase_masses_kg = {
            phase: float(melts.get_mass_of_phase(root, phase)) / 1000.0
            for phase in phases
            if float(melts.get_mass_of_phase(root, phase)) > 0.0
        }
        total_mass_kg = sum(phase_masses_kg.values())
        liquid_phase = self._select_liquid_phase(phases)
        liquid_mass_kg = phase_masses_kg.get(liquid_phase or '', 0.0)
        liquid_fraction = (
            max(0.0, min(1.0, liquid_mass_kg / total_mass_kg))
            if total_mass_kg > 0.0 else 0.0
        )
        # Autoreview r4 P2 (2026-05-27): only emit a liquid composition
        # / activities / Fe-redox split when ThermoEngine actually
        # reports a liquid phase.  The prior code fell back to the
        # bulk-oxide composition whenever ``liquid_comp`` was empty,
        # which (a) fabricated a liquid composition for subsolidus or
        # fully crystallized assemblages, then (b) derived activities
        # and Fe redox from that fabrication, breaking the
        # liquid_fraction-driven freeze-gate diagnostic (callers could
        # not distinguish a real liquid from a fabricated one).  Now
        # the bulk-fallback only fires when a liquid phase IS reported
        # but the composition API returned an incomplete payload, and
        # the situation surfaces as a warning so it is auditable.
        extra_warnings: tuple[str, ...] = ()
        if liquid_phase:
            liquid_comp = self._finite_mapping(
                melts.get_composition_of_phase(root, liquid_phase, 'oxide_wt'))
            if not liquid_comp:
                liquid_comp = dict(bulk_wt)
                extra_warnings = (
                    'ThermoEngine reported liquid phase '
                    f'{liquid_phase!r} but composition_of_phase returned '
                    'an empty payload; falling back to bulk composition.',
                )
            liquid_components = self._finite_mapping(
                melts.get_composition_of_phase(root, liquid_phase, 'component'))
        else:
            # No liquid phase: leave composition + activities + Fe redox
            # empty.  Subsolidus / fully crystallized states surface as
            # ``liquid_fraction=0`` with an empty ``liquid_composition_wt_pct``,
            # which is exactly what downstream consumers (freeze-gate,
            # evaporation flux gate) need to refuse evaporation cleanly.
            liquid_comp = {}
            liquid_components = {}

        if liquid_phase and liquid_comp:
            activities = self._activities_from_chemical_potentials(
                temperature_C=float(temperature_C),
                pressure_bar=float(pressure_bar),
                component_mole_fraction=liquid_components,
                comp_wt=liquid_comp,
            )
            fe_redox_split = self._fe_redox_split(liquid_comp)
        else:
            activities = {}
            fe_redox_split = {}

        return ThermoEnginePayload(
            phases_present=phases,
            phase_masses_kg=phase_masses_kg,
            liquid_fraction=liquid_fraction,
            liquid_composition_wt_pct=liquid_comp,
            activity_coefficients=activities,
            fe_redox_split=fe_redox_split,
            warnings=tuple(warnings) + extra_warnings + (
                f'ThermoEngine status: {status_text}',
            ),
        )

    def _activities_from_chemical_potentials(
        self,
        *,
        temperature_C: float,
        pressure_bar: float,
        component_mole_fraction: Mapping[str, float],
        comp_wt: Mapping[str, float],
    ) -> dict[str, float]:
        liq_phase = self._liq_phase
        if liq_phase is None:
            return {}

        endmember_names = tuple(str(name) for name in liq_phase.endmember_names)
        mol = [
            float(component_mole_fraction.get(name, 0.0) or 0.0)
            for name in endmember_names
        ]
        if sum(mol) <= 0.0:
            mol = self._endmember_moles_from_wt(comp_wt, endmember_names)
        total = sum(value for value in mol if value > 0.0)
        if total <= 0.0:
            return {}
        mol = [max(0.0, value) / total for value in mol]

        import numpy as np

        T_K = float(temperature_C) + 273.15
        P_bar = max(float(pressure_bar), 1.0e-6)
        mu_values = np.asarray(
            liq_phase.chem_potential(T_K, P_bar, mol=[mol]),
            dtype=float,
        ).reshape(-1)
        activities: dict[str, float] = {}
        for idx, name in enumerate(endmember_names):
            if idx >= len(mu_values):
                continue
            pure = [0.0 for _ in endmember_names]
            pure[idx] = 1.0
            mu0_values = np.asarray(
                liq_phase.gibbs_energy(T_K, P_bar, mol=[pure]),
                dtype=float,
            ).reshape(-1)
            if len(mu0_values) == 0:
                continue
            try:
                activity = self._activity_converter(
                    float(mu_values[idx]),
                    float(mu0_values[0]),
                    T_K,
                )
            except (OverflowError, ValueError):
                continue
            if activity > 0.0 and math.isfinite(activity):
                activities[name] = activity
        return activities

    def _endmember_moles_from_wt(
        self,
        comp_wt: Mapping[str, float],
        endmember_names: tuple[str, ...],
    ) -> list[float]:
        mol: list[float] = []
        for name in endmember_names:
            wt = float(comp_wt.get(name, 0.0) or 0.0)
            if wt <= 0.0:
                mol.append(0.0)
                continue
            try:
                molar_mass_g_per_mol = (
                    resolve_species_formula(
                        name,
                        None,
                    ).molar_mass_kg_per_mol()
                    * 1000.0
                )
            except Exception:
                mol.append(0.0)
                continue
            mol.append(wt / molar_mass_g_per_mol if molar_mass_g_per_mol > 0.0 else 0.0)
        return mol

    def _fe_redox_split(self, liquid_comp: Mapping[str, float]) -> dict[str, float]:
        feo_wt = float(liquid_comp.get('FeO', 0.0) or 0.0)
        fe2o3_wt = float(liquid_comp.get('Fe2O3', 0.0) or 0.0)
        feo_mol = self._oxide_mol('FeO', feo_wt)
        fe2o3_mol = self._oxide_mol('Fe2O3', fe2o3_wt)
        total_fe_mol = feo_mol + 2.0 * fe2o3_mol
        split = {
            'FeO_wt_pct': feo_wt,
            'Fe2O3_wt_pct': fe2o3_wt,
        }
        if total_fe_mol > 0.0:
            split['Fe3Fet_Liq'] = (2.0 * fe2o3_mol) / total_fe_mol
        return split

    def _oxide_mol(self, oxide: str, wt: float) -> float:
        if wt <= 0.0:
            return 0.0
        try:
            molar_mass_g_per_mol = (
                resolve_species_formula(oxide, None).molar_mass_kg_per_mol()
                * 1000.0
            )
        except Exception:
            return 0.0
        return wt / molar_mass_g_per_mol if molar_mass_g_per_mol > 0.0 else 0.0

    def _select_liquid_phase(self, phases: tuple[str, ...]) -> Optional[str]:
        for phase in phases:
            if phase.lower() == 'liquid':
                return phase
        for phase in phases:
            if phase.lower().startswith('liquid'):
                return phase
        return None

    def _finite_mapping(self, values: Mapping[str, Any]) -> dict[str, float]:
        result: dict[str, float] = {}
        for key, value in dict(values or {}).items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                result[str(key)] = number
        return result


def equilibrate_via_thermoengine(
    backend: Any,
    *,
    temperature_C: float,
    pressure_bar: float,
    fO2_log: float,
    composition_mol_by_account: Mapping[str, Mapping[str, float]],
    species_formula_registry: Mapping[str, Any],
) -> Any:
    """Run AlphaMELTS through the ThermoEngine transport mode."""
    mode = getattr(backend, '_mode', None)
    if mode != 'thermoengine':
        raise RuntimeError(
            'equilibrate_via_thermoengine requires backend._mode == '
            f'"thermoengine"; got {mode!r}. Provider must dispatch another '
            'transport instead.'
        )
    return backend.equilibrate(
        temperature_C=float(temperature_C),
        pressure_bar=float(pressure_bar),
        fO2_log=float(fO2_log),
        composition_mol_by_account=composition_mol_by_account,
        species_formula_registry=species_formula_registry,
    )


def thermoengine_available(backend: Any) -> bool:
    """True when the backend has initialized the ThermoEngine path."""
    return getattr(backend, '_mode', None) == 'thermoengine'


__all__ = (
    'ThermoEnginePayload',
    'ThermoEngineTransport',
    'equilibrate_via_thermoengine',
    'thermoengine_available',
)
