"""Table and isolated-migration pins for Oishi et al. (1986)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator, REPO_ROOT


EXTRACT = REPO_ROOT / "data/literature/extracts/oishi-1986-cu2o-cao-activities.yaml"
TABLE1_RENDER_GRID = {
    "1": ("90.32", "9.61", "0", "0.07"),
    "2": ("88.20", "10.01", "1.64", "0.15"),
    "3": ("86.06", "10.67", "2.78", "0.49"),
    "4": ("85.53", "10.32", "3.89", "0.26"),
    "5": ("83.59", "10.08", "6.01", "0.32"),
    "6": ("82.93", "10.26", "6.29", "0.52"),
    "7": ("81.93", "9.88", "7.81", "0.38"),
    "8": ("80.94", "9.95", "8.84", "0.27"),
    "9": ("79.73", "10.10", "9.55", "0.62"),
    "10": ("79.50", "9.70", "10.37", "0.43"),
    "11": ("79.04", "9.81", "10.85", "0.30"),
    "12**": ("78.34", "9.63", "11.55", "0.48"),
}


def _table1_grid(document: dict) -> dict[str, tuple[str, ...]]:
    grid: dict[str, list[str | None]] = {
        sample: [None, None, None, None] for sample in TABLE1_RENDER_GRID
    }
    for column, species in enumerate(("Cu", "O", "CaO", "MgO")):
        for observation in document["species"][species]["observations"]:
            sample = observation["sample_id"]
            grid[sample][column] = observation["values"]["value"]
    assert all(all(value is not None for value in row) for row in grid.values())
    return {sample: tuple(row) for sample, row in grid.items()}


def test_table1_cells_match_independent_600_dpi_transcription_and_mutations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert _table1_grid(document) == TABLE1_RENDER_GRID

    mutated_grid = deepcopy(TABLE1_RENDER_GRID)
    mutated_grid["1"] = ("90.33", *mutated_grid["1"][1:])
    assert _table1_grid(document) != mutated_grid

    mutated_extract = deepcopy(document)
    mutated_extract["species"]["Cu"]["observations"][0]["values"]["value"] = "90.33"
    with pytest.raises(AssertionError):
        assert _table1_grid(mutated_extract) == TABLE1_RENDER_GRID


def test_table1_component_rows_migrate_as_points_and_keep_unknown_oxygen_honest() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert not result.validation.hard_issues
    observations = list(result.observations.values())
    assert len(observations) == 48
    assert all(observation.value.kind is ValueKind.POINT for observation in observations)
    assert all(observation.identity.temperature_K.value == 1573 for observation in observations)
    printed_logs = ("-3.48", "-3.52", "-3.56", "-3.62", "-3.70", "-3.66", "-3.77", "-3.74", "-3.76", "-3.84", "-3.78", "-3.81")
    for observation in observations:
        oxygen = observation.point_conditions["fO2_log"]
        assert oxygen.state.is_unknown
        assert oxygen.locator is not None
        assert "p° = 101325 Pa = 1 atm" in oxygen.locator.note
        assert not observation.identity.fO2_Pa.is_value
    for formula in ("Cu", "O", "CaO", "MgO"):
        rows = document["species"][formula]["observations"]
        assert [row["values"]["log_fO2"]["value"] for row in rows] == list(printed_logs)
        assert all(row["values"]["log_fO2"]["units"] == "log10(atm)" for row in rows)

    printed_o2 = next(
        row for row in document["species"]["Cu"]["context"]
        if row["observation_id"] == "oishi_1986_table1_printed_oxygen_conditions"
    )
    assert printed_o2["values"]["reference_as_printed"] == "p° = 101325 Pa = 1 atm"
    assert printed_o2["values"]["field"] == "values.log_fO2 on each typed composition row"
    assert Decimal(observations[0].value.point) == Decimal("90.32")


def test_plotted_data_figures_are_deferred_without_numeric_points() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    figures = document["species"]["O"]["context"]
    figures = [row for row in figures if row.get("type") == "figure_record"]
    assert {row["locator"]["figure"] for row in figures} == {"2", "3", "4", "5"}
    assert all(row["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["numeric_points"] == "none" for row in figures)
    assert all(row["values"]["typed_quantity"] for row in figures)
    assert all(
        isinstance(series["marker_count"], int)
        for row in figures
        for series in row["series"]
    )
