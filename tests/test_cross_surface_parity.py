from __future__ import annotations

from dataclasses import dataclass, replace
import json
import shlex
from typing import Any

import app as app_module
import simulator.runner as runner_module
import simulator.session_cli as session_cli_module
import web.events as web_events
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.runner import PyrolysisRun
from simulator.session_cli import SessionScriptRunner


FEEDSTOCK = "lunar_mare_low_ti"
MASS_KG = 1000.0
CAMPAIGN = "C0"
HOURS = 20
BACKEND = "stub"
TRACK = "pyrolysis"
LEDGER_TOLERANCE_MOL = 1e-9
MASS_BALANCE_TOLERANCE_PCT = 5e-12

# Adapted from /tmp/ae_parity_harness.py and /tmp/ae_session_recipe.txt.
# C0/C0B force the first PATH_AB decision early; C2/C3 shortening keeps this
# suite guard fast while still reaching O2 and vapor-product ledger accounts.
# NB: the default PATH_AB is now A_staged (S2a4), so the run goes via
# C2A_STAGED. C2A_staged is NOT in the max_hours overrides below — it cannot be
# (its max_hold_hr must equal sum(stage duration_h), campaigns.py, so a
# max_hours=1 override would raise) — so the staged path consumes more of the
# HOURS horizon and reaches C4 but not C6 here. That shortens the expected
# campaign/decision sequence vs the old continuous default; 0.5.10 C6 gate
# unification now records C6_PROCEED on all three surfaces.
SETPOINT_OVERRIDES = {
    "C0": {"max_hours": 1.0, "ramp_rate": 2000.0},
    "C0B": {"max_hours": 1.0, "ramp_rate": 2000.0},
    "C2A": {"max_hours": 1.0, "ramp_rate": 2000.0},
    "C2B": {"max_hours": 1.0, "ramp_rate": 2000.0},
    "C3_K": {"max_hours": 1.0, "ramp_rate": 2000.0},
    "C3_NA": {"max_hours": 1.0, "ramp_rate": 2000.0},
    "C4": {"ramp_rate": 2000.0},
    "C5": {"ramp_rate": 2000.0},
}

EXPECTED_CORE_ACCOUNTS = {
    "process.cleaned_melt",
    "process.condensation_train",
    "terminal.oxygen_melt_offgas_stored",
}

OPTIONAL_PRODUCT_ACCOUNTS = {
    "terminal.offgas",
    "process.metal_phase",
    "terminal.oxygen_mre_anode_stored",
}


@dataclass(frozen=True)
class SurfaceResult:
    name: str
    ledger: dict[str, dict[str, float]]
    decisions: list[tuple[str, str]]
    summaries: list[dict[str, Any]]
    campaign_event_count: int = 0
    final_hour: int | None = None


class StopAfterStep(Exception):
    pass


def test_batch_cli_web_mol_ledger_parity(monkeypatch):
    _install_alpha_fallback_fixture(monkeypatch)
    batch = _run_batch()
    cli = _run_cli_session()
    web = _run_web_session(monkeypatch)
    surfaces = [batch, cli, web]

    assert {surface.final_hour for surface in surfaces} == {HOURS}
    assert batch.decisions == cli.decisions == web.decisions
    assert batch.decisions == [
        ("PATH_AB", "A_staged"),
        ("BRANCH_ONE_TWO", "two"),
        ("C6_PROCEED", "yes"),
    ]

    campaigns = _campaigns(batch.summaries)
    assert campaigns == ["C0", "C0B", "C2A_STAGED", "C3_NA", "C4", "C6"]
    assert _campaign_transition_exercised(batch.summaries)
    assert web.campaign_event_count >= 1

    assert EXPECTED_CORE_ACCOUNTS <= set(batch.ledger)
    for account in OPTIONAL_PRODUCT_ACCOUNTS:
        assert len({account in surface.ledger for surface in surfaces}) == 1
    assert {"Fe", "Cr", "Si"} <= set(
        batch.ledger["process.condensation_train"]
    )
    assert {"Na", "K", "Mg"} <= set(batch.ledger["terminal.offgas"])
    assert {"Al2O3", "CaO", "FeO", "MgO", "SiO2"} <= set(
        batch.ledger["process.cleaned_melt"]
    )
    assert batch.ledger["terminal.oxygen_melt_offgas_stored"].keys() == {"O2"}
    assert batch.ledger.get("terminal.oxygen_mre_anode_stored", {}) == {}

    comparisons = [
        _compare_ledgers(batch, cli),
        _compare_ledgers(batch, web),
        _compare_ledgers(cli, web),
    ]
    assert max(comparisons) <= LEDGER_TOLERANCE_MOL
    assert _max_mass_balance_pct(surfaces) <= MASS_BALANCE_TOLERANCE_PCT


