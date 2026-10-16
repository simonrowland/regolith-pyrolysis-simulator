from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2002-spinel-mgal2o4.yaml"


def test_shornikov_2002_spinel_migrates_and_pins_printed_estimates():
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
    assert len(migrator.result.observations) == 1
    numeric_context = next(
        row
        for rows in migrator.result.context_by_work.values()
        for row in rows
        if row["observation_id"] == "shornikov_2002_numeric_context"
    )
    printed = numeric_context["values"]["values_as_printed"]
    assert printed["peak_Al2O3_mole_percent"] == "83.02±0.18"
    assert printed["formation_enthalpy_kJ_per_mol"] == "−13.3±1.2"
