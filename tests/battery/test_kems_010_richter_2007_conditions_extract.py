from collections import Counter
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import Phase, QuenchState, Quantity, ValueKind
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator, load_yaml, wt_pct_to_mole_fraction
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[2]
EXTRACT = ROOT / "data/literature/extracts/kems-010-richter-2007.yaml"
SOURCE_ID = "kems-010-richter-2007"

SCHEMA_GAP_QUANTITIES = {
    "vacuum_evaporation_flux_J_Mg_vs_T",
    "published_mass_loss_and_reduced_flux_time_series",
    "published_reduced_silicon_flux_time_series",
    "qualitative_non_loss_bound",
}

CONTINUATION_EXPERIMENT_IDS = {
    "cai-r2-8",
    "cai-r2-9",
    "cai-r2-13",
    "cai-r2-14",
    "cai-r2-15",
    "cai-r3-1",
    "cai-r3-2",
    "cai-r3-4",
    "cai-r3-5",
    "cai-r3-7",
    "cai-r3-8",
    "cai-r3-18",
    "cai-r3-19",
    "cai-r3-20",
    "cai-r3-21",
}


def _assert_continuation_end_temperature_locator(experiment):
    end_temperature = experiment["thermal_schedule"]["ramps"][1][
        "end_temperature_K"
    ]
    locator = end_temperature["locator"]
    assert locator["page"] == 7
    assert locator["published_page"] == 5550
    assert locator["table"] == "1"
    assert locator["row"] == experiment["locator"]["row"]
    temperature = experiment["conditions"]["temperature_K"]["state"]["value"]
    assert end_temperature["state"]["value"]["point"] == temperature


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


def _assert_richter_mass_loss_pin(row, *, temperature, duration_min, mass_loss):
    assert row.identity.temperature_K.is_value
    assert row.identity.temperature_K.value == Decimal(temperature)
    time = row.point_conditions["time_min"]
    assert time.state.is_value and time.state.value == Decimal(duration_min)
    assert time.locator is not None
    assert time.locator.table == "1"
    assert time.locator.paragraph == row.locator.paragraph
    assert row.value.kind is ValueKind.POINT
    assert row.value.point == Decimal(mass_loss)


