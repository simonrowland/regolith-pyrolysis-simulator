"""Migration pins for Shornikov (2001), Figures 2 and 3."""

import copy
import math
import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass
from simulator.battery.identity import quantity_token
from simulator.yaml_cache import YAML12SafeLoader
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2001-mgalumina-silica-vaporization.yaml"
MA_S_ACTIVITY_PARENTS = {
    "shornikov_2001_f2_mgo_ma_s_2050k": 6,
    "shornikov_2001_f2_al2o3_ma_s_2050k": 5,
    "shornikov_2001_f2_sio2_ma_s_2050k": 5,
}
_PIXEL_PAIR = re.compile(r"\(([0-9]+(?:\.[0-9]+)?),([0-9]+(?:\.[0-9]+)?)\)")


def test_shornikov_2001_figure_observations_keep_printed_temperatures() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    observations = {
        observation.observation_id.split("::")[1]: observation
        for observation in migrator.result.observations.values()
    }
    assert len(observations) == 14
    mg = observations["shornikov_2001_f2_mgo_ma_s_2050k"]
    alumina = observations["shornikov_2001_f2_al2o3_ca_s_1933k"]

    # Figure 2 (printed p. 327) captions curves 1 at 2050 K and 5 at 1933 K.
    assert mg.point_conditions["temperature_K"].state.value == Decimal("2050.0")
    assert alumina.point_conditions["temperature_K"].state.value == Decimal("1933.0")
    assert mg.locator.published_page == alumina.locator.published_page == 327
    assert mg.locator.figure == alumina.locator.figure == "2"
    assert not migrator.result.validation.hard_issues


def _load_extract() -> dict:
    return yaml.load(EXTRACT.read_text(encoding="utf-8"), Loader=YAML12SafeLoader)


