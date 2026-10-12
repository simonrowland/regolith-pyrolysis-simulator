from __future__ import annotations

from pathlib import Path

import yaml


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "literature"
    / "extracts"
    / "floss-1998-hibonite-evaporation-residues.yaml"
)


def _table_row(context: dict, run_id: str) -> dict:
    return next(
        row
        for row in context["rows"]
        if row.get("run_id", row.get("sample")) == run_id
    )


def test_corrected_trace_element_cells_are_pinned() -> None:
    doc = yaml.safe_load(EXTRACT.read_text())
    context = next(
        item["values"]
        for item in doc["species"]["SiO2"]["context"]
        if item["observation_id"]
        == "floss_1998_tables3_4_glass_and_phase_compositions"
    )

    table6 = context["table6_SIMs_trace_element_ppm_context"]
    mh16 = _table_row(table6, "MH-16")
    assert mh16["values"][table6["elements"].index("Nd")] == "18496 ± 7363"

    table7 = context["table7_INAA_trace_element_context"]
    mh8 = _table_row(table7, "MH-8")
    co_column = table7["elements"].index("Co_ppm")
    assert mh8["values"][co_column] == "11"
    assert "Co_ppm" in mh8["high_error_cells"]


def test_table7_italicized_high_error_cells_are_pinned() -> None:
    doc = yaml.safe_load(EXTRACT.read_text())
    context = next(
        item["values"]
        for item in doc["species"]["SiO2"]["context"]
        if item["observation_id"]
        == "floss_1998_tables3_4_glass_and_phase_compositions"
    )
    table7 = context["table7_INAA_trace_element_context"]
    expected = {
        "MH-1": {"Co_ppm", "Ce_ppm"},
        "MH-2": {"Ce_ppm"},
        "MH-5": set(),
        "MH-9": {"Mn_ppm", "Co_ppm", "Ce_ppm"},
        "MH-3": {"Ce_ppm"},
        "MH-10": {"Mn_ppm", "Co_ppm", "Ce_ppm"},
        "MH-6": {"Mn_ppm"},
        "MH-8": {"Co_ppm", "Sc_ppm", "Ce_ppm"},
        "MH-7": {"Sc_ppm", "Ce_ppm"},
    }
    for run_id, cells in expected.items():
        assert set(_table_row(table7, run_id).get("high_error_cells", [])) == cells

    mh6 = _table_row(table7, "MH-6")
    assert mh6["values"][table7["elements"].index("Sc_ppm")] == "0.75"
    assert "Sc_ppm" not in mh6["high_error_cells"]
