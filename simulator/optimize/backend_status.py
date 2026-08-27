"""Single owner for reading a backend_status out of diagnostics carriers.

Why this module exists
----------------------
`evaluate.py` and `objective.py` both have to answer the same question -- "what
backend_status did this run actually end at?" -- by walking the same four
diagnostics carriers (the run execution, its trace, and the simulator's two
`_last_*_diagnostics` attributes). Each had grown its own copy of that walk.
`evaluate.py` imports `objective.py`, so the shared rule cannot live in
`evaluate.py`; and carrier-walking is not the objective function's job. Hence a
third module that both import.

The copies had already drifted apart in two places, which is the reason this is
a correctness fix and not tidying:

  * `_select_backend_status` carried a TRANSPOSED precedence in both optimizer
    copies -- `out_of_domain` ahead of `unavailable` -- while the runner ranked
    them the other way. Precedence now lives with the vocabulary it ranks, in
    `simulator.chemistry.kernel.dto.select_backend_status`, and is re-exported
    here so carrier-walkers get the ranked answer from one place.

  * `carrier_has_crash_point` diverged on the EMPTY-mapping case, which is the
    live defect this module settles. See the derivation on that function.
"""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from typing import Any

from simulator.chemistry.kernel import select_backend_status

__all__ = [
    "select_backend_status",
    "carrier_has_crash_point",
    "crash_point_from_carrier",
    "backend_statuses_from_carrier",
    "latest_backend_status_from_sequence",
    "backend_statuses_from_run_execution",
    "latest_backend_status",
]


def carrier_has_crash_point(carrier: MappingABC[Any, Any]) -> bool:
    """True when the carrier records a crash point with CONTENT in it.

    Derivation of the non-empty requirement
    ---------------------------------------
    ★ THIS DOCSTRING DESCRIBED THE OLD IMPLEMENTATION UNTIL A REVIEW CAUGHT IT.
    It said a True here SYNTHESISES an `out_of_domain` token into the status
    list. That stopped being true when the synthesis moved out of the walker:
    the walker now reports only what the engine said, and the single gated
    consumer (`evaluate._has_out_of_domain_backend_signal`) decides whether
    crash evidence amounts to a verdict.

    The requirement itself is unchanged and the reason still holds. A True here
    still feeds a decision that ends in `out_of_domain` -- a verdict that the
    recipe is physically infeasible, on which the optimizer prunes the candidate
    permanently rather than retrying it. Manufacturing that verdict is expensive
    and irreversible for the candidate, so the bar is evidence.

    An empty mapping is not evidence. `{}` carries no temperature, no pressure,
    no composition -- nothing that says where or how the backend left its
    domain. Treating presence-of-key as the signal would be reading a verdict
    out of silence, which is the flattering direction on the degrade-vs-flatter
    axis.

    Nothing real is lost by requiring content, and that is checkable rather than
    hopeful. Every producer of these keys writes `temperature_C` and
    `pressure_bar` unconditionally as floats:

        simulator/diagnostic_helpers/alphamelts_volatility.py  (crash_point literal)
        simulator/melt_backend/alphamelts.py                   (crash_point literal)
        engines/alphamelts/provider.py                          (crash_point literal)

    so a producer-emitted crash point is never empty; and the subprocess path at
    melt_backend/alphamelts.py always adds a 'stage' key, so it is non-empty even
    when it starts from `{}`. Serialisation cannot empty one either --
    `_compact_jsonable` preserves falsy values (verified: an all-zero/all-empty
    crash point round-trips with its keys intact). Two of the three producers
    also set `backend_status: 'out_of_domain'` in the same payload, so for them
    this predicate is a second path to a token the carrier already states
    outright.

    The strict reading is therefore the one that agrees with every real value
    and differs only on a content-free placeholder. It is pinned by
    `test_empty_crash_point_placeholder_does_not_mark_backend_out_of_domain`
    in tests/test_optimizer_evaluate.py. The `objective.py` copy had dropped the
    non-empty clause and had no equivalent test holding it in place.
    """
    return crash_point_from_carrier(carrier) is not None


