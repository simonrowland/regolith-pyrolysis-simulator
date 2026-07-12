"""Kernel invariant: the ChemistryKernel commit chokepoints
(:meth:`commit_batch` and :meth:`commit_validated_transition`) are the
only writers into the AtomLedger.

The kernel never exposes the :class:`AtomLedger` to providers.  The
provider ABC carries no ledger reference, the
:class:`IntentRequest` DTO does not expose the ledger, and providers
have no transitive route to it.  This test file proves those holes are
plugged at the API level.

It also exercises the positive path: a well-formed proposal that goes
through :meth:`commit_batch` actually lands in the ledger.
"""

from __future__ import annotations

import inspect
import json

import pytest

from simulator.accounting.ledger import AtomLedger, LedgerTransition
from simulator.chemistry.kernel import (
    AccountFilterViolation,
    AtomBalanceError,
    CapabilityProfile,
    ChemistryIntent,
    ChemistryKernel,
    ChemistryProvider,
    IntentRequest,
    IntentResult,
    LedgerTransitionProposal,
    ProposalRejected,
    ProviderRegistry,
    ProviderUnavailableError,
    UnauthorizedIntentError,
)


def test_provider_abc_does_not_accept_ledger_reference():
    """A :class:`ChemistryProvider` has no ledger field or method."""

    members = dict(inspect.getmembers(ChemistryProvider))
    public_names = {
        name for name in members if not name.startswith("_")
    }
    for forbidden in ("ledger", "atom_ledger", "commit", "commit_batch", "apply"):
        assert forbidden not in public_names, (
            f"ChemistryProvider exposes forbidden attribute {forbidden!r}"
        )


def test_intent_request_does_not_expose_ledger_reference():
    """The request DTO carries only a filtered view, not the ledger."""

    fields = {f for f in IntentRequest.__dataclass_fields__}
    for forbidden in ("ledger", "atom_ledger"):
        assert forbidden not in fields, (
            f"IntentRequest exposes forbidden field {forbidden!r}"
        )


def test_provider_can_only_reach_ledger_via_kernel_commit_batch():
    """Walk the public API surface and confirm there is no provider->ledger path.

    Every public method of :class:`ChemistryProvider` returns either an
    :class:`IntentResult` or its :class:`CapabilityProfile`.  There is
    no setter or getter for a ledger reference, no callback hook, and
    no other route into the kernel's writable path.  This is a
    structural assertion -- if someone adds such a path in the future,
    this test will trip.
    """

    public_provider_methods = {
        name for name, obj in inspect.getmembers(ChemistryProvider)
        if not name.startswith("_") and callable(obj)
    }
    # Whitelist of methods/attrs the provider ABC may carry.
    allowed = {"capability_profile", "dispatch", "emits_ledger_transition", "name"}
    extras = public_provider_methods - allowed
    assert extras == set(), (
        f"ChemistryProvider has unexpected public members: {sorted(extras)}; "
        f"a new public surface needs review for ledger-leak risk"
    )


# ---------------------------------------------------------------------------
# Positive path: commit_batch applies the proposal to the ledger.


class _CommitProvider(ChemistryProvider):
    name = "commit_provider"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="commit_provider",
            intents=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
            is_authoritative_for=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
            declared_accounts=frozenset(
                {"process.cleaned_melt", "process.overhead_gas"}
            ),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"SiO2": 0.25}},
                credits={"process.overhead_gas": {"SiO2": 0.25}},
                reason="evap_step",
            ),
            control_audit=None,
            diagnostic={},
            warnings=(),
        )


class _UnavailableCommitProvider(_CommitProvider):
    name = "unavailable_commit_provider"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.EVAPORATION_TRANSITION}
            ),
            declared_accounts=frozenset(
                {"process.cleaned_melt", "process.overhead_gas"}
            ),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        raise ProviderUnavailableError("authoritative provider unavailable")


class _FallbackCommitProvider(_CommitProvider):
    name = "fallback_commit_provider"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.EVAPORATION_TRANSITION}
            ),
            declared_accounts=frozenset(
                {"process.cleaned_melt", "process.condensation_train"}
            ),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"SiO2": 0.25}},
                credits={"process.condensation_train": {"SiO2": 0.25}},
                reason="fallback_evap_step",
            ),
        )


