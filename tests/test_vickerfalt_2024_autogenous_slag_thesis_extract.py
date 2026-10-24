from __future__ import annotations

import csv
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SID = "vickerfalt-2024-autogenous-slag-thesis"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
FIXTURE_DIR = ROOT / "tests/fixtures/literature"
GRID_NAMES = {
    "3": "vickerfalt-2024-autogenous-table3-render-grid.csv",
    "4": "vickerfalt-2024-autogenous-table4-render-grid.csv",
    "5": "vickerfalt-2024-autogenous-table5-render-grid.csv",
    "6": "vickerfalt-2024-autogenous-table6-render-grid.csv",
}
TABLE4_SLAG_COLUMNS = (
    "Al2O3 wt%",
    "CaO wt%",
    "MgO wt%",
    "P2O5 wt%",
    "SiO2 wt%",
    "TiO2 wt%",
    "V2O3 wt%",
    "FeO wt%",
)
LIQUID_COLUMNS = (
    "MgO wt%",
    "Al2O3 wt%",
    "SiO2 wt%",
    "CaO wt%",
    "TiO2 wt%",
    "V2O3 wt%",
    "FeO wt%",
)


def _read_grid(table: str) -> list[dict[str, str]]:
    with (FIXTURE_DIR / GRID_NAMES[table]).open(
        encoding="utf-8", newline=""
    ) as stream:
        return list(csv.DictReader(stream))


def _read_extract() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _obs_by_id(doc: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in doc["species"]["SiO2"]["observations"]
    }


def _context_by_id(doc: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in doc["species"]["SiO2"]["context"]
    }


def _sample_key(sample: str) -> str:
    return sample.lower().replace(".", "_")


def _cell_values(observation: dict) -> list[str] | str:
    values = observation["values"]
    if "range" in values:
        return [str(value) for value in values["range"]]
    return str(values["component_amount"])


def _assert_component_cell(
    observations: dict[str, dict],
    *,
    table: str,
    sample: str,
    phase_role: str,
    formula: str,
    printed: str,
) -> None:
    observation_id = (
        f"vickerfalt_thesis_table{table}_{_sample_key(sample)}_"
        f"{phase_role}_{formula.lower()}"
    )
    observation = observations[observation_id]
    assert observation["type"] == "composition_series"
    assert observation["values"]["quantity"] == "equilibrium_phase_composition"
    assert observation["values"]["formula"] == formula
    assert observation["values"]["phase_label"] == phase_role
    if "-" in printed:
        low, high = printed.split("-", 1)
        assert _cell_values(observation) == [low, high]
    else:
        assert _cell_values(observation) == printed


def _assert_context_rows(contexts: dict[str, dict], observation_id: str, expected: list) -> None:
    assert contexts[observation_id]["values"]["rows_as_printed"] == expected


