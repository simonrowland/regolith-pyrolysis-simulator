"""Source method and unreported effusion regime for Shornikov (2007)."""

from __future__ import annotations

import yaml

from simulator.battery.enums import MethodToken
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/shornikov-2007-cao-aluminosilicate-melts.yaml"
)


def test_prior_kems_method_is_known_and_regime_stays_unverified() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    experiment = next(
        value
        for experiment_id, value in migrator.result.experiments.items()
        if experiment_id.endswith("shornikov_2007_prior_kems_comparison_2033k")
    )

    assert document["experiments"][0]["method"] == "knudsen_effusion"
    assert experiment.method.value is MethodToken.KNUDSEN_EFFUSION
    assert experiment.pressure_environment.total_pressure_Pa.state.is_unknown
    assert experiment.pressure_environment.regime.regime_class.is_unknown
