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

Python bridge
-------------
MAGEMin has **no pure-PyPI package** — the clone ships zero Python
files, no ``setup.py``, no ``pyproject.toml``.  Its primary interface is
Julia (``MAGEMin_C.jl``); from Python it is reached either through that
Julia bridge or by driving the compiled ``MAGEMin`` binary over a
subprocess.  This adapter's supported, default path is **subprocess**:
``initialize()`` locates the compiled binary (sibling clone
``../MAGEMin/MAGEMin`` or ``engines/magemin/{,bin/}MAGEMin``) and
``_call_magemin`` invokes it with ``--Verb=0`` single-point arguments,
parsing the compact ``Phase :`` / ``Mode :`` stdout block.  The optional
``pymagemin`` / ``julia`` bridges are still probed first if a caller has
them installed, but the binary is the canonical route.  See
``pyproject.toml`` ``[magemin]`` for the build path.

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
import subprocess
import warnings
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from simulator.melt_backend.base import (
    DEFAULT_BACKEND_CAPABILITIES,
    EquilibriumResult,
    MeltBackend,
    MeltCompositionError,
    liquid_fraction_from_phase_masses,
    project_melt_to_oxide_wt_pct,
    split_cleaned_melt_account,
)
from simulator.melt_backend.liquidus import (
    LiquidusSolidusResult,
    find_liquidus_solidus_by_fraction,
)
from simulator.state import OXIDE_SPECIES


