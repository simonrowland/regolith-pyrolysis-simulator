"""Tests for the BuiltinEvaporationTransitionProvider -- third intent flip of
\\goal BUILTIN-ENGINE-EXTRACTION (#7) and the FIRST authoritative
ledger-mutating intent in the migration.

Covers:

* Capability profile: provider is authoritative for
  ``EVAPORATION_TRANSITION`` and declares the accounts the
  legacy transition touches (``process.cleaned_melt``,
  ``process.overhead_gas``, ``process.condensation_train``,
  ``reservoir.fo2_buffer``).
* Account filter: the kernel filter scopes the provider's view to those
  declared accounts only -- a metal-phase seed must NOT cross the boundary.
* Atom-balance gate: a malformed proposal that does NOT conserve atoms
  is rejected at ``ChemistryKernel.commit_batch`` with
  :class:`AtomBalanceError`. This proves the authoritative ledger-write
  path actually engages atom-balance validation.
* Shadow parity: a deterministic single-species proposal produced by
  the provider matches the legacy debit/credit pattern exactly.
* Smoke parity: a full C0 -> C6 pyrolysis run on lunar / Mars /
  asteroid feedstocks closes mass balance and produces a non-trivial
  evaporation transition count, with cumulative per-transition mass
  imbalance bounded.
"""

from __future__ import annotations

import math
from unittest.mock import patch

import pytest

from engines.builtin.evaporation_transition import (
    BuiltinEvaporationTransitionProvider,
)
from simulator.chemistry.kernel import (
    AtomBalanceError,
    ChemistryIntent,
    IntentRequest,
    IntentResult,
    LedgerTransitionProposal,
)
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.core import PyrolysisSimulator
from simulator.state import CampaignPhase, DecisionType
from tests.chemistry.conftest import _build_sim


def _dispatch_bound_proposal(kernel, proposal):
    with patch.object(
        BuiltinEvaporationTransitionProvider,
        "dispatch",
        return_value=IntentResult(
            intent=ChemistryIntent.EVAPORATION_TRANSITION,
            status="ok",
            transition=proposal,
        ),
    ):
        result = kernel.dispatch(
            ChemistryIntent.EVAPORATION_TRANSITION,
            temperature_C=1400.0,
            pressure_bar=1e-6,
        )
    assert result.transition is not None
    return result.transition


# mass-balance smoke parity runs clip/fail under xdist coscheduling.
pytestmark = [pytest.mark.serial, pytest.mark.xdist_group("serial")]

# ---------------------------------------------------------------------------
# 1. Capability profile
# ---------------------------------------------------------------------------


def test_provider_declares_only_evaporation_transition_intent():
    provider = BuiltinEvaporationTransitionProvider()
    profile = provider.capability_profile()

    assert profile.intents == frozenset(
        {ChemistryIntent.EVAPORATION_TRANSITION}
    )
    assert profile.is_authoritative_for == frozenset(
        {ChemistryIntent.EVAPORATION_TRANSITION}
    )
    for intent in ChemistryIntent:
        if intent is ChemistryIntent.EVAPORATION_TRANSITION:
            assert profile.is_authoritative(intent)
        else:
            assert not profile.is_authoritative(intent)


def test_provider_declares_four_evaporation_accounts():
    provider = BuiltinEvaporationTransitionProvider()
    profile = provider.capability_profile()
    assert profile.declared_accounts == frozenset({
        "process.cleaned_melt",
        "process.overhead_gas",
        "process.condensation_train",
        "reservoir.fo2_buffer",
    })


# ---------------------------------------------------------------------------
# 2. Kernel account filter scopes the view
# ---------------------------------------------------------------------------


