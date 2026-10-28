from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "literature"
    / "extracts"
    / "shornikov-2003-mullite-thermodynamics.yaml"
)


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block["observations"]
    }


def _assert_render_pins(document: dict) -> None:
    rows = _observations(document)
    assert rows[
        "shornikov_2003_mullite_thermodynamics_p002_t01_r02_c04"
    ]["values"]["value_as_printed"] == "– 8.0"
    assert rows[
        "shornikov_2003_mullite_thermodynamics_p002_t01_r05_c04"
    ]["values"]["value_as_printed"] == "22.2 ± 4.4"
    assert rows[
        "shornikov_2003_mullite_thermodynamics_p002_t01_r31_c04"
    ]["values"]["value_as_printed"] == "11.05 ± 0.08"
    assert rows[
        "shornikov_2003_mullite_thermodynamics_p004_t02_r08_c03"
    ]["values"]["value_as_printed"] == "12.9 ± 0.8"
    assert rows[
        "shornikov_2003_mullite_thermodynamics_text_2100k_gibbs_range"
    ]["values"]["range"] == ["-10", "-5"]
    sentence = rows[
        "shornikov_2003_mullite_thermodynamics_text_ref66_melt_gibbs_extrapolation"
    ]
    assert sentence["values"]["value_as_printed"] == "–12"
    assert sentence["values"]["value"] == "-12"
    assert sentence["phase"] == "l"
    assert "T_K" not in sentence["values"]
    assert sentence["point_conditions"]["temperature_K"]["state"]["tag"] == "unknown"
    assert "temperature of fusion of mullite" in sentence["locator"]["note"]


def _assert_figure_pins(document: dict) -> None:
    rows = _observations(document)
    figure_experiments = [
        experiment
        for experiment in document["experiments"]
        if experiment["experiment_id"].startswith(
            "shornikov_2003_mullite_figure1_series_"
        )
    ]
    assert len(figure_experiments) == 23
    for experiment in figure_experiments:
        assert "table" not in experiment["locator"]
        for condition in experiment.get("conditions", {}).values():
            assert "table" not in condition["locator"]
    expected = {
        1: ("-1.8", 1070.64, [662, 503]),
        4: ("-5.0", 1825.19, [1770, 840]),
        6: ("-1.0e+01", 1872.86, [1840, 1376]),
        20: ("-4.8", 1823.15, [1767, 817]),
        23: ("-5.1", 1774.80, [1696, 854]),
    }
    for series, (value, first_temperature, first_page_pixel) in expected.items():
        row = rows[
            f"shornikov_2003_mullite_thermodynamics_fig1_marker_{series:02d}"
        ]
        assert row["values"]["value"] == value
        first_read = row["values"]["figure_read"]["reads"][0]
        assert first_read["temperature_K"] == first_temperature
        assert first_read["page_pixel_xy"] == first_page_pixel
        assert len(row["values"]["figure_read"]["reads"]) == 2
        assert row["point_conditions"]["temperature_K"]["state"]["tag"] == "value"
        assert row["point_conditions"]["temperature_K"]["locator"]["figure"] == "1"
    for row_id, row in rows.items():
        if "_fig1_" not in row_id:
            continue
        assert "table" not in row["locator"]
        assert "table" not in row["point_conditions"]["temperature_K"]["locator"]


def test_shornikov_2003_mullite_rows_and_render_pins() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _observations(document)
    assert len(rows) == 94
    assert sum("_p002_t01_" in row_id and row_id.endswith("_c04") for row_id in rows) == 31
    assert sum("_p002_t01_" in row_id and row_id.endswith("_c05") for row_id in rows) == 17
    assert sum("_p004_t02_" in row_id and row_id.endswith("_c03") for row_id in rows) == 7
    assert sum("_p004_t02_" in row_id and row_id.endswith("_c04") for row_id in rows) == 7
    assert sum("_p004_t02_" in row_id and row_id.endswith("_c05") for row_id in rows) == 7
    assert sum("_fig1_marker_" in row_id for row_id in rows) == 10
    assert sum("_fig1_curve_" in row_id for row_id in rows) == 13
    _assert_render_pins(document)
    _assert_figure_pins(document)


def test_shornikov_2003_render_pins_reject_mutated_cells() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)
    mutated = copy.deepcopy(document)
    rows = _observations(mutated)
    rows[
        "shornikov_2003_mullite_thermodynamics_p002_t01_r02_c04"
    ]["values"]["value_as_printed"] = "8.0"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)
    _assert_figure_pins(document)
    mutated_figure = copy.deepcopy(document)
    rows = _observations(mutated_figure)
    rows[
        "shornikov_2003_mullite_thermodynamics_fig1_marker_01"
    ]["values"]["value"] = "-1.7"
    with pytest.raises(AssertionError):
        _assert_figure_pins(mutated_figure)
