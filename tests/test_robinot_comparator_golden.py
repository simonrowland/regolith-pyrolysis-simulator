from __future__ import annotations

import pytest

from scripts.benchtop import reproduce_robinot_2026 as reproduction
from simulator.thermal_budget import (
    ROBINOT_PYROMETER_POLICY,
    robinot_pyrometer_brightness_temperature_C,
)


def test_robinot_pyrometer_comparator_scores_planck_brightness() -> None:
    record = reproduction._pyrometer_record(
        {
            "power_temperature_records": [
                {"temperature_C": 2026.0, "face_set": "hot_upper", "time_min": 59.0},
                {"temperature_C": 2200.0, "face_set": "full_side", "time_min": 59.0},
            ]
        },
        observation={"value": 1800.0, "qualifier": "approximate_peak"},
        unavailable_reason=None,
        mode="imposed",
    )

    assert record["true_peak_temperature_C"] == 2026.0
    assert record["simulated"] == pytest.approx(1960.0, abs=1.0)
    assert record["residual"] == pytest.approx(160.0, abs=1.0)
    assert ROBINOT_PYROMETER_POLICY["ruling"] == "d-464"
    assert ROBINOT_PYROMETER_POLICY["wavelength_um"] == 5.0
    assert ROBINOT_PYROMETER_POLICY["eps_lambda"] == {
        "id": "DP-110",
        "status": "proposed",
        "value": 0.95,
        "band": (0.90, 0.98),
        "flag": "eps_lambda_assumed",
    }
    assert ROBINOT_PYROMETER_POLICY["tau_path"]["id"] == "DP-111"
    assert ROBINOT_PYROMETER_POLICY["tau_path"]["status"] == "proposed"
    assert ROBINOT_PYROMETER_POLICY["tau_path"]["value"] == 1.0
    assert ROBINOT_PYROMETER_POLICY["tau_path"]["band"] == (0.93, 1.0)
    assert ROBINOT_PYROMETER_POLICY["tau_path"]["flag"] == "tau_path_assumed"
    expected_band_residuals = [
        robinot_pyrometer_brightness_temperature_C(
            2026.0,
            emissivity=emissivity,
            path_transmittance=path_transmittance,
        )
        - 1800.0
        for emissivity in ROBINOT_PYROMETER_POLICY["eps_lambda"]["band"]
        for path_transmittance in ROBINOT_PYROMETER_POLICY["tau_path"]["band"]
    ]
    assert record["residual_band_C"]["min"] == pytest.approx(
        min(expected_band_residuals)
    )
    assert record["residual_band_C"]["max"] == pytest.approx(
        max(expected_band_residuals)
    )
    assert set(record["flags"]) >= {
        "eps_lambda_assumed",
        "tau_path_assumed",
        "pyrometer_spot_unprinted",
    }