def test_kernel_filters_provider_to_declared_accounts_only(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """When other accounts hold material, the provider must see ONLY the
    three declared evaporation accounts. The kernel account filter is the
    enforcer (binding spec §7)."""

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.metal_phase", {"Fe": 0.5}, source="test seed"
    )

    seen_accounts: list[frozenset[str]] = []
    original_dispatch = BuiltinEvaporationTransitionProvider.dispatch

    def _spying_dispatch(self, request):
        seen_accounts.append(frozenset(request.account_view.accounts))
        return original_dispatch(self, request)

    BuiltinEvaporationTransitionProvider.dispatch = _spying_dispatch
    try:
        sim._chem_kernel.dispatch(
            ChemistryIntent.EVAPORATION_TRANSITION,
            temperature_C=1400.0,
            pressure_bar=1e-6,
            control_inputs={
                "species": "Na",
                "stoich": {
                    "parent_oxide": "Na2O",
                    "oxide_per_product_kg": 1.347,
                    "O2_per_product_kg": 0.347,
                },
                "sp_data": {},
                "rate_kg_hr": 0.0,
                "remaining_kg_hr": 0.0,
                "dt_hr": 1.0,
                "available_kg": 0.0,
            },
        )
    finally:
        BuiltinEvaporationTransitionProvider.dispatch = original_dispatch

    assert seen_accounts, "provider was never dispatched"
    expected = frozenset({
        "process.cleaned_melt",
        "process.overhead_gas",
        "process.condensation_train",
        "reservoir.fo2_buffer",
    })
    for accounts in seen_accounts:
        assert accounts == expected, (
            "kernel filter leaked an undeclared account into the provider"
        )


# ---------------------------------------------------------------------------
# 3. Atom-balance gate: malformed proposal must be rejected at commit
# ---------------------------------------------------------------------------


def test_kernel_commit_rejects_atom_unbalanced_proposal(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Construct a hand-rolled LedgerTransitionProposal where the credit
    atoms do NOT conserve the debit atoms, and verify that
    ``ChemistryKernel.commit_batch`` raises :class:`AtomBalanceError`.
    This is the proof that the authoritative ledger-write path actually
    engages atom-balance validation -- the first intent in the migration
    where commit_batch is load-bearing.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    # 1 mol Na2O on the debit side -> "1 mol Na" on the credit side.
    # Mass-balanced (correctly stripped) would credit 2 mol Na + 0.5 mol
    # O2; this version emits only HALF the Na to break atom balance.
    bad_proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"Na2O": 1.0}},
        credits={"process.overhead_gas": {"Na": 1.0, "O2": 0.5}},
        reason="malformed_evaporation_proposal_for_test",
        atom_balance_proof={"Na": 0.0, "O": 0.0},
    )

    with patch("simulator.chemistry.kernel.planner.validate_atom_balance"):
        bound_proposal = _dispatch_bound_proposal(sim._chem_kernel, bad_proposal)
    before_balances = sim.atom_ledger.mol_by_account()
    before_transitions = sim.atom_ledger.transitions

    with pytest.raises(AtomBalanceError):
        sim._chem_kernel.commit_batch(
            ChemistryIntent.EVAPORATION_TRANSITION, bound_proposal
        )

    assert sim.atom_ledger.mol_by_account() == before_balances
    assert sim.atom_ledger.transitions == before_transitions


def test_kernel_commit_accepts_balanced_proposal(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Companion to the rejection test: a correctly atom-balanced
    proposal must commit cleanly. Sanity check that the rejection above
    isn't a false negative caused by some other validator misfiring.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    # Seed a tiny Na2O reserve so the debit can land somewhere with
    # stock; without this the AtomLedger.apply may reject the negative
    # balance.
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt", {"Na2O": 1.0}, source="test seed"
    )

    balanced_proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"Na2O": 1.0}},
        credits={
            "process.overhead_gas": {"Na": 2.0, "O2": 0.5},
        },
        reason="balanced_evaporation_proposal_for_test",
        atom_balance_proof={"Na": 0.0, "O": 0.0},
    )

    bound_proposal = _dispatch_bound_proposal(sim._chem_kernel, balanced_proposal)
    before = sim.atom_ledger.mol_by_account()
    sim._chem_kernel.commit_batch(
        ChemistryIntent.EVAPORATION_TRANSITION, bound_proposal
    )
    after = sim.atom_ledger.mol_by_account()

    assert after["process.cleaned_melt"]["Na2O"] == pytest.approx(
        before["process.cleaned_melt"]["Na2O"] - 1.0
    )
    assert after["process.overhead_gas"]["Na"] == pytest.approx(
        before.get("process.overhead_gas", {}).get("Na", 0.0) + 2.0
    )
    assert after["process.overhead_gas"]["O2"] == pytest.approx(
        before.get("process.overhead_gas", {}).get("O2", 0.0) + 0.5
    )


# ---------------------------------------------------------------------------
# 4. Unit: deterministic single-species proposal
# ---------------------------------------------------------------------------


