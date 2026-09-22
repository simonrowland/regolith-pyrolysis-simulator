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
``pymagemin`` / ``julia`` bridges are opt-in; the binary is the canonical
route.  See
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

from contextlib import contextmanager
import fcntl
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

from engines.domain_reason import OutOfDomainReason
from simulator.melt_backend.base import (
    CLEANED_MELT_ACCOUNT,
    DEFAULT_BACKEND_CAPABILITIES,
    EquilibriumResult,
    MeltBackend,
    MeltCompositionError,
    RealBackendAuthority,
    RealBackendFamily,
    liquid_fraction_from_phase_masses,
    projection_diagnostics_for_melt_input,
    project_melt_to_oxide_projection,
    split_cleaned_melt_account,
)
from simulator.engine_pool import (
    EngineWorkerPool,
    EngineWorkerRemoteError,
    EngineWorkerTimeout,
    WarmEngineWorker,
)
from simulator.melt_backend.liquidus import (
    DEFAULT_LIQUIDUS_FINDER_BUDGET_S,
    LiquidusSampleError,
    LiquidusSolidusResult,
    find_liquidus_solidus_by_fraction,
)
from simulator.melt_backend.pure_phase import (
    GIBBS_CONVENTION_APPARENT_298,
    PHASE_FACTOR_UNAVAILABLE,
    PHASE_NOT_PURE_AT_EQUILIBRIUM,
    PHASE_NOT_STABLE_AT_TP,
    PropertyAbsence,
    PurePhaseAccessError,
    PurePhaseBulkMismatchError,
    PurePhaseProperties,
    PurePhaseUnknownSymbolError,
    PurePhaseUnsupportedDatabaseError,
)
from simulator.state import OXIDE_SPECIES
from simulator.scalar_boundary import is_declared_real_scalar


# 2026-07-23 B1 gate-2 warm-gateway residue: per-call hard wall raised
# 2.0 -> 15.0 s. The 2.0 s wall was calibrated for an uncontended slot-free
# machine; measured under 6-way concurrent MAGEMin load on top of a full
# xdist gate (18 workers, M5 MacBook Pro): p50 = 0.16 s, p90 = 0.41 s,
# p99 = 1.73 s, max = 1.98 s, with 10/767 calls crossing the 2.0 s wall.
# Every crossing surfaced as EngineWorkerTimeout -> provider
# 'not_converged'/'unavailable' -> the freeze-gate curve silently fell back
# from the MAGEMin gate dispatch to the backend/kernel liquidus source,
# shifting wall-deposit/redox values ~0.1-0.5 % on warm xdist gateways
# (the coating-SHA / split_path[lunar] drift class). The timeout value does
# NOT touch physics: warm and cold subprocess results are byte-identical
# (tests/test_engine_worker_live_determinism.py A/B), so this only moves
# when the hard wall fires. 15.0 s gives ~8x headroom over the measured p99
# while staying well inside the 60.0 s finder aggregate budget below.
MAGEMIN_WARM_CALL_TIMEOUT_S = 15.0
# 2026-07-23 B1 gate-2 (same drift class as the call wall above): the warm
# liquidus-finder aggregate budget raised 15.0 -> 60.0 s. Measured full
# freeze-gate curve scans under the same 6-way + full-gate load: p50 =
# 12.7 s, p90 = max = 14.8 s completing, with 3/15 scans exhausting the
# 15.0 s budget at ~15.3 s -> 'not_converged' -> silent freeze-gate curve
# source fallback. 60.0 s is ~4x the measured scan max while still far
# below the cold/diagnostic DEFAULT_LIQUIDUS_FINDER_BUDGET_S (300 s) that
# bounds the spinel-hang class; like the call wall it moves only when the
# budget fires, never the physics of a completed scan.
MAGEMIN_WARM_LIQUIDUS_BUDGET_S = 60.0
_MAGEMIN_SUBPROCESS_LOCK_DIR = Path(
    tempfile.gettempdir(),
    'regolith-pyrolysis-simulator',
)
_MAGEMIN_SUBPROCESS_LOCK = _MAGEMIN_SUBPROCESS_LOCK_DIR / (
    f'magemin-subprocess-{os.getuid()}.lock'
)


