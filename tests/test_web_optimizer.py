from __future__ import annotations

import ast
import json
import math
import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from flask import Flask
import pytest
import yaml

from simulator import mre_ladder
from simulator.backends import backend_resolution_status
from simulator.condensation import (
    BOLTZMANN_CONSTANT_J_K,
    CONTINUUM_BUFFER_KN,
    DEFAULT_PIPE_DIAMETER_M,
    N2_COLLISION_DIAMETER_M,
)
from simulator.melt_backend.base import StubBackend
from simulator.optimize.evalspec import EvalSpec, cache_key, current_code_version
from simulator.optimize.evaluate import RunReference, ScoredResult, _build_eval_inputs
from simulator.optimize import job_runner as optimizer_job_runner
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.recipe import RecipePatch, RecipeSchema
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
    product_summary: Mapping[str, object] | None = None,
    trace: Mapping[str, object] | None = None,
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
            trace=trace
            or {"hours": [{"hour": 1, "oxygen_kg": oxygen, "backend_status": "ok"}]},
            product_summary=product_summary or {"oxygen_kg": oxygen},
        ),
        notes=("stored",),
    )


def _product_yield_table(status: str = "closed") -> dict[str, object]:
    return {
        "status": status,
        "inputs": [
            {"kind": "input", "id": "feedstock", "label": "Feedstock", "kg": 1000.0},
            {"kind": "input", "id": "additive:CaO", "label": "CaO", "kg": 1.5},
        ],
        "outputs": [
            {
                "kind": "output",
                "id": "ingots_metals",
                "label": "Ingots/metals",
                "kg": 50.0,
                "yield_pct": 4.992511,
            },
            {
                "kind": "output",
                "id": "glass",
                "label": "Glass",
                "kg": 40.0,
                "yield_pct": 3.994009,
            },
            {
                "kind": "output",
                "id": "oxygen",
                "label": "O2",
                "kg": 20.0,
                "yield_pct": 1.997004,
            },
            {
                "kind": "output",
                "id": "captured_volatiles",
                "label": "Captured volatiles",
                "kg": 5.0,
                "yield_pct": 0.499251,
            },
            {
                "kind": "output",
                "id": "refractory_ceramic_rump",
                "label": "Refractory ceramic/rump",
                "kg": 80.0,
                "yield_pct": 7.988018,
            },
        ],
        "mass_closure": {
            "kind": "mass_closure",
            "label": "Mass closure",
            "mass_in_kg": 1001.5,
            "accountable_mass_out_kg": 1001.5,
            "products_out_kg": 195.0,
            "balance_error_pct": 0.0,
            "tolerance_pct": 5e-12,
            "status": "closed",
        },
        "total_input_kg": 1001.5,
        "products_out_kg": 195.0,
    }


@pytest.fixture
def client(tmp_path):
    optimizer_job_runner.reset_runner_cache()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["OPTIMIZER_RUNS_DIR"] = str(tmp_path / "runs")
    app.register_blueprint(web_routes.bp)
    yield app.test_client()
    optimizer_job_runner.reset_runner_cache()


