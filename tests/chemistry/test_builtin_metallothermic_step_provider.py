"""Tests for the BuiltinMetallothermicStepProvider -- sixth intent flip
of ``\\goal BUILTIN-ENGINE-EXTRACTION`` (#7) and the FOURTH authoritative
ledger-mutating intent in the migration (after EVAPORATION_TRANSITION,
CONDENSATION_ROUTE, ELECTROLYSIS_STEP).

Covers:

* Capability profile: the provider is authoritative for
  ``METALLOTHERMIC_STEP`` and declares the three accounts the C3/C6
  reductions touch (``process.cleaned_melt``,
  ``process.metal_phase``, ``process.reagent_inventory``).
* Wrong-intent rejection: the provider returns an ``unsupported``
  ``IntentResult`` if dispatched against an intent it does not serve.
* Account filter: the kernel filter scopes the provider's view to the
  three declared accounts only -- any other ledger account (overhead
  gas, condensation_train, terminal O2 bins) is invisible.
* Per-family ground-truth proposals: deterministic per-reaction inputs
  yield the exact stoichiometric debit/credit dicts the legacy
  ``_record_atom_transition`` calls would have produced (within
  IEEE-754 round-off on the same operand sequence).  Covered:
  C3 K-shuttle, C3 Na-shuttle (Cr + Ti combined), C6 Mg thermite
  primary, C6 Al-SiO2 back-reduction.
* Atom-balance gate engagement -- a malformed thermite proposal
  (``3 Mg + Al2O3 -> 2 MgO + 2 Al``, missing 1 mol Mg and 1 mol O) is
  rejected at :meth:`ChemistryKernel.commit_batch` with
  :class:`AtomBalanceError`.  Companion accepts-balanced test pins
  that the rejection isn't a false negative.
* Smoke parity: full C0 -> C6 run on lunar + Mars feedstocks closes
  mass balance to the existing 5e-12 % tolerance and the kernel-
  committed shuttle / thermite transitions land in
  process.cleaned_melt / metal_phase / reagent_inventory only.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from engines.builtin.metallothermic_step import (
    BuiltinMetallothermicStepProvider,
    REACTION_FAMILY_C3_K,
    REACTION_FAMILY_C3_NA,
    REACTION_FAMILY_C6_MG,
)
from simulator.chemistry.kernel import (
    AtomBalanceError,
    ChemistryIntent,
    IntentRequest,
    LedgerTransitionProposal,
)
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.state import (
    MOLAR_MASS,
    CampaignPhase,
    DecisionType,
)
from tests.chemistry.conftest import _build_sim


# ---------------------------------------------------------------------------
# 1. Capability profile
# ---------------------------------------------------------------------------


def test_provider_declares_only_metallothermic_step_intent():
    provider = BuiltinMetallothermicStepProvider()
    profile = provider.capability_profile()

    assert profile.intents == frozenset(
        {ChemistryIntent.METALLOTHERMIC_STEP}
    )
    assert profile.is_authoritative_for == frozenset(
        {ChemistryIntent.METALLOTHERMIC_STEP}
    )
    for intent in ChemistryIntent:
        if intent is ChemistryIntent.METALLOTHERMIC_STEP:
            assert profile.is_authoritative(intent)
        else:
            assert not profile.is_authoritative(intent)


def test_provider_declares_three_metallothermic_accounts():
    """Provider declares exactly the three accounts touched by every
    legacy ``_record_atom_transition`` call inside
    ``_shuttle_inject_K``, ``_shuttle_inject_Na``, and
    ``_step_thermite``.  Pinning the set stops a future refactor from
    silently widening the surface (e.g. crediting overhead_gas, any
    terminal O2 bin, or condensation_train).
    """

    provider = BuiltinMetallothermicStepProvider()
    profile = provider.capability_profile()
    assert profile.declared_accounts == frozenset({
        "process.cleaned_melt",
        "process.metal_phase",
        "process.reagent_inventory",
    })
    assert "process.overhead_gas" not in profile.declared_accounts
    assert "process.condensation_train" not in profile.declared_accounts
    assert "terminal.oxygen_mre_anode_stored" not in profile.declared_accounts
    assert "terminal.oxygen_melt_offgas_stored" not in profile.declared_accounts
    assert "terminal.oxygen_stage0_stored" not in profile.declared_accounts


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
    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={},
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.ELECTROLYSIS_STEP,  # WRONG INTENT
        account_view=view,
        temperature_C=1300.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C3_K,
            "reagent_available_kg": 10.0,
            "dt_hr": 1.0,
        },
    )
    result = provider.dispatch(request)
    assert result.status == "unsupported"
    assert result.transition is None


def test_provider_rejects_unknown_reaction_family(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """An unknown ``reaction_family`` discriminator must surface as an
    ``unsupported`` IntentResult, not a silent mis-dispatch.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {},
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=view,
        temperature_C=1300.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": "future_unknown_family",
            "reagent_available_kg": 1.0,
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
    the three declared metallothermic accounts. The kernel account
    filter is the enforcer (binding spec §7); a process.overhead_gas
    seed must NOT cross the boundary into this provider's view.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    # Seed unrelated accounts so the filter has something to filter.
    sim.atom_ledger.load_external(
        "process.overhead_gas", {"Na": 0.5}, source="test seed"
    )
    sim.atom_ledger.load_external(
        "process.condensation_train", {"Fe": 0.5}, source="test seed"
    )

    seen_accounts: list[frozenset[str]] = []
    original_dispatch = BuiltinMetallothermicStepProvider.dispatch

    def _spying_dispatch(self, request):
        seen_accounts.append(frozenset(request.account_view.accounts))
        return original_dispatch(self, request)

    BuiltinMetallothermicStepProvider.dispatch = _spying_dispatch
    try:
        sim._chem_kernel.dispatch(
            ChemistryIntent.METALLOTHERMIC_STEP,
            temperature_C=1300.0,
            pressure_bar=1e-6,
            control_inputs={
                "reaction_family": REACTION_FAMILY_C3_K,
                # No reagent -> early exit, but filter still engages.
                "reagent_available_kg": 0.0,
            },
        )
    finally:
        BuiltinMetallothermicStepProvider.dispatch = original_dispatch

    assert seen_accounts, "provider was never dispatched"
    expected = frozenset({
        "process.cleaned_melt",
        "process.metal_phase",
        "process.reagent_inventory",
    })
    for accounts in seen_accounts:
        assert accounts == expected, (
            "kernel filter leaked an undeclared account into the provider"
        )
        assert "process.overhead_gas" not in accounts
        assert "process.condensation_train" not in accounts
        assert "terminal.oxygen_mre_anode_stored" not in accounts


