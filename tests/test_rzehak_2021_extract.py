"""Pins measured Table 2 titanium-isotope observations and their migration."""

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/rzehak-2021-ti-isotope-redox-lunar-melts.yaml"


def test_rzehak_2021_table_2_and_isolated_migration() -> None:
    payload = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = payload["species"]["TiO2"]["observations"]
    assert len(observations) == 3
    by_phase = {
        row["values"].get("mineral_name_as_printed", row["phase"]): row
        for row in observations
    }
    assert set(by_phase) == {"orthopyroxene", "armalcolite", "glass"}
    assert len(by_phase["orthopyroxene"]["values"]["points"]) == 6
    assert by_phase["orthopyroxene"]["values"]["points"][-1]["isotope_delta_delta49Ti_permil"] == 0.247
    assert by_phase["armalcolite"]["values"]["points"][0]["isotope_delta_delta49Ti_permil"] == -0.056
    assert by_phase["glass"]["values"]["points"][-1]["uncertainty_permil"] == 0.006
    assert all(
        point["uncertainty_permil"] > 0
        for row in observations
        for point in row["values"]["points"]
    )

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    migrated = list(migrator.result.observations.values())
    assert len(migrated) == 18
    assert all(str(row.identity.temperature_K.value) == "1533.15" for row in migrated)
    assert all("redox_condition" in row.point_conditions for row in migrated)
    assert all(
        row.point_conditions["redox_condition"].state.tag.value == "unknown"
        and row.point_conditions["redox_condition"].locator.page == 7
        for row in migrated
    )
