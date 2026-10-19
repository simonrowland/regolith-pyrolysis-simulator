from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2002-mullite-thermodynamics.yaml"


def test_shornikov_mullite_tables_migrate_and_preserve_temperature_and_context():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = document["species"]["Al2O3"]["observations"]
    assert len(rows) == 27
    table_1 = next(
        row for row in document["species"]["Al2O3"]["context"]
        if row["observation_id"] == "shornikov_2002_table1_oxide_formation_inventory"
    )
    assert len(table_1["values"]["rows_as_printed"]) == 22
    assert table_1["values"]["rows_as_printed"][17].endswith("present study")

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
    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 27
    rows_by_id = {row["observation_id"]: row for row in rows}
    for observation in observations:
        source_id = observation.observation_id.rsplit("::", 1)[-1]
        source = rows_by_id[source_id]
        if source["values"]["quantity"] == "transition_temperature":
            # The transition temperature is the observable itself, not the
            # identity.temperature_K axis in this profile.
            assert observation.value.point == Decimal(str(source["values"]["T_K"]))
        else:
            assert observation.identity.temperature_K is not None
            assert observation.identity.temperature_K.is_value

    fusion = next(
        row for row in observations
        if "shornikov_2002_t2_fusion_present" in row.observation_id
    )
    assert fusion.value.point == Decimal("74.84")
    entropy = next(
        row for row in observations
        if "shornikov_2002_t1_s_pankratz" in row.observation_id
    )
    assert entropy.value.point == Decimal("254.39")
    formation_enthalpy = next(
        row for row in observations
        if "shornikov_2002_t1_hf_neumann" in row.observation_id
    )
    assert formation_enthalpy.value.point == Decimal("-7547.9")
