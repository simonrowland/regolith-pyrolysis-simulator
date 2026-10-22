"""C5 preview/commit residual parity at one resolved removal boundary."""
from types import SimpleNamespace

import pytest

from simulator.gas_balance import FiniteCapacity, NoColdTrain
from simulator.state import Atmosphere
from tests.test_overhead_accounting import _gas_train_sim


def fixture(monkeypatch, *, conductance=1e-5, cold=False, external=0.21, relief=1e-8):
    sim = _gas_train_sim()
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    sim.melt.pO2_mbar = 0.0
    sim.melt.sweep_mol_per_s = None
    sim._overhead_headspace_config.update(enabled=True, conductance_kg_s=conductance)
    sim._headspace_volume_m3 = lambda: 0.137
    sim._headspace_temperature_K = lambda: 1873.15
    sim.atom_ledger.load_external_mol("process.overhead_gas", {"O2": 0.75, "Kr": 1.23456789},
        source="C5 retained inventory", material_origin="feedstock")
    sim._o2_bubbler_external_o2_in_overhead_mol = external
    if cold:
        capacity = FiniteCapacity(0.00123)
        train = SimpleNamespace(accumulator_enabled=False, relief={
            "k_relief_kg_hr_Pa": relief, "p_open_Pa": 40000.0, "vessel_rating_Pa": 1e6})
        monkeypatch.setattr(sim, "_cold_train_capacity_policy", lambda: (capacity, train))
    else:
        monkeypatch.setattr(sim, "_cold_train_capacity_policy", lambda: (NoColdTrain(), None))
    return sim


@pytest.mark.parametrize("dt_hr", [1.0, 0.137])
@pytest.mark.parametrize("conductance,cold,external", [
    (1e-5, False, 0.0), (1e-5, False, 0.21),
    (1e-5, True, 0.21), (0.0, True, 0.21), (0.0, False, 0.0),
])
def test_legacy_preview_equals_committed_residual(monkeypatch, dt_hr, conductance, cold, external):
    sim = fixture(monkeypatch, conductance=conductance, cold=cold, external=external)
    before = sim.atom_ledger.mol_by_account()
    duration = {} if dt_hr == 1.0 else {"dt_hr": dt_hr}
    preview = sim._inert_sweep_transport_pO2_bar(0.75, **duration)
    assert sim.atom_ledger.mol_by_account() == before
    receipt = sim._dispatch_overhead_bleed(**duration)
    assert receipt.status == "ok"
    residual = sim.atom_ledger.mol_by_account("process.overhead_gas").get("O2", 0.0)
    expected = max(sim._headspace_ledger_pO2_bar_from_o2_mol(residual), sim._oxygen_numerical_floor_bar())
    assert preview.hex() == expected.hex()


@pytest.mark.parametrize("conductance,relief", [(1e-5, 1e-30), (1e-5, 1e-8), (0.0, 1e-8)])
@pytest.mark.parametrize("dt_s", [3600.0, 493.2])
@pytest.mark.parametrize("source_exchange", [False, True])
def test_physical_sweep_preview_equals_commit_with_cold_train(monkeypatch, conductance, relief, dt_s, source_exchange):
    sim = fixture(monkeypatch, conductance=conductance, cold=True, relief=relief)
    sim.melt.body = "mars"
    sim.melt.sweep_mol_per_s = 0.0037
    sim.melt.p_total_mbar = 6.1
    sim.melt.background_gas_species = "Kr"
    sim.atom_ledger.load_external_mol("process.overhead_gas", {"CO2": 0.27},
        source="C5 Mars gas", material_origin="feedstock")
    initial = sim._overhead_holdup_mol()
    sim._sweep_transport_step = {
        "initial_species_mol": initial,
        "initial_external_o2_mol": 0.21,
        "transition_start": len(sim.atom_ledger.transitions), "dt_s": dt_s,
        "capacity_result": SimpleNamespace(partial_pressures_Pa={"O2": 50000.0, "Kr": 1000.0}, notices=()),
    }
    if source_exchange:
        from engines.builtin.oxygen_bubbler import TRANSITION_NAME
        ledger = sim.atom_ledger
        ledger.mark_amalgamated_pool("process.overhead_gas", ("O",))
        ledger.transfer("C5 consume oxygen",
            (ledger.debit_mol("process.overhead_gas", {"O2": 0.1}),),
            (ledger.credit_mol("terminal.offgas", {"O2": 0.1}),))
        ledger.load_external_mol("reservoir.reagent.O2", {"O2": 0.1},
            source="C5 external oxygen", material_origin="reagent")
        ledger.transfer(TRANSITION_NAME,
            (ledger.debit_mol("reservoir.reagent.O2", {"O2": 0.1}),),
            (ledger.credit_mol("process.overhead_gas", {"O2": 0.1}),))
    from simulator.overhead import sweep_source_split
    _, _, external_old, external_generated = sweep_source_split(
        initial, sim.atom_ledger.transitions[sim._sweep_transport_step["transition_start"]:],
        sim.atom_ledger.registry, initial_external_o2_mol=0.21)
    sim._o2_bubbler_external_o2_in_overhead_mol = external_old + external_generated
    resolved = []
    owner = sim._continuous_sweep_plan
    def capture_plan(*args, **kwargs):
        plan = owner(*args, **kwargs)
        resolved.append(plan)
        return plan
    monkeypatch.setattr(sim, "_continuous_sweep_plan", capture_plan)
    before = sim.atom_ledger.mol_by_account()
    preview = sim._physical_sweep_boundary()
    assert sim.atom_ledger.mol_by_account() == before
    # Commit the exact physical plan previewed, without a second ODE solve.
    plan, = resolved
    sim.atom_ledger.load_external_mol("reservoir.reagent.sweep_gas", plan["inlet_species_mol"],
        source="C5 physical inlet", material_origin="reagent")
    receipt = sim._dispatch_overhead_bleed(
        **({} if dt_s == 3600.0 else {"dt_hr": dt_s / 3600.0}),
        capacity_result=sim._sweep_transport_step["capacity_result"],
        sweep_outflow_species_mol=plan["outflow_species_mol"],
        sweep_external_o2_outflow_mol=plan["external_o2_outflow_mol"],
        sweep_pressure_bar=plan["pressure_bar"], sweep_capacity_integrated=True)
    assert receipt.status == "ok"
    expected = sim.overhead_model.sweep_boundary(
        sim._overhead_holdup_mol(), volume_m3=sim._headspace_volume_m3(),
        temperature_K=sim._headspace_temperature_K())
    assert preview["partials_bar"] == expected["partials_bar"]
    assert receipt.diagnostic["external_o2_bled_mol"] == plan["external_o2_outflow_mol"]


