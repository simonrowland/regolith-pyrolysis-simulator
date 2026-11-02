"""Render-grounded pins for Rego, Sigworth, and Philbrook (1985)."""

from collections import Counter
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SID = "rego-1985-na2o-sio2-transpiration"
EXTRACT = ROOT / "data/literature/extracts" / f"{SID}.yaml"

# Independently transcribed from the PDF page 4 render of printed Table I.
RENDER_GRID = {
    1: ("17.89", "26.3", "1.015", "10.0", "19.60"),
    2: ("18.34", "28.0", "1.009", "8.5", "15.60"),
    3: ("22.18", "20.6", "0.998", "17.4", "19.60"),
    4: ("27.52", "20.0", "1.000", "11.6", "7.87"),
    5: ("27.48", "20.6", "0.977", "19.4", "11.50"),
    6: ("30.94", "19.5", "1.005", "28.1", "11.70"),
    7: ("30.59", "21.4", "1.006", "27.6", "11.70"),
    8: ("35.18", "19.1", "0.989", "13.5", "3.89"),
    9: ("34.88", "13.9", "0.983", "19.1", "3.85"),
    10: ("34.89", "14.3", "0.987", "14.2", "3.86"),
    11: ("37.11", "19.8", "0.984", "23.4", "3.86"),
    12: ("36.74", "21.9", "1.006", "19.0", "3.93"),
    13: ("40.63", "14.4", "1.003", "40.4", "3.85"),
    14: ("41.29", "15.1", "0.996", "43.6", "3.93"),
    15: ("46.46", "16.3", "1.003", "79.6", "3.93"),
    16: ("22.40", "17.8", "0.981", "13.10", "3.88"),
    17: ("21.96", "18.5", "0.998", "11.30", "3.86"),
    18: ("22.33", "29.7", "0.989", "10.70", "3.87"),
    19: ("22.64", "28.2", "0.991", "12.50", "3.88"),
    20: ("22.47", "16.4", "0.991", "7.62", "1.92"),
    21: ("22.18", "21.0", "0.998", "12.70", "3.84"),
    22: ("27.47", "12.9", "0.990", "11.20", "1.92"),
    23: ("28.13", "15.7", "0.990", "12.40", "1.92"),
    24: ("27.93", "19.3", "0.993", "11.80", "1.92"),
    25: ("31.84", "20.0", "0.990", "16.20", "1.92"),
    26: ("31.64", "19.8", "0.992", "12.50", "1.92"),
    27: ("32.09", "18.2", "0.990", "17.10", "1.93"),
    28: ("35.69", "11.2", "0.991", "27.60", "1.93"),
    29: ("35.86", "16.3", "0.992", "31.60", "1.93"),
    30: ("36.09", "16.5", "1.002", "32.70", "1.95"),
    31: ("40.66", "17.9", "1.003", "108.50", "3.93"),
}

# Independent expected outputs from the rendered Table I inputs and printed
# Eqs. [4] and [6], with full precision carried through Eq. [6]. pNa is
# unit-converted to Pa by the real migrator after its final atm rounding.
EXPECTED_PNA_PA = (
    "226.968", "243.18", "387.0615", "639.36075", "713.328", "1033.515",
    "1023.3825", "1459.08", "2036.6325", "1540.14", "2462.1975", "2036.6325",
    "4164.4575", "4346.8425", "7335.93", "1408.4175", "1246.2975", "1165.2375",
    "1357.755", "1661.73", "1408.4175", "2391.27", "2634.45", "2522.9925",
    "3374.1225", "2654.715", "3526.11", "5400.6225", "6079.5", "6261.885",
    "9382.695",
)
EXPECTED_ACTIVITY = (
    "2.67e-9", "3.0e-9", "7.62e-9", "2.08e-8", "2.53e-8", "5.48e-8",
    "5.31e-8", "1.06e-7", "2.05e-7", "1.18e-7", "2.99e-7", "2.09e-7",
    "8.54e-7", "9.22e-7", "2.57e-6", "1.52e-8", "1.22e-8", "1.06e-8",
    "1.43e-8", "2.14e-8", "1.54e-8", "4.39e-8", "5.30e-8", "4.88e-8",
    "8.63e-8", "5.42e-8", "9.42e-8", "2.17e-7", "2.73e-7", "2.93e-7",
    "6.36e-7",
)

