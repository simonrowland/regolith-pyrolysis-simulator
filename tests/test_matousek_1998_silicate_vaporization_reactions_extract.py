from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import REPO_ROOT
from simulator.battery.validate import validate_corpus
from simulator.yaml_cache import YAML12SafeLoader
from tests.battery.test_migrate import _migrate_real_extract
from tests.test_yaml_flow_scalar_commas import _walk_mapping_source_issues


EXTRACT_NAME = "matousek-1998-silicate-vaporization-reactions.yaml"
EXTRACT = REPO_ROOT / "data/literature/extracts" / EXTRACT_NAME
SOURCE_PREFIX = "matousek-1998-silicate-vaporization-reactions::"


def test_tables_quotes_and_deferred_figures_migrate_cleanly(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = document["species"]["SiO2"]["context"]
    by_id = {item["observation_id"]: item for item in context}

    table1 = by_id["matousek_1998_table1_model_melt_vapor_species"]["values"]["rows"]
    assert len(table1) == 7
    assert table1[0]["temperature_range_as_printed"] == "1163 - 1500 K"
    assert table1[1]["gas_species_as_printed"] == ["NaBO2", "(NaBO2)2"]

    table2 = by_id["matousek_1998_table2_lead_oxide_equilibrium_constants"]["values"]["rows"]
    assert len(table2) == 4
    assert table2[0]["K_as_printed"] == "9.4 × 10^-2"
    assert table2[3]["K_as_printed"] == "3.4 × 10^-1"

    table3 = by_id["matousek_1998_table3_alkali_free_melt_vapor_species"]["values"]["rows"]
    assert len(table3) == 5
    assert table3[0]["temperature_range_as_printed"] == "1933 - 2133 K"
    assert table3[4]["temperature_as_printed"] == "1960 K"

    composition = by_id["matousek_1998_commercial_glass_compositions"]["values"]["examples"]
    assert composition[0]["components_as_printed"]["SiO2"] == 71.39
    assert composition[1]["composition_status"] == "unknown"
    assert composition[1]["components_as_printed"]["SiO2"] == 79.6

    quoted_pressures = [
        observation
        for formula in ("KOH", "NaOH")
        for observation in document["species"][formula]["observations"]
    ]
    assert [item["observation_id"] for item in quoted_pressures] == [
        "matousek_1998_koh_quoted_vapor_pressure",
        "matousek_1998_naoh_quoted_vapor_pressure",
    ]
    assert all(
        item["values"]["method_class"]
        == item["values"]["evidence_class"]
        == "quoted_attributed"
        for item in quoted_pressures
    )
    assert [item["values"]["attribution"] for item in quoted_pressures] == [
        "Ohe [13], Computer Aided Data Book of Vapor Pressure (1976)",
        "Ohe [13], Computer Aided Data Book of Vapor Pressure (1976)",
    ]
    assert [item["values"]["points"][0]["P"] for item in quoted_pressures] == [
        101.325,
        101.325,
    ]
    assert [item["values"]["points"][0]["T_C"] for item in quoted_pressures] == [
        1327,
        1378,
    ]
    assert [sample["value"] for sample in document["fidelity_samples"]] == [
        101.325,
        101.325,
    ]
    assert [
        sample["locator"]["pdf_page_index"]
        for sample in document["fidelity_samples"]
    ] == [2, 2]

    other_constants = by_id["matousek_1998_other_equilibrium_constants"]
    equation_locators = {
        row["equation"]: row["locator"]["pdf_page_index"]
        for row in other_constants["values"]["reactions"]
    }
    assert equation_locators == {"1": 0, "3": 1, "4": 1}
    water_constants = by_id["matousek_1998_water_vapor_equilibrium_constants"]
    assert water_constants["values"]["reactions"][-1]["locator"][
        "pdf_page_index"
    ] == 3
    thresholds = by_id["matousek_1998_approximate_temperature_thresholds"][
        "values"
    ]["temperatures_as_printed"]
    assert [
        thresholds[key]["locator"]["pdf_page_index"]
        for key in (
            "stable_oxide_volatilization",
            "pure_hydroxide_monomer_dimer_mixture",
            "water_reaction_with_silica_glass",
            "preferential_Si_OH4_formation",
        )
    ] == [1, 2, 3, 3]

    figures = [item for item in context if item["type"] == "figure_only"]
    assert [item["locator"]["figure"] for item in figures] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
    ]
    assert [item["locator"]["pdf_page_index"] for item in figures] == [
        2,
        2,
        2,
        3,
        3,
        5,
        5,
    ]
    for figure in figures:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
        assert values["typed_quantity"] in {
            Quantity.MASS_LOSS_AREAL_DENSITY.value,
            None,
        }
        assert all(series["marker_count"] >= 0 for series in values["series"])

    assert [
        [series["marker_count"] for series in figure["values"]["series"]]
        for figure in figures
    ] == [
        [7, 8, 10, 9],
        [5, 7],
        [6, 7],
        [5, 6],
        [5, 7, 8],
        [6, 3],
        [0],
    ]

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
    migrated = [
        observation
        for key, observation in result.observations.items()
        if key.startswith(SOURCE_PREFIX)
    ]
    assert len(migrated) == 2
    assert sorted(float(item.identity.temperature_K.value) for item in migrated) == [
        1600.15,
        1651.15,
    ]
    assert [float(item.value.point) for item in migrated] == [101325.0, 101325.0]
    for observation in migrated:
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator is not None
        assert temperature.locator.page == 76
        assert temperature.locator.published_page == 76
        assert temperature.locator.pdf_page_index == 2
    validation = validate_corpus(
        result.works,
        result.experiments,
        result.observations,
        residuals=None,
    )
    assert validation.hard_issues == ()
