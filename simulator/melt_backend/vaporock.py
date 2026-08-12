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
This adapter is intended to support the diagnostic VapoRock shadow beside
the builtin Antoine/Ellingham ``VAPOR_PRESSURE`` provider.  See also
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
VapoRock is diagnostic-only in active kernel wiring. Builtin
Antoine/Ellingham supplies the authoritative ``VAPOR_PRESSURE`` dict
consumed by evaporation; VapoRock may run beside it as a shadow surface.

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
VapoRock has no ``AtomLedger`` policy and must not be granted ledger
authority. The historical goal name ``VAPOROCK-AUTHORITY-PROMOTION`` remains
for compatibility only;
VapoRock is diagnostic-only for ``VAPOR_PRESSURE``. ``equilibrate()``
consumes only the cleaned
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
import os
import re
import tempfile
import threading
import warnings
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from engines.domain_reason import OutOfDomainReason
from simulator.engine_pool import (
    EngineWorkerPool,
    EngineWorkerRemoteError,
    WarmEngineWorker,
)
from simulator.melt_backend.base import (
    CLEANED_MELT_ACCOUNT,
    DEFAULT_BACKEND_CAPABILITIES,
    EquilibriumResult,
    MeltBackend,
    projection_diagnostics_for_melt_input,
    project_melt_to_oxide_projection,
    split_cleaned_melt_account,
)
from simulator.melt_backend.melt_envelope import melt_extrapolation_diagnostic
from simulator.state import OXIDE_SPECIES


# ---------------------------------------------------------------------------
# External validation domain (DESIGN-REV5 §4.2.1; VR-5)
# ---------------------------------------------------------------------------
# Load-bearing gate: the 2026-07-31 probe
# (docs-private/research/2026-07-31-vaporock-probe/findings.md) found zero
# typed refusals from 1200 K through 10000 K. At 10000 K the engine returned
# smooth finite fabrications with total finite partial pressure ~8.3e5 bar.
# A finite provider return is therefore no evidence of domain validity.
VAPOROCK_T_MIN_K = 1350.0
VAPOROCK_T_MAX_K = 1950.0
_MELT_MODEL_ID = 'MELTS-v1.0'
# In-domain mare totals are ≪ 1 bar even at 1950 K / reducing fO2. Ten bar
# is a conservative sum-pressure sanity ceiling well below the probe's
# 10000 K garbage (~8.3e5 bar) and well above any admitted-grid total.
VAPOROCK_MAX_SUM_PRESSURE_BAR = 10.0
# First-order liquid gate (DESIGN-REV5 §5.2): when a caller supplies a
# liquid_fraction from the melt backend, refuse sub-liquid cells rather
# than fabricating vapor over a mostly-solid assemblage.
VAPOROCK_MIN_LIQUID_FRACTION = 0.95
# Probe fixture (findings.md / raw_results.json range_behaviour T=10000):
# sum_P_bar_finite ≈ 8.323e5 bar. Used only as a regression anchor.
VAPOROCK_PROBE_10000K_SUM_P_BAR = 8.323344495585738e5

VAPOROCK_WARM_CALL_TIMEOUT_S = 60.0
VAPOROCK_WORKER_STARTUP_TIMEOUT_S = 60.0
# DESIGN-REV5 §5.5: while reuse_system is admitted, the warm worker
# compares reused-System output against a fresh System for the first N
# reuse hits. Any mismatch latches fresh-per-request for the rest of the
# worker lifetime. Absolute tolerance is on log10(bar) values (decades).
VAPOROCK_REUSE_EQUIVALENCE_PROBE_LIMIT = 20
VAPOROCK_REUSE_EQUIVALENCE_ATOL_LOG10 = 1e-6


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

# VapoRock consumes the same oxide basis as MELTS / alphaMELTS plus volatile
# melt components that upstream declares (H2O/CO2). Project 1:1 by name and
# drop components VapoRock does not declare.
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
_VAPOROCK_MELT_BASIS = tuple(dict.fromkeys((*OXIDE_SPECIES, 'H2O', 'CO2')))


def temperature_C_to_K(temperature_C: float) -> float:
    """Convert the adapter's Celsius melt temperature to Kelvin."""
    return float(temperature_C) + 273.15


def vaporock_temperature_in_domain(temperature_K: float) -> bool:
    """True iff *temperature_K* lies in the admitted VapoRock envelope."""
    try:
        t = float(temperature_K)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(t):
        return False
    return VAPOROCK_T_MIN_K <= t <= VAPOROCK_T_MAX_K


def vaporock_liquid_fraction_admitted(
    liquid_fraction: Optional[float],
) -> bool:
    """True when liquid gate is not applicable or the fraction passes.

    ``None`` means the caller did not supply a melt-assemblage liquid
    fraction; the gate does not invent one. When supplied, the fraction
    must be finite and at least ``VAPOROCK_MIN_LIQUID_FRACTION``.
    """
    if liquid_fraction is None:
        return True
    try:
        value = float(liquid_fraction)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(value):
        return False
    return value >= VAPOROCK_MIN_LIQUID_FRACTION


