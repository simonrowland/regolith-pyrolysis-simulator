"""
Condensation Train Model
=========================

★ TIER 2: SCIENTIST-READABLE ★

Models vapor flow through the 8-stage metals condensation train.
Each stage operates at a fixed temperature range and preferentially
collects species whose condensation temperature falls within that range.

Train topology (metals train, active C2A onward):
    Stage 0  Hot duct (>1400°C)      — IR spectroscopy, no condensation
    Stage 1  Fe condenser (1100-1400°C) — liquid Fe drains to sump
    Stage 2  Cr oxide harvester (1100-1300°C) — Cr2O3 product cartridge
    Stage 3  SiO zone (900-1200°C)   — fused silica on removable baffles.
             SiO capture here is *operator-controlled*. Under default
             0.5.3 conditions with ``StirState(axial=6.0, radial=1.0)``
             — the axial axis drives evaporation H-K-L surface renewal
             and the radial axis drives gas-side Sherwood enhancement —
             Stage 4 alkali/Mg carryover continues to receive more SiO
             than Stage 3 in absolute terms (a routing trade-off
             documented in the 0.5.3 CHANGELOG "Known limitation"
             section; operators raise ``stir_state.radial`` above 1.0
             to amplify the gas-side cold-wall mass transport into
             Stage 3, or retune Stage 3 temperatures down to widen the
             cold-wall ΔP). The absolute total capture remains
             rate-cap-driven by ``_pressure_isolated_capture_budget_kg``.
             Sub-laminar ``stir_state.axial`` or pO₂ hold suppresses
             Stage 3 capture and passes SiO downstream (silica fume)
             or holds it in the melt.
    Stage 4  Alkali/Mg cyclone (350-700°C) — Na/K/Mg condensation
    Stage 5  Vortex dust filter (200-350°C) — entrained particle capture
    Stage 6  Turbine-compressor      — pressure regulation, pO₂ control
    Stage 7  Turbine outlet monitor — terminal ledger owns O2 storage

A separate volatiles train handles C0/C0b products and is sealed
by a gate valve after devolatilisation.

Key physics:
    Condensation efficiency per species per stage:             [COND-1]
        η = 1 - exp(-t_res / τ_cond)
    where:
        t_res   = residence time in the stage (s)
        τ_cond  = characteristic condensation time (s)
              = f(T_stage, T_cond_species, surface_area, α_stick)

    If T_stage << T_condense → τ_cond is very small → η → 1
    If T_stage ≈ T_condense → τ_cond is large → η → 0

The Fe → SiO separation (Stage 1 → Stage 2):
    Stage 1 at 1200-1400°C: Fe condenses as liquid, SiO passes through
    (SiO condensation T is 900-1200°C, below Stage 1 operating T).
    Chevron separator at Stage 1 exit catches entrained Fe droplets.
    Sharp T boundary (radiation gap) prevents early SiO condensation.
    Impurity: ~0.1-1% Fe passes to Stage 2; ~0.5-2% SiO condenses in Stage 1.
"""

from __future__ import annotations

import copy
import math
import warnings
from collections.abc import Mapping, MutableMapping
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Dict

import yaml

from simulator.backend_names import (
    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    canonical_backend_name,
)
from simulator.transport_constants import (
    COLLISION_DIAMETERS_M,
    FREE_MOLECULAR_KNUDSEN_MIN,
    N2_COLLISION_DIAMETER_M,
    VISCOUS_KNUDSEN_MAX,
)
from simulator.physical_constants import (
    AVOGADRO,
    BOLTZMANN,
    CELSIUS_TO_KELVIN_OFFSET,
)
from simulator.accounting.queries import (
    wall_deposit_candidate_for_surface_kg as query_wall_deposit_candidate_for_surface_kg,
    wall_deposit_candidate_kg as query_wall_deposit_candidate_kg,
    wall_deposit_candidates_by_segment_kg as query_wall_deposit_candidates_by_segment_kg,
)
from simulator.config import load_config_bundle
from simulator.core import (
    CondensationTrain, CondensationStage, EvaporationFlux, MeltState,
)
from simulator.lab_geometry import (
    LabGeometry,
    LabGeometryError,
    parse_lab_geometry,
    require_lab_pipe_diameter,
)
from simulator.condensation_routing import (
    STAGE_KEY_BY_NUMBER,
    accepted_species_for_stage_number,
    coproduct_species_for_stage_number,
    designated_stage_number,
    is_designated_for_stage,
)
from simulator.state import (
    CampaignPhase,
    MAX_STIR_FACTOR,
    MOLAR_MASS,
    PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS,
    PipeSegment,
    clamp_stir_factor,
)


# Single-sourced from the physical_constants leaf (SC-CONST pass-B); byte-identical
# to the prior local literals (1.380649e-23 / 6.02214076e23).
BOLTZMANN_CONSTANT_J_K = BOLTZMANN
AVOGADRO_MOL = AVOGADRO
HKL_BAND_SAMPLES = 33
DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
WALL_DEPOSIT_ACCOUNT = 'process.wall_deposit'
WALL_DEPOSIT_SEGMENT_ACCOUNTS = PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS
DEFAULT_PIPE_TEMPERATURE_C = 1500.0
DEFAULT_PIPE_DIAMETER_M = 0.12
# N2_COLLISION_DIAMETER_M and the Knudsen flow-regime thresholds
# (VISCOUS_KNUDSEN_MAX / FREE_MOLECULAR_KNUDSEN_MIN) are single-sourced in
# simulator/transport_constants.py (shared with simulator/transport_regime.py) so
# the MFP/Knudsen path and the binary-diffusion path can never diverge
# (BUG-013 / BUG-023 / BUG-027). The Chapman-Enskog Lennard-Jones table below
# still derives its N2 sigma from N2_COLLISION_DIAMETER_M.
CONTINUUM_BUFFER_KN = VISCOUS_KNUDSEN_MAX
KNUDSEN_REFUSAL_REASON = 'knudsen_outside_viscous_flow'
KNUDSEN_TRANSITION_REASON = 'knudsen_transitional_flow'
INVALID_PIPE_DIAMETER_REASON = 'invalid_pipe_diameter'
COLD_SPOT_MARGIN_C = 25.0
DEFAULT_UPSTREAM_HOT_WALL_MIN_C = 1400.0
LAB_EXPOSED_MELT_AREA_BASIS = 'gram_lab_exposed_melt'

# Viscous-regime wall mass-transfer model (post-F3 follow-on, 2026-05-27).
# F3 added wall-side regime_factor = Kn/(Kn + 0.01): f -> 0 in viscous flow
# and f -> 1 in free-molecular flow. This is the cold-wall deposition gate;
# do not conflate it with the source-side Fuchs-Sutugin evaporation
# correction. The current wall flux composes HKL surface uptake and
# Bird/Stewart/Lightfoot gas-side mass transfer as series resistances:
#
#    1/k_total = 1/(alpha_s*k_HKL) + (1 - f)/k_MT
#
# So at Kn -> 0 (deep viscous) the boundary-layer mass-transfer resistance
# dominates; at Kn -> inf (free molecular) the mass-transfer resistance
# drops out and HKL surface uptake applies. The transition regime is a
# smooth resistance handoff.
#
# Sherwood number for laminar pipe flow with constant wall concentration
# (Bird/Stewart/Lightfoot 2007 "Transport Phenomena" 2nd ed., Eq 14.4-9):
#     Sh = 3.66 (asymptotic, fully developed laminar)
#
# For the simulator's overhead piping at C2A_continuous typical
# operating point (pN2 ~10 mbar, T_gas ~1700 C, D_pipe = 0.12 m), the
# Reynolds number Re = rho * v * D / mu is well below 2100 (laminar),
# so Sh = 3.66 is the right anchor. Mass-transfer coefficient:
#     k_c = Sh * D_AB / D_pipe   (m/s)
# And the deposition flux is:
#     J_mass = k_c * (P_local - P_sat) / (R * T_gas)   (mol/m^2/s)
#
# D_AB (binary diffusion coefficient of vapor A in carrier gas B) is
# pressure-inverse and weakly T-dependent. For SiO/Na/K vapor in N2
# at 10 mbar, 1700 C, Chapman-Enskog gives D_AB ~ 1.0e-2 m^2/s. The
# default below uses 1.0e-2 m^2/s as the order-of-magnitude anchor
# and documents the regime; species-specific refinements are open
# work (tickler §5 follow-on).
DEFAULT_SHERWOOD_LAMINAR = 3.66
# Per-species Chapman-Enskog diffusion is preferred when pressure and collision
# data produce a finite positive value. This constant remains the live fallback
# when pressure is absent/invalid or that computation fails, anchored to the
# historical SiO/N2 operating point at 10 mbar and 1700 C.
DEFAULT_BINARY_DIFFUSION_M2_S = 1.0e-2
GAS_CONSTANT_J_MOL_K = 8.314462618


def _carrier_collision_diameter_angstrom(species: str) -> float:
    return round(COLLISION_DIAMETERS_M[species] * 1e10, 3)


# Chapman-Enskog Lennard-Jones parameters (vapor species + carrier gas).
# Species: collision diameter (Angstrom), ε/k_B (K), molecular mass (g/mol).
# Primary source: Bird/Stewart/Lightfoot "Transport Phenomena" 2nd ed.
# Table E.1 for noble gases + Na/K/Ca; remaining species use the Svehla
# 1962 (NASA TR R-132) or Hirschfelder/Curtiss/Bird canonical estimates.
# Vapor-phase Fe/Cr/Mn/Al/Ti are estimates from atomic radii + Lennard-
# Jones rule-of-thumb (σ ≈ 1.18 × r_vdW, ε/k_B ≈ 1.3 × T_boiling) since
# direct kinetic-theory measurements for transition-metal vapor are
# sparse. The Chapman-Enskog result is moderately sensitive to σ (Ω_D
# ~constant in the high-T limit, D_AB ∝ 1/σ_AB²) and weakly sensitive
# to ε (collision integral Ω_D varies <30% across the simulator's T
# range). At the typical C2A operating point (10 mbar, 1973 K) the
# computed D_AB for SiO/N2 is ~0.042 m²/s vs the legacy 0.01 constant
# (4× higher) -- bringing the viscous-MT term into a more honest
# absolute magnitude.
_LENNARD_JONES_PARAMS: dict[str, tuple[float, float, float]] = {
    # (sigma Angstrom, eps/k_B K, M g/mol)
    # N2 sigma derives from N2_COLLISION_DIAMETER_M (one grounded source, BUG-013)
    'N2':  (N2_COLLISION_DIAMETER_M * 1e10, 71.4, 28.014),  # BSL Table E.1
    'Ar':  (_carrier_collision_diameter_angstrom('Ar'), 93.3,   39.948),  # BSL Table E.1
    'CO2': (_carrier_collision_diameter_angstrom('CO2'), 195.2,  44.010),  # BSL Table E.1
    'O2':  (_carrier_collision_diameter_angstrom('O2'), 106.7,  31.998),  # BSL Table E.1
    'Na':  (3.567, 1375.0, 22.990),  # Svehla 1962 vapor
    'K':   (3.987, 1305.0, 39.098),  # Svehla 1962 vapor
    'Ca':  (3.880, 1224.0, 40.078),  # BSL extension
    'Fe':  (2.940, 6026.0, 55.845),  # Estimated (transition-metal vapor)
    'Mg':  (3.060, 1614.0, 24.305),  # Estimated
    'Mn':  (2.950, 1100.0, 54.938),  # Estimated
    'Cr':  (2.880, 6000.0, 51.996),  # Estimated
    'Al':  (2.940, 3093.0, 26.982),  # Estimated
    'Ti':  (2.890, 6000.0, 47.867),  # Estimated
    'SiO': (3.374, 71.4,   44.085),  # Estimated; sparse direct data
}

_LENNARD_JONES_PROVENANCE: dict[str, dict[str, str]] = {
    'N2': {
        'status': 'sourced',
        'source': 'Bird/Stewart/Lightfoot Table E.1',
    },
    'Ar': {
        'status': 'sourced',
        'source': 'Bird/Stewart/Lightfoot Table E.1',
    },
    'CO2': {
        'status': 'sourced',
        'source': 'Bird/Stewart/Lightfoot Table E.1',
    },
    'O2': {
        'status': 'sourced',
        'source': 'Bird/Stewart/Lightfoot Table E.1',
    },
    'Na': {
        'status': 'sourced',
        'source': 'Svehla 1962 vapor transport table',
    },
    'K': {
        'status': 'sourced',
        'source': 'Svehla 1962 vapor transport table',
    },
    'Ca': {
        'status': 'proxy',
        'source': 'Bird/Stewart/Lightfoot extension; review before certification',
    },
    'Fe': {
        'status': 'proxy',
        'source': 'estimated transition-metal vapor Lennard-Jones row',
    },
    'Mg': {
        'status': 'proxy',
        'source': 'estimated vapor Lennard-Jones row',
    },
    'Mn': {
        'status': 'proxy',
        'source': 'estimated transition-metal vapor Lennard-Jones row',
    },
    'Cr': {
        'status': 'proxy',
        'source': 'estimated transition-metal vapor Lennard-Jones row',
    },
    'Al': {
        'status': 'proxy',
        'source': 'estimated transition-metal vapor Lennard-Jones row',
    },
    'Ti': {
        'status': 'proxy',
        'source': 'estimated transition-metal vapor Lennard-Jones row',
    },
    'SiO': {
        'status': 'proxy',
        'source': 'estimated sparse-data SiO vapor Lennard-Jones row',
    },
}

DEFAULT_CARRIER_GAS = 'N2'  # C2A pN2 sweep; CO2 for Mars feedstocks
STAGE_AREA_KEY_BY_STAGE_NUMBER = {
    1: 'fe_stage1',
    2: 'cr_stage2',
    3: 'sio_stage3',
    4: 'alkali_stage4',
    7: 'terminal',
}
SUPPORTED_CARRIER_GAS_LABELS = 'N2/pN2, Ar/pAr, CO2/pCO2'
STICKING_DATA_PATH = DATA_DIR / 'literature' / 'vacuum_pyrolysis_sticking.yaml'
WALL_REACTIVITY_MATRIX_PATH = (
    DATA_DIR / 'literature' / 'wall_reactivity_matrix.yaml'
)
STICKING_VALUE_REF_PREFIX = (
    'data/literature/vacuum_pyrolysis_sticking.yaml::species.'
)
STICKING_UNKNOWN_REF = (
    'data/literature/vacuum_pyrolysis_sticking.yaml::unknown_species_default'
)
STICKING_STATUSES = {'CITED', 'UNCERTIFIED'}
STICKING_REACTIVITY_CLASSES = {'reactive', 'physisorbing'}
C4B_WALL_ROUTE_ORDER = ('SiO', 'Mg', 'Fe', 'Na', 'K')


def _load_sticking_data(path: Path = STICKING_DATA_PATH) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    if not isinstance(raw, Mapping):
        raise ValueError(f'{path}: sticking data must be a mapping')
    species = raw.get('species')
    if not isinstance(species, Mapping) or not species:
        raise ValueError(f'{path}: missing species sticking table')
    for species_name, entry in species.items():
        _validate_sticking_entry(path, f'species.{species_name}', entry)
    reactivity_classes = raw.get('reactivity_class_by_species')
    if not isinstance(reactivity_classes, Mapping) or not reactivity_classes:
        raise ValueError(f'{path}: missing reactivity_class_by_species')
    for species_name, reactivity_class in reactivity_classes.items():
        if not isinstance(species_name, str) or not species_name:
            raise ValueError(f'{path}: reactivity_class species names must be strings')
        _validate_sticking_reactivity_class(
            path,
            f'reactivity_class_by_species.{species_name}',
            reactivity_class,
        )
    _validate_sticking_entry(
        path,
        'unknown_species_default',
        raw.get('unknown_species_default'),
    )
    floor = raw.get('capture_budget_regularizer_floor')
    if not isinstance(floor, Mapping):
        raise ValueError(f'{path}: missing capture_budget_regularizer_floor')
    _validate_sticking_value(
        path,
        'capture_budget_regularizer_floor.value',
        floor.get('value'),
    )
    if str(floor.get('status', '')).upper() not in STICKING_STATUSES:
        raise ValueError(f'{path}: regularizer floor must be CITED or UNCERTIFIED')
    if not floor.get('source') or not floor.get('source_class'):
        raise ValueError(f'{path}: regularizer floor needs source/source_class')
    time_constant = raw.get('capture_budget_regularizer_time_s')
    if not isinstance(time_constant, Mapping):
        raise ValueError(f'{path}: missing capture_budget_regularizer_time_s')
    if isinstance(time_constant.get('value'), bool):
        raise ValueError(f'{path}: regularizer time must be numeric, not boolean')
    try:
        time_s = float(time_constant.get('value'))
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{path}: regularizer time must be numeric') from exc
    if not math.isfinite(time_s) or time_s <= 0.0:
        raise ValueError(f'{path}: regularizer time must be finite and > 0')
    if str(time_constant.get('status', '')).upper() not in STICKING_STATUSES:
        raise ValueError(f'{path}: regularizer time must be CITED or UNCERTIFIED')
    if not time_constant.get('source') or not time_constant.get('source_class'):
        raise ValueError(f'{path}: regularizer time needs source/source_class')
    return dict(raw)


def _validate_sticking_entry(path: Path, name: str, entry: Any) -> None:
    if not isinstance(entry, Mapping):
        raise ValueError(f'{path}: {name} must be a mapping')
    _validate_sticking_spec(path, f'{name}.value', entry.get('value'))
    cold_wall = entry.get('cold_wall_condensation')
    if cold_wall is not None:
        _validate_cold_wall_condensation_spec(
            path,
            f'{name}.cold_wall_condensation',
            cold_wall,
        )
    if str(entry.get('status', '')).upper() not in STICKING_STATUSES:
        raise ValueError(f'{path}: {name}.status must be CITED or UNCERTIFIED')
    for key in ('source', 'source_class', 'temperature_range_K', 'uncertainty_flag'):
        if entry.get(key) in (None, ''):
            raise ValueError(f'{path}: {name}.{key} is required')


def _validate_sticking_reactivity_class(path: Path, name: str, value: Any) -> None:
    if value not in STICKING_REACTIVITY_CLASSES:
        allowed = ', '.join(sorted(STICKING_REACTIVITY_CLASSES))
        raise ValueError(f'{path}: {name} must be one of {allowed}')


def _validate_sticking_value(path: Path, name: str, value: Any) -> None:
    if isinstance(value, bool):
        raise ValueError(f'{path}: {name} must be numeric, not boolean')
    try:
        alpha_s = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{path}: {name} must be numeric') from exc
    if not math.isfinite(alpha_s) or not 0.0 <= alpha_s <= 1.0:
        raise ValueError(f'{path}: {name} must be finite and within [0, 1]')


def _validate_sticking_spec(path: Path, name: str, value: Any) -> None:
    if not isinstance(value, Mapping):
        _validate_sticking_value(path, name, value)
        return
    if value.get('form') != 'arrhenius':
        raise ValueError(f'{path}: {name}.form must be arrhenius')
    for key in ('A', 'B'):
        try:
            coeff = float(value.get(key))
        except (TypeError, ValueError) as exc:
            raise ValueError(f'{path}: {name}.{key} must be numeric') from exc
        if not math.isfinite(coeff) or coeff <= 0.0:
            raise ValueError(f'{path}: {name}.{key} must be finite and > 0')
    _validate_range_pair(path, f'{name}.valid_range_K',
                         value.get('valid_range_K'))
    _validate_range_pair(path, f'{name}.uncertainty_envelope',
                         value.get('uncertainty_envelope'),
                         lower_bound=0.0,
                         upper_bound=1.0)
    if str(value.get('status', '')).upper() not in STICKING_STATUSES:
        raise ValueError(f'{path}: {name}.status must be CITED or UNCERTIFIED')
    if not value.get('cite'):
        raise ValueError(f'{path}: {name}.cite is required')


def _validate_cold_wall_condensation_spec(
    path: Path,
    name: str,
    value: Any,
) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f'{path}: {name} must be a mapping')
    _validate_sticking_value(path, f'{name}.value', value.get('value'))
    if str(value.get('status', '')).upper() not in STICKING_STATUSES:
        raise ValueError(f'{path}: {name}.status must be CITED or UNCERTIFIED')
    for key in ('source', 'source_url', 'source_class', 'uncertainty_flag'):
        if value.get(key) in (None, ''):
            raise ValueError(f'{path}: {name}.{key} is required')
    _validate_range_pair(
        path,
        f'{name}.uncertainty_envelope',
        value.get('uncertainty_envelope'),
        lower_bound=0.0,
        upper_bound=1.0,
    )


def _validate_range_pair(
    path: Path,
    name: str,
    value: Any,
    *,
    lower_bound: float | None = None,
    upper_bound: float | None = None,
) -> None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f'{path}: {name} must be a two-value range')
    try:
        low = float(value[0])
        high = float(value[1])
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{path}: {name} must be numeric') from exc
    if not (math.isfinite(low) and math.isfinite(high) and low <= high):
        raise ValueError(f'{path}: {name} must be finite and ascending')
    if lower_bound is not None and low < lower_bound:
        raise ValueError(f'{path}: {name} lower bound below {lower_bound}')
    if upper_bound is not None and high > upper_bound:
        raise ValueError(f'{path}: {name} upper bound above {upper_bound}')


def _validate_wall_reaction(
    path: Path,
    name: str,
    entry: Any,
    *,
    species: str,
    status: str,
) -> None:
    if not isinstance(entry, Mapping):
        raise ValueError(f'{path}: reactions.{name} must be a mapping')
    if entry.get('vapor_species') != species:
        raise ValueError(
            f'{path}: reactions.{name}.vapor_species must be {species!r}'
        )
    if entry.get('status') != status:
        raise ValueError(f'{path}: reactions.{name} missing status {status!r}')
    refs = entry.get('source_refs')
    if not isinstance(refs, list) or not refs:
        raise ValueError(f'{path}: reactions.{name} missing source_refs')


def _validate_alkali_activity_entry(path: Path, species: str, entry: Any) -> None:
    if not isinstance(entry, Mapping):
        raise ValueError(
            f'{path}: alkali_activity_depression.{species} must be a mapping'
        )
    if entry.get('ledger_species') != species:
        raise ValueError(
            f'{path}: alkali_activity_depression.{species}.ledger_species '
            f'must be {species!r}'
        )
    if entry.get('authoritative_ledger') is not False:
        raise ValueError(
            f'{path}: alkali_activity_depression.{species} must be diagnostic-only'
        )
    if entry.get('ledger_credit_species') != species:
        raise ValueError(
            f'{path}: alkali_activity_depression.{species}.ledger_credit_species '
            f'must be {species!r}'
        )
    saturation = entry.get('saturation')
    if not isinstance(saturation, Mapping):
        raise ValueError(
            f'{path}: alkali_activity_depression.{species} missing saturation'
        )
    nominal = saturation.get('nominal_cold_wall')
    if not isinstance(nominal, (int, float)) or float(nominal) <= 0.0:
        raise ValueError(
            f'{path}: alkali_activity_depression.{species} missing numeric '
            'nominal_cold_wall saturation'
        )
    anchor = saturation.get('primary_anchor')
    if not isinstance(anchor, Mapping) or not anchor.get('citation'):
        raise ValueError(
            f'{path}: alkali_activity_depression.{species} missing cited '
            'primary_anchor'
        )
    if not entry.get('status'):
        raise ValueError(
            f'{path}: alkali_activity_depression.{species} missing status'
        )
    forbidden = entry.get('ledger_forbidden')
    if not isinstance(forbidden, list) or not forbidden:
        raise ValueError(
            f'{path}: alkali_activity_depression.{species} missing ledger_forbidden'
        )


