"""d-297 boundary probes use the real ig projector, with synthetic bounds."""

import pytest

from simulator.evaporation import EvaporationMixin
from simulator.melt_backend.magemin import (
    MAGEMinBackend,
    _magemin_bulk_projection_details,
)
from tests.test_magemin_backend import _make_fake_magemin_cli_liquidus


@pytest.mark.parametrize('dropped', [0.5, 1.0, 1.1, 2.0])
def test_projected_freeze_curve_vetoes_over_limit(dropped):
    projection = MAGEMinBackend()._build_db_bulk_projection(
        {'SiO2': 85.0, 'Al2O3': 10.0, 'CaO': 5.0 - dropped, 'MnO': dropped},
    )
    diagnostic = {
        'input_composition_projection': _magemin_bulk_projection_details(projection),
        'solidus_T_C': 1200.0,
        'liquidus_T_C': 1500.0,
    }
    gate = EvaporationMixin()
    reasons = []
    curve = gate._composition_projected_freeze_gate_curve(
        diagnostic, source='magemin', reasons=reasons,
    )
    if dropped <= 1.0:
        assert curve is not None
        assert curve['composition_projected_notice']['reason'] == 'composition_projected'
    else:
        assert curve is None
        assert any('composition_projected_over_threshold' in item for item in reasons)
        assert 'MnO' in reasons[-1]
        assert str(dropped) in reasons[-1]


@pytest.mark.parametrize('dropped', [0.5, 1.0, 1.1, 2.0])
def test_projected_liquidus_search_vetoes_over_limit(monkeypatch, dropped):
    backend, calls = _make_fake_magemin_cli_liquidus(monkeypatch)
    result = backend.find_liquidus_solidus(
        composition_kg={'SiO2': 85.0, 'Al2O3': 10.0, 'CaO': 5.0 - dropped, 'MnO': dropped},
        fO2_log=-9.0, pressure_bar=1.0,
        min_T_C=900.0, max_T_C=1300.0, scan_step_C=50.0, tolerance_C=1.0,
    )
    assert result.status == 'out_of_domain'
    if dropped <= 1.0:
        assert result.diagnostics['projected_liquidus_status'] == 'ok'
        assert calls
    else:
        assert not calls
        assert result.liquidus_T_C is None
        assert result.diagnostics['reason'] == 'composition_projected_over_threshold'
        assert any('MnO' in item and str(dropped) in item for item in result.warnings)
