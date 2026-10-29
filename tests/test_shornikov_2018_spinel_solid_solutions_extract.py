"""Migration pins for Shornikov (2018), Tables 1–3 and KEMS conditions."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Phase, ValueKind
from simulator.battery.identity import profile_for
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2018-spinel-solid-solutions.yaml"


def _row(observations, observation_id: str, table: str, composition: str):
    return next(
        observation
        for observation in observations
        if f"::{observation_id}::" in observation.observation_id
        and observation.locator.table == table
        and f"x_Al2O3={composition}" in (observation.locator.note or "")
    )


def _identity_complete(observation) -> bool:
    return all(
        (state := getattr(observation.identity, axis, None)) is not None
        and state.is_value
        for axis in profile_for(observation.identity).required
    )


def test_shornikov_2018_tables_and_conditions_migrate_with_page_coordinates() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 168
    assert not migrator.result.validation.hard_issues
    assert not any(
        observation.value.kind is ValueKind.UNAVAILABLE
        for observation in observations
    )
    assert all(
        observation.identity.quantity is not None
        and observation.identity.quantity.is_value
        and observation.identity.species is not None
        and observation.identity.species.phase.is_value
        for observation in observations
    )

    by_quantity = {
        quantity: [
            observation
            for observation in observations
            if observation.identity.quantity.value.value == quantity
        ]
        for quantity in ("activity", "p_partial")
    }
    assert len(by_quantity["activity"]) == 60
    assert len(by_quantity["p_partial"]) == 108
    assert sum(_identity_complete(row) for row in by_quantity["activity"]) == 60
    assert sum(_identity_complete(row) for row in by_quantity["p_partial"]) == 0

    # Table 1 is this paper's own KEMS result; preserve the green evidence
    # classes for its tabulated and reduced series.
    pressure_series = [
        observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
        if observation.get("locator", {}).get("table") == "1"
        and observation.get("values", {}).get("quantity") == "partial_pressure"
    ]
    assert len(pressure_series) == 8
    assert {
        row["observation_id"]: row["values"].get(
            "evidence_class", row["values"]["method_class"]
        )
        for row in pressure_series
    } == {
        "shornikov_2018_t1_p_mg": "measured_tabulated",
        "shornikov_2018_t1_p_mgo": "measured_tabulated",
        "shornikov_2018_t1_p_al": "measured_tabulated",
        "shornikov_2018_t1_p_alo": "measured_tabulated",
        "shornikov_2018_t1_p_al2o": "measured_tabulated",
        "shornikov_2018_t1_p_mgalo": "measured_tabulated",
        "shornikov_2018_t1_p_o": "measured_reduced",
        "shornikov_2018_t1_p_o2": "measured_reduced",
    }
    assert all("attribution" not in row["values"] for row in pressure_series)

    for row in observations:
        conditions = row.point_conditions
        assert conditions is not None
        for key in (
            "temperature_K",
            "composition",
            "pressure_kind",
        ):
            assert key in conditions
            assert conditions[key].locator is not None
        assert conditions["temperature_K"].state.is_value
        assert conditions["temperature_K"].state.value == Decimal("2100")
        assert conditions["composition"].state.is_value
        assert conditions["pressure_kind"].state.is_unknown
        assert conditions["pressure_kind"].locator.page == 4
        assert "total_pressure_Pa" not in conditions
        assert row.identity.total_pressure_Pa is not None
        assert row.identity.total_pressure_Pa.is_unknown

    p_o2_rows = [
        row
        for row in by_quantity["p_partial"]
        if "::shornikov_2018_t1_p_o2::" in row.observation_id
    ]
    assert len(p_o2_rows) == 12
    p_o2_ids = {row.observation_id for row in p_o2_rows}
    assert all(row.point_conditions["fO2_Pa"].state.is_unknown for row in p_o2_rows)
    assert all(
        row.identity.fO2_Pa is not None and row.identity.fO2_Pa.is_unknown
        for row in p_o2_rows
    )
    oxygen_condition_rows = [
        row for row in observations if row.observation_id not in p_o2_ids
    ]
    assert len(oxygen_condition_rows) == 156
    assert all(row.point_conditions["fO2_Pa"].state.is_value for row in oxygen_condition_rows)
    assert all(
        row.point_conditions["fO2_Pa"].locator is not None
        and row.point_conditions["fO2_Pa"].locator.table == "1"
        and row.point_conditions["fO2_Pa"].locator.page == 6
        for row in oxygen_condition_rows
    )

    experiment = next(iter(migrator.result.experiments.values()))
    assert experiment.method.value.value == "knudsen_effusion"
    assert experiment.sample.container.state.value == "molybdenum cell"
    assert experiment.sample.container.locator.page == 4
    assert experiment.sample.initial_composition.state.is_value
    assert experiment.sample.initial_composition.state.value.components == (
        ("MgO", Decimal("0.5")),
        ("Al2O3", Decimal("0.5")),
    )
    assert experiment.sample.initial_composition.locator.page == 4
    assert experiment.apparatus.geometry.orifice_diameter_m.state.is_unknown
    assert experiment.apparatus.geometry.orifice_diameter_m.locator.page == 5
    assert experiment.pressure_environment.total_pressure_Pa.state.is_unknown
    assert experiment.pressure_environment.total_pressure_Pa.locator.page == 4
    assert experiment.pressure_environment.sweep_gas.state.is_unknown
    assert experiment.pressure_environment.sweep_gas.locator.page == 4
    assert experiment.thermal_schedule.total_duration_s.state.is_unknown
    assert experiment.thermal_schedule.total_duration_s.locator.page == 4

    # Render-confirmed values from three tables and three distinct locators.
    mg_pressure = _row(observations, "shornikov_2018_t1_p_mg", "1", "50.00")
    oxygen_pressure = _row(observations, "shornikov_2018_t1_p_o2", "1", "50.00")
    atomic_oxygen = _row(observations, "shornikov_2018_t2_o_eq22_23", "2", "50.00")
    mgo_activity = _row(observations, "shornikov_2018_t3_amgo_eq27", "3", "50.00")

    # Table 1, p. 1452: 2.12e-5 atm; migrated pressure is Pa.
    assert mg_pressure.value.point == Decimal("2.1480900")
    assert mg_pressure.locator.published_page == 1452
    assert mg_pressure.locator.table == "1"
    # Table 1, p. 1452: the measured pO2 is matched onto other rows at this composition.
    assert oxygen_pressure.value.point == Decimal("0.032424")
    assert oxygen_pressure.locator.published_page == 1452
    assert oxygen_pressure.locator.table == "1"
    assert mg_pressure.identity.fO2_Pa.value == oxygen_pressure.value.point
    # Table 2, p. 1452: printed Eq. (22)/(23) pO = 7.78e-7 atm.
    assert atomic_oxygen.value.point == Decimal("0.07883085")
    assert atomic_oxygen.locator.published_page == 1452
    assert atomic_oxygen.locator.table == "2"
    # Table 3, p. 1454: a_MgO by Eq. 27 is 3.50e-1 at x_Al2O3=50.00.
    assert mgo_activity.value.point == Decimal("0.35")
    assert mgo_activity.identity.species.phase.value is Phase.CR
    assert mgo_activity.locator.published_page == 1454
    assert mgo_activity.locator.table == "3"
