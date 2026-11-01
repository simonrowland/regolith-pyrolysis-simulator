from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kunisada-iwai-1993-na-silicate-sulfide-capacity.yaml"
TABLE_ID = "kunisada_iwai_1993_table1_slag_compositions"


def _table_record(document: dict) -> dict:
    return next(
        row
        for row in document["species"]["S"]["context"]
        if row["observation_id"] == TABLE_ID
    )


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
    }


def test_table1_cells_and_observation_values_are_pinned_to_page_44_render() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table = _table_record(document)
    rows = table["values"]["rows_as_printed"]

    assert len(rows) == 57
    assert [int(row[0]) for row in rows] == (
        list(range(1, 15))
        + list(range(101, 112))
        + list(range(201, 212))
        + list(range(301, 312))
        + list(range(401, 411))
    )
    assert table["locator"]["page"] == 2
    assert table["locator"]["published_page"] == 44
    assert table["locator"]["table"] == "1"

    # These cells were checked against the 600-dpi render of printed page 44.
    by_row = {row[0]: row for row in rows}
    assert by_row["1"][1] == "29.1"
    assert by_row["4"][7] == "0.102"
    assert by_row["208"][1] == "31.1"
    assert by_row["408"][7] == "0.120"
    assert by_row["1"][3:7] == ["", "", "", ""]

    observations = _observations(document)
    assert len(observations) == 214
    assert {
        formula: len(species["observations"])
        for formula, species in document["species"].items()
    } == {"S": 57, "Na2O": 57, "SiO2": 57, "CaO": 11, "MnO": 11, "MgO": 11, "Al2O3": 10}
    column_index = {"Na2O": 1, "SiO2": 2, "CaO": 3, "MnO": 4, "MgO": 5, "Al2O3": 6, "S": 7}
    for formula, species in document["species"].items():
        for observation in species["observations"]:
            locator = observation["locator"]
            assert locator["row"] in by_row
            assert locator["column"] == formula
            assert locator["printed_cell"] == by_row[locator["row"]][column_index[formula]]
            assert observation["values"]["component_amount"] == locator["printed_cell"]
            assert observation["values"]["units"] == "wt%"

    rendered_samples = {
        "kunisada_iwai_1993_table1_sample_1_s_composition": ("0.003", "S"),
        "kunisada_iwai_1993_table1_sample_101_cao_composition": ("4.1", "CaO"),
        "kunisada_iwai_1993_table1_sample_408_al2o3_composition": ("20.0", "Al2O3"),
    }
    for observation_id, (amount, formula) in rendered_samples.items():
        observation = observations[observation_id]
        assert observation["values"]["formula"] == formula
        assert observation["values"]["component_amount"] == amount
        assert observation["locator"]["page"] == 2
        assert observation["locator"]["published_page"] == 44
        assert observation["locator"]["table"] == "1"


def test_unprinted_pressure_stays_unknown_and_figures_are_not_digitized() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = document["species"]["S"]["context"]
    experiment = next(row for row in context if row["type"] == "method")
    pressure = experiment["values"]["total_pressure_Pa"]
    assert pressure["state"]["tag"] == "unknown"
    assert "does not report total experimental pressure" in pressure["state"]["reason"]

    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 6
    assert all(row["values"]["points"] == [] for row in figures)
    assert not any(
        row["values"]["quantity"] == "sulfide_capacity"
        for row in _observations(document).values()
    )


def test_isolated_migration_preserves_observations_conditions_and_locators() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    expected_ids = _observations(document)
    assert len(expected_ids) == 214

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    rows = {
        row.observation_id.split("::", 1)[-1]: row
        for row in migrator.result.observations.values()
    }

    assert len(rows) == 214
    assert not migrator.result.validation.hard_issues
    assert all(row.value.kind.value != "unavailable" for row in rows.values())
    assert all(
        row.identity.quantity.value.value == "equilibrium_phase_composition"
        for row in rows.values()
    )
    assert all(
        row.identity.species.phase.value.value == "l" for row in rows.values()
    )
    assert all(
        row.evidence.class_.value.value == "measured_direct" for row in rows.values()
    )

    for row in rows.values():
        temperature = row.point_conditions["temperature_K"]
        gas = row.point_conditions["gas_mix"]
        p_o2 = row.point_conditions["pO2_Pa"]
        p_s2 = row.point_conditions["pS2_Pa"]
        total_pressure = row.point_conditions["total_pressure_Pa"]
        composition = row.point_conditions["composition"]
        assert temperature.state.value == Decimal("1773.15")
        assert temperature.locator.page == 1
        assert gas.state.value == "59.5 vol% CO; 39.7 vol% CO2; 0.8 vol% SO2"
        assert gas.locator.page == 1
        assert p_o2.state.value == Decimal("0.00134")
        assert p_o2.locator.page == 2
        assert p_s2.state.value == Decimal("181")
        assert p_s2.locator.page == 2
        assert total_pressure.state.is_unknown
        assert composition.state.is_value
        assert composition.locator.page == 2

    rendered_values = {
        "kunisada_iwai_1993_table1_sample_1_s_composition": "0.003",
        "kunisada_iwai_1993_table1_sample_101_cao_composition": "4.1",
        "kunisada_iwai_1993_table1_sample_408_al2o3_composition": "20.0",
    }
    for observation_id, amount in rendered_values.items():
        row = rows[observation_id]
        assert row.value.point == Decimal(amount)
        assert row.identity.subtype.is_value
        assert row.identity.subtype.value == "wt_percent"
        assert row.identity.composition.is_value
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa.is_value
        assert row.identity.temperature_K.value == Decimal("1773.15")

    contexts = {
        row["observation_id"]: row
        for context_rows in migrator.result.context_by_work.values()
        for row in context_rows
    }
    assert contexts[TABLE_ID]["locator"]["page"] == 2
    assert contexts[TABLE_ID]["locator"]["table"] == "1"
    assert contexts["kunisada_iwai_1993_experimental_conditions"]["locator"][
        "page"
    ] == 1
    assert contexts["kunisada_iwai_1993_capacity_definition_and_gas_potentials"][
        "locator"
    ]["page"] == 2
    assert contexts["kunisada_iwai_1993_capacity_correlations_abstract"][
        "locator"
    ]["page"] == 1
    assert contexts["kunisada_iwai_1993_capacity_correlation_binary"][
        "locator"
    ]["page"] == 3
    assert contexts["kunisada_iwai_1993_capacity_correlation_modified_basicity"][
        "locator"
    ]["page"] == 4
    assert contexts["kunisada_iwai_1993_capacity_correlation_optical_basicity"][
        "locator"
    ]["page"] == 5
    assert contexts["kunisada_iwai_1993_capacity_correlation_binary"]["values"][
        "equation_as_printed"
    ] == "log C_S = 4.64 B - 6.88 at 1500 °C"
    assert contexts["kunisada_iwai_1993_capacity_correlation_optical_basicity"][
        "values"
    ]["equation_as_printed"] == "log C_S = 27.0 Λ_th - 21.2 at 1500 °C"
    assert contexts["kunisada_iwai_1993_capacity_correlations_abstract"]["values"][
        "method_class"
    ] == "author_derived"
