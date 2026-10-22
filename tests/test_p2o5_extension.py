"""Pins for the openimcc P2O5 projection count before the opt-in extension.

Expected masses use CIAAW standard atomic weights written here, not
``ATOMIC_WEIGHTS_G_PER_MOL``.
"""

from __future__ import annotations

import pytest

# CIAAW standard atomic weights (g/mol).
_CIAAW_P = 30.973761998
_CIAAW_O = 15.999
_CIAAW_SI = 28.085


def _ciaaw_p2o5_dropped_wt_pct() -> float:
    """Wt% of 0.02 mol P2O5 in 1.00 mol SiO2, from the CIAAW masses above."""

    mass_sio2_g = 1.0 * (_CIAAW_SI + 2.0 * _CIAAW_O)
    mass_p2o5_g = 0.02 * (2.0 * _CIAAW_P + 5.0 * _CIAAW_O)
    return mass_p2o5_g / (mass_sio2_g + mass_p2o5_g) * 100.0


def test_p2o5_projection_count_pin_over_threshold() -> None:
    """Today's cleaned-melt policy counts P2O5 toward the 1 wt% gate."""

    from simulator.melt_backend.openimcc_bridge import (
        OpenImccCompositionPolicyRefusal,
        _cleaned_melt_policy,
    )

    with pytest.raises(OpenImccCompositionPolicyRefusal) as caught:
        _cleaned_melt_policy({"SiO2": 1.0, "P2O5": 0.02})
    assert caught.value.code == "openimcc_projection_over_threshold"
    classification = caught.value.diagnostics[
        "composition_projection_classification"
    ]
    assert classification["dropped_components"] == ["P2O5"]
    assert classification["dropped_total_wt_pct"] == pytest.approx(
        _ciaaw_p2o5_dropped_wt_pct(),
        rel=0.0,
        abs=1e-9,
    )
    assert classification["dropped_total_wt_pct"] > 1.0
