"""Rendered source pins and isolated migration for synthetic acmite."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.battery.test_migrate import _migrate_real_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/bennington-brown-1982-synthetic-acmite-ri8621.yaml"
SID = "bennington-brown-1982-synthetic-acmite-ri8621"
ACMITE = "NaFeSi2O6"

QUANTITY_UNKNOWN_SOURCE_IDS = {
    *(f"bennington_1982_table1_reaction_{n}_heat_of_solution" for n in range(1, 7)),
    "bennington_1982_table1_reaction_7_overall_calorimetric_reaction",
    "bennington_1982_prose_overall_calorimetric_reaction_heat",
    "bennington_1982_table2_reaction_1_previous_values",
    "bennington_1982_table2_reaction_1_confirmatory_values",
    *(f"bennington_1982_table2_reaction_{n}_individual_values" for n in range(2, 7)),
    *(f"bennington_1982_table2_reaction_{n}_mean" for n in range(1, 7)),
    "bennington_1982_table3_reaction_15_heat_of_formation_from_oxides",
    "bennington_1982_table5_negative_gibbs_function",
    "bennington_1982_table6_negative_gibbs_function",
    "bennington_1982_table6_delta_fH",
    "bennington_1982_table6_delta_fG",
    "bennington_1982_table6_log10_Kf",
}

TABLE5_AUXILIARY_REFERENCES = (
    "Hultgren [11] (Table 4: Fe(c,l), all data; Si(c,l), all data).",
    "JANAF NSRDS-NBS 37 [7] (Table 4: Na(c,l,g), all data; Si(c,l), low-temperature data).",
    "JANAF [8] (Table 4: O2(g), all data).",
)
TABLE6_AUXILIARY_REFERENCES = (
    "NBS 270-4 [21] (Table 4: Fe2O3, ΔHf298).",
    "Westrum and Grovold [23] (Table 4: Fe2O3, low-temperature data).",
    "Coughlin, King, and Bonnickson [6] (Table 4: Fe2O3, high-temperature data).",
    "O'Hare and Wagman [17,22] (Table 4: Na2O, ΔHf298).",
    "Grimley and Margrave [10] (Table 4: Na2O, S°).",
    "JANAF NSRDS-NBS 37 [7] (Table 4: Na2O, Cp°).",
    "Fredrickson and Chasanov [9] (Table 4: Na2O, high-temperature data).",
    "Wise [26] (Table 4: SiO2, ΔHf298).",
    "NBS-270-3 [20] (Table 4: SiO2, S°).",
    "Anderson [1] (Table 4: SiO2, low-temperature data).",
    "Moser [16] (Table 4: SiO2, high-temperature data).",
    "Roth and Bertram [18] (Table 4: SiO2, high-temperature data).",
    "White [24] (Table 4: SiO2, high-temperature data).",
    "Wietzel [25] (Table 4: SiO2, high-temperature data).",
)
TABLE6_LOG_KF_RENDER = (
    "23.773", "23.630", "17.774", "14.242", "11.868", "10.156", "8.856", "8.343",
    "8.342", "7.819", "7.292", "7.292", "6.973", "6.276", "5.693", "5.371",
)
TABLE1_REACTION_HEAT_SCALING = (
    (
        "bennington_1982_table1_reaction_1_heat_of_solution",
        "bennington_1982_table2_reaction_1_mean",
        "2",
        "2 SiO2",
        "-32.904",
        "-65.808",
        "2SiO2(c) + 12HF(sol) = 2H2SiF6(sol) + 4H2O(sol)",
        2,
    ),
    (
        "bennington_1982_table1_reaction_2_heat_of_solution",
        "bennington_1982_table2_reaction_2_mean",
        "1/2",
        "1/2 Fe2O3",
        "-42.026",
        "-21.013",
        "1/2Fe2O3(c) + 3H+(sol) = Fe3+(sol) + 3/2H2O(sol)",
        3,
    ),
    (
        "bennington_1982_table1_reaction_3_heat_of_solution",
        "bennington_1982_table2_reaction_3_mean",
        "1/2",
        "1/2 Na2SO4",
        "+1.056",
        "+0.528",
        "1/2Na2SO4 III(c) = Na+(sol) + 1/2SO4^2−(sol)",
        4,
    ),
    (
        "bennington_1982_table1_reaction_4_heat_of_solution",
        "bennington_1982_table2_reaction_4_mean",
        "4",
        "4 H2O",
        "+0.820",
        "+3.280",
        "4H2O(l) = 4H2O(sol)",
        5,
    ),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        row["observation_id"]: (species, row)
        for species, block in document["species"].items()
        for row in block.get("observations", [])
    }


def _context(document: dict, observation_id: str) -> dict:
    return next(
        row
        for block in document["species"].values()
        for row in block.get("context", [])
        if row["observation_id"] == observation_id
    )


def _assert_table1_reaction_heat_scaling(document: dict) -> None:
    observations = _observations(document)
    for observation_id, parent_id, factor, coefficient, mean, output, equation, row_number in (
        TABLE1_REACTION_HEAT_SCALING
    ):
        observation = observations[observation_id][1]
        derivation = observation["derivation"]
        assert observation["values"]["value"] == output
        assert observations[parent_id][1]["values"]["value"] == mean
        assert derivation["inputs"] == [parent_id]
        assert derivation["relation"] == (
            f"Table 1 reaction ({row_number - 1}) heat equals {factor} × the Table 2 "
            f"per-mole mean, using the {coefficient} stoichiometric coefficient in "
            "the located Table 1 equation."
        )
        assert derivation["method"]["state"]["value"] == equation
        locator = derivation["method"]["locator"]
        assert locator["page"] == 10
        assert locator["table"] == "1"
        assert locator["table_row_number"] == row_number


def _assert_render_pins(document: dict) -> None:
    observations = _observations(document)
    assert len(observations) == 43

    render_cells = [
        ("bennington_1982_table5_cp", 0, "40.607", 14, "5"),
        ("bennington_1982_table5_delta_fH", 0, "-615.870", 14, "5"),
        ("bennington_1982_table6_enthalpy_increment", 15, "54.111", 15, "6"),
        ("bennington_1982_table6_negative_gibbs_function", 14, "72.907", 15, "6"),
    ]
    for observation_id, index, value, page, table in render_cells:
        _, observation = observations[observation_id]
        point = observation["values"]["points"][index]
        assert point["value"] == value
        assert point["locator"]["page"] == page
        assert point["locator"]["table"] == table

    reaction14 = observations[
        "bennington_1982_table3_reaction_14_delta_fH_from_elements"
    ][1]
    assert reaction14["values"]["value"] == "-615.866"
    assert reaction14["locator"]["page"] == 12
    assert reaction14["locator"]["table"] == "3"
    assert reaction14["uncertainty"]["value_as_printed"] == "±0.719"
    assert reaction14["values"]["rounded_table_uncertainty_as_printed"] == "±0.72"
    assert reaction14["values"]["prose_uncertainty_as_printed"] == "±0.71"

    table2_trials = sum(
        len(observations[observation_id][1]["values"]["points"])
        for observation_id in (
            "bennington_1982_table2_reaction_1_previous_values",
            "bennington_1982_table2_reaction_1_confirmatory_values",
            *(f"bennington_1982_table2_reaction_{n}_individual_values" for n in range(2, 7)),
        )
    )
    assert table2_trials == 41
    assert sum(
        len(observations[f"bennington_1982_table5_{name}"][1]["values"]["points"])
        for name in (
            "cp", "entropy", "negative_gibbs_function", "enthalpy_increment",
            "delta_fH", "delta_fG", "log10_Kf",
        )
    ) == 19 * 7
    assert sum(
        len(observations[f"bennington_1982_table6_{name}"][1]["values"]["points"])
        for name in (
            "cp", "entropy", "negative_gibbs_function", "enthalpy_increment",
            "delta_fH", "delta_fG", "log10_Kf",
        )
    ) == 16 * 7

    for table, page, reaction, references in (
        (
            "5", 14,
            "2Si(s,l) + Fe(c,l) + Na(c,l,g) + 3O2(g) = NaFe3+Si2O6(c)",
            TABLE5_AUXILIARY_REFERENCES,
        ),
        (
            "6", 15,
            "2SiO2(c) + 0.5Fe2O3(c) + 0.5Na2O(c) = NaFe3+Si2O6(c)",
            TABLE6_AUXILIARY_REFERENCES,
        ),
    ):
        for property_name in ("delta_fH", "delta_fG", "log10_Kf"):
            row = observations[f"bennington_1982_table{table}_{property_name}"][1]
            derivation = row["derivation"]
            assert derivation["inputs"] == row["derived_from"]
            assert "raw_inputs" not in derivation
            assert tuple(derivation["references"]) == references
            assert derivation["method"]["state"]["value"].endswith(reaction)
            assert derivation["method"]["locator"]["page"] == page
            assert derivation["method"]["locator"]["table"] == table
            assert "Table 4 assigns" in derivation["method"]["locator"]["note"]
            assert row["values"]["reaction_as_printed"] == reaction
    assert sum(
        len(observations[f"bennington_1982_table{table}_{property_name}"][1]["values"]["points"])
        for table in ("5", "6")
        for property_name in ("delta_fH", "delta_fG", "log10_Kf")
    ) == 105

    # The source assigns the first six silica runs to earlier work and the last
    # three to this report's confirmatory measurements.
    assert observations["bennington_1982_table2_reaction_1_previous_values"][1][
        "values"
    ]["method_class"] == "quoted_attributed"
    assert observations["bennington_1982_table2_reaction_1_confirmatory_values"][1][
        "values"
    ]["method_class"] == "measured_direct"
    assert observations["bennington_1982_table2_reaction_5_individual_values"][1][
        "values"
    ]["attribution"].endswith("reference (3).")

    sample = _context(document, "bennington_1982_sample_characterisation")["values"]
    assert sample["composition_analysis_wt_percent_as_printed"] == {
        "Na2O": "13.32", "Fe2O3": "34.64", "SiO2": "51.99"
    }
    assert sample["spectrographic_trace_impurities_ppm_as_printed"] == {
        "Cr": "600", "Mn": "200", "Ni": "200"
    }
    assert "no Fe2+/Fe3+ ratio is printed" in sample["iron_valence_note"]

    abstract = _context(document, "bennington_1982_abstract_formation_summary")["values"]
    assert abstract["delta_fG298_from_elements_as_printed"] == "-575.8"
    assert abstract["delta_fH298_from_oxides_as_printed"] == "-32.08"
    assert _context(document, "bennington_1982_unprinted_ko_data_and_fit")["values"][
        "H298_minus_H0_status"
    ] == "H298−H0 is not printed in this report."

    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []


def test_render_cells_row_counts_and_fidelity_mutation() -> None:
    document = _document()
    _assert_render_pins(document)

    mutated = deepcopy(document)
    _observations(mutated)["bennington_1982_table5_cp"][1]["values"]["points"][0][
        "value"
    ] = "40.608"
    assert check_all_fidelity_samples_match(mutated, label=EXTRACT.stem)


def test_table6_log_kf_is_preserved_as_an_oxide_frame_schema_gap() -> None:
    document = _document()
    row = _observations(document)["bennington_1982_table6_log10_Kf"][1]
    values = row["values"]

    assert values["quantity"] == "log10_formation_from_oxides_as_printed"
    assert values["quantity_as_printed"] == "Log Kf"
    assert values["reference_state_as_printed"] == "formation from simple oxides"
    assert values["reaction_as_printed"] == (
        "2SiO2(c) + 0.5Fe2O3(c) + 0.5Na2O(c) = NaFe3+Si2O6(c)"
    )
    assert "log10_Kf denotes formation from the elements" in values["schema_gap"]
    assert "simulator/battery/enums.py::FORMATION_QUANTITIES" in values["schema_gap"]
    points = values["points"]
    assert len(points) == 16
    assert tuple(point["value_as_printed"] for point in points) == TABLE6_LOG_KF_RENDER
    assert all("value" not in point for point in points)
    assert all(point["locator"]["page"] == 15 for point in points)
    assert all(point["locator"]["table"] == "6" for point in points)


def test_table1_reaction_heats_apply_the_printed_stoichiometric_factors() -> None:
    document = _document()
    _assert_table1_reaction_heat_scaling(document)

    mutated = deepcopy(document)
    first_reaction = _observations(mutated)[
        "bennington_1982_table1_reaction_1_heat_of_solution"
    ][1]
    first_reaction["derivation"]["relation"] = (
        "The Table 1 reaction heat is the unscaled Table 2 per-mole mean."
    )
    with pytest.raises(AssertionError):
        _assert_table1_reaction_heat_scaling(mutated)


def test_isolated_migration_has_only_declared_quantity_gaps_and_printed_conditions(
    tmp_path: Path,
) -> None:
    document = _document()
    _assert_render_pins(document)
    result = _migrate_real_extract(
        tmp_path,
        EXTRACT.name,
        extract_doc=document,
    )
    assert result.validation is not None
    assert result.validation.hard_issues == ()

    migrated = {
        observation_id: row
        for observation_id, row in result.observations.items()
        if row.source_id == SID
    }
    assert len(migrated) == 308
    source_rows = _observations(document)
    for table, references in (
        ("5", TABLE5_AUXILIARY_REFERENCES),
        ("6", TABLE6_AUXILIARY_REFERENCES),
    ):
        for property_name in ("delta_fH", "delta_fG", "log10_Kf"):
            source_id = f"bennington_1982_table{table}_{property_name}"
            point_count = len(source_rows[source_id][1]["values"]["points"])
            point_rows = [
                row
                for observation_id, row in migrated.items()
                if observation_id.startswith(f"{SID}::{source_id}::")
            ]
            assert len(point_rows) == point_count
            for row in point_rows:
                assert row.derivation is not None
                assert row.derivation.references == references
                assert row.derivation.raw_inputs is None
    expected_quantity_unknown = set()
    for source_id in QUANTITY_UNKNOWN_SOURCE_IDS:
        assert source_id in source_rows
        points = source_rows[source_id][1]["values"].get("points")
        if points:
            point_ids = {
                observation_id
                for observation_id in migrated
                if observation_id.startswith(f"{SID}::{source_id}::")
            }
            assert len(point_ids) == len(points)
            expected_quantity_unknown.update(point_ids)
        else:
            expected_quantity_unknown.add(f"{SID}::{source_id}")

    quantity_unknown = {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.quantity.is_unknown
    }
    assert quantity_unknown == expected_quantity_unknown
    assert len(quantity_unknown) == 139
    assert sum(row.value.kind.value == "point" for row in migrated.values()) == 169
    assert sum(row.value.kind.value == "unavailable" for row in migrated.values()) == 139
    phase_unknown = {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.species.phase is None or row.identity.species.phase.is_unknown
    }
    assert phase_unknown == set()

    for source_id, temperature in (
        ("bennington_1982_table5_cp", "298.15"),
        ("bennington_1982_table5_delta_fH", "298.15"),
        ("bennington_1982_table6_cp", "1263"),
    ):
        row = next(
            row
            for observation_id, row in migrated.items()
            if observation_id.startswith(f"{SID}::{source_id}::")
            and row.identity.temperature_K.is_value
            and row.identity.temperature_K.value == Decimal(temperature)
        )
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal(temperature)
        assert row.identity.species.phase.is_value
        assert row.identity.composition is None or not row.identity.composition.is_value
        assert row.identity.total_pressure_Pa is None or not row.identity.total_pressure_Pa.is_value
        assert row.identity.fO2_Pa is None or not row.identity.fO2_Pa.is_value

    # The HF reactions have two printed participant temperatures, not a single
    # experiment T. Preserve both and keep the migrator's single T axis unknown.
    reaction1 = migrated[f"{SID}::bennington_1982_table1_reaction_1_heat_of_solution"]
    assert reaction1.identity.temperature_K is None or reaction1.identity.temperature_K.is_unknown
    assert reaction1.point_conditions["temperature_C"].state.is_unknown
    assert reaction1.point_conditions["reactant_temperature_C"].state.value == Decimal("25")
    assert reaction1.point_conditions["product_temperature_C"].state.value == Decimal("73.7")

    # No total pressure, oxygen fugacity, or melt composition is printed for the
    # experiments. They must not appear as numeric source conditions.
    for _, observation in source_rows.values():
        conditions = observation.get("point_conditions", {})
        assert "total_pressure_Pa" not in conditions
        assert "fO2_Pa" not in conditions
        assert "fO2_log" not in conditions
        assert "composition" not in conditions

    unavailable = sum(row.value.kind.value == "unavailable" for row in migrated.values())
    assert unavailable == len(expected_quantity_unknown)
