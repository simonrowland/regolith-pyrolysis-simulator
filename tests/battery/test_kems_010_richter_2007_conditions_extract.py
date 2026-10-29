from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import Phase, QuenchState, Quantity, ValueKind
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator, load_yaml, wt_pct_to_mole_fraction


ROOT = Path(__file__).resolve().parents[2]
EXTRACT = ROOT / "data/literature/extracts/kems-010-richter-2007.yaml"
SOURCE_ID = "kems-010-richter-2007"

SCHEMA_GAP_QUANTITIES = {
    "vacuum_evaporation_flux_J_Mg_vs_T",
    "sample_surface_area_series",
    "published_mass_loss_and_reduced_flux_time_series",
    "published_reduced_silicon_flux_time_series",
    "qualitative_non_loss_bound",
}


def _migrate_extract():
    migrator = Migrator(ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    rows = {
        row.observation_id: row
        for row in migrator.result.observations.values()
        if row.source_id == SOURCE_ID
    }
    return migrator.result, rows


def _source_observations(document):
    for species in document["species"].values():
        yield from species.get("observations", ())


def _complete_identity(row):
    if not row.identity.quantity.is_value:
        return False
    return all(
        (state := getattr(row.identity, axis, None)) is not None and state.is_value
        for axis in profile_for(row.identity).required
    )


def _assert_printed_pressure_condition(conditions):
    assert "total_pressure_Pa" in conditions
    pressure = conditions["total_pressure_Pa"]
    assert pressure.state.is_value and pressure.state.value.kind is ValueKind.BOUND
    assert pressure.locator.page == 5
    assert pressure.locator.published_page == 5548


def test_richter_2007_conditions_row_counts_and_render_pins():
    result, rows = _migrate_extract()
    document = load_yaml(EXTRACT)
    source_rows = list(_source_observations(document))

    assert len(rows) == 350
    composition_links = [
        source_row["values"]["composition_role"]["locator"]
        for source_row in source_rows
        if "composition_role" in source_row.get("values", {})
    ]
    assert len(composition_links) == 50
    assert all(
        locator["page"] == 6
        and locator["published_page"] == 5549
        and locator["table"] == "1"
        and locator["row"] == "Starting material"
        for locator in composition_links
    )
    assert not result.validation.hard_issues
    assert sum(_complete_identity(row) for row in rows.values()) == 0
    assert not Counter(
        row.identity.quantity.value.value
        for row in rows.values()
        if _complete_identity(row)
    )
    assert not any(
        row.identity.species is not None and row.identity.species.phase.is_unknown
        for row in rows.values()
    )

    # The exact unknown set is limited to the paper's quantities that have no
    # exact token; a near-match would misstate the data.
    schema_gap_source_ids = {
        row["observation_id"]
        for row in source_rows
        if row.get("values", {}).get("quantity") in SCHEMA_GAP_QUANTITIES
    }
    quantity_unknown_source_ids = {
        row.observation_id.split("::", 1)[1].split("::", 1)[0]
        for row in rows.values()
        if not row.identity.quantity.is_value
    }
    assert quantity_unknown_source_ids == schema_gap_source_ids
    assert Counter(
        row["values"]["quantity"]
        for row in source_rows
        if row.get("values", {}).get("quantity") in SCHEMA_GAP_QUANTITIES
    ) == {
        "vacuum_evaporation_flux_J_Mg_vs_T": 1,
        "sample_surface_area_series": 8,
        "published_mass_loss_and_reduced_flux_time_series": 51,
        "published_reduced_silicon_flux_time_series": 32,
        "qualitative_non_loss_bound": 4,
    }

    # Each migrated point carries the printed vacuum/pressure condition and an
    # explicit unknown oxygen condition. Temperature is exact for table rows;
    # only aggregate rows retain a typed unknown.
    temperature_kinds = Counter()
    for row in rows.values():
        conditions = row.point_conditions or {}
        experiment = result.experiments[row.experiment_id]
        atmosphere = conditions["atmosphere"]
        oxygen = conditions["fO2_Pa"]
        temperature = conditions["temperature_K"]
        initial_composition = experiment.sample.initial_composition
        assert initial_composition is not None and initial_composition.state.is_value
        assert initial_composition.locator.published_page == 5549
        assert initial_composition.locator.table == "1"
        duration = experiment.thermal_schedule.total_duration_s
        assert duration is not None and duration.locator is not None
        assert duration.state.is_value or duration.state.is_unknown
        assert atmosphere.state.is_value and atmosphere.state.value == "vacuum"
        assert atmosphere.locator.page == 5
        assert atmosphere.locator.published_page == 5548
        _assert_printed_pressure_condition(conditions)
        assert oxygen.state.is_unknown and oxygen.state.reason == "not_published"
        assert oxygen.locator.page == 5
        assert oxygen.locator.published_page == 5548
        assert temperature.locator is not None
        assert temperature.locator.page == temperature.locator.published_page - 5543
        temperature_kinds["value" if temperature.state.is_value else "unknown"] += 1
    assert temperature_kinds == {"value": 345, "unknown": 5}

    mutated_conditions = dict(next(iter(rows.values())).point_conditions)
    mutated_conditions.pop("total_pressure_Pa")
    with pytest.raises(AssertionError):
        _assert_printed_pressure_condition(mutated_conditions)

    run_experiments = [
        experiment
        for experiment in document["experiments"]
        if experiment["experiment_id"].startswith("cai-")
    ]
    assert len(run_experiments) == 51
    assert Counter(experiment["locator"]["published_page"] for experiment in run_experiments) == {
        5549: 36,
        5550: 15,
    }
    for experiment in run_experiments:
        page = experiment["locator"]["published_page"]
        physical_page = page - 5543
        row_label = experiment["experiment_id"].removeprefix("cai-")
        assert experiment["locator"]["page"] == physical_page
        assert experiment["locator"]["table"] == "1"
        assert experiment["locator"]["row"].casefold() == row_label.casefold()
        assert experiment["bench_id"] == "hashimoto-vacuum-furnace"
        assert experiment["conditions"]["temperature_K"]["state"]["tag"] == "value"
        assert experiment["conditions"]["temperature_K"]["locator"]["page"] == physical_page
        assert experiment["conditions"]["temperature_K"]["locator"]["published_page"] == page
        duration = experiment["thermal_schedule"]["total_duration_s"]
        assert duration["state"]["tag"] == "value"
        assert duration["state"]["value"]["kind"] == "point"
        assert duration["locator"]["page"] == physical_page
        assert duration["locator"]["published_page"] == page
        pressure = experiment["pressure_environment"]["total_pressure_Pa"]
        assert pressure["state"]["value"]["kind"] == "bound"
        assert pressure["state"]["value"]["bound_operator"] == "<"
        assert pressure["locator"]["page"] == 5
        assert pressure["locator"]["published_page"] == 5548
        assert experiment["conditions"]["fO2_Pa"]["state"]["tag"] == "unknown"
        assert experiment["conditions"]["fO2_Pa"]["locator"]["page"] == 5
        assert experiment["conditions"]["fO2_Pa"]["locator"]["published_page"] == 5548
        sweep_gas = experiment["conditions"]["sweep_gas"]
        assert sweep_gas["state"] == {"tag": "unknown", "reason": "not_published"}
        assert sweep_gas["locator"]["page"] == 5
        assert sweep_gas["locator"]["published_page"] == 5548
        for field in ("printed_composition", "initial_composition"):
            locator = experiment["sample"][field]["locator"]
            assert locator["published_page"] == 5549
            assert locator["page"] == 6
            assert locator["table"] == "1"
            assert locator["row"] == "Starting material"

    # Table 1 has 51 experimental runs; its residue compositions were measured
    # after the paper's glass quench. The source does not claim preservation.
    residue_source_rows = [
        row
        for row in source_rows
        if row["observation_id"].startswith("richter_2007_residue_table1_")
    ]
    assert len(residue_source_rows) == 51
    for source_row in residue_source_rows:
        assert source_row["phase"] == "glass"
        row_locator = source_row["locator"]
        assert row_locator["page"] == row_locator["published_page"] - 5543
        assert row_locator["table"] == "1"
        row_label = source_row["observation_id"].rsplit("_", 1)[-1]
        assert row_locator["row"].casefold() == row_label.casefold()
        quench = source_row["point_conditions"]["quench_state"]
        assert quench["state"] == {"tag": "value", "value": "not_stated"}
        assert quench["locator"]["page"] == 5
        assert quench["locator"]["published_page"] == 5548
        assert "quenched to glass" in quench["locator"]["note"]
    residue_rows = [
        row
        for row in rows.values()
        if "richter_2007_residue_table1_" in row.observation_id
    ]
    assert len(residue_rows) == 204
    assert Counter(row.identity.species.formula for row in residue_rows) == {
        "MgO": 51,
        "SiO2": 51,
        "Al2O3": 51,
        "CaO": 51,
    }
    assert all(row.identity.species.phase.value is Phase.GLASS for row in residue_rows)
    assert all(row.quench_state is QuenchState.NOT_STATED for row in residue_rows)
    assert all("quench_state" in row.point_conditions for row in residue_rows)

    # Render-checked Table 1 values remain intact, while row temperature and
    # source composition reach the migrated identities through existing paths.
    expected_composition = wt_pct_to_mole_fraction(
        {
            "MgO": Decimal("11.48"),
            "SiO2": Decimal("46.00"),
            "Al2O3": Decimal("19.39"),
            "CaO": Decimal("23.12"),
        }
    )
    pins = (
        ("R3-10", Decimal("0.32"), Decimal("2173.15"), 900, 5549),
        ("R-14", Decimal("0.317"), Decimal("2073.15"), 1800, 5549),
        ("R2-13", Decimal("0.367"), Decimal("1873.15"), 54000, 5550),
    )
    for row_label, mass_loss, temperature, duration_s, page in pins:
        matches = [
            row
            for row in rows.values()
            if f"richter_2007_mg_table1_mass_loss_fraction_{row_label}::" in row.observation_id
        ]
        assert len(matches) == 1
        row = matches[0]
        assert row.value.kind is ValueKind.POINT and row.value.point == mass_loss
        assert row.locator.page == page - 5543
        assert row.locator.published_page == page
        assert row.locator.table == "1"
        assert row.point_conditions["temperature_K"].state.value == temperature
        assert row.point_conditions["temperature_K"].locator.page == page - 5543
        assert row.point_conditions["temperature_K"].locator.published_page == page
        assert row.identity.composition.is_value
        assert row.identity.composition.value == expected_composition
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == temperature
        # The paper reports only the furnace's '<10^-6 Torr' bound outside its
        # heaters/shields; the sample-surface total pressure is not exact.
        assert row.point_conditions["total_pressure_Pa"].state.value.kind is ValueKind.BOUND
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa.is_unknown
        experiment = result.experiments[row.experiment_id]
        assert experiment.thermal_schedule.total_duration_s.is_value
        assert experiment.thermal_schedule.total_duration_s.value.point == Decimal(duration_s)

