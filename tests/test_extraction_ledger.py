import shlex

import pytest

from engines.builtin.metallothermic_step import SPENT_REDUCTANT_RESIDUE_ACCOUNT
from simulator.account_ids import C7_AL_CREDIT_ACCOUNT
from simulator.accounting import AccountingQueries, MaterialLot
from simulator.chemistry.kernel.capabilities import (
    CapabilityProfile,
    ChemistryIntent,
)
from simulator.chemistry.kernel.dto import (
    IntentRequest,
    IntentResult,
    LedgerTransitionProposal,
)
from simulator.chemistry.kernel.provider import ChemistryProvider
from simulator.core import PyrolysisSimulator
from simulator.electrolysis import (
    DECOMP_VOLTAGES,
    FERRIC_TO_FERROUS_REFERENCE_V,
    ElectrolysisModel,
    MRE_MULTI_OXIDE_PARTITION_REFUSAL,
)
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.session_cli import SessionScriptRunner
from simulator.state import (
    MOLAR_MASS,
    OXIDE_TO_METAL,
    CampaignPhase,
    MeltState,
)
from web.events import _completion_payload


def _sim(feedstocks):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        feedstocks,
        {"metals": {}, "oxide_vapors": {}},
    )


def _run_session_script(lines: list[str]):
    runner = SessionScriptRunner()
    for line in lines:
        runner.execute(shlex.split(line), line)
    return runner.session._sim


def _sim_with_c7_al_credit():
    sim = _sim(
        {
            "oxide": {
                "label": "Al oxide",
                "composition_wt_pct": {"Al2O3": 100.0},
            }
        }
    )
    sim.load_batch("oxide", mass_kg=MOLAR_MASS["Al2O3"])
    sim.atom_ledger.transfer(
        "test_c7_al_credit",
        debits=(
            MaterialLot(
                "process.cleaned_melt",
                {"Al2O3": MOLAR_MASS["Al2O3"]},
                source="test C7 credit source",
            ),
        ),
        credits=(
            MaterialLot(
                C7_AL_CREDIT_ACCOUNT,
                {"Al": 2.0 * MOLAR_MASS["Al"]},
                source="test C7 Al credit",
            ),
            MaterialLot(
                "process.overhead_gas",
                {"O2": 1.5 * MOLAR_MASS["O2"]},
                source="test C7 oxygen byproduct",
            ),
        ),
        reason="seed elemental Al credit for read-side consumer tests",
    )
    return sim


def _run_c2a_staged():
    return _run_session_script([
        (
            "start --feedstock=lunar_mare_low_ti --campaign=C2A_staged "
            "--additive=K=26.0"
        ),
        "adjust campaign_override C2A_staged hold_temp_C 1750.0",
        "advance 30",
    ])


