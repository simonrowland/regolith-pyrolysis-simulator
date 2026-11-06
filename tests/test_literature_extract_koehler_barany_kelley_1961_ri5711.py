from __future__ import annotations

import csv
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / (
    "data/literature/extracts/"
    "koehler-barany-kelley-1961-ri5711-ferrite-aluminate-formation.yaml"
)
SOURCE_ID = "koehler-barany-kelley-1961-ri5711-ferrite-aluminate-formation"


def _document() -> dict[str, object]:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_fidelity_values_and_observation_count_match_rendered_tables() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    expected = {
        "Ca2Fe2O5": {
            "ri5711_table7_ca2fe2o5_oxide": -10560,
            "ri5711_table7_ca2fe2o5_elements": -511240,
            "ri5711_table8_ca2fe2o5_entropy": 45.1,
        },
        "CaFe2O4": {
            "ri5711_table7_cafe2o4_oxide": -14580,
            "ri5711_table7_cafe2o4_elements": -363470,
            "ri5711_table8_cafe2o4_entropy": 34.7,
        },
        "NaFeO2": {
            "ri5711_table7_nafeo2_oxide": -18470,
            "ri5711_table7_nafeo2_elements": -166720,
            "ri5711_table8_nafeo2_entropy": 21.1,
        },
    }
    observations = document["species"]
    assert isinstance(observations, dict)
    for species, expected_values in expected.items():
        rows = observations[species]["observations"]
        assert len(rows) == (14 if species == "NaFeO2" else 12)
        by_id = {row["observation_id"]: row for row in rows}
        for observation_id, value in expected_values.items():
            source = by_id[observation_id]
            field = "entropy" if "entropy" in observation_id else "delta_fH"
            assert source["values"][field] == value
            assert source["values"]["phase_as_printed"] == f"{species}(c)"
            assert source["locator"]["page"] in {16, 17}
            if "table7" in observation_id:
                assert source["locator"]["page"] == 16
                assert source["locator"]["table"] == "7"
        if species == "CaFe2O4":
            assert by_id["ri5711_table7_cafe2o4_oxide"]["uncertainty"]["value"] == 180

    na_rows = {
        row["observation_id"]: row
        for row in observations["NaFeO2"]["observations"]
    }
    assert len(na_rows["ri5711_table12_nafeo2_oxide_enthalpy"]["values"]["points"]) + len(
        na_rows["ri5711_table12_nafeo2_oxide_enthalpy_liquid"]["values"]["points"]
    ) == 28
    assert len(na_rows["ri5711_table12_nafeo2_oxide_free_energy"]["values"]["points"]) + len(
        na_rows["ri5711_table12_nafeo2_oxide_free_energy_liquid"]["values"]["points"]
    ) == 28
    assert len(na_rows["ri5711_table23_nafeo2_element_enthalpy"]["values"]["points"]) + len(
        na_rows["ri5711_table23_nafeo2_element_enthalpy_liquid"]["values"]["points"]
    ) == 32
    assert len(na_rows["ri5711_table23_nafeo2_element_free_energy"]["values"]["points"]) + len(
        na_rows["ri5711_table23_nafeo2_element_free_energy_liquid"]["values"]["points"]
    ) == 32
    ca2_rows = {
        row["observation_id"]: row
        for row in observations["Ca2Fe2O5"]["observations"]
    }
    table9_h_points = [
        point
        for observation_id in (
            "ri5711_table9_ca2fe2o5_oxide_enthalpy",
            "ri5711_table9_ca2fe2o5_oxide_enthalpy_liquid",
        )
        for point in ca2_rows[observation_id]["values"]["points"]
    ]
    table9_g_points = [
        point
        for observation_id in (
            "ri5711_table9_ca2fe2o5_oxide_free_energy",
            "ri5711_table9_ca2fe2o5_oxide_free_energy_liquid",
        )
        for point in ca2_rows[observation_id]["values"]["points"]
    ]
    assert len(table9_h_points) == len(table9_g_points) == 22
    assert [point for point in table9_h_points if point["T_K"] == 1050] == [
        {"T_K": 1050, "delta_fH": -12.35, "row_marker_as_printed": "2"},
        {"T_K": 1050, "delta_fH": -12.35},
    ]
    assert [point for point in table9_g_points if point["T_K"] == 1050] == [
        {"T_K": 1050, "delta_fG": -15.5, "row_marker_as_printed": "2"},
        {"T_K": 1050, "delta_fG": -15.5},
    ]
    cafe_rows = {
        row["observation_id"]: row
        for row in observations["CaFe2O4"]["observations"]
    }
    ca2_context = {
        row["observation_id"]: row
        for row in observations["Ca2Fe2O5"]["context"]
    }
    cafe_context = {
        row["observation_id"]: row
        for row in observations["CaFe2O4"]["context"]
    }
    table10_h_points = [
        point
        for observation_id in (
            "ri5711_table10_cafe2o4_oxide_enthalpy",
            "ri5711_table10_cafe2o4_oxide_enthalpy_liquid",
        )
        for point in cafe_rows[observation_id]["values"]["points"]
    ]
    table10_g_points = [
        point
        for observation_id in (
            "ri5711_table10_cafe2o4_oxide_free_energy",
            "ri5711_table10_cafe2o4_oxide_free_energy_liquid",
        )
        for point in cafe_rows[observation_id]["values"]["points"]
    ]
    assert len(table10_h_points) == len(table10_g_points) == 22
    assert [point for point in table10_h_points if point["T_K"] == 1050] == [
        {"T_K": 1050, "delta_fH": -18.15, "row_marker_as_printed": "2"},
        {"T_K": 1050, "delta_fH": -18.15},
    ]
    assert [point for point in table10_g_points if point["T_K"] == 1050] == [
        {"T_K": 1050, "delta_fG": -17.9, "row_marker_as_printed": "2"},
        {"T_K": 1050, "delta_fG": -17.9},
    ]
    table12_h_points = (
        na_rows["ri5711_table12_nafeo2_oxide_enthalpy"]["values"]["points"]
        + na_rows["ri5711_table12_nafeo2_oxide_enthalpy_liquid"]["values"]["points"]
    )
    assert [point for point in table12_h_points if point["T_K"] == 1050] == [
        {"T_K": 1050, "delta_fH": -19.15, "row_marker_as_printed": "3"},
        {"T_K": 1050, "delta_fH": -19.15},
    ]
    assert next(point for point in table12_h_points if point["T_K"] == 950)[
        "row_marker_as_printed"
    ] == "2"
    marker_context = next(
        row for row in observations["NaFeO2"]["context"]
        if row["observation_id"] == "ri5711_nafeo2_transition_locator_key"
    )
    assert [entry["marker"] for entry in marker_context["values"]["table12_marker_mapping"]] == [
        "1", "2", "3", "4", "5", "6"
    ]
    assert marker_context["values"]["table12_marker_mapping"][3] == {
        "marker": "4",
        "label_as_printed": "Melting point of Na2O",
        "T_K": 1190,
    }
    assert [
        entry["marker"]
        for entry in ca2_context["ri5711_ca2fe2o5_transition_locator_key"]["values"]["table9_marker_mapping"]
    ] == ["1", "2", "3"]
    assert [
        entry["marker"]
        for entry in cafe_context["ri5711_cafe2o4_transition_locator_key"]["values"]["table10_marker_mapping"]
    ] == ["1", "2", "3"]
    marker_mappings = [
        ("Ca2Fe2O5", ca2_context["ri5711_ca2fe2o5_transition_locator_key"], "table9_marker_mapping"),
        ("CaFe2O4", cafe_context["ri5711_cafe2o4_transition_locator_key"], "table10_marker_mapping"),
        ("NaFeO2", marker_context, "table12_marker_mapping"),
    ]
    cells_path = ROOT / (
        "data/literature/extracts/audits/"
        "koehler-barany-kelley-1961-ri5711-ferrite-aluminate-formation.cells.csv"
    )
    with cells_path.open(newline="", encoding="utf-8") as stream:
        cells = list(csv.DictReader(stream))
    for _species, context, mapping_name in marker_mappings:
        observation_id = context["observation_id"]
        for index, marker in enumerate(context["values"][mapping_name]):
            cell_field = f"values.{mapping_name}[{index}].T_K"
            matches = [
                row for row in cells
                if row["observation_id"] == observation_id
                and row["cell_field"] == cell_field
            ]
            assert len(matches) == 1
            assert int(matches[0]["value"]) == marker["T_K"]
    table20_g = next(
        row for row in observations["Ca2Fe2O5"]["observations"]
        if row["observation_id"] == "ri5711_table20_ca2fe2o5_element_free_energy"
    )
    assert next(
        point["delta_fG"] for point in table20_g["values"]["points"]
        if point["T_K"] == 1200
    ) == -392.3

    mutated = deepcopy(document)
    mutated_h = next(
        row for row in mutated["species"]["NaFeO2"]["observations"]
        if row["observation_id"] == "ri5711_table12_nafeo2_oxide_enthalpy"
    )
    mutated_h["values"]["points"][0]["delta_fH"] = -18.46
    assert check_all_fidelity_samples_match(mutated, label="mutated Table 12")
    mutated_t20 = deepcopy(document)
    t20 = next(
        row for row in mutated_t20["species"]["Ca2Fe2O5"]["observations"]
        if row["observation_id"] == "ri5711_table20_ca2fe2o5_element_free_energy"
    )
    t20["values"]["points"][17]["delta_fG"] = -382.3
    assert check_all_fidelity_samples_match(mutated_t20, label="mutated Table 20")
    mutated_uncertainty = deepcopy(document)
    cafe = next(
        row for row in mutated_uncertainty["species"]["CaFe2O4"]["observations"]
        if row["observation_id"] == "ri5711_table7_cafe2o4_oxide"
    )
    cafe["uncertainty"]["value"] = 130
    assert cafe["uncertainty"]["value"] != 180
    # The fidelity resolver addresses evidence under values[]. Expose the
    # migrated numeric uncertainty in a test-only values path so the existing
    # checker verifies the source pin without adding a duplicate field to YAML.
    uncertainty_probe = deepcopy(mutated_uncertainty)
    probe_cafe = next(
        row for row in uncertainty_probe["species"]["CaFe2O4"]["observations"]
        if row["observation_id"] == "ri5711_table7_cafe2o4_oxide"
    )
    probe_cafe["values"]["numeric_uncertainty_probe"] = probe_cafe["uncertainty"]["value"]
    uncertainty_probe["fidelity_samples"].append(
        {
            "path": "species.CaFe2O4.observations[ri5711_table7_cafe2o4_oxide].values.numeric_uncertainty_probe",
            "value": 180,
            "locator": {
                "page": 16,
                "published_page": 9,
                "table": "7",
                "row": "CaFe2O4(c)",
                "column": "From oxides uncertainty",
            },
        }
    )
    assert check_all_fidelity_samples_match(uncertainty_probe, label="mutated numeric uncertainty")
    mutated_printed_uncertainty = deepcopy(document)
    mutated_cafe = next(
        row for row in mutated_printed_uncertainty["species"]["CaFe2O4"]["observations"]
        if row["observation_id"] == "ri5711_table7_cafe2o4_oxide"
    )
    mutated_cafe["values"]["uncertainty_as_printed"] = "±130 cal/mol"
    assert check_all_fidelity_samples_match(
        mutated_printed_uncertainty,
        label="mutated Table 7 printed uncertainty",
    )