def _linear_fit(pixels: list[float], values: list[float]) -> tuple[float, float]:
    """Ordinary least squares of value = intercept + slope * pixel."""
    count = float(len(pixels))
    sum_x = math.fsum(pixels)
    sum_y = math.fsum(values)
    sum_xx = math.fsum(pixel * pixel for pixel in pixels)
    sum_xy = math.fsum(pixel * value for pixel, value in zip(pixels, values))
    denominator = count * sum_xx - sum_x * sum_x
    slope = (count * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / count
    return intercept, slope


def _fit_axis(axis: dict, pixel_key: str) -> tuple[float, float, list[float], float]:
    ticks = axis["ticks"]
    assert len(ticks) >= 3
    pixels = [float(tick[pixel_key]) for tick in ticks]
    values = [float(tick["label"]) for tick in ticks]
    intercept, slope = _linear_fit(pixels, values)
    residuals = [
        (value - (intercept + slope * pixel)) / slope
        for pixel, value in zip(pixels, values)
    ]
    rms = math.sqrt(math.fsum(residual * residual for residual in residuals) / len(residuals))
    return intercept, slope, residuals, rms


def _assert_recorded_fit(axis: dict, pixel_key: str) -> tuple[float, float]:
    intercept, slope, residuals, rms = _fit_axis(axis, pixel_key)
    assert rms < 1.0
    assert intercept == pytest.approx(float(axis["intercept"]), abs=1e-6)
    assert slope == pytest.approx(float(axis["slope_per_native_px"]), abs=1e-8)
    assert rms == pytest.approx(float(axis["residual_rms_px"]), abs=1e-3)
    for tick, residual in zip(axis["ticks"], residuals):
        assert residual == pytest.approx(float(tick["residual_px"]), abs=2e-3)
    return intercept, slope


def _legend_by_curve(calibration: dict) -> dict[str, str]:
    legend: dict[str, str] = {}
    for row in calibration["legend"]:
        for curve in row["curves"]:
            key = str(curve)
            assert key not in legend
            legend[key] = row["glyph"]
    return legend


def _iter_observations(document: dict):
    for species in document["species"].values():
        for observation in species.get("observations") or []:
            yield observation


def _find_read(document: dict, read_id: str) -> tuple[dict, dict]:
    found: list[tuple[dict, dict]] = []
    for observation in _iter_observations(document):
        for point in (observation.get("values") or {}).get("points") or []:
            if point.get("audit_read_id") == read_id:
                found.append((observation, point))
    if len(found) != 1:
        raise AssertionError(f"{read_id} is on {len(found)} series")
    return found[0]


def _assert_read_on_printed_series(document: dict, calibration: dict) -> None:
    pinned = calibration["pinned_read"]
    observation, _point = _find_read(document, pinned["audit_read_id"])
    curve = str(observation["values"]["curve_as_printed"])
    symbol = (observation.get("locator") or {}).get("symbol")
    legend = _legend_by_curve(calibration)
    if (
        curve != str(pinned["curve_as_printed"])
        or symbol != pinned["glyph"]
        or symbol != legend.get(curve)
    ):
        raise AssertionError(
            f"{pinned['audit_read_id']} is on curve {curve} glyph {symbol!r}"
        )


def _mean_native_centre(note: str, calibration: dict) -> tuple[float, float]:
    pairs = [(float(x_px), float(y_px)) for x_px, y_px in _PIXEL_PAIR.findall(note)]
    if len(pairs) != 2:
        raise AssertionError(f"expected two pixel reads, found {pairs}")
    scale = float(calibration["native_image_ppi"]) / float(calibration["page_pixel_dpi"])
    return (
        sum(pair[0] for pair in pairs) / 2.0 * scale,
        sum(pair[1] for pair in pairs) / 2.0 * scale,
    )


def test_shornikov_2001_figure_2_axis_calibration_recomputes_pinned_point() -> None:
    document = _load_extract()
    calibration = document["extraction"]["figure_axis_calibration"]
    assert calibration["figure"] == "2"
    assert calibration["page_pixel_dpi"] == 600
    assert calibration["native_image_ppi"] == 72
    assert calibration["glyph_width_native_px"] == {"a": 15, "b": 16, "c": 15}

    slopes = {}
    for panel_name, panel in calibration["panels"].items():
        _assert_recorded_fit(panel["y"], "y_native")
        _assert_recorded_fit(panel["x"], "x_native")
        _intercept, slope = _fit_axis(panel["y"], "y_native")[:2]
        slopes[panel_name] = slope

    legend = _legend_by_curve(calibration)
    digitized = {
        str(curve)
        for row in calibration["legend"]
        if row["digitized"]
        for curve in row["curves"]
    }
    assert digitized == {"1", "4", "7"}
    assert set(legend) == {str(curve) for curve in range(1, 10)}
    point_counts: dict[str, int] = {}
    for observation in _iter_observations(document):
        if str((observation.get("locator") or {}).get("figure")) != "2":
            continue
        values = observation.get("values") or {}
        curve = str(values.get("curve_as_printed", ""))
        points = values.get("points") or []
        if curve in legend and curve not in digitized:
            assert points == []
        if points and curve in digitized:
            assert observation["locator"]["symbol"] == legend[curve]
            assert values["evidence_class"] == "figure_only"
            point_counts[curve] = point_counts.get(curve, 0) + len(points)
    assert point_counts == {"1": 6, "4": 5, "7": 5}

    _assert_read_on_printed_series(document, calibration)
    observation, point = _find_read(
        document, calibration["pinned_read"]["audit_read_id"]
    )
    native_x, native_y = _mean_native_centre(
        point["point_conditions"]["composition"]["locator"]["note"],
        calibration,
    )
    panel = calibration["panels"][calibration["pinned_read"]["panel"]]
    y_intercept, y_slope = _fit_axis(panel["y"], "y_native")[:2]
    x_intercept, x_slope = _fit_axis(panel["x"], "x_native")[:2]
    fitted_log = y_intercept + y_slope * native_y
    half_marker_dex = (
        float(calibration["glyph_width_native_px"]["a"]) / 2.0
    ) * abs(y_slope)
    assert abs(fitted_log - math.log10(float(point["activity"]))) <= half_marker_dex
    fitted_mole_percent = x_intercept + x_slope * native_x
    half_marker_mole_percent = (
        float(calibration["glyph_width_native_px"]["a"]) / 2.0
    ) * abs(x_slope)
    printed = float(point["SiO2_mole_percent_as_printed"])
    assert abs(fitted_mole_percent - printed) <= half_marker_mole_percent
    assert observation["locator"]["symbol"] == "filled upward triangles"


def test_shornikov_2001_moving_pinned_point_to_curve_2_goes_red() -> None:
    document = _load_extract()
    calibration = document["extraction"]["figure_axis_calibration"]
    mutated = copy.deepcopy(document)
    read_id = calibration["pinned_read"]["audit_read_id"]
    source, point = _find_read(mutated, read_id)
    source["values"]["points"].remove(point)
    target = next(
        observation
        for observation in _iter_observations(mutated)
        if str((observation.get("locator") or {}).get("figure")) == "2"
        and str(observation["values"].get("curve_as_printed")) == "2"
    )
    target["values"].setdefault("points", []).append(point)
    target["locator"]["symbol"] = _legend_by_curve(calibration)["2"]
    with pytest.raises(AssertionError):
        _assert_read_on_printed_series(mutated, calibration)


def test_shornikov_2001_mas_activity_points_migrate_with_conditions() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    observations = list(migrator.result.observations.values())
    assert len(observations) == 27
    assert not migrator.result.validation.hard_issues

    migrated = [
        observation
        for observation in observations
        if any(parent in observation.observation_id for parent in MA_S_ACTIVITY_PARENTS)
    ]
    assert len(migrated) == sum(MA_S_ACTIVITY_PARENTS.values())
    for observation in migrated:
        assert observation.value.kind.value == "point"
        assert quantity_token(observation.identity).value == "activity"
        assert observation.evidence.class_.value is EvidenceClass.FIGURE_ONLY
        assert observation.identity.temperature_K.is_value
        assert Decimal(observation.identity.temperature_K.value) == Decimal("2050")
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert Decimal(temperature.state.value) == Decimal("2050")
        assert temperature.locator is not None
        assert temperature.locator.figure == "2"
        composition = observation.point_conditions["composition"]
        assert composition.state.is_value
        assert composition.state.value.amount_basis.value == "mole_fraction"
        assert composition.locator is not None
        assert composition.locator.figure == "2"
