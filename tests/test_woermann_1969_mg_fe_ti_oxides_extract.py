"""Independent render pins and migration checks for Woermann et al. (1969)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, PhaseRole, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/woermann-1969-mg-fe-ti-oxides.yaml"

# Independent transcription from the 600 dpi invariant-table renders, PDF pp. 5 and 7.
LIQUID_RENDER_GRID = {
    "woermann1969_mgo_tio2_invariant_1756": {
        "temperature": "1756",
        "system": "MgO-TiO2",
        "assemblage": "Periclase, Mg2TiO4, liquid (43 wt percent MgO, 57 wt percent TiO2)",
        "components": {"MgO": "43", "TiO2": "57"},
    },
    "woermann1969_mgo_tio2_invariant_1630": {
        "temperature": "1630",
        "system": "MgO-TiO2",
        "assemblage": "Mg2TiO4, MgTiO3, liquid (32 wt percent MgO, 68 wt percent TiO2)",
        "components": {"MgO": "32", "TiO2": "68"},
    },
    "woermann1969_mgo_tio2_invariant_1620": {
        "temperature": "1620",
        "system": "MgO-TiO2",
        "assemblage": "MgTiO3, MgTi2O5, liquid (28 wt percent MgO, 72 wt percent TiO2)",
        "components": {"MgO": "28", "TiO2": "72"},
    },
    "woermann1969_mgo_tio2_invariant_1632": {
        "temperature": "1632",
        "system": "MgO-TiO2",
        "assemblage": "MgTi2O5, TiO2, liquid (14 wt percent MgO, 86 wt percent TiO2)",
        "components": {"MgO": "14", "TiO2": "86"},
    },
    "woermann1969_ternary_invariant_1596": {
        "temperature": "1596",
        "system": "MgO-iron oxide-TiO2",
        "assemblage": "α (geikielite solid solution), pseudobrookite solid solution, spinel solid solution, liquid (26 wt percent MgO, 65 wt percent TiO2, 9 wt percent Fe3O2)",
        "components": {"MgO": "26", "TiO2": "65", "Fe2O3": "9"},
    },
    "woermann1969_ternary_invariant_1530": {
        "temperature": "1530",
        "system": "MgO-iron oxide-TiO2",
        "assemblage": "α′ (hematite solid solution), pseudobrookite solid solution, spinel solid solution, liquid (7 wt percent MgO, 28 wt percent TiO2, 65 wt percent Fe2O3)",
        "components": {"MgO": "7", "TiO2": "28", "Fe2O3": "65"},
    },
}


def _observations(extract: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for payload in extract["species"].values()
        for row in payload.get("observations", [])
    }


def _liquid_grid(extract: dict) -> dict[str, dict]:
    grid: dict[str, dict] = {}
    for row in _observations(extract).values():
        values = row["values"]
        if values.get("quantity") != "equilibrium_phase_composition":
            continue
        assemblage_id = values["assemblage_id"]
        record = grid.setdefault(
            assemblage_id,
            {
                "temperature": str(values["T_C"]),
                "system": values["system_as_printed"],
                "assemblage": values["phase_assemblage_as_printed"],
                "components": {},
            },
        )
        record["components"][values["formula"]] = str(values["component_amount"])
    return grid


def _table_context(extract: dict, observation_id: str) -> dict:
    return next(
        context
        for payload in extract["species"].values()
        for context in payload.get("context", [])
        if context["observation_id"] == observation_id
    )


def test_rendered_liquid_invariants_match_the_independent_grid() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert _liquid_grid(extract) == LIQUID_RENDER_GRID

    records = _observations(extract)
    assert len(records) == 17
    assert sum(row["values"].get("quantity") == "equilibrium_phase_composition" for row in records.values()) == 14
    assert sum(row["values"].get("quantity") == "transition_temperature" for row in records.values()) == 3


def test_invariant_grid_pins_reject_render_and_extract_mutations() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))

    changed_render_grid = deepcopy(LIQUID_RENDER_GRID)
    changed_render_grid["woermann1969_mgo_tio2_invariant_1756"]["components"]["MgO"] = "43.1"
    with pytest.raises(AssertionError):
        assert _liquid_grid(extract) == changed_render_grid

    changed_extract = deepcopy(extract)
    row = _observations(changed_extract)["woermann1969_mgo_tio2_invariant_1756_mgo"]
    row["values"]["component_amount"] = "43.1"
    with pytest.raises(AssertionError):
        assert _liquid_grid(changed_extract) == LIQUID_RENDER_GRID


def test_rendered_run_tables_preserve_all_rows_and_sample_binding() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table1 = _table_context(extract, "woermann1969_table1_binary_mgo_tio2_runs")["values"]
    table2 = _table_context(extract, "woermann1969_table2_ternary_mgo_iron_tio2_runs")["values"]
    table3 = _table_context(extract, "woermann1969_table3_subsolidus_runs")["values"]

    assert (table1["sample_count"], table1["run_count"]) == (36, 132)
    assert (table2["sample_count"], table2["run_count"]) == (146, 332)
    assert table3["run_count"] == 120

    first_table1 = next(sample for sample in table1["samples"] if sample["sample_id"] == "left-row-003")
    assert first_table1["composition_wt_percent_as_printed"] == ["98.0", "2.0"]
    assert first_table1["quench_runs"][0]["temperature_C_as_printed"] == "1760"
    assert first_table1["quench_runs"][0]["phase_assemblage_as_printed"] == "MgO + tr L"
    page7_table1 = next(sample for sample in table1["samples"] if sample["sample_id"] == "left-row-068")
    assert page7_table1["locator"]["page"] == 7
    assert page7_table1["composition_wt_percent_as_printed"] == ["14.0", "86.0"]

    page12_table2 = next(sample for sample in table2["samples"] if sample["sample_id"] == "left-row-102")
    assert page12_table2["locator"]["page"] == 12
    assert page12_table2["composition_wt_percent_as_printed"] == ["25.1", "65.1", "9.8"]
    first_table3 = table3["runs"][0]
    assert first_table3["temperature_C_as_printed"] == "1700"
    assert first_table3["composition_wt_percent_as_printed"] == ["31.0", "9.0", "60.0"]
    assert first_table3["run_duration_hours_as_printed"] == "8"
    assert first_table3["phase_assemblage_as_printed"] == "W + tr S"
    assert first_table3["locator"]["page"] == 14
    page15_table3 = next(run for run in table3["runs"] if run["table_row_as_printed"] == 97)
    assert page15_table3["locator"]["page"] == 15
    assert page15_table3["temperature_C_as_printed"] == "1000"

    locators = [sample["locator"] for sample in table1["samples"] + table2["samples"]]
    locators.extend(
        run["locator"]
        for table in (table1, table2)
        for sample in table["samples"]
        for run in sample["quench_runs"]
    )
    locators.extend(run["locator"] for run in table3["runs"])
    assert all(locator["page"] == locator["pdf_page_index"] + 1 for locator in locators)


def test_typed_rows_migrate_with_species_phase_temperature_and_printed_oxygen() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    declared = _observations(extract)
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.validation.hard_issues

    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
    }
    assert len(migrated) == 17

    for observation_id, row in migrated.items():
        values = declared[observation_id]["values"]
        locator = declared[observation_id]["locator"]
        assert locator["page"] == locator["pdf_page_index"] + 1
        assert row.value.kind is ValueKind.POINT
        assert row.identity.quantity.value is Quantity(values["quantity"])
        assert row.point_conditions["temperature_K"].state.value == Decimal(values["T_C"]) + Decimal("273.15")
        assert all(notice.kind.value != "source_value_outside_physical_range" for notice in row.notices)

        if values["quantity"] == "equilibrium_phase_composition":
            assert row.point_conditions["pO2_Pa"].state.value == Decimal("20265.0")
            assert row.identity.fO2_Pa.value == Decimal("20265.0")
            assert row.point_conditions["gas_mix"].state.value == "air"
            assert row.identity.species.formula == values["formula"]
            assert row.identity.species.phase.value is Phase.L
            assert row.identity.phase_role.value is PhaseRole.MELT
            assert row.identity.temperature_K.value == Decimal(values["T_C"]) + Decimal("273.15")
            assert row.value.point == Decimal(values["component_amount"])
            if "total_pressure_Pa" in values["point_conditions"]:
                assert row.point_conditions["total_pressure_Pa"].state.value == Decimal("101325")
            else:
                assert "total_pressure_Pa" not in row.point_conditions
        else:
            assert row.identity.species.formula == values["formula"]
            assert row.value.point == Decimal(values["T_C"]) + Decimal("273.15")

    iron_oxide_row = declared["woermann1969_ternary_invariant_1596_fe2o3"]
    assert iron_oxide_row["values"]["formula"] == "Fe2O3"
    assert "line reads Fe3O2" in iron_oxide_row["locator"]["note"]
    assert "footnote defines total iron oxide as Fe2O3" in iron_oxide_row["locator"]["note"]

    melt_experiment = next(
        experiment
        for experiment in migrator.result.experiments.values()
        if experiment.experiment_id.endswith("::woermann1969_mgo_tio2_melting_relations")
    )
    assert melt_experiment.fO2_control is not None
    oxygen = melt_experiment.fO2_control.oxygen_partial_pressure_Pa
    assert oxygen is not None and oxygen.state.value.point == Decimal("20265.0")
    assert melt_experiment.fO2_control.channel.value.value == "gas_mix"
    transition_ids = [
        observation_id
        for observation_id, values in declared.items()
        if values["values"]["quantity"] == "transition_temperature"
    ]
    assert len(transition_ids) == 3
    assert all(
        migrated[observation_id].experiment_id == melt_experiment.experiment_id
        for observation_id in transition_ids
    )

    figure_records = [
        context
        for payload in extract["species"].values()
        for context in payload.get("context", [])
        if context.get("type") == "figure_context"
    ]
    assert len(figure_records) == 9
    for record in figure_records:
        values = record["values"]
        assert values["quantity"] == "equilibrium_phase_composition"
        assert values["species_system"]
        assert values["phase_as_printed"]
        assert values["T_C_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption"]
        assert values["panels"]
        assert values["series"]
        assert all("marker_count" in series for series in values["series"])
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["attribution"]
        assert record["locator"]["page"] == record["locator"]["pdf_page_index"] + 1