class _RuntimeDriftProvider(_CommitProvider):
    name = "runtime_drift_provider"

    def __init__(self) -> None:
        self.drop_capability = False
        self.dispatch_calls = 0

    def capability_profile(self) -> CapabilityProfile:
        intents = (
            frozenset()
            if self.drop_capability
            else frozenset({ChemistryIntent.EVAPORATION_TRANSITION})
        )
        return CapabilityProfile(
            provider_id=self.name,
            intents=intents,
            is_authoritative_for=intents,
            declared_accounts=frozenset(
                {"process.cleaned_melt", "process.overhead_gas"}
            ),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        self.dispatch_calls += 1
        return super().dispatch(request)


class _BackendEquilibriumProvider(ChemistryProvider):
    name = "backend_equilibrium_provider"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.BACKEND_EQUILIBRIUM}),
            is_authoritative_for=frozenset({
                ChemistryIntent.BACKEND_EQUILIBRIUM
            }),
            declared_accounts=frozenset(
                {"process.cleaned_melt", "process.overhead_gas"}
            ),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        return IntentResult(
            intent=ChemistryIntent.BACKEND_EQUILIBRIUM,
            status="unsupported",
        )


def test_commit_batch_is_sole_writer_and_applies_transition():
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
    registry = ProviderRegistry()
    registry.register(_CommitProvider(), [ChemistryIntent.EVAPORATION_TRANSITION])
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    result = kernel.dispatch(
        ChemistryIntent.EVAPORATION_TRANSITION,
        temperature_C=1500.0,
        pressure_bar=1e-6,
    )
    assert result.transition is not None
    # Before commit_batch -- ledger unchanged.
    assert ledger.mol_by_account("process.cleaned_melt")["SiO2"] == pytest.approx(
        1.0, rel=1e-9
    )
    assert "process.overhead_gas" not in ledger.mol_by_account()

    transition = kernel.commit_batch(
        ChemistryIntent.EVAPORATION_TRANSITION, result.transition
    )
    assert transition is not None

    # After commit_batch -- ledger reflects the transition.
    assert ledger.mol_by_account("process.cleaned_melt")["SiO2"] == pytest.approx(
        0.75, rel=1e-9
    )
    assert ledger.mol_by_account("process.overhead_gas")["SiO2"] == pytest.approx(
        0.25, rel=1e-9
    )
    ledger.assert_balanced()


def test_fallback_proposal_commits_against_its_bound_provider_profile():
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 0.25})
    registry = ProviderRegistry()
    registry.register(
        _UnavailableCommitProvider(),
        [ChemistryIntent.EVAPORATION_TRANSITION],
    )
    registry.register(
        _FallbackCommitProvider(),
        [ChemistryIntent.EVAPORATION_TRANSITION],
        fallback=True,
    )
    kernel = ChemistryKernel(
        ledger,
        registry,
        species_formula_registry={},
        allow_fallback_intents=frozenset(
            {ChemistryIntent.EVAPORATION_TRANSITION}
        ),
    )

    result = kernel.dispatch(
        ChemistryIntent.EVAPORATION_TRANSITION,
        temperature_C=1500.0,
        pressure_bar=1e-6,
    )
    assert result.transition is not None
    assert result.diagnostic["kernel_fallback_used"] == "fallback_commit_provider"

    kernel.commit_batch(ChemistryIntent.EVAPORATION_TRANSITION, result.transition)

    assert ledger.mol_by_account("process.condensation_train")[
        "SiO2"
    ] == pytest.approx(0.25)
    with pytest.raises(ProposalRejected, match="insufficient available"):
        kernel.commit_batch(
            ChemistryIntent.EVAPORATION_TRANSITION,
            result.transition,
        )


def test_off_path_proposal_cannot_borrow_fallback_account_authority():
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
    registry = ProviderRegistry()
    registry.register(
        _UnavailableCommitProvider(),
        [ChemistryIntent.EVAPORATION_TRANSITION],
    )
    registry.register(
        _FallbackCommitProvider(),
        [ChemistryIntent.EVAPORATION_TRANSITION],
        fallback=True,
    )
    kernel = ChemistryKernel(
        ledger,
        registry,
        species_formula_registry={},
        allow_fallback_intents=frozenset(
            {ChemistryIntent.EVAPORATION_TRANSITION}
        ),
    )
    off_path = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 0.25}},
        credits={"process.condensation_train": {"SiO2": 0.25}},
        reason="unbound_fallback_shape",
    )

    with pytest.raises(AccountFilterViolation):
        kernel.commit_batch(ChemistryIntent.EVAPORATION_TRANSITION, off_path)