TABLE_FIELDS = (
    "Na2O_mol_pct_as_published",
    "CO_flow_rate_ml_min_as_published",
    "P_atm_as_published",
    "Na_mg_in_leached_solution_as_published",
    "CO_moles_collected_x10_minus2_as_published",
)


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _input_rows(document):
    return {
        int(row["values"]["run_number_as_published"]): row
        for row in document["species"]["Na"]["observations"]
        if row["observation_id"].endswith("_inputs")
    }


def _assert_render_grid(document):
    rows = _input_rows(document)
    assert len(rows) == 31
    assert set(rows) == set(RENDER_GRID)
    for run, expected in RENDER_GRID.items():
        row = rows[run]
        assert tuple(row["values"][field] for field in TABLE_FIELDS) == expected
        assert row["locator"]["page"] == 4
        assert row["locator"]["published_page"] == 316
        assert row["locator"]["table"] == "I"
        assert row["locator"]["row"] == str(run)


def _assert_migrated_reductions_and_conditions(observations):
    assert len(EXPECTED_PNA_PA) == len(EXPECTED_ACTIVITY) == 31
    for run, rendered in RENDER_GRID.items():
        suffix = f"{run:02d}"
        input_row = observations[f"{SID}::rego_1985_table1_run_{suffix}_inputs"]
        pressure_row = observations[f"{SID}::rego_1985_table1_run_{suffix}_pna_eq4"]
        activity_row = observations[f"{SID}::rego_1985_table1_run_{suffix}_activity_eq6"]

        assert pressure_row.identity.quantity.value is Quantity.P_PARTIAL
        assert pressure_row.value.point == Decimal(EXPECTED_PNA_PA[run - 1])
        assert activity_row.identity.quantity.value is Quantity.ACTIVITY
        assert activity_row.value.point == Decimal(EXPECTED_ACTIVITY[run - 1])
        assert pressure_row.locator.page == 1
        assert pressure_row.locator.equation == "4"
        assert activity_row.locator.page == 2
        assert activity_row.locator.equation == "6"
        assert pressure_row.identity.species.phase.value is Phase.G
        assert activity_row.identity.species.phase.value is Phase.L

        temperature_c = Decimal("1300" if run <= 15 else "1400")
        na2o = Decimal(rendered[0]) / Decimal(100)
        expected_composition = (("Na2O", na2o), ("SiO2", Decimal(1) - na2o))
        expected_pressure_pa = Decimal(rendered[2]) * Decimal("101325")
        for observation in (input_row, pressure_row, activity_row):
            identity = observation.identity
            assert identity.temperature_K.value == temperature_c + Decimal("273.15")
            assert identity.composition.value.components == expected_composition
            assert identity.total_pressure_Pa.value == expected_pressure_pa
            if observation is input_row:
                assert identity.fO2_Pa is None
            else:
                assert identity.fO2_Pa is not None and identity.fO2_Pa.is_unknown

            conditions = observation.point_conditions
            assert conditions is not None
            assert conditions["T_C"].state.value == temperature_c
            assert conditions["T_C"].locator.page == 4
            assert conditions["T_C"].locator.table == "I"
            assert conditions["composition"].state.value.components == expected_composition
            assert conditions["composition"].locator.page == 4
            assert conditions["composition"].locator.table == "I"
            assert conditions["total_pressure_Pa"].state.value == expected_pressure_pa
            if observation is pressure_row:
                assert conditions["total_pressure_Pa"].locator.page == 1
                assert conditions["total_pressure_Pa"].locator.equation == "4"
            else:
                assert conditions["total_pressure_Pa"].locator.page == 4
                assert conditions["total_pressure_Pa"].locator.table == "I"
            assert conditions["atmosphere"].state.value == (
                "pure CO gas (Matheson, UHP grade, 99.8 pct min)"
            )
            assert conditions["atmosphere"].locator.page == 2
            assert conditions["fO2_log"].state.is_unknown
            assert conditions["fO2_log"].locator.page == 1

        reference_state = activity_row.identity.reference_state
        assert reference_state is not None and reference_state.is_value
        assert reference_state.value.endmember.formula == "Na2O"
        assert reference_state.value.endmember.phase.value is Phase.L


