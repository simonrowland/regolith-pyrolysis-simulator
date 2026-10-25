"""Previous-hour bounds seed the existing phase finder, never a cached answer."""
from types import SimpleNamespace

import pytest

from engines.magemin.provider import MAGEMinShadowProvider
from tests.test_c7_closure_routing import simulation
from tests.magemin_test_support import close_test_owned_magemin_backend


def test_narrow_window_preserves_finder_crossings_with_fewer_samples():
    from simulator.melt_backend.liquidus import find_liquidus_solidus_by_fraction

    calls = []

    def fraction(temperature_C):
        calls.append(temperature_C)
        return max(0.0, min(1.0, (temperature_C - 1200.0) / 150.0))

    broad = find_liquidus_solidus_by_fraction(fraction)
    broad_count = len(calls)
    calls.clear()
    warm = find_liquidus_solidus_by_fraction(fraction, min_T_C=1100.0, max_T_C=1450.0)
    assert warm.status == broad.status == 'ok'
    assert (warm.solidus_T_C, warm.liquidus_T_C) == (broad.solidus_T_C, broad.liquidus_T_C)
    assert len(calls) < broad_count


@pytest.mark.parametrize('previous_curve, expected', (
    ({'solidus_T_C': 1171.875, 'liquidus_T_C': 1360.9375},
     {'min_T_C': 1100.0, 'max_T_C': 1450.0}),
    (None, {}),
))
def test_previous_hour_bounds_seed_gate_search(monkeypatch, previous_curve, expected):
    sim = simulation()
    previous = {'key': 'previous hour', 'curve': previous_curve}
    sim._freeze_gate_liquid_fraction_cache = previous
    observed = []

    def stop_at_dispatch(intent, *, control_inputs, **kwargs):
        observed.append(control_inputs)
        raise LookupError('search inputs captured')

    monkeypatch.setattr(sim, '_dispatch_only', stop_at_dispatch)
    try:
        with pytest.raises(LookupError, match='search inputs captured'):
            sim._freeze_gate_curve_record()
        assert observed == [({'_liquidus_search_window_C': expected} if expected else {})]
        assert sim._freeze_gate_liquid_fraction_cache is previous
    finally:
        close_test_owned_magemin_backend(sim)


@pytest.mark.parametrize('window', ({'min_T_C': 1100.0, 'max_T_C': 1450.0}, {}))
def test_magemin_forwards_existing_finder_window(window):
    calls = []
    answer = object()

    def finder(**kwargs):
        calls.append(kwargs)
        return answer

    request = SimpleNamespace(
        pressure_bar=0.009, fO2_log=-9.0, redox_state=None,
        account_view=SimpleNamespace(species_formula_registry={}),
        control_inputs={'_liquidus_search_window_C': window},
    )
    result = MAGEMinShadowProvider()._run_liquidus_finder(
        SimpleNamespace(find_liquidus_solidus=finder), request,
        composition_mol_by_account={'process.cleaned_melt': {'SiO2': 1.0}},
    )
    assert result is answer
    assert {name: calls[0][name] for name in window} == window
    assert set(calls[0]) == {
        'pressure_bar', 'fO2_log', 'redox_state',
        'composition_mol_by_account', 'species_formula_registry', *window,
    }
