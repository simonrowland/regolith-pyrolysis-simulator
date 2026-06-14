"""Stage-0 P2 species honesty — physics-grounded routing regression tests.

Guards audit rows #10 (fluoride), #3/#3b (chloride), #9 (nitrate):
CaF2 to rump not cleared salt; chloride labeled separated/fouling-risk;
nitrate declaration fails loud.  Assertions use literature anchors, not
simulator self-parity.
"""

from __future__ import annotations

import pytest

from simulator.core import (
    STAGE0_CHLORIDE_SALT_ACCOUNT,
    STAGE0_CHLORIDE_SALT_DISPOSITION,
    PyrolysisSimulator,
)
from simulator.melt_backend.base import StubBackend

# NIST-normal boiling points (C) for halide volatility anchors.
NACL_BOILING_POINT_C = 1465.0
KCL_BOILING_POINT_C = 1420.0
CAF2_BOILING_POINT_C = 2530.0


def _sim(feedstocks):
    backend = StubBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        feedstocks,
        {"metals": {}, "oxide_vapors": {}},
    )


def test_caf2_routes_to_terminal_slag_not_cleared_salt():
    """CaF2 b.p. ~2530 C — refractory; belongs in rump, not removed salt."""
    assert CAF2_BOILING_POINT_C > 1700.0

    sim = _sim(
        {
            "fluorite_test": {
                "label": "CaF2 refractory",
                "composition_wt_pct": {
                    "SiO2": 97.0,
                    "CaF2": 3.0,
                },
            }
        }
    )
    mass_kg = 1000.0
    sim.load_batch("fluorite_test", mass_kg=mass_kg)

    caf2_kg = mass_kg * 0.03
    assert sim.inventory.terminal_slag_components_kg["CaF2"] == pytest.approx(
        caf2_kg
    )
    assert "CaF2" not in sim.inventory.salt_phase_kg
    assert "CaF2" not in sim.inventory.chloride_salt_phase_kg
    assert "CaF2" not in sim.inventory.residual_components_kg

    slag_ledger = sim.atom_ledger.kg_by_account("terminal.slag")
    assert slag_ledger.get("CaF2", 0.0) == pytest.approx(caf2_kg)

    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


def test_chloride_routes_to_fouling_risk_bucket_not_cleared_salt():
    """NaCl/KCl volatilize under mbar vacuum — separated, not gasified."""
    assert NACL_BOILING_POINT_C < 1700.0
    assert KCL_BOILING_POINT_C < 1700.0

    sim = _sim(
        {
            "chloride_test": {
                "label": "Chloride fouling-risk",
                "composition_wt_pct": {
                    "SiO2": 98.0,
                    "Cl": 2.0,
                },
            }
        }
    )
    mass_kg = 1000.0
    sim.load_batch("chloride_test", mass_kg=mass_kg)

    cl_kg = mass_kg * 0.02
    assert sim.inventory.chloride_salt_phase_kg["Cl"] == pytest.approx(cl_kg)
    assert "Cl" not in sim.inventory.salt_phase_kg

    chloride_ledger = sim.atom_ledger.kg_by_account(STAGE0_CHLORIDE_SALT_ACCOUNT)
    assert chloride_ledger.get("Cl", 0.0) == pytest.approx(cl_kg)
    assert STAGE0_CHLORIDE_SALT_DISPOSITION == (
        "separated_chloride_salt_fouling_risk"
    )

    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


@pytest.mark.parametrize(
    "component",
    ["nitrate", "NaNO3", "KNO3", "NO3"],
)
def test_declared_nitrate_fails_loud(component: str):
    sim = _sim(
        {
            "nitrate_test": {
                "label": "Nitrate guard",
                "composition_wt_pct": {
                    "SiO2": 99.0,
                    component: 1.0,
                },
            }
        }
    )
    with pytest.raises(ValueError, match="does not model nitrate"):
        sim.load_batch("nitrate_test", mass_kg=1000.0)


def test_bare_f_fluoride_key_fails_loud():
    sim = _sim(
        {
            "bare_f": {
                "label": "Bare F rejected",
                "composition_wt_pct": {
                    "SiO2": 99.0,
                    "f": 1.0,
                },
            }
        }
    )
    with pytest.raises(ValueError, match="explicit key"):
        sim.load_batch("bare_f", mass_kg=1000.0)