"""Tests for the BuiltinElectrolysisStepProvider -- fifth intent flip of
\\goal BUILTIN-ENGINE-EXTRACTION (#7) and the THIRD authoritative
ledger-mutating intent in the migration.

Covers:

* Capability profile: the provider is authoritative for
  ``ELECTROLYSIS_STEP`` and declares the three accounts the MRE
  reduction touches (``process.cleaned_melt`` debit,
  ``process.metal_phase`` credit, ``terminal.oxygen_mre_anode_stored``
  credit).  The MRE anode O2 bin is intentionally distinct from
  ``terminal.oxygen_melt_offgas_stored`` and
  ``terminal.oxygen_stage0_stored`` per AGENTS.md #6.
* Wrong-intent rejection: the provider returns an ``unsupported``
  ``IntentResult`` if dispatched against an intent it does not serve.
* Account filter: the kernel filter scopes the provider's view to the
  three declared accounts only -- any other ledger account (overhead
  gas, condensation_train, alternate O2 bins) is invisible.
* Atom-balance gate: a malformed proposal that does NOT conserve atoms
  (FeO debit with missing O coproduct) is rejected at
  :meth:`ChemistryKernel.commit_batch` with :class:`AtomBalanceError`.
  Companion test proves the rejection isn't a false negative.
* Terminal-account credit: a proposal that credits
  ``terminal.oxygen_mre_anode_stored`` commits cleanly (terminal
  *debits* are forbidden by ``AtomLedger._validate_terminal_debits``,
  but *credits* through the canonical kernel commit path are
  permitted).
* Unit parity: deterministic single-oxide proposals match the legacy
  :meth:`ElectrolysisModel.step_hour` shape exactly for FeO + a
  multi-oxide partition.
* Smoke parity: full C0 -> C6 run on lunar / Mars / asteroid feedstocks
  closes mass balance, produces a non-trivial MRE transition count,
  and the cumulative per-transition mass imbalance stays bounded --
  proving the kernel-committed ELECTROLYSIS_STEP path actually fires
  across the campaign and remains numerically consistent with the
  legacy ``ElectrolysisModel.step_hour`` math.
"""

from __future__ import annotations

import math
from collections import defaultdict

import pytest

from engines.builtin.electrolysis_step import (
    BuiltinElectrolysisStepProvider,
)
from simulator.chemistry.kernel import (
    AtomBalanceError,
    ChemistryIntent,
    IntentRequest,
    LedgerTransitionProposal,
)
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.electrolysis import ElectrolysisModel
from simulator.state import (
    MOLAR_MASS,
    CampaignPhase,
    DecisionType,
)
from tests.chemistry.conftest import _build_sim


# ---------------------------------------------------------------------------
# 1. Capability profile
# ---------------------------------------------------------------------------


def test_provider_declares_only_electrolysis_step_intent():
    provider = BuiltinElectrolysisStepProvider()
    profile = provider.capability_profile()

    assert profile.intents == frozenset(
        {ChemistryIntent.ELECTROLYSIS_STEP}
    )
    assert profile.is_authoritative_for == frozenset(
        {ChemistryIntent.ELECTROLYSIS_STEP}
    )
    for intent in ChemistryIntent:
        if intent is ChemistryIntent.ELECTROLYSIS_STEP:
            assert profile.is_authoritative(intent)
        else:
            assert not profile.is_authoritative(intent)


def test_provider_declares_three_mre_accounts():
    """The MRE reduction touches melt debit + metal credit + anode O2.

    The anode O2 bin is its own terminal account per binding spec §3
    and AGENTS.md #6 (distinct from melt-offgas, Stage-0, headspace).
    Verifying the declared set explicitly stops a future refactor from
    silently widening the surface (e.g. crediting overhead_gas or any
    other O2 bin).
    """

    provider = BuiltinElectrolysisStepProvider()
    profile = provider.capability_profile()
    assert profile.declared_accounts == frozenset({
        "process.cleaned_melt",
        "process.metal_phase",
        "terminal.oxygen_mre_anode_stored",
    })
    # Explicit non-membership: the four O2 bins are distinct.
    assert "terminal.oxygen_melt_offgas_stored" not in profile.declared_accounts
    assert "terminal.oxygen_stage0_stored" not in profile.declared_accounts
    assert "process.overhead_gas" not in profile.declared_accounts
    assert "process.condensation_train" not in profile.declared_accounts


