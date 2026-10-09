"""v2.1 scorer: comparison set, engine dispatch, Residual production.

Validity gates run before any numeric comparison. A refused quantity is
never priced as zero; absence is never a value. score_eligible is derived
exactly from SCHEMA-PROPOSAL-v2.1 §Validity gates and quantity-scoped
scoring. The engine list is the explicit SCORE_ENGINE_SET below — never
derived from resolve_backend.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from simulator.battery.enums import (
    QUANTITY_UNITS,
    AdmissionStatus,
    Authority,
    CellMaterial,
    CONDENSED_PHASES,
    EQUILIBRIUM_FIT_QUANTITIES,
    Engine,
    EvidenceClass,
    ExecutionState,
    FORMATION_QUANTITIES,
    KINETIC_YIELD_QUANTITIES,
    MEASURED_EVIDENCE,
    MELT_ACTIVITY_QUANTITIES,
    MetricOperation,
    MethodToken,
    NoticeKind,
    PerBasis,
    Phase,
    Polymorph,
    PURE_STANDARD_THERMO,
    Quantity,
    ReferenceStateConvention,
    Rail,
    RefusalReason,
    ResidualStatus,
    SourceRelation,
    UncertaintyKind,
    VAPORIZATION_ENTHALPIES,
    ValueKind,
    residual_status_token,
)
from simulator.battery.identity import (
    Identity,
    IdentityEqualKind,
    THERMOCHEMICAL_CALORIE_J,
    identity_equal,
    profile_for,
    quantity_token,
)
from simulator.battery.migrate import (
    REPO_ROOT,
    canonicalize_rail,
    iter_observation_store_paths,
    load_migrated_benches,
    load_migrated_store,
    load_yaml,
    observation_matches_source_tokens,
    path_matches_source_tokens,
    source_filter_tokens,
    to_plain,
)
from simulator.battery.records import (
    INITIAL_CHARGE_ONLY_PROXY_FLAG,
    SOURCE_INTERNALLY_INCONSISTENT_REASON_PREFIX,
    Bench,
    CandidateRequest,
    Composition,
    DecisionBand,
    EngineTrace,
    Evidence,
    Execution,
    Experiment,
    Located,
    Notice,
    Observation,
    Residual,
    ResidualNumeric,
    ResidualRefusal,
    State,
    Uncertainty,
    Value,
    Work,
    _is_source_internally_inconsistent,
    as_decimal,
    phase_token,
    polymorph_token,
    Species,
    StandardState,
    union_notices,
)
from simulator.battery.oxygen_balance import (
    IMCC_ENGINES,
    OXYGEN_BALANCE_EFFUSION_ENGINES,
    OXYGEN_BALANCE_NOTICE_PREFIX,
    has_own_engine_solved_oxygen_balance,
)
from simulator.battery.source_lineage import (
    coefficient_lineage_sources,
    openimcc_engine_identity_sources,
)
from simulator.battery.validate import (
    _observation_lineage,
    _pressure_blocking_notices,
    _resolve_coefficient_sources,
    _table_ids,
    _VAPOUR_EQUILIBRIUM,
)
from simulator.battery.validity import (
    GateOutcome,
    _partial_pressure_observations_by_experiment,
    comparison_method_cell_constant_cancels,
    run_validity_gates,
)
from simulator.reference_data.janaf import formula_composition

# Explicit closed set. openimcc is the only IMCC producer. Do not build this from
# resolve_backend, BATTERY_ENGINE_NAMES, or any probe of installed backends.
SCORE_ENGINE_SET: tuple[Engine, ...] = (
    Engine.VAPOROCK,
    Engine.ALPHAMELTS,
    Engine.THERMOENGINE,
    Engine.MAGEMIN,
    Engine.OPENIMCC,
    Engine.INTERNAL_ANALYTICAL,
)
MELTS_ENGINES: frozenset[Engine] = frozenset(
    {Engine.ALPHAMELTS, Engine.THERMOENGINE, Engine.MAGEMIN}
)
# These adapters consume the supplied composition as one homogeneous liquid.
# AlphaMELTS, ThermoEngine, and MAGEMin can resolve a liquid from a bulk input.
SINGLE_LIQUID_ENGINES: frozenset[Engine] = frozenset(
    {Engine.VAPOROCK, *IMCC_ENGINES, Engine.INTERNAL_ANALYTICAL}
)
TWO_PHASE_BULK_COMPOSITION_STATUS = "two_phase_bulk_composition_not_liquid_composition"
TWO_PHASE_BULK_COMPOSITION_PHASE_MARKER = "bulk_composition_in_two_phase_region"
# Engines that consume the SF04 magma companion workbook. Never score them
# against those rows (chunk-3 carried ruling).
SF04_WORKBOOK_SOURCE_ID = "sf04-magma-companion-workbook"
SF04_WORKBOOK_REGIME = "magma_model_companion_workbook"

INTERNAL_CONSISTENCY_STEMS: frozenset[str] = frozenset(
    {
        "gibbs_battery_residual_ledger.yaml",
        "species_rail_differential_ledger.yaml",
    }
)
COMPILATION_SOURCE_MARKERS: frozenset[str] = frozenset(
    {
        "janaf",
        "usgs",
        "usbm",
        "atct",
        "burcat",
        "nasa-glenn",
        "nasa-cea",
        "sgte",
        "nist-webbook",
        "nist-srd69",
        "nist-janaf",
    }
)
CIRCULARITY_WARNING = "Do not validate an engine against a compilation it consumes."

STORE_STAMP_KIND = "battery_store_stamp"
UNKNOWN_STORE_PROVENANCE_LINE = (
    "The measuring store for this ledger is unknown."
)
STORE_REVISION_PATHS: tuple[str, ...] = (
    "data/literature/observations-v2",
    "data/literature/extracts-v2",
    "data/literature/works",
    "data/battery/migration-report.md",
    "data/battery/migration-queue.yaml",
)
_MIGRATION_HEADLINE_FIELDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rows_in", re.compile(r"^rows in: (\d+)\s*$", re.M)),
    ("observations", re.compile(r"^records out \(observations\): (\d+)\s*$", re.M)),
    ("works", re.compile(r"^works: (\d+)\s*$", re.M)),
    ("experiments", re.compile(r"^experiments: (\d+)\s*$", re.M)),
    ("queue", re.compile(r"^queue size: (\d+)\s*$", re.M)),
    ("hard_issues", re.compile(r"^hard issues: (\d+)\s*$", re.M)),
)

REVIEW_PERMITS_USE: frozenset[str | None] = frozenset(
    {None, "draft", "reviewed", "adopted", "accepted"}
)
REVIEW_BLOCKS_USE: frozenset[str] = frozenset({"rejected", "withdrawn", "retired"})

# Quantity → metric operation. Derivation in comments at QUANTITY_METRIC.
# Premise: SCHEMA-PROPOSAL-v2.1 metrics {absolute, relative, dex}.
# Algebra: absolute = C−R in canonical unit; relative = (C−R)/|R| (R≠0);
# dex = log10(C/R) with both positive.
# Unit check: absolute carries QUANTITY_UNITS; relative and dex are
# dimensionless. Sanity: equal endpoints → 0 on every operation.
QUANTITY_METRIC: dict[Quantity, MetricOperation] = {
    Quantity.P_SAT: MetricOperation.DEX,
    Quantity.P_PARTIAL: MetricOperation.DEX,
    Quantity.P_REFERENCE: MetricOperation.DEX,
    Quantity.ACTIVITY: MetricOperation.DEX,
    Quantity.ACTIVITY_COEFFICIENT: MetricOperation.DEX,
    Quantity.DELTA_FG: MetricOperation.ABSOLUTE,
    Quantity.H_MINUS_H298: MetricOperation.ABSOLUTE,
    Quantity.PARTIAL_MOLAR_ENTHALPY: MetricOperation.ABSOLUTE,
    Quantity.ENTHALPY_OF_VAPORIZATION_2ND_LAW: MetricOperation.ABSOLUTE,
    Quantity.ENTHALPY_OF_VAPORIZATION_3RD_LAW: MetricOperation.ABSOLUTE,
    Quantity.CP: MetricOperation.ABSOLUTE,
    Quantity.S: MetricOperation.ABSOLUTE,
    Quantity.LOG10_KF: MetricOperation.ABSOLUTE,
    Quantity.LOG10_K_STAR: MetricOperation.ABSOLUTE,
    Quantity.EVAPORATION_COEFFICIENT_ALPHA: MetricOperation.ABSOLUTE,
    Quantity.MASS_LOSS_FRACTION: MetricOperation.RELATIVE,
    Quantity.MASS_LOSS_FRACTION_VS_T: MetricOperation.RELATIVE,
    Quantity.YIELD_FRACTION: MetricOperation.RELATIVE,
    Quantity.O2_YIELD: MetricOperation.RELATIVE,
    Quantity.EVAPORATION_RATE: MetricOperation.RELATIVE,
    Quantity.MASS_LOSS_RATE: MetricOperation.RELATIVE,
    Quantity.WALL_DEPOSIT_MASS: MetricOperation.RELATIVE,
    Quantity.TRANSITION_TEMPERATURE: MetricOperation.ABSOLUTE,
    Quantity.FE3_FE2_RATIO: MetricOperation.RELATIVE,
    Quantity.VISCOSITY: MetricOperation.DEX,
    Quantity.DENSITY: MetricOperation.RELATIVE,
    Quantity.ELECTRICAL_CONDUCTIVITY: MetricOperation.DEX,
    Quantity.ION_INTENSITY_RATIO: MetricOperation.DEX,
    Quantity.ION_INTENSITY: MetricOperation.RELATIVE,
    Quantity.INTERACTION_PARAMETER: MetricOperation.ABSOLUTE,
    Quantity.CONDENSATE_COMPOSITION: MetricOperation.ABSOLUTE,
    Quantity.LIQUIDUS_COMPOSITION: MetricOperation.ABSOLUTE,
    Quantity.EVOLVED_GAS_YIELD: MetricOperation.RELATIVE,
    Quantity.ISOTOPE_DELTA: MetricOperation.ABSOLUTE,
    Quantity.RESIDUE_COMPONENT_COMPOSITION: MetricOperation.ABSOLUTE,
}

# Legacy ΔfG bands from the residual ledger, retained as labeled fallbacks for
# comparisons without a compilation-derived band. The independent value is
# used for every relation: lineage labels do not select a decision threshold.
# Printed cell uncertainty and family residual bands supersede these values.
# Searched and not used as decision bands (do not invent a tolerance):
# - data/vapour_rail_validation_pins.yaml policy.margin_dex 0.01 is a pin /
#   regression envelope; SCHEMA-PROPOSAL-v2.1 forbids using a pin as the
#   agreement threshold.
# - validation-data/vapour_rail_sf04_high_t_DECISION.md 0.5 dex is an SF04
#   high-T species-disagreement check, "NOT evidence for a physical or
#   numerical ceiling".
# - openimcc's published-pack SHA-256 pins the package data, not an accuracy band.
# - engines/builtin/melt_effect_adjustment.py ±0.3 dex is a mixed-matte
#   error estimate, not an activity agreement band.
# - evaporation-α envelopes and the Robinot O2 error budget are value
#   ranges / lab diagnostics, not residual decision bands.
# - docs/lab-validation-whitepaper.md ~11% exp.1/exp.2 O2 floor
#   (|1.17−1.05|/1.11) is two runs of one apparatus at different heating
#   rates, not a sourced agreement band for o2_yield, yield_fraction, or
#   mass_loss_fraction. Using it would invent a yield tolerance.
THERMOCHEMISTRY_DECISION_BANDS: dict[SourceRelation, DecisionBand] = {
    SourceRelation.INDEPENDENT: DecisionBand(
        Decimal("1.0"),
        "kJ_per_declared_mol_basis",
        "LEGACY fallback: gibbs_battery_residual_ledger.yaml agreement_bands_kJ_mol.independent_tabulation",
    ),
    SourceRelation.SAME_INPUT: DecisionBand(
        Decimal("0.05"),
        "kJ_per_declared_mol_basis",
        "LEGACY historical only: gibbs_battery_residual_ledger.yaml agreement_bands_kJ_mol.engine_own_input",
    ),
    SourceRelation.TRAINING: DecisionBand(
        Decimal("0.05"),
        "kJ_per_declared_mol_basis",
        "LEGACY historical only: gibbs_battery_residual_ledger.yaml agreement_bands_kJ_mol.engine_own_input",
    ),
}

# Statistical floor for estimating a MAD, not a physics threshold.
MIN_DERIVED_BAND_N = 10

# Formation Gibbs energies stored in kJ/mol. Not log10_Kf (dimensionless), not
# cp/S (J/mol/K), not delta_fH or H-H298 (not sourced by this band).
GIBBS_BAND_QUANTITIES: frozenset[Quantity] = frozenset(
    {Quantity.DELTA_FG}
)
_UNIT_DIMENSION: dict[str, str] = {
    "kJ_per_declared_mol_basis": "energy_per_mol",
    "J_per_declared_mol_basis_per_K": "energy_per_mol_per_K",
    "dimensionless": "dimensionless",
}
_SIO_FORMULAS: frozenset[str] = frozenset({"SiO", "SiO2"})
_ALKALI_FORMULAS: frozenset[str] = frozenset(
    {"Na", "K", "NaO0.5", "KO0.5", "Na2O", "K2O"}
)
_NOT_VAPOUR_QUANTITIES: frozenset[Quantity] = frozenset(
    {
        Quantity.ISOTOPE_DELTA,
        Quantity.ION_INTENSITY_RATIO,
        Quantity.ION_INTENSITY,
    }
)

ENGINE_CHANNELS: dict[Engine, str] = {
    Engine.VAPOROCK: "vaporock",
    Engine.ALPHAMELTS: "alphamelts",
    Engine.THERMOENGINE: "thermoengine",
    Engine.MAGEMIN: "magemin",
    Engine.OPENIMCC: "openimcc",
    Engine.INTERNAL_ANALYTICAL: "internal-analytical",
}
ENGINE_COEFFICIENT_SOURCES: dict[Engine, tuple[str, ...]] = {
    Engine.VAPOROCK: ("vaporock",),
    Engine.ALPHAMELTS: ("alphamelts",),
    Engine.THERMOENGINE: ("thermoengine",),
    Engine.MAGEMIN: ("magemin",),
    Engine.OPENIMCC: ("openimcc-v1.0.2",),
    Engine.INTERNAL_ANALYTICAL: ("antoine_sidecar", "ellingham"),
}
# Engine-facing aliases → work/source/table identities in the v2.1 store.
# Unmapped aliases (MELTS calibration DBs) stay incomplete; do not invent
# a work. VapoRock/Ellingham consume JANAF; Antoine sidecar is NIST WebBook;
# IMCC consumes the SF04 magma companion workbook.
COEFFICIENT_SOURCE_STORE_IDS: dict[str, tuple[str, ...]] = {
    "vaporock": ("janaf-4th",),
    "antoine_sidecar": ("nist-webbook",),
    # JANAF-4th is the refit for the oxide segments that cite Chase.
    # Zr, Rb, Cs, and P cite NASA CEA thermo.inp. nasa-glenn is that
    # database; nasa-cea-thermo is the extract it superseded. Pankratz
    # B677 and B689 are different bulletins from the B672 Mn rows and
    # are not coefficient sources.
    "ellingham": ("janaf-4th", "nasa-cea-thermo", "nasa-glenn"),
    "imcc-sf04-v1.0.2": ("sf04-magma-companion-workbook",),
    "imcc-sf04-ext-v4": ("sf04-magma-companion-workbook",),
    "openimcc-v1.0.2": ("sf04-magma-companion-workbook",),
}
_ENGINE_SOURCE_ALIASES: frozenset[str] = frozenset(
    alias for aliases in ENGINE_COEFFICIENT_SOURCES.values() for alias in aliases
)

# score_eligible conjuncts (v2.1 YAML score_eligible). Tests mutate each.
SCORE_ELIGIBLE_CONJUNCTS: tuple[str, ...] = (
    "status_match_or_mismatch",
    "finite_numeric_point_endpoints",
    "valid_metric_domain",
    "reference_measured_evidence",
    "admission_admitted",
    "extract_review_permits_use",
    "validity_gates_pass",
    "identity_equal",
    "source_relation_independent_complete_ancestry",
    "candidate_engine_prediction_authority_allowed",
    "no_blocking_qualification",
    "selected_independent_lineage_level",
    "not_flagged_stratum",
)

FLAGGED_STRATUM_UNVERIFIED_APPARATUS = "unverified-apparatus"
FLAGGED_STRATUM_CALIBRATION_NOT_GROUNDED = "calibration-not-grounded"
FLAGGED_STRATUM_CELL_MATERIAL_INFERRED = "cell-material-inferred"
FLAGGED_STRATUM_CATALOGUE_COMPOSITION = "catalogue-composition"
FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT = "source-internally-inconsistent"
FLAGGED_STRATUM_IMCC_COMPLEX_SATURATION = "imcc_complex_saturation"
FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION = "reference_converted_via_fusion"
FLAGGED_STRATUM_FIGURE_ONLY = "figure_only"
FLAGGED_STRATUM_REACTIVE_CELL_NOT_MODELLED = "reactive-cell-not-modelled"
_FLAGGED_STRATUM_NOTICE_KINDS: frozenset[NoticeKind] = frozenset(
    {
        NoticeKind.UNVERIFIED_APPARATUS,
        NoticeKind.CELL_MATERIAL_INFERRED,
        NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG,
        NoticeKind.IMCC_COMPLEX_SATURATION,
        NoticeKind.FIGURE_ONLY,
        NoticeKind.REACTIVE_CELL_NOT_MODELLED,
    }
)


@dataclass(frozen=True)
class EnginePrediction:
    engine: Engine
    channel: str
    execution: Execution
    value: Decimal | None = None
    unit: str | None = None
    coefficient_basis: str | None = None
    authority: Authority | None = None
    notices: tuple[Notice, ...] = ()
    coefficient_sources: tuple[str, ...] = ()
    lineage_complete: bool = False
    certified_band: Mapping[str, tuple[Decimal, Decimal]] | None = None
    requested_composition: State[Composition] | None = None
    refusal_reason: RefusalReason | None = None
    refusal_detail: Mapping[str, object] = field(default_factory=dict)
    identity: Identity | None = None
    version: str | None = None


@dataclass(frozen=True)
class ScoreContext:
    works: Mapping[str, Work]
    experiments: Mapping[str, Experiment]
    observations: Mapping[str, Observation]
    origins: Mapping[str, str] = field(default_factory=dict)
    extract_review: Mapping[str, str | None] = field(default_factory=dict)
    hostname: str = ""
    benches: Mapping[str, Bench] = field(default_factory=dict)


@dataclass(frozen=True)
class EligibleConjuncts:
    """Named score_eligible inputs so tests can mutate one conjunct at a time."""

    status: ResidualStatus
    finite_numeric_point_endpoints: bool
    valid_metric_domain: bool
    reference_measured_evidence: bool
    admission_admitted: bool
    extract_review_permits_use: bool
    validity_gates_pass: bool
    identity_equal: bool
    source_relation_independent_complete_ancestry: bool
    candidate_engine_prediction_authority_allowed: bool
    no_blocking_qualification: bool
    selected_independent_lineage_level: bool
    not_flagged_stratum: bool = True

    def as_mapping(self) -> dict[str, bool]:
        return {
            "status_match_or_mismatch": self.status in {
                ResidualStatus.MATCH,
                ResidualStatus.MISMATCH,
                ResidualStatus.NO_BAND,
            },
            "finite_numeric_point_endpoints": self.finite_numeric_point_endpoints,
            "valid_metric_domain": self.valid_metric_domain,
            "reference_measured_evidence": self.reference_measured_evidence,
            "admission_admitted": self.admission_admitted,
            "extract_review_permits_use": self.extract_review_permits_use,
            "validity_gates_pass": self.validity_gates_pass,
            "identity_equal": self.identity_equal,
            "source_relation_independent_complete_ancestry": (
                self.source_relation_independent_complete_ancestry
            ),
            "candidate_engine_prediction_authority_allowed": (
                self.candidate_engine_prediction_authority_allowed
            ),
            "no_blocking_qualification": self.no_blocking_qualification,
            "selected_independent_lineage_level": self.selected_independent_lineage_level,
            "not_flagged_stratum": self.not_flagged_stratum,
        }

    def eligible(self) -> bool:
        return all(self.as_mapping().values())

    def exclusions(self) -> tuple[str, ...]:
        return tuple(name for name, ok in self.as_mapping().items() if not ok)


_RETIRED_SCORE_ENGINE_NAMES = frozenset({"imcc_sf04", "imcc_sf04_ext"})


def parse_engine(name: str) -> Engine:
    value = str(name).strip()
    if value in _RETIRED_SCORE_ENGINE_NAMES:
        raise ValueError(
            f"engine {value!r} is retired; use {Engine.OPENIMCC.value!r}"
        )
    return Engine(value)
def _require_score_engine(engine: Engine) -> None:
    if engine not in SCORE_ENGINE_SET:
        raise ValueError(
            f"engine {engine.value!r} is not in the explicit SCORE_ENGINE_SET"
        )


def _validated_score_engines(engines: Sequence[Engine]) -> tuple[Engine, ...]:
    out: list[Engine] = []
    seen: set[Engine] = set()
    for engine in engines:
        _require_score_engine(engine)
        if engine not in seen:
            seen.add(engine)
            out.append(engine)
    return tuple(out)


def engines_from_names(names: Sequence[str] | None) -> tuple[Engine, ...]:
    if not names:
        return SCORE_ENGINE_SET
    out: list[Engine] = []
    seen: set[Engine] = set()
    for raw in names:
        engine = parse_engine(raw)
        _require_score_engine(engine)
        if engine not in seen:
            seen.add(engine)
            out.append(engine)
    return tuple(out)


def parse_species_formula(formula: str) -> tuple[tuple[str, float], ...] | None:
    """Closed element-symbol parse. None → unmatched, never string equality."""

    if not formula or formula.strip().lower() in {"unknown", "none", "n/a"}:
        return None
    return formula_composition(formula)


def _is_raw_element_label(formula: str) -> bool:
    """Na, K, Fe. Not an oxide, and not a label the formula parser rejects."""

    parsed = parse_species_formula(formula)
    if parsed is None or len(parsed) != 1:
        return False
    element, count = parsed[0]
    return element != "O" and count == 1


def match_reported_species(
    formula: str,
    reported: Mapping[str, float],
    *,
    oxide_activity: bool = False,
) -> tuple[str, float] | None:
    """Closed-formula match. Oxide activity never accepts a raw element label.

    ``oxide_activity`` compares a parent-oxide formula to the canonical oxide
    map (``SiO2_Liq`` to ``SiO2``). A vapour row for elemental Na still matches
    ``Na`` when this flag is false.
    """

    target = parse_species_formula(formula)
    if target is None:
        return None
    if oxide_activity and _is_raw_element_label(formula):
        return None
    for name, value in reported.items():
        if oxide_activity and _is_raw_element_label(str(name)):
            continue
        parsed = parse_species_formula(str(name))
        if parsed == target:
            return str(name), float(value)
    if not oxide_activity:
        return None
    from engines.alphamelts.domain import canonical_oxide_activity_map

    for name, value in canonical_oxide_activity_map(reported).items():
        parsed = parse_species_formula(name)
        if parsed == target:
            return str(name), float(value)
    return None


def rail_for_quantity(quantity: Quantity | None, *, species_formula: str = "") -> Rail | None:
    """Headline rail, or None when the quantity is not on one.

    Vapour is only p_sat, p_partial, or p_reference (SiO/SiO2 partial
    pressures stay on SiO_evolution). Isotope ratios, ion ratios, and an
    unknown quantity do not fall through onto vapour. A non-alkali kinetic
    quantity (Zn, Cu, Mg evaporation coefficients, and the same else-branch)
    does not fall through onto SiO_evolution.
    """

    if quantity is Quantity.RESIDUE_COMPONENT_COMPOSITION:
        return Rail.RESIDUE_COMPOSITION
    if quantity in _VAPOUR_EQUILIBRIUM:
        if species_formula in _SIO_FORMULAS:
            return Rail.SIO_EVOLUTION
        return Rail.VAPOUR
    if quantity in MELT_ACTIVITY_QUANTITIES:
        return Rail.MELT_ACTIVITY
    if (
        quantity in FORMATION_QUANTITIES
        or quantity in EQUILIBRIUM_FIT_QUANTITIES
        or quantity in PURE_STANDARD_THERMO
        or quantity in VAPORIZATION_ENTHALPIES
    ):
        return Rail.THERMOCHEMISTRY
    if quantity is Quantity.WALL_DEPOSIT_MASS:
        return Rail.WALL_DEPOSITION
    if quantity is Quantity.FE3_FE2_RATIO:
        return Rail.REDOX
    if quantity in {
        Quantity.YIELD_FRACTION,
        Quantity.O2_YIELD,
        Quantity.MASS_LOSS_FRACTION,
        Quantity.MASS_LOSS_FRACTION_VS_T,
        Quantity.EVOLVED_GAS_YIELD,
    }:
        return Rail.PYROLYSIS_YIELD
    if quantity in KINETIC_YIELD_QUANTITIES:
        if species_formula in _SIO_FORMULAS:
            return Rail.SIO_EVOLUTION
        if species_formula in _ALKALI_FORMULAS:
            return Rail.ALKALI_SHUTTLE
        return None
    if quantity is Quantity.TRANSITION_TEMPERATURE:
        return Rail.THERMOCHEMISTRY
    return None


def no_headline_rail_reason(
    quantity: Quantity | None, *, species_formula: str = ""
) -> str:
    """Why rail_for_quantity returned None. Not a rail token."""

    del species_formula
    if quantity is None:
        return "quantity_unknown"
    if quantity in _NOT_VAPOUR_QUANTITIES:
        return "not_a_vapour_quantity"
    if quantity in KINETIC_YIELD_QUANTITIES:
        return "non_alkali_kinetic"
    return f"no_headline_rail:{quantity.value}"


def residual_key(
    *,
    reference_id: str,
    quantity: Quantity | None,
    engine: Engine,
    rail: Rail | None,
    temperature_K: Decimal | None = None,
) -> str:
    """Stable comparison slot: lineage + quantity + identity T + rail + engine.

    Run/version are excluded (v2.1 pin_key_map). No headline rail is the
    token ``none``, never a borrowed vapour or SiO label.
    """

    q = "quantity_unknown" if quantity is None else quantity.value
    t = "" if temperature_K is None else f":T={_dec_token(temperature_K)}"
    rail_token = "none" if rail is None else rail.value
    return f"{reference_id}{t}::{q}::{rail_token}::{engine.value}"


def _dec_token(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def metric_operation(quantity: Quantity | None) -> MetricOperation | None:
    if quantity is None:
        return None
    return QUANTITY_METRIC.get(quantity)


def metric_operation_for_identity(identity: Identity) -> MetricOperation | None:
    """Select the stored residue metric from its measured subtype."""
    quantity = quantity_token(identity)
    if quantity is not Quantity.RESIDUE_COMPONENT_COMPOSITION:
        return metric_operation(quantity)
    subtype = identity.subtype.value if identity.subtype.is_value else None
    if subtype == "element_ppm_by_mass":
        return MetricOperation.DEX
    if subtype == "oxide_wt_percent":
        return MetricOperation.ABSOLUTE
    return metric_operation(quantity)


def _residual_metric_operation(
    quantity: Quantity | None, observation: object | None
) -> MetricOperation | None:
    """Metric of a residual on ``observation``: identity-aware when typed."""

    identity = getattr(observation, "identity", None)
    if isinstance(identity, Identity) and quantity_token(identity) is quantity:
        return metric_operation_for_identity(identity)
    return metric_operation(quantity)


def compute_metric(
    operation: MetricOperation,
    candidate: Decimal,
    reference: Decimal,
) -> Decimal | None:
    """Return the metric or None when the domain is invalid.

    Premise: SCHEMA-PROPOSAL-v2.1 metrics block.
    Algebra:
      absolute = C − R
      relative = (C − R) / |R|  requiring R ≠ 0
      dex = log10(C / R) requiring C > 0 and R > 0
    Unit check: absolute inherits the quantity unit; relative and dex are
    dimensionless. Sanity: C = R → 0; R = 0 forbids relative (never price
    a zero reference as a successful relative residual); non-positive
    forbids dex (never take log10 of zero).
    """

    if not candidate.is_finite() or not reference.is_finite():
        return None
    if operation is MetricOperation.ABSOLUTE:
        return candidate - reference
    if operation is MetricOperation.RELATIVE:
        if reference == 0:
            return None
        return (candidate - reference) / abs(reference)
    if operation is MetricOperation.DEX:
        if candidate <= 0 or reference <= 0:
            return None
        ratio = candidate / reference
        # Decimal.ln / ln(10); math.log10 on float is the same operation
        # for the reported grain. Keep Decimal via log10 of the ratio.
        return Decimal(str(math.log10(float(ratio))))
    return None


def score_eligible_from_conjuncts(conjuncts: EligibleConjuncts) -> bool:
    return conjuncts.eligible()


def authority_allowed(quantity: Quantity | None, authority: Authority | None) -> bool:
    if authority is None or authority is Authority.REFUSED:
        return False
    # All three of certified/bridge/extrapolated are allowed for empirical
    # scoring; certification itself is a separate predicate.
    if authority in {Authority.CERTIFIED, Authority.BRIDGE, Authority.EXTRAPOLATED}:
        return True
    return False


def _origin_under_compilations(origin: str | None) -> bool:
    """True when the store path (relative) lives under a compilations-* payload."""

    if not origin:
        return False
    first = origin.replace("\\", "/").split("/", 1)[0]
    return first.startswith("compilations-")


def is_compilation_source(source_id: str | None, origin: str | None = None) -> bool:
    """Classify compilation rows by store path (or role), not source_id prefixes.

    Nested USGS shards live under ``compilations-<family>/<shard>.yaml``. Their
    ``source_id`` values (``robie-…``, ``hemingway-…``) do not match
    ``COMPILATION_SOURCE_MARKERS`` prefixes, so path membership is the gate.
    ``COMPILATION_SOURCE_MARKERS`` remains only as a legacy fallback when origin
    is unknown (synthetic / hand-built rows).
    """

    if _origin_under_compilations(origin):
        return True
    if not source_id:
        return False
    lowered = source_id.lower()
    if lowered.startswith("compilations-"):
        return True
    if origin:
        # Origin present but not under compilations-* → not a compilation by path.
        return False
    return any(lowered.startswith(marker) for marker in COMPILATION_SOURCE_MARKERS)


def is_internal_consistency(origin: str | None) -> bool:
    return bool(origin) and origin in INTERNAL_CONSISTENCY_STEMS


def is_sf04_workbook(observation: Observation) -> bool:
    if observation.source_id == SF04_WORKBOOK_SOURCE_ID:
        return True
    evidence = observation.evidence
    if evidence.original_method_class == SF04_WORKBOOK_REGIME:
        return True
    if evidence.model == SF04_WORKBOOK_REGIME:
        return True
    return False


def extract_review_permits(status: str | None) -> bool:
    if status in REVIEW_BLOCKS_USE:
        return False
    return status in REVIEW_PERMITS_USE


def point_magnitude(value: Value) -> Decimal | None:
    if value.kind is ValueKind.POINT and value.point is not None and value.point.is_finite():
        return value.point
    return None


def _catalogue_composition_notice(reference: Observation) -> Notice | None:
    identity = reference.identity
    if not isinstance(identity, Identity) or identity.composition is None:
        return None
    if not identity.composition.is_value or identity.composition.value is None:
        return None
    composition = identity.composition.value
    if composition.proxy_flag != "composition_from_sample_catalog":
        return None
    quantity = quantity_token(identity)
    if quantity is None:
        return None
    return Notice(
        kind=NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG,
        affected_quantities=(quantity,),
        reason=(
            "composition_from_sample_catalog: "
            f"source={composition.proxy_source}; "
            f"analysis_selection_rule={composition.analysis_selection_rule}"
        ),
        origin=reference.observation_id,
        source=composition.proxy_source,
    )


def _initial_charge_composition_notice(reference: Observation) -> Notice | None:
    """Predict with an initial-charge composition, then flag the residual.

    ``proxy_flag == initial_charge_only`` marks a printed starting charge
    standing in for a later (depleted) state. The residual joins the existing
    source-internally-inconsistent diagnostic stratum (b-716).
    """

    identity = reference.identity
    if not isinstance(identity, Identity):
        return None
    quantity = quantity_token(identity)
    if quantity is None:
        return None
    point = (reference.point_conditions or {}).get("composition")
    states = (identity.composition, point.state if isinstance(point, Located) else None)
    if not any(
        state is not None
        and state.is_value
        and isinstance(state.value, Composition)
        and state.value.proxy_flag == INITIAL_CHARGE_ONLY_PROXY_FLAG
        for state in states
    ):
        return None
    return Notice(
        kind=NoticeKind.SOURCE_DISAGREEMENT,
        affected_quantities=(quantity,),
        reason=(
            f"{SOURCE_INTERNALLY_INCONSISTENT_REASON_PREFIX} "
            f"composition_role={INITIAL_CHARGE_ONLY_PROXY_FLAG}: the printed "
            "composition is the starting charge, not the state of this datum"
        ),
        origin=reference.observation_id,
    )


def _missing_apparatus_fact(
    check: object,
    *,
    allow_calibration: bool,
    allow_unknown_method: bool,
) -> str | None:
    name = str(getattr(check, "name", ""))
    detail = getattr(check, "detail", {})
    if not isinstance(detail, Mapping):
        detail = {}
    if (
        name == "method"
        and not getattr(check, "passed", True)
        and allow_unknown_method
    ):
        return "method_unknown"
    if name == "kems_calibration" and allow_calibration:
        return "calibration"
    if name == "background_pressure_stated" and not getattr(check, "passed", True):
        return "background_pressure"
    if name == "orifice_knudsen" and not getattr(check, "passed", True):
        reason = str(detail.get("reason") or "").casefold()
        if "unknown" in reason or "not published" in reason or "missing" in reason:
            return "orifice_knudsen"
        return None
    if name == "background_pressure" and not getattr(check, "passed", True):
        # A stated high or straddling background is an explicit invalid input,
        # not an unverified apparatus fact.
        return None
    if name == "geometry_determinants" and allow_calibration:
        missing = detail.get("missing")
        if isinstance(missing, (list, tuple, set)) and set(missing) == {"calibration"}:
            return "calibration"
    return None


def _has_calibration_not_grounded(checks: Iterable[object]) -> bool:
    return any(
        getattr(check, "passed", True)
        and str(getattr(check, "name", ""))
        in {"kems_calibration", "in_cell_partial_pressure_sum"}
        and isinstance(getattr(check, "detail", {}), Mapping)
        and check.detail.get("flag") == "calibration_not_grounded"
        for check in checks
    )


def _unverified_apparatus_notices(
    reference: Observation,
    experiment: Experiment | None,
    gates: GateOutcome,
) -> tuple[Notice, ...]:
    """Return the narrow predict-and-flag admission for printed KEMS rows."""

    if experiment is None:
        return ()
    identity = reference.identity
    if not isinstance(identity, Identity):
        return ()
    quantity = quantity_token(identity)
    if quantity is None or point_magnitude(reference.value) is None:
        return ()
    if reference.admission.status is not AdmissionStatus.ADMITTED:
        return ()
    source_is_kems = bool(reference.source_id and reference.source_id.startswith("kems-"))
    author_reported_pressure = (
        source_is_kems
        and reference.evidence.original_method_class
        in {"measured", "measured_direct", "measured_tabulated"}
    )
    author_reported_activity = (
        (
            reference.evidence.class_.is_value
            and reference.evidence.class_.value in MEASURED_EVIDENCE
        )
        or reference.evidence.original_method_class
        in {
            "measured",
            "measured_direct",
            "measured_reduced",
            "measured_tabulated",
            "derived",
        }
    )
    reference_state = identity.reference_state
    typed_condensed_reference = (
        reference_state is not None
        and reference_state.is_value
        and isinstance(reference_state.value, StandardState)
        and phase_token(reference_state.value.endmember) in CONDENSED_PHASES
    )
    unknown_method_activity = (
        experiment.method.is_unknown
        and gates.reason is RefusalReason.METHOD_UNKNOWN
        and quantity in {Quantity.ACTIVITY, Quantity.ACTIVITY_COEFFICIENT}
        and author_reported_activity
        and typed_condensed_reference
    )
    if not unknown_method_activity and (
        not experiment.method.is_value
        or experiment.method.value is not MethodToken.KNUDSEN_EFFUSION
    ):
        return ()
    comparison_activity = (
        quantity in {Quantity.ACTIVITY, Quantity.ACTIVITY_COEFFICIENT}
        and author_reported_activity
        and comparison_method_cell_constant_cancels(reference.provenance)
    )
    measured_pressure = (
        quantity in {Quantity.P_SAT, Quantity.P_PARTIAL}
        and author_reported_pressure
        and reference.evidence.class_.is_value
        and reference.evidence.class_.value in MEASURED_EVIDENCE
    )
    calibration_flagged_partial_pressure = (
        quantity is Quantity.P_PARTIAL
        and reference.evidence.class_.is_value
        and reference.evidence.class_.value in MEASURED_EVIDENCE
        and _has_calibration_not_grounded(gates.checks)
    )
    calibration_flagged_activity = (
        quantity in {Quantity.ACTIVITY, Quantity.ACTIVITY_COEFFICIENT}
        and author_reported_activity
        and _has_calibration_not_grounded(gates.checks)
    )
    if not (
        measured_pressure
        or comparison_activity
        or unknown_method_activity
        or calibration_flagged_partial_pressure
        or calibration_flagged_activity
    ):
        return ()
    allow_calibration = (
        author_reported_pressure or comparison_activity or calibration_flagged_activity
    )
    missing: set[str] = set()
    calibration_notice = None
    calibration_reason = None
    for check in gates.checks:
        detail = getattr(check, "detail", {})
        if not isinstance(detail, Mapping):
            detail = {}
        if getattr(check, "passed", True):
            if (
                (allow_calibration or calibration_flagged_partial_pressure)
                and _has_calibration_not_grounded((check,))
            ):
                missing.add("calibration_not_grounded")
                notice_text = detail.get("calibration_notice")
                if isinstance(notice_text, str):
                    calibration_notice = notice_text
                    reason = detail.get("reason")
                    if isinstance(reason, str):
                        calibration_reason = reason
            continue
        fact = _missing_apparatus_fact(
            check,
            allow_calibration=allow_calibration,
            allow_unknown_method=unknown_method_activity,
        )
        if fact is None:
            return ()
        missing.add(fact)
    if not missing:
        return ()
    return tuple(
        Notice(
            kind=NoticeKind.UNVERIFIED_APPARATUS,
            affected_quantities=(quantity,),
            reason=(
                "calibration_not_grounded: "
                f"{calibration_notice or 'Calibration is not grounded.'}"
                + (
                    f" ({calibration_reason})"
                    if calibration_reason
                    else ""
                )
                if fact == "calibration_not_grounded"
                else (
                    "method_unknown: published activity has a typed condensed "
                    "reference but its experiment method is not stated"
                    if fact == "method_unknown"
                    else f"apparatus_unverified:{fact}"
                )
            ),
            origin=reference.observation_id,
        )
        for fact in sorted(missing)
    )


def _flagged_stratum_notices(
    reference: Observation,
    experiment: Experiment | None,
    gates: GateOutcome,
    bench: Bench | None = None,
) -> tuple[Notice, ...]:
    return union_notices(
        _unverified_apparatus_notices(reference, experiment, gates),
        _cell_apparatus_inference_notices(reference, experiment, bench),
        (() if (notice := _catalogue_composition_notice(reference)) is None else (notice,)),
        (
            ()
            if (notice := _initial_charge_composition_notice(reference)) is None
            else (notice,)
        ),
    )


def _cell_apparatus_inference_notices(
    reference: Observation,
    experiment: Experiment | None,
    bench: Bench | None,
) -> tuple[Notice, ...]:
    """Flag inferred cell materials used by the reactive-cell gate."""

    if experiment is None or not experiment.method.is_value:
        return ()
    if experiment.method.value is not MethodToken.KNUDSEN_EFFUSION:
        return ()
    quantity = (
        quantity_token(reference.identity)
        if isinstance(reference.identity, Identity)
        else None
    )
    if quantity is None:
        return ()
    fields: list[tuple[str, Located[Any]]] = []
    uses_cell_material = (
        quantity in _VAPOUR_EQUILIBRIUM
        and not _has_printed_fo2(reference, reference.identity)
    )
    if bench is not None and uses_cell_material:
        if bench.cell_material_and_liner is not None:
            fields.append(("bench.cell_material_and_liner", bench.cell_material_and_liner))
        fields.extend(
            (f"bench.cell_materials[{index}]", material)
            for index, material in enumerate(bench.cell_materials or ())
        )
    apparatus = experiment.apparatus
    if apparatus is not None and uses_cell_material:
        if apparatus.cell_material_and_liner is not None:
            fields.append(
                (
                    "experiment.apparatus.cell_material_and_liner",
                    apparatus.cell_material_and_liner,
                )
            )
    notices: list[Notice] = []
    for field_name, located in fields:
        if located.inference is None or not located.state.is_value:
            continue
        # These records are cell-material facts: a derivation on one says what
        # the cell was. A unit conversion or arithmetic normalization restates
        # a printed numeric quantity (such as orifice diameter) and is not a
        # material inference. Keep this semantic boundary here rather than
        # trying to enumerate derivation relation names.
        evidence = json.dumps(
            {
                "field": field_name,
                "inference": to_plain(located.inference),
                "locator": to_plain(located.locator),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        notices.append(
            Notice(
                kind=NoticeKind.CELL_MATERIAL_INFERRED,
                affected_quantities=(quantity,),
                reason=f"cell_material_inferred:{evidence}",
                origin=reference.observation_id,
            )
        )
    return tuple(notices)


def flagged_strata(notices: Sequence[Notice]) -> tuple[str, ...]:
    strata: list[str] = []
    kinds = {notice.kind for notice in notices}
    unverified_apparatus = tuple(
        notice
        for notice in notices
        if notice.kind is NoticeKind.UNVERIFIED_APPARATUS
    )
    if any(_is_calibration_not_grounded_reason(n.reason) for n in unverified_apparatus):
        strata.append(FLAGGED_STRATUM_CALIBRATION_NOT_GROUNDED)
    if any(
        not _is_calibration_not_grounded_reason(notice.reason)
        for notice in unverified_apparatus
    ):
        strata.append(FLAGGED_STRATUM_UNVERIFIED_APPARATUS)
    if NoticeKind.CELL_MATERIAL_INFERRED in kinds:
        strata.append(FLAGGED_STRATUM_CELL_MATERIAL_INFERRED)
    if NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG in kinds:
        strata.append(FLAGGED_STRATUM_CATALOGUE_COMPOSITION)
    if any(
        _is_source_internally_inconsistent(notice.kind, notice.reason)
        for notice in notices
    ):
        strata.append(FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT)
    if NoticeKind.IMCC_COMPLEX_SATURATION in kinds:
        strata.append(FLAGGED_STRATUM_IMCC_COMPLEX_SATURATION)
    if any(_is_fusion_conversion_notice(notice) for notice in notices):
        strata.append(FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION)
    if NoticeKind.FIGURE_ONLY in kinds:
        strata.append(FLAGGED_STRATUM_FIGURE_ONLY)
    if NoticeKind.REACTIVE_CELL_NOT_MODELLED in kinds:
        strata.append(FLAGGED_STRATUM_REACTIVE_CELL_NOT_MODELLED)
    return tuple(strata)


def _is_calibration_not_grounded_reason(reason: object) -> bool:
    return isinstance(reason, str) and reason.startswith("calibration_not_grounded:")


def _is_fusion_conversion_notice(notice: Notice) -> bool:
    return _is_fusion_conversion_reason(notice.reason)


def _is_fusion_conversion_reason(reason: object) -> bool:
    return isinstance(reason, str) and reason.startswith(
        f"{FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION};"
    )


def _is_flagged_stratum_notice(notice: Notice) -> bool:
    return (
        notice.kind in _FLAGGED_STRATUM_NOTICE_KINDS
        or _is_source_internally_inconsistent(notice.kind, notice.reason)
        or _is_fusion_conversion_notice(notice)
    )


@lru_cache(maxsize=1)
def _janaf_pure_solid_table_index() -> tuple[tuple[str, str], ...]:
    """Return the JANAF neutral crystal-table index by normalized formula."""

    from simulator.reference_data.janaf import load_manifest

    entries = load_manifest().get("entries")
    if not isinstance(entries, list):
        return ()
    return tuple(
        (str(entry["formula_normalised"]), str(entry["table_id"]))
        for entry in entries
        if isinstance(entry, Mapping)
        and entry.get("phase") == "cr"
        and entry.get("charge") == 0
        and isinstance(entry.get("formula_normalised"), str)
        and isinstance(entry.get("table_id"), str)
    )


@lru_cache(maxsize=None)
def _janaf_pure_solid_table_temperature_range(
    table_id: str,
) -> tuple[Decimal, Decimal] | None:
    from simulator.reference_data.janaf import TABLES_DIR, load_table_document

    document = load_table_document(TABLES_DIR / f"{table_id}.yaml")
    table = document.get("table")
    values = table.get("values") if isinstance(table, Mapping) else None
    if not isinstance(values, list):
        return None
    temperatures: list[Decimal] = []
    for row in values:
        if not isinstance(row, Mapping):
            continue
        temperature = row.get("temperature")
        gibbs = row.get("formation_gibbs_energy")
        if not isinstance(temperature, Mapping) or not isinstance(gibbs, Mapping):
            continue
        temperature_value = temperature.get("value")
        gibbs_value = gibbs.get("value")
        if temperature_value is None or gibbs_value is None:
            continue
        temperatures.append(Decimal(str(temperature_value)))
    if len(set(temperatures)) < 2:
        return None
    return min(temperatures), max(temperatures)


@lru_cache(maxsize=1)
def _b1259_tridymite_cristobalite_offset_rows() -> tuple[
    tuple[Decimal, Decimal, Decimal], ...
]:
    """Load common B1259 H/S rows as (T K, dH J/mol, dS J/mol/K)."""

    from simulator.reference_data.robie_waldbaum_1968_usgs_b1259_loader import (
        load_records,
    )

    wanted = {"b1259-ht-0113-cristobalite", "b1259-ht-0114-tridymite"}
    tables: dict[str, dict[Decimal, tuple[Decimal, Decimal]]] = {}
    for record in load_records(include_ocr_suspect=True):
        if record.get("record_id") not in wanted:
            continue
        rows: dict[Decimal, tuple[Decimal, Decimal]] = {}
        for row in record.get("rows", ()):
            if row.get("kind") != "data":
                continue
            temperature = row.get("temperature", {}).get("value")
            enthalpy = row.get("delta_f_H", {}).get("value")
            entropy = row.get("entropy", {}).get("value")
            if temperature is None or enthalpy is None or entropy is None:
                continue
            key = Decimal(str(temperature))
            # A duplicated T is a printed phase boundary, not a unique H/S
            # point for this polymorph comparison.
            if key in rows:
                rows.pop(key)
                continue
            # B1259 H is kcal/mol and S is cal/(mol K). Convert both to SI.
            rows[key] = (
                Decimal(str(enthalpy)) * Decimal(1000) * THERMOCHEMICAL_CALORIE_J,
                Decimal(str(entropy)) * THERMOCHEMICAL_CALORIE_J,
            )
        tables[str(record["record_id"])] = rows
    cristobalite = tables["b1259-ht-0113-cristobalite"]
    tridymite = tables["b1259-ht-0114-tridymite"]
    return tuple(
        (
            temperature,
            cristobalite[temperature][0] - tridymite[temperature][0],
            cristobalite[temperature][1] - tridymite[temperature][1],
        )
        for temperature in sorted(cristobalite.keys() & tridymite.keys())
    )


def _b1259_tridymite_cristobalite_delta_g(
    temperature_K: Decimal,
) -> tuple[Decimal, Decimal, Decimal, bool]:
    """Return ΔG(cristobalite−tridymite), certified bounds and extrapolation."""

    rows = _b1259_tridymite_cristobalite_offset_rows()
    lower, upper = rows[0][0], rows[-1][0]
    if temperature_K <= lower:
        edge = rows[0]
        extrapolated = temperature_K < lower
        delta_h, delta_s = edge[1], edge[2]
    elif temperature_K >= upper:
        edge = rows[-1]
        extrapolated = temperature_K > upper
        delta_h, delta_s = edge[1], edge[2]
    else:
        extrapolated = False
        for left, right in zip(rows, rows[1:]):
            if left[0] <= temperature_K <= right[0]:
                fraction = (temperature_K - left[0]) / (right[0] - left[0])
                delta_h = left[1] + fraction * (right[1] - left[1])
                delta_s = left[2] + fraction * (right[2] - left[2])
                break
        else:
            raise ValueError("B1259 common tridymite/cristobalite grid has a gap")

    # Premise: B1259's two SiO2 tables print formation H (kcal/mol) and S
    # (cal/(mol K)); the element reference cancels in their difference.
    # Thus ΔG_tr=G_cr−G_tr=ΔH_tr−TΔS_tr, interpolating H and S on their
    # common printed grid; beyond it use the edge ΔH/ΔS (dCp=0). At 2000 K,
    # dH=0.105 kcal/mol and dS=0.060 cal/(mol K), so dG=−15 cal/mol, matching
    # the printed ΔfG difference (−131.621−(−131.606) kcal/mol) to precision.
    # At the printed 1743 K tridymite→cristobalite transition the computed
    # difference is +5.94 J/mol (the two printed ΔfG values tie at precision),
    # and it is −12.55 J/mol at 1800 K, consistent with the transition crossing.
    # Units: kcal/mol×4184 J/kcal − K×cal/(mol K)×4.184 J/cal = J/mol.
    return delta_h - temperature_K * delta_s, lower, upper, extrapolated


def _fusion_comparison_reference(
    reference: Observation, *, engine: Engine | None = None
) -> Observation:
    """Return the liquid-reference comparison view for a solid activity row."""

    identity = reference.identity
    if (
        reference.admission.status is not AdmissionStatus.ADMITTED
        or not isinstance(identity, Identity)
        or quantity_token(identity) is not Quantity.ACTIVITY
        or identity.reference_state is None
        or not identity.reference_state.is_value
        or not isinstance(identity.reference_state.value, StandardState)
    ):
        return reference
    standard_state = identity.reference_state.value
    formula = standard_state.endmember.formula
    if (
        identity.species.formula != formula
        or standard_state.component_basis != formula
        or standard_state.convention is not ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
        or phase_token(standard_state.endmember) is not Phase.CR
        or reference.value.kind is not ValueKind.POINT
        or reference.value.point is None
        or reference.value.point <= 0
    ):
        return reference

    if engine is not None:
        from simulator.battery.waypoints import MELT_ACTIVITY_ENGINES

        if engine.value not in MELT_ACTIVITY_ENGINES:
            notice = Notice(
                kind=NoticeKind.OUT_OF_GAMMA_DOMAIN,
                affected_quantities=(Quantity.ACTIVITY,),
                reason=(
                    "solid/liquid reference conversion skipped: engine reference "
                    f"is unestablished for {engine.value}; measured "
                    "solid-reference value left unchanged"
                ),
                origin=reference.observation_id,
                band=f"engine activity reference unestablished: {engine.value}",
            )
            return replace(
                reference,
                notices=union_notices(reference.notices, (notice,)),
            )

    temperature_K = temperature_of(identity)
    if temperature_K is None:
        if engine is None:
            return reference
        notice = Notice(
            kind=NoticeKind.OUT_OF_GAMMA_DOMAIN,
            affected_quantities=(Quantity.ACTIVITY,),
            reason=(
                "fusion conversion missing input: activity observation has no "
                "temperature_K"
            ),
            origin=reference.observation_id,
            band="JANAF fusion conversion requires temperature_K",
        )
        return replace(reference, notices=union_notices(reference.notices, (notice,)))

    from simulator.battery.generators.janaf import (
        JANAF_R_J_PER_MOL_K,
        janaf_fusion_energy,
    )

    try:
        fusion = janaf_fusion_energy(formula, temperature_K)
    except ValueError as exc:
        reason = str(exc)
        if not any(
            expected in reason
            for expected in (
                f"no JANAF fusion table pair for {formula}",
                "JANAF crystal/liquid tables do not overlap",
                "expected one JANAF cr/l crossing",
                "outside the JANAF table range",
                "is not bracketed by JANAF table rows",
                "needed JANAF formation Gibbs row missing",
                "needed JANAF table node row missing or changed",
                "JANAF cached table is incomplete",
                "JANAF table values are missing",
                "JANAF table has fewer than two Gibbs points",
                "spans missing grid node",
            )
        ):
            raise
        notice = Notice(
            kind=NoticeKind.OUT_OF_GAMMA_DOMAIN,
            affected_quantities=(Quantity.ACTIVITY,),
            reason=f"fusion conversion missing input for {formula}: {reason}",
            origin=reference.observation_id,
            band=f"JANAF fusion data for {formula}: {reason}",
        )
        return replace(reference, notices=union_notices(reference.notices, (notice,)))

    # JANAF Mg-008 (MgO(cr), periclase) and Mg-009 (MgO(l)) give, by node
    # interpolation at 1873 K, G_s°=-345.91812 and G_l°=-317.44751 kJ/mol.
    # Thus ΔG_fus=+28.47061 kJ/mol and Δlog10(a)=ΔG_fus*1000/(R*T*ln(10))
    # = +0.793984 dex (R=8.31441 J mol^-1 K^-1). Their branches cross at
    # 3104.945598 K, where ΔG_fus and the reference shift go to zero.
    expected_polymorph = {
        "Ca-027": Polymorph.LIME,
        "Al-096": Polymorph.CORUNDUM,
        "Mg-008": Polymorph.PERICLASE,
        "O-035": Polymorph.CRISTOBALITE_HIGH,
    }.get(fusion.crystal_table)
    observed_polymorph = polymorph_token(standard_state.endmember)
    tridymite_to_cristobalite = (
        formula == "SiO2"
        and fusion.crystal_table == "O-035"
        and observed_polymorph is Polymorph.TRIDYMITE
    )
    source_polymorph = standard_state.endmember.polymorph
    source_polymorph_is_unknown = (
        source_polymorph is not None
        and source_polymorph.is_unknown
        and not (source_polymorph.reason or "").startswith("unrecognised polymorph ")
    )
    unique_unknown_polymorph_table = False
    if (
        expected_polymorph is not None
        and observed_polymorph is not expected_polymorph
        and source_polymorph_is_unknown
    ):
        eligible_solid_tables = tuple(
            table_id
            for candidate_formula, table_id in _janaf_pure_solid_table_index()
            if candidate_formula == formula
            and (
                table_range := _janaf_pure_solid_table_temperature_range(table_id)
            ) is not None
            and table_range[0] <= temperature_K <= table_range[1]
        )
        unique_unknown_polymorph_table = eligible_solid_tables == (
            fusion.crystal_table,
        )
    if (
        expected_polymorph is not None
        and observed_polymorph is not expected_polymorph
        and not tridymite_to_cristobalite
        and not unique_unknown_polymorph_table
    ):
        observed = "unknown" if observed_polymorph is None else observed_polymorph.value
        notice = Notice(
            kind=NoticeKind.OUT_OF_GAMMA_DOMAIN,
            affected_quantities=(Quantity.ACTIVITY,),
            reason=(
                "fusion conversion missing input: JANAF solid table "
                f"{fusion.crystal_table} represents polymorph "
                f"{expected_polymorph.value}, but measured reference polymorph "
                f"is {observed}"
            ),
            origin=reference.observation_id,
            band=(
                f"JANAF {fusion.crystal_table} requires "
                f"{expected_polymorph.value} solid reference"
            ),
        )
        return replace(reference, notices=union_notices(reference.notices, (notice,)))

    tridymite_notice: Notice | None = None
    tridymite_offset_dex = Decimal(0)
    if tridymite_to_cristobalite:
        delta_g_tr_J_per_mol, band_lo, band_hi, extrapolated = (
            _b1259_tridymite_cristobalite_delta_g(temperature_K)
        )
        # At equal chemical potential, G_cr + RT ln(a_cr) =
        # G_tr + RT ln(a_tr), so ln(a_cr/a_tr) = -ΔG_tr/(RT).
        tridymite_offset_dex = -delta_g_tr_J_per_mol / (
            JANAF_R_J_PER_MOL_K * temperature_K * Decimal(10).ln()
        )
        authority = Authority.EXTRAPOLATED if extrapolated else Authority.CERTIFIED
        band = f"B1259 common printed H/S band [{band_lo}, {band_hi}] K"
        original_reason = (
            "fusion conversion missing input: JANAF solid table O-035 represents "
            "polymorph cristobalite_high, but measured reference polymorph is tridymite"
        )
        tridymite_notice = Notice(
            kind=NoticeKind.DERIVATION_USES_COMPILATION,
            affected_quantities=(Quantity.ACTIVITY,),
            reason=(
                f"B1259 tridymite→cristobalite offset applied; {original_reason}; "
                f"DeltaG_cristobalite_minus_tridymite={delta_g_tr_J_per_mol} J/mol; "
                f"offset_dex={tridymite_offset_dex}; authority={authority.value}; "
                f"certified_band=[{band_lo}, {band_hi}] K"
            ),
            origin=reference.observation_id,
            band=band,
            authority=authority,
            certification=(
                "Robie & Waldbaum 1968, USGS Bulletin 1259 common printed H/S grid"
            ),
        )

    delta_g_fus_J_per_mol = fusion.delta_g_fus_kJ_per_mol * Decimal(1000)
    offset_dex = delta_g_fus_J_per_mol / (
        JANAF_R_J_PER_MOL_K * temperature_K * Decimal(10).ln()
    )
    liquid_endmember = replace(
        standard_state.endmember,
        phase=Phase.L,
        polymorph=None,
    )
    liquid_state = replace(
        standard_state,
        endmember=liquid_endmember,
    )
    comparison_identity = replace(
        identity, reference_state=State.of(liquid_state)
    )

    melts_notice: Notice | None = None
    if engine is not None and engine.value in {"alphamelts", "thermoengine"}:
        if formula == "SiO2":
            gap = (
                "measured MELTS/JANAF liquid-reference gap: |delta| <= 0.005 "
                "dex over 1600–2300 K"
            )
        elif formula == "Al2O3":
            gap = (
                "measured MELTS/JANAF liquid-reference gap: the JANAF fusion "
                "shift under-corrects by +0.03 dex at 1933 K, up to +0.11 dex "
                "at 1600 K, and 0 dex at 2300 K; not computed below 1600 K"
            )
        else:
            gap = None
        if gap is not None:
            melts_notice = Notice(
                kind=NoticeKind.DERIVATION_USES_COMPILATION,
                affected_quantities=(Quantity.ACTIVITY,),
                reason=(
                    "MELTS liquid-endmember reference differs from the JANAF "
                    f"liquid reference; applied shift is approximate; {gap}; "
                    "source=docs-private/research/2026-10-02-melts-vs-janaf-liquid/findings.md"
                ),
                origin=reference.observation_id,
                band="MELTS/JANAF liquid reference gap",
            )

    if temperature_K >= fusion.melting_temperature_K:
        if tridymite_to_cristobalite:
            cristobalite_endmember = replace(
                standard_state.endmember,
                phase=Phase.CR,
                polymorph=State.of(Polymorph.CRISTOBALITE_HIGH),
            )
            cristobalite_state = replace(
                standard_state,
                endmember=cristobalite_endmember,
            )
            comparison_identity = replace(
                identity,
                reference_state=State.of(cristobalite_state),
            )
            corrected_activity = reference.value.point * (
                tridymite_offset_dex * Decimal(10).ln()
            ).exp()
            notice = Notice(
                kind=NoticeKind.OUT_OF_GAMMA_DOMAIN,
                affected_quantities=(Quantity.ACTIVITY,),
                reason=(
                    f"solid reference recorded at T={temperature_K} K >= "
                    f"JANAF T_fus={fusion.melting_temperature_K} K; "
                    "JANAF liquid fusion shift not applied because liquid is "
                    "natural; B1259 tridymite-to-cristobalite shift applied"
                ),
                origin=reference.observation_id,
                band=(
                    f"JANAF fusion crossing {fusion.melting_temperature_K} K; "
                    f"tables={fusion.crystal_table}/{fusion.liquid_table}"
                ),
            )
            return replace(
                reference,
                identity=comparison_identity,
                value=replace(reference.value, point=corrected_activity),
                notices=union_notices(
                    reference.notices,
                    (notice, tridymite_notice),
                ),
            )
        notice = Notice(
            kind=NoticeKind.OUT_OF_GAMMA_DOMAIN,
            affected_quantities=(Quantity.ACTIVITY,),
            reason=(
                f"solid reference recorded at T={temperature_K} K >= "
                f"JANAF T_fus={fusion.melting_temperature_K} K; no numeric "
                "reference-state conversion applied because liquid is natural"
            ),
            origin=reference.observation_id,
            band=(
                f"JANAF fusion crossing {fusion.melting_temperature_K} K; "
                f"tables={fusion.crystal_table}/{fusion.liquid_table}"
            ),
        )
        notices = (notice,) if melts_notice is None else (notice, melts_notice)
        return replace(
            reference,
            identity=comparison_identity,
            notices=union_notices(reference.notices, notices),
        )

    # For the same chemical potential, mu = G° + RT ln(a) gives
    # log10(a_l/a_s) = -DeltaG_fus/(RT ln(10)); JANAF reports DeltaG_fus in
    # kJ/mol, so multiply by 1000 and use R=8.314462618 J/(mol*K). For CaO,
    # JANAF interpolation gives DeltaG_fus=33.80370 and 32.59870 kJ/mol at
    # 1823 and 1873 K, hence shifts -0.96857 and -0.90911 dex. Sanity check:
    # DeltaH_fus(1-T/Tm), with the JANAF crossing Tm=3200 K, gives 34.208 and
    # 32.966 kJ/mol at those temperatures.
    converted_activity = reference.value.point * (
        -delta_g_fus_J_per_mol / (JANAF_R_J_PER_MOL_K * temperature_K)
        + tridymite_offset_dex * Decimal(10).ln()
    ).exp()
    mismatch_K = fusion.melting_temperature_K - fusion.accepted_melting_temperature_K
    extrapolation_K = fusion.melting_temperature_K - temperature_K
    notice = Notice(
        kind=NoticeKind.DERIVATION_USES_COMPILATION,
        affected_quantities=(Quantity.ACTIVITY,),
        reason=(
            f"{FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION}; "
            f"{'source polymorph is unknown; ' if source_polymorph_is_unknown else ''}"
            "reference-state conversion (not model error); "
            f"oxide={formula}; source_activity_solid={reference.value.point}; "
            f"converted_activity_liquid={converted_activity}; "
            f"offset_dex=+{offset_dex}; "
            f"DeltaG_fus={fusion.delta_g_fus_kJ_per_mol} kJ/mol; T={temperature_K} K; "
            f"JANAF_Tm={fusion.melting_temperature_K} K; "
            f"distance_below_JANAF_Tm={extrapolation_K} K; "
            f"accepted_Tm~{fusion.accepted_melting_temperature_K} K; "
            f"JANAF_minus_accepted_Tm={mismatch_K} K; "
            "table/accepted melting-point mismatch adds uncertainty to the "
            f"metastable-liquid reference; tables={fusion.crystal_table}/"
            f"{fusion.liquid_table}; source_sha256={fusion.source_sha256[0]}/"
            f"{fusion.source_sha256[1]}"
        ),
        origin=reference.observation_id,
    )
    notices = (notice,) if melts_notice is None else (notice, melts_notice)
    if tridymite_notice is not None:
        notices += (tridymite_notice,)
    return replace(
        reference,
        identity=comparison_identity,
        value=replace(reference.value, point=converted_activity),
        evidence=replace(
            reference.evidence,
            class_=State.unknown(
                "activity converted from a solid to liquid reference with JANAF fusion Gibbs energy"
            ),
        ),
        notices=union_notices(reference.notices, notices),
    )


def temperature_of(identity: Identity) -> Decimal | None:
    state = identity.temperature_K
    if state is not None and state.is_value and state.value is not None:
        return as_decimal(state.value)
    return None


def composition_wt_pct(composition: Composition) -> dict[str, float] | None:
    """Convert an engine-basis composition to oxide wt%.

    Premise: Identity.composition is a complete ordered map on
    mol_inventory or mole_fraction. Equilibrate cells take oxide wt%.
    Algebra: m_i = n_i M_i; w_i = 100 m_i / Σ m_j.
    Unit check: (mol)×(g/mol) = g; g/g × 100 = wt%.
    Sanity: equal moles of SiO2 (60.084) and MgO (40.304) → ~59.9/40.1 wt%.
    """

    from simulator.diagnostic_helpers.binary_pot_scoring import oxide_molar_mass_g_mol

    masses: dict[str, float] = {}
    for oxide, amount in composition.components:
        parsed = parse_species_formula(oxide)
        if parsed is None:
            return None
        n = float(amount)
        if not math.isfinite(n) or n < 0.0:
            return None
        mass = n * oxide_molar_mass_g_mol(oxide)
        if not math.isfinite(mass) or mass < 0.0:
            return None
        masses[oxide] = mass
    total = sum(masses.values())
    if total <= 0.0:
        return None
    return {k: 100.0 * v / total for k, v in masses.items() if v > 0.0}


def blocking_qualifications(
    quantity: Quantity | None,
    notices: Sequence[Notice],
    *,
    oxygen_balance_effusion_solved: bool = False,
) -> tuple[Notice, ...]:
    flagged = tuple(
        notice for notice in notices if _is_flagged_stratum_notice(notice)
    )
    if flagged:
        return flagged
    if quantity in _VAPOUR_EQUILIBRIUM:
        return _pressure_blocking_notices(
            tuple(notices),
            oxygen_balance_effusion_solved=oxygen_balance_effusion_solved,
        )
    if quantity in MELT_ACTIVITY_QUANTITIES:
        found: list[Notice] = []
        for notice in notices:
            if notice.kind in {
                NoticeKind.FLOOR_INVERSION,
                NoticeKind.FALLBACK,
                NoticeKind.PRESSURE_PROVENANCE_UNKNOWN,
            } and any(q in MELT_ACTIVITY_QUANTITIES for q in notice.affected_quantities):
                found.append(notice)
        return tuple(found)
    return ()


def expand_coefficient_sources(sources: Sequence[str]) -> tuple[str, ...]:
    """Map engine coefficient aliases onto store work/source/table ids."""

    out: list[str] = []
    for src in sources:
        mapped = COEFFICIENT_SOURCE_STORE_IDS.get(src)
        if mapped is None:
            out.append(src)
        else:
            out.extend(mapped)
    return tuple(out)


def lineage_complete_for(
    sources: Sequence[str],
    *,
    works: Mapping[str, Work] | None = None,
    observations: Mapping[str, Observation] | None = None,
    experiments: Mapping[str, Experiment] | None = None,
) -> bool:
    """True when every engine alias is mapped and store ids resolve.

    Without a store, completeness is the mapping itself: unmapped MELTS
    aliases stay incomplete. With a store, ``_resolve_coefficient_sources``
    must return a set (unknown strings are not independence).
    """

    lineage_sources = coefficient_lineage_sources(sources)
    if not lineage_sources:
        return False
    for src in lineage_sources:
        if src in _ENGINE_SOURCE_ALIASES and src not in COEFFICIENT_SOURCE_STORE_IDS:
            return False
    expanded = expand_coefficient_sources(lineage_sources)
    if works is None or observations is None:
        return True
    return (
        _resolve_coefficient_sources(expanded, observations, works, experiments)
        is not None
    )


def resolve_source_relation(
    reference: Observation,
    coefficient_sources: Sequence[str],
    lineage_complete: bool,
    *,
    works: Mapping[str, Work],
    observations: Mapping[str, Observation],
    experiments: Mapping[str, Experiment],
) -> SourceRelation:
    if not lineage_complete:
        return SourceRelation.UNKNOWN
    table_ids = _table_ids(works)
    ref_ids = _observation_lineage(reference.observation_id, observations, table_ids)
    cand_ids = _resolve_coefficient_sources(
        coefficient_lineage_sources(coefficient_sources),
        observations,
        works,
        experiments,
    )
    if ref_ids is None or cand_ids is None:
        return SourceRelation.UNKNOWN
    if ref_ids & cand_ids:
        return SourceRelation.SAME_INPUT
    return SourceRelation.INDEPENDENT


def selected_lineage_level(
    observation: Observation,
    comparison_ids: set[str],
) -> bool:
    """True when no rawer parent in the comparison set is also selected."""

    for parent in observation.derived_from or ():
        if parent in comparison_ids:
            return False
    if observation.derivation is not None:
        for parent in observation.derivation.inputs:
            if parent in comparison_ids:
                return False
    return True


def _reference_has_measured_evidence(
    reference: Observation | None,
    *,
    exclusions: object = (),
) -> bool:
    if reference is not None:
        evidence = reference.evidence.class_
        return evidence.is_value and evidence.value in MEASURED_EVIDENCE
    if isinstance(exclusions, str):
        exclusion_tokens = {exclusions}
    elif isinstance(exclusions, Sequence):
        exclusion_tokens = set(exclusions)
    else:
        exclusion_tokens = set()
    return "reference_measured_evidence" not in exclusion_tokens


def build_conjuncts(
    *,
    status: ResidualStatus,
    reference: Observation,
    candidate: Observation | None,
    numeric: ResidualNumeric | None,
    source_relation: SourceRelation,
    lineage_complete: bool,
    gates: GateOutcome,
    extract_review_status: str | None,
    comparison_ids: set[str],
    notices: Sequence[Notice],
    oxygen_balance_effusion_solved: bool = False,
) -> EligibleConjuncts:
    quantity = quantity_token(reference.identity) if isinstance(reference.identity, Identity) else None
    ref_point = point_magnitude(reference.value)
    cand_point = None if candidate is None else point_magnitude(candidate.value)
    finite_points = ref_point is not None and cand_point is not None
    valid_domain = numeric is not None and numeric.value.is_finite()
    evidence_ok = _reference_has_measured_evidence(reference)
    identity_ok = False
    if (
        candidate is not None
        and isinstance(reference.identity, Identity)
        and isinstance(candidate.identity, Identity)
    ):
        identity_ok = identity_equal(reference.identity, candidate.identity).kind is IdentityEqualKind.EQUAL
    cand_ok = False
    if candidate is not None:
        cev = candidate.evidence.class_
        cand_ok = (
            cev.is_value
            and cev.value is EvidenceClass.ENGINE_PREDICTION
            and authority_allowed(quantity, candidate.authority)
        )
    independent = (
        source_relation is SourceRelation.INDEPENDENT and lineage_complete
    )
    return EligibleConjuncts(
        status=status,
        finite_numeric_point_endpoints=finite_points,
        valid_metric_domain=valid_domain,
        reference_measured_evidence=evidence_ok,
        admission_admitted=reference.admission.status is AdmissionStatus.ADMITTED,
        extract_review_permits_use=extract_review_permits(extract_review_status),
        validity_gates_pass=gates.passed,
        identity_equal=identity_ok,
        source_relation_independent_complete_ancestry=independent,
        candidate_engine_prediction_authority_allowed=cand_ok,
        no_blocking_qualification=not blocking_qualifications(
            quantity,
            notices,
            oxygen_balance_effusion_solved=oxygen_balance_effusion_solved,
        ),
        not_flagged_stratum=not any(
            _is_flagged_stratum_notice(notice) for notice in notices
        ),
        selected_independent_lineage_level=selected_lineage_level(
            reference, comparison_ids
        ),
    )


def unit_dimension(unit: str) -> str | None:
    """Physical dimension of a stored unit. Unknown units match nothing."""

    return _UNIT_DIMENSION.get(unit)


def band_dimension_matches(
    quantity: Quantity,
    band: DecisionBand,
    *,
    operation: MetricOperation | None = None,
) -> bool:
    """True only when the band and the quantity share one dimension.

    Residual metrics (relative and dex) are dimensionless even when their
    source quantity has a physical unit. kJ/mol does not match J/mol/K.
    Scale aliases that are not in ``_UNIT_DIMENSION`` fail closed.
    ``operation`` is the residual's own (identity-aware) metric when the
    caller knows it; default is the quantity's metric.
    """

    if band.unit == "dimensionless" and (
        operation or metric_operation(quantity)
    ) in {MetricOperation.RELATIVE, MetricOperation.DEX}:
        return True
    quantity_dim = unit_dimension(QUANTITY_UNITS[quantity])
    band_dim = unit_dimension(band.unit)
    return quantity_dim is not None and quantity_dim == band_dim


def pooled_log_pressure_sd(
    replicate_log_pressures: Sequence[Sequence[Decimal | int | float]],
) -> Decimal | None:
    """Return the pooled sample SD of replicate ``log10(p)`` values.

    Premise: rows in one replicate group share composition, temperature, and
    method, so their log-pressure spread estimates measurement scatter.
    Algebra: for group g, s²_g = Σ(x_g−x̄_g)²/(n_g−1); pool with
    s² = Σ(n_g−1)s²_g / Σ(n_g−1). Unit check: x = log10(p), so s is dex;
    no pressure unit remains. Sanity: groups ``(−1, 0, 1)`` and ``(9, 10,
    11)`` both have SD 1, hence the pooled result is 1 dex.
    """

    sum_squares = Decimal(0)
    degrees_of_freedom = 0
    for raw_group in replicate_log_pressures:
        group: list[Decimal] = []
        for raw_value in raw_group:
            try:
                value = as_decimal(raw_value)
            except (TypeError, ValueError, ArithmeticError):
                continue
            if value.is_finite():
                group.append(value)
        if len(group) < 2:
            continue
        mean = sum(group, Decimal(0)) / Decimal(len(group))
        sum_squares += sum((value - mean) ** 2 for value in group)
        degrees_of_freedom += len(group) - 1
    if degrees_of_freedom == 0:
        return None
    return (sum_squares / Decimal(degrees_of_freedom)).sqrt()


def _printed_pressure_uncertainty_dex(observation: Observation) -> Decimal | None:
    uncertainty = observation.uncertainty
    if uncertainty.kind is not UncertaintyKind.PRINTED:
        return None
    if uncertainty.value is not None and uncertainty.basis in {
        "relative",
        "fraction",
    }:
        raw = uncertainty.value
        if isinstance(raw, tuple):
            fraction = max(abs(raw[0]), abs(raw[1]))
        else:
            fraction = abs(raw)
        if fraction.is_finite() and fraction > 0:
            return (Decimal(1) + fraction).ln() / Decimal(10).ln()
    try:
        text = json.dumps(to_plain(uncertainty.verbatim), sort_keys=True)
    except (TypeError, ValueError):
        text = str(uncertainty.verbatim)
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", text, re.IGNORECASE):
        window = text[max(0, match.start() - 80) : min(len(text), match.end() + 80)]
        if not re.search(r"\b(?:k\s+)?pressure\b|\bp[_\s]?k\b", window, re.IGNORECASE):
            continue
        fraction = Decimal(match.group(1)) / Decimal(100)
        if fraction > 0:
            # The source prints a relative pressure envelope. Convert its
            # upper multiplicative edge to the dex residual metric; this is
            # the same ~0.15 dex reading used by the source's ±40% statement.
            return (Decimal(1) + fraction).ln() / Decimal(10).ln()
    return None


def _observation_flagged_strata(
    observation: Observation,
    experiments: Mapping[str, Experiment],
    point_observations_by_experiment: Mapping[str, tuple[Observation, ...]],
    benches: Mapping[str, Bench] | None = None,
) -> tuple[str, ...]:
    existing = flagged_strata(observation.notices)
    experiment = experiments.get(observation.experiment_id)
    if experiment is None:
        return existing
    gates = run_validity_gates(
        experiment,
        observation,
        point_observations=point_observations_by_experiment.get(
            observation.experiment_id, ()
        ),
    )
    bench = (
        None
        if experiment.bench_id is None or benches is None
        else benches.get(experiment.bench_id)
    )
    return tuple(
        dict.fromkeys(
            (
                *existing,
                *flagged_strata(
                    _flagged_stratum_notices(observation, experiment, gates, bench)
                ),
            )
        )
    )


def _kems_replicate_groups(
    observations: Mapping[str, Observation],
    experiments: Mapping[str, Experiment],
    benches: Mapping[str, Bench],
) -> tuple[tuple[Decimal, ...], ...]:
    groups: dict[tuple[object, ...], list[Decimal]] = {}
    point_observations_by_experiment = _partial_pressure_observations_by_experiment(
        observations.values()
    )
    for observation in observations.values():
        identity = observation.identity
        quantity = quantity_token(identity) if isinstance(identity, Identity) else None
        if quantity is not Quantity.P_PARTIAL or not isinstance(identity, Identity):
            continue
        if _observation_flagged_strata(
            observation, experiments, point_observations_by_experiment, benches
        ):
            continue
        evidence = observation.evidence.class_
        if not evidence.is_value or evidence.value not in MEASURED_EVIDENCE:
            continue
        experiment = experiments.get(observation.experiment_id)
        if (
            experiment is None
            or not experiment.method.is_value
            or experiment.method.value is not MethodToken.KNUDSEN_EFFUSION
            or observation.admission.status is not AdmissionStatus.ADMITTED
        ):
            continue
        temperature = temperature_of(identity)
        composition = identity.composition
        point = point_magnitude(observation.value)
        if (
            temperature is None
            or composition is None
            or not composition.is_value
            or composition.value is None
            or point is None
            or point <= 0
        ):
            continue
        key = (
            observation.experiment_id,
            identity.species.formula,
            str(temperature),
            tuple((name, str(amount)) for name, amount in composition.value.components),
        )
        groups.setdefault(key, []).append(
            point.ln() / Decimal(10).ln()
        )
    return tuple(tuple(values) for values in groups.values() if len(values) >= 2)


def derive_kems_partial_pressure_band(
    observations: Mapping[str, Observation],
    experiments: Mapping[str, Experiment],
    *,
    benches: Mapping[str, Bench] | None = None,
) -> DecisionBand | None:
    """Derive a band from measured rows and their optional bench context.

    Pass the bench map when a row's cell material is stored on its Bench. The
    private scorer path supplies the same map directly; a missing band never
    falls back to this helper from ``decision_band_for``.
    """
    return _derive_kems_partial_pressure_band(observations, experiments, benches or {})


def _derive_kems_partial_pressure_band(
    observations: Mapping[str, Observation],
    experiments: Mapping[str, Experiment],
    benches: Mapping[str, Bench],
) -> DecisionBand | None:
    """Derive one KEMS p_partial band from admitted measured observations.

    Printed pressure uncertainty is preferred. If a row has no usable printed
    pressure uncertainty, exact composition/T replicate groups supply the
    pooled log-pressure scatter; no engine residual enters this calculation.
    """

    candidates = []
    point_observations_by_experiment = _partial_pressure_observations_by_experiment(
        observations.values()
    )
    for observation in observations.values():
        identity = observation.identity
        quantity = quantity_token(identity) if isinstance(identity, Identity) else None
        if quantity is not Quantity.P_PARTIAL or not isinstance(identity, Identity):
            continue
        if _observation_flagged_strata(
            observation, experiments, point_observations_by_experiment, benches
        ):
            continue
        evidence = observation.evidence.class_
        if not evidence.is_value or evidence.value not in MEASURED_EVIDENCE:
            continue
        experiment = experiments.get(observation.experiment_id)
        if (
            experiment is None
            or not experiment.method.is_value
            or experiment.method.value is not MethodToken.KNUDSEN_EFFUSION
            or observation.admission.status is not AdmissionStatus.ADMITTED
            or rail_for_quantity(quantity, species_formula=identity.species.formula)
            is not Rail.VAPOUR
        ):
            continue
        candidates.append(observation)
    if not candidates:
        return None

    printed_observations = [
        (observation, width)
        for observation in candidates
        if (width := _printed_pressure_uncertainty_dex(observation)) is not None
    ]
    printed = [width for _, width in printed_observations]
    if printed:
        width = _rms(printed)
        if width is None or not width.is_finite() or width <= 0:
            return None
        if any(
            observation.source_id == "kems-042-plante-1979"
            for observation, _ in printed_observations
        ):
            rule = (
                "KEMS p_partial measured uncertainty: source-printed pressure "
                "envelope (RMS of admitted printed log10 pressure envelopes only); "
                "Plante width log10(1.40) from the printed page-280 1500 K "
                "sentence, using the upper multiplicative edge and accepting "
                "pressure ratios [1/1.40, 1.40] (-28.6%..+40%), not a symmetric "
                "+/-40% relative band; NOTICE: the single 1500 K figure is "
                "applied across the tabulated T range"
            )
        else:
            rule = (
                "KEMS p_partial measured uncertainty: source-printed pressure "
                "envelope (RMS of admitted printed log10 pressure envelopes only)"
            )
        return DecisionBand(width, "dimensionless", rule)

    replicate_scatter = pooled_log_pressure_sd(
        _kems_replicate_groups(observations, experiments, benches)
    )
    if replicate_scatter is None:
        return None
    width = replicate_scatter
    if width is None or not width.is_finite() or width <= 0:
        return None
    rule = (
        "KEMS p_partial measured uncertainty: replicate scatter (pooled replicate "
        "log10 pressure SD; used because no admitted printed pressure envelope "
        "exists)"
    )
    return DecisionBand(width, "dimensionless", rule)


def decision_band_for(
    quantity: Quantity | None,
    source_relation: SourceRelation,
    *,
    rail: Rail | None = None,
    method: MethodToken | None = None,
    observations: Mapping[str, Observation] | None = None,
    experiments: Mapping[str, Experiment] | None = None,
    derived_band: DecisionBand | None = None,
    operation: MetricOperation | None = None,
) -> DecisionBand | None:
    if (
        quantity is Quantity.P_PARTIAL
        and rail is Rail.VAPOUR
        and method is MethodToken.KNUDSEN_EFFUSION
        and observations is not None
        and experiments is not None
    ):
        # A None band is meaningful: the bench-aware scoring derivation found
        # no unflagged candidates.  Do not refill it with the public helper,
        # whose inputs cannot include the bench map.
        band = derived_band
        if band is not None and band_dimension_matches(
            quantity, band, operation=operation
        ):
            return band
    if derived_band is not None:
        if band_dimension_matches(quantity, derived_band, operation=operation):
            return derived_band
        return None
    if quantity not in GIBBS_BAND_QUANTITIES:
        return None
    band = THERMOCHEMISTRY_DECISION_BANDS[SourceRelation.INDEPENDENT]
    if band is None:
        return None
    if not band_dimension_matches(quantity, band):
        return None
    return band


def _mapped_reading_uncertainty_dex(
    uncertainty: Uncertainty,
) -> Decimal | None:
    """Dex width from mapped PRINTED verbatim or typed value/basis=dex.

    Extracts store reading uncertainty as a verbatim mapping with keys such as
    ``dex``, ``sigma_log10P_dex_per_point``, or nested
    ``figure_reading_estimate.log10_p_atm_absolute_uncertainty_dex``. A typed
    ``value`` with a dex ``basis`` is accepted the same way. Scalar PRINTED
    strings remain handled by ``_printed_uncertainty_band``.
    """

    if uncertainty.value is not None and uncertainty.basis:
        basis = uncertainty.basis.casefold()
        if "dex" in basis:
            raw = uncertainty.value
            if isinstance(raw, tuple):
                raw = abs(raw[1] - raw[0])
            try:
                width = abs(as_decimal(raw))
            except (TypeError, ValueError, ArithmeticError, InvalidOperation):
                width = None
            if width is not None and width.is_finite() and width > 0:
                return width

    verbatim = uncertainty.verbatim
    if isinstance(verbatim, Mapping):
        return _dex_width_from_mapping(verbatim)
    return None


def _dex_width_from_mapping(mapping: Mapping[str, object]) -> Decimal | None:
    keys = (
        "dex",
        "sigma_log10P_dex_per_point",
        "sigma_log10p_dex_per_point",
        "log10_p_atm_absolute_uncertainty_dex",
    )
    lower = {str(key).casefold(): value for key, value in mapping.items()}
    for key in keys:
        if key.casefold() not in lower:
            continue
        try:
            width = abs(as_decimal(lower[key.casefold()]))
        except (TypeError, ValueError, ArithmeticError, InvalidOperation):
            continue
        if width.is_finite() and width > 0:
            return width
    nested = mapping.get("figure_reading_estimate")
    if isinstance(nested, Mapping):
        return _dex_width_from_mapping(nested)
    return None


def _printed_uncertainty_band(
    quantity: Quantity,
    uncertainty: Uncertainty | None,
    reference: Decimal,
    *,
    source_observation: Observation | None = None,
) -> DecisionBand | None:
    """Convert a printed cell uncertainty to the scorer's metric unit.

    Accepts scalar PRINTED strings (``±30%``) and the mapped reading forms
    extracts actually store (``dex`` / ``sigma_log10P_dex_per_point`` / nested
    figure-reading dex), plus typed dex value+basis.
    """

    if uncertainty is None or uncertainty.kind is not UncertaintyKind.PRINTED:
        return None
    # The band must be in the residual's own metric: the same identity-aware
    # selector populate_numeric uses (residue element ppm is DEX, oxide wt%
    # ABSOLUTE), not the quantity default (b-693/b-691 r2 finding 4).
    operation = _residual_metric_operation(quantity, source_observation)
    mapped = _mapped_reading_uncertainty_dex(uncertainty)
    if mapped is not None:
        if operation is MetricOperation.DEX:
            return DecisionBand(
                mapped, "dimensionless", "source-printed per-cell uncertainty"
            )
        # Mapped dex widths are already in log10 space; only attach on DEX rails.
        return None
    if not isinstance(uncertainty.verbatim, str):
        return None
    match = re.fullmatch(
        r"\s*(?:(?:±|\+/-)\s*)?\(?([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\)?\s*(%)?\s*",
        uncertainty.verbatim,
    )
    if match is None:
        return None
    try:
        width = abs(Decimal(match.group(1)))
    except InvalidOperation:
        return None
    if not width.is_finite():
        return None

    if operation is None:
        return None
    percent = match.group(2) is not None
    if operation is MetricOperation.RELATIVE:
        if percent:
            width /= Decimal(100)
        elif reference != 0:
            width /= abs(reference)
        else:
            return None
        unit = "dimensionless"
    elif operation is MetricOperation.DEX:
        if reference <= 0:
            return None
        fraction = width / Decimal(100) if percent else width / reference
        if fraction >= 1:
            return None
        ln10 = Decimal(10).ln()
        width = max(
            abs((Decimal(1) + fraction).ln() / ln10),
            abs((Decimal(1) - fraction).ln() / ln10),
        )
        unit = "dimensionless"
    else:
        if percent:
            return None
        if source_observation is None:
            return None
        locator = source_observation.locator
        derivation = source_observation.derivation
        source_unit_match = (
            re.search(r"\bunit='([^']+)'", locator.note)
            if locator is not None and locator.note is not None
            else None
        )
        if (
            source_unit_match is None
            or derivation is None
            or derivation.output_unit != QUANTITY_UNITS[quantity]
        ):
            return None
        source_unit = source_unit_match.group(1)
        if quantity in {Quantity.DELTA_FG, Quantity.H_MINUS_H298}:
            source_scale = {
                "J/mol": Decimal("0.001"),
                "kJ/mol": Decimal(1),
                "kJ/mol at 298.15 K": Decimal(1),
                "kcal gfw^-1": Decimal("4.184"),
            }.get(source_unit)
        elif quantity in {Quantity.CP, Quantity.S}:
            source_scale = {
                "J/mol·K": Decimal(1),
                "cal deg^-1 gfw^-1": Decimal("4.184"),
            }.get(source_unit)
        elif quantity in {Quantity.LOG10_KF, Quantity.LOG10_K_STAR}:
            source_scale = Decimal(1) if source_unit == "dimensionless" else None
        else:
            source_scale = None
        if source_scale is None:
            return None
        width *= source_scale
        unit = QUANTITY_UNITS[quantity]
    return DecisionBand(width, unit, "source-printed per-cell uncertainty")


def _residual_distribution_band(
    values: Sequence[Decimal], *, unit: str, family: str, quantity: Quantity
) -> DecisionBand | None:
    """Twice the median absolute deviation for one family and quantity.

    Center at the sample median, then estimate MAD as the sample median of
    absolute deviations from that center (no normal-consistency multiplier).
    The band's width is twice that estimator; membership is tested against
    zero using ``abs(residual) <= width``.
    """

    if len(values) < MIN_DERIVED_BAND_N:
        return None
    center = _median(values)
    if center is None:
        return None
    mad = _median([abs(value - center) for value in values])
    if mad is None:
        return None
    return DecisionBand(
        Decimal(2) * mad,
        unit,
        f"{family}/{quantity.value} residual distribution: 2x median absolute deviation; derived_n={len(values)}",
    )


def populate_numeric(
    *,
    quantity: Quantity,
    candidate: Decimal,
    reference: Decimal,
    source_relation: SourceRelation,
    metric_uncertainty: Uncertainty | None = None,
    rail: Rail | None = None,
    method: MethodToken | None = None,
    observations: Mapping[str, Observation] | None = None,
    experiments: Mapping[str, Experiment] | None = None,
    derived_band: DecisionBand | None = None,
    operation_override: MetricOperation | None = None,
    band_operation: MetricOperation | None = None,
) -> tuple[ResidualNumeric | None, RefusalReason | None, dict[str, object]]:
    """``band_operation`` checks a row's OWN printed band against the row's
    identity-aware metric. It is None for every borrowed/derived band, which
    keeps the quantity-default dimension check (no envelope transfer).
    """
    operation = operation_override or metric_operation(quantity)
    if operation is None:
        return None, RefusalReason.METRIC_DOMAIN, {"quantity": quantity.value}
    unit = QUANTITY_UNITS[quantity]
    if operation in {MetricOperation.RELATIVE, MetricOperation.DEX}:
        unit = "dimensionless"
    value = compute_metric(operation, candidate, reference)
    if value is None:
        return None, RefusalReason.METRIC_DOMAIN, {
            "operation": operation.value,
            "candidate": str(candidate),
            "reference": str(reference),
        }
    if (
        rail is None
        and method is None
        and observations is None
        and experiments is None
    ):
        # Keep the narrow two-argument call for existing focused callers that
        # replace the band resolver while testing the dimension guard.
        band = decision_band_for(quantity, source_relation)
    else:
        band = decision_band_for(
            quantity,
            source_relation,
            rail=rail,
            method=method,
            observations=observations,
            experiments=experiments,
            derived_band=derived_band,
            operation=band_operation,
        )
    # Dimension guard. decision_band_for normally filters mismatched bands;
    # keep this check for callers that replace it in a focused test.
    if band is not None and not band_dimension_matches(
        quantity, band, operation=band_operation
    ):
        return None, RefusalReason.DECISION_RULE_MISSING, {
            "reason": f"band_dimension_mismatch:{quantity.value}",
            "quantity": quantity.value,
            "quantity_unit": QUANTITY_UNITS[quantity],
            "band_unit": band.unit,
            "operation": operation.value,
        }
    numeric = ResidualNumeric(
        operation=operation,
        unit=unit,
        value=value,
        decision_band=band,
        metric_uncertainty=metric_uncertainty,
    )
    return numeric, None, {}


def match_status(numeric: ResidualNumeric) -> ResidualStatus:
    if numeric.decision_band is None:
        return ResidualStatus.NO_BAND
    if abs(numeric.value) <= numeric.decision_band.value:
        return ResidualStatus.MATCH
    return ResidualStatus.MISMATCH


def qualification_battery_notice(
    quantity: Quantity,
    engine: Engine,
    gate: Mapping[str, object],
) -> Notice:
    band = gate.get("certified_band")
    return Notice(
        kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
        affected_quantities=(quantity,),
        reason=(
            str(gate.get("reason") or "MELTS qualification outside commissioning band")
        ),
        origin=f"engine:{engine.value}",
        band=None if band is None else str(band),
    )


def _row_out_of_certified_band(kind: str, row: Mapping[str, object]) -> bool:
    """Engine rows that are an out-of-band flag, not a new notice kind."""

    if kind in {"melts_domain_gate", "out_of_certified_band"}:
        return True
    if str(row.get("authority") or "") == "extrapolated":
        return True
    return (kind.startswith("imcc_") or kind.startswith("openimcc_")) and (
        "extrapolated" in kind or "outside" in kind
    )


def _interval_certified_band(
    band: object,
) -> dict[str, tuple[float, float]] | None:
    """Project an engine band onto EnginePrediction's interval-pair schema.

    Qualification bands also carry a crash floor and citations. Those are
    not intervals; copying them makes Observation reject the prediction.
    """

    if not isinstance(band, Mapping):
        return None
    out: dict[str, tuple[float, float]] = {}
    for key, value in band.items():
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            continue
        try:
            lo = float(value[0])
            hi = float(value[1])
        except (TypeError, ValueError):
            continue
        if math.isfinite(lo) and math.isfinite(hi):
            out[str(key)] = (lo, hi)
    return out or None


def _flags_from_scored_cell(
    cell: object,
    authority: Authority,
    certified_band: Mapping[str, tuple[float, float]] | None,
) -> tuple[Authority, Mapping[str, tuple[float, float]] | None]:
    """Upgrade a bridge default from the cell. Never downgrades a flag."""

    from simulator.diagnostic_helpers.binary_pot_battery import (
        AUTHORITY_EXTRAPOLATED,
        cell_score_authority,
    )

    if cell_score_authority(cell, refused=False) == AUTHORITY_EXTRAPOLATED:
        authority = Authority.EXTRAPOLATED
    if certified_band is None:
        band = _interval_certified_band(getattr(cell, "certified_band", None))
        if band is not None:
            certified_band = band
    return authority, certified_band


def cell_notices(
    quantity: Quantity,
    engine: Engine,
    cell: object,
) -> tuple[Notice, ...]:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        AUTHORITY_FALLBACK,
        _FLOOR_INVERSION_REASON,
    )

    notices: list[Notice] = []
    raw_notices = list(getattr(cell, "notices", None) or [])
    for row in raw_notices:
        if not isinstance(row, Mapping):
            continue
        kind = str(row.get("kind") or "")
        if _row_out_of_certified_band(kind, row):
            notices.append(
                Notice(
                    kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
                    affected_quantities=(quantity,),
                    reason=str(row.get("gate_reason") or row.get("reason") or kind),
                    origin=f"engine:{engine.value}",
                    band=str(row.get("certified_band") or row.get("band") or ""),
                )
            )
        elif kind == "floor_inversion":
            original = row.get("original")
            notices.append(
                Notice(
                    kind=NoticeKind.FLOOR_INVERSION,
                    affected_quantities=(quantity,),
                    reason=str(row.get("reason") or _FLOOR_INVERSION_REASON),
                    origin=f"engine:{engine.value}",
                    original=None if original is None else as_decimal(original),
                )
            )
        elif kind == "fallback":
            notices.append(
                Notice(
                    kind=NoticeKind.FALLBACK,
                    affected_quantities=(quantity,),
                    reason=str(row.get("reason") or "fallback"),
                    origin=f"engine:{engine.value}",
                    source=row.get("from"),
                    destination=row.get("to"),
                )
            )
        elif kind == "fo2_oxygen_balance_effusion_solved":
            notices.append(
                Notice(
                    kind=NoticeKind.SOURCE_DISAGREEMENT,
                    affected_quantities=(quantity,),
                    reason=(
                        OXYGEN_BALANCE_NOTICE_PREFIX
                        + " "
                        + json.dumps(dict(row), sort_keys=True, separators=(",", ":"))
                    ),
                    origin=f"engine:{engine.value}",
                )
            )
        elif kind == "composition_projected":
            notices.append(
                Notice(
                    kind=NoticeKind.COMPOSITION_PROJECTED,
                    affected_quantities=(quantity,),
                    reason=str(row.get("reason") or "composition projected"),
                    origin=f"engine:{engine.value}",
                    dropped=tuple(str(x) for x in (row.get("dropped") or ())),
                    dropped_mass_fraction=(
                        None
                        if row.get("dropped_mass_fraction") is None
                        else as_decimal(row["dropped_mass_fraction"])
                    ),
                )
            )
        elif kind == NoticeKind.INPUT_OMITTED.value:
            notices.append(
                Notice(
                    kind=NoticeKind.INPUT_OMITTED,
                    affected_quantities=(quantity,),
                    reason=str(row.get("reason") or kind),
                    origin=f"engine:{engine.value}",
                )
            )
        elif kind == "openimcc_notice":
            notices.append(
                Notice(
                    kind=NoticeKind.SOURCE_DISAGREEMENT,
                    affected_quantities=(quantity,),
                    reason=str(row.get("reason") or kind),
                    origin=f"engine:{engine.value}",
                )
            )
        elif kind == NoticeKind.IMCC_COMPLEX_SATURATION.value:
            notices.append(
                Notice(
                    kind=NoticeKind.IMCC_COMPLEX_SATURATION,
                    affected_quantities=(quantity,),
                    reason=str(row.get("reason") or row.get("flag") or kind),
                    origin=f"engine:{engine.value}",
                )
            )
        elif kind in {"openimcc_flag", "openimcc_gas_unavailable"}:
            notices.append(
                Notice(
                    kind=NoticeKind.SOURCE_DISAGREEMENT,
                    affected_quantities=(quantity,),
                    reason=str(row.get("reason") or kind),
                    origin=f"engine:{engine.value}",
                )
            )
    status_reason = getattr(cell, "vapor_pressure_backend_status_reason", None)
    backend_status = getattr(cell, "vapor_pressure_backend_status", None)
    if backend_status == AUTHORITY_FALLBACK or (
        isinstance(status_reason, str) and "fallback" in status_reason
    ):
        notices.append(
            Notice(
                kind=NoticeKind.FALLBACK,
                affected_quantities=(quantity,),
                reason=str(status_reason or "vapor pressure fallback"),
                origin=f"engine:{engine.value}",
                source="vaporock",
                destination="antoine_fallback",
            )
        )
    if isinstance(status_reason, str) and _FLOOR_INVERSION_REASON in status_reason:
        notices.append(
            Notice(
                kind=NoticeKind.FLOOR_INVERSION,
                affected_quantities=(quantity,),
                reason=_FLOOR_INVERSION_REASON,
                origin=f"engine:{engine.value}",
            )
        )
    return tuple(notices)


# More than one oxidation state in silicate melts over the fO2 range these
# engines are asked to run. The generator owns the table; scorer and contract
# use that same mapping so redox admission cannot drift between consumers.


def _oxide_lookup_key(name: str) -> str:
    key = str(name).strip()
    if key.endswith("_Liq"):
        key = key[:-4]
    if key.endswith("*"):
        key = key[:-1]
    return key


def _elements_of_component(name: str) -> frozenset[str]:
    from simulator.melt_backend.alphamelts import MELTS_OXIDE_ALIASES

    key = _oxide_lookup_key(name)
    canonical = MELTS_OXIDE_ALIASES.get(key.lower())
    formula = "FeO" if canonical == "FeO_total" else (canonical or key)
    parsed = formula_composition(formula)
    if not parsed:
        return frozenset()
    return frozenset(element for element, _count in parsed)


def _is_multivalent_name(name: str) -> bool:
    from simulator.battery.generators.bench import MULTIVALENT_MELT_ELEMENTS

    return any(
        element in MULTIVALENT_MELT_ELEMENTS for element in _elements_of_component(name)
    )


def multivalent_components(
    composition: Composition | None,
    species: str | None,
) -> tuple[str, ...]:
    """Names that hold a multivalent element at a positive amount, plus the species.

    A printed zero does not count, so FeO = 0 cannot hide TiO2.
    """

    present: list[str] = []
    if composition is not None:
        for name, amount in composition.components:
            if amount > 0 and _is_multivalent_name(str(name)):
                present.append(str(name))
    if species and _is_multivalent_name(species) and species not in present:
        present.append(species)
    return tuple(sorted(set(present)))


def stated_pure_substance_reservoir(identity: Identity) -> tuple[str, str] | None:
    """Pot formula and phase when the row states a pure condensed reservoir.

    Absent reservoir, an unknown phase, or a gas reservoir is not that
    statement. p_sat also requires the reservoir formula to be the gas species.
    """

    reservoir = identity.reservoir
    if reservoir is None or not reservoir.is_value or reservoir.value is None:
        return None
    src = reservoir.value
    token = phase_token(src)
    if token not in CONDENSED_PHASES:
        return None
    if parse_species_formula(src.formula) is None:
        return None
    if quantity_token(identity) is Quantity.P_SAT and src.formula != identity.species.formula:
        return None
    return src.formula, token.value


def oxygen_is_scorer_input(
    identity: Identity,
    composition: Composition | None,
) -> tuple[bool, str, tuple[str, ...]]:
    """Whether a missing fO2 must be refused, and the record of why.

    Melt activity takes oxygen only when a multivalent element is present.
    Every other quantity follows its identity profile: a required fO2 is an
    input, and a not-applicable fO2 is omitted rather than filled.
    """

    quantity = quantity_token(identity)
    redox = multivalent_components(composition, identity.species.formula)
    if quantity in MELT_ACTIVITY_QUANTITIES:
        if redox:
            return True, "melt contains multivalent " + ", ".join(redox), redox
        from simulator.battery.generators.bench import MULTIVALENT_MELT_ELEMENTS

        return (
            False,
            "melt has no multivalent element "
            f"({', '.join(MULTIVALENT_MELT_ELEMENTS)}) in the composition or measured species; "
            "oxygen is not an input and was omitted",
            (),
        )
    if "fO2_Pa" in profile_for(identity).required:
        qname = quantity.value if quantity is not None else "quantity"
        return True, f"{qname} requires oxygen", redox
    qname = quantity.value if quantity is not None else "quantity"
    return False, f"{qname} does not take oxygen as an input; oxygen was omitted", redox


def _unfilled_inputs(identity: Identity) -> dict[str, str]:
    """Which of the scorer's former fills this identity does not carry."""

    fo2 = identity.fO2_Pa
    pressure = identity.total_pressure_Pa
    fo2_missing = not (fo2 is not None and fo2.is_value and fo2.value is not None)
    pressure_missing = not (
        pressure is not None and pressure.is_value and pressure.value is not None
    )
    out: dict[str, str] = {}
    if fo2_missing:
        out["fO2_Pa"] = "missing"
    if pressure_missing:
        out["total_pressure_Pa"] = "missing"
    return out


