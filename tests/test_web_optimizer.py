from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from flask import Flask
import pytest
import yaml

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
    product_summary: Mapping[str, object] | None = None,
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
            trace={"hours": [{"hour": 1, "oxygen_kg": oxygen, "backend_status": "ok"}]},
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

    assert download.status_code == 200
    assert download.mimetype == "application/x-yaml"
    payload = yaml.safe_load(download.get_data(as_text=True))
    assert payload["result"]["candidate_id"] == "candidate-detail"
    assert payload["eval_spec"]["runtime_campaign_overrides"]["C4"][
        "hold_temperature_C"
    ] == 1300
    assert payload["eval_spec"]["mre_target_species"] == "FeO"
    assert payload["provenance"]["cache_key"] == key


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
