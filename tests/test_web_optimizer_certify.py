from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

from flask import Flask
import pytest

from simulator.fidelity_vocabulary import EvidenceClass
from simulator.optimize.evaluate import RunReference, ScoredResult
from simulator.optimize.evalspec import EvalSpec, cache_key, current_code_version
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.results_store import ResultStore
from web import routes as web_routes


def _base_spec(**overrides: object) -> EvalSpec:
    data = {
        "recipe_id": "recipe-certify",
        "feedstock_recipe_digest": "feedstock-recipe-digest",
        "feedstock_id": "lunar_mare_low_ti",
        "profile_id": "lunar-mare-low-ti-objectives-v1",
        "fidelity": "fast",
        "code_version": current_code_version(),
        "data_digests": {
            "setpoints": "setpoints-digest",
            "feedstocks": "feedstock-digest",
            "materials": "materials-digest",
            "vapor_pressures": "vapor-digest",
            "species_catalog": "species-catalog-digest",
            "profile": "profile-digest",
        },
        "chemistry_kernel": {
            "engine": "builtin",
            "allow_builtin_fallback": False,
            "pressure_Pa": 0.001,
        },
        "campaign": "C0",
        "hours": 24,
        "mass_kg": 1000.0,
        "additives_kg": {},
        "track": "pyrolysis",
        "backend_name": "cached-real",
        "runtime_campaign_overrides": {"C0": {"hold_time_h": 1.0}},
    }
    data.update(overrides)
    return EvalSpec(**data)


def _margin() -> GateMargin:
    return GateMargin(
        gate="delivered_stream_purity",
        feasible=True,
        margin=0.25,
        threshold=ThresholdSpec(
            id="gate",
            value=0.95,
            units="fraction",
            source="profile",
            source_ref="test",
        ),
        observed=0.98,
        detail="test",
    )


def _scored(spec: EvalSpec, *, candidate_id: str = "candidate-certify") -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue("oxygen_kg", "maximize", 12.0, "kg", ordinal=0),
                ObjectiveValue("energy_kWh", "minimize", 2.0, "kWh", ordinal=1),
            )
        ),
        feasibility_margins={"delivered_stream_purity": _margin()},
        failing_gates=(),
        run_reference=RunReference(
            status="ok",
            backend_name="alphamelts",
            backend_status="ok",
            backend_authoritative=True,
        ),
        notes=("stored",),
    )


class _FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None

    def poll(self) -> int | None:
        return self.returncode


class _FakePopenFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.processes: list[_FakeProcess] = []

    def __call__(self, cmd, *, cwd=None, stdout=None, stderr=None, env=None):
        process = _FakeProcess(pid=6000 + len(self.processes))
        self.processes.append(process)
        self.calls.append({"cmd": list(cmd), "cwd": cwd, "env": dict(env or {})})
        return process


@pytest.fixture
def client(tmp_path):
    from simulator.optimize import job_runner as optimizer_job_runner

    optimizer_job_runner.reset_runner_cache()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["OPTIMIZER_RUNS_DIR"] = str(tmp_path / "runs")
    app.register_blueprint(web_routes.bp)
    yield app.test_client()
    optimizer_job_runner.reset_runner_cache()


def _seed_certify_fixture(client) -> tuple[str, str]:
    runs_dir = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    run_dir = runs_dir / "run-certify"
    run_dir.mkdir(parents=True)
    spec = _base_spec()
    store = ResultStore(run_dir / "cache.sqlite")
    scored = _scored(spec)
    store.store(scored.eval_spec, scored, created_at="2026-06-13T00:00:00Z")
    key = cache_key(spec)
    with sqlite3.connect(run_dir / "cache.sqlite") as conn:
        conn.execute(
            "UPDATE results SET run_reference = ? WHERE cache_key = ?",
            (
                json.dumps(
                    {
                        "status": "ok",
                        "cache_state": "cached_interpolated",
                        "evidence_class": EvidenceClass.MELTS.value,
                        "backend_name": "alphamelts",
                        "backend_status": "ok",
                        "backend_authoritative": True,
                        "product_summary": {},
                    }
                ),
                key,
            ),
        )
    return "run-certify", key


def test_optimizer_certify_route_spawns_live_fill_cli(client) -> None:
    popen = _FakePopenFactory()
    client.application.config["OPTIMIZER_JOB_POPEN_FACTORY"] = popen
    run_id, key = _seed_certify_fixture(client)

    response = client.post(
        "/api/optimizer/certify",
        json={
            "run_id": run_id,
            "cache_key": key,
            "feedstock_id": "lunar_mare_low_ti",
            "profile_id": "lunar-mare-low-ti-objectives-v1",
            "fidelity": "fast",
        },
    )

    assert response.status_code == 202
    job = response.get_json()["job"]
    assert job["certify"] is True
    assert job["source_store_path"].endswith("cache.sqlite")
    assert job["certify_cache_key"] == key
    assert len(popen.calls) == 1
    cmd = popen.calls[0]["cmd"]
    assert cmd[1:3] == ["-m", "simulator.optimize"]
    assert "--certify" in cmd
    assert cmd[cmd.index("--source-store") + 1].endswith("cache.sqlite")
    assert cmd[cmd.index("--cache-key") + 1] == key
    assert cmd[cmd.index("--fidelity") + 1] == "fast"
    assert "--strategy" not in cmd


def test_web_routes_do_not_import_or_call_evaluate_inline() -> None:
    source = Path(web_routes.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "evaluate":
                raise AssertionError("web/routes.py calls evaluate() inline")
            if isinstance(func, ast.Attribute) and func.attr == "evaluate":
                raise AssertionError("web/routes.py calls evaluate() inline")
    assert "from simulator.optimize.evaluate import" not in source
    assert "import simulator.optimize.evaluate" not in source
