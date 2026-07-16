"""Structured in-process runner execution surface."""

from __future__ import annotations

import dataclasses
import math
from copy import copy, deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping

from engines.builtin.melt_effect_adjustment import CertifiedPointRefusedError
from engines.builtin.vapor_pressure import VaporPressureRangeError
from simulator.backends import requires_stage0_subprocess
from simulator.campaigns import CampaignPressureSetpointRefusal
from simulator.condensation import KnudsenRegimeRefusal
from simulator.cost_ledger import build_cost_rollup_diagnostic
from simulator.core import (
    BACKEND_FALLBACK_EXCEPTIONS,
    PoisonedHourError,
    PyrolysisSimulator,
)
from simulator.pumping_cost import pumping_context_from_sim
from simulator.session import (
    DecisionPolicy,
    SimSession,
    SimSessionConfig,
    _at_stage0_exit as _session_at_stage0_exit,
    drive_session,
)
from simulator.state import HourSnapshot
from simulator.trace import PhysicsTrace
from simulator.transport_regime import TransportRegimeRefusal


_TYPED_PHYSICS_REFUSALS = (
    VaporPressureRangeError,
    CertifiedPointRefusedError,
    TransportRegimeRefusal,
)
_ALL_TYPED_PHYSICS_REFUSALS = (
    KnudsenRegimeRefusal,
    CampaignPressureSetpointRefusal,
    *_TYPED_PHYSICS_REFUSALS,
)


def _typed_refusal_reason(exc: BaseException) -> str:
    for attr in ("reason", "category"):
        value = getattr(exc, attr, None)
        if value:
            return str(value)
    if isinstance(exc, CertifiedPointRefusedError):
        if "liquidus" in str(exc).lower():
            return "liquidus_authority_refused"
        return "certified_point_refused"
    prefix = str(exc).partition(":")[0].strip().lower().replace(" ", "_")
    return prefix or "typed_physics_refusal"


def _snapshot_atom_ledger(ledger: Any) -> Any:
    """Copy mutable ledger state without copying its immutable registries."""
    snapshot = copy(ledger)
    snapshot._balances = deepcopy(ledger._balances)
    snapshot._policies = dict(ledger._policies)
    snapshot._transitions = list(ledger._transitions)
    snapshot._terminal_debit_authorized_transition_ids = set(
        ledger._terminal_debit_authorized_transition_ids
    )
    snapshot._external_loads = list(ledger._external_loads)
    return snapshot


@dataclass(frozen=True)
class RunExecution:
    """Structured result of one simulator run before JSON projection."""

    session: SimSession
    simulator: PyrolysisSimulator
    snapshots: tuple[HourSnapshot, ...]
    trace: PhysicsTrace
    per_hour: tuple[dict[str, Any], ...] = ()
    operator_decisions: tuple[dict[str, Any], ...] = ()
    shadow_trace: tuple[dict[str, Any], ...] = ()
    status: str = "ok"
    error_message: str = ""
    reason: str = ""
    refusal_diagnostic: Mapping[str, Any] = field(default_factory=dict)
    reduced_real_cache: Mapping[str, Any] = field(default_factory=dict)
    backend_status: str = "ok"
    backend_authoritative: bool = True
    envelope_detail_unavailable: str = ""
    campaigns_elapsed: float = 1.0


