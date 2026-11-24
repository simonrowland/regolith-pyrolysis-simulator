"""Numeric residual transformation.

Owns metric selection and converts a candidate and a reference into a typed
``ResidualNumeric``. It does not select observations, build engine requests,
dispatch predictions, construct scoring refusals, or render reports.

Imports stay on records, enums, and identity. Callers that still import these
names from ``score`` receive the same objects through that facade.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Mapping, Sequence

from simulator.battery.enums import (
    QUANTITY_UNITS,
    MetricOperation,
    MethodToken,
    PRINTED_CAPACITY_QUANTITIES,
    Quantity,
    Rail,
    RelativeSeriesUnavailableReason,
    RefusalReason,
    ResidualStatus,
    SourceRelation,
    ValenceValueForm,
    ValueKind,
    effective_phase_basis,
)
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.records import (
    DecisionBand,
    Experiment,
    Observation,
    RelativeSeriesAnchor,
    RelativeSeriesAssumption,
    RelativeSeriesClock,
    RelativeSeriesComparison,
    RelativeSeriesEvidence,
    RelativeSeriesPoint,
    RelativeSeriesPointResidual,
    ResidualNumeric,
    Uncertainty,
)


# Quantity → metric operation.
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
    Quantity.DELTA_FH: MetricOperation.ABSOLUTE,
    Quantity.DELTA_FG: MetricOperation.ABSOLUTE,
    Quantity.ENTHALPY_OF_FUSION: MetricOperation.ABSOLUTE,
    Quantity.ENTROPY_OF_FUSION: MetricOperation.ABSOLUTE,
    Quantity.H_MINUS_H298: MetricOperation.ABSOLUTE,
    Quantity.PARTIAL_MOLAR_ENTHALPY: MetricOperation.ABSOLUTE,
    Quantity.ENTHALPY_OF_VAPORIZATION_2ND_LAW: MetricOperation.ABSOLUTE,
    Quantity.ENTHALPY_OF_VAPORIZATION_3RD_LAW: MetricOperation.ABSOLUTE,
    Quantity.CP: MetricOperation.ABSOLUTE,
    Quantity.S: MetricOperation.ABSOLUTE,
    Quantity.LOG10_KF: MetricOperation.ABSOLUTE,
    Quantity.LOG10_K_STAR: MetricOperation.ABSOLUTE,
    Quantity.EVAPORATION_COEFFICIENT_ALPHA: MetricOperation.ABSOLUTE,
    Quantity.MASS_LOSS_FRACTION: MetricOperation.ABSOLUTE,
    Quantity.MASS_LOSS_FRACTION_VS_T: MetricOperation.RELATIVE,
    Quantity.YIELD_FRACTION: MetricOperation.RELATIVE,
    Quantity.O2_YIELD: MetricOperation.RELATIVE,
    Quantity.EVAPORATION_RATE: MetricOperation.RELATIVE,
    Quantity.MASS_LOSS_RATE: MetricOperation.RELATIVE,
    Quantity.WALL_DEPOSIT_MASS: MetricOperation.RELATIVE,
    Quantity.WALL_DEPOSIT_AREAL_RATE: MetricOperation.RELATIVE,
    Quantity.TRANSITION_TEMPERATURE: MetricOperation.ABSOLUTE,
    Quantity.FE3_FE2_RATIO: MetricOperation.DEX,
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
    Quantity.ELEMENT_RETENTION_RATIO: MetricOperation.DEX,
    # Equilibrium ratios (not a closed phase basis) are absolute. Printed
    # component amounts and L_X are dex; metric_operation_for_identity
    # returns dex when the subtype is a PHASE_BASIS_TOKENS value.
    # Printed log10 C_S / log10 C_Cl are already logs and are often negative,
    # so they take absolute, the same shape as log10_Kf.
    Quantity.EQUILIBRIUM_PHASE_COMPOSITION: MetricOperation.ABSOLUTE,
    Quantity.DISTRIBUTION_RATIO: MetricOperation.DEX,
    Quantity.SULFIDE_CAPACITY: MetricOperation.ABSOLUTE,
    Quantity.CHLORIDE_CAPACITY: MetricOperation.ABSOLUTE,
}


def relative_channel_point_residuals(
    observed_series: Sequence[RelativeSeriesPoint],
    model_series: Sequence[RelativeSeriesPoint],
    anchor: RelativeSeriesAnchor,
    clock: RelativeSeriesClock,
    evidence: RelativeSeriesEvidence,
) -> RelativeSeriesComparison:
    """Compare one QMS channel against model samples on a relative clock.

    Clock units use exact DP-013 factors. When correspondence is absent, both
    series are zeroed at their first shared positive sample; no fitted shift
    or scale is applied. Source signal and model pressure are normalized there
    before using the existing dex metric.
    """

    def unavailable(
        reason: RelativeSeriesUnavailableReason,
    ) -> RelativeSeriesComparison:
        return RelativeSeriesComparison(unavailable_reason=reason)

    if not observed_series or not model_series:
        return unavailable(RelativeSeriesUnavailableReason.INVALID_SERIES)
    if evidence.quantity is not Quantity.ION_INTENSITY:
        return unavailable(RelativeSeriesUnavailableReason.QUANTITY_UNSUPPORTED)
    observed_channels = {point.observable for point in observed_series}
    if len(observed_channels) != 1:
        return unavailable(RelativeSeriesUnavailableReason.MIXED_CHANNELS)
    if observed_channels != {evidence.channel}:
        return unavailable(RelativeSeriesUnavailableReason.CHANNEL_MISMATCH)
    if _relative_series_source_run_mixed(observed_series):
        return unavailable(RelativeSeriesUnavailableReason.MIXED_SOURCE_RUNS)
    model_species = {point.observable for point in model_series}
    if len(model_species) != 1:
        return unavailable(RelativeSeriesUnavailableReason.MIXED_MODEL_SPECIES)
    if model_species != {evidence.target_species}:
        return unavailable(RelativeSeriesUnavailableReason.MODEL_SPECIES_MISMATCH)
    oxygen_reason = _relative_series_model_oxygen_unavailable_reason(
        observed_series, model_series, evidence
    )
    if oxygen_reason is not None:
        return unavailable(oxygen_reason)
    if _untied_physical_run(observed_series, model_series, evidence):
        return unavailable(RelativeSeriesUnavailableReason.AMBIGUOUS_RUN_IDENTITY)
    if evidence.model_quantity is not Quantity.P_PARTIAL:
        return unavailable(RelativeSeriesUnavailableReason.QUANTITY_UNSUPPORTED)
    unknown_boundaries = tuple(
        (series, index, point.boundary)
        for series, points in (("source", observed_series), ("model", model_series))
        for index, point in enumerate(points)
        if point.boundary in {"unknown", "unreported"}
    )
    unknown_comparison_boundary = evidence.boundary in {"unknown", "unreported"}
    assumed_boundary = evidence.boundary
    if unknown_boundaries or unknown_comparison_boundary:
        known_boundaries = {
            point.boundary
            for point in (*observed_series, *model_series)
            if point.boundary not in {"unknown", "unreported"}
        }
        if not unknown_comparison_boundary:
            known_boundaries.add(evidence.boundary)
        if len(known_boundaries) > 1:
            return unavailable(RelativeSeriesUnavailableReason.BOUNDARY_MISMATCH)
        assumed_boundary = (
            next(iter(known_boundaries))
            if known_boundaries
            else "shared_unreported_boundary"
        )
    if not _relative_series_boundaries_match(
        observed_series, model_series, assumed_boundary
    ):
        return unavailable(RelativeSeriesUnavailableReason.BOUNDARY_MISMATCH)
    assumptions: list[RelativeSeriesAssumption] = []
    if unknown_boundaries or unknown_comparison_boundary:
        assumptions.append(
            RelativeSeriesAssumption(
                "qms_boundary_assumed",
                {
                    "boundary": assumed_boundary,
                    "comparison_boundary": evidence.boundary,
                    "unknown_samples": unknown_boundaries,
                },
                "Unknown or unreported boundaries use the one shared known boundary; if none is known, use a shared-unreported boundary.",
            )
        )
    native_observed_series = tuple(observed_series)
    native_model_series = tuple(model_series)
    normalized_series = _relative_series_time_coordinates(
        observed_series, model_series, anchor
    )
    if normalized_series is None:
        return unavailable(RelativeSeriesUnavailableReason.COORDINATE_UNIT_MISMATCH)
    observed_series, model_series, anchor = normalized_series
    signal_reason = _relative_series_signal_response_reason(evidence)
    if signal_reason is not None:
        return unavailable(signal_reason)
    if not _relative_series_source_located(evidence):
        return unavailable(
            RelativeSeriesUnavailableReason.SOURCE_EVIDENCE_UNLOCATED
        )
    unverified_signal: dict[str, str] = {}
    for field, state, default in (
        (
            "isolation",
            evidence.target_isolated_or_interferences_corrected,
            "assume the target is isolated or interferences are corrected",
        ),
        (
            "background",
            evidence.background_treatment_supported,
            "assume the stated background treatment supports comparison",
        ),
        (
            "proportionality",
            evidence.response_proportional_over_interval,
            "assume response is proportional over the comparison interval",
        ),
    ):
        if state is None:
            unverified_signal[field] = default
    if unverified_signal:
        assumptions.append(
            RelativeSeriesAssumption(
                "qms_signal_response_assumed",
                {"unverified": unverified_signal},
                "The source does not verify each listed signal-response premise.",
            )
        )
    missing_children = {
        "source_point_indices": tuple(
            index for index, point in enumerate(observed_series) if point.locator is None
        ),
        "clock": clock.locator is None,
        "parent_locator": evidence.locator,
    }
    if missing_children["source_point_indices"] or missing_children["clock"]:
        assumptions.append(
            RelativeSeriesAssumption(
                "qms_provenance_assumed",
                missing_children,
                "A located parent panel supplies the missing child locator information.",
            )
        )
    observed_by_coordinate: dict[Decimal, RelativeSeriesPoint] = {}
    for point in observed_series:
        if point.coordinate in observed_by_coordinate:
            return unavailable(RelativeSeriesUnavailableReason.DUPLICATE_COORDINATE)
        observed_by_coordinate[point.coordinate] = point
    model_by_coordinate: dict[Decimal, RelativeSeriesPoint] = {}
    for point in model_series:
        if point.coordinate in model_by_coordinate:
            return unavailable(RelativeSeriesUnavailableReason.DUPLICATE_COORDINATE)
        model_by_coordinate[point.coordinate] = point

    source_anchor = observed_by_coordinate.get(anchor.source_coordinate)
    model_anchor = model_by_coordinate.get(anchor.model_coordinate)
    anchor_reason = _relative_series_explicit_anchor_missing_reason(
        clock.mapping_supported, source_anchor, model_anchor
    )
    if anchor_reason is not None:
        return unavailable(anchor_reason)
    assumed_anchor = clock.mapping_supported is not True
    if assumed_anchor:
        # Assume the first coordinate with positive samples on both series is
        # elapsed zero. This fixes an origin without fitting a shift or scale.
        source_anchor, model_anchor = _relative_series_default_anchor(
            observed_series, model_series
        )
        if source_anchor is None:
            return unavailable(RelativeSeriesUnavailableReason.SOURCE_ANCHOR_MISSING)
        if model_anchor is None:
            return unavailable(RelativeSeriesUnavailableReason.MODEL_ANCHOR_MISSING)
        source_anchor_index = next(
            index
            for index, point in enumerate(observed_series)
            if point is source_anchor
        )
        model_anchor_index = next(
            index for index, point in enumerate(model_series) if point is model_anchor
        )
        native_source_anchor = native_observed_series[source_anchor_index]
        native_model_anchor = native_model_series[model_anchor_index]
        assumptions.append(
            RelativeSeriesAssumption(
                "qms_clock_anchor_assumed",
                {
                    "index_base": 0,
                    "source_sample": {
                        "index": source_anchor_index,
                        "time": str(native_source_anchor.coordinate),
                        "unit": native_source_anchor.coordinate_unit,
                        "observation_id": native_source_anchor.observation_id,
                    },
                    "model_sample": {
                        "index": model_anchor_index,
                        "time": str(native_model_anchor.coordinate),
                        "unit": native_model_anchor.coordinate_unit,
                        "observation_id": native_model_anchor.observation_id,
                    },
                    "noise_floor": "not supplied; raw values > 0 select the anchor",
                    "clock_mapping": "no fitted shift or scale",
                },
                "Elapsed zero is the first shared positive sample; no noise floor was supplied, so raw > 0 is used; no fitted shift or scale is applied.",
            )
        )
        anchor = RelativeSeriesAnchor(
            source_coordinate=source_anchor.coordinate,
            model_coordinate=model_anchor.coordinate,
            source_event=anchor.source_event,
            model_event=anchor.model_event,
            physical_event=anchor.physical_event,
        )
    if not assumed_anchor and (
        clock.source_event != anchor.source_event
        or clock.model_event != anchor.model_event
        or clock.physical_event != anchor.physical_event
    ):
        return unavailable(RelativeSeriesUnavailableReason.CLOCK_EVENT_MISMATCH)
    if not _positive_anchor_value(source_anchor):
        return unavailable(
            RelativeSeriesUnavailableReason.SOURCE_ANCHOR_NOT_POSITIVE
        )
    if not _positive_anchor_value(model_anchor):
        return unavailable(
            RelativeSeriesUnavailableReason.MODEL_ANCHOR_NOT_POSITIVE
        )

    point_residuals: list[RelativeSeriesPointResidual] = []
    for observed in observed_series:
        model_coordinate = anchor.model_coordinate + (
            observed.coordinate - anchor.source_coordinate
        )
        model = model_by_coordinate.get(model_coordinate)
        if model is None:
            point_residuals.append(
                RelativeSeriesPointResidual(
                    observed.coordinate,
                    model_coordinate,
                    None,
                    RelativeSeriesUnavailableReason.MODEL_POINT_MISSING,
                    observed.uncertainty,
                )
            )
            continue
        observed_value = _relative_positive_value(observed.value)
        if observed_value is None:
            point_residuals.append(
                RelativeSeriesPointResidual(
                    observed.coordinate,
                    model_coordinate,
                    None,
                    RelativeSeriesUnavailableReason.SOURCE_VALUE_NOT_POSITIVE_POINT,
                    observed.uncertainty,
                )
            )
            continue
        if not _positive_anchor_value(model):
            point_residuals.append(
                RelativeSeriesPointResidual(
                    observed.coordinate,
                    model_coordinate,
                    None,
                    RelativeSeriesUnavailableReason.MODEL_VALUE_NOT_POSITIVE_POINT,
                    observed.uncertainty,
                )
            )
            continue
        observed_normalized = observed_value / source_anchor.value.point
        model_normalized = model.value.point / model_anchor.value.point
        residual = compute_metric(
            MetricOperation.DEX, model_normalized, observed_normalized
        )
        if residual is None:
            point_residuals.append(
                RelativeSeriesPointResidual(
                    observed.coordinate,
                    model_coordinate,
                    None,
                    RelativeSeriesUnavailableReason.SOURCE_VALUE_NOT_POSITIVE_POINT,
                    observed.uncertainty,
                )
            )
            continue
        point_residuals.append(
            RelativeSeriesPointResidual(
                observed.coordinate,
                model_coordinate,
                residual,
                uncertainty=observed.uncertainty,
            )
        )
    return RelativeSeriesComparison(
        points=tuple(point_residuals), assumptions=tuple(assumptions)
    )


_RELATIVE_TIME_SECONDS: dict[str, Decimal] = {
    "s": Decimal(1),
    "sec": Decimal(1),
    "second": Decimal(1),
    "seconds": Decimal(1),
    "min": Decimal(60),
    "minute": Decimal(60),
    "minutes": Decimal(60),
    "h": Decimal(3600),
    "hr": Decimal(3600),
    "hour": Decimal(3600),
    "hours": Decimal(3600),
    "ks": Decimal(1000),
}


def _relative_series_time_coordinates(
    observed: Sequence[RelativeSeriesPoint],
    model: Sequence[RelativeSeriesPoint],
    anchor: RelativeSeriesAnchor,
) -> tuple[
    tuple[RelativeSeriesPoint, ...],
    tuple[RelativeSeriesPoint, ...],
    RelativeSeriesAnchor,
] | None:
    units = {point.coordinate_unit for point in (*observed, *model)}
    factors = {_RELATIVE_TIME_SECONDS.get(unit.casefold()) for unit in units}
    if len(units) == 1 and None in factors:
        return tuple(observed), tuple(model), anchor
    if None in factors:
        return None

    def in_seconds(point: RelativeSeriesPoint) -> RelativeSeriesPoint:
        return RelativeSeriesPoint(
            run_id=point.run_id,
            coordinate=(
                point.coordinate
                * _RELATIVE_TIME_SECONDS[point.coordinate_unit.casefold()]
            ),
            value=point.value,
            observable=point.observable,
            boundary=point.boundary,
            coordinate_unit="s",
            locator=point.locator,
            observation_id=point.observation_id,
            uncertainty=point.uncertainty,
        )

    source_factor = _RELATIVE_TIME_SECONDS[observed[0].coordinate_unit.casefold()]
    model_factor = _RELATIVE_TIME_SECONDS[model[0].coordinate_unit.casefold()]
    return (
        tuple(map(in_seconds, observed)),
        tuple(map(in_seconds, model)),
        RelativeSeriesAnchor(
            source_coordinate=anchor.source_coordinate * source_factor,
            model_coordinate=anchor.model_coordinate * model_factor,
            source_event=anchor.source_event,
            model_event=anchor.model_event,
            physical_event=anchor.physical_event,
        ),
    )


def _relative_series_boundaries_match(
    observed: Sequence[RelativeSeriesPoint],
    model: Sequence[RelativeSeriesPoint],
    expected: str,
) -> bool:
    return all(
        point.boundary == expected
        or point.boundary in {"unknown", "unreported"}
        for point in (*observed, *model)
    )


def _relative_series_source_run_mixed(
    observed: Sequence[RelativeSeriesPoint],
) -> bool:
    return len({point.run_id for point in observed}) != 1


def _relative_series_signal_response_reason(
    evidence: RelativeSeriesEvidence,
) -> RelativeSeriesUnavailableReason | None:
    if evidence.target_isolated_or_interferences_corrected is False:
        return RelativeSeriesUnavailableReason.INTERFERENCE_UNRESOLVED
    if evidence.background_treatment_supported is False:
        return RelativeSeriesUnavailableReason.BACKGROUND_UNRESOLVED
    if evidence.response_proportional_over_interval is False:
        return RelativeSeriesUnavailableReason.PROPORTIONAL_TRACKING_UNESTABLISHED
    return None


def _relative_series_source_located(evidence: RelativeSeriesEvidence) -> bool:
    return evidence.locator is not None


def _relative_series_default_anchor(
    observed: Sequence[RelativeSeriesPoint], model: Sequence[RelativeSeriesPoint]
) -> tuple[RelativeSeriesPoint | None, RelativeSeriesPoint | None]:
    model_by_coordinate = {point.coordinate: point for point in model}
    for source_point in observed:
        model_point = model_by_coordinate.get(source_point.coordinate)
        if (
            model_point is not None
            and _positive_point(source_point.value)
            and _positive_point(model_point.value)
        ):
            return source_point, model_point
    return None, None


def _relative_series_explicit_anchor_missing_reason(
    mapping_supported: bool | None,
    source_anchor: RelativeSeriesPoint | None,
    model_anchor: RelativeSeriesPoint | None,
) -> RelativeSeriesUnavailableReason | None:
    if mapping_supported is not True:
        return None
    if source_anchor is None:
        return RelativeSeriesUnavailableReason.SOURCE_ANCHOR_MISSING
    if model_anchor is None:
        return RelativeSeriesUnavailableReason.MODEL_ANCHOR_MISSING
    return None


def _positive_anchor_value(point: RelativeSeriesPoint) -> bool:
    return _positive_point(point.value)


def _positive_point(value: Value) -> bool:
    return (
        value.kind is ValueKind.POINT
        and value.point is not None
        and value.point.is_finite()
        and value.point > 0
    )


def _relative_positive_value(value: Value) -> Decimal | None:
    return value.point if _positive_point(value) else None


def _untied_physical_run(
    observed_series: Sequence[RelativeSeriesPoint],
    model_series: Sequence[RelativeSeriesPoint],
    evidence: RelativeSeriesEvidence,
) -> bool:
    """Require model and evidence run IDs to match the already-validated source."""

    model = {point.run_id for point in model_series}
    reference = evidence.reference_run_id
    model_run = evidence.model_run_id
    if len(model) != 1:
        return True
    observed_run = observed_series[0].run_id
    modeled_run = next(iter(model))
    return (
        observed_run != modeled_run
        or reference is None
        or model_run is None
        or reference != observed_run
        or model_run != modeled_run
    )


def _relative_series_model_oxygen_unavailable_reason(
    observed_series: Sequence[RelativeSeriesPoint],
    model_series: Sequence[RelativeSeriesPoint],
    evidence: RelativeSeriesEvidence,
    *,
    expected_input_ids: frozenset[str] | None = None,
) -> RelativeSeriesUnavailableReason | None:
    """Own one model run for every channel, then O/O₂ input eligibility.

    The run check is not oxygen-only. A non-oxygen return before it let a
    mixed SiO series score by coordinate.
    """

    if not observed_series or not model_series:
        return None
    if len({point.run_id for point in model_series}) > 1:
        return RelativeSeriesUnavailableReason.MIXED_MODEL_RUNS
    if evidence.target_species not in {"O", "O2"}:
        return None
    input_ids = evidence.model_oxygen_input_observation_ids
    observed_ids = {
        point.observation_id
        for point in observed_series
        if point.observation_id is not None
    }
    if evidence.oxygen_independent is False or (
        input_ids is not None and observed_ids.intersection(input_ids)
    ):
        return RelativeSeriesUnavailableReason.OXYGEN_SELF_AGREEMENT
    if evidence.oxygen_independent is None:
        return RelativeSeriesUnavailableReason.OXYGEN_ELIGIBILITY_UNKNOWN
    if input_ids is None or len(set(input_ids)) != len(input_ids):
        return RelativeSeriesUnavailableReason.MODEL_OXYGEN_INPUTS_UNAVAILABLE
    if expected_input_ids is not None and set(input_ids) != expected_input_ids:
        return RelativeSeriesUnavailableReason.MODEL_OXYGEN_INPUTS_UNAVAILABLE
    return None


# Physical dimension of a stored unit. Unknown units match nothing.
_UNIT_DIMENSION: dict[str, str] = {
    "kJ_per_declared_mol_basis": "energy_per_mol",
    "J_per_declared_mol_basis_per_K": "energy_per_mol_per_K",
    "dimensionless": "dimensionless",
}


def metric_operation(quantity: Quantity | None) -> MetricOperation | None:
    if quantity is None:
        return None
    return QUANTITY_METRIC.get(quantity)


def metric_operation_for_identity(identity: Identity) -> MetricOperation | None:
    """Select an identity-aware metric when the quantity's form requires it."""
    quantity = quantity_token(identity)
    if quantity is Quantity.VALENCE_RATIO:
        basis = identity.valence_basis
        if basis is None or not basis.is_value:
            return None
        if basis.value.value_form in {
            ValenceValueForm.FRACTION,
            ValenceValueForm.RATIO,
        }:
            return MetricOperation.DEX
        if basis.value.value_form in {
            ValenceValueForm.LOG10_RATIO,
            ValenceValueForm.AVERAGE_OXIDATION_STATE,
        }:
            return MetricOperation.ABSOLUTE
        return None
    if quantity in PRINTED_CAPACITY_QUANTITIES:
        return MetricOperation.ABSOLUTE
    if quantity is Quantity.DISTRIBUTION_RATIO:
        return MetricOperation.DEX
    if quantity is Quantity.EQUILIBRIUM_PHASE_COMPOSITION:
        subtype = (
            identity.subtype.value
            if identity.subtype is not None and identity.subtype.is_value
            else None
        )
        # Prose wt% and mole-percent synonyms are the same dex observable
        # as a token phase row. The stored subtype is not rewritten.
        if effective_phase_basis(subtype) is not None:
            return MetricOperation.DEX
        return MetricOperation.ABSOLUTE
    if quantity is not Quantity.RESIDUE_COMPONENT_COMPOSITION:
        return metric_operation(quantity)
    subtype = identity.subtype.value if identity.subtype.is_value else None
    if subtype == "element_ppm_by_mass":
        return MetricOperation.DEX
    if subtype == "oxide_wt_percent":
        return MetricOperation.ABSOLUTE
    return metric_operation(quantity)