def _load_wall_reactivity_matrix(
    path: Path = WALL_REACTIVITY_MATRIX_PATH,
) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    if not isinstance(raw, Mapping):
        raise ValueError(f'{path}: wall reactivity matrix must be a mapping')
    if raw.get('version') != 1:
        raise ValueError(f'{path}: unsupported wall reactivity matrix version')
    reactions = raw.get('reactions')
    if not isinstance(reactions, Mapping):
        raise ValueError(f'{path}: missing reactions table')
    _validate_wall_reaction(
        path,
        'SiO_disproportionation',
        reactions.get('SiO_disproportionation'),
        species='SiO',
        status='GROUNDED_STOICHIOMETRY',
    )
    _validate_wall_reaction(
        path,
        'Mg_silica_reduction',
        reactions.get('Mg_silica_reduction'),
        species='Mg',
        status='GROUNDED_STOICHIOMETRY_RATE_GAP',
    )
    _validate_wall_reaction(
        path,
        'Fe_silicide',
        reactions.get('Fe_silicide'),
        species='Fe',
        status='OWNER_CONFIRMED_PRODUCT_RATE_GAP',
    )
    alkali = raw.get('alkali_activity_depression')
    if not isinstance(alkali, Mapping):
        raise ValueError(f'{path}: missing alkali_activity_depression')
    for species in ('Na', 'K'):
        _validate_alkali_activity_entry(path, species, alkali.get(species))
    return dict(raw)


def _wall_route_species_order(species_names: Any) -> tuple[str, ...]:
    names = tuple(str(species) for species in species_names)
    present = set(names)
    ordered = [species for species in C4B_WALL_ROUTE_ORDER if species in present]
    ordered.extend(species for species in names if species not in C4B_WALL_ROUTE_ORDER)
    return tuple(ordered)


STICKING_DATA = _load_sticking_data()
WALL_REACTIVITY_MATRIX = _load_wall_reactivity_matrix()


def _sticking_species_entries() -> Mapping[str, Any]:
    entries = STICKING_DATA.get('species', {})
    return entries if isinstance(entries, Mapping) else {}


def _sticking_species_entry(species: str) -> Mapping[str, Any]:
    entries = _sticking_species_entries()
    entry = entries.get(species)
    if isinstance(entry, Mapping):
        return entry
    fallback = STICKING_DATA.get('unknown_species_default', {})
    return fallback if isinstance(fallback, Mapping) else {}


def _cold_wall_condensation_spec(species: str) -> Mapping[str, Any] | None:
    if str(species) != 'SiO':
        return None
    block = _sticking_species_entry(species).get('cold_wall_condensation')
    return block if isinstance(block, Mapping) else None


def _sio_cold_wall_condensation_override(
    species: str,
    T_K: float,
    spec: Any,
    evaluation: Mapping[str, Any],
) -> tuple[float, dict[str, Any]] | None:
    if str(species) != 'SiO' or not isinstance(spec, Mapping):
        return None
    valid_range = spec.get('valid_range_K')
    if not isinstance(valid_range, (list, tuple)) or len(valid_range) != 2:
        return None
    try:
        valid_low = float(valid_range[0])
        T_value = float(T_K)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(valid_low) or not math.isfinite(T_value):
        return None
    if T_value >= valid_low:
        return None
    block = _cold_wall_condensation_spec(species)
    if block is None:
        return None
    value = float(block.get('value'))
    envelope = block.get('uncertainty_envelope')
    override = dict(evaluation)
    override.update({
        'alpha_s': value,
        'alpha_s_form': 'cold_wall_condensation',
        'alpha_s_raw_arrhenius': evaluation.get('alpha_s'),
        'alpha_s_extrapolated': True,
        'alpha_s_temperature_below_valid_range': True,
        'alpha_s_condensation_regime': 'cold_wall_high_supersaturation',
        'alpha_s_cold_wall_condensation': True,
        'alpha_s_cold_wall_validity_floor_K': valid_low,
        'alpha_s_cold_wall_source': str(block.get('source', '')),
        'alpha_s_cold_wall_source_url': block.get('source_url'),
        'alpha_s_cold_wall_source_class': str(block.get('source_class', '')),
        'alpha_s_cold_wall_uncertainty_envelope': (
            list(envelope) if isinstance(envelope, (list, tuple)) else None
        ),
        'alpha_s_cold_wall_uncertainty_flag': block.get('uncertainty_flag'),
        'alpha_s_cold_wall_status': str(block.get('status', '')).upper(),
    })
    return value, override


def _condensation_alpha_s(
    species: str,
    T_K: float,
    context: MutableMapping[str, Any],
) -> float:
    species_name = str(species)
    spec = (
        context.get('coefficient_spec')
        if 'coefficient_spec' in context
        else _sticking_species_entry(species_name).get('value')
    )
    value = alpha_s(species_name, T_K, context)
    evaluation = dict(context.get('alpha_s_evaluation', {}))
    override = _sio_cold_wall_condensation_override(
        species_name,
        T_K,
        spec,
        evaluation,
    )
    if override is not None:
        value, evaluation = override
        context['alpha_s_evaluation'] = dict(evaluation)
    return value


def _sticking_alpha_s(species: str, T_K: float) -> float:
    context = {'coefficient_spec': _sticking_species_entry(species).get('value')}
    return _condensation_alpha_s(species, T_K, context)


def _sticking_reactivity_class(species: str) -> str:
    classes = STICKING_DATA.get('reactivity_class_by_species')
    if not isinstance(classes, Mapping):
        raise ValueError(f'{STICKING_DATA_PATH}: missing reactivity_class_by_species')
    species_name = str(species)
    if species_name not in classes:
        raise ValueError(
            f'{STICKING_DATA_PATH}: missing reactivity_class for '
            f'species {species_name}'
        )
    reactivity_class = classes.get(species_name)
    _validate_sticking_reactivity_class(
        STICKING_DATA_PATH,
        f'reactivity_class_by_species.{species_name}',
        reactivity_class,
    )
    return str(reactivity_class)


def _sticking_ref_record(species: str, ref: Any) -> Mapping[str, Any] | None:
    if not isinstance(ref, str):
        return None
    if ref == STICKING_UNKNOWN_REF:
        fallback = STICKING_DATA.get('unknown_species_default')
        return fallback if isinstance(fallback, Mapping) else None
    if not ref.startswith(STICKING_VALUE_REF_PREFIX):
        return None
    suffix = ref.removeprefix(STICKING_VALUE_REF_PREFIX)
    if not suffix.endswith('.value'):
        return None
    ref_species = suffix[:-len('.value')]
    if ref_species != species:
        return None
    return _sticking_species_entry(species)


def _sticking_entry_ref_record(species: str, entry: Any) -> Mapping[str, Any] | None:
    if isinstance(entry, Mapping):
        return _sticking_ref_record(species, entry.get('value_ref'))
    return None


def _alpha_certification_source_class_token(value: Any) -> str:
    return str(value or '').strip().lower().replace('_', '-')


def _alpha_source_class_cannot_certify(value: Any) -> bool:
    return canonical_backend_name(_alpha_certification_source_class_token(value)) == (
        ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
    )


def _sticking_record_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    status = str(record.get('status', '')).upper()
    denied = _alpha_source_class_cannot_certify(record.get('source_class'))
    cited = status == 'CITED' and not denied
    output_status = str(record.get('output_status') or (
        'sourced_with_surface_proxy'
        if cited
        else 'status_bearing'
    ))
    if not cited:
        output_status = 'status_bearing'
    payload = {
        'source': str(record.get('source', '')),
        'source_url': record.get('source_url'),
        'source_class': str(record.get('source_class', '')),
        'status': 'sourced' if cited else 'UNCERTIFIED',
        'citation_status': 'CITED' if cited else 'UNCERTIFIED',
        'temperature_range_K': record.get('temperature_range_K'),
        'envelope': record.get('envelope'),
        'uncertainty_flag': record.get('uncertainty_flag'),
        'output_status': output_status,
    }
    if denied:
        payload['certification_status_reason'] = (
            'sticking alpha_s source_class '
            f'{_alpha_certification_source_class_token(record.get("source_class"))} '
            'cannot certify'
        )
    return payload


def _material_alpha_source_label(source: str) -> str | None:
    if source.startswith('data/materials.yaml::stages.'):
        return 'materials.yaml per-stage alpha_s'
    if source.startswith('data/materials.yaml::wall_surfaces.'):
        return 'materials.yaml wall alpha_s'
    if source.startswith('data/materials.yaml::liner_materials.'):
        return 'materials.yaml liner alpha_s'
    return None


def _material_alpha_certification_payload(
    entry: Any,
    record: Mapping[str, Any],
    *,
    source: str,
) -> dict[str, Any]:
    label = _material_alpha_source_label(source)
    if label is None:
        return {}
    if isinstance(entry, Mapping):
        raw_status_value = (
            entry.get('citation_status')
            or entry.get('status')
        )
        source_class_value = entry.get('source_class')
        entry_output_status = entry.get('output_status')
    else:
        raw_status_value = None
        source_class_value = None
        entry_output_status = None

    if raw_status_value in (None, ''):
        raw_status_value = (
            record.get('citation_status')
            or record.get('status')
            or ''
        )
    if source_class_value in (None, ''):
        source_class_value = record.get('source_class') or ''
    if entry_output_status in (None, ''):
        entry_output_status = record.get('output_status')

    raw_status = str(raw_status_value or '').upper()
    denied = _alpha_source_class_cannot_certify(source_class_value)
    cited = raw_status == 'CITED' and not denied
    output_status = (
        str(entry_output_status)
        if entry_output_status not in (None, '')
        else (
            'sourced_with_surface_proxy'
            if cited
            else 'status_bearing'
        )
    )
    if not cited:
        output_status = 'status_bearing'

    payload = {
        'citation_status': 'CITED' if cited else 'UNCERTIFIED',
        'status': 'sourced' if cited else 'UNCERTIFIED',
        'output_status': output_status,
    }
    if denied:
        payload['certification_status_reason'] = (
            f'{label} source_class '
            f'{_alpha_certification_source_class_token(source_class_value)} '
            'cannot certify'
        )
    elif raw_status not in {'CITED', 'UNCERTIFIED'}:
        payload['certification_status_reason'] = (
            f'{label} override lacks '
            'CITED/UNCERTIFIED certification status'
        )
    return payload


def _cold_wall_condensation_record_payload(
    species: str,
    evaluation: Mapping[str, Any],
) -> dict[str, Any]:
    block = _cold_wall_condensation_spec(species)
    if block is None:
        return {}
    status = str(block.get('status', '')).upper()
    denied = _alpha_source_class_cannot_certify(block.get('source_class'))
    cited = status == 'CITED' and not denied
    output_status = str(block.get('output_status') or (
        'sourced_with_surface_proxy'
        if cited
        else 'status_bearing'
    ))
    if not cited:
        output_status = 'status_bearing'
    payload = {
        'source': str(block.get('source', '')),
        'source_url': block.get('source_url'),
        'source_class': str(block.get('source_class', '')),
        'status': 'sourced' if cited else 'UNCERTIFIED',
        'citation_status': 'CITED' if cited else 'UNCERTIFIED',
        'envelope': block.get('uncertainty_envelope'),
        'uncertainty_flag': block.get('uncertainty_flag'),
        'output_status': output_status,
        'cold_wall_condensation': True,
        'cold_wall_condensation_temperature_rule': (
            'T_K < alpha_s_valid_range_K[0]'
        ),
    }
    if denied:
        payload['certification_status_reason'] = (
            'cold-wall condensation source_class '
            f'{_alpha_certification_source_class_token(block.get("source_class"))} '
            'cannot certify'
        )
    floor = evaluation.get('alpha_s_cold_wall_validity_floor_K')
    if floor is not None:
        payload['cold_wall_condensation_validity_floor_K'] = floor
    return payload


_ANTOINE_TELEMETRY_CONTEXT: ContextVar[
    tuple[MutableMapping[str, Dict[str, Any]] | None, list[str] | None] | None
] = ContextVar('condensation_antoine_telemetry_context', default=None)


def _valid_temperature_range_K(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        low = float(value[0])
        high = float(value[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(low) and math.isfinite(high) and low <= high):
        return None
    return [low, high]


def _scalar_alpha_s_spec(value: Any, temperature_range_K: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value
    valid_range = _valid_temperature_range_K(temperature_range_K)
    if valid_range is None:
        return value
    return {
        'form': 'scalar',
        'value': value,
        'temperature_range_K': valid_range,
    }


def _alpha_s_extrapolation_warning(
    species: str,
    form: str,
    T_K: float,
    valid_low: float,
    valid_high: float,
) -> str:
    return (
        f'{species} alpha_s {form} coefficient extrapolated beyond '
        f'valid_range_K [{valid_low:g}, {valid_high:g}] at {T_K:.2f} K'
    )


def _alpha_s_scalar_evaluation(
    species: str,
    T_K: float,
    spec: Mapping[str, Any],
) -> tuple[float, dict[str, Any]]:
    try:
        value = float(spec.get('value'))
    except (TypeError, ValueError) as exc:
        raise ValueError(f'alpha_s({species}): scalar coefficient required') from exc
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f'alpha_s({species}): scalar outside [0, 1]')

    valid_range = _valid_temperature_range_K(spec.get('temperature_range_K'))
    extrapolated = False
    result: dict[str, Any] = {
        'species': str(species),
        'alpha_s': value,
        'alpha_s_form': 'scalar',
        'alpha_s_temperature_K': T_K,
        'alpha_s_extrapolated': False,
    }
    if valid_range is not None:
        valid_low, valid_high = valid_range
        extrapolated = not (valid_low <= T_K <= valid_high)
        result.update({
            'alpha_s_valid_range_K': [valid_low, valid_high],
            'alpha_s_temperature_range_K': [valid_low, valid_high],
            'alpha_s_extrapolated': extrapolated,
        })
        if extrapolated:
            result['alpha_s_extrapolation_warning'] = (
                _alpha_s_extrapolation_warning(
                    species,
                    'scalar',
                    T_K,
                    valid_low,
                    valid_high,
                )
            )
            result[
                'alpha_s_temperature_below_valid_range'
                if T_K < valid_low
                else 'alpha_s_temperature_above_valid_range'
            ] = True
    return value, result


def _alpha_s_extrapolation_warnings_from_provenance(
    provenance_by_species: Mapping[str, Any],
) -> list[str]:
    warnings_out: list[str] = []
    for by_record in provenance_by_species.values():
        if not isinstance(by_record, Mapping):
            continue
        for record in by_record.values():
            if not isinstance(record, Mapping):
                continue
            warning = record.get('alpha_s_extrapolation_warning')
            if warning is not None:
                text = str(warning)
                if text and text not in warnings_out:
                    warnings_out.append(text)
    return warnings_out


def _with_antoine_telemetry_context(
    antoine_extrapolations: MutableMapping[str, Dict[str, Any]] | None,
    antoine_extrapolation_warnings: list[str] | None,
) -> Any:
    if antoine_extrapolations is None and antoine_extrapolation_warnings is None:
        return None
    return _ANTOINE_TELEMETRY_CONTEXT.set((
        antoine_extrapolations,
        antoine_extrapolation_warnings,
    ))


def _reset_antoine_telemetry_context(token: Any) -> None:
    if token is not None:
        _ANTOINE_TELEMETRY_CONTEXT.reset(token)


def _resolve_antoine_telemetry(
    antoine_extrapolations: MutableMapping[str, Dict[str, Any]] | None,
    antoine_extrapolation_warnings: list[str] | None,
) -> tuple[MutableMapping[str, Dict[str, Any]] | None, list[str] | None]:
    if antoine_extrapolations is not None or antoine_extrapolation_warnings is not None:
        return antoine_extrapolations, antoine_extrapolation_warnings
    context = _ANTOINE_TELEMETRY_CONTEXT.get()
    if context is None:
        return None, None
    return context


def _alpha_s_spec_from_entry(species: str, entry: Any) -> Any:
    if isinstance(entry, Mapping):
        if 'value_ref' in entry:
            ref_record = _sticking_entry_ref_record(
                str(entry.get('species', species)),
                entry,
            )
            if ref_record is None:
                raise ValueError(
                    f'alpha_s({species}): unresolved value_ref '
                    f'{entry.get("value_ref")!r}'
                )
            return _scalar_alpha_s_spec(
                ref_record.get('value'),
                ref_record.get('temperature_range_K'),
            )
        for key in ('value', 'alpha_s', 'alpha_s_value'):
            if key in entry:
                return _scalar_alpha_s_spec(
                    entry.get(key),
                    entry.get('temperature_range_K'),
                )
        return None
    return entry


def _alpha_s_evaluation(
    species: str,
    T_K: float,
    spec: Any,
) -> tuple[float, dict[str, Any]]:
    try:
        T_K = float(T_K)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'alpha_s({species}): T_K must be numeric') from exc
    if not math.isfinite(T_K) or T_K <= 0.0:
        raise ValueError(f'alpha_s({species}): T_K must be finite and > 0')

    if isinstance(spec, Mapping):
        form = spec.get('form')
        if form == 'scalar':
            return _alpha_s_scalar_evaluation(species, T_K, spec)
        if form != 'arrhenius':
            raise ValueError(f'alpha_s({species}): malformed coefficient spec')
        required_fields = (
            'A',
            'B',
            'valid_range_K',
            'uncertainty_envelope',
            'cite',
            'status',
        )
        missing_fields = [field for field in required_fields if field not in spec]
        if missing_fields:
            raise ValueError(
                f'alpha_s({species}): malformed arrhenius coefficient spec; '
                f"missing {', '.join(missing_fields)}"
            )
        try:
            A = float(spec['A'])
            B = float(spec['B'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f'alpha_s({species}): malformed arrhenius coefficient spec'
            ) from exc
        if (
            not math.isfinite(A)
            or not math.isfinite(B)
            or A <= 0.0
            or B <= 0.0
        ):
            raise ValueError(
                f'alpha_s({species}): arrhenius A/B must be finite and > 0'
            )
        cite = str(spec.get('cite') or '').strip()
        status = str(spec.get('status') or '').upper()
        if not cite or status not in {'CITED', 'UNCERTIFIED'}:
            raise ValueError(
                f'alpha_s({species}): malformed arrhenius coefficient metadata'
            )
        valid_range = spec.get('valid_range_K')
        if not isinstance(valid_range, (list, tuple)) or len(valid_range) != 2:
            raise ValueError(f'alpha_s({species}): malformed valid_range_K')
        try:
            valid_low = float(valid_range[0])
            valid_high = float(valid_range[1])
        except (TypeError, ValueError) as exc:
            raise ValueError(f'alpha_s({species}): malformed valid_range_K') from exc
        if (
            not math.isfinite(valid_low)
            or not math.isfinite(valid_high)
            or valid_low <= 0.0
            or valid_high < valid_low
        ):
            raise ValueError(f'alpha_s({species}): malformed valid_range_K')
        envelope = spec.get('uncertainty_envelope')
        if not isinstance(envelope, (list, tuple)) or len(envelope) != 2:
            raise ValueError(
                f'alpha_s({species}): malformed uncertainty_envelope'
            )
        try:
            envelope_low = float(envelope[0])
            envelope_high = float(envelope[1])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f'alpha_s({species}): malformed uncertainty_envelope'
            ) from exc
        if (
            not math.isfinite(envelope_low)
            or not math.isfinite(envelope_high)
            or not 0.0 <= envelope_low <= envelope_high <= 1.0
        ):
            raise ValueError(
                f'alpha_s({species}): malformed uncertainty_envelope'
            )
        value = A * math.exp(-B / T_K)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f'alpha_s({species}): evaluated value outside [0, 1]')
        extrapolated = not (valid_low <= T_K <= valid_high)
        result = {
            'species': str(species),
            'alpha_s': value,
            'alpha_s_form': 'arrhenius',
            'alpha_s_temperature_K': T_K,
            'alpha_s_A': A,
            'alpha_s_B': B,
            'alpha_s_valid_range_K': (
                [valid_low, valid_high]
                if valid_low is not None and valid_high is not None
                else None
            ),
            'alpha_s_extrapolated': extrapolated,
            'alpha_s_uncertainty_envelope': [envelope_low, envelope_high],
            'alpha_s_cite': cite,
            'alpha_s_status': status,
        }
        if T_K > valid_high:
            result['alpha_s_temperature_above_valid_range'] = True
            result['alpha_s_extrapolation_warning'] = (
                _alpha_s_extrapolation_warning(
                    species,
                    'arrhenius',
                    T_K,
                    valid_low,
                    valid_high,
                )
            )
        return value, result

    try:
        value = float(spec)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'alpha_s({species}): scalar coefficient required') from exc
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f'alpha_s({species}): scalar outside [0, 1]')
    return value, {
        'species': str(species),
        'alpha_s': value,
        'alpha_s_form': 'scalar',
        'alpha_s_temperature_K': T_K,
        'alpha_s_extrapolated': False,
    }


def alpha_s(
    species: str,
    T_K: float,
    context: MutableMapping[str, Any] | Mapping[str, Any] | None = None,
) -> float:
    species_name = str(species)
    spec = None
    if isinstance(context, Mapping):
        if 'coefficient_spec' in context:
            spec = context.get('coefficient_spec')
        elif 'entry' in context:
            spec = _alpha_s_spec_from_entry(species_name, context.get('entry'))
    if spec is None:
        spec = _alpha_s_spec_from_entry(
            species_name,
            _sticking_species_entry(species_name),
        )
    value, evaluation = _alpha_s_evaluation(species_name, T_K, spec)
    if isinstance(context, MutableMapping):
        context['alpha_s_evaluation'] = dict(evaluation)
    return value


def _nominal_sticking_value(value: Any) -> float:
    if isinstance(value, Mapping):
        prior = value.get('prior_scalar')
        if isinstance(prior, Mapping) and 'value' in prior:
            value = prior.get('value')
        else:
            return 0.0
    try:
        alpha_value = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(alpha_value):
        return 0.0
    return max(0.0, min(1.0, alpha_value))


STICKING_COEFF = {
    str(species): _nominal_sticking_value(entry.get('value'))
    for species, entry in _sticking_species_entries().items()
    if isinstance(entry, Mapping)
}

CAPTURE_BUDGET_REGULARIZER_FLOOR = float(
    STICKING_DATA['capture_budget_regularizer_floor']['value']
)
CAPTURE_BUDGET_REGULARIZER_TIME_S = float(
    STICKING_DATA['capture_budget_regularizer_time_s']['value']
)
CAPTURE_BUDGET_REGULARIZER_NOTICE = {
    'severity': 'warning',
    'code': 'pressure_isolated_capture_budget_regularizer_uncertified',
    'source_class': STICKING_DATA['capture_budget_regularizer_floor']['source_class'],
    'floor': CAPTURE_BUDGET_REGULARIZER_FLOOR,
    'time_constant_s': CAPTURE_BUDGET_REGULARIZER_TIME_S,
    'source': STICKING_DATA['capture_budget_regularizer_floor']['source'],
    'time_constant_source': STICKING_DATA['capture_budget_regularizer_time_s']['source'],
    'citation_status': STICKING_DATA['capture_budget_regularizer_floor']['status'],
    'time_constant_citation_status': (
        STICKING_DATA['capture_budget_regularizer_time_s']['status']
    ),
    'uncertainty_flag': (
        STICKING_DATA['capture_budget_regularizer_floor']['uncertainty_flag']
    ),
    'usage': '_pressure_isolated_stage_efficiency',
    'output_status': 'uncertainty_only',
    'message': (
        'Pressure-isolated capture budget uses a documented numerical '
        'regularizer floor; the value is surfaced as uncertainty-only and '
        'must not be treated as a sourced condensation constant.'
    ),
}