# ---------------------------------------------------------------------------
# 4. Atom-balance gate: malformed proposal must be rejected at commit
# ---------------------------------------------------------------------------


def test_kernel_commit_rejects_atom_unbalanced_thermite_proposal(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Construct a hand-rolled :class:`LedgerTransitionProposal` where
    the credit atoms do NOT conserve the debit atoms for thermite:
    ``3 Mg + Al2O3 -> 2 MgO + 2 Al`` (missing 1 mol Mg and 1 mol O on
    the credit side).  Verify that :meth:`ChemistryKernel.commit_batch`
    raises :class:`AtomBalanceError`.  Proves the authoritative ledger-
    write path actually engages atom-balance validation for this
    intent.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    # 3 Mg + Al2O3 -> 2 MgO + 2 Al -- credit has 2 Mg (not 3) and
    # 2 O (not 3), so the atom balance is off by 1 Mg + 1 O.
    bad_proposal = LedgerTransitionProposal(
        debits={
            "process.reagent_inventory": {"Mg": 3.0},
            "process.cleaned_melt": {"Al2O3": 1.0},
        },
        credits={
            "process.cleaned_melt": {"MgO": 2.0},
            "process.metal_phase": {"Al": 2.0},
        },
        reason="malformed_thermite_proposal_for_test",
        atom_balance_proof={"Mg": 0.0, "Al": 0.0, "O": 0.0},
    )

    with pytest.raises(AtomBalanceError):
        sim._chem_kernel.commit_batch(
            ChemistryIntent.METALLOTHERMIC_STEP, bad_proposal
        )


def test_kernel_commit_rejects_atom_unbalanced_k_shuttle_proposal(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """K-shuttle malformed proposal: ``2 K + FeO -> K2O + Fe`` but
    credit drops the Fe metal entirely.  Atom balance fails on Fe.
    Companion to the thermite rejection -- proves the gate engages on
    both reaction families.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    bad_proposal = LedgerTransitionProposal(
        debits={
            "process.reagent_inventory": {"K": 2.0},
            "process.cleaned_melt": {"FeO": 1.0},
        },
        credits={
            # No metal_phase credit -> Fe atom missing.
            "process.cleaned_melt": {"K2O": 1.0},
        },
        reason="malformed_k_shuttle_proposal_for_test",
        atom_balance_proof={"K": 0.0, "Fe": 0.0, "O": 0.0},
    )

    with pytest.raises(AtomBalanceError):
        sim._chem_kernel.commit_batch(
            ChemistryIntent.METALLOTHERMIC_STEP, bad_proposal
        )


def test_kernel_commit_accepts_balanced_thermite_proposal(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Companion to the rejection test: ``3 Mg + Al2O3 -> 3 MgO +
    2 Al`` must commit cleanly.  Mg: -3 + 3 = 0; Al: -2 + 2 = 0;
    O: -3 + 3 = 0.  Sanity check that the rejection above isn't a
    false negative caused by some other validator misfiring.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    # Seed reagent_inventory and cleaned_melt so the apply doesn't
    # underflow.  We use small mol values relative to the load_batch
    # seed.
    sim.atom_ledger.load_external_mol(
        "process.reagent_inventory", {"Mg": 5.0}, source="test seed"
    )

    balanced_proposal = LedgerTransitionProposal(
        debits={
            "process.reagent_inventory": {"Mg": 3.0},
            "process.cleaned_melt": {"Al2O3": 1.0},
        },
        credits={
            "process.cleaned_melt": {"MgO": 3.0},
            "process.metal_phase": {"Al": 2.0},
        },
        reason="balanced_thermite_proposal_for_test",
        atom_balance_proof={"Mg": 0.0, "Al": 0.0, "O": 0.0},
    )

    # Should not raise.
    sim._chem_kernel.commit_batch(
        ChemistryIntent.METALLOTHERMIC_STEP, balanced_proposal
    )


# ---------------------------------------------------------------------------
# 5. Unit: deterministic per-family proposals match legacy stoich exactly
# ---------------------------------------------------------------------------


def _atom_check(proposal, registry, *, tol=1e-12):
    """Independent atom-balance re-derivation.  Returns the worst
    absolute net per element.
    """
    from simulator.accounting.formulas import resolve_species_formula

    net = defaultdict(float)
    for side, sign in ((proposal.debits, -1.0), (proposal.credits, +1.0)):
        for _account, species_mol in side.items():
            for sp, mol in species_mol.items():
                formula = resolve_species_formula(sp, registry)
                for element, atoms in formula.atom_moles(float(mol)).items():
                    net[element] += sign * float(atoms)
    worst = max((abs(v) for v in net.values()), default=0.0)
    assert worst < tol, f"atom-balance net: {dict(net)}; worst {worst}"
    return dict(net)


def test_c3_k_shuttle_matches_legacy_stoich(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Drive the provider with a pure-FeO melt + K reagent inventory at
    a known composition and compare its proposal mol values against the
    legacy ``_shuttle_inject_K`` stoichiometry (2 K + FeO -> K2O + Fe).

    The provider is a refactor of where the proposal is built; the
    stoichiometric math is mirrored line-for-line, so the delta should
    be exactly zero modulo IEEE-754 round-off on the same operand
    sequence.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    # Set up a pure-FeO melt with a known mol load so the K-shuttle
    # solubility / FeO availability gates are easily comparable to the
    # legacy arithmetic.
    FeO_kg = 100.0
    FeO_mol = FeO_kg / (MOLAR_MASS["FeO"] / 1000.0)
    melt_mol = {"FeO": FeO_mol}

    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": melt_mol,
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    K_reagent_kg = 30.0
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=view,
        temperature_C=1300.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C3_K,
            "reagent_available_kg": K_reagent_kg,
            "dt_hr": 1.0,
        },
    )
    result = provider.dispatch(request)
    proposal = result.transition
    assert proposal is not None
    assert result.diagnostic["reaction_family"] == REACTION_FAMILY_C3_K

    # Independent legacy derivation. Mirrors _shuttle_inject_K line-by-
    # line at this composition.  total_kg = FeO_kg (pure FeO melt);
    # K2O wt% = 0 < 10 (solubility limit not hit). K2O_max_kg =
    # total_kg * 0.10. K_for_K2O_limit = K2O_max_kg * (2 * M_K / M_K2O).
    # K_for_FeO = FeO_available_kg / (M_FeO / (2 * M_K)).
    # K_inject = min(K_available_kg / 3, K_for_K2O_limit, K_for_FeO).
    total_kg = FeO_kg
    K2O_max_kg = total_kg * 0.10
    K_for_K2O_limit = K2O_max_kg * (2 * MOLAR_MASS["K"] / MOLAR_MASS["K2O"])
    K_for_FeO = FeO_kg / (MOLAR_MASS["FeO"] / (2 * MOLAR_MASS["K"]))
    K_inject_legacy = min(K_reagent_kg / 3.0, K_for_K2O_limit, K_for_FeO)
    mol_K_legacy = K_inject_legacy / MOLAR_MASS["K"] * 1000.0
    mol_FeO_available_legacy = FeO_kg / MOLAR_MASS["FeO"] * 1000.0
    mol_FeO_reduced_legacy = min(
        mol_K_legacy / 2.0, mol_FeO_available_legacy
    )
    mol_K_used_legacy = mol_FeO_reduced_legacy * 2.0

    # Mol-native comparison (kernel proposal values).
    assert proposal.debits["process.reagent_inventory"]["K"] == pytest.approx(
        mol_K_used_legacy, abs=1e-12, rel=1e-12
    )
    assert proposal.debits["process.cleaned_melt"]["FeO"] == pytest.approx(
        mol_FeO_reduced_legacy, abs=1e-12, rel=1e-12
    )
    assert proposal.credits["process.cleaned_melt"]["K2O"] == pytest.approx(
        mol_FeO_reduced_legacy, abs=1e-12, rel=1e-12
    )
    assert proposal.credits["process.metal_phase"]["Fe"] == pytest.approx(
        mol_FeO_reduced_legacy, abs=1e-12, rel=1e-12
    )

    # Independent atom check: net per element ~ 0.
    _atom_check(proposal, sim.species_formula_registry)


def test_c3_na_shuttle_combined_cr_ti_matches_legacy_stoich(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Drive the provider with a Cr2O3 + TiO2 melt + Na reagent.  The
    Na shuttle should reduce Cr2O3 first (priority by ΔG°f), then any
    leftover Na hits TiO2 with the 0.75 accessibility factor.  The
    provider's atom-balanced proposal must sum the two reactions
    exactly the same way the legacy code recorded them as two
    successive transitions.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    # Pick masses so the Cr2O3 cap is reached partway and the leftover
    # Na bleeds into the TiO2 reduction.  ~3.7 kg Na available this
    # hour, ~1.5 kg Cr2O3 consumes ~7.4 mol Na -> ~3 kg Na -> ~0.7 kg
    # Na for TiO2.
    Cr2O3_kg = 1.5
    TiO2_kg = 20.0
    Cr2O3_mol = Cr2O3_kg / (MOLAR_MASS["Cr2O3"] / 1000.0)
    TiO2_mol = TiO2_kg / (MOLAR_MASS["TiO2"] / 1000.0)
    melt_mol = {"Cr2O3": Cr2O3_mol, "TiO2": TiO2_mol}

    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": melt_mol,
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    Na_reagent_kg = 30.0  # 1/3 -> 10 kg available per hour
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=view,
        temperature_C=1300.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C3_NA,
            "reagent_available_kg": Na_reagent_kg,
            "dt_hr": 1.0,
        },
    )
    result = provider.dispatch(request)
    proposal = result.transition
    assert proposal is not None
    assert result.diagnostic["reaction_family"] == REACTION_FAMILY_C3_NA

    # Legacy re-derivation, line-for-line. total_kg, Na2O current pct
    # both 0; Na2O_max_kg = 0.10 * total_kg.  K_for_Na2O_limit dominates
    # only when total_kg is small (here = 50 kg -> 5 kg Na2O -> ~3.4
    # kg Na). Na_inject = min(10 kg / 1 hr, 3.4 kg) = 3.4 kg.
    total_kg = Cr2O3_kg + TiO2_kg
    Na2O_max_kg = total_kg * 0.10
    Na_for_Na2O_limit = Na2O_max_kg * (
        2 * MOLAR_MASS["Na"] / MOLAR_MASS["Na2O"]
    )
    Na_inject_legacy = min(Na_reagent_kg / 3.0, Na_for_Na2O_limit)
    mol_Na_initial = Na_inject_legacy / MOLAR_MASS["Na"] * 1000.0

    # Reaction 1: 6 Na + Cr2O3 -> 3 Na2O + 2 Cr.
    mol_Cr2O3_available = Cr2O3_kg / MOLAR_MASS["Cr2O3"] * 1000.0
    mol_Cr2O3_reduced = min(mol_Na_initial / 6.0, mol_Cr2O3_available)
    mol_Na_for_Cr = mol_Cr2O3_reduced * 6.0
    mol_Na_after_Cr = mol_Na_initial - mol_Na_for_Cr

    # Reaction 2: 4 Na + TiO2 -> 2 Na2O + Ti (0.75 accessibility).
    mol_TiO2_total = TiO2_kg / MOLAR_MASS["TiO2"] * 1000.0
    mol_TiO2_accessible = mol_TiO2_total * 0.75
    mol_TiO2_reduced = min(
        mol_Na_after_Cr / 4.0, mol_TiO2_accessible
    )
    mol_Na_for_Ti = mol_TiO2_reduced * 4.0

    expected_Na_total = mol_Na_for_Cr + mol_Na_for_Ti
    expected_Na2O_total = mol_Cr2O3_reduced * 3.0 + mol_TiO2_reduced * 2.0
    expected_Cr_total = mol_Cr2O3_reduced * 2.0
    expected_Ti_total = mol_TiO2_reduced

    assert proposal.debits["process.reagent_inventory"]["Na"] == pytest.approx(
        expected_Na_total, abs=1e-12, rel=1e-12
    )
    assert proposal.debits["process.cleaned_melt"]["Cr2O3"] == pytest.approx(
        mol_Cr2O3_reduced, abs=1e-12, rel=1e-12
    )
    assert proposal.debits["process.cleaned_melt"]["TiO2"] == pytest.approx(
        mol_TiO2_reduced, abs=1e-12, rel=1e-12
    )
    assert proposal.credits["process.cleaned_melt"]["Na2O"] == pytest.approx(
        expected_Na2O_total, abs=1e-12, rel=1e-12
    )
    assert proposal.credits["process.metal_phase"]["Cr"] == pytest.approx(
        expected_Cr_total, abs=1e-12, rel=1e-12
    )
    assert proposal.credits["process.metal_phase"]["Ti"] == pytest.approx(
        expected_Ti_total, abs=1e-12, rel=1e-12
    )

    _atom_check(proposal, sim.species_formula_registry)


def test_c6_mg_thermite_primary_matches_legacy_stoich(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Drive the provider with an Al2O3 melt + Mg reagent; assert the
    proposal matches the 3 Mg + Al2O3 -> 3 MgO + 2 Al stoichiometry
    line-for-line with the legacy ``_step_thermite`` rate-factor
    expression (``0.20 * exp(-0.05 * wt%MgO)``).
    """

    import math

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    Al2O3_kg = 100.0
    Al2O3_mol = Al2O3_kg / (MOLAR_MASS["Al2O3"] / 1000.0)
    # Add some MgO so the rate factor is < 0.20.
    MgO_kg = 20.0
    MgO_mol = MgO_kg / (MOLAR_MASS["MgO"] / 1000.0)
    melt_mol = {"Al2O3": Al2O3_mol, "MgO": MgO_mol}

    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": melt_mol,
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    Mg_reagent_kg = 50.0
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=view,
        temperature_C=1700.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C6_MG,
            "reagent_available_kg": Mg_reagent_kg,
            "dt_hr": 1.0,
        },
    )
    result = provider.dispatch(request)
    proposal = result.transition
    assert proposal is not None
    assert result.diagnostic["reaction_family"] == REACTION_FAMILY_C6_MG
    assert result.diagnostic["back_reduction"] is False

    # Legacy re-derivation. total_kg = Al2O3_kg + MgO_kg = 120.
    # MgO wt% = 20/120 * 100 ~= 16.67%. rate_factor = 0.20 * exp(
    # -0.05 * 16.67) ~= 0.0867, clamped [0.01, 0.25].
    total_kg = Al2O3_kg + MgO_kg
    MgO_pct = MgO_kg / total_kg * 100.0
    rate_factor = 0.20 * math.exp(-0.05 * MgO_pct)
    rate_factor = max(0.01, min(0.25, rate_factor))
    Mg_available_this_hr = Mg_reagent_kg * rate_factor
    mol_Mg = Mg_available_this_hr / MOLAR_MASS["Mg"] * 1000.0
    mol_Al2O3_avail = Al2O3_kg / MOLAR_MASS["Al2O3"] * 1000.0
    mol_Al2O3_reduced = min(mol_Mg / 3.0, mol_Al2O3_avail)
    mol_Mg_used = mol_Al2O3_reduced * 3.0

    assert proposal.debits["process.reagent_inventory"]["Mg"] == pytest.approx(
        mol_Mg_used, abs=1e-12, rel=1e-12
    )
    assert proposal.debits["process.cleaned_melt"]["Al2O3"] == pytest.approx(
        mol_Al2O3_reduced, abs=1e-12, rel=1e-12
    )
    assert proposal.credits["process.cleaned_melt"]["MgO"] == pytest.approx(
        mol_Al2O3_reduced * 3.0, abs=1e-12, rel=1e-12
    )
    assert proposal.credits["process.metal_phase"]["Al"] == pytest.approx(
        mol_Al2O3_reduced * 2.0, abs=1e-12, rel=1e-12
    )

    # mol_Al_produced is on the diagnostic for the back-reduction
    # chain (the caller passes it back in as control_input to the
    # second dispatch).
    assert result.diagnostic["mol_Al_produced"] == pytest.approx(
        mol_Al2O3_reduced * 2.0, abs=1e-12, rel=1e-12
    )

    _atom_check(proposal, sim.species_formula_registry)