# MAGEMin operates on the same 14-oxide MELTS basis as alphaMELTS, so
# the simulator's ``OXIDE_SPECIES`` list is the canonical projection
# target.  Upstream MAGEMin spells the oxides in standard chemistry
# notation; the simulator already uses the same spellings so this is a
# 1:1 rename; pass ``OXIDE_SPECIES`` directly to the projection helper
# rather than rebinding a private alias that hides the identity.
#
# TODO(magemin): once an actual MAGEMin install is available, verify the
# exact oxide-name spellings the upstream library expects (the C-level
# ``MAGEMin_init_db`` documents an oxide list; the Python wrappers may
# remap).  Today this adapter assumes 1:1 with ``OXIDE_SPECIES``.


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
        python_bridge:     ``'subprocess'``, ``'pymagemin'``, ``'ctypes'``
                           or ``'julia'``.  Defaults to autodetect — the
                           adapter prefers the ``pymagemin`` Python
                           package when present, then ``ctypes`` against
                           the shared library shipped with the binary,
                           then the ``julia`` bridge if PyJulia is
                           installed, and finally falls back to driving
                           the compiled ``MAGEMin`` binary over a
                           subprocess.  Subprocess is the supported
                           default because MAGEMin has no PyPI package.
    """

    name = 'magemin'

    def __init__(self) -> None:
        self._available: bool = False
        self._config: Dict[str, Any] = {}
        self._database: str = 'ig'
        # 'subprocess' | 'pymagemin' | 'ctypes' | 'julia'
        self._bridge: Optional[str] = None
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

        Returns True if the compiled MAGEMin binary is present.  The
        binary is always usable through the ``subprocess`` bridge; the
        ``pymagemin`` / ``ctypes`` / ``julia`` bridges are preferred when
        a caller has them installed but are not required.  A missing
        binary leaves ``is_available()`` False so the simulator can route
        around it.
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
        if bridge is None:
            # Should not happen: _import_magemin_bridge always returns the
            # subprocess bridge when a binary was located.  Guard anyway.
            self._warn(
                'MAGEMin binary located but no usable bridge resolved; '
                'backend disabled'
            )
            return False
        self._bridge = bridge
        self._magemin_module = module  # None for the subprocess bridge

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
        # The subprocess bridge has no Python module (the binary is the
        # bridge); the other bridges do.  Either way the backend must be
        # available with a resolved bridge.
        if not self._available or self._bridge is None:
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status='unavailable',
                warnings=['MAGEMin backend not initialized'],
            )

        prior_warnings: List[str] = []
        if composition_mol_by_account is not None:
            melt_mol, dropped_accounts = split_cleaned_melt_account(
                composition_mol_by_account)
            for account in dropped_accounts:
                prior_warnings.append(
                    'MAGEMin is silicate-only; ignored non-melt ledger '
                    f'account {account}'
                )
            # The cleaned-melt account is the canonical input; it
            # overrides any composition_mol passed alongside it.
            composition_mol = melt_mol

        comp_wt = project_melt_to_oxide_wt_pct(
            composition_kg=composition_kg,
            composition_mol=composition_mol,
            oxide_basis=tuple(OXIDE_SPECIES),
            species_formula_registry=species_formula_registry,
        )
        if not comp_wt:
            # No oxide species in MAGEMin's basis after the account split.
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status='out_of_domain',
                warnings=[
                    *prior_warnings,
                    'MAGEMin received empty melt composition; returning empty '
                    'equilibrium result',
                ],
            )

        try:
            raw = self._call_magemin(
                composition_wt_pct=comp_wt,
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
            )
        except Exception as exc:  # noqa: BLE001 - library-boundary catch
            # MAGEMin is present but the minimisation did not produce a
            # usable result.
            message = f'MAGEMin equilibrate failed: {exc}'
            self._last_error = message
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status='not_converged',
                warnings=[*prior_warnings, message],
            )

        # ledger_transition is left None: MAGEMin holds no AtomLedger
        # authority.  _populate_result fills phase_masses_kg, so a result
        # from this adapter fails closed if routed through
        # core.py::_get_equilibrium as the active backend (see the module
        # "Authority posture" note) — it is only valid for a shadow
        # comparator.
        all_warnings = list(prior_warnings)
        if isinstance(raw, dict):
            buffer_warnings = raw.get('buffer_warnings') or []
            for line in buffer_warnings:
                if line not in all_warnings:
                    all_warnings.append(line)
        (
            phases_present,
            phase_masses_kg,
            phase_compositions,
            liquid_fraction,
            liquid_composition_wt_pct,
        ) = self._phase_assemblage_payload(raw)
        result = EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            phases_present=phases_present,
            phase_masses_kg=phase_masses_kg,
            phase_compositions=phase_compositions,
            liquid_fraction=liquid_fraction,
            liquid_composition_wt_pct=liquid_composition_wt_pct,
            status='ok',
            warnings=all_warnings,
        )
        return result

    def find_liquidus_solidus(
        self,
        composition_kg: Optional[Dict[str, float]] = None,
        fO2_log: float = -9.0,
        pressure_bar: float = 1e-6,
        *,
        composition_mol: Optional[Dict[str, float]] = None,
        composition_mol_by_account: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
        species_formula_registry: Optional[Mapping[str, Any]] = None,
        min_T_C: float = 400.0,
        max_T_C: float = 2200.0,
        scan_step_C: float = 50.0,
        tolerance_C: float = 2.0,
    ) -> LiquidusSolidusResult:
        """Find solidus/liquidus by repeated MAGEMin single-point frac_M."""
        if not self._available or self._bridge is None:
            return LiquidusSolidusResult(
                status='unavailable',
                warnings=('MAGEMin backend not initialized',),
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
                if warning.startswith('MAGEMin: translated absolute fO2_log'):
                    continue
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
        warnings_out = [*result.warnings, *sample_warnings[:6]]
        return LiquidusSolidusResult(
            liquidus_T_C=result.liquidus_T_C,
            liquidus_T_K=result.liquidus_T_K,
            solidus_T_C=result.solidus_T_C,
            liquid_fraction=result.liquid_fraction,
            status=result.status,
            warnings=tuple(warnings_out),
            samples=result.samples,
            iterations=result.iterations,
        )

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    @staticmethod
    def _locate_binary(explicit: Optional[Any]) -> Optional[Path]:
        """
        Find the compiled MAGEMin binary.

        Order of preference:
            1. explicit path from config (``binary_path``)
            2. ``engines/magemin/{,bin/}MAGEMin`` relative to repo root
            3. ``../MAGEMin/MAGEMin`` — a sibling clone built in place
               (the documented build location, see ``pyproject.toml``
               ``[magemin]``)
            4. ``MAGEMin`` on the system PATH
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
            # Sibling clone built in place — see pyproject.toml [magemin].
            project_root.parent / 'MAGEMin' / 'MAGEMin',
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
        Resolve the bridge to MAGEMin.

        Returns ``(bridge_name, module)``.  ``module`` is the imported
        Python module for the ``pymagemin`` / ``ctypes`` / ``julia``
        bridges, and ``None`` for the ``subprocess`` bridge (the compiled
        binary is the bridge — there is nothing to import).  Returns
        ``('subprocess', None)`` as the final fallback whenever a binary
        was located, so a built MAGEMin is always usable even with no
        Python package installed.  Returns ``(None, None)`` only if no
        bridge at all can be resolved.  Never raises.

        MAGEMin has no PyPI package, so the published bridges are:
            - ``subprocess``: drive the compiled ``MAGEMin`` binary
              directly (the supported default).
            - ``pymagemin``: third-party ctypes wrapper (rare).
            - direct ``ctypes`` against ``libMAGEMin.so``/``.dylib``
              shipped with the binary.
            - ``julia`` bridge via ``MAGEMin_C.jl`` for PyJulia /
              juliacall users.

        Autodetect order is ``pymagemin -> julia -> subprocess``.
        ``ctypes`` is deliberately NOT auto-selected: its struct
        marshaling is unimplemented (``_call_magemin`` raises for it), so
        auto-preferring it over the working subprocess path would break a
        binary that is actually usable.  ``ctypes`` is reachable only via
        an explicit ``python_bridge="ctypes"`` config.
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

        # ctypes only when explicitly requested — see docstring.
        if normalised == 'ctypes':
            ctypes_module = self._try_ctypes_bridge()
            if ctypes_module is not None:
                return 'ctypes', ctypes_module
            self._last_error = (
                'MAGEMin ctypes bridge unavailable '
                '(libMAGEMin shared library not found)'
            )

        if normalised in (None, 'julia'):
            try:
                import julia  # type: ignore[import-not-found]
                # PyJulia is heavy — only flag as available if the
                # MAGEMin_C.jl package import succeeds.
                from julia import Main as JuliaMain  # noqa: F401
                JuliaMain.eval('import MAGEMin_C')  # may raise
                return 'julia', julia
            except Exception as exc:  # noqa: BLE001
                if normalised == 'julia':
                    self._last_error = f'julia bridge import failed: {exc}'

        # Subprocess fallback: the binary located in initialize() is
        # itself the bridge.  This is the supported default — MAGEMin
        # ships no PyPI package, so a built binary must always be usable.
        if normalised in (None, 'subprocess') and self._binary_path is not None:
            return 'subprocess', None

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

    # MAGEMin's igneous (``ig``) database ``--Bulk`` order.  See the
    # binary's ``--help``: 'ig' expects
    #   SiO2, Al2O3, CaO, MgO, FeOt, K2O, Na2O, TiO2, O, Cr2O3, H2O
    # FeOt is *total* iron — the simulator's FeO + Fe2O3 are folded into
    # it, and ``O`` is the free redox component (left at 0; fO2 is set by
    # the ``--buffer`` argument instead).
    _IG_BULK_ORDER: Tuple[str, ...] = (
        'SiO2', 'Al2O3', 'CaO', 'MgO', 'FeOt', 'K2O', 'Na2O', 'TiO2',
        'O', 'Cr2O3', 'H2O',
    )
    # Current standard atomic weights used for FeOt total-iron conversion.
    # The shared accounting table is rounded for legacy kg<->mol tests.
    _FE_MOLAR_MASS_G_PER_MOL = 55.845
    _O_MOLAR_MASS_G_PER_MOL = 15.999
    _FEO_MOLAR_MASS_G_PER_MOL = (
        _FE_MOLAR_MASS_G_PER_MOL + _O_MOLAR_MASS_G_PER_MOL
    )
    _FE2O3_MOLAR_MASS_G_PER_MOL = (
        2 * _FE_MOLAR_MASS_G_PER_MOL + 3 * _O_MOLAR_MASS_G_PER_MOL
    )
    _FEOT_FROM_FE2O3_MOLAR_MASS_G_PER_MOL = 2 * _FEO_MOLAR_MASS_G_PER_MOL
    _FEOT_FROM_FE2O3_FACTOR = (
        _FEOT_FROM_FE2O3_MOLAR_MASS_G_PER_MOL
        / _FE2O3_MOLAR_MASS_G_PER_MOL
    )

    # fO2 buffers MAGEMin's CLI accepts (``--buffer=``).  The simulator
    # works in absolute log10(fO2); MAGEMin's single-point CLI takes a
    # named buffer, so absent an explicit buffer config the adapter uses
    # ``qfm`` (the closest analog for the simulator's reducing regimes)
    # and records that substitution as a warning upstream.
    _BUFFER_CHOICES: frozenset = frozenset({
        'qfm', 'mw', 'qif', 'nno', 'hm', 'cco',
    })

    @staticmethod
    def _pressure_bar_to_GPa(pressure_bar: float) -> float:
        """Convert pressure from bar to GPa.  1 GPa = 10000 bar."""
        return float(pressure_bar) / 1.0e4

    @staticmethod
    def _GPa_to_kbar(pressure_GPa: float) -> float:
        """Convert pressure from GPa to kilobar.  1 GPa = 10 kbar."""
        return float(pressure_GPa) * 10.0

    def _call_magemin(
        self,
        composition_wt_pct: Dict[str, float],
        temperature_C: float,
        pressure_bar: float,
        fO2_log: float,
    ) -> Any:
        """
        Invoke MAGEMin via whichever bridge ``initialize`` selected.

        The supported default is the ``subprocess`` bridge, which drives
        the compiled ``MAGEMin`` binary directly.  The optional
        ``pymagemin`` / ``julia`` bridges assume a high-level
        ``minimize`` / ``single_point_minimization`` entry point.

        Pressure unit handling: the binding-spec contract (§4) is in GPa,
        whereas the MAGEMin binary's CLI takes kilobar.  ``pressure_bar``
        is converted ``bar -> GPa`` (1 GPa = 10000 bar) and then
        ``GPa -> kbar`` (1 GPa = 10 kbar) at the binary boundary, with
        both steps named so the conversion is auditable.
        """
        module = self._magemin_module
        temperature_K = temperature_C + 273.15
        pressure_GPa = self._pressure_bar_to_GPa(pressure_bar)
        pressure_kbar = self._GPa_to_kbar(pressure_GPa)

        if self._bridge == 'pymagemin':
            for name in ('minimize', 'run', 'equilibrium'):
                fn = getattr(module, name, None)
                if fn is None:
                    continue
                return fn(
                    composition=composition_wt_pct,
                    T_C=temperature_C,
                    T_K=temperature_K,
                    P_GPa=pressure_GPa,
                    P_kbar=pressure_kbar,
                    log_fO2=fO2_log,
                    database=self._database,
                )

        if self._bridge == 'julia':
            JuliaMain = module.Main  # type: ignore[attr-defined]
            # The Julia bridge expects a dict of oxide wt% and returns
            # a struct.  This is a thin wrapper — full marshaling is
            # the responsibility of MAGEMin_C.jl.
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
                'use the default subprocess bridge or configure '
                'python_bridge="julia"'
            )

        if self._bridge == 'subprocess':
            return self._call_magemin_subprocess(
                composition_wt_pct=composition_wt_pct,
                temperature_C=temperature_C,
                pressure_kbar=pressure_kbar,
                fO2_log=fO2_log,
            )

        raise RuntimeError(
            f'MAGEMin bridge {self._bridge!r} has no recognised entry point')

    def _call_magemin_subprocess(
        self,
        *,
        composition_wt_pct: Dict[str, float],
        temperature_C: float,
        pressure_kbar: float,
        fO2_log: float,
    ) -> Dict[str, Any]:
        """
        Drive the compiled MAGEMin binary for one single-point call.

        Builds the ``--Verb=0`` argument vector, runs the binary, and
        parses the compact ``Phase :`` / ``Mode :`` stdout block into the
        ``{'phases': {name: {'mass_kg': ...}}}`` shape ``_populate_result``
        already understands.  The buffer pseudo-phase (``qfm`` etc.) the
        binary echoes back is dropped — it is a control row, not a
        material phase.

        The caller's absolute ``fO2_log`` is honoured by translating it
        into MAGEMin's ``--buffer + --buffer_n`` form via
        ``_resolve_buffer`` (O'Neill 1987 QFM calibration); the
        substitution warning is returned alongside the parsed phase
        block so ``equilibrate`` can surface it on the
        ``EquilibriumResult``.

        Raises ``RuntimeError`` on a non-zero exit, a timeout, or an
        unparseable stdout — the explicit fail signal ``equilibrate()``
        converts into an empty result + warning.
        """
        if self._binary_path is None:
            raise RuntimeError('MAGEMin subprocess bridge has no binary path')

        bulk = self._build_ig_bulk_vector(composition_wt_pct)
        buffer_name, buffer_n, buffer_warnings = self._resolve_buffer(
            temperature_C=temperature_C, fO2_log=fO2_log,
        )

        args = [
            str(self._binary_path),
            '--Verb=0',
            f'--db={self._database}',
            f'--Temp={temperature_C:.6f}',
            f'--Pres={pressure_kbar:.6f}',
            '--sys_in=wt',
            '--Bulk=' + ','.join(f'{value:.6f}' for value in bulk),
            f'--buffer={buffer_name}',
            f'--buffer_n={buffer_n:.6f}',
        ]

        timeout_s = float(self._config.get('timeout_s', 60.0))
        try:
            completed = subprocess.run(  # noqa: S603 - args are adapter-built
                args,
                cwd=str(self._binary_path.parent),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f'MAGEMin binary timed out after {timeout_s:g}s'
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f'MAGEMin binary could not be executed: {exc}'
            ) from exc

        if completed.returncode != 0:
            stderr = (completed.stderr or '').strip()
            raise RuntimeError(
                f'MAGEMin binary exited {completed.returncode}: '
                f'{stderr or "no stderr"}'
            )

        phases = self._parse_subprocess_stdout(completed.stdout or '')
        if not phases:
            raise RuntimeError(
                'MAGEMin binary produced no parseable Phase/Mode block'
            )
        return {'phases': phases, 'buffer_warnings': buffer_warnings}

    def _build_ig_bulk_vector(
        self, composition_wt_pct: Mapping[str, float]
    ) -> List[float]:
        """
        Project the simulator's 14-oxide wt% onto MAGEMin's ``ig`` bulk
        order, folding FeO + Fe2O3 into the single FeOt (total iron)
        component and zeroing the free ``O`` redox component (fO2 is set
        by ``--buffer``).  Oxides outside the ``ig`` system (MnO, P2O5,
        NiO, CoO) are dropped — the ``ig`` database does not model them.
        """
        feo = float(composition_wt_pct.get('FeO', 0.0) or 0.0)
        fe2o3 = float(composition_wt_pct.get('Fe2O3', 0.0) or 0.0)
        # Fe2O3 -> FeO-equivalent mass: each Fe2O3 carries 2 Fe;
        # total-iron-as-FeOt reports that iron as 2 FeO formula masses.
        feot = feo + fe2o3 * self._FEOT_FROM_FE2O3_FACTOR

        vector: List[float] = []
        for component in self._IG_BULK_ORDER:
            if component == 'FeOt':
                vector.append(feot)
            elif component == 'O':
                vector.append(0.0)
            else:
                vector.append(
                    float(composition_wt_pct.get(component, 0.0) or 0.0))
        return vector

    @staticmethod
    def _qfm_logfo2_oneill(temperature_C: float) -> float:
        """
        Absolute log10(fO2) of the QFM buffer at ``temperature_C``.

        O'Neill (1987) formulation: ``logfO2_QFM = 8.58 - 25050 / T_K``.
        Same fit PySulfSat uses (see
        ``simulator/melt_backend/sulfsat.py::_qfm_logfo2_oneill``); keep
        the two in sync.
        """
        T_K = float(temperature_C) + 273.15
        return 8.58 - 25050.0 / T_K

    def _resolve_buffer(
        self, *, temperature_C: float, fO2_log: float,
    ) -> Tuple[str, float, List[str]]:
        """
        Translate the caller's absolute log10(fO2) into a MAGEMin
        ``(buffer_name, buffer_n, warnings)`` triple.

        The simulator carries absolute log10(fO2); MAGEMin's CLI takes a
        named buffer name PLUS a numeric ``buffer_n`` offset (the same
        ΔQFM idiom used elsewhere in this codebase) — see MAGEMin's
        ``examples/MAGEMin_C_single_point_with_buffer.jl`` and the
        ``pp_min_function.c`` Gibbs-energy correction
        ``z_b.T * 0.019145 * gv.buffer_n``.  The substitution must
        honour the caller's absolute value: this method computes
        ``delta = fO2_log - QFM(T)`` and returns it as the buffer
        offset so the subprocess receives the requested fO2 instead of
        silently snapping to the named buffer.

        An explicit ``fO2_buffer`` config (one of the legacy named
        buffers ``qfm``, ``mw``, ``qif``, ``nno``, ``hm``, ``cco``)
        overrides the offset-translation path and is passed through
        with ``buffer_n=0.0`` for backwards compatibility — the
        substitution warning still names the buffer so callers can
        spot mismatches.  Today only ``qfm`` has a calibration table
        here; configuring any other buffer keeps the legacy "named
        buffer only" behaviour but is flagged as a warning so the
        caller knows their absolute fO2 was NOT honoured.

        Returns:
            (buffer_name, buffer_n, warnings)
            - buffer_name: one of ``_BUFFER_CHOICES``
            - buffer_n: numeric offset for ``--buffer_n=...``
            - warnings: human-readable lines that ``equilibrate`` must
              surface on the EquilibriumResult so the caller cannot
              miss the substitution.
        """
        configured = self._config.get('fO2_buffer')
        warnings_out: List[str] = []
        if configured is not None:
            name = str(configured).lower().strip()
            if name not in self._BUFFER_CHOICES:
                warnings_out.append(
                    f'MAGEMin: unknown fO2_buffer {configured!r}; '
                    'falling back to qfm with offset translation'
                )
                name = 'qfm'
            if name == 'qfm':
                # An explicit qfm config still benefits from offset
                # translation against the caller's fO2_log.
                buffer_n = float(fO2_log) - self._qfm_logfo2_oneill(temperature_C)
                warnings_out.append(
                    f'MAGEMin: translated absolute fO2_log={fO2_log:.4f} at '
                    f'{temperature_C:.2f} C to --buffer=qfm '
                    f'--buffer_n={buffer_n:.4f} '
                    "(O'Neill 1987 QFM calibration)"
                )
                return 'qfm', buffer_n, warnings_out
            warnings_out.append(
                f'MAGEMin: configured fO2_buffer={name!r} has no offset '
                f'calibration in this adapter; passing --buffer={name} '
                f'with --buffer_n=0 so the absolute fO2_log={fO2_log:.4f} '
                'is NOT honoured'
            )
            return name, 0.0, warnings_out

        # Default path: translate fO2_log into qfm + offset.
        buffer_n = float(fO2_log) - self._qfm_logfo2_oneill(temperature_C)
        warnings_out.append(
            f'MAGEMin: translated absolute fO2_log={fO2_log:.4f} at '
            f'{temperature_C:.2f} C to --buffer=qfm '
            f'--buffer_n={buffer_n:.4f} '
            "(O'Neill 1987 QFM calibration)"
        )
        return 'qfm', buffer_n, warnings_out

    @staticmethod
    def _parse_subprocess_stdout(stdout: str) -> Dict[str, Dict[str, float]]:
        """
        Parse the compact MAGEMin ``--Verb=0`` ``Phase :`` / ``Mode :``
        block.

        The binary prints, e.g.::

             Phase :       ol      liq      spl      qfm
             Mode  :  0.02491  0.96156  0.00213  0.01140

        Mode values are mass fractions of the system.  The buffer
        pseudo-phase (any name in ``_BUFFER_CHOICES``) is dropped — it is
        a control row, not a material phase.  Returns
        ``{phase: {'mass_kg': fraction}}`` on a unit-mass basis (the
        adapter only needs relative masses for ``liquid_fraction`` and
        modal parity).
        """
        phase_line: Optional[str] = None
        mode_line: Optional[str] = None
        for line in stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith('Phase :') or stripped.startswith('Phase:'):
                phase_line = stripped.split(':', 1)[1]
            elif stripped.startswith('Mode :') or stripped.startswith('Mode:'):
                mode_line = stripped.split(':', 1)[1]
            elif stripped.startswith('Mode  :'):
                mode_line = stripped.split(':', 1)[1]
        if phase_line is None or mode_line is None:
            return {}

        names = phase_line.split()
        values = mode_line.split()
        if not names or len(names) != len(values):
            return {}

        phases: Dict[str, Dict[str, float]] = {}
        for name, raw in zip(names, values):
            if name.lower() in MAGEMinBackend._BUFFER_CHOICES:
                continue  # buffer control row, not a phase
            try:
                fraction = float(raw)
            except ValueError:
                continue
            if fraction <= 0.0:
                continue
            phases[name] = {'mass_kg': fraction}
        return phases

    # ------------------------------------------------------------------
    # Composition projection / result parsing
    # ------------------------------------------------------------------

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
        (
            phases_present,
            phase_masses_kg,
            phase_compositions,
            liquid_fraction,
            liquid_composition_wt_pct,
        ) = self._phase_assemblage_payload(raw)
        result.phases_present.extend(phases_present)
        result.phase_masses_kg.update(phase_masses_kg)
        result.phase_compositions.update(phase_compositions)
        result.liquid_fraction = liquid_fraction
        if liquid_composition_wt_pct:
            result.liquid_composition_wt_pct = liquid_composition_wt_pct

    def _phase_assemblage_payload(
        self,
        raw: Any,
    ) -> Tuple[
        List[str],
        Dict[str, float],
        Dict[str, Dict[str, float]],
        float,
        Dict[str, float],
    ]:
        if raw is None:
            raise MeltCompositionError('zero_total_phase_mass')

        phases = self._extract_phases(raw)
        liquid_fraction = liquid_fraction_from_phase_masses({
            name: mass_kg for name, mass_kg, _ in phases
        })
        if liquid_fraction is None:
            raise MeltCompositionError('zero_total_phase_mass')

        liquid_phase_names = ('liq', 'liquid', 'LIQUID', 'melt', 'Melt')
        phases_present: List[str] = []
        phase_masses_kg: Dict[str, float] = {}
        phase_compositions: Dict[str, Dict[str, float]] = {}
        liquid_composition: Dict[str, float] = {}

        for name, mass_kg, composition_wt_pct in phases:
            mass = float(mass_kg)
            if mass <= 0:
                continue
            phases_present.append(name)
            phase_masses_kg[name] = mass
            if composition_wt_pct:
                phase_compositions[name] = composition_wt_pct
            if name in liquid_phase_names or name.lower().startswith('liq'):
                if composition_wt_pct:
                    liquid_composition = composition_wt_pct

        return (
            phases_present,
            phase_masses_kg,
            phase_compositions,
            liquid_fraction,
            liquid_composition,
        )

    @staticmethod
    def _extract_phases(
        raw: Any,
    ) -> List[Tuple[str, Any, Dict[str, float]]]:
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
    def _extract_mass_kg(state: Any) -> Any:
        if isinstance(state, (int, float)):
            return state
        if isinstance(state, dict):
            for key in ('mass_kg', 'mass', 'm', 'amount_kg'):
                if key in state:
                    return state[key]
        for attr in ('mass_kg', 'mass', 'm', 'amount_kg'):
            value = getattr(state, attr, None)
            if value is not None:
                return value
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