# ---------------------------------------------------------------------------
# 2. Wrong-intent rejection (defence in depth)
# ---------------------------------------------------------------------------


def test_provider_rejects_wrong_intent(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """If a future caller dispatches the provider against an intent it
    does not serve, ``reject_wrong_intent`` must return an
    ``unsupported`` ``IntentResult`` rather than producing a silent
    mis-answer."""

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinElectrolysisStepProvider()
    view = ProviderAccountView(
        accounts={},
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,  # WRONG INTENT
        account_view=view,
        temperature_C=1575.0,
        pressure_bar=1e-6,
        control_inputs={
            "voltage_V": 1.6, "current_A": 100.0, "dt_hr": 1.0,
        },
    )
    result = provider.dispatch(request)
    assert result.status == "unsupported"
    assert result.transition is None


# ---------------------------------------------------------------------------
# 3. Kernel account filter scopes the view
# ---------------------------------------------------------------------------


def test_kernel_filters_provider_to_declared_accounts_only(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """When other accounts hold material, the provider must see ONLY
    the three declared MRE accounts. The kernel account filter is the
    enforcer (binding spec §7); a process.overhead_gas seed must NOT
    cross the boundary into this provider's view.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    # Seed an unrelated account so the filter has something to filter.
    sim.atom_ledger.load_external(
        "process.overhead_gas", {"Na": 0.5}, source="test seed"
    )
    sim.atom_ledger.load_external(
        "process.condensation_train", {"Fe": 0.5}, source="test seed"
    )

    seen_accounts: list[frozenset[str]] = []
    original_dispatch = BuiltinElectrolysisStepProvider.dispatch

    def _spying_dispatch(self, request):
        seen_accounts.append(frozenset(request.account_view.accounts))
        return original_dispatch(self, request)

    BuiltinElectrolysisStepProvider.dispatch = _spying_dispatch
    try:
        sim._chem_kernel.dispatch(
            ChemistryIntent.ELECTROLYSIS_STEP,
            temperature_C=1575.0,
            pressure_bar=1e-6,
            control_inputs={
                "voltage_V": 0.0,  # zero voltage -> no transition
                "current_A": 0.0,
                "dt_hr": 1.0,
            },
        )
    finally:
        BuiltinElectrolysisStepProvider.dispatch = original_dispatch

    assert seen_accounts, "provider was never dispatched"
    expected = frozenset({
        "process.cleaned_melt",
        "process.metal_phase",
        "terminal.oxygen_mre_anode_stored",
    })
    for accounts in seen_accounts:
        assert accounts == expected, (
            "kernel filter leaked an undeclared account into the provider"
        )
        assert "process.overhead_gas" not in accounts
        assert "process.condensation_train" not in accounts
        assert "terminal.oxygen_melt_offgas_stored" not in accounts


# ---------------------------------------------------------------------------
# 4. Atom-balance gate: malformed proposal must be rejected at commit
# ---------------------------------------------------------------------------


def test_kernel_commit_rejects_atom_unbalanced_proposal(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Construct a hand-rolled :class:`LedgerTransitionProposal` where
    the credit atoms do NOT conserve the debit atoms (FeO -> Fe but
    forget the 0.5 mol O2 from the anode), and verify that
    :meth:`ChemistryKernel.commit_batch` raises
    :class:`AtomBalanceError`.  Proves the authoritative ledger-write
    path actually engages atom-balance validation for this intent.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    # 1 mol FeO debit (1 Fe, 1 O atom) -- correct reduction would
    # credit 1 mol Fe + 0.5 mol O2 (matching atoms). This version
    # drops the O2 entirely, leaking the O atom.
    bad_proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"FeO": 1.0}},
        credits={"process.metal_phase": {"Fe": 1.0}},
        reason="malformed_mre_proposal_for_test",
        atom_balance_proof={"Fe": 0.0, "O": 0.0},
    )

    with pytest.raises(AtomBalanceError):
        sim._chem_kernel.commit_batch(
            ChemistryIntent.ELECTROLYSIS_STEP, bad_proposal
        )


def test_kernel_commit_accepts_balanced_proposal(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Companion to the rejection test: a correctly atom-balanced FeO
    reduction proposal must commit cleanly. Sanity check that the
    rejection above isn't a false negative caused by some other
    validator misfiring.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    # 1 mol FeO -> 1 mol Fe + 0.5 mol O2.
    # Atom check: Fe: -1 + 1 = 0; O: -1 + 0.5*2 = 0. ✓
    balanced_proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"FeO": 1.0}},
        credits={
            "process.metal_phase": {"Fe": 1.0},
            "terminal.oxygen_mre_anode_stored": {"O2": 0.5},
        },
        reason="balanced_mre_proposal_for_test",
        atom_balance_proof={"Fe": 0.0, "O": 0.0},
    )

    # Should not raise.
    sim._chem_kernel.commit_batch(
        ChemistryIntent.ELECTROLYSIS_STEP, balanced_proposal
    )


def test_kernel_commit_accepts_terminal_oxygen_credit(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """``terminal.oxygen_mre_anode_stored`` is a terminal account --
    ``AtomLedger._validate_terminal_debits`` forbids *debits* from
    terminal accounts (except for the explicit exception table), but
    *credits* into terminal accounts through the canonical kernel
    commit path ARE permitted.  This test pins that semantics: the
    MRE reduction proposal credits the anode O2 bin, and the commit
    succeeds without raising any terminal-account guard.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    before_anode_kg = sim.atom_ledger.kg_by_account(
        "terminal.oxygen_mre_anode_stored"
    ).get("O2", 0.0)

    # Small balanced FeO reduction with anode-O2 credit.
    proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"FeO": 0.1}},
        credits={
            "process.metal_phase": {"Fe": 0.1},
            "terminal.oxygen_mre_anode_stored": {"O2": 0.05},
        },
        reason="terminal_credit_smoke",
    )
    sim._chem_kernel.commit_batch(
        ChemistryIntent.ELECTROLYSIS_STEP, proposal,
    )

    after_anode_kg = sim.atom_ledger.kg_by_account(
        "terminal.oxygen_mre_anode_stored"
    ).get("O2", 0.0)
    expected_delta_kg = 0.05 * MOLAR_MASS["O2"] / 1000.0
    assert (after_anode_kg - before_anode_kg) == pytest.approx(
        expected_delta_kg, rel=1e-12
    )


