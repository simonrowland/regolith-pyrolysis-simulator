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
PRINTED_ROWS = {
    "shornikov_2024_crystalline_formation_enthalpy": (
        "enthalpy of wollastonite formation from simple oxides",
        "−38.92",
    ),
    "shornikov_2024_crystalline_formation_entropy": (
        "entropy of wollastonite formation from simple oxides",
        "2.33",
    ),
    "shornikov_2024_liquid_formation_enthalpy": (
        "enthalpy of wollastonite melt formation from simple oxides",
        "−11.29",
    ),
    "shornikov_2024_liquid_formation_entropy": (
        "entropy of wollastonite formation from simple oxides",
        "17.53",
    ),
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
        assert (values["quantity_as_printed"], values["value_as_printed"]) == PRINTED_ROWS[row_id]
        assert "quantity" not in values
        assert "delta_fH" not in values
        assert "S" not in values
        assert "entropy" not in values

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


def test_sc475_extracts_migrate_together_without_scoring_oxide_frame_rows() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    for path in (
        ROOT / "data/literature/extracts/itoh-1985-beta-alumina-na2o-emf.yaml",
        ROOT / "data/literature/extracts/arutyunyan-2005-magnesium-silicate-activities.yaml",
        EXTRACT,
    ):
        migrator._migrate_extract(path)
    migrator.finalize()

    observations = migrator.result.observations
    assert any("itoh_1985" in key for key in observations)
    assert sum("arutyunyan_2005" in key for key in observations) == 12
    migrated = {
        key.rsplit("::", 1)[-1]: observation
        for key, observation in observations.items()
        if key.rsplit("::", 1)[-1] in ROW_IDS
    }
    assert set(migrated) == ROW_IDS
    assert all(not item.identity.quantity.is_value for item in migrated.values())
    assert all(item.value.kind.value == "unavailable" for item in migrated.values())
    assert not migrator.result.validation.hard_issues
