import pytest

import app as app_module
from simulator.campaigns import CampaignManager
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from simulator.state import BatchRecord, CampaignPhase, DecisionType
from web.feedstock_data import load_visible_feedstocks


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

    assert manager.get_next_campaign(CampaignPhase.C0B, record) == CampaignPhase.C2A
    assert record.path == "A"
    assert (DecisionType.PATH_AB, "A") in record.decisions

    assert manager.get_next_campaign(CampaignPhase.C3_NA, record) == CampaignPhase.C4
    assert record.branch == "two"
    assert (DecisionType.BRANCH_ONE_TWO, "two") in record.decisions

    assert manager.get_next_campaign(CampaignPhase.C5, record) == CampaignPhase.C6
    assert (DecisionType.C6_PROCEED, "yes") in record.decisions


def test_normal_batches_still_pause_for_branching_decisions():
    manager = CampaignManager({"campaigns": {}})
    record = BatchRecord(feedstock_key="lunar_mare_low_ti")

    assert manager.get_next_campaign(CampaignPhase.C0B, record) is None
    record.path = "A"
    assert manager.get_next_campaign(CampaignPhase.C3_NA, record) is None
    record.branch = "two"
    assert manager.get_next_campaign(CampaignPhase.C5, record) is None


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
    assert sim.train.stages[3].collected_kg["Na"] > 0.0
    sim.atom_ledger.assert_balanced()