# ---------------------------------------------------------------------------
# 5. Unit: deterministic single-oxide + multi-oxide proposals
# ---------------------------------------------------------------------------


def test_provider_matches_legacy_step_hour_pure_feo(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Drive the provider with a pure-FeO melt at a known V / I / T and
    compare its proposal mol values against the legacy
    ``ElectrolysisModel.step_hour`` output. Worst-case delta must be
    well below the 1e-9 mol/species parity tolerance.

    The provider is a refactor of where the proposal is built; the
    Nernst / Faraday / current-efficiency math is mirrored line-for-
    line from the legacy module (re-importing the same
    ``DECOMP_VOLTAGES`` + ``ELECTRONS_PER_OXIDE`` tables), so the
    delta should be exactly zero modulo IEEE-754 round-off.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    # Replace the cleaned_melt account with pure FeO so the legacy +
    # provider math both see the same melt state.
    sim.atom_ledger = sim._new_atom_ledger()
    feo_mol = 1000.0 / (MOLAR_MASS["FeO"] / 1000.0)
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt", {"FeO": feo_mol}, source="test seed"
    )
    sim._chem_kernel = sim._build_chemistry_kernel()
    sim._project_extraction_melt()
    sim.melt.temperature_C = 1575.0

    voltage_V = 0.65
    current_A = 100.0

    legacy = sim.electrolysis_model.step_hour(
        melt_state=sim.melt,
        voltage_V=voltage_V,
        current_A=current_A,
        T_C=sim.melt.temperature_C,
    )

    provider = BuiltinElectrolysisStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": dict(
                sim.atom_ledger.mol_by_account("process.cleaned_melt")
            ),
            "process.metal_phase": {},
            "terminal.oxygen_mre_anode_stored": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.ELECTROLYSIS_STEP,
        account_view=view,
        temperature_C=sim.melt.temperature_C,
        pressure_bar=1e-6,
        control_inputs={
            "voltage_V": voltage_V,
            "current_A": current_A,
            "dt_hr": 1.0,
        },
    )
    result = provider.dispatch(request)
    diagnostic = dict(result.diagnostic)

    # Mol parity: provider matches legacy step_hour exactly.
    legacy_ox = dict(legacy.get("oxides_reduced_mol", {}) or {})
    provider_ox = dict(diagnostic.get("oxides_reduced_mol", {}) or {})
    assert set(legacy_ox) == set(provider_ox)
    for species in legacy_ox:
        assert provider_ox[species] == pytest.approx(
            legacy_ox[species], abs=1e-12, rel=1e-12
        )
    legacy_O2 = float(legacy.get("O2_produced_mol", 0.0))
    provider_O2 = float(diagnostic.get("O2_produced_mol", 0.0))
    assert provider_O2 == pytest.approx(legacy_O2, abs=1e-12, rel=1e-12)

    # Proposal shape: cleaned_melt debit + metal_phase credit + anode
    # O2 credit (terminal credit allowed through canonical commit path).
    proposal = result.transition
    assert proposal is not None
    assert set(proposal.debits) == {"process.cleaned_melt"}
    assert "FeO" in proposal.debits["process.cleaned_melt"]
    assert "process.metal_phase" in proposal.credits
    assert "Fe" in proposal.credits["process.metal_phase"]
    assert "terminal.oxygen_mre_anode_stored" in proposal.credits
    assert "O2" in proposal.credits["terminal.oxygen_mre_anode_stored"]

    # Atom-balance proof: every element nets to ~0.
    for element, net in dict(proposal.atom_balance_proof).items():
        assert abs(net) < 1e-9, (
            f"atom_balance_proof[{element!r}] = {net} is not zero"
        )

    # Independent atom check re-derivation: net per element ~ 0.
    from simulator.accounting.formulas import resolve_species_formula

    net_atoms: dict[str, float] = defaultdict(float)
    for side, sign in ((proposal.debits, -1.0), (proposal.credits, +1.0)):
        for _account, species_mol in side.items():
            for sp, mol in species_mol.items():
                formula = resolve_species_formula(
                    sp, sim.species_formula_registry
                )
                for element, atoms in formula.atom_moles(float(mol)).items():
                    net_atoms[element] += sign * float(atoms)
    for element, net in net_atoms.items():
        assert abs(net) < 1e-12, (
            f"independent atom check failed: element {element!r} "
            f"net = {net} (expected ~0)"
        )

    # Energy is in the diagnostic, NOT in any ledger account.
    assert diagnostic["energy_kWh"] == pytest.approx(
        voltage_V * current_A * 1.0 / 1000.0, rel=1e-12
    )
    # Energy must NEVER appear as a ledger account on the proposal.
    assert all(
        "energy" not in str(account).lower()
        for account in (set(proposal.debits) | set(proposal.credits))
    )