def _install_alpha_fallback_fixture(monkeypatch) -> None:
    """Opt this Cr/Mn-sampling parity fixture into the prototype alpha path."""

    def with_alpha_fallback(setpoints):
        payload = dict(setpoints)
        kernel_config = dict(payload.get("chemistry_kernel", {}) or {})
        # bbf0134 made missing measured Cr/Mn alphas fail loud by default.
        # Pending t-194 grounded values, all three surfaces explicitly use the
        # same alpha=1.0 prototype fallback so this remains a parity test.
        kernel_config["allow_unmeasured_alpha_fallback"] = True
        payload["chemistry_kernel"] = kernel_config
        return payload

    for module in (runner_module, session_cli_module):
        original_load_config_bundle = module.load_config_bundle

        def load_config_bundle_with_alpha_fallback(
            *args, _load=original_load_config_bundle, **kwargs
        ):
            bundle = _load(*args, **kwargs)
            return replace(bundle, setpoints=with_alpha_fallback(bundle.setpoints))

        monkeypatch.setattr(
            module,
            "load_config_bundle",
            load_config_bundle_with_alpha_fallback,
        )

    original_load_yaml = web_events._load_yaml

    def load_yaml_with_alpha_fallback(filename):
        payload = original_load_yaml(filename)
        if filename == "setpoints.yaml":
            return with_alpha_fallback(payload)
        return payload

    monkeypatch.setattr(web_events, "_load_yaml", load_yaml_with_alpha_fallback)


def _run_batch() -> SurfaceResult:
    run = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        campaign=CAMPAIGN,
        hours=HOURS,
        mass_kg=MASS_KG,
        backend_name=BACKEND,
        track=TRACK,
        setpoints_overrides=SETPOINT_OVERRIDES,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "parity-fixture",
        },
    )
    document = run.run()
    assert document["status"] == "ok"
    return SurfaceResult(
        name="batch",
        ledger=document["final_state"],
        decisions=[
            (event["decision_type"], event["choice"])
            for event in document["shadow_trace"]
            if event.get("event") == "operator_decision"
        ],
        summaries=document["per_hour_summary"],
        final_hour=document["run_metadata"]["hours_completed"],
    )


def _run_cli_session() -> SurfaceResult:
    runner = SessionScriptRunner()
    summaries: list[dict[str, Any]] = []
    decisions: list[tuple[str, str]] = []

    def execute(command: str) -> dict[str, Any]:
        return runner.execute(shlex.split(command), command)

    setpoint_args = " ".join(
        f"--setpoint={campaign}.{field}={value:g}"
        for campaign, fields in SETPOINT_OVERRIDES.items()
        for field, value in fields.items()
    )
    execute(
        f"start --feedstock={FEEDSTOCK} --campaign={CAMPAIGN} "
        f"--mass-kg={MASS_KG:g} --backend={BACKEND} --track={TRACK} "
        f"{setpoint_args}"
    )

    remaining = HOURS
    guard = 0
    while remaining > 0 and guard < 1000:
        guard += 1
        pending = runner.session.pending_decision()
        if pending is not None:
            choice = pending.recommendation or (
                pending.options[0] if pending.options else ""
            )
            decisions.append((pending.decision_type.name, choice))
            execute(f"decide {choice}")
            continue
        if runner.session.is_complete():
            break

        frame = execute(f"advance {remaining}")
        steps = frame.get("steps", [])
        summaries.extend(steps)
        consumed = len(steps)
        if consumed == 0:
            break
        remaining -= consumed

    assert guard < 1000
    return SurfaceResult(
        name="cli",
        ledger=_ledger_from_simulator(runner.session.simulator),
        decisions=decisions,
        summaries=summaries,
        final_hour=runner.session.simulator.melt.hour,
    )