class _FixedElectrolysisStepProvider(ChemistryProvider):
    """Test double for ``BuiltinElectrolysisStepProvider``.

    Returns a fixed :class:`LedgerTransitionProposal` (built from
    ``oxide_mol`` / ``metal_mol`` / ``O2_mol`` constructor args) so the
    tests can assert ``_step_mre``'s ledger / routing behaviour with
    deterministic numbers, the same way the legacy
    ``FixedElectrolysis.step_hour`` did before the ELECTROLYSIS_STEP
    intent was kernel-flipped.
    """

    name = "test-fixed-electrolysis-step"

    DECLARED_ACCOUNTS = frozenset({
        "process.cleaned_melt",
        "process.metal_phase",
        "terminal.oxygen_mre_anode_stored",
    })

    def __init__(
        self,
        *,
        oxide_species: str,
        oxide_mol: float,
        metal_species: str,
        metal_mol: float,
        O2_mol: float,
        oxide_kg: float,
        metal_kg: float,
        O2_kg: float,
        energy_kWh: float,
    ) -> None:
        self._oxide_species = oxide_species
        self._oxide_mol = float(oxide_mol)
        self._metal_species = metal_species
        self._metal_mol = float(metal_mol)
        self._O2_mol = float(O2_mol)
        self._oxide_kg = float(oxide_kg)
        self._metal_kg = float(metal_kg)
        self._O2_kg = float(O2_kg)
        self._energy_kWh = float(energy_kWh)

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="test-fixed-electrolysis-step",
            intents=frozenset({ChemistryIntent.ELECTROLYSIS_STEP}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.ELECTROLYSIS_STEP}
            ),
            declared_accounts=self.DECLARED_ACCOUNTS,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        debits: dict[str, dict[str, float]] = {
            "process.cleaned_melt": {self._oxide_species: self._oxide_mol},
        }
        credits: dict[str, dict[str, float]] = {}
        if self._metal_mol > 0.0:
            credits["process.metal_phase"] = {
                self._metal_species: self._metal_mol,
            }
        if self._O2_mol > 0.0:
            credits["terminal.oxygen_mre_anode_stored"] = {
                "O2": self._O2_mol,
            }
        proposal = LedgerTransitionProposal(
            debits=debits,
            credits=credits,
            reason="mre_electrolysis_reduction",
        )
        diagnostic = {
            "oxides_reduced_kg": {self._oxide_species: self._oxide_kg},
            "oxides_reduced_mol": {self._oxide_species: self._oxide_mol},
            "metals_produced_kg": {self._metal_species: self._metal_kg},
            "metals_produced_mol": {self._metal_species: self._metal_mol},
            "O2_produced_kg": self._O2_kg,
            "O2_produced_mol": self._O2_mol,
            "energy_kWh": self._energy_kWh,
        }
        return IntentResult(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            status="ok",
            transition=proposal,
            diagnostic=diagnostic,
        )


def _install_fixed_mre_provider(sim, **kwargs) -> None:
    """Replace the authoritative ELECTROLYSIS_STEP provider in-place.

    The kernel registry is rebuilt per ``load_batch`` but the
    underlying ``ProviderRegistry`` persists.  Overwriting the
    authoritative entry directly is the test-time path the kernel
    contract does NOT offer publicly (``register_idempotent`` rejects a
    different-provider-id swap by design).  Test-only -- production
    code never goes through this seam.
    """

    provider = _FixedElectrolysisStepProvider(**kwargs)
    # ``register_idempotent`` rejects a provider_id swap, so this test
    # uses the dedicated ``replace_for_test`` seam on ProviderRegistry
    # (0.5.4.1 B3 / M1 closure: replaces the prior direct
    # ``_chem_registry._authoritative[...] = provider`` private-dict
    # mutation, which would break silently if the registry internals
    # were renamed). Method name carries ``_for_test`` so a future
    # code-review can flag any production caller.
    sim._chem_registry.replace_for_test(
        ChemistryIntent.ELECTROLYSIS_STEP, provider
    )


def _enable_c5_mre(sim, *, target_species: str, max_voltage_V: float) -> None:
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = target_species
    sim.melt.mre_max_voltage_V = max_voltage_V
    sim.campaign_mgr.c5_enabled = True


def _assert_product_matches_account(sim, account, species):
    account_kg = sim.atom_ledger.kg_by_account(account).get(species, 0.0)
    assert account_kg > 0.0
    assert sim.product_ledger()[species] == pytest.approx(account_kg)


def test_product_ledger_includes_c7_al_credit():
    sim = _sim_with_c7_al_credit()
    al_credit_kg = sim.atom_ledger.kg_by_account(C7_AL_CREDIT_ACCOUNT)["Al"]

    assert sim.product_ledger()["Al"] == pytest.approx(al_credit_kg)


def test_rump_element_kg_includes_c7_al_credit():
    sim = _sim_with_c7_al_credit()
    al_credit_kg = sim.atom_ledger.kg_by_account(C7_AL_CREDIT_ACCOUNT)["Al"]

    assert sim._rump_element_kg("Al") == pytest.approx(al_credit_kg)
    assert sim._actual_rump_elements_kg()["Al"] == pytest.approx(al_credit_kg)
    assert sum(sim._terminal_rump_by_class().values()) == pytest.approx(
        al_credit_kg
    )


