"""
VapoRock Vapor-Melt Equilibrium Backend
========================================

Adapter around VapoRock for equilibrium vapor speciation over silicate melts.

Canonical upstream package metadata uses package/import name ``vaporock``
and exposes ``vaporock.System().set_melt_comp(...)`` plus
``eval_gas_abundances(T, logfO2)``.  The optional ``[vapor]`` extra pins
the GitLab v0.1 source tag because PyPI has no ``vaporock`` release and
the historical ``https://github.com/cwolfe/VapoRock`` target was not
available during the 2026-05-14 probe.

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

Authority posture
-----------------
VapoRock is **not yet wired into any active call site** — nothing
instantiates ``VapoRockBackend`` outside the test suite.  The
shadow/multiplexer runner described under "Intended call sites" above is
still future work (see the chemistry-kernel carve-out goal).

If this adapter *were* selected as the active melt backend today, it
would NOT silently produce a usable equilibrium: ``equilibrate()``
returns only ``vapor_pressures_Pa`` (no silicate phase assemblage, no
``ledger_transition``), so ``simulator/core.py::_get_equilibrium`` would
either fail closed — an un-initialized backend raises ``RuntimeError``
("VapoRockBackend is unavailable") — or, with the upstream library
present, hand back a vapor-only result that has no melt phases for the
rest of the step to consume.  Either way "diagnostic" means "not safe to
select as the authoritative backend," not "gracefully ignored."  The
honest place for VapoRock is behind a dedicated vapor-side shadow
consumer that reads ``vapor_pressures_Pa`` without routing the adapter
through ``_get_equilibrium`` as a phase solver.

``EquilibriumResult.ledger_transition`` is never populated and
``ledger_account_policies()`` returns no ledger-authoritative policy:
VapoRock has no ``AtomLedger`` authority and must not be granted any
until the ``VAPOROCK-AUTHORITY-PROMOTION`` goal (and even then only for
``VAPOR_PRESSURE``).  ``equilibrate()`` consumes only the cleaned
silicate melt — non-melt ledger accounts (gas, metal, salt, sulfide,
halide) are filtered out before the library is called.

Species-name normalization
--------------------------
The installed VapoRock build (``vaporock.System().eval_gas_abundances``)
returns every gas species with a ``(g)`` phase suffix — ``Na(g)``,
``SiO(g)``, ``O2(g)``, ``SiO2(g)``, ``Al2O(g)``, etc.
``_strip_gas_suffix`` reconciles these onto a vocabulary that is
provably disjoint from the condensed melt oxides: a gas species whose
bare spelling collides with an ``OXIDE_SPECIES`` member is namespaced
with ``_gas`` (``SiO2(g) -> SiO2_gas``, ``FeO(g) -> FeO_gas``); every
other gas species is returned bare (``Na(g) -> Na``).  Without this a
downstream vapor consumer keying ``vapor_pressures_Pa`` by species would
conflate gaseous SiO2 with melt SiO2 and break the atom-explicit
``SiO2 -> SiO + 1/2 O2`` stoichiometry.
"""

from __future__ import annotations

import importlib
import math
import re
import warnings
from typing import Any, Dict, List, Mapping, Optional

from simulator.melt_backend.base import (
    DEFAULT_BACKEND_CAPABILITIES,
    EquilibriumResult,
    MeltBackend,
)
from simulator.state import OXIDE_SPECIES


# VapoRock gas-species names carry a "(g)" phase suffix.  This pattern
# matches a trailing "(g)" (with optional surrounding whitespace) so the
# normalizer can recognise and strip the explicit gas marker.
_GAS_SUFFIX_RE = re.compile(r'\s*\(\s*g\s*\)\s*$', re.IGNORECASE)