def test_commit_materialization_retains_exact_mol_provenance():
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
    registry = ProviderRegistry()
    registry.register(_CommitProvider(), [ChemistryIntent.EVAPORATION_TRANSITION])
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})
    proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 0.1}},
        credits={"process.overhead_gas": {"SiO2": 0.1}},
        reason="mol_provenance",
    )

    transition = kernel.commit_batch(ChemistryIntent.EVAPORATION_TRANSITION, proposal)

    report = ledger.close_report()
    json.dumps(report)
    debit = report["transitions"][-1]["debits"][0]
    assert type(debit["species_mol"]) is dict
    assert type(debit["meta"]) is dict
    assert type(debit["meta"]["species_mol"]) is dict
    assert debit["species_mol"] == {"SiO2": pytest.approx(0.1)}
    assert debit["meta"]["amount_basis"] == "mol"
    assert debit["meta"]["species_mol"] == {"SiO2": pytest.approx(0.1)}
    with pytest.raises(TypeError, match="metadata is immutable"):
        transition.debits[0].meta["species_mol"]["SiO2"] = 0.2


def test_committed_nested_metadata_is_detached_and_immutable():
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
    registry = ProviderRegistry()
    registry.register(_CommitProvider(), [ChemistryIntent.EVAPORATION_TRANSITION])
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})
    provenance = {"provider": "original", "sources": ["source-a"]}
    transition_meta = {"provenance": provenance}
    proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 0.1}},
        credits={"process.overhead_gas": {"SiO2": 0.1}},
        reason="immutable_provenance",
    )

    transition = kernel.commit_batch(
        ChemistryIntent.EVAPORATION_TRANSITION,
        proposal,
        transition_meta=transition_meta,
    )
    provenance["provider"] = "mutated"
    provenance["sources"].append("source-b")

    report = ledger.close_report()
    json.dumps(report)
    reported = report["transitions"][-1]["debits"][0]["meta"]["provenance"]
    assert type(reported) is dict
    assert type(reported["sources"]) is list
    assert reported == {"provider": "original", "sources": ["source-a"]}

    committed = transition.debits[0].meta["provenance"]
    assert committed == {"provider": "original", "sources": ("source-a",)}
    with pytest.raises(TypeError, match="metadata is immutable"):
        committed["provider"] = "direct mutation"
    with pytest.raises(TypeError):
        dict.__setitem__(committed, "provider", "descriptor bypass")
    assert committed == {"provider": "original", "sources": ("source-a",)}


def test_runtime_capability_drift_rejects_before_provider_invocation():
    provider = _RuntimeDriftProvider()
    registry = ProviderRegistry()
    registry.register(provider, [ChemistryIntent.EVAPORATION_TRANSITION])
    kernel = ChemistryKernel(AtomLedger(), registry, species_formula_registry={})
    provider.drop_capability = True

    with pytest.raises(UnauthorizedIntentError, match="no longer declares dispatch"):
        kernel.dispatch(
            ChemistryIntent.EVAPORATION_TRANSITION,
            temperature_C=1500.0,
            pressure_bar=1e-6,
        )

    assert provider.dispatch_calls == 0


def test_commit_validated_transition_preserves_original_transition_identity():
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"FeO": 1.0})
    registry = ProviderRegistry()
    registry.register(
        _BackendEquilibriumProvider(),
        [ChemistryIntent.BACKEND_EQUILIBRIUM],
    )
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})
    transition = LedgerTransition(
        name="factsage_equilibrium_phase_update",
        debits=(ledger.debit_mol("process.cleaned_melt", {"FeO": 1.0}),),
        credits=(
            ledger.credit_mol(
                "process.cleaned_melt",
                {"Fe": 1.0},
                source="FactSAGE equilibrium",
                meta={"amount_basis": "mol", "species_mol": {"Fe": 1.0}},
            ),
            ledger.credit_mol(
                "process.overhead_gas",
                {"O2": 0.5},
                source="FactSAGE equilibrium",
                meta={"amount_basis": "mol", "species_mol": {"O2": 0.5}},
            ),
        ),
        reason="FactSAGE equilibrium phase species projected into AtomLedger",
    )

    applied = kernel.commit_validated_transition(
        ChemistryIntent.BACKEND_EQUILIBRIUM,
        transition,
    )

    assert applied == transition
    assert ledger.transitions[-1] == transition
    assert ledger.mol_by_account("process.cleaned_melt")["Fe"] == pytest.approx(1.0)
    assert ledger.mol_by_account("process.overhead_gas")["O2"] == pytest.approx(0.5)


