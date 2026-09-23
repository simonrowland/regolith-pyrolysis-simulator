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
import re
import socket
import subprocess
import time
import warnings
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from simulator.battery.enums import (
    QUANTITY_UNITS,
    AdmissionStatus,
    Authority,
    CONDENSED_PHASES,
    Engine,
    EvidenceClass,
    ExecutionState,
    FORMATION_QUANTITIES,
    KINETIC_YIELD_QUANTITIES,
    MEASURED_EVIDENCE,
    MELT_ACTIVITY_QUANTITIES,
    MetricOperation,
    NoticeKind,
    Phase,
    PURE_STANDARD_THERMO,
    Quantity,
    Rail,
    RefusalReason,
    ResidualStatus,
    SourceRelation,
    VAPORIZATION_ENTHALPIES,
    ValueKind,
)
from simulator.battery.identity import (
    Identity,
    IdentityEqualKind,
    identity_equal,
    quantity_token,
)
from simulator.battery.migrate import (
    REPO_ROOT,
    canonicalize_rail,
    load_migrated_store,
    load_yaml,
    to_plain,
)
from simulator.battery.records import (
    CandidateRequest,
    Composition,
    DecisionBand,
    EngineTrace,
    Evidence,
    Execution,
    Experiment,
    Notice,
    Observation,
    Residual,
    ResidualNumeric,
    ResidualRefusal,
    State,
    Uncertainty,
    Value,
    Work,
    as_decimal,
    union_notices,
)
from simulator.battery.validate import (
    _observation_lineage,
    _pressure_blocking_notices,
    _resolve_coefficient_sources,
    _table_ids,
    _VAPOUR_EQUILIBRIUM,
)
from simulator.battery.validity import GateOutcome, run_validity_gates
from simulator.reference_data.janaf import formula_composition

# Explicit closed set. IMCC is first-class. Do not build this from
# resolve_backend, BATTERY_ENGINE_NAMES, or any probe of installed backends.
SCORE_ENGINE_SET: tuple[Engine, ...] = (
    Engine.VAPOROCK,
    Engine.ALPHAMELTS,
    Engine.THERMOENGINE,
    Engine.MAGEMIN,
    Engine.IMCC_SF04,
    Engine.IMCC_SF04_EXT,
    Engine.INTERNAL_ANALYTICAL,
)
MELTS_ENGINES: frozenset[Engine] = frozenset(
    {Engine.ALPHAMELTS, Engine.THERMOENGINE, Engine.MAGEMIN}
)
IMCC_ENGINES: frozenset[Engine] = frozenset(
    {Engine.IMCC_SF04, Engine.IMCC_SF04_EXT}
)
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
}

# Sourced decision bands (not pins). Gibbs agreement_bands_kJ_mol from the
# residual ledger headers. Missing band → decision_rule_missing refusal.
# Searched and not used as decision bands (do not invent a tolerance):
# - data/vapour_rail_validation_pins.yaml policy.margin_dex 0.01 is a pin /
#   regression envelope; SCHEMA-PROPOSAL-v2.1 forbids using a pin as the
#   agreement threshold.
# - validation-data/vapour_rail_sf04_high_t_DECISION.md 0.5 dex is an SF04
#   high-T species-disagreement check, "NOT evidence for a physical or
#   numerical ceiling".
# - docs/imcc-sf04-spec.md ±0.01 dex vs JANAF is an IMCC own-input pin.
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
        "gibbs_battery_residual_ledger.yaml agreement_bands_kJ_mol.independent_tabulation",
    ),
    SourceRelation.SAME_INPUT: DecisionBand(
        Decimal("0.05"),
        "kJ_per_declared_mol_basis",
        "gibbs_battery_residual_ledger.yaml agreement_bands_kJ_mol.engine_own_input",
    ),
    SourceRelation.TRAINING: DecisionBand(
        Decimal("0.05"),
        "kJ_per_declared_mol_basis",
        "gibbs_battery_residual_ledger.yaml agreement_bands_kJ_mol.engine_own_input",
    ),
}

