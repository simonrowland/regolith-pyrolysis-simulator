from __future__ import annotations

import csv
from decimal import Decimal

import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract

SOURCE_ID = "takeda-1986-liquid-copper-soda-slag"
EXTRACT = REPO_ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
MAPPING = (
    REPO_ROOT
    / "data/literature/extracts/audits"
    / f"{SOURCE_ID}.mapping.yaml"
)
CELLS = (
    REPO_ROOT
    / "data/literature/extracts/audits"
    / f"{SOURCE_ID}.cells.csv"
)


def test_rendered_prose_values_and_isolated_migration_are_pinned() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mapping = yaml.safe_load(MAPPING.read_text(encoding="utf-8"))
    with CELLS.open(encoding="utf-8", newline="") as cells_file:
        cells = list(csv.DictReader(cells_file))
    source_species = document["species"]["Cu2O"]
    assert len(source_species["observations"]) == 1
    assert mapping["table_draft"]["tables_found"] == 0

    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.registry_issues
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == 1
    assert len(result.queue) == 2
    queued = {entry.axes[0]: entry for entry in result.queue}
    assert set(queued) == {"total_pressure_Pa", "method"}
    assert all(
        entry.observation_id == f"{SOURCE_ID}::takeda_1986_cu2o_melting_point"
        for entry in result.queue
    )
    assert queued["total_pressure_Pa"].why == (
        "source does not state a numeric total_pressure_Pa"
    )
    assert queued["method"].why == "source does not state method"
    assert len(result.context_by_work) == 1
    assert all(row.identity.quantity.is_value for row in result.observations.values())
    assert all(
        row.identity.species.phase.is_value for row in result.observations.values()
    )

    observation = result.observations[
        f"{SOURCE_ID}::takeda_1986_cu2o_melting_point"
    ]
    assert observation.value.kind is ValueKind.POINT
    assert observation.value.point == Decimal("1503")
    assert observation.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
    assert observation.identity.species.phase.value is Phase.CR
    assert observation.identity.temperature_K.is_not_applicable
    assert observation.locator is not None
    assert observation.locator.page == 2
    assert observation.locator.published_page == 609
    assert observation.locator.pdf_page_index == 1
    assert observation.point_conditions["temperature_K"].state.value == Decimal("1503")
    assert set(observation.point_conditions) == {"temperature_K"}
    assert (
        observation.identity.total_pressure_Pa is None
        or observation.identity.total_pressure_Pa.is_unknown
    )
    assert (
        observation.identity.fO2_Pa is None
        or observation.identity.fO2_Pa.is_not_applicable
    )
    assert (
        observation.identity.composition is None
        or observation.identity.composition.is_not_applicable
    )
    source_observation = source_species["observations"][0]
    assert source_observation["values"]["method_class"] == "quoted_unattributed"
    assert "background information" in source_observation["values"]["reason"]
    assert source_observation["point_conditions"]["temperature_K"]["locator"] == {
        "page": 2,
        "published_page": 609,
        "pdf_page_index": 1,
        "section": "Binary slag of Cu2O-Na2O or Cu2O-SiO2 system",
    }

    context = {
        row["observation_id"]: row
        for rows in result.context_by_work.values()
        for row in rows
    }
    assert len(context) == 24
    expected_context_pages = {
        "takeda_1986_experimental_conditions": (1, 608, 0),
        "takeda_1986_oxygen_cell_equation_and_reference": (1, 608, 0),
        "takeda_1986_cu_cu2o_reference_oxygen_potential": (3, 610, 2),
        "takeda_1986_cuo05_equilibrium_constants_and_copper_activity": (3, 610, 2),
        "takeda_1986_miscibility_gap_endpoints": (4, 611, 3),
        "takeda_1986_soda_composition_and_carbonate_ratio": (2, 609, 1),
        "takeda_1986_cuo05_activity_ranges": (5, 612, 4),
        "takeda_1986_na2o_activity_depression": (6, 613, 5),
        "takeda_1986_carbonate_co2_stoichiometric_reference": (6, 613, 5),
        "takeda_1986_carbonate_co2_observations": (6, 613, 5),
        "takeda_1986_acid_oxide_carbonate_decomposition_ratios": (6, 613, 5),
        "takeda_1986_intermediate_compound_stoichiometries": (7, 614, 6),
        "takeda_1986_cited_binary_phase_diagram_composition": (4, 611, 3),
        "takeda_1986_co2_analytical_method": (2, 609, 1),
        "takeda_1986_alumina_binary_slag_composition": (2, 609, 1),
        "takeda_1986_alumina_silica_slag_ratio": (3, 610, 2),
        "takeda_1986_alumina_cu2o_slag_composition": (7, 614, 6),
        "takeda_1986_abstract_activity_minimum_composition": (1, 608, 0),
        "takeda_1986_copper_solubility_and_activity_summary": (5, 612, 4),
        "takeda_1986_kojo_external_comparison": (5, 612, 4),
        "takeda_1986_yamato_external_comparison": (7, 614, 6),
        "takeda_1986_summary_binary_composition": (7, 614, 6),
        "takeda_1986_summary_activity_coefficient_minimum": (8, 615, 7),
        "takeda_1986_figures_not_digitized": (2, 609, 1),
    }
    assert set(context) == set(expected_context_pages)
    for row_id, expected_pages in expected_context_pages.items():
        locator = context[row_id]["locator"]
        assert (
            locator["page"],
            locator["published_page"],
            locator["pdf_page_index"],
        ) == expected_pages
    assert mapping["context_row_count"] == len(context)
    assert {
        row["observation_id"]: (row["page"], row["published_page"])
        for row in mapping["context_rows"]
    } == {
        row_id: (pages[0], pages[1])
        for row_id, pages in expected_context_pages.items()
    }
    expected_fidelity_pages = {
        "takeda_1986_cu2o_melting_point": (2, 609, 1),
        "takeda_1986_cu_cu2o_reference_oxygen_potential": (3, 610, 2),
        "takeda_1986_soda_composition_and_carbonate_ratio": (2, 609, 1),
        "takeda_1986_miscibility_gap_endpoints": (4, 611, 3),
        "takeda_1986_cited_binary_phase_diagram_composition": (4, 611, 3),
        "takeda_1986_carbonate_co2_stoichiometric_reference": (6, 613, 5),
    }
    assert {
        sample["observation_id"]: (
            sample["locator"]["page"],
            sample["locator"]["published_page"],
            sample["locator"]["pdf_page_index"],
        )
        for sample in document["fidelity_samples"]
    } == expected_fidelity_pages

    expected_corrected_cells = {
        ("takeda_1986_alumina_silica_slag_ratio", "Al2O3_over_SiO2"): (
            "3",
            "610",
            "about 0.6",
        ),
        ("takeda_1986_intermediate_compound_stoichiometries", "compound_formula"):
            ("7", "614", "2Na2O·SiO2"),
        ("takeda_1986_summary_binary_composition", "binary_addition"):
            ("7", "614", "up to 20 per cent"),
        ("takeda_1986_summary_activity_coefficient_minimum", "activity_coefficient_minimum_composition"):
            ("8", "615", "nNa2X/nSiO2 = 2"),
        ("takeda_1986_na2o_activity_depression", "activity_depression"):
            ("6", "613", "Na2O activity considerably depressed at 2Na2O·SiO2"),
        ("takeda_1986_figures_not_digitized", "figure_7_caption_temperature"):
            ("6", "613", "1523 K"),
    }
    csv_cells = {(row["record_id"], row["field"]): row for row in cells}
    for key, (page, published_page, printed_value) in expected_corrected_cells.items():
        cell = csv_cells[key]
        assert (cell["pdf_page"], cell["published_page"], cell["printed_value"]) == (
            page,
            published_page,
            printed_value,
        )

    activity_ranges = context["takeda_1986_cuo05_activity_ranges"]["values"]
    assert activity_ranges["method_class"] == "quoted_attributed"
    assert "numerical oxygen-potential inputs are not printed" in activity_ranges[
        "attribution"
    ]
    assert activity_ranges["status"] == "typed_refused_schema_gap"
    assert "derived_from" not in activity_ranges
    assert "raw_inputs" not in activity_ranges

    sodium_activity = context["takeda_1986_na2o_activity_depression"]["values"]
    assert sodium_activity["compound_as_printed"] == "2Na2O·SiO2"
    assert sodium_activity["numerical_activity"] == "not numerically stated"
    compound_stoichiometries = context[
        "takeda_1986_intermediate_compound_stoichiometries"
    ]["values"]
    assert compound_stoichiometries["Na2O_SiO2_compound_as_printed"] == "2Na2O·SiO2"
    assert "n_Na2O_over_n_SiO2_compound_as_printed" not in compound_stoichiometries

    figures = context["takeda_1986_figures_not_digitized"]["values"]["figures"]
    expected_figure_pages = {
        "1": (2, 609, 1),
        "2": (2, 609, 1),
        "3": (3, 610, 2),
        "4": (4, 611, 3),
        "5": (4, 611, 3),
        "6": (5, 612, 4),
        "7": (6, 613, 5),
        "8": (6, 613, 5),
        "9": (6, 613, 5),
        "10": (7, 614, 6),
        "11": (7, 614, 6),
    }
    assert {figure["figure"] for figure in figures} == set(expected_figure_pages)
    for figure in figures:
        assert (
            figure["page"],
            figure["published_page"],
            figure["pdf_page_index"],
        ) == expected_figure_pages[figure["figure"]]
    anchors = (
        (
            "takeda_1986_cu_cu2o_reference_oxygen_potential",
            "pO2_as_printed",
            "1.78 × 10^-4",
            3,
            610,
        ),
        (
            "takeda_1986_cuo05_equilibrium_constants_and_copper_activity",
            "equilibrium_constant_for_Oishi_data_as_printed",
            "K = 9.02",
            3,
            610,
        ),
        (
            "takeda_1986_miscibility_gap_endpoints",
            "silica_percent_where_gap_disappears_as_printed",
            "around 15%",
            4,
            611,
        ),
        (
            "takeda_1986_soda_composition_and_carbonate_ratio",
            "n_CO2_over_n_Na2X_as_printed",
            "around 0.4",
            2,
            609,
        ),
        (
            "takeda_1986_copper_solubility_and_activity_summary",
            "log_percent_Cu_vs_log_pO2_slope_as_printed",
            "1/4",
            5,
            612,
        ),
        (
            "takeda_1986_carbonate_co2_stoichiometric_reference",
            "stoichiometric_CO2_percent_as_printed",
            "41.5%",
            6,
            613,
        ),
    )
    for row_id, field, expected, page, published_page in anchors:
        row = context[row_id]
        assert row["values"][field] == expected
        assert row["locator"]["page"] == page
        assert row["locator"]["published_page"] == published_page

    p_o2 = context["takeda_1986_cu_cu2o_reference_oxygen_potential"]
    assert p_o2["values"]["status"] == "typed_refused_unit_unspecified"
    assert p_o2["values"]["method_class"] == "quoted_attributed"
    assert context["takeda_1986_miscibility_gap_endpoints"]["values"]["status"] == (
        "typed_refused_schema_gap"
    )
    tie_claims = context["takeda_1986_miscibility_gap_endpoints"]["values"][
        "printed_claims"
    ]
    assert tie_claims == [
        {
            "coexisting_phase": "liquid Na2CO3 melt",
            "component": "Cu2O",
            "amount_as_printed": "around 1%",
        },
        {
            "coexisting_phase": "liquid cuprous oxide",
            "component": "Na2X",
            "amount_as_printed": "around 20%",
        },
        {
            "coexisting_phase": "Na2O·SiO2 liquid",
            "component": "Cu2O",
            "amount_as_printed": "around 35 to 40%",
        },
    ]
    assert context["takeda_1986_cuo05_activity_ranges"]["values"]["status"] == (
        "typed_refused_schema_gap"
    )
    cited_diagram = context["takeda_1986_cited_binary_phase_diagram_composition"]
    assert cited_diagram["locator"]["page"] == 4
    assert cited_diagram["values"]["liquid_miscibility_gap_composition_as_printed"] == (
        "liquid SiO2 coexisting with a 30% SiO2-Cu2O melt"
    )
    assert all("caption_as_printed" in figure for figure in figures)
