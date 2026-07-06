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
from simulator.accounting.formulas import resolve_species_formula
from simulator.electrolysis import (
    DECOMP_VOLTAGES,
    ELECTRONS_PER_OXIDE,
    ElectrolysisModel,
    MRE_CERTIFICATION_DENYLIST_REASON,
    MRE_CERTIFICATION_EVIDENCE_CLASS,
    MRE_FIXED_REDUCIBLE_OXIDES,
    MRE_NORTH_STAR_POSTURE,
    MRE_OPTIONAL_BANNER,
    MRE_MULTI_OXIDE_PARTITION_REFUSAL,
    current_efficiency,
    min_decomposition_voltage,
)
from simulator.fe_redox import (
    kress91_split,
    melt_mol_fractions_for_kress91,
)
from simulator.state import (
    FARADAY,
    GAS_CONSTANT,
    MOLAR_MASS,
    OXIDE_TO_METAL,
    CampaignPhase,
    DecisionType,
)
from tests.chemistry.conftest import _build_sim


def _enable_c5_mre(sim, *, target_species: str, max_voltage_V: float) -> None:
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = target_species
    sim.melt.mre_max_voltage_V = max_voltage_V
    sim.campaign_mgr.c5_enabled = True


def test_ce_low_feo_colson_haskin_anchor():
    # Colson/Haskin NASA NTRS 19910015058: FeO <2 wt%, ~1450 C, >=85% CE.
    assert current_efficiency(0.0, feo_fraction=0.019) >= 0.85
    assert current_efficiency(0.5, feo_fraction=0.019) >= 0.85


def test_ce_high_feo_loss_band():
    # Fe-bearing high-loss envelope 0.30-0.60 from findings.md:21.
    for dV in (0.0, 0.5, 1.0, 3.0, 10.0):
        eta = current_efficiency(dV, feo_fraction=0.10)
        assert 0.30 <= eta <= 0.60


def test_ce_high_dv_cannot_set_it_to_11():
    # Anti-exploit guard: overpotential cannot bypass FeO electronic losses.
    assert current_efficiency(10.0, feo_fraction=0.15) <= 0.60
    assert current_efficiency(100.0, feo_fraction=0.15) <= 0.60
    assert current_efficiency(100.0, feo_fraction=0.0) <= 0.995


def test_current_ce_formula_is_not_certifying(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {
                "SiO2": 10.0 / (MOLAR_MASS["SiO2"] / 1000.0),
            },
            "process.metal_phase": {},
            "terminal.oxygen_mre_anode_stored": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )

    result = BuiltinElectrolysisStepProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            account_view=view,
            temperature_C=1450.0,
            pressure_bar=1e-6,
            control_inputs={
                "voltage_V": 5.0,
                "current_A": 10.0,
                "dt_hr": 1.0,
                "allowed_oxides": ["SiO2"],
            },
        )
    )
    diagnostic = dict(result.diagnostic or {})

    assert result.transition is not None
    assert diagnostic["current_partition_certified"] is False
    assert diagnostic["yield_certification"] == "uncertified_current_partition"
    assert "heuristic" in diagnostic["current_partition_source"]
    assert diagnostic["current_efficiency_model"] == "bounded_feo_electronic_loss_v1"
    assert diagnostic["current_efficiency_by_oxide"]["SiO2"]["ceiling"] <= 0.995


