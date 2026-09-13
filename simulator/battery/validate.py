"""Referential integrity and conditional-field rules (v2.1).

Order: this validator, then validity gates (``run_validity_gates`` on
each Residual's reference), then identity_equal, then metric/score
policy (chunk 2). A failed gate requires Residual.status=refused, no
numeric, score_eligible=false. Missing required identity axes become
``unknown`` for archival storage; they never equal another hole.

Ambiguity resolutions:
- ``derived_from`` / ``experiment_id`` / ``work_id`` must resolve inside the
  supplied record set. Ancestry must be acyclic.
- Conditional C() fields: literature requires work_id, locator, source_id,
  read_from; synthetic requires simulated_experiment_id; derived requires
  derived_from + derivation; prediction requires engine + authority;
  fallback notices require from/to; domain notices require band;
  projection notices require dropped; crystal species require polymorph
  (value or unknown, never guessed unspecified).
- Residual: numeric iff match/mismatch; refusal iff refused; candidate
  required when execution is produced; candidate_request required when no
  candidate; attempted_unavailable requires call_evidence.
  ``score_eligible`` cannot be true when status is refused, admission is
  not admitted, evidence is not measured_*, identity_equal is not
  equal, or a vapour-equilibrium residual carries a blocking pressure
  qualification (floor_inversion / fallback / pressure_provenance_unknown
  on p_sat, p_partial, or p_reference). Diagnostic numeric residuals with
  those notices remain valid at score_eligible=False (M02). Full scoring
  policy (lineage independence, live clamps) is chunk 2; this is the
  schema-level floor.
- Compilation pairs with legitimate ``not_applicable`` axes are valid
  records. The validator must not refuse them.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Iterable, Mapping, Sequence

from simulator.battery.enums import (
    AdmissionStatus,
    Authority,
    EvidenceClass,
    ExecutionState,
    ExperimentKind,
    FO2Channel,
    IdentityEqualKind,
    MEASURED_EVIDENCE,
    NoticeKind,
    Phase,
    Quantity,
    RefusalReason,
    ResidualStatus,
    SourceRelation,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.identity import (
    Identity,
    identity_equal,
    profile_for,
    validate_quantity_profile,
)
from simulator.battery.records import (
    Experiment,
    Located,
    Notice,
    Observation,
    Reaction,
    Residual,
    Species,
    Work,
    as_decimal,
    union_notices,
)
from simulator.battery.validity import run_validity_gates
from simulator.reference_data.janaf import formula_composition


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    reason: RefusalReason
    detail: str


_ADVISORY_REASONS = frozenset({RefusalReason.IDENTITY_INCOMPLETE})

# v2.1 vapour-equilibrium blocking qualifications. Sibling activity is
# not blocked by a pressure-only floor (quantity-scoped affected_quantities).
_VAPOUR_EQUILIBRIUM = frozenset(
    {Quantity.P_SAT, Quantity.P_PARTIAL, Quantity.P_REFERENCE}
)
_PRESSURE_BLOCKING_NOTICES = frozenset(
    {
        NoticeKind.FLOOR_INVERSION,
        NoticeKind.FALLBACK,
        NoticeKind.PRESSURE_PROVENANCE_UNKNOWN,
    }
)
_CERTIFICATION_DOWNGRADE_NOTICES = frozenset(
    {NoticeKind.OUT_OF_GAMMA_DOMAIN, NoticeKind.OUT_OF_CERTIFIED_BAND}
)


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]

    @property
    def ok(self) -> bool:
        return not any(i.reason not in _ADVISORY_REASONS for i in self.issues)

    @property
    def hard_issues(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.reason not in _ADVISORY_REASONS)


def _issue(path: str, reason: RefusalReason, detail: str) -> ValidationIssue:
    return ValidationIssue(path, reason, detail)


def _check_located(path: str, located: Located[Any], issues: list[ValidationIssue]) -> None:
    if located.state.is_value and located.locator is None:
        issues.append(
            _issue(
                path,
                RefusalReason.CONDITIONAL_FIELD,
                "empirical Located value requires a locator",
            )
        )


def _located_decimal_value(located: Located[Any] | None) -> Any:
    if located is None or not located.state.is_value or located.state.value is None:
        return None
    try:
        return as_decimal(located.state.value)
    except TypeError:
        return located.state.value


def _reconcile_identity_axis(
    path: str,
    identity_state: Any,
    experiment_located: Located[Any] | None,
    point_located: Located[Any] | None,
    issues: list[ValidationIssue],
) -> None:
    if identity_state is None or not identity_state.is_value or identity_state.value is None:
        return
    source = point_located if point_located is not None else experiment_located
    source_value = _located_decimal_value(source)
    if source_value is None:
        return
    try:
        ident_value = as_decimal(identity_state.value)
    except TypeError:
        ident_value = identity_state.value
    if ident_value != source_value:
        issues.append(
            _issue(
                path,
                RefusalReason.INVALID_IDENTITY,
                "identity disagrees with Experiment conditions / point_conditions",
            )
        )


def _walk_located(obj: object, path: str, issues: list[ValidationIssue], seen: set[int] | None = None) -> None:
    if obj is None:
        return
    if seen is None:
        seen = set()
    marker = id(obj)
    if marker in seen:
        return
    seen.add(marker)
    if isinstance(obj, Located):
        _check_located(path, obj, issues)
        if obj.inference is not None:
            _walk_located(obj.inference, f"{path}.inference", issues, seen)
        return
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            _walk_located(value, f"{path}.{key}", issues, seen)
        return
    if isinstance(obj, (tuple, list)):
        for i, value in enumerate(obj):
            _walk_located(value, f"{path}[{i}]", issues, seen)
        return
    if is_dataclass(obj) and not isinstance(obj, type):
        for field in fields(obj):
            _walk_located(getattr(obj, field.name), f"{path}.{field.name}", issues, seen)


def _table_payload(
    reference: Observation,
    observations: Mapping[str, Observation],
) -> dict | None:
    """Paired ΔfG / log10_Kf printed values at the same identity point."""

    ident = reference.identity
    if not isinstance(ident, Identity):
        return None
    if ident.quantity not in {Quantity.DELTA_FG, Quantity.LOG10_KF}:
        return None
    want = Quantity.LOG10_KF if ident.quantity is Quantity.DELTA_FG else Quantity.DELTA_FG
    sibling = None
    for other in observations.values():
        if other.observation_id == reference.observation_id:
            continue
        if other.experiment_id != reference.experiment_id:
            continue
        other_ident = other.identity
        if not isinstance(other_ident, Identity) or other_ident.quantity is not want:
            continue
        if other_ident.species.formula != ident.species.formula:
            continue
        if other_ident.species.phase is not ident.species.phase:
            continue
        if ident.temperature_K != other_ident.temperature_K:
            continue
        sibling = other
        break
    if sibling is None:
        return None
    delta_obs = reference if ident.quantity is Quantity.DELTA_FG else sibling
    log_obs = sibling if ident.quantity is Quantity.DELTA_FG else reference
    if delta_obs.value.kind is not ValueKind.POINT or log_obs.value.kind is not ValueKind.POINT:
        return None
    if ident.temperature_K is None or not ident.temperature_K.is_value:
        return None
    return {
        "delta_fG_kJ_mol": delta_obs.value.point,
        "log10_Kf": log_obs.value.point,
        "T_K": ident.temperature_K.value,
        "per": None if ident.per is None or not ident.per.is_value else ident.per.value,
        "standard_pressure_Pa": None
        if ident.standard_pressure_Pa is None or not ident.standard_pressure_Pa.is_value
        else ident.standard_pressure_Pa.value,
    }


def _pressure_blocking_notices(*groups: tuple[Notice, ...] | None) -> tuple[Notice, ...]:
    found: list[Notice] = []
    for group in groups:
        if not group:
            continue
        for notice in group:
            if notice.kind not in _PRESSURE_BLOCKING_NOTICES:
                continue
            if any(q in _VAPOUR_EQUILIBRIUM for q in notice.affected_quantities):
                found.append(notice)
    return tuple(found)


def reaction_atom_balance(reaction: Reaction) -> dict[str, float]:
    """Net element counts. Empty dict means balanced (within 1e-12)."""

    net: dict[str, float] = {}
    for term in reaction.terms:
        parsed = formula_composition(term.species.formula)
        if parsed is None:
            net[f"?{term.species.formula}"] = net.get(f"?{term.species.formula}", 0.0) + float(
                term.coefficient
            )
            continue
        coeff = float(term.coefficient)
        for element, count in parsed:
            net[element] = net.get(element, 0.0) + coeff * float(count)
    return {el: n for el, n in net.items() if abs(n) > 1e-12}


def _check_species(path: str, species: Species, issues: list[ValidationIssue]) -> None:
    if species.phase is Phase.CR:
        if species.polymorph is None:
            issues.append(
                _issue(
                    f"{path}.polymorph",
                    RefusalReason.POLYMORPH_UNRESOLVED,
                    "crystal phase requires a polymorph State (value or unknown); unspecified is invalid",
                )
            )
        elif species.polymorph.is_not_applicable:
            issues.append(
                _issue(
                    f"{path}.polymorph",
                    RefusalReason.POLYMORPH_UNRESOLVED,
                    "crystal polymorph cannot be not_applicable",
                )
            )
    elif species.polymorph is not None and species.polymorph.is_value:
        issues.append(
            _issue(
                f"{path}.polymorph",
                RefusalReason.INAPPLICABLE_AXIS,
                "non-crystal species cannot carry a polymorph value",
            )
        )


def _check_identity(path: str, identity: Identity, issues: list[ValidationIssue]) -> None:
    _check_species(f"{path}.species", identity.species, issues)
    profile_outcome = validate_quantity_profile(identity)
    if profile_outcome.kind is IdentityEqualKind.INVALID_IDENTITY:
        issues.append(
            _issue(
                path,
                RefusalReason.INVALID_IDENTITY,
                profile_outcome.detail or ",".join(profile_outcome.fields),
            )
        )
    profile = profile_for(identity)
    for name in profile.required:
        state = getattr(identity, name)
        if state is None:
            # archival: missing required axis is unknown, not a hard refuse of
            # the record. Completeness is reported so equality will be unknown.
            issues.append(
                _issue(
                    f"{path}.{name}",
                    RefusalReason.IDENTITY_INCOMPLETE,
                    f"required axis {name} absent; stored as unknown for archival, never equals another hole",
                )
            )
        elif state.is_unknown:
            issues.append(
                _issue(
                    f"{path}.{name}",
                    RefusalReason.IDENTITY_INCOMPLETE,
                    f"required axis {name} is unknown",
                )
            )
    if identity.reaction is not None and identity.reaction.is_value and identity.reaction.value is not None:
        residual = reaction_atom_balance(identity.reaction.value)
        if residual:
            issues.append(
                _issue(
                    f"{path}.reaction",
                    RefusalReason.REACTION_UNBALANCED,
                    f"atom balance residual {residual}",
                )
            )
        for i, term in enumerate(identity.reaction.value.terms):
            _check_species(f"{path}.reaction.terms[{i}].species", term.species, issues)
    if (
        identity.formation_elements is not None
        and identity.formation_elements.is_value
        and identity.formation_elements.value is not None
    ):
        for element, species in identity.formation_elements.value:
            _check_species(f"{path}.formation_elements.{element}", species, issues)
    if (
        identity.formation_elements is not None
        and identity.formation_elements.is_value
        and identity.formation_elements.value is not None
        and not identity.formation_elements.value
    ):
        issues.append(
            _issue(
                f"{path}.formation_elements",
                RefusalReason.INVALID_IDENTITY,
                "formation_elements value cannot be empty",
            )
        )
    if (
        identity.reference_state is not None
        and identity.reference_state.is_value
        and identity.reference_state.value is not None
    ):
        _check_species(
            f"{path}.reference_state.endmember",
            identity.reference_state.value.endmember,
            issues,
        )
    if identity.reservoir is not None and identity.reservoir.is_value and identity.reservoir.value is not None:
        _check_species(f"{path}.reservoir", identity.reservoir.value, issues)
        if identity.quantity is Quantity.P_SAT:
            src = identity.reservoir.value
            if src.formula != identity.species.formula:
                issues.append(
                    _issue(
                        f"{path}.reservoir",
                        RefusalReason.RESERVOIR_RULE,
                        "p_sat reservoir formula must match the gas species",
                    )
                )


def _check_notice(path: str, notice: Notice, issues: list[ValidationIssue]) -> None:
    if notice.kind is NoticeKind.FALLBACK:
        if not notice.source or not notice.destination:
            issues.append(
                _issue(
                    path,
                    RefusalReason.CONDITIONAL_FIELD,
                    "fallback notice requires from/to",
                )
            )
    if notice.kind in {NoticeKind.DOMAIN, NoticeKind.OUT_OF_CERTIFIED_BAND, NoticeKind.OUT_OF_GAMMA_DOMAIN}:
        if not notice.band:
            issues.append(
                _issue(path, RefusalReason.CONDITIONAL_FIELD, "domain notice requires band")
            )
    if notice.kind in {NoticeKind.PROJECTION, NoticeKind.COMPOSITION_PROJECTED}:
        if not notice.dropped:
            issues.append(
                _issue(
                    path,
                    RefusalReason.CONDITIONAL_FIELD,
                    "projection notice requires dropped components",
                )
            )


def validate_work(work: Work, path: str = "work") -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not work.work_id:
        issues.append(_issue(f"{path}.work_id", RefusalReason.CONDITIONAL_FIELD, "missing work_id"))
    return issues


def validate_experiment(
    experiment: Experiment,
    works: Mapping[str, Work],
    experiments: Mapping[str, Experiment],
    path: str = "experiment",
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if experiment.kind is ExperimentKind.LITERATURE:
        if not experiment.work_id:
            issues.append(
                _issue(f"{path}.work_id", RefusalReason.CONDITIONAL_FIELD, "literature requires work_id")
            )
        elif experiment.work_id not in works:
            issues.append(
                _issue(
                    f"{path}.work_id",
                    RefusalReason.REFERENTIAL_INTEGRITY,
                    f"work_id {experiment.work_id!r} does not resolve",
                )
            )
        if experiment.locator is None:
            issues.append(
                _issue(f"{path}.locator", RefusalReason.CONDITIONAL_FIELD, "literature requires locator")
            )
    elif experiment.kind is ExperimentKind.SYNTHETIC:
        if not experiment.simulated_experiment_id:
            issues.append(
                _issue(
                    f"{path}.simulated_experiment_id",
                    RefusalReason.CONDITIONAL_FIELD,
                    "synthetic requires simulated_experiment_id",
                )
            )
        elif experiment.simulated_experiment_id not in experiments:
            issues.append(
                _issue(
                    f"{path}.simulated_experiment_id",
                    RefusalReason.REFERENTIAL_INTEGRITY,
                    f"simulated_experiment_id {experiment.simulated_experiment_id!r} does not resolve",
                )
            )
    sweep = experiment.pressure_environment.sweep_gas.state
    if sweep.is_value and sweep.value is not None and sweep.value.species == "none":
        if sweep.value.flow_sccm.is_value or sweep.value.partial_pressure_Pa.is_value:
            issues.append(
                _issue(
                    f"{path}.pressure_environment.sweep_gas",
                    RefusalReason.INAPPLICABLE_AXIS,
                    "carrier-free sweep cannot carry flow or partial-pressure values",
                )
            )
    if experiment.fO2_control is not None:
        channel = experiment.fO2_control.channel
        if channel.is_value and channel.value is FO2Channel.BUFFER:
            if experiment.fO2_control.buffer is None:
                issues.append(
                    _issue(
                        f"{path}.fO2_control.buffer",
                        RefusalReason.CONDITIONAL_FIELD,
                        "buffer channel requires buffer",
                    )
                )
    _walk_located(experiment, path, issues)
    return issues


def _ancestry_cycles(observations: Mapping[str, Observation]) -> list[tuple[str, ...]]:
    cycles: list[tuple[str, ...]] = []
    visiting: set[str] = set()
    seen: set[str] = set()

    def walk(oid: str, stack: tuple[str, ...]) -> None:
        if oid in visiting:
            cycles.append(stack + (oid,))
            return
        if oid in seen:
            return
        visiting.add(oid)
        obs = observations.get(oid)
        if obs is not None and obs.derived_from:
            for parent in obs.derived_from:
                walk(parent, stack + (oid,))
        visiting.remove(oid)
        seen.add(oid)

    for oid in observations:
        walk(oid, ())
    return cycles


def validate_observation(
    observation: Observation,
    experiments: Mapping[str, Experiment],
    observations: Mapping[str, Observation],
    works: Mapping[str, Work] | None = None,
    path: str = "observation",
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    experiment = experiments.get(observation.experiment_id)
    if experiment is None:
        issues.append(
            _issue(
                f"{path}.experiment_id",
                RefusalReason.REFERENTIAL_INTEGRITY,
                f"experiment_id {observation.experiment_id!r} does not resolve",
            )
        )
    else:
        if experiment.kind is ExperimentKind.LITERATURE:
            if not observation.source_id:
                issues.append(
                    _issue(
                        f"{path}.source_id",
                        RefusalReason.CONDITIONAL_FIELD,
                        "literature observation requires source_id",
                    )
                )
            if observation.locator is None:
                issues.append(
                    _issue(
                        f"{path}.locator",
                        RefusalReason.CONDITIONAL_FIELD,
                        "literature observation requires locator",
                    )
                )
            if not observation.read_from:
                issues.append(
                    _issue(
                        f"{path}.read_from",
                        RefusalReason.CONDITIONAL_FIELD,
                        "literature observation requires read_from",
                    )
                )
    work = None
    if experiment is not None and experiment.work_id and works:
        work = works.get(experiment.work_id)
    asset_ids: set[str] = set()
    if work is not None:
        asset_ids = {f.asset_id for f in work.source_files.files}
        if observation.read_from and observation.read_from not in asset_ids:
            issues.append(
                _issue(
                    f"{path}.read_from",
                    RefusalReason.REFERENTIAL_INTEGRITY,
                    f"read_from {observation.read_from!r} is not a Work asset",
                )
            )
        if observation.source_id and observation.source_id not in work.source_ids:
            issues.append(
                _issue(
                    f"{path}.source_id",
                    RefusalReason.REFERENTIAL_INTEGRITY,
                    f"source_id {observation.source_id!r} is not in Work.source_ids",
                )
            )
    identity = observation.identity
    if not isinstance(identity, Identity):
        issues.append(
            _issue(f"{path}.identity", RefusalReason.INVALID_IDENTITY, "identity is not an Identity")
        )
    else:
        _check_identity(f"{path}.identity", identity, issues)
        if experiment is not None:
            pc = observation.point_conditions or {}
            _reconcile_identity_axis(
                f"{path}.identity.temperature_K",
                identity.temperature_K,
                experiment.conditions.get("temperature_K"),
                pc.get("temperature_K"),
                issues,
            )
            _reconcile_identity_axis(
                f"{path}.identity.fO2_Pa",
                identity.fO2_Pa,
                experiment.conditions.get("fO2_Pa"),
                pc.get("fO2_Pa"),
                issues,
            )
            _reconcile_identity_axis(
                f"{path}.identity.total_pressure_Pa",
                identity.total_pressure_Pa,
                experiment.pressure_environment.total_pressure_Pa,
                pc.get("total_pressure_Pa"),
                issues,
            )
    ev = observation.evidence.class_
    if ev.is_value and ev.value is EvidenceClass.ENGINE_PREDICTION:
        if observation.engine is None:
            issues.append(
                _issue(
                    f"{path}.engine",
                    RefusalReason.CONDITIONAL_FIELD,
                    "prediction requires engine trace",
                )
            )
        if observation.authority is None:
            issues.append(
                _issue(
                    f"{path}.authority",
                    RefusalReason.CONDITIONAL_FIELD,
                    "prediction requires authority",
                )
            )
        elif observation.authority is Authority.CERTIFIED:
            if any(n.kind in _CERTIFICATION_DOWNGRADE_NOTICES for n in observation.notices):
                issues.append(
                    _issue(
                        f"{path}.authority",
                        RefusalReason.CONDITIONAL_FIELD,
                        "out_of_gamma_domain/out_of_certified_band forbids certified; "
                        "authority must be extrapolated",
                    )
                )
    if ev.is_value and ev.value in {
        EvidenceClass.MODEL_DERIVED,
        EvidenceClass.MEASURED_REDUCED,
    }:
        if not observation.derived_from:
            issues.append(
                _issue(
                    f"{path}.derived_from",
                    RefusalReason.CONDITIONAL_FIELD,
                    "derived observation requires derived_from",
                )
            )
        if observation.derivation is None:
            issues.append(
                _issue(
                    f"{path}.derivation",
                    RefusalReason.CONDITIONAL_FIELD,
                    "derived observation requires derivation",
                )
            )
    if observation.derived_from:
        for parent in observation.derived_from:
            if parent not in observations:
                issues.append(
                    _issue(
                        f"{path}.derived_from",
                        RefusalReason.REFERENTIAL_INTEGRITY,
                        f"derived_from {parent!r} does not resolve",
                    )
                )
    if observation.derivation is not None:
        for inp in observation.derivation.inputs:
            if inp not in observations and inp not in asset_ids:
                issues.append(
                    _issue(
                        f"{path}.derivation.inputs",
                        RefusalReason.REFERENTIAL_INTEGRITY,
                        f"derivation input {inp!r} does not resolve",
                    )
                )
    if observation.admission.status is AdmissionStatus.SUPERSEDED and not observation.admission.superseded_by:
        issues.append(
            _issue(
                f"{path}.admission.superseded_by",
                RefusalReason.CONDITIONAL_FIELD,
                "superseded admission requires superseded_by",
            )
        )
    if observation.admission.superseded_by and observation.admission.superseded_by not in observations:
        issues.append(
            _issue(
                f"{path}.admission.superseded_by",
                RefusalReason.REFERENTIAL_INTEGRITY,
                f"superseded_by {observation.admission.superseded_by!r} does not resolve",
            )
        )
    for i, notice in enumerate(observation.notices):
        _check_notice(f"{path}.notices[{i}]", notice, issues)
    if observation.evidence.attribution and not (
        ev.is_value and ev.value is EvidenceClass.QUOTED_ATTRIBUTED
    ):
        # attribution is C(quoted); ignore extra attribution on other classes
        pass
    if ev.is_value and ev.value is EvidenceClass.QUOTED_ATTRIBUTED and not observation.evidence.attribution:
        issues.append(
            _issue(
                f"{path}.evidence.attribution",
                RefusalReason.CONDITIONAL_FIELD,
                "quoted_attributed requires attribution",
            )
        )
    if ev.is_value and ev.value is EvidenceClass.MODEL_DERIVED and not observation.evidence.model:
        issues.append(
            _issue(
                f"{path}.evidence.model",
                RefusalReason.CONDITIONAL_FIELD,
                "model_derived requires model",
            )
        )
    if ev.is_value and ev.value is EvidenceClass.AUTHOR_ESTIMATE and not observation.evidence.model:
        issues.append(
            _issue(
                f"{path}.evidence.model",
                RefusalReason.CONDITIONAL_FIELD,
                "author_estimate requires model",
            )
        )
    if (
        observation.admission.status is not AdmissionStatus.PENDING
        and observation.admission.decided_by is None
    ):
        issues.append(
            _issue(
                f"{path}.admission.decided_by",
                RefusalReason.CONDITIONAL_FIELD,
                "decided admission requires decided_by",
            )
        )
    _walk_located(observation.point_conditions, f"{path}.point_conditions", issues)
    if observation.derivation is not None:
        _walk_located(observation.derivation.parameters, f"{path}.derivation.parameters", issues)
    if observation.value.kind is ValueKind.POINT and observation.uncertainty.kind is UncertaintyKind.NONE:
        # none uncertainty is valid; never invent a z-score
        pass
    return issues


def _lineage_tokens(
    observation: Observation,
    experiments: Mapping[str, Experiment],
    works: Mapping[str, Work] | None,
) -> set[str]:
    tokens: set[str] = set()
    if observation.source_id:
        tokens.add(observation.source_id)
    experiment = experiments.get(observation.experiment_id)
    if experiment is None:
        return tokens
    if experiment.work_id:
        tokens.add(experiment.work_id)
        work = None if works is None else works.get(experiment.work_id)
        if work is not None:
            tokens.update(work.source_ids)
            tokens.update(asset.asset_id for asset in work.source_files.files)
    return tokens


def validate_residual(
    residual: Residual,
    observations: Mapping[str, Observation],
    experiments: Mapping[str, Experiment],
    works: Mapping[str, Work] | None = None,
    path: str = "residual",
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    reference = observations.get(residual.reference)
    if reference is None:
        issues.append(
            _issue(
                f"{path}.reference",
                RefusalReason.REFERENTIAL_INTEGRITY,
                f"reference {residual.reference!r} does not resolve",
            )
        )
    candidate = None
    if residual.candidate is not None:
        candidate = observations.get(residual.candidate)
        if candidate is None:
            issues.append(
                _issue(
                    f"{path}.candidate",
                    RefusalReason.REFERENTIAL_INTEGRITY,
                    f"candidate {residual.candidate!r} does not resolve",
                )
            )
    if residual.status in {ResidualStatus.MATCH, ResidualStatus.MISMATCH}:
        if residual.numeric is None:
            issues.append(
                _issue(
                    f"{path}.numeric",
                    RefusalReason.CONDITIONAL_FIELD,
                    "match/mismatch requires numeric",
                )
            )
        if residual.refusal is not None:
            issues.append(
                _issue(
                    f"{path}.refusal",
                    RefusalReason.CONDITIONAL_FIELD,
                    "numeric branch forbids refusal",
                )
            )
    elif residual.status is ResidualStatus.REFUSED:
        if residual.refusal is None:
            issues.append(
                _issue(
                    f"{path}.refusal",
                    RefusalReason.CONDITIONAL_FIELD,
                    "refused requires refusal",
                )
            )
        if residual.numeric is not None:
            issues.append(
                _issue(
                    f"{path}.numeric",
                    RefusalReason.CONDITIONAL_FIELD,
                    "refused forbids numeric",
                )
            )
    if residual.execution.state is ExecutionState.PRODUCED:
        if residual.candidate is None:
            issues.append(
                _issue(
                    f"{path}.candidate",
                    RefusalReason.CONDITIONAL_FIELD,
                    "produced execution requires candidate Observation",
                )
            )
    else:
        if residual.candidate_request is None:
            issues.append(
                _issue(
                    f"{path}.candidate_request",
                    RefusalReason.CONDITIONAL_FIELD,
                    "no-output residual requires candidate_request",
                )
            )
        if residual.execution.state is ExecutionState.ATTEMPTED_UNAVAILABLE and not residual.execution.call_evidence:
            issues.append(
                _issue(
                    f"{path}.execution.call_evidence",
                    RefusalReason.CONDITIONAL_FIELD,
                    "attempted_unavailable requires resolver-call evidence",
                )
            )
    if residual.candidate_request is not None:
        if residual.candidate_request.experiment_id not in experiments:
            issues.append(
                _issue(
                    f"{path}.candidate_request.experiment_id",
                    RefusalReason.REFERENTIAL_INTEGRITY,
                    "candidate_request.experiment_id does not resolve",
                )
            )
    if reference is not None:
        experiment = experiments.get(reference.experiment_id)
        if experiment is not None:
            table = _table_payload(reference, observations)
            gates = run_validity_gates(experiment, reference, table=table)
            if not gates.passed:
                if (
                    residual.score_eligible
                    or residual.status is not ResidualStatus.REFUSED
                    or residual.numeric is not None
                ):
                    issues.append(
                        _issue(
                            path,
                            gates.reason or RefusalReason.INVALID_SOURCE,
                            "validity gate failed; residual must be refused with no numeric "
                            f"and score_eligible=false ({gates.primary_check})",
                        )
                    )
    if residual.status in {ResidualStatus.MATCH, ResidualStatus.MISMATCH}:
        if (
            reference is not None
            and candidate is not None
            and isinstance(reference.identity, Identity)
            and isinstance(candidate.identity, Identity)
        ):
            equal = identity_equal(reference.identity, candidate.identity)
            if equal.kind is not IdentityEqualKind.EQUAL:
                reason = (
                    RefusalReason.IDENTITY_MISMATCH
                    if equal.kind is IdentityEqualKind.IDENTITY_MISMATCH
                    else RefusalReason.IDENTITY_UNKNOWN
                    if equal.kind is IdentityEqualKind.IDENTITY_UNKNOWN
                    else RefusalReason.INVALID_IDENTITY
                )
                issues.append(
                    _issue(
                        f"{path}.numeric",
                        reason,
                        "numeric residual requires identity_equal equal; "
                        f"got {equal.kind} — unknown identity is a refused attempt, not a diagnostic number",
                    )
                )
    if residual.score_eligible:
        if residual.source_relation is not SourceRelation.INDEPENDENT:
            issues.append(
                _issue(
                    f"{path}.score_eligible",
                    RefusalReason.LINEAGE_UNKNOWN
                    if residual.source_relation is SourceRelation.UNKNOWN
                    else RefusalReason.CONDITIONAL_FIELD,
                    "score_eligible requires source_relation independent",
                )
            )
        if residual.status is ResidualStatus.REFUSED:
            issues.append(
                _issue(
                    f"{path}.score_eligible",
                    RefusalReason.CONDITIONAL_FIELD,
                    "score_eligible cannot be true when status is refused",
                )
            )
        if reference is not None:
            if reference.admission.status is not AdmissionStatus.ADMITTED:
                issues.append(
                    _issue(
                        f"{path}.score_eligible",
                        RefusalReason.ADMISSION_NOT_ADMITTED,
                        "score_eligible requires canonical admitted reference",
                    )
                )
            ev = reference.evidence.class_
            if not (ev.is_value and ev.value in MEASURED_EVIDENCE):
                issues.append(
                    _issue(
                        f"{path}.score_eligible",
                        RefusalReason.CONDITIONAL_FIELD,
                        "score_eligible requires measured_* reference evidence",
                    )
                )
            if candidate is not None:
                cev = candidate.evidence.class_
                if not (cev.is_value and cev.value is EvidenceClass.ENGINE_PREDICTION):
                    issues.append(
                        _issue(
                            f"{path}.score_eligible",
                            RefusalReason.CONDITIONAL_FIELD,
                            "score_eligible requires engine_prediction candidate",
                        )
                    )
                if (
                    residual.source_relation is SourceRelation.INDEPENDENT
                    and candidate.engine is not None
                ):
                    overlap = _lineage_tokens(reference, experiments, works) & set(
                        candidate.engine.coefficient_sources
                    )
                    if overlap:
                        issues.append(
                            _issue(
                                f"{path}.source_relation",
                                RefusalReason.CONDITIONAL_FIELD,
                                "independent score_eligible requires coefficient_sources "
                                "disjoint from the reference work/source ids (same_input)",
                            )
                        )
            quantity = None
            if isinstance(reference.identity, Identity):
                quantity = reference.identity.quantity
            if quantity in _VAPOUR_EQUILIBRIUM:
                blocking = _pressure_blocking_notices(
                    residual.notices,
                    reference.notices,
                    None if candidate is None else candidate.notices,
                )
                if blocking:
                    issues.append(
                        _issue(
                            f"{path}.score_eligible",
                            RefusalReason.CONDITIONAL_FIELD,
                            "vapour-equilibrium score_eligible cannot be true with "
                            "floor_inversion/fallback/pressure_provenance_unknown; "
                            "diagnostic numeric residuals keep score_eligible=false",
                        )
                    )
    endpoint_notices = union_notices(
        None if reference is None else reference.notices,
        None if candidate is None else candidate.notices,
    )
    residual_set = set(residual.notices)
    missing_notices = [n for n in endpoint_notices if n not in residual_set]
    if missing_notices:
        issues.append(
            _issue(
                f"{path}.notices",
                RefusalReason.CONDITIONAL_FIELD,
                "residual notices must include the union of endpoint notices",
            )
        )
    for i, notice in enumerate(residual.notices):
        _check_notice(f"{path}.notices[{i}]", notice, issues)
    return issues


def _index_unique(
    items: Sequence[Any] | Mapping[str, Any],
    attr: str,
    label: str,
    issues: list[ValidationIssue],
) -> dict[str, Any]:
    if isinstance(items, Mapping):
        return dict(items)
    indexed: dict[str, Any] = {}
    for item in items:
        key = getattr(item, attr)
        if key in indexed:
            issues.append(
                _issue(
                    f"{label}[{key}]",
                    RefusalReason.REFERENTIAL_INTEGRITY,
                    f"duplicate {attr} {key!r}",
                )
            )
        indexed[key] = item
    return indexed


def validate_corpus(
    works: Sequence[Work] | Mapping[str, Work],
    experiments: Sequence[Experiment] | Mapping[str, Experiment],
    observations: Sequence[Observation] | Mapping[str, Observation],
    residuals: Sequence[Residual] | Mapping[str, Residual] | None = None,
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    work_map = _index_unique(works, "work_id", "work", issues)
    exp_map = _index_unique(experiments, "experiment_id", "experiment", issues)
    obs_map = _index_unique(observations, "observation_id", "observation", issues)
    res_items: Iterable[Residual]
    if residuals is None:
        res_items = ()
    elif isinstance(residuals, Mapping):
        res_items = residuals.values()
    else:
        res_items = residuals
        seen_keys: set[str] = set()
        for residual in res_items:
            if residual.key in seen_keys:
                issues.append(
                    _issue(
                        f"residual[{residual.key}]",
                        RefusalReason.REFERENTIAL_INTEGRITY,
                        f"duplicate residual key {residual.key!r}",
                    )
                )
            seen_keys.add(residual.key)

    for work in work_map.values():
        issues.extend(validate_work(work, f"work[{work.work_id}]"))
    for experiment in exp_map.values():
        issues.extend(
            validate_experiment(experiment, work_map, exp_map, f"experiment[{experiment.experiment_id}]")
        )
    for observation in obs_map.values():
        issues.extend(
            validate_observation(
                observation,
                exp_map,
                obs_map,
                work_map,
                f"observation[{observation.observation_id}]",
            )
        )
    for cycle in _ancestry_cycles(obs_map):
        issues.append(
            _issue(
                "derived_from",
                RefusalReason.CYCLIC_DERIVATION,
                " → ".join(cycle),
            )
        )
    for residual in res_items:
        issues.extend(
            validate_residual(
                residual, obs_map, exp_map, work_map, f"residual[{residual.key}]"
            )
        )
    return ValidationReport(tuple(issues))
