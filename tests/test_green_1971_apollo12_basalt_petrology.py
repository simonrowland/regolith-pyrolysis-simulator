from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/green-1971-apollo12-basalt-petrology.yaml"


def test_green_1971_phase_temperatures_migrate_and_pin_printed_values():
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
    assert len(migrator.result.observations) == 6
    observations = document["species"]["Apollo12_12065"]["observations"]
    liquidus = next(row for row in observations if row["observation_id"] == "green1971_12065_liquidus")
    olivine = next(row for row in observations if row["observation_id"] == "green1971_12065_olivine_reacts_out")
    assert liquidus["values"]["temperature_C_as_printed"] == 1180
    assert olivine["values"]["temperature_C_as_printed"] == 1150