def test_c7_credit_seeded_as_non_feedstock_provenance():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {"C7": {"al_credit_limit_kg": 7.5}}},
        {
            "oxide": {
                "label": "Al oxide",
                "composition_wt_pct": {"Al2O3": 100.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("oxide", mass_kg=MOLAR_MASS["Al2O3"])

    sim._init_c7_al_credit()

    assert sim.product_ledger()["Al"] == pytest.approx(7.5)
    assert sim._rump_element_kg("Al") >= 7.5
    assert AccountingQueries(sim).non_feedstock_reagent_element_kg_by_account()[
        C7_AL_CREDIT_ACCOUNT
    ]["Al"] == pytest.approx(7.5)


def test_c2a_staged_rump_expectation_keeps_ca_and_al():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"CaO": 40.0, "Al2O3": 60.0},
            }
        }
    )
    sim.load_batch("oxide", mass_kg=1000.0)
    sim.start_campaign(CampaignPhase.C2A_STAGED)

    diagnostic = sim._rump_expectation_diagnostic(CampaignPhase.C2A_STAGED)

    assert diagnostic["actual_rump_elements_kg"]["Ca"] > 0.0
    assert diagnostic["actual_rump_elements_kg"]["Al"] > 0.0
    assert set(diagnostic["expected_unconsumed_rump_elements"]) == {"Al", "Ca"}
    assert diagnostic["missing_expected_rump_elements"] == []


def test_c6_rump_expectation_treats_thermited_al_as_target():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"CaO": 90.0, "Al2O3": 10.0},
            }
        }
    )
    sim.load_batch("oxide", mass_kg=1000.0, additives_kg={"Mg": 1000.0})
    sim.start_campaign(CampaignPhase.C6)

    sim._step_thermite()

    diagnostic = sim._rump_expectation_diagnostic(CampaignPhase.C6)

    assert diagnostic["actual_rump_elements_kg"]["Ca"] > 0.0
    assert "Al" not in diagnostic["actual_rump_elements_kg"]
    assert diagnostic["expected_unconsumed_rump_elements"] == ["Ca"]
    assert diagnostic["targeted_rump_elements"] == ["Al"]
    assert diagnostic["missing_expected_rump_elements"] == []


def test_c5_branch_one_rump_expectation_honors_c5_targets():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"CaO": 100.0},
            }
        }
    )
    sim.setpoints["campaigns"]["C5"] = {"c5_targets": ["Ca"]}
    sim.load_batch("oxide", mass_kg=10.0)
    _enable_c5_mre(sim, target_species="CaO", max_voltage_V=2.5)
    sim.record.branch = "one"
    sim.start_campaign(CampaignPhase.C5)

    cao_removed_kg = 10.0
    cao_removed_mol = cao_removed_kg / (MOLAR_MASS["CaO"] / 1000.0)
    ca_kg = cao_removed_kg * MOLAR_MASS["Ca"] / MOLAR_MASS["CaO"]
    o2_kg = cao_removed_kg * MOLAR_MASS["O"] / MOLAR_MASS["CaO"]
    o2_mol = cao_removed_mol / 2.0

    _install_fixed_mre_provider(
        sim,
        oxide_species="CaO",
        oxide_mol=cao_removed_mol,
        metal_species="Ca",
        metal_mol=cao_removed_mol,
        O2_mol=o2_mol,
        oxide_kg=cao_removed_kg,
        metal_kg=ca_kg,
        O2_kg=o2_kg,
        energy_kWh=1.25,
    )
    sim._mre_voltage_sequence = [{
        "voltage": 2.5,
        "species": ["CaO"],
        "min_hold_hours": 1,
    }]
    sim._mre_voltage_step_idx = 0
    sim._mre_hold_hours = 0
    sim._mre_effective_current_A = 3000.0

    sim._step_mre()
    diagnostic = sim._rump_expectation_diagnostic(CampaignPhase.C5)

    assert "Ca" not in diagnostic["actual_rump_elements_kg"]
    assert diagnostic["targeted_rump_elements"] == ["Ca"]
    assert diagnostic["missing_expected_rump_elements"] == []