def test_richter_2007_conditions_row_counts_and_render_pins():
    assert validate_extract_file(EXTRACT) == []
    result, rows = _migrate_extract()
    document = load_yaml(EXTRACT)
    source_rows = list(_source_observations(document))

    assert len(rows) == 342
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
    area_context = {
        row["observation_id"]: row
        for context_rows in result.context_by_work.values()
        for row in context_rows
        if row["observation_id"].startswith(
            "richter_2007_mg_table1_numeric_areas_b1_"
        )
    }
    assert len(area_context) == 8
    assert all(row["type"] == "sample_geometry" for row in area_context.values())
    assert all(
        row["locator"]["page"] == 6
        and row["locator"]["published_page"] == 5549
        and row["locator"]["pdf_page_index"] == 5
        for row in area_context.values()
    )
    migrated_ids = {
        row.observation_id.rsplit("::", 1)[-1] for row in rows.values()
    }
    assert not area_context.keys() & migrated_ids
    expected_area_samples = {
        "R3-12": (2.5, 1900, 22.4, 21.3),
        "R3-10": (2.5, 1900, 24.0, 18.9),
        "R3-11": (2.5, 1900, 20.9, 15.9),
        "R-8": (6.0, 1800, 87.5, 83.4),
        "R-9": (6.0, 1800, 82.6, 76.6),
        "R-11": (6.0, 1800, 91.3, 80.0),
        "R2-4": (6.0, 1800, 88.5, 75.5),
        "R-16": (1.0, 1800, 5.9, 5.8),
    }
    assert len(expected_area_samples) == len(area_context) == 8
    assert {
        row["values"]["samples"][0]["sample"] for row in area_context.values()
    } == set(expected_area_samples)
    for row in area_context.values():
        sample_name = row["values"]["samples"][0]["sample"]
        diam, temp, initial, final = expected_area_samples[sample_name]
        assert row["values"]["samples"] == [
            {
                "sample": sample_name,
                "diam_mm": diam,
                "T_C": temp,
                "init_area_mm2": initial,
                "final_area_mm2": final,
            }
        ]
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
    assert temperature_kinds == {"value": 337, "unknown": 5}

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
    continuation_experiments = [
        experiment
        for experiment in run_experiments
        if experiment["experiment_id"] in CONTINUATION_EXPERIMENT_IDS
    ]
    assert {
        experiment["experiment_id"] for experiment in continuation_experiments
    } == CONTINUATION_EXPERIMENT_IDS
    for experiment in continuation_experiments:
        _assert_continuation_end_temperature_locator(experiment)
    mutated_continuation = deepcopy(
        next(
            experiment
            for experiment in continuation_experiments
            if experiment["experiment_id"] == "cai-r3-1"
        )
    )
    mutated_locator = mutated_continuation["thermal_schedule"]["ramps"][1][
        "end_temperature_K"
    ]["locator"]
    mutated_locator["page"] = 6
    mutated_locator["published_page"] = 5549
    with pytest.raises(AssertionError):
        _assert_continuation_end_temperature_locator(mutated_continuation)

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
        sweep_gas = experiment["pressure_environment"]["sweep_gas"]
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
    assert all(
        row.point_conditions["time_min"].state.is_value
        and row.point_conditions["time_min"].locator is not None
        for row in residue_rows
    )

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
        ("R3-10", Decimal("0.32"), Decimal("2173.15"), 15, 900, 5549),
        ("R-14", Decimal("0.317"), Decimal("2073.15"), 30, 1800, 5549),
        ("R2-13", Decimal("0.367"), Decimal("1873.15"), 900, 54000, 5550),
    )
    pinned_rows = {}
    for row_label, mass_loss, temperature, duration_min, duration_s, page in pins:
        matches = [
            row
            for row in rows.values()
            if f"richter_2007_mg_table1_mass_loss_fraction_{row_label}::" in row.observation_id
        ]
        assert len(matches) == 1
        row = matches[0]
        pinned_rows[row_label] = row
        _assert_richter_mass_loss_pin(
            row,
            temperature=temperature,
            duration_min=duration_min,
            mass_loss=mass_loss,
        )
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
        duration = experiment.thermal_schedule.total_duration_s
        assert duration.state.is_value
        assert duration.state.value.point == Decimal(duration_s)

    mass_loss_rows = [
        row
        for row in rows.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
    ]
    assert len(mass_loss_rows) == 50
    for row in mass_loss_rows:
        time = row.point_conditions["time_min"]
        assert time.state.is_value
        assert time.locator is not None
        assert time.locator.page == time.locator.published_page - 5543
        assert time.locator.table == "1"
        assert time.locator.paragraph == row.locator.paragraph

    row = pinned_rows["R-14"]
    expected = {
        "temperature": Decimal("2073.15"),
        "duration_min": Decimal("30"),
        "mass_loss": Decimal("0.317"),
    }
    wrong_temperature = replace(
        row,
        identity=replace(
            row.identity,
            temperature_K=replace(
                row.identity.temperature_K, value=Decimal("2074.15")
            ),
        ),
    )
    with pytest.raises(AssertionError):
        _assert_richter_mass_loss_pin(wrong_temperature, **expected)

    wrong_time = replace(
        row.point_conditions["time_min"],
        state=replace(row.point_conditions["time_min"].state, value=Decimal("31")),
    )
    wrong_conditions = dict(row.point_conditions)
    wrong_conditions["time_min"] = wrong_time
    with pytest.raises(AssertionError):
        _assert_richter_mass_loss_pin(
            replace(row, point_conditions=wrong_conditions), **expected
        )

    wrong_mass_loss = replace(
        row, value=replace(row.value, point=Decimal("0.318"))
    )
    with pytest.raises(AssertionError):
        _assert_richter_mass_loss_pin(wrong_mass_loss, **expected)