def test_c6_back_reduction_matches_legacy_stoich(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Drive the provider in back-reduction mode with a known Al
    quantity + SiO2 melt; assert 4 Al + 3 SiO2 -> 2 Al2O3 + 3 Si
    stoichiometry holds at the legacy 30 % consumption fraction.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    SiO2_kg = 50.0
    Al2O3_kg = 10.0
    SiO2_mol = SiO2_kg / (MOLAR_MASS["SiO2"] / 1000.0)
    Al2O3_mol = Al2O3_kg / (MOLAR_MASS["Al2O3"] / 1000.0)
    melt_mol = {"SiO2": SiO2_mol, "Al2O3": Al2O3_mol}
    # 10 mol Al from primary thermite -- 30 % goes to back-reduction.
    mol_Al_produced = 10.0

    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": melt_mol,
            # The Al hasn't been committed to metal_phase yet -- the
            # legacy reads the freshly-produced Al_produced_kg from
            # the primary thermite output, not from the metal_phase
            # account. The back-reduction proposal will debit
            # metal_phase, but the gate is on the freshly-produced
            # mol_Al_produced control input.
            "process.metal_phase": {"Al": mol_Al_produced},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=view,
        temperature_C=1700.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C6_MG,
            "reagent_available_kg": 0.0,  # not used in back-reduction
            "back_reduction": True,
            "mol_Al_produced": mol_Al_produced,
            "dt_hr": 1.0,
        },
    )
    result = provider.dispatch(request)
    proposal = result.transition
    assert proposal is not None
    assert result.diagnostic["back_reduction"] is True

    # Legacy: Al_produced_kg = mol_Al_produced * M_Al / 1000.
    Al_produced_kg = mol_Al_produced * MOLAR_MASS["Al"] / 1000.0
    mol_Al_for_back = (
        Al_produced_kg * 0.30 / MOLAR_MASS["Al"] * 1000.0
    )
    mol_SiO2_avail = SiO2_kg / MOLAR_MASS["SiO2"] * 1000.0
    mol_SiO2_consumed = min(mol_Al_for_back * 3.0 / 4.0, mol_SiO2_avail)
    mol_Al_consumed_legacy = mol_SiO2_consumed * 4.0 / 3.0

    assert proposal.debits["process.metal_phase"]["Al"] == pytest.approx(
        mol_Al_consumed_legacy, abs=1e-12, rel=1e-12
    )
    assert proposal.debits["process.cleaned_melt"]["SiO2"] == pytest.approx(
        mol_SiO2_consumed, abs=1e-12, rel=1e-12
    )
    assert proposal.credits["process.cleaned_melt"]["Al2O3"] == pytest.approx(
        mol_SiO2_consumed * 2.0 / 3.0, abs=1e-12, rel=1e-12
    )
    assert proposal.credits["process.metal_phase"]["Si"] == pytest.approx(
        mol_SiO2_consumed, abs=1e-12, rel=1e-12
    )

    _atom_check(proposal, sim.species_formula_registry)


