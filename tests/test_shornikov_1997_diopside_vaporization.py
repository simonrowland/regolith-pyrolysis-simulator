from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import Migrator
from simulator.battery.records import AmountBasis
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-1997-diopside-vaporization.yaml"


def _migrated():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    return migrator.result


def test_table_values_conditions_and_uncertainties_reach_migrated_records():
    result = _migrated()
    records = list(result.observations.values())
    assert len(records) == 65

    pressure_points = [
        row for row in records
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.P_PARTIAL
        and "_pressure_table1::" in row.observation_id
    ]
    assert len(pressure_points) == 34
    for row in pressure_points:
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value.value == "g"
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["composition"].state.is_value
        composition = row.point_conditions["composition"].state.value
        assert composition.amount_basis is AmountBasis.MOL_INVENTORY
        assert dict(composition.components) == {
            "CaO": Decimal("1"), "MgO": Decimal("1"), "SiO2": Decimal("2")
        }
        assert row.value.kind is ValueKind.POINT
        assert row.uncertainty.verbatim["sigma"] > 0

    fit_rows = [
        row for row in records
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.P_PARTIAL
        and "_pressure_table2" in row.observation_id
    ]
    assert len(fit_rows) == 7
    assert all(row.value.kind is ValueKind.UNAVAILABLE for row in fit_rows)

    activity_rows = [
        row for row in records
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.ACTIVITY
    ]
    assert len(activity_rows) == 18
    assert all(row.point_conditions["composition"].state.is_value for row in activity_rows)
    assert all(row.point_conditions["temperature_K"].state.is_value for row in activity_rows)

    table4_rows = [row for row in records if "partial_" in row.observation_id and "table4" in row.observation_id]
    assert len(table4_rows) == 6
    expected = {
        "cao_partial_enthalpy": (Quantity.PARTIAL_MOLAR_ENTHALPY, Decimal("0.0"), Decimal("1.4")),
        "cao_partial_entropy": (Quantity.S, Decimal("17.0"), Decimal("1.5")),
        "mgo_partial_enthalpy": (Quantity.PARTIAL_MOLAR_ENTHALPY, Decimal("-40.2"), Decimal("2.6")),
        "mgo_partial_entropy": (Quantity.S, Decimal("19.1"), Decimal("1.5")),
        "sio2_partial_enthalpy": (Quantity.PARTIAL_MOLAR_ENTHALPY, Decimal("9.6"), Decimal("2.4")),
        "sio2_partial_entropy": (Quantity.S, Decimal("23.2"), Decimal("2.4")),
    }
    for name, (quantity, value, sigma) in expected.items():
        row = next(record for record in table4_rows if name in record.observation_id)
        assert row.identity.quantity.value is quantity
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == value
        assert Decimal(str(row.uncertainty.verbatim["sigma"])) == sigma
        assert row.point_conditions["composition"].state.is_value
        assert row.identity.temperature_K.is_unknown


def test_fidelity_pins_cover_table_values_and_fail_after_mutation():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []

    mutated = copy.deepcopy(document)
    points = mutated["species"]["Ca"]["observations"][0]["values"]["points"]
    points[0]["pressure_as_printed"] = "1.7 ± 0.2"
    assert check_all_fidelity_samples_match(mutated, label=str(EXTRACT))

    mutated_table4 = copy.deepcopy(document)
    points = mutated_table4["species"]["CaO"]["observations"][1]["values"]["points"]
    points[0]["sigma"] = 1.6
    assert check_all_fidelity_samples_match(mutated_table4, label=str(EXTRACT))
