from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/sineva-2023-ni-sn-zn-slag-copper.yaml"

# Independent Table II/III render transcription pins. PDF pages 7, 9, and 10
# were rendered at 600 dpi; Table II's dropped minus signs were restored only
# after checking the printed glyphs on those renders.
RENDER_PINS = {
    "sineva23_tii_exp1_metal_cu2o": ("Cu2O", Decimal("96.0"), 7, Decimal("-7.1")),
    "sineva23_tii_exp1_slag_feo": ("FeO", Decimal("61.9"), 7, Decimal("-7.1")),
    "sineva23_tiii_exp56_slag_cao": ("CaO", Decimal("14.4"), 10, Decimal("-8.34")),
}


def test_sineva_tables_match_render_and_all_rows_migrate_with_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    observations = {
        observation["observation_id"]: observation
        for species in document["species"].values()
        for observation in species["observations"]
    }
    assert len(observations) == 648

    for observation_id, (formula, number, page, oxygen) in RENDER_PINS.items():
        row = observations[observation_id]
        assert row["values"]["component_as_published"] == formula
        assert Decimal(str(row["values"]["value"])) == number
        assert Decimal(str(row["values"]["log_fO2"]["value"])) == oxygen
        assert row["locator"]["page"] == page
        assert row["locator"]["pdf_page_index"] == page - 1
        assert row["values"]["log_fO2"]["units"] == "log10(atm)"

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = list(migrator.result.observations.values())
    assert len(migrated) == 648
    assert not migrator.result.validation.hard_issues
    assert all(row.value.kind is ValueKind.POINT for row in migrated)

    by_id = {row.observation_id.split("::", 1)[-1]: row for row in migrated}
    for observation_id, (formula, number, _page, oxygen) in RENDER_PINS.items():
        row = by_id[observation_id]
        assert row.value.point == number
        assert row.identity.species.formula == formula
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1523.15")
        for key, expected in (("temperature_K", Decimal("1523.15")), ("fO2_log", oxygen)):
            condition = row.point_conditions[key]
            assert condition.state.is_value
            assert condition.state.value == expected
            assert condition.locator is not None

    for row in migrated:
        assert row.identity.species.formula in document["species"]
        assert row.identity.temperature_K.is_value
        assert row.point_conditions["temperature_K"].locator is not None
        assert row.point_conditions["fO2_log"].locator is not None
        assert not any("source_value_outside_physical_range" in notice for notice in row.notices)