def _input_refusal(
    *,
    engine: Engine,
    channel: str,
    sources: tuple[str, ...],
    identity: Identity,
    requested: State[Composition] | None,
    reason: RefusalReason,
    detail: dict[str, object],
    notices: tuple[Notice, ...] = (),
) -> EnginePrediction:
    return EnginePrediction(
        engine=engine,
        channel=channel,
        execution=Execution(state=ExecutionState.NOT_PROBED),
        notices=notices,
        coefficient_sources=sources,
        lineage_complete=False,
        refusal_reason=reason,
        refusal_detail=detail,
        identity=identity,
        requested_composition=requested,
    )


def melt_quantity_report(
    quantity: Quantity,
    activities: Mapping[str, float],
    coefficients: Mapping[str, float],
) -> dict[str, float] | None:
    """Select the engine map that matches the activity quantity."""

    if quantity is Quantity.ACTIVITY:
        return dict(activities)
    if quantity is Quantity.ACTIVITY_COEFFICIENT:
        if not coefficients:
            return None
        return dict(coefficients)
    return None


def _activity_contract_refusal(
    engine: Engine,
    *,
    channel: str,
    sources: tuple[str, ...],
    identity: Identity,
    requested: State[Composition] | None,
    generated,
) -> EnginePrediction:
    from simulator.battery.waypoints import GapReason

    gaps = () if generated is None else generated.readiness.gaps
    first = gaps[0] if gaps else None
    reason_token = first.reason.value if first is not None else "engine_does_not_report_melt_activity"
    detail: dict[str, object] = {
        "reason": reason_token,
        "consumer": "melt_activity",
        "gaps": [
            {
                "waypoint": gap.waypoint,
                "reason": gap.reason.value,
                "missing": list(gap.missing),
            }
            for gap in gaps
        ],
    }
    if first is not None and first.reason is GapReason.REFERENCE_STATE_MISMATCH and len(first.missing) >= 2:
        detail["row_convention"] = first.missing[0]
        detail["engine_convention"] = first.missing[1]
    refusal = (
        RefusalReason.UNSUPPORTED
        if first is None or first.reason is GapReason.REFERENCE_STATE_MISMATCH
        else RefusalReason.IDENTITY_INCOMPLETE
    )
    return EnginePrediction(
        engine=engine,
        channel=channel,
        execution=Execution(state=ExecutionState.NOT_PROBED),
        coefficient_sources=sources,
        lineage_complete=False,
        refusal_reason=refusal,
        refusal_detail=detail,
        identity=identity,
        requested_composition=requested,
    )


