from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/greig-1927-ajs-immiscibility-silicate-melts-part1.yaml"


def test_greig_1927_quench_temperatures_migrate_and_pin_table_values():
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
    assert len(migrator.result.observations) == 3
    rows = document["species"]["SiO2"]["observations"]
    assert rows[0]["values"]["T_C_as_printed"] == 1713
    assert rows[1]["values"]["T_C_as_printed"] == 1698
    assert rows[2]["values"]["T_C_as_printed"] == 1695
