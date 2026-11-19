"""Derived facts and aggregates consumed by battery score report renderers."""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from typing import Callable, Iterable, Mapping, NamedTuple, Sequence

from simulator.battery.enums import Quantity, SourceRelation, ValueKind
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.records import Observation, Residual, Uncertainty


_RETENTION_LOSS_BINS = (
    "negative (FM > 1)",
    "0–1%",
    ">1–10%",
    ">10–50%",
    ">50%",
)

_RetentionCell = tuple[
    str, str, str, Decimal | None, Decimal | None, Uncertainty, Decimal | None
]


class ElementRetentionReportData(NamedTuple):
    cells: tuple[_RetentionCell, ...]
    loss_bin_counts: tuple[tuple[str, int], ...]


def element_retention_report_data(
    rows: Iterable[Residual | Mapping[str, object]],
    observations: Mapping[str, Observation],
) -> ElementRetentionReportData:
    """Prepare element-retention observations, residuals, and loss-bin counts."""

    cells: list[_RetentionCell] = []
    loss_bin_rows: dict[str, set[str]] = {
        label: set() for label in _RETENTION_LOSS_BINS
    }
    for row in rows:
        if isinstance(row, Residual):
            reference_id = row.reference
            status = row.status.value
            numeric = None if row.numeric is None else row.numeric.value
            engine = (
                row.candidate_request.engine.value
                if row.candidate_request is not None
                else "—"
            )
        else:
            reference_id = str(row.get("reference") or "")
            status = str(row.get("status") or "unknown")
            numeric_payload = row.get("numeric")
            raw_numeric = (
                numeric_payload.get("value")
                if isinstance(numeric_payload, Mapping)
                else None
            )
            try:
                numeric = None if raw_numeric is None else Decimal(str(raw_numeric))
            except (ArithmeticError, TypeError, ValueError):
                numeric = None
            request = row.get("candidate_request")
            engine = (
                str(request.get("engine") or "—")
                if isinstance(request, Mapping)
                else "—"
            )

        observation = observations.get(reference_id)
        if (
            observation is None
            or not isinstance(observation.identity, Identity)
            or quantity_token(observation.identity)
            is not Quantity.ELEMENT_RETENTION_RATIO
        ):
            continue
        fm = (
            observation.value.point
            if observation.value.kind is ValueKind.POINT
            else None
        )
        loss = None if fm is None else Decimal(1) - fm
        if fm is not None and fm > 0:
            if loss is not None and loss < 0:
                loss_bin = "negative (FM > 1)"
            elif loss is not None and loss <= Decimal("0.01"):
                loss_bin = "0–1%"
            elif loss is not None and loss <= Decimal("0.10"):
                loss_bin = ">1–10%"
            elif loss is not None and loss <= Decimal("0.50"):
                loss_bin = ">10–50%"
            else:
                loss_bin = ">50%"
            loss_bin_rows[loss_bin].add(observation.observation_id)
        cells.append(
            (
                observation.observation_id,
                engine,
                status,
                fm,
                loss,
                observation.uncertainty,
                numeric,
            )
        )
    counts = tuple(
        (label, len(loss_bin_rows[label])) for label in _RETENTION_LOSS_BINS
    )
    return ElementRetentionReportData(tuple(cells), counts)


class Nasa9FitComparisonData(NamedTuple):
    """Sorted NASA-9 comparison counts prepared for the report formatter."""

    counts: tuple[tuple[str, str, int, int, int], ...]
    refusals: tuple[tuple[str, str, str, str, int], ...]


class EligibilityFailureCensus(NamedTuple):
    """Numeric residual failures grouped by rail, engine, and conjunct."""

    rows: tuple[tuple[str, str, str, int, int, int], ...]
    engine_counts: tuple[tuple[str, str, int, int, int | None], ...]


