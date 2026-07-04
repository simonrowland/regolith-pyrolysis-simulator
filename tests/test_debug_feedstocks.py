import pytest
import yaml

import app as app_module
from simulator.campaigns import CampaignManager
from simulator.core import PyrolysisSimulator
from simulator.feedstock_guard import is_blocked_feedstock
from simulator.melt_backend.base import StubBackend
from simulator.state import BatchRecord, CampaignPhase, DecisionType, MeltState
from web.feedstock_data import (
    DATA_DIR,
    get_visible_feedstock,
    load_feedstock_groups,
    load_visible_feedstocks,
)


def test_debug_feedstocks_are_hidden_by_default(monkeypatch):
    monkeypatch.delenv("REGOLITH_DEBUG_FEEDSTOCKS", raising=False)
    monkeypatch.delenv("REGOLITH_FLASK_DEBUG", raising=False)
    client = app_module.create_app().test_client()

    page = client.get("/")
    feedstocks = client.get("/api/feedstocks").get_json()

    assert page.status_code == 200
    assert b"DEBUG - Pure FeO" not in page.data
    assert b"debug-inventory-json" not in page.data
    assert "debug_pure_feo" not in feedstocks
    assert client.get("/api/feedstock/debug_pure_feo").status_code == 404


def test_blocked_feedstocks_are_hidden_from_selection_and_api(monkeypatch):
    monkeypatch.delenv("REGOLITH_DEBUG_FEEDSTOCKS", raising=False)
    data_path = DATA_DIR / "feedstocks.yaml"
    all_feedstocks = yaml.safe_load(data_path.read_text())
    blocked_keys = {
        key for key, entry in all_feedstocks.items()
        if is_blocked_feedstock(entry)
    }
    assert blocked_keys

    base, debug = load_feedstock_groups()
    visible = load_visible_feedstocks(include_custom=True)
    client = app_module.create_app().test_client()
    api_feedstocks = client.get("/api/feedstocks").get_json()

    assert not blocked_keys & set(base)
    assert not blocked_keys & set(debug)
    assert not blocked_keys & set(visible)
    assert not blocked_keys & set(api_feedstocks)
    for key in blocked_keys:
        assert get_visible_feedstock(key, include_custom=True) is None
        assert client.get(f"/api/feedstock/{key}").status_code == 404


def test_debug_feedstocks_and_inventory_panel_are_visible_when_enabled(
    monkeypatch,
):
    monkeypatch.setenv("REGOLITH_DEBUG_FEEDSTOCKS", "1")
    client = app_module.create_app().test_client()

    page = client.get("/")
    feedstocks = client.get("/api/feedstocks").get_json()
    feedstock = client.get("/api/feedstock/debug_pure_feo").get_json()
    card = client.get("/partials/feedstock-card/debug_pure_feo")
    additives = client.get(
        "/api/additive-calc/debug_pure_feo?mass_kg=1000").get_json()

    assert page.status_code == 200
    assert b"Debug test feedstocks" in page.data
    assert b"DEBUG - Pure FeO" in page.data
    assert b"debug-inventory-json" in page.data
    assert feedstocks["debug_pure_feo"]["composition_wt_pct"] == {
        "FeO": 100.0,
    }
    assert feedstock["label"] == "DEBUG - Pure FeO 1400C Melt"
    assert b"Single-oxide FeO batch near its melt point" in card.data
    assert additives["K"] > 0.0


@pytest.mark.parametrize(
    "key",
    [
        "debug_pure_feo",
        "debug_pure_na2o",
        "debug_pure_k2o",
        "debug_low_melt_oxide_mix",
    ],
)
def test_debug_feedstocks_are_loadable_sanity_batches(monkeypatch, key):
    monkeypatch.setenv("REGOLITH_DEBUG_FEEDSTOCKS", "1")
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        load_visible_feedstocks(),
        {"metals": {}, "oxide_vapors": {}},
    )

    sim.load_batch(key, mass_kg=1000.0)
    snapshot = sim._make_snapshot()

    assert snapshot.mass_balance_error_pct == pytest.approx(0.0)
    assert snapshot.melt_mass_kg == pytest.approx(1000.0)


def test_debug_feedstocks_stay_low_melt_metal_oxide_probes(monkeypatch):
    monkeypatch.setenv("REGOLITH_DEBUG_FEEDSTOCKS", "1")

    feedstocks = load_visible_feedstocks()
    debug_feedstocks = {
        key: value
        for key, value in feedstocks.items()
        if key.startswith("debug_")
    }

    assert "debug_pure_sio2" not in debug_feedstocks
    assert "debug_pure_fe2o3" not in debug_feedstocks
    assert set(debug_feedstocks) == {
        "debug_pure_feo",
        "debug_pure_na2o",
        "debug_pure_k2o",
        "debug_low_melt_oxide_mix",
    }
    for feedstock in debug_feedstocks.values():
        assert set(feedstock["composition_wt_pct"]) <= {"FeO", "Na2O", "K2O"}


