from __future__ import annotations

import pytest

pytest.importorskip("openimcc")

from engines.builtin import vapor_pressure as vapor_pressure_module
from simulator.diagnostic_helpers.binary_pot_battery import (
    PO2_COMMANDED,
    Po2Request,
    _internal_analytical_vapor_pressure_adapter,
    _new_internal_analytical_core,
)
import simulator.melt_backend.openimcc_bridge as openimcc_bridge


_COMPOSITION_MOL = {
    "Na2O": 0.20,
    "K2O": 0.08,
    "SiO2": 5.00,
    "FeO": 1.00,
    "MgO": 1.00,
    "CaO": 1.00,
    "Al2O3": 1.00,
    "TiO2": 0.10,
}


def _call(core):
    return _internal_analytical_vapor_pressure_adapter(
        core=core,
        temperature_C=2400.65 - 273.15,
        pressure_bar=1.0e-6,
        composition_kg=None,
        composition_mol=_COMPOSITION_MOL,
        fO2_log=-9.0,
        po2_request=Po2Request(mode=PO2_COMMANDED, po2_bar=1.0e-9),
        include_diagnostic_shadows=False,
    )


def test_refusal_does_not_depend_on_previous_call_on_a_reused_core(monkeypatch):
    warm_core = _new_internal_analytical_core()
    successful = _call(warm_core)
    assert warm_core._last_high_t_melt_activity[
        "certified_effective_gamma_by_parent"
    ]

    def refuse(**_kwargs):
        from openimcc import ImccNonconvergenceError

        raise ImccNonconvergenceError("stateless refusal fixture", {})

    monkeypatch.setattr(openimcc_bridge, "evaluate", refuse)
    monkeypatch.setattr(
        vapor_pressure_module,
        "_same_composition_temperature_bridge",
        lambda *_args, **_kwargs: None,
    )
    warm = _call(warm_core)
    cold = _call(_new_internal_analytical_core())

    assert warm.vapor_pressures_Pa == cold.vapor_pressures_Pa
    assert warm.activity_coefficients == cold.activity_coefficients
    assert warm.diagnostics["vapor_pressure_diagnostic"][
        "high_t_melt_activity"
    ]["provider"] == "constant_gamma"
