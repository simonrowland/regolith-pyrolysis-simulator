"""Guards and regressions for simulator.equilibrium."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from simulator.equilibrium import (
    EquilibriumMixin,
    _internal_analytical_control_refusal,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET


def _minimal_sim(*, temperature_C, p_total_mbar):
    return SimpleNamespace(
        melt=SimpleNamespace(
            temperature_C=temperature_C,
            p_total_mbar=p_total_mbar,
        )
    )


def test_internal_analytical_cold_path_still_ok_at_25_c():
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=25.0, p_total_mbar=1.0)
    )
    assert result.status == "ok"
    assert result.pressure_bar == pytest.approx(0.001)
    assert result.vapor_pressures_Pa == {}
    assert result.liquid_fraction is None
    assert result.phase_assemblage_available is False


def test_internal_analytical_zero_mbar_is_admitted_vacuum():
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=25.0, p_total_mbar=0.0)
    )
    assert result.status == "ok"
    assert result.pressure_bar == 0.0


@pytest.mark.parametrize(
    "temperature_C",
    (
        -CELSIUS_TO_KELVIN_OFFSET,
        -CELSIUS_TO_KELVIN_OFFSET - 1.0,
        float("-inf"),
        float("nan"),
        float("inf"),
    ),
)
def test_internal_analytical_refuses_non_positive_or_nonfinite_temperature(
    temperature_C,
):
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=temperature_C, p_total_mbar=1.0)
    )
    assert result.status == "out_of_domain"
    assert result.vapor_pressures_Pa == {}
    assert result.fO2_log is None
    assert result.pressure_bar == 0.0
    assert result.diagnostics["internal_analytical_refusal"] == (
        "invalid_scientific_controls"
    )
    fields = {row["field"] for row in result.diagnostics["invalid_controls"]}
    assert "temperature_C" in fields


@pytest.mark.parametrize("p_total_mbar", (float("nan"), float("inf"), -1.0))
def test_internal_analytical_refuses_nonfinite_or_negative_total_pressure(
    p_total_mbar,
):
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=1600.0, p_total_mbar=p_total_mbar)
    )
    assert result.status == "out_of_domain"
    assert result.vapor_pressures_Pa == {}
    assert result.pressure_bar == 0.0
    fields = {row["field"] for row in result.diagnostics["invalid_controls"]}
    assert "p_total_mbar" in fields


def test_internal_analytical_nan_temperature_does_not_raise_ellingham_valueerror():
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=float("nan"), p_total_mbar=1.0)
    )
    assert result.status == "out_of_domain"
    assert result.diagnostics["internal_analytical_refusal"] == (
        "invalid_scientific_controls"
    )


def test_internal_analytical_refuses_non_numeric_controls():
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=True, p_total_mbar="1.0")
    )
    assert result.status == "out_of_domain"
    fields = {row["field"] for row in result.diagnostics["invalid_controls"]}
    assert fields == {"temperature_C", "p_total_mbar"}


def test_internal_analytical_control_refusal_helper_shape():
    diag = _internal_analytical_control_refusal(float("nan"), -1.0)
    assert diag is not None
    fields = {row["field"] for row in diag["invalid_controls"]}
    assert fields == {"temperature_C", "p_total_mbar"}
    assert _internal_analytical_control_refusal(25.0, 0.0) is None
    assert _internal_analytical_control_refusal(
        -CELSIUS_TO_KELVIN_OFFSET, 1.0
    ) is not None
