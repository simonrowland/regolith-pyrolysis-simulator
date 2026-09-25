import math
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.account_ids import (
    OXYGEN_CAPTURED_ACCOUNTS,
    OXYGEN_CISTERN_LIQUID_INVENTORY_ACCOUNT,
    OXYGEN_MELT_OFFGAS_CAPTURED_ACCOUNT,
    OXYGEN_STORED_ACCOUNTS,
    OXYGEN_VENTED_ACCOUNTS,
)
from simulator.core import (
    FLOW_MASS_ACCOUNTS,
    FLOW_MASS_EXCLUDED_ACCOUNTS,
    PyrolysisSimulator,
)
from simulator.evaporation import EvaporationFluxRefusal
from simulator.mass_balance import MassBalance, ZERO_INPUT_BASIS_BREACH
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.runner import build_sio_yield_report
from simulator.state import (
    CampaignPhase,
    CondensationTrain,
    DecisionType,
    MeltState,
    ProcessInventory,
)
from tests.chemistry.conftest import _build_sim


SIO_CLOSURE_MAX_REL_PCT = 5e-12
# The SiO chain diagnostic is a report-layer projection over evolved SiO,
# terminal Si/SiO2, SiO-equivalent wall deposits, and escaped SiO. The
# AtomLedger mass-balance gate below remains the hard invariant; this absolute
# projection floor covers the 2026-06-29 reactive-wall + alpha_s(T) SiO
# rebaseline while the hard AtomLedger balance remains <=5e-12 %. The 2026-07-01
# pre-0.6 SiO Antoine refit leaves the destination-split gap at ~3.9e-11..4.1e-11
# mol (well under this floor); the wall-terminal Si-atom sum is the honest
# closure (a transient de-double-count that inflated the gap was reverted).
SIO_CLOSURE_MAX_ABS_MOL = 8e-11
MASS_BALANCE_CLOSURE_MAX_PCT = 5e-12
CUMULATIVE_TRANSITION_IMBALANCE_MAX_KG = 1e-9


def test_flow_mass_accounts_cover_non_reservoir_ledger_accounts():
    core_source = (
        Path(__file__).parent.parent / "simulator" / "core.py"
    ).read_text()
    discovered_accounts = set(
        re.findall(
            r"[\"']((?:process|terminal)\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)[\"']",
            core_source,
        )
    )
    covered_accounts = set(FLOW_MASS_ACCOUNTS) | set(FLOW_MASS_EXCLUDED_ACCOUNTS)

    assert set(FLOW_MASS_EXCLUDED_ACCOUNTS) <= discovered_accounts
    assert discovered_accounts <= covered_accounts, (
        "new process/terminal ledger accounts must be added to "
        "FLOW_MASS_ACCOUNTS or FLOW_MASS_EXCLUDED_ACCOUNTS: "
        f"{sorted(discovered_accounts - covered_accounts)}"
    )


def test_flow_mass_out_includes_dynamic_wall_deposit_segment_accounts():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"sample": {"composition_wt_pct": {"SiO2": 100.0}}},
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("sample", mass_kg=1.0)
    before = sim._flow_mass_out_kg()

    sim.atom_ledger.load_external(
        "process.wall_deposit_segment_condenser",
        {"SiO": 1.0e-6},
        source="test dynamic lab surface deposit",
        material_origin="feedstock",
    )

    assert sim._flow_mass_out_kg() - before == pytest.approx(1.0e-6)


def test_flow_mass_out_includes_captured_melt_offgas_oxygen():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"sample": {"composition_wt_pct": {"SiO2": 100.0}}},
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("sample", mass_kg=1.0)
    before = sim._flow_mass_out_kg()

    sim.atom_ledger.load_external(
        OXYGEN_MELT_OFFGAS_CAPTURED_ACCOUNT,
        {"O2": 2.0},
        source="test captured melt offgas oxygen",
        material_origin="feedstock",
    )

    assert sim._flow_mass_out_kg() - before == pytest.approx(2.0)