ENGINE_CHANNELS: dict[Engine, str] = {
    Engine.VAPOROCK: "vaporock",
    Engine.ALPHAMELTS: "alphamelts",
    Engine.THERMOENGINE: "thermoengine",
    Engine.MAGEMIN: "magemin",
    Engine.IMCC_SF04: "imcc_sf04",
    Engine.IMCC_SF04_EXT: "imcc_sf04_ext",
    Engine.INTERNAL_ANALYTICAL: "internal-analytical",
}
ENGINE_COEFFICIENT_SOURCES: dict[Engine, tuple[str, ...]] = {
    Engine.VAPOROCK: ("vaporock",),
    Engine.ALPHAMELTS: ("alphamelts",),
    Engine.THERMOENGINE: ("thermoengine",),
    Engine.MAGEMIN: ("magemin",),
    Engine.IMCC_SF04: ("imcc-sf04-v1.0.2",),
    Engine.IMCC_SF04_EXT: ("imcc-sf04-ext-v4",),
    Engine.INTERNAL_ANALYTICAL: ("antoine_sidecar", "ellingham"),
}
# Engine-facing aliases → work/source/table identities in the v2.1 store.
# Unmapped aliases (MELTS calibration DBs) stay incomplete; do not invent
# a work. VapoRock/Ellingham consume JANAF; Antoine sidecar is NIST WebBook;
# IMCC consumes the SF04 magma companion workbook.
COEFFICIENT_SOURCE_STORE_IDS: dict[str, tuple[str, ...]] = {
    "vaporock": ("janaf-4th",),
    "antoine_sidecar": ("nist-webbook",),
    "ellingham": ("janaf-4th",),
    "imcc-sf04-v1.0.2": ("sf04-magma-companion-workbook",),
    "imcc-sf04-ext-v4": ("sf04-magma-companion-workbook",),
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
)


@dataclass(frozen=True)
class EnginePrediction:
    engine: Engine
    channel: str
    execution: Execution
    value: Decimal | None = None
    unit: str | None = None
    authority: Authority | None = None
    notices: tuple[Notice, ...] = ()
    coefficient_sources: tuple[str, ...] = ()
    lineage_complete: bool = False
    certified_band: Mapping[str, tuple[Decimal, Decimal]] | None = None
    requested_composition: State[Composition] | None = None
    refusal_reason: RefusalReason | None = None
    refusal_detail: Mapping[str, object] = field(default_factory=dict)
    identity: Identity | None = None


@dataclass(frozen=True)
class ScoreContext:
    works: Mapping[str, Work]
    experiments: Mapping[str, Experiment]
    observations: Mapping[str, Observation]
    origins: Mapping[str, str] = field(default_factory=dict)
    extract_review: Mapping[str, str | None] = field(default_factory=dict)
    hostname: str = ""


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

    def as_mapping(self) -> dict[str, bool]:
        return {
            "status_match_or_mismatch": self.status in {ResidualStatus.MATCH, ResidualStatus.MISMATCH},
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
        }

    def eligible(self) -> bool:
        return all(self.as_mapping().values())

    def exclusions(self) -> tuple[str, ...]:
        return tuple(name for name, ok in self.as_mapping().items() if not ok)


def parse_engine(name: str) -> Engine:
    return Engine(str(name).strip())


def engines_from_names(names: Sequence[str] | None) -> tuple[Engine, ...]:
    if not names:
        return SCORE_ENGINE_SET
    out: list[Engine] = []
    seen: set[Engine] = set()
    for raw in names:
        engine = parse_engine(raw)
        if engine not in SCORE_ENGINE_SET:
            raise ValueError(
                f"engine {engine.value!r} is not in the explicit SCORE_ENGINE_SET"
            )
        if engine not in seen:
            seen.add(engine)
            out.append(engine)
    return tuple(out)


def parse_species_formula(formula: str) -> tuple[tuple[str, float], ...] | None:
    """Closed element-symbol parse. None → unmatched, never string equality."""

    if not formula or formula.strip().lower() in {"unknown", "none", "n/a"}:
        return None
    return formula_composition(formula)


def match_reported_species(
    formula: str, reported: Mapping[str, float]
) -> tuple[str, float] | None:
    target = parse_species_formula(formula)
    if target is None:
        return None
    for name, value in reported.items():
        parsed = parse_species_formula(str(name))
        if parsed == target:
            return str(name), float(value)
    return None


