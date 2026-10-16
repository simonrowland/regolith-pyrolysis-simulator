from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shults-1995-calcium-aluminate-thermodynamics.yaml"


def test_shults_1995_calcium_aluminate_migrates_and_pins_table_1_currents():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(
        root=ROOT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert len(migrator.result.observations) == 1
    context = [
        row
        for rows in migrator.result.context_by_work.values()
        for row in rows
    ]
    alo = next(row for row in context if row["observation_id"] == "shults_1995_table1_alo_intensity_al2o3")
    ca = next(row for row in context if row["observation_id"] == "shults_1995_table1_ca_intensity_cao")
    assert alo["values"]["ion_intensity"] == 0.18
    assert ca["values"]["ion_intensity"] == 100.0
