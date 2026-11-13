"""Pins context-only mixing-energy examples in Shornikov et al. (2023)."""

from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2023-silica-phosphate-melts.yaml"
SOURCE_ID = "shornikov-2023-silica-phosphate-melts"
CONTEXT_IDS = {
    "shornikov_2023_sio2_mixing_minimum_1100",
    "shornikov_2023_sio2_mixing_minimum_1873",
}


def test_mixing_energy_examples_remain_context_only() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = {
        row["observation_id"]: row
        for row in document["species"]["SiO2"]["context"]
    }

    assert set(rows) >= CONTEXT_IDS
    assert rows["shornikov_2023_sio2_mixing_minimum_1100"]["values"][
        "method_class"
    ] == "quoted_attributed"
    assert rows["shornikov_2023_sio2_mixing_minimum_1873"]["values"][
        "method_class"
    ] == "quoted_attributed"


def test_isolated_migration_has_no_hard_issues() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(
        ROOT,
        EXTRACT,
        index={SOURCE_ID: dict(document["source"], source_id=SOURCE_ID)},
        aliases={},
    )
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
