from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.method_class_bands import (
    _located_fact,
    select_method_class_band,
)
from tests.battery.test_migrate import _migrate_real_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT_NAME = "kems-ms2000-044.yaml"
SOURCE_ID = "kems-ms2000-044"
CONTESTED_ID = "ms2000_044_k2o_activity_xsio2_0892_t1723_contested_class_quote_r2"
UNKNOWN_BAND = {"refusal": {"reason": "method_class_applicability_unknown"}}


def _migrate(tmp_path: Path, document: dict | None = None):
    return _migrate_real_extract(
        tmp_path / "isolated",
        EXTRACT_NAME,
        extract_doc=document,
    )


def _quote_activity_rows(result, prefix: str):
    return [
        observation
        for observation in result.observations.values()
        if observation.source_id == SOURCE_ID
        and observation.identity.quantity.value is Quantity.ACTIVITY
        and observation.observation_id.startswith(f"{SOURCE_ID}::{prefix}")
        and observation.observation_id.endswith("_class_quote_r2")
    ]


def test_direct_and_integrated_activity_facts_reach_migrated_rows(
    tmp_path: Path,
) -> None:
    result = _migrate(tmp_path)
    assert result.validation is not None

    direct_rows = [
        *_quote_activity_rows(result, "ms2000_044_na2o_activity_"),
        *_quote_activity_rows(result, "ms2000_044_k2o_activity_"),
    ]
    assert len(direct_rows) == 24
    assert sum("contested" not in row.observation_id for row in direct_rows) == 23
    for observation in direct_rows:
        experiment = result.experiments[observation.experiment_id]
        assert _located_fact(observation, experiment, "technique") == "mass_spectrometry"
        assert _located_fact(observation, experiment, "comparison") == (
            "multiple_cell_or_successive_pure_reference_sample"
        )
        assert _located_fact(observation, experiment, "sensitivity") == "stable"
        if observation.observation_id.endswith(CONTESTED_ID):
            assert _located_fact(observation, experiment, "activity_method") is None
            assert select_method_class_band(observation, experiment) == UNKNOWN_BAND
        else:
            assert _located_fact(observation, experiment, "activity_method") == (
                "direct_comparison"
            )
            assert observation.point_conditions["activity_method"].locator is not None
            assert select_method_class_band(observation, experiment)["class_id"] == (
                "kems_direct_comparison_activity"
            )

    silica_rows = _quote_activity_rows(result, "ms2000_044_sio2_activity_")
    assert len(silica_rows) == 24
    for observation in silica_rows:
        experiment = result.experiments[observation.experiment_id]
        assert _located_fact(observation, experiment, "technique") == "mass_spectrometry"
        assert _located_fact(observation, experiment, "activity_method") == (
            "gibbs_duhem_integration"
        )
        assert observation.point_conditions["activity_method"].locator is not None
        assert select_method_class_band(observation, experiment) == UNKNOWN_BAND


def test_changing_a_direct_activity_method_to_integration_removes_its_band(
    tmp_path: Path,
) -> None:
    extract = ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME
    document = yaml.safe_load(extract.read_text(encoding="utf-8"))
    row = next(
        row
        for row in document["species"]["Na2O"]["observations"]
        if row["observation_id"].endswith("_class_quote_r2")
    )
    row["point_conditions"]["activity_method"] = deepcopy(
        row["point_conditions"]["activity_method"]
    )
    row["point_conditions"]["activity_method"]["state"]["value"] = (
        "gibbs_duhem_integration"
    )

    result = _migrate(tmp_path, document)
    observation_id = f"{SOURCE_ID}::{row['observation_id']}"
    observation = result.observations[observation_id]
    experiment = result.experiments[observation.experiment_id]
    assert _located_fact(observation, experiment, "activity_method") == (
        "gibbs_duhem_integration"
    )
    assert select_method_class_band(observation, experiment) == UNKNOWN_BAND
