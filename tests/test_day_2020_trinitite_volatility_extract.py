from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root
from tests.helpers.pdf_text import pdftotext_page_text
from tools.validate_literature_extracts import check_all_fidelity_samples_match


SID = "day-2020-trinitite-volatility"
EXTRACT = REPO_ROOT / "data/literature/extracts" / f"{SID}.yaml"
TABLE_COLUMNS = (
    "Sample", "Distance (m)", "Type", "Mass (g)", "Cu (ug/g)", "d65Cu",
    "plusminus_2sigma", "Fraction", "Blank%", "sum_Cu (ug/g)", "sum_d65Cu",
    "Cu (ICP) (ug/g)", "Zn (ug/g)", "d66Zn", "K2O (wt%)", "d41K",
)
# Literal Table S-1 transcription from the physical page-11 render. Empty cells are None.
TABLE_S1_GRID = [
    ("IF-2", "10", "Bulk", "0.0386", "4.4", "0.30", "0.04", "100%", "2.3%", "4.4", "0.30", "7.39", "13.2", "0.72", "3.3", "-0.27"),
    ("IF-2", "10", "Residue", "0.3113", "3.7", "0.46", "0.05", "97%", "0.3%", "4.0", "0.44", None, "12.1", "0.85", None, "-0.27"),
    ("IF-2", "10", "20m H2O US", "0.0004", "25.3", "-1.11", "0.01", "0.1%", "30.5%", None, None, None, "3.0", None, None, "-0.41"),
    ("IF-2", "10", "20m 3N HCl US", "0.0012", "18.6", "-0.63", "0.10", "0.4%", "14.8%", None, None, None, "144.0", "-0.09", None, "-0.40"),
    ("IF-2", "10", "2hr 1N HF-HNO3", "0.0065", "11.8", "-0.01", "0.03", "2.0%", "4.9%", None, None, None, "32.0", "0.67", None, "-0.26"),
    ("IG-2", "100", "Bulk", "0.0618", "36.8", "0.40", "0.02", "100%", "0.2%", "36.8", "0.40", "44.58", "12.6", "0.64", "3.5", "-0.45"),
    ("IG-2", "100", "Residue", "0.1966", "32.8", "0.45", "0.04", "96%", "0.1%", "33.0", "0.43", None, "10.2", "0.65", None, "-0.43"),
    ("IG-2", "100", "20m H2O US", "0.0005", "42.5", "-0.07", "0.12", "0.2%", "15.6%", None, None, None, "17.6", None, None, "-0.77"),
    ("IG-2", "100", "20m 3N HCl US", "0.0014", "69.0", "2.56", "0.03", "0.7%", "3.8%", None, None, None, "47.3", "0.12", None, "-0.34"),
    ("IG-2", "100", "2hr 1N HF-HNO3", "0.0067", "45.7", "-0.23", "0.01", "3.3%", "1.3%", None, None, None, "31.0", "0.63", None, "-0.42"),
    ("T-1", "200", "Bulk", "0.0971", None, None, None, None, None, None, None, "48.8", "18.3", None, "3.4", "-0.48"),
    ("T-2", None, "Bulk", "0.0911", None, None, None, None, None, None, None, "58.1", "17.4", None, "3.6", "-0.46"),
    ("T-3", "200", "Bulk", "0.1269", "66.8", "0.10", "0.02", None, "0.05%", "66.8", "0.10", "72.8", "16.3", "0.31", "3.51", "-0.42"),
]
ISOTOPE_COLUMNS = {"Cu": ("d65Cu", "plusminus_2sigma"), "Zn": ("d66Zn",), "K": ("d41K",)}


def _table_s1_grid(document: dict) -> list[tuple[str | None, ...]]:
    context = next(
        row for row in document["species"]["Cu"]["context"]
        if row["observation_id"] == "day_2020_table_s1_non_token_cells"
    )
    rows = []
    row_lookup = {}
    for source_row in context["values"]["rows_as_published"]:
        cells = {column: None for column in TABLE_COLUMNS}
        cells["Sample"] = source_row["sample_id"]
        cells["Distance (m)"] = source_row["distance_m_as_published"]
        cells["Type"] = source_row["sample_description"]
        cells.update(source_row["values_as_published"])
        row_lookup[(cells["Sample"], cells["Type"])] = cells
        rows.append(cells)

    for formula, container in document["species"].items():
        if formula not in ISOTOPE_COLUMNS:
            continue
        for observation in container["observations"]:
            for point in observation["values"]["points"]:
                cells = row_lookup[(point["sample_id"], point["sample_description"])]
                value_column, *extra_columns = ISOTOPE_COLUMNS[formula]
                cells[value_column] = point["isotope_delta_isotope_delta"]
                if extra_columns:
                    cells[extra_columns[0]] = point.get("sigma")
    return [tuple(row[column] for column in TABLE_COLUMNS) for row in rows]


def _assert_table_s1_grid(document: dict, expected: list[tuple[str | None, ...]]) -> None:
    actual = _table_s1_grid(document)
    assert actual == expected
    assert len(actual) == 13
    assert all(len(row) == len(TABLE_COLUMNS) for row in actual)


