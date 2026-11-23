"""Derived facts and aggregates consumed by battery score report renderers."""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from typing import Callable, Iterable, Mapping, NamedTuple

from simulator.battery.enums import (
    DEFAULT_POLICY_NOTICE_KINDS,
    Quantity,
    ResidualStatus,
    ValueKind,
)
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


class DefaultPolicyCensus(NamedTuple):
    """Numeric flagged residual counts grouped by policy id and status."""

    rows: tuple[tuple[str, str, int], ...]


def default_policy_census(
    rows: Iterable[Residual | Mapping[str, object]],
) -> DefaultPolicyCensus:
    """Count numeric residuals carrying a typed default-policy notice."""

    counts: dict[tuple[str, str], int] = defaultdict(int)
    known_flags = {kind.value for kind in DEFAULT_POLICY_NOTICE_KINDS}
    required_fields = {"id", "version", "status", "value", "units"}
    for row in rows:
        if isinstance(row, Residual):
            if row.numeric is None or row.status is ResidualStatus.REFUSED:
                continue
            notices = row.notices
        else:
            numeric = row.get("numeric")
            if (
                not isinstance(numeric, Mapping)
                or row.get("status") == ResidualStatus.REFUSED.value
            ):
                continue
            notices = row.get("notices") or ()

        policies: set[tuple[str, str]] = set()
        for notice in notices:
            if isinstance(row, Residual):
                policy = notice.policy
                kind = notice.kind.value
            elif isinstance(notice, Mapping):
                policy = notice.get("policy")
                kind = str(notice.get("kind") or "")
            else:
                continue
            if policy is None:
                continue
            if not isinstance(policy, Mapping) or set(policy) != required_fields:
                raise ValueError("default-policy notice metadata is malformed")
            policy_id, policy_status = policy.get("id"), policy.get("status")
            if kind not in known_flags:
                raise ValueError("default-policy notice kind is not a table flag")
            if not isinstance(policy_id, str) or not policy_id.startswith("DP-"):
                raise ValueError("default-policy notice id must be a DP id")
            if policy_status not in {"canonical", "proposed", "rejected"}:
                raise ValueError("default-policy notice status is invalid")
            policy_version = policy.get("version")
            if not isinstance(policy_version, (int, str)) or not str(policy_version):
                raise ValueError("default-policy notice version is required")
            if (
                not isinstance(policy.get("units"), str)
                or not policy.get("units")
                or policy.get("value") is None
            ):
                raise ValueError("default-policy notice value and units are required")
            policies.add((policy_id, str(policy_status)))
        for policy in policies:
            counts[policy] += 1

    return DefaultPolicyCensus(
        tuple(
            (policy_id, status, count)
            for (policy_id, status), count in sorted(counts.items())
        )
    )


class AssumedBandReport:
    """Additive d-443 diagnostics; never consumes or modifies headline stats."""

    def __init__(self) -> None:
        self.groups: dict[tuple[str, ...], dict[str, object]] = {}
        self.observations: dict[tuple[str, ...], set[str]] = defaultdict(set)
        self.refusals: dict[str, int] = defaultdict(int)

    def add(self, row: Mapping[str, object], *, engine: str, quantity: str) -> None:
        details = row.get("assumed_band_details")
        if not isinstance(details, Mapping):
            return
        refusal = details.get("refusal")
        if isinstance(refusal, Mapping):
            self.refusals[str(refusal["reason"])] += 1
            return
        band, numeric = details.get("band"), row.get("numeric")
        if row.get("assumed_band") is not True or not isinstance(band, Mapping) or not isinstance(numeric, Mapping):
            return
        key = (
            str(row.get("rail") or ""), engine, quantity,
            str(numeric["operation"]), str(numeric["unit"]), str(details["class_id"]),
            str(details.get("evidence_class") or "unknown"),
        )
        group = self.groups.setdefault(key, {
            "stratum": "assumed_band", "rail": key[0], "engine": engine,
            "quantity": quantity, "operation": key[3], "unit": key[4],
            "class_id": key[5], "citation": details["citation"],
            "evidence_class": key[6],
            "precision": details["precision"], "n": 0, "n_inside_band": 0,
            "n_eligible": 0, "n_eligible_inside_band": 0,
        })
        inside, eligible = details["inside_band"] is True, details.get("eligible") is True
        group["n"] += 1
        group["n_inside_band"] += int(inside)
        group["n_eligible"] += int(eligible)
        group["n_eligible_inside_band"] += int(eligible and inside)
        self.observations[key].add(str(row["reference"]))

    def payload(self) -> dict[str, object]:
        return {
            "label": "assumed band — literature method precision; confidence not stated",
            "records": [
                {**group, "n_observations": len(self.observations[key]),
                 "n_outside_band": group["n"] - group["n_inside_band"],
                 "match_rate": group["n_inside_band"] / group["n"]}
                for key, group in sorted(self.groups.items())
            ],
            "refusal_counts": dict(sorted(self.refusals.items())),
        }


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
