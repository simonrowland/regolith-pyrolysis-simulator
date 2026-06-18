from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from flask import Flask
import pytest

from simulator.fidelity_vocabulary import EvidenceClass
from simulator.optimize.evaluate import RunReference
from simulator.optimize.evalspec import EvalSpec, cache_key, current_code_version
from simulator.optimize.evaluate import ScoredResult
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.results_store import ResultStore
from web import routes as web_routes


def _base_spec(**overrides: object) -> EvalSpec:
    data = {
        "recipe_id": "recipe-tier",
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


def _scored(spec: EvalSpec, *, candidate_id: str) -> ScoredResult:
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


def _patch_run_reference(
    run_dir: Path,
    key: str,
    *,
    cache_state: str,
    evidence_class: str = EvidenceClass.MELTS.value,
) -> None:
    payload = {
        "status": "ok",
        "cache_state": cache_state,
        "evidence_class": evidence_class,
        "backend_name": "alphamelts",
        "backend_status": "ok",
        "backend_authoritative": True,
        "product_summary": {"product_yield_table": _product_yield_table()},
    }
    with sqlite3.connect(run_dir / "cache.sqlite") as conn:
        conn.execute(
            "UPDATE results SET run_reference = ? WHERE cache_key = ?",
            (json.dumps(payload), key),
        )


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


def _product_yield_table() -> dict[str, object]:
    return {
        "status": "closed",
        "inputs": [{"kind": "input", "id": "feedstock", "label": "Feedstock", "kg": 1000.0}],
        "outputs": [{"kind": "output", "id": "oxygen", "label": "O2", "kg": 12.0}],
        "mass_closure": {
            "kind": "mass_closure",
            "label": "Mass closure",
            "mass_in_kg": 1000.0,
            "accountable_mass_out_kg": 1000.0,
            "products_out_kg": 12.0,
            "balance_error_pct": 0.0,
            "tolerance_pct": 5e-12,
            "status": "closed",
        },
    }


def _seed_tier_fixture(client) -> tuple[list[str], dict[str, int]]:
    runs_dir = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    rows = [
        (
            "run-certified",
            "candidate-certified",
            "cached_exact",
            "lunar_mare_low_ti",
            "lunar-mare-low-ti-objectives-v1",
        ),
        (
            "run-estimated-bucket",
            "candidate-estimated-bucket",
            "cached_physics_bucket",
            "lunar_mare_high_ti",
            "lunar-mare-high-ti-objectives-v1",
        ),
        (
            "run-estimated-interp",
            "candidate-estimated-interp",
            "cached_interpolated",
            "mars_basalt",
            "mars-basalt-objectives-v1",
        ),
    ]
    counts = {"CERTIFIED": 0, "ESTIMATED": 0}
    run_ids: list[str] = []
    for index, (
        run_name,
        candidate_id,
        cache_state,
        feedstock_id,
        profile_id,
    ) in enumerate(rows):
        run_dir = runs_dir / run_name
        run_dir.mkdir(parents=True)
        spec = _base_spec(
            recipe_id=f"recipe-{candidate_id}",
            profile_id=profile_id,
            feedstock_id=feedstock_id,
        )
        scored = ScoredResult(
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
                product_summary={"product_yield_table": _product_yield_table()},
            ),
            notes=("stored",),
        )
        store = ResultStore(run_dir / "cache.sqlite")
        store.store(spec, scored, created_at=f"2026-06-13T00:00:{index:02d}Z")
        _patch_run_reference(
            run_dir,
            scored.cache_key,
            cache_state=cache_state,
        )
        run_ids.append(run_name)
        if cache_state in {"cached_exact", "live_fill"}:
            counts["CERTIFIED"] += 1
        else:
            counts["ESTIMATED"] += 1
    return run_ids, counts


def test_optimizer_table_partial_renders_tier_badges(client) -> None:
    _run_ids, counts = _seed_tier_fixture(client)

    response = client.get("/partials/optimizer-table?limit=10")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "CERTIFIED / cached_exact" in html
    assert "ESTIMATED / cached_physics_bucket" in html
    assert "ESTIMATED / cached_interpolated" in html
    assert html.count("CERTIFIED /") == counts["CERTIFIED"]
    assert html.count("ESTIMATED /") == counts["ESTIMATED"]


def test_optimizer_detail_renders_certified_and_compute_button(client) -> None:
    run_ids, _counts = _seed_tier_fixture(client)
    run_id = run_ids[0]
    with sqlite3.connect(
        Path(client.application.config["OPTIMIZER_RUNS_DIR"])
        / run_id
        / "cache.sqlite"
    ) as conn:
        key = conn.execute(
            "SELECT cache_key FROM results WHERE candidate_id = ?",
            ("candidate-certified",),
        ).fetchone()[0]

    response = client.get(f"/optimizer/runs/{run_id}/results/{key}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Cache tier" in html
    assert "CERTIFIED / cached_exact" in html
    assert "COMPUTE (exact certify)" in html
    assert 'hx-post="/optimizer/certify"' in html