def vaporock_sum_pressure_bar(
    pressures_Pa: Mapping[str, float],
) -> float:
    """Sum finite positive partial pressures and return the total in bar."""
    total_pa = 0.0
    for value in pressures_Pa.values():
        try:
            pressure = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(pressure) and pressure > 0.0:
            total_pa += pressure
    return total_pa / 1e5


def vaporock_sum_pressure_sane(
    pressures_Pa: Mapping[str, float],
    *,
    max_sum_bar: float = VAPOROCK_MAX_SUM_PRESSURE_BAR,
) -> tuple[bool, float]:
    """Return ``(admitted, sum_bar)`` for the sum-pressure sanity gate."""
    sum_bar = vaporock_sum_pressure_bar(pressures_Pa)
    if not math.isfinite(sum_bar):
        return False, sum_bar
    return sum_bar <= float(max_sum_bar), sum_bar


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


def _serialize_log10_bar_pressures(raw: Any) -> Dict[str, float]:
    """Flatten upstream log10(bar) output to a plain ``species → float`` dict.

    Used inside the warm worker so the pipe only carries pickle-safe
    primitives (pandas DataFrames do not need to cross the process
    boundary).
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
            # P3 disposition (review-vr5-km P3-2): empty return keeps the
            # pre-existing in-process shape (silent empty speciation →
            # non_authoritative). A typed worker error tag is deferred;
            # output remains diagnostic-only for SC-50 consumers.
            return {}
    elif isinstance(raw, dict):
        items = raw.items()
    else:
        return {}
    out: Dict[str, float] = {}
    for species, log10_bar in items:
        try:
            value = float(log10_bar)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            out[str(species)] = value
    return out


def _log10_bar_maps_equivalent(
    left: Mapping[str, float],
    right: Mapping[str, float],
    *,
    atol: float = VAPOROCK_REUSE_EQUIVALENCE_ATOL_LOG10,
) -> bool:
    """True iff two log10(bar) maps match species-for-species within *atol*.

    Used by the warm-worker reuse probe (DESIGN-REV5 §5.5). Missing keys,
    extra keys, non-finite values, or any |Δlog10| > atol are mismatches.
    """
    left_keys = set(left)
    right_keys = set(right)
    if left_keys != right_keys:
        return False
    for species in left_keys:
        try:
            a = float(left[species])
            b = float(right[species])
        except (TypeError, ValueError):
            return False
        if not (math.isfinite(a) and math.isfinite(b)):
            return False
        if abs(a - b) > float(atol):
            return False
    return True


def _eval_system_log10_bar(
    system: Any,
    *,
    composition_wt_pct: Mapping[str, float],
    temperature_K: float,
    fO2_log: float,
) -> Dict[str, float]:
    """Run set_melt_comp + eval_gas_abundances on *system*; return log10 map."""
    set_melt_comp = getattr(system, 'set_melt_comp')
    eval_gas_abundances = getattr(system, 'eval_gas_abundances')
    set_melt_comp(dict(composition_wt_pct))
    # Upstream accepts optional P but our adapter never passes total
    # pressure (diagnostic-only; vaporock.py historically ignored it).
    logP = eval_gas_abundances(float(temperature_K), float(fO2_log))
    return _serialize_log10_bar_pressures(logP)


def _construct_system(resource: dict[str, Any]) -> Any:
    """Construct a new System and bump the worker construct counter."""
    system_cls = resource['system_cls']
    system = system_cls()
    resource['system_construct_count'] = (
        int(resource.get('system_construct_count') or 0) + 1
    )
    return system


def _latch_reuse_fresh(
    resource: dict[str, Any],
    *,
    reason: str,
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Permanently disable System reuse for this worker (DESIGN-REV5 §5.5).

    Clears the stored System so subsequent requests cannot accidentally
    re-use a residue-carrying instance. Returns the status-bearing
    diagnostic payload fragment.
    """
    resource['reuse_latched_fresh'] = True
    resource['system'] = None
    diagnostic: dict[str, Any] = {
        'reuse_latched_fresh': True,
        'reason': str(reason),
    }
    if detail:
        diagnostic['detail'] = dict(detail)
    resource['reuse_mismatch_diagnostic'] = diagnostic
    return diagnostic