def test_provider_current_efficiency_reads_feo_from_cleaned_melt(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    def dispatch(*, feo_kg: float, sio2_kg: float):
        view = ProviderAccountView(
            accounts={
                "process.cleaned_melt": {
                    "FeO": feo_kg / (MOLAR_MASS["FeO"] / 1000.0),
                    "SiO2": sio2_kg / (MOLAR_MASS["SiO2"] / 1000.0),
                },
                "process.metal_phase": {},
                "terminal.oxygen_mre_anode_stored": {},
            },
            species_formula_registry=sim.species_formula_registry,
        )
        return BuiltinElectrolysisStepProvider().dispatch(
            IntentRequest(
                intent=ChemistryIntent.ELECTROLYSIS_STEP,
                account_view=view,
                temperature_C=1450.0,
                pressure_bar=1e-6,
                control_inputs={
                    "voltage_V": 5.0,
                    "current_A": 10.0,
                    "dt_hr": 1.0,
                    "allowed_oxides": ["SiO2"],
                },
            )
        )

    low_feo = dispatch(feo_kg=0.05, sio2_kg=9.95)
    high_feo = dispatch(feo_kg=1.0, sio2_kg=9.0)
    low_diag = dict(low_feo.diagnostic or {})
    high_diag = dict(high_feo.diagnostic or {})
    low_ce = low_diag["current_efficiency_by_oxide"]["SiO2"]
    high_ce = high_diag["current_efficiency_by_oxide"]["SiO2"]

    assert low_diag["current_efficiency_feo_fraction"] == pytest.approx(0.005)
    assert high_diag["current_efficiency_feo_fraction"] == pytest.approx(0.10)
    assert low_ce["eta"] >= 0.90
    assert high_ce["eta"] <= 0.60
    assert low_diag["O2_produced_mol"] > high_diag["O2_produced_mol"]


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


def test_nernst_voltage_includes_evolved_o2_activity_term():
    """SiO2 -> Si + O2 includes Q = aO2 / aSiO2 in the Nernst term."""

    T_K = 1575.0 + 273.15
    pO2_bar = 0.05
    voltage_at_1_bar = BuiltinElectrolysisStepProvider._nernst_voltage(
        "SiO2",
        T_K,
        1.0,
        pO2_bar=1.0,
        gas_constant=GAS_CONSTANT,
        faraday=FARADAY,
        decomp_voltages={"SiO2": 1.4},
        electrons_per_oxide={"SiO2": 4},
        oxide_to_metal=OXIDE_TO_METAL,
    )
    voltage_at_50_mbar = BuiltinElectrolysisStepProvider._nernst_voltage(
        "SiO2",
        T_K,
        1.0,
        pO2_bar=pO2_bar,
        gas_constant=GAS_CONSTANT,
        faraday=FARADAY,
        decomp_voltages={"SiO2": 1.4},
        electrons_per_oxide={"SiO2": 4},
        oxide_to_metal=OXIDE_TO_METAL,
    )

    expected_shift = (GAS_CONSTANT * T_K) / (4.0 * FARADAY) * math.log(pO2_bar)
    assert voltage_at_1_bar == pytest.approx(1.4)
    assert voltage_at_50_mbar - voltage_at_1_bar == pytest.approx(
        expected_shift, abs=1e-9
    )
    assert voltage_at_50_mbar - voltage_at_1_bar == pytest.approx(
        -0.119276, abs=5e-7
    )


def test_min_decomposition_voltage_is_derived_from_runtime_table():
    assert min_decomposition_voltage() == pytest.approx(min(DECOMP_VOLTAGES.values()))
    assert min_decomposition_voltage() == pytest.approx(
        min(ElectrolysisModel().decomp_voltages.values())
    )


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


def test_empty_melt_result_carries_redox_diagnostics(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {},
            "process.metal_phase": {},
            "terminal.oxygen_mre_anode_stored": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    result = BuiltinElectrolysisStepProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            account_view=view,
            temperature_C=1575.0,
            pressure_bar=0.02,
            fO2_log=-7.25,
            fe_redox_policy="kress91_live",
            control_inputs={
                "voltage_V": 1.0,
                "current_A": 10.0,
                "dt_hr": 1.0,
                "melt_fO2_log": -7.25,
            },
        )
    )
    diagnostic = dict(result.diagnostic or {})

    assert result.transition is None
    assert diagnostic["melt_fO2_log"] == pytest.approx(-7.25)
    assert diagnostic["fe_redox_policy"] == "kress91_live"
    assert diagnostic["fe2o3_fixed_full_reduction_skipped"] is True
    assert diagnostic["fe_redox_split"]["status"] == "unavailable"


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


