"""Render pins and isolated migration for Ohta and Suito (1996), Table 1."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import yaml
import pytest

from simulator.battery.migrate import Migrator, REPO_ROOT


EXTRACT = REPO_ROOT / "data/literature/extracts/ohta-1996-cao-mgo-al2o3-activities.yaml"


def _document(path: Path = EXTRACT) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _table_rows(document: dict) -> dict[str, dict[str, dict]]:
    rows: dict[str, dict[str, dict]] = defaultdict(dict)
    for species, block in document["species"].items():
        for observation in block["observations"]:
            locator = observation.get("locator", {})
            if locator.get("table") == "1" and observation["observation_id"].startswith("ohta_1996_table1_r"):
                rows[locator["row"]][locator["column"]] = observation
    return dict(rows)


def _assert_table1_rendered_cells_and_row_count(extract_path: Path) -> None:
    document = _document(extract_path)
    rows = _table_rows(document)
    assert set(rows) == {f"1873-{i}" for i in range(1, 7)} | {f"1823-{i}" for i in range(1, 5)}
    assert sum(len(row) for row in rows.values()) == 130
    assert sum(
        "component_amount" in observation["values"]
        for row in rows.values() for observation in row.values()
    ) == 125
    assert sum(
        observation["values"].get("admission_status") == "unavailable"
        for row in rows.values() for observation in row.values()
    ) == 5
    assert rows["1873-1"]["Al_sol"]["values"]["component_amount"] == "43.5"
    assert rows["1873-1"]["Al_sol"]["values"]["component_amount_as_published"] == "43.5 mass ppm"
    assert rows["1873-1"]["CaO"]["values"]["component_amount"] == "27.4"
    assert rows["1873-1"]["CaO"]["values"]["composition_wt_pct"] == {
        "CaO": "27.4", "MgO": "12.3", "Al2O3": "57.5", "MnO": "0.274", "FeOT": "0.959"
    }
    assert rows["1823-4"]["Fe_tO"]["values"]["component_amount"] == "1.86"
    assert rows["1823-4"]["Fe_tO"]["locator"]["page"] == 2
    assert rows["1823-4"]["Fe_tO"]["locator"]["pdf_page_index"] == 1
    assert all(
        observation["values"]["quantity"] == "equilibrium_phase_composition"
        and observation["values"]["method_class"] == "measured_direct"
        for row in rows.values() for observation in row.values()
    )


def test_table1_rendered_cells_and_row_count(tmp_path: Path) -> None:
    _assert_table1_rendered_cells_and_row_count(EXTRACT)

    original = EXTRACT.read_text(encoding="utf-8")
    needle = "component_amount: '43.5'\n"
    assert original.count(needle) == 1
    mutated_path = tmp_path / EXTRACT.name
    mutated_path.write_text(
        original.replace(needle, "component_amount: '43.6'\n", 1),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError) as exc_info:
        _assert_table1_rendered_cells_and_row_count(mutated_path)
    assert "43.6" in str(exc_info.value)


def test_ohta_extract_isolated_migration_and_survives_existing_values() -> None:
    document = _document()
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues

    # Existing numeric prose values remain unchanged; these are activity values,
    # while Table 1 observations are measured phase compositions.
    activities = [
        observation["values"].get("activity")
        for observation in document["species"]["Al2O3"]["observations"]
        if observation["observation_id"].startswith("ohta_1996_al2o3_activity_")
    ]
    assert activities[:6] == [0.14, 0.13, 0.83, 0.83, 0.33, 0.26]
    prose_value = next(
        row for row in document["species"]["Al2O3"]["observations"]
        if row["observation_id"] == "ohta_1996_al2o3_activity_mgo_spinel_saturation_1873"
    )
    assert prose_value["locator"]["page"] == 3
    assert prose_value["locator"]["pdf_page_index"] == 2
    assert prose_value["locator"]["section"] == "3.1"

    table_ids = {
        observation["observation_id"]
        for row in _table_rows(document).values()
        for observation in row.values()
    }
    migrated_table = {
        source_observation["observation_id"]: next(
            observation
            for observation_id, observation in result.observations.items()
            if observation_id.endswith(source_observation["observation_id"])
        )
        for row in _table_rows(document).values()
        for source_observation in row.values()
    }
    assert len(migrated_table) == len(table_ids) == 130
    assert all(
        observation.identity.temperature_K.is_value
        and observation.identity.composition.is_value
        for observation in migrated_table.values()
    )
    table_queue = [
        item for item in result.queue
        if item.source == "data/literature/extracts/ohta-1996-cao-mgo-al2o3-activities.yaml"
        and item.observation_id.rsplit("::", 1)[-1] in table_ids
    ]
    assert all(not ({"quantity", "phase"} & set(item.axes)) for item in table_queue)
    for suffix in (
        "ohta_1996_table1_r1873_1_slag_cao",
        "ohta_1996_table1_r1873_1_slag_al2o3",
        "ohta_1996_table1_r1823_4_slag_fe_to",
    ):
        migrated = next(
            observation for oid, observation in result.observations.items()
            if oid.endswith(suffix)
        )
        assert migrated.identity.temperature_K.is_value
        assert migrated.identity.composition.is_value
        assert migrated.identity.total_pressure_Pa.is_unknown
        assert migrated.identity.fO2_Pa.is_unknown
