from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/zaitsev-2000-ms2000-045.yaml"


def test_zaitsev_2000_table_1_activities_migrate_and_pin_printed_values():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(
        root=ROOT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.ok
    assert migrator.result.validation.hard_issues == ()
    assert len(migrator.result.observations) == 33
    row = next(
        row for row in document["species"]["MnO"]["observations"]
        if row["observation_id"] == "zaitsev_2000_table1_mno_activities_eqn5"
    )
    points = row["values"]["points"]
    assert (points[0]["x_SiO2_as_printed"], points[0]["activity_eqn5_as_printed"]) == ("0.515", "0.137")
    assert (points[3]["x_SiO2_as_printed"], points[3]["activity_eqn5_as_printed"]) == ("0.405", "0.254")