def test_provider_short_circuits_on_empty_reagent(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """No reagent inventory -> ok-no-op for every reaction family
    (mirrors the legacy ``<= 0.01 kg`` early-return guard).
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {"FeO": 100.0, "Al2O3": 50.0},
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    for family in (
        REACTION_FAMILY_C3_K,
        REACTION_FAMILY_C3_NA,
        REACTION_FAMILY_C6_MG,
    ):
        request = IntentRequest(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            account_view=view,
            temperature_C=1300.0,
            pressure_bar=1e-6,
            control_inputs={
                "reaction_family": family,
                "reagent_available_kg": 0.0,
            },
        )
        result = provider.dispatch(request)
        assert result.status == "ok"
        assert result.transition is None


# ---------------------------------------------------------------------------
# 6. Smoke parity: full C0 -> C6 on two feedstocks (C3 + C6 exercised)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "feedstock_key, additives_kg",
    [
        ("lunar_mare_low_ti", {"K": 30.0, "Na": 25.0, "Mg": 60.0}),
        ("mars_basalt", {"C": 60.0, "K": 30.0, "Na": 25.0, "Mg": 60.0}),
    ],
)
def test_full_run_mass_balance_holds_with_kernel_committed_metallothermic(
    feedstock_key,
    additives_kg,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """Drive C0 -> C6 on each feedstock and verify:

    * the simulator runs to completion,
    * the AtomLedger holds a non-trivial number of metallothermic
      transitions (so we know the kernel-committed METALLOTHERMIC_STEP
      path actually fired across the C3 + C6 campaigns),
    * each metallothermic transition strictly debits / credits within
      the three declared accounts (no leak to overhead_gas, terminal
      O2 bins, condensation_train),
    * each transition closes mass within a tight 1 mg per-transition
      tolerance,
    * end-of-batch mass-balance closure stays at the same 5e-12 %
      ceiling the prior flips established.

    Asteroid feedstocks may not exercise C3/C6 in the default decision
    path -- they are excluded here; the lunar + Mars cases give the
    coverage the goal requires.
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
    metallothermic_reasons = {
        "c3_k_shuttle_fe_reduction",
        "c3_na_shuttle_reduction",
        "c6_mg_thermite_primary",
        "c6_al_si_back_reduction",
    }
    metallothermic_transitions = [
        t for t in transitions
        if t.name in metallothermic_reasons
    ]
    assert len(metallothermic_transitions) > 0, (
        f"feedstock {feedstock_key} produced zero metallothermic "
        "transitions; the kernel-committed METALLOTHERMIC_STEP path "
        "never fired"
    )

    registry = sim.atom_ledger.registry
    allowed_accounts = {
        "process.cleaned_melt",
        "process.metal_phase",
        "process.reagent_inventory",
    }
    cumulative_imbalance_kg = 0.0
    for trans in metallothermic_transitions:
        for lot in (*trans.debits, *trans.credits):
            assert lot.account in allowed_accounts, (
                f"metallothermic transition {trans.name} touches "
                f"unexpected account {lot.account!r}; expected one "
                f"of {sorted(allowed_accounts)}"
            )
        debit_kg = trans.debit_mass_kg(registry)
        credit_kg = trans.credit_mass_kg(registry)
        delta = abs(debit_kg - credit_kg)
        assert delta < 1e-3, (
            f"metallothermic transition {trans.name} has unbalanced "
            f"mass: debit={debit_kg:.6g} credit={credit_kg:.6g}"
        )
        cumulative_imbalance_kg += delta

    assert cumulative_imbalance_kg < 1e-6, (
        f"feedstock {feedstock_key} accumulated "
        f"{cumulative_imbalance_kg:.3e} kg metallothermic imbalance "
        "(expected <1e-6 kg)"
    )

    snapshot = sim._make_snapshot()
    assert abs(snapshot.mass_balance_error_pct) < 5e-12, (
        f"feedstock {feedstock_key} mass balance closure "
        f"{snapshot.mass_balance_error_pct:.3e} % exceeds the "
        "5e-12 % kernel-path bound"
    )