def eligibility_failure_census(
    rows: Iterable[Mapping[str, object]],
    *,
    conjunct_names: Iterable[str],
    observations: Mapping[str, Observation] | None = None,
    engine_for_row: Callable[[Mapping[str, object]], str],
) -> EligibilityFailureCensus:
    """Count failure events, observations, and first failure per report cell."""

    names = tuple(conjunct_names)
    event_counts: dict[tuple[str, str, str], int] = defaultdict(int)
    observation_ids: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    numeric_events: dict[tuple[str, str], int] = defaultdict(int)
    numeric_observations: dict[tuple[str, str], set[str]] = defaultdict(set)
    numeric_sources: dict[tuple[str, str], set[str]] = defaultdict(set)
    source_counts_available = observations is not None
    first_counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for row in rows:
        if not isinstance(row.get("numeric"), Mapping) or not row.get("rail"):
            continue
        rail = str(row["rail"])
        engine = engine_for_row(row)
        reference = str(row.get("reference") or "")
        if observations is None:
            observation = None
        else:
            from simulator.battery.compilation_tier import reference_observation

            observation = reference_observation(observations, reference)
        observation_id = (
            observation.observation_id if observation is not None else reference
        )
        engine_cell = (rail, engine)
        numeric_events[engine_cell] += 1
        numeric_observations[engine_cell].add(observation_id)
        if observation is not None and observation.source_id:
            numeric_sources[engine_cell].add(observation.source_id)
        exclusions = set(row.get("exclusions") or ())
        first = next((name for name in names if name in exclusions), None)
        if first is not None:
            first_counts[(rail, engine, first)] += 1
        for name in names:
            if name not in exclusions:
                continue
            cell = (rail, engine, name)
            event_counts[cell] += 1
            observation_ids[cell].add(observation_id)

    return EligibilityFailureCensus(
        tuple(
            (
                rail,
                engine,
                name,
                event_counts[(rail, engine, name)],
                len(observation_ids[(rail, engine, name)]),
                first_counts[(rail, engine, name)],
            )
            for rail, engine in sorted(numeric_events)
            for name in names
        ),
        tuple(
            (
                rail,
                engine,
                numeric_events[(rail, engine)],
                len(numeric_observations[(rail, engine)]),
                (
                    len(numeric_sources[(rail, engine)])
                    if source_counts_available
                    else None
                ),
            )
            for rail, engine in sorted(numeric_events)
        ),
    )