def test_day_isotope_observations_migrate_as_points_with_species_and_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    recombined = next(
        row for row in document["species"]["Cu"]["context"]
        if row["observation_id"] == "day_2020_recombined_residue_leachate_results_context"
    )
    assert recombined["values"]["values_as_published"] == {
        "IG_residue_etchate_leachate_Cu": "33.5",
        "IG_residue_etchate_leachate_delta65Cu": "0.44",
        "IF_residue_etchate_leachate_Cu": "4",
        "IF_residue_etchate_leachate_delta65Cu": "0.44",
    }
    assert recombined["locator"]["page"] == 10
    assert recombined["values"]["method_class"] == "author_derived"

    migration = migrate_one_extract(REPO_ROOT, EXTRACT, index={}, aliases={})
    migration.finalize()
    result = migration.result
    assert not result.validation.hard_issues, result.validation.hard_issues
    observations = [row for row in result.observations.values() if row.source_id == SID]
    assert len(observations) == 33

    expected = Counter()
    for formula, container in document["species"].items():
        for source_observation in container["observations"]:
            assert source_observation["values"]["quantity"] == Quantity.ISOTOPE_DELTA.value
            assert source_observation["values"].get("analyte", formula) == formula
            assert source_observation["values"]["method_class"] == source_observation["class_tag"]
            for point in source_observation["values"]["points"]:
                expected[(formula, Decimal(point["isotope_delta_isotope_delta"]))] += 1
                assert point["locator"]["page"] == point["locator"]["pdf_page_index"] + 1
                assert point["point_conditions"]["temperature_K"]["locator"]["page"] == 2
                if point["sample_id"] == "IF-2":
                    assert point["T_K"] == "1663"
                    assert point["point_conditions"]["temperature_K"]["state"]["value"] == "1663"
                if point["sample_id"] == "IG-2":
                    assert point["T_K"] == "1593"
                    assert point["point_conditions"]["temperature_K"]["state"]["value"] == "1593"
                if point["sample_id"] in {"T-1", "T-2", "T-3"}:
                    assert point["point_conditions"]["temperature_K"]["state"]["tag"] == "unknown"

    actual = Counter()
    for observation in observations:
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value is Quantity.ISOTOPE_DELTA
        assert observation.value.kind is ValueKind.POINT, observation.observation_id
        assert observation.identity.species.formula in ISOTOPE_COLUMNS
        assert observation.locator is not None
        assert not any(notice.kind.value == "source_value_outside_physical_range" for notice in observation.notices)
        conditions = observation.point_conditions or {}
        assert "temperature_K" in conditions, observation.observation_id
        assert conditions["temperature_K"].locator is not None
        temperature = conditions["temperature_K"].state
        if temperature.is_value:
            assert observation.identity.temperature_K.is_value
            assert observation.identity.temperature_K.value == temperature.value
        else:
            assert temperature.is_unknown
            assert observation.identity.temperature_K.is_unknown
        actual[(observation.identity.species.formula, observation.value.point)] += 1
    assert actual == expected
    assert Counter(row.identity.species.phase.value for row in observations) == Counter({"glass": 17, "aq": 16})
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []

    for formula, container in document["species"].items():
        for source_observation in container["observations"]:
            assert source_observation["class_tag"] == ("measured_direct" if formula == "Cu" else "quoted_attributed")
            assert source_observation["values"]["attribution"] == ("present work" if formula == "Cu" else "Day et al. (2017)" if formula == "Zn" else "Chen et al. (2019)")
            for point in source_observation["values"]["points"]:
                assert point["isotope_ratio"] == {"Cu":"65Cu/63Cu","Zn":"66Zn/64Zn","K":"41K/39K"}[formula]
                if formula == "Cu":
                    assert Decimal(point["sigma"]) > 0
                    assert point["uncertainty_convention"].startswith("±2σ")


def test_table_s1_grid_is_render_pinned_and_both_mutations_fail() -> None:
    pdf = regolith_corpus_root() / "raw" / SID / f"{SID}.pdf"
    if not pdf.is_file():
        pytest.skip(f"corpus PDF unavailable: {pdf}")
    page = pdftotext_page_text(pdf, 11)
    assert "Table S-1" in page
    assert "IF-2" in page and "IG-2" in page and "T-3" in page
    assert "25.3" in page and "2.56" in page and "66.8" in page

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table_s1_grid(document, TABLE_S1_GRID)

    changed_grid = deepcopy(TABLE_S1_GRID)
    changed_row = list(changed_grid[0])
    changed_row[4] = "999"
    changed_grid[0] = tuple(changed_row)
    with pytest.raises(AssertionError):
        _assert_table_s1_grid(document, changed_grid)

    changed_extract = deepcopy(document)
    table = next(
        row for row in changed_extract["species"]["Cu"]["context"]
        if row["observation_id"] == "day_2020_table_s1_non_token_cells"
    )
    table["values"]["rows_as_published"][0]["values_as_published"]["Cu (ug/g)"] = "999"
    with pytest.raises(AssertionError):
        _assert_table_s1_grid(changed_extract, TABLE_S1_GRID)

    for page_number, labels in {
        2: ("1663 K", "1593 K", "17 minutes"),
        3: ("Figure 1", "Figure 2"),
        4: ("Figure 3",),
        5: ("Figure 4",),
        10: ("Detailed Methods", "NIST SRM976"),
        11: ("Table S-1", "-1.11", "2.56", "-0.42"),
    }.items():
        page_text = pdftotext_page_text(pdf, page_number)
        assert all(label in page_text for label in labels), page_number