# ``MAX_STIR_FACTOR`` + ``clamp_stir_factor`` are canonical in
# ``simulator/state.py`` (where ``MeltState.stir_factor`` lives). They
# are imported here so the condensation Sherwood-enhancement honours the
# same ceiling as the evaporation linear-multiplier consumer at
# ``engines/builtin/evaporation_flux.py``. See ``MeltState.stir_factor``
# doc for the two-consumer rationale and the codex/gstack reviewer
# trail that surfaced the clamp-asymmetry P1.


def _stirring_enhanced_sherwood(
    stir_factor: float | None = None,
    sherwood_laminar: float = DEFAULT_SHERWOOD_LAMINAR,
    *,
    radial_stir_factor: float | None = None,
) -> float:
    """Enhanced Sherwood number for induction-stirred gas-pipe boundary
    layer.

    For an unstirred, fully-developed laminar pipe flow with constant
    wall concentration the asymptotic Sherwood number is 3.66 (Bird/
    Stewart/Lightfoot Eq 14.4-9). Induction stirring on the melt
    creates surface waves + vigorous convection in the gas just above
    the melt, which enhances bulk-to-wall mass transfer. Without
    stirring (factor=1) the laminar value applies. With operator
    stirring (factor 4-8 per `setpoints.yaml §C2A induction_stirring`),
    Sh increases by the rough square-root of the factor — a mild
    forced-convection correction in the Frössling style
    (`Sh = 2 + 0.6 Re^0.5 Sc^0.33`) without committing to a particular
    pipe-vs-tank correlation (the geometry is hybrid).

    0.5.3 Phase B (2-axis stirring) — which axis drives Sh:

    The Sherwood enhancement reads the RADIAL stirring axis (in-plane
    EM stirring drives the gas-side boundary-layer vortex, which is
    what reduces the bulk-to-wall mass-transport resistance). The
    axial axis drives a different consumer (the H-K-L linear
    multiplier in ``engines/builtin/evaporation_flux.py``) — it
    represents vertical melt-side surface renewal, not gas-side
    boundary-layer transport. Mixing them up would double-count the
    same physical knob.

    Signature contract (post-0.5.3):

    - ``radial_stir_factor`` (kwarg): canonical 2-axis caller path.
      Pass ``melt.stir_state.radial`` through
      ``CondensationModel.configure_operating_conditions`` →
      ``_series_resistance_deposition_flux_mol_m2_s`` → here.
    - ``stir_factor`` (positional): legacy scalar entry point preserved
      for backward-compat with pre-0.5.3 callers AND for direct-call
      unit tests that exercise the BSL Sh = 3.66·√stir_factor relation
      without going through the 2-axis ``StirState``. Historically this
      WAS the Sh driver, so treating it as the radial equivalent when
      no ``radial_stir_factor`` is supplied keeps every legacy caller
      and test green.
    - Precedence: ``radial_stir_factor`` wins if both are supplied
      (the new explicit kwarg cannot be silently overridden by a
      stale positional fallback).
    - Both ``None``: caller didn't drive Sh at all → no-stir laminar
      baseline (``Sh = 3.66``).

    Constrained: the driving factor is clamped to ``[0.0,
    MAX_STIR_FACTOR]`` at the operator boundary, with a Sh physics
    floor at 1.0 (laminar baseline never collapses to zero). Anything
    beyond ``MAX_STIR_FACTOR`` breaks the gas-side boundary-layer
    assumption AND the recipe setpoints; the clamp keeps physics
    defensible even under a bad campaign override.

    Returns: Sh in the range [``sherwood_laminar``, ~11.6] for the
    driving radial factor in [0, MAX_STIR_FACTOR].

    Two-tier clamp (codex /code-review max-effort, Phase B):

    - ``clamp_stir_factor`` is the OPERATOR-facing clamp ``[0, 10]``.
      It preserves the "halt evap" signal at the source for the
      evaporation consumer at ``axial=0`` AND maps non-finite/bool/
      etc to 0.
    - This helper applies its OWN physics floor at ``1.0`` so that
      Sherwood never drops below the BSL laminar-pipe asymptote
      ``Sh = 3.66`` regardless of operator value. Without stirring
      the gas-side boundary layer still has finite natural-convection
      transport; ``Sh = 0`` is unphysical.

    Net mapping for the Sherwood path:

    - factor = 0 (halt-evap signal): Sh = 3.66 (laminar baseline)
    - factor = 1 (no radial stir): Sh = 3.66 (matches)
    - factor = 6 (legacy C2A scalar default): Sh ≈ 9.0
    - factor = 10 (operator ceiling): Sh ≈ 11.6
    - factor = 100 or NaN: clamped/sanitised at the operator boundary,
      then Sh = 3.66 (defensive baseline)
    """
    # Precedence: explicit ``radial_stir_factor`` wins over the legacy
    # positional ``stir_factor``. Both None → no-stir baseline (1.0).
    if radial_stir_factor is not None:
        operator_value = clamp_stir_factor(radial_stir_factor)
    elif stir_factor is not None:
        # Legacy scalar caller: pre-0.5.3 ``stir_factor`` historically
        # drove Sh, so map it to the same operator-bounded value as
        # the new radial path would. Pre-Phase-B tests + direct call
        # sites in this module stay green via this fallback.
        operator_value = clamp_stir_factor(stir_factor)
    else:
        operator_value = 1.0
    # Physics floor: Sherwood must not drop below the laminar
    # asymptote. ``operator_value=0`` (halt-evap signal) maps here
    # to Sh = sherwood_laminar, not Sh = 0.
    sh_input = max(1.0, operator_value)
    return float(sherwood_laminar) * math.sqrt(sh_input)


