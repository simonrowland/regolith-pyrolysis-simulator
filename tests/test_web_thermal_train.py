from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from flask import Flask
import pytest
import yaml

from simulator.accounting.ledger import AtomLedger
from simulator.state import BatchRecord, EvaporationFlux, HourSnapshot, OverheadGas
from web import routes as web_routes
from web.events import _sim_locks, _simulations


@pytest.fixture
def client(tmp_path):
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="thermal-train-test",
        OPTIMIZER_RUNS_DIR=str(tmp_path / "runs"),
    )
    app.register_blueprint(web_routes.bp)
    return app.test_client()


def _live_sim():
    setpoints = yaml.safe_load(Path("data/setpoints.yaml").read_text(encoding="utf-8"))
    snapshot = HourSnapshot(
        hour=1,
        temperature_C=1500.0,
        evap_flux=EvaporationFlux(species_kg_hr={"Na": 1.0}, total_kg_hr=1.0),
        overhead=OverheadGas(transport_saturation_pct=3.0),
        melt_offgas_O2_mol_hr=100.0,
    )
    return SimpleNamespace(
        atom_ledger=AtomLedger(),
        record=BatchRecord(snapshots=[snapshot]),
        setpoints=setpoints,
    )


def test_no_source_page_and_htmx_partial_are_typed_and_do_not_resolve_artifacts(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(
        web_routes,
        "_optimizer_result_row",
        lambda *_args, **_kwargs: pytest.fail("no-source GET queried optimizer artifacts"),
    )
    response = client.get("/thermal-train")
    assert response.status_code == 200
    assert b'data-state="no_data"' in response.data
    assert b"This page never launches a simulation" in response.data
    assert b"thermal-train-default-artifact-v1" in response.data
    assert b"Load versioned default" in response.data
    partial = client.get("/partials/thermal-train-report")
    assert partial.status_code == 200
    assert b'hx-swap="outerHTML"' in partial.data
    assert b'data-state="no_data"' in partial.data


def test_versioned_default_artifact_is_exact_and_read_only(client, monkeypatch) -> None:
    monkeypatch.setattr(
        web_routes,
        "_optimizer_result_row",
        lambda *_args, **_kwargs: pytest.fail("default artifact queried optimizer store"),
    )
    response = client.get("/thermal-train?default_artifact=thermal-train-default-v1")
    assert response.status_code == 200
    assert b'data-state="artifact"' in response.data
    assert b"Read-only versioned default artifact report" in response.data
    artifact = json.loads(
        Path("data/fixtures/thermal_train/default-v1.json").read_text(encoding="utf-8")
    )
    assert artifact["artifact_schema_version"] == "thermal-train-default-artifact-v1"
    assert artifact["artifact_id"] == "thermal-train-default-v1"
    assert artifact["config"] == {
        "c3_shuttle_enabled": True,
        "c3_shuttle_recipe": {"K_kg": 0.0, "Na_kg": 12.0},
        "c5_enabled": False,
        "campaign": "C3_NA",
        "feedstock_id": "lunar_mare_low_ti",
        "hours": 33,
        "mass_kg": 1000.0,
        "track": "pyrolysis",
    }
    assert artifact["provenance"]["allow_unmeasured_alpha_fallback"] is True
    assert artifact["provenance"]["fallback_scope"] == "fixture_generation_only"
    assert artifact["provenance"]["backend_name"] == "internal-analytical"
    assert artifact["provenance"]["backend_policy"] == "runner-strict"
    assert artifact["provenance"]["backend_evidence_class"] == "internal-analytical"
    assert artifact["thermal_train_report"]["schema_version"] == "thermal-train-report-v1"
    assert artifact["thermal_train_report"]["peaks"]["hot_total_vapor_kg_hr"] > 0.0


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_thermal_train_routes_are_get_only(client, method: str) -> None:
    assert getattr(client, method)("/thermal-train").status_code == 405
    assert getattr(client, method)("/partials/thermal-train-report").status_code == 405


def test_live_page_reads_named_view_for_browser_owned_run(client) -> None:
    client_id = "thermal-live-browser"
    sid = "thermal-live-sid"
    _simulations[sid] = {
        "session": SimpleNamespace(simulator=_live_sim()),
        "run_id": "thermal-live-run",
        "ledger_client_id": client_id,
        "running": True,
    }
    _sim_locks[sid] = threading.RLock()
    try:
        with client.session_transaction() as browser_session:
            browser_session["ledger_client_id"] = client_id
        response = client.get("/thermal-train")
        assert response.status_code == 200
        assert b'data-state="live"' in response.data
        assert b"Live run history via the thermal_train named ledger view" in response.data
        assert b"Cold melt-offgas O2" in response.data
        assert response.data.count(b"Cold melt-offgas O2") == 1
    finally:
        _simulations.pop(sid, None)
        _sim_locks.pop(sid, None)


def test_selected_optimizer_artifact_is_read_only_report_payload(client, monkeypatch) -> None:
    report = {
        "schema_version": "thermal-train-report-v1",
        "status": "closed",
        "train_closes_for_run": True,
        "snapshot_count": 2,
        "peaks": {
            "hot_total_vapor_kg_hr": 1.0,
            "cold_o2_kg_hr": 2.0,
            "cold_o2_mol_hr": 62.5039,
        },
        "sections": {},
        "excluded_species": {},
        "excluded_species_nonzero": False,
        "display_costs": {"status": "unavailable"},
    }
    row = {"result_blob": json.dumps({"thermal_train_report": report})}
    monkeypatch.setattr(
        web_routes,
        "_optimizer_result_row",
        lambda run_id, cache_key: (Path("/root"), Path("/root/run"), row)
        if (run_id, cache_key) == ("run-1", "key-1")
        else None,
    )
    response = client.get("/thermal-train?run_id=run-1&cache_key=key-1")
    assert response.status_code == 200
    assert b'data-state="artifact"' in response.data
    assert b"Read-only optimizer artifact report" in response.data
    assert b"snapshots 2" in response.data


def test_selected_legacy_artifact_fails_closed_without_o2_series(client, monkeypatch) -> None:
    row = {"result_blob": json.dumps({"per_hour_summary": [{"vapor_species_kg_hr": {"Na": 1.0}}]})}
    monkeypatch.setattr(
        web_routes,
        "_optimizer_result_row",
        lambda *_args: (Path("/root"), Path("/root/run"), row),
    )
    response = client.get("/thermal-train?run_id=run-1&cache_key=legacy")
    assert response.status_code == 200
    assert b'data-state="artifact"' in response.data
    assert b"no authoritative thermal-train report" in response.data
    assert b"cumulative O2 is not substituted" in response.data


def test_passive_refusal_renders_reason_and_active_lift(client, monkeypatch) -> None:
    report = {
        "schema_version": "thermal-train-report-v1",
        "status": "incomplete",
        "train_closes_for_run": False,
        "snapshot_count": 1,
        "peaks": {"hot_total_vapor_kg_hr": 0.0, "cold_o2_kg_hr": 1.0, "cold_o2_mol_hr": 31.25},
        "sections": {
            "o2_passive_radiator_day": {
                "status": "passive_refused",
                "reason": "target_not_above_effective_sink_margin",
                "active_lift_W": 123.5,
            }
        },
        "excluded_species": {},
        "excluded_species_nonzero": False,
        "display_costs": {"status": "unavailable"},
    }
    monkeypatch.setattr(
        web_routes,
        "_optimizer_result_row",
        lambda *_args: (Path("/root"), Path("/root/run"), {"result_blob": json.dumps({"thermal_train_report": report})}),
    )
    response = client.get("/thermal-train?run_id=r&cache_key=k")
    assert b"target_not_above_effective_sink_margin" in response.data
    assert b"123.5 W active lift" in response.data
    assert response.data.count(b"Cold melt-offgas O2") == 1


def test_web_route_source_has_no_inline_simulation_or_worker_runtime_import() -> None:
    source = Path("web/routes.py").read_text(encoding="utf-8")
    assert "worker_runtime" not in source
    thermal_slice = source[source.index("def _thermal_train_context"):source.index("def _query_species")]
    assert "evaluate(" not in thermal_slice
    assert "PyrolysisSimulator(" not in thermal_slice
