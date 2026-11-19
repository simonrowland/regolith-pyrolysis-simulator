"""Source pins and isolated migration for Ichise et al. (1982)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.method_class_bands import (
    _located_fact,
    select_method_class_band,
)
from tests.battery.test_migrate import _migrate_real_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT_NAME = "kems-111-ichise-1982.yaml"
EXTRACT_PATH = ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME
SOURCE_ID = "kems-111-ichise-1982"
TABLE2_ID = "ichise_1982_table2_a_Fe_1823K"

TABLE2_ACTIVITY_ROWS = (
    ("Fe-Mo 6", "0.096", "0.897"),
    ("Fe-Mo 2", "0.154", "0.81"),
    ("Fe-Mo 4", "0.2", "0.8"),
    ("Fe-Mo 7", "0.201", "0.767"),
    ("Fe-Mo 1", "0.24", "0.692"),
    ("Fe-Mo 5", "0.251", "0.737"),
    ("Fe-Mo 9", "0.254", "0.741"),
    ("Fe-Mo 3", "0.3", "0.69"),
    ("Fe-Mo 8", "0.302", "0.673"),
    ("Fe-Mo 10", "0.333", "0.677"),
    ("Ag-Fe-Mo 7", "0.339", "0.652"),
    ("Ag-Fe-Mo 2", "0.348", "0.652"),
    ("Ag-Fe-Mo 10", "0.434", "0.621"),
    ("Ag-Fe-Mo 11", "0.434", "0.656"),
    ("Ag-Fe-Mo 13", "0.434", "0.528"),
    ("Ag-Fe-Mo 8", "0.556", "0.585"),
    ("Ag-Fe-Mo 9", "0.613", "0.552"),
    ("Ag-Fe-Mo 12", "0.666", "0.526"),
    ("Ag-Fe-Mo 4", "0.778", "0.556"),
    ("Ag-Fe-Mo 6", "0.793", "0.538"),
    ("Ag-Fe-Mo 3", "0.842", "0.518"),
    ("Ag-Fe-Mo 1", "0.912", "0.36"),
    ("Ag-Fe-Mo 5", "0.912", "0.383"),
)

TABLE4_NO_UNCERTAINTY_IDS = {
    "ichise_1982_table4_activity_Fe_x_Mo_0_0",
    "ichise_1982_table4_activity_Fe_x_Mo_0_1",
    "ichise_1982_table4_activity_Fe_x_Mo_0_3",
    "ichise_1982_table4_activity_Fe_x_Mo_0_9",
    "ichise_1982_table4_activity_Fe_x_Mo_1_0",
}

TABLE4_MODEL_DERIVED_IDS = {
    "ichise_1982_table4_activity_Fe_x_Mo_0_484",
    "ichise_1982_table4_activity_Fe_x_Mo_0_5",
    "ichise_1982_table4_activity_Fe_x_Mo_0_554",
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))


def _table2_rows(document: dict) -> list[dict]:
    return next(
        row
        for row in document["species"]["Fe"]["observations"]
        if row["observation_id"] == TABLE2_ID
    )["values"]["rows"]


def _migrated_table2_point(result, row: dict):
    matches = [
        observation
        for observation in result.observations.values()
        if observation.source_id == SOURCE_ID
        and observation.observation_id.startswith(f"{SOURCE_ID}::{TABLE2_ID}::")
        and observation.identity.quantity.value is Quantity.ACTIVITY
        and observation.value.point == Decimal(str(row["activity"]))
        and observation.point_conditions is not None
        and observation.point_conditions["composition"].state.value.as_map()["Mo"]
        == Decimal(str(row["x_Mo"]))
    ]
    assert len(matches) == 1, row
    return matches[0]


def _table4_rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in document["species"]["Fe"]["observations"]
        if row["observation_id"].startswith("ichise_1982_table4_activity_Fe_")
    }


def _migrated_table4_point(result, observation_id: str):
    return result.observations[f"{SOURCE_ID}::{observation_id}"]


def test_table2_activity_facts_migrate_for_all_direct_comparison_rows(
    tmp_path: Path,
) -> None:
    document = _document()
    rows = _table2_rows(document)
    assert len(rows) == len(TABLE2_ACTIVITY_ROWS) == 23
    assert tuple(
        (row["no"], str(row["x_Mo"]), str(row["activity"])) for row in rows
    ) == TABLE2_ACTIVITY_ROWS

    result = _migrate_real_extract(tmp_path / "source", EXTRACT_NAME, extract_doc=document)
    migrated_table2 = [
        observation
        for observation in result.observations.values()
        if observation.source_id == SOURCE_ID
        and observation.observation_id.startswith(f"{SOURCE_ID}::{TABLE2_ID}::")
    ]
    assert len(migrated_table2) == 23

    for row in rows:
        observation = _migrated_table2_point(result, row)
        experiment = result.experiments[observation.experiment_id]
        assert _located_fact(observation, experiment, "technique") == "mass_spectrometry"
        technique = experiment.apparatus.calibration["technique"]
        assert technique.state.is_value and technique.locator is not None
        assert (technique.locator.page, technique.locator.published_page) == (2, 553)
        assert (
            _located_fact(observation, experiment, "comparison")
            == "multiple_cell_or_successive_pure_reference_sample"
        )
        assert _located_fact(observation, experiment, "activity_method") == "direct_comparison"
        assert observation.point_conditions["comparison"].locator is not None
        assert observation.point_conditions["activity_method"].locator is not None

        method_band = select_method_class_band(observation, experiment)
        if row["no"].startswith("Ag-Fe-Mo"):
            assert _located_fact(observation, experiment, "sensitivity") == "stable"
            assert observation.point_conditions["sensitivity"].locator is not None
            assert method_band["class_id"] == "kems_direct_comparison_activity"
        else:
            assert _located_fact(observation, experiment, "sensitivity") is None
            assert method_band == {
                "refusal": {"reason": "method_class_applicability_unknown"}
            }

    table4 = _table4_rows(document)
    assert TABLE4_NO_UNCERTAINTY_IDS <= set(table4)
    assert TABLE4_MODEL_DERIVED_IDS <= set(table4)
    assert len(TABLE4_NO_UNCERTAINTY_IDS) == 5
    assert all("uncertainty" not in table4[row_id] for row_id in TABLE4_NO_UNCERTAINTY_IDS)
    assert all(
        table4[row_id]["values"]["method_class"] == "measured"
        for row_id in TABLE4_NO_UNCERTAINTY_IDS
    )
    assert all(
        table4[row_id]["values"]["method_class"] == "model_derived"
        for row_id in TABLE4_MODEL_DERIVED_IDS
    )
    for row_id in TABLE4_NO_UNCERTAINTY_IDS | TABLE4_MODEL_DERIVED_IDS:
        observation = _migrated_table4_point(result, row_id)
        experiment = result.experiments[observation.experiment_id]
        assert _located_fact(observation, experiment, "technique") == "mass_spectrometry"
        assert _located_fact(observation, experiment, "comparison") is None
        assert _located_fact(observation, experiment, "activity_method") is None
        assert _located_fact(observation, experiment, "sensitivity") is None
        assert select_method_class_band(observation, experiment) == {
            "refusal": {"reason": "method_class_applicability_unknown"}
        }


def test_mutating_ag_activity_method_removes_all_thirteen_assumed_bands(
    tmp_path: Path,
) -> None:
    document = _document()
    first_ag_row = next(
        row for row in _table2_rows(document) if row["no"].startswith("Ag-Fe-Mo")
    )
    first_ag_row["point_conditions"]["activity_method"]["state"]["value"] = (
        "gibbs_duhem_integration"
    )

    result = _migrate_real_extract(tmp_path / "mutated", EXTRACT_NAME, extract_doc=document)
    ag_rows = [row for row in _table2_rows(document) if row["no"].startswith("Ag-Fe-Mo")]
    assert len(ag_rows) == 13
    for row in ag_rows:
        observation = _migrated_table2_point(result, row)
        experiment = result.experiments[observation.experiment_id]
        assert _located_fact(observation, experiment, "activity_method") == (
            "gibbs_duhem_integration"
        )
        assert select_method_class_band(observation, experiment) == {
            "refusal": {"reason": "method_class_applicability_unknown"}
        }
