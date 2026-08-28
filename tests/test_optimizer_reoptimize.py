from __future__ import annotations

import ast
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from flask import Flask
import pytest
import yaml

from simulator.backend_names import ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
from simulator.config import DEFAULT_DATA_DIR
from simulator.optimize import cli as optimizer_cli
from simulator.optimize import import_bundle as import_bundle_module
from simulator.optimize import job_runner as optimizer_job_runner
from simulator.optimize import reoptimize as reoptimize_module
from simulator.optimize import study
from simulator.optimize.objective import ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC
from simulator.optimize.reoptimize import (
    GOALS_SOURCE_BUNDLED,
    GOALS_SOURCE_CURRENT,
    PROFILE_NAME,
    ReoptimizeError,
    ReoptimizeVocabularyDriftError,
    collect_vocabulary_drift,
    load_reoptimize_prefill,
    plan_reoptimize,
)
from web import routes as web_routes


ROOT = Path(__file__).resolve().parents[1]
LUNAR_PROFILE = ROOT / "data" / "optimize_profiles" / "lunar_mare_low_ti.yaml"
LUNAR_PROFILE_ID = "lunar-mare-low-ti-objectives-v1"
FEEDSTOCK = "lunar_mare_low_ti"
SOURCE_STUDY_ID = "source-study-aa11"

PROFILE = {
    "profile_id": LUNAR_PROFILE_ID,
    "profile_schema_version": "profile-schema-v1",
    "feedstock": FEEDSTOCK,
    "objectives": [
        {
            "metric": "oxygen_kg",
            "sense": "maximize",
            "units": "kg",
            "weight": 0.6,
            "rationale": "test oxygen",
        },
        {
            "metric": ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
            "sense": "minimize",
            "units": "kWh",
            "weight": 0.4,
            "rationale": "test energy",
        },
    ],
    "constraints": {"gates": ["delivered_stream_purity"]},
    "run": {
        "campaign": "C0",
        "hours": 1,
        "mass_kg": 1000.0,
        "backend_name": ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    },
    "fidelities": {
        ANALYTICAL_BACKEND_SERIALIZATION_TOKEN: {
            "backend_name": ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
            "hours": 1,
        }
    },
    "seed_recipes": [
        {
            "id": "study-c0-seed",
            "source_campaign": "C0",
            "patch": {"campaigns": {"C0": {"temp_range_C": [900, 950]}}},
        }
    ],
}


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
        process = _FakeProcess(pid=7000 + len(self.processes))
        self.processes.append(process)
        self.calls.append({"cmd": list(cmd), "env": dict(env or {})})
        return process


@pytest.fixture
def client(tmp_path):
    optimizer_job_runner.reset_runner_cache()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-only-secret-key"
    app.config["OPTIMIZER_RUNS_DIR"] = str(tmp_path / "runs")
    app.register_blueprint(web_routes.bp)
    yield app.test_client()
    optimizer_job_runner.reset_runner_cache()


def _manifest(
    *,
    study_id: str = SOURCE_STUDY_ID,
    strategy: str = "random",
    seed: int = 11,
    budget: int = 24,
    parallel: int = 2,
    fidelity: str = ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
) -> dict[str, object]:
    return {
        "save_schema_version": 1,
        "member_schema_version": 1,
        "study_id": study_id,
        "feedstock_id": FEEDSTOCK,
        "profile": {"id": LUNAR_PROFILE_ID, "display_name": LUNAR_PROFILE_ID},
        "strategy": {"name": "RandomStrategy", "class": "RandomStrategy", "config": {"strategy": strategy}},
        "seed": seed,
        "budget": budget,
        "parallel": parallel,
        "fidelity": fidelity,
        "study_status": "completed",
    }