def _assumption_notice(quantity: Quantity, reason: str) -> Notice:
    return Notice(
        kind=NoticeKind.PRESSURE_PROVENANCE_UNKNOWN,
        affected_quantities=(quantity,),
        reason=reason,
        origin="score:predict_with_engine",
    )


def _omission_notice(quantity: Quantity, reason: str) -> Notice:
    return Notice(
        kind=NoticeKind.INPUT_OMITTED,
        affected_quantities=(quantity,),
        reason=reason,
        origin="score:predict_with_engine",
    )


def _reactive_cell_not_modelled_notice(
    quantity: Quantity,
    materials: Sequence[Located[CellMaterial]] | None,
) -> Notice:
    cell_material = [
        {
            "field": "bench.cell_materials",
            "value": item.state.value.value,
        }
        for item in materials or ()
        if item.state.is_value and isinstance(item.state.value, CellMaterial)
    ]
    payload = json.dumps(
        {"cell_material": cell_material},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return Notice(
        kind=NoticeKind.REACTIVE_CELL_NOT_MODELLED,
        affected_quantities=(quantity,),
        reason=(
            "reactive cell: oxygen balance of the cell not modelled "
            f"{payload}"
        ),
        origin="score:predict_with_engine",
    )


def _figure_only_notice(
    quantity: Quantity | None, observation: Observation
) -> Notice:
    # Quantity may be unknown (typed refusal quantity_unknown); the evidence
    # class label must still attach. Empty affected_quantities is allowed only
    # for FIGURE_ONLY (see Notice.__post_init__).
    return Notice(
        kind=NoticeKind.FIGURE_ONLY,
        affected_quantities=() if quantity is None else (quantity,),
        reason="figure_only",
        origin=observation.observation_id,
    )


def _cell_material_class(
    materials: Sequence[Located[CellMaterial]] | None,
) -> str:
    if not materials:
        return "unknown"
    values: list[CellMaterial] = []
    for material in materials:
        if not material.state.is_value or not isinstance(material.state.value, CellMaterial):
            return "unknown"
        values.append(material.state.value)
    if any(
        material in {
            CellMaterial.W,
            CellMaterial.MO,
            CellMaterial.TA,
            CellMaterial.NB,
            CellMaterial.C_GRAPHITE,
            CellMaterial.RE,
        }
        for material in values
    ):
        return "reactive"
    if all(material in {CellMaterial.PT, CellMaterial.IR} for material in values):
        return "inert"
    return "not_inert"


_MODELLED_REACTIVE_CELL = frozenset({CellMaterial.W, CellMaterial.MO})


def _uniform_modelled_reactive_cell(
    materials: Sequence[Located[CellMaterial]] | None,
) -> str | None:
    """W or Mo when every typed entry is that same modelled reservoir metal."""

    if not materials:
        return None
    values: list[CellMaterial] = []
    for material in materials:
        if not material.state.is_value or not isinstance(material.state.value, CellMaterial):
            return None
        values.append(material.state.value)
    if not values:
        return None
    metal = values[0]
    if metal not in _MODELLED_REACTIVE_CELL:
        return None
    if any(item is not metal for item in values):
        return None
    return metal.value


def _bench_for_score(
    experiment: Experiment,
    benches: Mapping[str, Bench],
) -> Bench | None:
    """Bench the experiment records. An unset bench_id stays unresolved."""

    if experiment.bench_id is None:
        return None
    return benches.get(experiment.bench_id)


def _printed_point_composition(observation: Observation) -> State[Composition] | None:
    """Printed point composition when the identity axis does not carry one.

    Series migration stores the row composition on point_conditions and
    leaves identity.composition unknown. That stated composition is the
    melt. A derived point composition is not used.
    """

    point = (observation.point_conditions or {}).get("composition")
    if not isinstance(point, Located) or point.inference is not None:
        return None
    if not point.state.is_value or point.state.value is None:
        return None
    if not isinstance(point.state.value, Composition):
        return None
    return point.state


def _solved_effusion_po2_bar(prediction: EnginePrediction) -> float | None:
    engine = prediction.engine
    if engine is None:
        return None
    prefix = OXYGEN_BALANCE_NOTICE_PREFIX
    for notice in prediction.notices:
        if notice.origin != f"engine:{engine.value}":
            continue
        if not notice.reason.startswith(prefix):
            continue
        payload = json.loads(notice.reason.split(" ", 1)[1])
        value = payload.get("pO2_bar")
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
    return None


def _comparison_identity(
    identity: Identity,
    reference: Observation,
    prediction: EnginePrediction,
) -> Identity:
    """Comparison identity with conditions used by the numeric prediction.

    KEMS activity series can store printed composition on the point while
    leaving it unknown on the identity. Their comparison uses that same
    printed composition. Oxygen-balance effusion predictions also supply
    their solved pO2 and scorer pressure assumption. Stored identities remain
    unchanged.
    """

    solved_oxygen_balance = has_own_engine_solved_oxygen_balance(
        prediction.engine, prediction.notices
    )
    comparison_activity = (
        quantity_token(identity) in MELT_ACTIVITY_QUANTITIES
        and comparison_method_cell_constant_cancels(reference.provenance)
    )
    if not solved_oxygen_balance and not comparison_activity:
        return identity
    updates: dict[str, State] = {}
    if identity.composition is None or not identity.composition.is_value:
        point = _printed_point_composition(reference)
        if point is not None:
            updates["composition"] = point
    if solved_oxygen_balance and (
        identity.fO2_Pa is None or not identity.fO2_Pa.is_value
    ):
        po2_bar = _solved_effusion_po2_bar(prediction)
        if po2_bar is not None:
            updates["fO2_Pa"] = State.of(Decimal(str(po2_bar)) * Decimal("1e5"))
    if solved_oxygen_balance and (
        identity.total_pressure_Pa is None or not identity.total_pressure_Pa.is_value
    ):
        quantity = quantity_token(identity)
        if quantity is not None:
            bar, _notice, invalid = total_pressure_bar_for_score(identity, quantity)
            if invalid is None:
                updates["total_pressure_Pa"] = State.of(Decimal(str(bar)) * Decimal("1e5"))
    if not updates:
        return identity
    return replace(identity, **updates)


def _derived_fo2_condition(observation: Observation) -> bool:
    point = (observation.point_conditions or {}).get("fO2_Pa")
    if point is not None:
        if point.inference is not None:
            return True
        note = point.locator.note if point.locator is not None else None
        if note is not None and "derived condition" in note.casefold():
            return True
    return any(
        notice.kind is NoticeKind.PRESSURE_PROVENANCE_UNKNOWN
        and notice.reason.startswith("fO2_Pa is a DERIVED condition")
        for notice in observation.notices
    )


def _has_printed_fo2(observation: Observation, identity: Identity) -> bool:
    if _derived_fo2_condition(observation):
        return False
    fo2 = identity.fO2_Pa
    if fo2 is not None and fo2.is_value and fo2.value is not None:
        return True
    point = (observation.point_conditions or {}).get("fO2_Pa")
    return bool(
        point is not None
        and point.inference is None
        and point.state.is_value
    )


def total_pressure_bar_for_score(
    identity: Identity,
    quantity: Quantity,
) -> tuple[float, Notice | None, str | None]:
    """Observation pressure in bar, or the 1e-6 bar assumption with a notice.

    A present but non-finite or negative pressure is invalid. It is not
    replaced by the assumption.
    """

    from simulator.diagnostic_helpers.binary_pot_battery import _DEFAULT_PRESSURE_BAR

    state = identity.total_pressure_Pa
    if state is not None and state.is_value and state.value is not None:
        pa = state.value if isinstance(state.value, Decimal) else as_decimal(state.value)
        if not pa.is_finite() or pa < 0:
            return 0.0, None, "total_pressure_invalid"
        return float(pa) / 1.0e5, None, None
    detail = "observation does not carry total_pressure_Pa"
    if state is not None and state.reason:
        detail = f"{detail} ({state.tag.value}: {state.reason})"
    notice = _assumption_notice(
        quantity,
        f"{detail}; residual assumes {_DEFAULT_PRESSURE_BAR:g} bar",
    )
    return float(_DEFAULT_PRESSURE_BAR), notice, None


_IMPLIED_ALPHA_SCORING_KIND = "implied_alpha_from_alpha_times_Gamma"
_IMPLIED_ALPHA_LOW = Decimal("0.07")
_IMPLIED_ALPHA_HIGH = Decimal("0.3")
SINGLE_CATION_COEFFICIENT_BASIS = "single_cation"
_IMPLIED_ALPHA_SINGLE_CATION_FORMULAS = {
    "Na2O": "NaO0.5",
    "K2O": "KO0.5",
}


def _implied_alpha_metadata(observation: Observation) -> Mapping[str, object] | None:
    provenance = observation.provenance
    scoring = provenance.get("scoring") if isinstance(provenance, Mapping) else None
    if not isinstance(scoring, Mapping):
        return None
    if scoring.get("kind") != _IMPLIED_ALPHA_SCORING_KIND:
        return None
    oxide = scoring.get("oxide_formula")
    if not isinstance(oxide, str) or not oxide:
        return None
    return scoring


def _implied_alpha_activity_observation(observation: Observation) -> Observation | None:
    """Build the engine-facing activity identity for a Zhang bound row.

    The source identity stays an evaporation-alpha row.  Only the derived
    engine request uses the reported single-cation activity coefficient; the
    measured endpoint remains the printed alpha·Gamma product.
    """

    metadata = _implied_alpha_metadata(observation)
    if metadata is None or not isinstance(observation.identity, Identity):
        return None
    oxide = str(metadata["oxide_formula"])
    coefficient_formula = _IMPLIED_ALPHA_SINGLE_CATION_FORMULAS.get(oxide, oxide)
    identity = observation.identity
    activity_identity = replace(
        identity,
        quantity=Quantity.ACTIVITY_COEFFICIENT,
        species=replace(identity.species, formula=coefficient_formula),
        per=State.of(PerBasis.DIMENSIONLESS),
        reference_state=State.of(
            StandardState(
                convention=ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER,
                endmember=Species(coefficient_formula, Phase.L),
                component_basis=coefficient_formula,
            )
        ),
        standard_pressure_Pa=State.not_applicable("melt activity coefficient uses its reference state"),
        reaction=State.not_applicable("melt activity coefficient has no reaction axis"),
        formation_elements=State.not_applicable("melt activity coefficient has no formation-elements axis"),
        reservoir=State.not_applicable("melt activity coefficient has no gas reservoir axis"),
        sweep_gas=State.not_applicable("melt activity coefficient has no sweep-gas axis"),
        exposure=State.not_applicable("melt activity coefficient has no exposure axis"),
        sample_mass_kg=State.not_applicable("melt activity coefficient has no sample-mass axis"),
        wall=State.not_applicable("melt activity coefficient has no wall axis"),
        subtype=State.not_applicable("melt activity coefficient has no subtype axis"),
    )
    return replace(observation, identity=activity_identity, provenance=None)


def _implied_alpha_coefficient_basis_matches(
    expected: Observation,
    prediction: EnginePrediction,
) -> bool:
    if prediction.coefficient_basis != SINGLE_CATION_COEFFICIENT_BASIS:
        return False
    expected_identity = expected.identity
    actual_identity = prediction.identity
    if not isinstance(expected_identity, Identity) or not isinstance(
        actual_identity, Identity
    ):
        return False
    if actual_identity.species.formula != expected_identity.species.formula:
        return False
    expected_state = expected_identity.reference_state
    actual_state = actual_identity.reference_state
    if (
        expected_state is None
        or not expected_state.is_value
        or not isinstance(expected_state.value, StandardState)
        or actual_state is None
        or not actual_state.is_value
        or not isinstance(actual_state.value, StandardState)
    ):
        return False
    expected_standard = expected_state.value
    actual_standard = actual_state.value
    return (
        actual_standard.convention is expected_standard.convention
        and actual_standard.endmember.formula == expected_standard.endmember.formula
        and phase_token(actual_standard.endmember)
        is phase_token(expected_standard.endmember)
        and actual_standard.component_basis == expected_standard.component_basis
    )


def _implied_alpha_verdict(value: Decimal) -> str:
    if value > Decimal("1"):
        return "physically_impossible"
    if value < _IMPLIED_ALPHA_LOW or value > _IMPLIED_ALPHA_HIGH:
        return "outside_literature_band"
    return "consistent"


def _implied_alpha_numeric(
    value: Decimal,
) -> tuple[ResidualNumeric | None, RefusalReason | None, dict[str, object]]:
    if not value.is_finite() or value <= 0:
        return None, RefusalReason.METRIC_DOMAIN, {
            "reason": "implied_alpha_nonpositive_or_nonfinite",
            "implied_alpha": str(value),
        }
    dex = Decimal(str(math.log10(float(value))))
    return (
        ResidualNumeric(
            operation=MetricOperation.DEX,
            unit="dimensionless",
            value=dex,
            decision_band=None,
            verdict=_implied_alpha_verdict(value),
        ),
        None,
        {},
    )


def _knudsen_vapour_equilibrium(
    quantity: Quantity, experiment: Experiment | None
) -> bool:
    """Vapour-equilibrium quantity measured by Knudsen effusion."""

    return bool(
        quantity in _VAPOUR_EQUILIBRIUM
        and experiment is not None
        and experiment.method.is_value
        and experiment.method.value is MethodToken.KNUDSEN_EFFUSION
    )


def _cell_oxygen_class(
    materials: Sequence[Located[CellMaterial]] | None,
) -> tuple[str, str | None]:
    """(material class, modelled reactive reservoir metal or None)."""

    material_class = _cell_material_class(materials)
    modelled = (
        _uniform_modelled_reactive_cell(materials)
        if material_class == "reactive"
        else None
    )
    return material_class, modelled


def _oxygen_input_request(
    *,
    engine: Engine,
    channel: str,
    sources: tuple[str, ...],
    identity: Identity,
    requested: State[Composition] | None,
    quantity: Quantity,
    composition_value: Composition | None,
    input_notices: list[Notice],
) -> "Po2Request | EnginePrediction":
    """The scorer's one oxygen-input decision (b-693/b-691 r2 finding 5).

    A printed fO2 is the commanded point (fO2_Pa is fugacity; pO2_bar =
    fO2_Pa / 1e5 under the stated ideal-gas assumption, 1 bar = 100000 Pa
    exactly). Otherwise a quantity that needs oxygen refuses
    ``missing_fO2``; one that does not records the omission notice on
    ``input_notices`` and runs with oxygen not an input. Every cell branch of
    ``predict_with_engine`` consumes this; none re-derives it.
    """

    # Same lazy import predict_with_engine already uses (no new layer edge).
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        PO2_NOT_AN_INPUT,
        Po2Request,
    )

    oxygen_required, oxygen_why, redox = oxygen_is_scorer_input(
        identity, composition_value
    )
    fo2_state = identity.fO2_Pa
    if fo2_state is not None and fo2_state.is_value and fo2_state.value is not None:
        return Po2Request(mode=PO2_COMMANDED, po2_bar=float(fo2_state.value) / 1.0e5)
    if oxygen_required:
        return _input_refusal(
            engine=engine,
            channel=channel,
            sources=sources,
            identity=identity,
            requested=requested,
            reason=RefusalReason.IDENTITY_INCOMPLETE,
            detail={
                "reason": "missing_fO2",
                "quantity": quantity.value,
                "why": oxygen_why,
                "multivalent": list(redox),
            },
            notices=tuple(input_notices),
        )
    input_notices.append(_omission_notice(quantity, oxygen_why))
    return Po2Request(mode=PO2_NOT_AN_INPUT, po2_bar=None)


