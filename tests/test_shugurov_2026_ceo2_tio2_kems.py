from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shugurov-2026-ceo2-tio2-kems.yaml"


def test_table_2_and_3_migrate_printed_points_with_identity_and_conditions():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=ROOT, index={document["source_id"]: document["source"]}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 50
    assert all(item.value.kind.value == "point" for item in observations)
    assert all("source_value_outside_physical_range" not in item.notices for item in observations)

    by_id = {item.observation_id.split("::", 1)[1].split("::T=", 1)[0]: item for item in observations}
    assert by_id["shugurov-2026-ceo2-tio2-kems_table3_ceo2_activity_x0p1"].value.point == Decimal("0.006")
    assert by_id["shugurov-2026-ceo2-tio2-kems_table3_tio2_activity_x0p7"].value.point == Decimal("0.072")
    tio2_reference = by_id["shugurov-2026-ceo2-tio2-kems_table3_tio2_activity_x0p1"].identity.reference_state
    assert tio2_reference.tag.value == "unknown"
    assert "Ti3O5" in tio2_reference.reason
    assert by_id["shugurov-2026-ceo2-tio2-kems_table2_sample3_onset"].value.point == Decimal("2036.15")
    ti_context = document["species"]["TiO2"]["observations"]
    assert all("Ti3O5" in item["standard_state"]["state"]["reason"] for item in ti_context)
    xrd_rows = document["species"]["unknown"]["context"]
    ti_complements = {row["values"]["sample_id"]: row["values"]["synthesis_mole_fractions"]["TiO2"] for row in xrd_rows if row.get("type") == "source_context" and row.get("locator", {}).get("table") == "1"}
    assert [ti_complements[str(sample)] for sample in (7, 8, 9)] == ["0.3", "0.2", "0.1"]

    for species, container in document["species"].items():
        for raw in container.get("observations", []):
            item = by_id[raw["observation_id"]]
            expected = species if species != "unknown" else "unknown"
            assert item.identity.species.formula == expected
            if raw["observation_id"].startswith("shugurov-2026-ceo2-tio2-kems_table3_"):
                assert item.identity.temperature_K.value == Decimal("2150")
                assert item.identity.composition.is_value
                assert item.point_conditions["temperature_K"].state.value == Decimal("2150")
                assert item.point_conditions["composition"].state.is_value
            if raw["observation_id"].startswith("shugurov-2026-ceo2-tio2-kems_table2_"):
                assert item.point_conditions["composition"].state.is_value