def test_declared_physical_flow_keeps_cstr_path(monkeypatch):
    sim = fixture(monkeypatch)
    sim.melt.sweep_mol_per_s = 0.0
    monkeypatch.setattr(sim, "_physical_sweep_boundary", lambda: {"partials_bar": {"O2": 0.123}})
    def forbidden(*_args, **_kwargs):
        raise AssertionError("declared physical sweep fell back to virtual removal")
    monkeypatch.setattr(sim, "_inert_sweep_transport_pO2_bar", forbidden)
    assert sim._headspace_transport_pO2_bar_from_ledger(0.75) == 0.123


def test_preview_matches_net_transfer_roundoff_projection(monkeypatch):
    sim = fixture(monkeypatch, external=0.0)
    sim.atom_ledger.transfer("C5 clear fixture",
        (sim.atom_ledger.debit_mol("process.overhead_gas", sim._overhead_holdup_mol()),),
        (sim.atom_ledger.credit_mol("terminal.offgas", sim._overhead_holdup_mol()),))
    sim.atom_ledger.load_external_mol("process.overhead_gas", {"Kr": 0.1},
        source="C5 retained carrier", material_origin="reagent")
    inlet = {"Kr": 0.2}
    outlet = {"Kr": 0.1 + 0.2}
    controls = sim._overhead_bleed_controls()
    _, preview = sim._preview_overhead_removal(outlet, controls, inlet_mol=inlet)
    sim.atom_ledger.load_external_mol("reservoir.reagent.sweep_gas", inlet,
        source="C5 carrier inlet", material_origin="reagent")
    receipt = sim._dispatch_overhead_bleed(
        sweep_outflow_species_mol=outlet, sweep_external_o2_outflow_mol=0.0,
        sweep_pressure_bar=0.0061, sweep_capacity_integrated=True)
    assert receipt.status == "ok"
    assert preview == sim._overhead_holdup_mol() == {}
    # The ledger retains signed representational dust. A later credit must
    # start from that exact account value, matching commit evaluation order.
    next_outlet = {"Kr": 0.1}
    _, preview = sim._preview_overhead_removal(next_outlet, controls, inlet_mol=inlet)
    sim.atom_ledger.load_external_mol("reservoir.reagent.sweep_gas", inlet,
        source="C5 second carrier inlet", material_origin="reagent")
    receipt = sim._dispatch_overhead_bleed(
        sweep_outflow_species_mol=next_outlet, sweep_external_o2_outflow_mol=0.0,
        sweep_pressure_bar=0.0061, sweep_capacity_integrated=True)
    assert receipt.status == "ok"
    assert preview == sim._overhead_holdup_mol()


def test_zero_inlet_projects_existing_reagent_roundoff(monkeypatch):
    sim = fixture(monkeypatch, external=0.0)
    ledger = sim.atom_ledger
    ledger.load_external_mol("reservoir.reagent.sweep_gas", {"Kr": 0.3},
        source="C5 carrier roundoff", material_origin="reagent")
    ledger.transfer("C5 exhaust carrier",
        (ledger.debit_mol("reservoir.reagent.sweep_gas", {"Kr": 0.1 + 0.2}),),
        (ledger.credit_mol("terminal.offgas", {"Kr": 0.1 + 0.2}),))
    assert ledger.mol_by_account("reservoir.reagent.sweep_gas")["Kr"] < 0.0
    before = ledger.mol_by_account()
    _, preview = sim._preview_overhead_removal({}, sim._overhead_bleed_controls(),
        inlet_mol={"Kr": 0.0})
    assert ledger.mol_by_account() == before
    assert preview == sim._overhead_holdup_mol()