class RunExecutor:
    """Drive a configured session and return structured execution data."""

    def execute(
        self,
        config: SimSessionConfig,
        *,
        worker_runtime: Any | None = None,
    ) -> RunExecution:
        hours = _coerce_nonnegative_hours(config.hours)
        session = SimSession()
        try:
            session.start(
                config,
                backend=_backend_from_worker_runtime(config, worker_runtime),
            )
        except CampaignPressureSetpointRefusal as exc:
            return self.execute_session(
                session,
                hours=hours,
                initial_refusal=exc,
            )
        kwargs: dict[str, Any] = {"hours": hours}
        if bool(config.stop_at_stage0_exit):
            kwargs["stop_at_stage0_exit"] = True
        return self.execute_session(session, **kwargs)

    def execute_session(
        self,
        session: SimSession,
        *,
        hours: int,
        stop_at_stage0_exit: bool | None = None,
        initial_refusal: CampaignPressureSetpointRefusal | None = None,
    ) -> RunExecution:
        sim = session.simulator
        per_hour: list[dict[str, Any]] = []
        operator_decisions: list[dict[str, Any]] = []
        status = "ok"
        error_message = ""
        reason = ""
        refusal_diagnostic: dict[str, Any] = {}
        failure_exc: Exception | None = None
        hours = _coerce_nonnegative_hours(hours)
        snapshot_start = len(
            tuple(getattr(getattr(sim, "record", None), "snapshots", ()))
        )
        if stop_at_stage0_exit is None:
            config = getattr(session, "_config", None)
            stop_at_stage0_exit = bool(
                getattr(config, "stop_at_stage0_exit", False)
            )

        try:
            if initial_refusal is not None:
                raise initial_refusal
            driver = iter(drive_session(
                session,
                hours,
                DecisionPolicy.AUTO_APPLY,
                operator_decisions=operator_decisions,
                stop_at_stage0_exit=bool(stop_at_stage0_exit),
            ))
            while True:
                # A typed refusal is an infeasible point, not a partially
                # executed hour. Snapshot both authoritative state surfaces
                # immediately before advancing the generator so refusal can
                # roll the current hour back while retaining prior hours.
                ledger_before_hour = _snapshot_atom_ledger(sim.atom_ledger)
                melt_before_hour = deepcopy(sim.melt)
                try:
                    result = next(driver)
                except StopIteration:
                    break
                except _ALL_TYPED_PHYSICS_REFUSALS:
                    sim.atom_ledger = ledger_before_hour
                    sim.melt = melt_before_hour
                    sim._chem_kernel = sim._build_chemistry_kernel()
                    raise
                per_hour_summary = dict(result.per_hour_summary)
                cache_state = getattr(sim, "_last_reduced_real_cache_state", None)
                if cache_state is not None:
                    per_hour_summary["reduced_real_cache_state"] = str(cache_state)
                per_hour.append(per_hour_summary)
                o2_bubbler_refusal = getattr(
                    result.snapshot,
                    "o2_bubbler_diagnostic",
                    None,
                )
                if (
                    isinstance(o2_bubbler_refusal, Mapping)
                    and o2_bubbler_refusal.get("status") == "refused"
                ):
                    refusal_diagnostic = dict(o2_bubbler_refusal)
                    reason = str(
                        o2_bubbler_refusal.get("reason")
                        or "o2_bubbler_refused"
                    )
                    error_message = reason
                    status = "refused"
                    failure_exc = RuntimeError(reason)
                    break
                campaign_summary = result.campaign_summary
                c6_refusal = (
                    campaign_summary.get("c6_refusal_diagnostic")
                    if isinstance(campaign_summary, Mapping)
                    else None
                )
                if (
                    isinstance(c6_refusal, Mapping)
                    and c6_refusal.get("status") == "refused"
                ):
                    refusal_diagnostic = dict(c6_refusal)
                    diagnostic = c6_refusal.get("diagnostic")
                    refusal_reason = (
                        diagnostic.get("reason_refused")
                        if isinstance(diagnostic, Mapping)
                        else c6_refusal.get("reason")
                    )
                    reason = str(refusal_reason or "c6_mg_thermite_refused")
                    error_message = reason
                    status = "refused"
                    failure_exc = RuntimeError(reason)
                    break
            # Status semantics:
            #   * "ok"      -- the run consumed its full hour budget and
            #                  the simulator is either mid-batch or
            #                  exactly at the campaign endpoint.
            #   * "partial" -- the simulator finished mid-batch (either
            #                  the campaign closed early or operator
            #                  decisions consumed iteration slots
            #                  without advancing the hour counter).
            #   * "refused" -- a binding campaign diagnostic or typed
            #                  operating-envelope refusal stopped the run.
            #   * "failed"  -- set in the except blocks below.
            if status == "ok":
                pending_decision = _safe_pending_decision(session)
                if bool(stop_at_stage0_exit) and _session_at_stage0_exit(session):
                    reason = "stage0_exit"
                elif pending_decision is not None:
                    status = "partial"
                    reason = "pending_decision"
                elif sim.melt.hour < hours:
                    status = "partial"
        except (KnudsenRegimeRefusal, CampaignPressureSetpointRefusal) as exc:
            failure_exc = exc
            status = "refused"
            reason = exc.reason
            error_message = exc.reason
            refusal_diagnostic = dict(exc.diagnostic)
        except _TYPED_PHYSICS_REFUSALS as exc:
            failure_exc = exc
            status = "refused"
            reason = _typed_refusal_reason(exc)
            error_message = str(exc)
        except PoisonedHourError as exc:
            failure_exc = exc
            status = "failed"
            reason = "poisoned_hour"
            error_message = _safe_exception_text(exc)
        except BACKEND_FALLBACK_EXCEPTIONS as exc:
            failure_exc = exc
            status = "failed"
            error_message = f"backend failure: {_safe_exception_text(exc)}"
        except Exception as exc:  # noqa: BLE001 -- envelope the error
            failure_exc = exc
            status = "failed"
            error_message = _safe_exception_text(exc)

        try:
            unenriched_failure = (status, reason, error_message)
            poisoned = None
            try:
                poisoned = getattr(sim, "_poisoned_hour", None)
                if poisoned is not None:
                    status = "failed"
                    reason = "poisoned_hour"
                    poisoned_exc = PoisonedHourError(poisoned)
                    failure_exc = failure_exc or poisoned_exc
                    poisoned_detail = _safe_exception_text(poisoned_exc)
                    if error_message != poisoned_detail:
                        error_message = (
                            f"{error_message}; {poisoned_detail}"
                            if error_message
                            else poisoned_detail
                        )
            except Exception:  # noqa: BLE001 -- enrichment must not mask the failure
                status, reason, error_message = unenriched_failure
                poisoned = None

            shadow_trace = _collect_shadow_trace(sim, operator_decisions)
            all_snapshots = tuple(getattr(sim.record, "snapshots", ()))
            snapshots = all_snapshots[snapshot_start:]
            sim.record.cost_rollup = build_cost_rollup_diagnostic(
                cost_ledger=sim.cost_ledger,
                per_hour=tuple(per_hour),
                products_kg=sim.product_ledger(),
                pumping_context=pumping_context_from_sim(sim, snapshots),
                snapshots=snapshots,
            )
            trace = _slice_trace(PhysicsTrace.from_simulator(sim), snapshot_start)
            reduced_real_cache = _collect_reduced_real_cache_diagnostic(sim)
            latest_backend_status = str(
                getattr(
                    sim,
                    "_backend_selection_status",
                    getattr(sim, "_last_backend_status", "ok"),
                )
            )
            backend_status = _aggregate_backend_status(
                getattr(sim, "_backend_status_history", ()),
                latest_backend_status,
            )
            backend_authoritative = bool(
                getattr(sim, "_backend_authoritative", True)
            )
            return RunExecution(
                session=session,
                simulator=sim,
                snapshots=snapshots,
                trace=trace,
                per_hour=tuple(per_hour),
                operator_decisions=tuple(operator_decisions),
                shadow_trace=tuple(shadow_trace),
                status=status,
                error_message=error_message,
                reason=reason,
                refusal_diagnostic=refusal_diagnostic,
                reduced_real_cache=reduced_real_cache,
                backend_status=backend_status,
                backend_authoritative=backend_authoritative,
                campaigns_elapsed=float(
                    getattr(getattr(session, "_config", None), "campaigns_elapsed", 1.0)
                ),
            )
        except Exception as envelope_exc:  # noqa: BLE001 -- reporting must survive
            if failure_exc is None:
                raise
            snapshots = _safe_tuple(lambda: getattr(sim.record, "snapshots", ()))
            shadow_trace = _safe_tuple(
                lambda: _collect_shadow_trace(sim, operator_decisions)
            )
            reduced_real_cache = _safe_mapping(
                lambda: _collect_reduced_real_cache_diagnostic(sim)
            )
            latest_backend_status = _safe_str(
                lambda: getattr(
                    sim,
                    "_backend_selection_status",
                    getattr(sim, "_last_backend_status", "ok"),
                ),
                "unavailable",
            )
            backend_status = _safe_str(
                lambda: _aggregate_backend_status(
                    getattr(sim, "_backend_status_history", ()),
                    latest_backend_status,
                ),
                "unavailable",
            )
            backend_authoritative = _safe_bool(
                lambda: getattr(sim, "_backend_authoritative", False),
                False,
            )
            return RunExecution(
                session=session,
                simulator=sim,
                snapshots=snapshots,
                trace=PhysicsTrace(),
                per_hour=tuple(per_hour),
                operator_decisions=tuple(operator_decisions),
                shadow_trace=shadow_trace,
                status=status,
                error_message=error_message or _safe_exception_text(failure_exc),
                reason=reason,
                refusal_diagnostic=refusal_diagnostic,
                reduced_real_cache=reduced_real_cache,
                backend_status=backend_status,
                backend_authoritative=backend_authoritative,
                envelope_detail_unavailable=(
                    "envelope detail unavailable: "
                    f"{_safe_exception_text(envelope_exc)}"
                ),
                campaigns_elapsed=float(
                    getattr(getattr(session, "_config", None), "campaigns_elapsed", 1.0)
                ),
            )


