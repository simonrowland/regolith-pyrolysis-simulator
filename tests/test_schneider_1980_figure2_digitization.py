"""Pins Schneider et al. Figure 2's measured high-temperature markers."""

from __future__ import annotations

from pathlib import Path

import yaml


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/kems-212-schneider-1980-nbsir-80-2059.yaml"
)


def test_schneider_figure2_digitized_markers_match_pdf_page_16_render():
    """Three marker centers fall in 2116–2160 K on PDF p. 16, Figure 2."""
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    figure = next(
        item
        for item in doc["species"]["K"]["context"]
        if item["observation_id"] == "schneider_1980_kalsio4_figure2_pressure"
    )
    values = figure["values"]
    series = next(
        item
        for item in doc["species"]["K"]["observations"]
        if item["observation_id"]
        == "schneider_1980_k_psat_figure2_high_temperature_digitized"
    )
    points = series["values"]["points"]

    assert len(points) == 3
    assert series["values"]["method_class"] == "figure_only"
    assert series["values"]["admission_status"] == "typed_refusal"
    assert "pointwise full melt composition" in series["values"]["refusal_reason"]
    assert series["point_conditions"]["composition"]["state"]["tag"] == "unknown"
    assert values["digitized_marker_count"] == 3
    assert values["digitized_temperature_window_K"] == [2116, 2160]
    assert series["locator"]["pdf_page_index"] == 15
    assert values["printed_bulk_K2O_composition_ranges_wt_pct"] == [
        [28.9, 28.7],
        [26.1, 25.2],
        [20.6, 20.1],
        [14.9, 14.5],
        [12.5, 12.2],
        [11.6, 11.4],
        [10.8, 10.7],
    ]
    assert [
        (point["series"], point["T_K"], point["p_atm"])
        for point in points
    ] == [
        ("D", 2157, 3.0e-5),
        ("D", 2116, 1.9e-5),
        ("G", 2129, 1.0e-5),
    ]
    assert all(2116 <= point["T_K"] <= 2160 for point in points)


def test_schneider_keeps_unreported_apparatus_pressure_unknown():
    """The report does not state the KEMS chamber/background pressure."""
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    experiment = next(
        item
        for item in doc["experiments"]
        if item["experiment_id"] == "schneider-1980-kalsio4-vaporization"
    )

    pressure = experiment["pressure_environment"]["total_pressure_Pa"]["state"]
    assert pressure == {"tag": "unknown", "reason": "not_published"}
