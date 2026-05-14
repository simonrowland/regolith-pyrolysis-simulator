"""
VapoRock Vapor-Melt Equilibrium Backend
========================================

Adapter around VapoRock (Wolfe et al., https://github.com/cwolfe/VapoRock)
for equilibrium vapor speciation over silicate melts.

VapoRock combines the MELTS thermodynamic model with JANAF tables to
compute partial pressures for ~34 vapor species in the
Si-Mg-Fe-Al-Ca-Na-K-Ti-Cr-O system over silicate melts.  It is the
preferred vapor-side source when alphaMELTS / MELTS is the chosen
silicate engine because it consumes the same activity model and so
produces internally consistent γ_i × x_i × P_pure_i fluxes.

License: see upstream VapoRock repository (Wolfe et al.).  Cite:
    Wolfe C. A. et al., "VapoRock: A vapor-melt equilibrium model
    for silicate vapor speciation over magma oceans," (paper).

Intended call sites
-------------------
This adapter is intended to shadow / replace the vapor-pressure path in
``simulator/core.py::_calculate_evaporation`` once the melt-backend
multiplexer routes vapor-side queries to a capability holder.  See also
``AlphaMELTSBackend._get_vaporock_pressures`` which is the existing
in-line user of the same library — that path remains for backward
compatibility; this adapter exposes VapoRock as a first-class
``MeltBackend`` so it can be configured independently.

Capabilities
------------
VapoRock is vapor-side only — it does not solve the silicate phase
assemblage itself, it consumes one.  ``capabilities()`` therefore
reports ``silicate_melt=False`` and exposes the extra capability key
``vapor_melt_equilibrium=True`` so the simulator's backend router can
recognise this adapter as a vapor-pressure provider rather than a
melt-phase solver.

The library is imported lazily inside ``initialize()`` — the simulator
must remain importable and the test suite must run without VapoRock
installed.
"""

from __future__ import annotations

import warnings
from typing import Any, Dict, List, Optional

from simulator.melt_backend.base import (
    DEFAULT_BACKEND_CAPABILITIES,
    EquilibriumResult,
    MeltBackend,
)
from simulator.state import OXIDE_SPECIES


# VapoRock consumes the same oxide basis as MELTS / alphaMELTS.  The
# 14-oxide simulator basis is a strict subset; project 1:1 by name and
# drop any oxide VapoRock does not declare.  If the upstream library
# extends its basis, this map is the only place to update.
#
# TODO(vaporock): verify the exact oxide-name spellings expected by the
# installed VapoRock build (some forks use 'Al2O3' vs 'Al₂O₃', etc.)
# and confirm whether P2O5 / NiO / CoO are accepted.  If they are not,
# they must be stripped before the call.
_VAPOROCK_OXIDE_BASIS = tuple(OXIDE_SPECIES)