def test_mre_reduction_records_atom_ledger_transition():
    """After the ELECTROLYSIS_STEP kernel flip, ``_step_mre``'s ledger
    behaviour comes from a :class:`BuiltinElectrolysisStepProvider`
    proposal committed via :meth:`ChemistryKernel.commit_batch`. We
    inject a test double provider with deterministic numbers (the
    kernel-aware equivalent of the pre-flip ``FixedElectrolysis`` mock)
    and verify the same ledger / routing invariants the legacy test
    enforced.
    """

    sim = _sim(
        {
            "feo": {
                "label": "FeO",
                "composition_wt_pct": {"FeO": 100.0},
            }
        }
    )
    sim.load_batch("feo", mass_kg=1000.0)
    _enable_c5_mre(sim, target_species="FeO", max_voltage_V=0.75)

    feo_removed_kg = 1.0
    feo_removed_mol = feo_removed_kg / (MOLAR_MASS["FeO"] / 1000.0)
    fe_kg = feo_removed_kg * MOLAR_MASS["Fe"] / MOLAR_MASS["FeO"]
    o2_kg = feo_removed_kg * MOLAR_MASS["O"] / MOLAR_MASS["FeO"]
    o2_mol = feo_removed_mol / 2.0

    _install_fixed_mre_provider(
        sim,
        oxide_species="FeO",
        oxide_mol=feo_removed_mol,
        metal_species="Fe",
        metal_mol=feo_removed_mol,
        O2_mol=o2_mol,
        oxide_kg=feo_removed_kg,
        metal_kg=fe_kg,
        O2_kg=o2_kg,
        energy_kWh=1.25,
    )

    sim.melt.campaign = CampaignPhase.C5
    sim._mre_voltage_sequence = [{
        "voltage": DECOMP_VOLTAGES["FeO"],
        "species": ["FeO"],
        "min_hold_hours": 1,
    }]
    # BUG-048 (SC-09 single-source): the FeO ladder voltage is the canonical
    # DECOMP_VOLTAGES value (0.75 V), NOT the stale hard-coded 0.6 V the bug
    # carried. Pin against the literals so this can actually fail on regression
    # (asserting == DECOMP_VOLTAGES["FeO"] after setting from it is vacuous).
    assert sim._mre_voltage_sequence[0]["voltage"] == pytest.approx(0.75)
    assert sim._mre_voltage_sequence[0]["voltage"] != pytest.approx(0.6)
    sim._mre_voltage_step_idx = 0
    sim._mre_hold_hours = 0
    sim._mre_effective_current_A = 100.0

    assert sim._step_mre() == pytest.approx(o2_kg)

    sim.atom_ledger.assert_balanced()
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt")[
        "FeO"
    ] == pytest.approx(999.0)
    assert sim.atom_ledger.kg_by_account("process.metal_phase")[
        "Fe"
    ] == pytest.approx(fe_kg)
    assert sim.product_ledger()["Fe"] == pytest.approx(fe_kg)
    assert sim.atom_ledger.kg_by_account("terminal.oxygen_mre_anode_stored")[
        "O2"
    ] == pytest.approx(o2_kg)
    assert sim.train.stages[1].collected_kg["Fe"] == pytest.approx(fe_kg)
    assert sim._mre_metals_this_hr["Fe"] == pytest.approx(fe_kg)
    assert sim._mre_energy_this_hr == pytest.approx(1.25)
    assert sim._oxygen_stored_kg() == pytest.approx(o2_kg)


