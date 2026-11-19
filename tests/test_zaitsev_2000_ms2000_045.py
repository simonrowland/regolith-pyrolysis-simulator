from pathlib import Path
from decimal import Decimal

import yaml

from simulator.battery.enums import Phase
from simulator.battery.method_class_bands import (
    _located_fact,
    select_method_class_band,
)
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/zaitsev-2000-ms2000-045.yaml"


def test_zaitsev_2000_table_1_activities_migrate_and_pin_printed_values():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(
        ROOT,
        EXTRACT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.ok
    assert migrator.result.validation.hard_issues == ()
    assert len(migrator.result.observations) == 32
    row = next(
        row for row in document["species"]["MnO"]["observations"]
        if row["observation_id"] == "zaitsev_2000_table1_mno_activities_eqn5"
    )
    points = row["values"]["points"]
    assert (points[0]["x_SiO2_as_printed"], points[0]["activity_eqn5_as_printed"]) == ("0.515", "0.137")
    assert (points[3]["x_SiO2_as_printed"], points[3]["activity_eqn5_as_printed"]) == ("0.405", "0.254")
    eqn5_migrated = {
        row.value.point
        for row in migrator.result.observations.values()
        if "::zaitsev_2000_table1_mno_activities_eqn5::" in row.observation_id
    }
    assert {Decimal("0.137"), Decimal("0.165"), Decimal("0.254")} <= eqn5_migrated
    assert len(migrator.result.queue) == 0
    condensed_liquid_series = (
        "zaitsev_2000_table1_mno_activities_eqn5",
        "zaitsev_2000_table1_mno_activities_eqn11",
        "zaitsev_2000_table1_sio2_activities_eqn5",
        "zaitsev_2000_table1_sio2_activities_eqn11",
    )
    for parent_id in condensed_liquid_series:
        migrated_rows = [
            observation
            for observation in migrator.result.observations.values()
            if f"::{parent_id}::" in observation.observation_id
        ]
        assert migrated_rows
        assert all(
            observation.identity.species.phase.value is Phase.L
            for observation in migrated_rows
        ), parent_id

    activity_methods = {
        "zaitsev_2000_table1_mno_activities_eqn5": "direct_comparison",
        "zaitsev_2000_table1_sio2_activities_eqn5": "direct_comparison",
        "zaitsev_2000_table1_mno_activities_eqn11": "belton_fruehan_integration",
        "zaitsev_2000_table1_sio2_activities_eqn11": "belton_fruehan_integration",
    }
    activity_observations = [
        observation
        for observation in migrator.result.observations.values()
        if any(f"::{parent_id}::" in observation.observation_id for parent_id in activity_methods)
    ]
    assert len(activity_observations) == 32
    for observation in activity_observations:
        parent_id = next(
            parent_id
            for parent_id in activity_methods
            if f"::{parent_id}::" in observation.observation_id
        )
        experiment = migrator.result.experiments[observation.experiment_id]
        assert _located_fact(observation, experiment, "technique") == "mass_spectrometry"
        assert (
            _located_fact(observation, experiment, "comparison")
            == "multiple_cell_or_successive_pure_reference_sample"
        )
        assert _located_fact(observation, experiment, "sensitivity") == "stable"
        assert _located_fact(observation, experiment, "activity_method") == activity_methods[parent_id]
        method_band = select_method_class_band(observation, experiment)
        if activity_methods[parent_id] == "direct_comparison":
            assert method_band["class_id"] == "kems_direct_comparison_activity"
        else:
            assert method_band == {
                "refusal": {"reason": "method_class_applicability_unknown"}
            }
