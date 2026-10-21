"""Per-point trajectory stored with an optimizer result."""

from __future__ import annotations

import json
import math
import sqlite3
from types import SimpleNamespace

import pytest

from simulator.accounting.formulas import resolve_species_formula
from simulator.backend_names import ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
from simulator.diagnostics import _flatten_wall_deposit_species_kg
from simulator.optimize.evalspec import EvalSpec, cache_key, current_code_version
from simulator.optimize.evaluate import RunReference, ScoredResult
from simulator.optimize.import_bundle import import_study_bundle
from simulator.optimize.objective import (
    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
    ObjectiveValue,
    ObjectiveVector,
)
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.optimize.results_store import ResultStore, _result_blob
from simulator.optimize.save_bundle import export_study_bundle
from simulator.optimize.study import StudyRecord
from simulator.optimize import study
from simulator.optimize.trajectory import (
    TRAJECTORY_SCHEMA_VERSION,
    TRAJECTORY_STATUS_AVAILABLE,
    TRAJECTORY_STATUS_UNAVAILABLE,
    TrajectorySchemaError,
    decode_stored_trajectory,
    project_trajectory,
    read_trajectory,
    trajectory_stored_bytes,
)
from simulator.runner import _MBAR_TO_BAR

_STORED_BYTE_BOUND = 200_000
_HOUR_KEYS = (
    "hour",
    "campaign",
    "T_C",
    "p_total_mbar",
    "pO2_mbar",
    "vapour_kg_h",
    "capture_kg_h",
    "wall_deposit_kg_h",
)

PROFILE = {
    "profile_id": "trajectory-study",
    "profile_schema_version": "profile-schema-v1",
    "feedstock": "lunar_mare_low_ti",
    "objectives": [
        {
            "metric": "oxygen_kg",
            "sense": "maximize",
            "units": "kg",
            "weight": 0.6,
            "rationale": "test oxygen objective evidence",
        },
        {
            "metric": ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
            "sense": "minimize",
            "units": "kWh",
            "weight": 0.4,
            "rationale": "test energy objective evidence",
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
            "id": "trajectory-c0-seed",
            "source_campaign": "C0",
            "patch": {"campaigns": {"C0": {"temp_range_C": [900, 950]}}},
        }
    ],
}


def _margin() -> GateMargin:
    return GateMargin(
        gate="delivered_stream_purity",
        feasible=True,
        margin=0.25,
        threshold=ThresholdSpec(
            id="delivered_stream_purity-threshold",
            value=0.95,
            units="fraction",
            source="profile",
            source_ref="test profile",
        ),
        observed=0.98,
        detail="test margin",
    )


def _spec() -> EvalSpec:
    return EvalSpec(
        recipe_id="recipe-id",
        feedstock_recipe_digest="feedstock-recipe-digest",
        feedstock_id="lunar_mare_low_ti",
        profile_id="trajectory-test",
        fidelity="internal-analytical",
        code_version=current_code_version(),
        data_digests={
            "setpoints": "setpoints-digest",
            "feedstocks": "feedstock-digest",
            "foulant_thermo": "foulant-thermo-digest",
            "materials": "materials-digest",
            "vapor_pressures": "vapor-digest",
            "species_catalog": "species-catalog-digest",
            "profile": "profile-digest",
        },
        chemistry_kernel={
            "engine": "builtin",
            "allow_builtin_fallback": False,
            "pressure_Pa": 0.001,
        },
        campaign="C0",
        hours=1,
        mass_kg=1000.0,
        backend_name="alphamelts",
    )


def _scored(trace: dict[str, object]) -> ScoredResult:
    spec = _spec()
    return ScoredResult(
        candidate_id="trajectory-point",
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue("oxygen_kg", "maximize", 10.0, "kg", ordinal=0),
                ObjectiveValue("energy_kWh", "minimize", 2.0, "kWh", ordinal=1),
            )
        ),
        feasibility_margins={"delivered_stream_purity": _margin()},
        run_reference=RunReference(
            status="ok",
            trace=trace,
            product_summary={
                "backend_name": "alphamelts",
                "oxygen_kg": 10.0,
                "furnace_amortization_status": "available",
                "furnace_amortization_batch_cost_equivalents": 0.0,
                "mass_closure": {
                    "status": "closed",
                    "mass_balance_error_pct": 0.0,
                },
            },
            backend_name="alphamelts",
            backend_status="ok",
            backend_authoritative=True,
            evidence_class="melts",
        ),
    )


