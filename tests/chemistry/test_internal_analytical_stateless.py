from __future__ import annotations

import math

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
from simulator.oxygen_buffers import feo_iw_log10_fO2_bar


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

    assert warm.status == cold.status
    assert warm.vapor_pressures_Pa == cold.vapor_pressures_Pa
    assert warm.vapor_pressures_source == cold.vapor_pressures_source
    assert warm.activity_coefficients == cold.activity_coefficients
    warm_authority = warm.diagnostics["vapor_pressure_diagnostic"][
        "high_t_melt_activity"
    ]
    cold_authority = cold.diagnostics["vapor_pressure_diagnostic"][
        "high_t_melt_activity"
    ]
    assert warm_authority == cold_authority
    assert warm_authority["provider"] == "constant_gamma"


def test_battery_fe_vapour_notice_follows_the_consumed_activity_source():
    core = _new_internal_analytical_core()
    temperature_C = 1610.0
    temperature_K = temperature_C + 273.15
    fO2_log = feo_iw_log10_fO2_bar(temperature_K) - 0.25
    result = _internal_analytical_vapor_pressure_adapter(
        core=core,
        temperature_C=temperature_C,
        pressure_bar=1.0e-6,
        composition_kg=None,
        composition_mol=_COMPOSITION_MOL,
        fO2_log=fO2_log,
        po2_request=Po2Request(
            mode=PO2_COMMANDED, po2_bar=math.pow(10.0, fO2_log)
        ),
        include_diagnostic_shadows=False,
    )

    diagnostic = result.diagnostics["vapor_pressure_diagnostic"]
    fe_provenance = diagnostic["vapor_pressure_numerator_provenance"]["Fe"]
    assert result.vapor_pressures_Pa["Fe"] > 0.0
    assert fe_provenance["activity_basis"] == "kress91_ferrous"
    assert fe_provenance["activity_authority"]["extrapolation"] is True
    notice = next(
        row
        for row in result.diagnostics["imcc_notices"]
        if row.get("source") == "consumed_fe_vapor_activity"
    )
    assert notice["kind"] == "out_of_certified_band"
    assert notice["authority"] == "extrapolated"
    assert notice["band"] == {"temperature_C": (1300.0, 1600.0)}


def test_openimcc_selected_fe_keeps_its_own_activity_source():
    result = _call(_new_internal_analytical_core())
    diagnostic = result.diagnostics["vapor_pressure_diagnostic"]
    fe_provenance = diagnostic["vapor_pressure_numerator_provenance"]["Fe"]

    assert fe_provenance["activity_basis"] == "openimcc_single_cation"
    assert "activity_notices" not in fe_provenance
    assert not any(
        row.get("source") == "consumed_fe_vapor_activity"
        for row in result.diagnostics["imcc_notices"]
    )