def _run_web_session(monkeypatch) -> SurfaceResult:
    captured_tasks = _install_stepwise_web(monkeypatch)
    app = app_module.create_app()
    http_client = app.test_client()
    assert http_client.get("/").status_code == 200
    client = app_module.socketio.test_client(
        app,
        flask_test_client=http_client,
    )
    assert client.is_connected()
    client.get_received()

    summaries: list[dict[str, Any]] = []
    decisions: list[tuple[str, str]] = []
    campaign_event_count = 0

    def drain() -> None:
        nonlocal campaign_event_count
        for received in client.get_received():
            name = received.get("name")
            payload = (received.get("args") or [None])[0]
            if name == "per_hour_summary":
                summaries.append(payload)
            elif name == "campaign_complete_summary":
                campaign_event_count += 1

    try:
        client.emit(
            "start_simulation",
            {
                "backend": BACKEND,
                "feedstock": FEEDSTOCK,
                "mass_kg": MASS_KG,
                "speed": 1,
                "track": TRACK,
            },
        )
        drain()

        for campaign, fields in SETPOINT_OVERRIDES.items():
            for field, value in fields.items():
                client.emit(
                    "adjust_parameter",
                    {
                        "param": "campaign_override",
                        "campaign": campaign,
                        "field": field,
                        "value": value,
                    },
                )
                drain()

        sid = next(iter(web_events._simulations))

        def session_of():
            return web_events._simulations[sid]["session"]

        guard = 0
        while session_of().simulator.melt.hour < HOURS and guard < 1000:
            guard += 1
            try:
                captured_tasks[-1]()
            except StopAfterStep:
                pass
            drain()

            if session_of().simulator.melt.hour >= HOURS:
                break
            pending = session_of().pending_decision()
            if pending is not None:
                choice = pending.recommendation or (
                    pending.options[0] if pending.options else ""
                )
                decisions.append((pending.decision_type.name, choice))
                client.emit("make_decision", {"choice": choice})
                drain()

        assert guard < 1000
        session = session_of()
        return SurfaceResult(
            name="web",
            ledger=_ledger_from_simulator(session.simulator),
            decisions=decisions,
            summaries=summaries,
            campaign_event_count=campaign_event_count,
            final_hour=session.simulator.melt.hour,
        )
    finally:
        client.disconnect()
        for sid in list(web_events._simulations):
            web_events._clear_simulation_state(sid)


def _install_stepwise_web(monkeypatch) -> list:
    captured_tasks = []

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append(target)
        return {"captured_task": len(captured_tasks)}

    def stop_after_step(seconds=0):
        if seconds and seconds > 0:
            raise StopAfterStep()

    monkeypatch.setattr(web_events, "_safe_log", lambda _message: None)
    monkeypatch.setattr(web_events, "_get_backend", force_internal_analytical_backend)
    monkeypatch.setattr(app_module.socketio, "sleep", stop_after_step)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    return captured_tasks


def _ledger_from_simulator(sim) -> dict[str, dict[str, float]]:
    balances = sim.atom_ledger.mol_by_account()
    return {
        account: {
            species: float(mol)
            for species, mol in sorted(species_mol.items())
            if abs(float(mol)) > 0.0
        }
        for account, species_mol in sorted(balances.items())
    }


def _compare_ledgers(left: SurfaceResult, right: SurfaceResult) -> float:
    assert _canonical_ledger_bytes(left.ledger) == _canonical_ledger_bytes(
        right.ledger
    )
    assert set(left.ledger) == set(right.ledger)

    max_abs_diff = 0.0
    for account in sorted(set(left.ledger) | set(right.ledger)):
        left_species = left.ledger.get(account, {})
        right_species = right.ledger.get(account, {})
        assert set(left_species) == set(right_species)
        for species in sorted(set(left_species) | set(right_species)):
            diff = abs(left_species.get(species, 0.0) - right_species.get(species, 0.0))
            max_abs_diff = max(max_abs_diff, diff)
    return max_abs_diff


def _canonical_ledger_bytes(ledger: dict[str, dict[str, float]]) -> bytes:
    return json.dumps(
        ledger,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _campaigns(summaries: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(summary["campaign"] for summary in summaries))


def _campaign_transition_exercised(summaries: list[dict[str, Any]]) -> bool:
    return len(set(summary["campaign"] for summary in summaries)) > 1


def _max_mass_balance_pct(surfaces: list[SurfaceResult]) -> float:
    return max(
        abs(float(summary["mass_balance_pct"]))
        for surface in surfaces
        for summary in surface.summaries
    )