def rail_for_quantity(quantity: Quantity | None, *, species_formula: str = "") -> Rail:
    if quantity in _VAPOUR_EQUILIBRIUM:
        if species_formula in {"SiO", "SiO2"}:
            return Rail.SIO_EVOLUTION
        return Rail.VAPOUR
    if quantity in MELT_ACTIVITY_QUANTITIES:
        return Rail.MELT_ACTIVITY
    if quantity in FORMATION_QUANTITIES or quantity in PURE_STANDARD_THERMO or quantity in VAPORIZATION_ENTHALPIES:
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
        if species_formula in {"SiO", "SiO2"}:
            return Rail.SIO_EVOLUTION
        if species_formula in {"Na", "K", "NaO0.5", "KO0.5", "Na2O", "K2O"}:
            return Rail.ALKALI_SHUTTLE
        return Rail.SIO_EVOLUTION
    if quantity is Quantity.TRANSITION_TEMPERATURE:
        return Rail.THERMOCHEMISTRY
    return Rail.VAPOUR


def residual_key(
    *,
    reference_id: str,
    quantity: Quantity | None,
    engine: Engine,
    rail: Rail,
    temperature_K: Decimal | None = None,
) -> str:
    """Stable comparison slot: lineage + quantity + identity T + rail + engine.

    Run/version are excluded (v2.1 pin_key_map).
    """

    q = "quantity_unknown" if quantity is None else quantity.value
    t = "" if temperature_K is None else f":T={_dec_token(temperature_K)}"
    return f"{reference_id}{t}::{q}::{rail.value}::{engine.value}"


def _dec_token(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def metric_operation(quantity: Quantity | None) -> MetricOperation | None:
    if quantity is None:
        return None
    return QUANTITY_METRIC.get(quantity)


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


def is_compilation_source(source_id: str | None, origin: str | None = None) -> bool:
    if origin and origin.startswith("compilations-"):
        return True
    if not source_id:
        return False
    lowered = source_id.lower()
    if lowered.startswith("compilations-"):
        return True
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
) -> tuple[Notice, ...]:
    if quantity in _VAPOUR_EQUILIBRIUM:
        return _pressure_blocking_notices(tuple(notices))
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

    for src in sources:
        if src in _ENGINE_SOURCE_ALIASES and src not in COEFFICIENT_SOURCE_STORE_IDS:
            return False
    expanded = expand_coefficient_sources(sources)
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
        coefficient_sources, observations, works, experiments
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
) -> EligibleConjuncts:
    quantity = quantity_token(reference.identity) if isinstance(reference.identity, Identity) else None
    ref_point = point_magnitude(reference.value)
    cand_point = None if candidate is None else point_magnitude(candidate.value)
    finite_points = ref_point is not None and cand_point is not None
    valid_domain = numeric is not None and numeric.value.is_finite()
    evidence_ok = (
        reference.evidence.class_.is_value
        and reference.evidence.class_.value in MEASURED_EVIDENCE
    )
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
        no_blocking_qualification=not blocking_qualifications(quantity, notices),
        selected_independent_lineage_level=selected_lineage_level(
            reference, comparison_ids
        ),
    )


def decision_band_for(
    quantity: Quantity | None,
    source_relation: SourceRelation,
) -> DecisionBand | None:
    if quantity in FORMATION_QUANTITIES or quantity in PURE_STANDARD_THERMO:
        return THERMOCHEMISTRY_DECISION_BANDS.get(source_relation)
    return None


def populate_numeric(
    *,
    quantity: Quantity,
    candidate: Decimal,
    reference: Decimal,
    source_relation: SourceRelation,
) -> tuple[ResidualNumeric | None, RefusalReason | None, dict[str, object]]:
    operation = metric_operation(quantity)
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
    band = decision_band_for(quantity, source_relation)
    if band is None:
        return None, RefusalReason.DECISION_RULE_MISSING, {
            "reason": f"no_sourced_decision_band:{quantity.value}",
            "quantity": quantity.value,
            "operation": operation.value,
        }
    numeric = ResidualNumeric(
        operation=operation,
        unit=unit,
        value=value,
        decision_band=band,
    )
    return numeric, None, {}