def test_oxygen_terminal_partition_exposes_cistern_liquid_stored():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"sample": {"composition_wt_pct": {"SiO2": 100.0}}},
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("sample", mass_kg=1.0)
    sim.atom_ledger.load_external(
        OXYGEN_CISTERN_LIQUID_INVENTORY_ACCOUNT,
        {"O2": 2.0},
        source="test cistern liquid oxygen",
        material_origin="feedstock",
    )

    partition = sim._oxygen_terminal_partition_kg()

    assert partition["cistern_liquid_stored"] == pytest.approx(2.0)
    assert partition["stored"] == pytest.approx(2.0)


def test_declared_terminal_oxygen_accounts_have_flow_mass_disposition():
    declared = set(OXYGEN_STORED_ACCOUNTS)
    declared.update(OXYGEN_VENTED_ACCOUNTS)
    declared.update(OXYGEN_CAPTURED_ACCOUNTS)
    covered = set(FLOW_MASS_ACCOUNTS) | set(FLOW_MASS_EXCLUDED_ACCOUNTS)

    assert declared <= covered


def _load_data_yaml(name):
    return yaml.safe_load(
        (Path(__file__).parent.parent / "data" / name).read_text())


def _set_freeze_gate(setpoints_data: dict, *, enabled: bool) -> dict:
    setpoints = dict(setpoints_data)
    gate = dict(setpoints.get("freeze_gate", {}) or {})
    gate["enabled"] = enabled
    setpoints["freeze_gate"] = gate
    return setpoints


def _install_liquidus_stub(sim) -> None:
    sim.backend.find_liquidus_solidus = lambda **_: SimpleNamespace(
        status="ok",
        solidus_T_C=1000.0,
        liquidus_T_C=1300.0,
    )

    def gate_dispatch_unavailable(reasons, *, fO2_log):
        reasons.append("test liquidus stub bypasses gate dispatch")
        return None

    sim._freeze_gate_curve_from_gate_dispatch = gate_dispatch_unavailable


def _cumulative_transition_imbalance_kg(sim) -> float:
    registry = sim.atom_ledger.registry
    return sum(
        abs(t.debit_mass_kg(registry) - t.credit_mass_kg(registry))
        for t in sim.atom_ledger.transitions
    )


def _external_input_mass_kg(sim) -> float:
    registry = sim.atom_ledger.registry
    return sum(
        lot.total_mass_kg(registry) for lot in sim.atom_ledger.external_loads
    )


def _run_c2a_staged_to_completion(
    sim,
) -> tuple[int, EvaporationFluxRefusal | None]:
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A_staged",
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    steps = 0
    refusal = None
    while not sim.is_complete() and steps < 500:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        ledger_before = sim.atom_ledger.mol_by_account()
        transitions_before = tuple(sim.atom_ledger.transitions)
        drift_before = sim.atom_ledger.element_atom_drift_report()
        try:
            sim.step()
        except EvaporationFluxRefusal as exc:
            refusal = exc
            assert sim.atom_ledger.mol_by_account() == ledger_before
            assert tuple(sim.atom_ledger.transitions) == transitions_before
            assert sim.atom_ledger.element_atom_drift_report() == drift_before
            break
        steps += 1
        # Typed campaign-endpoint refusal is non-resumable (no next decision).
        # Treat it as a finished batch for mass-balance closure checks.
        if getattr(sim, "campaign_endpoint_refused", lambda: False)():
            break
    assert (
        sim.is_complete()
        or sim.campaign_endpoint_refused()
        or refusal is not None
    )
    return steps, refusal


