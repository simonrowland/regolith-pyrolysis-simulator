"""Pins Schneider et al. Figure 2's measured high-temperature markers.

The expected temperatures and pressures are computed from tick and marker
centres measured on the 500 dpi render of PDF page 16. They are not a copy
of the extract's stored tuples. A pressure snapped to the 10^-5 gridline
does not satisfy the calibration.
"""

from __future__ import annotations

import math
from pathlib import Path

import yaml


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/kems-212-schneider-1980-nbsir-80-2059.yaml"
)

# Stem centroids of the labeled 10^4/T ticks, 500 dpi render of PDF page 16.
_X_TICK_5 = 891.64
_X_TICK_6 = 2169.14
_X_TICK_7 = 3449.84

# The 10^-5 gridline. Marker y increases as pressure falls.
_Y_TICK_1E5 = 2163.25

# Marker ink centroids at 500 dpi, and the printed log ticks that bracket
# each marker's y. The hot triangle excludes the pressure-axis stroke it touches.
_MARKERS = (
    {
        "series": "F",
        "k2o": [11.6, 11.4],
        "x": 444.27,
        "y": 1547.10,
        "y0": 1402.25,
        "p0": 4.0e-5,
        "y1": 1561.00,
        "p1": 3.0e-5,
    },
    {
        "series": "F",
        "k2o": [11.6, 11.4],
        "x": 550.65,
        "y": 1773.22,
        "y0": 1561.00,
        "p0": 3.0e-5,
        "y1": 1783.83,
        "p1": 2.0e-5,
    },
    {
        "series": "G",
        "k2o": [10.8, 10.7],
        "x": 543.46,
        "y": 2385.50,
        "y0": 2360.03,
        "p0": 7.0e-6,
        "y1": 2443.63,
        "p1": 6.0e-6,
    },
)

_WINDOW_K = (2116.0, 2160.0)


def _inv_temperature(x_500: float) -> float:
    """10^4/T from the labeled 5.0 and 7.0 ticks. 6.0 is a check, not an anchor."""
    scale = (_X_TICK_7 - _X_TICK_5) / 2.0
    return 5.0 + (x_500 - _X_TICK_5) / scale


def _pressure_atm(marker: dict[str, float]) -> float:
    """Log-pressure interpolated between the two printed ticks around the marker."""
    fraction = (marker["y"] - marker["y0"]) / (marker["y1"] - marker["y0"])
    log_p = math.log10(marker["p0"]) + fraction * (
        math.log10(marker["p1"]) - math.log10(marker["p0"])
    )
    return 10.0 ** log_p


def test_schneider_figure2_digitized_markers_match_pdf_page_16_render():
    """Marker centres on PDF p. 16 follow the printed-tick calibration."""
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
    sample = next(
        item
        for item in doc["fidelity_samples"]
        if item["observation_id"]
        == "schneider_1980_k_psat_figure2_high_temperature_digitized"
    )

    # The middle labeled tick lies on the 5.0–7.0 line. A shifted axis fails.
    scale = (_X_TICK_7 - _X_TICK_5) / 2.0
    assert abs(_X_TICK_6 - (_X_TICK_5 + scale)) < 2.0

    assert len(points) == 3
    assert series["values"]["method_class"] == "figure_only"
    assert series["values"]["admission_status"] == "typed_refusal"
    assert "pointwise full melt composition" in series["values"]["refusal_reason"]
    assert series["point_conditions"]["composition"]["state"]["tag"] == "unknown"
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

    calibrated = []
    for marker, point in zip(_MARKERS, points, strict=True):
        inv_t = _inv_temperature(marker["x"])
        temperature_k = 10000.0 / inv_t
        pressure_atm = _pressure_atm(marker)
        calibrated.append(temperature_k)
        assert point["series"] == marker["series"]
        assert point["bulk_K2O_composition_range_wt_pct"] == marker["k2o"]
        assert abs(point["T_K"] - temperature_k) <= 0.06
        assert abs(point["invT_times_1e4_K_inv"] - inv_t) <= 0.001
        assert abs(point["p_atm"] - pressure_atm) / pressure_atm <= 0.003
        pixel = point["marker_pixel_250dpi"]
        assert abs(pixel[0] - marker["x"] / 2.0) <= 0.01
        assert abs(pixel[1] - marker["y"] / 2.0) <= 0.01

    # Centres, not a rounding into the 2116–2160 K window.
    assert _WINDOW_K[0] <= calibrated[0] <= _WINDOW_K[1]
    assert calibrated[1] < _WINDOW_K[0]
    assert calibrated[2] < _WINDOW_K[0]
    assert points[1]["T_K"] < _WINDOW_K[0]
    assert points[2]["T_K"] < _WINDOW_K[0]
    in_window = [
        temperature
        for temperature in calibrated
        if _WINDOW_K[0] <= temperature <= _WINDOW_K[1]
    ]
    assert values["digitized_marker_count"] == len(in_window) == 1
    assert series["T_range_K"] == [points[0]["T_K"], points[0]["T_K"]]
    assert abs(series["T_range_K"][0] - in_window[0]) <= 0.06

    # The series-G centre is below the 10^-5 tick. The gridline value fails.
    g_point = points[2]
    assert _MARKERS[2]["y"] - _Y_TICK_1E5 > 100.0
    assert abs(math.log10(g_point["p_atm"]) + 5.0) > 0.05
    assert g_point["p_atm"] != 1.0e-5

    assert sample["value"] == points


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
