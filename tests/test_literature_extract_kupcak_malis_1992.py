from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery import migrate as battery_migrate

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kupcak-malis-1992-na2o-sio2-oxygen-probe-emf.yaml"

TABLE_II_PINS = {
    "kupcak_1992_table2_partial_gibbs_x0p45": "54.4±3.8",
    "kupcak_1992_table2_partial_entropy_x0p50": "-55±52",
    "kupcak_1992_table2_partial_enthalpy_x0p75": "-103.0±21.0",
}


def _table_ii_cells(document: dict) -> dict[str, str]:
    return {
        row["observation_id"]: row["values"]["value_as_printed"]
        for row in document["species"]["Na2O"]["observations"]
    }


def test_render_pins_and_table_row_counts() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = document["species"]["Na2O"]["context"][0]
    groups = context["values"]["measurements_as_printed"]
    assert len(groups) == 6
    assert sum(len(group["pairs_as_printed"]) for group in groups) == 40
    assert context["locator"] == {"page": 3, "table": "I", "note": "Table I; measured EMF of concentration cell (I) relative to the 0.40 Na2O–0.60 SiO2 reference melt."}
    assert context["values"]["equation_3_as_printed"] == "E = −(RT/2F) ln[a(Na2O)/a⁰(Na2O)]"

    rows = document["species"]["Na2O"]["observations"]
    assert len(rows) == 21
    assert len({tuple(row["point_conditions"]["composition"]["state"]["value"]["components"][1]) for row in rows}) == 7
    for observation_id, value in TABLE_II_PINS.items():
        assert _table_ii_cells(document)[observation_id] == value
        assert next(row for row in rows if row["observation_id"] == observation_id)["locator"] == {
            "page": 4,
            "table": "II",
            "note": next(row for row in rows if row["observation_id"] == observation_id)["locator"]["note"],
        }

    mutated = deepcopy(document)
    mutated_row = next(
        row
        for row in mutated["species"]["Na2O"]["observations"]
        if row["observation_id"] == "kupcak_1992_table2_partial_entropy_x0p50"
    )
    mutated_row["values"]["value_as_printed"] = "-54±52"
    with pytest.raises(AssertionError):
        assert _table_ii_cells(mutated) == _table_ii_cells(document)


def test_isolated_migration_binds_conditions_and_only_schema_gap_quantities_are_unknown() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = battery_migrate.Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert migrator.result.validation.hard_issues == ()
    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
    }
    source_rows = document["species"]["Na2O"]["observations"]
    assert len(source_rows) == len(migrated) == 21
    assert all(row["values"]["method_class"] == "measured_tabulated" for row in source_rows)

    expected_unknown = {
        row["observation_id"]
        for row in source_rows
        if "partial_gibbs" in row["observation_id"] or "partial_entropy" in row["observation_id"]
    }
    actual_unknown = {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.quantity is None or row.identity.quantity.is_unknown
    }
    assert len(expected_unknown) == 14
    assert actual_unknown == expected_unknown
    assert all(
        row.identity.quantity.is_value
        for observation_id, row in migrated.items()
        if observation_id not in expected_unknown
    )
    assert all(
        row.evidence.class_.is_value
        and row.evidence.class_.value.value == "measured_tabulated"
        for row in migrated.values()
    )
    assert all(row.identity.species.phase.is_value for row in migrated.values())
    assert all(row.identity.species.phase.value.value == "l" for row in migrated.values())
    assert all(row.identity.temperature_K.value == Decimal("1473") for row in migrated.values())

    x_by_id = {
        "x0p45": ("0.55", "0.45"),
        "x0p50": ("0.50", "0.50"),
        "x0p55": ("0.45", "0.55"),
        "x0p60": ("0.40", "0.60"),
        "x0p65": ("0.35", "0.65"),
        "x0p70": ("0.30", "0.70"),
        "x0p75": ("0.25", "0.75"),
    }
    for observation_id, row in migrated.items():
        suffix = observation_id.rsplit("_", 1)[-1]
        expected = x_by_id[suffix]
        assert row.identity.composition.is_value
        assert row.identity.composition.value.as_map() == {
            "Na2O": Decimal(expected[0]),
            "SiO2": Decimal(expected[1]),
        }
        assert row.locator.page == 4
        assert row.locator.table == "II"
        assert row.identity.total_pressure_Pa is None or row.identity.total_pressure_Pa.is_unknown
        assert (
            row.identity.fO2_Pa is None
            or row.identity.fO2_Pa.is_unknown
            or row.identity.fO2_Pa.is_not_applicable
        )

    context = document["species"]["Na2O"]["context"][0]
    assert context["locator"]["page"] == 3 and context["locator"]["table"] == "I"
    migrated_context = [
        row
        for rows in migrator.result.context_by_work.values()
        for row in rows
        if row.get("context_id") == "kupcak-malis-1992-na2o-sio2-oxygen-probe-emf::context::kupcak_1992_table1_emf_context"
    ]
    assert len(migrated_context) == 1
    assert migrated_context[0]["locator"]["page"] == 3
    assert migrated_context[0]["locator"]["table"] == "I"
    assert sum(
        len(group["pairs_as_printed"])
        for group in migrated_context[0]["values"]["measurements_as_printed"]
    ) == 40
    assert "kupcak_1992_table1_emf_context" not in migrated

