"""Reported buffer conversion distinguishes reference spread from precision."""

from simulator.fe_redox import initialize_feedstock_iron
from tests.test_feedstock_iron_seed import _feedstock


def test_iw_reference_spread_is_visible_without_changing_potential():
    _, diagnostic = initialize_feedstock_iron(_feedstock())
    conversion = diagnostic['buffer_conversion']
    note = conversion['reference_uncertainty_note']
    assert '0.14 dex' in note
    assert '0.13 dex' in note
    assert '1.5 dex' in note
    assert diagnostic['fO2_log'] == -10.588416848423613