def _write_source(
    root: Path,
    *,
    profile: dict[str, object] | None = None,
    manifest: dict[str, object] | None = None,
    sqlite_bytes: bytes = b"TRAP-IMPORTED-SQLITE",
    copy_lunar_profile: bool = False,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "study.manifest.json").write_text(
        json.dumps(manifest or _manifest(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if copy_lunar_profile:
        (root / PROFILE_NAME).write_text(
            LUNAR_PROFILE.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    else:
        (root / PROFILE_NAME).write_text(
            yaml.safe_dump(profile or PROFILE, sort_keys=True),
            encoding="utf-8",
        )
    (root / "cache.sqlite").write_bytes(sqlite_bytes)
    (root / "study.summary.json").write_text(
        json.dumps(
            {
                "save_schema_version": 1,
                "member_schema_version": 1,
                "study_id": (manifest or _manifest()).get("study_id"),
                "feedstock_id": FEEDSTOCK,
                "profile_id": LUNAR_PROFILE_ID,
                "study_status": "completed",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "pareto.json").write_text(
        json.dumps({"member_schema_version": 1, "pareto": []}) + "\n",
        encoding="utf-8",
    )
    (root / "leaderboard.csv").write_text("candidate_id\n", encoding="utf-8")
    return root


def _drifted_profile() -> dict[str, object]:
    payload = json.loads(json.dumps(PROFILE))
    payload["objectives"].append(
        {
            "metric": "vanished_oxygen_kg",
            "sense": "maximize",
            "units": "kg",
            "weight": 0.1,
            "rationale": "removed metric",
        }
    )
    payload["constraints"]["gates"] = [
        "delivered_stream_purity",
        "ghost_gate",
    ]
    payload["pinned_paths"] = ["campaigns.C9.vanished_knob"]
    payload["seed_recipes"][0]["patch"]["campaigns"]["C9"] = {"vanished_knob": 1.0}
    return payload


def _cmd_value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def test_reoptimize_module_does_not_import_sqlite() -> None:
    tree = ast.parse(Path(reoptimize_module.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert "sqlite3" not in imported


@pytest.mark.parametrize("goals_source", (GOALS_SOURCE_BUNDLED, GOALS_SOURCE_CURRENT))
def test_reoptimize_sets_lineage_for_both_goal_variants(
    tmp_path: Path,
    goals_source: str,
) -> None:
    source = _write_source(tmp_path / "source", copy_lunar_profile=True)
    plan = plan_reoptimize(
        source,
        goals_source=goals_source,
        strategy="staged",
        seed=99,
        budget=8,
        fidelity="high",
        parallel=3,
        data_dir=DEFAULT_DATA_DIR,
    )
    assert plan.reoptimized_from == SOURCE_STUDY_ID
    assert plan.source_study_id == SOURCE_STUDY_ID
    assert plan.goals_source == goals_source

    out = tmp_path / f"written-{goals_source}"
    out.mkdir()
    config = study.StudyConfig(
        profile=PROFILE,
        feedstock=FEEDSTOCK,
        strategy="random",
        fidelity=ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        budget=1,
        parallel=1,
        out_dir=out,
        seed=0,
        reoptimized_from=plan.reoptimized_from,
        goals_source=plan.goals_source,
    )
    study._write_empty_artifacts(
        out,
        profile=PROFILE,
        feedstock=FEEDSTOCK,
        fidelity=ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        definitions=study.objective_definitions(PROFILE),
        failure_counts={"no_candidates": 1},
        config=config,
    )
    manifest = json.loads((out / "study.manifest.json").read_text(encoding="utf-8"))
    assert manifest["reoptimized_from"] == SOURCE_STUDY_ID
    assert manifest["goals_source"] == goals_source


@pytest.mark.parametrize("goals_source", (GOALS_SOURCE_BUNDLED, GOALS_SOURCE_CURRENT))
def test_reoptimize_goals_source_distinguishes_variants(
    tmp_path: Path,
    goals_source: str,
) -> None:
    source = _write_source(tmp_path / "source", copy_lunar_profile=True)
    plan = plan_reoptimize(
        source,
        goals_source=goals_source,
        strategy="random",
        seed=1,
        budget=2,
        fidelity=ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        parallel=1,
        data_dir=DEFAULT_DATA_DIR,
    )
    if goals_source == GOALS_SOURCE_BUNDLED:
        assert plan.profile_arg == str(source / PROFILE_NAME)
    else:
        assert plan.profile_arg.endswith("data/optimize_profiles/lunar_mare_low_ti.yaml")
        assert plan.profile_arg != str(source / PROFILE_NAME)
    assert plan.goals_source == goals_source
    assert plan.reoptimized_from == SOURCE_STUDY_ID


def test_reoptimize_run_params_are_prefilled_and_operator_override_is_honoured(
    tmp_path: Path,
) -> None:
    source = _write_source(
        tmp_path / "source",
        copy_lunar_profile=True,
        manifest=_manifest(strategy="random", seed=11, budget=24, parallel=2),
    )
    prefill = load_reoptimize_prefill(source)
    assert prefill.strategy == "random"
    assert prefill.seed == 11
    assert prefill.budget == 24
    assert prefill.parallel == 2
    assert prefill.fidelity == ANALYTICAL_BACKEND_SERIALIZATION_TOKEN

    plan = plan_reoptimize(
        source,
        goals_source=GOALS_SOURCE_BUNDLED,
        strategy="staged",
        seed=99,
        budget=8,
        fidelity="high",
        parallel=3,
        data_dir=DEFAULT_DATA_DIR,
    )
    assert plan.strategy == "staged"
    assert plan.seed == 99
    assert plan.budget == 8
    assert plan.fidelity == "high"
    assert plan.parallel == 3
    assert plan.strategy != prefill.strategy
    assert plan.seed != prefill.seed
    assert plan.budget != prefill.budget
    assert plan.parallel != prefill.parallel
    assert plan.fidelity != prefill.fidelity


def test_reoptimize_vocabulary_drift_refuses_and_enumerates_every_identifier(
    tmp_path: Path,
) -> None:
    source = _write_source(tmp_path / "source", profile=_drifted_profile())
    drifted = collect_vocabulary_drift(
        yaml.safe_load((source / PROFILE_NAME).read_text(encoding="utf-8"))
    )
    assert "vanished_oxygen_kg" in drifted
    assert "ghost_gate" in drifted
    assert "campaigns.C9.vanished_knob" in drifted
    with pytest.raises(ReoptimizeVocabularyDriftError) as raised:
        plan_reoptimize(
            source,
            goals_source=GOALS_SOURCE_BUNDLED,
            strategy="random",
            seed=1,
            budget=2,
            fidelity=ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
            parallel=1,
            data_dir=DEFAULT_DATA_DIR,
        )
    message = str(raised.value)
    assert "vanished_oxygen_kg" in message
    assert "ghost_gate" in message
    assert "campaigns.C9.vanished_knob" in message
    assert set(raised.value.identifiers) >= {
        "vanished_oxygen_kg",
        "ghost_gate",
        "campaigns.C9.vanished_knob",
    }


def test_current_local_profile_is_escape_hatch_for_bundled_vocab_drift(
    tmp_path: Path,
) -> None:
    source = _write_source(tmp_path / "source", profile=_drifted_profile())
    plan = plan_reoptimize(
        source,
        goals_source=GOALS_SOURCE_CURRENT,
        strategy="random",
        seed=1,
        budget=2,
        fidelity=ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        parallel=1,
        data_dir=DEFAULT_DATA_DIR,
    )
    assert plan.goals_source == GOALS_SOURCE_CURRENT
    assert plan.reoptimized_from == SOURCE_STUDY_ID
    assert plan.profile_arg.endswith("lunar_mare_low_ti.yaml")


def test_reoptimize_never_opens_imported_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_source(tmp_path / "imported-source", copy_lunar_profile=True)
    sqlite_path = str((source / "cache.sqlite").resolve())
    opened: list[str] = []
    real_connect = sqlite3.connect

    def trap_connect(database, *args, **kwargs):
        opened.append(str(database))
        if sqlite_path in str(database):
            raise AssertionError(f"imported sqlite opened: {database}")
        return real_connect(database, *args, **kwargs)

    def trap_untrusted(path, *args, **kwargs):
        raise AssertionError(f"open_untrusted_result_db called: {path}")

    monkeypatch.setattr(sqlite3, "connect", trap_connect)
    monkeypatch.setattr(import_bundle_module, "open_untrusted_result_db", trap_untrusted)
    load_reoptimize_prefill(source)
    plan_reoptimize(
        source,
        goals_source=GOALS_SOURCE_BUNDLED,
        strategy="random",
        seed=1,
        budget=2,
        fidelity=ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        parallel=1,
        data_dir=DEFAULT_DATA_DIR,
    )
    assert sqlite_path not in opened
    assert not any("cache.sqlite" in item and sqlite_path in item for item in opened)


def test_cli_forwards_reoptimize_lineage_in_both_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            out_dir=tmp_path / "out",
            winner=None,
            status="completed",
        )

    monkeypatch.setattr(optimizer_cli, "run", fake_run)
    monkeypatch.setattr(optimizer_cli, "_write_job_status", lambda *args, **kwargs: None)
    for goals_source in (GOALS_SOURCE_BUNDLED, GOALS_SOURCE_CURRENT):
        captured.clear()
        code = optimizer_cli.main(
            [
                "--feedstock",
                FEEDSTOCK,
                "--profile",
                str(LUNAR_PROFILE),
                "--strategy",
                "random",
                "--fidelity",
                ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
                "--budget",
                "1",
                "--reoptimized-from",
                SOURCE_STUDY_ID,
                "--goals-source",
                goals_source,
            ]
        )
        assert code == 0
        assert captured["reoptimized_from"] == SOURCE_STUDY_ID
        assert captured["goals_source"] == goals_source


def test_job_runner_passes_reoptimize_flags(tmp_path: Path) -> None:
    popen = _FakePopenFactory()
    runner = optimizer_job_runner.OptimizerJobRunner(
        tmp_path / "runs",
        popen_factory=popen,
        python_executable="python3",
    )
    job = runner.submit(
        optimizer_job_runner.OptimizerJobRequest(
            feedstock_id=FEEDSTOCK,
            profile_id=LUNAR_PROFILE_ID,
            strategy="random",
            fidelity=ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
            budget=4,
            parallel=1,
            seed=3,
            profile_arg=str(LUNAR_PROFILE),
            reoptimized_from=SOURCE_STUDY_ID,
            goals_source=GOALS_SOURCE_CURRENT,
        )
    )
    assert job["reoptimized_from"] == SOURCE_STUDY_ID
    assert job["goals_source"] == GOALS_SOURCE_CURRENT
    cmd = popen.calls[0]["cmd"]
    assert _cmd_value(cmd, "--reoptimized-from") == SOURCE_STUDY_ID
    assert _cmd_value(cmd, "--goals-source") == GOALS_SOURCE_CURRENT


@pytest.mark.parametrize("goals_source", (GOALS_SOURCE_BUNDLED, GOALS_SOURCE_CURRENT))
def test_web_reoptimize_records_lineage_for_both_variants(
    client,
    tmp_path: Path,
    goals_source: str,
) -> None:
    popen = _FakePopenFactory()
    client.application.config["OPTIMIZER_JOB_POPEN_FACTORY"] = popen
    imported = Path(client.application.config["OPTIMIZER_RUNS_DIR"]) / "imported" / SOURCE_STUDY_ID
    _write_source(imported, copy_lunar_profile=True)

    response = client.post(
        "/api/optimizer/reoptimize",
        json={
            "origin": "imported",
            "study_id": SOURCE_STUDY_ID,
            "goals_source": goals_source,
            "strategy": "random",
            "fidelity": ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
            "budget": 3,
            "parallel": 1,
            "seed": 4,
        },
    )
    assert response.status_code == 202
    job = response.get_json()["job"]
    assert job["reoptimized_from"] == SOURCE_STUDY_ID
    assert job["goals_source"] == goals_source
    cmd = popen.calls[0]["cmd"]
    assert _cmd_value(cmd, "--reoptimized-from") == SOURCE_STUDY_ID
    assert _cmd_value(cmd, "--goals-source") == goals_source


def test_web_reoptimize_prefills_and_honours_operator_override(client, tmp_path: Path) -> None:
    popen = _FakePopenFactory()
    client.application.config["OPTIMIZER_JOB_POPEN_FACTORY"] = popen
    imported = Path(client.application.config["OPTIMIZER_RUNS_DIR"]) / "imported" / SOURCE_STUDY_ID
    _write_source(
        imported,
        copy_lunar_profile=True,
        manifest=_manifest(strategy="random", seed=11, budget=24, parallel=2),
    )

    form = client.get(f"/optimizer/reoptimize/imported/{SOURCE_STUDY_ID}")
    assert form.status_code == 200
    html = form.get_data(as_text=True)
    assert 'id="reoptimize-strategy"' in html
    assert 'id="reoptimize-seed"' in html
    assert 'id="reoptimize-budget"' in html
    assert 'id="reoptimize-fidelity"' in html
    assert 'id="reoptimize-parallel"' in html
    assert "saved goals" in html
    assert "current local profile" in html
    assert 'value="24"' in html
    assert 'value="11"' in html
    assert 'value="2"' in html
    assert "selected>random</option>" in html or "value=\"random\" selected" in html

    response = client.post(
        "/api/optimizer/reoptimize",
        json={
            "origin": "imported",
            "study_id": SOURCE_STUDY_ID,
            "goals_source": GOALS_SOURCE_BUNDLED,
            "strategy": "staged",
            "fidelity": "high",
            "budget": 8,
            "parallel": 3,
            "seed": 99,
        },
    )
    assert response.status_code == 202
    cmd = popen.calls[0]["cmd"]
    assert _cmd_value(cmd, "--strategy") == "staged"
    assert _cmd_value(cmd, "--budget") == "8"
    assert _cmd_value(cmd, "--parallel") == "3"
    assert _cmd_value(cmd, "--seed") == "99"
    assert _cmd_value(cmd, "--fidelity") == "high"
    assert "--budget" in cmd and "24" not in (
        _cmd_value(cmd, "--budget"),
    )


def test_web_reoptimize_does_not_silently_inherit_missing_run_params(
    client,
    tmp_path: Path,
) -> None:
    popen = _FakePopenFactory()
    client.application.config["OPTIMIZER_JOB_POPEN_FACTORY"] = popen
    imported = Path(client.application.config["OPTIMIZER_RUNS_DIR"]) / "imported" / SOURCE_STUDY_ID
    _write_source(imported, copy_lunar_profile=True)

    response = client.post(
        "/api/optimizer/reoptimize",
        json={
            "origin": "imported",
            "study_id": SOURCE_STUDY_ID,
            "goals_source": GOALS_SOURCE_BUNDLED,
            "strategy": "random",
            "fidelity": ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
            "parallel": 1,
            "seed": 4,
        },
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "budget is required"
    assert popen.calls == []


def test_web_reoptimize_vocabulary_drift_enumerates_identifiers(
    client,
    tmp_path: Path,
) -> None:
    popen = _FakePopenFactory()
    client.application.config["OPTIMIZER_JOB_POPEN_FACTORY"] = popen
    imported = Path(client.application.config["OPTIMIZER_RUNS_DIR"]) / "imported" / SOURCE_STUDY_ID
    _write_source(imported, profile=_drifted_profile())

    response = client.post(
        "/api/optimizer/reoptimize",
        json={
            "origin": "imported",
            "study_id": SOURCE_STUDY_ID,
            "goals_source": GOALS_SOURCE_BUNDLED,
            "strategy": "random",
            "fidelity": ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
            "budget": 2,
            "parallel": 1,
            "seed": 0,
        },
    )
    assert response.status_code == 400
    error = response.get_json()["error"]
    assert "vanished_oxygen_kg" in error
    assert "ghost_gate" in error
    assert "campaigns.C9.vanished_knob" in error
    assert popen.calls == []


def test_web_reoptimize_never_opens_imported_sqlite(
    client,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen = _FakePopenFactory()
    client.application.config["OPTIMIZER_JOB_POPEN_FACTORY"] = popen
    imported = Path(client.application.config["OPTIMIZER_RUNS_DIR"]) / "imported" / SOURCE_STUDY_ID
    _write_source(imported, copy_lunar_profile=True)
    sqlite_path = str((imported / "cache.sqlite").resolve())
    opened: list[str] = []
    real_connect = sqlite3.connect

    def trap_connect(database, *args, **kwargs):
        opened.append(str(database))
        if sqlite_path in str(database):
            raise AssertionError(f"imported sqlite opened: {database}")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", trap_connect)
    monkeypatch.setattr(
        import_bundle_module,
        "open_untrusted_result_db",
        lambda path, *args, **kwargs: (_ for _ in ()).throw(
            AssertionError(f"open_untrusted_result_db: {path}")
        ),
    )

    form = client.get(f"/optimizer/reoptimize/imported/{SOURCE_STUDY_ID}")
    assert form.status_code == 200
    response = client.post(
        "/api/optimizer/reoptimize",
        json={
            "origin": "imported",
            "study_id": SOURCE_STUDY_ID,
            "goals_source": GOALS_SOURCE_BUNDLED,
            "strategy": "random",
            "fidelity": ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
            "budget": 2,
            "parallel": 1,
            "seed": 0,
        },
    )
    assert response.status_code == 202
    assert not any(sqlite_path in item for item in opened)


def test_ui_affordance_on_imported_and_local_study_cards(client, tmp_path: Path) -> None:
    runs_root = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    imported = runs_root / "imported" / SOURCE_STUDY_ID
    _write_source(imported, copy_lunar_profile=True)

    optimizer = client.get("/optimizer").get_data(as_text=True)
    assert (
        f'href="/optimizer/reoptimize/imported/{SOURCE_STUDY_ID}"' in optimizer
    )
    imported_detail = client.get(f"/optimizer/imported/{SOURCE_STUDY_ID}").get_data(
        as_text=True
    )
    assert (
        f'href="/optimizer/reoptimize/imported/{SOURCE_STUDY_ID}"' in imported_detail
    )
    assert "Re-optimize" in imported_detail

    local = runs_root / "local-run"
    _write_source(local, copy_lunar_profile=True)
    local_form = client.get("/optimizer/reoptimize/local/local-run")
    assert local_form.status_code == 200
    local_html = local_form.get_data(as_text=True)
    assert "Re-optimize Study" in local_html
    table = (ROOT / "web" / "templates" / "partials" / "optimizer_table.html").read_text(
        encoding="utf-8"
    )
    detail = (ROOT / "web" / "templates" / "optimizer_detail.html").read_text(
        encoding="utf-8"
    )
    assert "optimizer_reoptimize_form" in table
    assert "origin='local'" in table
    assert "optimizer_reoptimize_form" in detail
    assert "origin='local'" in detail


def test_unknown_goals_source_is_refused(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source", copy_lunar_profile=True)
    with pytest.raises(ReoptimizeError, match="goals_source must be"):
        plan_reoptimize(
            source,
            goals_source="best_effort",
            strategy="random",
            seed=1,
            budget=2,
            fidelity=ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
            parallel=1,
            data_dir=DEFAULT_DATA_DIR,
        )
