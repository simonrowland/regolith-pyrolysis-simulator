"""Run-scoped disclosure of an admitted diagnostic or extrapolated vapour law.

Invariant: a known stored disclosure is re-registered into the owning run
registry on every physics cache hit, and an incomplete disclosure record is
unknown. ``open_computation`` raises before the solve unless a durable run
registry is already bound. The session owns that registry. A simulator that
is itself the run keeps one on the instance via ``bind_simulation_run``; the
snapshot is merged into it and published on ``law_disclosures``. Hit and miss
share that bind. Re-registration of a known disclosure binds the owner when
no registry is active, so a hit cannot return the value and drop the flag.
A stored disclosure is known only when every public field is present and
typed. One missing or ill-typed field makes the whole record unknown, never
a measured row with empty authority.

- New rows are written only by ``register_executed_law``. Compiled laws
  call it from ``CompiledPressureEvaluator.evaluate``. A published
  continuation that never calls evaluate() — the provider's direct Antoine
  expression, including out-of-band SiO — calls it once at publication.
  A diagnostic admission registers from the evaluator notice. Any other
  continuation registers once when the evaluation is outside a certified
  domain: temperature ``out_of_range``, or a carrier notice whose
  ``authority_level`` is ``extrapolated`` (oxygen evaluator band and the
  oxygen mass-action floor). Temperature alone does not define the domain.
- A cache hit does not execute the law. It re-registers the disclosure stored
  with that computation. Counts from two computations add.
- Disclosure is metadata on the cached row, not a cache-key dimension.
- A row with no disclosure field is unknown. Callers treat that as a miss or
  as unusable for ranking. Absence is never a measured "no flagged law".
- ``accept_stored_memo`` is the only read of a disclosure-bearing cache.
  The PT0 store, the persistent equilibrium row, an interpolated hit, the
  optimizer result store, a journal replay, the freeze-gate memos, and a
  residue pressure-cache row all re-register through it. A new reader that
  calls ``reregister_cached_disclosures`` itself is a bug. The raw PT0 dict
  is ``_entries``. ``replay_gate_curve`` leaves the accepted list on
  ``_accepted_hit_disclosures`` so the replay record stores that measurement
  without counting the same read twice.
- A computation scope records only that computation, then adds its counts to
  the parent. Cache writes read the computation, never the whole run.
- Typed carrier notices stay lists (exact duplicates dropped). Bare-float
  helpers do not carry notices.
- A publisher reads the owning run via ``owning_run_disclosures``. The
  bound registry is another run or a computation sub-registry and does not
  replace that owner's snapshot, including a measured empty.

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
import math
from typing import Any

_ACTIVE: ContextVar[RunDisclosure | None] = ContextVar(
    "vapour_law_run_disclosure",
    default=None,
)

# Certification route for the Fe pseudo-law closer. Not part of the per-quantity
# notice: d-383 replaces the constant-dH sidecar and d-381 unifies Fe/FeO(g).
_T1723_CERTIFICATION_ROUTE = ("d-383", "d-381")
_GAP_KEYS = ("gap_dex_law_vs_rail", "gap_dex_law_vs_vaporock")
# Public shape of LawDisclosure.to_public. A stored row that omits any of
# these is unknown: re-registration must not fill the gap with "".
_DISCLOSURE_FIELDS = (
    "law_id",
    "provenance",
    "authority",
    "certified_band_K",
    "anchor_gaps_dex",
    "closer",
    "certification_route",
    "evaluation_count",
)


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
            # counts as one execution of that law. Domain extrapolation does
            # not add a second row for a law that is already diagnostic.
            self._register_notice(evaluator, notices[0])
            return
        if evaluation is not None and _evaluation_is_extrapolated(evaluation):
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
        self.entries[law_id] = LawDisclosure(
            law_id=law_id,
            provenance=_extrapolated_provenance(evaluation),
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


def _domain_extrapolation_notice(evaluation: Any) -> Mapping[str, Any] | None:
    """First carrier notice that admits an extrapolated continuation.

    Temperature ``out_of_range`` is only the thermal class. Oxygen-band and
    mass-action-floor continuations arrive as notices with
    ``authority_level == "extrapolated"``. Naming one notice key here would
    drop the next domain.
    """

    from simulator.vapour_rail.instrumentation import public_carrier_notices

    for items in public_carrier_notices(evaluation).values():
        for item in items:
            if item.get("authority_level") == "extrapolated":
                return item
    return None


def _evaluation_is_extrapolated(evaluation: Any) -> bool:
    if bool(getattr(evaluation, "out_of_range", False)):
        return True
    return _domain_extrapolation_notice(evaluation) is not None


def _extrapolated_provenance(evaluation: Any) -> str:
    """Thermal status and the domain reason, as one execution.

    A temperature continuation keeps ``out_of_range_conservative_continuation``.
    An in-temperature oxygen continuation keeps the notice reason
    (``pO2_outside_evaluator_band`` or the mass-action floor). Both domains
    on one evaluation stay one row: the thermal status, then the reason.
    """

    status = getattr(evaluation, "status", None)
    status_text = str(status).strip() if status else ""
    notice = _domain_extrapolation_notice(evaluation)
    reason = ""
    if isinstance(notice, Mapping):
        reason = str(notice.get("reason") or "").strip()
    if status_text and reason and reason not in status_text:
        return f"{status_text}; {reason}"
    return status_text or reason or "out_of_range"


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


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _well_formed_disclosure(item: Any) -> dict[str, Any] | None:
    """A complete disclosure row, or None when any required field is missing.

    Required fields are the public registry record: law id, provenance,
    authority, certified band, anchor gaps, closer, certification route, and
    evaluation count. Authority and provenance must be non-empty text.
    Closer, the gap map, and the certification route may be empty. The band
    may be null. A law id alone is not a known disclosure: re-registration
    would publish an empty authority.
    """

    if not isinstance(item, Mapping) or isinstance(item, (str, bytes)):
        return None
    if any(key not in item for key in _DISCLOSURE_FIELDS):
        return None
    law_id = item.get("law_id")
    provenance = item.get("provenance")
    authority = item.get("authority")
    closer = item.get("closer")
    if (
        not isinstance(law_id, str)
        or not law_id
        or not isinstance(provenance, str)
        or not provenance
        or not isinstance(authority, str)
        or not authority
        or not isinstance(closer, str)
    ):
        return None
    band = item.get("certified_band_K")
    certified: list[float] | None
    if band is None:
        certified = None
    elif (
        isinstance(band, (list, tuple))
        and not isinstance(band, (str, bytes))
        and len(band) == 2
    ):
        lo = _finite_number(band[0])
        hi = _finite_number(band[1])
        if lo is None or hi is None:
            return None
        certified = [lo, hi]
    else:
        return None
    raw_gaps = item.get("anchor_gaps_dex")
    if not isinstance(raw_gaps, Mapping) or isinstance(raw_gaps, (str, bytes)):
        return None
    gaps: dict[str, float] = {}
    for key, value in raw_gaps.items():
        if not isinstance(key, str) or not key:
            return None
        gap = _finite_number(value)
        if gap is None:
            return None
        gaps[key] = gap
    route = item.get("certification_route")
    if isinstance(route, (str, bytes)) or not isinstance(route, (list, tuple)):
        return None
    if not all(isinstance(part, str) for part in route):
        return None
    count = item.get("evaluation_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        return None
    return {
        "law_id": law_id,
        "provenance": provenance,
        "authority": authority,
        "certified_band_K": certified,
        "anchor_gaps_dex": gaps,
        "closer": closer,
        "certification_route": list(route),
        "evaluation_count": count,
    }


def reregister_cached_disclosures(
    disclosures: Any,
    *,
    owner: Any = None,
) -> None:
    """Re-register disclosures stored beside a cached value. Does not execute.

    A known disclosure is never dropped. An active run registry receives the
    counts. With no registry, ``owner`` is bound for this hit and the snapshot
    is published on ``owner.law_disclosures``. Malformed rows are not
    registered. The caller must have treated them as a miss; registering the
    well-formed subset would hide the bad entry.

    Result-object carriers (an optimizer row, a journal payload) omit
    ``owner``. Their disclosure stays on the object. There is no run to
    publish onto, so a missing registry is not a dropped simulator flag.
    """

    if disclosures is None:
        return
    measured = parse_stored_disclosure_field(disclosures, present=True)
    if not measured:
        if measured is not None and owner is not None and _ACTIVE.get() is None:
            # Measured zero. Publish it on the owner so this hit does not
            # leave the durable disclosure unset.
            with bind_simulation_run(owner):
                return
        return
    current = _ACTIVE.get()
    if current is None and owner is not None:
        with bind_simulation_run(owner):
            bound = _ACTIVE.get()
            if bound is not None:
                bound.reregister_public(measured)
        return
    if current is None:
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
    *,
    owner: Any = None,
) -> list[dict[str, Any]] | None:
    """Re-register one memo hit. ``None`` when the memo never recorded a field.

    A present empty list is a measured zero and is re-registered as such.
    Absence stays unknown: the caller must not treat it as unflagged.
    ``owner`` is the run that must publish the disclosure when no registry
    is already active.
    """

    if not isinstance(record, Mapping) or "law_disclosures" not in record:
        return None
    raw = record.get("law_disclosures")
    measured = parse_stored_disclosure_field(raw, present=raw is not None)
    if measured is None:
        return None
    reregister_cached_disclosures(measured, owner=owner)
    return [dict(item) for item in measured]


def accept_stored_memo(
    record: Mapping[str, Any] | None,
    *,
    owner: Any = None,
) -> list[dict[str, Any]] | None:
    """Re-register one stored memo, or refuse it as unknown.

    This is the only gate a freeze-gate memo return may use. The same-hour
    held curve, the local slot, the shared slot, and the core liquidus
    reader (its local cache and its curve memo) all call it. A hit is the
    list it returns. ``None`` means the row never recorded a readable
    disclosure: the caller misses and recomputes. A list, including ``[]``,
    is a known measurement and has already been re-registered onto the
    active run or ``owner``. Absence is never an empty disclosure.
    """

    if not stored_disclosures_known(record):
        return None
    return publish_stored_disclosures(record, owner=owner)


def owning_run_disclosures(owner: Any) -> tuple[dict[str, Any], ...]:
    """Disclosures of the run that owns ``owner``, including a measured empty.

    The context variable is not this owner. Another run's registry, and a
    computation sub-registry, are not the owner's accumulated disclosure.
    A registry stored on the owner wins, including when its snapshot is
    empty. A ``law_disclosures`` attribute with no registry is the same
    measurement, empty or not. Absence of both is a measured empty. Neither
    case is filled from whatever registry happens to be bound.
    """

    if owner is None:
        return ()
    for attr in ("_law_disclosure_registry", "_law_disclosure"):
        registry = getattr(owner, attr, None)
        if isinstance(registry, RunDisclosure) and not registry.computation:
            return registry.snapshot()
    if not hasattr(owner, "law_disclosures"):
        return ()
    raw = getattr(owner, "law_disclosures")
    if not raw:
        return ()
    return tuple(dict(item) for item in raw if isinstance(item, Mapping))


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