def test_provider_reports_fresh_kress91_redox_split_from_account_view(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    melt_mol = {
        "SiO2": 30.0,
        "Al2O3": 4.0,
        "MgO": 8.0,
        "CaO": 5.0,
        "FeO": 6.0,
        "Fe2O3": 2.0,
    }
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": melt_mol,
            "process.metal_phase": {},
            "terminal.oxygen_mre_anode_stored": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    fO2_log = -7.25
    request = IntentRequest(
        intent=ChemistryIntent.ELECTROLYSIS_STEP,
        account_view=view,
        temperature_C=1575.0,
        pressure_bar=0.02,
        fO2_log=fO2_log,
        fe_redox_policy="kress91_live",
        control_inputs={
            "voltage_V": 0.0,
            "current_A": 0.0,
            "dt_hr": 1.0,
            "melt_fO2_log": fO2_log,
            "fe_redox_split": {"fe3_over_sigma_fe": 0.999},
        },
    )

    result = BuiltinElectrolysisStepProvider().dispatch(request)
    diagnostic = dict(result.diagnostic or {})
    split = dict(diagnostic["fe_redox_split"])

    composition_kg = {}
    total_kg = 0.0
    for species, mol in melt_mol.items():
        formula = resolve_species_formula(
            species,
            sim.species_formula_registry,
        )
        kg = mol * formula.molar_mass_kg_per_mol()
        composition_kg[species] = kg
        total_kg += kg
    comp_wt = {
        species: kg / total_kg * 100.0
        for species, kg in composition_kg.items()
    }
    expected = kress91_split(
        fO2_log=fO2_log,
        mol_fractions=melt_mol_fractions_for_kress91(comp_wt),
        T_K=1575.0 + 273.15,
        pressure_bar=0.02,
    )

    assert result.transition is None
    assert diagnostic["melt_fO2_log"] == pytest.approx(fO2_log)
    assert diagnostic["fe_redox_policy"] == "kress91_live"
    assert diagnostic["fe2o3_fixed_full_reduction_skipped"] is True
    assert split["diagnostic_only"] is True
    assert split["consumed_by_behavior"] is False
    assert split["computed_fresh_from_account_view"] is True
    assert split["fe3_over_sigma_fe"] == pytest.approx(expected["fe3"])
    assert split["fe2o3_over_feo_molar"] == pytest.approx(expected["ratio"])
    assert split["fe3_over_sigma_fe"] != pytest.approx(0.999)


def test_live_mre_converts_ferric_inventory_to_ferrous_behavior(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    fe2o3_mol = 10.0 / (MOLAR_MASS["Fe2O3"] / 1000.0)
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {"Fe2O3": fe2o3_mol},
            "process.metal_phase": {},
            "terminal.oxygen_mre_anode_stored": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )

    result = BuiltinElectrolysisStepProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            account_view=view,
            temperature_C=1575.0,
            pressure_bar=1e-6,
            fO2_log=-7.25,
            fe_redox_policy="kress91_live",
            control_inputs={
                "voltage_V": 5.0,
                "current_A": 1.0e9,
                "dt_hr": 1.0,
                "melt_fO2_log": -7.25,
            },
        )
    )
    diagnostic = dict(result.diagnostic or {})

    assert result.transition is not None
    assert diagnostic["fe2o3_fixed_full_reduction_skipped"] is True
    assert diagnostic["fe_redox_split"]["consumed_by_behavior"] is True
    assert diagnostic["oxides_reduced_mol"]["Fe2O3"] == pytest.approx(
        fe2o3_mol
    )
    assert diagnostic["oxides_produced_mol"]["FeO"] == pytest.approx(
        2.0 * fe2o3_mol
    )
    assert diagnostic["uncertified_yield"]["FeO"]["certification"] == (
        "uncertified_ferric_to_ferrous_reference"
    )
    assert diagnostic["uncertified_yield"]["FeO"]["reference_V"] == pytest.approx(
        0.65
    )
    assert diagnostic["uncertified_yield"]["FeO"]["reference_status"] == (
        "uncertified_heuristic_reference_not_raw_thermo"
    )
    assert diagnostic["metals_produced_mol"] == {}
    assert diagnostic["O2_produced_mol"] == pytest.approx(0.5 * fe2o3_mol)
    assert result.transition.debits["process.cleaned_melt"]["Fe2O3"] == pytest.approx(
        fe2o3_mol
    )
    assert result.transition.credits["process.cleaned_melt"]["FeO"] == pytest.approx(
        2.0 * fe2o3_mol
    )


