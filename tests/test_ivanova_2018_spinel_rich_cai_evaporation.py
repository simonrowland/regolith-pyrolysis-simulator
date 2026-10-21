from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/ivanova-2018-spinel-rich-cai-evaporation.yaml"


def test_ivanova_2018_residue_series_migrates_and_pins_table_1_values():
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
    assert len(migrator.result.observations) == 32
    series = document["species"]["CaO"]["observations"][0]["values"]["series"]
    first = next(row for row in series if row["sample"] == "5aN-3")
    later = next(row for row in series if row["sample"] == "5aN-2")
    assert first["MgO_wt_pct"] == 16.02
    assert later["SiO2_wt_pct"] == 13.95