def _assert_table_matches_render(doc: dict, table: str, grid: list[dict[str, str]]) -> None:
    observations = _obs_by_id(doc)
    contexts = _context_by_id(doc)

    if table == "4":
        for source_row in grid:
            sample = source_row["Sample"]
            for column in TABLE4_SLAG_COLUMNS:
                _assert_component_cell(
                    observations,
                    table="4",
                    sample=sample,
                    phase_role="slag",
                    formula=column.split()[0],
                    printed=source_row[column],
                )
            for formula, column in (("P", "Iron P wt%"), ("V", "Iron V wt%")):
                printed = source_row[column]
                observation_id = (
                    f"vickerfalt_thesis_table4_{_sample_key(sample)}_metal_{formula.lower()}"
                )
                if printed.startswith("<"):
                    assert observation_id not in observations
                else:
                    _assert_component_cell(
                        observations,
                        table="4",
                        sample=sample,
                        phase_role="metal",
                        formula=formula,
                        printed=printed,
                    )
            for formula, column in (("P", "LP"), ("V", "LV")):
                printed = source_row[column]
                observation_id = (
                    f"vickerfalt_thesis_table4_{_sample_key(sample)}_"
                    f"{formula.lower()}_distribution_ratio"
                )
                if printed == "-":
                    assert observation_id not in observations
                else:
                    ratio = observations[observation_id]
                    assert ratio["values"]["quantity"] == "distribution_ratio"
                    assert ratio["values"]["distribution_ratio"] == printed
                    assert ratio["values"]["phase_label"] == "slag"
                    assert ratio["values"]["counter_phase_label"] == "metal"

        process_rows = [
            [row["Sample"], row["Reduction degree %"], row["FeO(calc.) wt%"]]
            for row in grid
        ]
        _assert_context_rows(
            contexts,
            "vickerfalt_thesis_table4_process_and_calculated_context",
            process_rows,
        )
        detection_rows = [
            [row["Sample"], row["Iron V wt%"], row["LV"]]
            for row in grid
            if row["Iron V wt%"].startswith("<")
        ]
        _assert_context_rows(
            contexts,
            "vickerfalt_thesis_table4_v_below_detection_context",
            detection_rows,
        )
        return

    liquid_rows = [row for row in grid if row["Phase block"] == "Liquid"]
    for source_row in liquid_rows:
        for column in LIQUID_COLUMNS:
            _assert_component_cell(
                observations,
                table=table,
                sample=source_row["Sample"],
                phase_role="slag",
                formula=column.split()[0],
                printed=source_row[column],
            )

    if table == "5":
        columns = [
            "Sample",
            "Red %",
            "MgO wt%",
            "Al2O3 wt%",
            "SiO2 wt%",
            "CaO wt%",
            "TiO2 wt%",
            "V2O3 wt%",
            "FeO wt%",
        ]
        for phase, context_id in (
            ("MW", "vickerfalt_thesis_table5_mw_solid_phase_compositions_context"),
            ("Spinel", "vickerfalt_thesis_table5_spinel_solid_phase_compositions_context"),
        ):
            expected = [
                [row[column] for column in columns]
                for row in grid
                if row["Phase block"] == phase
            ]
            _assert_context_rows(contexts, context_id, expected)
        return

    for phase, blocks, context_id in (
        (
            "magnesiowustite",
            {"MW small", "MW large surface", "MW large center"},
            "vickerfalt_thesis_table6_magnesiowustite_solid_compositions_context",
        ),
        ("spinel", {"Spinel"}, "vickerfalt_thesis_table6_spinel_solid_compositions_context"),
    ):
        expected = [
            [
                row["Sample"],
                row["FeOtot wt%"],
                row["Phase block"],
                *[row[column] for column in LIQUID_COLUMNS],
            ]
            for row in grid
            if row["Phase block"] in blocks
        ]
        _assert_context_rows(contexts, context_id, expected)


@pytest.mark.parametrize("table", ("4", "5", "6"))
def test_tables_4_to_6_match_independent_600_dpi_render_grids(table: str) -> None:
    doc = _read_extract()
    grid = _read_grid(table)
    _assert_table_matches_render(doc, table, grid)

    changed_grid = deepcopy(grid)
    changed_grid[0]["Al2O3 wt%"] = "999"
    with pytest.raises(AssertionError):
        _assert_table_matches_render(doc, table, changed_grid)

    changed_extract = deepcopy(doc)
    sample = (
        "S1.1"
        if table == "4"
        else next(row["Sample"] for row in grid if row["Phase block"] == "Liquid")
    )
    observation = _obs_by_id(changed_extract)[
        f"vickerfalt_thesis_table{table}_{_sample_key(sample)}_slag_al2o3"
    ]
    if "range" in observation["values"]:
        observation["values"]["range"][0] = "999"
    else:
        observation["values"]["component_amount"] = "999"
    with pytest.raises(AssertionError):
        _assert_table_matches_render(changed_extract, table, grid)


