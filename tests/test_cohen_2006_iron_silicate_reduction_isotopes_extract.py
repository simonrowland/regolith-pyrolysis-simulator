from copy import deepcopy
from pathlib import Path

import yaml

from literature_extract_assertions import assert_all_physical_locators
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/cohen-2006-iron-silicate-reduction-isotopes.yaml"


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observation(document, observation_id):
    return next(
        row
        for row in document["species"]["Fe"]["observations"]
        if row["observation_id"] == observation_id
    )


def _point(observation, temperature, duration):
    return next(
        row
        for row in observation["values"]["series"]
        if row["T_C"] == str(temperature) and row["duration_min"] == str(duration)
    )


def test_tables_are_split_at_printed_boundaries_and_table_4_keeps_measured_and_calculated_columns_distinct():
    document = _document()
    assert_all_physical_locators(document)

    tables = {
        row["locator"]["table"]: row
        for row in document["species"]["Fe"]["context"]
        if row["type"] == "table_context"
    }
    assert set(tables) == {"1", "2", "3", "4"}
    assert tables["1"]["values"]["printed_table_text"].startswith("Table 1.")
    assert "δ57Fe" in tables["1"]["values"]["printed_table_text"]
    assert tables["2"]["values"]["printed_table_text"].startswith("Table 2.")
    assert "1450 °C" in tables["2"]["values"]["printed_table_text"]
    assert tables["3"]["values"]["printed_table_text"].startswith("Table 3.")
    assert "Alabandite" in tables["3"]["values"]["printed_table_text"]
    assert tables["4"]["values"]["printed_table_text"].startswith("Table 4.")

    table_4 = tables["4"]["values"]
    assert table_4["measured_columns_as_printed"] == ["Metal weight (mg)", "Silicate weight (mg)"]
    assert table_4["calculated_columns_as_printed"] == ["Total Fe (mg)", "Bulk δ57Fe (‰)"]
    assert "measured" in table_4["note"] and "calculated" in table_4["note"]
    assert tables["1"]["locator"] == {
        "page": 3,
        "pdf_page_index": 2,
        "published_page": 3141,
        "table": "1",
    }
    assert tables["3"]["locator"]["page"] == 6
    assert tables["3"]["locator"]["published_page"] == 3144
    assert tables["4"]["locator"]["page"] == 7
    assert tables["4"]["locator"]["published_page"] == 3145


def test_render_checked_fidelity_samples_detect_a_mutated_numeric_cell():
    document = _document()
    assert check_all_fidelity_samples_match(document) == []

    mutated = deepcopy(document)
    _point(
        _observation(mutated, "cohen_2006_table_2_glass_sio2_residue"), 1450, 5
    )["SiO2_wt_pct"] = "45.8"
    assert check_all_fidelity_samples_match(mutated)


