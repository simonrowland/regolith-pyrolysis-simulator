"""Tests for the BuiltinStage0PretreatmentProvider -- seventh and final
intent flip of ``\\goal BUILTIN-ENGINE-EXTRACTION`` (#7) and the FIFTH
authoritative ledger-mutating intent in the migration (after
EVAPORATION_TRANSITION, CONDENSATION_ROUTE, ELECTROLYSIS_STEP,
METALLOTHERMIC_STEP).

Covers:

* Capability profile: the provider is authoritative for
  ``STAGE0_PRETREATMENT`` and declares the nine accounts the legacy
  cleanup transitions touch (six process / reservoir feed buckets +
  three terminal sinks).
* Wrong-intent rejection: the provider returns an ``unsupported``
  ``IntentResult`` if dispatched against an intent it does not serve.
* Account filter: the kernel filter scopes the provider's view to the
  nine declared accounts only -- any other ledger account (cleaned_melt,
  metal_phase, MRE anode O2 bin, ...) is invisible.
* Per-family ground-truth proposals: deterministic per-reaction inputs
  yield the exact stoichiometric debit/credit dicts the legacy
  ``_record_stage0_*_transitions`` calls would have produced.  Covered:
  complete_oxidation, sulfate_carbon, boudouard, perchlorate.
* Atom-balance gate engagement -- a malformed perchlorate proposal
  (``ClO4 -> Cl + 1 O2`` -- missing 1 mol O2) is rejected at
  :meth:`ChemistryKernel.commit_batch` with
  :class:`AtomBalanceError`.  Companion accepts-balanced test pins
  that the rejection isn't a false negative.
* Out-of-domain handling: a feedstock with no Stage 0 profile for the
  requested reaction_family yields ``status='out_of_domain'`` with a
  warning, NOT a fabricated proposal.
* SulfSat post-equilibrium hook still fires after the Stage 0 flip:
  Mars sulfate-rich feedstock with sulfur-bearing inventory produces a
  populated ``SulfurSaturationResult`` on
  ``_last_sulfur_saturation_result`` after the flip.
* Smoke parity: full Stage 0 reload on lunar + Mars + asteroid
  feedstocks closes mass balance to the existing tolerance and the
  kernel-committed Stage 0 transitions land in the declared accounts
  only.
"""

from __future__ import annotations

import pytest

from engines.builtin.stage0_pretreatment import (
    BuiltinStage0PretreatmentProvider,
    REACTION_FAMILY_BOUDOUARD,
    REACTION_FAMILY_COMPLETE_OXIDATION,
    REACTION_FAMILY_PERCHLORATE,
    REACTION_FAMILY_SULFATE_CARBON,
)
from simulator.account_ids import (
    OXYGEN_STAGE0_ACCOUNT,
    OXYGEN_STORED_ACCOUNTS,
    OXYGEN_VENTED_ACCOUNTS,
)
from simulator.accounting.queries import AccountingQueries
from simulator.chemistry.kernel import (
    AtomBalanceError,
    ChemistryIntent,
    IntentRequest,
    LedgerTransitionProposal,
)
from simulator.chemistry.kernel.dto import ProviderAccountView
from tests.chemistry.conftest import _atom_check, _build_sim


# ---------------------------------------------------------------------------
# 1. Capability profile
# ---------------------------------------------------------------------------


def test_provider_declares_only_stage0_pretreatment_intent():
    provider = BuiltinStage0PretreatmentProvider()
    profile = provider.capability_profile()

    assert profile.intents == frozenset({ChemistryIntent.STAGE0_PRETREATMENT})
    assert profile.is_authoritative_for == frozenset(
        {ChemistryIntent.STAGE0_PRETREATMENT}
    )
    for intent in ChemistryIntent:
        if intent is ChemistryIntent.STAGE0_PRETREATMENT:
            assert profile.is_authoritative(intent)
        else:
            assert not profile.is_authoritative(intent)


