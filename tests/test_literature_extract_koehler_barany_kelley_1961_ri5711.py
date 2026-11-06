from __future__ import annotations

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
        "1", "2", "3", "5", "6"
    ]
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
    assert len(result.observations) == 330
    assert len(result.queue) == 48
    assert not any("temperature_K" in entry.axes for entry in result.queue)
    printed_value_count = sum(
        len(source["values"].get("points", [])) or 1
        for rows in expected.values()
        for source in rows
    )
    assert printed_value_count == 330
    assert len(result.observations) == 330

    unknown_quantity_ids = set()
    for species, rows in expected.items():
        for source in rows:
            prefix = f"{SOURCE_ID}::{source['observation_id']}"
            matches = [
                row for key, row in result.observations.items()
                if key == prefix or key.startswith(prefix + "::")
            ]
            expected_count = len(source["values"].get("points", [])) or 1
            assert len(matches) == expected_count
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
    }
    table12_h = next(
        row for key, row in result.observations.items()
        if key.startswith(
            f"{SOURCE_ID}::ri5711_table12_nafeo2_oxide_enthalpy::T=298.15:h="
        )
    )
    assert table12_h.identity.quantity.value is Quantity.DELTA_FH
    assert table12_h.value.kind is ValueKind.POINT
    assert table12_h.value.point == Decimal("-77.19480")
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
