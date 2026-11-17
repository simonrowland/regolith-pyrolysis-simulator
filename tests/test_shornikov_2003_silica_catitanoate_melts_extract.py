"""Source method pin and isolated migration for Shornikov and Archakov (2003)."""

from __future__ import annotations

import yaml

from simulator.battery.enums import MethodToken, Quantity
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract


EXTRACT_NAME = "shornikov-2003-silica-catitanoate-melts.yaml"
EXTRACT = REPO_ROOT / "data/literature/extracts" / EXTRACT_NAME


def test_fig1_activities_migrate_with_the_printed_knudsen_method() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    experiment = document["experiments"][0]
    assert experiment["method"] == "knudsen_effusion"
    assert experiment["locator"]["page"] == 2
    assert experiment["locator"]["published_page"] == 113
    assert experiment["locator"]["section"] == "Experimental"

    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    rows = [
        row
        for row in migrator.result.observations.values()
        if row.source_id == document["source_id"]
        and row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.ACTIVITY
    ]
    assert len(rows) == 36
    assert all(
        migrator.result.experiments[row.experiment_id].method.value
        is MethodToken.KNUDSEN_EFFUSION
        for row in rows
    )
    for row in rows:
        assert row.identity.temperature_K.is_value
        assert row.point_conditions
        assert all(
            condition.state is not None and condition.locator is not None
            for condition in row.point_conditions.values()
        )
