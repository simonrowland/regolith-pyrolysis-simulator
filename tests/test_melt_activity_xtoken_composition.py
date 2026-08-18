"""t-691: composition lives on the point, never in the x-token."""

from __future__ import annotations

import re

import pytest

from benchmarks import melt_activity_benchmark as benchmark


_XTOKEN = re.compile(r"_x(\d{4})(?:_|$)")
_TSAPLIN_SAME_TOKEN = "tsaplin2000_a_sio2_x0405_1423"
_YAMAGUCHI_PIN = "yamaguchi1983_a_sio2_liquid_x0400_1373"


def _xtoken_value(point_id: str) -> float:
    match = _XTOKEN.search(point_id)
    assert match is not None, point_id
    return int(match.group(1)) / 1000.0


def _by_id(fixture, point_id: str) -> dict:
    return next(point for point in fixture["points"] if point["id"] == point_id)


def test_future_xtoken_point_without_fields_is_refused():
    offender = {
        "id": "future2000_a_sio2_x0500_1500",
        "composition_id": "future2000_na2o_sio2_x0500",
        "measured": 0.5,
    }
    with pytest.raises(ValueError, match="only in its id"):
        benchmark.assert_xtoken_points_carry_explicit_composition([offender])


def test_xtoken_points_carry_explicit_composition_fields():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    xtoken_points = [
        point
        for point in fixture["points"]
        if benchmark.id_encodes_composition_token(point["id"])
    ]
    assert len(xtoken_points) == 94
    benchmark.assert_xtoken_points_carry_explicit_composition(fixture["points"])
    for point in xtoken_points:
        wt = point["composition_wt_pct"]
        mole_fraction = point["published_mole_fraction"]
        assert set(wt) == {"SiO2", "Na2O"}
        assert set(mole_fraction) == {"SiO2", "Na2O"}
        assert mole_fraction["SiO2"] + mole_fraction["Na2O"] == pytest.approx(1.0)
        catalog = fixture["compositions"][point["composition_id"]]
        assert wt == catalog["composition_wt_pct"]


def test_same_xtoken_value_is_different_composition_across_sources():
    """Same four-digit token is x_SiO2 on Tsaplin and x_Na2O on Yamaguchi.

    The published set has no shared `_xNNNN` digits. The defect is still
    one assertion: token 0.405 on the known Tsaplin point is x_SiO2, and
    the same numeric token under Yamaguchi's convention is x_Na2O.
    """
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    tsaplin = _by_id(fixture, _TSAPLIN_SAME_TOKEN)
    yamaguchi = _by_id(fixture, _YAMAGUCHI_PIN)
    token = _xtoken_value(tsaplin["id"])
    assert token == pytest.approx(0.405)
    yamaguchi_same_token = {"Na2O": token, "SiO2": 1.0 - token}
    assert tsaplin["published_mole_fraction"]["SiO2"] == pytest.approx(token)
    assert tsaplin["published_mole_fraction"]["Na2O"] == pytest.approx(1.0 - token)
    assert yamaguchi["published_mole_fraction"]["Na2O"] == pytest.approx(
        _xtoken_value(yamaguchi["id"])
    )
    assert yamaguchi["published_mole_fraction"]["SiO2"] != pytest.approx(
        _xtoken_value(yamaguchi["id"])
    )
    assert tsaplin["published_mole_fraction"] != yamaguchi_same_token


def test_assigned_sio2_implied_gamma_is_physical():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    refused: list[str] = []
    tsaplin_silica_rich: list[float] = []
    yamaguchi_silica_rich: list[float] = []
    yamaguchi_by_x_na2o: dict[float, list[float]] = {}
    for point in fixture["points"]:
        if point.get("parent_oxide") != "SiO2" or point.get("observable") != "activity":
            continue
        if not point.get("score"):
            continue
        if not (
            point["id"].startswith("tsaplin2000_")
            or point["id"].startswith("yamaguchi1983_")
        ):
            continue
        x_sio2 = float(point["published_mole_fraction"]["SiO2"])
        x_na2o = float(point["published_mole_fraction"]["Na2O"])
        gamma = float(point["measured"]) / x_sio2
        if not (gamma > 0.0):
            refused.append(f"{point['id']} gamma={gamma}")
            continue
        if x_sio2 >= 0.75 and not (0.1 <= gamma <= 10.0):
            refused.append(f"{point['id']} silica-rich gamma={gamma}")
        if point["id"].startswith("tsaplin2000_") and x_sio2 >= 0.75:
            tsaplin_silica_rich.append(gamma)
        if point["id"].startswith("yamaguchi1983_") and x_sio2 >= 0.75:
            yamaguchi_silica_rich.append(gamma)
        if point["id"].startswith("yamaguchi1983_"):
            yamaguchi_by_x_na2o.setdefault(x_na2o, []).append(gamma)
    assert refused == []
    assert tsaplin_silica_rich
    assert max(tsaplin_silica_rich) == pytest.approx(0.86 / 0.805, rel=1e-12)
    assert max(tsaplin_silica_rich) <= 1.07
    assert yamaguchi_silica_rich
    assert max(yamaguchi_silica_rich) == pytest.approx(
        0.82683155722015 / 0.795, rel=1e-12
    )
    assert max(yamaguchi_silica_rich) <= 1.05
    mean_gamma = {
        x_na2o: sum(values) / len(values)
        for x_na2o, values in yamaguchi_by_x_na2o.items()
    }
    ordered = [mean_gamma[key] for key in sorted(mean_gamma)]
    assert ordered == sorted(ordered, reverse=True)

    yamaguchi_rich = _by_id(fixture, "yamaguchi1983_a_sio2_liquid_x0205_1373")
    wrong_x = _xtoken_value(yamaguchi_rich["id"])
    wrong_gamma = float(yamaguchi_rich["measured"]) / wrong_x
    assert wrong_gamma == pytest.approx(4.03, abs=0.01)
    assigned_gamma = float(yamaguchi_rich["measured"]) / float(
        yamaguchi_rich["published_mole_fraction"]["SiO2"]
    )
    assert assigned_gamma == pytest.approx(1.04, abs=0.01)


def test_kume_points_were_not_rewritten():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    kume = [
        point
        for point in fixture["points"]
        if str(point["id"]).startswith("kume2000_")
    ]
    assert len(kume) == 292
    first = _by_id(fixture, "kume2000_s1_a_sio2_1823")
    assert first["id"] == "kume2000_s1_a_sio2_1823"
    assert first["composition_wt_pct"] == {"SiO2": 66.063689, "CaO": 33.936311}
    assert first["published_mole_fraction"] == {"SiO2": 0.645, "CaO": 0.355}
    assert first["measured"] == 1
