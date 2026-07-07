from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.interpolation_uncertainty import (
    CACHE_DISTANCE,
    FEASIBLE,
    INFEASIBLE,
    INDETERMINATE,
    NONLINEARITY_GENERAL,
    THRESHOLD_STRADDLE,
    build_interpolation_uncertainty_vector,
    feasibility_verdict_from_reduced_real_cache,
    feasibility_verdict_with_interpolation_uncertainty,
    ranked_table_drain,
)
from simulator.optimize.physics import GateMargin, ThresholdSpec


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_vapor_pressures() -> dict:
    return yaml.safe_load((DATA_DIR / "vapor_pressures.yaml").read_text())


def _key(
    label: str,
    *,
    temperature_K: float,
    composition: tuple[tuple[str, float], ...] = (("FeO", 0.2), ("SiO2", 0.8)),
) -> dict:
    return {
        "label": label,
        "composition_mol_fraction": list(composition),
        "controls": {
            "T_K": temperature_K,
            "pressure_bar": 0.01,
            "pO2_bar": 1.0e-6,
        },
    }


def _neighbor(
    label: str,
    *,
    temperature_K: float,
    liquid_fraction: float,
    sio_pa: float = 10.0,
    distance: float | None = None,
    composition: tuple[tuple[str, float], ...] = (("FeO", 0.2), ("SiO2", 0.8)),
) -> dict:
    row = {
        "key_hash": label,
        "key": _key(label, temperature_K=temperature_K, composition=composition),
        "payload": {
            "equilibrium_result": {
                "liquid_fraction": liquid_fraction,
                "vapor_pressures_Pa": {"SiO": sio_pa},
            }
        },
    }
    if distance is not None:
        row["interpolation_distance"] = distance
    return row


def _margin(value: float, *, feasible: bool = True) -> GateMargin:
    return GateMargin(
        gate="yield",
        feasible=feasible,
        margin=value,
        threshold=ThresholdSpec(
            id="yield_min",
            value=0.0,
            units="fraction",
            source="synthetic",
            source_ref="exact synthetic margin",
        ),
        observed=value,
        detail="synthetic exact margin",
    )


def test_uncertainty_vector_reports_float64_cache_distance_component() -> None:
    vector = build_interpolation_uncertainty_vector(
        _key("query", temperature_K=1500.0),
        [
            _neighbor("low", temperature_K=1490.0, liquid_fraction=0.7, distance=0.01),
            _neighbor("high", temperature_K=1510.0, liquid_fraction=0.8, distance=0.025),
        ],
    )

    assert vector["dtype"] == "float64"
    assert vector["vector"][CACHE_DISTANCE] == pytest.approx(0.025)
    assert vector["components"][CACHE_DISTANCE]["status"] == "available"
    assert vector["vector"][THRESHOLD_STRADDLE] == pytest.approx(0.0)
    assert vector["components"][NONLINEARITY_GENERAL]["status"] == "insufficient_neighborhood"


def test_threshold_straddle_is_categorical_non_interpolable_near_liquidus() -> None:
    vector = build_interpolation_uncertainty_vector(
        _key("query", temperature_K=1500.0),
        [
            _neighbor("mushy", temperature_K=1490.0, liquid_fraction=0.95),
            _neighbor("liquid", temperature_K=1510.0, liquid_fraction=1.0),
        ],
    )

    threshold = vector["components"][THRESHOLD_STRADDLE]
    assert threshold["status"] == "non_interpolable"
    assert threshold["value"] == pytest.approx(1.0)
    assert threshold["surfaces"][0]["surface"] == "liquidus_liquid_fraction_proxy"


