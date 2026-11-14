"""Derived facts and aggregates consumed by battery score report renderers."""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from typing import Iterable, Mapping, NamedTuple

from simulator.battery.enums import Quantity, ValueKind
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
