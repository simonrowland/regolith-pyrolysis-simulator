import pytest

import app as app_module
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from web.events import (
    _clear_simulation_state,
    _completion_payload,
    _replace_simulation_state,
    _sim_locks,
    _simulations,
)


def test_launcher_defaults_to_localhost_and_debug_off(monkeypatch):
    call = {}

    monkeypatch.delenv("REGOLITH_HOST", raising=False)
    monkeypatch.delenv("REGOLITH_PORT", raising=False)
    monkeypatch.delenv("REGOLITH_FLASK_DEBUG", raising=False)
    monkeypatch.setattr(app_module, "create_app", lambda: object())

    def fake_run(app, **kwargs):
        call.update(kwargs)

    monkeypatch.setattr(app_module.socketio, "run", fake_run)

    app_module.main()

    assert call["host"] == "127.0.0.1"
    assert call["port"] == 3000
    assert call["debug"] is False
    assert call["allow_unsafe_werkzeug"] is True


def test_launcher_does_not_allow_unsafe_werkzeug_on_public_host(monkeypatch):
    call = {}

    monkeypatch.setenv("REGOLITH_HOST", "0.0.0.0")
    monkeypatch.setenv("REGOLITH_ALLOW_UNSAFE_WERKZEUG", "1")
    monkeypatch.delenv("REGOLITH_FLASK_DEBUG", raising=False)
    monkeypatch.setattr(app_module, "create_app", lambda: object())

    def fake_run(app, **kwargs):
        call.update(kwargs)

    monkeypatch.setattr(app_module.socketio, "run", fake_run)

    app_module.main()

    assert call["host"] == "0.0.0.0"
    assert call["debug"] is False
    assert call["allow_unsafe_werkzeug"] is False


def test_launcher_rejects_public_debug_host(monkeypatch):
    monkeypatch.setenv("REGOLITH_HOST", "0.0.0.0")
    monkeypatch.setenv("REGOLITH_FLASK_DEBUG", "1")

    with pytest.raises(RuntimeError, match="loopback host"):
        app_module.main()


def test_launcher_rejects_invalid_port(monkeypatch):
    monkeypatch.setenv("REGOLITH_PORT", "abc")

    with pytest.raises(SystemExit, match="REGOLITH_PORT"):
        app_module.main()


def test_replacing_simulation_state_stops_prior_run():
    sid = "test-replace"
    try:
        first, first_lock = _replace_simulation_state(
            sid, object(), speed=0.0)
        second, second_lock = _replace_simulation_state(
            sid, object(), speed=0.0)

        assert first["running"] is False
        assert second["running"] is True
        assert first["run_id"] != second["run_id"]
        assert _simulations[sid] is second
        assert _sim_locks[sid] is second_lock
        assert first_lock is not second_lock
    finally:
        _clear_simulation_state(sid)


def test_completion_payload_exposes_final_mass_reconciliation():
    backend = StubBackend()
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
                    "MgO": 34.0,
                },
                "bulk_additions": {
                    "metallic_FeNi_wt_pct": 15.0,
                },
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("s_type")

    payload = _completion_payload(sim)

    assert payload["mass_in_kg"] == pytest.approx(1000.0)
    assert payload["mass_out_kg"] == pytest.approx(1000.0)
    assert payload["mass_balance_error_pct"] == pytest.approx(0.0)
    assert payload["stage0_mass_balance_delta_kg"] == pytest.approx(0.0)
    assert "residual_inventory_kg" in payload