@pytest.mark.parametrize(
    ("surface", "liquid_fraction"),
    (
        ("liquidus_liquid_fraction_proxy", 0.98),
        ("solidus_liquid_fraction_proxy", 0.02),
    ),
)
def test_threshold_touch_is_categorical_non_interpolable(
    surface: str,
    liquid_fraction: float,
) -> None:
    vector = build_interpolation_uncertainty_vector(
        _key("query", temperature_K=1500.0),
        [
            _neighbor("touch", temperature_K=1490.0, liquid_fraction=liquid_fraction),
            _neighbor("clear", temperature_K=1510.0, liquid_fraction=0.5),
        ],
    )

    threshold = vector["components"][THRESHOLD_STRADDLE]
    assert threshold["status"] == "non_interpolable"
    assert [item["surface"] for item in threshold["surfaces"]] == [surface]


@pytest.mark.parametrize("alkali", ("Na", "K"))
def test_alkali_boiling_threshold_touch_uses_yaml_nist_rows(alkali: str) -> None:
    row = _load_vapor_pressures()["metals"][alkali]
    assert "NIST Chemistry WebBook SRD 69" in row["pure_component_antoine"]["source"]
    boiling_K = float(row["boiling_point_C"]) + 273.15
    vector = build_interpolation_uncertainty_vector(
        _key(
            "query",
            temperature_K=boiling_K,
            composition=((f"{alkali}2O", 0.1), ("SiO2", 0.9)),
        ),
        [
            _neighbor(
                "touch",
                temperature_K=boiling_K,
                liquid_fraction=0.5,
                composition=((f"{alkali}2O", 0.1), ("SiO2", 0.9)),
            ),
            _neighbor(
                "clear",
                temperature_K=boiling_K + 25.0,
                liquid_fraction=0.5,
                composition=((f"{alkali}2O", 0.1), ("SiO2", 0.9)),
            ),
        ],
    )

    surfaces = vector["components"][THRESHOLD_STRADDLE]["surfaces"]
    assert len(surfaces) == 1
    assert surfaces[0]["surface"] == f"{alkali}_normal_boiling_point"
    assert surfaces[0]["threshold_K"] == pytest.approx(boiling_K)
    assert "data/vapor_pressures.yaml NIST WebBook SRD 69" in surfaces[0]["source"]


def test_nonlinearity_component_uses_quadratic_fit_when_neighborhood_exists() -> None:
    vector = build_interpolation_uncertainty_vector(
        _key("query", temperature_K=1500.0),
        [
            _neighbor("left", temperature_K=1490.0, liquid_fraction=0.6, sio_pa=10.0),
            _neighbor("center", temperature_K=1500.0, liquid_fraction=0.7, sio_pa=12.0),
            _neighbor("right", temperature_K=1510.0, liquid_fraction=0.6, sio_pa=10.0),
        ],
    )

    component = vector["components"][NONLINEARITY_GENERAL]
    assert component["status"] == "available"
    assert component["output_scores"]["liquid_fraction"] == pytest.approx(1.0 / 7.0)
    assert component["value"] == pytest.approx(1.0 / 7.0)
    assert component["active_subspace"] == ["T_K"]


def test_ranked_table_drain_exhausts_threshold_straddles_before_numeric_table() -> None:
    threshold = build_interpolation_uncertainty_vector(
        _key("query", temperature_K=1500.0),
        [
            _neighbor("mushy", temperature_K=1490.0, liquid_fraction=0.95),
            _neighbor("liquid", temperature_K=1510.0, liquid_fraction=1.0),
        ],
    )
    high_numeric = build_interpolation_uncertainty_vector(
        _key("query", temperature_K=1500.0),
        [
            _neighbor("left", temperature_K=1490.0, liquid_fraction=0.6, distance=0.5),
            _neighbor("right", temperature_K=1510.0, liquid_fraction=0.7, distance=0.4),
        ],
    )
    low_numeric = build_interpolation_uncertainty_vector(
        _key("query", temperature_K=1500.0),
        [
            _neighbor("left", temperature_K=1490.0, liquid_fraction=0.6, distance=0.1),
            _neighbor("right", temperature_K=1510.0, liquid_fraction=0.7, distance=0.1),
        ],
    )

    drain = ranked_table_drain(
        [
            {"point_id": "numeric-low", "uncertainty": low_numeric},
            {"point_id": "threshold", "uncertainty": threshold},
            {"point_id": "numeric-high", "uncertainty": high_numeric},
        ],
        limit=2,
    )

    assert [point["point_id"] for point in drain["selected"]] == [
        "threshold",
        "numeric-high",
    ]
    assert drain["selected"][0]["ranked_table"] == THRESHOLD_STRADDLE
    assert drain["selected"][1]["ranked_table"] == NONLINEARITY_GENERAL