def predict_with_engine(
    engine: Engine,
    observation: Observation,
    *,
    handles: Mapping[str, object] | None = None,
    isolated: bool | None = None,
    experiment: Experiment | None = None,
    bench: Bench | None = None,
) -> EnginePrediction:
    """Dispatch one engine at the observation Identity. Isolated MELTS cells."""

    _require_score_engine(engine)
    from simulator.diagnostic_helpers.binary_pot_battery import (
        ARM_HEADLINE,
        ARM_QUALIFICATION,
        MELTS_FAMILY_ENGINES,
        PO2_COMMANDED,
        PO2_NOT_AN_INPUT,
        PO2_OXYGEN_BALANCE_EFFUSION,
        REFUSAL_ENGINE_CRASH,
        REFUSAL_TIMEOUT,
        REFUSAL_UNAVAILABLE,
        Po2Request,
        assess_qualification_gate,
        equilibrate_cell,
        identity_battery_pot,
        open_battery_engine,
    )

    quantity = quantity_token(observation.identity)
    channel = ENGINE_CHANNELS[engine]
    sources = ENGINE_COEFFICIENT_SOURCES[engine]
    identity = observation.identity
    if not isinstance(identity, Identity) or quantity is None:
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(state=ExecutionState.NOT_PROBED),
            coefficient_sources=sources,
            lineage_complete=False,
            refusal_reason=RefusalReason.IDENTITY_UNKNOWN,
            refusal_detail={"reason": "quantity_unknown"},
        )
    if quantity is Quantity.RESIDUE_COMPONENT_COMPOSITION:
        if (
            observation.source_id == "kems-012-sossi-2019"
            and identity.species.formula not in {"Mn", "Ti"}
        ):
            return EnginePrediction(
                engine=engine,
                channel=channel,
                execution=Execution(state=ExecutionState.UNSUPPORTED),
                coefficient_sources=sources,
                lineage_complete=False,
                refusal_reason=RefusalReason.OUTSIDE_SUPPORTED_SPECIES,
                refusal_detail={
                    "reason": "channel_missing",
                    "element": identity.species.formula,
                    "quantity": quantity.value,
                },
                identity=identity,
            )
        if engine in OXYGEN_BALANCE_EFFUSION_ENGINES:
            return EnginePrediction(
                engine=engine,
                channel=channel,
                execution=Execution(state=ExecutionState.NOT_PROBED),
                coefficient_sources=sources,
                lineage_complete=False,
                refusal_reason=RefusalReason.IDENTITY_INCOMPLETE,
                refusal_detail={
                    "reason": "melt_surface_area_evolution_missing",
                    "quantity": quantity.value,
                },
                identity=identity,
            )
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(state=ExecutionState.UNSUPPORTED),
            coefficient_sources=sources,
            lineage_complete=False,
            refusal_reason=RefusalReason.UNSUPPORTED,
            refusal_detail={
                "reason": "quantity_not_predicted",
                "quantity": quantity.value,
            },
            identity=identity,
        )
    formula = identity.species.formula
    if parse_species_formula(formula) is None:
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(state=ExecutionState.NOT_PROBED),
            coefficient_sources=sources,
            lineage_complete=False,
            refusal_reason=RefusalReason.IDENTITY_UNKNOWN,
            refusal_detail={"reason": "species_formula_unparsed", "formula": formula},
            identity=identity,
        )
    if is_sf04_workbook(observation) and engine in IMCC_ENGINES:
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(state=ExecutionState.NOT_PROBED),
            coefficient_sources=sources,
            lineage_complete=False,
            refusal_reason=RefusalReason.UNSUPPORTED,
            refusal_detail={
                "reason": "imcc_built_on_sf04_workbook",
                "source_id": observation.source_id,
            },
            identity=identity,
        )

    if quantity in EQUILIBRIUM_FIT_QUANTITIES:
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(state=ExecutionState.UNSUPPORTED),
            coefficient_sources=sources,
            lineage_complete=False,
            refusal_reason=RefusalReason.UNSUPPORTED,
            refusal_detail={
                "reason": "unsupported_observable:logKstar_not_activity_coefficient",
                "quantity": quantity.value,
            },
            identity=identity,
        )

    T = temperature_of(identity)
    if T is None:
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(state=ExecutionState.NOT_PROBED),
            coefficient_sources=sources,
            lineage_complete=False,
            refusal_reason=RefusalReason.IDENTITY_UNKNOWN,
            refusal_detail={"reason": "temperature_unknown"},
            identity=identity,
        )

    if (
        quantity in MELT_ACTIVITY_QUANTITIES
        and experiment is None
        and (identity.composition is None or not identity.composition.is_value)
    ):
        composition_reason = (
            "composition is missing"
            if identity.composition is None
            else identity.composition.reason or "composition is not a value"
        )
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(state=ExecutionState.NOT_PROBED),
            coefficient_sources=sources,
            lineage_complete=False,
            refusal_reason=RefusalReason.IDENTITY_INCOMPLETE,
            refusal_detail={"reason": "composition_incomplete", "detail": composition_reason},
            identity=identity,
            requested_composition=identity.composition,
        )

    # Formation and pure-standard quantities are not melt activities or
    # partial pressures. A missing branch used to read those maps and
    # return the wrong unit. Refuse instead of emitting 0.
    if quantity in FORMATION_QUANTITIES or quantity in PURE_STANDARD_THERMO:
        from simulator.battery.compilation_tier import predict_thermo_attempt

        attempt = predict_thermo_attempt(engine, observation)
        if attempt.value is not None:
            exec_state = ExecutionState.PRODUCED
        elif attempt.refusal_reason is RefusalReason.ATTEMPTED_UNAVAILABLE:
            exec_state = ExecutionState.ATTEMPTED_UNAVAILABLE
        elif attempt.refusal_reason is RefusalReason.UNSUPPORTED:
            exec_state = ExecutionState.UNSUPPORTED
        else:
            exec_state = ExecutionState.NOT_PROBED
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(
                state=exec_state, call_evidence=attempt.call_evidence
            ),
            value=attempt.value,
            unit=attempt.unit,
            authority=attempt.authority,
            notices=attempt.notices,
            coefficient_sources=sources,
            lineage_complete=lineage_complete_for(sources) if attempt.value is not None else False,
            refusal_reason=attempt.refusal_reason,
            refusal_detail=attempt.refusal_detail,
            identity=identity,
        )

    wt: dict[str, float] | None = None
    requested: State[Composition] | None = None
    input_notices: list[Notice] = []
    composition_value: Composition | None = None
    activity_payload: Mapping | None = None
    if quantity in MELT_ACTIVITY_QUANTITIES and experiment is not None:
        # Activity rows use the melt-activity contract. A gap is a typed
        # refusal. No engine fO2 default is an input on this path.
        from simulator.battery.enums import AmountBasis
        from simulator.battery.generators.bench import activity_request_for_engine
        from simulator.battery.records import Composition as OxideComposition

        if experiment is None:
            return EnginePrediction(
                engine=engine,
                channel=channel,
                execution=Execution(state=ExecutionState.NOT_PROBED),
                coefficient_sources=sources,
                lineage_complete=False,
                refusal_reason=RefusalReason.IDENTITY_INCOMPLETE,
                refusal_detail={
                    "reason": "melt_activity_contract_requires_experiment",
                    "consumer": "melt_activity",
                },
                identity=identity,
            )
        generated = activity_request_for_engine(experiment, observation, engine.value)
        if generated is None or generated.payload is None:
            return _activity_contract_refusal(
                engine,
                channel=channel,
                sources=sources,
                identity=identity,
                requested=identity.composition if identity.composition is not None and identity.composition.is_value else None,
                generated=generated,
            )
        activity_payload = generated.payload
        moles = dict(activity_payload.get("composition_mol") or {})
        built = OxideComposition(
            "oxides",
            tuple((str(name), Decimal(str(amount))) for name, amount in moles.items()),
            AmountBasis.MOLE_FRACTION,
        )
        requested = identity.composition if identity.composition is not None and identity.composition.is_value else State.of(built)
        composition_value = built
        wt = composition_wt_pct(built)
        if wt is None:
            return EnginePrediction(
                engine=engine,
                channel=channel,
                execution=Execution(state=ExecutionState.NOT_PROBED),
                coefficient_sources=sources,
                lineage_complete=False,
                refusal_reason=RefusalReason.IDENTITY_UNKNOWN,
                refusal_detail={"reason": "composition_unparsed"},
                identity=identity,
                requested_composition=requested,
            )
    elif identity.composition is not None and identity.composition.is_value:
        requested = identity.composition
        assert identity.composition.value is not None
        composition_value = identity.composition.value
    elif quantity in _VAPOUR_EQUILIBRIUM and (
        point_composition := _printed_point_composition(observation)
    ) is not None:
        requested = point_composition
        assert point_composition.value is not None
        composition_value = point_composition.value
    elif quantity in _VAPOUR_EQUILIBRIUM:
        # 100 wt% of a species is the pure substance only when the row says so.
        stated = stated_pure_substance_reservoir(identity)
        if stated is None:
            detail: dict[str, object] = {
                "reason": "composition_not_stated_pure_reservoir",
                "quantity": quantity.value,
                "formula": formula,
            }
            unfilled = _unfilled_inputs(identity)
            if unfilled:
                detail["also_unfilled"] = unfilled
            return _input_refusal(
                engine=engine,
                channel=channel,
                sources=sources,
                identity=identity,
                requested=requested,
                reason=RefusalReason.IDENTITY_INCOMPLETE,
                detail=detail,
            )
        formula_pot, phase_name = stated
        wt = {formula_pot: 100.0}
        input_notices.append(
            _omission_notice(
                quantity,
                "composition omitted; reservoir states the pure condensed "
                f"substance {formula_pot} ({phase_name})",
            )
        )

    if composition_value is None and wt is None:
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(state=ExecutionState.UNSUPPORTED),
            coefficient_sources=sources,
            lineage_complete=False,
            refusal_reason=RefusalReason.UNSUPPORTED,
            refusal_detail={"reason": "quantity_has_no_engine_pot", "quantity": quantity.value},
            identity=identity,
        )

    pressure_bar: float | None = None
    if activity_payload is None:
        pressure_bar, pressure_notice, pressure_invalid = total_pressure_bar_for_score(
            identity, quantity
        )
        if pressure_invalid is not None:
            return _input_refusal(
                engine=engine,
                channel=channel,
                sources=sources,
                identity=identity,
                requested=requested,
                reason=RefusalReason.INVALID_IDENTITY,
                detail={"reason": pressure_invalid, "quantity": quantity.value},
                notices=tuple(input_notices),
            )
        if pressure_notice is not None:
            input_notices.append(pressure_notice)

    knudsen_vapour = _knudsen_vapour_equilibrium(quantity, experiment)
    oxygen_balance_effusion = knudsen_vapour and not _has_printed_fo2(
        observation, identity
    )
    if activity_payload is not None:
        # The contract's oxygen point, or none. Never an engine default.
        if "fO2_log" in activity_payload:
            po2 = Po2Request(
                mode=PO2_COMMANDED,
                po2_bar=10.0 ** float(activity_payload["fO2_log"]),
            )
        else:
            po2 = Po2Request(mode=PO2_NOT_AN_INPUT, po2_bar=None)
    elif oxygen_balance_effusion:
        cell_materials = bench.cell_materials if bench is not None else None
        material_class, modelled = _cell_oxygen_class(cell_materials)
        if material_class == "reactive" and modelled is None:
            # Out-of-domain physics: predict and flag. Keep refusal only when
            # a required input is genuinely missing (the shared decision).
            input_notices.append(
                _reactive_cell_not_modelled_notice(quantity, cell_materials)
            )
            decision = _oxygen_input_request(
                engine=engine,
                channel=channel,
                sources=sources,
                identity=identity,
                requested=requested,
                quantity=quantity,
                composition_value=composition_value,
                input_notices=input_notices,
            )
            if isinstance(decision, EnginePrediction):
                return decision
            po2 = decision
        elif material_class != "inert" and modelled is None:
            refusal_token = {
                "not_inert": "cell_material_not_inert",
                "unknown": "cell_material_unknown",
            }[material_class]
            return _input_refusal(
                engine=engine,
                channel=channel,
                sources=sources,
                identity=identity,
                requested=requested,
                reason=(
                    RefusalReason.IDENTITY_INCOMPLETE
                    if material_class == "unknown"
                    else RefusalReason.UNSUPPORTED
                ),
                detail={
                    "reason": refusal_token,
                    "cell_material": [
                        {
                            "field": "bench.cell_materials",
                            "value": item.state.value.value,
                        }
                        for item in cell_materials or ()
                        if item.state.is_value
                    ],
                },
                notices=tuple(input_notices),
            )
        else:
            if engine not in OXYGEN_BALANCE_EFFUSION_ENGINES:
                return _input_refusal(
                    engine=engine,
                    channel=channel,
                    sources=sources,
                    identity=identity,
                    requested=requested,
                    reason=RefusalReason.UNSUPPORTED,
                    detail={
                        "reason": "oxygen_balance_effusion_unsupported_engine",
                        "engine": engine.value,
                    },
                    notices=tuple(input_notices),
                )
            # The openimcc bridge discards pressure_bar; its balance solve uses
            # printed composition-derived activities and T, never measured p_K.
            po2 = Po2Request(
                mode=PO2_OXYGEN_BALANCE_EFFUSION,
                po2_bar=None,
                cell_material=modelled,
            )
    else:
        decision = _oxygen_input_request(
            engine=engine,
            channel=channel,
            sources=sources,
            identity=identity,
            requested=requested,
            quantity=quantity,
            composition_value=composition_value,
            input_notices=input_notices,
        )
        if isinstance(decision, EnginePrediction):
            return decision
        po2 = decision
        # Printed-O2 (or oxygen-not-required) Knudsen path: still flag an
        # unmodelled reactive cell so the label travels with the prediction.
        if knudsen_vapour and bench is not None:
            material_class, modelled = _cell_oxygen_class(bench.cell_materials)
            if material_class == "reactive" and modelled is None:
                if not any(
                    notice.kind is NoticeKind.REACTIVE_CELL_NOT_MODELLED
                    for notice in input_notices
                ):
                    input_notices.append(
                        _reactive_cell_not_modelled_notice(
                            quantity, bench.cell_materials
                        )
                    )

    if composition_value is not None:
        wt = composition_wt_pct(composition_value)
        if wt is None:
            return EnginePrediction(
                engine=engine,
                channel=channel,
                execution=Execution(state=ExecutionState.NOT_PROBED),
                notices=tuple(input_notices),
                coefficient_sources=sources,
                lineage_complete=False,
                refusal_reason=RefusalReason.IDENTITY_UNKNOWN,
                refusal_detail={"reason": "composition_unparsed"},
                identity=identity,
                requested_composition=requested,
            )

    handle_map = handles if handles is not None else {}
    handle = handle_map.get(engine.value)
    if handle is not None and (
        not getattr(handle, "available", False)
        or getattr(handle, "backend", None) is None
    ):
        if isinstance(handle_map, dict):
            handle_map.pop(engine.value, None)
        handle = None
    if handle is None:
        handle = open_battery_engine(engine.value)
        if (
            isinstance(handle_map, dict)
            and getattr(handle, "available", False)
            and getattr(handle, "backend", None) is not None
        ):
            handle_map[engine.value] = handle
    name = str(getattr(handle, "name", engine.value))
    engine_version: str | None = None
    if engine is Engine.OPENIMCC:
        identity_block = getattr(handle, "identity", None) or {}
        if isinstance(identity_block, Mapping):
            engine_version = str(identity_block.get("version") or "") or None
            provenance = (
                ("openimcc-pack-version", identity_block.get("pack_version")),
                ("openimcc-pack-digest", identity_block.get("pack_digest")),
            )
            sources = tuple(
                dict.fromkeys(
                    [
                        *sources,
                        *openimcc_engine_identity_sources(
                            identity_block.get("engine_binding_identity")
                        ),
                        *(
                            f"{label}:{value}"
                            for label, value in provenance
                            if value
                        ),
                    ]
                )
            )
    available = bool(getattr(handle, "available", False))
    if not available:
        unavailable_reason = str(getattr(handle, "unavailable_reason", None) or "")
        if engine is Engine.OPENIMCC and "openimcc_not_importable" in unavailable_reason:
            remedy = unavailable_reason[unavailable_reason.find("remedy:") :]
            return EnginePrediction(
                engine=engine,
                channel=channel,
                execution=Execution(
                    state=ExecutionState.ATTEMPTED_UNAVAILABLE,
                    call_evidence=f"open_battery_engine:{name}",
                ),
                notices=tuple(input_notices),
                coefficient_sources=sources,
                lineage_complete=False,
                refusal_reason=RefusalReason.OPENIMCC_NOT_IMPORTABLE,
                refusal_detail={
                    "reason": RefusalReason.OPENIMCC_NOT_IMPORTABLE.value,
                    "engine_reason": unavailable_reason,
                    "remedy": remedy or unavailable_reason,
                },
                identity=identity,
                requested_composition=requested,
                version=engine_version,
            )
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(
                state=ExecutionState.ATTEMPTED_UNAVAILABLE,
                call_evidence=f"open_battery_engine:{name}",
            ),
            notices=tuple(input_notices),
            coefficient_sources=sources,
            lineage_complete=False,
            refusal_reason=RefusalReason.ATTEMPTED_UNAVAILABLE,
            refusal_detail={
                "reason": REFUSAL_UNAVAILABLE,
                "engine_reason": unavailable_reason,
            },
            identity=identity,
            requested_composition=requested,
            version=engine_version,
        )

    qualification = False
    extra_notices: list[Notice] = list(input_notices)
    authority = Authority.BRIDGE
    certified_band = None
    if name in MELTS_FAMILY_ENGINES:
        gate = assess_qualification_gate(wt, float(T))
        if not gate.get("valid"):
            qualification = True
            extra_notices.append(qualification_battery_notice(quantity, engine, gate))
            authority = Authority.EXTRAPOLATED
            certified_band = None

    pot = identity_battery_pot(f"obs:{observation.observation_id}", wt)
    use_isolated = isolated
    if use_isolated is None:
        use_isolated = qualification and name in MELTS_FAMILY_ENGINES
    cell = equilibrate_cell(
        handle,
        pot,
        temperature_K=float(T),
        po2=po2,
        qualification=qualification,
        isolated=use_isolated,
        arm=ARM_QUALIFICATION if qualification else ARM_HEADLINE,
        physical_pressure_bar=pressure_bar,
    )
    notices = union_notices(tuple(extra_notices), cell_notices(quantity, engine, cell))
    authority, certified_band = _flags_from_scored_cell(cell, authority, certified_band)
    refusal = getattr(cell, "refusal_reason", None)
    status = getattr(cell, "status", None)
    call_evidence = (
        f"equilibrate_cell:{name}:host={getattr(cell, 'hostname', '')}"
        f":exit={getattr(cell, 'exit_code', None)}"
    )
    if engine is Engine.INTERNAL_ANALYTICAL:
        engine_version = (
            getattr(cell, "vapor_pressure_backend_status_reason", None)
            or "PyrolysisSimulator VAPOR_PRESSURE core route"
        )
    if status != "ok":
        typed = str(refusal or status or "unavailable")
        engine_side_reason = str(getattr(cell, "engine_reason", None) or "")
        openimcc_outside_species = engine is Engine.OPENIMCC and any(
            token in f"{typed} {engine_side_reason}"
            for token in (
                "imcc_component_outside_domain",
                "imcc_species_not_found",
                "imcc_ferric_input_unsupported",
                "imcc_sp_extension_required",
            )
        )
        if typed in {REFUSAL_ENGINE_CRASH, "subprocess_died"} or getattr(cell, "exit_signal", None):
            return EnginePrediction(
                engine=engine,
                channel=channel,
                execution=Execution(
                    state=ExecutionState.ATTEMPTED_UNAVAILABLE,
                    call_evidence=call_evidence,
                ),
                authority=Authority.REFUSED,
                notices=notices,
                coefficient_sources=sources,
                lineage_complete=False,
                refusal_reason=RefusalReason.ATTEMPTED_UNAVAILABLE,
                refusal_detail={
                    "typed": "engine_crash",
                    "refusal_reason": typed,
                    "exit_code": getattr(cell, "exit_code", None),
                    "exit_signal": getattr(cell, "exit_signal", None),
                    "engine_reason": getattr(cell, "engine_reason", None),
                },
                identity=identity,
                requested_composition=requested,
                version=engine_version,
            )
        if typed == REFUSAL_TIMEOUT:
            exec_state = ExecutionState.ATTEMPTED_UNAVAILABLE
            reason = RefusalReason.ATTEMPTED_UNAVAILABLE
        elif typed == REFUSAL_UNAVAILABLE:
            exec_state = ExecutionState.ATTEMPTED_UNAVAILABLE
            reason = RefusalReason.ATTEMPTED_UNAVAILABLE
        elif openimcc_outside_species:
            exec_state = ExecutionState.UNSUPPORTED
            reason = RefusalReason.OUTSIDE_SUPPORTED_SPECIES
        elif engine is Engine.INTERNAL_ANALYTICAL and (
            engine_side_reason.startswith("internal_analytical_missing_")
            or engine_side_reason
            == "internal_analytical_oxygen_balance_unavailable"
        ):
            exec_state = ExecutionState.NOT_PROBED
            reason = RefusalReason.IDENTITY_INCOMPLETE
        elif engine is Engine.INTERNAL_ANALYTICAL and engine_side_reason.startswith(
            "internal_analytical_invalid_"
        ):
            exec_state = ExecutionState.NOT_PROBED
            reason = RefusalReason.INVALID_IDENTITY
        else:
            exec_state = ExecutionState.UNSUPPORTED
            reason = RefusalReason.UNSUPPORTED
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(state=exec_state, call_evidence=call_evidence),
            authority=Authority.REFUSED,
            notices=notices,
            coefficient_sources=sources,
            lineage_complete=False,
            refusal_reason=reason,
            refusal_detail={
                "refusal_reason": typed,
                "engine_reason": engine_side_reason,
                **(
                    {"reason": RefusalReason.OUTSIDE_SUPPORTED_SPECIES.value}
                    if openimcc_outside_species
                    else {}
                ),
            },
            identity=identity,
            requested_composition=requested,
            version=engine_version,
        )

    activities = dict(getattr(cell, "melt_activities", None) or {})
    pressures = dict(getattr(cell, "gas_partial_pressures_Pa", None) or {})
    reported: Mapping[str, float]
    unit = QUANTITY_UNITS[quantity]
    coefficient_basis: str | None = None
    if quantity in MELT_ACTIVITY_QUANTITIES:
        coefficients = dict(getattr(cell, "melt_activity_coefficients", None) or {})
        selected = melt_quantity_report(quantity, activities, coefficients)
        if selected is None:
            return EnginePrediction(
                engine=engine,
                channel=channel,
                execution=Execution(state=ExecutionState.PRODUCED, call_evidence=call_evidence),
                authority=Authority.REFUSED,
                notices=notices,
                coefficient_sources=sources,
                lineage_complete=False,
                refusal_reason=RefusalReason.UNSUPPORTED,
                refusal_detail={
                    "reason": "engine_reported_activity_not_coefficient",
                    "quantity": quantity.value,
                },
                identity=identity,
                requested_composition=requested,
                version=engine_version,
            )
        if quantity is Quantity.ACTIVITY_COEFFICIENT:
            details = getattr(cell, "melt_activity_coefficient_details", None)
            detail = details.get(formula) if isinstance(details, Mapping) else None
            if isinstance(detail, Mapping):
                reported_basis = detail.get("coefficient_basis")
                reported_standard_state = detail.get("standard_state")
                reference_state = identity.reference_state
                expected_standard_state = (
                    reference_state.value
                    if reference_state is not None
                    and reference_state.is_value
                    and isinstance(reference_state.value, StandardState)
                    else None
                )
                expected_phase = (
                    phase_token(expected_standard_state.endmember)
                    if expected_standard_state is not None
                    else None
                )
                if (
                    expected_standard_state is None
                    or expected_phase is None
                    or not isinstance(reported_standard_state, Mapping)
                    or reported_standard_state.get("convention")
                    != expected_standard_state.convention.value
                    or reported_standard_state.get("phase") != expected_phase.value
                    or reported_standard_state.get("component_basis")
                    != expected_standard_state.component_basis
                ):
                    return EnginePrediction(
                        engine=engine,
                        channel=channel,
                        execution=Execution(
                            state=ExecutionState.PRODUCED,
                            call_evidence=call_evidence,
                        ),
                        authority=Authority.REFUSED,
                        notices=notices,
                        coefficient_sources=sources,
                        lineage_complete=False,
                        refusal_reason=RefusalReason.COEFFICIENT_BASIS_MISMATCH,
                        refusal_detail={
                            "reason": RefusalReason.COEFFICIENT_BASIS_MISMATCH.value,
                            "formula": formula,
                            "reported_basis": reported_basis,
                            "reported_standard_state": dict(reported_standard_state)
                            if isinstance(reported_standard_state, Mapping)
                            else None,
                            "expected_standard_state": (
                                {
                                    "convention": expected_standard_state.convention.value,
                                    "phase": expected_phase.value,
                                    "component_basis": expected_standard_state.component_basis,
                                }
                                if expected_standard_state is not None
                                and expected_phase is not None
                                else None
                            ),
                        },
                        identity=identity,
                        requested_composition=requested,
                        version=engine_version,
                    )
                if isinstance(reported_basis, str):
                    coefficient_basis = reported_basis
        reported = selected
        unit = "dimensionless"
    elif quantity in _VAPOUR_EQUILIBRIUM:
        reported = pressures
        unit = "Pa"
    else:
        reported = {**activities, **pressures}

    magnitude: float | None = None
    converter_reason = ""
    # The melt-activity gate already required the measured species to equal
    # the admitted endmember. Compare that exact identity; never substitute a
    # different species into the residual.
    compared = formula
    if quantity is Quantity.ACTIVITY and engine in (Engine.ALPHAMELTS, Engine.THERMOENGINE):
        # The converter refuses Na2O, CaO, MgO, FeO, and K2O, including a
        # same-named key. It returns a(SiO2) from SiO2_Liq and a(H2O)
        # from H2O or H2O_Liq. Element labels are not oxide activities.
        from engines.alphamelts.domain import melts_endmember_to_parent_oxide_activity

        value, converter_reason = melts_endmember_to_parent_oxide_activity(
            reported, compared,
        )
        magnitude = value
    else:
        matched = match_reported_species(
            compared,
            reported,
            oxide_activity=quantity in MELT_ACTIVITY_QUANTITIES,
        )
        if matched is not None:
            _name, magnitude = matched
    if magnitude is None:
        detail: dict[str, object] = {
            "reason": "species_unmatched_in_engine_output",
            "formula": compared,
            "reported": sorted(str(name) for name in reported),
        }
        if converter_reason:
            detail["converter"] = converter_reason
        refusal_reason = RefusalReason.UNSUPPORTED
        if engine is Engine.OPENIMCC and quantity in _VAPOUR_EQUILIBRIUM:
            refusal_reason = RefusalReason.OUTSIDE_SUPPORTED_SPECIES
            detail["reason"] = RefusalReason.OUTSIDE_SUPPORTED_SPECIES.value
            detail["engine"] = "openimcc gas tables"
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(state=ExecutionState.PRODUCED, call_evidence=call_evidence),
            authority=authority,
            notices=notices,
            coefficient_sources=sources,
            lineage_complete=False,
            certified_band=certified_band,
            refusal_reason=refusal_reason,
            refusal_detail=detail,
            identity=identity,
            requested_composition=requested,
            version=engine_version,
        )
    if not math.isfinite(magnitude):
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(state=ExecutionState.PRODUCED, call_evidence=call_evidence),
            authority=Authority.REFUSED,
            notices=notices,
            coefficient_sources=sources,
            lineage_complete=False,
            refusal_reason=RefusalReason.METRIC_DOMAIN,
            refusal_detail={"reason": "nonfinite_engine_value", "value": str(magnitude)},
            identity=identity,
            requested_composition=requested,
            version=engine_version,
        )
    expanded = expand_coefficient_sources(sources)
    return EnginePrediction(
        engine=engine,
        channel=channel,
        execution=Execution(state=ExecutionState.PRODUCED, call_evidence=call_evidence),
        value=as_decimal(str(magnitude)),
        unit=unit,
        authority=authority,
        notices=notices,
        coefficient_sources=expanded,
        lineage_complete=lineage_complete_for(sources),
        certified_band=certified_band,
        coefficient_basis=coefficient_basis,
        identity=identity,
        requested_composition=requested,
        version=engine_version,
    )


def candidate_observation(
    reference: Observation,
    prediction: EnginePrediction,
) -> Observation:
    identity = prediction.identity or reference.identity
    if prediction.value is None:
        value = Value(
            kind=ValueKind.UNAVAILABLE,
            unavailable_reason=str(
                (prediction.refusal_detail or {}).get("reason") or "engine produced no value"
            ),
        )
    else:
        value = Value.point_of(prediction.value)
    return Observation(
        observation_id=f"engine:{prediction.engine.value}:{reference.observation_id}",
        experiment_id=reference.experiment_id,
        identity=identity,
        value=value,
        uncertainty=_none_uncertainty(),
        evidence=Evidence(class_=State.of(EvidenceClass.ENGINE_PREDICTION)),
        admission=replace(
            reference.admission, status=AdmissionStatus.PENDING, reason="engine prediction"
        ),
        notices=prediction.notices,
        source_id=reference.source_id,
        locator=reference.locator,
        read_from=reference.read_from,
        engine=EngineTrace(
            name=prediction.engine,
            channel=prediction.channel,
            run_id=prediction.execution.call_evidence or f"{prediction.engine.value}:run",
            coefficient_sources=prediction.coefficient_sources or ("unresolved",),
            lineage_complete=prediction.lineage_complete,
            version=prediction.version,
            requested_composition=prediction.requested_composition,
        ),
        authority=prediction.authority,
        certified_band=prediction.certified_band,
        provenance=(
            dict(prediction.refusal_detail)
            if quantity_token(identity) is Quantity.RESIDUE_COMPONENT_COMPOSITION
            and prediction.value is not None
            else None
        ),
    )


_HASHIMOTO_SOURCE_ID = "kems-015-hashimoto-1983"


def _hashimoto_residue_prediction(
    context: ScoreContext,
    reference: Observation,
    engine: Engine,
    cache: dict[tuple[str, Engine], Mapping[str, object]],
) -> EnginePrediction:
    """Predict a Hashimoto vector once per engine, then project by physical run."""
    channel = ENGINE_CHANNELS[engine]
    sources = ENGINE_COEFFICIENT_SOURCES[engine]
    cache_key = (_HASHIMOTO_SOURCE_ID, engine)
    try:
        if engine not in {Engine.INTERNAL_ANALYTICAL, Engine.OPENIMCC}:
            raise ValueError("engine_not_supported_for_hashimoto_residue")
        if cache_key not in cache:
            from simulator.battery.residue import (
                ResidueInventoryRefusal,
                _predict_hashimoto_residue_cohort,
            )

            observations = [
                row
                for row in context.observations.values()
                if row.source_id == _HASHIMOTO_SOURCE_ID
                and row.observation_id.startswith(_HASHIMOTO_SOURCE_ID + "::")
                and isinstance(row.identity, Identity)
                and quantity_token(row.identity)
                is Quantity.RESIDUE_COMPONENT_COMPOSITION
            ]
            experiments_by_id = {
                row.experiment_id: context.experiments[row.experiment_id]
                for row in observations
                if row.experiment_id in context.experiments
            }
            if len(experiments_by_id) != 24:
                raise ResidueInventoryRefusal(
                    "hashimoto_cohort_invalid",
                    f"scorer found {len(experiments_by_id)} Hashimoto runs, expected 24",
                )
            source_path = (
                REPO_ROOT
                / "data/literature/extracts/kems-015-hashimoto-1983.yaml"
            )
            source = load_yaml(source_path)
            geometry = next(
                item
                for item in source.get("metadata", ())
                if item.get("observation_id")
                == "hashimoto_1983_fe_fcmas_free_evap_geometry_quoted"
            )["values"]["starting_preform_mm"]
            experiments = [
                to_plain(experiments_by_id[experiment_id])
                for experiment_id in sorted(experiments_by_id)
            ]
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            rows = _predict_hashimoto_residue_cohort(
                experiments,
                load_yaml(REPO_ROOT / "data/vapor_pressures.yaml"),
                preform_dimensions_mm=geometry,
                code_revision=revision,
                engine=engine.value,
            )
            cache[cache_key] = {
                row.experiment_id + "::" + row.alpha_arm: row for row in rows
            }
        rows_by_key = cache[cache_key]
        if "__cohort_error__" in rows_by_key:
            raise ValueError(str(rows_by_key["__cohort_error__"]))
        primary_key = (
            reference.experiment_id + "::alpha_common_unity_sensitivity"
        )
        production_key = reference.experiment_id + "::alpha_runtime_catalog"
        primary = rows_by_key.get(primary_key)
        production = rows_by_key.get(production_key)
        if primary is None or production is None:
            raise ValueError("hashimoto_prediction_row_missing")
        primary_provenance = dict(primary.provenance)
        sources = (
            *sources,
            *openimcc_engine_identity_sources(
                primary_provenance.get("engine_binding_identity")
            ),
        )
        consumed_provenance = {
            "observation_id": reference.observation_id,
            "source_id": reference.source_id,
            "experiment_id": reference.experiment_id,
            "locator": to_plain(reference.locator),
            "read_from": reference.read_from,
        }
        primary_provenance.update(
            {
                "consumed_row": consumed_provenance,
                "production_alpha_arm": {
                    "alpha_arm": production.alpha_arm,
                    "primary_geometry_policy_id": production.primary_geometry_policy_id,
                    "primary_oxide_wt_pct": dict(production.primary_oxide_wt_pct),
                    "geometry_oxide_wt_pct": {
                        name: dict(values)
                        for name, values in production.geometry_oxide_wt_pct.items()
                    },
                    "sensitivity_band_wt_pct": dict(production.sensitivity_band_wt_pct),
                    "provenance": dict(production.provenance),
                },
            }
        )
        primary_geometry = primary.primary_geometry_policy_id
        run_refusal = (primary.provenance.get("geometry_refusal_by_geometry") or {}).get(
            primary_geometry
        )
        if run_refusal:
            from simulator.battery.residue import ResidueInventoryRefusal

            raise ResidueInventoryRefusal(
                str(run_refusal.get("reason") or "hashimoto_run_refused"),
                json.dumps(run_refusal, sort_keys=True, default=str),
            )
        formula = reference.identity.species.formula
        value = primary.primary_oxide_wt_pct.get(formula)
        band = primary.sensitivity_band_wt_pct.get(formula)
        if value is None or band is None:
            raise ValueError("hashimoto_oxide_prediction_missing:" + formula)
        notices = [
            Notice(
                kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
                affected_quantities=(Quantity.RESIDUE_COMPONENT_COMPOSITION,),
                reason="feo_melt_redox_stack2_pending; regimes=M5,M2,M4",
                origin="hashimoto-residue-r4",
                band="1973-2273 K; engine tip 191960ce8",
            )
        ]
        integration = primary.provenance.get("integration") or {}
        if integration.get("refinement_status") == "unconverged_at_cap":
            notices.append(
                Notice(
                    kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
                    affected_quantities=(Quantity.RESIDUE_COMPONENT_COMPOSITION,),
                    reason="residue_time_refinement_unconverged; finest prediction retained",
                    origin="hashimoto-residue-r3",
                    band=f"N={integration.get('steps')}; N_cap=256",
                )
            )
        if primary.provenance.get("openimcc_liquid_row_extrapolated"):
            notices.append(
                Notice(
                    kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
                    affected_quantities=(Quantity.RESIDUE_COMPONENT_COMPOSITION,),
                    reason="OpenIMCC liquid-row extrapolation below fitted temperature floor",
                    origin="hashimoto-residue-addendum-a",
                    band="liquid-row floors are recorded in provenance",
                )
            )
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(
                state=ExecutionState.PRODUCED,
                call_evidence=f"{engine.value}:hashimoto:{reference.experiment_id}:alpha_common_unity_sensitivity",
            ),
            value=Decimal(str(value)),
            unit=QUANTITY_UNITS[Quantity.RESIDUE_COMPONENT_COMPOSITION],
            coefficient_sources=sources,
            lineage_complete=False,
            notices=tuple(notices),
            # The source-resolved Phase.L identity denotes the modeled run-end
            # liquid's bulk oxide inventory; quenching preserves that inventory.
            identity=reference.identity,
            version=str(primary.provenance.get("code_revision") or "unknown"),
            refusal_detail=primary_provenance,
        )
    except Exception as exc:  # the scorer records typed per-cell refusal and continues
        reason = getattr(exc, "reason", None) or str(exc) or type(exc).__name__
        if cache_key not in cache:
            cache[cache_key] = {"__cohort_error__": str(reason)}
        detail: dict[str, object] = {
            "reason": str(reason),
            "engine": engine.value,
            "source_id": reference.source_id,
            "experiment_id": reference.experiment_id,
        }
        if getattr(exc, "detail", None):
            detail["detail"] = str(exc.detail)
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(state=ExecutionState.NOT_PROBED),
            coefficient_sources=sources,
            refusal_reason=(
                RefusalReason.OPENIMCC_NOT_IMPORTABLE
                if str(reason) == "openimcc_not_importable"
                else RefusalReason.UNSUPPORTED
            ),
            refusal_detail=detail,
            identity=reference.identity,
        )


_SOSSI_SOURCE_ID = "kems-012-sossi-2019"


