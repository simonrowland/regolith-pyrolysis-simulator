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
from functools import lru_cache
import math
import re
import warnings
from typing import Any, Dict, List, Mapping, Optional

from simulator.melt_backend.base import (
    CLEANED_MELT_ACCOUNT,
    DEFAULT_BACKEND_CAPABILITIES,
    EquilibriumResult,
    MeltBackend,
    project_melt_to_oxide_projection,
    split_cleaned_melt_account,
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

# VapoRock consumes the same oxide basis as MELTS / alphaMELTS.  The
# 14-oxide simulator basis is a strict subset; project 1:1 by name and
# drop any oxide VapoRock does not declare.
#
# Verified 2026-05-15 against the installed VapoRock package: oxide
# spellings in ``vaporock/chemistry.py::OXIDE_MOLWT`` match
# ``simulator.state.OXIDE_SPECIES`` 1:1 (SiO2, TiO2, Al2O3, Fe2O3,
# Cr2O3, FeO, MnO, MgO, NiO, CoO, CaO, Na2O, K2O, P2O5 plus H2O/CO2 the
# simulator does not pass through this adapter).  ``OXIDE_SPECIES`` is
# passed directly to ``project_melt_to_oxide_projection`` rather than via a
# private alias that just rebinds the same list.

# Verified 2026-05-15: the installed VapoRock package exposes the
# lowercase ``vaporock`` module name; the uppercase ``VapoRock`` probe
# is retained for the historical pre-rename installs documented in the
# project README. Drop the uppercase fallback if it is ever observed to
# resolve to a stale install in CI.
_IMPORT_CANDIDATES = (
    'vaporock',
    'VapoRock',
)


def _dropped_account_species(
    composition_mol_by_account: Mapping[str, Mapping[str, float]],
) -> Dict[str, tuple[str, ...]]:
    result: Dict[str, tuple[str, ...]] = {}
    for account, species_mol in composition_mol_by_account.items():
        account_name = str(account)
        if account_name == CLEANED_MELT_ACCOUNT:
            continue
        species = sorted(
            str(name)
            for name, mol in (species_mol or {}).items()
            if float(mol) > 0.0
        )
        if species:
            result[account_name] = tuple(species)
    return result


def _positive_mass_by_species(
    *,
    composition_kg: Optional[Dict[str, float]],
    composition_mol: Optional[Mapping[str, float]],
    species_formula_registry: Optional[Mapping[str, Any]],
) -> Dict[str, float]:
    if composition_mol is None:
        return {
            str(species): float(value)
            for species, value in (composition_kg or {}).items()
            if float(value) > 0.0
        }

    from simulator.accounting.formulas import resolve_species_formula

    masses: Dict[str, float] = {}
    for species, mol in composition_mol.items():
        value = float(mol)
        if value <= 0.0:
            continue
        mass_kg = value * resolve_species_formula(
            species, species_formula_registry
        ).molar_mass_kg_per_mol()
        masses[str(species)] = masses.get(str(species), 0.0) + mass_kg
    return masses


def _projection_diagnostics(
    *,
    backend: str,
    projection: Any,
    composition_kg: Optional[Dict[str, float]],
    composition_mol: Optional[Mapping[str, float]],
    oxide_basis: tuple[str, ...],
    species_formula_registry: Optional[Mapping[str, Any]],
    dropped_accounts: List[str],
    dropped_account_species: Mapping[str, tuple[str, ...]],
) -> Dict[str, Any]:
    diagnostics = dict(projection.diagnostics)
    masses = _positive_mass_by_species(
        composition_kg=composition_kg,
        composition_mol=composition_mol,
        species_formula_registry=species_formula_registry,
    )
    basis = set(oxide_basis)
    dropped_species_mass = {
        species: mass
        for species, mass in masses.items()
        if species not in basis and mass > 0.0
    }
    retained_mass = sum(
        mass for species, mass in masses.items()
        if species in basis and mass > 0.0
    )
    dropped_mass = sum(dropped_species_mass.values())
    input_mass = retained_mass + dropped_mass
    if not (dropped_species_mass or dropped_accounts or dropped_account_species):
        return diagnostics

    details: Dict[str, Any] = {
        'status': 'projected',
        'reason': 'input_composition_projected',
        'backend': backend,
        'projected_species': sorted(str(k) for k in projection.oxide_wt_pct),
    }
    if input_mass > 0.0:
        details['input_melt_mass_kg'] = input_mass
        details['retained_basis_melt_mass_kg'] = retained_mass
    if dropped_species_mass:
        details['dropped_species'] = sorted(dropped_species_mass)
        details['dropped_species_mass_kg'] = dict(
            sorted(dropped_species_mass.items())
        )
        details['dropped_non_basis_melt_mass_kg'] = dropped_mass
    if retained_mass > 0.0 and dropped_mass > 0.0:
        factor = input_mass / retained_mass
        details['renormalization_factor'] = factor
        details['renormalization_delta'] = factor - 1.0
        details['dropped_mass_fraction'] = dropped_mass / input_mass
    if dropped_accounts:
        details['dropped_accounts'] = sorted(
            str(account) for account in dropped_accounts
        )
    if dropped_account_species:
        details['dropped_account_species'] = {
            str(account): list(species)
            for account, species in sorted(dropped_account_species.items())
        }
    diagnostics['input_composition_projection'] = details
    return diagnostics


@lru_cache(maxsize=1)
def vaporock_runtime_available() -> bool:
    """Return the same adapter availability signal runtime fallback uses.

    This pays one adapter initialisation per process and performs no
    equilibrium solve. Force-builtin optimizer runs short-circuit before this
    probe, while live VapoRock runs initialise the adapter during execution
    anyway, so the probe adds no net cost on the VapoRock path.
    """
    backend = VapoRockBackend()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            initialized = backend.initialize({})
    except Exception:  # noqa: BLE001 - mirrors provider boundary catch
        return False
    if not initialized:
        return False
    return backend.is_available()


class VapoRockBackend(MeltBackend):
    """
    VapoRock vapor-melt equilibrium adapter.

    The backend operates on oxide wt% composition + temperature +
    pressure + fO2 and returns vapor partial pressures in Pa.  It does
    not populate ``phases_present`` because VapoRock consumes a melt
    state rather than producing one.

    Configuration (all optional):
        database_path:        filesystem path to a custom VapoRock thermo
                              database, if the installed build supports it.
        temperature_units:    'C' (default) or 'K'.
        pressure_units:       'bar' (default) or 'Pa' — the unit of the
                              *input* total pressure passed to VapoRock.
        vapor_pressure_units: 'bar' (default) or 'Pa' — the unit of the
                              partial pressures the upstream build
                              *returns* from a plain dict result.  The
                              0.1.x line returns bar, so 'bar' is the
                              documented default.  This is NOT inferred:
                              the dict result path mirrors the FactSAGE
                              ``amount_unit`` explicit-declaration pattern
                              because a basalt-analog melt can legitimately
                              have a dominant partial pressure below
                              1000 Pa, and a magnitude heuristic would
                              misclassify an already-Pa result and inflate
                              it 1e5x.  (The ``System.eval_gas_abundances``
                              log10(bar) path is unambiguous and unaffected.)
    """

    name = 'vaporock'

    def __init__(self) -> None:
        self._available: bool = False
        self._vaporock: Optional[Any] = None
        self._config: Dict[str, Any] = {}
        self._database_path: Optional[str] = None
        self._temperature_units: str = 'C'
        self._pressure_units: str = 'bar'
        self._vapor_pressure_units: str = 'bar'
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

        # Output-side unit for the plain-dict result path.  Fail closed on
        # an unrecognised value rather than guessing: the dict path used to
        # infer the unit from magnitude, which inflates an already-Pa
        # sub-1e3 result 1e5x (see _normalize_vapor_pressures).
        vapor_pressure_units = str(
            self._config.get('vapor_pressure_units') or 'bar').strip()
        if vapor_pressure_units not in ('bar', 'Pa'):
            self._last_error = (
                f'VapoRock vapor_pressure_units {vapor_pressure_units!r} '
                "not supported; declare 'bar' or 'Pa' explicitly"
            )
            self._warnings.append(self._last_error)
            return False
        self._vapor_pressure_units = vapor_pressure_units

        module = self._import_vaporock()
        if module is None:
            return False

        self._vaporock = module
        self._available = True
        return True

    def is_available(self) -> bool:
        return self._available

    def get_vapor_species(self) -> List[str]:
        # Reflect the VapoRock vapor model in the SAME vocabulary
        # ``_strip_gas_suffix`` emits.  The oxide-colliding bucket is
        # derived programmatically from the SAME ``OXIDE_SPECIES`` set the
        # normalizer keys on, so the advertised list cannot drift from
        # what ``_strip_gas_suffix`` actually emits: if VapoRock returns
        # ``TiO2(g)``/``Al2O3(g)`` the normalizer emits ``TiO2_gas`` /
        # ``Al2O3_gas`` and those names are advertised here too.  The
        # genuinely-non-colliding bare species (Na, SiO, Al2O, ...) are
        # already disjoint from the oxide basis and stay hand-curated; the
        # simulator filters on availability anyway.
        bare_species = [
            'Na', 'K', 'Fe', 'Mg', 'Ca', 'Si', 'Al', 'Ti', 'Cr', 'Mn',
            'SiO', 'AlO', 'TiO', 'NaO', 'KO', 'CrO', 'CrO2',
            'Al2O', 'Ti2O3',
            'O2', 'O',
            'Na2', 'K2', 'NaOH', 'KOH',
            'Si2', 'Mg2', 'Ca2',
        ]
        oxide_colliding = [
            ox + _GAS_NAMESPACE_SUFFIX for ox in OXIDE_SPECIES
        ]
        return bare_species + oxide_colliding

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
        if not self._available or self._vaporock is None:
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status='unavailable',
                warnings=['VapoRock backend not initialized'],
            )

        prior_warnings: List[str] = []
        dropped_accounts: List[str] = []
        dropped_account_species: Dict[str, tuple[str, ...]] = {}
        if composition_mol_by_account is not None:
            dropped_account_species = _dropped_account_species(
                composition_mol_by_account
            )
            melt_mol, dropped_accounts = split_cleaned_melt_account(
                composition_mol_by_account)
            for account in dropped_accounts:
                prior_warnings.append(
                    'VapoRock is vapor-side; ignored non-melt ledger '
                    f'account {account}'
                )
            # The cleaned-melt account is the canonical input; it
            # overrides any composition_mol passed alongside it.
            composition_mol = melt_mol

        projection = project_melt_to_oxide_projection(
            composition_kg=composition_kg,
            composition_mol=composition_mol,
            oxide_basis=tuple(OXIDE_SPECIES),
            species_formula_registry=species_formula_registry,
        )
        comp_wt = projection.oxide_wt_pct
        prior_warnings.extend(projection.warnings)
        projection_diagnostics = _projection_diagnostics(
            backend='VapoRock',
            projection=projection,
            composition_kg=composition_kg,
            composition_mol=composition_mol,
            oxide_basis=tuple(OXIDE_SPECIES),
            species_formula_registry=species_formula_registry,
            dropped_accounts=dropped_accounts,
            dropped_account_species=dropped_account_species,
        )
        if not comp_wt:
            # No oxide species in VapoRock's basis after the account
            # split; the vapor-melt solver has nothing valid to consume.
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status='out_of_domain',
                warnings=[
                    *prior_warnings,
                    'VapoRock received empty melt composition; returning empty '
                    'equilibrium result',
                ],
                diagnostics=projection_diagnostics,
            )

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
            vaporock_full_speciation_Pa = self._call_vaporock(
                composition_wt_pct=comp_wt,
                temperature=temperature_value,
                pressure=pressure_value,
                fO2_log=fO2_log,
            )
        except Exception as exc:  # noqa: BLE001 - library-boundary catch
            # VapoRock is present but the call did not produce a usable result.
            message = f'VapoRock equilibrate failed: {exc}'
            self._last_error = message
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status='not_converged',
                warnings=[*prior_warnings, message],
                diagnostics=projection_diagnostics,
            )

        # _call_vaporock already returns a finished species -> Pa dict
        # (declared-unit dict path or unambiguous log10(bar) path); do not
        # re-scale here or an already-Pa result is inflated 1e5x.
        # phases_present is intentionally left empty — VapoRock is
        # vapor-side only and does not return a silicate-phase
        # assemblage.  ledger_transition is left None: VapoRock holds no
        # AtomLedger authority (see the module "Authority posture" note —
        # this adapter is not safe to select as the active backend).
        result = EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            liquid_fraction=None,
            phase_assemblage_available=False,
            status='ok',
            warnings=list(prior_warnings),
            vapor_pressures_Pa=dict(vaporock_full_speciation_Pa),
            diagnostics=projection_diagnostics,
        )
        setattr(
            result,
            'vaporock_full_speciation_Pa',
            dict(vaporock_full_speciation_Pa),
        )
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

        Returns a finished ``species → Pa`` dict regardless of which
        entry point answered: the loosely-typed candidate-function
        results go through ``_normalize_vapor_pressures`` (which applies
        the explicitly-declared ``vapor_pressure_units``), and the
        ``System.eval_gas_abundances`` path goes through
        ``_log10_bar_pressures_to_pa`` (log10(bar), unambiguous).  Both
        unit conversions happen here so ``equilibrate`` never
        double-scales an already-Pa result.

        Verified 2026-05-15 against the installed VapoRock build: none
        of the four legacy top-level functions are present; the
        canonical entry point is ``System.eval_gas_abundances`` (see
        the second half of this method).  The candidate-name loop is
        retained as a defensive fallback for historical 0.1.x installs
        that exposed top-level functions instead of the ``System``
        class — it is a no-op on the current build but harmless.
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
                return self._normalize_vapor_pressures(fn(
                    composition=composition_wt_pct,
                    T_C=temperature if self._temperature_units == 'C' else None,
                    T_K=temperature if self._temperature_units == 'K' else None,
                    P_bar=pressure if self._pressure_units == 'bar' else None,
                    P_Pa=pressure if self._pressure_units == 'Pa' else None,
                    log_fO2=fO2_log,
                ))
            except TypeError as exc:
                # Older builds use positional / shorter signatures.
                # Fall back to a minimal call before declaring failure.
                last_attr_error = exc
                try:
                    return self._normalize_vapor_pressures(fn(
                        composition_wt_pct,
                        temperature,
                        fO2_log,
                    ))
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
                # log10(bar) result is unit-unambiguous; convert directly
                # without the declared-unit dict path.
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

    def _normalize_vapor_pressures(
        self, raw: Any
    ) -> Dict[str, float]:
        """
        Convert the upstream VapoRock result into a ``species → Pa``
        dict.

        The upstream API has historically returned ``{species: P_bar}``
        but newer builds may emit Pa directly.  The simulator's contract
        is Pa, so the result is scaled by the explicitly-declared
        ``vapor_pressure_units`` config key — the unit is **not** inferred.
        A magnitude heuristic (``max() < 1e3`` ⇒ bar) misclassifies a
        legitimate already-Pa result whose dominant partial pressure is
        below 1000 Pa (e.g. ~200 Pa SiO over a basalt analog at 1600 C)
        and inflates it 1e5x into Hertz-Knudsen.  This mirrors the
        FactSAGE ``amount_unit`` explicit-declaration pattern.
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

        try:
            float_values = [float(v) for v in raw.values()]
        except (TypeError, ValueError):
            return {}

        if not float_values:
            return {}

        # Scale by the explicitly-declared output unit; never guess.
        scale = 1e5 if self._vapor_pressure_units == 'bar' else 1.0
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
