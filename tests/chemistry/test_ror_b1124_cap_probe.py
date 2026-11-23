"""Mutation probe for condensed-metal cap value and notice propagation."""

from __future__ import annotations

from dataclasses import replace
import inspect
import textwrap

import pytest

from engines.builtin import vapor_pressure
from simulator.chemistry import ellingham_thermo
from tests.chemistry.conftest import _build_sim
from tests.chemistry.test_builtin_vapor_pressure_provider import _fe_redox_request


def test_fe_pressure_and_notice_follow_condensed_cap(
    monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti", vapor_pressure_data, feedstocks_data, setpoints_data
    )
    request = _fe_redox_request(
        species_formula_registry=sim.species_formula_registry,
        intrinsic_fO2_log=-8.0,
        fO2_log=-8.0,
    )
    request = replace(
        request,
        temperature_C=1605.0,
        control_inputs={"pO2_bar": 1e-8, "intrinsic_fO2_log": -8.0},
    )
    provider = vapor_pressure.BuiltinVaporPressureProvider(vapor_pressure_data)

    def fe_rail():
        diagnostic = provider.dispatch(request).diagnostic
        return diagnostic["vapor_pressure_numerator_provenance"]["Fe"]

    uncapped = fe_rail()
    assert uncapped["pressure_rail"] == "condensed_raoult_psat"
    assert uncapped["notice"]["affected_quantities"] == ["p_partial"]
    assert uncapped["notice"]["affected_species"] == ["Fe"]
    raw = uncapped["raw_metal_activity_root"]
    assert 0.0 < raw < 1.0

    cap = 0.5 * raw
    monkeypatch.setattr(ellingham_thermo, "CONDENSED_METAL_ACTIVITY_CAP", cap)

    def assert_capped_notice():
        rail = fe_rail()
        assert rail["raw_metal_activity_root"] == raw
        assert rail["P_eq_Pa"] == pytest.approx(
            cap * uncapped["P_reference_Antoine_Pa"], abs=0
        )
        assert "notice" not in rail, "capped Fe activity leaked a notice"
        return rail

    capped = assert_capped_notice()

    # Negative control: restore the old predicate in memory. The value still
    # follows the cap helper, but the emitted notice must make this assertion fail.
    dispatched = type(provider).dispatch
    source = textwrap.dedent(inspect.getsource(dispatched))
    current = "additionally_capped=condensed_activity_cap_bound,"
    old = (
        'additionally_capped=(pressure_rail == "condensed_raoult_psat" '
        "and metal_activity_root > 1.0),"
    )
    assert source.count(current) == 1
    namespace = vars(vapor_pressure).copy()
    exec(compile(source.replace(current, old), "<stale cap predicate>", "exec"), namespace)
    with monkeypatch.context() as mutation:
        mutation.setattr(type(provider), "dispatch", namespace["dispatch"])
        with pytest.raises(AssertionError, match="capped Fe activity leaked"):
            assert_capped_notice()

    assert capped["P_eq_Pa"] < uncapped["P_eq_Pa"]