def test_commit_validated_transition_aggregates_duplicate_account_lots():
    ledger = AtomLedger()
    ledger.load_external_mol(
        "process.cleaned_melt",
        {"FeO": 1.0, "SiO2": 1.0},
    )
    registry = ProviderRegistry()
    registry.register(
        _BackendEquilibriumProvider(),
        [ChemistryIntent.BACKEND_EQUILIBRIUM],
    )
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})
    transition = LedgerTransition(
        name="factsage_equilibrium_phase_update",
        debits=(
            ledger.debit_mol("process.cleaned_melt", {"FeO": 1.0}),
            ledger.debit_mol("process.cleaned_melt", {"SiO2": 1.0}),
        ),
        credits=(
            ledger.credit_mol(
                "process.overhead_gas",
                {"FeO": 1.0, "SiO2": 1.0},
                source="FactSAGE equilibrium",
                meta={
                    "amount_basis": "mol",
                    "species_mol": {"FeO": 1.0, "SiO2": 1.0},
                },
            ),
        ),
        reason="FactSAGE duplicate account lots projected into AtomLedger",
    )
    transition.validate_conservation()

    applied = kernel.commit_validated_transition(
        ChemistryIntent.BACKEND_EQUILIBRIUM,
        transition,
    )

    assert applied == transition
    assert ledger.mol_by_account("process.cleaned_melt") == {}
    assert ledger.mol_by_account("process.overhead_gas")["FeO"] == pytest.approx(1.0)
    assert ledger.mol_by_account("process.overhead_gas")["SiO2"] == pytest.approx(1.0)


def test_commit_batch_rejects_unbalanced_proposal():
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
    registry = ProviderRegistry()
    # Register an authoritative provider so commit_batch's
    # intent-authority + account-filter re-validation has a profile to
    # check against; the atom-balance gate is what should reject the
    # proposal here.
    registry.register(_CommitProvider(), [ChemistryIntent.EVAPORATION_TRANSITION])
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    bad = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 1.0}},
        credits={"process.overhead_gas": {"SiO2": 0.5}},
        reason="bad",
    )
    with pytest.raises(AtomBalanceError):
        kernel.commit_batch(ChemistryIntent.EVAPORATION_TRANSITION, bad)
    # Ledger must be untouched.
    assert ledger.mol_by_account("process.cleaned_melt")["SiO2"] == pytest.approx(
        1.0, rel=1e-9
    )


def test_commit_batch_propagates_overdraft_as_proposal_rejected():
    """Pulling more material than the account holds raises ProposalRejected."""

    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 0.1})
    registry = ProviderRegistry()
    registry.register(_CommitProvider(), [ChemistryIntent.EVAPORATION_TRANSITION])
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    overdraft = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 10.0}},
        credits={"process.overhead_gas": {"SiO2": 10.0}},
        reason="overdraft",
    )
    with pytest.raises(ProposalRejected):
        kernel.commit_batch(ChemistryIntent.EVAPORATION_TRANSITION, overdraft)
    assert ledger.mol_by_account("process.cleaned_melt")["SiO2"] == pytest.approx(
        0.1, rel=1e-9
    )


# ---------------------------------------------------------------------------
# Defence-in-depth: commit_batch re-validates intent authority and the
# account filter against the registry's authoritative provider, NOT just
# atom balance.  These tests exercise the off-dispatch path -- a hand-
# built proposal submitted directly to commit_batch must not bypass the
# gates dispatch() runs.