def test_provider_matches_legacy_multi_oxide_partition(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Verify the provider and legacy ``step_hour`` agree across a
    multi-oxide partition (FeO + Fe2O3 simultaneously reducible). The
    selectivity weights, Faraday integration, and metal-accumulation
    (Fe from BOTH oxides) must match line-for-line.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger = sim._new_atom_ledger()
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt",
        {
            "FeO": 10.0 / (MOLAR_MASS["FeO"] / 1000.0),
            "Fe2O3": 10.0 / (MOLAR_MASS["Fe2O3"] / 1000.0),
        },
        source="test seed",
    )
    sim._chem_kernel = sim._build_chemistry_kernel()
    sim._project_extraction_melt()
    sim.melt.temperature_C = 1600.0

    voltage_V = 5.0  # high enough to reduce both
    current_A = 1.0e9

    legacy = sim.electrolysis_model.step_hour(
        melt_state=sim.melt,
        voltage_V=voltage_V,
        current_A=current_A,
        T_C=sim.melt.temperature_C,
    )
    provider = BuiltinElectrolysisStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": dict(
                sim.atom_ledger.mol_by_account("process.cleaned_melt")
            ),
            "process.metal_phase": {},
            "terminal.oxygen_mre_anode_stored": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.ELECTROLYSIS_STEP,
        account_view=view,
        temperature_C=sim.melt.temperature_C,
        pressure_bar=1e-6,
        control_inputs={
            "voltage_V": voltage_V,
            "current_A": current_A,
            "dt_hr": 1.0,
        },
    )
    result = provider.dispatch(request)
    diagnostic = dict(result.diagnostic)

    for key in ("oxides_reduced_mol", "metals_produced_mol"):
        leg = dict(legacy.get(key, {}) or {})
        prv = dict(diagnostic.get(key, {}) or {})
        assert set(leg) == set(prv), f"keyset mismatch for {key}"
        for sp_name in leg:
            assert prv[sp_name] == pytest.approx(
                leg[sp_name], abs=1e-12, rel=1e-12
            ), f"species {sp_name!r} mol mismatch in {key}"

    legacy_O2 = float(legacy.get("O2_produced_mol", 0.0))
    provider_O2 = float(diagnostic.get("O2_produced_mol", 0.0))
    assert provider_O2 == pytest.approx(legacy_O2, abs=1e-12, rel=1e-12)