def _sossi_residue_prediction(
    context: ScoreContext,
    reference: Observation,
    engine: Engine,
    cache: dict[tuple[str, Engine], Mapping[str, object]],
) -> EnginePrediction:
    """Predict one Sossi Mn/Ti cell from the shared per-run inventory result."""
    channel = ENGINE_CHANNELS[engine]
    sources = ENGINE_COEFFICIENT_SOURCES[engine]
    formula = reference.identity.species.formula
    if formula not in {"Mn", "Ti"}:
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(state=ExecutionState.UNSUPPORTED),
            coefficient_sources=sources,
            lineage_complete=False,
            refusal_reason=RefusalReason.OUTSIDE_SUPPORTED_SPECIES,
            refusal_detail={
                "reason": "channel_missing",
                "element": formula,
                "quantity": Quantity.RESIDUE_COMPONENT_COMPOSITION.value,
            },
            identity=reference.identity,
        )
    cache_key = (_SOSSI_SOURCE_ID, engine)
    try:
        from simulator.battery.residue import (
            ResidueInventoryRefusal,
            _predict_sossi_residue_cohort,
        )

        if engine not in {Engine.INTERNAL_ANALYTICAL, Engine.OPENIMCC}:
            raise ValueError("sossi_engine_unsupported")
        if cache_key not in cache:
            live_rows = [
                row
                for row in context.observations.values()
                if row.source_id == _SOSSI_SOURCE_ID
                and isinstance(row.identity, Identity)
                and quantity_token(row.identity)
                is Quantity.RESIDUE_COMPONENT_COMPOSITION
                and row.admission.status.value == "admitted"
            ]
            experiments_by_run: dict[str, Mapping[str, object]] = {}
            trace_ppm_by_experiment: dict[str, dict[str, float]] = {}
            buffered_fO2_by_experiment: dict[str, float] = {}
            for row in live_rows:
                short_id = row.experiment_id.rsplit("::", 1)[-1]
                experiments_by_run[short_id] = {}
                point = row.point_conditions or {}
                oxygen = point.get("fO2_log")
                if oxygen is not None and oxygen.state.is_value:
                    buffered_fO2_by_experiment[short_id] = float(oxygen.state.value)
                element = row.identity.species.formula
                if element in {"Mn", "Ti"}:
                    trace = point.get("starting_component_ppm")
                    if trace is not None and trace.state.is_value:
                        trace_ppm_by_experiment.setdefault(short_id, {})[
                            element
                        ] = float(trace.state.value)
            if len(experiments_by_run) != 43:
                raise ResidueInventoryRefusal(
                    "sossi_cohort_invalid",
                    f"scorer found {len(experiments_by_run)} Sossi runs, expected 43",
                )
            source = load_yaml(
                REPO_ROOT / "data/literature/extracts/kems-012-sossi-2019.yaml"
            )
            source_experiments = {
                str(item.get("experiment_id")): item
                for item in source.get("experiments", ())
                if isinstance(item, Mapping)
            }
            missing_runs = set(experiments_by_run) - set(source_experiments)
            if missing_runs:
                raise ResidueInventoryRefusal(
                    "sossi_experiment_missing_from_extract", ",".join(sorted(missing_runs))
                )
            experiments = [source_experiments[key] for key in sorted(experiments_by_run)]
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            rows = _predict_sossi_residue_cohort(
                experiments,
                load_yaml(REPO_ROOT / "data/vapor_pressures.yaml"),
                starting_trace_ppm_by_experiment=trace_ppm_by_experiment,
                buffered_fO2_log_by_experiment=buffered_fO2_by_experiment,
                code_revision=revision,
                engine=engine.value,
            )
            cache[cache_key] = {
                row.experiment_id: row for row in rows
            }
        if "__cohort_error__" in cache[cache_key]:
            raise ValueError(str(cache[cache_key]["__cohort_error__"]))
        short_id = reference.experiment_id.rsplit("::", 1)[-1]
        prediction = cache[cache_key].get(short_id)
        if prediction is None:
            raise ValueError("sossi_prediction_row_missing")
        if formula in prediction.channel_missing_elements:
            raise ResidueInventoryRefusal(
                "channel_missing", f"no {formula} evaporation channel for {engine.value}"
            )
        value = prediction.primary_element_ppm.get(formula)
        band = prediction.sensitivity_band_ppm.get(formula)
        if value is None or band is None or value <= 0.0:
            raise ResidueInventoryRefusal(
                "residue_metric_domain", f"no positive Sossi {formula} prediction"
            )
        provenance = dict(prediction.provenance)
        sources = (
            *sources,
            *openimcc_engine_identity_sources(
                provenance.get("engine_binding_identity")
            ),
        )
        provenance["consumed_row"] = {
            "observation_id": reference.observation_id,
            "source_id": reference.source_id,
            "experiment_id": reference.experiment_id,
            "locator": to_plain(reference.locator),
            "read_from": reference.read_from,
        }
        provenance["predicted_element_ppm"] = value
        provenance["sensitivity_band_ppm_for_cell"] = band
        notices = [
            Notice(
                kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
                affected_quantities=(Quantity.RESIDUE_COMPONENT_COMPOSITION,),
                reason="bc_open_furnace_langmuir_limit_diagnostic",
                origin="sossi-residue-r5",
                band="open furnace at 101325 Pa; no gas-film resistance",
            ),
            Notice(
                kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
                affected_quantities=(Quantity.RESIDUE_COMPONENT_COMPOSITION,),
                reason="pt_wire_loop_bead_geometry_assumed",
                origin="sossi-residue-r5",
                band=str(band),
            ),
        ]
        if any(
            details.get("status") == "unconverged_at_cap"
            for details in (provenance.get("integration") or {}).values()
        ):
            notices.append(
                Notice(
                    kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
                    affected_quantities=(Quantity.RESIDUE_COMPONENT_COMPOSITION,),
                    reason="residue_time_refinement_unconverged; finest prediction retained",
                    origin="sossi-residue-r5",
                    band=f"N_cap=256; experiment={short_id}",
                )
            )
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(
                state=ExecutionState.PRODUCED,
                call_evidence=f"{engine.value}:sossi:{short_id}:alpha_runtime_catalog",
            ),
            value=Decimal(str(value)),
            unit=QUANTITY_UNITS[Quantity.RESIDUE_COMPONENT_COMPOSITION],
            coefficient_sources=sources,
            lineage_complete=False,
            notices=tuple(notices),
            identity=reference.identity,
            version=str(provenance.get("code_revision") or "unknown"),
            refusal_detail=provenance,
        )
    except Exception as exc:  # preserve a typed per-cell absence and keep scoring
        reason = getattr(exc, "reason", None) or str(exc) or type(exc).__name__
        if cache_key not in cache:
            cache[cache_key] = {"__cohort_error__": str(reason)}
        if reason == "oxygen_condition_missing":
            refusal = RefusalReason.IDENTITY_INCOMPLETE
        elif reason == "channel_missing":
            refusal = RefusalReason.OUTSIDE_SUPPORTED_SPECIES
        elif reason == "openimcc_not_importable":
            refusal = RefusalReason.OPENIMCC_NOT_IMPORTABLE
        else:
            refusal = RefusalReason.UNSUPPORTED
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(state=ExecutionState.NOT_PROBED),
            coefficient_sources=sources,
            refusal_reason=refusal,
            refusal_detail={
                "reason": str(reason),
                "engine": engine.value,
                "source_id": reference.source_id,
                "experiment_id": reference.experiment_id,
                "detail": str(getattr(exc, "detail", "")),
            },
            identity=reference.identity,
        )


def _is_bulk_not_liquid_composition(observation: Observation) -> bool:
    """Read the source's two-phase marker; never classify from composition values."""

    identity = observation.identity
    if isinstance(identity, Identity):
        phase = identity.species.phase
        if isinstance(phase, State) and phase.is_unknown:
            reason = phase.reason or ""
            if (
                TWO_PHASE_BULK_COMPOSITION_STATUS in reason
                or TWO_PHASE_BULK_COMPOSITION_PHASE_MARKER in reason
            ):
                return True
    return any(
        notice.band == TWO_PHASE_BULK_COMPOSITION_STATUS
        for notice in observation.notices
    )


def _none_uncertainty() -> Uncertainty:
    from simulator.battery.enums import UncertaintyKind

    return Uncertainty(kind=UncertaintyKind.NONE)


def compile_residual(
    reference: Observation,
    engine: Engine,
    *,
    context: ScoreContext,
    prediction: EnginePrediction | None = None,
    comparison_ids: set[str] | None = None,
    predict: Callable[..., EnginePrediction] | None = None,
    handles: Mapping[str, object] | None = None,
    lineage_observation_id: str | None = None,
    table_index: Mapping[tuple[str, Quantity], tuple[Observation, ...]] | None = None,
    derived_band: DecisionBand | None = None,
    point_observations: Sequence[Observation] | None = None,
) -> tuple[Residual, Observation | None]:
    _require_score_engine(engine)
    reference = _fusion_comparison_reference(reference, engine=engine)
    identity = reference.identity
    quantity = quantity_token(identity) if isinstance(identity, Identity) else None
    formula = identity.species.formula if isinstance(identity, Identity) else ""
    rail = rail_for_quantity(quantity, species_formula=formula)
    T = temperature_of(identity) if isinstance(identity, Identity) else None
    key = residual_key(
        reference_id=reference.observation_id,
        quantity=quantity,
        engine=engine,
        rail=rail,
        temperature_K=T,
    )
    origin = context.origins.get(reference.observation_id)
    review_status = context.extract_review.get(reference.source_id or "")
    experiment = context.experiments.get(reference.experiment_id)
    implied_alpha_reference = _implied_alpha_activity_observation(reference)
    gate_reference = implied_alpha_reference or reference
    if experiment is not None:
        gates = run_validity_gates(
            experiment,
            gate_reference,
            tables=_gate_tables(reference, context.observations, table_index),
            point_observations=point_observations,
        )
    else:
        from simulator.battery.validity import GateCheck

        gates = GateOutcome(
            passed=False,
            reason=RefusalReason.REFERENTIAL_INTEGRITY,
            checks=(GateCheck("experiment", False, {"reason": "missing experiment"}),),
            primary_check="experiment",
        )

    bench = (
        None
        if experiment is None
        else _bench_for_score(experiment, context.benches)
    )
    flagged_notices = _flagged_stratum_notices(reference, experiment, gates, bench)
    figure_only_notices: tuple[Notice, ...] = ()
    evidence_class = reference.evidence.class_
    figure_only = (
        evidence_class.is_value
        and evidence_class.value is EvidenceClass.FIGURE_ONLY
    )
    if figure_only:
        figure_only_notices = (_figure_only_notice(quantity, reference),)
    notices = union_notices(reference.notices, flagged_notices, figure_only_notices)
    comparison_ids = comparison_ids or {reference.observation_id}

    def _refused(
        reason: RefusalReason,
        detail: Mapping[str, object],
        *,
        execution: Execution,
        extra_notices: tuple[Notice, ...] = (),
        candidate: Observation | None = None,
        source_relation: SourceRelation = SourceRelation.UNKNOWN,
        exclusions: tuple[str, ...] = (),
    ) -> tuple[Residual, Observation | None]:
        all_notices = union_notices(notices, extra_notices)
        excl = exclusions or ("status_match_or_mismatch",)
        if (
            not _reference_has_measured_evidence(reference)
            and "reference_measured_evidence" not in excl
        ):
            excl = (*excl, "reference_measured_evidence")
        return (
            Residual(
                key=key,
                reference=reference.observation_id,
                execution=execution,
                rail=rail,
                status=ResidualStatus.REFUSED,
                source_relation=source_relation,
                score_eligible=False,
                exclusions=excl,
                notices=all_notices,
                candidate=None if candidate is None else candidate.observation_id,
                candidate_request=None
                if candidate is not None
                else CandidateRequest(
                    reference.experiment_id,
                    quantity or Quantity.P_SAT,
                    engine,
                    ENGINE_CHANNELS[engine],
                ),
                numeric=None,
                refusal=ResidualRefusal(reason, dict(detail), check_refs=_check_refs(gates, reason)),
            ),
            candidate,
        )

    if quantity is None:
        detail: dict[str, object] = {"reason": "quantity_unknown"}
        if reference.value.kind is ValueKind.CATEGORICAL:
            detail["value_kind"] = ValueKind.CATEGORICAL.value
            if isinstance(identity, Identity) and identity.quantity.is_unknown:
                detail["quantity_reason"] = identity.quantity.reason
        return _refused(
            RefusalReason.IDENTITY_UNKNOWN,
            detail,
            execution=Execution(state=ExecutionState.NOT_PROBED),
            exclusions=("status_match_or_mismatch", "finite_numeric_point_endpoints"),
        )
    if rail is None:
        return _refused(
            RefusalReason.UNSUPPORTED,
            {
                "reason": no_headline_rail_reason(quantity, species_formula=formula),
                "quantity": quantity.value,
                "species_formula": formula,
            },
            execution=Execution(state=ExecutionState.NOT_PROBED),
            exclusions=("status_match_or_mismatch",),
        )
    if engine in SINGLE_LIQUID_ENGINES and _is_bulk_not_liquid_composition(reference):
        return _refused(
            RefusalReason.BULK_NOT_LIQUID_COMPOSITION,
            {
                "reason": RefusalReason.BULK_NOT_LIQUID_COMPOSITION.value,
                "composition_status": TWO_PHASE_BULK_COMPOSITION_STATUS,
                "engine": engine.value,
            },
            execution=Execution(state=ExecutionState.NOT_PROBED),
        )
    if (
        quantity is Quantity.ACTIVITY
        and reference.evidence.class_.is_unknown
        and isinstance(identity, Identity)
        and identity.reference_state is not None
        and identity.reference_state.is_unknown
    ):
        return _refused(
            RefusalReason.IDENTITY_UNKNOWN,
            {
                "fields": ["reference_state"],
                "reason": "activity_reference_state_unknown",
                "detail": identity.reference_state.reason,
            },
            execution=Execution(state=ExecutionState.NOT_PROBED),
            exclusions=("identity_equal", "status_match_or_mismatch"),
        )
    if point_magnitude(reference.value) is None:
        reason_token = "value_unknown"
        if reference.value.kind is ValueKind.UNAVAILABLE:
            reason_token = reference.value.unavailable_reason or "value_unavailable"
        return _refused(
            RefusalReason.METRIC_DOMAIN,
            {"reason": reason_token, "value_kind": reference.value.kind.value},
            execution=Execution(state=ExecutionState.NOT_PROBED),
            exclusions=("status_match_or_mismatch", "finite_numeric_point_endpoints"),
        )
    if parse_species_formula(formula) is None:
        return _refused(
            RefusalReason.IDENTITY_UNKNOWN,
            {"reason": "species_formula_unparsed", "formula": formula},
            execution=Execution(state=ExecutionState.NOT_PROBED),
            exclusions=("identity_equal",),
        )
    apparatus_flagged = any(
        notice.kind is NoticeKind.UNVERIFIED_APPARATUS for notice in flagged_notices
    )
    scoring_gates = (
        replace(gates, passed=True, reason=None, primary_check=None)
        if apparatus_flagged
        else gates
    )
    if not gates.passed and not apparatus_flagged:
        return _refused(
            gates.reason or RefusalReason.INVALID_SOURCE,
            {"primary_check": gates.primary_check, "checks": [c.name for c in gates.checks]},
            execution=Execution(state=ExecutionState.NOT_PROBED),
            exclusions=("validity_gates_pass", "status_match_or_mismatch"),
        )

    missing_fusion_input = next(
        (
            notice
            for notice in notices
            if notice.reason.startswith("fusion conversion missing input:")
            or notice.reason.startswith("fusion conversion missing input for ")
        ),
        None,
    )
    if missing_fusion_input is not None:
        if "JANAF solid table " in missing_fusion_input.reason:
            missing_input = "JANAF solid polymorph matching the selected table"
        elif "no temperature_K" in missing_fusion_input.reason:
            missing_input = "activity observation temperature_K"
        else:
            missing_input = "JANAF solid/liquid formation Gibbs rows"
        return _refused(
            RefusalReason.IDENTITY_INCOMPLETE,
            {
                "reason": "solid_liquid_reference_conversion_missing_input",
                "missing_input": missing_input,
                "notice": missing_fusion_input.reason,
            },
            execution=Execution(state=ExecutionState.NOT_PROBED),
            exclusions=("reference_state_conversion_inputs_present",),
        )

    if prediction is None:
        predictor = predict or predict_with_engine
        predictor_kwargs: dict[str, object] = {
            "handles": handles,
            "experiment": experiment,
        }
        # The recorded bench goes to the built-in predictor whether it is
        # selected implicitly (predict=None) or explicitly
        # (predict=predict_with_engine): the reactive-cell label follows the
        # bench, not the call style (b-693/b-691 r2 finding 6). Caller-supplied
        # predictors keep the handles/experiment keyword contract.
        if predictor is predict_with_engine and experiment is not None:
            recorded = _bench_for_score(experiment, context.benches)
            if recorded is not None:
                predictor_kwargs["bench"] = recorded
        prediction = predictor(
            engine,
            implied_alpha_reference or reference,
            **predictor_kwargs,
        )

    notices = union_notices(notices, prediction.notices)
    expanded_sources = expand_coefficient_sources(prediction.coefficient_sources)
    lineage_complete = lineage_complete_for(
        prediction.coefficient_sources,
        works=context.works,
        observations=context.observations,
        experiments=context.experiments,
    )
    prediction = replace(
        prediction,
        coefficient_sources=expanded_sources,
        lineage_complete=lineage_complete,
    )
    from simulator.battery.compilation_tier import (
        is_compilation_evidence,
        uncertainty_text,
    )

    relation_reference = reference
    if lineage_observation_id and lineage_observation_id != reference.observation_id:
        relation_reference = replace(reference, observation_id=lineage_observation_id)
    source_relation = resolve_source_relation(
        relation_reference,
        prediction.coefficient_sources,
        prediction.lineage_complete,
        works=context.works,
        observations=context.observations,
        experiments=context.experiments,
    )
    compilation = is_compilation_evidence(reference) or is_compilation_source(
        reference.source_id, origin
    )
    # Internal-consistency ledgers are not a scoring tier. Compilations
    # keep the relation resolve_source_relation returned: INDEPENDENT
    # stays independent, and SAME_INPUT / TRAINING is the same-source
    # flag (engine coefficients drawn from this compilation). Evidence
    # is not restamped and admission is not changed.
    if is_internal_consistency(origin):
        if source_relation is SourceRelation.INDEPENDENT:
            source_relation = SourceRelation.UNKNOWN
        notices = union_notices(
            notices,
            (
                Notice(
                    kind=NoticeKind.DERIVATION_USES_COMPILATION,
                    affected_quantities=(quantity,),
                    reason=CIRCULARITY_WARNING,
                    origin=reference.observation_id,
                ),
            ),
        )
    elif compilation and source_relation in {
        SourceRelation.SAME_INPUT,
        SourceRelation.TRAINING,
    }:
        notices = union_notices(
            notices,
            (
                Notice(
                    kind=NoticeKind.DERIVATION_USES_COMPILATION,
                    affected_quantities=(quantity,),
                    reason=(
                        f"{CIRCULARITY_WARNING} same-source "
                        f"compilation={reference.source_id} "
                        f"uncertainty={uncertainty_text(reference.uncertainty)}"
                    ),
                    origin=reference.observation_id,
                ),
            ),
        )

    if prediction.execution.state is not ExecutionState.PRODUCED or prediction.value is None:
        reason = prediction.refusal_reason or RefusalReason.UNSUPPORTED
        return _refused(
            reason,
            prediction.refusal_detail or {"reason": prediction.execution.state.value},
            execution=prediction.execution,
            extra_notices=prediction.notices,
            source_relation=source_relation,
        )

    expected_unit = QUANTITY_UNITS[quantity]
    if prediction.unit != expected_unit:
        return _refused(
            RefusalReason.UNSUPPORTED,
            {
                "reason": "engine_prediction_unit_mismatch",
                "quantity": quantity.value,
                "expected_unit": expected_unit,
                "prediction_unit": prediction.unit,
            },
            execution=prediction.execution,
            extra_notices=prediction.notices,
            source_relation=source_relation,
        )

    implied_alpha = implied_alpha_reference is not None
    if implied_alpha:
        if not _implied_alpha_coefficient_basis_matches(
            implied_alpha_reference, prediction
        ):
            return _refused(
                RefusalReason.COEFFICIENT_BASIS_MISMATCH,
                {
                    "reason": RefusalReason.COEFFICIENT_BASIS_MISMATCH.value,
                    "expected_basis": SINGLE_CATION_COEFFICIENT_BASIS,
                    "reported_basis": prediction.coefficient_basis,
                    "expected_formula": (
                        implied_alpha_reference.identity.species.formula
                    ),
                    "reported_formula": (
                        prediction.identity.species.formula
                        if isinstance(prediction.identity, Identity)
                        else None
                    ),
                },
                execution=prediction.execution,
                extra_notices=prediction.notices,
                source_relation=source_relation,
                exclusions=("coefficient_basis_match",),
            )
        measured_product = point_magnitude(reference.value)
        assert measured_product is not None
        if prediction.value <= 0 or not prediction.value.is_finite():
            return _refused(
                RefusalReason.METRIC_DOMAIN,
                {
                    "reason": "predicted_activity_coefficient_nonpositive_or_nonfinite",
                    "predicted_gamma": str(prediction.value),
                },
                execution=prediction.execution,
                extra_notices=prediction.notices,
                source_relation=source_relation,
                exclusions=("valid_metric_domain",),
            )
        implied_value = measured_product / prediction.value
        prediction = replace(
            prediction,
            value=implied_value,
            unit="dimensionless",
            identity=reference.identity,
        )

    candidate = candidate_observation(reference, prediction)
    compared_reference = reference.identity
    compared_candidate = candidate.identity
    if isinstance(compared_reference, Identity) and isinstance(compared_candidate, Identity):
        compared_reference = _comparison_identity(
            compared_reference, reference, prediction
        )
        compared_candidate = _comparison_identity(
            compared_candidate, reference, prediction
        )
    equal = identity_equal(compared_reference, compared_candidate)
    if equal.kind is not IdentityEqualKind.EQUAL:
        reason = (
            RefusalReason.IDENTITY_MISMATCH
            if equal.kind is IdentityEqualKind.IDENTITY_MISMATCH
            else RefusalReason.IDENTITY_UNKNOWN
            if equal.kind is IdentityEqualKind.IDENTITY_UNKNOWN
            else RefusalReason.INVALID_IDENTITY
        )
        return _refused(
            reason,
            {"fields": list(equal.fields), "detail": equal.detail},
            execution=prediction.execution,
            extra_notices=prediction.notices,
            candidate=candidate,
            source_relation=source_relation,
            exclusions=("identity_equal",),
        )

    ref_point = point_magnitude(reference.value)
    assert ref_point is not None
    if implied_alpha:
        numeric, metric_reason, metric_detail = _implied_alpha_numeric(prediction.value)
    else:
        cell_band = derived_band
        if (
            cell_band is None
            and quantity is Quantity.P_PARTIAL
            and rail is Rail.VAPOUR
            and experiment is not None
            and experiment.method.is_value
            and experiment.method.value is MethodToken.KNUDSEN_EFFUSION
        ):
            cell_band = _derive_kems_partial_pressure_band(
                context.observations, context.experiments, context.benches
            )
        if compilation and quantity is not Quantity.P_PARTIAL:
            cell_band = _printed_uncertainty_band(
                quantity,
                reference.uncertainty,
                ref_point,
                source_observation=reference,
            ) or cell_band
        row_operation = _residual_metric_operation(quantity, reference)
        own_band_operation: MetricOperation | None = None
        if figure_only:
            # Figure reading band comes only from this row's stored uncertainty.
            # Never fall back to the global measured KEMS cell band (that
            # borrows another source's envelope). Being the row's own band, it
            # is dimension-checked in the row's identity-aware metric (r2 F4).
            cell_band = _printed_uncertainty_band(
                quantity,
                reference.uncertainty,
                ref_point,
                source_observation=reference,
            )
            if cell_band is not None:
                own_band_operation = row_operation
        numeric, metric_reason, metric_detail = populate_numeric(
            quantity=quantity,
            candidate=prediction.value,
            reference=ref_point,
            source_relation=source_relation,
            metric_uncertainty=(
                reference.uncertainty
                if reference.uncertainty.kind is UncertaintyKind.PRINTED
                else None
            ),
            rail=rail,
            method=(
                experiment.method.value
                if experiment is not None and experiment.method.is_value
                else None
            ),
            observations=context.observations,
            experiments=context.experiments,
            derived_band=cell_band,
            operation_override=row_operation,
            band_operation=own_band_operation,
        )
    if numeric is None:
        return _refused(
            metric_reason or RefusalReason.METRIC_DOMAIN,
            metric_detail,
            execution=prediction.execution,
            extra_notices=prediction.notices,
            candidate=candidate,
            source_relation=source_relation,
            exclusions=("valid_metric_domain",),
        )
    # figure_only and reactive-cell-not-modelled keep their numeric band: they
    # are labels on a predicted quantity, not reasons the reading band is void.
    # Catalogue-composition on a figure row is the same kind of label: preserve
    # the stored reading band while keeping the row out of the certified subset.
    band_preserving_kinds = {
        NoticeKind.CELL_MATERIAL_INFERRED,
        NoticeKind.FIGURE_ONLY,
        NoticeKind.REACTIVE_CELL_NOT_MODELLED,
    }
    if figure_only:
        band_preserving_kinds.add(NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG)
    has_no_band_flag = any(
        notice.kind not in band_preserving_kinds for notice in flagged_notices
    ) or any(_is_fusion_conversion_notice(notice) for notice in notices)
    if has_no_band_flag:
        numeric = replace(numeric, decision_band=None)
    if implied_alpha:
        status = {
            "physically_impossible": ResidualStatus.MISMATCH,
            "outside_literature_band": ResidualStatus.NO_BAND,
            "consistent": ResidualStatus.MATCH,
        }[numeric.verdict or "consistent"]
    else:
        status = match_status(numeric)
    if has_no_band_flag:
        status = ResidualStatus.NO_BAND
    eligibility_reference = reference
    eligibility_candidate = candidate
    if isinstance(compared_reference, Identity) and isinstance(compared_candidate, Identity):
        eligibility_reference = replace(reference, identity=compared_reference)
        eligibility_candidate = replace(candidate, identity=compared_candidate)
    conjuncts = build_conjuncts(
        status=status,
        reference=eligibility_reference,
        candidate=eligibility_candidate,
        numeric=numeric,
        source_relation=source_relation,
        lineage_complete=prediction.lineage_complete,
        gates=scoring_gates,
        extract_review_status=review_status,
        comparison_ids=comparison_ids,
        notices=notices,
        oxygen_balance_effusion_solved=has_own_engine_solved_oxygen_balance(
            prediction.engine, prediction.notices
        ),
    )
    if is_internal_consistency(origin) or compilation:
        conjuncts = replace(conjuncts, reference_measured_evidence=False)
    if is_sf04_workbook(reference):
        conjuncts = replace(conjuncts, reference_measured_evidence=False)
    eligible = conjuncts.eligible()
    return (
        Residual(
            key=key,
            reference=reference.observation_id,
            execution=prediction.execution,
            rail=rail,
            status=status,
            source_relation=source_relation,
            score_eligible=eligible,
            exclusions=conjuncts.exclusions(),
            notices=notices,
            candidate=candidate.observation_id,
            candidate_request=None,
            numeric=numeric,
            refusal=None,
        ),
        candidate,
    )


def _check_refs(gates: GateOutcome, reason: RefusalReason) -> tuple[str, ...]:
    if not gates.passed and gates.reason is reason and gates.primary_check:
        return (gates.primary_check,)
    return ()


def _gate_tables(
    reference: Observation,
    observations: Mapping[str, Observation],
    table_index: Mapping[tuple[str, Quantity], tuple[Observation, ...]] | None = None,
) -> tuple[dict, ...]:
    from simulator.battery.validate import _table_payloads

    return _table_payloads(reference, observations, table_index)


def load_score_context(
    root: Path | None = None,
    *,
    sources: Sequence[str] | None = None,
) -> ScoreContext:
    """Load the scoring store.

    ``sources`` is a case-insensitive substring filter on observation-store
    and extract filenames (``<source_id>.yaml``). Matching rows are kept
    when the token also appears in the observation id or source id. Works,
    experiments, and benches stay complete. ``None`` loads the full store.
    """

    root = root or REPO_ROOT
    tokens = source_filter_tokens(sources)
    # The unfiltered path keeps the historical call exactly, so callers and test
    # doubles that replace load_migrated_store(root) see no signature change.
    if sources is None:
        works, experiments, observations = load_migrated_store(root)
    else:
        works, experiments, observations = load_migrated_store(root, sources=sources)
    benches = load_migrated_benches(root)
    origins: dict[str, str] = {}
    extract_review: dict[str, str | None] = {}
    literature = root / "data" / "literature"
    for directory in (literature / "extracts-v2", literature / "observations-v2"):
        if not directory.is_dir():
            continue
        for path in iter_observation_store_paths(directory):
            if not path_matches_source_tokens(path, directory, tokens):
                continue
            doc = load_yaml(path)
            if not isinstance(doc, Mapping):
                continue
            try:
                origin_key = str(path.relative_to(directory))
            except ValueError:
                origin_key = path.name
            for raw in doc.get("observations") or []:
                if isinstance(raw, Mapping) and raw.get("observation_id"):
                    observation_id = str(raw["observation_id"])
                    raw_source = raw.get("source_id")
                    if not observation_matches_source_tokens(
                        observation_id,
                        None if raw_source is None else str(raw_source),
                        tokens,
                    ):
                        continue
                    origins[observation_id] = origin_key
    extracts = literature / "extracts"
    if extracts.is_dir():
        for path in sorted(extracts.glob("*.yaml")):
            if not path_matches_source_tokens(path, extracts, tokens):
                continue
            doc = load_yaml(path)
            if not isinstance(doc, Mapping):
                continue
            source_id = str(doc.get("source_id") or path.stem)
            extract_review[source_id] = (
                None if doc.get("review_status") is None else str(doc.get("review_status"))
            )
    return ScoreContext(
        works=works,
        experiments=experiments,
        observations=observations,
        origins=origins,
        extract_review=extract_review,
        hostname=socket.gethostname(),
        benches=benches,
    )


def comparison_candidates(context: ScoreContext) -> tuple[Observation, ...]:
    """Measured or figure_only evidence, admitted or pending (pending is diagnostic).

    FIGURE_ONLY rows are candidates (owner mandate: score everything; evidence
    class is a label). They stay out of the certified/measured headline via
    the figure_only notice and by not being in MEASURED_EVIDENCE.
    """

    out: list[Observation] = []
    for obs in context.observations.values():
        ev = obs.evidence.class_
        if not (
            ev.is_value
            and (
                ev.value in MEASURED_EVIDENCE
                or ev.value is EvidenceClass.FIGURE_ONLY
            )
        ):
            continue
        if obs.admission.status not in {AdmissionStatus.ADMITTED, AdmissionStatus.PENDING}:
            continue
        out.append(obs)
    return tuple(sorted(out, key=lambda o: o.observation_id))


def diagnostic_references(context: ScoreContext) -> tuple[Observation, ...]:
    """Compilation / ledger / model-derived rows: never empirical headline."""

    out: list[Observation] = []
    for obs in context.observations.values():
        origin = context.origins.get(obs.observation_id)
        if is_internal_consistency(origin) or is_compilation_source(obs.source_id, origin):
            out.append(obs)
            continue
        if is_sf04_workbook(obs):
            out.append(obs)
    return tuple(sorted(out, key=lambda o: o.observation_id))


def _refusal_diagnostic_references(context: ScoreContext) -> tuple[Observation, ...]:
    """Rows excluded from comparisons but retained so scoring can explain why."""

    out: list[Observation] = []
    for obs in context.observations.values():
        if not isinstance(obs.identity, Identity):
            continue
        evidence_class = obs.evidence.class_
        status = obs.admission.status
        activity_reference_unknown = (
            status in {AdmissionStatus.ADMITTED, AdmissionStatus.PENDING}
            and evidence_class.is_unknown
            and quantity_token(obs.identity) is Quantity.ACTIVITY
            and obs.identity.reference_state is not None
            and obs.identity.reference_state.is_unknown
        )
        categorical_quantity_unknown = (
            status is AdmissionStatus.REJECTED
            and obs.value.kind is ValueKind.CATEGORICAL
            and obs.identity.quantity.is_unknown
        )
        if activity_reference_unknown or categorical_quantity_unknown:
            out.append(obs)
    return tuple(sorted(out, key=lambda o: o.observation_id))


