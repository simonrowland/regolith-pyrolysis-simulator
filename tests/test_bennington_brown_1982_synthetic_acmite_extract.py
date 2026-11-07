"""Rendered source pins and isolated migration for synthetic acmite."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

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
}


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
    source_rows = _observations(document)
    expected_quantity_unknown = set()
    for source_id in QUANTITY_UNKNOWN_SOURCE_IDS:
        assert source_id in source_rows
        points = source_rows[source_id][1]["values"].get("points")
        if points:
            expected_quantity_unknown.update(
                f"{SID}::{source_id}::point:{index}" for index in range(len(points))
            )
        else:
            expected_quantity_unknown.add(f"{SID}::{source_id}")

    quantity_unknown = {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.quantity.is_unknown
    }
    assert quantity_unknown == expected_quantity_unknown
    assert len(quantity_unknown) == 123
    phase_unknown = {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.species.phase is None or row.identity.species.phase.is_unknown
    }
    assert phase_unknown == set()

    for observation_id, temperature in (
        (f"{SID}::bennington_1982_table5_cp::point:0", "298.15"),
        (f"{SID}::bennington_1982_table5_delta_fH::point:0", "298.15"),
        (f"{SID}::bennington_1982_table6_cp::point:15", "1263"),
    ):
        row = migrated[observation_id]
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal(temperature)
        assert row.identity.species.phase.is_value
        assert row.identity.composition is None or not row.identity.composition.is_value
        assert row.identity.total_pressure_Pa is None or row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa is None or row.identity.fO2_Pa.is_unknown

    # The HF reactions have two printed participant temperatures, not a single
    # experiment T. Preserve both and keep the migrator's single T axis unknown.
    reaction1 = migrated[f"{SID}::bennington_1982_table1_reaction_1_heat_of_solution"]
    assert reaction1.identity.temperature_K is None or reaction1.identity.temperature_K.is_unknown
    assert reaction1.point_conditions["temperature_C"].state.is_unknown
    assert reaction1.point_conditions["reactant_temperature_C"].state.value == "25"
    assert reaction1.point_conditions["product_temperature_C"].state.value == "73.7"

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
