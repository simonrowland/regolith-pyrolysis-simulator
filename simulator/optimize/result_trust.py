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


def carrier_worst_backend_status(carrier: Any) -> str | None:
    """The worst status anywhere in this carrier's subtree, ranked not positional.

    ★ THIS IS A DIFFERENT QUESTION FROM `carrier_backend_status`, and conflating
    them is a live trap -- I merged them and broke a real gate before catching it
    (2026-08-31).

        carrier_backend_status      -> "what does THIS carrier claim?"
        carrier_worst_backend_status-> "what is the worst thing in this SUBTREE?"

    `carrier_backend_status` answers by first branch that holds anything, and
    the result store DEPENDS on that: it asks each carrier what it individually
    claims and rejects when a reference and its own trace disagree
    (`backend_status_carrier_disagreement:'ok'|'unavailable'`). Making that
    accessor absorb its children's statuses makes the reference and its trace
    agree, and the internal contradiction the gate exists to catch becomes
    invisible. Verified: doing so turns
    tests/test_optimizer_results_store.py::
    test_store_rejects_authority_and_status_carrier_disagreement_without_masking
    from passing to failing.

    But a TRUST decision genuinely needs the other question. A carrier with
    `{per_hour: [ok], backend_diagnostics: unavailable}` answers ok to the
    own-claim accessor while the canonical collector
    (`backend_statuses_from_carrier` + `select_backend_status`) answers
    unavailable -- measured 2026-08-31, FIVE distinct branch pairs leak a
    flattering status that way. Reported as finding 2 of the m47 subsystem audit.

    So: two questions, two functions, and each caller picks. This one collects
    from every branch and ranks the union exactly once, delegating the ordering
    to `select_backend_status` so no second precedence is ever encoded here.

    Ranking, not position, in the flattering-vs-degrading direction: the failure
    the run actually had is the one that must survive the reduction.
    """
    if carrier is None:
        return None
    statuses: list[str] = []

    raw = carrier_value(carrier, "backend_status")
    if raw is not None:
        statuses.append(str(raw))

    for key in ("per_hour", "hours"):
        nested = carrier_value(carrier, key)
        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)) and nested:
            statuses.extend(
                value
                for value in (carrier_worst_backend_status(entry) for entry in nested)
                if value is not None
            )

    for key in ("trace", "backend_diagnostics", "diagnostics"):
        status = carrier_worst_backend_status(carrier_value(carrier, key))
        if status is not None:
            statuses.append(status)

    if not statuses:
        return None
    return select_backend_status(statuses)


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


# Certifying evidence classes that name a real engine. internal-datatables is
# a class-level certificate with no engine adapter, so it is not in this set.
REAL_ENGINE_EVIDENCE_CLASSES = frozenset({"melts", "magemin"})
# Versions the adapters write when they did not obtain a producer identity.
# MeltBackend.get_engine_version returns 'unavailable'; some call sites use
# 'unknown'. Neither is evidence that the named engine produced the row.
_PRODUCER_STAMP_SENTINELS = frozenset({"", "unavailable", "unknown"})
_STAMP_NEST_KEYS = ("diagnostics", "backend_diagnostics")
_STAMP_HOUR_KEYS = ("per_hour", "hours")


def producer_stamp_is_sentinel(version: str) -> bool:
    return version.strip().lower() in _PRODUCER_STAMP_SENTINELS


def real_engine_producer_stamp_rejections(
    trust: ResultTrustCarriers,
    *,
    evidence_class: str | None,
    claimed_backend: str | None,
) -> tuple[str, ...]:
    """Refuse a certifying real-engine claim that is not bound to a producer stamp.

    The stamp is the pair the engine adapter writes on one mapping:
    ``backend_name`` plus a non-sentinel ``engine_version``. A matching pair
    of caller-supplied authority fields is not that stamp. This is not a
    signature; a caller who forges both fields still matches the adapter shape.
    """
    if evidence_class not in REAL_ENGINE_EVIDENCE_CLASSES:
        return ()
    stamps = _producer_stamp_records_from_carriers(trust.carriers)
    if not stamps:
        return ("real_engine_producer_stamp_missing",)
    non_sentinel = [
        (backend, version)
        for backend, version in stamps
        if not producer_stamp_is_sentinel(version)
    ]
    if not non_sentinel:
        return ("real_engine_producer_stamp_unbound",)
    claimed = (
        canonical_backend_name(claimed_backend) if claimed_backend is not None else None
    )
    if claimed is None:
        return ("real_engine_producer_stamp_unbound",)
    bound: list[str] = []
    foreign: list[str] = []
    for backend, version in non_sentinel:
        if not backend:
            continue
        if canonical_backend_name(backend) == claimed:
            bound.append(version)
        else:
            foreign.append(backend)
    if bound:
        distinct = tuple(dict.fromkeys(bound))
        if len(distinct) == 1:
            return ()
        return (
            "real_engine_producer_stamp_disagreement:"
            + "|".join(sorted(distinct)),
        )
    if foreign:
        return (
            "real_engine_producer_stamp_disagreement:"
            + "|".join(sorted(set(foreign))),
        )
    return ("real_engine_producer_stamp_unbound",)


