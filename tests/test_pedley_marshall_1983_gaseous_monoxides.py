from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/pedley-marshall-1983-gaseous-monoxides.yaml"


def test_pedley_marshall_1983_tables_4_and_5_migrate_and_pin_enthalpies():
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
    assert len(migrator.result.observations) == 132
    observations = document["species"]["gaseous_monoxide_assessments"]["observations"]
    table4 = next(row for row in observations if row["observation_id"].endswith("table_4_assessed_formation_enthalpies"))
    table5 = next(row for row in observations if row["observation_id"].endswith("table_5_assessed_formation_enthalpies"))
    ag = next(row for row in table4["values"]["rows"] if row["sample"] == "AgO")
    zr = next(row for row in table5["values"]["rows"] if row["sample"] == "ZrO")
    assert ag["delta_fH"] == 75.0  # kcal/mol, Table 4 p. 982
    assert zr["delta_fH"] == 82  # kJ/mol, Table 5 p. 983
