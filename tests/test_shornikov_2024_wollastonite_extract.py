from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2024-wollastonite-evaporation.yaml"
ROW_IDS = {
    "shornikov_2024_crystalline_formation_enthalpy",
    "shornikov_2024_crystalline_formation_entropy",
    "shornikov_2024_liquid_formation_enthalpy",
    "shornikov_2024_liquid_formation_entropy",
}


def test_oxide_formation_functions_remain_unavailable_in_migration() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    records = {
        row["observation_id"]: row
        for row in document["species"]["CaSiO3"]["observations"]
    }
    assert set(records) >= ROW_IDS
    assert {gap["observation_id"] for gap in document["schema_gaps"]} == ROW_IDS
    for row_id in ROW_IDS:
        values = records[row_id]["values"]
        assert values["reference_state_as_printed"] == "formation from simple oxides"
        assert "formation from simple oxides" in values["quantity_as_printed"]
        assert "value_as_printed" in values
        assert "quantity" not in values
        assert "delta_fH" not in values
        assert "S" not in values

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = {
        key.rsplit("::", 1)[-1]: observation
        for key, observation in migrator.result.observations.items()
        if key.rsplit("::", 1)[-1] in ROW_IDS
    }
    assert set(migrated) == ROW_IDS
    assert all(not item.identity.quantity.is_value for item in migrated.values())
    assert all(item.value.kind.value == "unavailable" for item in migrated.values())
    assert not migrator.result.validation.hard_issues
