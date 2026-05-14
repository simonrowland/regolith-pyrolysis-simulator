"""
MAGEMin Silicate Phase Equilibrium Backend
===========================================

Adapter around MAGEMin (Riel et al.,
https://github.com/ComputationalThermodynamics/MAGEMin), an open-source
Gibbs free-energy minimiser for silicate phase equilibria.

MAGEMin is intended as a second-opinion silicate solver alongside
alphaMELTS:

    - Operates on the same 14-oxide MELTS basis used by the simulator
      (``simulator.state.OXIDE_SPECIES``), so shadow comparisons are
      straightforward.
    - Computes phase assemblage, modal abundances, liquid composition,
      and liquid fraction.
    - Does not compute vapor speciation — pair with VapoRock for the
      vapor-side.

License: see upstream MAGEMin repository (Riel et al.).  Cite:
    Riel N. et al., "MAGEMin, an efficient Gibbs energy minimizer
    for geodynamic modelling," G-cubed (paper).

Intended call site
------------------
This adapter is intended to run in **shadow mode** alongside alphaMELTS
so that liquidus and modal predictions can be cross-checked.  Parity
tolerance for the shadow comparison is:

    - liquidus temperature ±50 K
    - modal abundance ±2 wt%

A divergence outside that envelope is logged as a warning (the simulator
continues with the authoritative backend).

**Not yet wired into any active call site** — nothing instantiates
``MAGEMinBackend`` outside the test suite, and ``_get_equilibrium`` has
no shadow/multiplexer runner that would call it alongside alphaMELTS.
That runner is future work (see the chemistry-kernel carve-out goal and
``engines/magemin/`` for the kernel-shadow scaffold).

Capabilities
------------
``silicate_melt=True`` (authoritative once gated by the host
configuration).  All other capability flags are False — MAGEMin does
not handle vapor, salt, sulfide matte, or metal alloy phases.

The library is imported lazily inside ``initialize()`` — the simulator
must remain importable and the test suite must run without MAGEMin
installed.

Authority posture
-----------------
MAGEMin is **shadow / diagnostic** for ``SILICATE_LIQUIDUS`` and
``SILICATE_EQUILIBRIUM`` — when a shadow runner exists it is to run
alongside the authoritative alphaMELTS path, never instead of it
(binding spec §3 authority matrix).  ``ledger_account_policies()``
returns no ledger-authoritative policy and ``equilibrate()`` never
populates ``EquilibriumResult.ledger_transition``: MAGEMin has no
``AtomLedger`` authority and must not be granted any.

"Diagnostic" here does NOT mean "harmless if selected as the active
backend."  ``equilibrate()`` populates ``phase_masses_kg`` with a
post-equilibrium phase assemblage but leaves ``ledger_transition`` as
``None`` — and ``simulator/core.py::_get_equilibrium`` *rejects* exactly
that combination, raising ``RuntimeError`` ("backend returned
post-equilibrium phase material without an AtomLedger transition").  So
selecting ``MAGEMinBackend`` as the active melt backend fails closed by
design; it is not silently ignored.  The honest consumer for MAGEMin is
a dedicated shadow comparator (see ``engines/magemin/parity.py``) that
diff-checks its result against the authoritative engine without routing
it through ``_get_equilibrium`` as an authoritative phase solver.

MAGEMin consumes only the cleaned silicate melt — non-melt ledger
accounts (gas, metal, salt, sulfide, halide) are filtered out before the
library is called.
"""

from __future__ import annotations

import os
import shutil
import warnings
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from simulator.melt_backend.base import (
    DEFAULT_BACKEND_CAPABILITIES,
    EquilibriumResult,
    MeltBackend,
)
from simulator.state import OXIDE_SPECIES


# Cleaned silicate melt is the only ledger account MAGEMin may consume.
# Matches the alphamelts.py contract: every other account is filtered
# out before the library is called.
_MAGEMIN_MELT_ACCOUNT = 'process.cleaned_melt'


