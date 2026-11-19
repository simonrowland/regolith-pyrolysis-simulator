from pathlib import Path

import yaml

from simulator.battery.enums import MethodToken
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/shornikov-2019-perovskite-evaporation-thermodynamics.yaml"
)


def test_shornikov_2019_perovskite_methods_migrate_from_page_38():
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
    assert len(migrator.result.experiments) == 3
    assert {
        observation.experiment_id
        for observation in migrator.result.observations.values()
    } == set(migrator.result.experiments)
    assert all(
        experiment.method.value is MethodToken.KNUDSEN_EFFUSION
        for experiment in migrator.result.experiments.values()
    )
    assert all(
        experiment["locator"]["published_page"] == 38
        and experiment["locator"]["pdf_page_index"] == 0
        for experiment in document["experiments"]
    )