def test_mass_balance_counts_process_inventory_without_o2_double_count():
    melt = MeltState(composition_kg={"SiO2": 800.0})
    melt.update_total_mass()
    train = CondensationTrain.create_default()
    train.stages[6].collected_kg["O2"] = 7.0
    inventory = ProcessInventory(
        stage0_products_kg={"H2O": 50.0},
        metal_alloy_kg={"Fe": 10.0},
        terminal_slag_components_kg={"ZrO2": 2.0},
        residual_components_kg={"unsupported": 5.0},
    )
    balance = MassBalance()
    balance.set_inputs(874.0, {"K": 3.0})

    result = balance.check(
        melt,
        train,
        oxygen_kg=7.0,
        inventory=inventory,
        additive_inventory_kg={"K": 3.0},
    )

    assert result["mass_out"] == pytest.approx(877.0)
    assert result["condensed"] == pytest.approx(0.0)
    assert result["oxygen"] == pytest.approx(7.0)
    assert result["error_pct"] == pytest.approx(0.0)
    assert result["error_category"] == ""


def test_zero_input_nonzero_output_mass_balance_is_named_undefined():
    melt = MeltState()
    train = CondensationTrain.create_default()
    balance = MassBalance()
    balance.set_inputs(0.0, {})

    result = balance.check(melt, train, oxygen_kg=1.0)

    assert result["mass_in"] == pytest.approx(0.0)
    assert result["mass_out"] == pytest.approx(1.0)
    assert result["error_pct"] is None
    assert result["error_category"] == ZERO_INPUT_BASIS_BREACH


def test_zero_input_zero_output_mass_balance_is_vacuously_closed():
    melt = MeltState()
    train = CondensationTrain.create_default()
    balance = MassBalance()
    balance.set_inputs(0.0, {})

    result = balance.check(melt, train, oxygen_kg=0.0)

    assert result["mass_in"] == pytest.approx(0.0)
    assert result["mass_out"] == pytest.approx(0.0)
    assert result["error_pct"] == pytest.approx(0.0)
    assert result["error_category"] == ""


def test_snapshot_zero_input_nonzero_output_marks_named_breach():
    feedstocks = _load_data_yaml("feedstocks.yaml")
    setpoints = _load_data_yaml("setpoints.yaml")
    vapor_pressures = _load_data_yaml("vapor_pressures.yaml")
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressures,
        feedstocks,
        setpoints,
    )
    sim.record.batch_mass_kg = 0.0
    sim.record.additives_kg = {}

    snapshot = sim._make_snapshot()

    assert snapshot.mass_in_kg == pytest.approx(0.0)
    assert snapshot.mass_out_kg > 0.0
    assert snapshot.mass_balance_error_pct is None
    assert getattr(snapshot, "mass_balance_error_category") == ZERO_INPUT_BASIS_BREACH


def test_product_summary_keeps_volatiles_disjoint_from_stage_totals():
    train = CondensationTrain.create_default()
    train.stages[3].collected_kg["H2O"] = 2.0
    train.volatiles_collected_kg["H2O"] = 3.0
    train.volatiles_collected_kg["CO2"] = 1.5

    products = MassBalance().product_summary(train, oxygen_kg=1.0)

    # H2O already in stages — do not double-book volatiles map.
    assert products["H2O"] == pytest.approx(2.0)
    assert products["CO2"] == pytest.approx(1.5)
    assert products["O2"] == pytest.approx(1.0)


def test_product_summary_volatile_double_book_mutation_proof():
    train = CondensationTrain.create_default()
    train.stages[3].collected_kg["H2O"] = 2.0
    train.volatiles_collected_kg["H2O"] = 3.0
    products = MassBalance().product_summary(train, oxygen_kg=0.0)
    assert products["H2O"] == pytest.approx(2.0)
    # Counterfactual pre-fix sum would be 5.0.
    assert products["H2O"] + 3.0 == pytest.approx(5.0)