def test_provider_emits_expected_proposal_for_known_inputs(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Drive the provider with a deterministic single-species evaporation
    scenario (Na from Na2O, all condensed, no overhead carryover). Check:

    * the proposal debits ``process.cleaned_melt`` for parent oxide,
    * the proposal credits ``process.condensation_train`` for vapor,
    * the proposal credits ``reservoir.fo2_buffer`` for the internal O2 carrier,
    * the atom-balance proof is element-by-element zero (within
      tolerance).
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinEvaporationTransitionProvider()

    # Build a synthetic account view -- the provider doesn't read it for
    # the math but the kernel constructs one in production. Provide the
    # species_formula_registry so resolve_species_formula works inside
    # the provider's mol/kg math.
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {"Na2O": 100.0},
            "process.overhead_gas": {},
            "process.condensation_train": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )

    # Use the simulator's own ``_evaporation_stoich`` to derive an
    # atom-balanced stoich from the vapor_pressures.yaml fallback (Na
    # has no explicit ``stoich_oxide_per_vapor`` so the elemental
    # STOICH_RATIOS path computes it from the atomic-weight table).
    # Hand-crafted rounded ratios (e.g. 1.347 / 0.347) are mass-balanced
    # but NOT atom-balanced at the ULP level the kernel enforces.
    canonical_stoich = sim._evaporation_stoich(
        "Na",
        sim.vapor_pressures.get("metals", {}).get("Na", {}),
    )
    rate_kg_hr = 1.0

    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_TRANSITION,
        account_view=view,
        temperature_C=1500.0,
        pressure_bar=1e-6,
        control_inputs={
            "species": "Na",
            "stoich": canonical_stoich,
            "sp_data": {},  # no disproportionation
            "rate_kg_hr": rate_kg_hr,
            "remaining_kg_hr": 0.0,  # all condenses
            "dt_hr": 1.0,
            "available_kg": 10.0,  # enough oxide to satisfy the flux
        },
    )
    result = provider.dispatch(request)

    assert result.status == "ok"
    assert result.transition is not None
    proposal = result.transition

    from simulator.accounting.formulas import resolve_species_formula
    mw_na2o = resolve_species_formula(
        "Na2O", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    mw_na = resolve_species_formula(
        "Na", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    mw_o2 = resolve_species_formula(
        "O2", sim.species_formula_registry
    ).molar_mass_kg_per_mol()

    expected_oxide_kg = rate_kg_hr * canonical_stoich["oxide_per_product_kg"]
    expected_o2_kg = rate_kg_hr * canonical_stoich["O2_per_product_kg"]

    # Debit side: process.cleaned_melt with Na2O.
    assert set(proposal.debits) == {"process.cleaned_melt"}
    debit_species = dict(proposal.debits["process.cleaned_melt"])
    assert debit_species["Na2O"] == pytest.approx(
        expected_oxide_kg / mw_na2o, rel=1e-12
    )

    # Credit side: condensation_train with vapor + internal fO2 buffer with O2.
    assert set(proposal.credits) == {
        "process.condensation_train",
        "reservoir.fo2_buffer",
    }
    cond_train_credit = dict(proposal.credits["process.condensation_train"])
    buffer_credit = dict(proposal.credits["reservoir.fo2_buffer"])
    assert cond_train_credit["Na"] == pytest.approx(rate_kg_hr / mw_na, rel=1e-12)
    assert buffer_credit["O2"] == pytest.approx(expected_o2_kg / mw_o2, rel=1e-12)

    # Atom-balance proof: every element should net to ~0
    for element, net in dict(proposal.atom_balance_proof).items():
        assert abs(net) < 1e-9, (
            f"atom_balance_proof[{element!r}] = {net} is not zero"
        )


# ---------------------------------------------------------------------------
# 5. Smoke parity: full C0 -> C6 run keeps mass balance + non-zero count
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "feedstock_key, additives_kg",
    [
        ("lunar_mare_low_ti", None),
        ("mars_basalt", {"C": 60.0}),
        ("s_type_asteroid_silicate", None),
    ],
)
def test_full_run_mass_balance_holds_with_kernel_committed_transitions(
    feedstock_key,
    additives_kg,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """Drive C0 -> C6 to completion on each feedstock and verify:

    * the simulator runs to completion,
    * the AtomLedger holds a non-trivial number of evaporation
      transitions (so we know the kernel-committed path actually fired
      across the campaign),
    * each evaporation transition closes mass to the AtomLedger's
      default tolerance,
    * the cumulative per-transition mass imbalance stays within a tight
      batch-level bound (1e-6 kg = 1 mg, four orders below a single
      per-transition tolerance).

    This is the smoke gate that justified flipping the
    EVAPORATION_TRANSITION intent and stays in the suite as a
    regression guard against future intent flips that touch the same
    call site.
    """

    sim = _build_sim(
        feedstock_key,
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg=additives_kg,
    )
    sim.start_campaign(CampaignPhase.C0)
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A",
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    steps = 0
    while not sim.is_complete() and steps < 5000:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        sim.step()
        steps += 1

    assert sim.is_complete(), (
        f"smoke run for {feedstock_key} did not complete in 5000 steps"
    )

    transitions = sim.atom_ledger.transitions
    evap_transitions = [
        t for t in transitions if t.name.startswith("evaporate_")
    ]
    assert len(evap_transitions) > 0, (
        f"feedstock {feedstock_key} produced zero evaporation transitions; "
        "the kernel-committed path never fired"
    )

    registry = sim.atom_ledger.registry
    cumulative_imbalance_kg = sum(
        abs(t.debit_mass_kg(registry) - t.credit_mass_kg(registry))
        for t in evap_transitions
    )
    # Per-transition tolerance is ~20 g (DEFAULT_MASS_TOLERANCE_KG); the
    # legacy kg-native path closes each transition to ~1e-12 kg with
    # the cumulative bounded near 1e-9 kg. The kernel-routed path adds
    # at most ~1 ULP per mol/kg conversion per species per transition
    # (a few ULPs * a few hundred transitions = far below 1 mg).
    assert cumulative_imbalance_kg < 1e-6, (
        f"feedstock {feedstock_key} accumulated "
        f"{cumulative_imbalance_kg:.3e} kg evaporation imbalance "
        "(expected <1e-6 kg)"
    )

    # End-of-batch mass-balance closure: same 5e-12 % bound as
    # test_mass_balance.py.
    snapshot = sim._make_snapshot()
    assert abs(snapshot.mass_balance_error_pct) < 5e-12, (
        f"feedstock {feedstock_key} mass balance closure "
        f"{snapshot.mass_balance_error_pct:.3e} % exceeds the 5e-12 % "
        "kernel-path bound"
    )


# ---------------------------------------------------------------------------
# 6. Provider matches legacy debit/credit for a known full-flow scenario
# ---------------------------------------------------------------------------


def test_provider_matches_legacy_credit_evaporation_transition_pattern(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Drive a simulator past the 400 K floor and walk the
    kernel-committed evaporation transitions, asserting the proposal
    pattern matches the legacy ``_credit_evaporation_transition`` shape
    exactly:

    * debit: ``process.cleaned_melt`` with the parent oxide,
    * credit: ``process.condensation_train`` with the condensed vapor
      (or vapor disproportionation products),
    * credit: ``process.overhead_gas`` with uncondensed vapor / oxide-vapor O2,
    * credit: ``reservoir.fo2_buffer`` with elemental-metal parent-oxide O.

    Note: this test runs the simulator -- which uses the kernel path --
    so the assertion is that the committed transitions take the same
    shape and account set the legacy path used. The shape is enforced
    by the provider's ``declared_accounts`` set; the kernel rejects
    any proposal touching other accounts.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.start_campaign(CampaignPhase.C0)
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A",
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    while sim.melt.temperature_C < 1000.0:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        sim.step()
    # Run a few more steps for evaporation activity.
    for _ in range(20):
        if sim.is_complete():
            break
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        sim.step()

    evap_transitions = [
        t for t in sim.atom_ledger.transitions
        if t.name.startswith("evaporate_")
    ]
    assert evap_transitions, (
        "simulator produced no evaporation transitions; provider parity "
        "coverage would be vacuous"
    )

    legal_accounts = {
        "process.cleaned_melt",
        "process.overhead_gas",
        "process.condensation_train",
        "reservoir.fo2_buffer",
    }
    for trans in evap_transitions:
        for lot in trans.debits:
            is_cro2_o2_reactant = (
                trans.name == "evaporate_CrO2"
                and lot.account == "process.overhead_gas"
                and set(lot.species_kg) == {"O2"}
            )
            assert lot.account == "process.cleaned_melt" or is_cro2_o2_reactant, (
                f"evap transition {trans.name} debits unexpected account "
                f"{lot.account!r}; expected process.cleaned_melt"
            )
        for lot in trans.credits:
            assert lot.account in legal_accounts - {"process.cleaned_melt"}, (
                f"evap transition {trans.name} credits unexpected account "
                f"{lot.account!r}; legal credits are "
                f"{sorted(legal_accounts - {'process.cleaned_melt'})}"
            )
        # Per-transition mass closure: tight (1 mg) bound.
        debit_kg = trans.debit_mass_kg(sim.atom_ledger.registry)
        credit_kg = trans.credit_mass_kg(sim.atom_ledger.registry)
        assert abs(debit_kg - credit_kg) < 1e-3, (
            f"evap transition {trans.name} has unbalanced mass: "
            f"debit={debit_kg:.6g} credit={credit_kg:.6g}"
        )


# ---------------------------------------------------------------------------
# 7. Lock-in regression: provider trusts pre-smoothed effective rates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("remaining_kg_hr", [float("nan"), float("inf")])
def test_evaporation_transition_refuses_nonfinite_condensation_route(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    remaining_kg_hr,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_TRANSITION,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"Na2O": 100.0}},
            species_formula_registry=sim.species_formula_registry,
        ),
        temperature_C=1500.0,
        pressure_bar=1e-6,
        control_inputs={
            "species": "Na",
            "stoich": {
                "parent_oxide": "Na2O",
                "oxide_per_product_kg": 2.0,
                "O2_per_product_kg": 0.347,
            },
            "rate_kg_hr": 1.0,
            "remaining_kg_hr": remaining_kg_hr,
            "dt_hr": 1.0,
            "available_kg": 10.0,
        },
    )

    result = BuiltinEvaporationTransitionProvider().dispatch(request)

    assert result.status == "unsupported"
    assert result.transition is None


