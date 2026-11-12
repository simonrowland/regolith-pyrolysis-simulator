"""Bit pins for physical constants before ownership moves.

The CODATA / SI 2019 leaf and the shorter spellings are different floats.
Unifying them moves pinned flux and electrolysis heads. These hexes are the
pre-move values. Later commits may repoint the expression pins at the owner;
the hex must not change.
"""

from __future__ import annotations

import math

from simulator.physical_constants import (
    BOLTZMANN,
    FARADAY,
    FARADAY_10SIG,
    GAS_CONSTANT,
    GAS_CONSTANT_6SIG,
    PA_PER_BAR,
    STANDARD_ATMOSPHERE_PA,
    STEFAN_BOLTZMANN,
    STEFAN_BOLTZMANN_7SIG,
)
from simulator.state import (
    BOLTZMANN as STATE_BOLTZMANN,
    FARADAY as STATE_FARADAY,
    GAS_CONSTANT as STATE_GAS_CONSTANT,
    STEFAN_BOLTZMANN as STATE_STEFAN_BOLTZMANN,
)


def test_codata_leaf_bits() -> None:
    assert GAS_CONSTANT.hex() == "0x1.0a1013e8990bep+3"
    assert FARADAY.hex() == "0x1.78e55505d0fa6p+16"
    assert BOLTZMANN.hex() == "0x1.0b0e6d55e647cp-76"
    assert STEFAN_BOLTZMANN.hex() == "0x1.e714da268c0ccp-25"
    assert PA_PER_BAR.hex() == "0x1.86a0000000000p+16"
    assert STANDARD_ATMOSPHERE_PA.hex() == "0x1.8bcd000000000p+16"
    assert GAS_CONSTANT == 8.31446261815324
    assert PA_PER_BAR == 1e5
    assert STANDARD_ATMOSPHERE_PA == 101325.0


def test_state_truncations_stay_distinct_from_the_leaf() -> None:
    # Relative to the leaf: R −3.149e-7, F −2.073e-10, Stefan-Boltzmann −7.389e-8.
    assert STATE_GAS_CONSTANT.hex() == "0x1.0a100e6afcce2p+3"
    assert STATE_FARADAY.hex() == "0x1.78e55504816f0p+16"
    assert STATE_STEFAN_BOLTZMANN.hex() == "0x1.e714d7cab455ap-25"
    assert STATE_BOLTZMANN.hex() == BOLTZMANN.hex()
    assert ((STATE_GAS_CONSTANT - GAS_CONSTANT) / GAS_CONSTANT).hex() == (
        "-0x1.521cb44e56348p-22"
    )
    assert ((STATE_FARADAY - FARADAY) / FARADAY).hex() == "-0x1.c7d357879b828p-33"
    assert (
        (STATE_STEFAN_BOLTZMANN - STEFAN_BOLTZMANN) / STEFAN_BOLTZMANN
    ).hex() == "-0x1.3d5e05f551930p-24"
    assert STATE_GAS_CONSTANT != GAS_CONSTANT
    assert STATE_FARADAY != FARADAY
    assert STATE_STEFAN_BOLTZMANN != STEFAN_BOLTZMANN
    assert STATE_GAS_CONSTANT is GAS_CONSTANT_6SIG
    assert STATE_FARADAY is FARADAY_10SIG
    assert STATE_BOLTZMANN is BOLTZMANN
    assert STATE_STEFAN_BOLTZMANN is STEFAN_BOLTZMANN_7SIG


def test_six_sig_callers_use_the_owner_object() -> None:
    from simulator.chemistry.ellingham_graph import (
        GAS_CONSTANT_J_PER_MOL_K as ellingham_r,
    )
    from simulator.chemistry.organics_pyrolysis import (
        GAS_CONSTANT_J_PER_MOL_K as organics_r,
    )
    from simulator.equipment import STEFAN_BOLTZMANN as equipment_stefan

    assert ellingham_r is GAS_CONSTANT_6SIG
    assert organics_r is GAS_CONSTANT_6SIG
    assert equipment_stefan is STEFAN_BOLTZMANN_7SIG


def test_ten_significant_figure_gas_constant_is_not_the_leaf() -> None:
    # Spelling kept by condensation, NASA-CEA, pumping, and the melt envelope.
    # Relative to GAS_CONSTANT: −1.843e-11.
    spelling = 8.314462618
    assert spelling.hex() == "0x1.0a1013e883fc4p+3"
    assert ((spelling - GAS_CONSTANT) / GAS_CONSTANT).hex() == "-0x1.443b51e1184f0p-36"
    assert spelling != GAS_CONSTANT


def test_cell_oxide_buffer_expression_bits() -> None:
    """Pin the binary-pot coexistence expression before it moves."""

    log10_p = (
        2.0
        * -500_000.0
        / (2.0 * 8.31446261815324 * 1800.0 * math.log(10.0))
    )
    assert log10_p.hex() == "-0x1.d04c8123c6560p+3"


def test_knudsen_relative_weight_bits() -> None:
    """Pin p/sqrt(M) before the cancelled effusion weight moves."""

    weight = 2.5e-4 / math.sqrt(0.055845)
    assert weight.hex() == "0x1.1552ff5ce3623p-10"


def test_vant_hoff_antoine_and_clausius_bits() -> None:
    """Pin the carrier-tool van't Hoff expressions before they move."""

    gas_constant = 8.31446261815324
    ln10 = math.log(10.0)
    antoine_a = math.log10(100_000.0) + 150.0 / (gas_constant * ln10)
    antoine_b = 400_000.0 / (gas_constant * ln10)
    pressure = 1.23e11 * math.exp(-400_000.0 / (gas_constant * 1800.0))
    assert antoine_a.hex() == "0x1.9ab8ac22ada95p+3"
    assert antoine_b.hex() == "0x1.4675cacd27747p+14"
    assert pressure.hex() == "0x1.36fb264ef0908p-2"


def test_full_precision_gas_constant_has_one_owner() -> None:
    """CODATA R, its Decimal, and the kJ float are the leaf objects."""

    from decimal import Decimal

    from simulator.battery.identity import R_J_PER_MOL_K
    from simulator.battery.waypoints import _R as waypoint_r
    from simulator.chemistry.fo2_buffers import _R_CODATA
    from simulator.chemistry.melt_activity import R_KJ_PER_MOL_K
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _CELL_GAS_CONSTANT_J_MOL_K,
    )
    from simulator.oxygen_buffers import _ONEILL_IW_R
    from simulator.physical_constants import GAS_CONSTANT_DECIMAL, J_PER_KJ

    assert GAS_CONSTANT_DECIMAL == Decimal("8.31446261815324")
    assert GAS_CONSTANT_DECIMAL == Decimal(str(GAS_CONSTANT))
    assert R_J_PER_MOL_K is GAS_CONSTANT_DECIMAL
    assert waypoint_r is GAS_CONSTANT_DECIMAL
    assert _R_CODATA is GAS_CONSTANT_DECIMAL
    assert _ONEILL_IW_R is GAS_CONSTANT
    assert _CELL_GAS_CONSTANT_J_MOL_K is GAS_CONSTANT
    assert R_KJ_PER_MOL_K.hex() == "0x1.1072c483af26dp-7"
    assert R_KJ_PER_MOL_K == GAS_CONSTANT / J_PER_KJ
