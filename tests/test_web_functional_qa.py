import json
import math
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import app as app_module
import web.events as web_events
from simulator.account_ids import (
    C7_AL_CREDIT_ACCOUNT,
    CONDENSATION_RETAINED_HOLDUP_ACCOUNT,
    OXYGEN_BUBBLER_EXTERNAL_VENTED_ACCOUNT,
    OXYGEN_CAPTURED_ACCOUNTS,
    OXYGEN_STORED_ACCOUNTS,
    OXYGEN_VENTED_ACCOUNTS,
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
)
from simulator.accounting.queries import (
    PRODUCT_LEDGER_ACCOUNTS,
    TERMINAL_RUMP_REFRACTORY_OXIDES,
)
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from web.events import _clear_simulation_state, _simulations
from web.run_store import RunArtifactStore


pytestmark = [pytest.mark.serial, pytest.mark.xdist_group("serial")]

_ROOT = Path(__file__).resolve().parents[1]
_ADVISORY_HARNESS = (
    _ROOT / "tests/fixtures/web_render/render_simulator_advisory_dom.mjs"
)
_ADVISORY_SCRIPT = _ROOT / "web/static/js/simulator-advisory.js"
_START = {
    "feedstock": "lunar_mare_low_ti",
    "mass_kg": 1000,
    "backend": "internal-analytical",
    "track": "pyrolysis",
    "speed": 0,
    "c4_max_temp_C": 1670,
    "additives": {},
}


@pytest.fixture(autouse=True)
def _deterministic_web_run(monkeypatch):
    curve = {
        "source": "test_web_functional_qa",
        "solidus_T_C": 1000.0,
        "liquidus_T_C": 1700.0,
        "path": ((1000.0, 0.0), (1700.0, 1.0)),
    }
    monkeypatch.setattr(
        PyrolysisSimulator,
        "_freeze_gate_curve",
        lambda self: dict(curve),
    )
    original_load_yaml = web_events._load_yaml

    def load_yaml(filename):
        payload = original_load_yaml(filename)
        if filename == "setpoints.yaml":
            payload = dict(payload)
            campaigns = dict(payload.get("campaigns", {}) or {})
            c6 = dict(campaigns.get("C6", {}) or {})
            c6["max_hold_hr"] = 1
            campaigns["C6"] = c6
            payload["campaigns"] = campaigns
        return payload

    monkeypatch.setattr(web_events, "_load_yaml", load_yaml)
    monkeypatch.setattr(web_events, "_safe_log", lambda _message: None)


@pytest.fixture
def web_driver(monkeypatch, tmp_path):
    tasks = []

    def backend(_name):
        value = InternalAnalyticalBackend()
        value.initialize({})
        return value

    def capture(target, *args, **kwargs):
        tasks.append((target, args, kwargs))
        return {"captured_task": len(tasks)}

    monkeypatch.setattr(web_events, "_get_backend", backend)
    monkeypatch.setattr(app_module.socketio, "start_background_task", capture)
    app = app_module.create_app()
    run_dir = tmp_path / "runs"
    app.config["RUN_ARTIFACT_DIR"] = str(run_dir)
    http = app.test_client()
    html_response = http.get("/")
    assert html_response.status_code == 200
    client = app_module.socketio.test_client(app, flask_test_client=http)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)
    try:
        yield {
            "app": app,
            "client": client,
            "tasks": tasks,
            "before": before,
            "run_dir": run_dir,
            "html": html_response.get_data(as_text=True),
        }
    finally:
        if client.is_connected():
            client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def _drive(
    driver,
    *,
    alternate_path=False,
    perturb_every_gate=False,
    reject_first=False,
):
    client = driver["client"]
    client.emit("start_simulation", dict(_START))
    events = list(client.get_received())
    new_sids = set(_simulations) - driver["before"]
    assert len(new_sids) == 1
    sid = new_sids.pop()
    state = _simulations[sid]
    decisions = []
    completion = None
    rejected = False

    for _ in range(20):
        assert driver["tasks"], "run stopped without a terminal event"
        target, args, kwargs = driver["tasks"].pop(0)
        target(*args, **kwargs)
        received = list(client.get_received())
        events.extend(received)
        for event in received:
            if event["name"] == "decision_required":
                decision = event["args"][0]
                if perturb_every_gate:
                    client.emit("pause_simulation")
                    client.emit("resume_simulation")
                    events.extend(client.get_received())
                if reject_first and not rejected:
                    client.emit("make_decision", {"choice": "not-an-option"})
                    rejected_events = list(client.get_received())
                    events.extend(rejected_events)
                    assert any(
                        item["name"] == "simulation_status"
                        and item["args"][0].get("status") == "error"
                        for item in rejected_events
                    )
                    rejected = True
                choice = decision["recommendation"]
                if alternate_path and decision["type"] == "PATH_AB":
                    choice = "B"
                    assert choice in decision["options"]
                decisions.append((decision["type"], choice))
                client.emit("make_decision", {"choice": choice})
                events.extend(client.get_received())
            elif event["name"] == "simulation_complete":
                completion = event["args"][0]
        if completion is not None:
            break

    assert completion is not None
    return sid, state, events, decisions, completion


