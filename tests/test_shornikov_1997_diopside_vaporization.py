from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    Phase,
    Quantity,
    ReferenceStateConvention,
    ValueKind,
)
from simulator.battery.migrate import Migrator, convert_pressure_to_pa
from simulator.battery.records import AmountBasis
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-1997-diopside-vaporization.yaml"


def _migrated(extract: Path = EXTRACT):
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(extract)
    return migrator.result


_TABLE1_PRESSURES_ATM = {
    "shornikov_1997_ca_pressure_table1": [
        (1566, "1.6e-10", "2e-11"),
        (1640, "6.7e-10", "5e-11"),
        (1688, "1.5e-9", "1e-10"),
        (1743, "4.0e-9", "2e-10"),
        (1778, "7.2e-9", "3e-10"),
        (1862, "2.63e-8", "1e-9"),
    ],
    "shornikov_1997_mg_pressure_table1": [
        (1566, "2.2e-9", "2e-10"),
        (1640, "8.1e-9", "7e-10"),
        (1688, "1.9e-8", "1e-9"),
        (1743, "4.9e-8", "2e-9"),
        (1778, "8.5e-8", "3e-9"),
        (1862, "2.98e-7", "1e-8"),
    ],
    "shornikov_1997_sio_pressure_table1": [
        (1566, "1.2e-7", "2e-8"),
        (1640, "5.2e-7", "5e-8"),
        (1688, "1.1e-6", "1e-7"),
        (1743, "3.0e-6", "2e-7"),
        (1778, "5.2e-6", "3e-7"),
        (1862, "1.98e-5", "1e-6"),
    ],
    "shornikov_1997_sio2_pressure_table1": [
        (1688, "1.2e-10", "3e-11"),
        (1743, "5.4e-10", "5e-11"),
        (1778, "1.1e-9", "1e-10"),
        (1862, "5.9e-9", "5e-10"),
    ],
    "shornikov_1997_moo2_pressure_table1": [
        (1566, "6.5e-9", "6e-10"),
        (1640, "1.4e-8", "2e-9"),
        (1688, "2.1e-8", "2e-9"),
        (1743, "2.2e-8", "2e-9"),
        (1778, "2.9e-8", "2e-9"),
        (1862, "3.8e-8", "3e-9"),
    ],
    "shornikov_1997_moo3_pressure_table1": [
        (1566, "1.1e-9", "2e-10"),
        (1640, "3.2e-9", "4e-10"),
        (1688, "5.7e-9", "4e-10"),
        (1743, "6.4e-9", "4e-10"),
        (1778, "8.6e-9", "5e-10"),
        (1862, "1.2e-8", "1e-9"),
    ],
    "shornikov_1997_o_atomic_pressure_table1": [
        (1566, "4.0e-13", "1e-13"),
        (1640, "3.8e-12", "4e-13"),
        (1688, "1.5e-11", "2e-12"),
        (1743, "6.3e-11", "8e-12"),
        (1778, "1.4e-10", "1.5e-11"),
        (1862, "9.2e-10", "1e-10"),
    ],
    "shornikov_1997_o_atomic_alternate_pressure_table1": [
        (1688, "1.1e-11", "3e-12"),
        (1743, "5.0e-11", "9e-12"),
        (1778, "1.2e-10", "1.8e-11"),
        (1862, "7.0e-10", "1.5e-10"),
    ],
    "shornikov_1997_o2_pressure_table1": [
        (1566, "2.0e-15", "1e-15"),
        (1640, "2.8e-14", "4e-15"),
        (1688, "1.5e-13", "2e-14"),
        (1743, "8.2e-13", "1.3e-13"),
        (1778, "1.69e-12", "1.5e-13"),
        (1862, "1.86e-11", "9e-13"),
    ],
}