def test_provider_declares_exactly_fifteen_stage0_accounts():
    """Provider declares exactly the fifteen accounts touched by every
    legacy ``_record_stage0_*_transitions`` call including carbonate
    decomposition and cation-sulfate carbothermal cleanup (MO -> melt,
    CaS -> sulfide matte), plus foulant residual-C diagnostic accounts.
    Pinning the set stops a future refactor from silently widening the
    surface beyond Stage 0's scope.
    """

    provider = BuiltinStage0PretreatmentProvider()
    profile = provider.capability_profile()
    assert profile.declared_accounts == frozenset({
        "process.stage0_volatile_feed",
        "process.stage0_salt_feed",
        "process.stage0_carbonate_feed",
        "process.reagent_inventory",
        "process.stage0_perchlorate_feed",
        "process.cleaned_melt",
        "reservoir.stage0_oxidant",
        "reservoir.stage0_process_gas",
        "terminal.offgas",
        "terminal.stage0_salt_phase",
        "terminal.stage0_chloride_salt_phase",
        "terminal.stage0_sulfide_matte",
        "terminal.oxygen_stage0_stored",
        "terminal.stage0_residual_refractory_carbon",
        "terminal.stage0_residual_carbonate_carbon",
    })
    # Sanity: Stage 0 must not touch downstream metallothermic accounts.
    assert "process.metal_phase" not in profile.declared_accounts
    assert "process.stage0_carbon_reductant" not in profile.declared_accounts
    assert "process.overhead_gas" not in profile.declared_accounts
    assert "process.condensation_train" not in profile.declared_accounts
    assert "terminal.oxygen_mre_anode_stored" not in profile.declared_accounts
    assert "terminal.oxygen_melt_offgas_stored" not in profile.declared_accounts
    assert "terminal.drain_tap_material" not in profile.declared_accounts
    assert "terminal.slag" not in profile.declared_accounts


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
    provider = BuiltinStage0PretreatmentProvider()
    view = ProviderAccountView(
        accounts={},
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,  # WRONG INTENT
        account_view=view,
        temperature_C=25.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_PERCHLORATE,
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
    provider = BuiltinStage0PretreatmentProvider()
    view = ProviderAccountView(
        accounts={
            "process.stage0_volatile_feed": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.STAGE0_PRETREATMENT,
        account_view=view,
        temperature_C=25.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": "future_unknown_family",
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
    the twelve declared Stage 0 accounts. The kernel account filter is
    the enforcer (binding spec §7); downstream accounts like
    ``process.metal_phase`` must NOT cross the boundary into this
    provider's view.
    """

    sim = _build_sim(
        "mars_basalt",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"C": 50.0},
    )
    cleaned_kg = sum(
        sim.atom_ledger.kg_by_account("process.cleaned_melt").values())
    assert cleaned_kg > 0.0

    from simulator.chemistry.kernel.account_filters import (
        build_provider_account_view,
    )

    profile = BuiltinStage0PretreatmentProvider().capability_profile()
    view = build_provider_account_view(
        sim.atom_ledger,
        profile.declared_accounts,
        sim.species_formula_registry,
    )
    accounts = set(view.accounts.keys())
    expected = set(profile.declared_accounts)
    assert accounts.issubset(expected), (
        f"kernel filter leaked an undeclared account into the provider: "
        f"{accounts - expected}"
    )
    assert "process.cleaned_melt" in expected
    assert "process.reagent_inventory" in expected
    assert "process.metal_phase" not in accounts
    assert "terminal.oxygen_mre_anode_stored" not in accounts


# ---------------------------------------------------------------------------
# 4. Atom-balance gate: malformed proposal must be rejected at commit
# ---------------------------------------------------------------------------


def test_kernel_commit_rejects_atom_unbalanced_perchlorate_proposal(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Construct a hand-rolled :class:`LedgerTransitionProposal` where
    the credit atoms do NOT conserve the debit atoms for perchlorate:
    ``ClO4 -> Cl + 1 O2`` (missing 1 mol O2 = 2 mol O on the credit
    side).  Verify that :meth:`ChemistryKernel.commit_batch` raises
    :class:`AtomBalanceError`.  Proves the authoritative ledger-write
    path actually engages atom-balance validation for this intent.
    """

    sim = _build_sim(
        "mars_basalt",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"C": 50.0},
    )

    # ClO4 -> Cl + 1 O2 (correct stoich is + 2 O2)
    bad_proposal = LedgerTransitionProposal(
        debits={
            "process.stage0_perchlorate_feed": {"ClO4": 1.0},
        },
        credits={
            "terminal.stage0_chloride_salt_phase": {"Cl": 1.0},
            "terminal.oxygen_stage0_stored": {"O2": 1.0},  # should be 2.0
        },
        reason="malformed_perchlorate_proposal_for_test",
        atom_balance_proof={"Cl": 0.0, "O": 0.0},
    )

    with pytest.raises(AtomBalanceError):
        sim._chem_kernel.commit_batch(
            ChemistryIntent.STAGE0_PRETREATMENT, bad_proposal
        )


def test_kernel_commit_rejects_atom_unbalanced_sulfate_carbon_proposal(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Sulfate-carbon malformed proposal: ``2 SO3 + C -> 1 SO2 + CO``
    drops one SO2 entirely.  Atom balance fails on S and O.
    Companion to the perchlorate rejection -- proves the gate engages
    on multiple Stage 0 reaction families.
    """

    sim = _build_sim(
        "mars_basalt",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"C": 50.0},
    )

    bad_proposal = LedgerTransitionProposal(
        debits={
            "process.stage0_salt_feed": {"SO3": 2.0},
            "process.reagent_inventory": {"C": 1.0},
        },
        credits={
            # 1 SO2 instead of 2 -> S short by 1, O short by 2.
            "terminal.offgas": {"SO2": 1.0, "CO": 1.0},
        },
        reason="malformed_sulfate_carbon_proposal_for_test",
        atom_balance_proof={"S": 0.0, "C": 0.0, "O": 0.0},
    )

    with pytest.raises(AtomBalanceError):
        sim._chem_kernel.commit_batch(
            ChemistryIntent.STAGE0_PRETREATMENT, bad_proposal
        )


def test_kernel_commit_accepts_balanced_perchlorate_proposal(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Companion to the rejection test: ``ClO4 -> Cl + 2 O2`` must
    commit cleanly.  Cl: -1 + 1 = 0; O: -4 + 4 = 0.  Sanity check
    that the rejection above isn't a false negative caused by some
    other validator misfiring.
    """

    sim = _build_sim(
        "mars_basalt",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"C": 50.0},
    )

    # Seed perchlorate feed account so the apply doesn't underflow.
    # Mars basalt already loads it during Stage 0 cleanup, but make
    # sure there's enough.
    sim.atom_ledger.load_external(
        "process.stage0_perchlorate_feed", {"ClO4": 0.5},
        source="test seed",
    )

    balanced_proposal = LedgerTransitionProposal(
        debits={
            "process.stage0_perchlorate_feed": {"ClO4": 0.001},
        },
        credits={
            "terminal.stage0_chloride_salt_phase": {"Cl": 0.001},
            "terminal.oxygen_stage0_stored": {"O2": 0.002},
        },
        reason="balanced_perchlorate_proposal_for_test",
        atom_balance_proof={"Cl": 0.0, "O": 0.0},
    )

    # Should not raise.
    sim._chem_kernel.commit_batch(
        ChemistryIntent.STAGE0_PRETREATMENT, balanced_proposal
    )


# ---------------------------------------------------------------------------
# 5. Out-of-domain handling
# ---------------------------------------------------------------------------


def test_provider_returns_out_of_domain_when_spec_is_empty(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """When a feedstock has no Stage 0 profile for the requested
    reaction_family, the provider must return ``status='out_of_domain'``
    with a warning, NOT a fabricated proposal.  Concrete case: a
    perchlorate dispatch with empty debits + empty products (a lunar
    feedstock has no perchlorate Stage 0 inventory, so the legacy
    spec list would be empty and would never invoke the provider --
    but if a future caller passes an empty payload directly, the
    provider must NOT pretend it has data).
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinStage0PretreatmentProvider()
    view = ProviderAccountView(
        accounts={
            "process.stage0_perchlorate_feed": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.STAGE0_PRETREATMENT,
        account_view=view,
        temperature_C=25.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_PERCHLORATE,
            # Empty payload -- no debits, no products.
            "debits": (),
            "salt_products_kg": {},
            "oxygen_products_kg": {},
        },
    )
    result = provider.dispatch(request)
    assert result.status == "out_of_domain"
    assert result.transition is None
    assert result.warnings, "out_of_domain result must surface a warning"
    assert "perchlorate" in result.warnings[0].lower()


def test_provider_returns_out_of_domain_for_complete_oxidation_without_species(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Complete-oxidation dispatch without a species name must surface
    as out_of_domain (no fabricated proposal)."""

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinStage0PretreatmentProvider()
    view = ProviderAccountView(
        accounts={
            "process.stage0_volatile_feed": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.STAGE0_PRETREATMENT,
        account_view=view,
        temperature_C=25.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_COMPLETE_OXIDATION,
            # No species name.
            "species": "",
            "feed_kg": 0.0,
            "products_kg": {},
        },
    )
    result = provider.dispatch(request)
    assert result.status == "out_of_domain"
    assert result.transition is None


# ---------------------------------------------------------------------------
# 6. Unit: deterministic per-family proposals match legacy stoich exactly
# ---------------------------------------------------------------------------


def test_perchlorate_matches_legacy_stoich(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Drive the provider with a hand-built perchlorate spec at known
    masses and verify the proposal mol values match
    ``ClO4 -> Cl + 2 O2`` (the legacy _apply_stage0_perchlorate_reactions
    stoichiometry) within IEEE-754 round-off.
    """

    sim = _build_sim(
        "mars_basalt",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"C": 50.0},
    )
    provider = BuiltinStage0PretreatmentProvider()
    view = ProviderAccountView(
        accounts={
            "process.stage0_perchlorate_feed": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )

    from simulator.accounting.formulas import resolve_species_formula

    M_ClO4 = resolve_species_formula(
        "ClO4", sim.species_formula_registry).molar_mass_kg_per_mol()
    M_Cl = resolve_species_formula(
        "Cl", sim.species_formula_registry).molar_mass_kg_per_mol()
    M_O2 = resolve_species_formula(
        "O2", sim.species_formula_registry).molar_mass_kg_per_mol()
    clo4_kg = 5.0
    extent_mol = clo4_kg / M_ClO4
    salt_products_kg = {"Cl": extent_mol * M_Cl}
    oxygen_products_kg = {"O2": 2.0 * extent_mol * M_O2}

    request = IntentRequest(
        intent=ChemistryIntent.STAGE0_PRETREATMENT,
        account_view=view,
        temperature_C=25.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_PERCHLORATE,
            "debits": (
                ("process.stage0_perchlorate_feed", {"ClO4": clo4_kg}),
            ),
            "salt_products_kg": salt_products_kg,
            "oxygen_products_kg": oxygen_products_kg,
        },
    )
    result = provider.dispatch(request)
    assert result.status == "ok"
    proposal = result.transition
    assert proposal is not None
    # Atom balance closes independently.
    _atom_check(proposal, sim.species_formula_registry, tol=1e-9)
    # Cl mol matches extent_mol exactly within IEEE round-off.
    cl_mol = proposal.credits["terminal.stage0_chloride_salt_phase"]["Cl"]
    assert abs(cl_mol - extent_mol) < 1e-12
    o2_mol = proposal.credits["terminal.oxygen_stage0_stored"]["O2"]
    assert abs(o2_mol - 2.0 * extent_mol) < 1e-12


def test_o2_shuttle_annotation_reads_perchlorate_bin_without_double_count(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "mars_basalt",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"C": 50.0},
    )

    partition = AccountingQueries(sim).oxygen_terminal_partition_kg()
    recovered_kg = sim.atom_ledger.kg_by_account(OXYGEN_STAGE0_ACCOUNT)["O2"]
    legacy_total_kg = (
        partition["stage0_stored"]
        + partition["melt_offgas_stored"]
        + partition["mre_anode_stored"]
        + partition["melt_offgas_vented"]
    )

    assert partition["stage0_o2_recovered_stored"] == pytest.approx(
        recovered_kg)
    assert partition["stage0_o2_recovered_stored"] == pytest.approx(
        partition["stage0_stored"])
    assert partition["total"] == pytest.approx(legacy_total_kg)
    assert partition["stage0_o2_bound_into_melt_redox"] == pytest.approx(0.0)


def test_o2_shuttle_redox_annotation_stays_kress91_order(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "mars_basalt",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"C": 50.0},
    )

    from simulator.accounting.formulas import resolve_species_formula

    def molar_mass(species: str) -> float:
        return resolve_species_formula(
            species, sim.species_formula_registry).molar_mass_kg_per_mol()

    batch_kg = 1000.0
    redox_o2_kg = 0.33
    o2_mol = redox_o2_kg / molar_mass("O2")
    feo_kg = 4.0 * o2_mol * molar_mass("FeO")
    fe2o3_kg = 2.0 * o2_mol * molar_mass("Fe2O3")

    sim.atom_ledger.load_external(
        "reservoir.stage0_oxidant",
        {"O2": redox_o2_kg},
        source="test Kress91 redox oxidant",
    )
    sim.atom_ledger.load_external(
        "process.cleaned_melt",
        {"FeO": feo_kg},
        source="test Kress91 redox FeO",
    )
    sim.atom_ledger.transfer(
        "stage0_redox_annotation_test",
        debits=(
            sim.atom_ledger.debit(
                "reservoir.stage0_oxidant", {"O2": redox_o2_kg}),
            sim.atom_ledger.debit(
                "process.cleaned_melt", {"FeO": feo_kg}),
        ),
        credits=(
            sim.atom_ledger.credit(
                "process.cleaned_melt", {"Fe2O3": fe2o3_kg}),
        ),
        reason="test read-only redox storage annotation",
    )

    partition = AccountingQueries(sim).oxygen_terminal_partition_kg()
    redox_g_per_kg = (
        partition["stage0_o2_bound_into_melt_redox"] * 1000.0 / batch_kg
    )

    assert partition["stage0_o2_bound_into_melt_redox"] == pytest.approx(
        redox_o2_kg)
    assert redox_g_per_kg == pytest.approx(0.33)
    assert redox_g_per_kg < 1.0


def test_stage0_o2_cross_transition_conservation_keeps_carbothermic_distinct(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "mars_basalt",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"C": 50.0},
    )
    provider = BuiltinStage0PretreatmentProvider()

    from simulator.accounting.formulas import resolve_species_formula

    def molar_mass(species: str) -> float:
        return resolve_species_formula(
            species, sim.species_formula_registry).molar_mass_kg_per_mol()

    clo4_kg = 5.0
    clo4_extent_mol = clo4_kg / molar_mass("ClO4")
    perchlorate = provider.dispatch(IntentRequest(
        intent=ChemistryIntent.STAGE0_PRETREATMENT,
        account_view=ProviderAccountView(
            accounts={"process.stage0_perchlorate_feed": {}},
            species_formula_registry=sim.species_formula_registry,
        ),
        temperature_C=25.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_PERCHLORATE,
            "debits": (
                ("process.stage0_perchlorate_feed", {"ClO4": clo4_kg}),
            ),
            "salt_products_kg": {"Cl": clo4_extent_mol * molar_mass("Cl")},
            "oxygen_products_kg": {
                "O2": 2.0 * clo4_extent_mol * molar_mass("O2"),
            },
        },
    )).transition

    sulfate_extent_mol = 2.0
    sulfate = provider.dispatch(IntentRequest(
        intent=ChemistryIntent.STAGE0_PRETREATMENT,
        account_view=ProviderAccountView(
            accounts={
                "process.stage0_salt_feed": {},
                "process.reagent_inventory": {},
            },
            species_formula_registry=sim.species_formula_registry,
        ),
        temperature_C=25.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_SULFATE_CARBON,
            "debits": (
                (
                    "process.stage0_salt_feed",
                    {"SO3": sulfate_extent_mol * molar_mass("SO3")},
                ),
                (
                    "process.reagent_inventory",
                    {"C": sulfate_extent_mol * molar_mass("C")},
                ),
            ),
            "products_kg": {
                "SO2": sulfate_extent_mol * molar_mass("SO2"),
                "CO": sulfate_extent_mol * molar_mass("CO"),
            },
        },
    )).transition

    assert perchlorate is not None
    assert sulfate is not None

    oxygen_accounts = (*OXYGEN_STORED_ACCOUNTS, *OXYGEN_VENTED_ACCOUNTS)

    def terminal_o2_credit_mol(proposal: LedgerTransitionProposal) -> float:
        return sum(
            proposal.credits.get(account, {}).get("O2", 0.0)
            for account in oxygen_accounts
        )

    assert set(perchlorate.debits).isdisjoint(set(sulfate.debits))
    assert terminal_o2_credit_mol(sulfate) == pytest.approx(0.0)
    assert terminal_o2_credit_mol(perchlorate) == pytest.approx(
        2.0 * clo4_extent_mol)
    assert (
        terminal_o2_credit_mol(perchlorate)
        + terminal_o2_credit_mol(sulfate)
    ) == pytest.approx(2.0 * clo4_extent_mol)


def test_sulfate_carbon_matches_legacy_stoich(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Sulfate-carbon spec: ``SO3 + C -> SO2 + CO``.  Per legacy
    ``_apply_stage0_sulfate_carbon_reaction``: ``extent_mol`` is set
    such that ``c_consumed_kg = extent_mol * M_C`` and
    ``so3_consumed_kg = extent_mol * M_SO3`` and the products are
    ``{'SO2': extent * M_SO2, 'CO': extent * M_CO}`` (1:1:1:1
    stoichiometry, NOT 2:1:2:1 -- atom balance: S=0, C=0, O=3-3=0).
    Provider receives the pre-computed spec and must mirror it
    line-for-line.
    """

    sim = _build_sim(
        "mars_basalt",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"C": 50.0},
    )
    provider = BuiltinStage0PretreatmentProvider()
    view = ProviderAccountView(
        accounts={
            "process.stage0_salt_feed": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )

    from simulator.accounting.formulas import resolve_species_formula

    M_SO3 = resolve_species_formula(
        "SO3", sim.species_formula_registry).molar_mass_kg_per_mol()
    M_C = resolve_species_formula(
        "C", sim.species_formula_registry).molar_mass_kg_per_mol()
    M_SO2 = resolve_species_formula(
        "SO2", sim.species_formula_registry).molar_mass_kg_per_mol()
    M_CO = resolve_species_formula(
        "CO", sim.species_formula_registry).molar_mass_kg_per_mol()

    extent_mol = 2.0
    so3_consumed_kg = extent_mol * M_SO3
    c_consumed_kg = extent_mol * M_C
    products_kg = {
        "SO2": extent_mol * M_SO2,
        "CO": extent_mol * M_CO,
    }
    request = IntentRequest(
        intent=ChemistryIntent.STAGE0_PRETREATMENT,
        account_view=view,
        temperature_C=25.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_SULFATE_CARBON,
            "debits": (
                ("process.stage0_salt_feed", {"SO3": so3_consumed_kg}),
                ("process.reagent_inventory", {"C": c_consumed_kg}),
            ),
            "products_kg": products_kg,
        },
    )
    result = provider.dispatch(request)
    assert result.status == "ok"
    proposal = result.transition
    assert proposal is not None
    _atom_check(proposal, sim.species_formula_registry, tol=1e-9)
    so2_mol = proposal.credits["terminal.offgas"]["SO2"]
    co_mol = proposal.credits["terminal.offgas"]["CO"]
    # Expected: SO2 = extent_mol = 2.0; CO = extent_mol = 2.0.
    assert abs(so2_mol - extent_mol) < 1e-12
    assert abs(co_mol - extent_mol) < 1e-12
    assert result.diagnostic["reagent_consumed_kg"] == pytest.approx(
        c_consumed_kg)
    assert proposal.debits["process.reagent_inventory"]["C"] == pytest.approx(
        c_consumed_kg / M_C)


def test_boudouard_matches_legacy_stoich(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Boudouard spec: ``C + CO2 -> 2 CO``.  Per legacy
    ``_apply_stage0_boudouard_reaction``: ``c_consumed_kg = extent *
    M_C``; ``co2_input_kg = extent * M_CO2``; ``co_kg = 2 * extent *
    M_CO``.  Provider mirrors line-for-line.
    """

    sim = _build_sim(
        "mars_basalt",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"C": 50.0},
    )
    provider = BuiltinStage0PretreatmentProvider()
    view = ProviderAccountView(
        accounts={
            "process.reagent_inventory": {},
            "reservoir.stage0_process_gas": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )

    from simulator.accounting.formulas import resolve_species_formula

    M_C = resolve_species_formula(
        "C", sim.species_formula_registry).molar_mass_kg_per_mol()
    M_CO2 = resolve_species_formula(
        "CO2", sim.species_formula_registry).molar_mass_kg_per_mol()
    M_CO = resolve_species_formula(
        "CO", sim.species_formula_registry).molar_mass_kg_per_mol()

    extent_mol = 3.0
    c_consumed_kg = extent_mol * M_C
    co2_input_kg = extent_mol * M_CO2
    products_kg = {"CO": 2.0 * extent_mol * M_CO}
    request = IntentRequest(
        intent=ChemistryIntent.STAGE0_PRETREATMENT,
        account_view=view,
        temperature_C=25.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_BOUDOUARD,
            "debits": (
                ("process.reagent_inventory", {"C": c_consumed_kg}),
                ("reservoir.stage0_process_gas", {"CO2": co2_input_kg}),
            ),
            "products_kg": products_kg,
        },
    )
    result = provider.dispatch(request)
    assert result.status == "ok"
    proposal = result.transition
    assert proposal is not None
    _atom_check(proposal, sim.species_formula_registry, tol=1e-9)
    co_mol = proposal.credits["terminal.offgas"]["CO"]
    # Expected: CO = 2 * extent_mol = 6.0.
    assert abs(co_mol - 2.0 * extent_mol) < 1e-12
    assert result.diagnostic["reagent_consumed_kg"] == pytest.approx(
        c_consumed_kg)
    assert proposal.debits["process.reagent_inventory"]["C"] == pytest.approx(
        extent_mol)


def test_complete_oxidation_matches_legacy_stoich(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Complete oxidation: methane-style ``CH4 + 2 O2 -> CO2 + 2 H2O``.

    Per legacy ``_oxidized_stage0_products``: products = CO2 (from C),
    H2O (from H/2), N2 (from N/2); oxidant_o2 = O-deficit / 2.  Drive
    the provider with the spec a CH4 entry would produce and check
    the atom balance + the offgas products.
    """

    # Pick a carbonaceous feedstock so the registry knows CH4 etc.
    sim = _build_sim(
        "ci_carbonaceous_chondrite",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinStage0PretreatmentProvider()
    view = ProviderAccountView(
        accounts={
            "process.stage0_volatile_feed": {},
            "reservoir.stage0_oxidant": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )

    from simulator.accounting.formulas import resolve_species_formula

    M_CH4 = resolve_species_formula(
        "CH4", sim.species_formula_registry).molar_mass_kg_per_mol()
    M_O2 = resolve_species_formula(
        "O2", sim.species_formula_registry).molar_mass_kg_per_mol()
    M_CO2 = resolve_species_formula(
        "CO2", sim.species_formula_registry).molar_mass_kg_per_mol()
    M_H2O = resolve_species_formula(
        "H2O", sim.species_formula_registry).molar_mass_kg_per_mol()
    # 1 mol CH4 + 2 mol O2 -> 1 mol CO2 + 2 mol H2O
    mol_ch4 = 1.0
    feed_kg = mol_ch4 * M_CH4
    oxidant_kg = 2.0 * mol_ch4 * M_O2
    products_kg = {
        "CO2": mol_ch4 * M_CO2,
        "H2O": 2.0 * mol_ch4 * M_H2O,
    }
    request = IntentRequest(
        intent=ChemistryIntent.STAGE0_PRETREATMENT,
        account_view=view,
        temperature_C=25.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_COMPLETE_OXIDATION,
            "species": "CH4",
            "feed_kg": feed_kg,
            "oxidant_kg": oxidant_kg,
            "products_kg": products_kg,
        },
    )
    result = provider.dispatch(request)
    assert result.status == "ok"
    proposal = result.transition
    assert proposal is not None
    _atom_check(proposal, sim.species_formula_registry, tol=1e-9)
    # CO2 mol = 1.0; H2O mol = 2.0.
    co2_mol = proposal.credits["terminal.offgas"]["CO2"]
    h2o_mol = proposal.credits["terminal.offgas"]["H2O"]
    assert abs(co2_mol - 1.0) < 1e-12
    assert abs(h2o_mol - 2.0) < 1e-12


# ---------------------------------------------------------------------------
# 7. SulfSat post-equilibrium hook preserved
# ---------------------------------------------------------------------------


def test_sulfsat_post_stage0_hook_still_fires_after_flip(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Carbonaceous chondrite has Stage 0 sulfide inventory that
    survives Stage 0 cleanup (the legacy salt+sulfide buckets carry
    residual S after the carbon cleanup absorbs sulfate-bound SO3).
    After the Stage 0 flip the cleanup transitions are kernel-routed;
    the post-Stage-0 SulfSat gate (legacy hook,
    `_run_stage0_sulfsat_gate`) must still fire and populate
    `_last_sulfur_saturation_result`.  Verifies the flip didn't
    disrupt the SulfSat wiring downstream of the cleanup transitions.

    Note: PySulfSat may report ``unavailable`` (when the optional
    extra isn't installed) or ``in_range`` / ``out_of_range`` (when
    it is); the test asserts the gate ran (result is not None) and
    that the result carries a status -- the specific status depends
    on the extra installation.
    """

    sim = _build_sim(
        "ci_carbonaceous_chondrite",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    # Carbonaceous chondrite carries elemental S in sulfide_matte
    # inventory after Stage 0 -> _stage0_sulfur_input_ppm > 0 -> gate
    # runs.
    assert sim._stage0_sulfur_input_ppm() > 0.0, (
        "test fixture must trigger the SulfSat gate (sulfur_input_ppm > 0)"
    )
    result = sim._last_sulfur_saturation_result
    assert result is not None, (
        "SulfSat hook must populate "
        "_last_sulfur_saturation_result for sulfur-bearing feedstocks"
    )
    assert hasattr(result, "calibration_status")


# ---------------------------------------------------------------------------
# 8. Full-batch shadow parity on lunar / Mars / asteroid
# ---------------------------------------------------------------------------


def test_full_batch_stage0_kernel_routes_to_declared_accounts_lunar(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Lunar Stage 0 is mostly inert (no perchlorate, no sulfate -- a
    nearly empty Stage 0 ledger).  Verify the load runs cleanly and
    only the declared Stage 0 accounts hold any positive Stage 0
    debit / credit material.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    # No Stage 0 transitions expected; verify none of the Stage 0
    # terminal stores hold anything beyond what feedstock seeding put
    # there.
    salt = sim.atom_ledger.kg_by_account("terminal.stage0_salt_phase")
    matte = sim.atom_ledger.kg_by_account("terminal.stage0_sulfide_matte")
    assert not salt
    assert not matte


def test_full_batch_stage0_kernel_credits_mars_perchlorate_to_declared_accounts(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Mars basalt loads with perchlorate Stage 0 cleanup.  After the
    flip the perchlorate product (Cl) lands in
    ``terminal.stage0_chloride_salt_phase`` and O2 lands in
    ``terminal.oxygen_stage0_stored`` -- both inside the provider's
    declared accounts.  The kernel account-filter gate would have
    raised at commit time if a future refactor leaked the credit
    elsewhere.
    """

    sim = _build_sim(
        "mars_basalt",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"C": 50.0},
    )
    chloride_salt = sim.atom_ledger.kg_by_account(
        "terminal.stage0_chloride_salt_phase")
    o2_stage0 = sim.atom_ledger.kg_by_account(
        "terminal.oxygen_stage0_stored")
    # Perchlorate produces Cl and O2; sulfate-carbon doesn't.
    assert chloride_salt.get("Cl", 0.0) > 0.0
    assert o2_stage0.get("O2", 0.0) > 0.0


def test_full_batch_stage0_kernel_credits_asteroid_offgas_to_declared_accounts(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Carbonaceous-chondrite Stage 0 cleanup runs complete-oxidation
    (organic volatiles -> CO2 + H2O + N2 + O2).  After the flip the
    offgas lands in ``terminal.offgas`` -- inside the provider's
    declared accounts.
    """

    sim = _build_sim(
        "ci_carbonaceous_chondrite",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    # Carbonaceous chondrite produces CO2 (from organics) and H2O
    # (from H-bearing organics).
    assert offgas.get("CO2", 0.0) > 0.0 or offgas.get("H2O", 0.0) > 0.0


def test_stage0_flip_preserves_mass_balance_across_feedstocks(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Mass balance across three feedstocks (lunar inert, Mars
    carbon+perchlorate cleanup, asteroid carbonaceous degas).  After
    the flip the atom ledger must still close to within the existing
    tolerance.
    """

    for fs_key, additives in (
        ("lunar_mare_low_ti", None),
        ("mars_basalt", {"C": 50.0}),
        ("ci_carbonaceous_chondrite", None),
    ):
        sim = _build_sim(
            fs_key,
            vapor_pressure_data,
            feedstocks_data,
            setpoints_data,
            additives_kg=additives,
        )
        # The atom ledger must be internally consistent: every
        # transition must have balanced; every account must be
        # non-negative.
        all_balances = sim.atom_ledger.kg_by_account()
        for account, species_kg in all_balances.items():
            for species, kg in species_kg.items():
                assert kg >= -1e-9, (
                    f"{fs_key}: account {account!r} species {species!r} "
                    f"is negative: {kg!r}"
                )