def test_table1_render_grid_has_all_31_rows_and_detects_mutation():
    document = _document()
    _assert_render_grid(document)

    mutated = deepcopy(document)
    _input_rows(mutated)[10]["values"]["Na_mg_in_leached_solution_as_published"] = "14.3"
    with pytest.raises(AssertionError):
        _assert_render_grid(mutated)


def test_isolated_migration_binds_rendered_rows_and_refuses_only_the_input_bundle():
    document = _document()
    _assert_render_grid(document)

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 93
    assert len(result.experiments) == 31
    assert sum(
        observation.identity.quantity is not None
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.P_PARTIAL
        for observation in result.observations.values()
    ) == 31
    assert sum(
        observation.identity.quantity is not None
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY
        for observation in result.observations.values()
    ) == 31
    unavailable = {
        observation_id
        for observation_id, observation in result.observations.items()
        if observation.value.kind is ValueKind.UNAVAILABLE
    }
    expected_inputs = {
        f"{SID}::rego_1985_table1_run_{run:02d}_inputs" for run in range(1, 32)
    }
    assert unavailable == expected_inputs
    assert len(result.queue) == 93
    assert {entry.observation_id for entry in result.queue} == expected_inputs
    assert Counter(axis for entry in result.queue for axis in entry.axes) == Counter(
        {"quantity": 31, "method": 31, "value": 31}
    )
    print(
        "isolated migration:",
        f"obs={len(result.observations)}",
        f"hard={len(result.validation.hard_issues)}",
        f"unavailable={len(unavailable)}",
        f"queue={len(result.queue)}",
    )

    unknown_quantity = {
        observation_id
        for observation_id, observation in result.observations.items()
        if observation.identity.quantity is None or observation.identity.quantity.is_unknown
    }
    assert unknown_quantity == expected_inputs
    assert all(
        observation.identity.species.phase is not None
        and observation.identity.species.phase.is_value
        for observation in result.observations.values()
    )

    _assert_migrated_reductions_and_conditions(result.observations)
    migrated_input_row_10 = result.observations[
        f"{SID}::rego_1985_table1_run_10_inputs"
    ]
    assert migrated_input_row_10.locator.table == "I"
    assert migrated_input_row_10.locator.page == 4

    input_row_10 = next(
        row
        for row in document["species"]["Na"]["observations"]
        if row["observation_id"] == "rego_1985_table1_run_10_inputs"
    )
    input_row_16 = next(
        row
        for row in document["species"]["Na"]["observations"]
        if row["observation_id"] == "rego_1985_table1_run_16_inputs"
    )
    assert input_row_10["values"]["Na_mg_in_leached_solution_as_published"] == "14.2"
    assert input_row_16["values"]["CO_moles_collected_x10_minus2_as_published"] == "3.88"


def test_tenfold_run_18_activity_mutation_fails_the_migrated_pins():
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    observations = dict(migrator.result.observations)
    observation_id = f"{SID}::rego_1985_table1_run_18_activity_eq6"
    observation = observations[observation_id]
    observations[observation_id] = replace(
        observation,
        value=replace(observation.value, point=observation.value.point * Decimal(10)),
    )

    with pytest.raises(AssertionError):
        _assert_migrated_reductions_and_conditions(observations)
