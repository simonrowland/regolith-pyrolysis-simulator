"""Table 1 measured Mg pressures and deferred figure metadata for Gribchenkova et al."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Quantity, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/gribchenkova-2017-al2o3-mgo.yaml"
EXPECTED = {
    "gribchenkova_2017_table1_MgO_pMg_peff": ("MgO", "0.20863"),
    "gribchenkova_2017_table1_MgO+MgAl2O4_pMg_peff": ("MgO + MgAl2O4", "0.20863"),
    "gribchenkova_2017_table1_MgAl2O4+Al2O3_pMg_peff": ("MgAl2O4 + Al2O3", "0.03237"),
}


def test_table1_printed_mg_pressures_are_retained() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table_context = next(
        row for row in document["species"]["Mg"]["context"]
        if row["observation_id"] == "gribchenkova_2017_table1_calculated_pressures"
    )
    assert "table_csv" not in table_context["values"]
    observations = document["species"]["Mg"]["observations"]
    assert {
        observation["observation_id"]: (
            observation["values"]["sample_composition_as_printed"],
            observation["values"]["P_Pa"],
        )
        for observation in observations
    } == EXPECTED
    assert all(observation["point_conditions"]["temperature_K"]["state"]["value"] == "1900" for observation in observations)

    context = document["species"]["Mg"]["context"]
    assert table_context["values"]["Mg_pmin"] == [
        {"composition": "MgO", "value": "0.21491"},
        {"composition": "MgO + MgAl2O4", "value": "0.21491"},
        {"composition": "MgAl2O4 + Al2O3", "value": "0.03259"},
    ]
    assert len([row for row in context if row.get("type") == "figure_record"]) == 4
    assert all(row["digitisation"] == "deferred to a figure pass" for row in context if row.get("type") == "figure_record")


def test_isolated_migration_preserves_species_conditions_and_points() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 3
    assert not result.validation.hard_issues
    assert not result.registry_issues

    by_source_id = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in result.observations.values()
    }
    for source_id, (composition, value) in EXPECTED.items():
        record = by_source_id[source_id]
        assert record.identity.quantity.value is Quantity.P_PARTIAL
        assert record.identity.species.formula == "Mg"
        assert record.identity.species.phase.value.value == "g"
        assert record.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        assert record.value.kind is ValueKind.POINT
        assert str(record.value.point) == value
        assert record.identity.temperature_K.is_value
        assert record.identity.temperature_K.value == Decimal("1900")
        assert record.point_conditions["temperature_K"].locator is not None
        assert record.point_conditions["composition"].state.value == composition
        assert record.point_conditions["composition"].locator is not None
        assert not record.point_conditions["total_pressure_Pa"].state.is_value