def _phase_component_reference_wt_percent(
    identity: Identity, reference: Decimal
) -> Decimal:
    """Convert a printed mass-ppm phase amount for comparison with wt% engines."""
    quantity = quantity_token(identity)
    subtype = (
        identity.subtype.value
        if identity.subtype is not None and identity.subtype.is_value
        else None
    )
    if (
        quantity is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        and subtype == "element_ppm_by_mass"
    ):
        # 1 ppm = 1e-6 mass fraction = 1e-4 wt%; 250 ppm = 0.025 wt%.
        return reference / Decimal("10000")
    return reference


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


def _reference_from_metric(
    operation: MetricOperation,
    candidate: Decimal,
    residual: Decimal,
) -> Decimal | None:
    """Recover the reference a scored residual was measured against.

    Premise: invert the SCHEMA metric. The candidate is the predicted value.
    Algebra:
      absolute: R = C − residual, because residual = C − R.
      relative: residual = (C − R) / |R|.
        Positive root first: R = C / (1 + residual) when that R is > 0.
        Otherwise R = C / (1 − residual) when that R is < 0.
        A residual that fits both signs returns the positive root
        (C = 1, residual = 1.5 fits R = 0.4 and R = −2; the result is 0.4).
      dex: residual = log10(C / R), so R = C / 10^residual, requiring C > 0.
    Unit check: R is in the candidate's unit. Sanity: residual 0 returns
    R = C on every operation; relative uses the sign of C.
    """

    if not candidate.is_finite() or not residual.is_finite():
        return None
    if operation is MetricOperation.ABSOLUTE:
        return candidate - residual
    if operation is MetricOperation.RELATIVE:
        for denominator, positive_reference in (
            (Decimal(1) + residual, True),
            (Decimal(1) - residual, False),
        ):
            if denominator == 0:
                continue
            reference = candidate / denominator
            if (positive_reference and reference > 0) or (
                not positive_reference and reference < 0
            ):
                return reference
        return None
    if operation is MetricOperation.DEX:
        if candidate <= 0:
            return None
        try:
            ratio = Decimal(str(10.0 ** float(residual)))
        except (OverflowError, ValueError):
            return None
        return candidate / ratio if ratio.is_finite() and ratio > 0 else None
    return None


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
    # These two output dimensions are intentionally subtype-defined. The
    # printed-width resolver matches this scale to identity.subtype; permit
    # only its absolute path here and keep other units fail-closed.
    if (
        quantity is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and band.unit == "mass_percent"
    ):
        return operation is MetricOperation.ABSOLUTE
    if (
        quantity is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        and band.unit in {"mass_percent", "mole_percent"}
    ):
        return operation is MetricOperation.ABSOLUTE
    quantity_dim = unit_dimension(QUANTITY_UNITS[quantity])
    band_dim = unit_dimension(band.unit)
    return quantity_dim is not None and quantity_dim == band_dim


