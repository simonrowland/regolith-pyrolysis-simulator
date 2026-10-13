from __future__ import annotations

from copy import deepcopy
from pathlib import Path

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


def test_ferguson_2004_pressure_points_and_fidelity_survive_migration(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []

    mutated = deepcopy(document)
    mutated["species"]["Fe"]["observations"][0]["values"]["points"][0][
        "P_Pa"
    ] += 1
    assert check_all_fidelity_samples_match(mutated, label=str(EXTRACT))

    mutated_context = deepcopy(document)
    next(
        item
        for item in mutated_context["species"]["Fe"]["context"]
        if item["observation_id"] == "ferguson_2004_third_law_enthalpy_vaporization"
    )["values"]["value_kJ_mol"] += 1
    assert check_all_fidelity_samples_match(mutated_context, label=str(EXTRACT))

    result = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    table_rows = [
        observation
        for observation_id, observation in result.observations.items()
        if observation_id.startswith(TABLE_ROW_PREFIX)
    ]
    assert len(table_rows) == 45
    for observation in table_rows:
        temperature = observation.identity.temperature_K
        assert temperature.is_value
        conditions = observation.point_conditions or {}
        assert CONDITION_KEYS <= conditions.keys()
        assert all(conditions[key].state.is_value for key in CONDITION_KEYS)
        assert all(conditions[key].locator is not None for key in CONDITION_KEYS)

    validation = validate_corpus(
        result.works,
        result.experiments,
        result.observations,
        residuals=None,
    )
    assert validation.hard_issues == ()