def _backend_from_worker_runtime(
    config: SimSessionConfig,
    worker_runtime: Any | None,
) -> Any | None:
    if worker_runtime is None:
        return None
    if getattr(worker_runtime, "backend_name", None) != config.backend_name:
        return None
    context_feedstock = getattr(worker_runtime, "feedstock_id", None)
    if (
        context_feedstock is not None
        and context_feedstock != str(config.feedstock_id)
    ):
        return None
    subprocess_required = requires_stage0_subprocess(
        config.feedstock_id,
        config.feedstocks,
    )
    if (
        bool(getattr(worker_runtime, "stage0_subprocess_required", False))
        != subprocess_required
    ):
        return None
    if config.reduced_real_cache is not None:
        return None
    return getattr(worker_runtime, "backend", None)


def _coerce_nonnegative_hours(value: Any) -> int:
    hours = int(value)
    if hours < 0:
        raise ValueError("hours must be non-negative")
    return hours


def _slice_trace(trace: PhysicsTrace, snapshot_start: int) -> PhysicsTrace:
    if snapshot_start <= 0:
        return trace
    return dataclasses.replace(
        trace,
        snapshots=trace.snapshots[snapshot_start:],
        condensed_by_stage_species_delta=(
            trace.condensed_by_stage_species_delta[snapshot_start:]
        ),
        wall_deposit_by_segment_species_delta=(
            trace.wall_deposit_by_segment_species_delta[snapshot_start:]
        ),
        impurity_delta=trace.impurity_delta[snapshot_start:],
    )