def test_debug_batches_auto_apply_branching_decisions():
    manager = CampaignManager({"campaigns": {}})
    record = BatchRecord(feedstock_key="debug_pure_feo")

    assert manager.get_next_campaign(CampaignPhase.C0B, record) == CampaignPhase.C2A_STAGED
    assert record.path == "A_staged"
    assert (DecisionType.PATH_AB, "A_staged") in record.decisions

    assert manager.get_next_campaign(CampaignPhase.C3_NA, record) == CampaignPhase.C4
    assert record.branch == "two"
    assert (DecisionType.BRANCH_ONE_TWO, "two") in record.decisions

    assert manager.get_next_campaign(CampaignPhase.C5, record) == CampaignPhase.C6
    assert (DecisionType.C6_PROCEED, "yes") in record.decisions


def _setpoints() -> dict:
    return yaml.safe_load((DATA_DIR / "setpoints.yaml").read_text()) or {}


def _simulator_shell(melt: MeltState, setpoints: dict) -> PyrolysisSimulator:
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = melt
    sim.setpoints = setpoints
    return sim


def test_c2a_staged_c3_na_cool_cleanup_does_not_poison_reused_manager():
    manager = CampaignManager({"campaigns": {}})
    record = BatchRecord(path="A_staged")

    assert manager.get_next_campaign(
        CampaignPhase.C2A_STAGED,
        record,
    ) == CampaignPhase.C3_NA
    staged_melt = MeltState(campaign=CampaignPhase.C3_NA)
    manager.configure_campaign(staged_melt, CampaignPhase.C3_NA)

    assert manager.get_temp_target(
        CampaignPhase.C3_NA, 0, staged_melt
    ) == (1150.0, 600.0)
    assert manager.get_temp_target(
        CampaignPhase.C3_NA, 3, staged_melt
    ) == (1150.0, 600.0)
    assert "C3_NA" not in manager.overrides

    default_melt = MeltState(campaign=CampaignPhase.C3_NA)
    manager.configure_campaign(default_melt, CampaignPhase.C3_NA)

    assert manager.get_temp_target(
        CampaignPhase.C3_NA, 0, default_melt
    ) == (1275.0, 50.0)
    assert manager.get_temp_target(
        CampaignPhase.C3_NA, 3, default_melt
    ) == (1600.0, 50.0)


@pytest.mark.parametrize(
    "controlled_phase",
    [CampaignPhase.C3_K, CampaignPhase.C3_NA],
)
def test_c2a_staged_background_gas_does_not_leak_to_controlled_o2_stage(
    controlled_phase,
):
    setpoints = _setpoints()
    setpoints["campaigns"].setdefault(controlled_phase.name, {})[
        "carrier_gas"
    ] = "Ar"
    manager = CampaignManager(setpoints)
    melt = MeltState(campaign=CampaignPhase.C2A_STAGED)

    manager.configure_campaign(melt, CampaignPhase.C2A_STAGED)
    assert melt.background_gas_species == "N2"
    assert melt.background_gas_mole_fraction == pytest.approx(1.0)

    melt.campaign = controlled_phase
    manager.configure_campaign(melt, controlled_phase)

    assert melt.background_gas_species == ""
    assert melt.background_gas_mole_fraction == pytest.approx(0.0)
    assert _simulator_shell(
        melt,
        setpoints,
    )._resolve_condensation_carrier_gas() == "Ar"


def test_clean_stage_to_c2a_staged_reapplies_stage_n2_background():
    setpoints = _setpoints()
    manager = CampaignManager(setpoints)
    melt = MeltState(campaign=CampaignPhase.C3_NA)

    manager.configure_campaign(melt, CampaignPhase.C3_NA)
    assert melt.background_gas_species == ""
    assert melt.background_gas_mole_fraction == pytest.approx(0.0)

    melt.campaign = CampaignPhase.C2A_STAGED
    manager.configure_campaign(melt, CampaignPhase.C2A_STAGED)

    assert melt.background_gas_species == "N2"
    assert melt.background_gas_mole_fraction == pytest.approx(1.0)


def test_debug_batches_skip_c5_by_default_after_c4():
    manager = CampaignManager({"campaigns": {}})
    record = BatchRecord(feedstock_key="debug_pure_feo")
    record.branch = "two"

    assert manager.c5_enabled is False
    assert manager.get_next_campaign(CampaignPhase.C4, record) == CampaignPhase.C6
    assert (DecisionType.C6_PROCEED, "yes") in record.decisions


