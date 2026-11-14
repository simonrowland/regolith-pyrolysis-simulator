from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.test_migrate import _migrate_real_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT_NAME = "shults-1994-mullite-evaporation.yaml"
EXTRACT = ROOT / "data/literature/extracts" / EXTRACT_NAME


def test_related_source_audit_context_stays_unchanged() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    relationship = next(
        row
        for container in document["species"].values()
        for row in container.get("context", [])
        if row["observation_id"]
        == "shults_1994_source_relationship_to_existing_mullite_extract"
    )
    assert relationship["type"] == "source_relationship"
    assert relationship["values"]["method_class"] == "quoted_attributed"
    assert relationship["values"]["related_source_id"] == "shornikov-1994-mullite-kems"
    assert "attribution" not in relationship["values"]


def test_located_table2_oxygen_condition_survives_migration(tmp_path):
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_points = {
        observation["observation_id"]: observation["point_conditions"][
            "oxygen_partial_pressure_Pa"
        ]
        for species in document["species"].values()
        for observation in species["observations"]
        if "oxygen_partial_pressure_Pa" in observation.get("point_conditions", {})
    }
    assert len(source_points) == 45

    migrated = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    assert migrated.validation is not None
    assert not migrated.validation.hard_issues

    for observation_id, source_point in source_points.items():
        observation = migrated.observations[
            f"{document['source_id']}::{observation_id}"
        ]
        point = observation.point_conditions["oxygen_partial_pressure_Pa"]
        assert point.state.is_value
        assert point.locator is not None
        assert point.locator.published_page == source_point["locator"]["published_page"]
        assert point.locator.table == source_point["locator"]["table"]
        value = point.state.value
        if hasattr(value, "point"):
            value = value.point
        expected = Decimal(source_point["state"]["value"]["point"])
        assert value == expected
