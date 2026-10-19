from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from literature_extract_assertions import assert_all_physical_locators
from simulator.battery.enums import Phase, Quantity, ValenceValueForm, ValueKind
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/smythe-2013-cerium-thesis.yaml"
RENDER_PINS = {
    "smythe_2013_table23_xanes_log_ratio": ("DS09-C3-10", "-0.254", "0.034"),
    "smythe_2013_table23_chemical_fraction": ("DS09-C2-13", "0.281", "0.005"),
    "smythe_2013_table23_xanes_fraction": ("DS09-C2-12", "0.168", "0.008"),
}


def test_table23_values_match_render_and_migrate_with_conditions():
    extract = yaml.safe_load(EXTRACT.read_text())
    observations = extract["species"]["Ce"]["observations"]
    assert sum(len(item["values"]["series"]) for item in observations) == 46
    assert [len(item["values"]["series"]) for item in observations] == [11, 11, 12, 12]

    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = list(migrator.result.observations.values())
    assert len(migrated) == 46
    assert not migrator.result.validation.hard_issues

    for observation in migrated:
        assert observation.value.kind is ValueKind.POINT
        assert observation.identity.species.formula == "Ce"
        assert observation.identity.species.phase.value is Phase.GLASS
        assert observation.identity.quantity.value is Quantity.VALENCE_RATIO
        assert observation.identity.temperature_K.is_value
        assert observation.identity.fO2_Pa.is_value
        assert observation.identity.composition.is_unknown
        assert observation.point_conditions["temperature_K"].locator.table == "2.3"
        assert observation.point_conditions["fO2_log"].locator.table == "2.3"
        assert observation.point_conditions["quench_status"].state.value == "not_stated"
        assert Decimal(str(observation.uncertainty.verbatim["sigma"])) > 0

    for observation_id, (sample, value, sigma) in RENDER_PINS.items():
        source_observation = next(item for item in observations if item["observation_id"] == observation_id)
        source_row = next(item for item in source_observation["values"]["series"] if item["sample"] == sample)
        assert Decimal(str(source_row["value"])) == Decimal(value)
        assert Decimal(str(source_row["sigma"])) == Decimal(sigma)
        assert source_row["locator"]["page"] == 57
        assert source_row["locator"]["pdf_page_index"] == 56
        migrated_row = next(row for row in migrated if f"row={sample.casefold()}" in row.observation_id and observation_id in row.observation_id)
        assert migrated_row.identity.valence_basis.value.value_form is (
            ValenceValueForm.LOG10_RATIO if "log_ratio" in observation_id else ValenceValueForm.FRACTION
        )


def test_all_table_and_figure_locators_use_physical_pages():
    extract = yaml.safe_load(EXTRACT.read_text())
    assert_all_physical_locators(extract)

    locators = []

    def collect(value):
        if isinstance(value, dict):
            locator = value.get("locator")
            if isinstance(locator, dict):
                locators.append(locator)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(extract)
    assert len(locators) == 327
    for locator in locators:
        assert locator["published_page"] == locator["page"] - 14

    context = extract["species"]["Ce"]["context"]
    tables = {row["locator"].get("table"): row for row in context if row["type"] != "figure_context"}
    assert tables["3.3"]["locator"] == {
        "table": "3.3",
        "page": 90,
        "published_page": 76,
        "pdf_page_index": 89,
    }
    assert tables["A2.1"]["locator"] == {
        "table": "A2.1",
        "page": 131,
        "published_page": 117,
        "pdf_page_index": 130,
    }

    figures = {
        row["locator"]["figure"]: row
        for row in context
        if row["type"] == "figure_context"
    }
    assert set(figures) == {
        f"{chapter}.{number}"
        for chapter, last in ((1, 3), (2, 6), (3, 9), (4, 6))
        for number in range(1, last + 1)
    } | {"A1.1", "A1.2", "A4.1", "A4.2", "A4.3"}
    appendix_locator_pins = {
        "A1.1": (129, 128, 115),
        "A1.2": (130, 129, 116),
        "A4.1": (167, 166, 153),
        "A4.2": (168, 167, 154),
        "A4.3": (169, 168, 155),
    }
    for figure, (page, pdf_page_index, published_page) in appendix_locator_pins.items():
        assert figures[figure]["locator"] == {
            "figure": figure,
            "page": page,
            "pdf_page_index": pdf_page_index,
            "published_page": published_page,
        }
    for figure in figures.values():
        values = figure["values"]
        assert values["quantity"]
        assert values["identity"]
        assert values["panel"]
        assert values["axes"]
        assert values["series"]
        assert values["attribution"]
        assert values["digitisation"] == "marker inventory checked at 600 dpi; numeric digitisation deferred"
        assert all(
            series["panel"]
            and series["name"]
            and series["marker"]
            and isinstance(series["marker_count"], int)
            and series["attribution"]
            for series in values["series"]
        )
