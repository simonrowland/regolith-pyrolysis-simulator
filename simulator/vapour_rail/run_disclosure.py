"""Run-scoped disclosure of an admitted diagnostic or extrapolated vapour law.

Invariant: a vapour-law computation is never detached. ``open_computation``
raises before the solve unless a durable run registry is already bound. The
session owns that registry. A simulator that is itself the run keeps one on
the instance via ``bind_simulation_run``; the snapshot is merged into it and
published on ``law_disclosures``. A stored disclosure with any malformed entry
is unknown, never a measured empty.

- The only registration site is ``CompiledPressureEvaluator.evaluate``.
- A cache hit does not execute the law. It re-registers the disclosure stored
  with that computation. Counts from two computations add.
- Disclosure is metadata on the cached row, not a cache-key dimension.
- A row with no disclosure field is unknown. Callers treat that as a miss or
  as unusable for ranking. Absence is never a measured "no flagged law".
- A computation scope records only that computation, then adds its counts to
  the parent. Cache writes read the computation, never the whole run.
- Typed carrier notices stay lists (exact duplicates dropped). Bare-float
  helpers do not carry notices.

The ContextVar default is ``None``. That slot is not a registry. ``SimSession``
creates the ``RunDisclosure`` that is returned with the run. There is no
module-level singleton.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
from typing import Any

_ACTIVE: ContextVar[RunDisclosure | None] = ContextVar(
    "vapour_law_run_disclosure",
    default=None,
)

# Certification route for the Fe pseudo-law closer. Not part of the per-quantity
# notice: d-383 replaces the constant-dH sidecar and d-381 unifies Fe/FeO(g).
_T1723_CERTIFICATION_ROUTE = ("d-383", "d-381")
_GAP_KEYS = ("gap_dex_law_vs_rail", "gap_dex_law_vs_vaporock")


@dataclass
class LawDisclosure:
    law_id: str
    provenance: str
    authority: str
    certified_band_K: tuple[float, float] | None
    anchor_gaps_dex: dict[str, float]
    closer: str
    certification_route: tuple[str, ...]
    evaluation_count: int

    def to_public(self) -> dict[str, Any]:
        return {
            "law_id": self.law_id,
            "provenance": self.provenance,
            "authority": self.authority,
            "certified_band_K": (
                None
                if self.certified_band_K is None
                else [
                    float(self.certified_band_K[0]),
                    float(self.certified_band_K[1]),
                ]
            ),
            "anchor_gaps_dex": {
                key: float(value)
                for key, value in self.anchor_gaps_dex.items()
            },
            "closer": self.closer,
            "certification_route": list(self.certification_route),
            "evaluation_count": int(self.evaluation_count),
        }


@dataclass
class RunDisclosure:
    entries: dict[str, LawDisclosure] = field(default_factory=dict)
    # A computation registry records one cached computation. A run registry
    # accumulates those computations. Cache metadata reads only the former.
    computation: bool = False

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.entries[key].to_public() for key in sorted(self.entries))

    def register_from_evaluator(self, evaluator: Any, evaluation: Any = None) -> None:
        notice = getattr(evaluator, "diagnostic_admission_notice", None)
        if isinstance(notice, (list, tuple)) and not isinstance(notice, (str, bytes)):
            notices = [item for item in notice if isinstance(item, Mapping) and item]
        elif isinstance(notice, Mapping) and notice:
            notices = [notice]
        else:
            notices = []
        if notices:
            # One row per law. A second notice on the same evaluator still
            # counts as one execution of that law.
            self._register_notice(evaluator, notices[0])
            return
        if evaluation is not None and bool(getattr(evaluation, "out_of_range", False)):
            self._register_extrapolated(evaluator, evaluation)

    def _register_notice(self, evaluator: Any, notice: Mapping[str, Any]) -> None:
        species = str(
            notice.get("executed_species_id")
            or getattr(evaluator, "species_id", "")
            or ""
        )
        family = str(getattr(evaluator, "evaluator_family", "") or "")
        law_id = f"{species}:{family}" if family else species
        if not law_id:
            law_id = "diagnostic_law"
        existing = self.entries.get(law_id)
        if existing is not None:
            existing.evaluation_count += 1
            return
        self.entries[law_id] = LawDisclosure(
            law_id=law_id,
            provenance=_provenance_text(notice),
            authority=str(notice.get("authority") or ""),
            certified_band_K=_certified_band(evaluator),
            anchor_gaps_dex=_anchor_gaps(notice),
            closer=str(notice.get("closes_with") or ""),
            certification_route=_certification_route(notice),
            evaluation_count=1,
        )

    def _register_extrapolated(self, evaluator: Any, evaluation: Any) -> None:
        """One out-of-band continuation. Authority is extrapolated, not diagnostic."""

        species = str(getattr(evaluator, "species_id", "") or "")
        family = str(getattr(evaluator, "evaluator_family", "") or "")
        law_id = f"{species}:{family}" if family else species
        if not law_id:
            law_id = "extrapolated_law"
        existing = self.entries.get(law_id)
        if existing is not None:
            existing.evaluation_count += 1
            return
        status = getattr(evaluation, "status", None)
        self.entries[law_id] = LawDisclosure(
            law_id=law_id,
            provenance=str(status or "out_of_range"),
            authority="extrapolated",
            certified_band_K=_certified_band(evaluator),
            anchor_gaps_dex={},
            closer="",
            certification_route=(),
            evaluation_count=1,
        )

    def reregister_public(self, disclosures: Sequence[Mapping[str, Any]]) -> None:
        """Install cache metadata. Does not execute a law. Counts add."""

        for item in disclosures:
            law_id = str(item.get("law_id") or "")
            if not law_id:
                continue
            try:
                count = int(item.get("evaluation_count") or 1)
            except (TypeError, ValueError):
                count = 1
            if count < 1:
                count = 1
            existing = self.entries.get(law_id)
            if existing is not None:
                existing.evaluation_count += count
                continue
            band = item.get("certified_band_K")
            certified: tuple[float, float] | None = None
            if isinstance(band, (list, tuple)) and len(band) == 2:
                try:
                    certified = (float(band[0]), float(band[1]))
                except (TypeError, ValueError):
                    certified = None
            gaps: dict[str, float] = {}
            raw_gaps = item.get("anchor_gaps_dex") or {}
            if isinstance(raw_gaps, Mapping):
                for key, value in raw_gaps.items():
                    try:
                        gaps[str(key)] = float(value)
                    except (TypeError, ValueError):
                        continue
            route = item.get("certification_route") or ()
            route_t = (
                tuple(str(part) for part in route)
                if isinstance(route, (list, tuple)) and not isinstance(route, (str, bytes))
                else ()
            )
            self.entries[law_id] = LawDisclosure(
                law_id=law_id,
                provenance=str(item.get("provenance") or ""),
                authority=str(item.get("authority") or ""),
                certified_band_K=certified,
                anchor_gaps_dex=gaps,
                closer=str(item.get("closer") or ""),
                certification_route=route_t,
                evaluation_count=count,
            )


def _provenance_text(notice: Mapping[str, Any]) -> str:
    status = str(notice.get("provenance_status") or notice.get("status") or "")
    law = str(notice.get("law_provenance") or "")
    if status and law:
        return f"{status}: {law}"
    return status or law


def _certified_band(evaluator: Any) -> tuple[float, float] | None:
    band = getattr(evaluator, "valid_temperature_K", None)
    if not isinstance(band, (list, tuple)) or len(band) != 2:
        return None
    try:
        return (float(band[0]), float(band[1]))
    except (TypeError, ValueError):
        return None


def _anchor_gaps(notice: Mapping[str, Any]) -> dict[str, float]:
    comparators = notice.get("comparators")
    source: Mapping[str, Any] = (
        comparators if isinstance(comparators, Mapping) else notice
    )
    gaps: dict[str, float] = {}
    for key in _GAP_KEYS:
        if key not in source and key in notice and source is not notice:
            raw = notice[key]
        elif key in source:
            raw = source[key]
        else:
            continue
        try:
            gaps[key] = float(raw)
        except (TypeError, ValueError):
            continue
    return gaps


def _certification_route(notice: Mapping[str, Any]) -> tuple[str, ...]:
    if str(notice.get("closes_with") or "") == "t-1723":
        return _T1723_CERTIFICATION_ROUTE
    return ()


class LawDisclosureParentError(RuntimeError):
    """A computation was opened with no session-owned run registry."""


def active_registry() -> RunDisclosure | None:
    """The registry bound for this context, or None when no run is bound."""

    return _ACTIVE.get()


def active_snapshot() -> tuple[dict[str, Any], ...]:
    current = _ACTIVE.get()
    if current is None:
        return ()
    return current.snapshot()


@contextmanager
def disclosure_scope(*, inherit: bool = True) -> Iterator[RunDisclosure]:
    """Bind a run-scoped registry. Nested inherit shares the caller's registry."""

    current = _ACTIVE.get()
    if inherit and current is not None:
        yield current
        return
    created = RunDisclosure()
    token = _ACTIVE.set(created)
    try:
        yield created
    finally:
        _ACTIVE.reset(token)