def gram_lab_exposed_melt_area_bridge(
    lab_geometry: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(lab_geometry, Mapping):
        return {}
    if str(lab_geometry.get('scale') or '').strip() != 'gram_lab':
        return {}
    sample = lab_geometry.get('sample')
    if not isinstance(sample, Mapping):
        return {}
    raw_area = sample.get('exposed_melt_area_m2')
    if raw_area in (None, ''):
        return {}
    if isinstance(raw_area, bool):
        raise LabGeometryError(
            'invalid_lab_geometry_positive_value',
            'lab_geometry.sample.exposed_melt_area_m2 must be finite positive',
        )
    try:
        area = float(raw_area)
    except (TypeError, ValueError) as exc:
        raise LabGeometryError(
            'invalid_lab_geometry_positive_value',
            'lab_geometry.sample.exposed_melt_area_m2 must be finite positive',
        ) from exc
    if not math.isfinite(area) or area <= 0.0:
        raise LabGeometryError(
            'invalid_lab_geometry_positive_value',
            'lab_geometry.sample.exposed_melt_area_m2 must be finite positive',
        )
    return {
        'effective_exposed_area_m2': area,
        'area_basis': LAB_EXPOSED_MELT_AREA_BASIS,
    }


def _neufeld_collision_integral_omega_d(T_star: float) -> float:
    """Neufeld 1972 correlation for the dimensionless collision integral
    ``Ω_D`` as a function of the reduced temperature
    ``T* = k_B * T / ε_AB``. Accurate to ≲0.3% across the typical
    pyrolysis temperature range (T* ~3-50 for transition-metal vapor in
    N2 at 1500-2000 K).

    Reference: Neufeld, P.D., Janzen, A.R., Aziz, R.A.,
    "Empirical equations to calculate 16 of the transport collision
    integrals for the Lennard-Jones (12-6) potential",
    J. Chem. Phys. 57, 1100 (1972).
    """
    if T_star <= 0.0:
        return 1.0
    return (
        1.06036 / (T_star ** 0.1561)
        + 0.193 / math.exp(0.47635 * T_star)
        + 1.03587 / math.exp(1.52996 * T_star)
        + 1.76474 / math.exp(3.89411 * T_star)
    )


def _chapman_enskog_d_ab_m2_s(
    species: str,
    T_K: float,
    pressure_pa: float,
    carrier: str = DEFAULT_CARRIER_GAS,
) -> float:
    """Binary diffusion coefficient ``D_AB`` for ``species`` in
    ``carrier`` gas at ``T_K``, ``pressure_pa``. Returns m²/s.

    Standard kinetic-theory form (Bird/Stewart/Lightfoot Eq 17.3-10):

        D_AB [cm²/s] = 0.00266 * T^1.5 / (P[atm] * M_AB^0.5 * σ_AB² * Ω_D)

    where:
        T in Kelvin
        P in atmospheres (1 atm = 101325 Pa)
        M_AB = 2 / (1/M_A + 1/M_B)   (reduced molecular mass, g/mol)
        σ_AB = (σ_A + σ_B) / 2       (collision diameter, Angstrom)
        Ω_D  = Neufeld collision integral at T* = T * k_B / ε_AB

    Returns 0 on unknown species (caller falls back to the legacy
    constant via the explicit ``diffusion_coefficient_m2_s`` parameter
    on the flux callers).
    """
    species_params = _LENNARD_JONES_PARAMS.get(species)
    carrier_key = _canonical_carrier_gas_key(carrier)
    carrier_params = _LENNARD_JONES_PARAMS.get(carrier_key)
    if species_params is None or carrier_params is None:
        return 0.0
    if T_K <= 0.0 or pressure_pa <= 0.0:
        return 0.0
    sigma_a, eps_a, M_a = species_params
    sigma_b, eps_b, M_b = carrier_params
    sigma_ab = 0.5 * (sigma_a + sigma_b)            # Angstrom
    eps_ab = math.sqrt(eps_a * eps_b)               # K
    M_ab_reduced = 2.0 / (1.0 / M_a + 1.0 / M_b)    # g/mol
    T_star = T_K / eps_ab
    omega_d = _neufeld_collision_integral_omega_d(T_star)
    pressure_atm = pressure_pa / 101325.0
    if pressure_atm <= 0.0:
        return 0.0
    # cm²/s by formula, then convert to m²/s (1 cm² = 1e-4 m²)
    D_AB_cm2_s = (
        0.00266 * (T_K ** 1.5)
        / (pressure_atm * math.sqrt(M_ab_reduced)
           * (sigma_ab ** 2) * omega_d)
    )
    return D_AB_cm2_s * 1.0e-4


def _unsupported_carrier_gas_error(carrier_gas: Any) -> ValueError:
    return ValueError(
        f'Unsupported condensation carrier_gas {carrier_gas!r}; '
        f'supported carrier gases: {SUPPORTED_CARRIER_GAS_LABELS}'
    )


def _canonical_carrier_gas_key(carrier_gas: str | None) -> str:
    if carrier_gas is None:
        return DEFAULT_CARRIER_GAS
    text = str(carrier_gas).strip()
    if not text:
        raise ValueError(
            'condensation carrier_gas must be non-empty when provided'
        )
    upper = text.upper().replace(' ', '').replace('_', '').replace('-', '')
    if upper in {'N2', 'PN2', 'N2SWEEP', 'PN2SWEEP'}:
        return 'N2'
    if upper in {'AR', 'PAR'}:
        return 'Ar'
    if upper in {'O2', 'PO2', 'O2BACKPRESSURE', 'CONTROLLEDO2'}:
        return 'O2'
    if upper in {'CO2', 'PCO2', 'CO2BACKPRESSURE'}:
        return 'CO2'
    if upper.endswith('%CO2'):
        try:
            co2_percent = float(upper[:-4])
        except ValueError:
            co2_percent = 0.0
        if 0.0 < co2_percent <= 100.0:
            return 'CO2'
    raise _unsupported_carrier_gas_error(carrier_gas)


def _carrier_collision_diameter_diagnostic(carrier_gas: str) -> dict[str, Any]:
    requested_key = _canonical_carrier_gas_key(carrier_gas)
    requested = (
        DEFAULT_CARRIER_GAS
        if carrier_gas is None
        else str(carrier_gas).strip()
    )
    params = _LENNARD_JONES_PARAMS.get(requested_key)
    if params is not None:
        provenance = _LENNARD_JONES_PROVENANCE.get(requested_key, {})
        return {
            'requested_carrier_gas': requested,
            'applied_carrier_gas': requested_key,
            'carrier_gas_status': provenance.get('status', 'sourced'),
            'carrier_gas_reason': '',
            'carrier_collision_diameter_m': float(params[0]) * 1.0e-10,
            'carrier_collision_diameter_source': provenance.get('source', ''),
        }
    raise _unsupported_carrier_gas_error(carrier_gas)


def _carrier_collision_diameter_m(carrier_gas: str) -> float:
    params = _LENNARD_JONES_PARAMS.get(_canonical_carrier_gas_key(carrier_gas))
    if params is None:
        return N2_COLLISION_DIAMETER_M
    return float(params[0]) * 1.0e-10


def _transport_parameter_notice(
    species: str,
    carrier_gas: str,
) -> dict[str, Any]:
    rows: dict[str, dict[str, str]] = {}
    for name in (str(species), str(carrier_gas)):
        provenance = _LENNARD_JONES_PROVENANCE.get(name)
        if provenance is None:
            rows[name] = {
                'status': 'missing_proxy_fallback',
                'source': 'no Lennard-Jones row; diffusion falls back status-bearing',
            }
        else:
            rows[name] = dict(provenance)
    if all(row.get('status') == 'sourced' for row in rows.values()):
        return {}
    return {
        'severity': 'warning',
        'code': 'transport_lennard_jones_proxy_rows',
        'source_class': 'transport_proxy_not_authoritative',
        'carrier_gas': str(carrier_gas),
        'rows': rows,
        'output_status': 'status_bearing',
        'message': (
            'One or more Lennard-Jones transport rows are proxy estimates; '
            'wall deposition transport diagnostics are status-bearing until '
            'species-specific data replaces them.'
        ),
    }


class KnudsenRegime(Enum):
    VISCOUS = 'viscous'
    TRANSITIONAL = 'transitional'
    FREE_MOLECULAR = 'free_molecular'


class KnudsenRegimeRefusal(RuntimeError):
    """Raised when viscous-flow condensation assumptions are invalid."""

    reason = KNUDSEN_REFUSAL_REASON

    def __init__(self, diagnostic: Mapping[str, Any]):
        self.diagnostic = dict(diagnostic)
        self.reason = str(
            diagnostic.get('reason_refused')
            or diagnostic.get('reason')
            or KNUDSEN_REFUSAL_REASON
        )
        super().__init__(self.reason)


# Condensation temperatures at ~1 mbar partial pressure (°C).
# Used to determine where each species preferentially deposits in the
# routing logic (``_species_condensation_temperature_C``).
#
# Sources (historical cheap-win audit CW3, 2026-05-27 — these had been
# uncited bare numbers, misleading anyone retuning the routing surface):
#   - Fe / SiO / Mg / Na / K / Ca / Mn / Cr / Al / Ti default values
#     are the routing setpoints curated in ``data/setpoints.yaml §
#     condensation_temperature_sources`` (operator-tuned for the
#     pressure-vessel-internal pipe-geometry against published P_sat
#     vs T curves).
#   - SiO 1050 °C is the conservative gas-phase disproportionation
#     onset for SiO(g) → 0.5 SiO2(s) + 0.5 Si(s) at low pO₂ per
#     Schick (1960) / Nuth-Donn (1982) thermodynamic re-analysis; the
#     1 mbar partial pressure level is the Stage-3 condenser operating
#     point in `data/setpoints.yaml`.
#   - Future agents: prefer `data/setpoints.yaml` overrides when
#     adjusting these per recipe. This dict is the in-process default
#     when an individual species has no setpoints override.
CONDENSATION_TEMPS_C = {
    'Fe':  1250,
    # SiO: ENGINEERING MIDPOINT of the documented 900-1200 °C Stage 3
    # SiO zone (per ``data/setpoints.yaml § condensation_train.stages
    # [3].temp_range_C`` and ``data/vapor_pressures.yaml § SiO.
    # condensation_T_C: [900, 1200]``), NOT a literature-derived
    # T_cond. 0.5.4.1 B1 (CW3 historical-audit closure, 2026-05-28):
    # corpus scan (Cardiff 2007 / Matchett 2006 / Tsuchiyama 1998 /
    # Sesko 2022 / Schaefer-Fegley 2004) confirms NO paper independently
    # pins 1050 °C as the SiO cold-wall condensation temperature; the
    # 900-1200 °C zone is the engineering target for SiO → amorphous
    # SiO₂ disproportionation per the recipe playbook. Operators
    # retune via setpoints YAML; see worker scan at
    # ``docs-private/reviews/2026-05-28-b1-e2a-scan/codex-scan.txt``.
    'SiO': 1050,   # condenses as amorphous SiO₂ (disproportionation)
    'CrO2': 1250,  # condenses as Cr2O3 + O2 in the dedicated Cr stage
    'Mg':  580,
    'Na':  480,
    'K':   420,
    'Ca':  780,
    'Mn':  1000,
    'Cr':  1280,
    'Al':  1180,   # negligible at process T, but included for completeness
    'Ti':  1500,   # negligible at process T
}

_CONFIG_BUNDLE = load_config_bundle(DATA_DIR)
VAPOR_PRESSURE_DATA = _CONFIG_BUNDLE.vapor_pressures
MATERIALS_DATA = _CONFIG_BUNDLE.materials
_ANTOINE_COEFFICIENT_BLOCKS = ('antoine', 'pure_component_antoine')
_ANTOINE_REQUIRED_KEYS = frozenset(('A', 'B', 'C'))


@dataclass(frozen=True)
class CondensationRouteResult:
    """Per-hour routing plan; quantities are projections until ledger credit."""

    remaining_by_species: Dict[str, float] = field(default_factory=dict)
    condensed_by_stage_species: Dict[int, Dict[str, float]] = field(default_factory=dict)
    wall_deposit_by_species: Dict[str, float] = field(default_factory=dict)
    wall_deposit_by_segment_species: Dict[str, Dict[str, float]] = field(
        default_factory=dict)
    wall_deposit_fraction_by_species: Dict[str, float] = field(
        default_factory=dict)
    wall_deposit_account_fractions_by_species: Dict[
        str, Dict[str, float]] = field(default_factory=dict)
    wall_route_species_order: tuple[str, ...] = ()
    wall_reaction_products_by_segment_species_mol: Dict[
        str, Dict[str, Dict[str, float]]
    ] = field(default_factory=dict)
    wall_reaction_substrate_debits_by_segment_species_mol: Dict[
        str, Dict[str, Dict[str, float]]
    ] = field(default_factory=dict)
    wall_reaction_diagnostics_by_segment_species: Dict[
        str, Dict[str, Dict[str, Any]]
    ] = field(default_factory=dict)
    wall_alkali_binding_diagnostic_state_by_segment: Dict[
        str, Dict[str, Any]
    ] = field(default_factory=dict)
    impurity_by_stage_species: Dict[int, Dict[str, float]] = field(default_factory=dict)
    antoine_extrapolations: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    antoine_extrapolation_warnings: tuple[str, ...] = ()
    cold_spot_warnings: tuple[str, ...] = ()
    knudsen_regime_diagnostic: Dict[str, Any] = field(default_factory=dict)
    sticking_alpha_provenance_notice: Dict[str, Any] = field(
        default_factory=dict)
    transport_parameter_notice: Dict[str, Any] = field(default_factory=dict)
    capture_budget_regularizer_notice: Dict[str, Any] = field(default_factory=dict)
    stage_area_geometry_provenance_notice: Dict[str, Any] = field(
        default_factory=dict)

    def condensed_for_species(self, species: str) -> float:
        return sum(
            stage_species.get(species, 0.0)
            for stage_species in self.condensed_by_stage_species.values()
        )

    def silica_fume_fraction_of_feedstock(self, feedstock_kg: float) -> float:
        """Return SiO-derived SiO2 condensate mass divided by feedstock mass."""

        if feedstock_kg <= 0.0:
            return 0.0
        sio_condensed_kg = self.condensed_for_species('SiO')
        sio_to_sio2 = 0.5 * MOLAR_MASS['SiO2'] / MOLAR_MASS['SiO']
        return (sio_condensed_kg * sio_to_sio2) / feedstock_kg


class CondensationModel:
    """
    Routes evaporated species through the condensation train.

    For each species in the evaporation flux, calculates the
    fraction that condenses in each stage based on the stage
    temperature relative to the species' condensation temperature.
    """

    def __init__(
        self,
        train: CondensationTrain,
        vapor_pressure_data: MutableMapping[str, Any] | None = None,
        wall_surface_area_m2: float | None = None,
        wall_temperature_C: float = DEFAULT_PIPE_TEMPERATURE_C,
        materials: Mapping[str, Any] | None = None,
    ):
        self.train = train
        self.vapor_pressure_data = copy.deepcopy(vapor_pressure_data)
        self.materials = copy.deepcopy(
            materials if materials is not None else MATERIALS_DATA
        )
        # 0.5.4.1 review-cluster-C (P2 #1, evening-4commits review):
        # per-instance per-species condensation temperatures so each
        # CondensationModel can carry its own setpoints overrides
        # without cross-contaminating the module-level
        # ``CONDENSATION_TEMPS_C`` dict. Multi-tenant servers
        # (``web/events.py`` per-SID, ``runner.py`` per-run
        # setpoints_path) need this isolation. Initialised from the
        # module-level fallback; ``apply_setpoints_overrides`` later
        # merges YAML values into this instance dict only — the
        # module dict is no longer the canonical production-path
        # source-of-truth (it remains the fallback for callers
        # that don't pass an instance).
        self.condensation_temperatures_C: dict[str, float] = dict(
            CONDENSATION_TEMPS_C
        )
        self.wall_surface_area_m2 = (
            float(wall_surface_area_m2)
            if wall_surface_area_m2 is not None
            else _default_pipe_surface_area_m2()
        )
        self.wall_temperature_C = float(wall_temperature_C)
        self.overhead_pressure_mbar = 0.0
        self.pipe_diameter_m = DEFAULT_PIPE_DIAMETER_M
        self.stage_area_m2_by_stage: dict[str, float] = {}
        self.stage_area_geometry_provenance_notice: dict[str, Any] = {}
        self.gas_temperature_C = float(wall_temperature_C)
        self.carrier_gas = DEFAULT_CARRIER_GAS
        # Induction-stirring intensity — recipe-controlled per
        # ``setpoints.yaml § induction_stirring``. Constructor default
        # is ``1.0`` (no-stir laminar baseline, ``Sh = 3.66``), NOT the
        # ``MeltState.stir_factor = 6.0`` default. The two are
        # intentionally different: ``MeltState`` is the operator-facing
        # field carrying the C2A recipe value, ``CondensationModel`` is
        # a transport object whose flux helpers reduce to honest
        # no-stir physics until ``configure_operating_conditions(
        # stir_factor=...)`` pushes the recipe value through. Direct-
        # construction callers (typically unit tests) MUST call
        # ``configure_operating_conditions`` before ``route()`` for the
        # series-resistance branch to engage; otherwise ``regime_factor
        # = 1.0`` (free-molecular default) collapses the form back to
        # pure HKL. gstack /review subagent flagged this Phase B P3 as
        # a footgun for future direct-construction tests; documenting
        # rather than coupling here avoids importing state.py for what
        # is properly an operator-boundary concern.
        self.stir_factor = 1.0
        # 0.5.3 Phase B (2-axis stirring): the radial axis is the
        # canonical Sh driver. Constructor default ``None`` is the
        # explicit "not configured" sentinel — the deposition helper
        # (``_stirring_enhanced_sherwood``) falls back to the legacy
        # ``stir_factor`` when ``radial_stir_factor is None``, which
        # preserves pre-Phase-B Sh-enhancement semantics for legacy
        # callers that only pass ``stir_factor`` to
        # ``configure_operating_conditions``.
        #
        # Phase B chunk-review P1 (codex 2026-05-28): the previous
        # default of ``1.0`` made the radial axis ALWAYS take precedence
        # in the deposition call (per helper precedence: explicit
        # radial wins over stir_factor) so a legacy
        # ``configure_operating_conditions(stir_factor=6)`` call left
        # Sh stuck at the laminar 3.66 baseline — silently broke the
        # documented 0.5.2 backward-compat. The ``None`` sentinel lets
        # the helper's fallback fire.
        self.radial_stir_factor: float | None = None
        self.knudsen_number = math.inf
        self.regime_factor = 1.0
        self.knudsen_regime = KnudsenRegime.FREE_MOLECULAR
        self._knudsen_policy_configured = False
        self._viscous_flow_required = True
        self.pipe_segments = self._build_default_pipe_segments(
            float(wall_temperature_C))
        self.cold_spot_margin_C = COLD_SPOT_MARGIN_C
        self.upstream_hot_wall_min_C = DEFAULT_UPSTREAM_HOT_WALL_MIN_C
        self.last_cold_spot_diagnostic: dict[str, Any] = {
            'has_cold_spot': False,
            'warnings': [],
            'findings': [],
            'has_upstream_hot_wall_violation': False,
            'upstream_hot_wall_warnings': [],
            'upstream_hot_wall_findings': [],
            'upstream_hot_wall_min_C': DEFAULT_UPSTREAM_HOT_WALL_MIN_C,
        }
        self.last_knudsen_regime_diagnostic: dict[str, Any] = {}
        self.last_knudsen_pressure_adjustment: dict[str, Any] = {}
        self.last_sticking_alpha_provenance_notice: dict[str, Any] = {}
        self.last_transport_parameter_notice: dict[str, Any] = {}
        self.last_capture_budget_regularizer_notice: dict[str, Any] = {}
        self.wall_alkali_binding_diagnostic_state_by_account: dict[
            str, dict[str, Any]
        ] = {}
        self.cold_spot_history: list[dict[str, Any]] = []
        self.operating_history: list[dict[str, Any]] = []

        # Default residence time per stage (seconds)
        # In a real design, this comes from equipment sizing
        self.residence_time_s = {
            0: 0.5,    # Hot duct — fast transit
            1: 5.0,    # Fe condenser — baffles slow the flow
            2: 240.0,  # Cr oxide harvester — dedicated hot cartridge
            3: 4.0,    # SiO zone — removable baffles
            4: 3.0,    # Cyclone — vortex residence
            5: 2.0,    # Dust filter
            6: 0.2,    # Turbine — very fast
            7: 0.0,    # Accumulator — no condensation
        }

    @property
    def wall_deposit_accounts(self) -> tuple[str, ...]:
        return tuple(segment.wall_deposit_account for segment in self.pipe_segments)

    def configure_operating_conditions(
        self,
        *,
        wall_temperature_C: float | None = None,
        overhead_pressure_mbar: float | None = None,
        pipe_diameter_m: float | None = None,
        gas_temperature_C: float | None = None,
        stage_area_m2_by_stage: Mapping[str, float] | None = None,
        stage_area_geometry_provenance_notice: Mapping[str, Any] | None = None,
        pipe_segment_temperatures_C: Mapping[str, float] | None = None,
        stir_factor: float | None = None,
        radial_stir_factor: float | None = None,
        carrier_gas: str | None = None,
        campaign_name: str | None = None,
        campaign_hour: float | None = None,
    ) -> None:
        """Update tick-local wall and Knudsen conditions for cached models.

        ``stir_factor`` (when provided): legacy 0.5.2 single-axis input.
        Kept for backward-compat with direct callers and pre-Phase-B
        test fixtures. Pre-0.5.3 this WAS the Sh driver; post-0.5.3 it
        is preserved as an audit-history record (``operating_history``
        snapshots it alongside ``radial_stir_factor``).

        ``radial_stir_factor`` (when provided, 0.5.3 Phase B): canonical
        2-axis input. Drives the series-resistance flux's Sherwood
        enhancement (gas-side in-plane vortex mixing → reduced bulk-
        to-wall transport resistance). Defaults to ``1.0`` (no-stir
        laminar baseline, ``Sh = 3.66``) when unset; recipes wire
        ``melt.stir_state.radial`` through
        ``core._configure_condensation_operating_conditions``. The
        AXIAL axis lives on ``melt.stir_state.axial`` and drives a
        DIFFERENT consumer (the H-K-L linear multiplier in
        ``engines/builtin/evaporation_flux.py``); it is not consumed
        here.
        """

        # Validate the complete candidate first. A late invalid pressure,
        # campaign, area, or segment must not leave earlier fields mutated.
        for label, raw_temperature in (
            ('wall_temperature_C', wall_temperature_C),
            ('gas_temperature_C', gas_temperature_C),
        ):
            if raw_temperature is None:
                continue
            value = float(raw_temperature)
            if not math.isfinite(value) or value <= -273.15:
                raise ValueError(
                    f'{label} must be finite and above absolute zero'
                )
        if pipe_diameter_m is not None:
            require_lab_pipe_diameter(pipe_diameter_m, 'pipe_diameter_m')
        if stage_area_m2_by_stage is not None:
            for stage, raw_area in stage_area_m2_by_stage.items():
                if isinstance(raw_area, bool):
                    raise ValueError(f'stage area {stage!r} must be finite and non-negative')
                area = float(raw_area)
                if not math.isfinite(area) or area < 0.0:
                    raise ValueError(f'stage area {stage!r} must be finite and non-negative')
        if overhead_pressure_mbar is not None:
            pressure = float(overhead_pressure_mbar)
            if not math.isfinite(pressure) or pressure < 0.0:
                raise ValueError(
                    'overhead_pressure_mbar must be finite and non-negative'
                )
            _campaign_requires_viscous_flow(campaign_name)
        if carrier_gas is not None:
            _canonical_carrier_gas_key(carrier_gas)
        if campaign_hour is not None:
            if isinstance(campaign_hour, bool):
                raise ValueError('campaign_hour must be finite and non-negative')
            try:
                hour = float(campaign_hour)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    'campaign_hour must be finite and non-negative'
                ) from exc
            if not math.isfinite(hour) or hour < 0.0:
                raise ValueError('campaign_hour must be finite and non-negative')
        if pipe_segment_temperatures_C is not None:
            for segment in self.pipe_segments:
                raw_temperature = float(pipe_segment_temperatures_C.get(
                    segment.name, self.wall_temperature_C
                ))
                if (
                    not math.isfinite(raw_temperature)
                    or raw_temperature <= -273.15
                ):
                    raise ValueError(
                        f'{segment.name} wall temperature must be finite '
                        'and above absolute zero'
                    )

        if wall_temperature_C is not None:
            wall_temperature = float(wall_temperature_C)
            if not math.isfinite(wall_temperature) or wall_temperature <= -273.15:
                raise ValueError('wall_temperature_C must be finite and above absolute zero')
            self.wall_temperature_C = wall_temperature
        if pipe_diameter_m is not None:
            self.pipe_diameter_m = require_lab_pipe_diameter(
                pipe_diameter_m, 'pipe_diameter_m')
            self.pipe_segments = [
                replace(segment, inner_diameter_m=self.pipe_diameter_m)
                for segment in self.pipe_segments
            ]
        if gas_temperature_C is not None:
            gas_temperature = float(gas_temperature_C)
            if not math.isfinite(gas_temperature) or gas_temperature <= -273.15:
                raise ValueError('gas_temperature_C must be finite and above absolute zero')
            self.gas_temperature_C = gas_temperature
        elif wall_temperature_C is not None:
            self.gas_temperature_C = float(wall_temperature_C)
        if stage_area_m2_by_stage is not None:
            self.stage_area_m2_by_stage = {
                str(stage): float(area_m2)
                for stage, area_m2 in stage_area_m2_by_stage.items()
            }
            self._apply_stage_area_m2_to_pipe_segments()
        if stage_area_geometry_provenance_notice is not None:
            self.stage_area_geometry_provenance_notice = dict(
                stage_area_geometry_provenance_notice)
        if carrier_gas is not None:
            self.carrier_gas = _canonical_carrier_gas_key(carrier_gas)
        # Track requested vs applied stir for the operating-history audit.
        # Codex + gstack reviewers (Phase B P3): the canonical clamp at
        # ``clamp_stir_factor`` is silent — a downstream auditor reading
        # the history can't otherwise tell "operator chose 10.0" from
        # "operator chose 100, got clamped". Record both.
        _stir_factor_requested: float | None = None
        _stir_factor_clamped: bool = False
        if stir_factor is not None:
            # Capture the as-requested numeric value if it's coercible.
            # Non-finite (NaN/+/-inf), bool, and non-numeric inputs all
            # land at ``_stir_factor_requested = None`` so the snapshot
            # explicitly says "no numeric request" rather than lying
            # about it.
            if not isinstance(stir_factor, bool):
                try:
                    _coerced = float(stir_factor)
                except (TypeError, ValueError):
                    _coerced = None
                if _coerced is not None and math.isfinite(_coerced):
                    _stir_factor_requested = _coerced
            # Canonical clamp from ``simulator/state.py``. The operator
            # override paths in ``simulator/campaigns.py`` and
            # ``simulator/session.py`` use the SAME helper, so the value
            # carried on ``melt.stir_factor`` and the value reflected
            # here are consistent.
            self.stir_factor = clamp_stir_factor(stir_factor)
            # ``_stir_factor_clamped`` MUST be True whenever
            # ``clamp_stir_factor`` modified the input — including
            # non-finite/bool/non-numeric cases where the sanitisation
            # IS the clamp event. Pre-0.5.2 code-review max-effort
            # caught the earlier short-circuit that hid these from
            # auditors.
            if _stir_factor_requested is None:
                # Non-finite / bool / non-numeric / None all hit the
                # defensive 0.0 path; report as clamped.
                _stir_factor_clamped = True
            else:
                _stir_factor_clamped = not math.isclose(
                    _stir_factor_requested,
                    self.stir_factor,
                    rel_tol=1e-12,
                    abs_tol=0.0,
                )
        # 0.5.3 Phase B: radial axis capture + clamp + audit. Mirrors
        # the stir_factor block above, since the same defensive
        # contract applies (operator boundary clamp, non-finite/bool
        # fail-closed, requested-vs-applied audit trail). Kept as a
        # parallel block (not refactored into a shared helper) because
        # the operating_history snapshot keys are different
        # (radial_stir_factor / radial_stir_factor_clamped /
        # radial_stir_factor_requested) and inlining keeps the audit
        # surface explicit for a downstream auditor scanning the file.
        _radial_stir_factor_requested: float | None = None
        _radial_stir_factor_clamped: bool = False
        if radial_stir_factor is not None:
            if not isinstance(radial_stir_factor, bool):
                try:
                    _coerced = float(radial_stir_factor)
                except (TypeError, ValueError):
                    _coerced = None
                if _coerced is not None and math.isfinite(_coerced):
                    _radial_stir_factor_requested = _coerced
            self.radial_stir_factor = clamp_stir_factor(radial_stir_factor)
            if _radial_stir_factor_requested is None:
                _radial_stir_factor_clamped = True
            else:
                _radial_stir_factor_clamped = not math.isclose(
                    _radial_stir_factor_requested,
                    self.radial_stir_factor,
                    rel_tol=1e-12,
                    abs_tol=0.0,
                )
        if overhead_pressure_mbar is not None:
            pressure_mbar = float(overhead_pressure_mbar)
            if not math.isfinite(pressure_mbar) or pressure_mbar < 0.0:
                raise ValueError('overhead_pressure_mbar must be finite and non-negative')
            self.overhead_pressure_mbar = pressure_mbar
            self._knudsen_policy_configured = True
            self._viscous_flow_required = _campaign_requires_viscous_flow(
                campaign_name)
        pressure_pa = self.overhead_pressure_mbar * 100.0
        gas_temperature_K = max(
            self.gas_temperature_C + CELSIUS_TO_KELVIN_OFFSET,
            1.0,
        )
        self.knudsen_number = _knudsen_number(
            pressure_pa,
            gas_temperature_K,
            self.pipe_diameter_m,
            carrier_gas=self.carrier_gas,
        )
        self.regime_factor = _knudsen_regime_factor(self.knudsen_number)
        self.knudsen_regime = classify_knudsen_regime(self.knudsen_number)
        if pipe_segment_temperatures_C is not None:
            self._apply_pipe_segment_temperatures(pipe_segment_temperatures_C)
        elif wall_temperature_C is not None:
            self._apply_pipe_segment_temperatures({
                segment.name: float(wall_temperature_C)
                for segment in self.pipe_segments
            })
        self.last_knudsen_regime_diagnostic = self._current_knudsen_diagnostic()
        # gstack reviewer Phase B P2: previously this snapshot was gated
        # ONLY on ``overhead_pressure_mbar is not None``, so a caller that
        # tweaked wall temperatures or stir_factor without supplying a
        # pressure (e.g. ``runner._apply_sio_wall_sweep_controls``) left
        # zero audit trail. Broadened to fire when any operating-condition
        # input changed this call. Mass-balance closure stays honest by
        # the same path.
        _snapshot_inputs_changed = any(
            x is not None
            for x in (
                overhead_pressure_mbar,
                stir_factor,
                radial_stir_factor,
                wall_temperature_C,
                pipe_diameter_m,
                gas_temperature_C,
                stage_area_m2_by_stage,
                stage_area_geometry_provenance_notice,
                pipe_segment_temperatures_C,
                carrier_gas,
            )
        )
        if _snapshot_inputs_changed:
            snapshot: dict[str, Any] = {
                "campaign": str(campaign_name or ""),
                "campaign_hour": (
                    0.0 if campaign_hour is None
                    else float(campaign_hour)
                ),
                "wall_temperature_C": float(self.wall_temperature_C),
                "pipe_segment_temperatures_C": {
                    segment.name: float(segment.wall_temperature_C)
                    for segment in self.pipe_segments
                },
                "stage_area_m2_by_stage": dict(self.stage_area_m2_by_stage),
                "stage_area_geometry_provenance_notice": dict(
                    self.stage_area_geometry_provenance_notice),
                "overhead_pressure_mbar": float(self.overhead_pressure_mbar),
                "stir_factor": float(self.stir_factor),
                "stir_factor_clamped": bool(_stir_factor_clamped),
                # 0.5.3 Phase B: radial axis carried in the snapshot
                # alongside the legacy ``stir_factor`` field so a
                # downstream auditor can read both axes' applied values.
                # ``radial_stir_factor_requested`` is added below ONLY
                # when the caller explicitly supplied it, mirroring the
                # ``stir_factor_requested`` convention. The applied
                # ``radial_stir_factor`` is ``None`` (not 0.0) when the
                # caller never configured radial — distinguishes "no-
                # configure, falls back to legacy stir_factor for Sh"
                # from "explicit radial=0 halt signal" in the audit
                # trail. Phase B chunk-review P1 fix (codex 2026-05-28).
                "radial_stir_factor": (
                    float(self.radial_stir_factor)
                    if self.radial_stir_factor is not None
                    else None
                ),
                "radial_stir_factor_clamped": bool(
                    _radial_stir_factor_clamped),
                "knudsen_number": float(self.knudsen_number),
                "knudsen_regime": self.knudsen_regime.value,
                "regime_factor": float(self.regime_factor),
                "carrier_gas": self.carrier_gas,
                "knudsen_warnings": tuple(
                    self.last_knudsen_regime_diagnostic.get(
                        "warnings", ())),
                "knudsen_regime_diagnostic": dict(
                    self.last_knudsen_regime_diagnostic),
            }
            # Record the as-requested stir_factor only when it was passed
            # this call; otherwise the field is intentionally omitted so
            # downstream auditors can distinguish "no override this tick"
            # from "override that survived the clamp" from "override that
            # got clamped down".
            if _stir_factor_requested is not None:
                snapshot["stir_factor_requested"] = (
                    float(_stir_factor_requested)
                    if math.isfinite(_stir_factor_requested)
                    else None
                )
            # 0.5.3 Phase B: mirror the requested-record convention on
            # the radial axis. Omission semantics: no record → operator
            # did not touch the radial axis this tick (which is the
            # majority case for pre-Phase-B campaign overrides that
            # only carry the legacy scalar ``stir_factor``).
            if _radial_stir_factor_requested is not None:
                snapshot["radial_stir_factor_requested"] = (
                    float(_radial_stir_factor_requested)
                    if math.isfinite(_radial_stir_factor_requested)
                    else None
                )
            self.operating_history.append(snapshot)

    def apply_setpoints_overrides(
        self, setpoints: Mapping[str, Any] | None,
    ) -> None:
        """0.5.4.1 review-cluster-C (P2 #1): instance-isolated
        version of the module-level ``apply_setpoints_condensation_
        temperature_overrides``. Reads per-species condensation
        temperatures from
        ``setpoints['condensation_train']['condensation_temperatures_C']``
        and merges into ``self.condensation_temperatures_C`` —
        WITHOUT touching the process-global
        ``CONDENSATION_TEMPS_C`` fallback dict.

        This is the canonical production seam: each
        ``CondensationModel`` instance carries its own setpoints
        overrides, so multi-tenant servers (``web/events.py`` per-
        SID, ``runner.py`` per-run setpoints_path) can build sims
        with different setpoints in the same Python interpreter
        without cross-contamination.

        Same parse contract as the legacy module-level helper:
        non-finite / non-coercible entries skipped; idempotent;
        partial overrides keep other species at the fallback.
        """
        if not setpoints:
            return
        self.upstream_hot_wall_min_C = _stage0_hot_wall_min_C_from_setpoints(
            setpoints,
            default_C=self.upstream_hot_wall_min_C,
        )
        block = (
            (setpoints.get('condensation_train', {}) or {})
            .get('condensation_temperatures_C', {}) or {}
        )
        if not isinstance(block, Mapping):
            return
        for species, value in block.items():
            try:
                T_C = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(T_C):
                continue
            self.condensation_temperatures_C[str(species)] = T_C

    def _build_default_pipe_segments(
        self,
        wall_temperature_C: float,
    ) -> list[PipeSegment]:
        stages = sorted(self.train.stages, key=lambda stage: stage.stage_number)
        if len(stages) < 2:
            return []
        diameter_m = require_lab_pipe_diameter(
            self.pipe_diameter_m, 'pipe_diameter_m')
        total_length_m = (
            max(0.0, float(self.wall_surface_area_m2))
            / (math.pi * diameter_m)
        )
        length_m = total_length_m / float(len(stages) - 1)
        segments: list[PipeSegment] = []
        for upstream, downstream in zip(stages, stages[1:]):
            downstream_material = _stage_material_config(downstream, self.materials)
            segments.append(PipeSegment(
                name=(
                    f'stage_{upstream.stage_number}'
                    f'_to_stage_{downstream.stage_number}'
                ),
                upstream_stage=f'stage_{upstream.stage_number}',
                downstream_stage=f'stage_{downstream.stage_number}',
                wall_temperature_C=float(wall_temperature_C),
                length_m=length_m,
                inner_diameter_m=diameter_m,
                declared_area_m2=self._stage_area_m2_for_stage_number(
                    downstream.stage_number),
                liner_material=str(
                    downstream_material.get('liner_material') or ''
                ),
            ))
        return segments

    def _stage_area_m2_for_stage_number(self, stage_number: int) -> float | None:
        key = STAGE_AREA_KEY_BY_STAGE_NUMBER.get(int(stage_number))
        raw = None if key is None else self.stage_area_m2_by_stage.get(key)
        if raw is None:
            raw = self.stage_area_m2_by_stage.get(f'stage_{stage_number}')
        if raw is None:
            raw = self.stage_area_m2_by_stage.get(str(stage_number))
        if raw is None:
            return None
        try:
            area_m2 = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(area_m2) or area_m2 < 0.0:
            return None
        return area_m2

    def _apply_stage_area_m2_to_pipe_segments(self) -> None:
        if not self.pipe_segments:
            return
        updated: list[PipeSegment] = []
        for segment in self.pipe_segments:
            downstream_stage = str(segment.downstream_stage or '')
            stage_number = None
            if downstream_stage.startswith('stage_'):
                try:
                    stage_number = int(downstream_stage.removeprefix('stage_'))
                except ValueError:
                    stage_number = None
            declared_area_m2 = (
                self._stage_area_m2_for_stage_number(stage_number)
                if stage_number is not None
                else None
            )
            updated.append(PipeSegment(
                name=segment.name,
                upstream_stage=segment.upstream_stage,
                downstream_stage=segment.downstream_stage,
                wall_temperature_C=segment.wall_temperature_C,
                length_m=segment.length_m,
                inner_diameter_m=segment.inner_diameter_m,
                role=segment.role,
                declared_area_m2=(
                    declared_area_m2
                    if declared_area_m2 is not None
                    else segment.declared_area_m2
                ),
                view_factor_from_melt=segment.view_factor_from_melt,
                line_of_sight_to_melt=segment.line_of_sight_to_melt,
                source_class=segment.source_class,
                sensitivity_marker=segment.sensitivity_marker,
                extraction_note=segment.extraction_note,
                liner_material=segment.liner_material,
            ))
        self.pipe_segments = updated

    def _apply_pipe_segment_temperatures(
        self,
        temperatures_C: Mapping[str, float],
    ) -> None:
        if not self.pipe_segments:
            self.pipe_segments = self._build_default_pipe_segments(
                self.wall_temperature_C)
        updated: list[PipeSegment] = []
        for segment in self.pipe_segments:
            raw_temperature = float(temperatures_C.get(
                segment.name, self.wall_temperature_C)
            )
            if not math.isfinite(raw_temperature) or raw_temperature <= -273.15:
                raise ValueError(
                    f'{segment.name} wall temperature must be finite and above absolute zero'
                )
            updated.append(PipeSegment(
                name=segment.name,
                upstream_stage=segment.upstream_stage,
                downstream_stage=segment.downstream_stage,
                wall_temperature_C=raw_temperature,
                length_m=segment.length_m,
                inner_diameter_m=segment.inner_diameter_m,
                role=segment.role,
                declared_area_m2=segment.declared_area_m2,
                view_factor_from_melt=segment.view_factor_from_melt,
                line_of_sight_to_melt=segment.line_of_sight_to_melt,
                source_class=segment.source_class,
                sensitivity_marker=segment.sensitivity_marker,
                extraction_note=segment.extraction_note,
                liner_material=segment.liner_material,
            ))
        self.pipe_segments = updated

    def update_pipe_segment_temperatures(
        self,
        temperatures_C: Mapping[str, float],
    ) -> None:
        self._apply_pipe_segment_temperatures(temperatures_C)
        if not self.pipe_segments:
            return
        self.wall_temperature_C = min(
            segment.wall_temperature_C for segment in self.pipe_segments
        )
        if self.operating_history:
            self.operating_history[-1]["wall_temperature_C"] = float(
                self.wall_temperature_C
            )
            self.operating_history[-1]["pipe_segment_temperatures_C"] = {
                segment.name: float(segment.wall_temperature_C)
                for segment in self.pipe_segments
            }

    def configure_lab_geometry(
        self,
        lab_geometry: LabGeometry | Mapping[str, Any],
    ) -> LabGeometry:
        geometry = (
            lab_geometry
            if isinstance(lab_geometry, LabGeometry)
            else parse_lab_geometry(lab_geometry)
        )
        if geometry is None:
            raise ValueError("lab_geometry is required")
        self.pipe_segments = geometry.to_pipe_segments(
            default_diameter_m=require_lab_pipe_diameter(
                self.pipe_diameter_m, 'pipe_diameter_m'),
        )
        self.wall_surface_area_m2 = geometry.total_surface_area_m2
        if self.pipe_segments:
            self.pipe_diameter_m = min(
                segment.inner_diameter_m for segment in self.pipe_segments
            )
            self.wall_temperature_C = min(
                segment.wall_temperature_C for segment in self.pipe_segments
            )
            self.gas_temperature_C = self.wall_temperature_C
        self.lab_geometry = geometry
        return geometry

    def route(self, evap_flux: EvaporationFlux, melt: MeltState):
        """
        Route all evaporated species through the train.

        For each species, walk through stages 0→6.  At each stage,
        calculate condensation fraction η.  Whatever condenses is
        added to that stage's collected_kg; the remainder passes
        to the next stage.

        O2 terminal storage is handled by the simulator atom ledger.  Stage
        collection dictionaries are UI projections and are updated only after
        the simulator commits the matching ledger transition.
        """
        remaining_by_species = {}
        condensed_by_stage_species: Dict[int, Dict[str, float]] = {}
        wall_deposit_by_species: Dict[str, float] = {}
        wall_deposit_by_segment_species: Dict[str, Dict[str, float]] = {}
        wall_deposit_fraction_by_species: Dict[str, float] = {}
        wall_deposit_account_fractions_by_species: Dict[
            str, Dict[str, float]] = {}
        impurity_by_stage_species: Dict[int, Dict[str, float]] = {}
        antoine_extrapolations: Dict[str, Dict[str, Any]] = {}
        antoine_extrapolation_warnings: list[str] = []
        wall_sticking_alpha_by_species: dict[str, float] = {}
        wall_sticking_alpha_provenance_by_species: dict[str, Any] = {}
        transport_parameter_notice_by_species: dict[str, Any] = {}
        used_capture_budget_regularizer = False
        knudsen_diagnostic = self._enforce_knudsen_regime()
        diagnostic = cold_spot_diagnostic(
            self.pipe_segments,
            evap_flux.species_kg_hr,
            margin_C=self.cold_spot_margin_C,
            upstream_hot_wall_min_C=self.upstream_hot_wall_min_C,
            temps=self.condensation_temperatures_C,
            vapor_pressure_data=self.vapor_pressure_data,
        )
        self.last_cold_spot_diagnostic = diagnostic
        self.cold_spot_history.append(diagnostic)
        cold_spot_warnings = tuple(diagnostic.get('warnings', ()))
        if self.operating_history:
            self.operating_history[-1]['cold_spot_warning_count'] = len(
                cold_spot_warnings)
            self.operating_history[-1]['cold_spot_warnings'] = cold_spot_warnings

        for species, rate_kg_hr in evap_flux.species_kg_hr.items():
            remaining_kg = rate_kg_hr  # Mass still in vapor phase
            wall_deposit_fraction_by_species[species] = 0.0
            wall_deposit_account_fractions_by_species[species] = {}

            T_cond = _species_condensation_temperature_C(
                species,
                temps=self.condensation_temperatures_C,
                vapor_pressure_data=self.vapor_pressure_data,
            )
            hkl_condensed_by_stage: Dict[int, float] = {}
            remaining_after_stage: Dict[int, float] = {}
            stage_alpha_records_by_stage: Dict[int, dict[str, Any]] = {}

            for stage in self.train.stages:
                if remaining_kg <= 1e-15:
                    break
                if _cr_stage_isolation_blocks(stage, species):
                    continue
                stage_alpha_record = _stage_alpha_record(
                    stage,
                    species,
                    self.materials,
                )
                # Every EVALUATED stage's alpha influences the final wall
                # deposit: it sets how much vapor this stage removes versus
                # passes downstream to the wall sink (the wall_hkl / hkl_sink
                # split), so its provenance must enter deposit authority even
                # when this stage itself condenses ~0 -- otherwise an uncertified
                # low/zero stage alpha drives a positive wall deposit while it
                # stays authoritative (an F0 fail-open; BUG-096 sibling caught by
                # the codex repro 2026-06-27). Only stages reached before full
                # capture (remaining_kg>1e-15) are evaluated, so this never
                # demotes on a non-influencing stage.
                stage_alpha_records_by_stage[stage.stage_number] = dict(
                    stage_alpha_record)
                # Calculate band-aware H-K-L deposition efficiency [COND-2]
                eta = self._condensation_efficiency(
                    stage=stage,
                    species=species,
                    T_cond_C=T_cond,
                    residence_s=self.residence_time_s.get(
                        stage.stage_number, 1.0),
                    available_kg=remaining_kg,
                    alpha_s_value=float(stage_alpha_record.get('alpha_s', 0.0)),
                    alpha_record=stage_alpha_record,
                    antoine_extrapolations=antoine_extrapolations,
                    antoine_extrapolation_warnings=(
                        antoine_extrapolation_warnings),
                )

                condensed_kg = remaining_kg * eta
                if condensed_kg > 1e-15:
                    hkl_condensed_by_stage[stage.stage_number] = (
                        hkl_condensed_by_stage.get(stage.stage_number, 0.0)
                        + condensed_kg)

                remaining_kg -= condensed_kg
                remaining_after_stage[stage.stage_number] = max(
                    0.0, remaining_kg)

            hkl_condensed_total_kg = sum(hkl_condensed_by_stage.values())
            segment_supply = self._segment_supply_by_name(
                rate_kg_hr,
                remaining_after_stage,
            )
            wall_hkl_by_segment = self._wall_deposit_candidates_by_segment_kg(
                species=species,
                rate_kg_hr=rate_kg_hr,
                T_cond_C=T_cond,
                melt_temperature_C=float(getattr(melt, 'temperature_C', T_cond)),
                supply_by_segment_kg=segment_supply,
                antoine_extrapolations=antoine_extrapolations,
                antoine_extrapolation_warnings=(
                    antoine_extrapolation_warnings),
            )
            candidate_segments = self._mixed_temperature_wall_candidate_segments(
                species)
            if candidate_segments:
                alpha_records = [
                    _wall_alpha_record(
                        species,
                        self.materials,
                        segment=segment,
                    )
                    for segment in candidate_segments
                ]
                wall_sticking_alpha_by_species[species] = max(
                    float(record.get('alpha_s', 0.0))
                    for record in alpha_records
                )
                wall_sticking_alpha_provenance_by_species[species] = {
                    str(record.get('segment', '')): dict(record)
                    for record in alpha_records
                    if record.get('segment')
                }
                transport_notice = _transport_parameter_notice(
                    species,
                    self.carrier_gas,
                )
                if transport_notice:
                    transport_parameter_notice_by_species[species] = (
                        transport_notice)
            wall_hkl_kg = sum(wall_hkl_by_segment.values())
            hkl_sink_total_kg = hkl_condensed_total_kg + wall_hkl_kg
            capture_budget_alpha_record: dict[str, Any] = {}
            capture_budget_kg = _pressure_isolated_capture_budget_kg(
                species,
                rate_kg_hr,
                self.train.stages,
                self.residence_time_s,
                temps=self.condensation_temperatures_C,
                vapor_pressure_data=self.vapor_pressure_data,
                alpha_record_out=capture_budget_alpha_record,
            )
            if hkl_sink_total_kg <= 1e-15:
                capture_budget_kg = 0.0
            elif capture_budget_kg > 0.0:
                used_capture_budget_regularizer = True
                wall_deposit_kg = capture_budget_kg * (
                    wall_hkl_kg / hkl_sink_total_kg
                )
                if wall_deposit_kg > 1e-15:
                    wall_deposit_by_species[species] = (
                        wall_deposit_by_species.get(species, 0.0)
                        + wall_deposit_kg
                    )
                    segment_deposits = _allocate_total_by_weights(
                        wall_deposit_kg,
                        wall_hkl_by_segment,
                    )
                    for segment_name, segment_kg in segment_deposits.items():
                        if segment_kg <= 1e-15:
                            continue
                        segment_species = (
                            wall_deposit_by_segment_species.setdefault(
                                segment_name, {}))
                        segment_species[species] = (
                            segment_species.get(species, 0.0) + segment_kg)
                wall_fraction = (
                    wall_deposit_kg / capture_budget_kg
                    if capture_budget_kg > 0.0 else 0.0
                )
                segment_fractions = _wall_segment_account_fractions(
                    wall_deposit_by_segment_species,
                    species,
                    wall_deposit_kg,
                    self.pipe_segments,
                )
                wall_deposit_fraction_by_species[species] = wall_fraction
                wall_deposit_account_fractions_by_species[species] = dict(
                    segment_fractions)

                baffle_budget_kg = max(0.0, capture_budget_kg - wall_deposit_kg)
                if hkl_condensed_total_kg > 1e-15 and baffle_budget_kg > 0.0:
                    scale = baffle_budget_kg / hkl_condensed_total_kg
                    for stage_number, hkl_stage_kg in hkl_condensed_by_stage.items():
                        condensed_kg = hkl_stage_kg * scale
                        if condensed_kg <= 1e-15:
                            continue
                        stage_species = condensed_by_stage_species.setdefault(
                            stage_number, {})
                        stage_species[species] = (
                            stage_species.get(species, 0.0) + condensed_kg)
                        if not is_designated_for_stage(species, stage_number):
                            stage_impurity = (
                                impurity_by_stage_species.setdefault(
                                    stage_number, {}))
                            stage_impurity[species] = (
                                stage_impurity.get(species, 0.0)
                                + condensed_kg)
                if wall_deposit_kg > 1e-15:
                    alpha_provenance = (
                        wall_sticking_alpha_provenance_by_species
                        .setdefault(species, {})
                    )
                    for stage_number, record in (
                        stage_alpha_records_by_stage.items()
                    ):
                        alpha_provenance[f'stage_{stage_number}'] = dict(
                            record)
                    if capture_budget_alpha_record:
                        alpha_provenance['capture_budget'] = dict(
                            capture_budget_alpha_record)

            remaining_by_species[species] = max(
                0.0, rate_kg_hr - capture_budget_kg)

        from simulator.diagnostics import wall_sticking_alpha_provenance_notice

        sticking_notice = wall_sticking_alpha_provenance_notice(
            wall_sticking_alpha_by_species,
            wall_sticking_alpha_provenance_by_species,
        )
        geometry_notice = dict(self.stage_area_geometry_provenance_notice)
        if geometry_notice:
            sticking_notice = dict(sticking_notice)
            sticking_notice['surface_geometry_provenance'] = geometry_notice
            sticking_notice['stage_area_geometry_provenance_notice'] = (
                geometry_notice)
        alpha_extrapolation_warnings = (
            _alpha_s_extrapolation_warnings_from_provenance(
                wall_sticking_alpha_provenance_by_species,
            )
        )
        if sticking_notice and alpha_extrapolation_warnings:
            sticking_notice = dict(sticking_notice)
            sticking_notice['alpha_s_extrapolation_warnings'] = (
                alpha_extrapolation_warnings
            )
        self.last_sticking_alpha_provenance_notice = dict(sticking_notice)
        if sticking_notice and self.operating_history:
            self.operating_history[-1][
                'wall_sticking_alpha_provenance_notice'
            ] = dict(sticking_notice)

        transport_notice: dict[str, Any] = {}
        if transport_parameter_notice_by_species:
            transport_notice = {
                'severity': 'warning',
                'code': 'transport_lennard_jones_proxy_rows',
                'species': sorted(transport_parameter_notice_by_species),
                'by_species': transport_parameter_notice_by_species,
                'carrier_gas': self.carrier_gas,
                'output_status': 'status_bearing',
            }
        self.last_transport_parameter_notice = dict(transport_notice)
        if transport_notice and self.operating_history:
            self.operating_history[-1][
                'transport_parameter_notice'
            ] = dict(transport_notice)

        capture_notice = (
            dict(CAPTURE_BUDGET_REGULARIZER_NOTICE)
            if used_capture_budget_regularizer else {}
        )
        self.last_capture_budget_regularizer_notice = dict(capture_notice)
        if capture_notice and self.operating_history:
            self.operating_history[-1][
                'capture_budget_regularizer_notice'
            ] = dict(capture_notice)

        return CondensationRouteResult(
            remaining_by_species=remaining_by_species,
            condensed_by_stage_species=condensed_by_stage_species,
            wall_deposit_by_species=wall_deposit_by_species,
            wall_deposit_by_segment_species=wall_deposit_by_segment_species,
            wall_deposit_fraction_by_species=wall_deposit_fraction_by_species,
            wall_deposit_account_fractions_by_species=(
                wall_deposit_account_fractions_by_species),
            wall_route_species_order=_wall_route_species_order(
                evap_flux.species_kg_hr.keys()
            ),
            wall_alkali_binding_diagnostic_state_by_segment=copy.deepcopy(
                self.wall_alkali_binding_diagnostic_state_by_account
            ),
            impurity_by_stage_species=impurity_by_stage_species,
            antoine_extrapolations=dict(antoine_extrapolations),
            antoine_extrapolation_warnings=tuple(
                antoine_extrapolation_warnings),
            cold_spot_warnings=cold_spot_warnings,
            knudsen_regime_diagnostic=knudsen_diagnostic,
            sticking_alpha_provenance_notice=sticking_notice,
            transport_parameter_notice=transport_notice,
            capture_budget_regularizer_notice=capture_notice,
            stage_area_geometry_provenance_notice=geometry_notice,
        )

    def adjust_c2a_pressure_setpoint(
        self,
        *,
        requested_p_total_mbar: float,
        pO2_mbar: float,
        gas_temperature_C: float,
        pipe_diameter_m: float,
        pN2_min_mbar: float,
        pN2_max_mbar: float,
        carrier_gas: str,
        pressure_margin_fraction: float = 0.01,
    ) -> dict[str, Any]:
        """Repair a stranded C2A pressure setpoint or retain typed refusal."""

        band_min = float(pN2_min_mbar)
        band_max = float(pN2_max_mbar)
        if (
                not math.isfinite(band_min)
                or not math.isfinite(band_max)
                or band_min <= 0.0
                or band_max < band_min
        ):
            raise ValueError('C2A pN2 operating band is empty or invalid')
        current = knudsen_regime_diagnostic(
            overhead_pressure_mbar=requested_p_total_mbar,
            gas_temperature_C=gas_temperature_C,
            pipe_diameter_m=pipe_diameter_m,
            pipe_segments=self.pipe_segments,
            carrier_gas=carrier_gas,
        )
        if current.get('status') != 'refused':
            prior = self.last_knudsen_pressure_adjustment
            if (
                    prior.get('status') == 'applied'
                    and math.isclose(
                        float(prior.get('applied_p_total_mbar', -1.0)),
                        float(requested_p_total_mbar),
                        rel_tol=0.0,
                        abs_tol=1.0e-12,
                    )
            ):
                return dict(prior)
            self.last_knudsen_pressure_adjustment = {}
            return {}

        basis = minimum_pressure_mbar_for_knudsen(
            gas_temperature_C=gas_temperature_C,
            pipe_diameter_m=pipe_diameter_m,
            pipe_segments=self.pipe_segments,
            carrier_gas=carrier_gas,
        )
        pO2 = max(0.0, float(pO2_mbar))
        minimum_total_with_margin_mbar = (
            float(basis['minimum_pressure_mbar'])
            * (1.0 + float(pressure_margin_fraction))
        )
        applied_pN2_mbar = max(
            band_min,
            minimum_total_with_margin_mbar - pO2,
        )
        diagnostic = {
            'status': 'applied',
            'reason': 'c2a_knudsen_pressure_floor',
            'requested_p_total_mbar': float(requested_p_total_mbar),
            'requested_pN2_mbar': max(
                0.0,
                float(requested_p_total_mbar) - pO2,
            ),
            'pO2_mbar': pO2,
            'physical_pN2_band_mbar': [
                band_min,
                band_max,
            ],
            'pressure_margin_fraction': float(pressure_margin_fraction),
            **basis,
        }
        if applied_pN2_mbar > band_max:
            diagnostic.update({
                'status': 'refused',
                'reason': 'c2a_knudsen_pressure_window_empty',
                'reason_refused': KNUDSEN_REFUSAL_REASON,
                'required_pN2_mbar': applied_pN2_mbar,
            })
            self.last_knudsen_pressure_adjustment = dict(diagnostic)
            refusal = dict(current)
            refusal['pressure_adjustment'] = dict(diagnostic)
            raise KnudsenRegimeRefusal(refusal)

        diagnostic.update({
            'applied_pN2_mbar': applied_pN2_mbar,
            'applied_p_total_mbar': pO2 + applied_pN2_mbar,
        })
        self.last_knudsen_pressure_adjustment = dict(diagnostic)
        return diagnostic

    def _current_knudsen_diagnostic(self) -> dict[str, Any]:
        diagnostic = knudsen_regime_diagnostic(
            overhead_pressure_mbar=self.overhead_pressure_mbar,
            gas_temperature_C=self.gas_temperature_C,
            pipe_diameter_m=self.pipe_diameter_m,
            pipe_segments=self.pipe_segments,
            regime_factor=self.regime_factor,
            carrier_gas=self.carrier_gas,
        )
        diagnostic['stage_area_m2_by_stage'] = dict(self.stage_area_m2_by_stage)
        diagnostic['stage_area_geometry_provenance_notice'] = dict(
            self.stage_area_geometry_provenance_notice)
        if self._knudsen_policy_configured:
            if not self._viscous_flow_required:
                relaxed = dict(diagnostic)
                relaxed['status'] = 'ok'
                relaxed['reason'] = ''
                relaxed['warnings'] = []
                relaxed['viscous_flow_required'] = False
                return relaxed
            diagnostic['viscous_flow_required'] = True
            if self.last_knudsen_pressure_adjustment:
                diagnostic['pressure_adjustment'] = dict(
                    self.last_knudsen_pressure_adjustment
                )
            return diagnostic
        unconfigured = dict(diagnostic)
        unconfigured['status'] = 'refused'
        unconfigured['reason'] = 'knudsen_policy_unconfigured'
        unconfigured['reason_refused'] = 'knudsen_policy_unconfigured'
        unconfigured['message'] = (
            'Knudsen pressure policy is unconfigured; condensation routing '
            'refused.'
        )
        return unconfigured

    def _enforce_knudsen_regime(self) -> dict[str, Any]:
        diagnostic = self._current_knudsen_diagnostic()
        self.last_knudsen_regime_diagnostic = diagnostic
        if diagnostic.get('status') == 'refused':
            raise KnudsenRegimeRefusal(diagnostic)
        warnings = tuple(diagnostic.get('warnings', ()))
        if warnings and self.operating_history:
            self.operating_history[-1]['knudsen_warnings'] = warnings
            self.operating_history[-1]['knudsen_regime_diagnostic'] = dict(
                diagnostic)
        return diagnostic

    def _wall_deposit_candidate_kg(
        self,
        *,
        species: str,
        rate_kg_hr: float,
        T_cond_C: float,
        melt_temperature_C: float,
        antoine_extrapolations: MutableMapping[str, Dict[str, Any]] | None = None,
        antoine_extrapolation_warnings: list[str] | None = None,
    ) -> float:
        if (
            antoine_extrapolations is not None
            or antoine_extrapolation_warnings is not None
        ):
            _local_wall_species_pressure_pa(
                species,
                melt_temperature_C,
                T_cond_C,
                vapor_pressure_data=self.vapor_pressure_data,
                antoine_extrapolations=antoine_extrapolations,
                antoine_extrapolation_warnings=(
                    antoine_extrapolation_warnings),
            )
            _record_wall_surface_antoine_telemetry(
                species,
                self.wall_temperature_C,
                vapor_pressure_data=self.vapor_pressure_data,
                antoine_extrapolations=antoine_extrapolations,
                antoine_extrapolation_warnings=(
                    antoine_extrapolation_warnings),
            )
        token = _with_antoine_telemetry_context(
            antoine_extrapolations,
            antoine_extrapolation_warnings,
        )
        try:
            return query_wall_deposit_candidate_kg(
                self,
                species=species,
                rate_kg_hr=rate_kg_hr,
                T_cond_C=T_cond_C,
                melt_temperature_C=melt_temperature_C,
            )
        finally:
            _reset_antoine_telemetry_context(token)

    def _wall_deposit_candidates_by_segment_kg(
        self,
        *,
        species: str,
        rate_kg_hr: float,
        T_cond_C: float,
        melt_temperature_C: float,
        supply_by_segment_kg: Mapping[str, float],
        antoine_extrapolations: MutableMapping[str, Dict[str, Any]] | None = None,
        antoine_extrapolation_warnings: list[str] | None = None,
    ) -> Dict[str, float]:
        if (
            antoine_extrapolations is not None
            or antoine_extrapolation_warnings is not None
        ):
            _local_wall_species_pressure_pa(
                species,
                melt_temperature_C,
                T_cond_C,
                vapor_pressure_data=self.vapor_pressure_data,
                antoine_extrapolations=antoine_extrapolations,
                antoine_extrapolation_warnings=(
                    antoine_extrapolation_warnings),
            )
            for segment in self._mixed_temperature_wall_candidate_segments(
                species,
            ):
                _record_wall_surface_antoine_telemetry(
                    species,
                    segment.wall_temperature_C,
                    vapor_pressure_data=self.vapor_pressure_data,
                    antoine_extrapolations=antoine_extrapolations,
                    antoine_extrapolation_warnings=(
                        antoine_extrapolation_warnings),
                )
        token = _with_antoine_telemetry_context(
            antoine_extrapolations,
            antoine_extrapolation_warnings,
        )
        try:
            return query_wall_deposit_candidates_by_segment_kg(
                self,
                species=species,
                rate_kg_hr=rate_kg_hr,
                T_cond_C=T_cond_C,
                melt_temperature_C=melt_temperature_C,
                supply_by_segment_kg=supply_by_segment_kg,
            )
        finally:
            _reset_antoine_telemetry_context(token)

    def _mixed_temperature_wall_candidate_segments(
        self,
        species: str,
    ) -> list[PipeSegment]:
        target_stage_number = designated_stage_number(species)
        if target_stage_number is None:
            return []
        segments: list[PipeSegment] = []
        for segment in self.pipe_segments:
            downstream_number = _segment_stage_number(segment.downstream_stage)
            if downstream_number is None:
                continue
            if downstream_number <= target_stage_number:
                segments.append(segment)
        return segments

    def _wall_deposit_candidate_for_surface_kg(
        self,
        *,
        species: str,
        rate_kg_hr: float,
        T_cond_C: float,
        melt_temperature_C: float,
        wall_temperature_C: float,
        surface_area_m2: float,
        antoine_extrapolations: MutableMapping[str, Dict[str, Any]] | None = None,
        antoine_extrapolation_warnings: list[str] | None = None,
    ) -> float:
        if (
            antoine_extrapolations is not None
            or antoine_extrapolation_warnings is not None
        ):
            _local_wall_species_pressure_pa(
                species,
                melt_temperature_C,
                T_cond_C,
                vapor_pressure_data=self.vapor_pressure_data,
                antoine_extrapolations=antoine_extrapolations,
                antoine_extrapolation_warnings=(
                    antoine_extrapolation_warnings),
            )
            _record_wall_surface_antoine_telemetry(
                species,
                wall_temperature_C,
                vapor_pressure_data=self.vapor_pressure_data,
                antoine_extrapolations=antoine_extrapolations,
                antoine_extrapolation_warnings=(
                    antoine_extrapolation_warnings),
            )
        token = _with_antoine_telemetry_context(
            antoine_extrapolations,
            antoine_extrapolation_warnings,
        )
        try:
            return query_wall_deposit_candidate_for_surface_kg(
                self,
                species=species,
                rate_kg_hr=rate_kg_hr,
                T_cond_C=T_cond_C,
                melt_temperature_C=melt_temperature_C,
                wall_temperature_C=wall_temperature_C,
                surface_area_m2=surface_area_m2,
            )
        finally:
            _reset_antoine_telemetry_context(token)

    def _segment_supply_by_name(
        self,
        rate_kg_hr: float,
        remaining_after_stage: Mapping[int, float],
    ) -> Dict[str, float]:
        supply: Dict[str, float] = {}
        for segment in self.pipe_segments:
            upstream_number = _segment_stage_number(segment.upstream_stage)
            if upstream_number is None:
                supply[segment.name] = max(0.0, float(rate_kg_hr))
            else:
                prior_numbers = [
                    stage_number
                    for stage_number in remaining_after_stage
                    if stage_number <= upstream_number
                ]
                default_supply = (
                    remaining_after_stage[max(prior_numbers)]
                    if prior_numbers else rate_kg_hr
                )
                supply[segment.name] = max(
                    0.0,
                    min(
                        float(rate_kg_hr),
                        float(remaining_after_stage.get(
                            upstream_number, default_supply)),
                    ),
                )
        return supply

    def _condensation_efficiency(
        self,
        *,
        stage: CondensationStage,
        species: str,
        T_cond_C: float,
        residence_s: float,
        available_kg: float,
        alpha_s_value: float,
        alpha_record: MutableMapping[str, Any] | None = None,
        antoine_extrapolations: MutableMapping[str, Dict[str, Any]] | None = None,
        antoine_extrapolation_warnings: list[str] | None = None,
    ) -> float:
        """
        Condensation efficiency for one species in one stage.

        Wall deposition uses ``_series_resistance_deposition_flux_mol_m2_s``:
        HKL surface uptake in series with gas-side mass transfer, with
        ``f = Kn/(Kn+0.01)`` removing boundary-layer resistance as flow
        becomes free molecular.

        Reactive wall products use the product-P_sat floor in the shared
        driving-pressure helper instead of the vapor species' Antoine P_sat.

        Chunk A keeps the existing stage residence-time surrogate for
        geometry and integrates the H-K-L driving force across the actual
        stage T-band. Chunk C replaces the constant regime factor with
        pressure/Knudsen coupling.
        """
        if residence_s <= 0.0 or alpha_s_value <= 0.0:
            return 0.0

        P_local_pa = _local_species_pressure_pa(
            species,
            T_cond_C,
            vapor_pressure_data=self.vapor_pressure_data,
            antoine_extrapolations=antoine_extrapolations,
            antoine_extrapolation_warnings=antoine_extrapolation_warnings,
        )
        if P_local_pa <= 0.0:
            return 0.0

        T_ref_K = max(T_cond_C + CELSIUS_TO_KELVIN_OFFSET, 1.0)
        reference_flux = _hkl_impingement_flux_mol_m2_s(
            species,
            P_local_pa,
            T_ref_K,
            vapor_pressure_data=self.vapor_pressure_data,
        )
        if reference_flux <= 0.0:
            return 0.0

        lo_C, hi_C = _stage_temp_band_C(stage)
        if hi_C < lo_C:
            lo_C, hi_C = hi_C, lo_C

        band_flux_fraction = 0.0
        band_flux_mol_m2_s = 0.0
        width_C = hi_C - lo_C
        spec = (
            alpha_record.get('alpha_s_coefficient_spec')
            if isinstance(alpha_record, Mapping)
            else None
        )
        sample_extrapolated = False
        for sample in range(HKL_BAND_SAMPLES):
            if width_C <= 0.0:
                T_surface_C = lo_C
            else:
                T_surface_C = (
                    lo_C + width_C * (sample + 0.5) / HKL_BAND_SAMPLES
                )
            T_surface_K = max(T_surface_C + CELSIUS_TO_KELVIN_OFFSET, 1.0)
            sample_alpha_s = alpha_s_value
            if isinstance(spec, Mapping):
                alpha_context: dict[str, Any] = {'coefficient_spec': spec}
                sample_alpha_s = _condensation_alpha_s(
                    species,
                    T_surface_K,
                    alpha_context,
                )
                sample_eval = alpha_context.get('alpha_s_evaluation', {})
                sample_extrapolated = sample_extrapolated or bool(
                    sample_eval.get('alpha_s_extrapolated')
                )
            # 0.5.2 Phase B (series-resistance + stir-Sherwood): same
            # series-resistance form as the wall-deposit candidate path so
            # the stage-condensation band integration honors the canonical
            # mass-transfer composition (Bird/Stewart/Lightfoot) rather
            # than the v1 additive blend. T_surface_K (stage T-band
            # sample) drives P_sat; T_gas_K (bulk gas) drives the
            # ideal-gas denominator. overhead_pressure_pa feeds the
            # Chapman-Enskog D_AB(T, P) per Phase A1. stir_factor
            # amplifies the boundary-layer Sherwood per the operator's
            # induction-stirring power.
            T_gas_K = max(
                float(self.gas_temperature_C) + CELSIUS_TO_KELVIN_OFFSET,
                1.0,
            )
            overhead_pressure_pa = float(self.overhead_pressure_mbar) * 100.0
            flux = _series_resistance_deposition_flux_mol_m2_s(
                species, P_local_pa, T_surface_K, sample_alpha_s,
                pipe_diameter_m=self.pipe_diameter_m,
                # 0.5.3 Phase B: pass both axes (see twin call above
                # in ``_wall_deposit_candidate_for_surface_kg`` for the
                # precedence rationale; the helper reads radial as the
                # Sh driver, legacy stir_factor as audit-history).
                stir_factor=self.stir_factor,
                radial_stir_factor=self.radial_stir_factor,
                regime_factor=self.regime_factor,
                T_gas_K=T_gas_K,
                overhead_pressure_pa=overhead_pressure_pa,
                carrier_gas=self.carrier_gas,
                vapor_pressure_data=self.vapor_pressure_data,
                # Owner scope for 2026-06-29 re-evap fix is wall deposits
                # only; keep historical condenser-stage capture unchanged.
                reactive_product_backstop=False,
                antoine_extrapolations=antoine_extrapolations,
                antoine_extrapolation_warnings=antoine_extrapolation_warnings,
            )
            band_flux_fraction += flux / reference_flux
            band_flux_mol_m2_s += flux
        band_flux_fraction /= HKL_BAND_SAMPLES
        band_flux_mol_m2_s /= HKL_BAND_SAMPLES
        if isinstance(alpha_record, MutableMapping) and isinstance(spec, Mapping):
            alpha_record['alpha_s_sample_temperature_range_K'] = [
                max(lo_C + CELSIUS_TO_KELVIN_OFFSET, 1.0),
                max(hi_C + CELSIUS_TO_KELVIN_OFFSET, 1.0),
            ]
            alpha_record['alpha_s_sample_extrapolated'] = sample_extrapolated

        stage_area_m2 = self._stage_area_m2_for_stage_number(stage.stage_number)
        if stage_area_m2 is not None:
            molar_mass_kg_mol = (
                _molecular_mass_kg_per_molecule(
                    species,
                    vapor_pressure_data=self.vapor_pressure_data,
                )
                * AVOGADRO_MOL
            )
            if (
                not math.isfinite(available_kg)
                or available_kg <= 0.0
                or molar_mass_kg_mol <= 0.0
            ):
                return 0.0
            available_mol = available_kg / molar_mass_kg_mol
            # HKL/transport flux is mol m^-2 s^-1. Integrating over physical
            # baffle area and residence time gives capturable mol:
            # (mol m^-2 s^-1)(m^2)(s) = mol. Dividing by available vapor mol
            # makes eta dimensionless and lets configured Stage-3 area affect
            # capture without inventing an area-normalized rate constant.
            capturable_mol = (
                max(0.0, band_flux_mol_m2_s)
                * stage_area_m2
                * residence_s
            )
            eta = capturable_mol / available_mol
        else:
            # Premise: ``band_flux_fraction = flux / reference_flux`` is
            # dimensionless. Algebra requires capturable mol =
            # flux(mol m^-2 s^-1) * area(m^2) * residence(s), then
            # eta = capturable_mol / available_mol. Unit check: without a
            # configured area there is no m^2 term, so treating the ratio as
            # s^-1 invents a rate constant. Sanity: leave vapor un-baffled
            # rather than create synthetic capture.
            eta = 0.0
        return max(0.0, min(1.0, eta))