def test_isolated_migration_maps_quantities_temperatures_and_unprinted_pressure() -> None:
    document = _document()
    source_species = document["species"]
    expected = {
        species: [row for row in section["observations"]]
        for species, section in source_species.items()
    }
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == (), "\n".join(
        str(issue) for issue in result.validation.hard_issues[:30]
    )
    assert result.registry_issues == []
    oxide_basis_ids = {
        "ri5711_table7_ca2fe2o5_oxide",
        "ri5711_table9_ca2fe2o5_oxide_enthalpy",
        "ri5711_table9_ca2fe2o5_oxide_enthalpy_liquid",
        "ri5711_table9_ca2fe2o5_oxide_free_energy",
        "ri5711_table9_ca2fe2o5_oxide_free_energy_liquid",
        "ri5711_table7_cafe2o4_oxide",
        "ri5711_table10_cafe2o4_oxide_enthalpy",
        "ri5711_table10_cafe2o4_oxide_enthalpy_liquid",
        "ri5711_table10_cafe2o4_oxide_free_energy",
        "ri5711_table10_cafe2o4_oxide_free_energy_liquid",
        "ri5711_table7_nafeo2_oxide",
        "ri5711_table12_nafeo2_oxide_enthalpy",
        "ri5711_table12_nafeo2_oxide_enthalpy_liquid",
        "ri5711_table12_nafeo2_oxide_free_energy",
        "ri5711_table12_nafeo2_oxide_free_energy_liquid",
    }
    oxide_point_count = sum(
        len(source["values"].get("points", [])) or 1
        for rows in expected.values()
        for source in rows
        if source["observation_id"] in oxide_basis_ids
    )
    assert oxide_point_count == 147
    assert len(result.observations) == 334
    assert len(result.queue) == 210
    assert not any("temperature_K" in entry.axes for entry in result.queue)
    printed_value_count = sum(
        len(source["values"].get("points", [])) or 1
        for rows in expected.values()
        for source in rows
    )
    assert printed_value_count == 334
    assert len(result.observations) == 334

    unknown_quantity_ids = set()
    for species, rows in expected.items():
        for source in rows:
            prefix = f"{SOURCE_ID}::{source['observation_id']}"
            matches = [
                row for key, row in result.observations.items()
                if key == prefix or key.startswith(prefix + "::")
            ]
            printed_count = len(source["values"].get("points", [])) or 1
            assert len(matches) == printed_count
            for migrated in matches:
                assert migrated.identity.species.formula == species
                expected_phase = Phase.L if source["phase"] == "l" else Phase.CR
                assert migrated.identity.species.phase.value is expected_phase
                assert migrated.identity.total_pressure_Pa is None or not (
                    migrated.identity.total_pressure_Pa.is_value
                )
                assert migrated.identity.temperature_K is not None
                assert migrated.identity.temperature_K.is_value
                if migrated.point_conditions is not None:
                    assert set(migrated.point_conditions) == {"temperature_K"}
                if migrated.identity.quantity.is_unknown:
                    unknown_quantity_ids.add(source["observation_id"])
                    assert migrated.value.kind is ValueKind.UNAVAILABLE
                else:
                    assert migrated.identity.quantity.is_value

    table12_g = [
        key for key in result.observations
        if key.startswith(
            f"{SOURCE_ID}::ri5711_table12_nafeo2_oxide_free_energy::T="
        )
    ]
    assert len(table12_g) == 25
    assert len(set(table12_g)) == 25
    table12_g_liquid = [
        key for key in result.observations
        if key.startswith(
            f"{SOURCE_ID}::ri5711_table12_nafeo2_oxide_free_energy_liquid::T="
        )
    ]
    assert len(table12_g_liquid) == 3
    assert len(set(table12_g_liquid)) == 3

    assert unknown_quantity_ids == {
        "ri5711_ca2fe2o5_calorimetric_inputs",
        "ri5711_cafe2o4_calorimetric_inputs",
        "ri5711_nafeo2_calorimetric_inputs",
        "ri5711_nafeo2_calorimetric_reaction30",
        "ri5711_nafeo2_calorimetric_reaction30_298_15",
    } | oxide_basis_ids
    table12_h_source = next(
        row for row in expected["NaFeO2"]
        if row["observation_id"] == "ri5711_table12_nafeo2_oxide_enthalpy"
    )
    assert table12_h_source["values"]["points"][0] == {
        "T_K": 298.15,
        "delta_fH": -18.45,
    }
    table12_h = next(
        row for key, row in result.observations.items()
        if key.startswith(
            f"{SOURCE_ID}::ri5711_table12_nafeo2_oxide_enthalpy::T=298.15:"
        )
    )
    assert table12_h.identity.quantity.is_unknown
    assert table12_h.value.kind is ValueKind.UNAVAILABLE
    assert table12_h.identity.temperature_K.value == Decimal("298.15")
    table23_g = next(
        row for key, row in result.observations.items()
        if key.startswith(
            f"{SOURCE_ID}::ri5711_table23_nafeo2_element_free_energy::T=1178:h="
        )
    )
    assert table23_g.identity.quantity.value is Quantity.DELTA_FG
    assert table23_g.value.point == Decimal("-468.39880")
    assert table23_g.identity.temperature_K.value == Decimal("1178")
    cafe2o4_elements = result.observations[
        f"{SOURCE_ID}::ri5711_table7_cafe2o4_elements"
    ]
    assert cafe2o4_elements.identity.quantity.value is Quantity.DELTA_FH
    assert cafe2o4_elements.value.point == Decimal("-1520.75848")


