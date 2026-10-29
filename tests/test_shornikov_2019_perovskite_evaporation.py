from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2019-perovskite-evaporation.yaml"


def test_shornikov_2019_perovskite_table_1_migrates_and_pins_printed_pressures():
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
    assert len(migrator.result.observations) == 76
    ca = document["species"]["Ca"]["observations"][0]["values"]["points"]
    cao = document["species"]["CaO"]["observations"][0]["values"]["points"]
    assert (ca[0]["T_K"], ca[0]["pressure_atm"]) == (1791, 2.08e-9)
    assert (cao[0]["T_K"], cao[0]["pressure_atm"]) == (1920, 3.93e-12)
