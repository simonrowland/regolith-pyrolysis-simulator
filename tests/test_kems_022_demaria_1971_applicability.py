from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.method_class_bands import (
    _located_fact,
    select_method_class_band,
)
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data" / "literature" / "extracts" / "kems-022-demaria-1971.yaml"
SOURCE_ID = "kems-022-demaria-1971"
UNKNOWN_BAND = {"refusal": {"reason": "method_class_applicability_unknown"}}


def test_located_technique_reaches_all_pressure_observations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(
        ROOT,
        EXTRACT,
        index={SOURCE_ID: dict(document["source"], source_id=SOURCE_ID)},
        aliases={},
    )
    migrator.finalize()

    pressure_rows = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == SOURCE_ID
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value in {Quantity.P_PARTIAL, Quantity.P_SAT}
        and any(
            f"::experiment::{experiment_id}" in observation.experiment_id
            for experiment_id in (
                "demaria-1971-na-figure1",
                "demaria-1971-k-figure2",
                "demaria-1971-main-kems",
            )
        )
    ]
    assert pressure_rows
    assert {
        experiment_id
        for experiment_id in (
            "demaria-1971-na-figure1",
            "demaria-1971-k-figure2",
            "demaria-1971-main-kems",
        )
        if any(f"::experiment::{experiment_id}" in row.experiment_id for row in pressure_rows)
    } == {
        "demaria-1971-na-figure1",
        "demaria-1971-k-figure2",
        "demaria-1971-main-kems",
    }
    for observation in pressure_rows:
        experiment = migrator.result.experiments[observation.experiment_id]
        assert _located_fact(observation, experiment, "technique") == "mass_spectrometry"
        assert select_method_class_band(observation, experiment) == UNKNOWN_BAND