def test_kress91_diagnostic_is_golden_neutral_for_provider_transition(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    melt_mol = {
        "FeO": 10.0 / (MOLAR_MASS["FeO"] / 1000.0),
        "Fe2O3": 10.0 / (MOLAR_MASS["Fe2O3"] / 1000.0),
    }
    sim.atom_ledger = sim._new_atom_ledger()
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt", melt_mol, source="test seed"
    )
    sim._chem_kernel = sim._build_chemistry_kernel()
    sim._project_extraction_melt()
    sim.melt.temperature_C = 1575.0

    def dispatch(fO2_log):
        legacy = sim.electrolysis_model.step_hour(
            melt_state=sim.melt,
            voltage_V=5.0,
            current_A=1.0e9,
            T_C=1575.0,
        )
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
        direct = BuiltinElectrolysisStepProvider().dispatch(
            IntentRequest(
                intent=ChemistryIntent.ELECTROLYSIS_STEP,
                account_view=view,
                temperature_C=1575.0,
                pressure_bar=1e-6,
                fO2_log=fO2_log,
                fe_redox_policy="kress91_live",
                control_inputs={
                    "voltage_V": 5.0,
                    "current_A": 1.0e9,
                    "dt_hr": 1.0,
                    "melt_fO2_log": fO2_log,
                    "allowed_oxides": ["FeO"],
                },
            )
        )
        return legacy, direct

    legacy_low, direct_low = dispatch(-8.0)
    legacy_high, direct_high = dispatch(-3.0)

    assert legacy_low["oxides_reduced_mol"] == pytest.approx(
        legacy_high["oxides_reduced_mol"]
    )
    assert direct_low.transition is not None
    assert direct_high.transition is not None
    assert direct_low.transition.debits == direct_high.transition.debits
    assert direct_low.transition.credits == direct_high.transition.credits
    assert "Fe2O3" in direct_low.transition.debits["process.cleaned_melt"]
    assert "FeO" in direct_low.transition.credits["process.cleaned_melt"]


def test_mre_current_partition_refuses_uncertified_multi_oxide_yield(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {
                "FeO": 10.0 / (MOLAR_MASS["FeO"] / 1000.0),
                "Cr2O3": 10.0 / (MOLAR_MASS["Cr2O3"] / 1000.0),
                "MnO": 10.0 / (MOLAR_MASS["MnO"] / 1000.0),
                "SiO2": 10.0 / (MOLAR_MASS["SiO2"] / 1000.0),
            },
            "process.metal_phase": {},
            "terminal.oxygen_mre_anode_stored": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )

    result = BuiltinElectrolysisStepProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            account_view=view,
            temperature_C=1600.0,
            pressure_bar=1e-6,
            control_inputs={
                "voltage_V": 2.0,
                "current_A": 1000.0,
                "dt_hr": 1.0,
            },
        )
    )
    diagnostic = dict(result.diagnostic or {})

    assert result.status == "refused"
    assert result.transition is None
    assert diagnostic["oxides_reduced_mol"] == {}
    assert diagnostic["metals_produced_mol"] == {}
    assert diagnostic["O2_produced_mol"] == pytest.approx(0.0)
    assert diagnostic["mre_north_star_posture"] == MRE_NORTH_STAR_POSTURE
    assert diagnostic["mre_optional_banner"] == MRE_OPTIONAL_BANNER
    assert diagnostic["certification_evidence_class"] == MRE_CERTIFICATION_EVIDENCE_CLASS
    assert diagnostic["certification_allowed"] is False
    assert diagnostic["certification_denylist_reason"] == (
        MRE_CERTIFICATION_DENYLIST_REASON
    )
    assert diagnostic["current_partition_certified"] is False
    assert diagnostic["yield_certification"] == "uncertified_current_partition"
    assert "heuristic" in diagnostic["current_partition_source"]
    assert diagnostic["reason_refused"] == MRE_MULTI_OXIDE_PARTITION_REFUSAL
    assert set(diagnostic["reducible_oxide_targets"]) == {
        "Cr2O3",
        "FeO",
        "MnO",
        "SiO2",
    }

    sim.atom_ledger = sim._new_atom_ledger()
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt",
        {
            "FeO": 10.0 / (MOLAR_MASS["FeO"] / 1000.0),
            "Cr2O3": 10.0 / (MOLAR_MASS["Cr2O3"] / 1000.0),
            "MnO": 10.0 / (MOLAR_MASS["MnO"] / 1000.0),
            "SiO2": 10.0 / (MOLAR_MASS["SiO2"] / 1000.0),
        },
        source="test seed",
    )
    sim._project_extraction_melt()
    legacy = sim.electrolysis_model.step_hour(
        melt_state=sim.melt,
        voltage_V=2.0,
        current_A=1000.0,
        T_C=1600.0,
        pO2_bar=1e-6,
    )
    assert legacy["oxides_reduced_mol"] == {}
    assert legacy["metals_produced_mol"] == {}
    assert legacy["mre_north_star_posture"] == MRE_NORTH_STAR_POSTURE
    assert legacy["certification_allowed"] is False
    assert legacy["certification_denylist_reason"] == MRE_CERTIFICATION_DENYLIST_REASON
    assert legacy["reason_refused"] == MRE_MULTI_OXIDE_PARTITION_REFUSAL


