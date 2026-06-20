"""Planner and kernel orchestrator.

:class:`Planner` walks the :class:`ProviderRegistry` for a single
:class:`IntentRequest`: it runs the authoritative provider and every
shadow provider for that intent.  Shadow results are captured into the
trace but their proposals are NEVER applied.

:class:`ChemistryKernel` is the top-level entry point.  It:

* builds a :class:`ProviderAccountView` from the live :class:`AtomLedger`
  (using :func:`build_provider_account_view`),
* constructs an :class:`IntentRequest`,
* dispatches it through the :class:`Planner`,
* runs the validation suite (intent authority, account filter, atom
  balance, control audit) against the authoritative result, and
* exposes the SOLE writable path into the ledger via
  :meth:`commit_batch`.

Providers see only frozen DTOs.  They never receive an ``AtomLedger``
reference.  This is the invariant covered by
``tests/chemistry/test_kernel_commit_batch.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from simulator.accounting.ledger import AtomLedger, LedgerTransition
from simulator.chemistry.kernel.account_filters import build_provider_account_view
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.config import (
    DEFAULT_OXYGEN_SINK_CHANNEL_MODE,
    OxygenSinkChannelMode,
    normalize_oxygen_sink_channel_mode,
)
from simulator.chemistry.kernel.dto import (
    IntentRequest,
    IntentResult,
    LedgerTransitionProposal,
    ProviderAccountView,
)
from simulator.chemistry.kernel.errors import (
    KernelError,
    ProposalRejected,
    ProviderUnavailableError,
)
from simulator.chemistry.kernel.provider import ChemistryProvider
from simulator.chemistry.kernel.registry import ProviderRegistry
from simulator.chemistry.kernel.validation import (
    _proposal_to_ledger_transition,
    validate_atom_balance,
    validate_control_audit,
    validate_intent_authority,
    validate_proposal_accounts,
)


class Planner:
    """Routes a single :class:`IntentRequest` to authoritative + shadows."""

    #: Cap on retained shadow-trace entries; the buffer drops the oldest
    #: record once this is exceeded.  The kernel is instantiated once per
    #: simulator and reused across an entire campaign / loop, so an
    #: unbounded list would leak memory.  Tune via
    #: :meth:`set_shadow_trace_cap`.
    DEFAULT_SHADOW_TRACE_CAP: int = 10_000

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry
        self._shadow_trace: list[dict[str, Any]] = []
        self._shadow_trace_cap: int = Planner.DEFAULT_SHADOW_TRACE_CAP

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    @property
    def shadow_trace(self) -> tuple[dict[str, Any], ...]:
        """Tuple of ``{provider_id, intent, result}`` records.

        Populated by every call to :meth:`dispatch`; only shadow
        provider results are recorded here -- the authoritative result
        is the return value.  Capped at
        :attr:`DEFAULT_SHADOW_TRACE_CAP` entries; older records are
        dropped FIFO when the cap is exceeded.  Call
        :meth:`clear_shadow_trace` to reset, e.g. between batches.
        """

        return tuple(self._shadow_trace)

    @property
    def shadow_trace_cap(self) -> int:
        return self._shadow_trace_cap

    def set_shadow_trace_cap(self, cap: int) -> None:
        """Configure the ring-buffer cap; ``0`` disables retention."""

        if int(cap) < 0:
            raise ValueError("shadow_trace_cap must be non-negative")
        self._shadow_trace_cap = int(cap)
        if len(self._shadow_trace) > self._shadow_trace_cap:
            # Trim from the front so the most recent records survive.
            overflow = len(self._shadow_trace) - self._shadow_trace_cap
            del self._shadow_trace[:overflow]

    def clear_shadow_trace(self) -> None:
        """Drop every retained shadow-trace entry.

        Called from :meth:`PyrolysisSimulator.load_batch` so per-batch
        diagnostics start clean and the kernel does not accumulate
        unbounded state across long-running web sessions or
        ``\\goal``-driven loops.
        """

        self._shadow_trace.clear()

    def dispatch(self, request: IntentRequest) -> IntentResult:
        """Run the authoritative + shadow providers for ``request``.

        Returns the authoritative provider's :class:`IntentResult`.
        Shadow results are appended to :attr:`shadow_trace` for the
        caller (typically :class:`ChemistryKernel`) to surface.

        After the authoritative dispatch completes, each shadow that
        exposes a :meth:`parity_compare` method is given a chance to
        compare its result against the authoritative one.  When a
        parity comparator reports disagreement, a ``parity_warning``
        record is appended to :attr:`shadow_trace` (in addition to the
        per-shadow result record).  Disagreement is NEVER a
        :class:`KernelError` -- the shadow's only job is to flag
        suspicion, not to block dispatch.  Goal #9
        ``MAGEMIN-SHADOW-PARITY`` binds this contract.

        Raises:
            ProviderUnavailableError: No authoritative provider is
                registered for ``request.intent``.
        """

        authoritative = self._registry.authoritative_for(request.intent)
        if authoritative is None:
            raise ProviderUnavailableError(
                f"no authoritative provider registered for intent {request.intent.value!r}"
            )

        shadows = self._registry.shadows_for(request.intent)
        shadow_results: list[tuple[ChemistryProvider, IntentResult]] = []
        for shadow in shadows:
            provider_id = shadow.capability_profile().provider_id
            try:
                shadow_result = shadow.dispatch(request)
            except Exception as exc:  # noqa: BLE001 -- never block dispatch
                self._append_shadow_trace(
                    {
                        "event": "shadow_error",
                        "provider_id": provider_id,
                        "intent": request.intent.value,
                        "error": repr(exc),
                    }
                )
                continue
            shadow_results.append((shadow, shadow_result))
            self._append_shadow_trace(
                {
                    "event": "shadow_dispatch",
                    "provider_id": provider_id,
                    "intent": request.intent.value,
                    "result": shadow_result,
                }
            )
            # Shadow proposals NEVER apply.  We don't even pass them
            # along.  If the shadow accidentally tried to set a
            # transition, that data is captured here for parity
            # logging but the kernel will reject it on its own commit
            # path because the shadow isn't registered as
            # authoritative.

        authoritative_result = authoritative.dispatch(request)

        # Parity pass: each shadow that exposes ``parity_compare`` gets
        # to weigh its result against the authoritative one.  The
        # comparator returns a per-provider report; only disagreement
        # produces a ``parity_warning`` event in the trace.  Goal #9
        # forbids silent averaging -- both numbers stay visible on the
        # warning record.
        for shadow, shadow_result in shadow_results:
            comparator = getattr(shadow, "parity_compare", None)
            if not callable(comparator):
                continue
            try:
                report = comparator(authoritative_result, shadow_result)
            except Exception as exc:  # noqa: BLE001 -- never block dispatch
                self._append_shadow_trace(
                    {
                        "event": "parity_error",
                        "provider_id": shadow.capability_profile().provider_id,
                        "intent": request.intent.value,
                        "error": repr(exc),
                    }
                )
                continue
            if report is None:
                continue
            if getattr(report, "agreement", True):
                continue
            self._append_shadow_trace(
                self._build_parity_warning_record(
                    shadow=shadow,
                    intent=request.intent,
                    report=report,
                    authoritative_result=authoritative_result,
                    shadow_result=shadow_result,
                )
            )

        return authoritative_result

    def _append_shadow_trace(self, record: dict[str, Any]) -> None:
        if self._shadow_trace_cap == 0:
            return
        self._shadow_trace.append(record)
        if len(self._shadow_trace) > self._shadow_trace_cap:
            # FIFO drop of the oldest record.
            del self._shadow_trace[0]

    @staticmethod
    def _build_parity_warning_record(
        *,
        shadow: ChemistryProvider,
        intent: ChemistryIntent,
        report: Any,
        authoritative_result: IntentResult,
        shadow_result: IntentResult,
    ) -> dict[str, Any]:
        """Project a parity report into a trace event.

        The record schema is stable: trace consumers (debug UI, parity
        tests) pin on these keys.  Goal #9 binds the wording (``event ==
        'parity_warning'``).  The authoritative and shadow numbers are
        both retained verbatim so silent averaging is impossible: any
        disagreement is auditable from the trace alone.
        """

        auth_diag = dict(getattr(authoritative_result, "diagnostic", {}) or {})
        shadow_diag = dict(getattr(shadow_result, "diagnostic", {}) or {})

        warnings_tuple = tuple(getattr(report, "warnings", ()) or ())

        return {
            "event": "parity_warning",
            "provider_id": shadow.capability_profile().provider_id,
            "intent": intent.value,
            "agreement": False,
            "liquidus_T_delta_K": getattr(report, "liquidus_T_delta_K", None),
            "mode_pct_max_delta": getattr(report, "mode_pct_max_delta", None),
            "phases_only_in_authoritative": tuple(
                getattr(report, "phases_only_in_authoritative", ()) or ()
            ),
            "phases_only_in_shadow": tuple(
                getattr(report, "phases_only_in_shadow", ()) or ()
            ),
            "warnings": warnings_tuple,
            "authoritative_liquidus_T_K": auth_diag.get("liquidus_T_K"),
            "shadow_liquidus_T_K": shadow_diag.get("liquidus_T_K"),
            "authoritative_status": getattr(authoritative_result, "status", None),
            "shadow_status": getattr(shadow_result, "status", None),
        }


class ChemistryKernel:
    """Top-level kernel: filters, dispatches, validates, commits.

    The kernel owns the only writable paths into the
    :class:`AtomLedger` -- :meth:`commit_batch` (provider proposals) and
    :meth:`commit_validated_transition` (pre-built backend transitions,
    run through the same authority/account/atom-balance gates).  Providers receive
    only :class:`IntentRequest` instances (whose
    :class:`ProviderAccountView` is already filtered), and may return
    :class:`IntentResult` instances; they have no way to reach the
    ledger directly.
    """

    def __init__(
        self,
        ledger: AtomLedger,
        registry: ProviderRegistry,
        species_formula_registry: Mapping[str, Any],
        *,
        allow_fallback_intents: Optional[frozenset[ChemistryIntent]] = None,
        oxygen_sink_channel_mode: OxygenSinkChannelMode | str = (
            DEFAULT_OXYGEN_SINK_CHANNEL_MODE
        ),
    ) -> None:
        self._ledger = ledger
        self._registry = registry
        self._species_formula_registry = dict(species_formula_registry or {})
        self._planner = Planner(registry)
        # Set of intents the kernel is allowed to retry against the
        # registered fallback provider when the authoritative provider
        # raises :class:`ProviderUnavailableError`.  Goal #10
        # ``VAPOROCK-AUTHORITY-PROMOTION`` binds this surface: silent
        # fallback is forbidden, so the kernel only consults the
        # fallback when the caller has explicitly opted in for the
        # intent (typically via a per-intent simulator config flag like
        # ``allow_fallback_vapor``).
        self._allow_fallback_intents: frozenset[ChemistryIntent] = frozenset(
            allow_fallback_intents or ()
        )
        self._oxygen_sink_channel_mode = normalize_oxygen_sink_channel_mode(
            oxygen_sink_channel_mode
        )

    @property
    def planner(self) -> Planner:
        return self._planner

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    @property
    def species_formula_registry(self) -> Mapping[str, Any]:
        return dict(self._species_formula_registry)

    @property
    def allow_fallback_intents(self) -> frozenset[ChemistryIntent]:
        """Intents the kernel may retry against the registered fallback."""

        return self._allow_fallback_intents

    @property
    def oxygen_sink_channel_mode(self) -> OxygenSinkChannelMode:
        return self._oxygen_sink_channel_mode

    def clear_shadow_trace(self) -> None:
        """Pass-through to :meth:`Planner.clear_shadow_trace`."""

        self._planner.clear_shadow_trace()

    def dispatch(
        self,
        intent: ChemistryIntent,
        *,
        temperature_C: float,
        pressure_bar: float,
        fO2_log: Optional[float] = None,
        fe_redox_policy: str = "intrinsic",
        control_inputs: Optional[Mapping[str, Any]] = None,
        declared_accounts: Optional[frozenset[str]] = None,
        account_mol_overrides: Optional[Mapping[str, Mapping[str, float]]] = None,
    ) -> IntentResult:
        """Orchestrate one chemistry intent end-to-end.

        Args:
            intent: The :class:`ChemistryIntent` to dispatch.
            temperature_C, pressure_bar, fO2_log: Engine controls.
            control_inputs: Extra control payload routed through to the
                provider unchanged.
            declared_accounts: Override the authoritative provider's
                declared-accounts set; defaults to whatever the
                authoritative provider declares in its
                :class:`CapabilityProfile`.

        Returns the authoritative provider's :class:`IntentResult`
        AFTER it has passed every kernel validator.  If the
        authoritative provider raises
        :class:`ProviderUnavailableError`, returns ``status='unavailable'``,
        or is absent AND ``intent`` is in :attr:`allow_fallback_intents`
        AND a fallback is registered, the dispatch is retried against the
        fallback provider; the fallback result is returned with a
        ``kernel_fallback_used`` diagnostic key surfaced for trace consumers.

        Raises:
            ProviderUnavailableError: No authoritative provider for
                ``intent``, or the authoritative provider raised
                ``ProviderUnavailableError`` and either no fallback is
                registered or the caller did not opt into fallback for
                this intent.
            UnauthorizedIntentError, AccountFilterViolation,
            AtomBalanceError, ControlAuditMismatch: One of the
            validators rejected the authoritative result.
        """

        provider = self._registry.authoritative_for(intent)
        if provider is None:
            fallback = self._registry.fallback_for(intent)
            if fallback is None or intent not in self._allow_fallback_intents:
                raise ProviderUnavailableError(
                    f"no authoritative provider registered for intent {intent.value!r}"
                )
            return self._dispatch_through_provider(
                intent,
                fallback,
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                fe_redox_policy=fe_redox_policy,
                control_inputs=control_inputs,
                declared_accounts=declared_accounts,
                account_mol_overrides=account_mol_overrides,
                role="fallback",
            )

        try:
            result = self._dispatch_through_provider(
                intent,
                provider,
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                fe_redox_policy=fe_redox_policy,
                control_inputs=control_inputs,
                declared_accounts=declared_accounts,
                account_mol_overrides=account_mol_overrides,
                role="authoritative",
            )
            if str(result.status) != "unavailable":
                return result
            fallback = self._registry.fallback_for(intent)
            if fallback is None or intent not in self._allow_fallback_intents:
                return result
            return self._dispatch_through_provider(
                intent,
                fallback,
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                fe_redox_policy=fe_redox_policy,
                control_inputs=control_inputs,
                declared_accounts=declared_accounts,
                account_mol_overrides=account_mol_overrides,
                role="fallback",
            )
        except ProviderUnavailableError:
            # Goal #10 ``VAPOROCK-AUTHORITY-PROMOTION``: authority swap
            # without silent fallback.  The kernel only consults the
            # registered fallback when the caller opted in for THIS
            # intent.  Otherwise re-raise -- the contract is loud
            # failure, not a quiet downgrade.
            fallback = self._registry.fallback_for(intent)
            if fallback is None or intent not in self._allow_fallback_intents:
                raise
            return self._dispatch_through_provider(
                intent,
                fallback,
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                fe_redox_policy=fe_redox_policy,
                control_inputs=control_inputs,
                declared_accounts=declared_accounts,
                account_mol_overrides=account_mol_overrides,
                role="fallback",
            )

    def _dispatch_through_provider(
        self,
        intent: ChemistryIntent,
        provider: ChemistryProvider,
        *,
        temperature_C: float,
        pressure_bar: float,
        fO2_log: Optional[float],
        fe_redox_policy: str,
        control_inputs: Optional[Mapping[str, Any]],
        declared_accounts: Optional[frozenset[str]],
        account_mol_overrides: Optional[Mapping[str, Mapping[str, float]]],
        role: str,
    ) -> IntentResult:
        """Build the IntentRequest, dispatch, and validate.

        Shared implementation between the authoritative path and the
        fallback retry path.  ``role`` is ``"authoritative"`` or
        ``"fallback"``; it controls which planner call is made
        (authoritative path runs the full shadow + parity sweep;
        fallback runs a bare ``provider.dispatch`` because the planner's
        shadow trace was already populated by the authoritative attempt
        that raised) and is surfaced as a diagnostic key so trace
        consumers can tell which slot answered.
        """

        profile = provider.capability_profile()
        if declared_accounts is None:
            declared_accounts = profile.declared_accounts
        declared_accounts = frozenset(declared_accounts or ())

        account_view = build_provider_account_view(
            self._ledger,
            declared_accounts,
            self._species_formula_registry,
            account_mol_overrides=account_mol_overrides,
        )
        request = IntentRequest(
            intent=intent,
            account_view=account_view,
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            fe_redox_policy=fe_redox_policy,
            control_inputs=control_inputs or {},
        )
        if role == "authoritative":
            result = self._planner.dispatch(request)
        else:
            # Fallback path -- bypass the planner's authoritative dispatch
            # (which would re-raise ``ProviderUnavailableError``) and call
            # the fallback provider directly.  Shadows are not re-run on
            # the fallback retry: the authoritative attempt already
            # appended their per-provider records to the trace before it
            # raised, and re-running them against the fallback would
            # pollute the trace with parity records the planner never
            # had a chance to produce in the first place.
            result = provider.dispatch(request)

        if result.intent != intent:
            raise KernelError(
                f"provider {profile.provider_id!r} returned intent "
                f"{result.intent.value!r} for request {intent.value!r}"
            )

        if result.transition is not None:
            validate_intent_authority(intent, profile)
            validate_proposal_accounts(result.transition, declared_accounts)
            validate_atom_balance(result.transition, self._species_formula_registry)
        if result.control_audit is not None:
            validate_control_audit(result.control_audit, request)

        # Tag the result so a trace consumer can tell whether the
        # authoritative or the fallback provider answered.  The kernel
        # adds the marker only on the fallback path -- a successful
        # authoritative dispatch returns the provider's result
        # unchanged so the existing test suite's diagnostic-equality
        # assertions do not need a per-intent waiver.
        if role == "fallback":
            tagged_diagnostic = dict(result.diagnostic or {})
            tagged_diagnostic["kernel_fallback_used"] = (
                profile.provider_id
            )
            result = IntentResult(
                intent=result.intent,
                status=result.status,
                transition=result.transition,
                control_audit=result.control_audit,
                diagnostic=tagged_diagnostic,
                warnings=result.warnings,
            )
        return result

    def commit_batch(
        self,
        intent: ChemistryIntent,
        proposal: LedgerTransitionProposal,
    ) -> LedgerTransition:
        """Apply ``proposal`` to the ledger -- the ONLY writable path.

        Re-runs the full pre-commit validator stack against the current
        registry (defence in depth: the proposal DTO is in the public
        surface, so a replay harness or future shadow tool may submit
        one off the dispatch path -- atom balance alone is not enough
        gate).  In order:

        1. :func:`validate_intent_authority` -- the registry's
           authoritative provider for ``intent`` must declare it in its
           ``is_authoritative_for`` set.
        2. :func:`validate_proposal_accounts` -- every account touched
           must be in the authoritative provider's
           :attr:`CapabilityProfile.declared_accounts`.
        3. :func:`validate_atom_balance` -- conservation gate.

        Translates the validated proposal into a canonical
        :class:`LedgerTransition` and applies it via
        :meth:`AtomLedger.apply`.

        Args:
            intent: The :class:`ChemistryIntent` this proposal was
                produced for. Re-validation looks up the authoritative
                provider via the registry; mismatched or unauthoritative
                intents raise :class:`ProviderUnavailableError` /
                :class:`UnauthorizedIntentError`.
            proposal: The :class:`LedgerTransitionProposal` to commit.

        Returns the applied :class:`LedgerTransition` so callers can
        record it in their trace.  Raises :class:`ProposalRejected` on
        any pre-commit failure other than the kernel's own invariant
        violations (which surface as their original
        :class:`KernelError` subclasses).
        """

        provider = self._registry.authoritative_for(intent)
        if provider is None:
            raise ProviderUnavailableError(
                f"no authoritative provider registered for intent {intent.value!r}; "
                "cannot commit proposal"
            )
        profile = provider.capability_profile()
        # Re-check intent authority + account-filter at commit time so
        # an off-path proposal (replay, future API, hand-built test)
        # cannot bypass the dispatch-time gates.  Defence in depth
        # matches the docstring contract.
        validate_intent_authority(intent, profile)
        validate_proposal_accounts(proposal, profile.declared_accounts)

        try:
            validate_atom_balance(proposal, self._species_formula_registry)
        except KernelError:
            raise
        except Exception as exc:  # noqa: BLE001 -- surface as ProposalRejected
            raise ProposalRejected(str(exc)) from exc

        transition = _proposal_to_ledger_transition(
            proposal, self._species_formula_registry
        )
        try:
            return self._ledger.apply(transition)
        except Exception as exc:  # noqa: BLE001
            raise ProposalRejected(str(exc)) from exc

    def _transition_lots_to_proposal_accounts(
        self,
        lots: tuple[Any, ...],
    ) -> dict[str, dict[str, float]]:
        by_account: dict[str, dict[str, float]] = {}
        for lot in lots:
            account_totals = by_account.setdefault(lot.account, {})
            for species, moles in lot.species_moles_for(
                self._species_formula_registry
            ).items():
                account_totals[species] = (
                    account_totals.get(species, 0.0) + moles
                )
        return by_account

    def commit_validated_transition(
        self,
        intent: ChemistryIntent,
        transition: LedgerTransition,
    ) -> LedgerTransition:
        """Validate and apply an already-materialized ledger transition.

        This is the narrow legacy-backend chokepoint: it runs the same
        authority, declared-account, and atom-balance gates as
        :meth:`commit_batch`, then applies the original transition inside
        the kernel so lot source/meta/reason stay byte-identical to the
        backend output.
        """

        proposal = LedgerTransitionProposal(
            debits=self._transition_lots_to_proposal_accounts(transition.debits),
            credits=self._transition_lots_to_proposal_accounts(transition.credits),
            reason=transition.name,
        )
        provider = self._registry.authoritative_for(intent)
        if provider is None:
            raise ProviderUnavailableError(
                f"no authoritative provider registered for intent "
                f"{intent.value!r}; cannot commit transition"
            )
        profile = provider.capability_profile()
        validate_intent_authority(intent, profile)
        validate_proposal_accounts(proposal, profile.declared_accounts)
        try:
            validate_atom_balance(proposal, self._species_formula_registry)
        except KernelError:
            raise
        except Exception as exc:  # noqa: BLE001 -- surface as ProposalRejected
            raise ProposalRejected(str(exc)) from exc
        try:
            return self._ledger.apply(transition)
        except Exception as exc:  # noqa: BLE001
            raise ProposalRejected(str(exc)) from exc