def _species_vapor_data(
    species: str,
    *,
    vapor_pressure_data: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    global_data: Mapping[str, Any] = {}
    for family in ('metals', 'oxide_vapors'):
        data = (VAPOR_PRESSURE_DATA.get(family, {}) or {}).get(species, {})
        if data and isinstance(data, Mapping):
            global_data = data
            break
    if vapor_pressure_data is not None:
        for family in ('metals', 'oxide_vapors'):
            group = vapor_pressure_data.get(family, {}) or {}
            if not isinstance(group, Mapping):
                continue
            data = group.get(species)
            if data and isinstance(data, Mapping):
                merged = dict(global_data)
                for key, value in data.items():
                    if key in _ANTOINE_COEFFICIENT_BLOCKS:
                        missing = _missing_required_antoine_keys(value)
                        if missing:
                            fallback = global_data.get(key)
                            if isinstance(fallback, Mapping):
                                merged[key] = fallback
                                action = 'falling back to global block'
                            else:
                                merged.pop(key, None)
                                action = 'dropping incomplete block'
                            warnings.warn(
                                f"{species} incomplete custom vapor-pressure "
                                f"{key} block missing required coefficient "
                                f"keys {', '.join(missing)}; {action}",
                                RuntimeWarning,
                                stacklevel=2,
                            )
                            continue
                    merged[key] = value
                return merged
    return global_data


def _missing_required_antoine_keys(block: Any) -> tuple[str, ...]:
    """Return coefficient keys missing from the block consumed at runtime."""

    if not isinstance(block, Mapping):
        return tuple(sorted(_ANTOINE_REQUIRED_KEYS))
    segments = block.get('segments')
    candidates: list[Mapping[str, Any]] = []
    if isinstance(segments, list):
        candidates.extend(segment for segment in segments
                          if isinstance(segment, Mapping))
    if not candidates:
        candidates.append(block)
    missing = {
        key
        for candidate in candidates
        for key in _ANTOINE_REQUIRED_KEYS
        if key not in candidate
    }
    return tuple(sorted(missing))


def apply_setpoints_condensation_temperature_overrides(
    setpoints: Mapping[str, Any] | None,
) -> dict[str, float]:
    """0.5.4.1 B1-tunable (CW3 follow-on, 2026-05-28): merge
    operator-supplied per-species condensation temperatures from
    ``data/setpoints.yaml § condensation_train.condensation_temperatures_C``
    into the module-level ``CONDENSATION_TEMPS_C`` fallback dict.

    Returns the snapshot of the original (pre-merge) module dict so
    callers can restore in a ``try`` / ``finally`` when needed (mainly
    useful for tests that swap setpoints across runs in the same
    process).

    Per the worker recommendation on B1 (codex /review scan
    ``docs-private/reviews/2026-05-28-b1-e2a-scan/codex-scan.txt``):
    the hardcoded SiO=1050 °C value is the recipe MIDPOINT of the
    documented 900-1200 °C Stage 3 SiO zone, NOT a literature-derived
    T_cond. Operators retune via this YAML seam without code edits.

    Schema (per ``data/setpoints.yaml``):

      condensation_train:
        condensation_temperatures_C:
          SiO: 1050
          Fe:  1250
          ...

    - Non-finite / non-coercible entries are skipped (defensive).
    - Species not present in the YAML keep their fallback value.
    - Idempotent: calling twice with the same setpoints leaves the
      module dict in the same state.

    **Module-state caveat (multi-tenant)**: this helper mutates the
    module-level ``CONDENSATION_TEMPS_C`` dict in place. In a single-
    sim-per-process layout (the default for tests + standalone runs)
    this is safe — pytest-xdist gives each worker its own Python
    process, so module state is isolated. In a multi-tenant server
    that builds multiple ``PyrolysisSimulator`` instances with
    *different* setpoints inside one interpreter, the LAST call to
    this helper wins; previously-built sims that cached their
    ``condensation_model`` property won't pick up the new values
    either. If multi-tenant use ever lands, the fix shape is to
    move the dict onto ``CondensationModel`` as an instance
    attribute and have ``_species_condensation_temperature_C``
    read from that instance. Tracked in
    ``docs-private/goal-deferred-and-roadmap-2026-05-28.md`` for
    the F2 web UI thin-driver chunk.
    """
    snapshot = dict(CONDENSATION_TEMPS_C)
    if not setpoints:
        return snapshot
    block = (
        (setpoints.get('condensation_train', {}) or {})
        .get('condensation_temperatures_C', {}) or {}
    )
    if not isinstance(block, Mapping):
        return snapshot
    for species, value in block.items():
        try:
            T_C = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(T_C):
            continue
        CONDENSATION_TEMPS_C[str(species)] = T_C
    return snapshot


def restore_condensation_temperature_overrides(
    snapshot: Mapping[str, float],
) -> None:
    """Restore the module-level ``CONDENSATION_TEMPS_C`` dict to a
    prior snapshot (from
    ``apply_setpoints_condensation_temperature_overrides``)."""
    CONDENSATION_TEMPS_C.clear()
    CONDENSATION_TEMPS_C.update(snapshot)


def _species_condensation_temperature_C(
    species: str,
    *,
    temps: Mapping[str, float] | None = None,
    vapor_pressure_data: Mapping[str, Any] | None = None,
) -> float:
    """0.5.4.1 review-cluster-C (P2 #1): accept an optional
    instance-level ``temps`` mapping so each ``CondensationModel``
    can isolate its overrides from the module-level fallback dict.
    When ``temps`` is None (legacy / test-only callers), falls back
    to the process-global ``CONDENSATION_TEMPS_C`` dict; when
    provided, the instance dict takes precedence. Production paths
    (inside ``CondensationModel.route()``) pass
    ``self.condensation_temperatures_C``; legacy module-level
    callers pass nothing and get the previous behaviour.
    """
    source = temps if temps is not None else CONDENSATION_TEMPS_C
    if species in source:
        return float(source[species])
    data = _species_vapor_data(
        species,
        vapor_pressure_data=vapor_pressure_data,
    )
    raw_temperature = data.get('condensation_T_C_at_1mbar')
    if raw_temperature is None:
        raise ValueError(
            f'condensation temperature unavailable for species {species!r}'
        )
    try:
        temperature_C = float(raw_temperature)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f'invalid condensation temperature for species {species!r}'
        ) from exc
    if not math.isfinite(temperature_C):
        raise ValueError(
            f'invalid condensation temperature for species {species!r}'
        )
    return temperature_C