def _bootstrap_vaporock_worker(
    temperature_units: str,
    vapor_pressure_units: str,
    reuse_system: bool,
) -> tuple[dict[str, Any], str]:
    """Import VapoRock inside the killable child; own the System lifecycle.

    Fresh ``System`` per request is the default (``reuse_system=False``).
    Reuse is only enabled after equivalence against fresh-System evaluation
    over the admitted grid has been proven (DESIGN-REV5 §5.5). Even when
    the bootstrap flag is True, the handler re-probes reused-vs-fresh on
    the first N reuse hits and latches fresh-per-request on any mismatch.
    """
    module = None
    errors: list[str] = []
    for module_name in _IMPORT_CANDIDATES:
        try:
            module = importlib.import_module(module_name)
            break
        except Exception as exc:  # noqa: BLE001
            errors.append(f'{module_name}: {exc}')
    if module is None:
        raise RuntimeError(
            'VapoRock import failed in warm worker: ' + '; '.join(errors)
        )
    system_cls = getattr(module, 'System', None)
    resource: dict[str, Any] = {
        'module': module,
        'system_cls': system_cls if callable(system_cls) else None,
        'system': None,
        'temperature_units': str(temperature_units),
        'vapor_pressure_units': str(vapor_pressure_units),
        # Bootstrap intent; may be overridden by reuse_latched_fresh.
        'reuse_system': bool(reuse_system),
        'system_construct_count': 0,
        # §5.5 in-worker equivalence probe state.
        'reuse_equivalence_checks_done': 0,
        'reuse_latched_fresh': False,
        'reuse_mismatch_diagnostic': None,
    }
    return resource, 'vaporock-ready'


def _handle_vaporock_request(
    resource: dict[str, Any],
    request: Mapping[str, Any],
    _errlog: Any,
) -> dict[str, Any]:
    """Evaluate one VapoRock request inside the warm worker.

    Default: construct a fresh ``System`` every call. When
    ``reuse_system`` is True and reuse has not been latched off, the
    worker keeps a single mutable System and re-calls ``set_melt_comp``.

    DESIGN-REV5 §5.5 hard condition: while reuse is active, the first
    ``VAPOROCK_REUSE_EQUIVALENCE_PROBE_LIMIT`` reuse hits also evaluate a
    fresh System on the same inputs; any mismatch beyond tolerance latches
    fresh-per-request for the remainder of the worker lifetime and emits a
    status-bearing diagnostic on the response.
    """
    composition_wt_pct = dict(request['composition_wt_pct'])
    temperature_K = float(request['temperature_K'])
    fO2_log = float(request['fO2_log'])
    system_cls = resource.get('system_cls')
    if system_cls is None:
        raise RuntimeError(
            'VapoRock warm worker has no System entry point'
        )

    latched = bool(resource.get('reuse_latched_fresh'))
    want_reuse = bool(resource.get('reuse_system')) and not latched
    stored = resource.get('system') if want_reuse else None
    reuse_mismatch: dict[str, Any] | None = None
    reuse_mode: str

    if stored is not None:
        # Actual reuse path — optionally probe against a fresh System.
        checks_done = int(resource.get('reuse_equivalence_checks_done') or 0)
        probe = checks_done < int(VAPOROCK_REUSE_EQUIVALENCE_PROBE_LIMIT)
        reused_log = _eval_system_log10_bar(
            stored,
            composition_wt_pct=composition_wt_pct,
            temperature_K=temperature_K,
            fO2_log=fO2_log,
        )
        if probe:
            fresh_system = _construct_system(resource)
            fresh_log = _eval_system_log10_bar(
                fresh_system,
                composition_wt_pct=composition_wt_pct,
                temperature_K=temperature_K,
                fO2_log=fO2_log,
            )
            resource['reuse_equivalence_checks_done'] = checks_done + 1
            if not _log10_bar_maps_equivalent(reused_log, fresh_log):
                # Null hypothesis: if latch is absent, residue-carrying
                # reuse continues silently. Latch + return the fresh map.
                reuse_mismatch = _latch_reuse_fresh(
                    resource,
                    reason='reused_vs_fresh_mismatch',
                    detail={
                        'probe_index': checks_done + 1,
                        'temperature_K': temperature_K,
                        'fO2_log': fO2_log,
                        'atol_log10': VAPOROCK_REUSE_EQUIVALENCE_ATOL_LOG10,
                        'reused_species': sorted(reused_log),
                        'fresh_species': sorted(fresh_log),
                    },
                )
                log10_bar = fresh_log
                reuse_mode = 'fresh_latched'
            else:
                # Probe passed: keep the stored System as the reuse target.
                # (fresh_system is discarded — construct count still rose.)
                log10_bar = reused_log
                reuse_mode = 'reused_probed'
        else:
            log10_bar = reused_log
            reuse_mode = 'reused'
    else:
        system = _construct_system(resource)
        if want_reuse:
            resource['system'] = system
        log10_bar = _eval_system_log10_bar(
            system,
            composition_wt_pct=composition_wt_pct,
            temperature_K=temperature_K,
            fO2_log=fO2_log,
        )
        reuse_mode = 'fresh_latched' if latched else (
            'fresh_seed' if want_reuse else 'fresh'
        )

    payload: dict[str, Any] = {
        'log10_bar': log10_bar,
        'path': 'system',
        'system_construct_count': int(
            resource.get('system_construct_count') or 0
        ),
        'reuse_mode': reuse_mode,
        'reuse_latched_fresh': bool(resource.get('reuse_latched_fresh')),
    }
    if reuse_mismatch is not None:
        payload['reuse_mismatch'] = reuse_mismatch
    elif resource.get('reuse_mismatch_diagnostic') is not None:
        # Surface the latched diagnostic on later calls too so the parent
        # can keep status-bearing provenance without re-probing.
        payload['reuse_mismatch'] = dict(resource['reuse_mismatch_diagnostic'])
    return payload