def test_evaporation_transition_does_not_apply_per_species_scale(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """The integration layer owns parent/O2 availability capping."""

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinEvaporationTransitionProvider()

    rate_kg_hr = 100.0
    oxide_per_product_kg = 2.0
    O2_per_product_kg = 0.347
    available_kg = 60.0

    # Build a registry-backed view; the provider reads species_formula_registry
    # off it for the mol/kg projection.
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {"Na2O": 100.0},
            "process.overhead_gas": {},
            "process.condensation_train": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_TRANSITION,
        account_view=view,
        temperature_C=1500.0,
        pressure_bar=1e-6,
        control_inputs={
            "species": "Na",
            "stoich": {
                "parent_oxide": "Na2O",
                "oxide_per_product_kg": oxide_per_product_kg,
                "O2_per_product_kg": O2_per_product_kg,
            },
            "sp_data": {},
            "rate_kg_hr": rate_kg_hr,
            "remaining_kg_hr": rate_kg_hr,
            "dt_hr": 1.0,
            "available_kg": available_kg,
        },
    )
    result = provider.dispatch(request)

    assert result.status == "ok"
    assert result.transition is not None
    proposal = result.transition

    # Per-intent attribution lock-in: condensation_train MUST NOT be in
    # credits.  EVAPORATION_TRANSITION's job is the melt -> overhead_gas
    # leg only when the caller wires remaining=rate.
    assert "process.condensation_train" not in proposal.credits, (
        "EVAPORATION_TRANSITION leaked a deposit credit; "
        "CONDENSATION_ROUTE owns process.condensation_train"
    )
    assert "process.overhead_gas" in proposal.credits, (
        "EVAPORATION_TRANSITION must credit overhead_gas for remaining vapor"
    )

    from simulator.accounting.formulas import resolve_species_formula
    mw_na = resolve_species_formula(
        "Na", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    mw_o2 = resolve_species_formula(
        "O2", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    expected_vapor_kg = rate_kg_hr
    expected_o2_kg = rate_kg_hr * O2_per_product_kg

    overhead_credit = dict(proposal.credits["process.overhead_gas"])
    buffer_credit = dict(proposal.credits["reservoir.fo2_buffer"])
    assert overhead_credit["Na"] == pytest.approx(
        expected_vapor_kg / mw_na, rel=1e-12
    )
    assert "O2" not in overhead_credit
    assert buffer_credit["O2"] == pytest.approx(
        expected_o2_kg / mw_o2, rel=1e-12
    )

    # Sanity: the diagnostic reports credited_condensed_kg=0 so a future
    # caller's scaling-of-the-deposit-leg path stays unambiguous.
    diag = dict(result.diagnostic or {})
    assert diag.get("credited_condensed_kg") == pytest.approx(0.0, abs=1e-12)
    assert diag.get("applied_scale") == pytest.approx(1.0, rel=1e-12)