def test_mre_returned_oxygen_kg_comes_from_ledger_mol():
    """``_step_mre`` returns the O2 kg delta read back from the ledger
    after the kernel commits the proposal. The legacy test injected a
    ``step_hour`` mock that returned 0 in ``O2_produced_kg`` but
    non-zero in ``O2_produced_mol``; after the kernel flip the proposal
    is mol-native (the kg is derived at commit time via
    ``mol * MW``), so the test is naturally rephrased: the test
    double's proposal credits O2 in mol, the kernel commits via the
    registry's MW path, and ``_step_mre`` reads back the kg delta from
    ``terminal.oxygen_mre_anode_stored``.
    """

    sim = _sim(
        {
            "feo": {
                "label": "FeO",
                "composition_wt_pct": {"FeO": 100.0},
            }
        }
    )
    sim.load_batch("feo", mass_kg=1000.0)
    _enable_c5_mre(sim, target_species="FeO", max_voltage_V=0.75)

    feo_removed_kg = 1.0
    feo_removed_mol = feo_removed_kg / (MOLAR_MASS["FeO"] / 1000.0)
    fe_kg = feo_removed_kg * MOLAR_MASS["Fe"] / MOLAR_MASS["FeO"]
    o2_mol = feo_removed_mol / 2.0
    # The pre-flip test passed O2_produced_kg=0 but O2_produced_mol=non-zero;
    # _step_mre then read O2 kg from the ledger. After the kernel flip the
    # proposal is mol-native so the kg-vs-mol mismatch the legacy test
    # constructed is no longer expressible -- the kg is derived from mol
    # at commit time. The behavioural assertion (returned == ledger
    # readback) still holds.
    o2_kg_from_mol = o2_mol * MOLAR_MASS["O2"] / 1000.0

    _install_fixed_mre_provider(
        sim,
        oxide_species="FeO",
        oxide_mol=feo_removed_mol,
        metal_species="Fe",
        metal_mol=feo_removed_mol,
        O2_mol=o2_mol,
        oxide_kg=feo_removed_kg,
        metal_kg=fe_kg,
        O2_kg=0.0,  # diagnostic only -- not used by post-flip _step_mre
        energy_kWh=1.25,
    )

    sim.melt.campaign = CampaignPhase.C5
    sim._mre_voltage_sequence = [{
        "voltage": DECOMP_VOLTAGES["FeO"],
        "species": ["FeO"],
        "min_hold_hours": 1,
    }]
    # BUG-048 (SC-09 single-source): the FeO ladder voltage is the canonical
    # DECOMP_VOLTAGES value (0.75 V), NOT the stale hard-coded 0.6 V the bug
    # carried. Pin against the literals so this can actually fail on regression
    # (asserting == DECOMP_VOLTAGES["FeO"] after setting from it is vacuous).
    assert sim._mre_voltage_sequence[0]["voltage"] == pytest.approx(0.75)
    assert sim._mre_voltage_sequence[0]["voltage"] != pytest.approx(0.6)
    sim._mre_voltage_step_idx = 0
    sim._mre_hold_hours = 0
    sim._mre_effective_current_A = 100.0

    produced_kg = sim._step_mre()

    ledger_kg = sim.atom_ledger.kg_by_account(
        "terminal.oxygen_mre_anode_stored")["O2"]
    assert produced_kg == pytest.approx(ledger_kg)
    assert produced_kg > 0.0
    assert produced_kg == pytest.approx(o2_kg_from_mol)