class VapoRockBackend(MeltBackend):
    """
    VapoRock vapor-melt equilibrium adapter.

    The backend operates on oxide wt% composition + temperature +
    pressure + fO2 and returns vapor partial pressures in Pa.  It does
    not populate ``phases_present`` because VapoRock consumes a melt
    state rather than producing one.

    Configuration (all optional):
        database_path:     filesystem path to a custom VapoRock thermo
                           database, if the installed build supports it.
        temperature_units: 'C' (default) or 'K'.
        pressure_units:    'bar' (default) or 'Pa'.
    """

    name = 'vaporock'

    def __init__(self) -> None:
        self._available: bool = False
        self._vaporock: Optional[Any] = None
        self._config: Dict[str, Any] = {}
        self._database_path: Optional[str] = None
        self._temperature_units: str = 'C'
        self._pressure_units: str = 'bar'
        self._warnings: List[str] = []
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # MeltBackend interface
    # ------------------------------------------------------------------

    def initialize(self, config: dict) -> bool:
        """
        Lazy-import VapoRock and stash configuration.

        Returns True only if the upstream library imports cleanly.
        Never raises — a missing library is a normal "not available"
        outcome.
        """
        self._available = False
        self._warnings = []
        self._last_error = None
        self._config = dict(config or {})

        self._database_path = self._config.get('database_path')
        temperature_units = str(
            self._config.get('temperature_units') or 'C').strip()
        if temperature_units not in ('C', 'K'):
            self._last_error = (
                f'VapoRock temperature_units {temperature_units!r} not '
                "supported; use 'C' or 'K'"
            )
            self._warnings.append(self._last_error)
            return False
        self._temperature_units = temperature_units

        pressure_units = str(
            self._config.get('pressure_units') or 'bar').strip()
        if pressure_units not in ('bar', 'Pa'):
            self._last_error = (
                f'VapoRock pressure_units {pressure_units!r} not '
                "supported; use 'bar' or 'Pa'"
            )
            self._warnings.append(self._last_error)
            return False
        self._pressure_units = pressure_units

        module = self._import_vaporock()
        if module is None:
            return False

        self._vaporock = module
        self._available = True
        return True

    def is_available(self) -> bool:
        return self._available

    def get_vapor_species(self) -> List[str]:
        # Reflect the 34-species VapoRock vapor model.  This list must
        # stay in sync with whatever the installed library actually
        # returns; the simulator filters on availability anyway.
        return [
            'Na', 'K', 'Fe', 'Mg', 'Ca', 'Si', 'Al', 'Ti', 'Cr', 'Mn',
            'SiO', 'FeO', 'MgO', 'CaO', 'AlO', 'TiO', 'NaO', 'KO',
            'CrO', 'MnO',
            'SiO2_gas', 'Al2O', 'Fe2O3_gas', 'Ti2O3_gas',
            'O2', 'O',
            'Na2', 'K2', 'NaOH', 'KOH',
            'Si2', 'Mg2', 'Ca2',
        ]

    def capabilities(self) -> Dict[str, bool]:
        """
        VapoRock is vapor-side only.

        Returns the canonical capability dict with ``silicate_melt`` and
        all multi-phase flags False, ``gas_volatiles`` True, plus the
        extension key ``vapor_melt_equilibrium`` True so the router can
        identify this adapter as a vapor-pressure provider.
        """
        caps: Dict[str, bool] = {key: False for key in DEFAULT_BACKEND_CAPABILITIES}
        caps['gas_volatiles'] = True
        caps['vapor_melt_equilibrium'] = True
        return caps

    def equilibrate(
        self,
        temperature_C: float,
        composition_kg: Optional[Dict[str, float]] = None,
        fO2_log: float = -9.0,
        pressure_bar: float = 1e-6,
        *,
        composition_mol: Optional[Dict[str, float]] = None,
    ) -> EquilibriumResult:
        """
        Call VapoRock for vapor-melt equilibrium.

        The melt composition is projected to oxide wt% in the
        14-oxide simulator basis (which is a strict subset of the
        MELTS basis VapoRock expects).

        On any library error the method returns an empty
        ``EquilibriumResult`` and appends a one-line warning rather
        than raising — the simulator can then degrade to its
        Antoine-equation stub path.
        """
        result = EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
        )

        if not self._available or self._vaporock is None:
            result.warnings.append('VapoRock backend not initialized')
            return result

        comp_wt = self._project_to_oxide_wt_pct(
            composition_kg=composition_kg,
            composition_mol=composition_mol,
        )
        if not comp_wt:
            result.warnings.append(
                'VapoRock received empty melt composition; returning empty '
                'equilibrium result'
            )
            return result

        temperature_value = (
            temperature_C + 273.15
            if self._temperature_units == 'K'
            else temperature_C
        )
        pressure_value = (
            pressure_bar * 1e5
            if self._pressure_units == 'Pa'
            else pressure_bar
        )

        try:
            raw = self._call_vaporock(
                composition_wt_pct=comp_wt,
                temperature=temperature_value,
                pressure=pressure_value,
                fO2_log=fO2_log,
            )
        except Exception as exc:  # noqa: BLE001 - library-boundary catch
            message = f'VapoRock equilibrate failed: {exc}'
            self._last_error = message
            result.warnings.append(message)
            return result

        result.vapor_pressures_Pa = self._normalize_vapor_pressures(raw)
        # phases_present is intentionally left empty — VapoRock is
        # vapor-side only and does not return a silicate-phase
        # assemblage.
        return result

    # ------------------------------------------------------------------
    # Library boundary
    # ------------------------------------------------------------------

    def _import_vaporock(self) -> Optional[Any]:
        """
        Lazy import of the upstream VapoRock library.

        Returns None if the import fails (the caller treats this as
        "backend not available").  Never raises.
        """
        try:
            import VapoRock  # type: ignore[import-not-found]
            return VapoRock
        except Exception as exc:  # noqa: BLE001 - import-boundary catch
            self._last_error = f'VapoRock import failed: {exc}'
            # Single-line stderr-style notification, but routed through
            # warnings so test harnesses can suppress it.
            warnings.warn(
                'VapoRock not available; vapor-melt backend disabled',
                stacklevel=2,
            )
            return None

    def _call_vaporock(
        self,
        composition_wt_pct: Dict[str, float],
        temperature: float,
        pressure: float,
        fO2_log: float,
    ) -> Dict[str, float]:
        """
        Invoke the upstream VapoRock equilibrium entry point.

        The exact symbol exposed by the upstream library has varied
        across releases — the function probes the common names in
        order of preference.  Add new candidates here rather than
        changing the call shape in ``equilibrate``.

        TODO(vaporock): pin to a single documented entry point once
        the upstream package has a stable Python API.  Today the
        published interface is loosely documented in the README and
        these candidates are the union observed across the 0.1.x
        line.
        """
        module = self._vaporock
        candidate_names = (
            'calc_vapor_pressures',
            'calc_vapor',
            'equilibrium_vapor',
            'vapor_equilibrium',
        )
        last_attr_error: Optional[Exception] = None
        for name in candidate_names:
            fn = getattr(module, name, None)
            if fn is None:
                continue
            try:
                return fn(
                    composition=composition_wt_pct,
                    T_C=temperature if self._temperature_units == 'C' else None,
                    T_K=temperature if self._temperature_units == 'K' else None,
                    P_bar=pressure if self._pressure_units == 'bar' else None,
                    P_Pa=pressure if self._pressure_units == 'Pa' else None,
                    log_fO2=fO2_log,
                )
            except TypeError as exc:
                # Older builds use positional / shorter signatures.
                # Fall back to a minimal call before declaring failure.
                last_attr_error = exc
                try:
                    return fn(
                        composition_wt_pct,
                        temperature,
                        fO2_log,
                    )
                except Exception as inner_exc:  # noqa: BLE001
                    last_attr_error = inner_exc
                    continue

        raise RuntimeError(
            'VapoRock library does not expose a recognised equilibrium '
            f'entry point (tried: {", ".join(candidate_names)})'
            + (f'; last error: {last_attr_error}' if last_attr_error else '')
        )

    # ------------------------------------------------------------------
    # Composition / result projection
    # ------------------------------------------------------------------

    def _project_to_oxide_wt_pct(
        self,
        *,
        composition_kg: Optional[Dict[str, float]],
        composition_mol: Optional[Dict[str, float]],
    ) -> Dict[str, float]:
        """
        Project the simulator's mol/kg melt composition to the oxide
        wt% basis VapoRock expects.

        VapoRock's basis is identical to MELTS for the oxides shared
        with the simulator (the 14-oxide list in ``simulator.state``),
        so this is a straight rename + normalisation.  Any species not
        in the VapoRock basis is dropped with a warning.

        TODO(vaporock): once the installed VapoRock build is known,
        confirm whether P2O5 / NiO / CoO are accepted; if not, the
        drops are silent today.
        """
        from simulator.accounting.formulas import resolve_species_formula

        if composition_mol is not None:
            kg_by_species: Dict[str, float] = {}
            for species, mol in composition_mol.items():
                value = float(mol)
                if value <= 0.0:
                    continue
                kg = value * resolve_species_formula(
                    species).molar_mass_kg_per_mol()
                kg_by_species[species] = kg
        else:
            kg_by_species = {
                species: float(value)
                for species, value in (composition_kg or {}).items()
                if float(value) > 0.0
            }

        # Filter to VapoRock's oxide basis.
        filtered = {
            species: kg
            for species, kg in kg_by_species.items()
            if species in _VAPOROCK_OXIDE_BASIS
        }

        total = sum(filtered.values())
        if total <= 0:
            return {}

        return {
            species: kg / total * 100.0
            for species, kg in filtered.items()
        }

    def _normalize_vapor_pressures(
        self, raw: Any
    ) -> Dict[str, float]:
        """
        Convert the upstream VapoRock result into a ``species → Pa``
        dict.

        The upstream API has historically returned ``{species: P_bar}``
        but newer builds may emit Pa directly.  The simulator's contract
        is Pa, so we infer the unit and scale.
        """
        if raw is None:
            return {}

        # Some upstream builds wrap the dict in an object with a
        # ``.pressures`` attribute or expose ``.to_dict()``.
        if not isinstance(raw, dict):
            for attr in ('pressures', 'partial_pressures', 'vapor_pressures'):
                value = getattr(raw, attr, None)
                if isinstance(value, dict):
                    raw = value
                    break
            else:
                to_dict = getattr(raw, 'to_dict', None)
                if callable(to_dict):
                    try:
                        raw = to_dict()
                    except Exception:  # noqa: BLE001
                        return {}
                else:
                    return {}

        if not isinstance(raw, dict):
            return {}

        # Heuristic: if the largest pressure is below 1e3 we assume bar
        # (typical vapor pressures < 1 bar) and scale to Pa.  If values
        # already look like Pa (max ≥ 1e3) we leave them.
        try:
            float_values = [float(v) for v in raw.values()]
        except (TypeError, ValueError):
            return {}

        if not float_values:
            return {}

        scale = 1e5 if max(float_values) < 1e3 else 1.0
        return {
            str(species): float(value) * scale
            for species, value in raw.items()
            if float(value) > 0.0
        }
