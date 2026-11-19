"""No-uncertainty typing for admitted MS2000-044 Table 1 activities."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import UncertaintyKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-ms2000-044.yaml"


def test_admitted_table1_activities_migrate_with_no_printed_uncertainty() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_rows = [
        (formula, row)
        for formula, body in document["species"].items()
        for row in body["observations"]
        if row["observation_id"].endswith("_class_quote_r2")
        and row["type"] == "activity_coefficient"
        and row["values"].get("method_class") == "measured_direct"
        and row["values"].get("admission_status") == "admitted"
    ]
    assert len(source_rows) == 24
    assert Counter(formula for formula, _row in source_rows) == {
        "Na2O": 12,
        "K2O": 12,
    }

    migrator = migrate_one_extract(
        ROOT,
        EXTRACT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator.finalize()
    assert not migrator.result.registry_issues

    for _formula, source in source_rows:
        assert source["uncertainty"] is None
        migrated = migrator.result.observations[
            f"{document['source_id']}::{source['observation_id']}"
        ]
        assert migrated.uncertainty.kind is UncertaintyKind.NONE
        assert migrated.locator is not None
        assert migrated.locator.page == 22
        assert migrated.locator.table == "1"
        assert source["locator"]["note"]
        assert source["locator"]["note"] in migrated.locator.note
        assert migrated.value.point == Decimal(str(source["values"]["activity"]))

    model_rows = [
        row
        for body in document["species"].values()
        for row in body["observations"]
        if row["observation_id"].endswith("_class_quote_r2")
        and row["type"] == "activity_coefficient"
        and row["values"].get("method_class") != "measured_direct"
    ]
    assert len(model_rows) == 24
    for source in model_rows:
        assert source["uncertainty"] is None
        migrated = migrator.result.observations[
            f"{document['source_id']}::{source['observation_id']}"
        ]
        assert migrated.uncertainty.kind is UncertaintyKind.NONE
        assert migrated.locator is not None
        assert source["locator"]["note"] == (
            "Table 1 states no uncertainty for this point."
        )
        assert source["locator"]["note"] in migrated.locator.note