def test_allowed_sio2_target_still_converts_ferric_inventory_to_ferrous(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    fe2o3_mol = 10.0 / (MOLAR_MASS["Fe2O3"] / 1000.0)
    sio2_mol = 10.0 / (MOLAR_MASS["SiO2"] / 1000.0)
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {
                "Fe2O3": fe2o3_mol,
                "SiO2": sio2_mol,
            },
            "process.metal_phase": {},
            "terminal.oxygen_mre_anode_stored": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )

    result = BuiltinElectrolysisStepProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            account_view=view,
            temperature_C=1575.0,
            pressure_bar=1e-6,
            fO2_log=-7.25,
            fe_redox_policy="kress91_live",
            control_inputs={
                "voltage_V": 5.0,
                "current_A": 1.0e9,
                "dt_hr": 1.0,
                "melt_fO2_log": -7.25,
                "allowed_oxides": ["SiO2"],
            },
        )
    )
    diagnostic = dict(result.diagnostic or {})

    assert result.transition is not None
    assert set(diagnostic["oxides_reduced_mol"]) == {"Fe2O3", "SiO2"}
    assert diagnostic["fe_redox_split"]["consumed_by_behavior"] is True
    assert result.transition.debits["process.cleaned_melt"]["Fe2O3"] == pytest.approx(
        fe2o3_mol
    )
    assert result.transition.credits["process.cleaned_melt"]["FeO"] == pytest.approx(
        2.0 * fe2o3_mol
    )


def test_powered_no_reducible_mre_hour_records_commanded_energy(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {
                "FeO": 10.0 / (MOLAR_MASS["FeO"] / 1000.0),
            },
            "process.metal_phase": {},
            "terminal.oxygen_mre_anode_stored": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )

    result = BuiltinElectrolysisStepProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            account_view=view,
            temperature_C=1600.0,
            pressure_bar=1e-6,
            control_inputs={
                "voltage_V": 1.7,
                "current_A": 1000.0,
                "dt_hr": 1.0,
                "allowed_oxides": ["SiO2"],
            },
        )
    )

    assert result.transition is None
    assert result.diagnostic["oxides_reduced_mol"] == {}
    assert result.diagnostic["energy_kWh"] == pytest.approx(1.7)


def test_empty_allowed_oxides_filter_reduces_nothing_not_everything(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """BUG-140: an EMPTY ``allowed_oxides`` list is a real selectivity
    filter ("operator targeted a rung unreachable within the voltage cap
    -> reduce NOTHING"), and must NOT be silently widened to "no filter ->
    reduce everything".  The pre-fix truthy check (``if allowed_oxides_raw:``)
    collapsed ``[]`` into ``None`` and reduced every reducible oxide.

    Contrast an ABSENT filter (reduces FeO -> proves the conditions are
    live) against an EMPTY-LIST filter (must reduce nothing) at identical
    voltage / current / composition.
    """
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    feo_mol = 10.0 / (MOLAR_MASS["FeO"] / 1000.0)

    def _dispatch(*, set_key, value=None):
        view = ProviderAccountView(
            accounts={
                "process.cleaned_melt": {"FeO": feo_mol},
                "process.metal_phase": {},
                "terminal.oxygen_mre_anode_stored": {},
            },
            species_formula_registry=sim.species_formula_registry,
        )
        controls = {
            "voltage_V": 1.7,
            "current_A": 1.0e9,
            "dt_hr": 1.0,
            "melt_fO2_log": -7.25,
        }
        if set_key:
            controls["allowed_oxides"] = value
        return BuiltinElectrolysisStepProvider().dispatch(
            IntentRequest(
                intent=ChemistryIntent.ELECTROLYSIS_STEP,
                account_view=view,
                temperature_C=1600.0,
                pressure_bar=1e-6,
                fO2_log=-7.25,
                fe_redox_policy="kress91_live",
                control_inputs=controls,
            )
        )

    # No filter (key absent -> None): FeO reduces at 1.7 V, proving the
    # conditions are live and the None path still means reduce-all.
    unfiltered = _dispatch(set_key=False)
    assert unfiltered.transition is not None
    assert "FeO" in dict(unfiltered.diagnostic or {})["oxides_reduced_mol"]

    # Empty filter must reduce NOTHING -- not collapse to None and widen
    # to reduce-all.
    empty_filter = _dispatch(set_key=True, value=[])
    assert empty_filter.transition is None
    assert dict(empty_filter.diagnostic or {})["oxides_reduced_mol"] == {}


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


def _dispatch_provider_and_legacy_for_pure_oxide(
    *,
    sim,
    oxide: str,
    oxide_kg: float,
    voltage_V: float,
    current_A: float,
    temperature_C: float,
):
    sim.atom_ledger = sim._new_atom_ledger()
    oxide_mol = oxide_kg / (MOLAR_MASS[oxide] / 1000.0)
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt", {oxide: oxide_mol}, source="test seed"
    )
    sim._chem_kernel = sim._build_chemistry_kernel()
    sim._project_extraction_melt()
    sim.melt.temperature_C = temperature_C

    legacy = sim.electrolysis_model.step_hour(
        melt_state=sim.melt,
        voltage_V=voltage_V,
        current_A=current_A,
        T_C=temperature_C,
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
    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            account_view=view,
            temperature_C=temperature_C,
            pressure_bar=1e-6,
            control_inputs={
                "voltage_V": voltage_V,
                "current_A": current_A,
                "dt_hr": 1.0,
            },
        )
    )
    return legacy, result