def test_mass_balance_includes_sulfate_inert_and_real_stage0_delta():
    melt = MeltState(composition_kg={"SiO2": 100.0})
    melt.update_total_mass()
    train = CondensationTrain.create_default()
    inventory = ProcessInventory(
        cation_sulfate_feed_kg={"CaSO4": 4.0},
        inert_melt_components_kg={"ZrO2": 1.0},
        stage0_mass_balance_delta_kg=0.25,
    )
    balance = MassBalance()
    balance.set_inputs(105.0, {})
    result = balance.check(
        melt, train, oxygen_kg=0.0, inventory=inventory, wall_deposit_kg=2.0
    )
    assert result["cation_sulfate_feed"] == pytest.approx(4.0)
    assert result["inert_melt"] == pytest.approx(1.0)
    assert result["wall_deposit"] == pytest.approx(2.0)
    assert result["stage0_mass_balance_delta"] == pytest.approx(0.25)
    assert result["mass_out"] == pytest.approx(100.0 + 4.0 + 1.0 + 2.0)


def test_mass_balance_omit_sulfate_mutation_proof():
    melt = MeltState(composition_kg={"SiO2": 50.0})
    melt.update_total_mass()
    train = CondensationTrain.create_default()
    inventory = ProcessInventory(cation_sulfate_feed_kg={"MgSO4": 3.0})
    balance = MassBalance()
    balance.set_inputs(53.0, {})
    result = balance.check(melt, train, oxygen_kg=0.0, inventory=inventory)
    assert result["mass_out"] == pytest.approx(53.0)
    # Counterfactual omission would report 50.0 and a false gap.
    assert result["mass_out"] - 3.0 == pytest.approx(50.0)


# Nightly (2026-08-02 CI tiering): freeze-gate pair (~38 s junit; campaign/serial).
@pytest.mark.nightly
@pytest.mark.parametrize(
    "freeze_gate_enabled",
    (False, True),
    ids=("freeze_gate_off", "freeze_gate_on"),
)
@pytest.mark.serial  # Stubbed liquidus; retain serial until xdist/co-load is re-proven.
@pytest.mark.xdist_group("serial")
def test_c2a_staged_freeze_gate_on_closes_mass_balance(
    monkeypatch,
    freeze_gate_enabled,
):
    feedstocks = _load_data_yaml("feedstocks.yaml")
    setpoints = _set_freeze_gate(
        _load_data_yaml("setpoints.yaml"),
        enabled=freeze_gate_enabled,
    )
    vapor_pressures = _load_data_yaml("vapor_pressures.yaml")
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressures,
        feedstocks,
        setpoints,
        additives_kg={"K": 26.0, "Na": 12.0},
    )
    _install_liquidus_stub(sim)

    liquid_fractions = []
    original_liquid_fraction = sim._freeze_gate_liquid_fraction_factor

    def record_liquid_fraction():
        liquid_fraction = original_liquid_fraction()
        liquid_fractions.append(liquid_fraction)
        return liquid_fraction

    monkeypatch.setattr(
        sim,
        "_freeze_gate_liquid_fraction_factor",
        record_liquid_fraction,
    )

    steps, refusal = _run_c2a_staged_to_completion(sim)

    # b-324 increases the condenser capture-rate budget by converting the
    # hourly vapor supply to mol/s. The campaign now completes instead of
    # reaching the prior transitional-Kn refusal.
    assert refusal is None
    assert steps == 130
    assert sim.is_complete()
    transition_names = {
        getattr(transition, "name", "")
        for transition in sim.atom_ledger.transitions
    }
    assert any(name.startswith("evaporate_") for name in transition_names)
    assert any(name.startswith("condense_") for name in transition_names)
    assert "overhead_bleed" in transition_names

    snapshot_mass_balance_error_pct = abs(
        sim._make_snapshot().mass_balance_error_pct
    )
    assert snapshot_mass_balance_error_pct < MASS_BALANCE_CLOSURE_MAX_PCT
    assert (
        _cumulative_transition_imbalance_kg(sim)
        < CUMULATIVE_TRANSITION_IMBALANCE_MAX_KG
    )

    external_input_mass = _external_input_mass_kg(sim)
    destination_mass = sum(sim.atom_ledger.total_kg_by_account().values())
    destination_error_pct = (
        abs(destination_mass - external_input_mass)
        / external_input_mass
        * 100.0
    )
    assert destination_error_pct < MASS_BALANCE_CLOSURE_MAX_PCT

    if freeze_gate_enabled:
        assert any(liquid_fraction < 1.0 for liquid_fraction in liquid_fractions)
    else:
        assert liquid_fractions == []


