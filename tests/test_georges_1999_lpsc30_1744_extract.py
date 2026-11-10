"""Source pins for Georges et al. (1999), LPSC abstract 1744."""

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator
from tests.literature_extract_test_helpers import source_page_text

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/georges-1999-lpsc30-1744.yaml"


def test_printed_results_and_deferred_figures_are_retained() -> None:
    data = yaml.safe_load(EXTRACT.read_text())
    context = data["species"]["K2O"]["context"]
    results = context[0]["values"]["printed_values"]
    evaporation_compositions = {
        row["experiment_id"]: row["sample"]["printed_composition"]["state"]["value"]
        for row in data["experiments"]
        if row["method"] == "controlled_potassium_partial_pressure_evaporation"
    }
    assert evaporation_compositions == {
        "georges-k-evaporation": {
            "SiO2": "44.2", "Al2O3": "14.9", "CaO": "12.6", "MgO": "10.2", "K2O": "18.1"
        },
        "georges-k-evaporation-k-free": {
            "SiO2": "57.6", "Al2O3": "15.1", "CaO": "15.3", "MgO": "12"
        },
    }
    assert "6.0 ± 0.5 wt%" in results["experiment_1_result"]
    assert "K-free" in results["experiment_1_conditions"]
    assert "both at 1400°C" in results["experiment_1_conditions"]
    assert "11.2 wt%" in results["experiment_2_result"]
    assert len([row for row in context if row["type"] == "figure_only"]) == 2
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in context[1:])
    assert [s["marker_count"] for s in context[1]["values"]["series"]] == [7, 5, 0]
    assert [s["marker_count"] for s in context[2]["values"]["series"]] == [5, 10]


def test_page_one_abstract_identifier_is_not_a_printed_folio() -> None:
    data = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    locators = [
        data["experiments"][0]["locator"],
        data["experiments"][0]["sample"]["printed_composition"]["locator"],
        data["experiments"][1]["locator"],
        data["experiments"][1]["sample"]["printed_composition"]["locator"],
        data["experiments"][2]["sample"]["printed_composition"]["locator"],
        data["species"]["K2O"]["context"][0]["locator"],
        data["species"]["K2O"]["context"][1]["locator"],
    ]
    assert len(locators) == 7
    for locator in locators:
        assert (locator["page"], locator["pdf_page_index"]) == (1, 0)
        assert "published_page" not in locator

    page1 = source_page_text(data["source_id"], 1)
    assert "Lunar and Planetary Science XXX" in page1
    assert "1744.pdf" in page1
    assert "6.0 ± 0.5 wt%" in page1


def test_isolated_migration_has_no_hard_issues_or_unsupported_points() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.observations
