"""F2 root A: wall Knudsen regime fails closed (no invented 1.0)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from simulator.condensation import KnudsenRegimeRefusal
from simulator import wall_deposition as wd


def _model(**kwargs):
    base = dict(
        overhead_pressure_mbar=100.0,
        gas_temperature_C=1000.0,
        pipe_diameter_m=0.1,
        carrier_gas="N2",
        regime_factor=1.0,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _segment():
    return SimpleNamespace(inner_diameter_m=0.1, name="s1")


def test_wall_knudsen_healthy_path_returns_viscous_factor() -> None:
    factor = wd._segment_wall_regime_factor(_model(), _segment())
    assert 0.0 < factor < 0.01


def test_wall_knudsen_none_pressure_refuses_not_one() -> None:
    with pytest.raises(KnudsenRegimeRefusal, match="wall_segment_knudsen_inputs_unusable"):
        wd._segment_wall_regime_factor(_model(overhead_pressure_mbar=None), _segment())


def test_wall_knudsen_bogus_carrier_refuses_not_one() -> None:
    with pytest.raises(KnudsenRegimeRefusal):
        wd._segment_wall_regime_factor(_model(carrier_gas="BogusGas"), _segment())


def test_mutation_proof_old_swallow_returns_one(monkeypatch: pytest.MonkeyPatch) -> None:
    def _swallowed(model, segment):
        from simulator.condensation import _knudsen_number, _knudsen_regime_factor

        try:
            pressure_pa = float(model.overhead_pressure_mbar) * 100.0
            gas_temperature_K = max(
                float(model.gas_temperature_C) + wd.CELSIUS_TO_KELVIN_OFFSET,
                1.0,
            )
            diameter_m = float(getattr(segment, "inner_diameter_m", model.pipe_diameter_m))
            carrier_gas = str(getattr(model, "carrier_gas", "N2") or "N2")
            kn = _knudsen_number(
                pressure_pa,
                gas_temperature_K,
                diameter_m,
                carrier_gas=carrier_gas,
            )
            return _knudsen_regime_factor(kn)
        except Exception:
            return float(getattr(model, "regime_factor", 1.0) or 1.0)

    monkeypatch.setattr(wd, "_segment_wall_regime_factor", _swallowed)
    assert wd._segment_wall_regime_factor(
        _model(overhead_pressure_mbar=None), _segment()
    ) == 1.0