class _FakeProcess:
    def __init__(self, pid: int, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


class _FakePopenFactory:
    def __init__(self, *, output: bytes = b"") -> None:
        self.output = output
        self.calls: list[dict[str, object]] = []
        self.processes: list[_FakeProcess] = []

    def __call__(self, cmd, *, cwd=None, stdout=None, stderr=None, env=None):
        process = _FakeProcess(pid=5000 + len(self.processes))
        self.processes.append(process)
        self.calls.append(
            {
                "cmd": list(cmd),
                "cwd": cwd,
                "stderr": stderr,
                "env": dict(env or {}),
            }
        )
        if stdout is not None and self.output:
            stdout.write(self.output)
            stdout.flush()
        return process


def _write_minimal_result_table(job_dir: Path) -> None:
    with sqlite3.connect(job_dir / "cache.sqlite") as conn:
        conn.execute("CREATE TABLE results (id INTEGER)")
        conn.execute("INSERT INTO results VALUES (1)")


def _job_request(
    *,
    feedstock_id: str = "lunar_mare_low_ti",
    profile_id: str = "lunar-mare-low-ti-objectives-v1",
    strategy: str = "random",
    fidelity: str = "stub",
    budget: int = 2,
    parallel: int = 1,
    seed: int = 0,
) -> optimizer_job_runner.OptimizerJobRequest:
    return optimizer_job_runner.OptimizerJobRequest(
        feedstock_id=feedstock_id,
        profile_id=profile_id,
        strategy=strategy,
        fidelity=fidelity,
        budget=budget,
        parallel=parallel,
        seed=seed,
        profile_arg=str(Path("data/optimize_profiles/lunar_mare_low_ti.yaml")),
    )


def test_mre_preset_catalog_route_returns_shared_ladder(client) -> None:
    response = client.get("/api/mre-preset-catalog")

    assert response.status_code == 200
    payload = response.get_json()
    presets = payload["presets"]
    source = mre_ladder.preset_catalog(web_routes._load_yaml("setpoints.yaml"))
    canonical_fields = {
        "id",
        "label",
        "target_oxide",
        "c5_enabled",
        "mre_target_species",
        "mre_max_voltage_V",
        "enabled",
        "disabled_reason",
        "legacy",
    }
    ui_derived_fields = {"included_species", "included_species_label"}

    def canonical(preset: Mapping[str, object]) -> dict[str, object | None]:
        return {
            field: preset.get(field, "") if field == "disabled_reason"
            else preset.get(field)
            for field in sorted(canonical_fields)
        }

    assert [canonical(preset) for preset in presets] == [
        canonical(preset)
        for preset in source
    ]
    for preset in presets:
        assert set(preset) <= canonical_fields | ui_derived_fields

    by_target = {preset.get("mre_target_species"): preset for preset in presets}
    assert by_target[""]["c5_enabled"] is False
    assert by_target["SiO2"]["mre_max_voltage_V"] == pytest.approx(1.4)
    assert by_target["SiO2"]["included_species"] == ["Fe", "Cr", "Mn", "Si"]
    assert by_target["Na2O"]["enabled"] is False
    assert "pre-depleted" in by_target["Na2O"]["disabled_reason"]
    assert by_target["K2O"]["enabled"] is False


def test_mre_preset_catalog_fragment_is_golden(client) -> None:
    response = client.get("/partials/mre-preset-catalog")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'value="off"' in html
    assert 'data-c5-enabled="false"' in html
    assert 'value="target:SiO2"' in html
    assert 'data-max-voltage="1.4"' in html
    assert 'data-included-species="Fe, Cr, Mn, Si"' in html
    assert "Na2O" in html
    assert "disabled" in html
    assert "pre-depleted by C3" in html


def test_simulator_config_renders_mre_default_off_backend_badge_and_levers(
    client,
) -> None:
    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="mre-enabled"' in html
    assert 'id="mre-enabled" checked' not in html
    assert 'id="mre-fields" hidden' in html
    assert 'data-catalog-url="/api/mre-preset-catalog"' in html
    assert 'id="mre-max-voltage" value="0" readonly' in html
    assert 'id="status-backend"' in html
    assert 'id="lever-po2-mbar"' in html
    assert 'id="lever-pn2-mbar"' in html
    assert 'id="knudsen-indicator"' in html
    assert "No live wall-temperature offset path exists yet" in html
    assert "pN2 and wall-temperature controls are display-only" in html


def test_knudsen_config_exposes_condensation_model_constants(client) -> None:
    response = client.get("/api/knudsen-config")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["boltzmann_constant_j_k"] == BOLTZMANN_CONSTANT_J_K
    assert payload["characteristic_length_m"] == DEFAULT_PIPE_DIAMETER_M
    assert payload["n2_collision_diameter_m"] == N2_COLLISION_DIAMETER_M
    assert payload["continuum_buffer_kn"] == CONTINUUM_BUFFER_KN

    html = client.get("/").get_data(as_text=True)
    assert (
        f'data-characteristic-length-m="{DEFAULT_PIPE_DIAMETER_M}"'
        in html
    )
    assert f'data-continuum-buffer-kn="{CONTINUUM_BUFFER_KN}"' in html


def test_cli_web_evalspec_parity_for_mre_preset(client, tmp_path) -> None:
    catalog = client.get("/api/mre-preset-catalog").get_json()["presets"]
    si_preset = next(
        preset for preset in catalog
        if preset["mre_target_species"] == "SiO2"
    )
    profile = {
        "profile_id": "web-parity-mre-policy",
        "profile_schema_version": "profile-schema-v1",
        "feedstock": "lunar_mare_low_ti",
        "objectives": [
            {
                "metric": "oxygen_kg",
                "sense": "maximize",
                "units": "kg",
                "weight": 1.0,
                "rationale": "web parity test objective",
            }
        ],
        "constraints": {"gates": ["delivered_stream_purity"]},
        "seed_recipes": [{"id": "seed", "source_campaign": "C0", "patch": {}}],
        "run": {
            "campaign": "C5",
            "hours": 1,
            "mass_kg": 1000.0,
            "backend_name": "stub",
            "c5_enabled": si_preset["c5_enabled"],
            "mre_max_voltage_V": si_preset["mre_max_voltage_V"],
            "mre_target_species": si_preset["mre_target_species"],
        },
        "fidelities": {"stub": {"backend_name": "stub"}},
    }
    spec, run_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        RecipeSchema(),
    )
    assert run_config.c5_enabled is True
    assert run_config.mre_target_species == "SiO2"

    runs_dir = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    run_dir = runs_dir / "run-web-parity"
    run_dir.mkdir(parents=True)
    store = ResultStore(run_dir / "cache.sqlite")
    store.store(
        spec,
        _scored(
            spec,
            candidate_id="candidate-web-parity",
            trace={"backend_status": "ok", "hours": [{"hour": 1}]},
        ),
        created_at="2026-06-02T00:00:00Z",
    )
    key = cache_key(spec)

    leaderboard = client.get(
        "/api/optimizer/leaderboard"
        "?feedstock_id=lunar_mare_low_ti&profile_id=web-parity-mre-policy"
    )
    detail = client.get(f"/optimizer/runs/run-web-parity/results/{key}")
    download = client.get(f"/optimizer/runs/run-web-parity/results/{key}/recipe.yaml")

    assert leaderboard.status_code == 200
    entry = leaderboard.get_json()["entries"][0]
    assert entry["eval_spec"]["c5_enabled"] is True
    assert entry["eval_spec"]["mre_target_species"] == spec.mre_target_species
    assert entry["eval_spec"]["mre_max_voltage_V"] == pytest.approx(
        spec.mre_max_voltage_V
    )
    assert entry["backend"]["backend_status"] == "unavailable"
    assert detail.status_code == 200
    assert "candidate-web-parity" in detail.get_data(as_text=True)
    assert download.status_code == 200
    payload = yaml.safe_load(download.get_data(as_text=True))
    assert payload["eval_spec"]["c5_enabled"] is True
    assert payload["eval_spec"]["mre_target_species"] == spec.mre_target_species
    assert payload["eval_spec"]["mre_max_voltage_V"] == pytest.approx(
        spec.mre_max_voltage_V
    )