def _assert_true_finite_mol(ledger):
    for account, species_values in ledger.items():
        assert isinstance(species_values, dict), account
        for species, value in species_values.items():
            assert not isinstance(value, bool), (account, species, value)
            assert isinstance(value, (int, float)), (account, species, value)
            assert math.isfinite(value), (account, species, value)


def _render_product_story(*, html, payload):
    completed = subprocess.run(
        ["node", str(_ADVISORY_HARNESS)],
        input=json.dumps({
            "html": html,
            "event": "simulation_complete",
            "payload": payload,
            "script_path": str(_ADVISORY_SCRIPT),
            "ids": ["product-ledger-state", "product-ledger-content"],
        }),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _display_mass(value):
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _story_total(story, *keys):
    return sum(story[key]["class_total_kg"] for key in keys)


# Regression: WEBQA-001 — terminal UI hid the feedstock-to-product story.
# Found by /qa on 2026-07-19
# Report: docs-private/research/2026-07-19-webqa/report.md
def test_headless_full_run_ledgers_and_product_story_match_runner(web_driver):
    sid, state, events, decisions, completion = _drive(
        web_driver,
        perturb_every_gate=True,
        reject_first=True,
    )
    assert [kind for kind, _choice in decisions] == [
        "PATH_AB",
        "BRANCH_ONE_TWO",
        "C6_PROCEED",
    ]
    names = [event["name"] for event in events]
    assert names.count("simulation_tick") == names.count("per_hour_summary")
    assert names.count("decision_required") == len(decisions)
    assert names.count("simulation_complete") == 1
    assert not any(
        event["name"] == "simulation_status"
        and event["args"][0].get("status") == "error"
        and "not-an-option" not in event["args"][0].get("message", "")
        for event in events
    )

    artifact = RunArtifactStore(web_driver["run_dir"]).load(state["run_id"])
    assert artifact is not None
    assert artifact["execution_status"] == "ok"
    assert len(artifact["timesteps"]) == names.count("simulation_tick")
    assert all("ledger" in timestep for timestep in artifact["timesteps"])
    for timestep in artifact["timesteps"]:
        _assert_true_finite_mol(timestep["ledger"])
    _assert_true_finite_mol(artifact["terminal"]["final_state"])

    runner = web_events._full_runner_payload(
        state["session"],
        projector=state["runner_projector"],
        status="ok",
    )
    assert artifact["terminal"]["final_state"] == runner["final_state"]
    sim = state["session"].simulator
    assert (
        sim.setpoints["chemistry_kernel"]["allow_unmeasured_alpha_fallback"]
        is False
    )
    assert completion["products"] == {
        species: round(value, 2)
        for species, value in sim.product_ledger().items()
    }
    assert completion["oxygen_kg"] == sim._oxygen_total_kg()
    assert completion["terminal_rump_by_species"] == sim._terminal_rump_by_species()
    assert completion["mass_balance_error_pct"] == pytest.approx(0.0, abs=1e-9)

    story = completion["product_story"]
    assert story["input"] == {
        "feedstock": "lunar_mare_low_ti",
        "feedstock_label": sim.record.feedstock_label,
        "batch_mass_kg": 1000,
    }
    assert story["metal_ingots"]["class_total_kg"] > 0
    assert story["oxygen"]["class_total_kg"] > 0
    assert story["refractory_ceramic"]["class_total_kg"] > 0
    assert story["escaped_to_vacuum"]["class_total_kg"] >= 0
    assert story["terminal_residue"]["class_total_kg"] > 0
    assert completion["terminal_rump_by_species"]["Cr2O3"] > 0
    assert story["refractory_ceramic"]["species_kg"]["Cr2O3"] > 0
    ree_extent = story["refractory_ceramic"]["ree_enrichment_extent"]
    assert ree_extent["basis"] == (
        "initial_cleaned_melt_to_terminal_residual_ceramic"
    )
    assert ree_extent["source_ids"] == ["REF-056", "REF-057"]
    assert ree_extent["derivation"] == (
        "E=(R1/M1)/(R0/M0); X=1-M1/M0; retention=R1/R0"
    )

    stage_collection = sim._stage_collection_kg_by_source
    assert story["glass"]["species_kg"] == {
        species: round(sum(
            mass
            for (account, stage, routed_species), mass in stage_collection.items()
            if account == "process.condensation_train"
            and stage == 3
            and routed_species == species
        ), 2)
        for species in {"SiO", "SiO2"}
        if any(
            account == "process.condensation_train"
            and stage == 3
            and routed_species == species
            and mass > 0
            for (account, stage, routed_species), mass in stage_collection.items()
        )
    }
    assert story["captured_volatiles"]["species_kg"] == {
        species: round(sum(
            mass
            for (account, stage, routed_species), mass in stage_collection.items()
            if account == "process.condensation_train"
            and stage == 4
            and routed_species == species
        ), 2)
        for species in {"Na", "K", "Mg"}
        if any(
            account == "process.condensation_train"
            and stage == 4
            and routed_species == species
            and mass > 0
            for (account, stage, routed_species), mass in stage_collection.items()
        )
    }
    assert story["escaped_to_vacuum"]["species_kg"] == {
        species: round(mass, 2)
        for species, mass in sim.atom_ledger.project_account_kg(
            "terminal.offgas"
        ).items()
        if mass > 0
    }
    rump = sim._terminal_rump_by_species()
    assert story["refractory_ceramic"]["species_kg"] == {
        species: round(mass, 2)
        for species, mass in rump.items()
        if species in TERMINAL_RUMP_REFRACTORY_OXIDES and mass > 0
    }
    assert set(story["terminal_residue"]["species_kg"]) == (
        set(rump) - TERMINAL_RUMP_REFRACTORY_OXIDES
    )

    product_class_species = [
        set(story[key]["species_kg"])
        for key in (
            "metal_ingots",
            "glass",
            "captured_volatiles",
            "unclassified",
        )
    ]
    for index, species in enumerate(product_class_species):
        assert all(
            species.isdisjoint(other)
            for other in product_class_species[index + 1:]
        )

    source_total = sum(
        sum(sim.atom_ledger.project_account_kg(account).values())
        for account in PRODUCT_LEDGER_ACCOUNTS
        if account != C7_AL_CREDIT_ACCOUNT
    )
    source_total += sum(
        sum(sim.atom_ledger.project_account_kg(account).values())
        for account in (
            *OXYGEN_STORED_ACCOUNTS,
            *OXYGEN_CAPTURED_ACCOUNTS,
            *OXYGEN_VENTED_ACCOUNTS,
            OXYGEN_BUBBLER_EXTERNAL_VENTED_ACCOUNT,
            CONDENSATION_RETAINED_HOLDUP_ACCOUNT,
            SPENT_REDUCTANT_RESIDUE_ACCOUNT,
        )
    )
    source_total += sum(
        sum(sim.atom_ledger.project_account_kg(account).values())
        for account in sim.atom_ledger.mol_by_account()
        if account == "process.wall_deposit"
        or account.startswith("process.wall_deposit_segment_")
    )
    source_total += sum(rump.values())
    story_total = _story_total(
        story,
        "metal_ingots",
        "glass",
        "oxygen",
        "captured_volatiles",
        "refractory_ceramic",
        "terminal_residue",
        "escaped_to_vacuum",
        "unrecovered_process_inventory",
        "wall_deposits",
        "process_residue",
        "off_spec_condensate",
        "unclassified",
    )
    assert story_total == pytest.approx(source_total, abs=0.1)

    for key in (
        "metal_ingots",
        "glass",
        "oxygen",
        "captured_volatiles",
        "refractory_ceramic",
        "terminal_residue",
        "escaped_to_vacuum",
        "unrecovered_process_inventory",
        "wall_deposits",
        "process_residue",
        "off_spec_condensate",
        "unclassified",
    ):
        bucket = story[key]
        assert bucket["class_total_kg"] == round(bucket["class_total_kg"], 2)
        assert all(
            mass == round(mass, 2)
            for mass in bucket.get("species_kg", {}).values()
        )

    rendered = _render_product_story(
        html=web_driver["html"],
        payload=completion,
    )
    text = rendered["text"]["product-ledger-content"]
    assert rendered["text"]["product-ledger-state"] == "ok"
    for phrase in (
        "Pot of regolith in",
        sim.record.feedstock_label,
        "Metal ingots out",
        "Glass out",
        "Oxygen out",
        "Captured volatiles out",
        "Refractory ceramic out",
        "Terminal residue — incompletely extracted",
        "Escaped to vacuum",
        "Unrecovered process inventory",
        "Furnace wall deposits",
        "Process residue",
        "Off-spec condenser capture",
    ):
        assert phrase in text
    for key in (
        "metal_ingots",
        "glass",
        "oxygen",
        "captured_volatiles",
        "refractory_ceramic",
        "terminal_residue",
        "escaped_to_vacuum",
        "unrecovered_process_inventory",
        "wall_deposits",
        "process_residue",
        "off_spec_condensate",
    ):
        rendered_mass = _display_mass(story[key]["class_total_kg"])
        assert f"class total kg: {rendered_mass} kg" in text
    assert f"feedstock label: {sim.record.feedstock_label} kg" not in text

    empty_story_payload = dict(completion)
    empty_story_payload["product_story"] = dict(story)
    for key in ("metal_ingots", "glass"):
        empty_story_payload["product_story"][key] = {
            "species_kg": {},
            "class_total_kg": 0.0,
        }
    empty_rendered = _render_product_story(
        html=web_driver["html"],
        payload=empty_story_payload,
    )
    empty_text = empty_rendered["text"]["product-ledger-content"]
    assert empty_rendered["text"]["product-ledger-state"] == "ok"
    assert "Metal ingots out" in empty_text
    assert "Glass out" in empty_text
    assert "class total kg: 0 kg" in empty_text

    assert sid in _simulations


def test_alternate_path_b_completes_with_gate_pause_resume(web_driver):
    _sid, state, events, decisions, completion = _drive(
        web_driver,
        alternate_path=True,
        perturb_every_gate=True,
    )
    assert decisions[0][0] == "PATH_AB"
    assert decisions[0][1] != "A_staged"
    assert state["session"].simulator.record.path == "B"
    assert completion["mass_balance_error_pct"] == pytest.approx(0.0, abs=1e-9)
    assert sum(event["name"] == "decision_required" for event in events) == len(
        decisions
    )


def test_completion_payload_degrades_when_product_classifier_raises(monkeypatch):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "s_type": {
                "label": "S type",
                "composition_wt_pct": {"SiO2": 51.5, "FeO": 13.0, "MgO": 35.5},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("s_type")
    monkeypatch.setattr(
        web_events,
        "classify_products",
        lambda _sim: (_ for _ in ()).throw(ValueError("rump mismatch")),
    )
    monkeypatch.setattr(
        sim,
        "_terminal_rump_by_class",
        lambda: (_ for _ in ()).throw(ValueError("rump mismatch")),
    )

    payload = web_events._completion_payload(sim)

    assert payload["product_story"] is None
    assert payload["product_story_status"] == "unavailable"
    assert payload["products"] == {
        species: round(value, 2)
        for species, value in sim.product_ledger().items()
    }
    assert payload["terminal_rump_by_species"] == sim._terminal_rump_by_species()


def test_empty_product_classes_render_from_completion_projection(web_driver):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "s_type": {
                "label": "S type",
                "composition_wt_pct": {"SiO2": 51.5, "FeO": 13.0, "MgO": 35.5},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("s_type")

    payload = web_events._completion_payload(sim)

    assert payload["product_story_status"] == "ok"
    assert payload["product_story"]["metal_ingots"]["class_total_kg"] == 0.0
    assert payload["product_story"]["glass"]["class_total_kg"] == 0.0
    rendered = _render_product_story(html=web_driver["html"], payload=payload)
    text = rendered["text"]["product-ledger-content"]
    assert rendered["text"]["product-ledger-state"] == "ok"
    assert "Metal ingots out" in text
    assert "Glass out" in text
    assert "class total kg: 0 kg" in text


def test_product_story_requires_designated_stage_provenance(monkeypatch):
    balances = {
        "process.condensation_train": {
            "SiO": 1.0,
            "Na": 2.0,
            "Fe": 3.0,
            "Ca": 6.0,
        },
        "process.wall_deposit": {"Ca": 4.0},
        OXYGEN_BUBBLER_EXTERNAL_VENTED_ACCOUNT: {"O2": 5.0},
    }

    class Ledger:
        def project_account_kg(self, account):
            return dict(balances.get(account, {}))

        def mol_by_account(self):
            return dict(balances)

    sim = SimpleNamespace(
        atom_ledger=Ledger(),
        record=SimpleNamespace(
            feedstock_key="test",
            feedstock_label="Test",
            batch_mass_kg=15.0,
        ),
        _stage_collection_kg_by_source={
            ("process.condensation_train", 2, "SiO"): 1.0,
            ("process.condensation_train", 3, "Na"): 2.0,
            ("process.condensation_train", 1, "Fe"): 3.0,
            ("process.condensation_train", 4, "Ca"): 6.0,
        },
    )
    monkeypatch.setattr(web_events, "classify_products", lambda _sim: {})

    story = web_events._product_story_payload(
        sim,
        terminal_rump_by_species={},
    )

    assert story["glass"]["class_total_kg"] == 0.0
    assert story["captured_volatiles"]["class_total_kg"] == 0.0
    assert story["metal_ingots"]["species_kg"] == {"Ca": 6.0, "Fe": 3.0}
    assert story["off_spec_condensate"]["species_kg"] == {
        "Na": 2.0,
        "SiO": 1.0,
    }
    assert story["wall_deposits"]["species_kg"] == {"Ca": 4.0}
    assert story["escaped_to_vacuum"]["species_kg"] == {"O2": 5.0}


def test_socket_without_http_identity_is_refused_without_state():
    app = app_module.create_app()
    before = set(_simulations)
    client = app_module.socketio.test_client(app)
    try:
        connect_events = client.get_received()
        client.emit("start_simulation", dict(_START))
        events = connect_events + client.get_received()
        statuses = [
            event["args"][0]
            for event in events
            if event["name"] == "simulation_status"
        ]
        assert statuses
        assert all(
            status.get("error_type") == "client_identity_required"
            for status in statuses
        )
        assert set(_simulations) == before
        assert not any(
            event["name"] in {"simulation_tick", "simulation_complete"}
            for event in events
        )
    finally:
        if client.is_connected():
            client.disconnect()


def test_bad_start_is_rejected_without_fabricated_run_data(web_driver):
    client = web_driver["client"]
    client.emit("start_simulation", {**_START, "mass_kg": True})
    events = client.get_received()
    statuses = [
        event["args"][0]
        for event in events
        if event["name"] == "simulation_status"
    ]
    assert statuses[-1]["status"] == "error"
    assert statuses[-1]["error_type"] == "invalid_run_input"
    assert set(_simulations) == web_driver["before"]
    assert web_driver["tasks"] == []
    assert not any(
        event["name"] in {
            "simulation_tick",
            "per_hour_summary",
            "simulation_complete",
        }
        for event in events
    )