def _score_store_with_decisions(
    context: ScoreContext,
    *,
    engines: Sequence[Engine] | None = None,
    rail: Rail | None = None,
    work_id: str | None = None,
    limit: int | None = None,
    include_diagnostics: bool = True,
    predict: Callable[..., EnginePrediction] | None = None,
    handles: Mapping[str, object] | None = None,
    _stream: _ResidualJsonlStream | None = None,
) -> tuple[tuple[Residual, ...], dict[str, Observation], list[dict[str, object]]]:
    engine_set = (
        _validated_score_engines(tuple(engines))
        if engines is not None
        else SCORE_ENGINE_SET
    )
    provided_handles = dict(handles or {})
    resolved_handles = dict(provided_handles)
    refs = list(comparison_candidates(context))
    fusion_diagnostic_ids: set[str] = set()
    admitted_model_derived_ids: set[str] = set()
    if include_diagnostics:
        seen = {o.observation_id for o in refs}
        for obs in diagnostic_references(context):
            if obs.observation_id not in seen:
                refs.append(obs)
                seen.add(obs.observation_id)
        for obs in context.observations.values():
            converted = _fusion_comparison_reference(obs)
            if converted is not obs:
                if any(
                    _is_fusion_conversion_notice(notice) for notice in converted.notices
                ):
                    fusion_diagnostic_ids.add(obs.observation_id)
                if obs.observation_id not in seen:
                    refs.append(converted)
                    seen.add(obs.observation_id)
        for obs in context.observations.values():
            evidence_class = obs.evidence.class_
            if (
                obs.admission.status is not AdmissionStatus.ADMITTED
                or not evidence_class.is_value
                or evidence_class.value is not EvidenceClass.MODEL_DERIVED
                or obs.observation_id in seen
            ):
                continue
            refs.append(obs)
            seen.add(obs.observation_id)
            admitted_model_derived_ids.add(obs.observation_id)
        for obs in _refusal_diagnostic_references(context):
            if obs.observation_id not in seen:
                refs.append(obs)
                seen.add(obs.observation_id)
    if work_id:
        filtered: list[Observation] = []
        for obs in refs:
            experiment = context.experiments.get(obs.experiment_id)
            if experiment is not None and experiment.work_id == work_id:
                filtered.append(obs)
            elif obs.source_id == work_id:
                filtered.append(obs)
        refs = filtered
    if rail is not None:
        kept: list[Observation] = []
        for obs in refs:
            quantity = quantity_token(obs.identity) if isinstance(obs.identity, Identity) else None
            formula = obs.identity.species.formula if isinstance(obs.identity, Identity) else ""
            if rail_for_quantity(quantity, species_formula=formula) is rail:
                kept.append(obs)
        refs = kept
    refs.sort(key=lambda o: o.observation_id)
    if limit is not None:
        refs = refs[: int(limit)]
    from simulator.battery.compilation_tier import (
        compilation_family,
        _compilation_series_point_count,
        _iter_compilation_series_points,
        is_compilation_evidence,
    )

    comparison_ids = {o.observation_id for o in comparison_candidates(context)}
    residuals: list[Residual] | None = [] if _stream is None else None
    candidates: dict[str, Observation] = {}
    compilation_family_by_reference: dict[str, tuple[str, Quantity]] = {}
    # Snapshot. Engine candidates are returned separately and are not
    # lineage inputs. The work-input index is this snapshot.
    observations = dict(context.observations)
    origins = context.origins
    live_context = replace(context, observations=observations, origins=origins)
    point_observations_by_experiment = _partial_pressure_observations_by_experiment(
        observations.values()
    )
    empirical_ids = comparison_ids
    started = time.monotonic()
    last_progress = started
    done = 0
    total = sum(
        _compilation_series_point_count(obs, origins.get(obs.observation_id))
        for obs in refs
    ) * max(len(engine_set), 1)
    family_residuals: dict[tuple[str, Quantity, str, str], list[Decimal]] = {}
    from simulator.battery.validate import bound_work_inputs, build_printed_thermo_index

    table_index = build_printed_thermo_index(observations)
    kems_band = _derive_kems_partial_pressure_band(
        observations, context.experiments, context.benches
    )
    residue_prediction_cache: dict[tuple[str, Engine], Mapping[str, object]] = {}
    try:
        with bound_work_inputs(context.works, observations, context.experiments):
            for obs in refs:
                origin = origins.get(obs.observation_id)
                for point in _iter_compilation_series_points(obs, origin):
                    family_quantity: tuple[str, Quantity] | None = None
                    point_origin = origins.get(point.observation_id, origin or "")
                    diagnostic = (
                        obs.observation_id not in empirical_ids
                        or is_internal_consistency(point_origin)
                        or is_compilation_source(point.source_id, point_origin)
                        or is_compilation_evidence(point)
                        or is_sf04_workbook(point)
                    )
                    quantity = (
                        quantity_token(point.identity)
                        if isinstance(point.identity, Identity)
                        else None
                    )
                    thermo = quantity in FORMATION_QUANTITIES or quantity in PURE_STANDARD_THERMO
                    compilation_thermo = (
                        thermo
                        and (
                            is_compilation_evidence(point)
                            or is_compilation_source(point.source_id, point_origin)
                        )
                        and not is_internal_consistency(point_origin)
                        and not is_sf04_workbook(point)
                    )
                    if compilation_thermo and quantity is not None:
                        family_quantity = (
                            compilation_family(point.source_id, point_origin),
                            quantity,
                        )
                        if _stream is None:
                            compilation_family_by_reference[point.observation_id] = family_quantity
                    for engine in engine_set:
                        prediction = None
                        if (
                            diagnostic
                            and obs.observation_id not in admitted_model_derived_ids
                            and point.observation_id not in fusion_diagnostic_ids
                            and predict is None
                            and not compilation_thermo
                        ):
                            prediction = EnginePrediction(
                                engine=engine,
                                channel=ENGINE_CHANNELS[engine],
                                execution=Execution(state=ExecutionState.NOT_PROBED),
                                coefficient_sources=ENGINE_COEFFICIENT_SOURCES[engine],
                                lineage_complete=False,
                                refusal_reason=RefusalReason.UNSUPPORTED,
                                refusal_detail={"reason": "diagnostic_population"},
                                identity=point.identity if isinstance(point.identity, Identity) else None,
                            )
                        if (
                            predict is None
                            and isinstance(point.identity, Identity)
                            and quantity is Quantity.RESIDUE_COMPONENT_COMPOSITION
                            and point.source_id == _HASHIMOTO_SOURCE_ID
                            and engine in {Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL}
                        ):
                            prediction = _hashimoto_residue_prediction(
                                context,
                                point,
                                engine,
                                residue_prediction_cache,
                            )
                        if (
                            predict is None
                            and isinstance(point.identity, Identity)
                            and quantity is Quantity.RESIDUE_COMPONENT_COMPOSITION
                            and point.source_id == _SOSSI_SOURCE_ID
                            and engine in {Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL}
                        ):
                            prediction = _sossi_residue_prediction(
                                context,
                                point,
                                engine,
                                residue_prediction_cache,
                            )
                        residual, candidate = compile_residual(
                            point,
                            engine,
                            context=live_context,
                            prediction=prediction,
                            comparison_ids=comparison_ids,
                            predict=predict,
                            handles=resolved_handles,
                            lineage_observation_id=(
                                obs.observation_id
                                if point.observation_id != obs.observation_id
                                else None
                            ),
                            table_index=table_index,
                            derived_band=kems_band,
                            point_observations=point_observations_by_experiment.get(
                                point.experiment_id, ()
                            ),
                        )
                        if _stream is None:
                            assert residuals is not None
                            residuals.append(residual)
                        else:
                            _stream.append(residual, candidate, family_quantity)
                        family_key = _family_pool_key(residual, family_quantity)
                        if family_key is not None:
                            family_residuals.setdefault(family_key, []).append(
                                residual.numeric.value  # type: ignore[union-attr]
                            )
                        if candidate is not None and _stream is None:
                            candidates[candidate.observation_id] = candidate
                        done += 1
                        now = time.monotonic()
                        if now - last_progress >= 60:
                            if _stream is not None:
                                _stream.flush()
                            progress = f"score progress {done}/{total} residuals"
                            if _stream is not None:
                                progress += f" written={_stream.count}"
                            print(
                                f"{progress} {int(now - started)}s "
                                f"host={context.hostname}",
                                flush=True,
                            )
                            last_progress = now
    finally:
        active_error = sys.exc_info()[1]
        close_errors: list[BaseException] = []
        for name, handle in resolved_handles.items():
            if provided_handles.get(name) is handle:
                continue
            backend = getattr(handle, "backend", None)
            close = getattr(backend, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException as exc:  # noqa: BLE001 - close every owned handle
                    close_errors.append(exc)
        if close_errors:
            if active_error is not None:
                for error in close_errors:
                    add_note = getattr(active_error, "add_note", None)
                    if callable(add_note):
                        add_note(f"engine handle cleanup failed: {error!r}")
                if not callable(getattr(active_error, "add_note", None)):
                    raise active_error from close_errors[0]
            else:
                first_error = close_errors[0]
                for error in close_errors[1:]:
                    add_note = getattr(first_error, "add_note", None)
                    if callable(add_note):
                        add_note(f"additional engine handle cleanup failure: {error!r}")
                raise first_error

    family_bands = {
        key: _residual_distribution_band(
            values, unit=key[2], family=key[0], quantity=key[1]
        )
        for key, values in family_residuals.items()
    }
    family_pool_sizes = {
        key: len(values)
        for key, values in family_residuals.items()
        if len(values) < MIN_DERIVED_BAND_N
    }
    family_residuals.clear()
    if _stream is not None:
        _stream.finalize(
            family_pool_sizes=family_pool_sizes,
            family_bands=family_bands,
            context=context,
            engines=engine_set,
        )
        return (), {}, []

    assert residuals is not None
    banded_residuals: list[Residual] = []
    for residual in residuals:
        family_quantity = compilation_family_by_reference.get(residual.reference)
        numeric = residual.numeric
        apply, band, no_band = _family_band_update(
            numeric=numeric,
            status=residual.status,
            key=residual.key,
            source_relation=residual.source_relation,
            family_quantity=family_quantity,
            flagged=_has_flagged_decision_notice(residual),
            family_pool_sizes=family_pool_sizes,
            family_bands=family_bands,
        )
        if not apply:
            banded_residuals.append(residual)
            continue
        assert numeric is not None
        if no_band:
            updated_numeric = replace(numeric, decision_band=None)
            banded_residuals.append(
                replace(
                    residual,
                    numeric=updated_numeric,
                    status=ResidualStatus.NO_BAND,
                )
            )
            continue
        assert band is not None
        updated_numeric = replace(numeric, decision_band=band)
        banded_residuals.append(
            replace(
                residual,
                numeric=updated_numeric,
                status=match_status(updated_numeric),
            )
        )
    residuals = banded_residuals
    residuals.sort(
        key=lambda r: (
            "" if r.rail is None else r.rail.value,
            r.reference,
            r.key,
        )
    )
    scored = tuple(residuals)
    return scored, candidates, _compilation_decision_strata(scored, context)


def _score_store_to_jsonl(
    context: ScoreContext,
    path: Path,
    *,
    root: Path | None = None,
    engines: Sequence[Engine] | None = None,
    rail: Rail | None = None,
    work_id: str | None = None,
    limit: int | None = None,
    include_diagnostics: bool = True,
    predict: Callable[..., EnginePrediction] | None = None,
    handles: Mapping[str, object] | None = None,
) -> tuple[int, _ResidualReportMetadata]:
    """Score directly to a sorted ``.partial`` JSONL without retaining rows."""

    stream = _ResidualJsonlStream(path, root=root)
    try:
        _score_store_with_decisions(
            context,
            engines=engines,
            rail=rail,
            work_id=work_id,
            limit=limit,
            include_diagnostics=include_diagnostics,
            predict=predict,
            handles=handles,
            _stream=stream,
        )
        return stream.count, _ResidualReportMetadata(
            family_band_values=dict(stream.band_values),
            headline_band_values=dict(stream.headline_band_values),
            tier_band_values=dict(stream.tier_band_values),
            aggregate=stream.report_aggregate,
        )
    finally:
        stream.close()


def score_store(
    context: ScoreContext,
    *,
    engines: Sequence[Engine] | None = None,
    rail: Rail | None = None,
    work_id: str | None = None,
    limit: int | None = None,
    include_diagnostics: bool = True,
    predict: Callable[..., EnginePrediction] | None = None,
    handles: Mapping[str, object] | None = None,
) -> tuple[tuple[Residual, ...], dict[str, Observation]]:
    """Score the store and keep the historical residual/candidate return shape."""

    residuals, candidates, _decision_strata = _score_store_with_decisions(
        context,
        engines=engines,
        rail=rail,
        work_id=work_id,
        limit=limit,
        include_diagnostics=include_diagnostics,
        predict=predict,
        handles=handles,
    )
    return residuals, candidates


def residual_to_plain(
    residual: Residual, candidate: Observation | None = None
) -> dict[str, object]:
    payload = to_plain(residual)
    assert isinstance(payload, dict)
    if candidate is not None:
        payload["candidate_observation"] = to_plain(candidate)
    return payload


def dumps_residual_line(
    residual: Residual, candidate: Observation | None = None
) -> str:
    payload = residual_to_plain(residual, candidate)
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def parse_migration_headline(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key, pattern in _MIGRATION_HEADLINE_FIELDS:
        match = pattern.search(text)
        if match is None:
            raise ValueError(f"migration report missing {key} headline")
        counts[key] = int(match.group(1))
    return counts


def store_git_revision(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%h", "--", *STORE_REVISION_PATHS],
        check=False,
        capture_output=True,
        text=True,
    )
    revision = result.stdout.strip()
    if result.returncode != 0 or not revision:
        detail = (result.stderr or result.stdout or "git log failed").strip()
        raise RuntimeError(f"could not derive store revision: {detail}")
    return revision


def derive_store_stamp(root: Path | None = None) -> dict[str, object]:
    """Git revision + migration headline. Never a hand-set identity."""

    root = root or REPO_ROOT
    headline = parse_migration_headline(
        (root / "data" / "battery" / "migration-report.md").read_text(encoding="utf-8")
    )
    return {
        "kind": STORE_STAMP_KIND,
        "revision": store_git_revision(root),
        **headline,
    }


def dumps_store_stamp(stamp: Mapping[str, object]) -> str:
    payload = {
        "kind": STORE_STAMP_KIND,
        "revision": str(stamp["revision"]),
        "rows_in": int(stamp["rows_in"]),
        "observations": int(stamp["observations"]),
        "works": int(stamp["works"]),
        "experiments": int(stamp["experiments"]),
        "queue": int(stamp["queue"]),
        "hard_issues": int(stamp["hard_issues"]),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class UnregeneratedLedgerStampError(ValueError):
    """Raised when a store stamp is requested for a ledger this run did not produce."""


def stamp_existing_residuals_jsonl(
    path: Path,
    stamp: Mapping[str, object] | None = None,
    *,
    root: Path | None = None,
) -> None:
    """Refuse to stamp a residuals ledger this run did not regenerate.

    A scoring-run writer emits the stamp with the residual body it produces.
    This named path exists so a prepend onto an already-written body cannot recur.
    """

    del path, stamp, root
    raise UnregeneratedLedgerStampError(
        "cannot attach a store stamp to a residuals ledger that was not regenerated in this run"
    )


def is_store_stamp_payload(payload: object) -> bool:
    return isinstance(payload, Mapping) and payload.get("kind") == STORE_STAMP_KIND


def store_stamp_has_known_revision(stamp: Mapping[str, object] | None) -> bool:
    if not isinstance(stamp, Mapping):
        return False
    revision = stamp.get("revision")
    return bool(revision) and str(revision) not in {"", "unknown"}


def load_residuals_stamp(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            payload = json.loads(line)
            return dict(payload) if is_store_stamp_payload(payload) else None
    return None


def store_stamp_mismatch_warning(
    recorded: Mapping[str, object] | None,
    live: Mapping[str, object],
) -> str | None:
    live_rev = str(live.get("revision") or "")
    if not store_stamp_has_known_revision(recorded):
        return (
            f"residuals ledger has no store revision; live store is `{live_rev}`"
        )
    assert recorded is not None
    recorded_rev = str(recorded["revision"])
    if recorded_rev != live_rev:
        return (
            f"residuals ledger recorded store `{recorded_rev}` "
            f"but the live store is `{live_rev}`"
        )
    return None


def emit_store_stamp_mismatch_warning(
    recorded: Mapping[str, object] | None,
    live: Mapping[str, object],
) -> str | None:
    warning = store_stamp_mismatch_warning(recorded, live)
    if warning:
        warnings.warn(warning, UserWarning, stacklevel=2)
    return warning


def format_store_stamp_report_lines(
    stamp: Mapping[str, object] | None,
    *,
    mismatch_warning: str | None = None,
) -> list[str]:
    if not store_stamp_has_known_revision(stamp):
        lines = [UNKNOWN_STORE_PROVENANCE_LINE]
    else:
        assert stamp is not None
        lines = [
            (
                f"This report measured store `{stamp['revision']}`: "
                f"{stamp['rows_in']} rows in, {stamp['observations']} observations, "
                f"{stamp['works']} works, {stamp['experiments']} experiments, "
                f"queue {stamp['queue']}, {stamp['hard_issues']} hard issues."
            ),
        ]
    if mismatch_warning:
        lines.extend(["", f"Warning: {mismatch_warning}"])
    return lines


def write_residuals_jsonl(
    residuals: Sequence[Residual],
    candidates: Mapping[str, Observation],
    path: Path,
    *,
    root: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = derive_store_stamp(root or REPO_ROOT)
    lines = [dumps_store_stamp(stamp)]
    lines.extend(
        dumps_residual_line(residual, candidates.get(residual.candidate or ""))
        for residual in residuals
    )
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def load_residuals_jsonl(path: Path) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    if not path.is_file():
        return ()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict) and not is_store_stamp_payload(payload):
            rows.append(payload)
    return tuple(rows)


def _iter_residuals_jsonl(path: Path) -> Iterable[dict[str, object]]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict) and not is_store_stamp_payload(payload):
                yield payload


@dataclass(frozen=True)
class _ResidualReportMetadata:
    family_band_values: Mapping[tuple[str, str, str, str], set[str]]
    headline_band_values: Mapping[tuple[str, str, str], set[str]]
    tier_band_values: Mapping[tuple[str, str, str, str, str, str], set[str]]
    aggregate: _ScorePayloadAccumulator | None = None


class _ResidualJsonlRows:
    """A replayable, line-at-a-time view of a residual JSONL file."""

    def __init__(
        self,
        path: Path,
        *,
        metadata: _ResidualReportMetadata | None = None,
    ) -> None:
        self.path = path
        self._typed_family_band_values = (
            {} if metadata is None else metadata.family_band_values
        )
        self._typed_headline_band_values = (
            {} if metadata is None else metadata.headline_band_values
        )
        self._typed_tier_band_values = (
            {} if metadata is None else metadata.tier_band_values
        )

    def __iter__(self) -> Iterable[dict[str, object]]:
        return _iter_residuals_jsonl(self.path)


def _count_residuals_jsonl(path: Path) -> int:
    return sum(1 for _ in _iter_residuals_jsonl(path))


def _median_abs(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(abs(v) for v in values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal(2)


def _median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal(2)


def _bias_to_scatter_ratio(
    signed_median: Decimal | None, mad: Decimal | None, n: int
) -> Decimal | None:
    if signed_median is None or mad is None or mad == 0 or n < MIN_DERIVED_BAND_N:
        return None
    return abs(signed_median) / mad


def _rms(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum((value * value for value in values), Decimal(0)) / Decimal(len(values))).sqrt()


def _iqr(values: Sequence[Decimal]) -> Decimal | None:
    """Interquartile range of ``values``; None when fewer than two samples."""

    if len(values) < 2:
        return None
    ordered = sorted(values)
    n = len(ordered)

    def _quartile(p: float) -> Decimal:
        # Inclusive rank; matches the descriptive IQR used in the report.
        pos = p * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = Decimal(str(pos - lo))
        return ordered[lo] + (ordered[hi] - ordered[lo]) * frac

    return _quartile(0.75) - _quartile(0.25)


def _measured_residuals(
    residuals: Sequence[Residual],
    context: ScoreContext | None,
) -> list[Residual]:
    """Keep only measured-evidence rows outside flagged and compilation tiers."""

    if context is None:
        return [
            residual
            for residual in residuals
            if not flagged_strata(residual.notices)
            and _reference_has_measured_evidence(
                None, exclusions=residual.exclusions
            )
        ]
    from simulator.battery.compilation_tier import compilation_row_observation

    return [
        residual
        for residual in residuals
        if not flagged_strata(residual.notices)
        if _reference_has_measured_evidence(
            context.observations.get(residual.reference),
            exclusions=residual.exclusions,
        )
        if compilation_row_observation(
            residual.reference, context.observations, context.origins
        )
        is None
    ]


def _compilation_residuals(
    residuals: Sequence[Residual],
    context: ScoreContext | None,
) -> list[Residual]:
    if context is None:
        return []
    from simulator.battery.compilation_tier import compilation_row_observation

    return [
        residual
        for residual in residuals
        if not flagged_strata(residual.notices)
        if compilation_row_observation(
            residual.reference, context.observations, context.origins
        )
        is not None
    ]


def _compilation_decision_strata(
    residuals: Sequence[Residual], context: ScoreContext | None
) -> list[dict[str, object]]:
    if context is None:
        return []
    from simulator.battery.compilation_tier import (
        compilation_family,
        compilation_origin,
        compilation_row_observation,
    )

    strata: dict[tuple[str, str, str, str], list[Residual]] = defaultdict(list)
    derived_n: dict[tuple[str, str, str], int] = defaultdict(int)
    for residual in residuals:
        if residual.numeric is None or residual.status is ResidualStatus.REFUSED or any(
            _is_flagged_stratum_notice(notice)
            or _is_fusion_conversion_notice(notice)
            for notice in residual.notices
        ):
            continue
        observation = compilation_row_observation(
            residual.reference, context.observations, context.origins
        )
        if observation is None or not isinstance(observation.identity, Identity):
            continue
        quantity = quantity_token(observation.identity)
        if quantity is None:
            continue
        family = compilation_family(
            observation.source_id,
            compilation_origin(residual.reference, context.origins),
        )
        engine = _engine_of(residual)
        key = (family, quantity.value, engine)
        band = None if residual.numeric is None else residual.numeric.decision_band
        if (
            residual.numeric is not None
            and band is None
            and not any(
                _is_flagged_stratum_notice(notice)
                or _is_fusion_conversion_notice(notice)
                for notice in residual.notices
            )
        ):
            derived_n[key] += 1
        strata[(*key, residual.source_relation.value)].append(residual)

    rendered = []
    for (family, quantity, engine, relation), bucket in sorted(strata.items()):
        values = [r.numeric.value for r in bucket if r.numeric is not None]
        center = _median(values)
        mad = None if center is None else _median([abs(value - center) for value in values])
        bands = [r.numeric.decision_band for r in bucket if r.numeric is not None and r.numeric.decision_band is not None]
        kinds = sorted({
            "printed" if band.rule == "source-printed per-cell uncertainty"
            else "derived_2xMAD" if "residual distribution" in band.rule
            else "legacy_fallback"
            for band in bands
        }) or ["no_band"]
        widths = sorted({str(band.value) for band in bands})
        units = sorted({band.unit for band in bands})
        band_n = sorted({
            int(band.rule.rsplit("derived_n=", 1)[1])
            for band in bands if "derived_n=" in band.rule
        })
        if not band_n and not bands and derived_n[(family, quantity, engine)] > 0:
            band_n = [derived_n[(family, quantity, engine)]]
        derived_rows = [
            r for r in bucket
            if r.numeric is not None
            and r.numeric.decision_band is not None
            and "residual distribution" in r.numeric.decision_band.rule
        ]
        tail_in = sum(1 for r in derived_rows if r.status is ResidualStatus.MATCH) if "derived_2xMAD" in kinds else None
        tail_out = sum(1 for r in derived_rows if r.status is ResidualStatus.MISMATCH) if "derived_2xMAD" in kinds else None
        rendered.append({
            "family": family,
            "quantity": quantity,
            "engine": engine,
            "relation": relation,
            "n": len(values),
            "signed_median_residual": None if center is None else str(center),
            "mad": None if mad is None else str(mad),
            "bias_to_scatter_ratio": (
                None
                if (ratio := _bias_to_scatter_ratio(center, mad, len(values))) is None
                else str(ratio)
            ),
            "max_abs_residual": None if not values else str(max(abs(value) for value in values)),
            "band_value": widths[0] if len(widths) == 1 else widths,
            "unit": units[0] if len(units) == 1 else units or QUANTITY_UNITS[Quantity(quantity)],
            "band_kind": kinds[0] if len(kinds) == 1 else kinds,
            "band_derived_n": band_n[0] if len(band_n) == 1 else band_n,
            "match_count": sum(1 for r in bucket if r.status is ResidualStatus.MATCH),
            "mismatch_count": sum(1 for r in bucket if r.status is ResidualStatus.MISMATCH),
            "no_band_count": sum(1 for r in bucket if r.status is ResidualStatus.NO_BAND),
            "tail_in_count": tail_in,
            "tail_out_count": tail_out,
            "no_band_reason": "derived_band_insufficient_n" if not bands and 0 < derived_n[(family, quantity, engine)] < MIN_DERIVED_BAND_N else None,
        })
    return rendered


def _headline_band_width(bucket: Sequence[Residual]) -> Decimal | None:
    widths = {
        residual.numeric.decision_band.value
        for residual in bucket
        if (
            residual.numeric is not None
            and residual.numeric.operation is MetricOperation.DEX
            and residual.numeric.decision_band is not None
            and residual.numeric.decision_band.unit == "dimensionless"
        )
    }
    return next(iter(widths)) if len(widths) == 1 else None


def _headline_metric_row(
    rail: str,
    engine: str,
    bucket: Sequence[Residual],
    *,
    tier: str,
) -> dict[str, object]:
    if tier == "measured":
        # Numeric measured rows remain in the report even when a gate keeps
        # them out of score_eligible. ``n_inside_band`` separates the
        # measured agreement result from those other eligibility gates.
        scored = [r for r in bucket if r.numeric is not None]
    elif tier == "compilation":
        scored = [r for r in bucket if r.numeric is not None]
    else:
        raise ValueError(f"unknown headline tier {tier!r}")
    matches = [
        r
        for r in scored
        if r.status is ResidualStatus.MATCH
    ]
    banded = [
        r
        for r in scored
        if r.status in {ResidualStatus.MATCH, ResidualStatus.MISMATCH}
    ]
    dex_values = [
        r.numeric.value
        for r in scored
        if r.numeric is not None and r.numeric.operation is MetricOperation.DEX
    ]
    band_width = _headline_band_width(scored)
    median = _median_abs(dex_values)
    signed_median = _median(dex_values)
    rms = _rms(dex_values)
    n_scored = len(scored)
    rms_over_band = (
        None
        if rms is None or band_width is None or band_width <= 0
        else rms / band_width
    )
    match_rate: str | float | None
    if not banded:
        match_rate = None
    else:
        match_rate = len(matches) / len(banded)
    return {
        "tier": tier,
        "rail": rail,
        "engine": engine,
        "n": n_scored,
        "n_candidates": len(bucket),
        "n_refused": sum(1 for r in bucket if r.status is ResidualStatus.REFUSED),
        "n_scored": n_scored,
        "n_score_eligible": sum(1 for r in scored if r.score_eligible),
        "n_inside_band": len(matches),
        "n_match": len(matches),
        "match_rate": match_rate,
        "match_rate_label": (
            "band membership; derived_2xMAD means tail membership, not accuracy"
            if tier == "compilation"
            else "band membership"
        ),
        "rms_dex": None if rms is None else str(rms),
        "median_dex": None if signed_median is None else str(signed_median),
        "median_abs_dex": None if median is None else str(median),
        "band_width_dex": None if band_width is None else str(band_width),
        "rms_over_band": None if rms_over_band is None else str(rms_over_band),
        "n_no_band": sum(1 for r in scored if r.status is ResidualStatus.NO_BAND),
        "data_scatter_ratio": None if rms_over_band is None else str(rms_over_band),
    }


def headline_rows(
    residuals: Sequence[Residual],
    *,
    context: ScoreContext | None = None,
    tier: str = "measured",
    engines: Sequence[Engine] | None = None,
) -> list[dict[str, object]]:
    """Per rail × engine headline for one tier.

    Measured and compilation rows use numeric residuals for descriptive
    accuracy. ``score_eligible`` remains a separately reported gate result;
    compilation rows remain a separate diagnostic tier.
    """

    if tier == ALL_NUMERIC_TIER:
        return _all_numeric_rows_from_residuals(
            residuals, context=context, engines=engines
        )
    groups: dict[tuple[str, str], list[Residual]] = {}
    engines_seen: set[str] = set()
    if tier == "measured":
        tier_residuals = _measured_residuals(residuals, context)
    elif tier == "compilation":
        tier_residuals = _compilation_residuals(residuals, context)
    else:
        raise ValueError(f"unknown headline tier {tier!r}")
    for residual in tier_residuals:
        if residual.rail is None:
            continue
        engine = _engine_of(residual)
        engines_seen.add(engine)
        groups.setdefault((residual.rail.value, engine), []).append(residual)
    engine_names = [engine.value for engine in engines or ()]
    engine_names.extend(name for name in sorted(engines_seen) if name not in engine_names)
    for rail in Rail:
        for engine in engine_names or [e.value for e in SCORE_ENGINE_SET]:
            groups.setdefault((rail.value, engine), [])
    rows: list[dict[str, object]] = []
    for (rail, engine), bucket in sorted(groups.items()):
        row = _headline_metric_row(rail, engine, bucket, tier=tier)
        row["decision_strata"] = (
            _compilation_decision_strata(
                [
                    r
                    for r in tier_residuals
                    if _engine_of(r) == engine
                    and r.rail is not None
                    and r.rail.value == rail
                ],
                context,
            )
            if tier == "compilation"
            else []
        )
        row["n_eligible_references"] = sum(
            1
            for r in bucket
            if r.score_eligible or "reference_measured_evidence" not in r.exclusions
        )
        rows.append(row)
    return rows


def flagged_stratum_rows(
    residuals: Sequence[Residual],
    *,
    engines: Sequence[Engine] | None = None,
) -> list[dict[str, object]]:
    """Summarize numeric flagged diagnostics without a decision band."""

    groups: dict[tuple[str, str, str], list[Residual]] = {}
    for residual in residuals:
        if residual.rail is None or residual.numeric is None:
            continue
        engine = _engine_of(residual)
        for stratum in flagged_strata(residual.notices):
            groups.setdefault((stratum, residual.rail.value, engine), []).append(residual)
    rows: list[dict[str, object]] = []
    for (stratum, rail, engine), bucket in sorted(groups.items()):
        dex_values = [
            residual.numeric.value
            for residual in bucket
            if residual.numeric is not None
            and residual.numeric.operation is MetricOperation.DEX
        ]
        rows.append(
            {
                "stratum": stratum,
                "rail": rail,
                "engine": engine,
                "n": len(bucket),
                "median_dex": (
                    None if not dex_values else str(_median(dex_values))
                ),
                "rms_dex": None if not dex_values else str(_rms(dex_values)),
            }
        )
    return rows


def headline_records(
    residuals: Sequence[Residual],
    *,
    context: ScoreContext | None = None,
    engines: Sequence[Engine] | None = None,
) -> list[dict[str, object]]:
    """Machine-readable all-numeric (A), certified (B), and compilation records."""

    return [
        *headline_rows(residuals, context=context, tier=ALL_NUMERIC_TIER, engines=engines),
        *headline_rows(residuals, context=context, tier="measured", engines=engines),
        *headline_rows(residuals, context=context, tier="compilation", engines=engines),
    ]


# ---------------------------------------------------------------------------
# b-691 all-numeric (A) headline: ONE owner (b-693/b-691 round-2 finding 5).
#
# Every writer (typed JSON, streamed JSON + Markdown, report-only payload JSON
# + Markdown) feeds the same facts to ``_AllNumericHeadline`` and prints its
# records. A never re-derives admission: ``certified`` on each fact is the
# caller's own measured/certified (B) membership decision for that row.
# Statistics are stratified by quantity / metric operation / unit; only DEX
# residuals contribute to a ``*_dex`` statistic (round-2 finding 3).
# ---------------------------------------------------------------------------

ALL_NUMERIC_TIER = "all_numeric"


@dataclass(frozen=True)
class _AllNumericFact:
    rail: str
    engine: str
    quantity: str
    operation: str
    unit: str
    value: Decimal
    status: str
    score_eligible: bool
    strata: tuple[str, ...]
    source_id: str
    certified: bool


def _residual_key_quantity(key: object) -> str:
    """Quantity token of a ``residual_key`` (``…::q::rail::engine``)."""

    parts = str(key or "").rsplit("::", 3)
    return parts[1] if len(parts) == 4 and parts[1] else "quantity_unknown"


def _all_numeric_source_id(reference: str, observation: Observation | None) -> str:
    return (
        observation.source_id
        if observation is not None and observation.source_id
        else reference
    )


def _all_numeric_fact(
    row: Mapping[str, object],
    *,
    engine: str,
    observation: Observation | None,
    certified: bool,
    value: Decimal | None = None,
) -> _AllNumericFact | None:
    """One numeric residual payload as the A headline sees it, or None."""

    numeric = row.get("numeric")
    rail = str(row.get("rail") or "")
    if not rail or not isinstance(numeric, Mapping):
        return None
    if value is None:
        raw = numeric.get("value")
        if raw is None:
            return None
        try:
            value = as_decimal(raw)
        except (TypeError, ValueError, ArithmeticError):
            return None
    return _AllNumericFact(
        rail=rail,
        engine=engine,
        quantity=_residual_key_quantity(row.get("key")),
        operation=str(numeric.get("operation") or ""),
        unit=str(numeric.get("unit") or ""),
        value=value,
        status=str(row.get("status") or ""),
        score_eligible=bool(row.get("score_eligible")),
        strata=_flagged_payload_strata(row),
        source_id=_all_numeric_source_id(str(row.get("reference") or ""), observation),
        certified=certified,
    )


def _all_numeric_metric_label(operation: str, unit: str) -> str:
    """Label of one metric stratum. Only a DEX metric is ever called dex."""

    if operation == MetricOperation.DEX.value:
        return "dex"
    if operation == MetricOperation.RELATIVE.value:
        return f"relative ({unit})"
    return f"absolute ({unit})"


def _all_numeric_stats(values: Sequence[Decimal]) -> dict[str, str | None]:
    median = _median(values)
    median_abs = _median_abs(values)
    iqr = _iqr(values)
    rms = _rms(values)
    return {
        "median": None if median is None else str(median),
        "median_abs": None if median_abs is None else str(median_abs),
        "iqr": None if iqr is None else str(iqr),
        "rms": None if rms is None else str(rms),
    }


class _AllNumericHeadline:
    """Single owner of the A headline: grid, strata, flag classes, sources."""

    def __init__(self) -> None:
        self._facts: dict[tuple[str, str], list[_AllNumericFact]] = defaultdict(list)

    def add(self, fact: _AllNumericFact | None) -> None:
        if fact is not None:
            self._facts[(fact.rail, fact.engine)].append(fact)

    @property
    def engines_seen(self) -> set[str]:
        return {engine for _rail, engine in self._facts}

    def records(
        self,
        engine_names: Iterable[str],
        *,
        include_seen: bool = True,
    ) -> list[dict[str, object]]:
        """Complete rail × engine grid; empty cells are n = 0 records."""

        names = set(engine_names)
        if include_seen:
            names |= self.engines_seen
        return [
            self._record(rail, engine, self._facts.get((rail, engine), ()))
            for rail, engine in sorted(
                (rail.value, engine) for rail in Rail for engine in names
            )
        ]

    @staticmethod
    def _record(
        rail: str, engine: str, facts: Sequence[_AllNumericFact]
    ) -> dict[str, object]:
        dex_values = [
            fact.value for fact in facts if fact.operation == MetricOperation.DEX.value
        ]
        dex = _all_numeric_stats(dex_values)
        strata: dict[tuple[str, str, str], list[Decimal]] = defaultdict(list)
        flag_counts: dict[str, int] = defaultdict(int)
        sources: dict[str, dict[str, object]] = {}
        for fact in facts:
            strata[(fact.quantity, fact.operation, fact.unit)].append(fact.value)
            for stratum in fact.strata or ("unflagged",):
                flag_counts[stratum] += 1
            entry = sources.setdefault(
                fact.source_id,
                {
                    "source_id": fact.source_id,
                    "n_numeric": 0,
                    "n_certified": 0,
                    "n_flagged": 0,
                    "flag_class_counts": defaultdict(int),
                },
            )
            entry["n_numeric"] = int(entry["n_numeric"]) + 1
            entry["n_certified"] = int(entry["n_certified"]) + int(fact.certified)
            if fact.strata:
                entry["n_flagged"] = int(entry["n_flagged"]) + 1
                source_flags = entry["flag_class_counts"]
                assert isinstance(source_flags, defaultdict)
                for stratum in fact.strata:
                    source_flags[stratum] += 1
        return {
            "tier": ALL_NUMERIC_TIER,
            "rail": rail,
            "engine": engine,
            "n": len(facts),
            "n_certified": sum(1 for fact in facts if fact.certified),
            "n_flagged": sum(1 for fact in facts if fact.strata),
            "n_score_eligible": sum(1 for fact in facts if fact.score_eligible),
            "n_inside_band": sum(
                1 for fact in facts if fact.status == ResidualStatus.MATCH.value
            ),
            "n_no_band": sum(
                1 for fact in facts if fact.status == ResidualStatus.NO_BAND.value
            ),
            "n_dex": len(dex_values),
            "median_dex": dex["median"],
            "median_abs_dex": dex["median_abs"],
            "iqr_dex": dex["iqr"],
            "rms_dex": dex["rms"],
            "metric_strata": [
                {
                    "quantity": quantity,
                    "operation": operation,
                    "unit": unit,
                    "label": _all_numeric_metric_label(operation, unit),
                    "n": len(values),
                    **_all_numeric_stats(values),
                }
                for (quantity, operation, unit), values in sorted(strata.items())
            ],
            "flag_class_counts": dict(sorted(flag_counts.items())),
            "sources": [
                {
                    **{key: value for key, value in entry.items() if key != "flag_class_counts"},
                    "flag_class_counts": dict(
                        sorted(entry["flag_class_counts"].items())  # type: ignore[union-attr]
                    ),
                }
                for _source_id, entry in sorted(sources.items())
            ],
        }


def _all_numeric_rows_from_residuals(
    residuals: Sequence[Residual],
    *,
    context: ScoreContext | None,
    engines: Sequence[Engine] | None,
) -> list[dict[str, object]]:
    """Typed adapter: certified membership is ``_measured_residuals`` (B)."""

    from simulator.battery.compilation_tier import reference_observation

    certified_ids = {id(residual) for residual in _measured_residuals(residuals, context)}
    owner = _AllNumericHeadline()
    for residual in residuals:
        if residual.numeric is None or residual.rail is None:
            continue
        owner.add(
            _all_numeric_fact(
                residual_to_plain(residual),
                engine=_engine_of(residual),
                observation=(
                    None
                    if context is None
                    else reference_observation(context.observations, residual.reference)
                ),
                certified=id(residual) in certified_ids,
                value=residual.numeric.value,
            )
        )
    engine_names = [engine.value for engine in engines or ()]
    if not engine_names and not owner.engines_seen:
        engine_names = [engine.value for engine in SCORE_ENGINE_SET]
    return owner.records(engine_names)


def _payload_measured_reference(
    observations: Mapping[str, Observation] | None,
    origins: Mapping[str, str] | None,
) -> Callable[[Mapping[str, object]], bool]:
    """Measured-tier reference membership for residual payloads (one owner)."""

    if observations is None:
        return lambda row: _reference_has_measured_evidence(
            None, exclusions=row.get("exclusions")
        )
    from simulator.battery.compilation_tier import compilation_row_observation

    def member(row: Mapping[str, object]) -> bool:
        reference = str(row.get("reference") or "")
        return compilation_row_observation(
            reference, observations, origins
        ) is None and _reference_has_measured_evidence(
            observations.get(reference), exclusions=row.get("exclusions")
        )

    return member


def all_numeric_payload_records(
    rows: Iterable[Mapping[str, object]],
    *,
    engines: Sequence[Engine],
    observations: Mapping[str, Observation] | None = None,
    origins: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Report-only adapter shared by the payload JSON and Markdown writers.

    ``certified`` is the payload B decision: measured-tier reference
    membership plus the measured headline row filter.
    """

    from simulator.battery.compilation_tier import reference_observation

    measured_reference = _payload_measured_reference(observations, origins)
    owner = _AllNumericHeadline()
    for row in rows:
        reference = str(row.get("reference") or "")
        owner.add(
            _all_numeric_fact(
                row,
                engine=_ScorePayloadAccumulator._engine(row),
                observation=(
                    None
                    if observations is None
                    else reference_observation(observations, reference)
                ),
                certified=measured_reference(row)
                and _headline_payload_admits(row, tier="measured"),
            )
        )
    return owner.records(engine.value for engine in engines)


def _all_numeric_markdown_lines(
    records: Sequence[Mapping[str, object]],
    *,
    regenerated: bool = True,
) -> list[str]:
    """The A section for both Markdown writers; every grid row is printed."""

    lines = [
        "## All-numeric tier",
        "",
        "Co-equal descriptive headline (A) over every priced residual, certified and "
        "flagged. Every rail × engine cell is printed, n = 0 included. Dex statistics "
        "use DEX residuals only; every other metric stays in its own quantity / "
        "operation / unit stratum below and is never labelled dex. n certified is the "
        "measured (B) membership decision, consumed rather than recomputed. Does not "
        "change the measured/certified table.",
        "",
    ]
    if not regenerated:
        lines.append("Not regenerated.")
        return lines
    lines.extend(
        [
            "| rail | engine | n | n certified | n flagged | n dex | median dex | "
            "IQR dex | flag classes | sources |",
            "|---|---|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    strata_lines: list[str] = []
    for row in records:
        flags = row.get("flag_class_counts") or {}
        assert isinstance(flags, Mapping)
        flag_s = ", ".join(f"{k}={v}" for k, v in flags.items()) if flags else "—"
        sources = row.get("sources") or []
        assert isinstance(sources, Sequence)
        source_s = (
            ", ".join(
                f"{s.get('source_id')}(n={s.get('n_numeric')},"
                f"cert={s.get('n_certified')},flag={s.get('n_flagged')})"
                for s in sources
                if isinstance(s, Mapping)
            )
            or "—"
        )
        lines.append(
            f"| {row['rail']} | {row['engine']} | {row['n']} | {row['n_certified']} | "
            f"{row['n_flagged']} | {row['n_dex']} | {row.get('median_dex') or '—'} | "
            f"{row.get('iqr_dex') or '—'} | {flag_s} | {source_s} |"
        )
        for stratum in row.get("metric_strata") or ():
            assert isinstance(stratum, Mapping)
            strata_lines.append(
                f"| {row['rail']} | {row['engine']} | {stratum['quantity']} | "
                f"{stratum['label']} | {stratum['n']} | {stratum.get('median') or '—'} | "
                f"{stratum.get('iqr') or '—'} |"
            )
    lines.extend(
        [
            "",
            "### All-numeric metric strata",
            "",
            "| rail | engine | quantity | metric | n | median | IQR |",
            "|---|---|---|---|---:|---:|---:|",
            *(strata_lines or ["| (none) | — | — | — | 0 | — | — |"]),
        ]
    )
    return lines


HEADLINE_SUMMARY_KIND = "battery_headline_summary"


def headline_summary_payload(
    records: Sequence[Mapping[str, object]],
    *,
    store_stamp: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "kind": HEADLINE_SUMMARY_KIND,
        "schema_version": "v1",
        "store": None if store_stamp is None else dict(store_stamp),
        "records": [dict(record) for record in records],
    }


def write_headline_summary_json(
    residuals: Sequence[Residual],
    path: Path,
    *,
    context: ScoreContext,
    engines: Sequence[Engine],
    root: Path | None = None,
) -> None:
    """Write the tier-separated accuracy ratchet input."""

    stamp = derive_store_stamp(root or REPO_ROOT)
    payload = headline_summary_payload(
        headline_records(residuals, context=context, engines=engines),
        store_stamp=stamp,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _short_refusal_token(detail_reason: object) -> str | None:
    if not isinstance(detail_reason, str) or not detail_reason:
        return None
    # Machine tokens only. Long OCR/page sentences stay on the residual.
    if len(detail_reason) > 80:
        return None
    if not all(ch.isalnum() or ch in "._:-" for ch in detail_reason):
        return None
    return detail_reason


def refusal_census(residuals: Sequence[Residual]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for residual in residuals:
        if residual.status is not ResidualStatus.REFUSED or residual.refusal is None:
            continue
        reason = residual.refusal.reason.value
        token = _short_refusal_token(residual.refusal.detail.get("reason"))
        key = reason if token is None else f"{reason}:{token}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def admission_census(
    residuals: Sequence[Residual],
    *,
    context: ScoreContext | None = None,
) -> dict[str, int]:
    """How many comparison candidates die on admission, including admission alone.

    Does not change the score_eligible rule. Pending remains diagnostic.
    """

    pending_candidates = 0
    admitted_candidates = 0
    if context is not None:
        for obs in comparison_candidates(context):
            if obs.admission.status is AdmissionStatus.PENDING:
                pending_candidates += 1
            elif obs.admission.status is AdmissionStatus.ADMITTED:
                admitted_candidates += 1
    with_admission = 0
    admission_alone = 0
    admission_alone_refs: set[str] = set()
    with_admission_refs: set[str] = set()
    for residual in residuals:
        if "admission_admitted" not in residual.exclusions:
            continue
        with_admission += 1
        with_admission_refs.add(residual.reference)
        if all(name == "admission_admitted" for name in residual.exclusions):
            admission_alone += 1
            admission_alone_refs.add(residual.reference)
    return {
        "comparison_candidates_pending": pending_candidates,
        "comparison_candidates_admitted": admitted_candidates,
        "residuals_with_admission_exclusion": with_admission,
        "residuals_admission_alone": admission_alone,
        "unique_obs_admission_alone": len(admission_alone_refs),
        "unique_obs_with_admission_exclusion": len(with_admission_refs),
    }


def admission_census_payloads(rows: Iterable[Mapping[str, object]]) -> dict[str, int]:
    with_admission = 0
    admission_alone = 0
    admission_alone_refs: set[str] = set()
    for row in rows:
        exclusions = tuple(row.get("exclusions") or ())
        if "admission_admitted" not in exclusions:
            continue
        with_admission += 1
        if all(name == "admission_admitted" for name in exclusions):
            admission_alone += 1
            admission_alone_refs.add(str(row.get("reference") or ""))
    return {
        "residuals_with_admission_exclusion": with_admission,
        "residuals_admission_alone": admission_alone,
        "unique_obs_admission_alone": len(admission_alone_refs),
    }


def notice_backlog(residuals: Sequence[Residual]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for residual in residuals:
        for notice in residual.notices:
            counts[notice.kind.value] = counts.get(notice.kind.value, 0) + 1
    return dict(sorted(counts.items()))


def _engine_of(residual: Residual) -> str:
    if residual.candidate_request is not None:
        return residual.candidate_request.engine.value
    if residual.candidate and residual.candidate.startswith("engine:"):
        parts = residual.candidate.split(":")
        if len(parts) >= 2:
            return parts[1]
    if residual.key:
        return residual.key.rsplit("::", 1)[-1]
    return "unknown"


def candidate_rail_census(
    context: ScoreContext,
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    """Per-rail comparison candidates, admitted split from pending.

    One row per observation. Not multiplied by engines. Quantities with no
    headline rail are returned in the second tuple, keyed by reason.
    """

    rail_rows: dict[str, dict[str, object]] = {
        rail.value: {
            "rail": rail.value,
            "candidates": 0,
            "admitted": 0,
            "pending": 0,
            "points": 0,
        }
        for rail in Rail
    }
    unassigned: dict[str, dict[str, object]] = {}
    for obs in comparison_candidates(context):
        identity = obs.identity
        quantity = quantity_token(identity) if isinstance(identity, Identity) else None
        formula = identity.species.formula if isinstance(identity, Identity) else ""
        rail = rail_for_quantity(quantity, species_formula=formula)
        if rail is None:
            reason = no_headline_rail_reason(quantity, species_formula=formula)
            bucket = unassigned.get(reason)
            if bucket is None:
                bucket = {
                    "reason": reason,
                    "candidates": 0,
                    "admitted": 0,
                    "pending": 0,
                    "points": 0,
                }
                unassigned[reason] = bucket
        else:
            bucket = rail_rows[rail.value]
        bucket["candidates"] = int(bucket["candidates"]) + 1
        if obs.admission.status is AdmissionStatus.ADMITTED:
            bucket["admitted"] = int(bucket["admitted"]) + 1
        elif obs.admission.status is AdmissionStatus.PENDING:
            bucket["pending"] = int(bucket["pending"]) + 1
        if obs.value.kind is ValueKind.POINT:
            bucket["points"] = int(bucket["points"]) + 1
    rails = tuple(rail_rows[rail.value] for rail in Rail)
    off = tuple(unassigned[key] for key in sorted(unassigned))
    return rails, off


def _census_count_line(row: Mapping[str, object], label: str) -> str:
    return (
        f"| {label} | {row['candidates']} | {row['admitted']} | "
        f"{row['pending']} | {row['points']} |"
    )


class _ResidualRowsPayloadView:
    def __init__(self, residuals: Sequence[Residual], context: ScoreContext) -> None:
        self.residuals = residuals
        family_values: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
        headline_values: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        tier_values: dict[tuple[str, str, str, str, str, str], set[str]] = defaultdict(set)
        from simulator.battery.compilation_tier import (
            compilation_family,
            compilation_origin,
            compilation_row_observation,
        )

        for residual in residuals:
            numeric = residual.numeric
            if (
                numeric is None
                or numeric.decision_band is None
                or residual.status is ResidualStatus.REFUSED
                or _has_flagged_decision_notice(residual)
            ):
                continue
            value = str(numeric.decision_band.value)
            observation = compilation_row_observation(
                residual.reference, context.observations, context.origins
            )
            if observation is None:
                if _reference_has_measured_evidence(
                    context.observations.get(residual.reference),
                    exclusions=residual.exclusions,
                ) and numeric.operation is MetricOperation.DEX and numeric.decision_band.unit == "dimensionless":
                    headline_values[("measured", residual.rail.value if residual.rail else "", _engine_of(residual))].add(value)
                continue
            family = compilation_family(
                observation.source_id,
                compilation_origin(residual.reference, context.origins),
            )
            quantity = quantity_token(observation.identity) if isinstance(observation.identity, Identity) else None
            if quantity is None:
                continue
            engine = _engine_of(residual)
            relation = residual.source_relation.value
            family_values[(family, quantity.value, engine, relation)].add(value)
            rail = residual.rail.value if residual.rail else "none"
            if numeric.operation is MetricOperation.DEX and numeric.decision_band.unit == "dimensionless":
                headline_values[("compilation", rail, engine)].add(value)
            if numeric.operation is not None:
                tier_values[(family, rail, engine, relation, quantity.value, numeric.unit)].add(value)
        self.metadata = _ResidualReportMetadata(
            family_band_values=dict(family_values),
            headline_band_values=dict(headline_values),
            tier_band_values=dict(tier_values),
        )
        self._typed_family_band_values = self.metadata.family_band_values
        self._typed_headline_band_values = self.metadata.headline_band_values
        self._typed_tier_band_values = self.metadata.tier_band_values

    def __iter__(self) -> Iterable[dict[str, object]]:
        def rows() -> Iterable[dict[str, object]]:
            for residual in self.residuals:
                payload = residual_to_plain(residual)
                numeric = payload.get("numeric")
                if residual.numeric is not None and isinstance(numeric, dict):
                    numeric["value"] = str(residual.numeric.value)
                yield payload

        return rows()


def _report_engine_set(rows: Iterable[Mapping[str, object]]) -> tuple[Engine, ...]:
    names = set()
    for row in rows:
        request = row.get("candidate_request")
        engine = (
            str(request.get("engine"))
            if isinstance(request, Mapping) and request.get("engine")
            else str(row.get("key") or "").rsplit("::", 1)[-1]
        )
        if engine:
            names.add(engine)
    return tuple(Engine(name) for name in sorted(names))


def render_score_report(
    residuals: Sequence[Residual],
    *,
    context: ScoreContext,
    engines: Sequence[Engine],
    pin_failures: Sequence[Mapping[str, object]] = (),
    status_diff: Sequence[Mapping[str, object]] = (),
    unmapped_legacy_keys: Sequence[str] = (),
    studio_hostname: str | None = None,
    root: Path | None = None,
) -> str:
    return _render_score_report_from_payloads_legacy(
        _ResidualRowsPayloadView(residuals, context),
        context=context,
        engines=engines,
        pin_failures=pin_failures,
        status_diff=status_diff,
        unmapped_legacy_keys=unmapped_legacy_keys,
        studio_hostname=studio_hostname,
        store_stamp=derive_store_stamp(root or REPO_ROOT),
    )


def _render_score_report_from_payloads_legacy(
    rows: Iterable[Mapping[str, object]],
    *,
    context: ScoreContext,
    engines: Sequence[Engine],
    pin_failures: Sequence[Mapping[str, object]] = (),
    status_diff: Sequence[Mapping[str, object]] = (),
    unmapped_legacy_keys: Sequence[str] = (),
    studio_hostname: str | None = None,
    store_stamp: Mapping[str, object] | None = None,
    _aggregate: _ScorePayloadAccumulator | None = None,
) -> str:
    aggregate = _aggregate or _ScorePayloadAccumulator.from_rows(
        rows, context=context, engines=engines
    )
    has_residuals = aggregate.count > 0
    lines: list[str] = [
        "# Battery score report (schema v2.1)",
        "",
        "Generated only. Pins are an independent baseline and are never",
        "re-centred from these residuals. Refusals are diagnostics, never hidden.",
        "The measured tier keeps numeric rows visible; its headline reports",
        "n, n inside band, RMS dex, signed and absolute median dex, band width,",
        "RMS/band, and score_eligible separately. The compilation tier is beside",
        "it and is never added to it. Match rate is banded rows only.",
        "",
        f"Hostname: `{context.hostname}`.",
    ]
    if studio_hostname:
        lines.append(f"Studio hostname: `{studio_hostname}`.")
    stamp = derive_store_stamp(REPO_ROOT) if store_stamp is None else store_stamp
    lines.extend(["", *format_store_stamp_report_lines(stamp)])
    lines.extend(["", f"Engines: {', '.join(e.value for e in engines)}."])
    if not has_residuals:
        lines.extend(
            [
                "",
                "No engine residuals were regenerated for this store revision.",
                "Match rate is blank. score_eligible is 0.",
                "The live candidate census is the published candidate count.",
            ]
        )
    rail_census, unassigned_census = candidate_rail_census(context)
    lines.extend(
        [
            "",
            "## Live candidate census",
            "",
            "Comparison candidates are measured rows admitted or pending.",
            "Admitted is split from pending. Counts are observations, not",
            "residuals, so they are not multiplied by the engine set.",
            "A vapour candidate is only `p_sat`, `p_partial`, or `p_reference`.",
            "",
            "| rail | candidates | admitted | pending | points |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in rail_census:
        lines.append(_census_count_line(row, str(row["rail"])))
    lines.extend(
        [
            "",
            "### No headline rail",
            "",
            "These quantities are not vapour candidates and not SiO_evolution",
            "candidates. The reason is the refusal, not a borrowed rail.",
            "",
            "| reason | candidates | admitted | pending | points |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    if not unassigned_census:
        lines.append("| (none) | 0 | 0 | 0 | 0 |")
    else:
        for row in unassigned_census:
            lines.append(_census_count_line(row, f"`{row['reason']}`"))
    report_engine_names = set(aggregate.report_engine_names)
    if not report_engine_names:
        report_engine_names = {engine.value for engine in SCORE_ENGINE_SET}
    report_engines = tuple(Engine(name) for name in sorted(report_engine_names))
    # A has its own complete engine × rail grid (the run's engines plus any
    # engine with an A row); B keeps its measured-only membership below.
    lines.extend(
        [
            "",
            *_all_numeric_markdown_lines(
                aggregate.all_numeric.records(engine.value for engine in engines),
                regenerated=has_residuals,
            ),
        ]
    )
    lines.extend(
        [
            "",
            "## Measured tier",
            "",
            "Numeric measured rows stay visible. score_eligible and inside-band "
            "counts are reported separately. Compilation rows are not in this table.",
            "",
        ]
    )
    if not has_residuals:
        lines.append("Not regenerated. Match rate is blank. score_eligible is 0.")
    else:
        lines.extend(
            [
                "| rail | engine | n candidates | n refused | n scored | n score eligible | "
                "n inside band | RMS dex | median dex | median abs dex | band width dex | "
                "RMS/band | n no band | match rate |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in aggregate.headline_records(engines=report_engines):
            if row.get("tier") != "measured":
                continue
            rate = row["match_rate"]
            rate_s = "—" if rate is None else f"{rate:.3f}"
            lines.append(
                f"| {row['rail']} | {row['engine']} | {row['n_candidates']} | "
                f"{row['n_refused']} | {row['n']} | {row['n_score_eligible']} | "
                f"{row['n_inside_band']} | {row['rms_dex'] or '—'} | "
                f"{row['median_dex'] or '—'} | {row['median_abs_dex'] or '—'} | "
                f"{row['band_width_dex'] or '—'} | {row['rms_over_band'] or '—'} | "
                f"{row['n_no_band']} | {rate_s} |"
            )
    lines.extend(
        [
            "",
            "## Flagged strata",
            "",
            "These numeric diagnostics carry an explicit source or apparatus flag. "
            "Flagged rows do not derive measurement bands or enter unflagged headlines. "
            "Cell-material-inferred residuals are still judged against the band; other "
            "flag kinds retain their existing scoring rules. A row carrying both flags "
            "appears in both strata.",
            "",
            "| stratum | rail | engine | n | median dex | RMS dex |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    flagged_rows = aggregate.flagged_records()
    if not flagged_rows:
        lines.append("| (none) | — | — | 0 | — | — |")
    else:
        for row in flagged_rows:
            lines.append(
                f"| {row['stratum']} | {row['rail']} | {row['engine']} | "
                f"{row['n']} | {row['median_dex'] or '—'} | {row['rms_dex'] or '—'} |"
            )
    lines.extend(
        [
            "",
            *aggregate.compilation_lines(),
        ]
    )
    lines.extend(["", "## Refusal census", ""])
    if not has_residuals:
        lines.append(
            "Engine refusals were not regenerated with this census. "
            "Rows with no headline rail are counted above and are not hidden."
        )
    else:
        lines.extend(
            [
                "Measured tier only. Compilation refusals are in the compilation table.",
                "",
                "| reason | n |",
                "|---|---:|",
            ]
        )
        census = dict(sorted(aggregate.refusal_counts.items()))
        if not census:
            lines.append("| (none) | 0 |")
        else:
            for reason, n in census.items():
                lines.append(f"| `{reason}` | {n} |")
    admit = {
        "residuals_with_admission_exclusion": aggregate.admission_with,
        "residuals_admission_alone": aggregate.admission_alone,
        "unique_obs_admission_alone": aggregate.admission_alone_unique,
    }
    pending = 0
    admitted = 0
    for observation in comparison_candidates(context):
        if observation.admission.status is AdmissionStatus.PENDING:
            pending += 1
        elif observation.admission.status is AdmissionStatus.ADMITTED:
            admitted += 1
    lines.extend(
        [
            "",
            "## Admission",
            "",
            "score_eligible requires canonical admission admitted. Pending rows",
            "stay in the comparison set as diagnostics (flagged and priced when",
            "numeric, never the empirical headline). The admission rule is unchanged.",
            "",
            "| count | n |",
            "|---|---:|",
            f"| comparison candidates pending | {pending} |",
            f"| comparison candidates admitted | {admitted} |",
            f"| residuals with admission_admitted exclusion | {admit['residuals_with_admission_exclusion']} |",
            f"| residuals that die on admission alone | {admit['residuals_admission_alone']} |",
            f"| unique observations that die on admission alone | {admit['unique_obs_admission_alone']} |",
        ]
    )
    lines.extend(
        [
            "",
            "## Notice backlog (certification projects)",
            "",
            "| notice kind | n |",
            "|---|---:|",
        ]
    )
    backlog = dict(sorted(aggregate.notice_counts.items()))
    if not backlog:
        lines.append("| (none) | 0 |")
    else:
        for kind, n in backlog.items():
            lines.append(f"| `{kind}` | {n} |")
    n_diag = aggregate.diagnostic_count
    lines.extend(
        [
            "",
            "## Internal-consistency diagnostics",
            "",
            "species_rail_differential_ledger and gibbs_battery_residual_ledger",
            "are internal-consistency instruments, not scoring ledgers. They do",
            "not enter either headline tier. Compilation rows are scored in the",
            "compilation tier above, still COMPILATION_ASSESSED, and never added",
            "to the measured tier. " + CIRCULARITY_WARNING,
            "",
            f"Diagnostic residuals in this file: {n_diag}.",
            "",
            "## Pin failures",
            "",
        ]
    )
    if not has_residuals and not pin_failures:
        lines.append(
            "Pins were not compared; no residuals ledger was regenerated for this store revision."
        )
    elif not pin_failures:
        lines.append("None.")
    else:
        lines.append(
            f"{len(pin_failures)} pin failures (coverage or outside pin_band). A live residual outside its pin_band is a failure, never a re-centre."
        )
        lines.extend(["", "| key | reason | live | centre | pin_band |", "|---|---|---:|---:|---:|"])
        for failure in list(pin_failures)[:50]:
            lines.append(
                f"| `{failure.get('key')}` | {failure.get('reason')} | {failure.get('live')} | "
                f"{failure.get('centre')} | {failure.get('pin_band')} |"
            )
        if len(pin_failures) > 50:
            lines.append(f"| … | {len(pin_failures) - 50} more | | | |")
    lines.extend(["", "## status_diff vs old scorers", ""])
    if not has_residuals and not status_diff:
        lines.append(
            "status_diff was not run; no residuals ledger was regenerated for this store revision."
        )
    elif not status_diff:
        lines.append("No mapped outcome changes.")
    else:
        lines.extend(["| old key | old | new | axis |", "|---|---|---|---|"])
        for row in status_diff:
            lines.append(
                f"| `{row.get('old_key')}` | {row.get('old')} | {row.get('new')} | "
                f"{row.get('axis')} |"
            )
    if unmapped_legacy_keys:
        lines.extend(
            ["", f"Unmapped legacy keys: {len(unmapped_legacy_keys)}. Old ledgers retained."]
        )
    elif has_residuals:
        lines.extend(["", "All mapped legacy keys have a v2.1 comparison slot."])
    lines.append("")
    return "\n".join(lines)


def refusal_census_payloads(rows: Iterable[Mapping[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("status") != ResidualStatus.REFUSED.value:
            continue
        refusal = row.get("refusal") or {}
        if not isinstance(refusal, Mapping):
            continue
        reason = str(refusal.get("reason") or "refused")
        detail = refusal.get("detail") if isinstance(refusal.get("detail"), Mapping) else {}
        token = _short_refusal_token((detail or {}).get("reason"))
        key = reason if token is None else f"{reason}:{token}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _empty_headline_payload_stats() -> dict[str, object]:
    return {
        "n_candidates": 0,
        "n_refused": 0,
        "n_eligible_references": 0,
        "n_scored": 0,
        "n_score_eligible": 0,
        "n_inside_band": 0,
        "n_banded": 0,
        "n_no_band": 0,
        "dex_values": [],
        "band_widths": set(),
    }


def _headline_payload_record(
    tier: str,
    rail: str,
    engine: str,
    stats: Mapping[str, object],
    *,
    typed_widths: Iterable[str] | None = None,
    include_eligible_references: bool = True,
) -> dict[str, object]:
    dex_values = stats["dex_values"]
    band_widths = stats["band_widths"]
    if typed_widths is not None:
        band_widths = {as_decimal(value) for value in typed_widths}
    n_scored = int(stats["n_scored"])
    rms = _rms(dex_values)
    signed_median = _median(dex_values)
    band_width = next(iter(band_widths)) if len(band_widths) == 1 else None
    rms_over_band = (
        None
        if rms is None or band_width is None or band_width <= 0
        else rms / band_width
    )
    record = {
        "tier": tier,
        "rail": rail,
        "engine": engine,
        "n": n_scored,
        "n_candidates": stats["n_candidates"],
        "n_refused": stats["n_refused"],
        "n_eligible_references": stats["n_eligible_references"],
        "n_scored": n_scored,
        "n_score_eligible": stats["n_score_eligible"],
        "n_inside_band": stats["n_inside_band"],
        "n_match": stats["n_inside_band"],
        "match_rate": (
            None
            if not stats["n_banded"]
            else int(stats["n_inside_band"]) / int(stats["n_banded"])
        ),
        "match_rate_label": (
            "band membership; derived_2xMAD means tail membership, not accuracy"
            if tier == "compilation"
            else "band membership"
        ),
        "rms_dex": None if rms is None else str(rms),
        "median_dex": None if signed_median is None else str(signed_median),
        "median_abs_dex": None if not dex_values else str(_median_abs(dex_values)),
        "band_width_dex": None if band_width is None else str(band_width),
        "rms_over_band": None if rms_over_band is None else str(rms_over_band),
        "n_no_band": stats["n_no_band"],
        "decision_strata": [],
        "data_scatter_ratio": None if rms_over_band is None else str(rms_over_band),
    }
    if not include_eligible_references:
        record.pop("n_eligible_references")
    return record


def headline_payloads(
    rows: Iterable[Mapping[str, object]],
    engines: Sequence[Engine],
    *,
    tier: str = "measured",
) -> list[dict[str, object]]:
    if tier not in {"measured", "compilation"}:
        raise ValueError(f"unknown headline tier {tier!r}")
    groups: dict[tuple[str, str], dict[str, object]] = {}
    engine_names = [e.value for e in engines]
    for rail in Rail:
        for engine in engine_names:
            groups[(rail.value, engine)] = _empty_headline_payload_stats()
    for row in rows:
        if not _headline_payload_admits(row, tier=tier):
            continue
        rail = str(row.get("rail"))
        engine = str(
            ((row.get("candidate_request") or {}) if isinstance(row.get("candidate_request"), Mapping) else {}).get("engine")
            or str(row.get("key") or "").rsplit("::", 1)[-1]
        )
        stats = groups.setdefault(
            (rail, engine),
            _empty_headline_payload_stats(),
        )
        stats["n_candidates"] = int(stats["n_candidates"]) + 1
        status = str(row.get("status") or "")
        exclusions = tuple(row.get("exclusions") or ())
        if row.get("score_eligible") or "reference_measured_evidence" not in exclusions:
            stats["n_eligible_references"] = int(stats["n_eligible_references"]) + 1
        if status == ResidualStatus.REFUSED.value:
            stats["n_refused"] = int(stats["n_refused"]) + 1
        numeric = row.get("numeric")
        if not isinstance(numeric, Mapping):
            continue
        stats["n_scored"] = int(stats["n_scored"]) + 1
        if row.get("score_eligible"):
            stats["n_score_eligible"] = int(stats["n_score_eligible"]) + 1
        if status == ResidualStatus.MATCH.value:
            stats["n_inside_band"] = int(stats["n_inside_band"]) + 1
        if status in {ResidualStatus.MATCH.value, ResidualStatus.MISMATCH.value}:
            stats["n_banded"] = int(stats["n_banded"]) + 1
        if status == ResidualStatus.NO_BAND.value:
            stats["n_no_band"] = int(stats["n_no_band"]) + 1
        if numeric.get("operation") == MetricOperation.DEX.value:
            try:
                stats["dex_values"].append(as_decimal(numeric.get("value")))  # type: ignore[union-attr]
            except (TypeError, ValueError, ArithmeticError):
                pass
        band = numeric.get("decision_band")
        if (
            numeric.get("operation") == MetricOperation.DEX.value
            and isinstance(band, Mapping)
            and band.get("unit") == "dimensionless"
        ):
            try:
                stats["band_widths"].add(as_decimal(band.get("value")))  # type: ignore[union-attr]
            except (TypeError, ValueError, ArithmeticError):
                pass
    out: list[dict[str, object]] = []
    for (rail, engine), stats in sorted(groups.items()):
        typed_widths = getattr(rows, "_typed_headline_band_values", {}).get(
            (tier, rail, engine)
        )
        out.append(
            _headline_payload_record(
                tier,
                rail,
                engine,
                stats,
                typed_widths=typed_widths,
                include_eligible_references=False,
            )
        )
    return out


def _headline_payload_admits(row: Mapping[str, object], *, tier: str) -> bool:
    """Row filter of the measured/compilation payload headline (one owner).

    Flagged rows never enter; the measured tier also needs measured evidence.
    The all-numeric headline consumes this as the payload B decision.
    """

    if _flagged_payload_strata(row):
        return False
    if tier == "measured" and not _reference_has_measured_evidence(
        None, exclusions=row.get("exclusions")
    ):
        return False
    return bool(row.get("rail"))


def _flagged_payload_strata(row: Mapping[str, object]) -> tuple[str, ...]:
    notices = tuple(
        notice
        for notice in row.get("notices") or ()
        if isinstance(notice, Mapping)
    )
    kinds = {str(notice.get("kind")) for notice in notices if notice.get("kind")}
    out: list[str] = []
    unverified_apparatus = tuple(
        notice
        for notice in notices
        if notice.get("kind") == NoticeKind.UNVERIFIED_APPARATUS.value
    )
    if any(
        _is_calibration_not_grounded_reason(notice.get("reason"))
        for notice in unverified_apparatus
    ):
        out.append(FLAGGED_STRATUM_CALIBRATION_NOT_GROUNDED)
    if any(
        not _is_calibration_not_grounded_reason(notice.get("reason"))
        for notice in unverified_apparatus
    ):
        out.append(FLAGGED_STRATUM_UNVERIFIED_APPARATUS)
    if NoticeKind.CELL_MATERIAL_INFERRED.value in kinds:
        out.append(FLAGGED_STRATUM_CELL_MATERIAL_INFERRED)
    if NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG.value in kinds:
        out.append(FLAGGED_STRATUM_CATALOGUE_COMPOSITION)
    if any(
        _is_source_internally_inconsistent(
            str(notice.get("kind") or ""), notice.get("reason")
        )
        for notice in notices
    ):
        out.append(FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT)
    if NoticeKind.IMCC_COMPLEX_SATURATION.value in kinds:
        out.append(FLAGGED_STRATUM_IMCC_COMPLEX_SATURATION)
    if any(_is_fusion_conversion_reason(notice.get("reason")) for notice in notices):
        out.append(FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION)
    if NoticeKind.FIGURE_ONLY.value in kinds:
        out.append(FLAGGED_STRATUM_FIGURE_ONLY)
    if NoticeKind.REACTIVE_CELL_NOT_MODELLED.value in kinds:
        out.append(FLAGGED_STRATUM_REACTIVE_CELL_NOT_MODELLED)
    return tuple(out)


def _has_flagged_decision_notice(residual: Residual) -> bool:
    return any(
        _is_flagged_stratum_notice(notice)
        or _is_fusion_conversion_notice(notice)
        for notice in residual.notices
    )


def _family_pool_key(
    residual: Residual,
    family_quantity: tuple[str, Quantity] | None,
) -> tuple[str, Quantity, str, str] | None:
    numeric = residual.numeric
    if (
        numeric is None
        or residual.status is ResidualStatus.REFUSED
        or family_quantity is None
        or (
            numeric.decision_band is not None
            and numeric.decision_band.rule == "source-printed per-cell uncertainty"
        )
        or _has_flagged_decision_notice(residual)
    ):
        return None
    family, quantity = family_quantity
    engine = residual.key.rsplit("::", 1)[-1]
    return (family, quantity, numeric.unit, engine)


def _family_band_update(
    *,
    numeric: ResidualNumeric | None,
    status: ResidualStatus,
    key: str,
    source_relation: SourceRelation,
    family_quantity: tuple[str, Quantity] | None,
    flagged: bool,
    family_pool_sizes: Mapping[tuple[str, Quantity, str, str], int],
    family_bands: Mapping[tuple[str, Quantity, str, str], DecisionBand],
) -> tuple[bool, DecisionBand | None, bool]:
    if (
        numeric is None
        or status is ResidualStatus.REFUSED
        or family_quantity is None
        or (
            numeric.decision_band is not None
            and numeric.decision_band.rule == "source-printed per-cell uncertainty"
        )
        or flagged
    ):
        return False, None, False
    family, quantity = family_quantity
    engine = key.rsplit("::", 1)[-1]
    band_key = (family, quantity, numeric.unit, engine)
    band = family_bands.get(band_key)
    has_insufficient_pool = (
        band is None
        and 0 < family_pool_sizes.get(band_key, 0) < MIN_DERIVED_BAND_N
    )
    if band is None and not has_insufficient_pool:
        band = decision_band_for(quantity, source_relation)
    if has_insufficient_pool:
        return True, None, True
    if band is None or not band_dimension_matches(quantity, band):
        return False, None, False
    return True, band, False


class _ResidualJsonlStream:
    """Keep the in-flight JSONL on disk and sort rows without retaining them."""

    _FLUSH_LINES = 1000
    _INDEX_BATCH = 10_000

    def __init__(
        self,
        path: Path,
        *,
        root: Path | None = None,
    ) -> None:
        self.partial_path = path.with_name(path.name + ".partial")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.partial_path.open("wb")
        self.file.write(
            (dumps_store_stamp(derive_store_stamp(root or REPO_ROOT)) + "\n").encode(
                "utf-8"
            )
        )
        self.file.flush()
        self._temporary = tempfile.TemporaryDirectory(prefix="battery-score-index-")
        self._database = sqlite3.connect(
            Path(self._temporary.name) / "residual-order.sqlite"
        )
        self._database.execute("PRAGMA cache_size=-16384")
        self._database.execute("PRAGMA temp_store=FILE")
        self._database.execute("PRAGMA journal_mode=OFF")
        self._database.execute("PRAGMA synchronous=OFF")
        self._database.execute(
            "CREATE TABLE residual_order ("
            "seq INTEGER PRIMARY KEY, rail TEXT NOT NULL, reference TEXT NOT NULL, "
            "key TEXT NOT NULL, offset INTEGER NOT NULL, family TEXT, quantity TEXT, "
            "engine TEXT NOT NULL, relation TEXT NOT NULL, band_value TEXT, "
            "band_rule TEXT, status TEXT NOT NULL, flagged INTEGER NOT NULL, "
            "has_numeric INTEGER NOT NULL, numeric_value TEXT)"
        )
        self._pending: list[
            tuple[
                str, str, str, int, str | None, str | None, str, str,
                str | None, str | None, str, int, int, str | None
            ]
        ] = []
        self.band_values: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
        self.headline_band_values: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        self.tier_band_values: dict[
            tuple[str, str, str, str, str, str], set[str]
        ] = defaultdict(set)
        self.report_aggregate: _ScorePayloadAccumulator | None = None
        self.count = 0
        self._last_flush = time.monotonic()

    def append(
        self,
        residual: Residual,
        candidate: Observation | None,
        family_quantity: tuple[str, Quantity] | None,
    ) -> None:
        offset = self.file.tell()
        line = dumps_residual_line(residual, candidate).encode("utf-8")
        self.file.write(line + b"\n")
        numeric = residual.numeric
        decision_band = None if numeric is None else numeric.decision_band
        flagged = _has_flagged_decision_notice(residual)
        band_value = None if decision_band is None else str(decision_band.value)
        numeric_value = None if numeric is None else str(numeric.value)
        self._pending.append(
            (
                "" if residual.rail is None else residual.rail.value,
                residual.reference,
                residual.key,
                offset,
                None if family_quantity is None else family_quantity[0],
                None if family_quantity is None else family_quantity[1].value,
                residual.key.rsplit("::", 1)[-1],
                residual.source_relation.value,
                band_value,
                None if decision_band is None else decision_band.rule,
                residual.status.value,
                int(flagged),
                int(numeric is not None),
                numeric_value,
            )
        )
        self.count += 1
        if len(self._pending) >= self._INDEX_BATCH:
            self._flush_index()
        now = time.monotonic()
        if self.count % self._FLUSH_LINES == 0 or now - self._last_flush >= 5:
            self.flush()

    def _flush_index(self) -> None:
        if not self._pending:
            return
        self._database.executemany(
            "INSERT INTO residual_order (rail, reference, key, offset, family, quantity, "
            "engine, relation, band_value, band_rule, status, flagged, has_numeric, "
            "numeric_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            self._pending,
        )
        self._database.commit()
        self._pending.clear()

    def flush(self) -> None:
        self.file.flush()
        self._last_flush = time.monotonic()

    def finalize(
        self,
        *,
        family_pool_sizes: Mapping[tuple[str, Quantity, str, str], int],
        family_bands: Mapping[tuple[str, Quantity, str, str], DecisionBand],
        context: ScoreContext,
        engines: Sequence[Engine],
    ) -> None:
        self.flush()
        self._flush_index()
        self._database.execute(
            "CREATE INDEX residual_order_key ON residual_order("
            "rail COLLATE BINARY, reference COLLATE BINARY, key COLLATE BINARY, seq)"
        )
        report_aggregate = _ScorePayloadAccumulator(
            context,
            engines,
            typed_family_band_values=self.band_values,
            typed_headline_band_values=self.headline_band_values,
            typed_tier_band_values=self.tier_band_values,
        )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".residuals-final-", dir=self.partial_path.parent,
                delete=False,
            ) as output:
                temporary_path = Path(output.name)
                with self.partial_path.open("rb") as source:
                    output.write(source.readline())
                    rows = self._database.execute(
                        "SELECT offset, reference, family, quantity, engine, relation, "
                        "band_value, band_rule, status, flagged, has_numeric, "
                        "numeric_value "
                        "FROM residual_order "
                        "ORDER BY rail COLLATE BINARY, reference COLLATE BINARY, "
                        "key COLLATE BINARY, seq"
                    )
                    for (
                        offset,
                        reference,
                        family,
                        quantity,
                        engine,
                        relation,
                        band_value,
                        band_rule,
                        status,
                        flagged,
                        has_numeric,
                        numeric_value,
                    ) in rows:
                        source.seek(offset)
                        raw_line = source.readline()
                        can_apply_family_band = (
                            family is not None
                            and quantity is not None
                            and status != ResidualStatus.REFUSED.value
                            and band_rule != "source-printed per-cell uncertainty"
                            and not flagged
                            and has_numeric
                        )
                        payload = json.loads(raw_line)
                        applied = False
                        typed_band_value = None
                        if can_apply_family_band:
                            applied, typed_band_value = _apply_family_band_to_payload(
                                payload,
                                family_pool_sizes=family_pool_sizes,
                                family_bands=family_bands,
                                family_quantity=(
                                    None
                                    if family is None or quantity is None
                                    else (family, Quantity(quantity))
                                ),
                            )
                        band_value = typed_band_value if applied else band_value
                        numeric_payload = payload.get("numeric")
                        report_payload = payload
                        if (
                            isinstance(numeric_payload, dict)
                            and numeric_value is not None
                            and str(numeric_payload.get("value")) != numeric_value
                        ):
                            report_numeric = dict(numeric_payload)
                            report_numeric["value"] = numeric_value
                            report_payload = dict(payload)
                            report_payload["numeric"] = report_numeric
                        row_metadata = report_aggregate.add(report_payload)
                        if band_value is not None:
                            self._collect_report_band_values(
                                payload,
                                typed_value=str(band_value),
                                row_metadata=row_metadata,
                            )
                        if applied:
                            raw_line = json.dumps(
                                payload,
                                sort_keys=True,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ).encode("utf-8") + b"\n"
                        output.write(raw_line)
                output.flush()
            self.report_aggregate = report_aggregate
            os.replace(temporary_path, self.partial_path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _collect_report_band_values(
        self,
        payload: Mapping[str, object],
        *,
        typed_value: str,
        row_metadata: _ScorePayloadRowMetadata,
    ) -> None:
        numeric = payload.get("numeric")
        if not isinstance(numeric, Mapping):
            return
        rail = str(payload.get("rail") or "")
        flags = _flagged_payload_strata(payload)
        if (
            not rail
            or payload.get("status") == ResidualStatus.REFUSED.value
            or flags
        ):
            return
        band = numeric.get("decision_band")
        if not isinstance(band, Mapping):
            return
        self._collect_report_band_values_from_fields(
            typed_value=typed_value,
            rail=rail,
            status=str(payload.get("status") or ""),
            flagged=bool(flags),
            operation=str(numeric.get("operation") or ""),
            unit=str(numeric.get("unit") or ""),
            band_unit=str(band.get("unit") or ""),
            source_relation=str(payload.get("source_relation") or ""),
            row_metadata=row_metadata,
        )

    def _collect_report_band_values_from_fields(
        self,
        *,
        typed_value: str,
        rail: str,
        status: str,
        flagged: bool,
        operation: str,
        unit: str,
        band_unit: str,
        source_relation: str,
        row_metadata: _ScorePayloadRowMetadata,
    ) -> None:
        if not rail or status == ResidualStatus.REFUSED.value or flagged:
            return
        if row_metadata.is_compilation:
            tier = "compilation"
        elif row_metadata.is_measured:
            tier = "measured"
        else:
            return
        if (
            operation == MetricOperation.DEX.value
            and band_unit == "dimensionless"
        ):
            self.headline_band_values[
                (tier, rail, row_metadata.engine)
            ].add(typed_value)
        family_quantity = row_metadata.family_quantity
        if family_quantity is None or not operation or not unit:
            return
        family, quantity = family_quantity
        self.band_values[
            (family, quantity.value, row_metadata.engine, source_relation)
        ].add(typed_value)
        key = (
            family,
            rail,
            row_metadata.engine,
            source_relation or SourceRelation.UNKNOWN.value,
            quantity.value,
            unit,
        )
        self.tier_band_values[key].add(typed_value)

    def close(self) -> None:
        if not self.file.closed:
            self.file.close()
        self._database.close()
        self._temporary.cleanup()


def _apply_family_band_to_payload(
    payload: dict[str, object],
    *,
    family_pool_sizes: Mapping[tuple[str, Quantity, str, str], int],
    family_bands: Mapping[tuple[str, Quantity, str, str], DecisionBand],
    family_quantity: tuple[str, Quantity] | None,
) -> tuple[bool, str | None]:
    numeric_payload = payload.get("numeric")
    if not isinstance(numeric_payload, dict):
        return False, None
    raw_band = numeric_payload.get("decision_band")
    band = (
        DecisionBand(
            as_decimal(raw_band["value"]),
            str(raw_band["unit"]),
            str(raw_band["rule"]),
        )
        if isinstance(raw_band, Mapping)
        else None
    )
    numeric = ResidualNumeric(
        operation=MetricOperation(str(numeric_payload["operation"])),
        unit=str(numeric_payload["unit"]),
        value=as_decimal(numeric_payload["value"]),
        decision_band=band,
    )
    status = ResidualStatus(str(payload["status"]))
    source_relation = SourceRelation(str(payload["source_relation"]))
    apply, updated_band, no_band = _family_band_update(
        numeric=numeric,
        status=status,
        key=str(payload.get("key") or ""),
        source_relation=source_relation,
        family_quantity=family_quantity,
        flagged=bool(_flagged_payload_strata(payload)),
        family_pool_sizes=family_pool_sizes,
        family_bands=family_bands,
    )
    if not apply:
        return False, None
    if no_band:
        numeric_payload.pop("decision_band", None)
        payload["status"] = ResidualStatus.NO_BAND.value
    else:
        assert updated_band is not None
        plain_band = to_plain(updated_band)
        numeric_payload["decision_band"] = plain_band
        updated_numeric = replace(numeric, decision_band=updated_band)
        payload["status"] = match_status(updated_numeric).value
    return True, None if no_band else str(updated_band.value)


def flagged_stratum_payloads(
    rows: Iterable[Mapping[str, object]],
    engines: Sequence[Engine],
) -> list[dict[str, object]]:
    groups: dict[tuple[str, str, str], list[Decimal]] = {}
    counts: dict[tuple[str, str, str], int] = {}
    engine_names = {engine.value for engine in engines}
    for row in rows:
        numeric = row.get("numeric")
        if not isinstance(numeric, Mapping):
            continue
        rail = str(row.get("rail") or "")
        if not rail:
            continue
        engine = str(
            ((row.get("candidate_request") or {}) if isinstance(row.get("candidate_request"), Mapping) else {}).get("engine")
            or str(row.get("key") or "").rsplit("::", 1)[-1]
        )
        if engine_names and engine not in engine_names:
            continue
        for stratum in _flagged_payload_strata(row):
            key = (stratum, rail, engine)
            counts[key] = counts.get(key, 0) + 1
            dex = numeric.get("value") if numeric.get("operation") == MetricOperation.DEX.value else None
            if dex is None:
                continue
            try:
                value = as_decimal(dex)
            except (TypeError, ValueError, ArithmeticError):
                continue
            groups.setdefault(key, []).append(value)
    return [
        {
            "stratum": stratum,
            "rail": rail,
            "engine": engine,
            "n": counts[(stratum, rail, engine)],
            "median_dex": (
                None
                if not groups.get((stratum, rail, engine))
                else str(_median(groups[(stratum, rail, engine)]))
            ),
            "rms_dex": (
                None
                if not groups.get((stratum, rail, engine))
                else str(_rms(groups[(stratum, rail, engine)]))
            ),
        }
        for stratum, rail, engine in sorted(counts)
    ]


def headline_payload_records(
    rows: Iterable[Mapping[str, object]],
    *,
    engines: Sequence[Engine],
    observations: Mapping[str, Observation] | None = None,
    origins: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Machine-readable headline records from residual payloads."""

    compilation_rows: Iterable[Mapping[str, object]] = ()
    is_measured = _payload_measured_reference(observations, origins)
    measured_rows: Iterable[Mapping[str, object]] = (
        row for row in rows if is_measured(row)
    )
    if observations is not None:
        from simulator.battery.compilation_tier import compilation_row_observation

        def is_compilation(row: Mapping[str, object]) -> bool:
            return (
                compilation_row_observation(
                    str(row.get("reference") or ""), observations, origins
                )
                is not None
            )

        compilation_rows = (row for row in rows if is_compilation(row))
    records = [
        *all_numeric_payload_records(
            rows, engines=engines, observations=observations, origins=origins
        ),
        *headline_payloads(measured_rows, engines, tier="measured"),
        *headline_payloads(compilation_rows, engines, tier="compilation"),
    ]
    if observations is not None:
        compilation_rows = (row for row in rows if is_compilation(row))
        from simulator.battery.compilation_tier import (
            compilation_family,
            compilation_origin,
            compilation_row_observation,
        )

        payload_groups: dict[tuple[str, str, str, str, str], dict[str, object]] = {}
        derived_ns: dict[tuple[str, str, str], int] = defaultdict(int)
        for row in compilation_rows:
            reference = str(row.get("reference") or "")
            observation = compilation_row_observation(reference, observations, origins)
            numeric = row.get("numeric")
            if observation is None or not isinstance(observation.identity, Identity) or not isinstance(numeric, Mapping):
                continue
            quantity = quantity_token(observation.identity)
            if quantity is None:
                continue
            engine = str(
                ((row.get("candidate_request") or {}) if isinstance(row.get("candidate_request"), Mapping) else {}).get("engine")
                or str(row.get("key") or "").rsplit("::", 1)[-1]
            )
            family = compilation_family(observation.source_id, compilation_origin(reference, origins))
            relation = str(row.get("source_relation") or SourceRelation.UNKNOWN.value)
            key = (family, quantity.value, engine)
            if str(row.get("status") or "") == ResidualStatus.REFUSED.value:
                continue
            band_data = numeric.get("decision_band")
            band = None if not isinstance(band_data, Mapping) else DecisionBand(
                as_decimal(band_data["value"]), str(band_data["unit"]), str(band_data["rule"])
            )
            flagged = bool(_flagged_payload_strata(row))
            if flagged:
                continue
            if band is None and not flagged:
                derived_ns[key] += 1
            rail = str(row.get("rail") or "")
            group = payload_groups.setdefault(
                (*key, relation, rail),
                {
                    "values": [],
                    "kinds": set(),
                    "widths": set(),
                    "units": set(),
                    "band_derived_n": set(),
                    "match_count": 0,
                    "mismatch_count": 0,
                    "no_band_count": 0,
                    "tail_in_count": 0,
                    "tail_out_count": 0,
                },
            )
            value = as_decimal(numeric["value"])
            group["values"].append(value)  # type: ignore[union-attr]
            status = str(row.get("status") or "")
            if status == ResidualStatus.MATCH.value:
                group["match_count"] = int(group["match_count"]) + 1
            elif status == ResidualStatus.MISMATCH.value:
                group["mismatch_count"] = int(group["mismatch_count"]) + 1
            elif status == ResidualStatus.NO_BAND.value:
                group["no_band_count"] = int(group["no_band_count"]) + 1
            if band is None:
                continue
            if band.rule == "source-printed per-cell uncertainty":
                group["kinds"].add("printed")  # type: ignore[union-attr]
            elif "residual distribution" in band.rule:
                group["kinds"].add("derived_2xMAD")  # type: ignore[union-attr]
                if status == ResidualStatus.MATCH.value:
                    group["tail_in_count"] = int(group["tail_in_count"]) + 1
                elif status == ResidualStatus.MISMATCH.value:
                    group["tail_out_count"] = int(group["tail_out_count"]) + 1
            else:
                group["kinds"].add("legacy_fallback")  # type: ignore[union-attr]
            group["widths"].add(str(band_data["value"]))  # type: ignore[union-attr]
            group["units"].add(band.unit)  # type: ignore[union-attr]
            if "derived_n=" in band.rule:
                group["band_derived_n"].add(  # type: ignore[union-attr]
                    int(band.rule.rsplit("derived_n=", 1)[1])
                )
        typed_band_values = getattr(rows, "_typed_family_band_values", {})
        for (family, quantity, engine, relation, _rail), group in payload_groups.items():
            exact_values = typed_band_values.get(
                (family, quantity, engine, relation)
            )
            if exact_values is not None:
                group["widths"] = set(exact_values)
        for record in records:
            if record.get("tier") != "compilation":
                continue
            engine = str(record["engine"])
            rail = str(record["rail"])
            grouped = [
                (key, group)
                for key, group in payload_groups.items()
                if key[2] == engine and key[4] == rail
            ]
            strata = []
            for (family, quantity, _engine, relation, _rail), group in sorted(grouped):
                values = group["values"]
                center = _median(values)
                mad = None if center is None else _median([abs(value - center) for value in values])
                kinds = sorted(group["kinds"]) or ["no_band"]
                derived_n = sorted(group["band_derived_n"])
                if not derived_n and not group["widths"] and derived_ns[(family, quantity, engine)] > 0:
                    derived_n = [derived_ns[(family, quantity, engine)]]
                widths = sorted(group["widths"])
                units = sorted(group["units"])
                derived = "derived_2xMAD" in kinds
                strata.append({
                    "family": family, "quantity": quantity, "engine": engine, "relation": relation,
                    "n": len(values),
                    "signed_median_residual": None if center is None else str(center),
                    "mad": None if mad is None else str(mad),
                    "bias_to_scatter_ratio": (
                        None
                        if (ratio := _bias_to_scatter_ratio(center, mad, len(values))) is None
                        else str(ratio)
                    ),
                    "max_abs_residual": None if not values else str(max(abs(value) for value in values)),
                    "band_value": widths[0] if len(widths) == 1 else widths,
                    "unit": units[0] if len(units) == 1 else units or QUANTITY_UNITS[Quantity(quantity)],
                    "band_kind": kinds[0] if len(kinds) == 1 else kinds,
                    "band_derived_n": derived_n[0] if len(derived_n) == 1 else derived_n,
                    "match_count": group["match_count"],
                    "mismatch_count": group["mismatch_count"],
                    "no_band_count": group["no_band_count"],
                    "tail_in_count": group["tail_in_count"] if derived else None,
                    "tail_out_count": group["tail_out_count"] if derived else None,
                    "no_band_reason": "derived_band_insufficient_n" if not group["widths"] and 0 < derived_ns[(family, quantity, engine)] < MIN_DERIVED_BAND_N else None,
                })
            record["decision_strata"] = strata
    return records


@dataclass(frozen=True)
class _ScorePayloadRowMetadata:
    reference: str
    engine: str
    observation: Observation | None
    origin: str | None
    is_compilation: bool
    is_measured: bool
    flags: tuple[str, ...]
    family_quantity: tuple[str, Quantity] | None
    tier_family: str
    tier_quantity: str
    tier_rail: str
    tier_uncertainty: str


class _ScorePayloadAccumulator:
    """Compact report statistics collected in one pass over residual payloads."""

    def __init__(
        self,
        context: ScoreContext,
        engines: Sequence[Engine],
        *,
        typed_family_band_values: Mapping[
            tuple[str, str, str, str], set[str]
        ] | None = None,
        typed_headline_band_values: Mapping[
            tuple[str, str, str], set[str]
        ] | None = None,
        typed_tier_band_values: Mapping[
            tuple[str, str, str, str, str, str], set[str]
        ] | None = None,
    ) -> None:
        from simulator.battery.compilation_tier import _TierMarkdownAccumulator

        self.context = context
        self.engines = tuple(engines)
        self.engine_names = {engine.value for engine in engines}
        self._row_metadata_cache: dict[
            str,
            tuple[
                Observation | None,
                str | None,
                bool,
                tuple[str, Quantity] | None,
                str,
                str,
                str,
                str,
            ],
        ] = {}
        self.typed_family_band_values = (
            {} if typed_family_band_values is None else typed_family_band_values
        )
        self.typed_headline_band_values = (
            {} if typed_headline_band_values is None else typed_headline_band_values
        )
        self.typed_tier_band_values = (
            {} if typed_tier_band_values is None else typed_tier_band_values
        )
        self.headline_groups: dict[
            str, dict[tuple[str, str], dict[str, object]]
        ] = {"measured": {}, "compilation": {}}
        self.all_numeric = _AllNumericHeadline()
        self.report_engine_names: set[str] = set()
        self.count = 0
        self.scored_count = 0
        self.flagged_counts: dict[tuple[str, str, str], int] = defaultdict(int)
        self.flagged_values: dict[tuple[str, str, str], list[Decimal]] = defaultdict(list)
        self.compilation_groups: dict[
            tuple[str, str, str, str, str], dict[str, object]
        ] = {}
        self.derived_ns: dict[tuple[str, str, str], int] = defaultdict(int)
        self.refusal_counts: dict[str, int] = defaultdict(int)
        self.admission_with = 0
        self.admission_alone = 0
        self.admission_alone_unique = 0
        self._last_admission_reference: str | None = None
        self.notice_counts: dict[str, int] = defaultdict(int)
        self.diagnostic_count = 0
        self.tier_accumulator = _TierMarkdownAccumulator(self.typed_tier_band_values)

        for tier in self.headline_groups:
            for rail in Rail:
                for engine in engines:
                    self.headline_groups[tier][(rail.value, engine.value)] = (
                        _empty_headline_payload_stats()
                    )

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[Mapping[str, object]],
        *,
        context: ScoreContext,
        engines: Sequence[Engine],
    ) -> _ScorePayloadAccumulator:
        aggregate = cls(
            context,
            engines,
            typed_family_band_values=getattr(rows, "_typed_family_band_values", None),
            typed_headline_band_values=getattr(rows, "_typed_headline_band_values", None),
            typed_tier_band_values=getattr(rows, "_typed_tier_band_values", None),
        )
        for row in rows:
            aggregate.add(row)
        return aggregate

    @staticmethod
    def _engine(row: Mapping[str, object]) -> str:
        request = row.get("candidate_request")
        return str(
            request.get("engine")
            if isinstance(request, Mapping) and request.get("engine")
            else str(row.get("key") or "").rsplit("::", 1)[-1]
        )

    def _add_headline(
        self,
        row: Mapping[str, object],
        *,
        tier: str,
        rail: str,
        engine: str,
        numeric_value: Decimal | None,
    ) -> None:
        groups = self.headline_groups[tier]
        stats = groups.setdefault((rail, engine), _empty_headline_payload_stats())
        stats["n_candidates"] = int(stats["n_candidates"]) + 1
        status = str(row.get("status") or "")
        exclusions = tuple(row.get("exclusions") or ())
        if row.get("score_eligible") or "reference_measured_evidence" not in exclusions:
            stats["n_eligible_references"] = int(stats["n_eligible_references"]) + 1
        if status == ResidualStatus.REFUSED.value:
            stats["n_refused"] = int(stats["n_refused"]) + 1
        numeric = row.get("numeric")
        if not isinstance(numeric, Mapping):
            return
        stats["n_scored"] = int(stats["n_scored"]) + 1
        if row.get("score_eligible"):
            stats["n_score_eligible"] = int(stats["n_score_eligible"]) + 1
        if status == ResidualStatus.MATCH.value:
            stats["n_inside_band"] = int(stats["n_inside_band"]) + 1
        if status in {ResidualStatus.MATCH.value, ResidualStatus.MISMATCH.value}:
            stats["n_banded"] = int(stats["n_banded"]) + 1
        if status == ResidualStatus.NO_BAND.value:
            stats["n_no_band"] = int(stats["n_no_band"]) + 1
        if (
            numeric.get("operation") == MetricOperation.DEX.value
            and numeric_value is not None
        ):
            stats["dex_values"].append(numeric_value)
        band = numeric.get("decision_band")
        if (
            numeric.get("operation") == MetricOperation.DEX.value
            and isinstance(band, Mapping)
            and band.get("unit") == "dimensionless"
        ):
            try:
                stats["band_widths"].add(as_decimal(band.get("value")))
            except (TypeError, ValueError, ArithmeticError):
                pass

    def _add_compilation_decision(
        self,
        row: Mapping[str, object],
        *,
        family_quantity: tuple[str, Quantity] | None,
        engine: str,
        flagged: bool,
        numeric_value: Decimal | None,
    ) -> None:
        numeric = row.get("numeric")
        if family_quantity is None or not isinstance(numeric, Mapping):
            return
        family, quantity = family_quantity
        status = str(row.get("status") or "")
        if status == ResidualStatus.REFUSED.value:
            return
        band_data = numeric.get("decision_band")
        if flagged:
            return
        key = (family, quantity.value, engine)
        if not isinstance(band_data, Mapping):
            self.derived_ns[key] += 1
        relation = str(row.get("source_relation") or SourceRelation.UNKNOWN.value)
        rail = str(row.get("rail") or "")
        group_key = (*key, relation, rail)
        group = self.compilation_groups.setdefault(
            group_key,
            {
                "values": [],
                "kinds": set(),
                "widths": set(),
                "units": set(),
                "band_derived_n": set(),
                "match_count": 0,
                "mismatch_count": 0,
                "no_band_count": 0,
                "tail_in_count": 0,
                "tail_out_count": 0,
            },
        )
        value = numeric_value
        if value is None:
            value = as_decimal(numeric["value"])
        group["values"].append(value)
        if status == ResidualStatus.MATCH.value:
            group["match_count"] = int(group["match_count"]) + 1
        elif status == ResidualStatus.MISMATCH.value:
            group["mismatch_count"] = int(group["mismatch_count"]) + 1
        elif status == ResidualStatus.NO_BAND.value:
            group["no_band_count"] = int(group["no_band_count"]) + 1
        if not isinstance(band_data, Mapping):
            return
        band_rule = str(band_data["rule"])
        if band_rule == "source-printed per-cell uncertainty":
            group["kinds"].add("printed")
        elif "residual distribution" in band_rule:
            group["kinds"].add("derived_2xMAD")
            if status == ResidualStatus.MATCH.value:
                group["tail_in_count"] = int(group["tail_in_count"]) + 1
            elif status == ResidualStatus.MISMATCH.value:
                group["tail_out_count"] = int(group["tail_out_count"]) + 1
        else:
            group["kinds"].add("legacy_fallback")
        group["widths"].add(str(band_data["value"]))
        group["units"].add(str(band_data["unit"]))
        if "derived_n=" in band_rule:
            group["band_derived_n"].add(int(band_rule.rsplit("derived_n=", 1)[1]))

    def _row_metadata(
        self, row: Mapping[str, object]
    ) -> _ScorePayloadRowMetadata:
        from simulator.battery.compilation_tier import (
            compilation_family,
            is_compilation_evidence,
            parent_observation_id,
            uncertainty_text,
        )

        reference = str(row.get("reference") or "")
        engine = self._engine(row)
        flags = _flagged_payload_strata(row)
        flagged = bool(flags)
        exclusions = row.get("exclusions")
        parent = parent_observation_id(reference)
        reference_observation = self.context.observations.get(reference)
        cache_key = (
            reference
            if reference_observation is not None or reference in self.context.origins
            else parent
        )
        cached = self._row_metadata_cache.get(cache_key)
        if cached is None:
            observation = reference_observation or self.context.observations.get(parent)
            origin = self.context.origins.get(reference) or self.context.origins.get(parent)
            is_compilation = bool(
                observation is not None
                and (
                    is_compilation_evidence(observation)
                    or is_compilation_source(observation.source_id, origin)
                )
            )
            family = ""
            tier_quantity = "unknown"
            tier_rail = "none"
            tier_uncertainty = ""
            family_quantity = None
            if is_compilation and observation is not None:
                family = compilation_family(observation.source_id, origin)
                token = (
                    quantity_token(observation.identity)
                    if isinstance(observation.identity, Identity)
                    else None
                )
                tier_quantity = token.value if token is not None else "unknown"
                formula = (
                    observation.identity.species.formula
                    if isinstance(observation.identity, Identity)
                    else ""
                )
                headline_rail = rail_for_quantity(token, species_formula=formula)
                if headline_rail is not None:
                    tier_rail = headline_rail.value
                tier_uncertainty = uncertainty_text(observation.uncertainty)
                if token is not None:
                    family_quantity = (family, token)
            cached = (
                observation,
                origin,
                is_compilation,
                family_quantity,
                family,
                tier_quantity,
                tier_rail,
                tier_uncertainty,
            )
            self._row_metadata_cache[cache_key] = cached
        (
            observation,
            origin,
            is_compilation,
            family_quantity,
            family,
            tier_quantity,
            tier_rail,
            tier_uncertainty,
        ) = cached
        measured = (
            not flagged
            and not is_compilation
            and _reference_has_measured_evidence(
                reference_observation, exclusions=exclusions
            )
        )
        return _ScorePayloadRowMetadata(
            reference=reference,
            engine=engine,
            observation=observation,
            origin=origin,
            is_compilation=is_compilation,
            is_measured=measured,
            flags=flags,
            family_quantity=family_quantity,
            tier_family=family,
            tier_quantity=tier_quantity,
            tier_rail=tier_rail,
            tier_uncertainty=tier_uncertainty,
        )

    def add(
        self,
        row: Mapping[str, object],
        *,
        row_metadata: _ScorePayloadRowMetadata | None = None,
    ) -> _ScorePayloadRowMetadata:
        from simulator.battery.compilation_tier import _tier_cell_from_payload_fields

        metadata = row_metadata or self._row_metadata(row)
        self.count += 1
        if row.get("score_eligible"):
            self.scored_count += 1
        numeric_payload = row.get("numeric")
        numeric_value = None
        if (
            isinstance(numeric_payload, Mapping)
            and numeric_payload.get("value") is not None
        ):
            try:
                numeric_value = as_decimal(numeric_payload["value"])
            except (TypeError, ValueError, ArithmeticError):
                pass
        reference = metadata.reference
        engine = metadata.engine
        flags = metadata.flags
        flagged = bool(flags)
        exclusions = row.get("exclusions")
        compilation_observation = metadata.observation if metadata.is_compilation else None
        measured = metadata.is_measured
        rail = str(row.get("rail") or "")
        # The certified (B) membership decision for this row, made once and
        # consumed by the all-numeric (A) owner rather than re-derived there.
        in_measured_headline = bool(
            measured
            and _reference_has_measured_evidence(None, exclusions=exclusions)
            and rail
        )
        if numeric_value is not None:
            # A never adds to report_engine_names: that set is B's grid.
            self.all_numeric.add(
                _all_numeric_fact(
                    row,
                    engine=engine,
                    observation=metadata.observation,
                    certified=in_measured_headline,
                    value=numeric_value,
                )
            )
        if measured:
            if in_measured_headline:
                self.report_engine_names.add(engine)
                self._add_headline(
                    row,
                    tier="measured",
                    rail=rail,
                    engine=engine,
                    numeric_value=numeric_value,
                )
            if row.get("status") == ResidualStatus.REFUSED.value:
                refusal = row.get("refusal") or {}
                if isinstance(refusal, Mapping):
                    reason = str(refusal.get("reason") or "refused")
                    detail = (
                        refusal.get("detail")
                        if isinstance(refusal.get("detail"), Mapping)
                        else {}
                    )
                    token = _short_refusal_token((detail or {}).get("reason"))
                    key = reason if token is None else f"{reason}:{token}"
                    self.refusal_counts[key] += 1
        if compilation_observation is not None:
            self.tier_accumulator.add(
                _tier_cell_from_payload_fields(
                    row,
                    family=metadata.tier_family,
                    quantity=metadata.tier_quantity,
                    rail=metadata.tier_rail,
                    uncertainty=metadata.tier_uncertainty,
                    derive_eligible=not flagged,
                    numeric_value=numeric_value,
                )
            )
            if not flagged:
                rail = str(row.get("rail") or "")
                if rail:
                    self._add_headline(
                        row,
                        tier="compilation",
                        rail=rail,
                        engine=engine,
                        numeric_value=numeric_value,
                    )
            self._add_compilation_decision(
                row,
                family_quantity=metadata.family_quantity,
                engine=engine,
                flagged=flagged,
                numeric_value=numeric_value,
            )
        numeric = row.get("numeric")
        rail = str(row.get("rail") or "")
        if (
            isinstance(numeric, Mapping)
            and rail
            and (not self.engine_names or engine in self.engine_names)
        ):
            dex = (
                numeric.get("value")
                if numeric.get("operation") == MetricOperation.DEX.value
                else None
            )
            for stratum in flags:
                key = (stratum, rail, engine)
                self.flagged_counts[key] += 1
                if dex is not None and numeric_value is not None:
                    self.flagged_values[key].append(numeric_value)
        exclusions_tuple = tuple(exclusions or ())
        if "admission_admitted" in exclusions_tuple:
            self.admission_with += 1
            if all(name == "admission_admitted" for name in exclusions_tuple):
                self.admission_alone += 1
                if reference != self._last_admission_reference:
                    self.admission_alone_unique += 1
                    self._last_admission_reference = reference
        for notice in row.get("notices") or ():
            if isinstance(notice, Mapping) and notice.get("kind"):
                self.notice_counts[str(notice["kind"])] += 1
        if (
            "reference_measured_evidence" in exclusions_tuple
            or is_internal_consistency(metadata.origin)
            or is_compilation_source(
                None if metadata.observation is None else metadata.observation.source_id,
                metadata.origin,
            )
        ):
            self.diagnostic_count += 1
        return metadata

    def headline_records(
        self,
        *,
        engines: Sequence[Engine] | None = None,
    ) -> list[dict[str, object]]:
        selected_names = (
            self.engine_names if engines is None else {engine.value for engine in engines}
        )
        records: list[dict[str, object]] = self.all_numeric.records(
            selected_names, include_seen=engines is None
        )
        for tier in ("measured", "compilation"):
            groups = self.headline_groups[tier]
            keys = {
                key for key in groups
                if engines is None or key[1] in selected_names
            }
            for rail in Rail:
                for engine in selected_names:
                    keys.add((rail.value, engine))
            for rail, engine in sorted(keys):
                stats = groups.get((rail, engine), _empty_headline_payload_stats())
                records.append(
                    _headline_payload_record(
                        tier,
                        rail,
                        engine,
                        stats,
                        typed_widths=self.typed_headline_band_values.get(
                            (tier, rail, engine)
                        ),
                    )
                )
        self._add_compilation_decision_strata(records)
        return records

    def _add_compilation_decision_strata(
        self, records: list[dict[str, object]]
    ) -> None:
        for (family, quantity, engine, relation, _rail), group in self.compilation_groups.items():
            exact = self.typed_family_band_values.get(
                (family, quantity, engine, relation)
            )
            if exact is not None:
                group["widths"] = set(exact)
        for record in records:
            if record.get("tier") != "compilation":
                continue
            engine = str(record["engine"])
            rail = str(record["rail"])
            grouped = [
                (key, group)
                for key, group in self.compilation_groups.items()
                if key[2] == engine and key[4] == rail
            ]
            strata = []
            for (family, quantity, _engine, relation, _rail), group in sorted(grouped):
                values = group["values"]
                center = _median(values)
                mad = None if center is None else _median(
                    [abs(value - center) for value in values]
                )
                kinds = sorted(group["kinds"]) or ["no_band"]
                derived_n = sorted(group["band_derived_n"])
                if (
                    not derived_n
                    and not group["widths"]
                    and self.derived_ns[(family, quantity, engine)] > 0
                ):
                    derived_n = [self.derived_ns[(family, quantity, engine)]]
                widths = sorted(group["widths"])
                units = sorted(group["units"])
                derived = "derived_2xMAD" in kinds
                strata.append(
                    {
                        "family": family,
                        "quantity": quantity,
                        "engine": engine,
                        "relation": relation,
                        "n": len(values),
                        "signed_median_residual": None if center is None else str(center),
                        "mad": None if mad is None else str(mad),
                        "bias_to_scatter_ratio": (
                            None
                            if (ratio := _bias_to_scatter_ratio(center, mad, len(values))) is None
                            else str(ratio)
                        ),
                        "max_abs_residual": None if not values else str(max(abs(value) for value in values)),
                        "band_value": widths[0] if len(widths) == 1 else widths,
                        "unit": units[0] if len(units) == 1 else units or QUANTITY_UNITS[Quantity(quantity)],
                        "band_kind": kinds[0] if len(kinds) == 1 else kinds,
                        "band_derived_n": derived_n[0] if len(derived_n) == 1 else derived_n,
                        "match_count": group["match_count"],
                        "mismatch_count": group["mismatch_count"],
                        "no_band_count": group["no_band_count"],
                        "tail_in_count": group["tail_in_count"] if derived else None,
                        "tail_out_count": group["tail_out_count"] if derived else None,
                        "no_band_reason": (
                            "derived_band_insufficient_n"
                            if not group["widths"]
                            and 0 < self.derived_ns[(family, quantity, engine)] < MIN_DERIVED_BAND_N
                            else None
                        ),
                    }
                )
            record["decision_strata"] = strata

    def flagged_records(self) -> list[dict[str, object]]:
        return [
            {
                "stratum": stratum,
                "rail": rail,
                "engine": engine,
                "n": self.flagged_counts[(stratum, rail, engine)],
                "median_dex": (
                    None
                    if not self.flagged_values.get((stratum, rail, engine))
                    else str(_median(self.flagged_values[(stratum, rail, engine)]))
                ),
                "rms_dex": (
                    None
                    if not self.flagged_values.get((stratum, rail, engine))
                    else str(_rms(self.flagged_values[(stratum, rail, engine)]))
                ),
            }
            for stratum, rail, engine in sorted(self.flagged_counts)
        ]

    def compilation_lines(self) -> list[str]:
        from simulator.battery.compilation_tier import _tier_markdown

        return _tier_markdown((), accumulator=self.tier_accumulator)

    def summary_payload(self, store_stamp: Mapping[str, object] | None) -> dict[str, object]:
        return headline_summary_payload(
            self.headline_records(), store_stamp=store_stamp
        )


def write_headline_summary_from_payloads_json(
    rows: Iterable[Mapping[str, object]],
    path: Path,
    *,
    engines: Sequence[Engine],
    observations: Mapping[str, Observation] | None = None,
    origins: Mapping[str, str] | None = None,
    store_stamp: Mapping[str, object] | None = None,
) -> None:
    payload = headline_summary_payload(
        headline_payload_records(
            rows,
            engines=engines,
            observations=observations,
            origins=origins,
        ),
        store_stamp=store_stamp,
    )
    _write_headline_summary_payload_json(payload, path)


def _write_headline_summary_from_accumulator_json(
    aggregate: _ScorePayloadAccumulator,
    path: Path,
    *,
    store_stamp: Mapping[str, object] | None,
) -> None:
    _write_headline_summary_payload_json(
        aggregate.summary_payload(store_stamp), path
    )


def _write_headline_summary_payload_json(
    payload: Mapping[str, object],
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def notice_backlog_payloads(rows: Iterable[Mapping[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for notice in row.get("notices") or ():
            if isinstance(notice, Mapping) and notice.get("kind"):
                kind = str(notice["kind"])
                counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


class _FilteredPayloadRows:
    def __init__(
        self,
        rows: Iterable[Mapping[str, object]],
        predicate: Callable[[Mapping[str, object]], bool],
    ) -> None:
        self.rows = rows
        self.predicate = predicate
        for name in (
            "_typed_family_band_values",
            "_typed_headline_band_values",
            "_typed_tier_band_values",
        ):
            if hasattr(rows, name):
                setattr(self, name, getattr(rows, name))

    def __iter__(self) -> Iterable[Mapping[str, object]]:
        return (row for row in self.rows if self.predicate(row))


def render_score_report_from_payloads(
    rows: Iterable[Mapping[str, object]],
    *,
    engines: Sequence[Engine],
    hostname: str,
    pin_failures: Sequence[Mapping[str, object]] = (),
    status_diff: Sequence[Mapping[str, object]] = (),
    unmapped_legacy_keys: Sequence[str] = (),
    studio_hostname: str | None = None,
    store_stamp: Mapping[str, object] | None = None,
    mismatch_warning: str | None = None,
    observations: Mapping[str, Observation] | None = None,
    origins: Mapping[str, str] | None = None,
) -> str:
    unflagged_rows = _FilteredPayloadRows(
        rows, lambda row: not _flagged_payload_strata(row)
    )
    measured_rows: Iterable[Mapping[str, object]] = unflagged_rows
    compilation_lines: list[str] = []
    if observations is not None:
        from simulator.battery.compilation_tier import (
            compilation_tier_lines_from_payloads,
        )

        measured_rows = _FilteredPayloadRows(
            unflagged_rows, _payload_measured_reference(observations, origins)
        )
        compilation_lines = compilation_tier_lines_from_payloads(
            unflagged_rows, observations, origins
        )
    lines: list[str] = [
        "# Battery score report (schema v2.1)",
        "",
        "Generated only. Pins are an independent baseline and are never",
        "re-centred from these residuals. Refusals are diagnostics, never hidden.",
        "Headline accuracy per rail reports numeric n, inside-band n, RMS dex,",
        "median dex, band width, and RMS/band. Measured and compilation tiers",
        "are separate and never summed; match rate is secondary.",
        "",
        f"Hostname: `{hostname}`.",
    ]
    if studio_hostname:
        lines.append(f"Studio hostname: `{studio_hostname}`.")
    lines.extend(
        ["", *format_store_stamp_report_lines(store_stamp, mismatch_warning=mismatch_warning)]
    )
    lines.extend(
        [
            "",
            f"Engines: {', '.join(e.value for e in engines)}.",
            "",
            *_all_numeric_markdown_lines(
                all_numeric_payload_records(
                    rows, engines=engines, observations=observations, origins=origins
                )
            ),
        ]
    )
    lines.extend(
        [
            "",
            "## Measured tier" if observations is not None else "## Per rail × engine headline",
            "",
            *(
                [
                    "Numeric measured rows stay visible. score_eligible and inside-band "
                    "counts are reported separately.",
                    "",
                ]
                if observations is not None
                else []
            ),
            "| rail | engine | n candidates | n refused | n scored | n score eligible | "
            "n inside band | RMS dex | median dex | median abs dex | band width dex | "
            "RMS/band | n no band | match rate |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in headline_payloads(measured_rows, engines):
        rate = row["match_rate"]
        rate_s = "—" if rate is None else f"{rate:.3f}"
        rms = row["rms_dex"] or "—"
        med = row["median_dex"] or "—"
        med_abs = row["median_abs_dex"] or "—"
        band = row["band_width_dex"] or "—"
        ratio = row["rms_over_band"] or "—"
        lines.append(
            f"| {row['rail']} | {row['engine']} | {row['n_candidates']} | "
            f"{row['n_refused']} | {row['n']} | {row['n_score_eligible']} | "
            f"{row['n_inside_band']} | {rms} | {med} | {med_abs} | {band} | {ratio} | "
            f"{row['n_no_band']} | {rate_s} |"
        )
    lines.extend(
        [
            "",
            "## Flagged strata",
            "",
            "Flagged numeric diagnostics are excluded from the measured headline, "
            "bands, and band statistics.",
            "",
            "| stratum | rail | engine | n | median dex | RMS dex |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    flagged_rows = flagged_stratum_payloads(rows, engines)
    if not flagged_rows:
        lines.append("| (none) | — | — | 0 | — | — |")
    else:
        for row in flagged_rows:
            lines.append(
                f"| {row['stratum']} | {row['rail']} | {row['engine']} | "
                f"{row['n']} | {row['median_dex'] or '—'} | {row['rms_dex'] or '—'} |"
            )
    if compilation_lines:
        lines.extend(["", *compilation_lines])
    lines.extend(
        [
            "",
            "## Refusal census",
            "",
            *(
                ["Measured tier only. Compilation refusals are in the compilation table.", ""]
                if observations is not None
                else []
            ),
            "| reason | n |",
            "|---|---:|",
        ]
    )
    census = refusal_census_payloads(measured_rows)
    if not census:
        lines.append("| (none) | 0 |")
    else:
        for reason, n in census.items():
            lines.append(f"| `{reason}` | {n} |")
    admit = admission_census_payloads(rows)
    lines.extend(
        [
            "",
            "## Admission",
            "",
            "score_eligible requires canonical admission admitted. Pending rows",
            "stay in the comparison set as diagnostics (flagged and priced when",
            "numeric, never the empirical headline). The admission rule is unchanged.",
            "",
            "| count | n |",
            "|---|---:|",
            f"| residuals with admission_admitted exclusion | {admit['residuals_with_admission_exclusion']} |",
            f"| residuals that die on admission alone | {admit['residuals_admission_alone']} |",
            f"| unique observations that die on admission alone | {admit['unique_obs_admission_alone']} |",
        ]
    )
    lines.extend(
        [
            "",
            "## Notice backlog (certification projects)",
            "",
            "| notice kind | n |",
            "|---|---:|",
        ]
    )
    backlog = notice_backlog_payloads(rows)
    if not backlog:
        lines.append("| (none) | 0 |")
    else:
        for kind, n in backlog.items():
            lines.append(f"| `{kind}` | {n} |")
    n_diag = sum(
        1
        for r in rows
        if "reference_measured_evidence" in (r.get("exclusions") or ())
        or (
            isinstance(r.get("refusal"), Mapping)
            and ((r.get("refusal") or {}).get("detail") or {}).get("reason")
            == "diagnostic_population"
        )
    )
    lines.extend(
        [
            "",
            "## Internal-consistency / compilation diagnostics",
            "",
            "species_rail_differential_ledger and gibbs_battery_residual_ledger",
            "are internal-consistency instruments, not scoring ledgers. Compilation",
            "observations are engine reference inputs. Neither population enters",
            "the empirical headline. " + CIRCULARITY_WARNING,
            "",
            f"Diagnostic residuals in this file: {n_diag}.",
            "",
            "## Pin failures",
            "",
        ]
    )
    if not pin_failures:
        lines.append("None.")
    else:
        lines.append(
            f"{len(pin_failures)} pin failures (coverage or outside pin_band). "
            "A live residual outside its pin_band is a failure, never a re-centre."
        )
        lines.append("")
        lines.append("| key | reason | live | centre | pin_band |")
        lines.append("|---|---|---:|---:|---:|")
        for failure in list(pin_failures)[:50]:
            lines.append(
                f"| `{failure.get('key')}` | {failure.get('reason')} | {failure.get('live')} | "
                f"{failure.get('centre')} | {failure.get('pin_band')} |"
            )
        if len(pin_failures) > 50:
            lines.append(f"| … | {len(pin_failures) - 50} more | | | |")
    lines.extend(["", "## status_diff vs old scorers", ""])
    if not status_diff:
        lines.append("No mapped outcome changes.")
    else:
        lines.append("| old key | old | new | axis |")
        lines.append("|---|---|---|---|")
        for row in status_diff[:200]:
            lines.append(
                f"| `{row.get('old_key')}` | {row.get('old')} | {row.get('new')} | "
                f"{row.get('axis')} |"
            )
    if unmapped_legacy_keys:
        lines.extend(
            [
                "",
                f"Unmapped legacy keys: {len(unmapped_legacy_keys)}. Old ledgers retained.",
            ]
        )
    else:
        lines.extend(["", "All mapped legacy keys have a v2.1 comparison slot."])
    lines.append("")
    return "\n".join(lines)


def _legacy_bucket(row: Mapping[str, object]) -> str:
    if row.get("score_eligible"):
        return "scored"
    raw_status = str(row.get("status") or row.get("terminal_bucket") or "")
    status = residual_status_token(raw_status)
    if (
        status is ResidualStatus.REFUSED
        or raw_status == "failed-to-run"
        or row.get("authority") == "refused"
    ):
        return "refused"
    if status in {ResidualStatus.MATCH, ResidualStatus.MISMATCH}:
        return "scored"
    return "excluded"


def load_legacy_score_rows(root: Path | None = None) -> list[dict[str, object]]:
    """Old Gibbs / species-rail / vapour-pin rows for status_diff_rows."""

    root = root or REPO_ROOT
    rows: list[dict[str, object]] = []
    for rel in (
        "data/literature/gibbs_battery_residual_ledger.yaml",
        "data/literature/species_rail_differential_ledger.yaml",
    ):
        path = root / rel
        if not path.is_file():
            continue
        doc = load_yaml(path)
        if not isinstance(doc, Mapping):
            continue
        for point in doc.get("points") or []:
            if not isinstance(point, Mapping) or not point.get("key"):
                continue
            rows.append(
                {
                    "key": str(point["key"]),
                    "status": str(point.get("status") or ""),
                    "observation_id": str(point.get("observation_id") or ""),
                }
            )
    vapour_path = root / "data" / "vapour_rail_validation_pins.yaml"
    if vapour_path.is_file():
        vapour = load_yaml(vapour_path)
        if isinstance(vapour, Mapping):
            species_block = vapour.get("species") or {}
            if isinstance(species_block, Mapping):
                for species, body in species_block.items():
                    if not isinstance(body, Mapping):
                        continue
                    validations = body.get("validations") or {}
                    if not isinstance(validations, Mapping):
                        continue
                    for engine, payload in validations.items():
                        if not isinstance(payload, Mapping):
                            continue
                        if payload.get("pinned_residual_dex") is None:
                            continue
                        rows.append(
                            {
                                "key": f"vapour_rail_validation_pins::{species}::{engine}",
                                "status": ResidualStatus.MATCH.value,
                            }
                        )
    return rows


def status_diff_rows(
    *,
    old_rows: Sequence[Mapping[str, object]],
    new_rows: Iterable[Mapping[str, object]],
    key_map: Mapping[str, str],
) -> tuple[list[dict[str, object]], list[str]]:
    """Category-by-category diff: scored/refused/excluded and match/mismatch.

    Every change names the schema axis/gate. Unmapped old keys are returned
    so the caller can keep the old ledger.
    """

    wanted_keys = set(key_map.values())
    new_by_key: dict[str, Mapping[str, object]] = {}
    for row in new_rows:
        key = str(row.get("key") or "")
        if key in wanted_keys:
            new_by_key[key] = row
    mapped_old: set[str] = set()
    diffs: list[dict[str, object]] = []
    unmapped: list[str] = []
    for old in old_rows:
        old_key = str(old.get("key") or old.get("observation_id") or "")
        if not old_key:
            continue
        new_key = key_map.get(old_key)
        if not new_key or new_key not in new_by_key:
            unmapped.append(old_key)
            continue
        mapped_old.add(old_key)
        new = new_by_key[new_key]
        old_bucket = _legacy_bucket(old)
        new_bucket = str(new.get("terminal_bucket") or _legacy_bucket(new))
        old_mm = str(old.get("status") or "")
        new_mm = str(new.get("status") or "")
        if old_bucket != new_bucket:
            diffs.append(
                {
                    "old_key": old_key,
                    "old": old_bucket,
                    "new": new_bucket,
                    "axis": "score_eligible/admission/evidence/gate",
                }
            )
        elif old_mm in {"match", "mismatch"} and new_mm in {"match", "mismatch"} and old_mm != new_mm:
            diffs.append(
                {
                    "old_key": old_key,
                    "old": old_mm,
                    "new": new_mm,
                    "axis": "decision_band",
                }
            )
    return diffs, unmapped
