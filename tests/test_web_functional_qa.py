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
    METAL_FLOAT_LAYER_ACCOUNT,
    METAL_PHASE_ACCOUNT,
    OXYGEN_BUBBLER_EXTERNAL_VENTED_ACCOUNT,
    OXYGEN_CAPTURED_ACCOUNTS,
    OXYGEN_STORED_ACCOUNTS,
    OXYGEN_VENTED_ACCOUNTS,
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
    TERMINAL_DRAIN_TAP_ACCOUNT,
)
from simulator.accounting.queries import (
    CONDENSATION_TRAIN_ACCOUNT,
    PRODUCT_LEDGER_ACCOUNTS,
    TERMINAL_RUMP_REFRACTORY_OXIDES,
)
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.runner import RUNNER_MASS_BALANCE_LIMIT_PCT
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
    terminal_status = None
    rejected = False

    # 2026-07-22 B1: 20 -> 60 rounds — the composed canonical path adds the
    # 160 h final C2A + C6 leg to every full drive.
    for _ in range(60):
        assert driver["tasks"], "run stopped without a terminal event"
        target, args, kwargs = driver["tasks"].pop(0)
        target(*args, **kwargs)
        received = list(client.get_received())
        events.extend(received)
        for event in received:
            if event["name"] == "simulation_status":
                status_payload = event["args"][0]
                if (
                    status_payload.get("run_id")
                    and status_payload.get("status") in {
                        "cancelled", "error", "refused"
                    }
                ):
                    terminal_status = status_payload
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
        if terminal_status is not None:
            break

    assert completion is not None, (
        "run terminated before simulation_complete: "
        f"{terminal_status or 'no terminal event received'}"
    )
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
# Nightly (2026-08-02 CI tiering): serial web full path (~187 s junit).
@pytest.mark.nightly
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
    # Production completion emits a percent value (not a fraction) plus the
    # named breach state.  The mandate's 5e-12 % bound therefore applies
    # directly, and this fails for any correct-but-different out-of-bound value.
    assert abs(completion["mass_balance_error_pct"]) <= (
        RUNNER_MASS_BALANCE_LIMIT_PCT
    )
    assert completion["mass_balance_error_breached"] is False

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
    # 2026-08-06 b-145: physical-composite OOR lowers multi-dex inflated
    # low-T composite pressures. The prior pin required residual Cr2O3 in the
    # terminal ceramic rump; under the physical continuation the C3/C6 Cr
    # metallothermic path fully reduces the lunar Cr2O3 inventory to metallic
    # Cr (~2.44 kg product) rather than leaving ceramic dust. Sign check: Cr
    # leaves as metal product (not vacuum oxide escape), mass balance holds
    # (mb% ~1e-14), and the oxide residual is zero by reduction not by the
    # wrong-direction volatilisation the OOR fix removes. Contract is now
    # "Cr is extracted as metal" rather than "Cr2O3 survives as ceramic".
    # 2026-08-07 t-534: the exact ``== 0.0`` pin above was float-brittle.
    # AtomLedger keeps sub-tolerance signed dust, so a fully reduced parent
    # can legitimately terminate at O(1e-16) kg rather than an exact zero
    # (observed Cr2O3 residual 2.56e-16 kg once the trajectory shifted — the
    # perturbation source is the Ca Pref retarget and/or engines.local.toml
    # presence, b-146; either way the brittleness is the defect). Same
    # numerical-dust class as the b-145 melt_activity floor.
    # 2026-08-07 b-147 (Mg TE-μ0 demotion): the Cr story flips back — and the
    # physics says it should. Corrected Mg Pref (JANAF gas_fugacity, ~+0.55 dex
    # over the demoted TE fit) means dosed Mg reductant evaporates out of the
    # melt instead of staying to reduce Cr2O3, so under the DEFAULT lunar
    # recipe the C3/C6 path recovers only ~3% of Cr as metal:
    # rump Cr2O3 = 3.4668 kg (2.372 kg Cr as oxide) + ingot Cr = 0.07 kg,
    # closing the ~2.44 kg Cr inventory (mass balance holds). This is a
    # finding about the recipe, not the rail: the old full-reduction outcome
    # was an artifact of the under-predicted Mg vapor pressure. Contract is
    # now "Cr inventory closes across ceramic + ingot under the default
    # recipe"; recipe re-tuning for Cr recovery under corrected Mg physics is
    # tracked as its own store task (see b-147 closure notes).
    # 2026-08-09 b-151 (Na2/Na2O_gas base retarget to L&H monatomic Pref):
    # rump Cr2O3 3.4667933925375607 → 3.429852312059024 (post-rebase onto
    # 4a0a574 / b-136 surface; prior pre-rebase probe was 3.4339744047894474).
    # CANDIDATE shuttle mechanism FALSIFIED: C3 Na-shuttle
    # per_oxide_reduced_kg['Cr2O3'] = 0.0 on both before/after legs (after does
    # reduce FeO 0.161 kg once Na inventory is non-zero; still zero Cr).
    #
    # MECHANISM (artifact-supported; dual-review rewrite):
    # The before leg's retired Na2O_gas composite base drives an unphysical
    # cold Na2O boil-off (~4.1 kg/hr at ~175 °C) that drains melt Na2O by ~h96
    # and bifurcates the later trajectory (Fe tap storm h101–106; Cr leaves
    # mainly as metal and is fully captured). The after leg retains melt Na2O,
    # keeps Cr oxidised through the 1800 °C C2A dwell, and evolves CrO vapor.
    # Capture fractions are LEG-INVARIANT (Cr 100% / CrO 0% both legs —
    # pressure-isolated per species). CrO has 0% capture by data construction
    # (no Antoine block, no sticking-α; condensation_reference_at_1mbar_C=2195
    # is a routing-only proxy — NOT "poorer capture"). Offgas CrO 0.0627 →
    # 1.06718 mol; Cr-eq offgas 0.00350 → 0.055636 kg. Display-rounded product
    # Cr 0.07 → 0.06; product/offgas CrO 0.0 → 0.07.
    # Contract: Cr inventory closes across ceramic + ingot + offgas CrO, not
    # ceramic+ingot alone. Pins = executed post-rebase probe values; never
    # hand-pasted. See docs-private/research/2026-08-09-b151-disposition/ and
    # validation-data/pin-evidence/na2_composite_lh_base_2026-08-09.yaml.
    rump = completion["terminal_rump_by_species"]
    _CR_MASS_FRACTION_IN_CR2O3 = 2 * 51.9961 / 151.9904  # 2 Cr per Cr2O3
    _CR_MW = 51.9961
    _CRO_MW = 51.9961 + 15.9994  # CrO
    # Exact offgas CrO mass (kg species) from AtomLedger terminal.offgas;
    # display-rounded product story uses round(mass, 2) → 0.07.
    # Pin = executed web-golden value on the post-rebase surface (stable across
    # repeated -n0 runs of this test; not the instrumented probe path which
    # differs at ~6e-6 rel). Derivation: project_account_kg("terminal.offgas")
    # ["CrO"] after the headless full-run path. Cr-eq = Σ n_Cr,i * M_Cr from
    # Cr/CrO/CrO2/CrO3 offgas species masses.
    _PINNED_CRO_OFFGAS_KG = 0.07256276804358505
    _PINNED_CR_OFFGAS_EQ_KG = 0.0556351828
    cr_in_rump_kg = rump.get("Cr2O3", 0.0) * _CR_MASS_FRACTION_IN_CR2O3
    cr_ingot_kg = completion["products"].get("Cr", 0.0)
    offgas_kg = sim.atom_ledger.project_account_kg("terminal.offgas")
    cro_offgas_kg = float(offgas_kg.get("CrO", 0.0) or 0.0)
    # Offgas Cr metal-equivalent (kg Cr) across Cr-bearing offgas species.
    _CRO2_MW = _CR_MW + 2.0 * 15.9994
    _CRO3_MW = _CR_MW + 3.0 * 15.9994
    cr_offgas_eq_kg = (
        float(offgas_kg.get("Cr", 0.0) or 0.0)
        + cro_offgas_kg * (_CR_MW / _CRO_MW)
        + float(offgas_kg.get("CrO2", 0.0) or 0.0) * (_CR_MW / _CRO2_MW)
        + float(offgas_kg.get("CrO3", 0.0) or 0.0) * (_CR_MW / _CRO3_MW)
    )
    assert rump.get("Cr2O3", 0.0) == pytest.approx(3.429852312059024, rel=1e-9)
    assert cr_ingot_kg == pytest.approx(0.06, abs=0.005)
    # Offgas kg has ~1e-9 relative run-to-run noise on a full multi-hour path;
    # pin at 1e-6 rel (same class as unit-activity pressure pins).
    assert cro_offgas_kg == pytest.approx(_PINNED_CRO_OFFGAS_KG, rel=1e-6)
    assert cr_offgas_eq_kg == pytest.approx(_PINNED_CR_OFFGAS_EQ_KG, rel=1e-6)
    # story payload values are display-rounded (2 dp), so compare loosely.
    assert story["metal_ingots"]["species_kg"].get("Cr", 0.0) == pytest.approx(
        cr_ingot_kg, abs=0.005
    )
    assert story["escaped_to_vacuum"]["species_kg"].get("CrO", 0.0) == pytest.approx(
        0.07, abs=0.005
    )
    # Inventory closure: ceramic Cr + ingot Cr + offgas Cr-eq closes the
    # ~2.46 kg lunar Cr feed (AtomLedger total 2.459769). Ceramic+ingot alone
    # no longer closes after b-151 parks ~0.055 kg Cr in terminal.offgas CrO.
    assert cr_in_rump_kg + cr_ingot_kg + cr_offgas_eq_kg == pytest.approx(
        2.46, abs=0.02
    )
    assert story["refractory_ceramic"]["species_kg"].get("Cr2O3", 0.0) == (
        pytest.approx(rump.get("Cr2O3", 0.0), abs=0.005)
    )
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


