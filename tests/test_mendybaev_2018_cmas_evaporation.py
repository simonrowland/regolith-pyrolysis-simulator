from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/mendybaev-2018-cmas-evaporation.yaml"


def test_mendybaev_2018_quoted_mg_rates_migrate_and_pin_printed_values():
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
    assert len(migrator.result.observations) == 4
    points = document["species"]["Mg"]["observations"][0]["values"]["points"]
    assert (points[0]["temperature_C_as_printed"], points[0]["rate_as_printed"]) == (1600, "~2×10^-9")
    assert (points[1]["temperature_C_as_printed"], points[1]["rate_as_printed"]) == (1700, "~1×10^-8")
