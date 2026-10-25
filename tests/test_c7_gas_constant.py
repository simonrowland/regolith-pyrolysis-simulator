"""C7 ideal-gas owner pins before the rounding-level physics move."""
from simulator import environment, state, transport_regime
from simulator.physical_constants import GAS_CONSTANT


def test_gas_constants_delegate_to_physical_owner():
    assert environment._OVERHEAD_EOS_GAS_CONSTANT_J_PER_MOL_K == GAS_CONSTANT
    assert state.GAS_CONSTANT == GAS_CONSTANT
    assert transport_regime.PIPE_GAS_CONSTANT_J_MOL_K == GAS_CONSTANT
    assert transport_regime.SURFACE_GAS_CONSTANT_J_MOL_K == GAS_CONSTANT
