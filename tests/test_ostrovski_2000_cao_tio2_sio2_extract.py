"""Source method and printed pressure-limit coverage for Ostrovski et al. (2000)."""

from __future__ import annotations

import yaml

from simulator.battery.enums import MethodToken
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/ostrovski-2000-cao-tio2-sio2.yaml"
)


def test_method_is_known_but_unpublished_regime_remains_typed_unknown() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    experiment = next(
        value
        for experiment_id, value in migrator.result.experiments.items()
        if experiment_id.endswith("cao-tio2-sio2-series")
    )

    assert document["experiments"][0]["method"] == "knudsen_effusion"
    assert experiment.method.value is MethodToken.KNUDSEN_EFFUSION
    assert experiment.pressure_environment.total_pressure_Pa.state.is_unknown
    assert experiment.pressure_environment.regime.regime_class.is_unknown