def test_ranked_table_drain_breaks_numeric_ties_by_point_id() -> None:
    numeric = build_interpolation_uncertainty_vector(
        _key("query", temperature_K=1500.0),
        [
            _neighbor("left", temperature_K=1490.0, liquid_fraction=0.6, distance=0.4),
            _neighbor("right", temperature_K=1510.0, liquid_fraction=0.7, distance=0.4),
        ],
    )

    drain = ranked_table_drain(
        [
            {"point_id": "b-point", "uncertainty": numeric},
            {"point_id": "a-point", "uncertainty": numeric},
        ],
        limit=2,
    )

    assert [point["point_id"] for point in drain["selected"]] == ["a-point", "b-point"]


def test_margin_inside_uncalibrated_interpolation_error_is_indeterminate() -> None:
    vector = build_interpolation_uncertainty_vector(
        _key("query", temperature_K=1500.0),
        [
            _neighbor("low", temperature_K=1490.0, liquid_fraction=0.7, distance=0.04),
            _neighbor("high", temperature_K=1510.0, liquid_fraction=0.8, distance=0.04),
        ],
    )

    near = feasibility_verdict_with_interpolation_uncertainty(
        {"yield": _margin(0.05)},
        vector,
    )
    far = feasibility_verdict_with_interpolation_uncertainty(
        {"yield": _margin(0.2)},
        vector,
    )

    assert near["verdict"] == INDETERMINATE
    assert far["verdict"] == FEASIBLE


def test_reduced_real_cache_verdict_preserves_margin_error_audit_fields() -> None:
    vector = build_interpolation_uncertainty_vector(
        _key("query", temperature_K=1500.0),
        [
            _neighbor("low", temperature_K=1490.0, liquid_fraction=0.7, distance=0.04),
            _neighbor("high", temperature_K=1510.0, liquid_fraction=0.8, distance=0.04),
        ],
    )

    verdict = feasibility_verdict_from_reduced_real_cache(
        {"yield": _margin(0.05)},
        {
            "interpolation_uncertainty_ranked_table_drain": {
                "selected": [{"point_id": "seed-a", "uncertainty": vector}]
            }
        },
    )

    assert verdict["verdict"] == INDETERMINATE
    assert verdict["point_id"] == "seed-a"
    assert verdict["closest_gate"] == "yield"
    assert verdict["closest_abs_margin"] == pytest.approx(0.05)
    assert verdict["uncalibrated_margin_error_bound"] is not None
    assert verdict["margin_error_source"] == "consumer_side_uncalibrated_wide_band"


def test_margin_outside_uncalibrated_error_preserves_infeasible_branch() -> None:
    vector = build_interpolation_uncertainty_vector(
        _key("query", temperature_K=1500.0),
        [
            _neighbor("low", temperature_K=1490.0, liquid_fraction=0.7, distance=0.04),
            _neighbor("high", temperature_K=1510.0, liquid_fraction=0.8, distance=0.04),
        ],
    )

    verdict = feasibility_verdict_with_interpolation_uncertainty(
        {"yield": _margin(-0.2, feasible=False)},
        vector,
    )

    assert verdict["verdict"] == INFEASIBLE
    assert verdict["reason"] == "gate_margin_exceeds_interpolation_error"