_VAPOROCK_RUNTIME_AVAILABLE_CACHE: bool | None = None


def vaporock_runtime_available() -> bool:
    """Return the same adapter availability signal runtime fallback uses.

    This pays one adapter initialisation per process and performs no
    equilibrium solve. Force-builtin optimizer runs short-circuit before this
    probe, while live VapoRock runs initialise the adapter during execution
    anyway, so the probe adds no net cost on the VapoRock path.

    Availability probes always disable the warm pool: spawning an isolated
    worker just to check importability is wasteful, and unit tests that
    monkeypatch the import only affect the parent process.
    """
    global _VAPOROCK_RUNTIME_AVAILABLE_CACHE
    if _VAPOROCK_RUNTIME_AVAILABLE_CACHE is True:
        return True
    backend = VapoRockBackend()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            initialized = backend.initialize({'warm_worker': False})
    except Exception:  # noqa: BLE001 - mirrors provider boundary catch
        return False
    if not initialized:
        return False
    available = backend.is_available()
    if available:
        _VAPOROCK_RUNTIME_AVAILABLE_CACHE = True
    try:
        backend.close()
    except Exception:  # noqa: BLE001
        pass
    return available


def effective_vapor_pressure_provider_selection() -> str:
    """Stable token for the branch used by MELTS-family vapor projection."""

    return "vaporock" if vaporock_runtime_available() else "activity-antoine"


def _clear_vaporock_runtime_available_cache() -> None:
    global _VAPOROCK_RUNTIME_AVAILABLE_CACHE
    _VAPOROCK_RUNTIME_AVAILABLE_CACHE = None


vaporock_runtime_available.cache_clear = (  # type: ignore[attr-defined]
    _clear_vaporock_runtime_available_cache
)


# ---------------------------------------------------------------------------
# Process-scoped warm-boot cache (CI-speed; golden-neutral)
# ---------------------------------------------------------------------------
# VR-5 warm pools are opt-in per backend instance. Test suites that construct
# many short-lived VapoRockProvider shadows otherwise pay a cold import (or a
# fresh warm-pool spawn) on every construction. When enabled, one warm backend
# is shared for identical initialize configs within the process. Equilibrate
# results are NOT cached — only the boot/import/worker lifecycle.
#
# Enable with REGOLITH_VAPOROCK_SESSION_WARM=1 (pytest conftest sets this by
# default). Availability probes and monkeypatched unit tests keep warm_worker
# False and never enter this path.
SESSION_WARM_ENV = 'REGOLITH_VAPOROCK_SESSION_WARM'
_SESSION_BACKEND_CACHE: Dict[tuple, 'VapoRockBackend'] = {}
_SESSION_BACKEND_LOCK = threading.Lock()


def session_warm_enabled() -> bool:
    """True when process-scoped VapoRock warm-boot sharing is requested."""
    raw = os.environ.get(SESSION_WARM_ENV, '').strip().lower()
    return raw in ('1', 'true', 'yes', 'on')


def _session_backend_cache_key(config: Mapping[str, Any]) -> tuple:
    """Stable hashable key for initialize-config identity."""
    items: list[tuple[str, Any]] = []
    for key in sorted(config.keys()):
        value = config[key]
        if isinstance(value, (str, int, float, bool)) or value is None:
            items.append((str(key), value))
        else:
            items.append((str(key), repr(value)))
    return tuple(items)


def get_or_create_session_backend(
    config: Optional[Mapping[str, Any]] = None,
) -> Optional['VapoRockBackend']:
    """Return a process-scoped warm ``VapoRockBackend`` for *config*.

    Golden-neutral: callers share the VR-5 warm pool for identical boots;
    each ``equilibrate`` still runs through the worker. Returns ``None`` when
    session warm is disabled or initialization fails (caller falls back to a
    private cold backend).
    """
    if not session_warm_enabled():
        return None

    cfg: Dict[str, Any] = dict(config or {})
    # Session sharing only pays off with a warm pool; force the VR-5 path.
    cfg['warm_worker'] = True
    key = _session_backend_cache_key(cfg)

    with _SESSION_BACKEND_LOCK:
        existing = _SESSION_BACKEND_CACHE.get(key)
        if existing is not None:
            if existing.is_available():
                return existing
            # Cache hit but backend is no longer available: close the dead
            # instance before replacing it (N4a). Worker is already gone;
            # close() is still the correct teardown for any residual state.
            try:
                existing.close()
            except Exception:  # noqa: BLE001
                pass
            _SESSION_BACKEND_CACHE.pop(key, None)

        backend = VapoRockBackend()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                ok = backend.initialize(cfg)
        except Exception:  # noqa: BLE001 - mirrors provider boundary catch
            try:
                backend.close()
            except Exception:  # noqa: BLE001
                pass
            return None
        if not ok or not backend.is_available():
            try:
                backend.close()
            except Exception:  # noqa: BLE001
                pass
            return None
        _SESSION_BACKEND_CACHE[key] = backend
        return backend