# Nightly (2026-08-02 CI tiering): serial web full path (~327 s junit).
@pytest.mark.nightly
@pytest.mark.timeout(1200)
# Hang-backstop, not a perf bar: measured 268s serial on Studio 1 pre-train and
# slower with the disposition machinery; the 300s global default was always
# marginal there. 1200s ~= 3x measured per the contention-robust ceiling policy.
def test_alternate_path_b_completes_with_gate_pause_resume(web_driver):
    _sid, state, events, decisions, completion = _drive(
        web_driver,
        alternate_path=True,
        perturb_every_gate=True,
    )
    assert decisions[0][0] == "PATH_AB"
    assert decisions[0][1] != "A_staged"
    assert state["session"].simulator.record.path == "B"
    # Production completion emits a percent value (not a fraction) plus the
    # named breach state.  The mandate's 5e-12 % bound therefore applies
    # directly, and this fails for any correct-but-different out-of-bound value.
    assert abs(completion["mass_balance_error_pct"]) <= (
        RUNNER_MASS_BALANCE_LIMIT_PCT
    )
    assert completion["mass_balance_error_breached"] is False
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


def test_degraded_product_story_badge_uses_canonical_extraction_evidence(
    web_driver,
    monkeypatch,
):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "s_type": {
                "label": "S type",
                "composition_wt_pct": {
                    "SiO2": 51.5,
                    "FeO": 13.0,
                    "MgO": 35.5,
                },
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("s_type")
    sim.atom_ledger.move(
        "test-unrecovered-overhead",
        "process.cleaned_melt",
        "process.overhead_gas",
        {"SiO2": 1.0},
    )
    monkeypatch.setattr(
        web_events,
        "classify_products",
        lambda _sim: (_ for _ in ()).throw(ValueError("rump mismatch")),
    )

    zero_extraction = web_events._completion_payload(sim)
    sim.atom_ledger.move(
        "test-stage-3-glass",
        "process.cleaned_melt",
        CONDENSATION_TRAIN_ACCOUNT,
        {"SiO2": 1.0},
    )
    sim._stage_collection_kg_by_source[
        (CONDENSATION_TRAIN_ACCOUNT, 3, "SiO2")
    ] = 1.0
    real_product = web_events._completion_payload(sim)

    states = tuple(
        _render_product_story(html=web_driver["html"], payload=payload)["text"][
            "product-ledger-state"
        ]
        for payload in (zero_extraction, real_product)
    )

    assert states == ("no-products", "ok")
    assert (
        zero_extraction["extracted_product_kg"],
        real_product["extracted_product_kg"],
    ) == pytest.approx((0.0, 1.0))
    assert zero_extraction["product_story_status"] == "unavailable"
    assert real_product["product_story_status"] == "unavailable"


@pytest.mark.parametrize(
    ("float_layer_kg", "drain_tap_kg", "expected_state"),
    [
        (2.0, 0.0, "no-products"),
        (0.0, 1.0, "ok"),
        (2.0, 1.0, "ok"),
    ],
)
def test_product_badge_counts_terminal_metal_tap_not_diagnostic_float_layer(
    web_driver,
    float_layer_kg,
    drain_tap_kg,
    expected_state,
):
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
    total_metal_kg = float_layer_kg + drain_tap_kg
    sim.atom_ledger.load_external(
        METAL_PHASE_ACCOUNT,
        {"Al": total_metal_kg},
        source="test extracted metal before stratification",
        material_origin="feedstock",
    )
    if float_layer_kg:
        sim.atom_ledger.move(
            "test-diagnostic-float-layer",
            METAL_PHASE_ACCOUNT,
            METAL_FLOAT_LAYER_ACCOUNT,
            {"Al": float_layer_kg},
            reason="metal_phase_stratification_diagnostic_only_no_tap_gate",
        )
    if drain_tap_kg:
        sim.atom_ledger.move(
            "test-terminal-drain-tap",
            METAL_PHASE_ACCOUNT,
            TERMINAL_DRAIN_TAP_ACCOUNT,
            {"Al": drain_tap_kg},
            reason="terminal metal drain tap",
        )

    payload = web_events._completion_payload(sim)
    rendered = _render_product_story(html=web_driver["html"], payload=payload)
    text = rendered["text"]["product-ledger-content"]

    assert rendered["text"]["product-ledger-state"] == expected_state
    assert payload["extracted_product_kg"] == pytest.approx(drain_tap_kg)
    assert payload["product_story"]["metal_ingots"]["class_total_kg"] == pytest.approx(
        drain_tap_kg
    )
    assert payload["product_story"]["unrecovered_process_inventory"][
        "class_total_kg"
    ] == pytest.approx(float_layer_kg)
    if float_layer_kg:
        assert sim.atom_ledger.project_account_kg(METAL_FLOAT_LAYER_ACCOUNT) == {
            "Al": pytest.approx(float_layer_kg)
        }
        assert "Unrecovered process inventory" in text
        assert f"Al {_display_mass(float_layer_kg)} kg" in text


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
    # This batch is loaded and never run, so nothing was extracted. The badge
    # must say so: CLAUDE.md section 4 names incomplete extraction as failure
    # mode #1, and residue that was in the charge all along is not evidence of
    # production. The subject of THIS test is that empty classes still RENDER
    # (assertions below); the badge state is incidental to it and was pinned at
    # "ok" by the defect. See test_product_badge_uses_extracted_classes_not_residue
    # for the both-directions pin.
    assert rendered["text"]["product-ledger-state"] == "no-products"
    assert "Metal ingots out" in text
    assert "Glass out" in text
    assert "class total kg: 0 kg" in text


@pytest.mark.parametrize(
    ("story_product", "flat_products", "expected_state"),
    [
        ({"refractory_ceramic": 280.0}, {}, "no-products"),
        ({}, {"unspent_Na_reagent": 280.0}, "no-products"),
        ({"metal_ingots": 1.0}, {}, "ok"),
        (None, {"Fe": 1.0}, "ok"),
    ],
)
def test_product_badge_uses_extracted_classes_not_residue(
    web_driver, story_product, flat_products, expected_state
):
    zero_bucket = {"species_kg": {}, "class_total_kg": 0.0}
    story = None
    if story_product is not None:
        buckets = {
            key: dict(zero_bucket)
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
            )
        }
        for key, value in story_product.items():
            buckets[key] = {
                "species_kg": {"test": value},
                "class_total_kg": value,
            }
        story = {
            "input": {
                "feedstock": "test",
                "feedstock_label": "Test feed",
                "batch_mass_kg": 1000.0,
            },
            **buckets,
        }
    payload = {
        "mass_in_kg": 1000.0,
        "products": flat_products,
        "oxygen_kg": 0.0,
        "oxygen_stored_kg": 0.0,
        "product_story": story,
        "process_inventory_spent_reductant": {
            "kg_by_species": {"Na2O": 5.0},
            "class_total_kg": 5.0,
            "account": "process.spent_reductant_residue",
            "disposition": "process_inventory_spent_reductant",
        },
    }

    rendered = _render_product_story(html=web_driver["html"], payload=payload)
    text = rendered["text"]["product-ledger-content"]

    assert rendered["text"]["product-ledger-state"] == expected_state
    assert "Spent reductant residue" in text
    if story_product and "refractory_ceramic" in story_product:
        assert "Refractory ceramic out" in text
    if any("reagent" in key for key in flat_products):
        assert "Reagent bookkeeping residue" in text