def real_producer_stamp_pair(carrier: Any) -> dict[str, str] | None:
    """The first non-sentinel same-mapping stamp on this carrier, if any."""
    for backend, version in _producer_stamp_records(carrier):
        if backend and not producer_stamp_is_sentinel(version):
            return {"backend_name": backend, "engine_version": version}
    return None


def attach_producer_engine_stamp(
    payload: dict[str, Any],
    stamp: Mapping[str, str],
) -> None:
    """Record an adapter stamp on ``payload`` without binding a foreign engine.

    The diagnostics pair is always merged. The top-level ``engine_version``
    is set only when the stamp's backend is the payload's backend, so a
    version from a different engine is not stored beside the claim name.
    """
    backend_name = str(stamp["backend_name"])
    engine_version = str(stamp["engine_version"])
    current = payload.get("diagnostics")
    diagnostics = dict(current) if isinstance(current, Mapping) else {}
    diagnostics.setdefault("backend_name", backend_name)
    diagnostics.setdefault("engine_version", engine_version)
    payload["diagnostics"] = diagnostics
    claimed = payload.get("backend_name")
    claimed_name = (
        canonical_backend_name(str(claimed)) if isinstance(claimed, str) else None
    )
    stamp_name = canonical_backend_name(backend_name)
    if claimed_name is None or stamp_name == claimed_name:
        if claimed_name is None:
            payload.setdefault("backend_name", backend_name)
        payload.setdefault("engine_version", engine_version)


def producer_engine_stamp_from_execution(run_execution: Any) -> dict[str, str] | None:
    """The stamp the adapter wrote, else the live backend's own version call.

    A diagnostics mapping that already carries ``engine_version`` is the
    adapter's answer, including a sentinel. Do not replace that sentinel by
    calling ``get_engine_version`` again. The fallback exists for adapters
    that never write the pair (MAGEMin): one call per trace build, using
    ``backend.name`` when ``backend_name`` is still the base ``unknown``.
    """
    simulator = getattr(run_execution, "simulator", None)
    diagnostics = getattr(simulator, "_last_backend_diagnostics", None)
    if isinstance(diagnostics, Mapping) and "engine_version" in diagnostics:
        return real_producer_stamp_pair(diagnostics)
    backend = getattr(simulator, "backend", None)
    if backend is None:
        return None
    getter = getattr(backend, "get_engine_version", None)
    if not callable(getter):
        return None
    version = getter()
    if not isinstance(version, str) or producer_stamp_is_sentinel(version.strip()):
        return None
    raw_name = getattr(backend, "backend_name", None)
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    if name.lower() in {"", "unknown"}:
        raw_class_name = getattr(backend, "name", None)
        name = raw_class_name.strip() if isinstance(raw_class_name, str) else ""
    if not name or name.lower() == "unknown":
        return None
    return {"backend_name": name, "engine_version": version.strip()}


def _producer_stamp_records_from_carriers(
    carriers: tuple[Any, ...],
) -> tuple[tuple[str | None, str], ...]:
    records: list[tuple[str | None, str]] = []
    for carrier in carriers:
        records.extend(_producer_stamp_records(carrier))
    return tuple(records)


def _producer_stamp_records(carrier: Any) -> tuple[tuple[str | None, str], ...]:
    """Stamps on this carrier, one level into diagnostics and per-hour entries.

    Nested ``trace`` is not walked. The trace is already its own carrier, and
    walking it again would let a reference and its trace count as two stamps.
    """
    if not isinstance(carrier, Mapping):
        return ()
    records: list[tuple[str | None, str]] = []
    own = _stamp_record(carrier)
    if own is not None:
        records.append(own)
    for key in _STAMP_NEST_KEYS:
        nested = carrier.get(key)
        if isinstance(nested, Mapping):
            nested_record = _stamp_record(nested)
            if nested_record is not None:
                records.append(nested_record)
    for key in _STAMP_HOUR_KEYS:
        entries = carrier.get(key)
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            entry_record = _stamp_record(entry)
            if entry_record is not None:
                records.append(entry_record)
            for nest_key in _STAMP_NEST_KEYS:
                nested = entry.get(nest_key)
                if isinstance(nested, Mapping):
                    nested_record = _stamp_record(nested)
                    if nested_record is not None:
                        records.append(nested_record)
    return tuple(records)


def _stamp_record(mapping: Mapping[Any, Any]) -> tuple[str | None, str] | None:
    if "engine_version" not in mapping:
        return None
    raw_version = mapping.get("engine_version")
    version = raw_version.strip() if isinstance(raw_version, str) else ""
    raw_backend = mapping.get("backend_name")
    backend = (
        raw_backend.strip()
        if isinstance(raw_backend, str) and raw_backend.strip()
        else None
    )
    return (backend, version)