def clear_session_backend_cache() -> None:
    """Close and drop every process-scoped warm backend (session teardown)."""
    with _SESSION_BACKEND_LOCK:
        backends = list(_SESSION_BACKEND_CACHE.values())
        _SESSION_BACKEND_CACHE.clear()
    for backend in backends:
        try:
            backend.close()
        except Exception:  # noqa: BLE001
            pass


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
        self._last_pressure_authority_warning: Optional[str] = None
        # Warm pool (DESIGN-REV5 §5.5 / VR-5). Opt-in: calibration runners
        # and live warm-path tests pass warm_worker=True. Default off so
        # the diagnostic shadow and corpus harnesses do not spawn a pool
        # per simulator instance. Spawn children do not see parent
        # monkeypatches — fake-import unit tests must also keep the
        # in-process path (warm_worker=False).
        self._warm_worker_enabled: bool = False
        self._reuse_system: bool = False
        self._warm_pool: Optional[EngineWorkerPool] = None
        self._warm_pool_size: int = 1
        self._warm_call_timeout_s: float = VAPOROCK_WARM_CALL_TIMEOUT_S
        self._worker_startup_timeout_s: float = (
            VAPOROCK_WORKER_STARTUP_TIMEOUT_S
        )

    # ------------------------------------------------------------------
    # MeltBackend interface
    # ------------------------------------------------------------------

    def initialize(self, config: dict) -> bool:
        """
        Lazy-import VapoRock and stash configuration.

        Returns True only if the upstream library imports cleanly.
        Never raises — a missing library is a normal "not available"
        outcome.

        When ``warm_worker`` is True the import is owned by an isolated
        warm worker and the parent keeps only a thin transport. Default
        is False (in-process import) so diagnostic shadows and unit tests
        stay lightweight; calibration runners opt in explicitly.
        """
        self.close()
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

        self._warm_worker_enabled = bool(
            self._config.get('warm_worker', False)
        )
        self._reuse_system = bool(self._config.get('reuse_system', False))
        pool_size = int(self._config.get('warm_pool_size', 1))
        if pool_size <= 0:
            self._last_error = 'VapoRock warm_pool_size must be positive'
            self._warnings.append(self._last_error)
            return False
        self._warm_pool_size = pool_size
        warm_timeout = float(
            self._config.get(
                'warm_call_timeout_s', VAPOROCK_WARM_CALL_TIMEOUT_S
            )
        )
        if not math.isfinite(warm_timeout) or warm_timeout <= 0.0:
            self._last_error = (
                'VapoRock warm_call_timeout_s must be finite and positive'
            )
            self._warnings.append(self._last_error)
            return False
        self._warm_call_timeout_s = warm_timeout
        startup_timeout = float(
            self._config.get(
                'worker_startup_timeout_s',
                VAPOROCK_WORKER_STARTUP_TIMEOUT_S,
            )
        )
        if not math.isfinite(startup_timeout) or startup_timeout <= 0.0:
            self._last_error = (
                'VapoRock worker_startup_timeout_s must be finite and positive'
            )
            self._warnings.append(self._last_error)
            return False
        self._worker_startup_timeout_s = startup_timeout

        if self._warm_worker_enabled:
            return self._initialize_warm_pool()

        module = self._import_vaporock()
        if module is None:
            return False
        self._vaporock = module
        self._available = True
        return True

    def _initialize_warm_pool(self) -> bool:
        """Start the isolated warm pool that owns the VapoRock import."""
        diagnostic_path = Path(
            tempfile.gettempdir(),
            'regolith-pyrolysis-simulator',
            'vaporock-diagnostics.log',
        )

        def worker_factory(index: int) -> WarmEngineWorker:
            return WarmEngineWorker(
                name=f'VapoRock warm pool slot {index}',
                bootstrap=_bootstrap_vaporock_worker,
                handler=_handle_vaporock_request,
                bootstrap_args=(
                    self._temperature_units,
                    self._vapor_pressure_units,
                    self._reuse_system,
                ),
                startup_timeout_s=self._worker_startup_timeout_s,
                call_timeout_s=self._warm_call_timeout_s,
                diagnostic_log_path=diagnostic_path.with_name(
                    f'{diagnostic_path.stem}-{index}{diagnostic_path.suffix}'
                ),
            )

        try:
            self._warm_pool = EngineWorkerPool(
                worker_factory,
                size=self._warm_pool_size,
            )
        except EngineWorkerRemoteError as exc:
            self._last_error = (
                f'VapoRock warm pool failed to initialize: {exc.detail}'
            )
            self._warnings.append(self._last_error)
            self._warm_pool = None
            return False
        except Exception as exc:  # noqa: BLE001
            self._last_error = (
                f'VapoRock warm pool failed to initialize: {exc}'
            )
            self._warnings.append(self._last_error)
            self._warm_pool = None
            return False
        self._vaporock = None  # import lives in the child only
        self._available = True
        return True

    def close(self) -> None:
        """Shut down any warm pool; safe to call repeatedly."""
        if self._warm_pool is not None:
            try:
                self._warm_pool.close(cancel_pending=True)
            except Exception:  # noqa: BLE001
                pass
            self._warm_pool = None
        self._available = False
        self._vaporock = None

    def is_available(self) -> bool:
        return self._available

    @property
    def uses_warm_pool(self) -> bool:
        """True when equilibrate dispatches through an isolated warm worker."""
        return self._warm_pool is not None

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
        the melt-debit/gas-credit transition stay with the builtin engine.
        The ``VAPOROCK-AUTHORITY-PROMOTION`` name is historical only.
        Returning an empty tuple
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
        liquid_fraction: Optional[float] = None,
    ) -> EquilibriumResult:
        """
        Call VapoRock for vapor-melt equilibrium.

        Conforms to the layered ``MeltBackend`` ABC: when
        ``composition_mol_by_account`` is supplied, only the
        ``process.cleaned_melt`` account is consumed — gas, metal, salt,
        sulfide and halide accounts are filtered out before the library
        is called (binding spec §7).  The melt composition is then
            projected to VapoRock's MELTS-compatible oxide/volatile wt% basis.

        External domain gate (DESIGN-REV5 §4.2.1 / VR-5; load-bearing):
        refuse outside 1350–1950 K, on liquid-fraction failure when a
        fraction is supplied, on projection/non-basis failure, and on
        sum-pressure sanity failure after the engine returns. Upstream
        fabricates smooth finite garbage at extreme T (probe: ~8.3e5 bar
        total at 10000 K) and never self-refuses.

        ``pressure_bar`` remains diagnostic-only: the System path does not
        pass total pressure through to the engine.

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
        temperature_K = temperature_C_to_K(temperature_C)
        melt_envelope_diagnostics = melt_extrapolation_diagnostic(
            temperature_K,
            _MELT_MODEL_ID,
        )

        if not self._available or (
            self._warm_pool is None and self._vaporock is None
        ):
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status='unavailable',
                warnings=['VapoRock backend not initialized'],
                diagnostics=melt_envelope_diagnostics,
            )

        prior_warnings: List[str] = []

        # --- HARD external temperature gate (before any engine call) ---
        if not vaporock_temperature_in_domain(temperature_K):
            diagnostics = {
                **melt_envelope_diagnostics,
                'backend_status_reason': (
                    OutOfDomainReason.TEMPERATURE_RANGE.value
                ),
                **(
                    {'temperature_K': float(temperature_K)}
                    if math.isfinite(temperature_K)
                    else {'temperature_input_category': 'non_finite'}
                ),
                'vaporock_t_min_K': VAPOROCK_T_MIN_K,
                'vaporock_t_max_K': VAPOROCK_T_MAX_K,
                'requested_pressure_bar': float(pressure_bar),
                'pressure_control_authoritative': False,
            }
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                liquid_fraction=liquid_fraction,
                phase_assemblage_available=False,
                status='out_of_domain',
                warnings=[
                    f'VapoRock refused T={temperature_K:g} K outside '
                    f'admitted domain '
                    f'[{VAPOROCK_T_MIN_K:g}, {VAPOROCK_T_MAX_K:g}] K '
                    '(external domain gate; upstream fabricates finite '
                    'garbage outside this envelope)'
                ],
                diagnostics=diagnostics,
            )

        # --- Liquid-state gate when the melt backend supplied a fraction ---
        if not vaporock_liquid_fraction_admitted(liquid_fraction):
            # Diagnostics must not re-raise on non-floatable values: the
            # admit helper already refused them (review-vr5-km P3-1). Only
            # format a float after a successful coercion.
            liquid_diag: Any
            if liquid_fraction is None:
                liquid_diag = None
            else:
                try:
                    coerced = float(liquid_fraction)
                except (TypeError, ValueError):
                    liquid_diag = repr(liquid_fraction)
                else:
                    liquid_diag = (
                        coerced if math.isfinite(coerced) else repr(liquid_fraction)
                    )
            diagnostics = {
                **melt_envelope_diagnostics,
                'backend_status_reason': (
                    OutOfDomainReason.LIQUID_STATE.value
                ),
                'liquid_fraction': liquid_diag,
                'vaporock_min_liquid_fraction': VAPOROCK_MIN_LIQUID_FRACTION,
                'temperature_K': float(temperature_K),
                'requested_pressure_bar': float(pressure_bar),
                'pressure_control_authoritative': False,
            }
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                # EquilibriumResult may require float|None; keep caller
                # value only when coercible so the typed field stays sane.
                liquid_fraction=(
                    liquid_diag if isinstance(liquid_diag, float) else None
                ),
                phase_assemblage_available=False,
                status='out_of_domain',
                warnings=[
                    'VapoRock refused liquid_fraction='
                    f'{liquid_fraction!r} below admitted minimum '
                    f'{VAPOROCK_MIN_LIQUID_FRACTION:g} '
                    '(external liquid-state domain gate)'
                ],
                diagnostics=diagnostics,
            )

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
            oxide_basis=_VAPOROCK_MELT_BASIS,
            species_formula_registry=species_formula_registry,
        )
        comp_wt = projection.oxide_wt_pct
        prior_warnings.extend(projection.warnings)
        projection_diagnostics = projection_diagnostics_for_melt_input(
            backend='VapoRock',
            projection=projection,
            composition_kg=composition_kg,
            composition_mol=composition_mol,
            oxide_basis=_VAPOROCK_MELT_BASIS,
            species_formula_registry=species_formula_registry,
            dropped_accounts=dropped_accounts,
            dropped_account_species=dropped_account_species,
        )
        projection_diagnostics = {
            **melt_envelope_diagnostics,
            **projection_diagnostics,
        }
        if (
            projection.dropped_mass_kg_by_species
            or dropped_accounts
            or dropped_account_species
        ):
            diagnostics = dict(projection_diagnostics)
            diagnostics['backend_status_reason'] = (
                OutOfDomainReason.FORBIDDEN_SPECIES.value
            )
            diagnostics['temperature_K'] = float(temperature_K)
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                liquid_fraction=liquid_fraction,
                phase_assemblage_available=False,
                status='out_of_domain',
                warnings=[
                    *prior_warnings,
                    'VapoRock refused projected/dropped non-basis melt input',
                ],
                diagnostics=diagnostics,
            )
        if not comp_wt:
            # No oxide species in VapoRock's basis after the account
            # split; the vapor-melt solver has nothing valid to consume.
            diagnostics = dict(projection_diagnostics)
            diagnostics['backend_status_reason'] = (
                OutOfDomainReason.FORBIDDEN_SPECIES.value
            )
            diagnostics['temperature_K'] = float(temperature_K)
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                liquid_fraction=liquid_fraction,
                status='out_of_domain',
                warnings=[
                    *prior_warnings,
                    'VapoRock received empty melt composition; returning empty '
                    'equilibrium result',
                ],
                diagnostics=diagnostics,
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
                liquid_fraction=liquid_fraction,
                status='not_converged',
                warnings=[*prior_warnings, message],
                diagnostics=projection_diagnostics,
            )

        # --- Sum-pressure sanity (defense against silent fabrication) ---
        sum_ok, sum_bar = vaporock_sum_pressure_sane(
            vaporock_full_speciation_Pa
        )
        if not sum_ok:
            diagnostics = dict(projection_diagnostics)
            diagnostics.update({
                'backend_status_reason': (
                    OutOfDomainReason.SUM_PRESSURE_SANITY.value
                ),
                'sum_pressure_bar': float(sum_bar),
                'vaporock_max_sum_pressure_bar': VAPOROCK_MAX_SUM_PRESSURE_BAR,
                'temperature_K': float(temperature_K),
                'requested_pressure_bar': float(pressure_bar),
                'pressure_control_authoritative': False,
            })
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                liquid_fraction=liquid_fraction,
                phase_assemblage_available=False,
                status='out_of_domain',
                warnings=[
                    *prior_warnings,
                    f'VapoRock refused sum partial pressure '
                    f'{sum_bar:g} bar above sanity ceiling '
                    f'{VAPOROCK_MAX_SUM_PRESSURE_BAR:g} bar '
                    '(external sum-pressure domain gate; probe anchor '
                    f'~{VAPOROCK_PROBE_10000K_SUM_P_BAR:.3g} bar at 10000 K)',
                ],
                diagnostics=diagnostics,
            )

        pressure_authority_warning = self._last_pressure_authority_warning
        if pressure_authority_warning:
            prior_warnings.append(pressure_authority_warning)
            projection_diagnostics = dict(projection_diagnostics)
            projection_diagnostics.update({
                'pressure_control_authoritative': False,
                'pressure_control_reason': pressure_authority_warning,
                'requested_pressure_bar': float(pressure_bar),
                'temperature_K': float(temperature_K),
                'sum_pressure_bar': float(sum_bar),
            })
        else:
            projection_diagnostics = dict(projection_diagnostics)
            projection_diagnostics['pressure_control_authoritative'] = True
            projection_diagnostics['temperature_K'] = float(temperature_K)
            projection_diagnostics['sum_pressure_bar'] = float(sum_bar)

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
            liquid_fraction=liquid_fraction,
            phase_assemblage_available=False,
            status=(
                'non_authoritative'
                if pressure_authority_warning
                else 'ok'
            ),
            warnings=list(prior_warnings),
            vapor_pressures_Pa=(
                {}
                if pressure_authority_warning
                else dict(vaporock_full_speciation_Pa)
            ),
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

        When a warm pool is active the isolated worker owns the import and
        ``System`` lifecycle (fresh System per request unless
        ``reuse_system`` was admitted).  The in-process path remains for
        availability probes, fake-import unit tests, and historical
        top-level candidate functions.

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
        self._last_pressure_authority_warning = None
        temperature_K = (
            float(temperature)
            if self._temperature_units == 'K'
            else float(temperature) + 273.15
        )

        if self._warm_pool is not None:
            return self._call_vaporock_via_pool(
                composition_wt_pct=composition_wt_pct,
                temperature_K=temperature_K,
                fO2_log=fO2_log,
            )

        module = self._vaporock
        if module is None:
            raise RuntimeError('VapoRock module is not loaded')
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
                vapor_pressures = self._normalize_vapor_pressures(fn(
                    composition=composition_wt_pct,
                    T_C=temperature if self._temperature_units == 'C' else None,
                    T_K=temperature if self._temperature_units == 'K' else None,
                    P_bar=pressure if self._pressure_units == 'bar' else None,
                    P_Pa=pressure if self._pressure_units == 'Pa' else None,
                    log_fO2=fO2_log,
                ))
                self._last_pressure_authority_warning = (
                    'VapoRock candidate vapor function has unverified total '
                    'pressure authority; requested pressure_bar is '
                    'diagnostic-only and this vapor result is '
                    'non-authoritative for pressure-sensitive transport.'
                )
                return vapor_pressures
            except TypeError as exc:
                # Older builds use positional / shorter signatures.
                # Fall back to a minimal call before declaring failure.
                last_attr_error = exc
                try:
                    vapor_pressures = self._normalize_vapor_pressures(fn(
                        composition_wt_pct,
                        temperature,
                        fO2_log,
                    ))
                    self._last_pressure_authority_warning = (
                        'VapoRock legacy candidate vapor function did not '
                        'accept total pressure; requested pressure_bar is '
                        'diagnostic-only and this vapor result is '
                        'non-authoritative for pressure-sensitive transport.'
                    )
                    return vapor_pressures
                except Exception as inner_exc:  # noqa: BLE001
                    last_attr_error = inner_exc
                    continue

        system_cls = getattr(module, 'System', None)
        if callable(system_cls):
            try:
                # Fresh System per request (in-process path). Reuse is only
                # admitted inside the warm worker after grid equivalence.
                system = system_cls()
                set_melt_comp = getattr(system, 'set_melt_comp')
                eval_gas_abundances = getattr(system, 'eval_gas_abundances')
                # VapoRock's System.set_melt_comp takes the oxide wt%
                # dict positionally; eval_gas_abundances expects an
                # absolute temperature in Kelvin (verified against the
                # installed vaporock build, 2026-05-14).
                set_melt_comp(composition_wt_pct)
                logP = eval_gas_abundances(temperature_K, fO2_log)
                self._last_pressure_authority_warning = (
                    'VapoRock System.eval_gas_abundances ignores total '
                    'pressure; requested pressure_bar is diagnostic-only '
                    'and this vapor result is non-authoritative for '
                    'pressure-sensitive transport.'
                )
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

    def _call_vaporock_via_pool(
        self,
        *,
        composition_wt_pct: Dict[str, float],
        temperature_K: float,
        fO2_log: float,
    ) -> Dict[str, float]:
        """Dispatch one request through the warm pool (fresh System default)."""
        pool = self._warm_pool
        if pool is None:
            raise RuntimeError('VapoRock warm pool is not initialised')
        # pressure_bar is intentionally NOT sent: diagnostic-only; worker
        # never passes total P to eval_gas_abundances.
        request = {
            'composition_wt_pct': dict(composition_wt_pct),
            'temperature_K': float(temperature_K),
            'fO2_log': float(fO2_log),
        }
        future = pool.submit(
            request, timeout_s=self._warm_call_timeout_s
        )
        payload = future.result()
        if not isinstance(payload, dict):
            raise RuntimeError(
                f'VapoRock warm worker returned non-dict payload: '
                f'{type(payload)!r}'
            )
        log10_bar = payload.get('log10_bar') or {}
        self._last_pressure_authority_warning = (
            'VapoRock System.eval_gas_abundances ignores total '
            'pressure; requested pressure_bar is diagnostic-only '
            'and this vapor result is non-authoritative for '
            'pressure-sensitive transport.'
        )
        return self._log10_bar_pressures_to_pa(log10_bar)

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