def test_mre_refused_partition_fails_loud_without_energy_or_products():
    sim = _sim(
        {
            "mixed": {
                "label": "Mixed reducible oxides",
                "composition_wt_pct": {
                    "FeO": 25.0,
                    "Cr2O3": 25.0,
                    "MnO": 25.0,
                    "SiO2": 25.0,
                },
            }
        }
    )
    sim.load_batch("mixed", mass_kg=1000.0)
    _enable_c5_mre(sim, target_species="", max_voltage_V=2.0)
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.temperature_C = 1600.0
    sim.melt.hour = 42
    sim.melt.campaign_hour = 7
    sim._mre_voltage_sequence = [{
        "voltage": 2.0,
        "species": ["FeO", "Cr2O3", "MnO", "SiO2"],
        "min_hold_hours": 1,
    }]
    sim._mre_voltage_step_idx = 0
    sim._mre_hold_hours = 0
    sim._mre_effective_current_A = 1000.0

    no_op_before = sim._chem_no_op_dispatch_count
    melt_before = dict(sim.atom_ledger.kg_by_account("process.cleaned_melt"))
    metal_before = dict(sim.atom_ledger.kg_by_account("process.metal_phase"))
    o2_before = dict(
        sim.atom_ledger.kg_by_account("terminal.oxygen_mre_anode_stored")
    )

    with pytest.raises(RuntimeError, match=MRE_MULTI_OXIDE_PARTITION_REFUSAL):
        sim._step_mre()

    assert sim._chem_no_op_dispatch_count == no_op_before + 1
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt") == pytest.approx(
        melt_before
    )
    assert sim.atom_ledger.kg_by_account("process.metal_phase") == pytest.approx(
        metal_before
    )
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_mre_anode_stored"
    ) == pytest.approx(o2_before)
    assert sim.product_ledger() == {}
    assert sim._mre_metals_this_hr == {}
    assert sim._mre_energy_this_hr == pytest.approx(0.0)
    assert sim._mre_voltage_V == pytest.approx(0.0)
    assert sim._mre_current_A == pytest.approx(0.0)
    assert sim._mre_effective_current_A == pytest.approx(0.0)
    assert sim.melt.mre_voltage_V == pytest.approx(0.0)
    assert sim.melt.mre_current_A == pytest.approx(0.0)

    refusal = sim._last_mre_refusal_diagnostic
    assert refusal["intent"] == ChemistryIntent.ELECTROLYSIS_STEP.name
    assert refusal["hour"] == 42
    assert refusal["campaign_hour"] == 7
    assert refusal["campaign"] == CampaignPhase.C5.name
    assert refusal["temperature_C"] == pytest.approx(1600.0)
    assert refusal["voltage_V"] == pytest.approx(2.0)
    assert refusal["current_A"] == pytest.approx(1000.0)
    assert refusal["diagnostic"]["reason_refused"] == (
        MRE_MULTI_OXIDE_PARTITION_REFUSAL
    )
    assert refusal["diagnostic"]["energy_kWh"] > 0.0
    assert sim._mre_refusal_history == [refusal]


def test_condensed_species_projection_does_not_double_count_across_stages():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"FeO": 100.0},
            }
        }
    )
    sim.load_batch("oxide", mass_kg=1000.0)
    sim.atom_ledger.load_external(
        "process.condensation_train",
        {"Fe": 2.0},
        source="test condensed Fe",
    )
    sim.train.stages[2].collected_kg["Fe"] = 1.0

    sim._project_condensed_species(1, "Fe")

    assert sim.train.stages[1].collected_kg["Fe"] == pytest.approx(1.0)
    assert sim.train.stages[2].collected_kg["Fe"] == pytest.approx(1.0)
    assert sim.train.total_by_species()["Fe"] == pytest.approx(2.0)


def test_condensed_species_projection_delta_cannot_exceed_ledger_total():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"FeO": 100.0},
            }
        }
    )
    sim.load_batch("oxide", mass_kg=1000.0)
    sim.atom_ledger.load_external(
        "process.condensation_train",
        {"Fe": 2.0},
        source="test condensed Fe",
    )
    sim.train.stages[2].collected_kg["Fe"] = 1.0

    sim._project_condensed_species(1, "Fe", delta_kg=2.0)

    assert sim.train.stages[1].collected_kg["Fe"] == pytest.approx(1.0)
    assert sim.train.stages[2].collected_kg["Fe"] == pytest.approx(1.0)
    assert sim.train.total_by_species()["Fe"] == pytest.approx(2.0)


def test_electrolysis_skips_ferric_full_reduction_with_feo_present():
    melt = MeltState()
    melt.composition_kg = {"FeO": 10.0, "Fe2O3": 10.0}
    melt.update_total_mass()

    result = ElectrolysisModel().step_hour(
        melt_state=melt,
        voltage_V=5.0,
        current_A=1.0e9,
        T_C=1600.0,
    )

    metal, n_metal, _n_oxygen = OXIDE_TO_METAL["FeO"]
    expected_fe_kg = (
        result["oxides_reduced_kg"]["FeO"]
        * n_metal
        * MOLAR_MASS[metal]
        / MOLAR_MASS["FeO"]
    )

    assert "Fe2O3" in result["oxides_reduced_kg"]
    assert result["oxides_produced_kg"]["FeO"] > 0.0
    assert result["metals_produced_kg"]["Fe"] == pytest.approx(expected_fe_kg)
    assert result["O2_produced_mol"] > 0.0