def decision_band_for(
    quantity: Quantity | None,
    source_relation: SourceRelation,
    *,
    printed_band: DecisionBand | None = None,
    printed_refusal: Mapping[str, object] | None = None,
    derived_band: DecisionBand | None = None,
    operation: MetricOperation | None = None,
) -> DecisionBand | None:
    """Choose a printed or derived band, then apply its quantity and unit gates.

    ``source_relation`` labels lineage only. A KEMS pressure envelope covers
    admitted measured partial-pressure rows whose cell uncertainty is unparseable;
    other printed-uncertainty refusals block derived-band fallthrough.
    """
    del source_relation
    band = printed_band if printed_band is not None else derived_band
    if band is None:
        return None
    if band.band_basis == "method_class_assumed":
        return None
    kems_pressure_band = band.rule.startswith(
        "KEMS p_partial measured uncertainty:"
    )
    if kems_pressure_band:
        if quantity is not Quantity.P_PARTIAL:
            return None
    elif printed_refusal is not None:
        return None
    if not band_dimension_matches(quantity, band, operation=operation):
        return None
    return band


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
    printed_band: DecisionBand | None = None,
    printed_refusal: Mapping[str, object] | None = None,
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
    band = decision_band_for(
        quantity,
        source_relation,
        printed_band=printed_band,
        printed_refusal=printed_refusal,
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