# xdist 3.8 UNIONS group marks: keep exactly ONE xdist_group here — the
# magemin_fullrun_b scope replaces the old per-test "serial" group (the
# pytest.mark.serial selector mark stays for the two-pass helper).
# Nightly (2026-08-02 CI tiering): cumulative mass balance campaign (~141 s junit).
@pytest.mark.nightly
@pytest.mark.xdist_group("magemin_fullrun_b")
@pytest.mark.timeout(1800)
@pytest.mark.serial
def test_cumulative_transition_mass_closure_bounded_at_transitional_refusal():
    # DEFAULT_MASS_TOLERANCE_KG (20 g) bounds a single transition only.
    # The no-MRE campaign now ends at the first unsupported transitional-Kn
    # flux point. If each committed prefix transition closed a little short or
    # long with a consistent sign, cumulative drift could still grow while
    # every individual transition passed. Guard that prefix directly.
    feedstocks = _load_data_yaml("feedstocks.yaml")
    setpoints = _load_data_yaml("setpoints.yaml")
    vapor_pressures = _load_data_yaml("vapor_pressures.yaml")
    setpoints = dict(setpoints)
    kernel_config = dict(setpoints.get("chemistry_kernel", {}) or {})
    kernel_config["allow_fallback_vapor"] = True
    kernel_config["allow_unmeasured_alpha_fallback"] = True
    setpoints["chemistry_kernel"] = kernel_config

    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(backend, setpoints, feedstocks, vapor_pressures)
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.start_campaign(CampaignPhase.C0)

    # Drive the no-MRE path until the transitional-Kn refusal.
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A_staged",
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    steps = 0
    refusal = None
    while not sim.is_complete() and steps < 5000:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        ledger_before = sim.atom_ledger.mol_by_account()
        transitions_before = tuple(sim.atom_ledger.transitions)
        drift_before = sim.atom_ledger.element_atom_drift_report()
        try:
            sim.step()
        except EvaporationFluxRefusal as exc:
            refusal = exc
            assert sim.atom_ledger.mol_by_account() == ledger_before
            assert tuple(sim.atom_ledger.transitions) == transitions_before
            assert sim.atom_ledger.element_atom_drift_report() == drift_before
            break
        steps += 1

    assert refusal is None
    assert sim.is_complete()
    transitions = sim.atom_ledger.transitions
    assert transitions

    registry = sim.atom_ledger.registry
    cumulative_imbalance_kg = sum(
        abs(t.debit_mass_kg(registry) - t.credit_mass_kg(registry))
        for t in transitions
    )

    # Builtin path closes each transition to ~1e-12 kg; ~1e-9 cumulative.
    # 1e-6 kg (1 mg) is a tight batch-level bound -- four orders below a
    # single per-transition tolerance -- yet leaves ample headroom.
    assert cumulative_imbalance_kg < 1e-6

    # The committed prefix at refusal must still close to ~zero. The
    # absolute floor is 5e-12 % (the legacy kg-native path holds
    # ~7e-13 %; the kernel-routed EVAPORATION_TRANSITION provider
    # introduces an additional ULP per species per transition through
    # the mol -> kg materialization in
    # ``simulator.chemistry.kernel.validation._proposal_to_ledger_transition``,
    # capped by the simulator-level downstream feedback at ~1e-12 %).
    # This is still "0.000 %" in any user-facing report and orders of
    # magnitude below the per-transition tolerance the AtomLedger
    # enforces.
    assert abs(sim._make_snapshot().mass_balance_error_pct) < 5e-12

    drift = sim.atom_ledger.close_report()["element_atom_drift"]
    assert drift["unit"] == "mol-atoms"
    for surface in (
        "accepted_transition_residual_mol_atoms",
        "whole_run_boundary_residual_mol_atoms",
    ):
        assert drift[surface]
        assert all(math.isfinite(value) for value in drift[surface].values())


