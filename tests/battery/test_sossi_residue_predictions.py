from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.battery.residue import (
    _SOSSI_GEOMETRIES,
    _sossi_geometry_areas,
    _sossi_initial_inventory,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/literature/extracts/kems-012-sossi-2019.yaml"


def test_sossi_start_inventory_contains_table1_host_and_starting_trace() -> None:
    source = yaml.safe_load(SOURCE.read_text())
    experiment = source["experiments"][0]
    starting_trace_ppm = {
        element: source["species"][element]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
        for element in ("Mn", "Ti")
    }
    inventory, sample_mass_kg, inventory_mass_kg = _sossi_initial_inventory(
        experiment, starting_trace_ppm
    )

    assert sample_mass_kg == 25.0e-6
    assert {
        "SiO2",
        "Al2O3",
        "MgO",
        "FeO",
        "CaO",
        "MnO",
        "TiO2",
    } <= set(inventory)
    assert inventory["MnO"] > 0.0
    assert inventory["TiO2"] > 0.0
    assert inventory_mass_kg > 0.0
    assert experiment["sample"]["printed_composition"]["locator"]["table"] == "1"
    assert experiment["sample"]["printed_composition"]["state"]["value"] == {
        "SiO2": 40.6,
        "Al2O3": 10.67,
        "MgO": 15.4,
        "FeO": 16.26,
        "CaO": 16.82,
    }


def test_sossi_geometry_is_declared_from_bead_mass_without_residue_inputs() -> None:
    areas = _sossi_geometry_areas(25.0e-6)

    assert tuple(areas) == _SOSSI_GEOMETRIES
    assert all(value > 0.0 for value in areas.values())
    assert areas["pt_loop_bead_sphere_constant"] == areas[
        "pt_loop_bead_sphere_shrinking"
    ]
    radius_m = (3.0 * 25.0e-6 / (4.0 * math.pi * 2700.0)) ** (1.0 / 3.0)
    assert areas["pt_loop_bead_sphere_constant"] == pytest.approx(
        4.0 * math.pi * radius_m**2
    )
    assert areas["pt_loop_bead_disk_equivalent_volume"] != areas[
        "pt_loop_bead_sphere_constant"
    ]


def test_sossi_scorer_projects_mn_and_types_unsupported_elements(monkeypatch) -> None:
    from simulator.battery.enums import Engine, MetricOperation, RefusalReason
    from simulator.battery.score import (
        _sossi_residue_prediction,
        load_score_context,
        metric_operation_for_identity,
        point_magnitude,
    )
    import simulator.battery.residue as residue

    context = load_score_context(sources=("kems-012-sossi-2019",))
    rows = [
        row
        for row in context.observations.values()
        if row.source_id == "kems-012-sossi-2019"
        and row.admission.status.value == "admitted"
        and point_magnitude(row.value) is not None
        and row.identity.species.formula in {"Mn", "Gd"}
    ]
    manganese = next(row for row in rows if row.identity.species.formula == "Mn")
    gadolinium = next(row for row in rows if row.identity.species.formula == "Gd")
    expected_run = manganese.experiment_id.rsplit("::", 1)[-1]

    def fake_cohort(experiments, *_args, **_kwargs):
        return tuple(
            SimpleNamespace(
                experiment_id=item["experiment_id"],
                primary_element_ppm={"Mn": 900.0, "Ti": 450.0},
                sensitivity_band_ppm={"Mn": (800.0, 1000.0), "Ti": (400.0, 500.0)},
                channel_missing_elements=(),
                provenance={
                    "integration": {"primary": {"status": "converged"}},
                    "code_revision": "fixture-revision",
                },
            )
            for item in experiments
        )

    monkeypatch.setattr(residue, "_predict_sossi_residue_cohort", fake_cohort)
    cache = {}
    prediction = _sossi_residue_prediction(
        context, manganese, Engine.OPENIMCC, cache
    )
    refusal = _sossi_residue_prediction(
        context, gadolinium, Engine.OPENIMCC, cache
    )

    assert expected_run in cache[Engine.OPENIMCC]
    assert prediction.execution.state.value == "produced"
    assert prediction.value == 900.0
    assert prediction.identity == manganese.identity
    assert any(
        notice.reason == "bc_open_furnace_langmuir_limit_diagnostic"
        for notice in prediction.notices
    )
    assert metric_operation_for_identity(manganese.identity) is MetricOperation.DEX
    assert refusal.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
    assert refusal.refusal_detail["reason"] == "channel_missing"
