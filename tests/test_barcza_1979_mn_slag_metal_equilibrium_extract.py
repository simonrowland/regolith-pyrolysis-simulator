from __future__ import annotations

import csv
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/barcza-1979-mn-slag-metal-equilibrium.yaml"
RENDER_GRID = ROOT / "tests/fixtures/literature/barcza-1979-table6-render-grid.csv"
TABLE_VI_CONTEXT = "barcza_1979_table_vi_context"
TABLE_III_CONTEXT = "barcza_1979_table_iii_context"
TABLE_II_CONTEXT = "barcza_1979_table_ii_context"


def _read_grid() -> tuple[list[str], list[list[str]]]:
    with RENDER_GRID.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    return rows[0], rows[1:]


def _assert_context_grid_matches_render(
    doc: dict, observation_id: str, headers: list[str], grid: list[list[str]], columns: int
) -> None:
    context = next(
        row for row in doc["species"]["SiO2"]["context"]
        if row["observation_id"] == observation_id
    )
    printed_rows = context["values"]["rows_as_printed"]
    assert len(headers) == columns
    assert all(len(rendered) == columns for rendered in grid)
    assert len(printed_rows) == len(grid)
    for extracted, rendered in zip(printed_rows, grid, strict=True):
        assert extracted["cells"] == rendered


def test_barcza_table_vi_is_bound_to_independent_render_transcription() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    headers, grid = _read_grid()
    _assert_context_grid_matches_render(doc, TABLE_VI_CONTEXT, headers, grid, 13)

    changed_grid = deepcopy(grid)
    changed_grid[0][0] = "7,01"
    with pytest.raises(AssertionError):
        _assert_context_grid_matches_render(doc, TABLE_VI_CONTEXT, headers, changed_grid, 13)

    changed_extract = deepcopy(doc)
    context = next(
        row for row in changed_extract["species"]["SiO2"]["context"]
        if row["observation_id"] == TABLE_VI_CONTEXT
    )
    context["values"]["rows_as_printed"][0]["cells"][0] = "7,01"
    with pytest.raises(AssertionError):
        _assert_context_grid_matches_render(changed_extract, TABLE_VI_CONTEXT, headers, grid, 13)


def test_barcza_table_iii_is_bound_to_independent_render_transcription() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    with (ROOT / "tests/fixtures/literature/barcza-1979-table3-render-grid.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        rows = list(csv.reader(stream))
    headers, grid = rows[0], rows[1:]
    _assert_context_grid_matches_render(doc, TABLE_III_CONTEXT, headers, grid, 10)

    changed_grid = deepcopy(grid)
    changed_grid[0][2] = "0,855"
    with pytest.raises(AssertionError):
        _assert_context_grid_matches_render(doc, TABLE_III_CONTEXT, headers, changed_grid, 10)

    changed_extract = deepcopy(doc)
    context = next(
        row for row in changed_extract["species"]["SiO2"]["context"]
        if row["observation_id"] == TABLE_III_CONTEXT
    )
    context["values"]["rows_as_printed"][0]["cells"][2] = "0,855"
    with pytest.raises(AssertionError):
        _assert_context_grid_matches_render(changed_extract, TABLE_III_CONTEXT, headers, grid, 10)


def test_barcza_table_ii_is_bound_to_independent_render_transcription() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    with (ROOT / "tests/fixtures/literature/barcza-1979-table2-render-grid.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        rows = list(csv.reader(stream))
    headers, grid = rows[0], rows[1:]
    _assert_context_grid_matches_render(doc, TABLE_II_CONTEXT, headers, grid, 5)

    changed_grid = deepcopy(grid)
    changed_grid[0][1] = "22,6"
    with pytest.raises(AssertionError):
        _assert_context_grid_matches_render(doc, TABLE_II_CONTEXT, headers, changed_grid, 5)

    changed_extract = deepcopy(doc)
    context = next(
        row for row in changed_extract["species"]["SiO2"]["context"]
        if row["observation_id"] == TABLE_II_CONTEXT
    )
    context["values"]["rows_as_printed"][0]["cells"][1] = "22,6"
    with pytest.raises(AssertionError):
        _assert_context_grid_matches_render(changed_extract, TABLE_II_CONTEXT, headers, grid, 5)


def test_barcza_table_v_values_migrate_with_species_temperature_and_points(tmp_path: Path) -> None:
    from tests.battery.test_migrate import _migrate_real_extract

    result = _migrate_real_extract(tmp_path, EXTRACT.name)
    assert result.validation is not None
    assert result.validation.ok, result.validation.hard_issues
    assert result.validation.hard_issues == (), result.validation.hard_issues
    rows = {
        row.observation_id.split("::", 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == "barcza-1979-mn-slag-metal-equilibrium"
    }
    assert len(rows) == 12

    expected = {
        "barcza_1979_table_v_slag_MnO": ("MnO", "slag", "14.3", True, False),
        "barcza_1979_table_v_slag_CaO": ("CaO", "slag", "32", True, False),
        "barcza_1979_table_v_slag_MgO": ("MgO", "slag", "4.0", True, False),
        "barcza_1979_table_v_slag_SiO2": ("SiO2", "slag", "33.5", True, False),
        "barcza_1979_table_v_slag_Al2O3": ("Al2O3", "slag", "5.0", True, True),
        "barcza_1979_table_v_alloy_Mn": ("Mn", "metal", "84.5", True, False),
        "barcza_1979_table_v_alloy_Fe": ("Fe", "metal", "7.5", True, False),
        "barcza_1979_table_v_alloy_Si": ("Si", "metal", "8.0", True, False),
        "barcza_1979_table_v_slag_MnO_activity": ("MnO", "slag", "0.14", False, True),
        "barcza_1979_table_v_slag_SiO2_activity": ("SiO2", "slag", "0.10", False, True),
        "barcza_1979_table_v_alloy_Mn_activity": ("Mn", "metal", "0.85", False, True),
        "barcza_1979_table_v_alloy_Si_activity": ("Si", "metal", "0.05", False, True),
    }
    for observation_id, (formula, role, magnitude, temperature_applies, approximate) in expected.items():
        row = rows[observation_id]
        assert row.value.point == Decimal(magnitude)
        assert row.value.approximate is approximate
        assert row.identity.species.formula == formula
        if observation_id.endswith("_activity"):
            assert row.identity.phase_role is None
            assert row.identity.species.phase.value.value == "l"
        else:
            assert row.identity.phase_role.value.value == role
        if temperature_applies:
            assert row.identity.temperature_K.value == Decimal("1773.15")
            condition = row.point_conditions["temperature_K"]
            assert condition.state.value == Decimal("1773.15")
            assert condition.locator.page == 10
            assert condition.locator.pdf_page_index == 9
        else:
            assert row.identity.temperature_K.value is None
        assert not any(
            notice.kind.value == "source_value_outside_physical_range"
            for notice in row.notices
        )