def _materials_source(materials: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    return materials if materials is not None else MATERIALS_DATA


def _stage_material_config(
    stage: CondensationStage,
    materials: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    stages = _materials_source(materials).get('stages', {}) or {}
    if not isinstance(stages, Mapping):
        return {}
    config = stages.get(stage.stage_number, stages.get(str(stage.stage_number), {}))
    return config if isinstance(config, Mapping) else {}


def _stage_temp_band_C(stage: CondensationStage) -> tuple[float, float]:
    lo_C, hi_C = stage.temp_range_C
    return float(lo_C), float(hi_C)


def _stage_midpoint_temperature_K(stage: CondensationStage) -> float:
    lo_C, hi_C = _stage_temp_band_C(stage)
    return max(((lo_C + hi_C) / 2.0) + CELSIUS_TO_KELVIN_OFFSET, 1.0)


def _stage_alpha_s(
    stage: CondensationStage,
    species: str,
    materials: Mapping[str, Any] | None = None,
) -> float:
    return float(_stage_alpha_record(
        stage,
        species,
        materials,
        T_K=_stage_midpoint_temperature_K(stage),
    )['alpha_s'])


def _alpha_entry_with_species(species: str, entry: Any) -> Any:
    if isinstance(entry, Mapping) and 'value_ref' in entry and 'species' not in entry:
        enriched = dict(entry)
        enriched['species'] = species
        return enriched
    return entry


def _alpha_entry_value(entry: Any) -> Any:
    species = str(entry.get('species', '')) if isinstance(entry, Mapping) else ''
    return _alpha_s_spec_from_entry(species, entry)


def _coerce_alpha_s(
    entry: Any,
    *,
    species: str = '',
    T_K: float | None = None,
    evaluation_out: MutableMapping[str, Any] | None = None,
) -> float:
    spec = _alpha_s_spec_from_entry(species, entry)
    if isinstance(spec, Mapping):
        if T_K is None:
            raise ValueError(f'alpha_s({species}): T_K required for T-form')
        context: dict[str, Any] = {'coefficient_spec': spec}
        value = _condensation_alpha_s(species, T_K, context)
        if evaluation_out is not None:
            evaluation_out.clear()
            evaluation_out.update(context.get('alpha_s_evaluation', {}))
        return value
    try:
        alpha_value = float(spec)
    except (TypeError, ValueError):
        alpha_value = 0.0
    if not math.isfinite(alpha_value):
        return 0.0
    value = max(0.0, min(1.0, alpha_value))
    if evaluation_out is not None:
        evaluation_out.clear()
        evaluation_out.update({
            'species': str(species),
            'alpha_s': value,
            'alpha_s_form': 'scalar',
            'alpha_s_temperature_K': T_K,
            'alpha_s_extrapolated': False,
        })
    return value


def _alpha_record(
    *,
    species: str,
    entry: Any,
    source: str,
    T_K: float | None = None,
    liner_material: str = '',
    segment: PipeSegment | None = None,
    source_class: str = 'assumption_ungrounded_fitted_coefficient',
    status: str = 'proxy',
    output_status: str = 'uncertainty_only',
) -> dict[str, Any]:
    evaluation: dict[str, Any] = {}
    record: dict[str, Any] = {
        'species': str(species),
        'alpha_s': _coerce_alpha_s(
            _alpha_entry_with_species(species, entry),
            species=species,
            T_K=T_K,
            evaluation_out=evaluation,
        ),
        'source': source,
        'source_class': source_class,
        'status': status,
        'output_status': output_status,
    }
    if evaluation:
        record.update(evaluation)
    entry = _alpha_entry_with_species(species, entry)
    ref_record = _sticking_entry_ref_record(species, entry)
    if ref_record is not None:
        record.update(_sticking_record_payload(ref_record))
        record['value_ref'] = entry.get('value_ref')
        record['material_source'] = source
    if liner_material:
        record['liner_material'] = str(liner_material)
    if segment is not None:
        record['segment'] = str(segment.name)
    if isinstance(entry, Mapping):
        for key in ('source', 'source_class', 'status', 'output_status', 'basis'):
            value = entry.get(key)
            if value not in (None, ''):
                record[key] = value
    record.update(_material_alpha_certification_payload(
        entry,
        record,
        source=source,
    ))
    if record.get('alpha_s_cold_wall_condensation') is True:
        record.update(_cold_wall_condensation_record_payload(species, record))
    spec = _alpha_s_spec_from_entry(species, entry)
    if isinstance(spec, Mapping):
        record['alpha_s_coefficient_spec'] = dict(spec)
    return record


def _wall_material_config(
    materials: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    surfaces = _materials_source(materials).get('wall_surfaces', {}) or {}
    if not isinstance(surfaces, Mapping):
        return {}
    config = surfaces.get('interstage_duct', {}) or {}
    return config if isinstance(config, Mapping) else {}


def _wall_alpha_s(
    species: str,
    materials: Mapping[str, Any] | None = None,
    *,
    segment: PipeSegment | None = None,
    T_K: float | None = None,
) -> float:
    return float(_wall_alpha_record(
        species,
        materials,
        segment=segment,
        T_K=T_K,
    )['alpha_s'])


def _sidecar_alpha_record(species: str, T_K: float | None = None) -> dict[str, Any]:
    return _alpha_record(
        species=species,
        entry={
            'species': species,
            'value_ref': (
                f'{STICKING_VALUE_REF_PREFIX}{species}.value'
                if species in STICKING_COEFF
                else STICKING_UNKNOWN_REF
            ),
        },
        source=(
            'data/literature/vacuum_pyrolysis_sticking.yaml::species.'
            f'{species}'
        ),
        T_K=T_K,
    )


def _stage_alpha_record(
    stage: CondensationStage,
    species: str,
    materials: Mapping[str, Any] | None = None,
    *,
    T_K: float | None = None,
) -> dict[str, Any]:
    if T_K is None:
        T_K = _stage_midpoint_temperature_K(stage)
    config = _stage_material_config(stage, materials)
    alpha_by_species = config.get('alpha_s_by_species', {}) or {}
    entry = (
        alpha_by_species.get(species)
        if isinstance(alpha_by_species, Mapping)
        else None
    )
    # An explicit null / absent stage entry falls back to the grounded sidecar
    # record (matching the pre-record _stage_alpha_s behaviour); only a present
    # non-None material entry overrides it.
    if entry is not None:
        record = _alpha_record(
            species=species,
            entry=entry,
            source=(
                'data/materials.yaml::stages.'
                f'{stage.stage_number}.alpha_s_by_species.{species}'
            ),
            T_K=T_K,
            source_class='material_stage_alpha',
        )
        record['stage_number'] = int(stage.stage_number)
        return record
    record = _sidecar_alpha_record(species, T_K=T_K)
    record['stage_number'] = int(stage.stage_number)
    return record


def _wall_alpha_record(
    species: str,
    materials: Mapping[str, Any] | None = None,
    *,
    segment: PipeSegment | None = None,
    T_K: float | None = None,
) -> dict[str, Any]:
    if T_K is None and segment is not None:
        T_K = max(
            float(segment.wall_temperature_C) + CELSIUS_TO_KELVIN_OFFSET,
            1.0,
        )
    config = _wall_material_config(materials)
    alpha_by_species = config.get('alpha_s_by_species', {}) or {}
    if segment is not None and getattr(segment, 'liner_material', ''):
        liner_material = str(segment.liner_material)
        material_config = _liner_material_config(liner_material, materials)
        material_alpha = material_config.get('alpha_s_by_species', {}) or {}
        if (
            isinstance(material_alpha, Mapping)
            and material_alpha.get(species) is not None
        ):
            return _alpha_record(
                species=species,
                entry=material_alpha.get(species),
                source=(
                    'data/materials.yaml::liner_materials.'
                    f'{liner_material}.alpha_s_by_species.{species}'
                ),
                T_K=T_K,
                liner_material=liner_material,
                segment=segment,
                source_class='material_liner_alpha',
            )
    if (
        isinstance(alpha_by_species, Mapping)
        and alpha_by_species.get(species) is not None
    ):
        return _alpha_record(
            species=species,
            entry=alpha_by_species.get(species),
            source=(
                'data/materials.yaml::wall_surfaces.interstage_duct.'
                f'alpha_s_by_species.{species}'
            ),
            T_K=T_K,
            liner_material=str(config.get('liner_material') or ''),
            segment=segment,
        )
    liner_material = str(config.get('liner_material') or '')
    if liner_material:
        material_config = _liner_material_config(liner_material, materials)
        material_alpha = material_config.get('alpha_s_by_species', {}) or {}
        if (
            isinstance(material_alpha, Mapping)
            and material_alpha.get(species) is not None
        ):
            return _alpha_record(
                species=species,
                entry=material_alpha.get(species),
                source=(
                    'data/materials.yaml::liner_materials.'
                    f'{liner_material}.alpha_s_by_species.{species}'
                ),
                T_K=T_K,
                liner_material=liner_material,
                segment=segment,
                source_class='material_liner_alpha',
            )
    return _sidecar_alpha_record(species, T_K=T_K)


def _capture_budget_alpha_record(
    species: str,
    T_K: float | None = None,
) -> dict[str, Any]:
    return _sidecar_alpha_record(species, T_K=T_K)


def _liner_material_config(
    material: str,
    materials: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    liner_materials = _materials_source(materials).get('liner_materials', {}) or {}
    if not isinstance(liner_materials, Mapping):
        return {}
    config = liner_materials.get(material, {}) or {}
    return config if isinstance(config, Mapping) else {}


def _default_pipe_surface_area_m2() -> float:
    from simulator.equipment import PipeSpec

    pipe = PipeSpec()
    if pipe.surface_area_m2 > 0.0:
        return float(pipe.surface_area_m2)
    return math.pi * float(pipe.diameter_m) * float(pipe.length_m)


def _record_antoine_extrapolation(
    species: str,
    T_K: float,
    data: Mapping[str, Any],
    coefficient_block: str | None = None,
    *,
    antoine_extrapolations: MutableMapping[str, Dict[str, Any]] | None,
    antoine_extrapolation_warnings: list[str] | None,
) -> None:
    from engines.builtin.vapor_pressure import vapor_pressure_valid_range_K

    valid_range = vapor_pressure_valid_range_K(
        data,
        coefficient_block,
        temperature_K=T_K,
    )
    if not (isinstance(valid_range, (list, tuple)) and len(valid_range) == 2):
        return
    try:
        valid_low = float(valid_range[0])
        valid_high = float(valid_range[1])
    except (TypeError, ValueError):
        return
    if not (
        math.isfinite(valid_low)
        and math.isfinite(valid_high)
        and valid_low <= valid_high
    ):
        return
    if valid_low <= T_K <= valid_high:
        return

    record = {
        'temperature_K': T_K,
        'valid_range_K': (valid_low, valid_high),
    }
    if antoine_extrapolations is not None:
        existing_records = [
            existing
            for key, existing in antoine_extrapolations.items()
            if str(key).split("#", 1)[0] == species
        ]
        if not any(dict(existing) == record for existing in existing_records):
            key = species
            if key in antoine_extrapolations:
                suffix = 2
                while f'{species}#{suffix}' in antoine_extrapolations:
                    suffix += 1
                key = f'{species}#{suffix}'
            antoine_extrapolations[key] = record
    if antoine_extrapolation_warnings is not None:
        temperature_text = f"{T_K:.3f}".rstrip('0').rstrip('.')
        warning = (
            f"{species} metal Antoine fit extrapolated beyond "
            f"valid_range_K [{valid_low:g}, {valid_high:g}] at "
            f"{temperature_text} K"
        )
        if warning not in antoine_extrapolation_warnings:
            antoine_extrapolation_warnings.append(warning)


def _antoine_psat_pa(
    species: str,
    T_K: float,
    *,
    vapor_pressure_data: Mapping[str, Any] | None = None,
    antoine_extrapolations: MutableMapping[str, Dict[str, Any]] | None = None,
    antoine_extrapolation_warnings: list[str] | None = None,
) -> float | None:
    data = _species_vapor_data(
        species,
        vapor_pressure_data=vapor_pressure_data,
    )
    from engines.builtin.vapor_pressure import (
        require_antoine_source_certified_temperature,
        wall_condensation_antoine_coefficients,
    )

    antoine, coefficient_block = wall_condensation_antoine_coefficients(
        data,
        temperature_K=T_K,
    )
    if not isinstance(antoine, Mapping):
        return None
    try:
        A = float(antoine.get('A', 0.0))
        B = float(antoine.get('B', 0.0))
        C = float(antoine.get('C', 0.0))
        T_K = float(T_K)
    except (TypeError, ValueError):
        return None
    if not (A > 0.0 and math.isfinite(T_K) and T_K + C > 0.0):
        return None
    require_antoine_source_certified_temperature(
        species,
        data,
        coefficient_block,
        T_K,
        consumer="wall_condensation",
    )
    _record_antoine_extrapolation(
        species,
        T_K,
        data,
        coefficient_block,
        antoine_extrapolations=antoine_extrapolations,
        antoine_extrapolation_warnings=antoine_extrapolation_warnings,
    )
    # Same Antoine form used by equilibrium.py and builtin vapor pressure.
    return 10.0 ** (A - B / (T_K + C))


def _try_antoine_psat_pa(
    species: str,
    T_K: float,
    *,
    vapor_pressure_data: Mapping[str, Any] | None = None,
    antoine_extrapolations: MutableMapping[str, Dict[str, Any]] | None = None,
    antoine_extrapolation_warnings: list[str] | None = None,
) -> tuple[float | None, bool]:
    """Return a wall pressure or a named, fail-closed range refusal."""

    from engines.builtin.vapor_pressure import VaporPressureRangeError

    try:
        return (
            _antoine_psat_pa(
                species,
                T_K,
                vapor_pressure_data=vapor_pressure_data,
                antoine_extrapolations=antoine_extrapolations,
                antoine_extrapolation_warnings=antoine_extrapolation_warnings,
            ),
            False,
        )
    except VaporPressureRangeError as exc:
        if (
            antoine_extrapolation_warnings is not None
            and str(exc) not in antoine_extrapolation_warnings
        ):
            antoine_extrapolation_warnings.append(str(exc))
        return None, True


def _local_species_pressure_pa(
    species: str,
    T_cond_C: float,
    *,
    vapor_pressure_data: Mapping[str, Any] | None = None,
    antoine_extrapolations: MutableMapping[str, Dict[str, Any]] | None = None,
    antoine_extrapolation_warnings: list[str] | None = None,
) -> float:
    P_local_pa, refused = _try_antoine_psat_pa(
        species,
        T_cond_C + CELSIUS_TO_KELVIN_OFFSET,
        vapor_pressure_data=vapor_pressure_data,
        antoine_extrapolations=antoine_extrapolations,
        antoine_extrapolation_warnings=antoine_extrapolation_warnings,
    )
    if refused:
        return 0.0
    if P_local_pa is not None and P_local_pa > 0.0:
        return P_local_pa
    # Existing condensation temperatures are documented at ~1 mbar.
    return 100.0


def _record_wall_surface_antoine_telemetry(
    species: str,
    wall_temperature_C: float,
    *,
    vapor_pressure_data: Mapping[str, Any] | None = None,
    antoine_extrapolations: MutableMapping[str, Dict[str, Any]] | None = None,
    antoine_extrapolation_warnings: list[str] | None = None,
) -> None:
    if (
        antoine_extrapolations is None
        and antoine_extrapolation_warnings is None
    ):
        return
    _try_antoine_psat_pa(
        species,
        wall_temperature_C + CELSIUS_TO_KELVIN_OFFSET,
        vapor_pressure_data=vapor_pressure_data,
        antoine_extrapolations=antoine_extrapolations,
        antoine_extrapolation_warnings=antoine_extrapolation_warnings,
    )


def _local_wall_species_pressure_pa(
    species: str,
    melt_temperature_C: float,
    fallback_T_cond_C: float,
    *,
    vapor_pressure_data: Mapping[str, Any] | None = None,
    antoine_extrapolations: MutableMapping[str, Dict[str, Any]] | None = None,
    antoine_extrapolation_warnings: list[str] | None = None,
) -> float:
    P_source_pa, refused = _try_antoine_psat_pa(
        species,
        melt_temperature_C + CELSIUS_TO_KELVIN_OFFSET,
        vapor_pressure_data=vapor_pressure_data,
        antoine_extrapolations=antoine_extrapolations,
        antoine_extrapolation_warnings=antoine_extrapolation_warnings,
    )
    if refused:
        return 0.0
    if P_source_pa is not None and P_source_pa > 0.0:
        return P_source_pa
    return _local_species_pressure_pa(
        species,
        fallback_T_cond_C,
        vapor_pressure_data=vapor_pressure_data,
        antoine_extrapolations=antoine_extrapolations,
        antoine_extrapolation_warnings=antoine_extrapolation_warnings,
    )


def _molecular_mass_kg_per_molecule(
    species: str,
    *,
    vapor_pressure_data: Mapping[str, Any] | None = None,
) -> float:
    data = _species_vapor_data(
        species,
        vapor_pressure_data=vapor_pressure_data,
    )
    value = data.get('molar_mass_g_mol') if isinstance(data, Mapping) else None
    if value is None:
        value = MOLAR_MASS.get(species)
    try:
        molar_mass_g_mol = float(value)
    except (TypeError, ValueError):
        molar_mass_g_mol = 50.0
    if not math.isfinite(molar_mass_g_mol) or molar_mass_g_mol <= 0.0:
        molar_mass_g_mol = 50.0
    return (molar_mass_g_mol / 1000.0) / AVOGADRO_MOL


def _hkl_impingement_flux_mol_m2_s(
    species: str,
    pressure_pa: float,
    T_K: float,
    *,
    vapor_pressure_data: Mapping[str, Any] | None = None,
) -> float:
    if (
        not math.isfinite(pressure_pa)
        or not math.isfinite(T_K)
        or pressure_pa <= 0.0
        or T_K <= 0.0
    ):
        return 0.0
    molecule_kg = _molecular_mass_kg_per_molecule(
        species,
        vapor_pressure_data=vapor_pressure_data,
    )
    denominator = math.sqrt(
        2.0 * math.pi * molecule_kg * BOLTZMANN_CONSTANT_J_K * T_K
    )
    if denominator <= 0.0:
        return 0.0
    return pressure_pa / denominator / AVOGADRO_MOL


def _wall_deposition_driving_pressure_pa(
    species: str,
    P_local_pa: float,
    T_surface_K: float,
    *,
    vapor_pressure_data: Mapping[str, Any] | None = None,
    reactive_product_backstop: bool = True,
    antoine_extrapolations: MutableMapping[str, Dict[str, Any]] | None = None,
    antoine_extrapolation_warnings: list[str] | None = None,
) -> float:
    try:
        local_pressure_pa = float(P_local_pa)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(local_pressure_pa) or local_pressure_pa <= 0.0:
        return 0.0

    (
        antoine_extrapolations,
        antoine_extrapolation_warnings,
    ) = _resolve_antoine_telemetry(
        antoine_extrapolations,
        antoine_extrapolation_warnings,
    )
    P_sat_pa, _ = _try_antoine_psat_pa(
        species,
        T_surface_K,
        vapor_pressure_data=vapor_pressure_data,
        antoine_extrapolations=antoine_extrapolations,
        antoine_extrapolation_warnings=antoine_extrapolation_warnings,
    )
    if P_sat_pa is None or not math.isfinite(P_sat_pa):
        return 0.0
    if reactive_product_backstop:
        reactivity_class = _sticking_reactivity_class(species)
        if reactivity_class == 'reactive':
            if species != 'SiO':
                raise ValueError(
                    'reactive wall-product backstop is C4b-authorized only '
                    f'for SiO, got {species!r}'
                )
            if P_sat_pa < local_pressure_pa:
                return max(0.0, local_pressure_pa - P_sat_pa)
            # Reactive deposits are less-volatile wall products, not the vapor
            # species. Today SiO uses the disproportionation-product limit
            # P_sat ~= 0; this explicit hook can grow a real product P_sat later.
            effective_product_psat_pa = 0.0
            return max(0.0, local_pressure_pa - effective_product_psat_pa)
    return max(0.0, local_pressure_pa - P_sat_pa)


def _hkl_surface_deposition_flux_mol_m2_s(
    species: str,
    P_local_pa: float,
    T_surface_K: float,
    alpha_s: float,
    regime_factor: float = 1.0,
    *,
    vapor_pressure_data: Mapping[str, Any] | None = None,
    reactive_product_backstop: bool = True,
    antoine_extrapolations: MutableMapping[str, Dict[str, Any]] | None = None,
    antoine_extrapolation_warnings: list[str] | None = None,
) -> float:
    driving_pressure_pa = _wall_deposition_driving_pressure_pa(
        species,
        P_local_pa,
        T_surface_K,
        vapor_pressure_data=vapor_pressure_data,
        reactive_product_backstop=reactive_product_backstop,
        antoine_extrapolations=antoine_extrapolations,
        antoine_extrapolation_warnings=antoine_extrapolation_warnings,
    )
    if driving_pressure_pa <= 0.0:
        return 0.0
    return (
        alpha_s
        * max(0.0, min(1.0, float(regime_factor)))
        * _hkl_impingement_flux_mol_m2_s(
            species,
            driving_pressure_pa,
            T_surface_K,
            vapor_pressure_data=vapor_pressure_data,
        )
    )


# Note: ``_viscous_mass_transfer_flux_mol_m2_s`` and
# ``_combined_deposition_flux_mol_m2_s`` (the v1 additive-blend
# pair from 0.5.0 → 0.5.1) were removed in 0.5.2 Phase B. They had
# zero remaining callers after both production call sites switched
# to the canonical Bird/Stewart/Lightfoot series-resistance form
# (``_series_resistance_deposition_flux_mol_m2_s`` below). Codex
# /code-review max-effort flagged the orphan helpers as
# maintenance-drift risk; removal keeps the viscous-MT physics in
# exactly one place. The legacy additive-blend physics is fully
# described in the 0.5.2 CHANGELOG entry + the docstring of
# ``_series_resistance_deposition_flux_mol_m2_s`` if a future
# investigator needs to reproduce the pre-Phase-B fluxes.


def _series_resistance_deposition_flux_mol_m2_s(
    species: str,
    P_local_pa: float,
    T_surface_K: float,
    alpha_s: float,
    pipe_diameter_m: float = DEFAULT_PIPE_DIAMETER_M,
    stir_factor: float = 1.0,
    regime_factor: float = 0.0,
    T_gas_K: float | None = None,
    overhead_pressure_pa: float | None = None,
    carrier_gas: str = DEFAULT_CARRIER_GAS,
    *,
    radial_stir_factor: float | None = None,
    vapor_pressure_data: Mapping[str, Any] | None = None,
    reactive_product_backstop: bool = True,
    antoine_extrapolations: MutableMapping[str, Dict[str, Any]] | None = None,
    antoine_extrapolation_warnings: list[str] | None = None,
) -> float:
    """Series-resistance deposition flux (Bird/Stewart/Lightfoot canonical
    form), regime-aware: ``1/k_total = 1/(α_s · k_HKL) + (1 − f) / k_MT``,
    where ``f = regime_factor = Kn/(Kn+0.01)`` weights the boundary-layer
    resistance OUT in free-molecular regime (no continuum boundary layer).

    Replaces the v1 *additive-blend*
    ``f·J_HKL + (1−f)·J_MT`` of 0.5.1's post-F3 viscous-regime model.
    The codex challenge against the v1 blend at C2A viscous regime correctly
    observed that:

      * the additive form let HKL contribute its absolute magnitude (which
        is hundreds of × the MT flux in viscous regime),
      * so even with the regime-factor weight of ~3×10⁻⁴ the resulting flux
        was dominated by HKL leakage (~95% of blended flux per the codex
        worked example),
      * which is wrong physics — in the viscous boundary-layer limit the
        rate-limiting step IS gas-phase diffusion through the boundary layer.

    The series-resistance form is the canonical mass-transfer composition:
    a flux must pass through both resistances sequentially, so the slower
    process limits the total. The ``(1 − f)`` factor on the MT term encodes
    that the boundary-layer resistance only exists when there IS a continuum
    boundary layer (viscous regime); in free-molecular regime molecules cross
    the gas ballistically and only HKL applies. Limits:

      * Free-molecular (Kn ≫ 0.01, f → 1): MT resistance → 0,
        ``1/k_total → 1/k_HKL``, ``J → J_HKL`` (correct: no boundary layer).
      * Viscous (Kn ≪ 0.01, f → 0): k_MT ≪ k_HKL, ``1/k_total → 1/k_MT``,
        ``J → J_MT`` (correct: boundary layer rate-limits).
      * Transition: smooth, with both resistances active. Operationally F3
        refuses pure transition regime, so callers don't actually evaluate
        deep in this band; the smoothness is a safety property, not a
        recipe regime.

    The canonical 0.5.3 path enhances the Sherwood number through the
    ``radial_stir_factor`` axis, which drives gas-side bulk-to-wall
    transport. At radial factor 1 (no radial stirring), Sh = 3.66 (laminar
    pipe asymptote, BSL Eq 14.4-9). The C2A default
    ``StirState(axial=6.0, radial=1.0)`` leaves Sh = 3.66; operators must
    raise the radial axis to enhance k_MT. Legacy direct callers that pass
    only ``stir_factor=6`` are still treated as radial-equivalent and give
    Sh ≈ 9.0 for backward compatibility. See
    ``_stirring_enhanced_sherwood`` for the Frössling rationale.

    Returns 0 when there is no driving force (physisorber
    ``P_local <= P_sat`` at ``T_surface``; reactive species use the
    product-P_sat floor) or when the pipe geometry is degenerate.
    """
    # Defensive input validation: any non-finite or non-physical input
    # in this hot path silently propagates through the rate-coefficient
    # math and out the other side as NaN/inf fluxes that poison the
    # downstream ledger. Codex pre-0.5.2 Phase B P1 (NaN propagation +
    # regime_factor escape route). Fail closed at the gate.
    if pipe_diameter_m <= 0.0 or alpha_s <= 0.0:
        return 0.0
    if not (math.isfinite(pipe_diameter_m) and math.isfinite(alpha_s)
            and math.isfinite(P_local_pa) and math.isfinite(T_surface_K)):
        return 0.0
    driving_pressure_pa = _wall_deposition_driving_pressure_pa(
        species,
        P_local_pa,
        T_surface_K,
        vapor_pressure_data=vapor_pressure_data,
        reactive_product_backstop=reactive_product_backstop,
        antoine_extrapolations=antoine_extrapolations,
        antoine_extrapolation_warnings=antoine_extrapolation_warnings,
    )
    if driving_pressure_pa <= 0.0:
        return 0.0

    _t_gas_raw = float(T_gas_K) if T_gas_K is not None else T_surface_K
    if not math.isfinite(_t_gas_raw) or _t_gas_raw <= 0.0:
        return 0.0
    effective_T_gas_K = _t_gas_raw

    # Incoming HKL incidence is J_inc = p/sqrt(2*pi*m*k_B*T_gas), so the
    # kinetic gas temperature belongs in this coefficient. T_surface remains
    # in P_sat(T_surface) above; conflating the two overstates cold-wall arrival.
    # Extract the per-Pa coefficient by calling the unit-pressure helper.
    k_hkl_per_pa = alpha_s * _hkl_impingement_flux_mol_m2_s(
        species,
        1.0,
        effective_T_gas_K,
        vapor_pressure_data=vapor_pressure_data,
    )
    if not math.isfinite(k_hkl_per_pa) or k_hkl_per_pa <= 0.0:
        return 0.0

    # Sanitise ``regime_factor`` BEFORE computing mt_weight. NaN or
    # out-of-range values previously could route the helper into the
    # pure-HKL early-return branch even in viscous regime (codex
    # pre-0.5.2 Phase B P1): e.g. ``regime_factor=2.0`` made
    # ``1.0 - 2.0 = -1.0`` → ``max(0.0, -1.0) = 0.0`` → free-mol
    # shortcut fires and returns the unbounded HKL flux. Clamp to
    # ``[0.0, 1.0]`` and treat non-finite as viscous (``f=0.0``) so the
    # series-resistance branch carries.
    try:
        _f = float(regime_factor)
    except (TypeError, ValueError):
        _f = 0.0
    if not math.isfinite(_f):
        _f = 0.0
    _f = max(0.0, min(1.0, _f))
    mt_weight = 1.0 - _f

    # Free-molecular shortcut: when the regime weight collapses the MT
    # resistance to zero, the series reduces to pure HKL. Skip the MT
    # branch entirely (avoids spurious dependence on the legacy fallback
    # D_AB constant in tests that don't configure overhead pressure).
    if mt_weight <= 0.0:
        return k_hkl_per_pa * driving_pressure_pa

    # k_MT per Pa: Sh_eff × D_AB / (L_pipe × R × T_gas). T_gas (bulk) sets
    # the ideal-gas denominator; T_surface (wall) sets the saturation
    # pressure (already consumed by the driving_pressure_pa above).
    # 0.5.3 Phase B: Sh enhancement reads the RADIAL stirring axis
    # (in-plane EM stirring drives gas-side bulk-to-wall transport).
    # Backward-compat: if the caller passes only the legacy positional
    # ``stir_factor`` (pre-Phase-B path, or a unit test exercising the
    # BSL Sh relation directly), the helper treats it as the radial
    # equivalent — see ``_stirring_enhanced_sherwood`` doc.
    sherwood_eff = _stirring_enhanced_sherwood(
        stir_factor,
        radial_stir_factor=radial_stir_factor,
    )
    if (overhead_pressure_pa is not None
            and math.isfinite(overhead_pressure_pa)
            and overhead_pressure_pa > 0.0):
        d_ab_m2_s = _chapman_enskog_d_ab_m2_s(
            species, effective_T_gas_K,
            float(overhead_pressure_pa),
            carrier=carrier_gas,
        )
        if not math.isfinite(d_ab_m2_s) or d_ab_m2_s <= 0.0:
            d_ab_m2_s = DEFAULT_BINARY_DIFFUSION_M2_S
    else:
        d_ab_m2_s = DEFAULT_BINARY_DIFFUSION_M2_S
    if not math.isfinite(d_ab_m2_s) or d_ab_m2_s <= 0.0:
        # MT diffusion coefficient invalid (species/carrier missing
        # from the LJ table AND the legacy fallback constant also
        # zeroed — pathological). In viscous regime where the
        # boundary-layer rate-limits, an invalid k_MT means INFINITE
        # boundary-layer resistance, not "HKL escapes the gate" — so
        # fail closed. Pure free-molecular (``mt_weight == 0``) already
        # returned the pure-HKL value above before reaching this
        # branch. Codex pre-0.5.2 Phase B P1+P2.
        return 0.0
    k_mt_per_pa = (
        sherwood_eff * d_ab_m2_s
        / (pipe_diameter_m * GAS_CONSTANT_J_MOL_K * effective_T_gas_K)
    )
    if not math.isfinite(k_mt_per_pa) or k_mt_per_pa <= 0.0:
        # Same fail-closed rationale.
        return 0.0

    # Series resistance with regime-weighted boundary-layer term. The
    # MT resistance ``1/k_MT`` is scaled DOWN by ``mt_weight`` (i.e.,
    # the apparent k_MT is INFLATED in free-molecular regime to make
    # the boundary-layer resistance negligible). At ``mt_weight=1``
    # (viscous) the form is pure series-resistance; at ``mt_weight=0``
    # (free-molecular, handled by the early return above) it would be
    # pure HKL.
    inv_k_total = 1.0 / k_hkl_per_pa + mt_weight / k_mt_per_pa
    if not math.isfinite(inv_k_total) or inv_k_total <= 0.0:
        return 0.0
    flux = driving_pressure_pa / inv_k_total
    if not math.isfinite(flux):
        # Belt-and-suspenders: a non-finite product (e.g., +inf / +inf
        # → NaN) still escapes the per-branch checks above on some
        # platforms. Fail closed at the exit.
        return 0.0
    return flux


def _mean_free_path_m(
    pressure_pa: float,
    T_K: float,
    molecular_diameter_m: float | None = None,
    carrier_gas: str = DEFAULT_CARRIER_GAS,
) -> float:
    if molecular_diameter_m is None:
        molecular_diameter_m = _carrier_collision_diameter_m(carrier_gas)
    if pressure_pa <= 0.0:
        return math.inf
    if T_K <= 0.0 or molecular_diameter_m <= 0.0:
        return 0.0
    denominator = (
        math.sqrt(2.0)
        * math.pi
        * molecular_diameter_m ** 2
        * pressure_pa
    )
    if denominator <= 0.0:
        return math.inf
    return BOLTZMANN_CONSTANT_J_K * T_K / denominator


def _knudsen_number(
    pressure_pa: float,
    T_K: float,
    characteristic_length_m: float,
    *,
    carrier_gas: str = DEFAULT_CARRIER_GAS,
    molecular_diameter_m: float | None = None,
) -> float:
    if characteristic_length_m <= 0.0:
        return math.inf
    return (
        _mean_free_path_m(
            pressure_pa,
            T_K,
            molecular_diameter_m=molecular_diameter_m,
            carrier_gas=carrier_gas,
        )
        / characteristic_length_m
    )


def minimum_pressure_mbar_for_knudsen(
    *,
    gas_temperature_C: float,
    pipe_diameter_m: float,
    pipe_segments: list[PipeSegment] | None = None,
    carrier_gas: str = DEFAULT_CARRIER_GAS,
    knudsen_ceiling: float = FREE_MOLECULAR_KNUDSEN_MIN,
) -> dict[str, Any]:
    """Return the total-pressure floor set by the controlling pipe diameter."""

    ceiling = float(knudsen_ceiling)
    if not math.isfinite(ceiling) or ceiling <= 0.0:
        raise ValueError('knudsen_ceiling must be finite and positive')
    fallback_diameter_m = require_lab_pipe_diameter(
        pipe_diameter_m,
        'pipe_diameter_m',
    )
    lengths = [('default_pipe', fallback_diameter_m)]
    for segment in pipe_segments or ():
        name = str(getattr(segment, 'name', 'default_pipe'))
        lengths.append((
            name,
            require_lab_pipe_diameter(
                getattr(segment, 'inner_diameter_m', fallback_diameter_m),
                f'{name}.inner_diameter_m',
            ),
        ))
    controlling_name, controlling_length_m = min(
        lengths,
        key=lambda item: item[1],
    )
    temperature_K = max(
        float(gas_temperature_C) + CELSIUS_TO_KELVIN_OFFSET,
        1.0,
    )
    collision = _carrier_collision_diameter_diagnostic(carrier_gas)
    collision_diameter_m = float(collision['carrier_collision_diameter_m'])

    # Premise: the typed refusal starts at Kn=lambda/L >= Kn_ceiling.
    # Algebra: lambda=k_B*T/(sqrt(2)*pi*d^2*P), hence
    # P_min=k_B*T/(sqrt(2)*pi*d^2*L*Kn_ceiling). Unit check: (J/K*K)/m^3
    # = N/m^2 = Pa; /100 gives mbar. Sanity: hotter gas or a smaller pipe
    # raises P_min, while a larger pipe lowers it.
    minimum_pressure_pa = (
        BOLTZMANN_CONSTANT_J_K
        * temperature_K
        / (
            math.sqrt(2.0)
            * math.pi
            * collision_diameter_m ** 2
            * controlling_length_m
            * ceiling
        )
    )
    return {
        'minimum_pressure_pa': minimum_pressure_pa,
        'minimum_pressure_mbar': minimum_pressure_pa / 100.0,
        'knudsen_ceiling': ceiling,
        'gas_temperature_C': float(gas_temperature_C),
        'carrier_gas': str(carrier_gas),
        'carrier_collision_diameter_m': collision_diameter_m,
        'controlling_segment': controlling_name,
        'controlling_characteristic_length_m': controlling_length_m,
        'formula': 'k_B*T/(sqrt(2)*pi*d^2*L*Kn_ceiling)',
    }


def _knudsen_regime_factor(knudsen_number: float) -> float:
    if not math.isfinite(knudsen_number):
        return 1.0
    if knudsen_number <= 0.0:
        return 0.0
    factor = knudsen_number / (knudsen_number + CONTINUUM_BUFFER_KN)
    return max(0.0, min(1.0, factor))


def classify_knudsen_regime(knudsen_number: float) -> KnudsenRegime:
    if not math.isfinite(knudsen_number):
        return KnudsenRegime.FREE_MOLECULAR
    if knudsen_number < VISCOUS_KNUDSEN_MAX:
        return KnudsenRegime.VISCOUS
    if knudsen_number < FREE_MOLECULAR_KNUDSEN_MIN:
        return KnudsenRegime.TRANSITIONAL
    return KnudsenRegime.FREE_MOLECULAR


def _invalid_pipe_diameter_diagnostic(
    exc: LabGeometryError,
    *,
    field: str,
    raw_value: Any,
    overhead_pressure_mbar: float,
    gas_temperature_C: float,
    regime_factor: float | None,
    segment_name: str | None = None,
) -> dict[str, Any]:
    try:
        pipe_diameter_m = _finite_or_none(float(raw_value))
    except (TypeError, ValueError):
        pipe_diameter_m = None
    try:
        diagnostic_regime_factor = (
            None if regime_factor is None else float(regime_factor)
        )
    except (TypeError, ValueError):
        diagnostic_regime_factor = None
    segment: dict[str, Any] | None = None
    if segment_name is not None:
        segment = {
            'name': segment_name,
            'reason_refused': INVALID_PIPE_DIAMETER_REASON,
            'characteristic_length_m': pipe_diameter_m,
        }
    return {
        'status': 'refused',
        'reason': INVALID_PIPE_DIAMETER_REASON,
        'reason_refused': INVALID_PIPE_DIAMETER_REASON,
        'detail': str(exc),
        'field': field,
        'regime': 'invalid',
        'knudsen_number': None,
        'mean_free_path_m': None,
        'overhead_pressure_mbar': max(0.0, float(overhead_pressure_mbar)),
        'gas_temperature_C': float(gas_temperature_C),
        'pipe_diameter_m': pipe_diameter_m,
        'regime_factor': diagnostic_regime_factor,
        'segments': [] if segment is None else [segment],
        'warnings': [
            'Pipe diameter is invalid; condensation routing refused.'
        ],
    }


def knudsen_regime_diagnostic(
    *,
    overhead_pressure_mbar: float,
    gas_temperature_C: float,
    pipe_diameter_m: float,
    pipe_segments: list[PipeSegment] | None = None,
    regime_factor: float | None = None,
    carrier_gas: str = DEFAULT_CARRIER_GAS,
) -> dict[str, Any]:
    pressure_pa = max(0.0, float(overhead_pressure_mbar)) * 100.0
    gas_temperature_K = max(
        float(gas_temperature_C) + CELSIUS_TO_KELVIN_OFFSET,
        1.0,
    )
    carrier_diagnostic = _carrier_collision_diameter_diagnostic(carrier_gas)
    carrier_collision_diameter_m = float(
        carrier_diagnostic['carrier_collision_diameter_m']
    )
    try:
        fallback_diameter_m = require_lab_pipe_diameter(
            pipe_diameter_m, 'pipe_diameter_m')
    except LabGeometryError as exc:
        return _invalid_pipe_diameter_diagnostic(
            exc,
            field='pipe_diameter_m',
            raw_value=pipe_diameter_m,
            overhead_pressure_mbar=overhead_pressure_mbar,
            gas_temperature_C=gas_temperature_C,
            regime_factor=regime_factor,
        )
    mean_free_path_m = _mean_free_path_m(
        pressure_pa,
        gas_temperature_K,
        molecular_diameter_m=carrier_collision_diameter_m,
        carrier_gas=carrier_gas,
    )

    segments: list[dict[str, Any]] = []
    source_segments = list(pipe_segments or ())
    if not source_segments:
        source_segments = [
            PipeSegment(
                name='default_pipe',
                upstream_stage='',
                downstream_stage='',
                wall_temperature_C=float(gas_temperature_C),
                length_m=0.0,
                inner_diameter_m=fallback_diameter_m,
            )
        ]

    worst_regime = KnudsenRegime.VISCOUS
    severity = {
        KnudsenRegime.VISCOUS: 0,
        KnudsenRegime.TRANSITIONAL: 1,
        KnudsenRegime.FREE_MOLECULAR: 2,
    }
    for segment in source_segments:
        raw_diameter_m = getattr(
            segment, 'inner_diameter_m', fallback_diameter_m)
        segment_name = str(getattr(segment, 'name', 'default_pipe'))
        try:
            diameter_m = require_lab_pipe_diameter(
                raw_diameter_m, f'{segment_name}.inner_diameter_m')
        except LabGeometryError as exc:
            return _invalid_pipe_diameter_diagnostic(
                exc,
                field=f'{segment_name}.inner_diameter_m',
                raw_value=raw_diameter_m,
                overhead_pressure_mbar=overhead_pressure_mbar,
                gas_temperature_C=gas_temperature_C,
                regime_factor=regime_factor,
                segment_name=segment_name,
            )
        knudsen_number = _knudsen_number(
            pressure_pa,
            gas_temperature_K,
            diameter_m,
            carrier_gas=carrier_gas,
            molecular_diameter_m=carrier_collision_diameter_m,
        )
        regime = classify_knudsen_regime(knudsen_number)
        if severity[regime] > severity[worst_regime]:
            worst_regime = regime
        segments.append({
            'name': segment_name,
            'knudsen_number': _finite_or_none(knudsen_number),
            'regime': regime.value,
            'characteristic_length_m': diameter_m,
            'regime_factor': _knudsen_regime_factor(knudsen_number),
        })

    global_knudsen_number = _knudsen_number(
        pressure_pa,
        gas_temperature_K,
        fallback_diameter_m,
        carrier_gas=carrier_gas,
        molecular_diameter_m=carrier_collision_diameter_m,
    )
    global_regime = classify_knudsen_regime(global_knudsen_number)
    if severity[global_regime] > severity[worst_regime]:
        worst_regime = global_regime

    warnings: list[str] = []
    carrier_warning = carrier_diagnostic.get('warning')
    if carrier_warning:
        warnings.append(str(carrier_warning))
    carrier_status_fields: dict[str, Any] = {}
    if carrier_diagnostic['carrier_gas_status'] == 'unsupported_carrier_fallback':
        carrier_status_fields = {
            'requested_carrier_gas': carrier_diagnostic['requested_carrier_gas'],
            'applied_carrier_gas': carrier_diagnostic['applied_carrier_gas'],
            'carrier_gas_status': carrier_diagnostic['carrier_gas_status'],
            'carrier_gas_reason': carrier_diagnostic['carrier_gas_reason'],
            'carrier_collision_diameter_source': (
                carrier_diagnostic['carrier_collision_diameter_source']
            ),
        }
    status = 'ok'
    reason = ''
    if worst_regime is KnudsenRegime.FREE_MOLECULAR:
        status = 'refused'
        reason = KNUDSEN_REFUSAL_REASON
        warnings.append(
            'Knudsen number is outside viscous-flow validity; '
            'condensation routing refused.'
        )
    elif worst_regime is KnudsenRegime.TRANSITIONAL:
        status = 'warning'
        reason = KNUDSEN_TRANSITION_REASON
        warnings.append(
            'Knudsen number is transitional; surface deposition carries '
            'extra uncertainty and uses the continuity correction.'
        )

    return {
        'status': status,
        'reason': reason,
        'regime': worst_regime.value,
        'knudsen_number': _finite_or_none(global_knudsen_number),
        'mean_free_path_m': _finite_or_none(mean_free_path_m),
        'overhead_pressure_mbar': max(0.0, float(overhead_pressure_mbar)),
        'gas_temperature_C': float(gas_temperature_C),
        'carrier_gas': str(carrier_gas),
        'carrier_collision_diameter_m': carrier_collision_diameter_m,
        **carrier_status_fields,
        'pipe_diameter_m': fallback_diameter_m,
        'regime_factor': (
            _knudsen_regime_factor(global_knudsen_number)
            if regime_factor is None else float(regime_factor)
        ),
        'segments': segments,
        'warnings': warnings,
    }


def _finite_or_none(value: float) -> float | None:
    value = float(value)
    return value if math.isfinite(value) else None


def _stage0_hot_wall_min_C_from_setpoints(
    setpoints: Mapping[str, Any] | None,
    *,
    default_C: float = DEFAULT_UPSTREAM_HOT_WALL_MIN_C,
) -> float:
    if not setpoints:
        return float(default_C)
    block = (
        (setpoints.get('condensation_train', {}) or {})
        .get('metals_train', {}) or {}
    )
    if not isinstance(block, Mapping):
        return float(default_C)
    stage0 = block.get('stage_0_hot_duct', {}) or {}
    if not isinstance(stage0, Mapping):
        return float(default_C)
    band = stage0.get('temp_range_C')
    if not isinstance(band, (list, tuple)) or not band:
        return float(default_C)
    try:
        low = float(band[0])
    except (TypeError, ValueError):
        return float(default_C)
    if not math.isfinite(low):
        return float(default_C)
    return low


def _campaign_requires_viscous_flow(campaign_name: str | None) -> bool:
    if campaign_name is None:
        return True
    name = str(campaign_name)
    if not name:
        return True
    known_campaigns = {phase.name for phase in CampaignPhase} | {
        'C0b_p_cleanup', 'C2A_continuous', 'C2A_staged', 'C3',
    }
    if name not in known_campaigns:
        raise ValueError(f'unknown campaign for viscous-flow policy: {name!r}')
    return name in {
        'C2A',
        'C2A_continuous',
        'C2A_STAGED',
        'C2A_staged',
    }


def _pressure_isolated_capture_budget_kg(
    species: str,
    rate_kg_hr: float,
    stages: list[CondensationStage],
    residence_time_s: Mapping[int, float],
    *,
    temps: Mapping[str, float] | None = None,
    vapor_pressure_data: Mapping[str, Any] | None = None,
    alpha_record_out: MutableMapping[str, Any] | None = None,
) -> float:
    """Hold total vapor removal fixed until Chunk C pressure coupling lands."""

    remaining_kg = max(0.0, rate_kg_hr)
    T_cond_C = _species_condensation_temperature_C(
        species,
        temps=temps,
        vapor_pressure_data=vapor_pressure_data,
    )
    alpha_record = _capture_budget_alpha_record(
        species,
        T_K=max(T_cond_C + CELSIUS_TO_KELVIN_OFFSET, 1.0),
    )
    stage_alpha_evaluations: dict[str, Any] = {}
    for stage in stages:
        if remaining_kg <= 1e-15:
            break
        if _cr_stage_isolation_blocks(stage, species):
            continue
        T_stage_K = _stage_midpoint_temperature_K(stage)
        alpha_context: dict[str, Any] = {
            'coefficient_spec': _sticking_species_entry(species).get('value'),
        }
        stage_alpha_s = _condensation_alpha_s(species, T_stage_K, alpha_context)
        stage_alpha_evaluations[str(stage.stage_number)] = dict(
            alpha_context.get('alpha_s_evaluation', {})
        )
        eta = _pressure_isolated_stage_efficiency(
            stage,
            T_cond_C,
            float(residence_time_s.get(stage.stage_number, 1.0)),
            stage_alpha_s,
        )
        remaining_kg -= remaining_kg * eta
    if stage_alpha_evaluations:
        alpha_record['alpha_s_stage_evaluations'] = stage_alpha_evaluations
        alpha_record['alpha_s_stage_extrapolated'] = any(
            bool(record.get('alpha_s_extrapolated'))
            for record in stage_alpha_evaluations.values()
            if isinstance(record, Mapping)
        )
    if alpha_record_out is not None:
        alpha_record_out.clear()
        alpha_record_out.update(alpha_record)
    return max(0.0, min(rate_kg_hr, rate_kg_hr - remaining_kg))


def _pressure_isolated_stage_efficiency(
    stage: CondensationStage,
    T_cond_C: float,
    residence_s: float,
    alpha_s: float,
) -> float:
    lo_C, hi_C = stage.temp_range_C
    values = (T_cond_C, residence_s, alpha_s, lo_C, hi_C)
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError('pressure-isolated capture inputs must be finite')
    if residence_s < 0.0 or not 0.0 <= alpha_s <= 1.0:
        raise ValueError('residence_s and alpha_s are outside physical bounds')
    if float(lo_C) > float(hi_C):
        raise ValueError('stage temperature bounds must be ordered low-to-high')
    if residence_s == 0.0 or alpha_s == 0.0:
        return 0.0
    T_stage_C = (float(lo_C) + float(hi_C)) / 2.0
    if T_stage_C >= T_cond_C:
        return 0.0
    delta_T = T_cond_C - T_stage_C
    normalized_drive = max(
        delta_T / max(T_cond_C, 1.0),
        CAPTURE_BUDGET_REGULARIZER_FLOOR,
    )
    # Premise: alpha_s and normalized_drive are dimensionless. Algebra:
    # tau_s = t_ref_s / (alpha_s * normalized_drive). Unit check:
    # s / 1 = s, so residence_s / tau_s is dimensionless. Sanity:
    # colder stages or higher sticking shorten tau; the explicit
    # one-second t_ref_s is surfaced as an uncertified numerical regularizer.
    tau_s = CAPTURE_BUDGET_REGULARIZER_TIME_S / (alpha_s * normalized_drive)
    eta = 1.0 - math.exp(-residence_s / tau_s)
    return max(0.0, min(1.0, eta))


def _cr_stage_isolation_blocks(stage: CondensationStage, species: str) -> bool:
    chromium_stage = 'CrO2' in stage.target_species
    designated_stage = designated_stage_number(species)
    if chromium_stage:
        return not is_designated_for_stage(species, stage.stage_number)
    if designated_stage == 2:
        return True
    return False


def _allocate_total_by_weights(
    total: float,
    weights: Mapping[str, float],
) -> Dict[str, float]:
    if total <= 0.0:
        return {}
    positive = [
        (str(name), float(weight))
        for name, weight in weights.items()
        if float(weight) > 0.0
    ]
    weight_total = sum(weight for _, weight in positive)
    if weight_total <= 0.0:
        return {}
    allocated: Dict[str, float] = {}
    running = 0.0
    for name, weight in positive[:-1]:
        value = float(total) * weight / weight_total
        allocated[name] = value
        running += value
    last_name = positive[-1][0]
    allocated[last_name] = max(0.0, float(total) - running)
    return allocated


def _wall_segment_account_fractions(
    wall_deposit_by_segment_species: Mapping[str, Mapping[str, float]],
    species: str,
    wall_deposit_kg: float,
    pipe_segments: list[PipeSegment],
) -> Dict[str, float]:
    if wall_deposit_kg <= 0.0:
        return {}
    segment_by_name = {segment.name: segment for segment in pipe_segments}
    fractions: Dict[str, float] = {}
    for segment_name, species_kg in wall_deposit_by_segment_species.items():
        segment_kg = float(species_kg.get(species, 0.0))
        if segment_kg <= 0.0:
            continue
        segment = segment_by_name.get(segment_name)
        if segment is None:
            continue
        fractions[segment.wall_deposit_account] = segment_kg / wall_deposit_kg
    return _allocate_total_by_weights(1.0, fractions)


def _segment_stage_number(stage_name: str) -> int | None:
    token = str(stage_name)
    if not token.startswith('stage_'):
        return None
    suffix = token.removeprefix('stage_')
    return int(suffix) if suffix.isdecimal() else None


def cold_spot_diagnostic(
    pipe_segments: list[PipeSegment],
    vapor_species_kg_hr: Mapping[str, float],
    *,
    margin_C: float = COLD_SPOT_MARGIN_C,
    upstream_hot_wall_min_C: float | None = DEFAULT_UPSTREAM_HOT_WALL_MIN_C,
    temps: Mapping[str, float] | None = None,
    vapor_pressure_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Flag pipe segments colder than a flowing species' landing threshold."""

    findings: list[dict[str, Any]] = []
    upstream_hot_wall_findings: list[dict[str, Any]] = []
    hot_wall_threshold_C = (
        None
        if upstream_hot_wall_min_C is None
        else float(upstream_hot_wall_min_C)
    )
    for species, raw_kg_hr in vapor_species_kg_hr.items():
        kg_hr = float(raw_kg_hr)
        if kg_hr <= 1e-15:
            continue
        target_stage_number = designated_stage_number(species)
        if target_stage_number is None:
            continue
        condensation_T_C = _species_condensation_temperature_C(
            species,
            temps=temps,
            vapor_pressure_data=vapor_pressure_data,
        )
        threshold_C = condensation_T_C - float(margin_C)
        for segment in pipe_segments:
            downstream_number = _segment_stage_number(segment.downstream_stage)
            if downstream_number is None:
                continue
            if downstream_number > target_stage_number:
                continue
            wall_T_C = float(segment.wall_temperature_C)
            if (
                hot_wall_threshold_C is not None
                and math.isfinite(hot_wall_threshold_C)
                and wall_T_C < hot_wall_threshold_C
            ):
                upstream_hot_wall_findings.append({
                    'segment': segment.name,
                    'account': segment.wall_deposit_account,
                    'species': str(species),
                    'kg_hr': kg_hr,
                    'wall_temperature_C': wall_T_C,
                    'upstream_hot_wall_min_C': hot_wall_threshold_C,
                    'target_stage_number': target_stage_number,
                    'warning': (
                        f'upstream hot-wall violation {segment.name}: '
                        f'{wall_T_C:.1f} C below {hot_wall_threshold_C:.1f} C '
                        f'before stage {target_stage_number}'
                    ),
                })
            if wall_T_C >= threshold_C:
                continue
            findings.append({
                'segment': segment.name,
                'account': segment.wall_deposit_account,
                'species': str(species),
                'kg_hr': kg_hr,
                'wall_temperature_C': wall_T_C,
                'condensation_temperature_C': condensation_T_C,
                'margin_C': float(margin_C),
                'target_stage_number': target_stage_number,
                'warning': (
                    f'cold spot {segment.name}: {species} sees '
                    f'{wall_T_C:.1f} C before stage {target_stage_number}; '
                    f'threshold {threshold_C:.1f} C'
                ),
            })

    warnings = [str(finding['warning']) for finding in findings]
    upstream_hot_wall_warnings = [
        str(finding['warning'])
        for finding in upstream_hot_wall_findings
    ]
    return {
        'has_cold_spot': bool(findings),
        'margin_C': float(margin_C),
        'warnings': warnings,
        'findings': findings,
        'has_upstream_hot_wall_violation': bool(upstream_hot_wall_findings),
        'upstream_hot_wall_min_C': hot_wall_threshold_C,
        'upstream_hot_wall_warnings': upstream_hot_wall_warnings,
        'upstream_hot_wall_findings': upstream_hot_wall_findings,
    }


def stage_purity_report(train: CondensationTrain) -> dict[str, dict[str, Any]]:
    """Classify each stage's accumulated product as designated or impurity."""

    report: dict[str, dict[str, Any]] = {}
    for stage in train.stages:
        stage_number = int(stage.stage_number)
        stage_key = STAGE_KEY_BY_NUMBER.get(
            stage_number, f'stage_{stage_number}')
        accepted_species = accepted_species_for_stage_number(stage_number)
        coproduct_species = coproduct_species_for_stage_number(stage_number)
        designated_species_kg: dict[str, float] = {}
        coproduct_species_kg: dict[str, float] = {}
        impurity_species_kg: dict[str, float] = {}
        for species, kg in sorted(stage.collected_kg.items()):
            kg = float(kg)
            if abs(kg) <= 1e-12:
                continue
            if species in coproduct_species:
                coproduct_species_kg[species] = kg
            elif is_designated_for_stage(species, stage_number):
                designated_species_kg[species] = kg
            else:
                impurity_species_kg[species] = kg

        designated_kg = (
            sum(designated_species_kg.values())
            + sum(coproduct_species_kg.values())
        )
        impurity_kg = sum(impurity_species_kg.values())
        total_kg = designated_kg + impurity_kg
        purity_fraction = 1.0 if total_kg <= 1e-12 else designated_kg / total_kg
        if purity_fraction > 0.95:
            verdict = 'PURE'
        elif purity_fraction >= 0.80:
            verdict = 'MIXED'
        else:
            verdict = 'CONTAMINATED'

        report[stage_key] = {
            'stage_number': stage_number,
            'label': stage.label,
            'accepted_species': sorted(accepted_species),
            'designated_species_kg': designated_species_kg,
            'coproduct_species_kg': coproduct_species_kg,
            'impurity_species_kg': impurity_species_kg,
            'designated_kg': designated_kg,
            'impurity_kg': impurity_kg,
            'total_kg': total_kg,
            'purity_fraction': purity_fraction,
            'verdict': verdict,
            'warning': (
                'non-designated condensate present'
                if impurity_kg > 1e-12 else ''
            ),
        }
    return report
