"""Shared collection of trust metadata presented by optimizer results."""

from __future__ import annotations

from simulator.chemistry.kernel import select_backend_status

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from simulator.backend_names import canonical_backend_name


@dataclass(frozen=True)
class ResultTrustCarriers:
    carriers: tuple[Any, ...]
    backend_names: tuple[str, ...]
    backend_statuses: tuple[str, ...]
    backend_authorities: tuple[bool, ...]
    evidence_classes: tuple[str, ...]
    certification_allowances: tuple[bool, ...]
    inherited_evidence_requirements: tuple[bool, ...]

    @property
    def disagreement_rejections(self) -> tuple[str, ...]:
        fields = (
            ("backend_name", self.backend_names),
            ("evidence_class", self.evidence_classes),
            ("backend_status", self.backend_statuses),
            ("backend_authoritative", self.backend_authorities),
        )
        reasons: list[str] = []
        for field, values in fields:
            distinct = tuple(dict.fromkeys(values))
            if len(distinct) > 1:
                reasons.append(
                    f"{field}_carrier_disagreement:"
                    + "|".join(repr(value) for value in sorted(distinct))
                )
        return tuple(reasons)


def collect_result_trust_carriers(result: Any) -> ResultTrustCarriers:
    carriers = result_trust_carriers(result)
    backend_names: list[str] = []
    backend_statuses: list[str] = []
    backend_authorities: list[bool] = []
    evidence_classes: list[str] = []
    certification_allowances: list[bool] = []
    inherited_evidence_requirements: list[bool] = []
    for carrier in carriers:
        raw_name = carrier_value(carrier, "backend_name")
        if raw_name is not None:
            canonical = canonical_backend_name(str(raw_name).strip())
            if canonical is not None:
                backend_names.append(canonical)
        status = carrier_backend_status(carrier)
        if status is not None:
            backend_statuses.append(str(status).strip())
        raw_authority = carrier_value(carrier, "backend_authoritative")
        if raw_authority is not None:
            backend_authorities.append(strict_bool(raw_authority))
        raw_evidence = carrier_value(carrier, "evidence_class")
        if raw_evidence is not None:
            evidence_classes.append(str(raw_evidence).strip())
        raw_certification = carrier_value(carrier, "certification_allowed")
        if raw_certification is not None:
            certification_allowances.append(strict_bool(raw_certification))
        raw_inherited = carrier_value(
            carrier,
            "requires_inherited_evidence_class",
        )
        if raw_inherited is not None:
            inherited_evidence_requirements.append(strict_bool(raw_inherited))
    return ResultTrustCarriers(
        carriers=carriers,
        backend_names=tuple(backend_names),
        backend_statuses=tuple(backend_statuses),
        backend_authorities=tuple(backend_authorities),
        evidence_classes=tuple(evidence_classes),
        certification_allowances=tuple(certification_allowances),
        inherited_evidence_requirements=tuple(inherited_evidence_requirements),
    )


def result_trust_carriers(result: Any) -> tuple[Any, ...]:
    carriers: list[Any] = []
    run_reference = getattr(result, "run_reference", None)
    if run_reference is not None:
        carriers.extend(
            (
                run_reference,
                getattr(run_reference, "trace", None),
                getattr(run_reference, "product_summary", None),
            )
        )
    if hasattr(result, "result_blob"):
        carriers.append(getattr(result, "result_blob"))
    return tuple(carrier for carrier in carriers if carrier is not None)


def compact_result_trust_carrier(carrier: Any) -> dict[str, Any]:
    if carrier is None:
        return {}
    compact: dict[str, Any] = {}
    for key in (
        "backend_name",
        "backend_authoritative",
        "evidence_class",
        "certification_allowed",
        "requires_inherited_evidence_class",
    ):
        value = carrier_value(carrier, key)
        if value is not None:
            compact[key] = value
    status = carrier_backend_status(carrier)
    if status is not None:
        compact["backend_status"] = status
    return compact


def carrier_value(carrier: Any, key: str) -> Any:
    if carrier is None:
        return None
    if isinstance(carrier, Mapping):
        return carrier.get(key)
    return getattr(carrier, key, None)


def carrier_backend_status(carrier: Any) -> str | None:
    if carrier is None:
        return None
    raw = carrier_value(carrier, "backend_status")
    if raw is not None:
        return str(raw)
    for key in ("per_hour", "hours"):
        nested = carrier_value(carrier, key)
        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)) and nested:
            # ★ THE MOST SEVERE HOUR, NOT THE LAST ONE. This read nested[-1],
            # so a run that refused at hour 3 and recovered to ok at hour 24
            # reported ok -- position decided, which is the defect the kernel
            # owner was corrected for. It matters most here of all: this module
            # decides whether a result may be TRUSTED, so the hour that failed
            # is exactly the hour that must survive the reduction.
            #
            # Answering by position also restates the ordering WITHOUT naming
            # a token, which is why the AST ownership guard cannot see sites
            # like this one; the behavioural agreement test in
            # tests/test_backend_status_owner.py is what covers them.
            statuses = [
                carrier_backend_status(entry)
                for entry in nested
            ]
            status = select_backend_status(
                [value for value in statuses if value is not None]
            )
            if status is not None:
                return status
    for key in ("trace", "backend_diagnostics", "diagnostics"):
        status = carrier_backend_status(carrier_value(carrier, key))
        if status is not None:
            return status
    return None


def strict_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return bool(value) if value in (0, 1) else False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    return False
