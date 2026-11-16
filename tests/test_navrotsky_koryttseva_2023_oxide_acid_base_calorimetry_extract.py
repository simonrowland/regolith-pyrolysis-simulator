from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from literature_extract_assertions import assert_all_physical_locators
from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/navrotsky-koryttseva-2023-oxide-acid-base-calorimetry.yaml"
SOURCE_PATH = "tables/navrotsky-koryttseva-2023-oxide-acid-base-calorimetry/cited-pages.txt"


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table_locators(value):
    locators = []

    def visit(item):
        if isinstance(item, dict):
            locator = item.get("locator")
            if isinstance(locator, dict) and "table" in locator:
                locators.append(locator)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return locators


def test_table1_extrema_are_render_checked_and_table_text_is_in_corpus():
    document = _document()
    assert len(document["fidelity_samples"]) == 6
    assert check_all_fidelity_samples_match(document) == []
    assert_all_physical_locators(document)

    mg = document["species"]["MgO"]["observations"][0]
    v2o5 = document["species"]["V2O5"]["observations"][0]
    assert [point["enthalpy_increment"] for point in mg["values"]["series"]] == [
        "31.60",
        "36.71",
    ]
    assert [point["enthalpy_increment"] for point in v2o5["values"]["series"]] == [
        "177.19",
        "196.25",
    ]

    tables = {
        row["locator"]["table"]: row
        for row in document["species"]["SiO2"]["context"]
        if row["type"] == "table_context"
    }
    assert set(tables) == {"1", "2"}
    assert tables["1"]["locator"]["page"] == 5
    assert tables["2"]["locator"]["page"] == 9
    assert all("printed_table_text" not in row["values"] for row in tables.values())

    table_locators = _table_locators(document)
    assert table_locators
    assert all(locator["source_path"] == SOURCE_PATH for locator in table_locators)
    assert all(len(locator["source_sha256"]) == 64 for locator in table_locators)

    mutated = deepcopy(document)
    mutated["species"]["K2O"]["observations"][0]["values"]["series"][0][
        "enthalpy_increment"
    ] = "66.71"
    assert check_all_fidelity_samples_match(mutated)


def test_table1_keeps_selected_outside_reference_and_phase_distinctions():
    document = _document()
    tho2 = document["species"]["ThO2"]["observations"][0]["values"]["series"][1]
    assert tho2["enthalpy_increment"] == "57.5"
    assert tho2["sigma"] == "1.7"
    teo2 = document["species"]["TeO2"]["observations"]
    assert [
        (row["phase"], row["values"]["series"][0]["enthalpy_increment"])
        for row in teo2
    ] == [("cr", "48.84"), ("l", "88.37")]


def test_isolated_migration_carries_temperature_to_every_heat_content_point():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 95
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    temperatures = {Decimal("973.15"), Decimal("1073.15")}
    counts = {temperature: 0 for temperature in temperatures}
    for row in result.observations.values():
        assert row.value.kind is ValueKind.POINT
        assert row.identity.quantity.value is Quantity.H_MINUS_H298
        assert row.identity.temperature_K.value in temperatures
        assert row.identity.species.phase.value in {Phase.CR, Phase.L, Phase.G}
        condition = row.point_conditions["temperature_K"]
        assert condition.state.is_value
        assert condition.state.value in temperatures
        assert condition.locator.table == "1"
        assert row.locator.table == "1"
        counts[row.identity.temperature_K.value] += 1
    assert counts == {Decimal("973.15"): 49, Decimal("1073.15"): 46}


def test_tables_figures_and_all_nested_locators_are_preserved():
    document = _document()
    assert_all_physical_locators(document)
    context = document["species"]["SiO2"]["context"]
    figures = {
        row["locator"]["figure"]: row
        for row in context
        if row["type"] == "figure_context"
    }
    assert set(figures) == {str(number) for number in range(1, 9)}
    for figure in figures.values():
        values = figure["values"]
        assert values["quantity"]
        assert values["identity"]
        assert values["panel"]
        assert values["axes"]
        assert values["attribution"]
        assert values["digitisation"] == "marker inventory checked at 600 dpi; numeric digitisation deferred"
        assert values["series"]
        assert all(
            series["name"]
            and series["marker"]
            and isinstance(series["marker_count"], int)
            and series["attribution"]
            for series in values["series"]
        )
