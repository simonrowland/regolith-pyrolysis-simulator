from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/lee-bruckner-1986-borate-redox-series.yaml"
SOURCE_ID = "lee-bruckner-1986-borate-redox-series"

# Independent transcription of Table 1, checked against the 600 dpi physical-page-4 render.
RATIO_GRID = {
    "Mn": [
        ("90 mol% B2O3, 10 mol% Na2O", "0,5 wt% MnO", "4,5"),
        ("80 mol% B2O3, 20 mol% Na2O", "0,5 wt% MnO", "13,25"),
        ("70 mol% B2O3, 30 mol% Na2O", "0,5 wt% MnO", "20,00"),
        ("80 mol% B2O3, 20 mol% Cs2O", "0,5 wt% MnO", "26,5"),
        ("70 mol% B2O3, 30 mol% Cs2O", "0,5 wt% MnO", "40,4"),
    ],
    "Cu": [
        ("90 mol% B2O3, 10 mol% Na2O", "0,5 wt% CuO", "75,8"),
        ("80 mol% B2O3, 20 mol% Na2O", "0,5 wt% CuO", "80,4"),
        ("70 mol% B2O3, 30 mol% Na2O", "0,5 wt% CuO", "80,2"),
        ("90 mol% B2O3, 10 mol% Cs2O", "0,5 wt% CuO", "74,61"),
        ("80 mol% B2O3, 20 mol% Cs2O", "0,5 wt% CuO", "72,56"),
        ("70 mol% B2O3, 30 mol% Cs2O", "0,5 wt% CuO", "66,10"),
    ],
    "Fe": [
        ("90 mol% B2O3, 10 mol% Na2O", "0,5 wt% Fe2O3", "4,8"),
        ("80 mol% B2O3, 20 mol% Na2O", "0,5 wt% Fe2O3", "4,26"),
        ("70 mol% B2O3, 30 mol% Na2O", "0,5 wt% Fe2O3", "3,88"),
    ],
    "V": [
        ("90 mol% B2O3, 10 mol% Na2O", "0,5 wt% V2O5", "5,16"),
        ("80 mol% B2O3, 20 mol% Na2O", "0,5 wt% V2O5", "4,7"),
        ("70 mol% B2O3, 30 mol% Na2O", "0,5 wt% V2O5", "4,4"),
    ],
    "Cr": [
        ("90 mol% B2O3, 10 mol% Na2O", "0,05 wt% Cr2O3", "13,45"),
        ("80 mol% B2O3, 20 mol% Na2O", "0,05 wt% Cr2O3", "22,20"),
        ("70 mol% B2O3, 30 mol% Na2O", "0,05 wt% Cr2O3", "35,89"),
        ("50 mol% B2O3, 50 mol% Na2O", "0,05 wt% Cr2O3", "91,82"),
    ],
    "Cr3": [
        ("90 mol% B2O3, 10 mol% Na2O", "0,05 wt% Cr2O3", "86,55"),
        ("80 mol% B2O3, 20 mol% Na2O", "0,05 wt% Cr2O3", "77,80"),
        ("70 mol% B2O3, 30 mol% Na2O", "0,05 wt% Cr2O3", "64,11"),
        ("50 mol% B2O3, 50 mol% Na2O", "0,05 wt% Cr2O3", "8,12"),
    ],
    "Ti": [
        ("90 mol% B2O3, 10 mol% Na2O", "0,5 wt% TiO2", "5,1"),
        ("70 mol% B2O3, 30 mol% Na2O", "0,5 wt% TiO2", "2,8"),
    ],
    "Co": [
        ("50 mol% B2O3, 50 mol% Na2O", "0,1 wt% Co3O4", "1,3"),
        ("40 mol% B2O3, 60 mol% Na2O", "0,1 wt% Co3O4", "2,05"),
        ("30 mol% B2O3, 70 mol% Na2O", "0,1 wt% Co3O4", "5,73"),
    ],
}
DENSITY_GRID = {
    "Mn": [
        ("90 mol% B2O3, 10 mol% Na2O", "0,5 wt% MnO", "2,042"),
        ("80 mol% B2O3, 20 mol% Na2O", "0,5 wt% MnO", "2,189"),
        ("70 mol% B2O3, 30 mol% Na2O", "0,5 wt% MnO", "2,299"),
        ("80 mol% B2O3, 20 mol% Cs2O", "0,5 wt% MnO", "2,78"),
        ("70 mol% B2O3, 30 mol% Cs2O", "0,5 wt% MnO", "3,29"),
    ],
    "Cu": [
        ("90 mol% B2O3, 10 mol% Na2O", "0,5 wt% CuO", "2,057"),
        ("80 mol% B2O3, 20 mol% Na2O", "0,5 wt% CuO", "2,190"),
        ("70 mol% B2O3, 30 mol% Na2O", "0,5 wt% CuO", "2,346"),
        ("90 mol% B2O3, 10 mol% Cs2O", "0,5 wt% CuO", "2,424"),
        ("80 mol% B2O3, 20 mol% Cs2O", "0,5 wt% CuO", "2,816"),
        ("70 mol% B2O3, 30 mol% Cs2O", "0,5 wt% CuO", "3,290"),
    ],
    "Fe": [
        ("90 mol% B2O3, 10 mol% Na2O", "0,5 wt% Fe2O3", "2,054"),
        ("80 mol% B2O3, 20 mol% Na2O", "0,5 wt% Fe2O3", "2,184"),
        ("70 mol% B2O3, 30 mol% Na2O", "0,5 wt% Fe2O3", "2,344"),
    ],
    "V": [
        ("90 mol% B2O3, 10 mol% Na2O", "0,5 wt% V2O5", "2,05"),
        ("80 mol% B2O3, 20 mol% Na2O", "0,5 wt% V2O5", "2,181"),
        ("70 mol% B2O3, 30 mol% Na2O", "0,5 wt% V2O5", "2,334"),
    ],
    "Cr": [
        ("90 mol% B2O3, 10 mol% Na2O", "0,05 wt% Cr2O3", "2,046"),
        ("80 mol% B2O3, 20 mol% Na2O", "0,05 wt% Cr2O3", "2,184"),
        ("70 mol% B2O3, 30 mol% Na2O", "0,05 wt% Cr2O3", "2,335"),
    ],
    "Ti": [
        ("90 mol% B2O3, 10 mol% Na2O", "0,5 wt% TiO2", "2,043"),
        ("70 mol% B2O3, 30 mol% Na2O", "0,5 wt% TiO2", "2,337"),
    ],
}
FIGURE_MARKERS = {
    "1": [0, 0, 0, 0, 0, 0, 0], "2": [0, 0, 0, 0, 0], "3": [0, 0], "4": [0], "5": [0],
    "6": [4, 14], "7": [4, 10], "8": [2, 4], "9": [0, 0, 0],
    "10": [0, 0], "11": [1, 4], "12": [0, 0], "13": [0, 0], "14": [0, 0],
    "15": [0, 0, 0, 0, 0, 0, 0],
}
FIGURE_PAGES = {
    "1": 3, "2": 6, "3": 8, "4": 9, "5": 10, "6": 10, "7": 11, "8": 11,
    "9": 12, "10": 13, "11": 14, "12": 15, "13": 16, "14": 17, "15": 18,
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _grid(document: dict, kind: str) -> dict:
    output = {}
    for element, body in document["species"].items():
        for observation in body["observations"]:
            obs_id = observation["observation_id"]
            if "_ratio" in obs_id:
                table_kind = "ratio"
            elif "_density" in obs_id:
                table_kind = "density"
            else:
                continue
            if table_kind != kind:
                continue
            rows = []
            for point in observation["values"]["points"]:
                rows.append((
                    point["sample"].split("; ")[0],
                    point["added_oxide_as_printed"],
                    point["value_as_published"],
                ))
            key = obs_id.split("_table1_", 1)[1].rsplit(f"_{table_kind}", 1)[0]
            output[key.title()] = rows
    return output


def _assert_render_grid(document: dict, ratio: dict = RATIO_GRID) -> None:
    assert _grid(document, "ratio") == ratio
    assert _grid(document, "density") == DENSITY_GRID


def test_table_1_grid_migrates_all_render_pinned_points_and_conditions() -> None:
    document = _document()
    assert validate_extract_document(
        document, expected_source_id=SOURCE_ID, pre_policy_ids=set(), check_fidelity_match=True
    ) == []
    _assert_render_grid(document)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 52
    assert not migrator.result.validation.hard_issues
    assert sum(row.value.kind.value == "point" for row in observations) == 52
    assert not any(row.value.kind.value == "unavailable" for row in observations)

    for element, grid in RATIO_GRID.items():
        key = element.lower()
        actual = [
            row.value.point
            for row in observations
            if row.observation_id.split("::")[1].endswith(f"_{key}_ratio")
        ]
        expected = [Decimal(value.replace(",", ".")) / Decimal("100") for _, _, value in grid]
        assert sorted(actual) == sorted(expected)

    for element, grid in DENSITY_GRID.items():
        key = element.lower()
        actual = [
            row.value.point
            for row in observations
            if row.observation_id.split("::")[1].endswith(f"_{key}_density")
        ]
        expected = [Decimal(value.replace(",", ".")) * Decimal("1000") for _, _, value in grid]
        assert sorted(actual) == sorted(expected)
    mn_density = next(row for row in observations if "_mn_density::" in row.observation_id)
    assert mn_density.value.point == Decimal("2042")

    for row in observations:
        observation_key = row.observation_id.split("::")[1]
        density = observation_key.endswith("_density")
        assert row.identity.species.phase.value.value == "glass"
        assert row.identity.quantity.is_value
        assert row.identity.quantity.value is (Quantity.DENSITY if density else Quantity.VALENCE_RATIO)
        assert row.point_conditions is not None
        assert row.point_conditions["composition"].state.is_value
        assert row.point_conditions["composition"].locator.page == 4
        assert row.point_conditions["atmosphere_as_printed"].state.value == "air"
        assert row.point_conditions["atmosphere_as_printed"].locator.page == 2
        assert row.point_conditions["quench_state"].state.value == "not_stated"
        assert row.point_conditions["quench_state"].locator.page == 2
        if density:
            assert row.identity.species.formula == "unknown"
            assert row.point_conditions["T_C"].state.is_unknown
            assert "room temperature" in row.point_conditions["T_C"].state.reason
            assert row.identity.temperature_K.is_unknown
            assert row.identity.total_pressure_Pa.is_unknown
            assert row.identity.composition == row.point_conditions["composition"].state
        else:
            element = observation_key.split("_table1_")[1].split("_ratio")[0]
            expected_species = "Cr" if element.lower().startswith("cr") else element.title()
            assert row.identity.species.formula == expected_species
            assert row.identity.temperature_K.is_value
            assert row.identity.temperature_K.value == Decimal("1273.15")
            assert row.point_conditions["T_C"].state.value == Decimal("1000")
            assert row.point_conditions["T_C"].locator.page == 2
            assert row.point_conditions["quench_state"].state.value == "not_stated"


def test_independent_render_grid_detects_mutations_in_both_sources() -> None:
    document = _document()
    changed_render = deepcopy(RATIO_GRID)
    changed_render["Mn"][0] = (*changed_render["Mn"][0][:2], "4,6")
    try:
        _assert_render_grid(document, ratio=changed_render)
    except AssertionError:
        pass
    else:
        raise AssertionError("render-grid mutation did not fail the extract comparison")

    changed_extract = deepcopy(document)
    changed_extract["species"]["Mn"]["observations"][0]["values"]["points"][0]["value_as_published"] = "4,6"
    try:
        _assert_render_grid(changed_extract)
    except AssertionError:
        pass
    else:
        raise AssertionError("extract-cell mutation did not fail the render-grid comparison")


def test_figures_1_to_15_are_deferred_with_rendered_marker_counts() -> None:
    document = _document()
    figures = {
        record["locator"]["figure"]: record
        for body in document["species"].values()
        for record in body["context"]
        if record["type"] == "figure_record"
    }
    assert set(figures) == set(FIGURE_MARKERS)
    for number, record in figures.items():
        values = record["values"]
        assert values["caption"]
        assert record["phase"] == "glass"
        assert values["species"]
        assert values["temperature_as_printed"]
        assert values["axes_as_printed"]
        assert [series["marker_count"] for series in values["series"]] == FIGURE_MARKERS[number], number
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
        assert record["locator"]["page"] == record["locator"]["pdf_page_index"] + 1
        assert record["locator"]["page"] == FIGURE_PAGES[number]


def test_table_1_columns_without_quantity_tokens_remain_in_context() -> None:
    document = _document()
    record = next(
        item
        for item in document["species"]["Cr"]["context"]
        if item.get("observation_id") == "lee_bruckner_1986_table1_untyped_concentrations_and_extinctions"
    )
    values = record["values"]
    groups = {group["species"]: group for group in values["printed_columns_without_store_quantity"]}
    assert set(groups) == {"Mn", "Cu", "Fe", "V", "Cr", "Ti"}
    assert groups["Mn"]["rows"][0]["values_as_printed"] == ["0,0065", "0,87", "133,8"]
    assert groups["Cr"]["rows"][0]["values_as_printed"] == ["0,0018", "0,0116", "1,38", "0,53", "767", "46"]
    assert "no matching quantity token" in values["disposition"]
