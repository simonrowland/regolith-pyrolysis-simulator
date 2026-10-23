"""Migration pins for the measured KEMS currents and activities."""

from __future__ import annotations

from decimal import Decimal

import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import Migrator, REPO_ROOT
from tools.validate_literature_extracts import validate_extract_document


EXTRACT = REPO_ROOT / "data/literature/extracts/kems-055-kambayashi-1983.yaml"
TABLE2_MGO_ACTIVITY_03 = Decimal("0.138")


def test_extract_covers_table_values_and_migrates_every_typed_row_as_a_point() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    assert len(migrator.result.observations) == 50

    for formula, body in document["species"].items():
        for row in body["observations"]:
            migrated = migrator.result.observations[
                f"{EXTRACT.stem}::{row['observation_id']}"
            ]
            quantity = Quantity(row["values"]["quantity"])
            field = {
                Quantity.ION_INTENSITY: "ion_intensity",
                Quantity.ION_INTENSITY_RATIO: "ion_intensity_ratio",
                Quantity.ACTIVITY: "activity",
            }[quantity]
            assert migrated.value.kind is ValueKind.POINT, row["observation_id"]
            assert migrated.value.point == Decimal(str(row["values"][field]))
            assert migrated.identity.species.formula == formula
            assert migrated.identity.temperature_K.is_value
            assert migrated.point_conditions is not None
            source_conditions = row["point_conditions"]
            assert set(source_conditions) <= set(migrated.point_conditions)
            for key, source_condition in source_conditions.items():
                condition = migrated.point_conditions[key]
                assert condition.state.tag.value == source_condition["state"]["tag"]
                assert condition.locator is not None
            temperature = migrated.point_conditions["temperature_K"]
            assert temperature.state.is_value
            assert temperature.state.value == migrated.identity.temperature_K.value

    # Endpoints and the low-silica activity are fidelity pins from the page-6 render.
    assert migrator.result.observations[
        f"{EXTRACT.stem}::kambayashi_1983_table2_sio2_activity_10"
    ].value.point == Decimal("0.0261")
    assert migrator.result.observations[
        f"{EXTRACT.stem}::kambayashi_1983_table2_mgo_activity_10"
    ].value.point == Decimal("1")
    assert migrator.result.observations[
        f"{EXTRACT.stem}::kambayashi_1983_table2_mgo_activity_03"
    ].value.point == TABLE2_MGO_ACTIVITY_03

    x_0220_rows = [
        row
        for body in document["species"].values()
        for row in body["observations"]
        if row["values"].get("composition_mole_fraction", {}).get("SiO2") == "0.220"
    ]
    assert len(x_0220_rows) == 2
    for row in x_0220_rows:
        assert row["values"]["phase_assemblage_as_printed"] == "MgO(s) + Mg2SiO4(s)"
        migrated = migrator.result.observations[f"{EXTRACT.stem}::{row['observation_id']}"]
        phase = migrated.point_conditions["phase_assemblage"]
        assert phase.state.value == "MgO(s) + Mg2SiO4(s)"
        assert phase.locator is not None


def test_table1_ratio_and_table2_activity_rows_are_complete() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    silicon = document["species"]["SiO2"]["observations"]
    magnesium = document["species"]["MgO"]["observations"]
    ratios = [row for row in silicon if row["values"]["quantity"] == "ion_intensity_ratio"]
    activities = [
        row
        for row in (*silicon, *magnesium)
        if row["values"]["quantity"] == "activity"
    ]
    assert len(ratios) == 10
    assert len(activities) == 20
    assert all(len(row["derived_from"]) == 10 for row in activities)
    assert all(
        row["point_conditions"]["temperature_K"]["state"]["value"] == "1873"
        for row in (*ratios, *activities)
    )
