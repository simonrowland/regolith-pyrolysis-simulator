from pathlib import Path

import pytest
import yaml

from simulator.core import PyrolysisSimulator
from simulator.mass_balance import MassBalance
from simulator.melt_backend.base import StubBackend
from simulator.runner import build_sio_yield_report
from simulator.state import (
    CampaignPhase,
    CondensationTrain,
    DecisionType,
    MeltState,
    ProcessInventory,
)


SIO_CLOSURE_MAX_REL_PCT = 5e-12
SIO_CLOSURE_MAX_ABS_MOL = 2e-12


def _load_data_yaml(name):
    return yaml.safe_load(
        (Path(__file__).parent.parent / "data" / name).read_text())


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


def test_product_summary_sums_duplicate_volatile_species():
    train = CondensationTrain.create_default()
    train.stages[3].collected_kg["H2O"] = 2.0
    train.volatiles_collected_kg["H2O"] = 3.0

    products = MassBalance().product_summary(train, oxygen_kg=1.0)

    assert products["H2O"] == pytest.approx(5.0)
    assert products["O2"] == pytest.approx(1.0)


def test_cumulative_transition_mass_closure_bounded():
    # DEFAULT_MASS_TOLERANCE_KG (20 g) bounds a single transition only.
    # A full C0->C6 batch commits hundreds of transitions; if each closed a
    # little short/long with a consistent sign, cumulative drift could grow
    # unbounded while every individual transition still passed. Guard that
    # gap directly: sum abs(debit - credit) over every committed transition
    # and bound the total far below even one per-transition tolerance.
    feedstocks = _load_data_yaml("feedstocks.yaml")
    setpoints = _load_data_yaml("setpoints.yaml")
    vapor_pressures = _load_data_yaml("vapor_pressures.yaml")
    setpoints = dict(setpoints)
    kernel_config = dict(setpoints.get("chemistry_kernel", {}) or {})
    kernel_config["allow_fallback_vapor"] = True
    setpoints["chemistry_kernel"] = kernel_config

    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(backend, setpoints, feedstocks, vapor_pressures)
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.start_campaign(CampaignPhase.C0)

    # Drive the full pyrolysis path C0 -> ... -> C6 to completion.
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
    transitions = sim.atom_ledger.transitions
    assert len(transitions) > 100  # a real multi-campaign batch

    registry = sim.atom_ledger.registry
    cumulative_imbalance_kg = sum(
        abs(t.debit_mass_kg(registry) - t.credit_mass_kg(registry))
        for t in transitions
    )

    # Builtin path closes each transition to ~1e-12 kg; ~1e-9 cumulative.
    # 1e-6 kg (1 mg) is a tight batch-level bound -- four orders below a
    # single per-transition tolerance -- yet leaves ample headroom.
    assert cumulative_imbalance_kg < 1e-6

    # The final batch mass balance must still close to ~zero. The
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


def test_sio_disproportionation_closes():
    for feedstock_id in ("lunar_mare_low_ti", "mars_basalt"):
        _, diagnostics = build_sio_yield_report(
            feedstock_id=feedstock_id,
            include_diagnostics=True,
        )
        _assert_sio_destination_closure(diagnostics)


def test_sio_destination_split_closes_with_wall_deposit():
    report, diagnostics = build_sio_yield_report(
        feedstock_id="lunar_mare_low_ti",
        include_diagnostics=True,
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