def match_status(numeric: ResidualNumeric) -> ResidualStatus:
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
    return kind.startswith("imcc_") and (
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


def predict_with_engine(
    engine: Engine,
    observation: Observation,
    *,
    handles: Mapping[str, object] | None = None,
    isolated: bool | None = None,
) -> EnginePrediction:
    """Dispatch one engine at the observation Identity. Isolated MELTS cells."""

    from simulator.diagnostic_helpers.binary_pot_battery import (
        ARM_HEADLINE,
        ARM_QUALIFICATION,
        MELTS_FAMILY_ENGINES,
        PO2_COMMANDED,
        PO2_ENGINE_DEFAULT,
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

    wt: dict[str, float] | None = None
    requested: State[Composition] | None = None
    if identity.composition is not None and identity.composition.is_value:
        requested = identity.composition
        assert identity.composition.value is not None
        wt = composition_wt_pct(identity.composition.value)
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
    elif quantity in _VAPOUR_EQUILIBRIUM or quantity in FORMATION_QUANTITIES:
        # Pure-species identity: 100 wt% of the reservoir/species formula.
        reservoir = identity.reservoir
        formula_pot = formula
        if reservoir is not None and reservoir.is_value and reservoir.value is not None:
            formula_pot = reservoir.value.formula
        if parse_species_formula(formula_pot) is None:
            return EnginePrediction(
                engine=engine,
                channel=channel,
                execution=Execution(state=ExecutionState.NOT_PROBED),
                coefficient_sources=sources,
                lineage_complete=False,
                refusal_reason=RefusalReason.IDENTITY_UNKNOWN,
                refusal_detail={"reason": "species_formula_unparsed", "formula": formula_pot},
                identity=identity,
            )
        wt = {formula_pot: 100.0}

    if wt is None:
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

    handle_map = handles or {}
    handle = handle_map.get(engine.value)
    if handle is None:
        handle = open_battery_engine(engine.value)
    name = str(getattr(handle, "name", engine.value))
    available = bool(getattr(handle, "available", False))
    if not available:
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(
                state=ExecutionState.ATTEMPTED_UNAVAILABLE,
                call_evidence=f"open_battery_engine:{name}",
            ),
            coefficient_sources=sources,
            lineage_complete=False,
            refusal_reason=RefusalReason.ATTEMPTED_UNAVAILABLE,
            refusal_detail={
                "reason": REFUSAL_UNAVAILABLE,
                "engine_reason": getattr(handle, "unavailable_reason", None),
            },
            identity=identity,
            requested_composition=requested,
        )

    fo2_state = identity.fO2_Pa
    if fo2_state is not None and fo2_state.is_value and fo2_state.value is not None:
        # fO2_Pa is fugacity. Commanded pO2_bar = fO2_Pa / 1e5 under the
        # stated ideal-gas assumption (1 bar = 100000 Pa exactly).
        po2 = Po2Request(mode=PO2_COMMANDED, po2_bar=float(fo2_state.value) / 1.0e5)
    else:
        po2 = Po2Request(mode=PO2_ENGINE_DEFAULT, po2_bar=None)

    qualification = False
    extra_notices: list[Notice] = []
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
    )
    notices = union_notices(tuple(extra_notices), cell_notices(quantity, engine, cell))
    authority, certified_band = _flags_from_scored_cell(cell, authority, certified_band)
    refusal = getattr(cell, "refusal_reason", None)
    status = getattr(cell, "status", None)
    call_evidence = (
        f"equilibrate_cell:{name}:host={getattr(cell, 'hostname', '')}"
        f":exit={getattr(cell, 'exit_code', None)}"
    )
    if status != "ok":
        typed = str(refusal or status or "unavailable")
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
            )
        if typed == REFUSAL_TIMEOUT:
            exec_state = ExecutionState.ATTEMPTED_UNAVAILABLE
            reason = RefusalReason.ATTEMPTED_UNAVAILABLE
        elif typed == REFUSAL_UNAVAILABLE:
            exec_state = ExecutionState.ATTEMPTED_UNAVAILABLE
            reason = RefusalReason.ATTEMPTED_UNAVAILABLE
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
                "engine_reason": getattr(cell, "engine_reason", None),
            },
            identity=identity,
            requested_composition=requested,
        )

    activities = dict(getattr(cell, "melt_activities", None) or {})
    pressures = dict(getattr(cell, "gas_partial_pressures_Pa", None) or {})
    reported: Mapping[str, float]
    unit = QUANTITY_UNITS[quantity]
    if quantity in MELT_ACTIVITY_QUANTITIES:
        reported = activities
        unit = "dimensionless"
    elif quantity in _VAPOUR_EQUILIBRIUM:
        reported = pressures
        unit = "Pa"
    else:
        reported = {**activities, **pressures}

    matched = match_reported_species(formula, reported)
    if matched is None:
        return EnginePrediction(
            engine=engine,
            channel=channel,
            execution=Execution(state=ExecutionState.PRODUCED, call_evidence=call_evidence),
            authority=authority,
            notices=notices,
            coefficient_sources=sources,
            lineage_complete=False,
            certified_band=certified_band,
            refusal_reason=RefusalReason.UNSUPPORTED,
            refusal_detail={
                "reason": "species_unmatched_in_engine_output",
                "formula": formula,
                "reported": sorted(reported),
            },
            identity=identity,
            requested_composition=requested,
        )
    _name, magnitude = matched
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
        identity=identity,
        requested_composition=requested,
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
            requested_composition=prediction.requested_composition,
        ),
        authority=prediction.authority,
        certified_band=prediction.certified_band,
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
) -> tuple[Residual, Observation | None]:
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
    if experiment is not None:
        gates = run_validity_gates(
            experiment, reference, tables=_gate_tables(reference, context.observations)
        )
    else:
        from simulator.battery.validity import GateCheck

        gates = GateOutcome(
            passed=False,
            reason=RefusalReason.REFERENTIAL_INTEGRITY,
            checks=(GateCheck("experiment", False, {"reason": "missing experiment"}),),
            primary_check="experiment",
        )

    notices = union_notices(reference.notices)
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
        return _refused(
            RefusalReason.IDENTITY_UNKNOWN,
            {"reason": "quantity_unknown"},
            execution=Execution(state=ExecutionState.NOT_PROBED),
            exclusions=("status_match_or_mismatch", "finite_numeric_point_endpoints"),
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
    if not gates.passed:
        return _refused(
            gates.reason or RefusalReason.INVALID_SOURCE,
            {"primary_check": gates.primary_check, "checks": [c.name for c in gates.checks]},
            execution=Execution(state=ExecutionState.NOT_PROBED),
            exclusions=("validity_gates_pass", "status_match_or_mismatch"),
        )

    if prediction is None:
        predictor = predict or predict_with_engine
        prediction = predictor(engine, reference, handles=handles)

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
    source_relation = resolve_source_relation(
        reference,
        prediction.coefficient_sources,
        prediction.lineage_complete,
        works=context.works,
        observations=context.observations,
        experiments=context.experiments,
    )
    if is_internal_consistency(origin) or is_compilation_source(reference.source_id, origin):
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

    if prediction.execution.state is not ExecutionState.PRODUCED or prediction.value is None:
        reason = prediction.refusal_reason or RefusalReason.UNSUPPORTED
        return _refused(
            reason,
            prediction.refusal_detail or {"reason": prediction.execution.state.value},
            execution=prediction.execution,
            extra_notices=prediction.notices,
            source_relation=source_relation,
        )

    candidate = candidate_observation(reference, prediction)
    equal = identity_equal(reference.identity, candidate.identity)
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
    numeric, metric_reason, metric_detail = populate_numeric(
        quantity=quantity,
        candidate=prediction.value,
        reference=ref_point,
        source_relation=source_relation,
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
    status = match_status(numeric)
    conjuncts = build_conjuncts(
        status=status,
        reference=reference,
        candidate=candidate,
        numeric=numeric,
        source_relation=source_relation,
        lineage_complete=prediction.lineage_complete,
        gates=gates,
        extract_review_status=review_status,
        comparison_ids=comparison_ids,
        notices=notices,
    )
    if is_internal_consistency(origin) or is_compilation_source(reference.source_id, origin):
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
) -> tuple[dict, ...]:
    from simulator.battery.validate import _table_payloads

    return _table_payloads(reference, observations)


def load_score_context(root: Path | None = None) -> ScoreContext:
    root = root or REPO_ROOT
    works, experiments, observations = load_migrated_store(root)
    origins: dict[str, str] = {}
    extract_review: dict[str, str | None] = {}
    literature = root / "data" / "literature"
    for directory in (literature / "extracts-v2", literature / "observations-v2"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            doc = load_yaml(path)
            if not isinstance(doc, Mapping):
                continue
            for raw in doc.get("observations") or []:
                if isinstance(raw, Mapping) and raw.get("observation_id"):
                    origins[str(raw["observation_id"])] = path.name
    extracts = literature / "extracts"
    if extracts.is_dir():
        for path in sorted(extracts.glob("*.yaml")):
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
    )


def comparison_candidates(context: ScoreContext) -> tuple[Observation, ...]:
    """Measured evidence, admitted or pending (pending is diagnostic)."""

    out: list[Observation] = []
    for obs in context.observations.values():
        ev = obs.evidence.class_
        if not (ev.is_value and ev.value in MEASURED_EVIDENCE):
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
    engine_set = tuple(engines) if engines is not None else SCORE_ENGINE_SET
    refs = list(comparison_candidates(context))
    if include_diagnostics:
        seen = {o.observation_id for o in refs}
        for obs in diagnostic_references(context):
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
    comparison_ids = {o.observation_id for o in comparison_candidates(context)}
    residuals: list[Residual] = []
    candidates: dict[str, Observation] = {}
    observations = dict(context.observations)
    live_context = replace(context, observations=observations)
    empirical_ids = comparison_ids
    started = time.monotonic()
    last_progress = started
    done = 0
    total = len(refs) * max(len(engine_set), 1)
    for obs in refs:
        origin = context.origins.get(obs.observation_id)
        diagnostic = (
            obs.observation_id not in empirical_ids
            or is_internal_consistency(origin)
            or is_compilation_source(obs.source_id, origin)
            or is_sf04_workbook(obs)
        )
        for engine in engine_set:
            prediction = None
            if diagnostic and predict is None:
                prediction = EnginePrediction(
                    engine=engine,
                    channel=ENGINE_CHANNELS[engine],
                    execution=Execution(state=ExecutionState.NOT_PROBED),
                    coefficient_sources=ENGINE_COEFFICIENT_SOURCES[engine],
                    lineage_complete=False,
                    refusal_reason=RefusalReason.UNSUPPORTED,
                    refusal_detail={"reason": "diagnostic_population"},
                    identity=obs.identity if isinstance(obs.identity, Identity) else None,
                )
            residual, candidate = compile_residual(
                obs,
                engine,
                context=live_context,
                prediction=prediction,
                comparison_ids=comparison_ids,
                predict=predict,
                handles=handles,
            )
            residuals.append(residual)
            if candidate is not None:
                candidates[candidate.observation_id] = candidate
                observations[candidate.observation_id] = candidate
            done += 1
            now = time.monotonic()
            if now - last_progress >= 60:
                print(
                    f"score progress {done}/{total} residuals "
                    f"{int(now - started)}s host={context.hostname}",
                    flush=True,
                )
                last_progress = now
    residuals.sort(key=lambda r: (r.rail.value, r.reference, r.key))
    return tuple(residuals), candidates


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

    The only writer is write_residuals_jsonl, which emits the stamp with
    the residual body produced in the same scoring run. This named path
    exists so a prepend onto an already-written body cannot recur.
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
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if is_store_stamp_payload(payload):
            return dict(payload)
        return None
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


def _median_abs(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(abs(v) for v in values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal(2)


def headline_rows(
    residuals: Sequence[Residual],
    *,
    context: ScoreContext | None = None,
) -> list[dict[str, object]]:
    """Per rail × engine headline. Empirical score_eligible only."""

    groups: dict[tuple[str, str], list[Residual]] = {}
    engines_seen: set[str] = set()
    for residual in residuals:
        engine = _engine_of(residual)
        engines_seen.add(engine)
        groups.setdefault((residual.rail.value, engine), []).append(residual)
    for rail in Rail:
        for engine in sorted(engines_seen) or [e.value for e in SCORE_ENGINE_SET]:
            groups.setdefault((rail.value, engine), [])
    rows: list[dict[str, object]] = []
    for (rail, engine), bucket in sorted(groups.items()):
        scored = [r for r in bucket if r.score_eligible]
        matches = [r for r in scored if r.status is ResidualStatus.MATCH]
        dex_values = [
            r.numeric.value
            for r in scored
            if r.numeric is not None and r.numeric.operation is MetricOperation.DEX
        ]
        n_scored = len(scored)
        match_rate: str | float | None
        if n_scored == 0:
            match_rate = None
        else:
            match_rate = len(matches) / n_scored
        median = _median_abs(dex_values)
        rows.append(
            {
                "rail": rail,
                "engine": engine,
                "n_candidates": len(bucket),
                "n_refused": sum(1 for r in bucket if r.status is ResidualStatus.REFUSED),
                "n_scored": n_scored,
                "n_match": len(matches),
                "match_rate": match_rate,
                "median_abs_dex": None if median is None else str(median),
                "n_eligible_references": sum(
                    1
                    for r in bucket
                    if r.score_eligible or "reference_measured_evidence" not in r.exclusions
                ),
            }
        )
    return rows


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


def admission_census_payloads(rows: Sequence[Mapping[str, object]]) -> dict[str, int]:
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
    stamp = derive_store_stamp(root or REPO_ROOT)
    lines: list[str] = [
        "# Battery score report (schema v2.1)",
        "",
        "Generated only. Pins are an independent baseline and are never",
        "re-centred from these residuals. Refusals are diagnostics, never hidden.",
        "Headline accuracy per rail is the product of score_eligible rows;",
        "a rail with zero eligible references is reported as zero.",
        "",
        f"Hostname: `{context.hostname}`.",
    ]
    if studio_hostname:
        lines.append(f"Studio hostname: `{studio_hostname}`.")
    lines.extend(["", *format_store_stamp_report_lines(stamp)])
    lines.extend(
        [
            "",
            f"Engines: {', '.join(e.value for e in engines)}.",
            "",
            "## Per rail × engine headline",
            "",
            "| rail | engine | n candidates | n refused | n scored | match rate | median abs dex |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in headline_rows(residuals, context=context):
        rate = row["match_rate"]
        rate_s = "—" if rate is None else f"{rate:.3f}"
        med = row["median_abs_dex"] or "—"
        lines.append(
            f"| {row['rail']} | {row['engine']} | {row['n_candidates']} | "
            f"{row['n_refused']} | {row['n_scored']} | {rate_s} | {med} |"
        )
    lines.extend(["", "## Refusal census", "", "| reason | n |", "|---|---:|"])
    census = refusal_census(residuals)
    if not census:
        lines.append("| (none) | 0 |")
    else:
        for reason, n in census.items():
            lines.append(f"| `{reason}` | {n} |")
    admit = admission_census(residuals, context=context)
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
            f"| comparison candidates pending | {admit['comparison_candidates_pending']} |",
            f"| comparison candidates admitted | {admit['comparison_candidates_admitted']} |",
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
    backlog = notice_backlog(residuals)
    if not backlog:
        lines.append("| (none) | 0 |")
    else:
        for kind, n in backlog.items():
            lines.append(f"| `{kind}` | {n} |")
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
        ]
    )
    n_diag = sum(
        1
        for r in residuals
        if "reference_measured_evidence" in r.exclusions
        or is_internal_consistency(context.origins.get(r.reference))
        or is_compilation_source(
            context.observations[r.reference].source_id
            if r.reference in context.observations
            else None,
            context.origins.get(r.reference),
        )
    )
    lines.append(f"Diagnostic residuals in this file: {n_diag}.")
    lines.extend(["", "## Pin failures", ""])
    if not pin_failures:
        lines.append("None.")
    else:
        lines.append(f"{len(pin_failures)} pin failures (coverage or outside pin_band). A live residual outside its pin_band is a failure, never a re-centre.")
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
        for row in status_diff:
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


def refusal_census_payloads(rows: Sequence[Mapping[str, object]]) -> dict[str, int]:
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


def headline_payloads(
    rows: Sequence[Mapping[str, object]],
    engines: Sequence[Engine],
) -> list[dict[str, object]]:
    groups: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    engine_names = [e.value for e in engines]
    for rail in Rail:
        for engine in engine_names:
            groups[(rail.value, engine)] = []
    for row in rows:
        rail = str(row.get("rail") or "")
        engine = str(
            ((row.get("candidate_request") or {}) if isinstance(row.get("candidate_request"), Mapping) else {}).get("engine")
            or str(row.get("key") or "").rsplit("::", 1)[-1]
        )
        groups.setdefault((rail, engine), []).append(row)
    out: list[dict[str, object]] = []
    for (rail, engine), bucket in sorted(groups.items()):
        scored = [r for r in bucket if r.get("score_eligible")]
        matches = [r for r in scored if r.get("status") == ResidualStatus.MATCH.value]
        dex_values = []
        for r in scored:
            numeric = r.get("numeric") if isinstance(r.get("numeric"), Mapping) else None
            if numeric and numeric.get("operation") == MetricOperation.DEX.value:
                try:
                    dex_values.append(as_decimal(numeric.get("value")))
                except (TypeError, ValueError, ArithmeticError):
                    pass
        n_scored = len(scored)
        out.append(
            {
                "rail": rail,
                "engine": engine,
                "n_candidates": len(bucket),
                "n_refused": sum(1 for r in bucket if r.get("status") == ResidualStatus.REFUSED.value),
                "n_scored": n_scored,
                "n_match": len(matches),
                "match_rate": None if n_scored == 0 else len(matches) / n_scored,
                "median_abs_dex": None if not dex_values else str(_median_abs(dex_values)),
            }
        )
    return out


def notice_backlog_payloads(rows: Sequence[Mapping[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for notice in row.get("notices") or ():
            if isinstance(notice, Mapping) and notice.get("kind"):
                kind = str(notice["kind"])
                counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def render_score_report_from_payloads(
    rows: Sequence[Mapping[str, object]],
    *,
    engines: Sequence[Engine],
    hostname: str,
    pin_failures: Sequence[Mapping[str, object]] = (),
    status_diff: Sequence[Mapping[str, object]] = (),
    unmapped_legacy_keys: Sequence[str] = (),
    studio_hostname: str | None = None,
    store_stamp: Mapping[str, object] | None = None,
    mismatch_warning: str | None = None,
) -> str:
    lines: list[str] = [
        "# Battery score report (schema v2.1)",
        "",
        "Generated only. Pins are an independent baseline and are never",
        "re-centred from these residuals. Refusals are diagnostics, never hidden.",
        "Headline accuracy per rail is the product of score_eligible rows;",
        "a rail with zero eligible references is reported as zero.",
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
            "## Per rail × engine headline",
            "",
            "| rail | engine | n candidates | n refused | n scored | match rate | median abs dex |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in headline_payloads(rows, engines):
        rate = row["match_rate"]
        rate_s = "—" if rate is None else f"{rate:.3f}"
        med = row["median_abs_dex"] or "—"
        lines.append(
            f"| {row['rail']} | {row['engine']} | {row['n_candidates']} | "
            f"{row['n_refused']} | {row['n_scored']} | {rate_s} | {med} |"
        )
    lines.extend(["", "## Refusal census", "", "| reason | n |", "|---|---:|"])
    census = refusal_census_payloads(rows)
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
    status = str(row.get("status") or row.get("terminal_bucket") or "")
    if status in {"refused", "typed-refusal", "failed-to-run"} or row.get("authority") == "refused":
        return "refused"
    if status in {"match", "mismatch"}:
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
    new_rows: Sequence[Mapping[str, object]],
    key_map: Mapping[str, str],
) -> tuple[list[dict[str, object]], list[str]]:
    """Category-by-category diff: scored/refused/excluded and match/mismatch.

    Every change names the schema axis/gate. Unmapped old keys are returned
    so the caller can keep the old ledger.
    """

    new_by_key = {str(row.get("key") or ""): row for row in new_rows}
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