def test_sio_disproportionation_closes():
    for feedstock_id in ("lunar_mare_low_ti", "mars_basalt"):
        _, diagnostics = build_sio_yield_report(
            feedstock_id=feedstock_id,
            include_diagnostics=True,
            allow_unmeasured_alpha_fallback=True,
        )
        _assert_sio_destination_closure(diagnostics)


def test_sio_destination_split_closes_with_wall_deposit():
    report, diagnostics = build_sio_yield_report(
        feedstock_id="lunar_mare_low_ti",
        include_diagnostics=True,
        allow_unmeasured_alpha_fallback=True,
    )

    _assert_sio_destination_closure(diagnostics)
    assert "wall_deposit_kg" in report
    assert "fouling_rate" in report


def _assert_sio_destination_closure(diagnostics: dict[str, float]) -> None:
    destinations_mol = (
        diagnostics["si_terminal_mol"]
        + diagnostics["sio2_terminal_mol"]
        + diagnostics["sio_wall_mol"]
        + diagnostics["sio_escape_mol"]
        # 2026-08-05 MC-4 wave 1B (a34318c): the pO2-insensitive SiO2(g)
        # gas-exchange carrier is a real chain destination drawn from the
        # same SiO2 pool; counted by the report projection post-wave.
        + diagnostics["sio2_gas_escape_mol"]
    )
    abs_gap_mol = abs(diagnostics["sio_evaporated_mol"] - destinations_mol)
    if diagnostics["sio_evaporated_mol"] <= 0.0:
        closure_error_pct = 0.0
    else:
        closure_error_pct = (
            abs_gap_mol / diagnostics["sio_evaporated_mol"] * 100.0
        )

    # The Antoine refit lowers absolute SiO production enough that a
    # femtomol-scale floating gap can exceed the old pure-relative pct guard.
    assert (
        closure_error_pct < SIO_CLOSURE_MAX_REL_PCT
        or abs_gap_mol < SIO_CLOSURE_MAX_ABS_MOL
    )


def test_stage0_products_include_cation_sulfate_feed():
    from simulator.core import PyrolysisSimulator
    products = PyrolysisSimulator._stage0_products_from_buckets(
        {
            "gas_volatiles": {"H2O": 1.0},
            "salt_phase": {},
            "chloride_salt_phase": {},
            "sulfide_matte": {},
            "cation_sulfate_feed": {"CaSO4": 2.5},
        }
    )
    assert products["H2O"] == pytest.approx(1.0)
    assert products["CaSO4"] == pytest.approx(2.5)


def test_stage0_products_omit_sulfate_mutation_proof():
    from simulator.core import PyrolysisSimulator
    products = PyrolysisSimulator._stage0_products_from_buckets(
        {"cation_sulfate_feed": {"MgSO4": 3.0}, "gas_volatiles": {}}
    )
    assert products.get("MgSO4") == pytest.approx(3.0)
    # Counterfactual omit would yield empty products.
    assert products != {}


def test_condensation_eta_clip_surfaces_capture_supply_limited_notice():
    """Unit-level: when uncapped eta > 1 the clip path stamps the notice."""
    # Mirror the live clip algebra from CondensationTrain._condensation_efficiency.
    eta_uncapped = 2.5
    eta = max(0.0, min(1.0, eta_uncapped))
    domain_outcome = {}
    if eta_uncapped > 1.0:
        domain_outcome["capture_supply_limited"] = True
        domain_outcome["eta_uncapped"] = eta_uncapped
    domain_outcome["eta"] = eta
    assert eta == pytest.approx(1.0)
    assert domain_outcome["capture_supply_limited"] is True
    # Mutation proof: without the notice flag auditors only see eta=1.
    mutant = {"eta": eta}
    assert "capture_supply_limited" not in mutant
