"""Run-scoped disclosure of an admitted diagnostic or extrapolated vapour law.

Invariant: a published, priced, or ranked quantity discloses every admitted
diagnostic or extrapolated vapour law its own computation executed.

- The only registration site is ``CompiledPressureEvaluator.evaluate``.
- A cache hit does not execute the law. It re-registers the disclosure stored
  with that computation. Counts from two computations add.
- Disclosure is metadata on the cached row, not a cache-key dimension.
- A row with no disclosure field is unknown. Callers treat that as a miss or
  as unusable for ranking. Absence is never a measured "no flagged law".
- A computation scope records only that computation, then merges its counts
  into the parent run. Cache writes read the computation, never the whole run.
- Typed carrier notices stay lists (exact duplicates dropped). Bare-float
  helpers do not carry notices.

The ContextVar default is ``None``. That slot is not a registry. Each
``disclosure_scope()`` creates a ``RunDisclosure`` that is returned with the
result. There is no module-level singleton.
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
    """Record one computation, then add its counts to the parent run."""

    parent = _ACTIVE.get()
    created = RunDisclosure(computation=True)
    token = _ACTIVE.set(created)
    try:
        yield created
    finally:
        _ACTIVE.reset(token)
        if parent is not None:
            parent.reregister_public(created.snapshot())


def parse_stored_disclosure_field(
    raw: Any,
    *,
    present: bool,
) -> tuple[dict[str, Any], ...] | None:
    """Measured disclosures, or None when the field is absent or unreadable.

    A present empty list is a measured zero. ``None`` means unknown: the
    caller must miss or refuse to rank, and must not treat it as unflagged.
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
        if isinstance(item, Mapping) and item.get("law_id"):
            measured.append(dict(item))
    return tuple(measured)


def reregister_cached_disclosures(disclosures: Any) -> None:
    """Re-register disclosures stored beside a cached value. Does not execute."""

    current = _ACTIVE.get()
    if current is None or disclosures is None:
        return
    if isinstance(disclosures, Mapping):
        items: Sequence[Any] = (disclosures,)
    elif isinstance(disclosures, (list, tuple)) and not isinstance(
        disclosures, (str, bytes)
    ):
        items = disclosures
    else:
        return
    current.reregister_public(
        tuple(item for item in items if isinstance(item, Mapping))
    )


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


def disclosure_metadata(disclosures: Any) -> tuple[dict[str, Any], ...]:
    """Plain snapshot tuples for a stored disclosure payload."""

    if isinstance(disclosures, Mapping):
        items: Sequence[Any] = (disclosures,)
    elif isinstance(disclosures, (list, tuple)) and not isinstance(
        disclosures, (str, bytes)
    ):
        items = disclosures
    else:
        return ()
    plain: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Mapping) and item.get("law_id"):
            plain.append(dict(item))
    return tuple(plain)