def test_ferric_reference_threshold_is_not_fixed_mre_sequence():
    melt = MeltState()
    melt.composition_kg = {"FeO": 10.0, "Fe2O3": 10.0}
    melt.update_total_mass()

    sequence = ElectrolysisModel().get_reduction_sequence(melt, T_C=1600.0)
    order = [oxide for oxide, _voltage in sequence]

    assert "FeO" in order
    assert "Fe2O3" not in order
    assert FERRIC_TO_FERROUS_REFERENCE_V < 0.75


def test_k_shuttle_draws_from_process_reagent_inventory():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"FeO": 50.0, "SiO2": 50.0},
            }
        }
    )
    sim.setpoints.setdefault("campaigns", {}).setdefault("C3", {}).setdefault(
        "alkali_dosing", {}
    )["K_kg"] = 9.0
    sim.load_batch("oxide", mass_kg=1000.0, additives_kg={})
    sim._init_shuttle_inventory(CampaignPhase.C3_K)

    assert sim.atom_ledger.kg_by_account("reservoir.reagent.K").get(
        "K", 0.0
    ) == pytest.approx(-9.0)
    assert sim.atom_ledger.kg_by_account("process.reagent_inventory")[
        "K"
    ] == pytest.approx(9.0)
    assert sim._c3_alkali_credit_drawn_kg_by_species["K"] == pytest.approx(9.0)
    assert sim._c3_alkali_credit_outstanding_kg_by_species()["K"] == (
        pytest.approx(9.0)
    )

    sim._shuttle_inject_K()

    sim.atom_ledger.assert_balanced()
    assert sim._shuttle_injected_this_hr > 0.0
    assert sim.atom_ledger.kg_by_account("process.reagent_inventory")[
        "K"
    ] == pytest.approx(9.0 - sim._shuttle_injected_this_hr)
    assert sim.shuttle_K_inventory_kg == pytest.approx(
        sim.atom_ledger.kg_by_account("process.reagent_inventory")["K"]
    )
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt")[
        "K2O"
    ] > 0.0
    assert sim.atom_ledger.kg_by_account("process.metal_phase")[
        "Fe"
    ] == pytest.approx(sim.train.stages[1].collected_kg["Fe"])
    _assert_product_matches_account(sim, "process.metal_phase", "Fe")


def test_na_shuttle_metals_are_reported_from_process_metal_phase():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"Cr2O3": 1.0, "TiO2": 99.0},
            }
        }
    )
    sim.setpoints.setdefault("campaigns", {}).setdefault("C3", {}).setdefault(
        "alkali_dosing", {}
    )["Na_kg"] = 60.0
    sim.load_batch("oxide", mass_kg=1000.0, additives_kg={})
    sim._init_shuttle_inventory(CampaignPhase.C3_NA)
    assert sim.atom_ledger.kg_by_account("reservoir.reagent.Na").get(
        "Na", 0.0
    ) == pytest.approx(-60.0)
    assert sim._c3_alkali_credit_drawn_kg_by_species["Na"] == pytest.approx(60.0)

    sim._shuttle_inject_Na()

    sim.atom_ledger.assert_balanced()
    assert sim.atom_ledger.kg_by_account(SPENT_REDUCTANT_RESIDUE_ACCOUNT).get(
        "Na2O", 0.0
    ) > 0.0
    assert "Na2O" not in sim.atom_ledger.kg_by_account(
        "process.reagent_inventory"
    )
    assert sim.shuttle_Na_inventory_kg == pytest.approx(
        sim.atom_ledger.kg_by_account("process.reagent_inventory").get("Na", 0.0)
    )
    _assert_product_matches_account(sim, "process.metal_phase", "Cr")
    assert sim.atom_ledger.kg_by_account("process.metal_phase").get(
        "Ti", 0.0
    ) == pytest.approx(0.0)
    assert sim.product_ledger().get("Ti", 0.0) == pytest.approx(0.0)