def test_table_2_glass_oxide_and_phase_specific_isotope_values_keep_temperature_2s_and_run_conditions():
    document = _document()
    assert_all_physical_locators(document)
    observations = document["species"]["Fe"]["observations"]
    composition = [
        row for row in observations if row["values"]["quantity"] == "residue_component_composition"
    ]
    isotope = [row for row in observations if row["values"]["quantity"] == "isotope_delta"]
    assert len(composition) == 9
    assert len(isotope) == 6
    assert sum(len(row["values"]["series"]) for row in composition) == 225
    assert sum(len(row["values"]["series"]) for row in isotope) == 49

    sio2 = _observation(document, "cohen_2006_table_2_glass_sio2_residue")
    assert sio2["values"]["component_basis_as_published"] == "oxide weight percent in the residual silicate glass"
    assert _point(sio2, 1450, 5)["SiO2_wt_pct"] == "45.7"
    al2o3 = _observation(document, "cohen_2006_table_2_glass_al2o3_residue")
    assert _point(al2o3, 1450, 5)["Al2O3_wt_pct"] == "9.3"

    metal_1450 = _observation(document, "cohen_2006_table_2_1450_metal_iron_isotope_delta")
    metal_1450_2 = _point(metal_1450, 1450, 2)
    assert metal_1450_2["isotope_delta_permil"] == "-0.31"
    assert metal_1450_2["two_sigma_permil"] == "0.25"
    assert metal_1450_2["uncertainty_as_published"] == "2s = 0.25‰"
    metal_1450_10 = _point(metal_1450, 1450, 10)
    assert metal_1450_10["isotope_delta_permil"] == "-0.34"
    assert metal_1450_10["two_sigma_permil"] == "0.25"
    assert metal_1450_10["uncertainty_as_published"] == "2s = 0.25‰"
    glass_1450 = _observation(document, "cohen_2006_table_2_1450_glass_iron_isotope_delta")
    assert all(point["duration_min"] != "2" for point in glass_1450["values"]["series"])
    assert _point(glass_1450, 1450, 20)["isotope_delta_permil"] == "-0.12"
    assert _point(glass_1450, 1450, 20)["two_sigma_permil"] == "0.04"
    glass_1500 = _observation(document, "cohen_2006_table_2_1500_glass_iron_isotope_delta")
    assert _point(glass_1500, 1500, 20)["isotope_delta_permil"] == "0.77"
    assert "two_sigma_permil" not in _point(glass_1500, 1500, 20)
    glass_1540 = _observation(document, "cohen_2006_table_2_1540_glass_iron_isotope_delta")
    assert _point(glass_1540, 1540, 240)["isotope_delta_permil"] == "1.05"
    assert "two_sigma_permil" not in _point(glass_1540, 1540, 240)

    for observation in composition + isotope:
        assert observation["locator"]["page"] == 3
        assert observation["locator"]["published_page"] == 3141
        assert observation["values"]["method_class"] == "measured_direct"
        for point in observation["values"]["series"]:
            assert point["point_conditions"]["temperature_C"]["state"]["value"] == point["T_C"]
            assert point["point_conditions"]["duration_min"]["state"]["value"] == point["duration_min"]
            assert point["point_conditions"]["furnace_as_printed"]["state"]["value"] == "1-atm vertical Deltech furnace"
            assert point["point_conditions"]["atmosphere_as_printed"]["state"]["value"] == (
                "pure CO or a CO/CO2 mixture; the exact mixture by row is not printed"
            )
            assert point["point_conditions"]["oxygen_fugacity_buffer_as_printed"]["state"]["value"] == (
                "IW−0.5 (run reduction condition as printed)"
            )
            assert point["locator"]["page"] == point["locator"]["pdf_page_index"] + 1


def test_isolated_migration_emits_every_typed_table_2_value_with_temperature_and_conditions():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 274
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert all(
        observation.identity.temperature_K is not None
        and observation.identity.temperature_K.is_value
        for observation in result.observations.values()
    )
    required_conditions = {
        "duration_min",
        "furnace_as_printed",
        "atmosphere_as_printed",
        "oxygen_fugacity_buffer_as_printed",
    }
    assert all(
        required_conditions <= set(observation.point_conditions or {})
        for observation in result.observations.values()
    )


def test_all_seven_figures_have_attributed_inventory_and_physical_locators():
    document = _document()
    assert_all_physical_locators(document)
    figures = {
        row["locator"]["figure"]: row
        for row in document["species"]["Fe"]["context"]
        if row.get("type") == "figure_context"
    }
    assert set(figures) == {str(number) for number in range(1, 8)}
    expected_counts = {
        "1": [15, 0],
        "2": [0, 0, 0, 0, 0, 0],
        "3": [10, 9, 9, 4, 7, 2],
        "4": [10, 9, 7, 8],
        "5": [0],
        "6": [9, 9, 8, 8, 7, 8],
        "7": [5, 8, 6, 0],
    }
    for number, figure in figures.items():
        values = figure["values"]
        assert values["typed_quantity"]
        assert values["species"]
        assert values["phase"]
        assert values["temperature_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert values["panel"]
        assert values["attribution"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert [series["marker_count"] for series in values["series"]] == expected_counts[number]
        assert all(series.get("name") and series.get("marker") and series.get("attribution") for series in values["series"])

    assert figures["1"]["locator"] == {
        "figure": "1",
        "page": 4,
        "pdf_page_index": 3,
        "published_page": 3142,
    }
    assert figures["4"]["locator"]["page"] == 5
    assert figures["4"]["locator"]["published_page"] == 3143