@contextmanager
def bind_run_registry(registry: RunDisclosure) -> Iterator[RunDisclosure]:
    """Bind a session-owned registry. A nested bind of the same object is a no-op."""

    current = _ACTIVE.get()
    if current is registry:
        yield registry
        return
    token = _ACTIVE.set(registry)
    try:
        yield registry
    finally:
        _ACTIVE.reset(token)


def disclosure_delta(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Counts added between two snapshots of one registry.

    Merging this delta into an outer registry adds each law once. Merging the
    whole snapshot on every hour would count the same execution again.
    """

    before_counts: dict[str, int] = {}
    for item in before:
        law_id = str(item.get("law_id") or "")
        if not law_id:
            continue
        try:
            before_counts[law_id] = int(item.get("evaluation_count") or 0)
        except (TypeError, ValueError):
            before_counts[law_id] = 0
    added: list[dict[str, Any]] = []
    for item in after:
        law_id = str(item.get("law_id") or "")
        if not law_id:
            continue
        try:
            after_count = int(item.get("evaluation_count") or 0)
        except (TypeError, ValueError):
            continue
        extra = after_count - before_counts.get(law_id, 0)
        if extra < 1:
            continue
        copied = dict(item)
        copied["evaluation_count"] = extra
        added.append(copied)
    return tuple(added)


def register_executed_law(evaluator: Any, evaluation: Any = None) -> None:
    """Register one execution. No-op without an active scope or a flagged law."""

    current = _ACTIVE.get()
    if current is None:
        return
    current.register_from_evaluator(evaluator, evaluation)


def active_computation_snapshot() -> tuple[dict[str, Any], ...]:
    """Disclosures of the active computation only.

    A run registry is not a computation. Callers that ask outside a
    computation get a measured empty tuple, never the laws already accumulated
    on the run.
    """

    current = _ACTIVE.get()
    if current is None or not current.computation:
        return ()
    return current.snapshot()


@contextmanager
def computation_disclosure() -> Iterator[RunDisclosure]:
    """Record one computation, then add its counts to the parent run.

    A computation with no parent is a programming error. The snapshot is never
    discarded for want of a parent: the call raises before the solve.
    """

    parent = _ACTIVE.get()
    if parent is None:
        raise LawDisclosureParentError(
            "computation disclosure requires the session-owned run registry"
        )
    created = RunDisclosure(computation=True)
    token = _ACTIVE.set(created)
    try:
        yield created
    finally:
        _ACTIVE.reset(token)
        parent.reregister_public(created.snapshot())


def _owner_run_registry(owner: Any) -> RunDisclosure:
    """The durable registry stored on ``owner``, creating it once.

    A computation flag on the stored object is not a run registry. Failing to
    store the registry raises: there is no temporary registry to discard.
    """

    registry = getattr(owner, "_law_disclosure_registry", None)
    if isinstance(registry, RunDisclosure) and not registry.computation:
        return registry
    registry = RunDisclosure()
    try:
        owner._law_disclosure_registry = registry
    except Exception as exc:
        raise LawDisclosureParentError(
            "computation disclosure requires a durable run registry"
        ) from exc
    return registry


@contextmanager
def bind_simulation_run(owner: Any) -> Iterator[RunDisclosure]:
    """Bind the owner's run registry when no session registry is active.

    A session bind already on the context stays the parent. Otherwise the
    owner's registry is the run: it survives this context, and its snapshot is
    published on ``owner.law_disclosures``. Nested calls do not replace it.
    """

    current = _ACTIVE.get()
    if current is not None:
        yield current
        return
    registry = _owner_run_registry(owner)
    token = _ACTIVE.set(registry)
    try:
        yield registry
    finally:
        _ACTIVE.reset(token)
        owner.law_disclosures = registry.snapshot()


@contextmanager
def open_computation() -> Iterator[RunDisclosure]:
    """One production computation. Requires a bound durable run registry.

    No detached registry. A solve that has neither a session nor
    ``bind_simulation_run`` raises before the law executes, and nothing is
    published as an unflagged result.
    """

    if _ACTIVE.get() is None:
        raise LawDisclosureParentError(
            "computation disclosure requires the session-owned run registry"
        )
    with computation_disclosure() as created:
        yield created


def parse_stored_disclosure_field(
    raw: Any,
    *,
    present: bool,
) -> tuple[dict[str, Any], ...] | None:
    """Measured disclosures, or None when the field is absent or unreadable.

    A present empty list is a measured zero. ``None`` means unknown: the
    caller must miss or refuse to rank, and must not treat it as unflagged.
    One malformed entry makes the whole field unknown. Dropping it would
    publish the remaining rows as a measured zero.
    """

    if not present or raw is None:
        return None
    if isinstance(raw, str):
        if not raw:
            return None
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if raw is None:
            return None
    if isinstance(raw, Mapping):
        raw = (raw,)
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        return None
    measured: list[dict[str, Any]] = []
    for item in raw:
        parsed = _well_formed_disclosure(item)
        if parsed is None:
            return None
        measured.append(parsed)
    return tuple(measured)


def _well_formed_disclosure(item: Any) -> dict[str, Any] | None:
    """A disclosure row with a law id, or None when the row is malformed."""

    if not isinstance(item, Mapping) or isinstance(item, (str, bytes)):
        return None
    law_id = item.get("law_id")
    if not isinstance(law_id, str) or not law_id:
        return None
    return dict(item)


def reregister_cached_disclosures(disclosures: Any) -> None:
    """Re-register disclosures stored beside a cached value. Does not execute.

    Malformed rows are not registered. The caller must have treated them as
    a miss; registering the well-formed subset would hide the bad entry.
    """

    current = _ACTIVE.get()
    if current is None or disclosures is None:
        return
    measured = parse_stored_disclosure_field(disclosures, present=True)
    if not measured:
        return
    current.reregister_public(measured)


def stored_disclosures_known(record: Mapping[str, Any] | None) -> bool:
    """True when the row recorded a disclosure field that parses.

    An empty list is known (a measured zero). Absence, unreadable values, and
    any malformed entry are unknown. A hit path must miss on unknown before
    it returns the row.
    """

    if not isinstance(record, Mapping) or "law_disclosures" not in record:
        return False
    raw = record.get("law_disclosures")
    return parse_stored_disclosure_field(raw, present=raw is not None) is not None


def publish_stored_disclosures(
    record: Mapping[str, Any] | None,
) -> list[dict[str, Any]] | None:
    """Re-register one memo hit. ``None`` when the memo never recorded a field.

    A present empty list is a measured zero and is re-registered as such.
    Absence stays unknown: the caller must not treat it as unflagged.
    """

    if not isinstance(record, Mapping) or "law_disclosures" not in record:
        return None
    raw = record.get("law_disclosures")
    measured = parse_stored_disclosure_field(raw, present=raw is not None)
    if measured is None:
        return None
    reregister_cached_disclosures(measured)
    return [dict(item) for item in measured]


def published_run_disclosures(execution: Any) -> tuple[dict[str, Any], ...]:
    """Disclosures of the run that produced ``execution``.

    The session registry is the source. An empty session snapshot is a
    measured zero and is not replaced by a simulator attribute or by whatever
    registry happens to be active. A result with no session falls back to the
    simulator mirror, then to the active registry.
    """

    if execution is None:
        return ()
    session = getattr(execution, "session", None)
    if session is not None and hasattr(session, "law_disclosures"):
        raw = getattr(session, "law_disclosures", ()) or ()
        return tuple(dict(item) for item in raw if isinstance(item, Mapping))
    sim = getattr(execution, "simulator", None)
    raw = getattr(sim, "law_disclosures", ()) if sim is not None else ()
    if raw:
        return tuple(dict(item) for item in raw if isinstance(item, Mapping))
    return tuple(dict(item) for item in active_snapshot())


def disclosure_metadata(
    disclosures: Any,
) -> tuple[dict[str, Any], ...] | None:
    """Measured disclosure rows, or None when the payload is unknown."""

    if disclosures is None:
        return None
    return parse_stored_disclosure_field(disclosures, present=True)