def _safe_pending_decision(session: SimSession) -> Any | None:
    pending_decision = getattr(session, "pending_decision", None)
    if not callable(pending_decision):
        return None
    try:
        return pending_decision()
    except Exception:  # noqa: BLE001 -- status enrichment must not self-fail
        return None


def _safe_exception_text(exc: BaseException) -> str:
    try:
        message = str(exc)
    except Exception as message_exc:  # noqa: BLE001 -- reporting must survive
        message = f"<message unavailable: {type(message_exc).__name__}>"
    return f"{type(exc).__name__}: {message}"


def _safe_tuple(builder: Any) -> tuple[Any, ...]:
    try:
        return tuple(builder() or ())
    except Exception:  # noqa: BLE001 -- degraded envelope fallback
        return ()


def _safe_mapping(builder: Any) -> Mapping[str, Any]:
    try:
        value = builder()
    except Exception:  # noqa: BLE001 -- degraded envelope fallback
        return {}
    return value if isinstance(value, Mapping) else {}


def _safe_str(builder: Any, default: str) -> str:
    try:
        return str(builder())
    except Exception:  # noqa: BLE001 -- degraded envelope fallback
        return default


def _safe_bool(builder: Any, default: bool) -> bool:
    try:
        return bool(builder())
    except Exception:  # noqa: BLE001 -- degraded envelope fallback
        return default


def _aggregate_backend_status(history: Any, latest: str) -> str:
    try:
        statuses = [str(status) for status in history]
    except TypeError:
        statuses = []
    statuses.append(str(latest))
    for status in ("unavailable", "out_of_domain", "not_converged"):
        if status in statuses:
            return status
    return str(latest)


def _collect_shadow_trace(
    sim: PyrolysisSimulator,
    operator_decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = list(operator_decisions)
    kernel = getattr(sim, "_chem_kernel", None)
    if kernel is None:
        return events
    # ``ChemistryKernel.planner`` is the public property; fall back
    # to the private slot as a forward-compatible safety net in
    # case a future refactor renames the property without breaking
    # the underlying attribute (e.g. dataclass conversion).  The
    # runner is not allowed to assume kernel internals, so neither
    # attribute is a hard requirement.
    planner = getattr(kernel, "planner", None) or getattr(
        kernel, "_planner", None
    )
    if planner is None:
        return events
    shadow_trace = getattr(planner, "shadow_trace", None)
    if shadow_trace is None:
        return events
    try:
        kernel_events = list(shadow_trace)
    except TypeError:
        kernel_events = []
    # Only surface ``parity_warning`` entries -- the bulk shadow
    # dispatch records are noise for the operator-facing JSON.
    for record in kernel_events:
        if not isinstance(record, Mapping):
            continue
        event_type = record.get("event")
        if event_type in ("parity_warning", "parity_error"):
            events.append(_json_safe(dict(record)))
    return events


def _collect_reduced_real_cache_diagnostic(
    sim: PyrolysisSimulator,
) -> dict[str, Any]:
    store_getter = getattr(sim, "_pt0_store", None)
    store = store_getter() if callable(store_getter) else None
    if store is None:
        return {}
    summary_getter = getattr(store, "summary", None)
    summary = summary_getter() if callable(summary_getter) else {}
    diagnostic = dict(summary or {})
    diagnostic["last_cache_state"] = getattr(
        sim,
        "_last_reduced_real_cache_state",
        None,
    )
    diagnostic["miss_policy"] = getattr(
        store,
        "cached_real_miss_policy",
        None,
    )
    return _json_safe(diagnostic)


def _json_safe(value: Any) -> Any:
    """Recursively convert ``value`` into a JSON-serialisable form.

    Non-finite numeric telemetry exports as ``None`` so strict JSON consumers
    see schema-compatible nulls instead of Python-only NaN/Infinity tokens or
    stringified pseudo-numbers.
    """

    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, bool)) or value is None:
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value))
    # Enums, IntentResult-like wrappers -- fall back to repr so the
    # shadow_trace stays informative without leaking object identity.
    return repr(value)