def _admissible_trace(extra: dict[str, object] | None = None) -> dict[str, object]:
    trace: dict[str, object] = {
        "backend_status": "ok",
        "backend_authoritative": True,
        "snapshots": [{"mass_balance_error_pct": 0.0}],
    }
    if extra:
        trace.update(extra)
    return trace


def _snapshot(**kwargs: object) -> SimpleNamespace:
    payload = {"duration_h": 1.0, "condensed_by_stage_species_delta": {}}
    payload.update(kwargs)
    return SimpleNamespace(**payload)


def test_projection_copies_report_fields_and_omits_zeros() -> None:
    raw_vapour = 1.23456789e-4
    raw_total_bar = 1.25e-6
    raw_po2_bar = 4.5e-9
    row = {
        "hour": 3,
        "campaign": "C3",
        "T_C": 1320.1234567,
        "P_total_bar": raw_total_bar,
        "pO2_bar": raw_po2_bar,
        "vapor_species_kg_hr": {"Na": raw_vapour, "He": 0.0, "K": 1.0e-15},
        "wall_deposit_delta_kg": {
            "stage_1_to_stage_2": {"SiO2": 2.0, "Ar": 0.0},
            "stage_2_to_stage_3": {"SiO2": 1.0},
        },
        "fe_redox_split": {
            "native_fe_partition": {"native_fe_tap_mol": 1.0},
        },
    }
    snapshot = _snapshot(
        duration_h=2.0,
        condensed_by_stage_species_delta={
            (1, "Fe"): 0.2,
            (1, "Na"): 0.05,
            (4, "Na"): 0.05,
            (2, "K"): 0.0,
        },
    )

    hour = project_trajectory([row], [snapshot])["hours"][0]

    assert hour["hour"] == 3
    assert hour["campaign"] == "C3"
    assert math.isclose(hour["T_C"], 1320.1234567, rel_tol=5e-6)
    assert hour["T_C"] != 1320.1234567
    assert math.isclose(
        hour["p_total_mbar"],
        raw_total_bar / _MBAR_TO_BAR,
        rel_tol=5e-6,
    )
    assert math.isclose(
        hour["pO2_mbar"],
        raw_po2_bar / _MBAR_TO_BAR,
        rel_tol=5e-6,
    )
    assert hour["p_total_mbar"] != hour["pO2_mbar"]
    assert set(hour["vapour_kg_h"]) == {"Na"}
    assert math.isclose(hour["vapour_kg_h"]["Na"], raw_vapour, rel_tol=5e-6)
    assert hour["vapour_kg_h"]["Na"] != raw_vapour
    flattened = _flatten_wall_deposit_species_kg(row["wall_deposit_delta_kg"])
    assert hour["wall_deposit_kg_h"]["SiO2"] == pytest.approx(
        flattened["SiO2"] / 2.0
    )
    assert "Ar" not in hour["wall_deposit_kg_h"]
    assert hour["capture_kg_h"]["1"] == pytest.approx(0.25 / 2.0)
    assert hour["capture_kg_h"]["4"] == pytest.approx(0.05 / 2.0)
    assert "2" not in hour["capture_kg_h"]
    expected_tap = (
        resolve_species_formula("Fe").molar_mass_kg_per_mol() / 2.0
    )
    assert hour["capture_kg_h"]["liquid_fe_tapped"] == pytest.approx(
        expected_tap,
        rel=5e-6,
    )


def test_wall_rate_field_wins_over_delta() -> None:
    row = {
        "hour": 1,
        "campaign": "C0",
        "T_C": 1000.0,
        "wall_deposit_rate_by_species_kg_h": {"SiO2": 0.5},
        "wall_deposit_delta_kg": {"seg": {"SiO2": 9.0}},
    }

    hour = project_trajectory([row], [_snapshot(duration_h=2.0)])["hours"][0]

    assert hour["wall_deposit_kg_h"] == {"SiO2": 0.5}


