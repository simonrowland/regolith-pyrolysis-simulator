from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from flask import Flask
import pytest

from simulator.optimize.evalspec import EvalSpec, cache_key, current_code_version
from simulator.optimize.evaluate import RunReference, ScoredResult
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.results_store import ResultStore
from web import routes as web_routes


def _base_spec(**overrides: object) -> EvalSpec:
    data = {
        "recipe_id": "recipe-id",
        "feedstock_recipe_digest": "feedstock-recipe-digest",
        "feedstock_id": "lunar_mare_low_ti",
        "profile_id": "oxygen-yield-v1",
        "fidelity": "fast",
        "code_version": current_code_version(),
        "data_digests": {
            "setpoints": "setpoints-digest",
            "feedstocks": "feedstock-digest",
            "vapor_pressures": "vapor-digest",
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
        "additives_kg": {"CaO": 1.5},
        "track": "pyrolysis",
        "backend_name": "stub",
        "runtime_campaign_overrides": {"C0": {"hold_time_h": 1.0}},
    }
    data.update(overrides)
    return EvalSpec(**data)


def _margin(
    gate: str = "delivered_stream_purity",
    feasible: bool = True,
    *,
    margin: float | None = None,
    observed: float | None = None,
) -> GateMargin:
    return GateMargin(
        gate=gate,
        feasible=feasible,
        margin=margin if margin is not None else (0.25 if feasible else -0.25),
        threshold=ThresholdSpec(
            id=f"{gate}-threshold",
            value=0.95,
            units="fraction",
            source="profile",
            source_ref="test profile",
        ),
        observed=observed if observed is not None else (0.98 if feasible else 0.90),
        detail="test margin",
    )


def _objectives(oxygen: float = 10.0, energy: float = 2.0) -> ObjectiveVector:
    return ObjectiveVector(
        (
            ObjectiveValue("oxygen_kg", "maximize", oxygen, "kg", ordinal=0),
            ObjectiveValue("energy_kWh", "minimize", energy, "kWh", ordinal=1),
        )
    )


def _scored(
    spec: EvalSpec,
    *,
    candidate_id: str = "candidate-a",
    oxygen: float = 10.0,
    energy: float = 2.0,
    objectives: ObjectiveVector | None = None,
    margins: Mapping[str, GateMargin] | None = None,
) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=objectives or _objectives(oxygen, energy),
        feasibility_margins=margins or {"delivered_stream_purity": _margin()},
        failing_gates=(),
        run_reference=RunReference(
            status="ok",
            trace={"hours": [{"hour": 1, "oxygen_kg": oxygen}]},
            product_summary={"oxygen_kg": oxygen},
        ),
        notes=("stored",),
    )


@pytest.fixture
def client(tmp_path):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["OPTIMIZER_RUNS_DIR"] = str(tmp_path / "runs")
    app.register_blueprint(web_routes.bp)
    return app.test_client()


def test_optimizer_reader_returns_fixture_db_metadata(client, tmp_path) -> None:
    runs_dir = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    run_dir = runs_dir / "run-a"
    run_dir.mkdir(parents=True)
    (run_dir / "leaderboard.csv").write_text(
        "rank,candidate_id\n1,candidate-b\n",
        encoding="utf-8",
    )

    spec_a = _base_spec(recipe_id="recipe-a")
    spec_b = replace(spec_a, recipe_id="recipe-b")
    store = ResultStore(run_dir / "cache.sqlite")
    store.store(
        spec_a,
        _scored(spec_a, candidate_id="candidate-a", oxygen=10.0, energy=2.0),
        created_at="2026-06-01T00:00:00Z",
    )
    store.store(
        spec_b,
        _scored(spec_b, candidate_id="candidate-b", oxygen=12.0, energy=4.0),
        created_at="2026-06-02T00:00:00Z",
    )

    response = client.get("/api/optimizer/runs")
    assert response.status_code == 200
    payload = response.get_json()
    run = payload["runs"][0]

    assert run["id"] == "run-a"
    assert run["result_count"] == 2
    assert run["selectors"] == [
        {
            "feedstock_id": spec_a.feedstock_id,
            "profile_id": spec_a.profile_id,
            "fidelity": spec_a.fidelity,
            "count": 2,
        }
    ]
    assert {artifact["name"] for artifact in run["artifacts"]} >= {
        "cache.sqlite",
        "leaderboard.csv",
    }
    assert run["latest_result"]["candidate_id"] == "candidate-b"
    assert run["latest_result"]["objectives"]["oxygen_kg"] == 12.0
    assert (
        run["latest_result"]["run_reference"]["product_summary"]["oxygen_kg"]
        == 12.0
    )

    leaderboard = client.get(
        "/api/optimizer/leaderboard"
        "?feedstock_id=lunar_mare_low_ti&objective=oxygen_kg&limit=1"
    )
    assert leaderboard.status_code == 200
    board_payload = leaderboard.get_json()
    assert board_payload["objective_metric"] == "oxygen_kg"
    assert [entry["candidate_id"] for entry in board_payload["entries"]] == [
        "candidate-b"
    ]
    assert board_payload["entries"][0]["objective_value"] == 12.0


def test_optimizer_feedstock_profile_scanner(client, tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    profiles_dir = data_dir / "optimize_profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "oxygen.yaml").write_text(
        "\n".join(
            (
                "profile_id: oxygen-yield-v1",
                "feedstock: lunar_mare_low_ti",
                "objectives:",
                "  - {metric: oxygen_kg, sense: maximize, units: kg}",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(web_routes, "DATA_DIR", data_dir)

    response = client.get("/api/optimizer/feedstock-profiles")
    assert response.status_code == 200
    payload = response.get_json()

    assert payload["feedstocks"] == {
        "lunar_mare_low_ti": ["oxygen-yield-v1"],
    }
    assert payload["profiles"] == [
        {
            "profile_id": "oxygen-yield-v1",
            "feedstock_id": "lunar_mare_low_ti",
            "relative_path": "optimize_profiles/oxygen.yaml",
            "objective_metrics": ["oxygen_kg"],
        }
    ]


def test_web_routes_do_not_import_evaluate_or_worker_runtime() -> None:
    source = Path(web_routes.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            forbidden.extend(
                alias.name
                for alias in node.names
                if alias.name == "simulator.optimize.evaluate"
            )
        if isinstance(node, ast.ImportFrom):
            if node.module == "simulator.optimize.evaluate":
                forbidden.append(node.module)
            if node.module and node.module.endswith("worker_runtime"):
                forbidden.append(node.module)
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "evaluate":
                forbidden.append("evaluate()")
            if isinstance(func, ast.Attribute) and func.attr == "evaluate":
                forbidden.append("*.evaluate()")

    assert forbidden == []
    assert "worker_runtime" not in source