# Suffix appended to a normalized gas-species name whose bare spelling
# would otherwise collide with a condensed melt oxide in OXIDE_SPECIES
# (e.g. gaseous SiO2 vs. melt SiO2).  Keeping the gas vocabulary disjoint
# from the oxide basis stops a downstream vapor consumer from conflating
# "SiO2(g)" with melt SiO2 and breaking the atom-explicit
# SiO2 -> SiO + 1/2 O2 stoichiometry.
_GAS_NAMESPACE_SUFFIX = '_gas'

# Gas species whose bare name collides with a condensed melt oxide.  Only
# these get the "_gas" namespace; every other vapor species (Na, SiO, O2,
# Al2O, ...) is already disjoint from OXIDE_SPECIES and stays bare so the
# builtin Antoine path and the VapoRock path share keys for the shared
# volatiles.
_OXIDE_COLLIDING_GAS_SPECIES = frozenset(OXIDE_SPECIES)

# Cleaned silicate melt is the only ledger account VapoRock may consume.
# Matches the alphamelts.py contract: every other account is filtered
# out before the library is called.
_VAPOROCK_MELT_ACCOUNT = 'process.cleaned_melt'


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

_IMPORT_CANDIDATES = (
    'vaporock',
    # TODO(vaporock): remove the legacy uppercase probe if no local installs
    # still expose the historical module name.
    'VapoRock',
)


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
        # Reflect the 34-species VapoRock vapor model in the SAME
        # vocabulary ``_strip_gas_suffix`` emits: gas species whose bare
        # spelling collides with a melt oxide in OXIDE_SPECIES carry the
        # "_gas" namespace (FeO_gas, MgO_gas, CaO_gas, MnO_gas, SiO2_gas,
        # Fe2O3_gas); every other species (Na, SiO, Al2O, Ti2O3, ...) is
        # already disjoint from the oxide basis and stays bare.  This list
        # must stay in sync with the normalizer; the simulator filters on
        # availability anyway.
        return [
            'Na', 'K', 'Fe', 'Mg', 'Ca', 'Si', 'Al', 'Ti', 'Cr', 'Mn',
            'SiO', 'FeO_gas', 'MgO_gas', 'CaO_gas', 'AlO', 'TiO', 'NaO',
            'KO', 'CrO', 'MnO_gas',
            'SiO2_gas', 'Al2O', 'Fe2O3_gas', 'Ti2O3',
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
        # Keep vapor_melt_equilibrium instance-local: adding it to the base
        # capability dict widens every backend contract and breaks exact
        # capability assertions unrelated to vapor-side routing.
        caps: Dict[str, bool] = {key: False for key in DEFAULT_BACKEND_CAPABILITIES}
        caps['gas_volatiles'] = True
        caps['vapor_melt_equilibrium'] = True
        return caps

    def ledger_account_policies(self) -> tuple[Any, ...]:
        """
        VapoRock requires no AtomLedger account policy.

        VapoRock is vapor-side / diagnostic: it returns partial pressures,
        never a ledger-authoritative transition.  The evaporation flux and
        the melt-debit/gas-credit transition stay with the builtin engine
        until ``VAPOROCK-AUTHORITY-PROMOTION``.  Returning an empty tuple
        keeps the layered-ABC contract explicit (same posture as
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
        Call VapoRock for vapor-melt equilibrium.

        Conforms to the layered ``MeltBackend`` ABC: when
        ``composition_mol_by_account`` is supplied, only the
        ``process.cleaned_melt`` account is consumed — gas, metal, salt,
        sulfide and halide accounts are filtered out before the library
        is called (binding spec §7).  The melt composition is then
        projected to oxide wt% in the 14-oxide simulator basis (a strict
        subset of the MELTS basis VapoRock expects).

        ``EquilibriumResult.ledger_transition`` is left ``None`` and no
        phase assemblage is reported: VapoRock holds no ``AtomLedger``
        authority.  This is **not** the same as "the result is harmless
        if selected as the active backend" — see the module-level
        "Authority posture" note.  The result is only meaningful to a
        dedicated vapor-side consumer that reads ``vapor_pressures_Pa``.

        On any library error the method returns an empty
        ``EquilibriumResult`` and appends a one-line warning rather
        than raising.
        """
        result = EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
        )

        if not self._available or self._vaporock is None:
            result.warnings.append('VapoRock backend not initialized')
            return result

        if composition_mol_by_account is not None:
            melt_mol, dropped_accounts = self._melt_account_composition(
                composition_mol_by_account)
            for account in dropped_accounts:
                result.warnings.append(
                    'VapoRock is vapor-side; ignored non-melt ledger '
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
        # assemblage.  ledger_transition is left None: VapoRock holds no
        # AtomLedger authority (see the module "Authority posture" note —
        # this adapter is not safe to select as the active backend).
        return result

    @staticmethod
    def _melt_account_composition(
        composition_mol_by_account: Mapping[str, Mapping[str, float]],
    ) -> tuple[Dict[str, float], List[str]]:
        """
        Extract the cleaned-melt account; report every other account.

        Returns ``(melt_species_mol, dropped_account_names)``.  VapoRock
        only consumes ``process.cleaned_melt``; any other account that
        carries positive material is reported back so the caller can
        record a warning (binding spec §7 — VapoRock must not receive
        metal / sulfide / salt / halide accounts).
        """
        melt_mol: Dict[str, float] = {}
        for species, mol in (
            composition_mol_by_account.get(_VAPOROCK_MELT_ACCOUNT, {}) or {}
        ).items():
            value = float(mol)
            if value > 0.0:
                melt_mol[str(species)] = melt_mol.get(str(species), 0.0) + value

        dropped: List[str] = []
        for account, species_mol in composition_mol_by_account.items():
            if str(account) == _VAPOROCK_MELT_ACCOUNT:
                continue
            if any(float(mol) > 0.0 for mol in (species_mol or {}).values()):
                dropped.append(str(account))
        return melt_mol, sorted(dropped)

    # ------------------------------------------------------------------
    # Library boundary
    # ------------------------------------------------------------------

    def _import_vaporock(self) -> Optional[Any]:
        """
        Lazy import of the upstream VapoRock library.

        Returns None if the import fails (the caller treats this as
        "backend not available").  Never raises.
        """
        errors: List[str] = []
        for module_name in _IMPORT_CANDIDATES:
            try:
                return importlib.import_module(module_name)
            except Exception as exc:  # noqa: BLE001 - import-boundary catch
                errors.append(f'{module_name}: {exc}')

        self._last_error = (
            'VapoRock import failed: ' + '; '.join(errors)
        )
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

        system_cls = getattr(module, 'System', None)
        if callable(system_cls):
            try:
                system = system_cls()
                set_melt_comp = getattr(system, 'set_melt_comp')
                eval_gas_abundances = getattr(system, 'eval_gas_abundances')
                # VapoRock's System.set_melt_comp takes the oxide wt%
                # dict positionally; eval_gas_abundances expects an
                # absolute temperature in Kelvin (verified against the
                # installed vaporock build, 2026-05-14).
                set_melt_comp(composition_wt_pct)
                temperature_K = (
                    temperature
                    if self._temperature_units == 'K'
                    else temperature + 273.15
                )
                logP = eval_gas_abundances(temperature_K, fO2_log)
                return self._log10_bar_pressures_to_pa(logP)
            except Exception as exc:  # noqa: BLE001 - upstream boundary
                last_attr_error = exc

        raise RuntimeError(
            'VapoRock library does not expose a recognised equilibrium '
            'entry point (tried: '
            f'{", ".join(candidate_names)}, System.eval_gas_abundances)'
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
        species_formula_registry: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, float]:
        """
        Project the simulator's mol/kg melt composition to the oxide
        wt% basis VapoRock expects.

        VapoRock's basis is identical to MELTS for the oxides shared
        with the simulator (the 14-oxide list in ``simulator.state``),
        so this is a straight rename + normalisation.  Any species not
        in the VapoRock basis is dropped with a warning.

        ``species_formula_registry`` is the simulator's formula registry
        (threaded through from the layered ABC) used for the mol -> kg
        projection; ``None`` falls back to the builtin formula table.

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
                    species, species_formula_registry).molar_mass_kg_per_mol()
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
        pressures: Dict[str, float] = {}
        for species, value in raw.items():
            pressure = float(value) * scale
            if pressure > 0.0:
                pressures[self._strip_gas_suffix(species)] = pressure
        return pressures

    def _log10_bar_pressures_to_pa(self, raw: Any) -> Dict[str, float]:
        """
        Convert VapoRock System log10(bar) output to simulator Pa.

        VapoRock's ``eval_gas_abundances`` returns a pandas DataFrame
        indexed by ``species_name`` (one column, the temperature) whose
        values are log10(partial pressure / bar).  Species names carry a
        ``(g)`` phase suffix; ``_strip_gas_suffix`` maps them onto the
        simulator's collision-free vocabulary (oxide-colliding gas names
        namespaced with ``_gas``, the rest bare).  ``-inf`` rows (species
        with no thermodynamic data, e.g. Cr gases in some builds) drop
        out.
        """
        if raw is None:
            return {}

        if hasattr(raw, 'iloc') and hasattr(raw, 'index'):
            try:
                if len(getattr(raw, 'shape', ())) == 2:
                    series = raw.iloc[:, 0]
                else:
                    series = raw
                items = series.items()
            except Exception:  # noqa: BLE001
                return {}
        elif isinstance(raw, dict):
            items = raw.items()
        else:
            return {}

        pressures: Dict[str, float] = {}
        for species, log10_bar in items:
            try:
                value = float(log10_bar)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                pressure_pa = (10.0 ** value) * 1e5
                if pressure_pa > 0.0:
                    pressures[self._strip_gas_suffix(species)] = pressure_pa
        return pressures

    @staticmethod
    def _strip_gas_suffix(species: Any) -> str:
        """
        Map a VapoRock gas-species name onto a collision-free simulator
        vocabulary.

        VapoRock labels every gas species with a ``(g)`` phase suffix
        (``Na(g)``, ``SiO(g)``, ``O2(g)``, ``SiO2(g)``, ``Al2O(g)``...).
        Naively stripping the suffix would map ``SiO2(g)`` and
        ``Fe2O3(g)`` onto ``SiO2`` / ``Fe2O3`` — the *exact* strings used
        for the condensed melt oxides in ``OXIDE_SPECIES``.  A downstream
        consumer keying ``vapor_pressures_Pa`` by species would then
        conflate gaseous SiO2 with melt SiO2 and silently break the
        atom-explicit ``SiO2 -> SiO + 1/2 O2`` stoichiometry.

        To keep the gas vocabulary provably disjoint from the oxide
        basis, a species that arrives with the explicit ``(g)`` marker
        AND whose bare spelling is a member of ``OXIDE_SPECIES`` is
        namespaced with ``_gas`` (``SiO2(g) -> SiO2_gas``,
        ``FeO(g) -> FeO_gas``).  Every other gas species — ``Na``,
        ``SiO``, ``O2``, ``Al2O``, ... — is already disjoint from the
        oxide basis and is returned bare, so the builtin Antoine path
        and the VapoRock path still share keys for the shared volatiles.

        A name with no ``(g)`` marker is returned unchanged (stripped of
        surrounding whitespace only): the marker is VapoRock's explicit
        "this is a gas" signal, so mocked / legacy result dicts that
        already use bare names are passed through untouched.
        """
        raw = str(species)
        stripped = _GAS_SUFFIX_RE.sub('', raw).strip()
        had_gas_marker = stripped != raw.strip()
        if had_gas_marker and stripped in _OXIDE_COLLIDING_GAS_SPECIES:
            return stripped + _GAS_NAMESPACE_SUFFIX
        return stripped