def test_missing_report_fields_stay_null_and_do_not_raise() -> None:
    trajectory = project_trajectory(
        (
            "not-a-row",
            {
                "hour": 2,
                "reduced_real_cache_state": "cached_exact",
                "P_total_bar": float("nan"),
                "pO2_bar": float("inf"),
            },
        ),
        [
            _snapshot(
                condensed_by_stage_species_delta={(1, "Na"): 9.0},
            ),
            _snapshot(
                condensed_by_stage_species_delta={(4, "K"): 0.3},
            ),
        ],
    )

    assert trajectory["schema_version"] == TRAJECTORY_SCHEMA_VERSION
    assert trajectory["status"] == TRAJECTORY_STATUS_AVAILABLE
    assert len(trajectory["hours"]) == 1
    hour = trajectory["hours"][0]
    assert hour["hour"] == 2
    assert hour["campaign"] is None
    assert hour["T_C"] is None
    assert hour["p_total_mbar"] is None
    assert hour["pO2_mbar"] is None
    assert hour["vapour_kg_h"] == {}
    assert hour["capture_kg_h"] == {"4": 0.3}
    assert hour["wall_deposit_kg_h"] == {}
    assert set(hour) == set(_HOUR_KEYS)


def test_empty_run_is_available_and_distinct_from_unavailable() -> None:
    empty = project_trajectory(())

    assert empty == {
        "schema_version": TRAJECTORY_SCHEMA_VERSION,
        "status": TRAJECTORY_STATUS_AVAILABLE,
        "hours": [],
    }
    assert decode_stored_trajectory(None)["status"] == TRAJECTORY_STATUS_UNAVAILABLE
    assert (
        decode_stored_trajectory(
            {"schema_version": 2, "status": TRAJECTORY_STATUS_AVAILABLE, "hours": []}
        )["status"]
        == TRAJECTORY_STATUS_UNAVAILABLE
    )


@pytest.mark.parametrize("key", _HOUR_KEYS)
def test_dropped_hour_field_fails(key: str) -> None:
    trajectory = project_trajectory(
        [{"hour": 1, "campaign": "C0", "T_C": 900.0, "P_total_bar": 1.0e-6}]
    )
    del trajectory["hours"][0][key]

    with pytest.raises(TrajectorySchemaError):
        decode_stored_trajectory(trajectory)


def test_225h_trajectory_stays_under_byte_bound() -> None:
    species = (
        "Na",
        "K",
        "Fe",
        "SiO",
        "Mg",
        "Mn",
        "Cr",
        "Al",
        "Ca",
        "Ti",
        "O2",
        "Zn",
    )
    rows = []
    snapshots = []
    for hour in range(1, 226):
        rows.append(
            {
                "hour": hour,
                "campaign": "C0" if hour < 100 else "C3",
                "T_C": 900.0 + hour,
                "P_total_bar": 1.23456789e-6,
                "pO2_bar": 9.87654321e-9,
                "vapor_species_kg_hr": {
                    name: 1.23456789e-4 * (index + 1)
                    for index, name in enumerate(species)
                }
                | {"He": 0.0},
                "wall_deposit_delta_kg": {
                    "stage_1_to_stage_2": {
                        name: 2.34567891e-5 * (index + 1)
                        for index, name in enumerate(species[:8])
                    }
                },
                "fe_redox_split": {
                    "native_fe_partition": {
                        "native_fe_tap_mol": 0.01 if hour % 10 == 0 else 0.0
                    }
                },
            }
        )
        snapshots.append(
            _snapshot(
                condensed_by_stage_species_delta={
                    (1, "Na"): 0.002,
                    (1, "K"): 0.001,
                    (3, "SiO"): 0.0004,
                    (4, "Fe"): 0.0001,
                    (2, "Zn"): 0.0,
                }
            )
        )

    trajectory = project_trajectory(rows, snapshots)
    size = trajectory_stored_bytes(trajectory)

    assert len(trajectory["hours"]) == 225
    assert "He" not in trajectory["hours"][0]["vapour_kg_h"]
    assert "Na" in trajectory["hours"][0]["vapour_kg_h"]
    assert "liquid_fe_tapped" in trajectory["hours"][9]["capture_kg_h"]
    assert "liquid_fe_tapped" not in trajectory["hours"][0]["capture_kg_h"]
    assert 10_000 < size < _STORED_BYTE_BOUND


