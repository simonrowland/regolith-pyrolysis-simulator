from __future__ import annotations

from pathlib import Path

from simulator.grind_preflight import assert_strict_vapor_result_store
from simulator.optimize import study
from simulator.optimize.evalspec import EvalSpec, cache_key, current_code_version
from simulator.optimize.evaluate import RunReference, ScoredResult
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.results_store import ResultStore


def _source_report(source: str = "vaporock") -> dict[str, object]:
    return {
        "species": {"Na": source, "SiO": source},
        "summary": {source: {"count": 2, "percentage": 100.0}},
        "total_species": 2,
    }


def _spec() -> EvalSpec:
    return EvalSpec(
        recipe_id="recipe-id",
        feedstock_recipe_digest="feedstock-recipe-digest",
        feedstock_id="lunar_mare_low_ti",
        profile_id="source-gate-v1",
        fidelity="fast",
        code_version=current_code_version(),
        data_digests={
            "setpoints": "setpoints-digest",
            "feedstocks": "feedstock-digest",
            "materials": "materials-digest",
            "vapor_pressures": "vapor-digest",
            "species_catalog": "species-catalog-digest",
            "profile": "profile-digest",
        },
        campaign="C2A_continuous",
        backend_name="cached-real",
        vapor_pressure_provider_id="vaporock",
        allow_fallback_vapor=False,
        force_builtin_vapor_pressure=False,
    )


def _objectives() -> ObjectiveVector:
    return ObjectiveVector(
        (
            ObjectiveValue("oxygen_kg", "maximize", 10.0, "kg", ordinal=0),
            ObjectiveValue("energy_kWh", "minimize", 2.0, "kWh", ordinal=1),
        )
    )


def _margin() -> GateMargin:
    return GateMargin(
        gate="delivered_stream_purity",
        feasible=True,
        margin=0.25,
        threshold=ThresholdSpec(
            id="delivered-stream-purity-threshold",
            value=0.95,
            units="fraction",
            source="profile",
            source_ref="test profile",
        ),
        observed=0.98,
        detail="test margin",
    )


def test_strip_heavy_result_preserves_vapor_source_report_for_store_gate(
    tmp_path: Path,
) -> None:
    spec = _spec()
    trace = {
        "backend_status": "ok",
        "vapor_pressure_source_report": _source_report("vaporock"),
        "vapor_pressure_provider_id": "vaporock",
        "allow_fallback_vapor": False,
        "force_builtin_vapor_pressure": False,
        "warnings": [
            "WARNING: SiO vapor pressure uses a backsolved VapoRock "
            "fallback (curve-fit), NOT first-principles"
        ],
    }
    scored = ScoredResult(
        candidate_id="candidate-a",
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=_objectives(),
        feasibility_margins={"delivered_stream_purity": _margin()},
        failing_gates=(),
        run_reference=RunReference(
            status="ok",
            trace=trace,
            product_summary={"oxygen_kg": 10.0},
        ),
        notes=("stored",),
    )
    db_path = tmp_path / "results.sqlite"
    store = ResultStore(
        db_path,
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )

    store.store(
        spec,
        study._strip_heavy_result(scored),
        created_at="2026-06-15T00:00:00Z",
    )

    loaded = store.lookup(spec)
    assert loaded is not None
    assert loaded.run_reference is not None
    assert loaded.run_reference.trace["vapor_pressure_source_report"] == _source_report(
        "vaporock"
    )
    assert assert_strict_vapor_result_store(db_path) == {
        "rows": 1,
        "vapor_active_rows": 1,
        "source_reports": 1,
    }


def test_reference_trace_fallback_preserves_vapor_source_report_for_store_gate(
    tmp_path: Path,
) -> None:
    spec = _spec()
    trace = {
        "backend_status": "ok",
        "vapor_pressure_source_report": _source_report("vaporock"),
        "vapor_pressure_provider_id": "vaporock",
        "allow_fallback_vapor": False,
        "force_builtin_vapor_pressure": False,
        "non_jsonable": object(),
    }
    scored = ScoredResult(
        candidate_id="candidate-fallback",
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=_objectives(),
        feasibility_margins={"delivered_stream_purity": _margin()},
        failing_gates=(),
        run_reference=RunReference(
            status="ok",
            trace=trace,
            product_summary={"oxygen_kg": 10.0},
        ),
        notes=("stored",),
    )
    db_path = tmp_path / "results.sqlite"
    store = ResultStore(
        db_path,
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )

    store.store(
        spec,
        scored,
        created_at="2026-06-15T00:00:00Z",
    )

    loaded = store.lookup(spec)
    assert loaded is not None
    assert loaded.run_reference is not None
    assert loaded.run_reference.trace["vapor_pressure_source_report"] == _source_report(
        "vaporock"
    )
    assert loaded.run_reference.trace["vapor_pressure_provider_id"] == "vaporock"
    assert loaded.run_reference.trace["allow_fallback_vapor"] is False
    assert loaded.run_reference.trace["force_builtin_vapor_pressure"] is False
    assert assert_strict_vapor_result_store(db_path) == {
        "rows": 1,
        "vapor_active_rows": 1,
        "source_reports": 1,
    }