def test_optimizer_reader_returns_fixture_db_metadata(client, tmp_path) -> None:
    runs_dir = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    run_dir = runs_dir / "run-a"
    run_dir.mkdir(parents=True)
    (run_dir / "leaderboard.csv").write_text(
        "rank,candidate_id\n1,candidate-b\n",
        encoding="utf-8",
    )

    spec_a = _base_spec(recipe_id="recipe-a")
    spec_b = replace(
        spec_a,
        recipe_id="recipe-b",
        c5_enabled=True,
        mre_max_voltage_V=1.4,
        mre_target_species="SiO2",
    )
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
    assert run["latest_result"]["eval_spec"]["c5_enabled"] is True
    assert run["latest_result"]["eval_spec"]["mre_max_voltage_V"] == 1.4
    assert run["latest_result"]["eval_spec"]["mre_target_species"] == "SiO2"
    assert run["latest_result"]["backend"]["backend_requested"] == "stub"
    assert run["latest_result"]["backend"]["backend_active"] == "StubBackend"
    assert run["latest_result"]["backend"]["backend_status"] == "unavailable"
    assert run["latest_result"]["backend"]["backend_authoritative"] is False

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
    assert board_payload["entries"][0]["eval_spec"]["c5_enabled"] is True
    assert (
        board_payload["entries"][0]["eval_spec"]["mre_target_species"]
        == "SiO2"
    )
    assert board_payload["entries"][0]["backend"] == run["latest_result"]["backend"]


def test_optimizer_reader_discovers_completed_job_result_dirs(client) -> None:
    runs_dir = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    job_id = "job-complete-001"
    run_id = f"jobs/{job_id}"
    job_dir = runs_dir / "jobs" / job_id
    job_dir.mkdir(parents=True)

    spec = _base_spec(
        recipe_id="job-recipe",
        profile_id="job-profile",
        fidelity="high",
    )
    store = ResultStore(job_dir / "cache.sqlite")
    store.store(
        spec,
        _scored(spec, candidate_id="candidate-job", oxygen=14.0, energy=3.0),
        created_at="2026-06-09T00:00:00Z",
    )
    key = cache_key(spec)

    runs = client.get("/api/optimizer/runs")
    assert runs.status_code == 200
    run = runs.get_json()["runs"][0]
    assert run["id"] == run_id
    assert run["latest_result"]["run_id"] == run_id

    table = client.get(
        "/partials/optimizer-table"
        "?feedstock_id=lunar_mare_low_ti&profile_id=job-profile&objective=oxygen_kg"
    )
    assert table.status_code == 200
    table_html = table.get_data(as_text=True)
    assert "job-profile" in table_html
    assert f'href="/optimizer/runs/{run_id}/results/{key}"' in table_html

    detail = client.get(f"/optimizer/runs/{run_id}/results/{key}")
    assert detail.status_code == 200
    assert "candidate-job" in detail.get_data(as_text=True)


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
                "constraints:",
                "  gates: [mass_balance]",
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
            "constraints_gates": ["mass_balance"],
        }
    ]