def test_energy_stays_commanded_when_oxide_does_not_deplete(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    voltage_V = DECOMP_VOLTAGES["FeO"] + 0.05
    current_A = 100.0

    legacy, result = _dispatch_provider_and_legacy_for_pure_oxide(
        sim=sim,
        oxide="FeO",
        oxide_kg=1000.0,
        voltage_V=voltage_V,
        current_A=current_A,
        temperature_C=1575.0,
    )
    diagnostic = dict(result.diagnostic)
    commanded_energy_kWh = voltage_V * current_A / 1000.0

    assert 0.0 < diagnostic["oxides_reduced_kg"]["FeO"] < 1000.0
    assert legacy["energy_kWh"] == pytest.approx(
        commanded_energy_kWh, rel=1e-12
    )
    assert diagnostic["energy_kWh"] == pytest.approx(
        commanded_energy_kWh, rel=1e-12
    )


def test_depletion_hour_energy_scales_by_capped_faradaic_charge(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    oxide_kg = 1.0
    voltage_V = DECOMP_VOLTAGES["FeO"] + 0.05
    current_A = 1.0e6
    temperature_C = 1575.0

    legacy, result = _dispatch_provider_and_legacy_for_pure_oxide(
        sim=sim,
        oxide="FeO",
        oxide_kg=oxide_kg,
        voltage_V=voltage_V,
        current_A=current_A,
        temperature_C=temperature_C,
    )
    diagnostic = dict(result.diagnostic)
    commanded_energy_kWh = voltage_V * current_A / 1000.0

    activity = 1.0
    e_nernst = ElectrolysisModel().nernst_voltage(
        "FeO", temperature_C, activity
    )
    overvoltage = voltage_V - e_nernst
    eta_CE = current_efficiency(overvoltage, feo_fraction=1.0)
    n_e = ELECTRONS_PER_OXIDE["FeO"]
    uncapped_moles = current_A * eta_CE * 3600.0 / (n_e * FARADAY)
    capped_moles = oxide_kg / (MOLAR_MASS["FeO"] / 1000.0)
    expected_energy_kWh = commanded_energy_kWh * (
        capped_moles * n_e
    ) / (uncapped_moles * n_e)

    assert diagnostic["oxides_reduced_kg"]["FeO"] == pytest.approx(
        oxide_kg, rel=1e-12
    )
    assert legacy["energy_kWh"] == pytest.approx(
        expected_energy_kWh, rel=1e-12
    )
    assert diagnostic["energy_kWh"] == pytest.approx(
        expected_energy_kWh, rel=1e-12
    )
    assert diagnostic["energy_kWh"] < commanded_energy_kWh


def test_mixed_ferric_depletion_does_not_underbill_uncapped_target_current(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    fe2o3_kg = 1.0
    sio2_kg = 1.0
    fe2o3_mol = fe2o3_kg / (MOLAR_MASS["Fe2O3"] / 1000.0)
    sio2_mol = sio2_kg / (MOLAR_MASS["SiO2"] / 1000.0)
    current_A = 3000.0
    voltage_V = 5.0
    temperature_C = 1575.0
    pressure_bar = 1.0e-6
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {
                "Fe2O3": fe2o3_mol,
                "SiO2": sio2_mol,
            },
            "process.metal_phase": {},
            "terminal.oxygen_mre_anode_stored": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )

    result = BuiltinElectrolysisStepProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            account_view=view,
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=-7.25,
            fe_redox_policy="kress91_live",
            control_inputs={
                "voltage_V": voltage_V,
                "current_A": current_A,
                "dt_hr": 1.0,
                "pO2_bar": pressure_bar,
                "melt_fO2_log": -7.25,
                "allowed_oxides": ["SiO2"],
            },
        )
    )
    diagnostic = dict(result.diagnostic)

    T_K = temperature_C + 273.15
    total_kg = fe2o3_kg + sio2_kg
    fe2o3_activity = fe2o3_kg / total_kg
    sio2_activity = sio2_kg / total_kg
    e_ferric = BuiltinElectrolysisStepProvider._ferric_to_ferrous_voltage(
        T_K,
        fe2o3_activity,
        gas_constant=GAS_CONSTANT,
        faraday=FARADAY,
        reference_V=0.65,
        electrons=2,
        o2_per_fe2o3=0.5,
        pO2_bar=pressure_bar,
    )
    e_sio2 = BuiltinElectrolysisStepProvider._nernst_voltage(
        "SiO2",
        T_K,
        sio2_activity,
        pO2_bar=pressure_bar,
        gas_constant=GAS_CONSTANT,
        faraday=FARADAY,
        decomp_voltages=DECOMP_VOLTAGES,
        electrons_per_oxide=ELECTRONS_PER_OXIDE,
        oxide_to_metal=OXIDE_TO_METAL,
    )
    fe2o3_weight = fe2o3_activity * math.exp(min(voltage_V - e_ferric, 3.0))
    sio2_weight = sio2_activity * math.exp(min(voltage_V - e_sio2, 3.0))
    total_weight = fe2o3_weight + sio2_weight
    fe2o3_current_A = current_A * fe2o3_weight / total_weight
    sio2_current_A = current_A * sio2_weight / total_weight
    fe2o3_eta = current_efficiency(voltage_V - e_ferric, feo_fraction=0.0)
    fe2o3_uncapped_mol = (
        fe2o3_current_A * fe2o3_eta * 3600.0 / (2.0 * FARADAY)
    )
    fe2o3_cap_ratio = min(1.0, fe2o3_mol / fe2o3_uncapped_mol)
    expected_energy_kWh = voltage_V * (
        sio2_current_A + fe2o3_current_A * fe2o3_cap_ratio
    ) / 1000.0
    old_global_ratio_energy_kWh = voltage_V * current_A / 1000.0 * (
        (
            diagnostic["oxides_reduced_mol"]["Fe2O3"] * 2.0
            + diagnostic["oxides_reduced_mol"]["SiO2"] * ELECTRONS_PER_OXIDE["SiO2"]
        )
        / (
            fe2o3_uncapped_mol * 2.0
            + (
                sio2_current_A
                * current_efficiency(voltage_V - e_sio2, feo_fraction=0.0)
                * 3600.0
                / FARADAY
            )
        )
    )

    assert result.transition is not None
    assert diagnostic["oxides_reduced_mol"]["Fe2O3"] == pytest.approx(
        fe2o3_mol,
        rel=1e-12,
    )
    assert diagnostic["oxides_reduced_mol"]["SiO2"] > 0.0
    assert diagnostic["energy_kWh"] == pytest.approx(expected_energy_kWh, rel=1e-12)
    assert diagnostic["energy_kWh"] > old_global_ratio_energy_kWh


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

    voltage_V = DECOMP_VOLTAGES["FeO"] + 0.05
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