def test_deferred_figures_and_unavailable_tables_stay_in_context() -> None:
    doc = _read_extract()
    species = doc["species"]
    assert set(species) == {"SiO2"}
    context = species["SiO2"]["context"]
    figure_rows = [row for row in context if row["type"] == "figure_only"]
    assert {row["locator"]["figure"] for row in figure_rows} == {
        str(number) for number in range(26, 35)
    }
    for row in figure_rows:
        values = row["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["axes_as_printed"]
        assert values["phase"]
        assert values["temperature_as_printed"]
        assert values["series"]
        assert "points" not in values

    assert any(
        row["observation_id"] == "vickerfalt_thesis_table9_context"
        and row["values"]["published_source_id"] == "vickerfalt-2020-mgo-v2o3"
        for row in context
    )
    assert any(
        row["observation_id"] == "vickerfalt_thesis_table14_context"
        and row["values"]["published_source_id"] == "vickerfalt-2024-al-si-v-phase-diagram"
        for row in context
    )
    table1 = next(
        row for row in context
        if row["observation_id"] == "vickerfalt_thesis_table1_quoted_hematite_pellet_context"
    )
    assert table1["locator"]["page"] == 18
    assert table1["locator"]["pdf_page_index"] == 17
    assert table1["values"]["rows_as_printed"] == [
        ["Commercial hematite pellets [21]", "67.8", "0.65", "0.2", "0.8", "0.06", "0.9", "0.18", "0.2", "0.07"]
    ]
    table2 = next(
        row for row in context
        if row["observation_id"] == "vickerfalt_thesis_table2_initial_synthetic_slag_compositions_context"
    )
    assert table2["locator"]["page"] == 19
    assert table2["locator"]["pdf_page_index"] == 18
    assert table2["locator"]["published_page"] == 8


def test_table3_context_matches_independent_600_dpi_render_grid() -> None:
    doc = _read_extract()
    grid = _read_grid("3")
    context = _context_by_id(doc)[
        "vickerfalt_thesis_table3_foaming_phase_compositions_context"
    ]
    assert context["locator"]["page"] == 22
    assert context["locator"]["pdf_page_index"] == 21
    assert context["values"]["rows_as_printed"] == [
        list(row.values()) for row in grid
    ]
    assert context["values"]["MoO2_content_in_liquid_as_printed"] == "around 10 wt%"

    changed_grid = deepcopy(grid)
    changed_grid[0]["Liquid FeO wt%"] = "999"
    with pytest.raises(AssertionError):
        assert context["values"]["rows_as_printed"] == [
            list(row.values()) for row in changed_grid
        ]

    changed_extract = deepcopy(doc)
    changed_context = _context_by_id(changed_extract)[
        "vickerfalt_thesis_table3_foaming_phase_compositions_context"
    ]
    changed_context["values"]["rows_as_printed"][0][7] = "999"
    with pytest.raises(AssertionError):
        assert changed_context["values"]["rows_as_printed"] == [
            list(row.values()) for row in grid
        ]


def test_phase_rows_migrate_as_printed_values_with_species_and_conditions(tmp_path: Path) -> None:
    from tests.battery.test_migrate import _migrate_real_extract

    result = _migrate_real_extract(tmp_path, EXTRACT.name)
    assert result.validation is not None
    assert result.validation.ok, result.validation.hard_issues
    assert result.validation.hard_issues == (), result.validation.hard_issues

    source_rows = {
        row["observation_id"]: row
        for row in _read_extract()["species"]["SiO2"]["observations"]
    }
    migrated = {
        row.observation_id.split("::", 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == SID
    }
    assert len(source_rows) == len(migrated) == 150
    assert migrated.keys() == source_rows.keys()

    for observation_id, source in source_rows.items():
        values = source["values"]
        row = migrated[observation_id]
        expected_formula = values["formula"]
        assert row.identity.species.formula == expected_formula
        assert row.identity.temperature_K.value == Decimal("1873")
        assert row.identity.phase_role.value.value == values["phase_label"]
        assert row.point_conditions["temperature_K"].state.value == Decimal("1873")
        assert row.point_conditions["gas_mix"].state.value == "argon"
        expected_locator_page = 20 if source["experiment"].endswith("synthetic_slag") else 18
        assert row.point_conditions["temperature_K"].locator.page == expected_locator_page
        assert row.point_conditions["temperature_K"].locator.pdf_page_index == expected_locator_page - 1

        if values["quantity"] == "distribution_ratio":
            assert row.value.kind.value == "point"
            assert row.value.point == Decimal(values["distribution_ratio"])
            assert row.identity.counter_phase_role.value.value == values["counter_phase_label"]
        elif "range" in values:
            assert row.value.kind.value == "interval"
            assert row.value.interval_low == Decimal(values["range"][0])
            assert row.value.interval_high == Decimal(values["range"][1])
        else:
            assert row.value.kind.value == "point"
            assert row.value.point == Decimal(values["component_amount"])

        assert not any(
            notice.kind.value == "source_value_outside_physical_range"
            for notice in row.notices
        )

    for observation_id, row in migrated.items():
        if observation_id.endswith("_distribution_ratio"):
            assert row.identity.phase_role.value.value == "slag"
            assert row.identity.counter_phase_role.value.value == "metal"