def _magemin_subprocess_slot_count() -> int:
    """Bounded machine-wide MAGEMin binary concurrency (K slots).

    Each MAGEMin CLI call runs in a private TemporaryDirectory — there is
    no shared mutable state between invocations, so the old exclusive lock
    (K=1) was a CPU-contention bound, not a correctness requirement (t-385
    lock audit, 2026-07-22). K>1 lets independent heavy tests overlap
    instead of paying each other's wall-clock. Determinism under bounded K
    is verified by the split_path concurrent A/B against sequential pins
    (rel=1e-12). Override with REGOLITH_MAGEMIN_SLOTS; K=1 restores strict
    serialization.
    """
    raw = os.environ.get('REGOLITH_MAGEMIN_SLOTS', '')
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if value >= 1:
        return value
    cores = os.cpu_count() or 4
    return max(1, min(3, cores // 6))


@contextmanager
def _magemin_subprocess_slot(timeout_s: float) -> Iterator[None]:
    """Acquire one of K machine-wide MAGEMin launch slots (see above)."""
    deadline = time.monotonic() + max(0.001, float(timeout_s))
    _MAGEMIN_SUBPROCESS_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    slots = _magemin_subprocess_slot_count()
    lock_files = []
    acquired_index = -1
    try:
        for index in range(slots):
            path = (
                _MAGEMIN_SUBPROCESS_LOCK if index == 0
                else _MAGEMIN_SUBPROCESS_LOCK.with_suffix(f'.slot{index}')
            )
            lock_files.append(path.open('a+b'))
        while True:
            for index, lock_file in enumerate(lock_files):
                try:
                    fcntl.flock(
                        lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                    )
                    acquired_index = index
                    break
                except BlockingIOError:
                    continue
            if acquired_index >= 0:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise RuntimeError(
                    f'MAGEMin subprocess slot timed out after {timeout_s:g}s'
                )
            time.sleep(min(0.01, remaining))
        yield
    finally:
        try:
            if lock_files and acquired_index >= 0:
                fcntl.flock(
                    lock_files[acquired_index].fileno(), fcntl.LOCK_UN
                )
        finally:
            for lock_file in lock_files:
                lock_file.close()


@dataclass(frozen=True)
class _MAGEMinBulkProjection:
    database: str
    order: Tuple[str, ...]
    vector: Tuple[float, ...]
    composition_wt_pct: Dict[str, float]
    warnings: Tuple[str, ...]
    dropped_components: Tuple[str, ...] = ()
    merged_components: Tuple[str, ...] = ()
    source_sum_wt_pct: float = 0.0
    projected_sum_wt_pct: float = 0.0


def _bootstrap_magemin_subprocess_worker(
    binary_path: str,
    database: str,
    config: Mapping[str, Any],
):
    backend = MAGEMinBackend()
    backend._binary_path = Path(binary_path)
    backend._database = str(database)
    backend._config = dict(config)
    backend._bridge = 'subprocess'
    backend._available = True
    return backend, str(binary_path)


def _handle_magemin_subprocess_request(backend, request, _errlog):
    return backend._call_magemin_subprocess(
        bulk_projection=request['bulk_projection'],
        temperature_C=request['temperature_C'],
        pressure_kbar=request['pressure_kbar'],
        fO2_log=request['fO2_log'],
        call_timeout_s=request.get('call_timeout_s'),
    )


def _dropped_account_species(
    composition_mol_by_account: Mapping[str, Mapping[str, float]],
) -> Dict[str, Tuple[str, ...]]:
    result: Dict[str, Tuple[str, ...]] = {}
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


# Notice flag / backend_status_reason. Projection payload still uses
# VapoRock's `input_composition_projected` reason token.
COMPOSITION_PROJECTED = 'composition_projected'


def _magemin_dropped_mass_fraction(
    bulk_projection: _MAGEMinBulkProjection,
) -> float:
    source_sum = float(bulk_projection.source_sum_wt_pct)
    if source_sum <= 0.0:
        return 0.0
    dropped_wt = max(
        0.0,
        source_sum - float(bulk_projection.projected_sum_wt_pct),
    )
    return dropped_wt / source_sum


def _magemin_bulk_projection_details(
    bulk_projection: Optional[_MAGEMinBulkProjection],
) -> Dict[str, Any]:
    if bulk_projection is None:
        return {}

    bulk_dropped = tuple(bulk_projection.dropped_components)
    bulk_merged = tuple(bulk_projection.merged_components)
    details: Dict[str, Any] = {
        'magemin_database': bulk_projection.database,
        'magemin_bulk_projected_components': sorted(
            str(k) for k in bulk_projection.composition_wt_pct
        ),
    }
    if bulk_dropped:
        dropped_mass_fraction = _magemin_dropped_mass_fraction(bulk_projection)
        details['dropped_bulk_components'] = list(bulk_dropped)
        details['dropped_mass_fraction'] = dropped_mass_fraction
        details[COMPOSITION_PROJECTED] = {
            'dropped_components': list(bulk_dropped),
            'dropped_mass_fraction': dropped_mass_fraction,
        }
    if bulk_merged:
        details['merged_bulk_components'] = list(bulk_merged)
    if bulk_projection.source_sum_wt_pct > 0.0:
        details['bulk_source_sum_wt_pct'] = bulk_projection.source_sum_wt_pct
        details['bulk_projected_sum_wt_pct'] = (
            bulk_projection.projected_sum_wt_pct
        )
        details['bulk_dropped_wt_pct'] = max(
            0.0,
            bulk_projection.source_sum_wt_pct
            - bulk_projection.projected_sum_wt_pct,
        )
    return details


# ----------------------------------------------------------------------
# Pure-phase registry and stdout/matlab parsers (diagnostic accessor)
# ----------------------------------------------------------------------

# Minimum endmember wt-fraction for a stable solution phase's S/Cp/H to be
# reported as the pure endmember's properties (else typed absence).
_MAGEMIN_PURITY_MIN = 0.999

# Absolute wt-fraction tolerance for the SYS-row bulk-echo guard.  The KLB1
# fallback moves SiO2 by ~0.38 wt-fraction; the token SiO2=1e-6 used to clear
# the upstream arg_bulk[0]>0 gate moves it 1e-8.  3e-3 sits between.
_MAGEMIN_BULK_ECHO_TOL = 3.0e-3


@dataclass(frozen=True)
class _MAGEMinPurePhaseSpec:
    """How to reach one phase's standard-state properties in the ig DB."""

    host_phase: Optional[str]  # solution host ('ol','fper','opx'); None = PP
    endmember: str             # symbol in the Verb=1 endmember table
    assemblage_phase: str      # name in the matlab stable-assemblage table
    formula: str               # formula unit of the returned mol basis
    formula_basis: str         # basis note incl. divisor derivation
    formula_divisor: float     # engine formula units per `formula` unit
    polymorph: str
    bulk_wt_pct: Mapping[str, float]


# Bulk vectors are wt% in ig oxide names.  Derivation of the stoichiometric
# bulks (CIAAW masses: Mg 24.305, Si 28.085, O 15.999):
#   MgO      40.3044 ; MgSiO3   100.3887 ; Mg2SiO4 140.6931 g/mol
#   enstatite bulk: SiO2 60.0843/100.3887 = 59.8515, MgO 40.3044/100.3887
#   forsterite bulk: SiO2 60.0843/140.6931 = 42.7059, MgO 80.6088/140.6931
# The periclase bulk carries a token SiO2=1e-6 because upstream
# src/toolkit.c retrieve_bulk_PT honors --Bulk only when its first (SiO2)
# component is > 0; zero falls back to the KLB1 test bulk silently (the
# guard below refuses the run if the SYS echo ever disagrees).
_MAGEMIN_PURE_PHASES: Mapping[str, _MAGEMinPurePhaseSpec] = {
    'fo': _MAGEMinPurePhaseSpec(
        host_phase='ol',
        endmember='fo',
        assemblage_phase='ol',
        formula='Mg2SiO4',
        formula_basis='per 1 mol Mg2SiO4',
        formula_divisor=1.0,
        polymorph='forsterite',
        bulk_wt_pct={'SiO2': 42.7059, 'MgO': 57.2941},
    ),
    'per': _MAGEMinPurePhaseSpec(
        host_phase='fper',
        endmember='per',
        assemblage_phase='fper',
        formula='MgO',
        formula_basis='per 1 mol MgO',
        formula_divisor=1.0,
        polymorph='periclase',
        bulk_wt_pct={'SiO2': 1.0e-6, 'MgO': 100.0},
    ),
    # ig opx 'en' endmember is Mg2Si2O6 (HP ds6 orthopyroxene basis), so all
    # per-formula properties are divided by 2 onto the JANAF MgSiO3 basis.
    'en': _MAGEMinPurePhaseSpec(
        host_phase='opx',
        endmember='en',
        assemblage_phase='opx',
        formula='MgSiO3',
        formula_basis='per 1 mol MgSiO3 (engine endmember Mg2Si2O6 / 2)',
        formula_divisor=2.0,
        polymorph='orthoenstatite',
        bulk_wt_pct={'SiO2': 59.8515, 'MgO': 40.1485},
    ),
    'q': _MAGEMinPurePhaseSpec(
        host_phase=None,
        endmember='q',
        assemblage_phase='q',
        formula='SiO2',
        formula_basis='per 1 mol SiO2',
        formula_divisor=1.0,
        polymorph='quartz',
        bulk_wt_pct={'SiO2': 100.0},
    ),
    'crst': _MAGEMinPurePhaseSpec(
        host_phase=None,
        endmember='crst',
        assemblage_phase='crst',
        formula='SiO2',
        formula_basis='per 1 mol SiO2',
        formula_divisor=1.0,
        polymorph='cristobalite',
        bulk_wt_pct={'SiO2': 100.0},
    ),
    'trd': _MAGEMinPurePhaseSpec(
        host_phase=None,
        endmember='trd',
        assemblage_phase='trd',
        formula='SiO2',
        formula_basis='per 1 mol SiO2',
        formula_divisor=1.0,
        polymorph='tridymite',
        bulk_wt_pct={'SiO2': 100.0},
    ),
    # W2 catalogue phases (CIAAW masses consistent with fo/per/en above:
    # Al 26.9815, Mg 24.305, Si 28.085, O 15.999 -> Al2O3 101.961,
    # MgAl2O4 142.2654, Al2SiO5 162.0453). Token SiO2 on Al/Mg-only bulks
    # is the same retrieve_bulk_PT guard used for periclase.
    'cor': _MAGEMinPurePhaseSpec(
        host_phase=None,
        endmember='cor',
        assemblage_phase='cor',
        formula='Al2O3',
        formula_basis='per 1 mol Al2O3',
        formula_divisor=1.0,
        polymorph='corundum',
        bulk_wt_pct={'SiO2': 1.0e-6, 'Al2O3': 100.0},
    ),
    'sp': _MAGEMinPurePhaseSpec(
        host_phase='spn',
        endmember='sp',
        assemblage_phase='spn',
        formula='MgAl2O4',
        formula_basis='per 1 mol MgAl2O4',
        formula_divisor=1.0,
        polymorph='spinel',
        bulk_wt_pct={
            'SiO2': 1.0e-6,
            'Al2O3': 71.6697,
            'MgO': 28.3303,
        },
    ),
    'and': _MAGEMinPurePhaseSpec(
        host_phase=None,
        endmember='and',
        assemblage_phase='and',
        formula='Al2SiO5',
        formula_basis='per 1 mol Al2SiO5',
        formula_divisor=1.0,
        polymorph='andalusite',
        bulk_wt_pct={'SiO2': 37.0781, 'Al2O3': 62.9219},
    ),
    'ky': _MAGEMinPurePhaseSpec(
        host_phase=None,
        endmember='ky',
        assemblage_phase='ky',
        formula='Al2SiO5',
        formula_basis='per 1 mol Al2SiO5',
        formula_divisor=1.0,
        polymorph='kyanite',
        bulk_wt_pct={'SiO2': 37.0781, 'Al2O3': 62.9219},
    ),
    'sill': _MAGEMinPurePhaseSpec(
        host_phase=None,
        endmember='sill',
        assemblage_phase='sill',
        formula='Al2SiO5',
        formula_basis='per 1 mol Al2SiO5',
        formula_divisor=1.0,
        polymorph='sillimanite',
        bulk_wt_pct={'SiO2': 37.0781, 'Al2O3': 62.9219},
    ),
}

_MAGEMIN_PP_GBASE_RE = re.compile(
    r'^\s*([A-Za-z0-9_]+):\s+(-?\d+\.\d+)\s+\+?(-?\d+\.\d+)\s*$'
)
_MAGEMIN_SS_BLOCK_RE = re.compile(r'^\s*([a-z][a-z0-9]*):\s*$')
_MAGEMIN_ASSEMBLAGE_ROW_RE = re.compile(
    r'^\s*\d+\s*\|\s*([A-Za-z0-9_]+)\s*\|([^|]+)\|([^|]+)\|([^|]+)\|'
)


def _parse_magemin_gbase_tables(
    stdout: str,
) -> Tuple[Dict[str, Tuple[float, float]], Dict[str, Dict[str, float]]]:
    """Parse the Verb=1 pre-minimisation gbase table.

    Returns ({pp_name: (gbase_kJ_per_mol_formula, factor)},
             {ss_host: {endmember: gbase_kJ_per_mol_formula}}).
    First occurrence wins; the table is printed once before levelling.
    """
    pp: Dict[str, Tuple[float, float]] = {}
    ss: Dict[str, Dict[str, float]] = {}
    lines = stdout.splitlines()
    for i, line in enumerate(lines):
        m = _MAGEMIN_PP_GBASE_RE.match(line)
        if m:
            pp.setdefault(m.group(1), (float(m.group(2)), float(m.group(3))))
            continue
        m = _MAGEMIN_SS_BLOCK_RE.match(line)
        if (
            m
            and i + 3 < len(lines)
            and lines[i + 1].strip().startswith('----')
            and m.group(1) not in ss
        ):
            names = lines[i + 2].split()
            try:
                values = [float(v) for v in lines[i + 3].split()]
            except ValueError:
                continue
            if len(names) == len(values):
                ss[m.group(1)] = dict(zip(names, values))
    return pp, ss


def _parse_magemin_assemblage_factors(stdout: str) -> Dict[str, float]:
    """Per-phase ``factor`` from the Verb=1 PHASE ASSEMBLAGE table.

    Rows look like::
        1 | fper |  +0.041778 |  +0.000001 |  +1.168795 |  +1.000000 | ...
    i.e. ON | phase | fraction | delta_G | factor | sum_xi | ...
    The table repeats per global iteration; the last occurrence wins.
    """
    factors: Dict[str, float] = {}
    for line in stdout.splitlines():
        m = _MAGEMIN_ASSEMBLAGE_ROW_RE.match(line)
        if not m:
            continue
        try:
            factor = float(m.group(4))
        except ValueError:
            continue
        factors[m.group(1)] = factor
    return factors


def _parse_magemin_matlab_assemblage(
    matlab_text: str,
) -> Dict[str, List[Dict[str, float]]]:
    """Rows of the out_matlab 'Stable mineral assemblage:' table.

    Column bases (upstream headers partly mislabelled; see class notes):
    G and H in kJ per mol formula, Cp in kJ/K per mol formula, Entropy in
    kJ/K scaled by the phase factor.  Returns ordered row lists per phase
    name: compositionally split SS instances print one row per instance, and
    the instance order matches the 'End-members fractions' block.
    """
    rows: Dict[str, List[Dict[str, float]]] = {}
    in_table = False
    for line in matlab_text.splitlines():
        if line.startswith('Stable mineral assemblage:'):
            in_table = True
            continue
        if not in_table:
            continue
        if line.strip().startswith('phase') or not line.strip():
            continue
        tokens = line.split()
        if tokens[0] == 'SYS':
            break
        if len(tokens) < 10:
            continue
        try:
            row = {
                'frac_wt': float(tokens[1]),
                'G_kJ': float(tokens[2]),
                'Cp_kJ_K': float(tokens[5]),
                'S_kJ_K': float(tokens[8]),
                'H_kJ': float(tokens[9]),
            }
        except ValueError:
            continue
        rows.setdefault(tokens[0], []).append(row)
    return rows


def _parse_magemin_endmember_fractions(
    matlab_text: str,
) -> Dict[str, List[Dict[str, float]]]:
    """The matlab 'End-members fractions[wt fr]' block: {phase: [{em: fr}]}.

    One entry per phase INSTANCE, in table order (matching the stable
    assemblage row order).  Header rows carry '-' placeholders over padding
    columns while value rows carry numbers there, so the zip is positional
    and only name != '-' pairs survive.
    """
    fractions: Dict[str, List[Dict[str, float]]] = {}
    in_block = False
    header: List[str] = []
    for line in matlab_text.splitlines():
        if line.startswith('End-members fractions'):
            in_block = True
            continue
        if not in_block:
            continue
        if line.startswith('Site fractions'):
            break
        tokens = line.split()
        if not tokens:
            continue
        is_value_row = any(_is_float_token(t) for t in tokens[1:])
        if is_value_row:
            row = {
                name: float(value)
                for name, value in zip(header, tokens[1:])
                if name != '-' and _is_float_token(value)
            }
            fractions.setdefault(tokens[0], []).append(row)
        else:
            header = tokens
    return fractions


def _is_float_token(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False


def _parse_magemin_sys_oxide_row(
    matlab_text: str,
) -> Optional[Dict[str, float]]:
    """The SYS row of the matlab 'Oxide compositions [wt fr]' block."""
    in_block = False
    header: List[str] = []
    for line in matlab_text.splitlines():
        if line.startswith('Oxide compositions'):
            in_block = True
            continue
        if not in_block:
            continue
        tokens = line.split()
        if not tokens:
            if header:
                break
            continue
        if not header:
            header = tokens
            continue
        if tokens[0] == 'SYS':
            values = [float(t) for t in tokens[1:]]
            if len(values) != len(header):
                return None
            return dict(zip(header, values))
    return None


def _assert_magemin_bulk_echo(
    requested_wt_pct: Mapping[str, float],
    matlab_text: str,
) -> None:
    """KLB1-trap guard: the SYS oxide row must equal the requested bulk.

    Upstream src/toolkit.c honors --Bulk only when its first component
    (SiO2 in ig) is > 0, else silently minimizes the default KLB1 bulk.
    Comparing the matlab SYS row against the request catches that and any
    future bulk-mangling regression.  The 'O' column is excluded: the qfm
    buffer pseudo-phase carries oxygen into the system by design.
    """
    sys_row = _parse_magemin_sys_oxide_row(matlab_text)
    if sys_row is None:
        raise PurePhaseBulkMismatchError(
            'matlab dump lacks a parseable SYS oxide row; cannot verify '
            'the bulk MAGEMin actually ran'
        )
    total = sum(float(v) for v in requested_wt_pct.values())
    if total <= 0.0:
        raise PurePhaseBulkMismatchError('requested bulk sums to zero')
    order = MAGEMinBackend._DB_BULK_ORDERS['ig']
    for ig_name in order:
        if ig_name == 'O':
            continue  # buffer-controlled component
        sys_name = 'FeO' if ig_name == 'FeOt' else ig_name
        got = sys_row.get(sys_name)
        if got is None:
            raise PurePhaseBulkMismatchError(
                f'matlab SYS row lacks oxide column {sys_name!r}'
            )
        want = float(requested_wt_pct.get(ig_name, 0.0)) / total
        if abs(got - want) > _MAGEMIN_BULK_ECHO_TOL:
            raise PurePhaseBulkMismatchError(
                f'MAGEMin ran a different bulk than requested: SYS row '
                f'{sys_name}={got:.6f} vs requested {want:.6f} '
                f'(tol {_MAGEMIN_BULK_ECHO_TOL:g}); upstream ignores --Bulk '
                'when its first component is 0 (KLB1 fallback)'
            )


class MAGEMinBackend(MeltBackend, RealBackendAuthority):
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
                           or ``'julia'``.  Defaults to the compiled
                           ``MAGEMin`` binary over a subprocess. Optional
                           Python/Julia bridges are opt-in only.
    """

    real_backend_family = RealBackendFamily.MAGEMIN

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
        self._subprocess_pool: Optional[EngineWorkerPool] = None

    # ------------------------------------------------------------------
    # MeltBackend interface
    # ------------------------------------------------------------------

    def initialize(self, config: dict) -> bool:
        """
        Detect MAGEMin and stash configuration.

        Returns True if the compiled MAGEMin binary is present.  The
        binary is always usable through the ``subprocess`` bridge.  The
        ``pymagemin`` / ``ctypes`` / ``julia`` bridges are opt-in via
        ``python_bridge``.  A missing binary leaves ``is_available()``
        False so the simulator can route around it.
        """
        self.close()
        self._available = False
        self._warnings = []
        self._last_error = None
        self._config = dict(config or {})

        self._database = str(self._config.get('database') or 'ig')

        explicit_path = self._config.get('binary_path')
        if explicit_path:
            binary_path = self._locate_binary(explicit_path)
        else:
            from simulator.engine_local_config import configured_magemin_binary_path

            binary_path = configured_magemin_binary_path()
            if binary_path is None:
                binary_path = self._locate_binary(None)
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

        if bridge == 'subprocess' and self._config.get('warm_worker', True):
            diagnostic_path = Path(
                tempfile.gettempdir(),
                'regolith-pyrolysis-simulator',
                'magemin-diagnostics.log',
            )
            raw_warm_timeout_s = self._config.get(
                'warm_call_timeout_s', MAGEMIN_WARM_CALL_TIMEOUT_S
            )
            raw_pool_size = self._config.get('warm_pool_size', 1)
            raw_startup_timeout_s = self._config.get(
                'worker_startup_timeout_s', 30.0
            )
            for field_name, value in (
                ('warm_call_timeout_s', raw_warm_timeout_s),
                ('warm_pool_size', raw_pool_size),
                ('worker_startup_timeout_s', raw_startup_timeout_s),
            ):
                if not is_declared_real_scalar(value, allow_numeric_str=True):
                    raise ValueError(f'MAGEMin {field_name} must be numeric')
            warm_timeout_s = float(raw_warm_timeout_s)
            pool_size = int(raw_pool_size)
            if not math.isfinite(warm_timeout_s) or warm_timeout_s <= 0.0:
                raise ValueError(
                    'MAGEMin warm_call_timeout_s must be finite and positive'
                )
            if pool_size <= 0:
                raise ValueError('MAGEMin warm_pool_size must be positive')

            def worker_factory(index: int) -> WarmEngineWorker:
                return WarmEngineWorker(
                    name=f'MAGEMin subprocess pool slot {index}',
                    bootstrap=_bootstrap_magemin_subprocess_worker,
                    handler=_handle_magemin_subprocess_request,
                    bootstrap_args=(
                        str(binary_path.resolve()),
                        self._database,
                        self._config,
                    ),
                    startup_timeout_s=float(raw_startup_timeout_s),
                    call_timeout_s=warm_timeout_s,
                    diagnostic_log_path=diagnostic_path.with_name(
                        f'{diagnostic_path.stem}-{index}{diagnostic_path.suffix}'
                    ),
                )
            try:
                self._subprocess_pool = EngineWorkerPool(
                    worker_factory,
                    size=pool_size,
                )
            except EngineWorkerRemoteError as exc:
                self._warn(
                    'MAGEMin warm pool failed to initialize: '
                    f'{exc.detail}'
                )
                self._subprocess_pool = None
                return False

        self._available = True
        return True

    def close(self) -> None:
        if self._subprocess_pool is not None:
            self._subprocess_pool.close(cancel_pending=True)
            self._subprocess_pool = None

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
        call_timeout_s: Optional[float] = None,
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
        dropped_accounts: List[str] = []
        dropped_account_species: Dict[str, Tuple[str, ...]] = {}
        if composition_mol_by_account is not None:
            dropped_account_species = _dropped_account_species(
                composition_mol_by_account
            )
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

        projection = project_melt_to_oxide_projection(
            composition_kg=composition_kg,
            composition_mol=composition_mol,
            oxide_basis=self._MAGEMIN_INPUT_BASIS,
            species_formula_registry=species_formula_registry,
        )
        comp_wt = projection.oxide_wt_pct
        prior_warnings.extend(projection.warnings)
        projection_diagnostics = projection_diagnostics_for_melt_input(
            backend='MAGEMin',
            projection=projection,
            composition_kg=composition_kg,
            composition_mol=composition_mol,
            oxide_basis=self._MAGEMIN_INPUT_BASIS,
            species_formula_registry=species_formula_registry,
            dropped_accounts=dropped_accounts,
            dropped_account_species=dropped_account_species,
        )
        if (
            projection.dropped_mass_kg_by_species
            or dropped_accounts
            or dropped_account_species
        ):
            diagnostics = dict(projection_diagnostics)
            diagnostics['backend_status_reason'] = (
                OutOfDomainReason.FORBIDDEN_SPECIES.value
            )
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status='out_of_domain',
                warnings=[
                    *prior_warnings,
                    'MAGEMin refused projected/dropped non-basis melt input',
                ],
                diagnostics=diagnostics,
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
                diagnostics=projection_diagnostics,
            )

        try:
            bulk_projection = self._build_db_bulk_projection(comp_wt)
        except MeltCompositionError as exc:
            message = f'MAGEMin bulk projection failed: {exc}'
            self._last_error = message
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status='out_of_domain',
                warnings=[*prior_warnings, message],
                diagnostics=projection_diagnostics,
            )

        result_diagnostics = projection_diagnostics_for_melt_input(
            backend='MAGEMin',
            projection=projection,
            composition_kg=composition_kg,
            composition_mol=composition_mol,
            oxide_basis=self._MAGEMIN_INPUT_BASIS,
            species_formula_registry=species_formula_registry,
            dropped_accounts=dropped_accounts,
            dropped_account_species=dropped_account_species,
            extra_projection_details=_magemin_bulk_projection_details(
                bulk_projection
            ),
        )
        if bulk_projection.dropped_components:
            # A result for a different (projected) bulk is not a result for
            # this one. Do not call MAGEMin on the truncated vector.
            diagnostics = dict(result_diagnostics)
            diagnostics['backend_status'] = 'out_of_domain'
            diagnostics['backend_status_reason'] = COMPOSITION_PROJECTED
            dropped = ', '.join(bulk_projection.dropped_components)
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status='out_of_domain',
                warnings=[
                    *prior_warnings,
                    *bulk_projection.warnings,
                    'MAGEMin refused projected composition; dropped '
                    f'components have no documented ig endmember: {dropped}',
                ],
                diagnostics=diagnostics,
            )

        try:
            raw = self._call_magemin(
                bulk_projection=bulk_projection,
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                call_timeout_s=call_timeout_s,
            )
        except EngineWorkerTimeout:
            # A hang is a control-plane event, not an equilibrium refusal.
            # The pool has already killed the slot; preserve the typed signal
            # so the caller can retry while the next call respawns it.
            raise
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
                warnings=[*prior_warnings, *bulk_projection.warnings, message],
                diagnostics=result_diagnostics,
            )

        # ledger_transition is left None: MAGEMin holds no AtomLedger
        # authority.  _populate_result fills phase_masses_kg, so a result
        # from this adapter fails closed if routed through
        # core.py::_get_equilibrium as the active backend (see the module
        # "Authority posture" note) — it is only valid for a shadow
        # comparator.
        all_warnings = [*prior_warnings, *bulk_projection.warnings]
        result_status = 'ok'
        result_fO2_log = fO2_log
        result_diagnostics = dict(result_diagnostics)
        converged = (
            raw.get('converged')
            if isinstance(raw, Mapping)
            else getattr(raw, 'converged', None)
        )
        if converged is not None and not bool(converged):
            message = (
                'MAGEMin bridge reported converged=False; refusing '
                'provisional phase rows'
            )
            result_diagnostics.update({
                'backend_status': 'not_converged',
                'backend_status_reason': 'negative_convergence_evidence',
            })
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status='not_converged',
                warnings=[*all_warnings, message],
                diagnostics=result_diagnostics,
            )
        if isinstance(raw, dict):
            buffer_warnings = raw.get('buffer_warnings') or []
            for line in buffer_warnings:
                if line not in all_warnings:
                    all_warnings.append(line)
            operating_point = raw.get('operating_point_diagnostics') or {}
            if isinstance(operating_point, Mapping):
                result_diagnostics.update(dict(operating_point))
                solved_fO2_log = operating_point.get('solved_fO2_log')
                if solved_fO2_log is not None:
                    result_fO2_log = float(solved_fO2_log)
                requested_point_non_authoritative = (
                    bool(operating_point.get('operating_point_clamped'))
                    or operating_point.get(
                        'authoritative_for_requested_conditions'
                    ) is False
                )
                if requested_point_non_authoritative:
                    result_status = 'out_of_domain'
                    result_diagnostics['backend_status'] = 'out_of_domain'
                    result_diagnostics.setdefault(
                        'backend_status_reason',
                        'clamped_operating_point',
                    )
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
            fO2_log=result_fO2_log,
            phases_present=phases_present,
            phase_masses_kg=phase_masses_kg,
            phase_compositions=phase_compositions,
            liquid_fraction=liquid_fraction,
            liquid_composition_wt_pct=liquid_composition_wt_pct,
            status=result_status,
            warnings=all_warnings,
            diagnostics=result_diagnostics,
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

        # Mandatory aggregate budget: generic finder default is unbounded so
        # AlphaMELTS is not silently capped; MAGEMin always applies a finite
        # wall and rejects an explicit None config (boundedness is invariant).
        if 'liquidus_finder_budget_s' in self._config:
            raw_budget = self._config.get('liquidus_finder_budget_s')
        else:
            raw_budget = (
                MAGEMIN_WARM_LIQUIDUS_BUDGET_S
                if self._subprocess_pool is not None
                else DEFAULT_LIQUIDUS_FINDER_BUDGET_S
            )
        if raw_budget is None:
            return LiquidusSolidusResult(
                status='not_converged',
                warnings=(
                    'liquidus_finder_budget_s=None is rejected; '
                    'MAGEMin requires a finite aggregate liquidus budget',
                ),
                diagnostics={
                    'reason': 'invalid_liquidus_finder_budget',
                    'liquidus_finder_budget_s': None,
                },
            )
        try:
            budget_s = float(raw_budget)
        except (TypeError, ValueError) as exc:
            return LiquidusSolidusResult(
                status='not_converged',
                warnings=(f'invalid liquidus_finder_budget_s: {exc}',),
            )
        if not math.isfinite(budget_s) or budget_s <= 0.0:
            return LiquidusSolidusResult(
                status='not_converged',
                warnings=(
                    'invalid liquidus_finder_budget_s: must be finite and positive',
                ),
            )

        def sample_fraction(
            temperature_C: float,
            remaining_budget_s: Optional[float] = None,
        ) -> float:
            result = self.equilibrate(
                float(temperature_C),
                composition_kg=composition_kg,
                fO2_log=fO2_log,
                pressure_bar=pressure_bar,
                composition_mol=composition_mol,
                composition_mol_by_account=composition_mol_by_account,
                species_formula_registry=species_formula_registry,
                call_timeout_s=remaining_budget_s,
            )
            if result.status != 'ok':
                warning = '; '.join(result.warnings) or result.status
                # Raise the TYPED sample error so the finder preserves which
                # kind of refusal this was.  A bare RuntimeError falls through
                # to the finder's generic library-boundary guard, which mints
                # 'not_converged' -- turning 'the engine was absent'
                # (unavailable) or 'the physics is outside the model'
                # (out_of_domain) into 'the solver failed to converge', an
                # affirmative claim about a solve that never happened.
                #
                # The token fit is exact and checked, not assumed: this
                # backend's equilibrate() can only return
                # {ok, out_of_domain, not_converged, unavailable}, so the
                # non-ok branch yields exactly LIQUIDUS_REFUSAL_STATUSES
                # {not_converged, out_of_domain, unavailable} and the typed
                # error's own status validation cannot reject it.  The
                # AlphaMELTS sampler already raises this way; this site was
                # the outlier (b-299).
                raise LiquidusSampleError(
                    result.status,
                    tuple(result.warnings),
                    dict(result.diagnostics or {}),
                )
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
            budget_s=budget_s,
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
            diagnostics=dict(result.diagnostics or {}),
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

        ``initialize()`` probes ``engines.local.toml`` before this helper when
        no explicit ``binary_path`` is supplied.
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
            project_root.parent / 'MAGEMin' / 'MAGEMin',
        ]
        for candidate in candidates:
            if candidate.exists() and os.access(candidate, os.X_OK):
                return candidate

        which = shutil.which('MAGEMin')
        if which:
            return Path(which)

        return None

    def get_engine_version(self) -> str:
        from simulator.engine_local_config import (
            cache_version_for,
            warn_legacy_once,
        )

        config_version = cache_version_for('magemin')
        if config_version is not None:
            return config_version

        if self._binary_path is None:
            return 'unavailable'
        binary_path = self._binary_path.resolve()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                result = subprocess.run(
                    [str(binary_path), '--version', '--db', self._database],
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            lines = (result.stdout or result.stderr).strip().splitlines()
            if result.returncode == 0 and lines:
                legacy = lines[0].strip()
            else:
                legacy = f'MAGEMin subprocess ({self._binary_path})'
        except (OSError, subprocess.TimeoutExpired):
            legacy = f'MAGEMin subprocess ({self._binary_path})'
        warn_legacy_once(
            'magemin',
            'engines.local.toml absent; using legacy MAGEMin '
            'path-based identity for cache comparison',
        )
        return legacy

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

        Autodetect order is ``subprocess``. Optional Python/Julia
        bridges are selected only when explicitly requested. ``ctypes``
        remains explicit-only: its struct marshaling is unimplemented
        (``_call_magemin`` raises for it).
        """
        normalised = (str(requested).lower().strip()
                      if requested is not None else None)

        if normalised is None and self._binary_path is not None:
            return 'subprocess', None

        if normalised == 'pymagemin':
            try:
                import pymagemin  # type: ignore[import-not-found]
                return 'pymagemin', pymagemin
            except Exception as exc:  # noqa: BLE001
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

        if normalised == 'julia':
            try:
                import julia  # type: ignore[import-not-found]
                # PyJulia is heavy — only flag as available if the
                # MAGEMin_C.jl package import succeeds.
                from julia import Main as JuliaMain  # noqa: F401
                JuliaMain.eval('import MAGEMin_C')  # may raise
                return 'julia', julia
            except Exception as exc:  # noqa: BLE001
                self._last_error = f'julia bridge import failed: {exc}'

        # Subprocess fallback: the binary located in initialize() is
        # itself the bridge.  This is the supported default — MAGEMin
        # ships no PyPI package, so a built binary must always be usable.
        if normalised == 'subprocess' and self._binary_path is not None:
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

    # Local MAGEMin 1.9.6 ``--help`` declares a DB-specific ``--Bulk`` order.
    # Keep this table exact: the CLI is positional and silently accepts
    # wrongly shaped vectors.
    _DB_BULK_ORDERS: Dict[str, Tuple[str, ...]] = {
        'ig': (
            'SiO2', 'Al2O3', 'CaO', 'MgO', 'FeOt', 'K2O', 'Na2O',
            'TiO2', 'O', 'Cr2O3', 'H2O',
        ),
        'igad': (
            'SiO2', 'Al2O3', 'CaO', 'MgO', 'FeOt', 'K2O', 'Na2O',
            'TiO2', 'O', 'Cr2O3',
        ),
        'mp': (
            'SiO2', 'Al2O3', 'CaO', 'MgO', 'FeOt', 'K2O', 'Na2O',
            'TiO2', 'O', 'MnO', 'H2O',
        ),
        'mb': (
            'SiO2', 'Al2O3', 'CaO', 'MgO', 'FeOt', 'K2O', 'Na2O',
            'TiO2', 'O', 'H2O',
        ),
        'um': ('SiO2', 'Al2O3', 'MgO', 'FeOt', 'O', 'H2O', 'S'),
        'ume': (
            'SiO2', 'Al2O3', 'MgO', 'FeOt', 'O', 'H2O', 'S', 'CaO',
            'Na2O',
        ),
        'mtl': ('SiO2', 'Al2O3', 'CaO', 'MgO', 'FeOt', 'Na2O'),
    }
    _IG_BULK_ORDER: Tuple[str, ...] = _DB_BULK_ORDERS['ig']
    _MAGEMIN_INPUT_BASIS: Tuple[str, ...] = tuple(dict.fromkeys((
        *OXIDE_SPECIES,
        'FeOt',
        'O',
        'H2O',
        'S',
    )))
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
    _EXCESS_O_FROM_FE2O3_FACTOR = (
        _O_MOLAR_MASS_G_PER_MOL / _FE2O3_MOLAR_MASS_G_PER_MOL
    )
    # Lunar/Apollo bulk chemistry reports total iron as ``FeO`` / ``FeO_T`` —
    # a spectroscopy bookkeeping convention, not literal stoichiometric FeO.
    # MAGEMin still needs a nonzero ``O`` bulk component for the qfm buffer.
    # When no explicit ``Fe2O3`` is present, provision redox O from the total
    # iron inventory using the same half-O-per-Fe stoichiometry as explicit
    # Fe2O3 excess oxygen (mass-conserving with the Fe2O3 path above).
    _EXCESS_O_FROM_FEO_TOTAL_IRON_FACTOR = (
        _O_MOLAR_MASS_G_PER_MOL / (2.0 * _FEO_MOLAR_MASS_G_PER_MOL)
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
        bulk_projection: _MAGEMinBulkProjection,
        temperature_C: float,
        pressure_bar: float,
        fO2_log: float,
        call_timeout_s: Optional[float] = None,
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
        call_started = time.monotonic()
        temperature_K = temperature_C + 273.15
        pressure_GPa = self._pressure_bar_to_GPa(pressure_bar)
        pressure_kbar = self._GPa_to_kbar(pressure_GPa)

        # Even in-process bridges cannot be cancelled mid-call, but refuse to
        # start a new evaluation once the residual aggregate budget is gone.
        if call_timeout_s is not None:
            remaining = float(call_timeout_s)
            if not math.isfinite(remaining) or remaining <= 0.0:
                raise RuntimeError(
                    'MAGEMin call cancelled: aggregate liquidus budget exhausted'
                )

        if self._bridge == 'pymagemin':
            for name in ('minimize', 'run', 'equilibrium'):
                fn = getattr(module, name, None)
                if not callable(fn):
                    continue
                try:
                    return fn(
                        composition=bulk_projection.composition_wt_pct,
                        T_C=temperature_C,
                        T_K=temperature_K,
                        P_GPa=pressure_GPa,
                        P_kbar=pressure_kbar,
                        log_fO2=fO2_log,
                        database=self._database,
                    )
                except Exception as exc:  # noqa: BLE001 - optional bridge boundary
                    if self._binary_path is not None:
                        return self._call_magemin_subprocess_after_bridge_failure(
                            bulk_projection=bulk_projection,
                            temperature_C=temperature_C,
                            pressure_kbar=pressure_kbar,
                            fO2_log=fO2_log,
                            bridge='pymagemin',
                            exc=exc,
                            call_timeout_s=self._residual_call_timeout_s(
                                call_timeout_s,
                                call_started,
                            ),
                        )
                    raise
            if self._binary_path is not None:
                return self._call_magemin_subprocess_after_bridge_failure(
                    bulk_projection=bulk_projection,
                    temperature_C=temperature_C,
                    pressure_kbar=pressure_kbar,
                    fO2_log=fO2_log,
                    bridge='pymagemin',
                    exc=RuntimeError('pymagemin exposes no minimize/run/equilibrium entry point'),
                    call_timeout_s=self._residual_call_timeout_s(
                        call_timeout_s,
                        call_started,
                    ),
                )

        if self._bridge == 'julia':
            JuliaMain = module.Main  # type: ignore[attr-defined]
            # The Julia bridge expects a dict of oxide wt% and returns
            # a struct.  This is a thin wrapper — full marshaling is
            # the responsibility of MAGEMin_C.jl.
            try:
                return JuliaMain.MAGEMin.single_point_minimization(
                    bulk_projection.composition_wt_pct,
                    temperature_K,
                    pressure_kbar,
                    self._database,
                    fO2_log,
                )
            except Exception as exc:  # noqa: BLE001 - optional bridge boundary
                if self._binary_path is not None:
                    return self._call_magemin_subprocess_after_bridge_failure(
                        bulk_projection=bulk_projection,
                        temperature_C=temperature_C,
                        pressure_kbar=pressure_kbar,
                        fO2_log=fO2_log,
                        bridge='julia',
                        exc=exc,
                        call_timeout_s=self._residual_call_timeout_s(
                            call_timeout_s,
                            call_started,
                        ),
                    )
                raise

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
            if self._subprocess_pool is not None:
                timeout_s = (
                    float(self._config.get(
                        'warm_call_timeout_s',
                        MAGEMIN_WARM_CALL_TIMEOUT_S,
                    ))
                    if call_timeout_s is None
                    else min(
                        float(self._config.get(
                            'warm_call_timeout_s',
                            MAGEMIN_WARM_CALL_TIMEOUT_S,
                        )),
                        float(call_timeout_s),
                    )
                )
                try:
                    future = self._subprocess_pool.submit({
                        'bulk_projection': bulk_projection,
                        'temperature_C': temperature_C,
                        'pressure_kbar': pressure_kbar,
                        'fO2_log': fO2_log,
                        # Let the pool's typed hard wall fire first. The inner
                        # subprocess timeout is only a secondary containment
                        # wall for platforms without process-group cleanup.
                        'call_timeout_s': timeout_s + 0.5,
                    }, timeout_s=timeout_s)
                    return future.result()
                except EngineWorkerTimeout:
                    raise
                except EngineWorkerRemoteError as exc:
                    raise RuntimeError(
                        f'MAGEMin subprocess worker failed: {exc.detail}\n'
                        f'{exc.remote_traceback}'
                    ) from exc
            return self._call_magemin_subprocess(
                bulk_projection=bulk_projection,
                temperature_C=temperature_C,
                pressure_kbar=pressure_kbar,
                fO2_log=fO2_log,
                call_timeout_s=call_timeout_s,
            )

        raise RuntimeError(
            f'MAGEMin bridge {self._bridge!r} has no recognised entry point')

    @staticmethod
    def _residual_call_timeout_s(
        call_timeout_s: Optional[float],
        started_at: float,
    ) -> Optional[float]:
        if call_timeout_s is None:
            return None
        residual = float(call_timeout_s) - max(
            0.0,
            time.monotonic() - float(started_at),
        )
        if not math.isfinite(residual) or residual <= 0.0:
            raise RuntimeError(
                'MAGEMin bridge consumed aggregate liquidus budget; '
                'subprocess retry cancelled'
            )
        return residual

    def _call_magemin_subprocess_after_bridge_failure(
        self,
        *,
        bulk_projection: _MAGEMinBulkProjection,
        temperature_C: float,
        pressure_kbar: float,
        fO2_log: float,
        bridge: str,
        exc: Exception,
        call_timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        message = f'MAGEMin {bridge} bridge failed; retried subprocess: {exc}'
        self._last_error = message
        try:
            raw = self._call_magemin_subprocess(
                bulk_projection=bulk_projection,
                temperature_C=temperature_C,
                pressure_kbar=pressure_kbar,
                fO2_log=fO2_log,
                call_timeout_s=call_timeout_s,
            )
        except Exception as subprocess_exc:
            raise RuntimeError(
                f'{message}; subprocess retry failed: {subprocess_exc}'
            ) from subprocess_exc
        warnings_out = tuple(raw.get('buffer_warnings') or ())
        return {**raw, 'buffer_warnings': (message, *warnings_out)}

    def _call_magemin_subprocess(
        self,
        *,
        bulk_projection: _MAGEMinBulkProjection,
        temperature_C: float,
        pressure_kbar: float,
        fO2_log: float,
        call_timeout_s: Optional[float] = None,
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

        binary_path = self._binary_path.resolve()

        bulk = bulk_projection.vector
        buffer_name, buffer_n, buffer_warnings = self._resolve_buffer(
            temperature_C=temperature_C, fO2_log=fO2_log,
        )
        solved_fO2_log: Optional[float]
        if buffer_name == 'qfm':
            solved_fO2_log = self._qfm_logfo2_oneill(temperature_C) + buffer_n
        else:
            solved_fO2_log = None
        if solved_fO2_log is None:
            fO2_clamped = True
        else:
            fO2_clamped = not math.isclose(
                float(solved_fO2_log),
                float(fO2_log),
                rel_tol=0.0,
                abs_tol=1.0e-9,
            )
        operating_point_diagnostics: Dict[str, Any] = {
            'requested_fO2_log': float(fO2_log),
            'applied_fO2_buffer': buffer_name,
            'applied_fO2_buffer_n': float(buffer_n),
            'solved_fO2_log': solved_fO2_log,
            'fO2_clamped': fO2_clamped,
            'authoritative_for_requested_conditions': not fO2_clamped,
            'authoritative_for_solved_conditions': solved_fO2_log is not None,
        }
        if fO2_clamped:
            operating_point_diagnostics.update({
                'operating_point_clamped': True,
                'backend_status': 'out_of_domain',
                'backend_status_reason': 'clamped_operating_point',
            })

        args = [
            str(binary_path),
            '--Verb=0',
            f'--db={self._database}',
            f'--Temp={temperature_C:.6f}',
            f'--Pres={pressure_kbar:.6f}',
            '--sys_in=wt',
            '--Bulk=' + ','.join(f'{value:.6f}' for value in bulk),
            f'--buffer={buffer_name}',
            f'--buffer_n={buffer_n:.6f}',
        ]

        configured_timeout_s = float(self._config.get('timeout_s', 60.0))
        if call_timeout_s is None:
            timeout_s = configured_timeout_s
        else:
            # Clamp per-call wall to residual aggregate budget so a call
            # started near the deadline cannot overrun by a full timeout_s.
            remaining = float(call_timeout_s)
            if not math.isfinite(remaining) or remaining <= 0.0:
                raise RuntimeError(
                    'MAGEMin call cancelled: aggregate liquidus budget exhausted'
                )
            timeout_s = min(configured_timeout_s, remaining)
        call_started = time.monotonic()
        try:
            with _magemin_subprocess_slot(timeout_s):
                remaining_timeout_s = timeout_s - max(
                    0.0, time.monotonic() - call_started
                )
                if remaining_timeout_s <= 0.0:
                    raise RuntimeError(
                        'MAGEMin call cancelled while waiting for subprocess slot'
                    )
                with tempfile.TemporaryDirectory() as tmpdir:
                    completed = subprocess.run(  # noqa: S603 - adapter-built
                        args,
                        cwd=tmpdir,
                        capture_output=True,
                        text=True,
                        timeout=remaining_timeout_s,
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
        return {
            'phases': phases,
            'buffer_warnings': buffer_warnings,
            'operating_point_diagnostics': operating_point_diagnostics,
        }

    def _build_ig_bulk_vector(
        self, composition_wt_pct: Mapping[str, float]
    ) -> List[float]:
        return list(
            self._build_db_bulk_projection(
                composition_wt_pct, database='ig',
            ).vector
        )

    def _build_db_bulk_projection(
        self,
        composition_wt_pct: Mapping[str, float],
        *,
        database: Optional[str] = None,
    ) -> _MAGEMinBulkProjection:
        """
        Project simulator oxide wt% into the installed MAGEMin DB bulk order.

        FeO/Fe2O3 are MAGEMin's documented total-iron convention:
        ``FeOt`` plus free ``O`` where the DB exposes an O component. Other
        positive components absent from the selected DB order are not hidden:
        they are listed in warnings as documented drops. Unknown component
        names fail before the binary sees a positional vector.
        """
        db = str(database or self._database).lower().strip()
        if db not in self._DB_BULK_ORDERS:
            raise MeltCompositionError(
                f'unknown MAGEMin database {database or self._database!r}; '
                f'known databases: {", ".join(sorted(self._DB_BULK_ORDERS))}'
            )
        order = self._DB_BULK_ORDERS[db]
        allowed = set(self._MAGEMIN_INPUT_BASIS)
        source: Dict[str, float] = {}
        unknown: List[str] = []
        for component, raw_value in composition_wt_pct.items():
            value = float(raw_value or 0.0)
            if value <= 0.0:
                continue
            name = str(component)
            if name not in allowed:
                unknown.append(name)
                continue
            source[name] = source.get(name, 0.0) + value
        if unknown:
            raise MeltCompositionError(
                'unprojectable MAGEMin bulk component(s): '
                + ', '.join(sorted(unknown))
            )

        feo = source.pop('FeO', 0.0)
        fe2o3 = source.pop('Fe2O3', 0.0)
        feot = source.pop('FeOt', 0.0)
        excess_o = source.pop('O', 0.0)
        merged: List[str] = []
        if feo > 0.0:
            merged.append('FeO->FeOt+O')
        if fe2o3 > 0.0:
            merged.append('Fe2O3->FeOt+O')

        # Fe2O3 -> FeO-equivalent mass: each Fe2O3 carries 2 Fe;
        # total-iron-as-FeOt reports that iron as 2 FeO formula masses.
        feot += feo + fe2o3 * self._FEOT_FROM_FE2O3_FACTOR
        excess_o += fe2o3 * self._EXCESS_O_FROM_FE2O3_FACTOR
        if fe2o3 <= 0.0 and feo > 0.0:
            # FeO key is total-iron inventory (FeO_T), not literal FeO only.
            # Peel buffer O from the FeOt slot so FeOt + O == feo (same
            # mass bookkeeping as the explicit Fe2O3 fold above).
            feo_excess_o = feo * self._EXCESS_O_FROM_FEO_TOTAL_IRON_FACTOR
            excess_o += feo_excess_o
            feot -= feo_excess_o

        if feot > 0.0:
            source['FeOt'] = source.get('FeOt', 0.0) + feot
        if excess_o > 0.0:
            source['O'] = source.get('O', 0.0) + excess_o

        order_set = set(order)
        projected = {
            component: value
            for component, value in source.items()
            if component in order_set and value > 0.0
        }
        dropped = sorted(
            component for component, value in source.items()
            if component not in order_set and value > 0.0
        )
        if not projected:
            detail = f'; dropped: {", ".join(dropped)}' if dropped else ''
            raise MeltCompositionError(
                f'no positive components remain in MAGEMin {db!r} bulk order'
                + detail
            )

        diagnostics: List[str] = []
        if merged:
            diagnostics.append(
                f'MAGEMin: database {db!r} merged '
                + ', '.join(merged)
            )
        if dropped:
            diagnostics.append(
                f'MAGEMin: database {db!r} dropped components outside '
                f'documented bulk order: {", ".join(dropped)}'
            )

        return _MAGEMinBulkProjection(
            database=db,
            order=order,
            vector=tuple(float(projected.get(component, 0.0)) for component in order),
            composition_wt_pct={
                component: float(projected[component])
                for component in order
                if component in projected
            },
            warnings=tuple(diagnostics),
            dropped_components=tuple(dropped),
            merged_components=tuple(merged),
            source_sum_wt_pct=sum(float(value) for value in source.values()),
            projected_sum_wt_pct=sum(float(value) for value in projected.values()),
        )

    @staticmethod
    def _qfm_logfo2_oneill(temperature_C: float) -> float:
        """
        Absolute log10(fO2) of the QFM buffer at ``temperature_C``.

        O'Neill (1987) formulation: ``logfO2_QFM = 8.58 - 25050 / T_K``.
        Kept in sync with ``sulfsat.py::_qfm_logfo2_oneill`` for engine-aligned
        buffer reporting only. NOTE (b-094): the Jugo-2010 S6+/ST path no
        longer uses this fit — Jugo deltaQFM is referenced to Frost (1991)
        per PySulfSat's own requirement.
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

        parsed_modes: list[tuple[str, float]] = []
        for name, raw in zip(names, values):
            try:
                fraction = float(raw)
            except ValueError as exc:
                raise RuntimeError(
                    f'MAGEMin Mode token for {name!r} is not numeric: {raw!r}'
                ) from exc
            if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
                raise RuntimeError(
                    f'MAGEMin Mode token for {name!r} is invalid: {raw!r}'
                )
            parsed_modes.append((name, fraction))
        mode_total = sum(fraction for _, fraction in parsed_modes)
        if not math.isclose(mode_total, 1.0, rel_tol=0.0, abs_tol=1.0e-4):
            raise RuntimeError(
                'MAGEMin Mode vector must sum to 1.0; '
                f'got {mode_total:.9g}'
            )

        phases: Dict[str, Dict[str, float]] = {}
        for name, fraction in parsed_modes:
            if name.lower() in MAGEMinBackend._BUFFER_CHOICES:
                continue  # buffer control row, not a phase
            if fraction <= 0.0:
                continue
            phases[name] = {'mass_kg': fraction}
        return phases

    # ------------------------------------------------------------------
    # Pure-phase standard-state properties (diagnostic accessor)
    # ------------------------------------------------------------------
    #
    # This path never touches equilibrate(): it runs the binary with
    # --Verb=1 --out_matlab=1 and parses two additional stdout/file blocks.
    #
    # Units and bases (derivations verified against upstream source and
    # NIST-JANAF, see docs-private/research/2026-09-22-janaf-p4a-scout/):
    #
    #   * The Verb=1 pre-minimisation table prints, per pure phase and per
    #     solution endmember, ``gbase`` in kJ per mol of the printed formula
    #     (Holland-Powell apparent Gibbs energy of formation: elements
    #     referenced at 298.15 K only).  ``G_J = gbase_kJ * 1000 / divisor``.
    #     The opx ``en`` endmember formula is Mg2Si2O6 (HP ds6), so
    #     divisor=2 puts it on the JANAF MgSiO3 basis.
    #   * The out_matlab "Stable mineral assemblage" table prints, per STABLE
    #     phase: G [kJ/mol formula] (the "G[J]" header is mislabelled upstream;
    #     verified: quartz at 298.15 K prints -923.048, matching apparent G in
    #     kJ), Cp [kJ/K per mol formula, unscaled], and Entropy/Enthalpy
    #     [kJ/K resp. kJ] scaled by the phase's ``factor``
    #     (src/toolkit.c:1525,1555,1648,1651: phase_entropy =
    #     -dGdT*factor; phase_enthalpy = phase_entropy*T + G*factor).
    #     factor = fbc/ape (system bulk atoms-per-oxide over phase
    #     atoms-per-formula, src/pp_min_function.c:117) is printed per phase:
    #     for pure phases as the second column of the Verb=1 pure-phase table
    #     line, for solution phases in the final "PHASE ASSEMBLAGE" table.
    #     Molar S = printed/factor; sanity anchor: forsterite-bulk ol at
    #     298.15 K prints S=31.708 J/K with factor 1/3 -> 95.13 J/K vs JANAF
    #     Mg2SiO4 S(298.15) = 95.14 J/K/mol.
    #   * S/Cp/H exist ONLY when the phase is stable for the stoichiometric
    #     bulk at the requested T,P (the table lists stable phases only).
    #     A solution phase must additionally sit at its endmember (purity
    #     gate), else its S/Cp/H describe a mixture, not the pure phase.
    #     Enstatite is never stable for an MgSiO3 bulk in ig at 298-1500 K
    #     (ol + silica instead) -> typed absence, not a number.

    def pure_phase_properties(
        self,
        phase_id: str,
        *,
        temperature_K: float,
        pressure_bar: float,
    ) -> PurePhaseProperties:
        """G/S/Cp/H of one named phase at (T, P) from the MAGEMin binary.

        ``phase_id`` is an ig-database symbol: ``fo`` (olivine endmember),
        ``per`` (ferropericlase endmember), ``en`` (orthopyroxene endmember,
        Mg2Si2O6 basis), or the pure phases ``q`` / ``crst`` / ``trd``.
        Cold subprocess: same binary and slot discipline as the production
        subprocess bridge; equilibrate() is untouched.
        """
        if not math.isfinite(temperature_K) or temperature_K <= 0.0:
            raise ValueError(
                f'pure_phase temperature_K must be finite and positive: '
                f'{temperature_K!r}'
            )
        if not math.isfinite(pressure_bar) or pressure_bar <= 0.0:
            raise ValueError(
                f'pure_phase pressure_bar must be finite and positive: '
                f'{pressure_bar!r}'
            )
        if self._database != 'ig':
            raise PurePhaseUnsupportedDatabaseError(
                f'pure-phase map verified for db=ig only; loaded {self._database!r}'
            )
        spec = _MAGEMIN_PURE_PHASES.get(str(phase_id))
        if spec is None:
            raise PurePhaseUnknownSymbolError(
                f'unknown ig pure-phase id {phase_id!r}; known: '
                f'{sorted(_MAGEMIN_PURE_PHASES)}'
            )
        if (
            not self._available
            or self._bridge != 'subprocess'
            or self._binary_path is None
        ):
            raise PurePhaseAccessError(
                'MAGEMin pure-phase query requires the subprocess bridge '
                '(initialized binary); equilibrate warm-pool requests carry '
                'only equilibrate payloads'
            )

        temperature_C = temperature_K - 273.15
        # bar -> kbar: 1 kbar = 1000 bar (CLI --Pres takes kbar).
        pressure_kbar = float(pressure_bar) * 1.0e-4
        stdout, matlab_text, warnings = self._run_pure_phase_probe(
            bulk_wt_ig=dict(spec.bulk_wt_pct),
            temperature_C=temperature_C,
            pressure_kbar=pressure_kbar,
        )
        _assert_magemin_bulk_echo(spec.bulk_wt_pct, matlab_text)

        pp_gbase, ss_endmember_gbase = _parse_magemin_gbase_tables(stdout)
        pp_factor: Optional[float] = None
        if spec.host_phase is None:
            g_entry = pp_gbase.get(spec.endmember)
            if g_entry is not None:
                g_kJ, pp_factor = g_entry
        else:
            g_kJ = ss_endmember_gbase.get(spec.host_phase, {}).get(
                spec.endmember
            )
            g_entry = g_kJ
        if g_entry is None:
            raise PurePhaseAccessError(
                f'MAGEMin ig endmember table lacks {spec.endmember!r} '
                f'(host {spec.host_phase!r}); cannot certify phase identity'
            )
        G_J_mol = g_kJ * 1000.0 / spec.formula_divisor

        absences: List[PropertyAbsence] = []
        S_J_K_mol: Optional[float] = None
        Cp_J_K_mol: Optional[float] = None
        H_J_mol: Optional[float] = None

        assemblage = _parse_magemin_matlab_assemblage(matlab_text)
        phase_rows = assemblage.get(spec.assemblage_phase) or []
        # A compositionally split SS prints one row per instance; the
        # dominant instance (max wt fraction) is the candidate pure phase.
        phase_index: Optional[int] = None
        if phase_rows:
            phase_index = max(
                range(len(phase_rows)),
                key=lambda i: phase_rows[i]['frac_wt'],
            )
        phase_row = phase_rows[phase_index] if phase_index is not None else None
        if phase_row is None or phase_row['frac_wt'] <= 0.0:
            absences.extend(
                PropertyAbsence(prop, PHASE_NOT_STABLE_AT_TP)
                for prop in ('S', 'Cp', 'H')
            )
        else:
            if spec.host_phase is not None:
                # Instance order matches between the two matlab blocks; the
                # endmember fractions of the dominant instance decide purity.
                em_rows = _parse_magemin_endmember_fractions(
                    matlab_text
                ).get(spec.host_phase) or []
                em_row = (
                    em_rows[phase_index]
                    if phase_index is not None and phase_index < len(em_rows)
                    else None
                )
                endmember_fraction = (em_row or {}).get(spec.endmember)
                if (
                    endmember_fraction is None
                    or endmember_fraction < _MAGEMIN_PURITY_MIN
                ):
                    absences.extend(
                        PropertyAbsence(prop, PHASE_NOT_PURE_AT_EQUILIBRIUM)
                        for prop in ('S', 'Cp', 'H')
                    )
                    phase_row = None
            if phase_row is not None:
                factor = (
                    pp_factor
                    if spec.host_phase is None
                    else _parse_magemin_assemblage_factors(stdout).get(
                        spec.host_phase
                    )
                )
                if factor is None or factor <= 0.0:
                    absences.extend(
                        PropertyAbsence(prop, PHASE_FACTOR_UNAVAILABLE)
                        for prop in ('S', 'Cp', 'H')
                    )
                else:
                    Cp_J_K_mol = (
                        phase_row['Cp_kJ_K'] * 1000.0 / spec.formula_divisor
                    )
                    S_J_K_mol = (
                        phase_row['S_kJ_K'] / factor * 1000.0
                        / spec.formula_divisor
                    )
                    H_J_mol = (
                        phase_row['H_kJ'] / factor * 1000.0
                        / spec.formula_divisor
                    )

        return PurePhaseProperties(
            engine='magemin',
            phase_id=str(phase_id),
            host_phase=spec.host_phase,
            polymorph=spec.polymorph,
            formula=spec.formula,
            formula_basis=spec.formula_basis,
            database='ig (Holland et al. 2018 -> Green et al. 2024; THERMOCALC ds6 family)',
            gibbs_convention=GIBBS_CONVENTION_APPARENT_298,
            temperature_K=float(temperature_K),
            pressure_bar=float(pressure_bar),
            G_J_mol=G_J_mol,
            S_J_K_mol=S_J_K_mol,
            Cp_J_K_mol=Cp_J_K_mol,
            H_J_mol=H_J_mol,
            absences=tuple(absences),
            warnings=tuple(warnings),
        )

    def _run_pure_phase_probe(
        self,
        *,
        bulk_wt_ig: Mapping[str, float],
        temperature_C: float,
        pressure_kbar: float,
    ) -> Tuple[str, str, List[str]]:
        """Run the binary at Verb=1 + out_matlab=1 for one stoich bulk.

        Returns (stdout, matlab_output_text, buffer_warnings). Same slot
        lock, buffer translation, and TemporaryDirectory discipline as
        ``_call_magemin_subprocess``; only the verbosity/output flags and the
        parsed payload differ.
        """
        if self._binary_path is None:
            raise PurePhaseAccessError('MAGEMin binary path not resolved')
        binary_path = self._binary_path.resolve()

        order = self._DB_BULK_ORDERS[self._database]
        vector = [float(bulk_wt_ig.get(name, 0.0)) for name in order]
        buffer_name, buffer_n, buffer_warnings = self._resolve_buffer(
            temperature_C=temperature_C,
            fO2_log=-9.0,
        )
        args = [
            str(binary_path),
            '--Verb=1',
            f'--db={self._database}',
            f'--Temp={temperature_C:.6f}',
            f'--Pres={pressure_kbar:.6f}',
            '--sys_in=wt',
            '--Bulk=' + ','.join(f'{value:.8g}' for value in vector),
            f'--buffer={buffer_name}',
            f'--buffer_n={buffer_n:.6f}',
            '--out_matlab=1',
        ]
        timeout_s = float(self._config.get('timeout_s', 60.0))
        try:
            with _magemin_subprocess_slot(timeout_s):
                with tempfile.TemporaryDirectory() as tmpdir:
                    completed = subprocess.run(  # noqa: S603 - adapter-built
                        args,
                        cwd=tmpdir,
                        capture_output=True,
                        text=True,
                        timeout=timeout_s,
                        check=False,
                    )
                    matlab_path = (
                        Path(tmpdir) / 'output' / '_matlab_output.txt'
                    )
                    matlab_text = (
                        matlab_path.read_text() if matlab_path.exists() else ''
                    )
        except subprocess.TimeoutExpired as exc:
            raise PurePhaseAccessError(
                f'MAGEMin binary timed out after {timeout_s:g}s'
            ) from exc
        except OSError as exc:
            raise PurePhaseAccessError(
                f'MAGEMin binary could not be executed: {exc}'
            ) from exc
        if completed.returncode != 0:
            stderr = (completed.stderr or '').strip()
            raise PurePhaseAccessError(
                f'MAGEMin binary exited {completed.returncode}: '
                f'{stderr or "no stderr"}'
            )
        if not matlab_text:
            raise PurePhaseAccessError(
                'MAGEMin produced no output/_matlab_output.txt '
                '(out_matlab=1); cannot verify the bulk echo'
            )
        return completed.stdout or '', matlab_text, buffer_warnings

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
                    value = state[key]
                    # Present key with None is the same unparseable class as a
                    # missing key — typed refuse, do not return None for a
                    # later TypeError on float(None). Non-finite numeric mass
                    # still flows to LiquidFractionInvalidError downstream.
                    if value is None:
                        raise MeltCompositionError(
                            'unparseable_phase_mass: '
                            f'{key!r} is None; refusing mass=0.0'
                        )
                    return value
            # Unrecognized mass key is not proof of zero phase mass — that
            # would freeze liquid_fraction and hold species in the melt.
            raise MeltCompositionError(
                'unparseable_phase_mass: no recognized mass key in '
                f'{sorted(state.keys())!r}; refusing mass=0.0'
            )
        for attr in ('mass_kg', 'mass', 'm', 'amount_kg'):
            value = getattr(state, attr, None)
            if value is not None:
                return value
        raise MeltCompositionError(
            f'unparseable_phase_mass: no mass attribute on {type(state).__name__}; '
            'refusing mass=0.0'
        )

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