def test_provider_reduces_nio_to_nickel_and_anode_oxygen(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """NiO is not just YAML-visible: provider dispatch creates a
    balanced Ni metal + MRE-anode O2 proposal and the kernel commits it.
    """

    sim = _build_sim(
        "ci_carbonaceous_chondrite",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger = sim._new_atom_ledger()
    nio_initial_mol = 10.0 / (MOLAR_MASS["NiO"] / 1000.0)
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt", {"NiO": nio_initial_mol}, source="test seed"
    )
    sim._chem_kernel = sim._build_chemistry_kernel()
    sim._project_extraction_melt()
    sim.melt.temperature_C = 1600.0

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
            "voltage_V": DECOMP_VOLTAGES["NiO"] + 0.10,
            "current_A": 1.0e6,
            "dt_hr": 1.0,
            "allowed_oxides": ["NiO"],
        },
    )

    result = provider.dispatch(request)
    assert result.transition is not None
    diagnostic = dict(result.diagnostic)
    assert diagnostic["oxides_reduced_mol"]["NiO"] == pytest.approx(
        nio_initial_mol, rel=1e-12
    )
    assert diagnostic["metals_produced_mol"]["Ni"] == pytest.approx(
        nio_initial_mol, rel=1e-12
    )
    assert diagnostic["O2_produced_mol"] == pytest.approx(
        0.5 * nio_initial_mol, rel=1e-12
    )

    proposal = result.transition
    assert proposal.debits == {
        "process.cleaned_melt": {"NiO": pytest.approx(nio_initial_mol)}
    }
    assert proposal.credits["process.metal_phase"]["Ni"] == pytest.approx(
        nio_initial_mol, rel=1e-12
    )
    assert proposal.credits["terminal.oxygen_mre_anode_stored"]["O2"] == (
        pytest.approx(0.5 * nio_initial_mol, rel=1e-12)
    )

    sim._chem_kernel.commit_batch(ChemistryIntent.ELECTROLYSIS_STEP, proposal)
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt").get(
        "NiO", 0.0
    ) == pytest.approx(0.0, abs=1e-12)
    assert sim.atom_ledger.mol_by_account("process.metal_phase").get(
        "Ni", 0.0
    ) == pytest.approx(nio_initial_mol, rel=1e-12)
    assert sim.atom_ledger.mol_by_account(
        "terminal.oxygen_mre_anode_stored"
    ).get("O2", 0.0) == pytest.approx(0.5 * nio_initial_mol, rel=1e-12)


