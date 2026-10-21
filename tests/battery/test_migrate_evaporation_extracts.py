from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity, ValueKind
from tests.battery.test_migrate import _migrate_real_extract


SID = "kems-012-sossi-2019"
EXTRACT = f"{SID}.yaml"
FEDKIN_SID = "kems-005-fedkin-2006"
FEDKIN_EXTRACT = f"{FEDKIN_SID}.yaml"
HASHIMOTO_SID = "kems-015-hashimoto-1983"
HASHIMOTO_EXTRACT = f"{HASHIMOTO_SID}.yaml"
RICHTER07_SID = "kems-010-richter-2007"
RICHTER07_EXTRACT = f"{RICHTER07_SID}.yaml"
TABLE2_RUNS = {
    "sossi-2019-run-2m-16-07-16e": Decimal("848.9"),
    "sossi-2019-run-2m-16-07-16a": Decimal("742.5"),
    "sossi-2019-run-2m-16-07-16b": Decimal("739.9"),
}


def _assert_sossi_pins(result) -> None:
    assert result.validation.hard_issues == ()
    observations = [
        row
        for row in result.observations.values()
        if "table2_open_furnace_residue_ppm" in row.observation_id
    ]
    assert observations
    assert all(row.value.kind is ValueKind.POINT for row in observations)
    assert all(row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION for row in observations)
    assert all(row.identity.temperature_K.is_value for row in observations)
    assert all(row.identity.fO2_Pa is not None and row.identity.fO2_Pa.is_value for row in observations)
    assert all(row.identity.total_pressure_Pa is not None and row.identity.total_pressure_Pa.is_value for row in observations)

    manganese = [
        row
        for row in observations
        if row.identity.species.formula == "Mn"
        and "_quoted_" not in row.observation_id
    ]
    for run_id, expected in TABLE2_RUNS.items():
        matches = [
            row
            for row in manganese
            if row.experiment_id.endswith(f"::experiment::{run_id}")
        ]
        assert len(matches) == 1
        row = matches[0]
        assert row.identity.temperature_K.value == Decimal("1773.15")
        assert row.value.point == expected

    contexts = [
        context
        for records in result.context_by_work.values()
        for context in records
        if context.get("source_id") == SID
    ]
    table3 = next(
        context
        for context in contexts
        if context.get("observation_id") == "sossi_2019_table3_apparent_vaporization_constant_K_star"
    )
    assert table3["schema_gap"] == "apparent vaporization constant K*: no store quantity token"
    assert len(table3["rows"]) == 17
    na = next(row for row in table3["rows"] if row["element"] == "Na")
    assert na["values_by_temperature_K"] == [
        {
            "temperature_K": 1673.15,
            "log10_K_star": -4.25,
            "log10_K_star_uncertainty": 0.1,
            "N_points_at_T": 2,
        }
    ]
    assert {context.get("table") for context in contexts if context.get("table") in {"4", "5", "6"}} == {"4", "5", "6"}
    assert all(
        context["schema_gap"] == "t-1382"
        for context in contexts
        if context.get("table") in {"4", "5", "6"}
        or context.get("observation_id")
        == "sossi_2019_narrative_evaporation_model_inferences"
    )


def test_sossi_fitted_outputs_are_context_and_run_measurements_keep_conditions(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, EXTRACT)
    _assert_sossi_pins(result)


def _assert_fedkin_pins(result) -> None:
    assert result.validation.hard_issues == ()
    expected = {
        "fedkin_2006_na_yu_langmuir": Decimal("0.26"),
        "fedkin_2006_k_yu_langmuir": Decimal("0.13"),
    }
    points = {
        row.observation_id.rsplit("::T=", 1)[0].split("::")[-1]: row
        for row in result.observations.values()
        if row.value.kind is ValueKind.POINT
        and row.observation_id.rsplit("::T=", 1)[0].split("::")[-1] in expected
    }
    assert set(points) == set(expected)
    for observation_id, value in expected.items():
        row = points[observation_id]
        assert row.value.point == value
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1723.0")
        assert row.point_conditions["temperature_K"].state.value == Decimal("1723.0")
        assert row.point_conditions["total_pressure_Pa"].state.value.point == Decimal("1.0")

    contexts = [
        context
        for records in result.context_by_work.values()
        for context in records
        if context.get("source_id") == FEDKIN_SID
    ]
    table3 = next(context for context in contexts if context.get("table") == "3")
    assert table3["locator"]["page"] == 8
    assert {row["element"] for row in table3["rows"]} == {"Fe", "Mg", "SiO"}
    assert all(len(row["temperature_values"]) == 4 for row in table3["rows"])
    table4 = next(context for context in contexts if context.get("table") == "4")
    assert table4["locator"]["page"] == 14
    assert len(table4["rows"]) == 4


def test_fedkin_table3_fit_outputs_are_context_and_yu_runs_keep_conditions(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, FEDKIN_EXTRACT)
    _assert_fedkin_pins(result)


def _assert_hashimoto_table3_pins(result) -> None:
    assert result.validation.hard_issues == ()
    values = {}
    for row in result.observations.values():
        if (
            row.value.kind is ValueKind.POINT
            and "::hashimoto_1983_table3_residue_composition_series::" in row.observation_id
            and ":row=c3-(2):" in row.observation_id
            and row.identity.temperature_K.is_value
            and row.identity.temperature_K.value == Decimal("1973.15")
        ):
            values[row.identity.species.formula] = row
    expected = {"SiO2": Decimal("39.09"), "Al2O3": Decimal("3.57"), "FeO": Decimal("27.92")}
    assert set(values) >= set(expected)
    for formula, value in expected.items():
        row = values[formula]
        assert row.value.point == value
        assert row.identity.temperature_K.value == Decimal("1973.15")
        assert row.identity.total_pressure_Pa is not None
        assert row.identity.fO2_Pa is not None


def test_hashimoto_table3_residue_values_keep_row_temperature_and_conditions(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, HASHIMOTO_EXTRACT)
    _assert_hashimoto_table3_pins(result)


def _assert_richter07_pins(result) -> None:
    assert result.validation.hard_issues == ()
    expected = {
        "R3-1": Decimal("0.265"),
        "R3-2": Decimal("0.0"),
        "R3-4": Decimal("0.333"),
    }
    points = {
        sample: next(
            row
            for row in result.observations.values()
            if row.value.kind is ValueKind.POINT
            and row.observation_id.rsplit("::T=", 1)[0].endswith(
                f"richter_2007_mg_table1_mass_loss_fraction_{sample}"
            )
        )
        for sample in expected
    }
    for sample, value in expected.items():
        row = points[sample]
        assert row.value.point == value
        assert row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        assert row.identity.temperature_K.value == Decimal("1873.15")
        assert row.identity.total_pressure_Pa is not None
        assert row.point_conditions["temperature_K"].state.value == Decimal("1873.15")

    contexts = [
        context
        for records in result.context_by_work.values()
        for context in records
        if context.get("source_id") == RICHTER07_SID
    ]
    table1 = next(context for context in contexts if context.get("table") == "1")
    assert table1["schema_gap"] == "t-1382"
    assert any(row.get("sample") == "R3-1" for row in table1["rows"])


def test_richter07_table1_mass_loss_and_context_keep_conditions(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, RICHTER07_EXTRACT)
    _assert_richter07_pins(result)


def test_richter02_type_b_melt_phase_is_canonical(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-037-richter-2002.yaml")
    assert result.validation.hard_issues == ()
    assert not any(
        "phase string 'silicate_melt_Type_B_CAI_like'" in item.why
        for item in result.queue
    )

