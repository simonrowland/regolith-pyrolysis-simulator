from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import MethodToken
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/shornikov-2019-perovskite-evaporation-thermodynamics.yaml"
)


def _assert_method_evidence(document: dict) -> None:
    experiments = document["experiments"]
    assert all(row["method"] == "knudsen_effusion" for row in experiments)
    assert all(
        row["locator"]
        == {
            "page": 2,
            "pdf_page_index": 1,
            "published_page": 39,
            "figure": figure,
            "panel": panel,
        }
        for row, figure, panel in zip(
            experiments, ("2", "2", "3"), ("a", "b", "a"), strict=True
        )
    )
    assert all(
        row["method_locator"]["page"] == 1
        and row["method_locator"]["pdf_page_index"] == 0
        and row["method_locator"]["published_page"] == 38
        and "Knudsen effusion mass spectrometric method"
        in row["method_locator"]["note"]
        for row in experiments
    )


def test_shornikov_2019_perovskite_methods_migrate_from_page_38():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_method_evidence(document)
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
    assert len(migrator.result.experiments) == 3
    assert {
        observation.experiment_id
        for observation in migrator.result.observations.values()
    } == set(migrator.result.experiments)
    assert all(
        experiment.method.value is MethodToken.KNUDSEN_EFFUSION
        for experiment in migrator.result.experiments.values()
    )
    changed = deepcopy(document)
    changed["experiments"][0]["method"] = "transpiration"
    with pytest.raises(AssertionError):
        _assert_method_evidence(changed)