def test_c5_enabled_routes_c4_to_c5():
    manager = CampaignManager({"campaigns": {}})
    manager.c5_enabled = True
    record = BatchRecord(feedstock_key="debug_pure_feo")
    record.branch = "two"

    assert manager.get_next_campaign(CampaignPhase.C4, record) == CampaignPhase.C5


def test_branch_decision_context_matches_c5_default_off():
    manager = CampaignManager({"campaigns": {}})
    record = BatchRecord(feedstock_key="lunar_mare_low_ti")

    context = manager.get_decision(CampaignPhase.C3_NA, record).context

    assert "C4 Mg pyrolysis + C6 Mg thermite" in context
    assert "complete pyrolysis-only" in context
    assert "C5 limited MRE" not in context
    assert "full MRE to 2.5 V" not in context


def test_branch_one_completes_when_c5_default_off():
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {},
        {"metals": {}, "oxide_vapors": {}},
    )

    sim.apply_decision(DecisionType.BRANCH_ONE_TWO, "one")

    assert sim.melt.campaign == CampaignPhase.COMPLETE


def test_normal_batches_still_pause_for_branching_decisions():
    manager = CampaignManager({"campaigns": {}})
    record = BatchRecord(feedstock_key="lunar_mare_low_ti")

    assert manager.get_next_campaign(CampaignPhase.C0B, record) is None
    record.path = "A"
    assert manager.get_next_campaign(CampaignPhase.C3_NA, record) is None
    record.branch = "two"
    assert manager.get_next_campaign(CampaignPhase.C4, record) is None
    assert manager.get_decision(CampaignPhase.C4, record).decision_type == (
        DecisionType.C6_PROCEED
    )


def test_low_voltage_debug_feedstock_exercises_mre_electrolysis(monkeypatch):
    monkeypatch.setenv("REGOLITH_DEBUG_FEEDSTOCKS", "1")
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        load_visible_feedstocks(),
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("debug_pure_na2o", mass_kg=1000.0)
    sim.start_campaign(CampaignPhase.MRE_BASELINE)
    sim.melt.temperature_C = 1575.0

    oxygen_kg = sim._step_mre()

    assert oxygen_kg > 0.0
    assert sim.melt.composition_kg["Na2O"] < 1000.0
    assert sim.atom_ledger.kg_by_account("process.metal_phase")["Na"] > 0.0
    mre_oxygen = sim.atom_ledger.kg_by_account(
        "terminal.oxygen_mre_anode_stored"
    )["O2"]
    assert mre_oxygen == pytest.approx(oxygen_kg)
    assert sim.train.stages[4].collected_kg["Na"] > 0.0
    sim.atom_ledger.assert_balanced()


def test_c5_step_noops_when_c5_disabled(monkeypatch):
    monkeypatch.setenv("REGOLITH_DEBUG_FEEDSTOCKS", "1")
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        load_visible_feedstocks(),
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("debug_pure_na2o", mass_kg=1000.0)
    sim.start_campaign(CampaignPhase.C5)
    sim.melt.temperature_C = 1575.0
    metal_before = dict(sim.atom_ledger.kg_by_account("process.metal_phase"))
    transitions_before = len(sim.atom_ledger.transitions)
    sim._mre_metals_this_hr = {"Na": 1.0}
    sim._mre_voltage_V = 1.2
    sim._mre_current_A = 99.0
    sim._mre_effective_current_A = 10.0
    sim._mre_energy_this_hr = 3.0
    sim.melt.mre_voltage_V = 1.2
    sim.melt.mre_current_A = 10.0

    def fail_dispatch(*_args, **_kwargs):
        pytest.fail("_step_mre dispatched while C5 was disabled")

    sim._dispatch_only = fail_dispatch

    oxygen_kg = sim._step_mre()

    assert oxygen_kg == pytest.approx(0.0)
    assert sim._mre_metals_this_hr == {}
    assert sim._mre_voltage_V == pytest.approx(0.0)
    assert sim._mre_current_A == pytest.approx(0.0)
    assert sim._mre_effective_current_A == pytest.approx(0.0)
    assert sim._mre_energy_this_hr == pytest.approx(0.0)
    assert sim.melt.mre_voltage_V == pytest.approx(0.0)
    assert sim.melt.mre_current_A == pytest.approx(0.0)
    assert sim.melt.composition_kg["Na2O"] == pytest.approx(1000.0)
    assert sim.atom_ledger.kg_by_account("process.metal_phase") == metal_before
    assert len(sim.atom_ledger.transitions) == transitions_before
    sim.atom_ledger.assert_balanced()