def test_product_badge_does_not_count_unrecovered_flat_inventory_as_extraction(
    web_driver,
):
    zero_bucket = {"species_kg": {}, "class_total_kg": 0.0}
    story = {
        "input": {
            "feedstock": "test",
            "feedstock_label": "Test feed",
            "batch_mass_kg": 1000.0,
        },
        **{
            key: dict(zero_bucket)
            for key in (
                "metal_ingots",
                "glass",
                "oxygen",
                "captured_volatiles",
                "refractory_ceramic",
                "escaped_to_vacuum",
                "wall_deposits",
                "process_residue",
                "off_spec_condensate",
                "unclassified",
            )
        },
        "terminal_residue": {
            "species_kg": {"SiO2": 950.0},
            "class_total_kg": 950.0,
        },
        "unrecovered_process_inventory": {
            "species_kg": {"SiO": 50.0},
            "class_total_kg": 50.0,
        },
    }
    payload = {
        "mass_in_kg": 1000.0,
        "products": {"SiO": 50.0},
        "oxygen_kg": 0.0,
        "oxygen_stored_kg": 0.0,
        "product_story": story,
    }

    rendered = _render_product_story(html=web_driver["html"], payload=payload)
    text = rendered["text"]["product-ledger-content"]

    assert rendered["text"]["product-ledger-state"] == "no-products"
    assert "Terminal residue — incompletely extracted" in text
    assert "SiO2 950 kg" in text
    assert "Unrecovered process inventory" in text
    assert "SiO 50 kg" in text


def test_completion_payload_marks_story_incomplete_without_builder_exception(
    monkeypatch,
):
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
    incomplete_story = {
        "input": {
            "feedstock": "s_type",
            "feedstock_label": "S type",
            "batch_mass_kg": 1000.0,
        },
        "metal_ingots": {"species_kg": {}, "class_total_kg": 0.0},
    }
    monkeypatch.setattr(
        web_events,
        "_product_story_payload",
        lambda *_args, **_kwargs: incomplete_story,
    )

    payload = web_events._completion_payload(sim)

    assert payload["product_story"] == incomplete_story
    assert payload["product_story_status"] == "incomplete"


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