def test_light_trace_keeps_trajectory_not_full_per_hour() -> None:
    trajectory = project_trajectory(
        [{"hour": 1, "campaign": "C0", "T_C": 900.0}]
    )
    scored = ScoredResult(
        candidate_id="light",
        eval_spec=None,
        cache_key=None,
        feasible=False,
        run_reference=RunReference(
            status="ok",
            trace={
                "backend_status": "ok",
                "backend_authoritative": True,
                "per_hour": [
                    {"hour": 1, "full_run_payload": {"must": "not leak"}}
                ],
                "trajectory": trajectory,
            },
            backend_status="ok",
            backend_authoritative=True,
        ),
    )

    light = study._strip_heavy_result(scored)

    assert light.run_reference is not None
    stored = light.run_reference.trace
    assert stored["trajectory"]["status"] == TRAJECTORY_STATUS_AVAILABLE
    assert stored["trajectory"]["hours"][0]["campaign"] == "C0"
    assert "per_hour" not in stored
    summary = study._run_reference_trace_summary(light.run_reference)
    assert "trajectory" not in summary


def test_pareto_payload_links_cache_key_without_embedding_hours() -> None:
    record = StudyRecord(
        candidate_id="point-1",
        patch=RecipePatch({}),
        feasible=True,
        status="ok",
        objectives={"oxygen_kg": 1.0},
        feasibility_margins={},
        cache_key="cache-1",
        trace_summary={"backend_status": "ok"},
    )

    payload = study._record_payload(record, RecipeSchema(), {"profile_id": "p"})

    assert payload["trajectory_cache_key"] == "cache-1"
    assert payload["cache_key"] == "cache-1"
    assert "hours" not in payload
    assert "trajectory" not in payload["trace_summary"]


def test_storage_fallback_keeps_trajectory_when_a_sibling_is_not_jsonable() -> None:
    class _Boom(dict):
        def items(self):  # type: ignore[override]
            raise TypeError("not jsonable")

    trajectory = project_trajectory(
        [{"hour": 1, "campaign": "C0", "T_C": 900.0}]
    )
    scored = ScoredResult(
        candidate_id="fallback",
        eval_spec=None,
        cache_key=None,
        feasible=False,
        run_reference=RunReference(
            status="ok",
            trace={
                "backend_status": "ok",
                "trajectory": trajectory,
                "boom": _Boom(),
            },
            backend_status="ok",
        ),
    )

    blob = _result_blob(scored)

    assert isinstance(blob, dict)
    assert blob["trajectory"]["schema_version"] == TRAJECTORY_SCHEMA_VERSION
    assert "boom" not in blob


def test_old_cache_row_reads_as_unavailable(tmp_path) -> None:
    spec = _spec()
    store = ResultStore(tmp_path / "cache.sqlite")
    store.store(
        spec,
        _scored(_admissible_trace()),
        created_at="2026-10-20T00:00:00+00:00",
    )

    unavailable = read_trajectory(store, cache_key(spec))

    assert unavailable == {
        "status": TRAJECTORY_STATUS_UNAVAILABLE,
        "schema_version": None,
        "hours": None,
    }
    assert read_trajectory(store, "missing-cache-key") == unavailable


