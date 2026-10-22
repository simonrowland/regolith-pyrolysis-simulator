"""Pareto scatter, trajectory chart, and compare routes for one local run."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sqlite3
from pathlib import Path

from flask import Flask
import pytest

from simulator.optimize import job_runner as optimizer_job_runner
from simulator.optimize.evalspec import cache_key
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.results_store import ResultStore
from simulator.optimize.trajectory import project_trajectory
from web import routes as web_routes

_FOM = "separated_product_kg_per_furnace_h"
_ROOT = Path(__file__).resolve().parents[1]
_UNAVAILABLE = "trajectory unavailable for this point"
_UNREADABLE = "trajectory unreadable for this point"


def _load_helpers():
    path = Path(__file__).with_name("test_web_optimizer.py")
    spec = importlib.util.spec_from_file_location("optimizer_page_helpers", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


_HELPERS = _load_helpers()
_base_spec = _HELPERS._base_spec
_scored = _HELPERS._scored


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


def _vector(furnace: float, fom: float, oxygen: float, duration: float) -> ObjectiveVector:
    return ObjectiveVector((
        ObjectiveValue("oxygen_kg", "maximize", oxygen, "kg", ordinal=0),
        ObjectiveValue("furnace_time_h", "minimize", furnace, "h", ordinal=1),
        ObjectiveValue(_FOM, "maximize", fom, "kg/h", ordinal=2),
        ObjectiveValue("duration_h", "minimize", duration, "h", ordinal=3),
    ))


def _traced(trajectory: dict | None) -> dict:
    trace = {
        "backend_status": "ok",
        "backend_authoritative": True,
    }
    if trajectory is not None:
        trace["trajectory"] = trajectory
    else:
        trace["hours"] = [{"hour": 1, "oxygen_kg": 1.0, "backend_status": "ok"}]
    return trace


def _front_a_trajectory() -> dict:
    return project_trajectory(
        [
            {
                "hour": 1,
                "campaign": "C0",
                "T_C": 900.0,
                "P_total_bar": 0.01,
                "pO2_bar": 1.0e-8,
                "vapor_species_kg_hr": {"Na": 0.2, "Fe": 0.05},
                "wall_deposit_rate_by_species_kg_h": {"SiO": 0.01},
            },
            {
                "hour": 2,
                "campaign": "C0",
                "T_C": 1100.0,
                "P_total_bar": 0.01,
                "pO2_bar": 1.0e-8,
                "vapor_species_kg_hr": {"Na": 0.4, "Fe": 0.05},
                "wall_deposit_rate_by_species_kg_h": {"SiO": 0.02},
            },
        ],
        [
            {
                "duration_h": 1.0,
                "condensed_by_stage_species_delta": {("1", "Na"): 0.1},
            },
            {
                "duration_h": 1.0,
                "condensed_by_stage_species_delta": {("1", "Na"): 0.3},
            },
        ],
    )


def _temperature_trajectory(t_c: float) -> dict:
    return project_trajectory(
        [{"hour": 1, "campaign": "C0", "T_C": t_c}]
    )


def _store_point(
    store: ResultStore,
    *,
    candidate_id: str,
    furnace: float,
    fom: float,
    oxygen: float,
    duration: float,
    trajectory: dict | None,
    created_at: str,
) -> str:
    spec = _base_spec(recipe_id=f"recipe-{candidate_id}")
    scored = _scored(
        spec,
        candidate_id=candidate_id,
        oxygen=oxygen,
        objectives=_vector(furnace, fom, oxygen, duration),
        trace=_traced(trajectory),
    )
    store.store(spec, scored, created_at=created_at)
    assert scored.cache_key == cache_key(spec)
    return str(scored.cache_key)


def _seed_tradeoff(run_dir: Path) -> dict[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    store = ResultStore(run_dir / "cache.sqlite")
    keys = {
        "front-a": _store_point(
            store,
            candidate_id="front-a",
            furnace=4.0,
            fom=2.5,
            oxygen=10.0,
            duration=4.5,
            trajectory=_front_a_trajectory(),
            created_at="2026-10-20T00:00:01Z",
        ),
        "front-b": _store_point(
            store,
            candidate_id="front-b",
            furnace=8.0,
            fom=2.0,
            oxygen=8.0,
            duration=8.5,
            trajectory=_temperature_trajectory(700.0),
            created_at="2026-10-20T00:00:02Z",
        ),
        "infeasible": _store_point(
            store,
            candidate_id="infeasible",
            furnace=6.0,
            fom=0.5,
            oxygen=1.0,
            duration=6.0,
            trajectory=_temperature_trajectory(800.0),
            created_at="2026-10-20T00:00:03Z",
        ),
        "backend-unavailable": _store_point(
            store,
            candidate_id="backend-unavailable",
            furnace=5.0,
            fom=1.0,
            oxygen=3.0,
            duration=5.0,
            trajectory=_temperature_trajectory(750.0),
            created_at="2026-10-20T00:00:04Z",
        ),
        "missing-metric": _store_point(
            store,
            candidate_id="missing-metric",
            furnace=9.0,
            fom=9.0,
            oxygen=9.0,
            duration=9.0,
            trajectory=None,
            created_at="2026-10-20T00:00:05Z",
        ),
        "old-cache": _store_point(
            store,
            candidate_id="old-cache",
            furnace=7.0,
            fom=1.2,
            oxygen=4.0,
            duration=7.0,
            trajectory=None,
            created_at="2026-10-20T00:00:06Z",
        ),
    }
    cache_path = run_dir / "cache.sqlite"
    with sqlite3.connect(cache_path) as conn:
        conn.execute(
            """
            UPDATE results
            SET feasible = 0, failure_category = ?
            WHERE candidate_id = ?
            """,
            ("infeasible_recipe", "infeasible"),
        )
        conn.execute(
            """
            UPDATE results
            SET feasible = 0, failure_category = ?
            WHERE candidate_id = ?
            """,
            ("backend_unavailable", "backend-unavailable"),
        )
        conn.execute(
            """
            UPDATE results
            SET objectives = ?
            WHERE candidate_id = ?
            """,
            (
                json.dumps([
                    {
                        "metric": "oxygen_kg",
                        "sense": "maximize",
                        "value": 99,
                        "value_status": "unavailable",
                        "units": "kg",
                        "ordinal": 0,
                    },
                    {
                        "metric": "furnace_time_h",
                        "sense": "minimize",
                        "value": 99,
                        "value_status": "unavailable",
                        "units": "h",
                        "ordinal": 1,
                    },
                    {
                        "metric": _FOM,
                        "sense": "maximize",
                        "value": 99,
                        "value_status": "unavailable",
                        "units": "kg/h",
                        "ordinal": 2,
                    },
                    {
                        "metric": "duration_h",
                        "sense": "minimize",
                        "value": 3.0,
                        "value_status": "available",
                        "units": "h",
                        "ordinal": 3,
                    },
                ]),
                "missing-metric",
            ),
        )
    (run_dir / "pareto.json").write_text(
        json.dumps({
            "member_schema_version": 1,
            "objectives": [
                {
                    "metric": "oxygen_kg",
                    "sense": "maximize",
                    "units": "kg",
                    "ordinal": 0,
                },
                {
                    "metric": "furnace_time_h",
                    "sense": "minimize",
                    "units": "h",
                    "ordinal": 1,
                },
                {
                    "metric": _FOM,
                    "sense": "maximize",
                    "units": "kg/h",
                    "ordinal": 2,
                },
            ],
            "pareto": [
                {
                    "trajectory_cache_key": keys["front-a"],
                    "cache_key": "decoy-not-a-stored-point",
                },
                {"trajectory_cache_key": keys["front-b"]},
            ],
        }),
        encoding="utf-8",
    )
    return keys


def _seed_fallback(run_dir: Path) -> str:
    run_dir.mkdir(parents=True, exist_ok=True)
    store = ResultStore(run_dir / "cache.sqlite")
    spec = _base_spec(recipe_id="recipe-fallback-a")
    scored = _scored(
        spec,
        candidate_id="fallback-a",
        oxygen=6.0,
        objectives=ObjectiveVector((
            ObjectiveValue("oxygen_kg", "maximize", 6.0, "kg", ordinal=0),
            ObjectiveValue("duration_h", "minimize", 11.0, "h", ordinal=1),
        )),
        trace=_traced(None),
    )
    store.store(spec, scored, created_at="2026-10-20T00:01:00Z")
    (run_dir / "pareto.json").write_text(
        json.dumps({
            "objectives": [
                {
                    "metric": "oxygen_kg",
                    "sense": "maximize",
                    "units": "kg",
                    "ordinal": 0,
                }
            ],
            "pareto": [{"trajectory_cache_key": scored.cache_key}],
        }),
        encoding="utf-8",
    )
    return str(scored.cache_key)


@pytest.fixture
def tradeoff(client):
    root = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    keys = _seed_tradeoff(root / "tradeoff-run")
    fallback = _seed_fallback(root / "tradeoff-fallback")
    return {
        "root": root,
        "keys": keys,
        "fallback": fallback,
        "cache": root / "tradeoff-run" / "cache.sqlite",
    }


def _by_candidate(payload: dict) -> dict:
    return {point["candidate_id"]: point for point in payload["points"]}


def _embedded(html: str, element_id: str) -> dict:
    match = re.search(
        rf'<script type="application/json" id="{element_id}">(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert match is not None, element_id
    return json.loads(match.group(1))


def _row(html: str, candidate_id: str) -> str:
    marker = f'data-candidate="{candidate_id}"'
    assert marker in html
    return html.split(marker, 1)[1].split("</tr>", 1)[0]


def test_pareto_api_classifies_stored_points(client, tradeoff) -> None:
    response = client.get("/api/optimizer/runs/tradeoff-run/pareto")

    assert response.status_code == 200
    body = response.get_json()
    assert body["primary_objective"] == "oxygen_kg"
    assert body["x"] == "furnace_time_h"
    assert body["y"] == _FOM
    assert body["default_x"] == "furnace_time_h"
    assert body["default_y"] == _FOM
    points = _by_candidate(body)
    assert list(points) == [
        "backend-unavailable",
        "front-a",
        "front-b",
        "infeasible",
        "missing-metric",
        "old-cache",
    ]
    assert points["front-a"]["on_pareto_front"] is True
    assert points["front-b"]["on_pareto_front"] is True
    assert points["front-a"]["plot_status"] == "feasible"
    assert points["front-a"]["plotted"] is True
    assert points["front-a"]["x"] == 4
    assert points["front-a"]["y"] == 2.5
    assert points["infeasible"]["plot_status"] == "infeasible"
    assert points["infeasible"]["plotted"] is True
    assert points["infeasible"]["on_pareto_front"] is False
    assert points["backend-unavailable"]["plot_status"] == "unavailable"
    assert points["backend-unavailable"]["plotted"] is True
    assert points["backend-unavailable"]["failure_category"] == "backend_unavailable"
    missing = points["missing-metric"]
    assert missing["plotted"] is False
    assert missing["x"] is None
    assert missing["y"] is None
    assert missing["x"] != 0
    assert missing["objectives"]["furnace_time_h"] is None
    assert missing["objectives"]["duration_h"] == 3
    assert "decoy-not-a-stored-point" not in {
        point["cache_key"] for point in body["points"]
    }
    assert points["old-cache"]["trajectory_message"] == _UNAVAILABLE
    assert points["front-a"]["detail_url"].endswith(
        f"/results/{tradeoff['keys']['front-a']}"
    )


def test_pareto_api_unknown_metric_keeps_the_default_axes(client, tradeoff) -> None:
    response = client.get(
        "/api/optimizer/runs/tradeoff-run/pareto",
        query_string={"x": "nope-x", "y": "nope-y"},
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body["axis_error"] == (
        "unknown objective metric: nope-x; unknown objective metric: nope-y"
    )
    assert body["x"] == "furnace_time_h"
    assert body["y"] == _FOM
    assert _by_candidate(body)["front-a"]["x"] == 4


def test_pareto_api_honours_explicit_axes(client, tradeoff) -> None:
    response = client.get(
        "/api/optimizer/runs/tradeoff-run/pareto",
        query_string={"x": "duration_h", "y": "oxygen_kg"},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["x"] == "duration_h"
    assert body["y"] == "oxygen_kg"
    assert _by_candidate(body)["front-a"]["x"] == 4.5
    assert _by_candidate(body)["front-a"]["y"] == 10


def test_fallback_run_uses_duration_and_its_primary_objective(client, tradeoff) -> None:
    response = client.get("/api/optimizer/runs/tradeoff-fallback/pareto")

    assert response.status_code == 200
    body = response.get_json()
    metrics = {item["metric"] for item in body["metrics"]}
    assert body["x"] == "duration_h"
    assert body["y"] == "oxygen_kg"
    assert body["primary_objective"] == "oxygen_kg"
    assert "furnace_time_h" not in metrics
    assert _FOM not in metrics
    assert _by_candidate(body)["fallback-a"]["on_pareto_front"] is True


def test_missing_run_is_404(client, tradeoff) -> None:
    page = client.get("/optimizer/runs/no-such-run/pareto")
    api = client.get("/api/optimizer/runs/no-such-run/pareto")

    assert page.status_code == 404
    assert "Optimizer Result Not Found" in page.get_data(as_text=True)
    assert api.status_code == 404
    assert api.get_json()["error"] == "Optimizer run not found"


def test_absent_pareto_document_marks_no_front(client) -> None:
    root = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    run_dir = root / "tradeoff-bare"
    run_dir.mkdir(parents=True)
    _store_point(
        ResultStore(run_dir / "cache.sqlite"),
        candidate_id="bare",
        furnace=1.0,
        fom=1.0,
        oxygen=1.0,
        duration=1.0,
        trajectory=None,
        created_at="2026-10-20T00:02:00Z",
    )

    response = client.get("/api/optimizer/runs/tradeoff-bare/pareto")

    assert response.status_code == 200
    body = response.get_json()
    assert body["pareto_document"] == "absent"
    assert body["front_note"] == "No pareto.json for this run; no front is marked."
    assert body["points"]
    assert all(point["on_pareto_front"] is False for point in body["points"])


def test_pareto_page_renders_controls_legend_and_links(client, tradeoff) -> None:
    response = client.get("/optimizer/runs/tradeoff-run/pareto")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="pareto-x"' in html
    assert 'for="pareto-x"' in html
    assert 'id="pareto-y"' in html
    assert 'for="pareto-y"' in html
    assert 'value="furnace_time_h" selected' in html
    assert f'value="{_FOM}" selected' in html
    assert 'value="duration_h" selected' not in html
    assert 'value="duration_h"' in html
    assert 'type="submit"' in html
    assert "Update axes" in html
    assert "Compare trajectories" in html
    assert "plot-status-feasible" in html
    assert "plot-status-infeasible" in html
    assert "plot-status-unavailable" in html
    assert "plot-status-front" in html
    assert "table-scroll" in html
    assert 'id="pareto-scatter"' in html
    assert 'for="compare-1"' in html
    assert "Select backend-unavailable" in html
    assert "Select front-a" in html
    embedded = _embedded(html, "pareto-payload")
    assert embedded["x"] == "furnace_time_h"
    assert embedded["y"] == _FOM
    for point in embedded["points"]:
        assert point["detail_url"] in html
    missing = _row(html, "missing-metric")
    assert "unavailable" in missing
    assert ">0<" not in missing
    backend = _row(html, "backend-unavailable")
    assert "plot-status-unavailable" in backend
    assert "plot-status-infeasible" not in backend
    assert "plot-status-infeasible" in _row(html, "infeasible")
    assert "plot-status-front" in _row(html, "front-a")
    assert _UNAVAILABLE in _row(html, "old-cache")


def test_compare_api_overlays_labels_and_selected_series(client, tradeoff) -> None:
    keys = tradeoff["keys"]
    response = client.get(
        "/api/optimizer/runs/tradeoff-run/compare",
        query_string=[
            ("cache_key", keys["front-a"]),
            ("cache_key", keys["front-b"]),
        ],
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["chart"] is True
    assert body["series_id"] == "T_C"
    points = _by_candidate(body)
    assert points["front-a"]["label"] == (
        "front-a · furnace_time_h=4 · separated_product_kg_per_furnace_h=2.5"
    )
    assert points["front-b"]["label"] == (
        "front-b · furnace_time_h=8 · separated_product_kg_per_furnace_h=2"
    )
    assert [row["value"] for row in points["front-a"]["series_points"]] == [900, 1100]
    assert points["front-b"]["series_points"][0]["value"] == 700

    vapour = client.get(
        "/api/optimizer/runs/tradeoff-run/compare",
        query_string=[
            ("cache_key", keys["front-a"]),
            ("cache_key", keys["front-b"]),
            ("series", "vapour_kg_h:Na"),
        ],
    )
    assert vapour.status_code == 200
    vapour_points = _by_candidate(vapour.get_json())
    assert [row["value"] for row in vapour_points["front-a"]["series_points"]] == [
        0.2,
        0.4,
    ]
    assert 900 not in [
        row["value"] for row in vapour_points["front-a"]["series_points"]
    ]
    assert all(
        row["value"] is None for row in vapour_points["front-b"]["series_points"]
    )


def test_compare_api_rejects_count_duplicates_and_missing_keys(client, tradeoff) -> None:
    keys = tradeoff["keys"]
    one = client.get(
        "/api/optimizer/runs/tradeoff-run/compare",
        query_string={"cache_key": keys["front-a"]},
    )
    five = client.get(
        "/api/optimizer/runs/tradeoff-run/compare",
        query_string=[
            ("cache_key", keys["front-a"]),
            ("cache_key", keys["front-b"]),
            ("cache_key", keys["infeasible"]),
            ("cache_key", keys["backend-unavailable"]),
            ("cache_key", keys["old-cache"]),
        ],
    )
    duplicate = client.get(
        "/api/optimizer/runs/tradeoff-run/compare",
        query_string=[
            ("cache_key", keys["front-a"]),
            ("cache_key", keys["front-a"]),
        ],
    )
    missing = client.get(
        "/api/optimizer/runs/tradeoff-run/compare",
        query_string=[
            ("cache_key", keys["front-a"]),
            ("cache_key", "missing-key"),
        ],
    )

    for response in (one, five, duplicate):
        assert response.status_code == 400
        body = response.get_json()
        assert body["error"] == "compare requires 2 to 4 points"
        assert body["error_type"] == "compare_count"
        assert "compare" not in body
    assert missing.status_code == 404
    assert missing.get_json()["error"] == "optimizer result not found: missing-key"
    assert missing.get_json()["error_type"] == "compare_point"


def test_compare_api_unknown_series_does_not_fall_back(client, tradeoff) -> None:
    keys = tradeoff["keys"]
    response = client.get(
        "/api/optimizer/runs/tradeoff-run/compare",
        query_string=[
            ("cache_key", keys["front-a"]),
            ("cache_key", keys["front-b"]),
            ("series", "not-a-series"),
        ],
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] == "unknown trajectory series: not-a-series"
    assert body["error_type"] == "unknown_trajectory_series"
    assert body["compare"]["series_id"] is None
    assert body["compare"]["chart"] is False
    assert all(
        point["series_points"] is None for point in body["compare"]["points"]
    )


def test_compare_reports_unavailable_trajectories_without_fake_zeros(
    client, tradeoff
) -> None:
    keys = tradeoff["keys"]
    mixed = client.get(
        "/api/optimizer/runs/tradeoff-run/compare",
        query_string=[
            ("cache_key", keys["front-a"]),
            ("cache_key", keys["old-cache"]),
        ],
    )
    assert mixed.status_code == 200
    mixed_points = _by_candidate(mixed.get_json())
    assert mixed_points["front-a"]["series_points"][0]["value"] == 900
    assert mixed_points["old-cache"]["series_points"] is None
    assert mixed_points["old-cache"]["message"] == _UNAVAILABLE

    empty = client.get(
        "/api/optimizer/runs/tradeoff-run/compare",
        query_string=[
            ("cache_key", keys["old-cache"]),
            ("cache_key", keys["missing-metric"]),
        ],
    )
    assert empty.status_code == 200
    empty_body = empty.get_json()
    assert empty_body["chart"] is False
    assert [point["series_points"] for point in empty_body["points"]] == [None, None]
    assert [point["message"] for point in empty_body["points"]] == [
        _UNAVAILABLE,
        _UNAVAILABLE,
    ]

    page = client.get(
        "/optimizer/runs/tradeoff-run/pareto",
        query_string=[
            ("cache_key", keys["old-cache"]),
            ("cache_key", keys["missing-metric"]),
        ],
    )
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert html.count(_UNAVAILABLE) >= 2
    assert 'id="compare-chart"' not in html


def test_compare_page_renders_the_overlay(client, tradeoff) -> None:
    keys = tradeoff["keys"]
    response = client.get(
        "/optimizer/runs/tradeoff-run/pareto",
        query_string=[
            ("cache_key", keys["front-a"]),
            ("cache_key", keys["front-b"]),
            ("series", "vapour_kg_h:Na"),
        ],
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="compare-chart"' in html
    assert '<optgroup label="Vapour">' in html
    assert "Vapour kg/h · Na" in html
    assert "Capture kg/h · stage 1" in html
    assert "Wall deposit kg/h · SiO" in html
    assert "Temperature (T_C)" in html
    assert (
        "front-a · furnace_time_h=4 · separated_product_kg_per_furnace_h=2.5"
        in html
    )
    assert 'id="compare-series"' in html
    assert 'for="compare-series"' in html
    assert "Show series" in html
    embedded = _embedded(html, "compare-payload")
    assert [row["value"] for row in _by_candidate(embedded)["front-a"]["series_points"]] == [
        0.2,
        0.4,
    ]


def test_detail_charts_an_available_trajectory(client, tradeoff) -> None:
    detail = (
        "/optimizer/runs/tradeoff-run/results/"
        + tradeoff["keys"]["front-a"]
    )
    response = client.get(detail)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="optimizer-trajectory-chart"' in html
    assert 'id="trajectory-series"' in html
    assert 'for="trajectory-series"' in html
    assert '<optgroup label="Vapour">' in html
    assert "Vapour kg/h · Na" in html
    assert "Capture kg/h · stage 1" in html
    assert "Wall deposit kg/h · SiO" in html
    assert "Temperature (T_C)" in html
    assert 'type="submit"' in html
    payload = _embedded(html, "trajectory-payload")
    assert [row["value"] for row in payload["series_points"]] == [900, 1100]
    assert f"/optimizer/runs/tradeoff-run/pareto" in html
    downloaded = client.get(detail + "/recipe.yaml")
    assert downloaded.status_code == 200
    yaml_body = downloaded.get_data(as_text=True)
    assert "vapour_kg_h" not in yaml_body
    assert "wall_deposit_kg_h" not in yaml_body


def test_detail_says_when_the_trajectory_is_unavailable(client, tradeoff) -> None:
    detail = (
        "/optimizer/runs/tradeoff-run/results/"
        + tradeoff["keys"]["old-cache"]
        + "?series=nope"
    )
    response = client.get(detail)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "trajectory unavailable for this point" in html
    assert 'id="optimizer-trajectory-chart"' not in html
    assert 'id="trajectory-series"' not in html


def test_detail_unknown_series_on_an_available_point_is_400(client, tradeoff) -> None:
    detail = (
        "/optimizer/runs/tradeoff-run/results/"
        + tradeoff["keys"]["front-a"]
        + "?series=nope"
    )
    response = client.get(detail)

    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "unknown trajectory series: nope" in html
    assert 'id="optimizer-trajectory-chart"' not in html
    assert 'id="trajectory-series"' in html


def test_deleting_the_stored_trajectory_removes_the_chart(client, tradeoff) -> None:
    detail = (
        "/optimizer/runs/tradeoff-run/results/"
        + tradeoff["keys"]["front-a"]
    )
    before = client.get(detail)
    assert before.status_code == 200
    before_html = before.get_data(as_text=True)
    assert 'id="optimizer-trajectory-chart"' in before_html
    assert _embedded(before_html, "trajectory-payload")["series_points"][0]["value"] == 900

    with sqlite3.connect(tradeoff["cache"]) as conn:
        row = conn.execute(
            "SELECT result_blob FROM results WHERE candidate_id = ?",
            ("front-a",),
        ).fetchone()
        blob = json.loads(row[0])
        assert "trajectory" in blob
        del blob["trajectory"]
        conn.execute(
            "UPDATE results SET result_blob = ? WHERE candidate_id = ?",
            (json.dumps(blob), "front-a"),
        )

    after = client.get(detail)
    assert after.status_code == 200
    after_html = after.get_data(as_text=True)
    assert "trajectory unavailable for this point" in after_html
    assert 'id="optimizer-trajectory-chart"' not in after_html


def test_unreadable_trajectory_is_not_the_unavailable_sentence(client, tradeoff) -> None:
    detail = (
        "/optimizer/runs/tradeoff-run/results/"
        + tradeoff["keys"]["front-b"]
    )
    with sqlite3.connect(tradeoff["cache"]) as conn:
        row = conn.execute(
            "SELECT result_blob FROM results WHERE candidate_id = ?",
            ("front-b",),
        ).fetchone()
        blob = json.loads(row[0])
        del blob["trajectory"]["hours"][0]["T_C"]
        conn.execute(
            "UPDATE results SET result_blob = ? WHERE candidate_id = ?",
            (json.dumps(blob), "front-b"),
        )

    response = client.get(detail)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "trajectory unreadable for this point" in html
    assert "trajectory unavailable for this point" not in html
    assert 'id="optimizer-trajectory-chart"' not in html


def test_one_selected_point_keeps_the_pareto_page_and_reports_the_count(
    client, tradeoff
) -> None:
    response = client.get(
        "/optimizer/runs/tradeoff-run/pareto",
        query_string={"cache_key": tradeoff["keys"]["front-a"]},
    )

    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert 'id="compare-error"' in html
    assert "compare requires 2 to 4 points" in html
    assert 'id="pareto-scatter"' in html


def test_optimizer_index_links_every_local_run(client, tradeoff) -> None:
    response = client.get("/optimizer")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'class="tradeoff-run-list"' in html
    assert 'href="/optimizer/runs/tradeoff-run/pareto"' in html
    assert 'href="/optimizer/runs/tradeoff-fallback/pareto"' in html


def test_chart_assets_keep_shape_colour_and_the_simulator_snapshot() -> None:
    css = (_ROOT / "web/static/css/style.css").read_text(encoding="utf-8")
    feasible = css.split(".plot-status-feasible::before", 1)[1][:240]
    infeasible = css.split(".plot-status-infeasible::before", 1)[1][:320]
    unavailable = css.split(".plot-status-unavailable::before", 1)[1][:240]
    front = css.split(".plot-status-front::before", 1)[1][:160]
    assert "var(--accent)" in feasible
    assert "var(--danger)" in infeasible
    assert "var(--warning)" in unavailable
    assert "var(--text)" in front
    assert "overflow: hidden" in css.split(".tradeoff-chart {", 1)[1][:240]
    charts = (_ROOT / "web/static/js/simulator-charts.js").read_text(encoding="utf-8")
    assert "getElementById('chart-temperature')" in charts
    assert "getElementById('chart-pressure')" in charts
    assert "function regolithChartTheme(" in charts
    assert "color: chartFontColor" in charts
    tradeoff_js = (_ROOT / "web/static/js/optimizer-tradeoff.js").read_text(
        encoding="utf-8"
    )
    assert "connectgaps: false" in tradeoff_js
    assert "plotted === false" in tradeoff_js


def test_tradeoff_modules_do_not_score_or_recompute_the_front() -> None:
    for relative in ("web/optimizer_tradeoff.py", "web/routes.py"):
        source = (_ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "simulator.optimize.evaluate"
            ):
                forbidden.append(node.module)
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else getattr(func, "attr", "")
                )
                if name in {"evaluate", "evaluate_batch", "pareto_front"}:
                    forbidden.append(name)
        assert forbidden == []
        assert "pareto_front(" not in source