def test_optimizer_job_submit_spawns_cli_under_runs_jobs(client) -> None:
    popen = _FakePopenFactory()
    client.application.config["OPTIMIZER_JOB_POPEN_FACTORY"] = popen
    client.application.config["OPTIMIZER_JOB_BUDGET_CAP"] = 3

    response = client.post(
        "/api/optimizer/jobs",
        json={
            "feedstock_id": "lunar_mare_low_ti",
            "profile_id": "lunar-mare-low-ti-objectives-v1",
            "strategy": "random",
            "fidelity": "stub",
            "budget": 3,
            "parallel": 1,
            "seed": 11,
        },
    )

    assert response.status_code == 202
    job = response.get_json()["job"]
    assert job["status"] == "RUNNING"
    assert job["feedstock_id"] == "lunar_mare_low_ti"
    assert job["profile_id"] == "lunar-mare-low-ti-objectives-v1"
    assert job["eta"] is None
    assert job["version_badge"]["status"] == "current"

    assert len(popen.calls) == 1
    cmd = popen.calls[0]["cmd"]
    assert cmd[1:3] == ["-m", "simulator.optimize"]
    assert cmd[cmd.index("--feedstock") + 1] == "lunar_mare_low_ti"
    assert cmd[cmd.index("--strategy") + 1] == "random"
    assert cmd[cmd.index("--fidelity") + 1] == "stub"
    assert cmd[cmd.index("--budget") + 1] == "3"
    assert cmd[cmd.index("--parallel") + 1] == "1"
    assert cmd[cmd.index("--seed") + 1] == "11"
    assert cmd[cmd.index("--profile") + 1].endswith(
        "data/optimize_profiles/lunar_mare_low_ti.yaml"
    )
    out_dir = Path(cmd[cmd.index("--out") + 1])
    assert out_dir.parent == Path(client.application.config["OPTIMIZER_RUNS_DIR"]) / "jobs"
    assert Path(job["log_path"]).name == "job.log"
    meta = yaml.safe_load((out_dir / ".job_meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "RUNNING"
    assert meta["pid"] == popen.processes[0].pid


def test_optimizer_jobs_partial_keeps_polling_attrs_after_outer_swap(client) -> None:
    response = client.get("/partials/optimizer-jobs")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="optimizer-jobs"' in html
    assert 'hx-get="/partials/optimizer-jobs"' in html
    assert 'hx-trigger="load, every 5s"' in html
    assert 'hx-swap="outerHTML"' in html


def test_optimizer_job_submit_rejects_unknown_profile_before_spawn(client) -> None:
    popen = _FakePopenFactory()
    client.application.config["OPTIMIZER_JOB_POPEN_FACTORY"] = popen

    response = client.post(
        "/api/optimizer/jobs",
        json={
            "feedstock_id": "lunar_mare_low_ti",
            "profile_id": "unknown-profile",
            "strategy": "random",
            "fidelity": "stub",
            "budget": 1,
            "parallel": 1,
            "seed": 0,
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "unknown profile_id: unknown-profile"
    assert popen.calls == []


@pytest.mark.parametrize(
    ("override", "expected_error"),
    [
        ({"budget": "many"}, "budget must be a positive integer"),
        ({"budget": 0}, "budget must be a positive integer"),
        ({"budget": 6}, "budget must be <= 5"),
        ({"parallel": "wide"}, "parallel must be a positive integer"),
        ({"parallel": 0}, "parallel must be a positive integer"),
        ({"parallel": 3}, "parallel must be <= 2"),
        ({"seed": "late"}, "seed must be a non-negative integer"),
        ({"seed": -1}, "seed must be a non-negative integer"),
        ({"strategy": "magic"}, "unknown strategy: magic"),
        ({"fidelity": "oracle"}, "unknown fidelity: oracle"),
    ],
)
def test_optimizer_job_submit_rejects_bad_launch_values_before_spawn(
    client,
    override,
    expected_error,
) -> None:
    popen = _FakePopenFactory()
    client.application.config["OPTIMIZER_JOB_POPEN_FACTORY"] = popen
    client.application.config["OPTIMIZER_JOB_BUDGET_CAP"] = 5
    client.application.config["OPTIMIZER_JOB_PARALLEL_CAP"] = 2
    payload = {
        "feedstock_id": "lunar_mare_low_ti",
        "profile_id": "lunar-mare-low-ti-objectives-v1",
        "strategy": "random",
        "fidelity": "stub",
        "budget": 1,
        "parallel": 1,
        "seed": 0,
    }
    payload.update(override)

    response = client.post("/api/optimizer/jobs", json=payload)

    assert response.status_code == 400
    assert response.get_json()["error"] == expected_error
    assert popen.calls == []
    jobs_dir = Path(client.application.config["OPTIMIZER_RUNS_DIR"]) / "jobs"
    assert not jobs_dir.exists() or list(jobs_dir.iterdir()) == []


def test_optimizer_job_exit_zero_without_results_fails_loud(client) -> None:
    popen = _FakePopenFactory(output=b"finished without persisting rows\n")
    client.application.config["OPTIMIZER_JOB_POPEN_FACTORY"] = popen
    created = client.post(
        "/api/optimizer/jobs",
        json={
            "feedstock_id": "lunar_mare_low_ti",
            "profile_id": "lunar-mare-low-ti-objectives-v1",
            "strategy": "random",
            "fidelity": "stub",
            "budget": 1,
            "parallel": 1,
            "seed": 0,
        },
    ).get_json()["job"]
    popen.processes[0].returncode = 0

    detail = client.get(f"/api/optimizer/jobs/{created['job_id']}")

    assert detail.status_code == 200
    job = detail.get_json()["job"]
    assert job["status"] == "FAILED"
    assert "finished without persisting rows" in job["stderr_tail"]
    assert "optimizer exited 0 without stored results" in job["stderr_tail"]
    assert not (Path(created["out_dir"]) / "cache.sqlite").exists()


def test_optimizer_job_nonzero_exit_fails_loud_and_surfaces_stderr(client) -> None:
    popen = _FakePopenFactory(output=b"backend license missing\n")
    client.application.config["OPTIMIZER_JOB_POPEN_FACTORY"] = popen
    created = client.post(
        "/api/optimizer/jobs",
        json={
            "feedstock_id": "lunar_mare_low_ti",
            "profile_id": "lunar-mare-low-ti-objectives-v1",
            "strategy": "random",
            "fidelity": "high",
            "budget": 1,
            "parallel": 1,
            "seed": 0,
        },
    ).get_json()["job"]
    popen.processes[0].returncode = 2

    detail = client.get(f"/api/optimizer/jobs/{created['job_id']}")
    html = client.get(f"/optimizer/jobs/{created['job_id']}").get_data(as_text=True)

    assert detail.status_code == 200
    job = detail.get_json()["job"]
    assert job["status"] == "FAILED"
    assert "backend license missing" in job["stderr_tail"]
    assert "FAILED" in html
    assert "backend license missing" in html


def test_optimizer_job_runner_fifo_queue_and_single_running(tmp_path) -> None:
    popen = _FakePopenFactory()
    moments = [
        datetime(2026, 6, 8, tzinfo=UTC) + timedelta(seconds=offset)
        for offset in range(20)
    ]
    runner = optimizer_job_runner.OptimizerJobRunner(
        tmp_path / "runs",
        popen_factory=popen,
        now_factory=lambda: moments.pop(0),
    )

    first = runner.submit(_job_request(seed=1))
    second = runner.submit(_job_request(seed=2))
    third = runner.submit(_job_request(seed=3))

    assert len(popen.calls) == 1
    jobs = {job["job_id"]: job for job in runner.list_jobs()}
    assert jobs[first["job_id"]]["status"] == "RUNNING"
    assert jobs[second["job_id"]]["status"] == "QUEUED"
    assert jobs[second["job_id"]]["queue_depth"] == 1
    assert jobs[third["job_id"]]["status"] == "QUEUED"
    assert jobs[third["job_id"]]["queue_depth"] == 2

    _write_minimal_result_table(Path(first["out_dir"]))
    popen.processes[0].returncode = 0
    jobs = {job["job_id"]: job for job in runner.list_jobs()}

    assert len(popen.calls) == 2
    assert jobs[first["job_id"]]["status"] == "SUCCEEDED"
    assert jobs[second["job_id"]]["status"] == "RUNNING"
    assert jobs[third["job_id"]]["status"] == "QUEUED"
    assert jobs[third["job_id"]]["queue_depth"] == 1


def test_optimizer_job_register_rebuilds_from_disk_on_fresh_app(tmp_path) -> None:
    runs_dir = tmp_path / "runs"
    job_dir = runs_dir / "jobs" / "job-from-disk"
    job_dir.mkdir(parents=True)
    (job_dir / ".job_meta.json").write_text(
        json.dumps(
            {
                "job_id": "job-from-disk",
                "feedstock": "lunar_mare_low_ti",
                "profile": "lunar-mare-low-ti-objectives-v1",
                "feedstock_id": "lunar_mare_low_ti",
                "profile_id": "lunar-mare-low-ti-objectives-v1",
                "strategy": "random",
                "fidelity": "stub",
                "budget": 1,
                "parallel": 1,
                "seed": 0,
                "pid": None,
                "status": "SUCCEEDED",
                "created_at": "2026-06-08T00:00:00+00:00",
                "completed_at": "2026-06-08T00:01:00+00:00",
                "eta": None,
                "stderr_tail": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    optimizer_job_runner.reset_runner_cache()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["OPTIMIZER_RUNS_DIR"] = str(runs_dir)
    app.register_blueprint(web_routes.bp)

    response = app.test_client().get("/api/optimizer/jobs")

    assert response.status_code == 200
    job = response.get_json()["jobs"][0]
    assert job["job_id"] == "job-from-disk"
    assert job["status"] == "SUCCEEDED"
    assert job["version_badge"]["status"] == "unknown"


def test_optimizer_job_register_marks_orphan_with_results_succeeded_on_rebuild(
    tmp_path,
) -> None:
    runs_dir = tmp_path / "runs"
    job_dir = runs_dir / "jobs" / "orphan-with-results"
    job_dir.mkdir(parents=True)
    (job_dir / ".job_meta.json").write_text(
        json.dumps(
            {
                "job_id": "orphan-with-results",
                "feedstock": "lunar_mare_low_ti",
                "profile": "lunar-mare-low-ti-objectives-v1",
                "feedstock_id": "lunar_mare_low_ti",
                "profile_id": "lunar-mare-low-ti-objectives-v1",
                "strategy": "random",
                "fidelity": "stub",
                "budget": 1,
                "parallel": 1,
                "seed": 0,
                "pid": 999999999,
                "status": "RUNNING",
                "created_at": "2026-06-08T00:00:00+00:00",
                "started_at": "2026-06-08T00:00:01+00:00",
                "eta": None,
                "stderr_tail": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write_minimal_result_table(job_dir)

    runner = optimizer_job_runner.OptimizerJobRunner(runs_dir)
    jobs = runner.list_jobs()

    assert jobs[0]["job_id"] == "orphan-with-results"
    assert jobs[0]["status"] == "SUCCEEDED"
    meta = json.loads((job_dir / ".job_meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "SUCCEEDED"
    assert meta["completed_at"] is not None


def test_optimizer_job_register_marks_dead_running_job_failed_on_rebuild(tmp_path) -> None:
    runs_dir = tmp_path / "runs"
    job_dir = runs_dir / "jobs" / "dead-running-job"
    job_dir.mkdir(parents=True)
    (job_dir / ".job_meta.json").write_text(
        json.dumps(
            {
                "job_id": "dead-running-job",
                "feedstock": "lunar_mare_low_ti",
                "profile": "lunar-mare-low-ti-objectives-v1",
                "feedstock_id": "lunar_mare_low_ti",
                "profile_id": "lunar-mare-low-ti-objectives-v1",
                "strategy": "random",
                "fidelity": "stub",
                "budget": 1,
                "parallel": 1,
                "seed": 0,
                "pid": 999999999,
                "status": "RUNNING",
                "created_at": "2026-06-08T00:00:00+00:00",
                "started_at": "2026-06-08T00:00:01+00:00",
                "eta": None,
                "stderr_tail": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    runner = optimizer_job_runner.OptimizerJobRunner(runs_dir)
    jobs = runner.list_jobs()

    assert jobs[0]["job_id"] == "dead-running-job"
    assert jobs[0]["status"] == "FAILED"
    assert (
        "optimizer process exited before the web process recorded an exit code"
        in jobs[0]["stderr_tail"]
    )
    meta = json.loads((job_dir / ".job_meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "FAILED"


def test_optimizer_page_and_table_render_feedstock_profile_winners(
    client,
    tmp_path,
) -> None:
    runs_dir = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    run_dir = runs_dir / "run-page"
    run_dir.mkdir(parents=True)
    spec = _base_spec(recipe_id="recipe-page")
    store = ResultStore(run_dir / "cache.sqlite")
    store.store(
        spec,
        _scored(
            spec,
            candidate_id="candidate-page",
            oxygen=14.0,
            product_summary={
                "product_yield_table": _product_yield_table(),
                "wall_deposit_kg_by_segment_species": {
                    "C4-cold-wall": {"SiO2": 0.25, "Al2O3": 0.05},
                },
                "campaigns_to_resinter": 3,
                "extraction_completeness": {
                    "status": "available",
                    "target_species": "Fe",
                    "denominator_account": "cleaned_silicate_feed",
                    "allowed_residual": "0.1 kg",
                    "product_bin": "ingots_metals",
                    "fraction": 0.95,
                },
            },
        ),
        created_at="2026-06-02T00:00:00Z",
    )

    response = client.get("/optimizer")
    partial = client.get("/partials/optimizer-table")

    assert response.status_code == 200
    assert partial.status_code == 200
    html = response.get_data(as_text=True)
    table = partial.get_data(as_text=True)
    assert 'hx-get="/partials/optimizer-table"' in html
    assert "Launch Optimizer Job" in html
    assert "minutes to hours" in html
    assert 'hx-post="/optimizer/jobs"' in html
    assert 'hx-get="/partials/optimizer-jobs"' in html
    assert "candidate-page" in table
    assert "lunar_mare_low_ti" in table
    assert "oxygen-yield-v1" in table
    assert "oxygen_kg" in table
    assert "14.0" in table
    assert "95.00 %" in table
    assert "campaigns to resinter" in table
    assert "Ingots/metals" in table
    assert "Glass" in table
    assert "O2" in table
    assert "Captured volatiles" in table
    assert "Refractory ceramic/rump" in table
    assert "backend-badge" in table
    assert "StubBackend / unavailable" in table
    assert "current" in table


def test_optimizer_page_marks_missing_readouts_inconclusive(
    client,
    tmp_path,
) -> None:
    runs_dir = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    run_dir = runs_dir / "run-inconclusive-page"
    run_dir.mkdir(parents=True)
    spec = _base_spec()
    product_yield_table = {
        "status": "closed",
        "inputs": [
            {"kind": "input", "id": "feedstock", "label": "Feedstock", "kg": 1000.0},
        ],
        "outputs": [
            {"kind": "output", "id": "oxygen", "label": "O2", "kg": 20.0},
        ],
        "mass_closure": {
            "kind": "mass_closure",
            "label": "Mass closure",
            "mass_in_kg": 1000.0,
            "accountable_mass_out_kg": 1000.0,
            "products_out_kg": 20.0,
            "balance_error_pct": 0.0,
            "tolerance_pct": 5e-12,
            "status": "closed",
        },
    }
    store = ResultStore(run_dir / "cache.sqlite")
    store.store(
        spec,
        _scored(
            spec,
            candidate_id="candidate-inconclusive",
            product_summary={
                "product_yield_table": product_yield_table,
                "product_classes": {
                    "unclassified": {
                        "kg_by_species": {"MysteryOxide": 7.0},
                        "total_kg": 7.0,
                    },
                },
            },
        ),
        created_at="2026-06-02T00:00:00Z",
    )

    response = client.get("/optimizer")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "candidate-inconclusive" in html
    assert "Product inconclusive" in html
    assert "Completeness inconclusive" in html
    assert "Coating inconclusive" in html
    assert "extraction completeness metric missing" in html
    assert "coating artifact missing" in html


def test_optimizer_page_marks_unclassified_product_status_inconclusive(
    client,
    tmp_path,
) -> None:
    runs_dir = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    run_dir = runs_dir / "run-unclassified-status-page"
    run_dir.mkdir(parents=True)
    spec = _base_spec()
    product_yield_table = _product_yield_table(status="unclassified")
    product_yield_table["reason"] = "stored product classes unresolved"
    store = ResultStore(run_dir / "cache.sqlite")
    store.store(
        spec,
        _scored(
            spec,
            candidate_id="candidate-unclassified-status",
            product_summary={
                "product_yield_table": product_yield_table,
            },
        ),
        created_at="2026-06-02T00:00:00Z",
    )

    response = client.get("/optimizer")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "candidate-unclassified-status" in html
    assert "Product inconclusive" in html
    assert "product_yield_table status unclassified" in html
    assert "stored product classes unresolved" in html


def test_optimizer_result_detail_yaml_and_recipe_viewer_contract(
    client,
    tmp_path,
) -> None:
    runs_dir = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    run_dir = runs_dir / "run-detail"
    run_dir.mkdir(parents=True)
    spec = _base_spec(
        recipe_id="recipe-detail",
        hours=36,
        additives_kg={"Na": 2.0},
        c5_enabled=True,
        mre_max_voltage_V=1.35,
        mre_target_species="FeO",
        runtime_campaign_overrides={
            "C4": {
                "temperature_ramp_C_per_h": 25,
                "hold_temperature_C": 1300,
                "hold_time_h": 3,
                "p_total_mbar": 0.01,
                "pO2_mbar": 0.001,
                "pN2_mbar": 0.009,
                "wall_temp_offset_C": -40,
                "wall_temp_zone": "cold-wall",
                "alkali_dosing": {"Na_kg": 2.0},
            },
        },
    )
    store = ResultStore(run_dir / "cache.sqlite")
    store.store(
        spec,
        _scored(
            spec,
            candidate_id="candidate-detail",
            product_summary={
                "product_yield_table": _product_yield_table(),
                "extraction_completeness": {
                    "status": "available",
                    "target_species": "Fe",
                    "denominator_account": "cleaned_silicate_feed",
                    "product_bin": "ingots_metals",
                    "fraction": 0.9,
                },
            },
        ),
        created_at="2026-06-02T00:00:00Z",
    )
    key = cache_key(spec)

    detail = client.get(f"/optimizer/runs/run-detail/results/{key}")
    download = client.get(f"/optimizer/runs/run-detail/results/{key}/recipe.yaml")

    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "candidate-detail" in html
    assert "Recipe Patch" in html
    assert "Run Recipe" in html
    assert "Stage C4" in html
    assert "Temperature ramp rate" in html
    assert "Hold point" in html
    assert "Overhead pressure setpoint" in html
    assert "pO2" in html
    assert "pN2 sweep" in html
    assert "MRE policy" in html
    assert "Wall-temp offset" in html
    assert "Alkali-shuttle dosing" in html
    assert "Declared" in html
    assert "Derived" in html
    assert "Hours at run" in html
    assert "computed from EvalSpec.hours" in html
    assert "Backend" in html
    assert "backend-badge" in html
    assert "StubBackend / unavailable" in html

    assert download.status_code == 200
    assert download.mimetype == "application/x-yaml"
    payload = yaml.safe_load(download.get_data(as_text=True))
    assert payload["result"]["candidate_id"] == "candidate-detail"
    assert payload["eval_spec"]["runtime_campaign_overrides"]["C4"][
        "hold_temperature_C"
    ] == 1300
    assert payload["eval_spec"]["mre_target_species"] == "FeO"
    assert payload["provenance"]["cache_key"] == key


def test_optimizer_result_yaml_download_sanitizes_stored_candidate_id(
    client,
    tmp_path,
) -> None:
    runs_dir = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    run_dir = runs_dir / "run-unsafe-name"
    run_dir.mkdir(parents=True)
    spec = _base_spec(recipe_id="recipe-unsafe-name")
    store = ResultStore(run_dir / "cache.sqlite")
    store.store(
        spec,
        _scored(spec, candidate_id='candidate"\r\nbad/name'),
        created_at="2026-06-02T00:00:00Z",
    )
    key = cache_key(spec)

    response = client.get(
        f"/optimizer/runs/run-unsafe-name/results/{key}/recipe.yaml"
    )

    assert response.status_code == 200
    disposition = response.headers["Content-Disposition"]
    assert "\r" not in disposition
    assert "\n" not in disposition
    assert '"' not in disposition
    assert "/" not in disposition
    assert "candidate-bad-name-recipe.yaml" in disposition
    payload = yaml.safe_load(response.get_data(as_text=True))
    assert payload["result"]["candidate_id"] == 'candidate"\r\nbad/name'


@pytest.mark.parametrize("mass_kg", ["abc", "-1", "0", "nan", "inf", "1e309"])
def test_additive_calc_rejects_invalid_mass_kg(client, mass_kg: str) -> None:
    response = client.get(f"/api/additive-calc/lunar_mare_low_ti?mass_kg={mass_kg}")

    assert response.status_code == 400
    assert "mass_kg" in response.get_json()["error"]


def test_additive_calc_returns_finite_non_negative_masses(client) -> None:
    response = client.get("/api/additive-calc/lunar_mare_low_ti?mass_kg=1000")

    assert response.status_code == 200
    payload = response.get_json()
    assert set(payload) == {"Na", "K", "Mg", "Ca", "C"}
    assert all(isinstance(value, (int, float)) for value in payload.values())
    assert all(math.isfinite(value) and value >= 0.0 for value in payload.values())


def test_product_ledger_panel_has_ingots_glass_o2_volatiles_ceramic_and_mass_closure(
    client,
    tmp_path,
) -> None:
    runs_dir = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    run_dir = runs_dir / "run-products"
    run_dir.mkdir(parents=True)
    spec = _base_spec()
    product_yield_table = {
        "status": "closed",
        "inputs": [
            {"kind": "input", "id": "feedstock", "label": "Feedstock", "kg": 1000.0},
            {"kind": "input", "id": "additive:CaO", "label": "CaO", "kg": 1.5},
        ],
        "outputs": [
            {"kind": "output", "id": "ingots_metals", "label": "Ingots/metals", "kg": 50.0, "yield_pct": 4.992511},
            {"kind": "output", "id": "glass", "label": "Glass", "kg": 40.0, "yield_pct": 3.994009},
            {"kind": "output", "id": "oxygen", "label": "O2", "kg": 20.0, "yield_pct": 1.997004},
            {"kind": "output", "id": "captured_volatiles", "label": "Captured volatiles", "kg": 5.0, "yield_pct": 0.499251},
            {"kind": "output", "id": "refractory_ceramic_rump", "label": "Refractory ceramic/rump", "kg": 80.0, "yield_pct": 7.988018},
        ],
        "mass_closure": {
            "kind": "mass_closure",
            "label": "Mass closure",
            "mass_in_kg": 1001.5,
            "accountable_mass_out_kg": 1001.5,
            "products_out_kg": 195.0,
            "balance_error_pct": 0.0,
            "tolerance_pct": 5e-12,
            "status": "closed",
        },
        "total_input_kg": 1001.5,
        "products_out_kg": 195.0,
    }
    store = ResultStore(run_dir / "cache.sqlite")
    store.store(
        spec,
        _scored(
            spec,
            product_summary={
                "product_bins": {
                    row["id"]: {"label": row["label"], "kg": row["kg"]}
                    for row in product_yield_table["outputs"]
                },
                "product_yield_table": product_yield_table,
            },
        ),
        created_at="2026-06-02T00:00:00Z",
    )

    response = client.get("/api/optimizer/runs")

    assert response.status_code == 200
    result = response.get_json()["runs"][0]["latest_result"]
    panel = result["product_ledger_panel"]
    outputs = {row["id"]: row for row in panel["outputs"]}
    assert set(outputs) == {
        "ingots_metals",
        "glass",
        "oxygen",
        "captured_volatiles",
        "refractory_ceramic_rump",
    }
    assert result["product_bins"]["oxygen"]["kg"] == 20.0
    assert panel["inputs"][1]["id"] == "additive:CaO"
    assert panel["mass_closure"]["status"] == "closed"
    assert panel["mass_closure"]["tolerance_pct"] == 5e-12


def test_product_ledger_panel_surfaces_unclassified_mass_as_inconclusive(
    client,
    tmp_path,
) -> None:
    runs_dir = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    run_dir = runs_dir / "run-unclassified"
    run_dir.mkdir(parents=True)
    spec = _base_spec()
    product_yield_table = {
        "status": "closed",
        "inputs": [
            {"kind": "input", "id": "feedstock", "label": "Feedstock", "kg": 1000.0},
        ],
        "outputs": [
            {"kind": "output", "id": "oxygen", "label": "O2", "kg": 20.0},
        ],
        "mass_closure": {
            "kind": "mass_closure",
            "label": "Mass closure",
            "mass_in_kg": 1000.0,
            "accountable_mass_out_kg": 1000.0,
            "products_out_kg": 20.0,
            "balance_error_pct": 0.0,
            "tolerance_pct": 5e-12,
            "status": "closed",
        },
        "total_input_kg": 1000.0,
        "products_out_kg": 20.0,
    }
    store = ResultStore(run_dir / "cache.sqlite")
    store.store(
        spec,
        _scored(
            spec,
            product_summary={
                "product_classes": {
                    "unclassified": {
                        "kg_by_species": {"MysteryOxide": 7.0},
                        "total_kg": 7.0,
                    },
                },
                "product_yield_table": product_yield_table,
            },
        ),
        created_at="2026-06-02T00:00:00Z",
    )

    response = client.get("/api/optimizer/runs")

    assert response.status_code == 200
    panel = response.get_json()["runs"][0]["latest_result"]["product_ledger_panel"]
    assert panel["mass_closure"]["status"] == "closed"
    assert panel["status"] == "inconclusive"
    assert panel["unclassified_product_mass"]["total_kg"] == 7.0
    assert panel["unclassified_product_mass"]["kg_by_species"] == {
        "MysteryOxide": 7.0,
    }
    diagnostics = {row["id"]: row for row in panel["diagnostics"]}
    assert diagnostics["unclassified_product_mass"]["kind"] == "diagnostic"


def test_web_routes_do_not_import_evaluate_or_worker_runtime() -> None:
    forbidden: list[str] = []

    for path in (
        Path(web_routes.__file__),
        Path(optimizer_job_runner.__file__),
    ):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                forbidden.extend(
                    alias.name
                    for alias in node.names
                    if alias.name == "simulator.optimize.evaluate"
                )
            if isinstance(node, ast.ImportFrom):
                if node.module == "simulator.optimize.evaluate":
                    forbidden.append(f"{path.name}:{node.module}")
                if node.module and node.module.endswith("worker_runtime"):
                    forbidden.append(f"{path.name}:{node.module}")
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in {
                    "evaluate",
                    "evaluate_batch",
                }:
                    forbidden.append(f"{path.name}:{func.id}()")
                if isinstance(func, ast.Attribute) and func.attr in {
                    "evaluate",
                    "evaluate_batch",
                }:
                    forbidden.append(f"{path.name}:*.{func.attr}()")
        assert "worker_runtime" not in source

    assert forbidden == []
