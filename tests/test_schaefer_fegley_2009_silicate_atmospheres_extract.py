from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import REPO_ROOT
from simulator.battery.validate import validate_corpus
from simulator.yaml_cache import YAML12SafeLoader
from tests.battery.test_migrate import _migrate_real_extract
from tests.test_yaml_flow_scalar_commas import _walk_mapping_source_issues


EXTRACT_NAME = "schaefer-fegley-2009-silicate-atmospheres.yaml"
EXTRACT = REPO_ROOT / "data/literature/extracts" / EXTRACT_NAME
SOURCE_PREFIX = "schaefer-fegley-2009-silicate-atmospheres::"


def test_quoted_table_and_model_figures_remain_context_only(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = document["species"]["SiO2"]["context"]
    by_id = {item["observation_id"]: item for item in context}

    expected = {
        "schaefer_fegley_2009_table1_continental_crust": (
            "Wedepohl (1995)",
            ["62.93", "3.79", "15.45", "0.70", "5.78", "5.63", "3.27", "2.45"],
            "100.00",
        ),
        "schaefer_fegley_2009_table1_oceanic_crust": (
            "Klein (2005)",
            ["50.36", "7.61", "15.85", "1.48", "9.56", "12.24", "2.77", "0.13"],
            "100.00",
        ),
        "schaefer_fegley_2009_table1_bse": (
            "O’Neill & Palme (1998)",
            ["45.97", "36.66", "4.77", "0.18", "8.24", "3.78", "0.35", "0.04"],
            "99.99",
        ),
        "schaefer_fegley_2009_table1_moon": (
            "Warren (2005)",
            ["47.17", "36.29", "3.90", "0.18", "9.31", "3.08", "0.05", "0.005"],
            "99.985",
        ),
    }
    for observation_id, (attribution, components, total) in expected.items():
        record = by_id[observation_id]
        values = record["values"]
        assert values["method_class"] == values["evidence_class"] == "quoted_attributed"
        assert values["attribution"] == attribution
        assert [item["component"] for item in values["components"]] == [
            "SiO2", "MgO", "Al2O3", "TiO2", "FeO", "CaO", "Na2O", "K2O"
        ]
        assert [item["value_as_printed"] for item in values["components"]] == components
        assert values["total_wt_percent_as_printed"] == total
        assert record["locator"]["page"] == 15
        assert record["locator"]["pdf_page_index"] == 14

    figures = [item for item in context if item["type"] == "figure_only"]
    assert [item["locator"]["figure"] for item in figures] == ["1", "2", "3"]
    assert [item["locator"]["pdf_page_index"] for item in figures] == [15, 16, 17]
    for figure in figures:
        values = figure["values"]
        assert values["method_class"] == "model_derived"
        assert values["digitisation"] == "deferred to a figure pass"
        assert all(series["marker_count"] == 0 for series in values["series"])
        assert "points" not in values

    assert [len(item["values"]["series"]) for item in figures] == [4, 13, 20]
    model_results = by_id["schaefer_fegley_2009_model_results_as_printed"]["values"]
    assert model_results["initial_atmospheric_pressure"][0]["value"]["approximate"] is True
    assert model_results["initial_atmospheric_pressure"][2]["pressure_endpoints_bar"][0][
        "value"
    ]["approximate"] is True
    assert (
        model_results["bulk_silicate_earth_at_2200K"][
            "refractory_gas_regime_from_fraction"
        ]["value"]["kind"]
        == "bound"
    )
    assert [
        point["value"]["point"]
        if "value" in point
        else [edge["value"]["point"] for edge in point["pressure_endpoints_bar"]]
        for point in model_results["initial_atmospheric_pressure"]
    ] == [0.1, 0.0000001, [0.001, 0.00055]]

    bse = model_results["bulk_silicate_earth_at_2200K"]
    assert bse["locator"]["pdf_page_index"] == 6
    assert [
        bse[key]["value"]["point"]
        for key in (
            "sodium_loss_fraction",
            "potassium_loss_fraction",
            "SiO_peak_fraction",
            "Mg_dominant_from_fraction",
            "Fe_most_abundant_at_fraction",
            "Fe_loss_fraction",
        )
    ] == [2, 22, 16, 27, 2, 90]
    assert bse["refractory_gas_regime_from_fraction"]["value"][
        "bound_value"
    ] == 90
    assert [
        [edge["value"]["point"] for edge in bse[key]]
        for key in (
            "pressure_after_sodium_loss_bar",
            "pressure_after_90_percent_vaporized_bar",
        )
    ] == [[0.001, 0.0001], [0.000001, 0.0000001]]
    assert [
        edge["locator"]["pdf_page_index"]
        for key in (
            "pressure_after_sodium_loss_bar",
            "pressure_after_90_percent_vaporized_bar",
        )
        for edge in bse[key]
    ] == [7, 7, 7, 7]

    temperature_dependence = model_results[
        "bulk_silicate_earth_temperature_dependence"
    ]
    assert temperature_dependence["locator"]["pdf_page_index"] == 7
    assert [
        point["value"]["point"]
        for point in temperature_dependence["sodium_loss_fraction"]
    ] == [1, 11]
    assert temperature_dependence["at_1800K"]["Fe_dominant_until_fraction"][
        "value"]["point"] == 8
    assert temperature_dependence["at_1800K"]["Mg_dominant_from_fraction"][
        "value"]["point"] == 22

    crust = model_results["crust_at_2200K"]
    assert crust["locator"]["pdf_page_index"] == 7
    assert [
        crust[key]["value"]["point"]
        for key in (
            "Na_dominant_until_fraction",
            "sodium_persists_until_fraction",
            "pressure_drop_by_fraction",
            "continental_crust_potassium_peak_fraction",
            "continental_crust_potassium_loss_fraction",
        )
    ] == [11, 50, 63, 67, 74]

    source = EXTRACT.read_text(encoding="utf-8")
    loader = YAML12SafeLoader(source)
    try:
        node = loader.get_single_node()
        assert node is not None
        assert _walk_mapping_source_issues(node, loader, source) == []
        loader.construct_document(node)
    finally:
        loader.dispose()

    result = _migrate_real_extract(tmp_path / "isolated", EXTRACT_NAME)
    assert not any(key.startswith(SOURCE_PREFIX) for key in result.observations)
    validation = validate_corpus(
        result.works,
        result.experiments,
        result.observations,
        residuals=None,
    )
    assert validation.hard_issues == ()