def nasa9_fit_comparison_report_data(
    comparisons: Iterable[Mapping[str, object]],
) -> Nasa9FitComparisonData:
    """Aggregate NASA-9 fit-comparison totals and typed refusal details."""

    counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    refusals: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for row in comparisons:
        channel = row.get("comparison_channel")
        if not isinstance(channel, str) or not channel.startswith("nasa9_fit:"):
            continue
        engine = channel.partition(":")[2]
        quantity = str(row.get("quantity") or "unknown")
        numeric = row.get("value") is not None and row.get("status") != "refused"
        group = counts[(engine, quantity)]
        group[0] += 1
        group[1 if numeric else 2] += 1
        if numeric:
            continue
        reason = str(row.get("refusal_reason") or "untyped")
        detail = row.get("refusal_detail")
        detail_text = json.dumps(
            dict(detail) if isinstance(detail, Mapping) else {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        refusals[(engine, quantity, reason, detail_text)] += 1

    sorted_counts = tuple(
        (engine, quantity, *group)
        for (engine, quantity), group in sorted(counts.items())
    )
    sorted_refusals = tuple(
        (*key, count) for key, count in sorted(refusals.items())
    )
    return Nasa9FitComparisonData(sorted_counts, sorted_refusals)


class _SuspectedCalibrationOverlapThresholds(NamedTuple):
    minimum_rows: int
    median_abs_residual_fraction_of_band: Decimal
    rows_inside_tight_band_fraction: Decimal
    tight_band_fraction: Decimal


# Diagnostic heuristic only (d-445). These cuts are not a physics claim
# and do not change score_eligible. A counted row with no usable band
# means the pair is not flagged.
SUSPECTED_CALIBRATION_OVERLAP = _SuspectedCalibrationOverlapThresholds(
    minimum_rows=10,
    median_abs_residual_fraction_of_band=Decimal("0.10"),
    rows_inside_tight_band_fraction=Decimal("0.80"),
    tight_band_fraction=Decimal("0.10"),
)


class EligibleSourceRelationCount(NamedTuple):
    """Eligible independent and unknown rows for one engine."""

    engine: str
    n_independent: int
    n_unknown: int


class SuspectedCalibrationOverlap(NamedTuple):
    """One (reference source, engine) pair the diagnostic flagged."""

    source_id: str
    engine: str
    n: int
    median_abs_residual: Decimal
    median_band_half_width: Decimal
    fraction_inside_tight_band: Decimal


def _decimal_median(values: Sequence[Decimal]) -> Decimal:
    """Median of a nonempty decimal sequence.

    Same odd/even rule as ``score._median``. This copy stays here because
    ``score`` imports this module; importing ``score._median`` would cycle.
    """

    ordered = sorted(values)
    count = len(ordered)
    mid = count // 2
    if count % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal(2)


def _source_relation_token(row: Mapping[str, object]) -> str:
    relation = row.get("source_relation")
    if isinstance(relation, SourceRelation):
        return relation.value
    if relation is None:
        return ""
    return str(relation)


def _finite_decimal(raw: object) -> Decimal | None:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except (ArithmeticError, TypeError, ValueError):
        return None
    if not value.is_finite():
        return None
    return value


def eligible_source_relation_breakdown(
    rows: Iterable[Mapping[str, object]],
    *,
    engine_for_row: Callable[[Mapping[str, object]], str],
) -> tuple[EligibleSourceRelationCount, ...]:
    """Count score_eligible rows by engine and source_relation.

    Only ``independent`` and ``unknown`` are columns. A score_eligible
    row with any other relation is omitted, not folded into either count.
    Does not change eligibility.
    """

    independent: dict[str, int] = defaultdict(int)
    unknown: dict[str, int] = defaultdict(int)
    for row in rows:
        engine = engine_for_row(row)
        if row.get("score_eligible") is not True:
            continue
        token = _source_relation_token(row)
        if token == SourceRelation.INDEPENDENT.value:
            independent[engine] += 1
        elif token == SourceRelation.UNKNOWN.value:
            unknown[engine] += 1
    engines = sorted(set(independent) | set(unknown))
    return tuple(
        EligibleSourceRelationCount(
            engine,
            independent[engine],
            unknown[engine],
        )
        for engine in engines
    )


def suspected_calibration_overlaps(
    rows: Iterable[Mapping[str, object]],
    observations: Mapping[str, Observation] | None,
    *,
    engine_for_row: Callable[[Mapping[str, object]], str],
) -> tuple[SuspectedCalibrationOverlap, ...]:
    """Flag implausibly tight eligible pairs. Diagnostic only.

    A pair is one reference ``source_id`` and one engine with at least
    ``SUSPECTED_CALIBRATION_OVERLAP.minimum_rows`` eligible numeric
    residuals. It is flagged when the median absolute residual is below
    the named fraction of the median decision-band half-width and at
    least the named fraction of rows sit inside that fraction of their
    own band. ``DecisionBand.value`` is the half-width. If any counted
    row lacks a positive band, or the residual and band units disagree,
    the pair is skipped. Does not write notices or change eligibility.
    """

    if observations is None:
        return ()
    from simulator.battery.compilation_tier import reference_observation

    thresholds = SUSPECTED_CALIBRATION_OVERLAP
    grouped: dict[tuple[str, str], list[tuple[Decimal, str, Decimal | None, str | None]]] = defaultdict(list)
    for row in rows:
        if row.get("score_eligible") is not True:
            continue
        numeric = row.get("numeric")
        if not isinstance(numeric, Mapping):
            continue
        value = _finite_decimal(numeric.get("value"))
        if value is None:
            continue
        observation = reference_observation(
            observations, str(row.get("reference") or "")
        )
        source_id = "" if observation is None else str(observation.source_id or "")
        if not source_id:
            continue
        unit = numeric.get("unit")
        residual_unit = "" if unit is None else str(unit)
        band = numeric.get("decision_band")
        band_value: Decimal | None = None
        band_unit: str | None = None
        if isinstance(band, Mapping):
            band_value = _finite_decimal(band.get("value"))
            raw_band_unit = band.get("unit")
            band_unit = None if raw_band_unit is None else str(raw_band_unit)
        grouped[(source_id, engine_for_row(row))].append(
            (value, residual_unit, band_value, band_unit)
        )

    flagged: list[SuspectedCalibrationOverlap] = []
    for (source_id, engine), samples in grouped.items():
        if len(samples) < thresholds.minimum_rows:
            continue
        residual_units = {sample[1] for sample in samples}
        if len(residual_units) != 1:
            continue
        residual_unit = next(iter(residual_units))
        usable = True
        abs_values: list[Decimal] = []
        band_values: list[Decimal] = []
        for value, _unit, band_value, band_unit in samples:
            if (
                band_value is None
                or band_value <= 0
                or band_unit != residual_unit
                or not residual_unit
            ):
                usable = False
                break
            abs_values.append(abs(value))
            band_values.append(band_value)
        if not usable:
            continue
        median_abs = _decimal_median(abs_values)
        median_band = _decimal_median(band_values)
        tight = thresholds.tight_band_fraction
        inside = sum(
            1
            for abs_value, band_value in zip(abs_values, band_values)
            if abs_value < tight * band_value
        )
        fraction = Decimal(inside) / Decimal(len(samples))
        if (
            median_abs
            < thresholds.median_abs_residual_fraction_of_band * median_band
            and fraction >= thresholds.rows_inside_tight_band_fraction
        ):
            flagged.append(
                SuspectedCalibrationOverlap(
                    source_id,
                    engine,
                    len(samples),
                    median_abs,
                    median_band,
                    fraction,
                )
            )
    return tuple(sorted(flagged, key=lambda row: (row.source_id, row.engine)))