def test_reader_fails_when_stored_hour_drops_a_field(tmp_path) -> None:
    spec = _spec()
    key = cache_key(spec)
    trajectory = project_trajectory(
        [
            {
                "hour": 1,
                "campaign": "C0",
                "T_C": 900.0,
                "P_total_bar": 1.0e-6,
                "pO2_bar": 1.0e-8,
                "vapor_species_kg_hr": {"Na": 0.01},
            }
        ]
    )
    store = ResultStore(tmp_path / "cache.sqlite")
    store.store(
        spec,
        _scored(_admissible_trace({"trajectory": trajectory})),
        created_at="2026-10-20T00:00:00+00:00",
    )
    with sqlite3.connect(tmp_path / "cache.sqlite") as connection:
        raw = connection.execute(
            "SELECT result_blob FROM results WHERE cache_key = ?",
            (key,),
        ).fetchone()[0]
        blob = json.loads(raw)
        del blob["trajectory"]["hours"][0]["pO2_mbar"]
        connection.execute(
            "UPDATE results SET result_blob = ? WHERE cache_key = ?",
            (json.dumps(blob), key),
        )

    with pytest.raises(TrajectorySchemaError):
        read_trajectory(store, key)


def test_tiny_study_stores_a_trajectory_for_every_point(tmp_path) -> None:
    out = tmp_path / "study"
    result = study.run(
        PROFILE,
        "lunar_mare_low_ti",
        "random",
        "internal-analytical",
        1,
        1,
        out,
        seed=7,
    )

    assert result.status in {
        study.COMPLETED_STATUS,
        study.COMPLETED_NO_FEASIBLE_WINNER_STATUS,
    }
    with sqlite3.connect(out / "cache.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        stored = list(
            connection.execute("SELECT cache_key, result_blob FROM results")
        )
    assert stored
    store = ResultStore(out / "cache.sqlite")
    # run.hours on this profile is the fidelity label. The C0 seed still
    # records three hourly rows (625 C, 900 C, 950 C).
    expected_hours = 3
    for row in stored:
        trajectory = read_trajectory(store, row["cache_key"])
        assert trajectory["status"] == TRAJECTORY_STATUS_AVAILABLE
        assert trajectory["schema_version"] == TRAJECTORY_SCHEMA_VERSION
        assert [hour["hour"] for hour in trajectory["hours"]] == list(
            range(1, expected_hours + 1)
        )
        for hour in trajectory["hours"]:
            assert set(_HOUR_KEYS) <= set(hour)
            assert isinstance(hour["p_total_mbar"], float)
            assert isinstance(hour["pO2_mbar"], float)
            assert isinstance(hour["T_C"], float)
            assert isinstance(hour["vapour_kg_h"], dict)
            assert isinstance(hour["capture_kg_h"], dict)
            assert isinstance(hour["wall_deposit_kg_h"], dict)
        assert trajectory_stored_bytes(trajectory) < _STORED_BYTE_BOUND
        blob = json.loads(row["result_blob"])
        assert blob["trajectory"]["status"] == TRAJECTORY_STATUS_AVAILABLE

    provenance = [
        json.loads(line)
        for line in (out / "provenance.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert provenance
    for row in provenance:
        assert row["trajectory_cache_key"] == row["cache_key"]

    pareto = json.loads((out / "pareto.json").read_text(encoding="utf-8"))
    if result.status == study.COMPLETED_STATUS:
        assert pareto["pareto"]
    for member in pareto["pareto"]:
        assert member["trajectory_cache_key"] == member["cache_key"]
        linked = read_trajectory(store, member["trajectory_cache_key"])
        assert linked["status"] == TRAJECTORY_STATUS_AVAILABLE
        assert len(linked["hours"]) == expected_hours

    summary = json.loads((out / "study.summary.json").read_text(encoding="utf-8"))
    study_status = summary["study_status"]
    job_status = (
        "SUCCEEDED"
        if study_status == study.COMPLETED_STATUS
        else "COMPLETED-NO-FEASIBLE-WINNER"
    )
    (out / "job_status.json").write_text(
        json.dumps(
            {
                "status": job_status,
                "success": True,
                "study_status": study_status,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    bundle = export_study_bundle(out, output_path=tmp_path / "study.rpstudy.zip")
    imported = import_study_bundle(
        bundle,
        tmp_path / "runs",
        verification_tier=0,
    )
    imported_store = ResultStore(imported.path / "cache.sqlite")
    for row in stored:
        exported = read_trajectory(store, row["cache_key"])
        restored = read_trajectory(imported_store, row["cache_key"])
        assert restored == exported
