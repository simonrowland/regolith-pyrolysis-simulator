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
    """The coexistence owner keeps the pre-move hex."""

    from simulator.oxygen_buffers import metal_oxide_coexistence_log10_fO2_bar

    log10_p = metal_oxide_coexistence_log10_fO2_bar(-500_000.0, 2.0, 1800.0)
    assert log10_p.hex() == "-0x1.d04c8123c6560p+3"


def test_knudsen_relative_weight_bits() -> None:
    """The effusion owner keeps the pre-move hex, including a count of 2."""

    from simulator.chemistry.langmuir_knudsen import (
        knudsen_effusion_relative_weight,
    )

    weight = knudsen_effusion_relative_weight(2.5e-4, 0.055845)
    counted = knudsen_effusion_relative_weight(
        2.5e-4, 0.055845, coefficient=2.0
    )
    assert weight.hex() == "0x1.1552ff5ce3623p-10"
    assert counted.hex() == "0x1.1552ff5ce3623p-9"


def test_delta_iw_subtraction_bits() -> None:
    """ΔIW is the caller's log10 difference. The Decimal hero path is not this."""

    from simulator.oxygen_buffers import delta_iw_log10

    assert delta_iw_log10(-10.0, -12.5).hex() == "0x1.4000000000000p+1"


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


def test_ten_sig_gas_constant_has_one_owner() -> None:
    """The 10-significant-figure spelling is one object. It is not the leaf."""

    from engines.builtin.foulant_disposition import (
        GAS_CONSTANT_J_PER_MOL_K as foulant_r,
    )
    from simulator.condensation import GAS_CONSTANT_J_MOL_K as condensation_r
    from simulator.melt_backend.melt_envelope import R_J_MOL_K as envelope_r
    from simulator.physical_constants import GAS_CONSTANT_10SIG
    from simulator.pumping_cost import _R_J_PER_MOL_K as pumping_r
    from simulator.vapour_rail.nasa_cea import R_J_PER_MOL_K as cea_r

    assert GAS_CONSTANT_10SIG.hex() == "0x1.0a1013e883fc4p+3"
    assert foulant_r is GAS_CONSTANT_10SIG
    assert condensation_r is GAS_CONSTANT_10SIG
    assert envelope_r is GAS_CONSTANT_10SIG
    assert pumping_r is GAS_CONSTANT_10SIG
    assert cea_r is GAS_CONSTANT_10SIG
    assert GAS_CONSTANT_10SIG is not GAS_CONSTANT


def test_bar_to_pascal_has_one_owner() -> None:
    """1 bar is 1e5 Pa. The float and the decimal each have one object."""

    from decimal import Decimal

    from simulator.battery.identity import PA_PER_BAR_DEC
    from simulator.battery.oxygen_identity import BAR_PA as oxygen_bar_pa
    from simulator.chemistry.ellingham_graph import ELLINGHAM_STANDARD_PRESSURE_PA
    from simulator.physical_constants import (
        BAR_PA,
        BAR_PA_DECIMAL,
        PA_PER_BAR,
        PA_PER_MBAR,
    )
    from simulator.pumping_cost import _PA_PER_BAR, _PA_PER_MBAR

    assert BAR_PA is PA_PER_BAR
    assert PA_PER_BAR == 1e5
    assert BAR_PA_DECIMAL == Decimal(100000)
    assert BAR_PA_DECIMAL == Decimal("100000.0")
    assert BAR_PA_DECIMAL == Decimal("1e5")
    assert PA_PER_BAR_DEC is BAR_PA_DECIMAL
    assert oxygen_bar_pa is BAR_PA_DECIMAL
    assert ELLINGHAM_STANDARD_PRESSURE_PA is PA_PER_BAR
    assert _PA_PER_BAR is PA_PER_BAR
    assert _PA_PER_MBAR is PA_PER_MBAR


def test_standard_atmosphere_has_one_owner() -> None:
    """1 atm is 101325 Pa. The float and the decimal each have one object."""

    from decimal import Decimal

    from simulator.battery.identity import PA_PER_ATM_DEC
    from simulator.physical_constants import (
        ATM_PA,
        ATM_PA_DECIMAL,
        STANDARD_ATMOSPHERE_PA,
    )
    from simulator.thermal_train import (
        MMHG_TO_PA,
        OXYGEN_NORMAL_BOILING_PRESSURE_PA,
    )

    assert ATM_PA is STANDARD_ATMOSPHERE_PA
    assert STANDARD_ATMOSPHERE_PA == 101325.0
    assert ATM_PA_DECIMAL == Decimal(101325)
    assert ATM_PA_DECIMAL == Decimal("101325.0")
    assert PA_PER_ATM_DEC is ATM_PA_DECIMAL
    assert OXYGEN_NORMAL_BOILING_PRESSURE_PA is STANDARD_ATMOSPHERE_PA
    assert MMHG_TO_PA == STANDARD_ATMOSPHERE_PA / 760.0
