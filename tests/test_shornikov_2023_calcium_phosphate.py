from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2023-calcium-phosphate.yaml"


def test_shornikov_2023_calcium_phosphate_migrates_and_pins_printed_examples():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(
        ROOT,
        EXTRACT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.ok
    assert migrator.result.validation.hard_issues == ()
    assert len(migrator.result.observations) == 4
    context = [
        row
        for rows in migrator.result.context_by_work.values()
        for row in rows
    ]
    activity = next(
        row for row in context
        if row["observation_id"] == "shornikov_2023_p4o10_activity_estimate_cap3o8"
    )
    mixing = next(
        row for row in context
        if row["observation_id"] == "shornikov_2023_minimum_mixing_energy"
    )
    assert activity["values"]["activity"] == 2.21e-38
    assert activity["values"]["temperature_K"] == 1900
    assert mixing["values"]["mixing_energy_kJ_mol"] == -210
