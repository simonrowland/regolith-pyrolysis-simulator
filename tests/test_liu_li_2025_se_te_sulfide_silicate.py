from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from tests.battery.test_migrate import _migrate_real_extract
from tests.literature_extract_test_helpers import iter_locators, source_page_text


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "liu-li-2025-se-te-sulfide-silicate"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
FIGURE_PAGES = {
    1: 4,
    2: 5,
    3: 6,
    4: 7,
    5: 8,
    6: 9,
    7: 9,
    8: 10,
    9: 11,
}

# Independent transcription of every Table 1 row and column from the 600 dpi
# render of physical PDF page 3. Columns follow the printed table order.
TABLE1_GRID = (
    ("C-2", "Basalt + Sulfide-1 + 10 % FeO", "1400", "1", "24", "Graphite", "Sil + Sul", "< −1.6", "3889", "317", "176", "9", "1036", "94", "83", "7", "256", "42"),
    ("E-1", "Basalt + Sulfide-1 + 5 % FeO", "1400", "1", "36", "Graphite", "Sil + Sul", "< −1.6", "1802", "147", "310", "18", "1682", "409", "189", "16", "510", "42"),
    ("C-1", "Basalt + Sulfide-1", "1400", "1", "24", "Graphite", "Sil + Sul", "< −1.6", "2572", "134", "333", "14", "2833", "259", "131", "7", "516", "40"),
    ("C-4", "Andesite + Sulfide-1", "1400", "1", "24", "Graphite", "Sil + Sul", "< −1.6", "881", "121", "664", "39", "2531", "407", "389", "54", "890", "153"),
    ("C-3", "Dacite + Sulfide-1", "1400", "1", "24", "Graphite", "Sil + Sul", "< −1.6", "429", "72", "860", "99", "7344", "3717", "774", "131", "1734", "248"),
    ("C-5", "Rhyolite + Sulfide-2", "1400", "1", "24", "Graphite", "Sil + Sul", "< −1.6", "499", "88", "1715", "205", "25,394", "5332", "672", "119", "2614", "482"),
    ("O-2", "Rhyolite + Sulfide-2 + 5 % FeS2", "1400", "1", "24", "Graphite", "Sil + Sul", "< −1.6", "815", "47", "2170", "285", "21,035", "3531", "434", "26", "3901", "508"),
    ("O-1", "Rhyolite + Sulfide-2 + 10 % FeS2", "1400", "1", "24", "Graphite", "Sil + Sul", "< −1.6", "1615", "54", "1690", "149", "4443", "1210", "241", "8", "4207", "339"),
    ("E-2", "Basalt + Sulfide-1", "1300", "1", "36", "Graphite", "Sil + Sul", "< −1.6", "967", "98", "308", "53", "1871", "392", "345", "35", "872", "24"),
    ("E-3", "Basalt + Sulfide-1", "1500", "1", "24", "Graphite", "Sil + Sul", "< −1.6", "2218", "107", "306", "20", "1608", "423", "152", "8", "436", "15"),
    ("E-4", "Basalt + Sulfide-1", "1600", "1", "24", "Graphite", "Sil + Sul", "< −1.6", "2775", "330", "261", "6", "1759", "102", "121", "15", "284", "68"),
    ("E-5", "Basalt + Sulfide-3", "1500", "1", "24", "Graphite", "Sil + Sul", "< −1.6", "2114", "190", "397", "10", "2766", "189", "155", "15", "446", "86"),
    ("E-6", "Basalt + Sulfide-3", "1600", "1", "24", "Graphite", "Sil + Sul", "< −1.6", "2458", "246", "335", "13", "2154", "212", "129", "13", "354", "29"),
    ("ED-2", "Andesite + Sulfide-1", "1200", "1", "48", "Graphite", "Sil + Sul", "< −1.6", "328", "36", "716", "44", "2695", "786", "1019", "112", "1293", "227"),
    ("ED-3", "Andesite + Sulfide-1", "1300", "1", "36", "Graphite", "Sil + Sul", "< −1.6", "427", "60", "772", "44", "2947", "266", "757", "108", "1171", "231"),
    ("ED-5", "Andesite + Sulfide-2", "1500", "1", "24", "Graphite", "Sil + Sul", "< −1.6", "1247", "29", "869", "47", "3574", "522", "277", "8", "814", "126"),
    ("ED-6", "Andesite + Sulfide-3", "1500", "1", "24", "Graphite", "Sil + Sul", "< −1.6", "1596", "96", "610", "64", "3725", "477", "193", "14", "482", "60"),
    ("DO1", "Andesite + Sulfide-4 + Anhydrite", "1200", "1", "48", "MgO", "Sil + Oli + Sul + Anhy", "~1.5", "1173", "83", "877", "153", "2460", "159", "242", "18", "1218", "200"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table1(document: dict) -> dict:
    return next(
        item
        for item in document["species"]["SiO2"]["context"]
        if item["observation_id"] == "liu_li_2025_table_1_experimental_runs"
    )


def _s_observations(document: dict) -> dict[str, tuple[dict, dict]]:
    return {
        point["sample_id_as_published"]: (observation, point)
        for observation in document["species"]["S"]["observations"]
        for point in observation["values"]["points"]
    }


def _table1_render_grid(document: dict) -> tuple[tuple[str, ...], ...]:
    observations = _s_observations(document)
    context_rows = {
        row["run_id"]: row for row in _table1(document)["values"]["rows"]
    }
    grid = []
    for expected in TABLE1_GRID:
        run_id = expected[0]
        observation, point = observations[run_id]
        context = context_rows[run_id]
        uncertainty = point["uncertainty"]["value"]
        grid.append((
            run_id,
            point["starting_materials_as_published"],
            point["T_C"],
            context["P_GPa"],
            context["duration_hours"],
            context["capsule"],
            point["run_products_as_printed"],
            context["delta_FMQ_as_printed"],
            point["value_as_printed"],
            uncertainty,
            context["DSe_sulfide_silicate"],
            context["DSe_1sigma"],
            context["DTe_sulfide_silicate"],
            context["DTe_1sigma"],
            context["DS_sulfide_silicate"],
            context["DS_1sigma"],
            context["DCu_sulfide_silicate"],
            context["DCu_1sigma"],
        ))
    return tuple(tuple(str(cell) for cell in row) for row in grid)


def test_table1_render_grid_binds_all_rows_and_fails_both_mutations() -> None:
    document = _document()
    actual = _table1_render_grid(document)
    assert len(TABLE1_GRID) == len(actual) == 18
    assert all(len(row) == 18 for row in TABLE1_GRID)
    assert actual == TABLE1_GRID
    assert actual[0][8:10] == ("3889", "317")
    assert actual[9][12] == "1608"
    assert actual[17][16] == "1218"

    changed_grid = list(TABLE1_GRID)
    changed_row = list(changed_grid[0])
    changed_row[8] = "3888"
    changed_grid[0] = tuple(changed_row)
    with pytest.raises(AssertionError):
        assert actual == tuple(changed_grid)

    changed_extract = deepcopy(document)
    _, c2_point = _s_observations(changed_extract)["C-2"]
    c2_point["value_as_printed"] = "3888"
    with pytest.raises(AssertionError):
        assert _table1_render_grid(changed_extract) == TABLE1_GRID


def test_isolated_migration_preserves_species_and_every_row_condition(tmp_path: Path) -> None:
    document = _document()
    assert all(
        locator["page"] == locator["pdf_page_index"] + 1
        for locator in iter_locators(document)
        if "page" in locator and "pdf_page_index" in locator
    )

    result = _migrate_real_extract(tmp_path, EXTRACT.name)
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == 18
    assert all(
        row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        for row in result.observations.values()
    )

    source_observations = {
        observation["observation_id"]: observation
        for observation in document["species"]["S"]["observations"]
    }
    for migrated_id, row in result.observations.items():
        source_observation_id = next(
            observation_id
            for observation_id in source_observations
            if f"::{observation_id}::" in migrated_id
        )
        source = source_observations[source_observation_id]
        point = source["values"]["points"][0]
        temp_c = Decimal(point["T_C"])
        assert row.identity.species.formula == "S"
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value.value == "glass"
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == temp_c + Decimal("273.15")
        # This quantity profile excludes total pressure from identity; the
        # printed pressure remains typed on every observation's conditions.
        assert row.identity.total_pressure_Pa.is_not_applicable
        assert row.value.point == Decimal(point["value_as_printed"])
        assert row.point_conditions is not None
        conditions = row.point_conditions
        assert conditions["temperature_C"].state.value == temp_c
        assert conditions["total_pressure_Pa"].state.value == Decimal("1000000000")
        assert conditions["delta_FMQ"].state.value == context_delta_fmq(
            document, point["sample_id_as_published"]
        )
        assert conditions["composition"].state.is_unknown
        assert source["values"]["points"][0]["quench_statement"]["preservation"] == "not_stated"


def context_delta_fmq(document: dict, run_id: str) -> str:
    return next(
        row["delta_FMQ_as_printed"]
        for row in _table1(document)["values"]["rows"]
        if row["run_id"] == run_id
    )


def test_source_pages_match_table_quench_statement_and_figure_locators() -> None:
    page2 = source_page_text(SOURCE_ID, 2)
    assert "experiments were quenched" in " ".join(page2.split())
    page3 = source_page_text(SOURCE_ID, 3)
    for marker in ("Table 1", "C-2", "3889", "DO1", "1218"):
        assert marker in page3

    document = _document()
    for observation in document["species"]["S"]["observations"]:
        assert observation["locator"]["page"] == 3
        assert observation["locator"]["pdf_page_index"] == 2
        assert observation["values"]["points"][0]["quench_statement"]["locator"]["page"] == 2

    for figure, page in FIGURE_PAGES.items():
        page_text = source_page_text(SOURCE_ID, page)
        assert f"Fig. {figure}." in page_text


def test_figures_are_deferred_and_include_rendered_series_counts() -> None:
    figures = [
        item
        for item in _document()["species"]["SiO2"]["context"]
        if item["type"] == "figure_record"
    ]
    assert len(figures) == 9
    assert {int(item["locator"]["figure"]) for item in figures} == set(range(1, 10))
    for figure in figures:
        values = figure["values"]
        assert values["quantity"] == "unavailable"
        assert values["schema_gap"]
        assert values["numeric_points"] == "none"
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["species"]
        assert values["phase"]
        assert values["temperature_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption"]
        assert values["series"]
        assert all(
            isinstance(series["marker_count"], int) and series["marker_count"] >= 0
            for series in values["series"]
        )
