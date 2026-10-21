from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity, ValueKind
from tests.battery.test_migrate import _migrate_real_extract


SID = "kems-012-sossi-2019"
EXTRACT = f"{SID}.yaml"
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