# MAGEMin operates on the same 14-oxide MELTS basis as alphaMELTS, so
# the simulator's ``OXIDE_SPECIES`` list is the canonical projection
# target.  Upstream MAGEMin spells the oxides in standard chemistry
# notation; the simulator already uses the same spellings so this is a
# 1:1 rename.
#
# TODO(magemin): once an actual MAGEMin install is available, verify the
# exact oxide-name spellings the upstream library expects (the C-level
# ``MAGEMin_init_db`` documents an oxide list; the Python wrappers may
# remap).  Today this adapter assumes 1:1 with ``OXIDE_SPECIES``.
_MAGEMIN_OXIDE_BASIS: Tuple[str, ...] = tuple(OXIDE_SPECIES)


class MAGEMinBackend(MeltBackend):
    """
    MAGEMin silicate phase equilibrium adapter.

    Configuration (all optional):
        binary_path:       explicit path to the MAGEMin binary.  If
                           omitted, the adapter probes ``engines/magemin``
                           and then ``PATH``.
        database:          MAGEMin internal database identifier (e.g.
                           ``'ig'`` for the igneous database).  Defaults
                           to ``'ig'``.
        python_bridge:     ``'ctypes'`` or ``'julia'``.  Defaults to
                           autodetect — the adapter prefers the
                           ``pymagemin`` Python package when present,
                           otherwise tries ``ctypes`` against the
                           shared library shipped with the binary, and
                           finally falls back to the ``julia`` bridge
                           if PyJulia is installed.
    """

    name = 'magemin'

    def __init__(self) -> None:
        self._available: bool = False
        self._config: Dict[str, Any] = {}
        self._database: str = 'ig'
        self._bridge: Optional[str] = None  # 'pymagemin' | 'ctypes' | 'julia'
        self._magemin_module: Optional[Any] = None
        self._binary_path: Optional[Path] = None
        self._warnings: List[str] = []
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # MeltBackend interface
    # ------------------------------------------------------------------

    def initialize(self, config: dict) -> bool:
        """
        Detect MAGEMin and stash configuration.

        Returns True only if **both** the MAGEMin binary AND its Python
        bridge are present.  Either missing leaves ``is_available()``
        False so the simulator can route around it.
        """
        self._available = False
        self._warnings = []
        self._last_error = None
        self._config = dict(config or {})

        self._database = str(self._config.get('database') or 'ig')

        binary_path = self._locate_binary(self._config.get('binary_path'))
        if binary_path is None:
            self._warn(
                'MAGEMin binary not found in engines/magemin or PATH; '
                'backend disabled'
            )
            return False
        self._binary_path = binary_path

        bridge, module = self._import_magemin_bridge(
            requested=self._config.get('python_bridge'))
        if bridge is None or module is None:
            self._warn(
                'MAGEMin Python bridge not available '
                '(tried pymagemin, ctypes, julia); backend disabled'
            )
            return False
        self._bridge = bridge
        self._magemin_module = module

        self._available = True
        return True

    def is_available(self) -> bool:
        return self._available

    def get_vapor_species(self) -> List[str]:
        # MAGEMin does not compute vapor speciation.  Returning an empty
        # list signals the simulator's router not to ask this backend
        # for vapor pressures.
        return []

    def capabilities(self) -> Dict[str, bool]:
        caps = dict(DEFAULT_BACKEND_CAPABILITIES)  # silicate_melt=True default
        # All other flags are False by default; reassert for clarity.
        caps['gas_volatiles'] = False
        caps['salt_phase'] = False
        caps['sulfide_matte'] = False
        caps['metal_alloy'] = False
        return caps

    def ledger_account_policies(self) -> tuple[Any, ...]:
        """
        MAGEMin requires no AtomLedger account policy.

        MAGEMin is shadow / diagnostic: it cross-checks alphaMELTS on
        liquidus and modal predictions but never emits a
        ledger-authoritative transition (binding spec §3; promotion is
        gated by ``MAGEMIN-SHADOW-PARITY`` and even then alphaMELTS keeps
        authority).  Returning an empty tuple keeps the layered-ABC
        contract explicit (same posture as
        ``AlphaMELTSBackend.ledger_account_policies``).
        """
        return ()

    def equilibrate(
        self,
        temperature_C: float,
        composition_kg: Optional[Dict[str, float]] = None,
        fO2_log: float = -9.0,
        pressure_bar: float = 1e-6,
        *,
        composition_mol: Optional[Dict[str, float]] = None,
        composition_mol_by_account: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
        species_formula_registry: Optional[Mapping[str, Any]] = None,
    ) -> EquilibriumResult:
        """
        Minimize Gibbs energy via MAGEMin.

        Conforms to the layered ``MeltBackend`` ABC: when
        ``composition_mol_by_account`` is supplied, only the
        ``process.cleaned_melt`` account is consumed — gas, metal, salt,
        sulfide and halide accounts are filtered out before the library
        is called (binding spec §7).  Populates ``phases_present``,
        ``phase_masses_kg``, ``liquid_fraction``, and
        ``liquid_composition_wt_pct``.

        ``EquilibriumResult.ledger_transition`` is left ``None``: MAGEMin
        holds no ``AtomLedger`` authority.  Because this method still
        populates ``phase_masses_kg`` with a phase assemblage, a result
        from this adapter is **not** safe to feed through
        ``simulator/core.py::_get_equilibrium`` as the active backend —
        that path rejects a populated phase result with no ledger
        transition and fails closed (see the module "Authority posture"
        note).  The result is only meaningful to a shadow comparator.

        On library error returns an empty result with a warning rather
        than raising.
        """
        result = EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
        )

        if not self._available or self._magemin_module is None:
            result.warnings.append('MAGEMin backend not initialized')
            return result

        if composition_mol_by_account is not None:
            melt_mol, dropped_accounts = self._melt_account_composition(
                composition_mol_by_account)
            for account in dropped_accounts:
                result.warnings.append(
                    'MAGEMin is silicate-only; ignored non-melt ledger '
                    f'account {account}'
                )
            # The cleaned-melt account is the canonical input; it
            # overrides any composition_mol passed alongside it.
            composition_mol = melt_mol

        comp_wt = self._project_to_oxide_wt_pct(
            composition_kg=composition_kg,
            composition_mol=composition_mol,
            species_formula_registry=species_formula_registry,
        )
        if not comp_wt:
            result.warnings.append(
                'MAGEMin received empty melt composition; returning empty '
                'equilibrium result'
            )
            return result

        try:
            raw = self._call_magemin(
                composition_wt_pct=comp_wt,
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
            )
        except Exception as exc:  # noqa: BLE001 - library-boundary catch
            message = f'MAGEMin equilibrate failed: {exc}'
            self._last_error = message
            result.warnings.append(message)
            return result

        # ledger_transition is left None: MAGEMin holds no AtomLedger
        # authority.  _populate_result fills phase_masses_kg, so a result
        # from this adapter fails closed if routed through
        # core.py::_get_equilibrium as the active backend (see the module
        # "Authority posture" note) — it is only valid for a shadow
        # comparator.
        self._populate_result(result, raw)
        return result

    @staticmethod
    def _melt_account_composition(
        composition_mol_by_account: Mapping[str, Mapping[str, float]],
    ) -> tuple[Dict[str, float], List[str]]:
        """
        Extract the cleaned-melt account; report every other account.

        Returns ``(melt_species_mol, dropped_account_names)``.  MAGEMin
        only consumes ``process.cleaned_melt``; any other account that
        carries positive material is reported back so the caller can
        record a warning (binding spec §7 — MAGEMin must not receive
        metal / sulfide / salt / halide accounts).
        """
        melt_mol: Dict[str, float] = {}
        for species, mol in (
            composition_mol_by_account.get(_MAGEMIN_MELT_ACCOUNT, {}) or {}
        ).items():
            value = float(mol)
            if value > 0.0:
                melt_mol[str(species)] = melt_mol.get(str(species), 0.0) + value

        dropped: List[str] = []
        for account, species_mol in composition_mol_by_account.items():
            if str(account) == _MAGEMIN_MELT_ACCOUNT:
                continue
            if any(float(mol) > 0.0 for mol in (species_mol or {}).values()):
                dropped.append(str(account))
        return melt_mol, sorted(dropped)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    @staticmethod
    def _locate_binary(explicit: Optional[Any]) -> Optional[Path]:
        """
        Find the MAGEMin binary.

        Order of preference:
            1. explicit path from config
            2. ``engines/magemin/MAGEMin`` relative to repo root
            3. ``MAGEMin`` on the system PATH
        """
        if explicit:
            path = Path(str(explicit)).expanduser()
            if path.exists():
                return path
            return None

        project_root = Path(__file__).resolve().parent.parent.parent
        candidates = [
            project_root / 'engines' / 'magemin' / 'MAGEMin',
            project_root / 'engines' / 'magemin' / 'bin' / 'MAGEMin',
        ]
        for candidate in candidates:
            if candidate.exists() and os.access(candidate, os.X_OK):
                return candidate

        which = shutil.which('MAGEMin')
        if which:
            return Path(which)

        return None

    def _import_magemin_bridge(
        self, *, requested: Optional[Any]
    ) -> Tuple[Optional[str], Optional[Any]]:
        """
        Lazy-import the Python bridge to MAGEMin.

        Returns ``(bridge_name, module)`` on success, ``(None, None)``
        if no usable bridge is installed.  Never raises.

        TODO(magemin): once the upstream Python entry point stabilises,
        collapse this to a single named import.  Today the published
        bridges are:
            - ``pymagemin``: third-party ctypes wrapper.
            - direct ``ctypes`` against ``libMAGEMin.so`` shipped with
              the binary.
            - ``julia`` bridge via ``MAGEMin.jl`` for PyJulia users.
        """
        normalised = (str(requested).lower().strip()
                      if requested is not None else None)

        if normalised in (None, 'pymagemin'):
            try:
                import pymagemin  # type: ignore[import-not-found]
                return 'pymagemin', pymagemin
            except Exception as exc:  # noqa: BLE001
                if normalised == 'pymagemin':
                    self._last_error = f'pymagemin import failed: {exc}'

        if normalised in (None, 'ctypes'):
            ctypes_module = self._try_ctypes_bridge()
            if ctypes_module is not None:
                return 'ctypes', ctypes_module
            if normalised == 'ctypes':
                self._last_error = (
                    'MAGEMin ctypes bridge unavailable '
                    '(libMAGEMin shared library not found)'
                )

        if normalised in (None, 'julia'):
            try:
                import julia  # type: ignore[import-not-found]
                # PyJulia is heavy — only flag as available if the
                # MAGEMin.jl package import succeeds.
                from julia import Main as JuliaMain  # noqa: F401
                JuliaMain.eval('import MAGEMin')  # may raise
                return 'julia', julia
            except Exception as exc:  # noqa: BLE001
                if normalised == 'julia':
                    self._last_error = f'julia bridge import failed: {exc}'

        warnings.warn(
            'MAGEMin not available; silicate-melt shadow backend disabled',
            stacklevel=2,
        )
        return None, None

    def _try_ctypes_bridge(self) -> Optional[Any]:
        """
        Look for ``libMAGEMin`` next to the binary and wrap it in
        ctypes.  Returns the loaded ``ctypes.CDLL`` or None.
        """
        if self._binary_path is None:
            return None

        binary_dir = self._binary_path.parent
        library_candidates = [
            binary_dir / 'libMAGEMin.so',
            binary_dir / 'libMAGEMin.dylib',
            binary_dir / 'MAGEMin.dll',
            binary_dir / 'lib' / 'libMAGEMin.so',
            binary_dir / 'lib' / 'libMAGEMin.dylib',
        ]
        for candidate in library_candidates:
            if candidate.exists():
                try:
                    import ctypes
                    return ctypes.CDLL(str(candidate))
                except OSError as exc:
                    self._last_error = (
                        f'libMAGEMin load failed at {candidate}: {exc}'
                    )
                    continue
        return None

    # ------------------------------------------------------------------
    # Library call
    # ------------------------------------------------------------------

    def _call_magemin(
        self,
        composition_wt_pct: Dict[str, float],
        temperature_C: float,
        pressure_bar: float,
        fO2_log: float,
    ) -> Any:
        """
        Invoke MAGEMin via whichever bridge ``initialize`` selected.

        TODO(magemin): the call shape below assumes a high-level
        ``pymagemin.minimize`` / ``MAGEMin.run`` entry point that
        consumes oxide wt%, temperature in C, pressure in kbar, and
        log fO2.  Confirm the exact signature once an install is
        available; the ``RuntimeError`` raised on missing entry points
        is the explicit fail signal the simulator already handles.
        """
        module = self._magemin_module
        temperature_K = temperature_C + 273.15
        pressure_kbar = pressure_bar / 1000.0  # MAGEMin convention

        if self._bridge == 'pymagemin':
            for name in ('minimize', 'run', 'equilibrium'):
                fn = getattr(module, name, None)
                if fn is None:
                    continue
                return fn(
                    composition=composition_wt_pct,
                    T_C=temperature_C,
                    T_K=temperature_K,
                    P_kbar=pressure_kbar,
                    log_fO2=fO2_log,
                    database=self._database,
                )

        if self._bridge == 'julia':
            JuliaMain = module.Main  # type: ignore[attr-defined]
            # The Julia bridge expects a dict of oxide wt% and returns
            # a struct.  This is a thin wrapper — full marshaling is
            # the responsibility of MAGEMin.jl.
            return JuliaMain.MAGEMin.single_point_minimization(
                composition_wt_pct,
                temperature_K,
                pressure_kbar,
                self._database,
                fO2_log,
            )

        if self._bridge == 'ctypes':
            # ctypes path is intentionally NOT auto-marshaled here —
            # the C API needs careful struct setup that depends on
            # the exact MAGEMin build.  Raise so the simulator falls
            # back to alphaMELTS rather than silently returning empty.
            raise RuntimeError(
                'MAGEMin ctypes bridge marshaling is not implemented; '
                'install pymagemin or configure python_bridge="julia"'
            )

        raise RuntimeError(
            f'MAGEMin bridge {self._bridge!r} has no recognised entry point')

    # ------------------------------------------------------------------
    # Composition projection / result parsing
    # ------------------------------------------------------------------

    def _project_to_oxide_wt_pct(
        self,
        *,
        composition_kg: Optional[Dict[str, float]],
        composition_mol: Optional[Dict[str, float]],
        species_formula_registry: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, float]:
        """
        Project the simulator's mol/kg melt composition to oxide wt%
        in the 14-oxide MELTS basis MAGEMin consumes.

        ``species_formula_registry`` is the simulator's formula registry
        (threaded through from the layered ABC) used for the mol -> kg
        projection; ``None`` falls back to the builtin formula table.
        """
        from simulator.accounting.formulas import resolve_species_formula

        if composition_mol is not None:
            kg_by_species: Dict[str, float] = {}
            for species, mol in composition_mol.items():
                value = float(mol)
                if value <= 0.0:
                    continue
                kg = value * resolve_species_formula(
                    species, species_formula_registry).molar_mass_kg_per_mol()
                kg_by_species[species] = kg
        else:
            kg_by_species = {
                species: float(value)
                for species, value in (composition_kg or {}).items()
                if float(value) > 0.0
            }

        filtered = {
            species: kg
            for species, kg in kg_by_species.items()
            if species in _MAGEMIN_OXIDE_BASIS
        }

        total = sum(filtered.values())
        if total <= 0:
            return {}

        return {
            species: kg / total * 100.0
            for species, kg in filtered.items()
        }

    def _populate_result(
        self, result: EquilibriumResult, raw: Any
    ) -> None:
        """
        Marshal MAGEMin output into ``EquilibriumResult``.

        Tolerates several common output shapes — dict, object with
        ``.phases`` / ``.ph_frac`` / ``.bulk_M`` attributes (the
        documented MAGEMin output struct), and to_dict-style wrappers.

        TODO(magemin): pin to the documented output struct once the
        upstream Python entry point is stable.  Today this is a
        best-effort projection.
        """
        if raw is None:
            return

        phases = self._extract_phases(raw)
        liquid_phase_names = ('liq', 'liquid', 'LIQUID', 'melt', 'Melt')

        total_mass_kg = 0.0
        liquid_mass_kg = 0.0
        liquid_composition: Dict[str, float] = {}

        for name, mass_kg, composition_wt_pct in phases:
            if mass_kg <= 0:
                continue
            result.phases_present.append(name)
            result.phase_masses_kg[name] = mass_kg
            if composition_wt_pct:
                result.phase_compositions[name] = composition_wt_pct
            total_mass_kg += mass_kg
            if name in liquid_phase_names or name.lower().startswith('liq'):
                liquid_mass_kg += mass_kg
                if composition_wt_pct:
                    liquid_composition = composition_wt_pct

        if total_mass_kg > 0:
            result.liquid_fraction = liquid_mass_kg / total_mass_kg
        if liquid_composition:
            result.liquid_composition_wt_pct = liquid_composition

    @staticmethod
    def _extract_phases(
        raw: Any,
    ) -> List[Tuple[str, float, Dict[str, float]]]:
        """
        Convert the upstream phase block into a list of
        ``(name, mass_kg, composition_wt_pct)`` triples.
        """
        if isinstance(raw, dict):
            phases_block = (
                raw.get('phases')
                or raw.get('ph')
                or raw.get('ph_frac')
                or {}
            )
        else:
            phases_block = (
                getattr(raw, 'phases', None)
                or getattr(raw, 'ph_frac', None)
                or {}
            )

        output: List[Tuple[str, float, Dict[str, float]]] = []
        if isinstance(phases_block, dict):
            for name, state in phases_block.items():
                mass_kg = MAGEMinBackend._extract_mass_kg(state)
                composition = MAGEMinBackend._extract_phase_composition(state)
                output.append((str(name), mass_kg, composition))
        return output

    @staticmethod
    def _extract_mass_kg(state: Any) -> float:
        if isinstance(state, (int, float)):
            return float(state)
        if isinstance(state, dict):
            for key in ('mass_kg', 'mass', 'm', 'amount_kg'):
                if key in state:
                    try:
                        return float(state[key])
                    except (TypeError, ValueError):
                        continue
        for attr in ('mass_kg', 'mass', 'm', 'amount_kg'):
            value = getattr(state, attr, None)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return 0.0

    @staticmethod
    def _extract_phase_composition(state: Any) -> Dict[str, float]:
        if isinstance(state, dict):
            comp = (
                state.get('composition_wt_pct')
                or state.get('composition')
                or state.get('comp')
            )
        else:
            comp = (
                getattr(state, 'composition_wt_pct', None)
                or getattr(state, 'composition', None)
                or getattr(state, 'comp', None)
            )
        if not isinstance(comp, dict):
            return {}
        out: Dict[str, float] = {}
        for species, value in comp.items():
            try:
                v = float(value)
            except (TypeError, ValueError):
                continue
            if v > 0:
                out[str(species)] = v
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _warn(self, message: str) -> None:
        if message not in self._warnings:
            self._warnings.append(message)