def crash_point_from_carrier(carrier: MappingABC[Any, Any]) -> MappingABC[Any, Any] | None:
    """Return the crash point that HAS CONTENT, or None. The single rule.

    Both the boolean predicate above and evaluate.py's direct extractor resolve
    through here, so they cannot disagree -- which they did, and which is what
    review r1 caught: an empty mapping was not evidence to one of them and was
    evidence to the other, so an evidence-free placeholder permanently pruned a
    recipe through the path that had not been unified.

    ONE DELIBERATE BEHAVIOUR CHANGE beyond restoring agreement. The previous
    extractor fell back from `out_of_domain_crash_point` to `crash_point` only
    when the first key was literally absent (`raw is None`), so a carrier
    holding an EMPTY canonical key and a POPULATED alias returned the empty one
    and reported no evidence. Skipping empties at each key means real evidence
    under the alias is now found. That is strictly more truthful -- an empty
    placeholder should never mask a populated sibling -- but it is a change, so
    it is stated rather than absorbed.

    Key order is preference order: the canonical `out_of_domain_crash_point`
    first, the `crash_point` alias second.

    DELIBERATELY NOT GUARDED against a non-mapping carrier. A draft of this
    function opened with `if not isinstance(carrier, MappingABC): return None`,
    which reads like robustness and is the opposite: today a non-mapping raises
    AttributeError on `.get`, loudly, because it is a programming error. The
    guard would convert that into a silent "no crash point found" -- a false
    negative manufactured out of a type error, in the flattering direction, in
    the function whose entire job is deciding what counts as evidence. Both
    callers already hold Mappings (`backend_statuses_from_carrier` tests
    `isinstance` before calling; `_out_of_domain_diagnostics` always returns a
    dict), so the guard protects nothing and hides something.
    """
    for key in ("out_of_domain_crash_point", "crash_point"):
        raw = carrier.get(key)
        if isinstance(raw, MappingABC) and raw:
            return raw
    return None


def backend_statuses_from_carrier(carrier: Any) -> tuple[str, ...]:
    """Collect every backend_status token a single carrier can testify to.

    Order is collection order, not rank -- ranking is `select_backend_status`'s
    job. Recurses into nested trace/diagnostics carriers so a token buried a
    level down is not silently dropped.
    """
    if carrier is None:
        return ()
    statuses: list[str] = []
    if isinstance(carrier, MappingABC):
        raw = carrier.get("backend_status")
        if raw is not None:
            statuses.append(str(raw))
        # NOTE: crash-point content is deliberately NOT synthesised into a
        # status here. It used to be, and that made the status walker answer a
        # question it is not entitled to answer.
        #
        # The optimizer gates crash evidence on "did the engine actually
        # answer?" -- but it asked that of _latest_backend_status(), which was
        # itself appending `out_of_domain` whenever a crash point existed. So
        # the gate was handed an answer already manufactured from the evidence
        # it was supposed to be judging, and its first clause
        # (`status == "out_of_domain" -> True`) fired before the gate ran. It
        # was dead code on every crash-bearing carrier: `refused` became
        # OUT_OF_DOMAIN instead of PHYSICS_REFUSED, `not_converged` instead of
        # TIMEOUT, `not_attempted` instead of its bug-abort, and a status of
        # None became a physics verdict the commit had explicitly forbidden.
        # Only `unavailable` survived, and only because it OUTRANKS the
        # synthesised token -- not because the gate worked.
        #
        # This walker now reports what the ENGINE SAID. Whether crash evidence
        # amounts to an out-of-domain verdict is decided in exactly one place,
        # `evaluate._has_out_of_domain_backend_signal`, which reads the crash
        # point directly and gates it. One question, one answerer.
        for key in ("per_hour", "hours"):
            status = latest_backend_status_from_sequence(carrier.get(key))
            if status is not None:
                statuses.append(status)
        for key in ("trace", "backend_diagnostics", "diagnostics"):
            statuses.extend(backend_statuses_from_carrier(carrier.get(key)))
        return tuple(statuses)
    raw = getattr(carrier, "backend_status", None)
    if raw is not None:
        statuses.append(str(raw))
    for attr in ("per_hour", "hours"):
        status = latest_backend_status_from_sequence(getattr(carrier, attr, None))
        if status is not None:
            statuses.append(status)
    for attr in ("trace", "backend_diagnostics", "diagnostics"):
        statuses.extend(backend_statuses_from_carrier(getattr(carrier, attr, None)))
    return tuple(statuses)


def latest_backend_status_from_sequence(value: Any) -> str | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    return select_backend_status(
        status for item in value for status in backend_statuses_from_carrier(item)
    )


def backend_statuses_from_run_execution(run_execution: Any) -> tuple[str, ...]:
    sim = getattr(run_execution, "simulator", None)
    carriers = (
        run_execution,
        getattr(run_execution, "trace", None),
        getattr(sim, "_last_backend_diagnostics", None),
        getattr(sim, "_last_out_of_domain_diagnostics", None),
    )
    return tuple(
        status
        for carrier in carriers
        for status in backend_statuses_from_carrier(carrier)
    )


def latest_backend_status(run_execution: Any) -> str | None:
    return select_backend_status(backend_statuses_from_run_execution(run_execution))
