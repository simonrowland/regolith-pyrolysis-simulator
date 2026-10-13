from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import REPO_ROOT
from simulator.battery.validate import validate_corpus
from tests.battery.test_migrate import _migrate_real_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


EXTRACT_NAME = "ferguson-2004-iron-vapor-pressure.yaml"
EXTRACT = REPO_ROOT / "data/literature/extracts" / EXTRACT_NAME
TABLE_ROW_PREFIX = (
    "ferguson-2004-iron-vapor-pressure::"
    "ferguson_2004_table1_iron_vapor_pressure::"
)
CONDITION_KEYS = {
    "starting_composition",
    "chamber_pressure_Pa",
    "atmosphere",
    "container",
    "heating_method",
    "orifice_diameter_mm",
    "duration_s",
}


def _assert_migrated_table_values(document: dict, result) -> None:
    points = document["species"]["Fe"]["observations"][0]["values"]["points"]
    table_rows = [
        observation
        for observation_id, observation in result.observations.items()
        if observation_id.startswith(TABLE_ROW_PREFIX)
    ]
    assert len(table_rows) == len(points) == 45

    by_row = {
        int(observation_id.split(":row=", 1)[1].split(":", 1)[0]): observation
        for observation_id, observation in result.observations.items()
        if observation_id.startswith(TABLE_ROW_PREFIX)
    }
    assert len(by_row) == len(points)

    for point in points:
        observation = by_row[point["row"]]
        assert observation.value.point == Decimal(str(point["P_Pa"]))
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == Decimal(
            str(point["T_K_as_printed"])
        )
        assert observation.uncertainty.kind.value == "printed"
        assert Decimal(str(observation.uncertainty.verbatim["sigma"])) == Decimal(
            str(point["P_uncertainty_Pa_as_printed"])
        )
        assert observation.uncertainty.value is None
        assert observation.uncertainty.basis is None

        conditions = observation.point_conditions or {}
        assert CONDITION_KEYS <= conditions.keys()
        assert all(conditions[key].state.is_value for key in CONDITION_KEYS)
        assert all(conditions[key].locator is not None for key in CONDITION_KEYS)


def test_ferguson_2004_pressure_points_and_fidelity_survive_migration(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []

    result = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    _assert_migrated_table_values(document, result)

    validation = validate_corpus(
        result.works,
        result.experiments,
        result.observations,
        residuals=None,
    )
    assert validation.hard_issues == ()

    by_row = {
        int(observation_id.split(":row=", 1)[1].split(":", 1)[0]): observation
        for observation_id, observation in result.observations.items()
        if observation_id.startswith(TABLE_ROW_PREFIX)
    }
    mutated = by_row[1]
    original = result.observations[mutated.observation_id]
    result.observations[mutated.observation_id] = replace(
        original,
        value=replace(original.value, point=original.value.point + Decimal("1")),
    )
    with pytest.raises(AssertionError):
        _assert_migrated_table_values(document, result)
    result.observations[mutated.observation_id] = original