def test_commit_batch_rejects_proposal_touching_undeclared_terminal_account():
    """A proposal that credits ``terminal.offgas`` (outside the provider's
    declared accounts) must be rejected at commit time -- even though the
    proposal is atom-balanced.

    Previously commit_batch only ran atom-balance; an off-path proposal
    could write to any account the kernel could project to, including
    terminal sinks.  Defence in depth: commit_batch now re-runs
    :func:`validate_proposal_accounts` against the authoritative
    provider's declared set.
    """

    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
    registry = ProviderRegistry()
    # _CommitProvider declares ONLY {process.cleaned_melt,
    # process.overhead_gas}.  terminal.offgas is outside that set.
    registry.register(_CommitProvider(), [ChemistryIntent.EVAPORATION_TRANSITION])
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    off_path = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 0.5}},
        credits={"terminal.offgas": {"SiO2": 0.5}},  # undeclared sink
        reason="off_path_attempt",
    )
    with pytest.raises(AccountFilterViolation):
        kernel.commit_batch(ChemistryIntent.EVAPORATION_TRANSITION, off_path)
    # Ledger untouched.
    assert "terminal.offgas" not in ledger.mol_by_account()
    assert ledger.mol_by_account("process.cleaned_melt")["SiO2"] == pytest.approx(
        1.0, rel=1e-9
    )


class _LiquidusOnlyProvider(ChemistryProvider):
    """Authoritative for SILICATE_LIQUIDUS, NOT for EVAPORATION_TRANSITION."""

    name = "liquidus_only"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="liquidus_only",
            intents=frozenset({ChemistryIntent.SILICATE_LIQUIDUS}),
            is_authoritative_for=frozenset({ChemistryIntent.SILICATE_LIQUIDUS}),
            declared_accounts=frozenset({"process.cleaned_melt"}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=None,
            control_audit=None,
            diagnostic={},
            warnings=(),
        )


def test_commit_batch_rejects_proposal_for_intent_with_no_authoritative_provider():
    """Submitting a proposal for an intent that has no authoritative
    provider must raise :class:`ProviderUnavailableError` at commit time,
    even if the proposal is balanced and touches declared accounts of
    SOME other provider.
    """

    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
    registry = ProviderRegistry()
    # Register an authoritative provider for SILICATE_LIQUIDUS only;
    # NOTHING is registered for EVAPORATION_TRANSITION.
    registry.register(_LiquidusOnlyProvider(), [ChemistryIntent.SILICATE_LIQUIDUS])
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    balanced = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 0.25}},
        credits={"process.overhead_gas": {"SiO2": 0.25}},
        reason="unauthoritative_intent",
    )
    with pytest.raises(ProviderUnavailableError):
        kernel.commit_batch(ChemistryIntent.EVAPORATION_TRANSITION, balanced)
    # Ledger untouched.
    assert "process.overhead_gas" not in ledger.mol_by_account()
    assert ledger.mol_by_account("process.cleaned_melt")["SiO2"] == pytest.approx(
        1.0, rel=1e-9
    )


def test_commit_batch_rejects_unauthoritative_intent_with_drifting_profile(monkeypatch):
    """A provider registered authoritatively, whose profile later drifts
    to drop the intent from ``is_authoritative_for``, must have its
    proposals rejected at commit time.

    The profile drift simulates a buggy patch or a runtime mutation; the
    kernel's commit-time re-validation must not trust dispatch-time
    state.  Mirrors the runtime drift scenario in
    test_kernel_intent_authority.py.
    """

    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
    registry = ProviderRegistry()
    provider = _CommitProvider()
    registry.register(provider, [ChemistryIntent.EVAPORATION_TRANSITION])
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    # Build a balanced proposal touching declared accounts.
    balanced = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 0.25}},
        credits={"process.overhead_gas": {"SiO2": 0.25}},
        reason="drift_test",
    )

    # Drift: provider's profile no longer claims authority for
    # EVAPORATION_TRANSITION.
    drifted_profile = CapabilityProfile(
        provider_id="commit_provider",
        intents=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
        is_authoritative_for=frozenset(),  # drifted
        declared_accounts=frozenset(
            {"process.cleaned_melt", "process.overhead_gas"}
        ),
    )
    monkeypatch.setattr(provider, "capability_profile", lambda: drifted_profile)

    with pytest.raises(UnauthorizedIntentError):
        kernel.commit_batch(ChemistryIntent.EVAPORATION_TRANSITION, balanced)
    # Ledger untouched.
    assert "process.overhead_gas" not in ledger.mol_by_account()