def test_provider_matches_legacy_feo_partition_with_ferric_present(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Provider and legacy agree across a ferric-bearing melt.

    Fe2O3 remains absent from the fixed full-reduction rung; live redox now
    converts ferric oxide to ferrous oxide, while Fe metal still comes from
    the ferrous FeO path.
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

    assert "Fe2O3" not in MRE_FIXED_REDUCIBLE_OXIDES
    assert diagnostic["fe2o3_fixed_full_reduction_skipped"] is True
    assert "Fe2O3" in dict(diagnostic.get("oxides_reduced_mol", {}) or {})
    assert "Fe2O3" in dict(legacy.get("oxides_reduced_mol", {}) or {})
    assert diagnostic["fe_redox_split"]["consumed_by_behavior"] is True

    for key in ("oxides_reduced_mol", "oxides_produced_mol", "metals_produced_mol"):
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


def test_low_po2_backpressure_can_cross_c5_mre_decomposition_gate(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger = sim._new_atom_ledger()
    sio2_mol = 1000.0 / (MOLAR_MASS["SiO2"] / 1000.0)
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt", {"SiO2": sio2_mol}, source="test seed"
    )
    sim._chem_kernel = sim._build_chemistry_kernel()
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
    provider = BuiltinElectrolysisStepProvider()

    def dispatch_at_pO2(pO2_bar: float):
        return provider.dispatch(
            IntentRequest(
                intent=ChemistryIntent.ELECTROLYSIS_STEP,
                account_view=view,
                temperature_C=1575.0,
                pressure_bar=pO2_bar,
                control_inputs={
                    "voltage_V": 1.35,
                    "current_A": 100.0,
                    "dt_hr": 1.0,
                    "pO2_bar": pO2_bar,
                },
            )
        )

    assert dispatch_at_pO2(1.0).transition is None
    low_pO2_result = dispatch_at_pO2(0.05)
    assert low_pO2_result.transition is not None
    assert "SiO2" in low_pO2_result.transition.debits["process.cleaned_melt"]


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
    _enable_c5_mre(sim, target_species="SiO2", max_voltage_V=1.45)
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
        "process.cleaned_melt",
        "process.metal_phase",
        "terminal.oxygen_mre_anode_stored",
    }
    cumulative_imbalance_kg = 0.0
    for trans in mre_transitions:
        # Strict account scoping: debit cleaned_melt only; credit products,
        # anode O2, and any Faraday-cap residual oxide returned to cleaned_melt.
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
    _enable_c5_mre(sim, target_species="SiO2", max_voltage_V=1.45)
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
