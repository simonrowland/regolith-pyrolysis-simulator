from __future__ import annotations

import pytest

from simulator.diagnostic_helpers.reproduction_compare import (
    normalize_comparison_artifact,
)
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.mre_reproduction import (
    MREReproductionError,
    MREReproductionInterval,
    PlantMREIntervalControl,
    load_mre_reproduction_program,
)
from simulator.core import PyrolysisSimulator
from tests.mre_reproduction_fixtures import (
    DURATIONS_H,
    source_documents,
    synthetic_program,
    synthetic_voltage_documents,
)


def test_authoritative_yu_package_refuses_missing_figure_2b_transcription() -> None:
    preset, observations = source_documents()

    with pytest.raises(
        MREReproductionError,
        match="requires at least two points",
    ):
        load_mre_reproduction_program(preset, observations, "one_hour")


@pytest.mark.parametrize("case_id", tuple(DURATIONS_H))
def test_strict_loader_resolves_published_case_controls(case_id: str) -> None:
    program = synthetic_program(case_id)

    assert program.session_kind == "literature-reproduction"
    assert program.domain == "mre"
    assert program.case_id == case_id
    assert program.duration_h == pytest.approx(DURATIONS_H[case_id])
    assert program.current_program["current_A"] == pytest.approx(0.5)
    assert program.temperature_program["temperature_C"] == pytest.approx(1600.0)
    assert program.gas_boundary["carrier_gas"] == "Ar"
    assert program.gas_boundary["runtime_inlet_pO2_status"] == "assumed_derived"
    assert max(interval.dt_h for interval in program.intervals()) <= (
        5.0 / 60.0 + 1e-12
    )


@pytest.mark.parametrize(
    "plant_field",
    (
        "c5_enabled",
        "mre_target_species",
        "mre_max_voltage_V",
        "min_hold_hours",
        "limited_c5_current_A",
        "baseline_mre_current_A",
    ),
)
def test_mre_preset_refuses_plant_policy_fields(plant_field: str) -> None:
    preset, observations = synthetic_voltage_documents()
    preset[plant_field] = True

    with pytest.raises(
        MREReproductionError,
        match="mre_reproduction_forbids_plant_policy",
    ):
        load_mre_reproduction_program(preset, observations, "one_hour")


def test_cross_origin_control_objects_refuse_before_dispatch() -> None:
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"x": {"label": "X", "composition_wt_pct": {"FeO": 100.0}}},
        {"metals": {}, "oxide_vapors": {}},
    )
    reproduction = MREReproductionInterval(
        start_h=0.0,
        end_h=1.0,
        dt_h=1.0,
        applied_current_A=0.5,
        applied_voltage_V=3.0,
        temperature_C=1600.0,
        pO2_bar=5.07e-5,
        source_locator={"fixture": "cross_origin"},
    )
    plant = PlantMREIntervalControl(
        dt_h=1.0,
        applied_current_A=1000.0,
        applied_voltage_V=2.0,
        temperature_C=1600.0,
        pO2_bar=0.05,
    )
    sim._dispatch_only = lambda *_args, **_kwargs: pytest.fail(
        "wrong-origin control reached dispatch"
    )

    with pytest.raises(TypeError, match="PlantMREIntervalControl"):
        sim._execute_mre_interval(
            reproduction,
            execution_origin="plant",
        )
    with pytest.raises(TypeError, match="MREReproductionInterval"):
        sim._execute_mre_interval(
            plant,
            execution_origin="literature-reproduction",
        )


def test_chunk_a_schema_v1_artifact_upgrades_additively() -> None:
    legacy = {
        "schema_version": 1,
        "measurement_id": "chunk_a_fixture",
        "sidecar_path": "observations.yaml",
        "markdown_path": "comparison.md",
        "digests": {},
        "records": [],
        "qualitative_observations": [],
    }

    upgraded = normalize_comparison_artifact(legacy)

    assert upgraded["schema_version"] == 2
    assert upgraded["domain"] == "vacuum_pyrolysis"
    assert upgraded["measurement_id"] == "chunk_a_fixture"
    assert upgraded["records"] == []
    assert upgraded["unsupported_observables"] == []


def test_yu_feed_keeps_reported_subtotal_and_unreported_balance() -> None:
    preset, _ = source_documents()

    assert preset["feed"]["quoted_composition_subtotal"] == {
        "value": 99.1,
        "unit": "wt_pct",
        "status": "derived_from_published_rows",
        "source_locator": {"table": 1},
    }
    assert preset["feed"]["unreported_balance"]["value"] == pytest.approx(0.9)
    assert preset["feed"]["unreported_balance"]["status"] == "not_reported"


def test_event_grid_refuses_sub_quantum_duration_collapse() -> None:
    """rereview-cx P2: a 4e-13 h duration rounded every event to one point,
    intervals() was empty, and the vacuous strict-increase check accepted a
    run that executes nothing. The grid must refuse instead."""
    from simulator.mre_reproduction import MREReproductionError, _event_grid

    with pytest.raises(MREReproductionError, match="collapsed to a single point"):
        _event_grid(duration_h=4e-13, max_interval_h=1.2e-11 / 60.0, knots=())