def _assert_table1_migrated_pressures(result):
    rows = [
        row
        for row in result.observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.P_PARTIAL
        and "_pressure_table1::" in row.observation_id
    ]
    assert len(rows) == 50
    assert len(_TABLE1_PRESSURES_ATM) == 9
    assert sum(map(len, _TABLE1_PRESSURES_ATM.values())) == 50

    for observation_id, expected_points in _TABLE1_PRESSURES_ATM.items():
        matching = [
            row for row in rows if f"::{observation_id}::T=" in row.observation_id
        ]
        matching.sort(
            key=lambda row: Decimal(row.observation_id.split("::T=")[1].split(":")[0])
        )
        assert len(matching) == len(expected_points)
        for row, (temperature, pressure_atm, sigma_atm) in zip(
            matching, expected_points, strict=True
        ):
            expected_pa, relation = convert_pressure_to_pa(
                Decimal(pressure_atm), "atm"
            )
            assert relation == "atm_to_Pa"
            assert row.value.kind is ValueKind.POINT
            assert row.value.point == expected_pa, row.observation_id
            assert Decimal(str(row.uncertainty.verbatim["sigma"])) == Decimal(
                sigma_atm
            )
            temperature_condition = row.point_conditions["temperature_K"]
            composition_condition = row.point_conditions["composition"]
            assert temperature_condition.state.is_value
            assert temperature_condition.state.value == Decimal(temperature)
            assert temperature_condition.locator is not None
            assert composition_condition.locator is not None


def test_table_values_conditions_and_uncertainties_reach_migrated_records():
    result = _migrated()
    records = list(result.observations.values())
    assert len(records) == 81

    pressure_points = [
        row for row in records
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.P_PARTIAL
        and "_pressure_table1::" in row.observation_id
    ]
    assert len(pressure_points) == 50
    for row in pressure_points:
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value.value == "g"
        assert row.point_conditions["temperature_K"].state.is_value
        if "moo" in row.observation_id:
            assert row.point_conditions["composition"].state.is_unknown
        else:
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
        if quantity is Quantity.PARTIAL_MOLAR_ENTHALPY:
            assert row.identity.composition.is_value
            composition = row.identity.composition.value
            assert composition.amount_basis is AmountBasis.MOLE_FRACTION
            assert dict(composition.components) == {
                "CaO": Decimal("0.25"),
                "MgO": Decimal("0.25"),
                "SiO2": Decimal("0.5"),
            }
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


def test_table1_preserves_temperature_widths_separately_from_pressure_widths():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    expected_widths = {1566: 7, 1640: 3, 1688: 5, 1743: 8, 1778: 5, 1862: 4}
    table1_points = [
        point
        for entry in document["species"].values()
        for observation in entry.get("observations", [])
        if "_pressure_table1" in observation["observation_id"]
        for point in observation["values"].get("points", [])
    ]

    assert len(table1_points) == 50
    for point in table1_points:
        temperature = point["temperature_K"]
        width = expected_widths[temperature]
        assert point["temperature_uncertainty_K_as_printed"] == width
        assert point["temperature_as_printed"] == f"{temperature} ± {width} K"


def test_mgo_activity_uses_the_source_stated_pure_solid_reference():
    rows = [
        row
        for row in _migrated().observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.ACTIVITY
        and row.identity.species.formula == "MgO"
    ]

    assert len(rows) == 6
    for row in rows:
        reference = row.identity.reference_state
        assert reference is not None and reference.is_value
        standard_state = reference.value
        assert standard_state.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
        assert standard_state.endmember.formula == "MgO"
        assert standard_state.endmember.phase.is_value
        assert standard_state.endmember.phase.value is Phase.CR
        assert standard_state.endmember.polymorph is None
        assert standard_state.reference_pressure_bar is None


def test_table1_scaled_pressure_widths_and_point_temperatures_reach_migration():
    _assert_table1_migrated_pressures(_migrated())


def test_mutating_consumed_pressure_atm_makes_numeric_regression_fail(tmp_path: Path):
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    ca_points = document["species"]["Ca"]["observations"][0]["values"]["points"]
    ca_points[0]["pressure_atm"] = 1.6e-9
    mutated_extract = tmp_path / EXTRACT.name
    mutated_extract.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(AssertionError, match="shornikov_1997_ca_pressure_table1"):
        _assert_table1_migrated_pressures(_migrated(mutated_extract))