def test_recovered_condensate_transfers_once_to_reagent_inventory():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"SiO2": 100.0},
            }
        }
    )
    sim.load_batch("oxide", mass_kg=1000.0)
    sim.train.stages[4].collected_kg["K"] = 2.0

    sim._init_shuttle_inventory(CampaignPhase.C3_K)

    assert sim.train.total_by_species().get("K", 0.0) == pytest.approx(0.0)
    assert sim.shuttle_K_inventory_kg == pytest.approx(0.0)
    assert sim.atom_ledger.kg_by_account("process.condensation_train").get(
        "K", 0.0
    ) == pytest.approx(0.0)
    assert sim.atom_ledger.kg_by_account("process.reagent_inventory").get(
        "K", 0.0
    ) == pytest.approx(0.0)

    sim.atom_ledger.load_external(
        "process.condensation_train",
        {"K": 2.0},
        source="test recovered K condensate",
    )
    sim.train.stages[4].collected_kg["K"] = 2.0
    assert sim._transfer_condensed_species("K") == pytest.approx(2.0)

    assert sim._transfer_condensed_species("K") == pytest.approx(0.0)
    assert sim.train.stages[4].collected_kg.get("K", 0.0) == pytest.approx(0.0)
    assert sim.atom_ledger.kg_by_account("process.reagent_inventory")[
        "K"
    ] == pytest.approx(2.0)


def test_mg_thermite_debits_process_reagent_inventory():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"Al2O3": 80.0, "SiO2": 20.0},
            }
        }
    )
    sim.load_batch("oxide", mass_kg=1000.0, additives_kg={"Mg": 12.0})
    sim._init_thermite_inventory()

    sim._step_thermite()

    sim.atom_ledger.assert_balanced()
    assert sim._thermite_Mg_consumed_this_hr > 0.0
    assert sim.atom_ledger.kg_by_account("reservoir.reagent.Mg").get(
        "Mg", 0.0
    ) == pytest.approx(0.0)
    assert sim.atom_ledger.kg_by_account("process.reagent_inventory")[
        "Mg"
    ] == pytest.approx(12.0 - sim._thermite_Mg_consumed_this_hr)
    assert sim.thermite_Mg_inventory_kg == pytest.approx(
        sim.atom_ledger.kg_by_account("process.reagent_inventory")["Mg"]
    )
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt")["MgO"] > 0.0
    _assert_product_matches_account(sim, "process.metal_phase", "Al")
    _assert_product_matches_account(sim, "process.metal_phase", "Si")
    assert sim.train.total_by_species().get("Al", 0.0) == pytest.approx(0.0)
    assert sim.train.total_by_species().get("Si", 0.0) == pytest.approx(0.0)


def test_mg_thermite_counters_are_net_after_committed_back_reduction():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"Al2O3": 80.0, "SiO2": 20.0},
            }
        }
    )
    sim.load_batch("oxide", mass_kg=1000.0, additives_kg={"Mg": 12.0})
    sim._init_thermite_inventory()

    sim._step_thermite()

    ledger_al_kg = sim.atom_ledger.kg_by_account("process.metal_phase")["Al"]
    assert sim._thermite_Al_produced_this_hr == pytest.approx(ledger_al_kg)


def test_c2a_staged_payload_exposes_terminal_rump_composition():
    sim = _run_c2a_staged()

    payload = _completion_payload(sim)

    assert {
        "terminal_rump_kg",
        "terminal_rump_by_species",
        "terminal_rump_by_class",
    } <= payload.keys()
    assert "terminal_slag_kg" in payload

    total_kg = payload["terminal_rump_kg"]
    by_species = payload["terminal_rump_by_species"]
    by_class = payload["terminal_rump_by_class"]

    assert total_kg > 0.0
    assert by_species
    assert set(by_class) == {
        "refractory_oxides",
        "silicate_residual",
        "unextracted_metals",
        "other",
    }

    species_total_kg = sum(by_species.values())
    class_total_kg = sum(by_class.values())
    species_error_pct = abs(species_total_kg - total_kg) / total_kg * 100.0
    class_error_pct = abs(class_total_kg - total_kg) / total_kg * 100.0

    assert species_error_pct <= 5e-12
    assert class_error_pct <= 5e-12