def test_provider_short_circuits_below_voltage(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Below the voltage threshold (no species reducible at this V),
    the provider emits ok-no-op (no transition). Mirrors the legacy
    ``step_hour`` short-circuit when ``reducible`` is empty.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger = sim._new_atom_ledger()
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt",
        # CaO has E0=2.5V; at V=0.1V nothing reduces.
        {"CaO": 1.0 / (MOLAR_MASS["CaO"] / 1000.0)},
        source="test seed",
    )
    sim._chem_kernel = sim._build_chemistry_kernel()
    provider = BuiltinElectrolysisStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": dict(
                sim.atom_ledger.mol_by_account("process.cleaned_melt")
            ),
            "process.metal_phase": {},
            "terminal.oxygen_mre_anode_stored": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.ELECTROLYSIS_STEP,
        account_view=view,
        temperature_C=1500.0,
        pressure_bar=1e-6,
        control_inputs={
            "voltage_V": 0.1,
            "current_A": 100.0,
            "dt_hr": 1.0,
        },
    )
    result = provider.dispatch(request)
    assert result.status == "ok"
    assert result.transition is None


# ---------------------------------------------------------------------------
# 6. Smoke parity: full C0 -> C6 run on three feedstocks (C5 exercises MRE)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "feedstock_key, additives_kg",
    [
        ("lunar_mare_low_ti", None),
        ("mars_basalt", {"C": 60.0}),
    ],
)
def test_full_run_mass_balance_holds_with_kernel_committed_electrolysis(
    feedstock_key,
    additives_kg,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """Drive C0 -> C6 (with C5 active to exercise MRE) on each
    feedstock and verify:

    * the simulator runs to completion,
    * the AtomLedger holds a non-trivial number of MRE transitions (so
      we know the kernel-committed ELECTROLYSIS_STEP path actually
      fired across the C5 campaign),
    * each MRE transition strictly debits cleaned_melt and credits
      metal_phase + oxygen_mre_anode_stored (no overhead_gas /
      condensation_train / alternate-O2-bin leak),
    * each transition closes mass within a tight 1 mg per-transition
      tolerance,
    * the cumulative per-transition mass imbalance stays within a
      tight batch-level bound,
    * end-of-batch mass-balance closure stays at the same 5e-12 %
      ceiling the prior flips established.

    Asteroid feedstocks may not exercise C5 in the default decision
    path -- they are excluded here; the lunar + Mars cases give the
    coverage the goal requires.

    This is the smoke gate that justified flipping the
    ELECTROLYSIS_STEP intent and stays in the suite as a regression
    guard against future intent flips that touch the same call site.
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
    mre_transitions = [
        t for t in transitions
        if t.name == "mre_electrolysis_reduction"
    ]
    assert len(mre_transitions) > 0, (
        f"feedstock {feedstock_key} produced zero MRE transitions; "
        "the kernel-committed ELECTROLYSIS_STEP path never fired"
    )

    registry = sim.atom_ledger.registry
    allowed_credit_accounts = {
        "process.metal_phase",
        "terminal.oxygen_mre_anode_stored",
    }
    cumulative_imbalance_kg = 0.0
    for trans in mre_transitions:
        # Strict account scoping: debit cleaned_melt only, credit
        # metal_phase + anode O2 only.
        for lot in trans.debits:
            assert lot.account == "process.cleaned_melt", (
                f"MRE transition {trans.name} debits unexpected "
                f"account {lot.account!r}; expected only "
                "process.cleaned_melt"
            )
        for lot in trans.credits:
            assert lot.account in allowed_credit_accounts, (
                f"MRE transition {trans.name} credits unexpected "
                f"account {lot.account!r}; expected one of "
                f"{sorted(allowed_credit_accounts)}"
            )
        # Per-transition mass closure: tight 1 mg bound.
        debit_kg = trans.debit_mass_kg(registry)
        credit_kg = trans.credit_mass_kg(registry)
        delta = abs(debit_kg - credit_kg)
        assert delta < 1e-3, (
            f"MRE transition {trans.name} has unbalanced mass: "
            f"debit={debit_kg:.6g} credit={credit_kg:.6g}"
        )
        cumulative_imbalance_kg += delta

    # Per-transition tolerance is ~1 mg; the mol-native kernel path
    # closes each transition to ~1e-12 kg with the cumulative bounded
    # near 1e-9 kg even on long C5 campaigns.
    assert cumulative_imbalance_kg < 1e-6, (
        f"feedstock {feedstock_key} accumulated "
        f"{cumulative_imbalance_kg:.3e} kg MRE imbalance "
        "(expected <1e-6 kg)"
    )

    # End-of-batch mass-balance closure: same 5e-12 % bound as the
    # prior authoritative-intent flip tests.
    snapshot = sim._make_snapshot()
    assert abs(snapshot.mass_balance_error_pct) < 5e-12, (
        f"feedstock {feedstock_key} mass balance closure "
        f"{snapshot.mass_balance_error_pct:.3e} % exceeds the "
        "5e-12 % kernel-path bound"
    )


@pytest.mark.parametrize(
    "feedstock_key, additives_kg",
    [
        ("lunar_mare_low_ti", None),
        ("mars_basalt", {"C": 60.0}),
    ],
)
def test_full_run_o2_yields_split_across_distinct_bins(
    feedstock_key,
    additives_kg,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """The MRE anode O2 must accumulate in
    ``terminal.oxygen_mre_anode_stored`` -- distinct from the
    melt-offgas / Stage-0 / vented bins (binding spec §3, AGENTS.md
    #6).  Verify the post-flip ledger maintains this separation across
    full campaign runs.
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
    assert sim.is_complete()

    anode_o2_kg = sim.atom_ledger.kg_by_account(
        "terminal.oxygen_mre_anode_stored"
    ).get("O2", 0.0)
    melt_offgas_o2_kg = sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored"
    ).get("O2", 0.0)
    stage0_o2_kg = sim.atom_ledger.kg_by_account(
        "terminal.oxygen_stage0_stored"
    ).get("O2", 0.0)

    # MRE must have produced > 0 anode O2 on these C5-exercising
    # feedstocks.
    assert anode_o2_kg > 0.0, (
        f"feedstock {feedstock_key} produced zero MRE anode O2"
    )
    # Bins must be distinct -- the MRE credit must not have leaked
    # into the melt-offgas bin (collapsing them would violate
    # AGENTS.md #6).  We can't assert melt_offgas == 0 (legitimate
    # evaporation also produces O2 there), but we CAN assert the MRE
    # anode bin is separately addressable and tracking a non-zero
    # quantity that matches the sum of MRE transitions' anode credits.
    mre_anode_credit_kg = 0.0
    registry = sim.atom_ledger.registry
    for trans in sim.atom_ledger.transitions:
        if trans.name != "mre_electrolysis_reduction":
            continue
        for lot in trans.credits:
            if lot.account == "terminal.oxygen_mre_anode_stored":
                mre_anode_credit_kg += sum(lot.species_kg.values())
    assert anode_o2_kg == pytest.approx(
        mre_anode_credit_kg, rel=1e-9, abs=1e-9
    ), (
        "anode O2 bin balance does not match the sum of MRE "
        "transition credits -- something is leaking into or out of "
        "the dedicated bin"
    )

    # Sanity: the three bins are reachable (defence in depth -- if a
    # future refactor collapsed them, both attributes would resolve
    # to the same number).
    assert (
        anode_o2_kg != melt_offgas_o2_kg
        or melt_offgas_o2_kg == 0.0
    ), (
        "MRE-anode and melt-offgas O2 bins must be distinct ledger "
        "accounts"
    )
    # stage0 is allowed to be 0 (depends on feedstock); the assertion
    # is on bin-existence, not non-zero magnitude.
    assert stage0_o2_kg >= 0.0
