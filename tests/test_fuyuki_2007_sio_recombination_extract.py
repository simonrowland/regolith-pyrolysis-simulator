from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import REPO_ROOT
from simulator.battery.validate import validate_corpus
from simulator.yaml_cache import YAML12SafeLoader, yaml_mapping_source_issues
from tests.battery.test_migrate import _migrate_real_extract


EXTRACT_NAME = "fuyuki-2007-sio-recombination.yaml"
EXTRACT = REPO_ROOT / "data/literature/extracts" / EXTRACT_NAME
SOURCE_PREFIX = "fuyuki-2007-sio-recombination::"


def test_text_results_table_and_deferred_figures_migrate_without_observations(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = document["species"]["SiO2"]["context"]
    by_id = {item["observation_id"]: item for item in context}

    summary = by_id["fuyuki_2007_measured_recombination_summary"]["values"]
    assert summary["temperature_interval_as_printed"] == "5000 K to 3000 K"
    assert summary["Si_to_SiO_number_ratio_as_printed"] == "15 to 0.5"
    assert summary["Si_to_SiO_number_ratio_as_printed_in_Fig14_caption"] == "10 to 0.1"
    early_temperature = by_id[
        "fuyuki_2007_textual_temperature_and_cooling_results"
    ]["values"]["early_plume_temperature"]
    assert early_temperature == {
        "value": 5200,
        "unit": "K",
        "approximate": True,
        "condition": "early observation, 10–13 ns after laser irradiation",
        "locator": {"page": 4, "published_page": 440, "pdf_page_index": 3},
    }
    temperature_results = by_id[
        "fuyuki_2007_textual_temperature_and_cooling_results"
    ]
    assert temperature_results["locator"]["pdf_page_index"] == 6
    comparison = temperature_results["values"][
        "Si_and_SiO_central_temperature_difference"
    ]
    assert comparison["value"] == "less than 200"
    assert comparison["unit"] == "K"
    assert comparison["observation_period_as_printed"] == "40–80 μs"
    assert comparison["locator"] == {
        "page": 7,
        "published_page": 443,
        "pdf_page_index": 6,
    }
    assert temperature_results["values"][
        "observation_period_temperature_locator"
    ]["pdf_page_index"] == 8
    assert temperature_results["values"][
        "observation_period_continuation_locator"
    ]["pdf_page_index"] == 9

    spectral = by_id["fuyuki_2007_model_derived_spectral_parameters"]
    assert spectral["values"]["initial_vapor_state_estimate"]["locator"][
        "pdf_page_index"
    ] == 7
    assert spectral["values"]["effective_activation_energy_as_printed"][
        "locator"
    ]["pdf_page_index"] == 9

    table = by_id["fuyuki_2007_table1_effective_collision_cross_sections"]["values"]["rows"]
    assert len(table) == 13
    assert [table[i]["collision_diameter_as_printed"] for i in (0, 4, 5, 7, 11, 12)] == [
        "10.8",
        "3.7",
        "11.4",
        "10.3",
        "8.2",
        "9.8",
    ]
    assert [table[i]["effective_collision_cross_section_as_printed"] for i in (0, 4, 5, 7, 11, 12)] == [
        "9.2",
        "11",
        "10.2",
        "8.3",
        "5.3",
        "7.5",
    ]
    assert [table[i]["particle_pair"] for i in (6, 8, 9)] == ["Si–O", "O–O", "O–O2"]
    assert all(table[i]["collision_diameter_as_printed"] == "N/A" for i in (6, 8, 9))

    figures = [item for item in context if item["type"] == "figure_only"]
    assert [item["locator"]["figure"] for item in figures] == [str(i) for i in range(1, 15)]
    assert [item["locator"]["pdf_page_index"] for item in figures] == [
        1, 2, 3, 3, 3, 4, 5, 5, 6, 6, 7, 8, 8, 8
    ]
    expected_markers = [
        [], [0, 0, 0, 0], [0, 0, 0, 0], [5], [6], [525, 0, 525],
        [0, 0, 0, 0], [4, 4], [0, 4], [4], [0, 0], [4, 4],
        [8, 36, 36, 24, 36, 36], [4, 0, 0, 0, 0],
    ]
    assert [
        [series["marker_count"] for series in item["values"]["series"]]
        for item in figures
    ] == expected_markers
    for figure in figures:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
        assert values["species"]
        assert values["phase"]
        assert values["temperature_as_printed"]
        assert values["x_axis_as_printed"]
        assert values["y_axis_as_printed"]
        assert values["caption_as_printed"]
        assert all(series["marker_count"] >= 0 for series in values["series"])

    source = EXTRACT.read_text(encoding="utf-8")
    loader = YAML12SafeLoader(source)
    try:
        node = loader.get_single_node()
        assert node is not None
        assert yaml_mapping_source_issues(source) == []
        loader.construct_document(node)
    finally:
        loader.dispose()

    result = _migrate_real_extract(tmp_path / "isolated", EXTRACT_NAME)
    migrated = [
        observation
        for key, observation in result.observations.items()
        if key.startswith(SOURCE_PREFIX)
    ]
    assert migrated == []
    validation = validate_corpus(
        result.works,
        result.experiments,
        result.observations,
        residuals=None,
    )
    assert validation.hard_issues == ()