def test_prior_nbs_values_remain_separate_and_flagged() -> None:
    document = _document()
    ca_prior = next(
        row for row in document["species"]["CaFe2O4"]["context"]
        if row["observation_id"] == "ri5711_cafe2o4_nbs_comparison"
    )
    na_prior = next(
        row for row in document["species"]["NaFeO2"]["context"]
        if row["observation_id"] == "ri5711_nafeo2_nbs_comparison"
    )
    assert ca_prior["values"]["value_as_printed"] == -49.5
    assert na_prior["values"]["value_as_printed"] == 22.5
    assert ca_prior["units"] == "kcal/mol"
    assert na_prior["units"] == "kcal/mol"
    assert ca_prior["locator"] == {
        "page": 20,
        "published_page": 13,
        "section": "Discussion and conclusions",
    }
    assert ca_prior["values"]["reference_state_as_printed"] == "oxides"
    assert na_prior["values"]["reference_state_as_printed"] == "oxides"
    assert "not printed" in ca_prior["values"]["comparison_with_this_work"]
    assert "not printed" in na_prior["values"]["comparison_with_this_work"]
    reaction25 = next(
        row for row in document["species"]["NaFeO2"]["observations"]
        if row["observation_id"] == "ri5711_nafeo2_calorimetric_inputs"
    )
    reaction30 = next(
        row for row in document["species"]["NaFeO2"]["observations"]
        if row["observation_id"] == "ri5711_nafeo2_calorimetric_reaction30"
    )
    assert reaction25["values"]["reaction_number_as_printed"] == "25"
    assert reaction25["values"]["calorimetric_reaction_enthalpy_as_printed"] == -34350
    assert reaction25["values"]["uncertainty_as_printed"] == "±20 cal/mol"
    assert "corrected_at_298_15_K_cal_mol" not in reaction25["values"]
    assert reaction30["values"]["reaction_number_as_printed"] == "30"
    assert reaction30["values"]["calorimetric_reaction_enthalpy_as_printed"] == 7640
    assert reaction30["values"]["uncertainty_as_printed"] == "±40 cal/mol"
    reaction30_298 = next(
        row for row in document["species"]["NaFeO2"]["observations"]
        if row["observation_id"] == "ri5711_nafeo2_calorimetric_reaction30_298_15"
    )
    assert reaction30_298["values"]["reaction_number_as_printed"] == "30"
    assert reaction30_298["values"]["calorimetric_reaction_enthalpy_as_printed"] == 8240
    assert reaction30_298["values"]["uncertainty_as_printed"] == "±40 cal/mol"
