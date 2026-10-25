"""C7 ideal-gas owner pins before the rounding-level physics move."""
from simulator import environment, state, transport_regime


def test_historical_gas_constants_hex():
    assert environment._OVERHEAD_EOS_GAS_CONSTANT_J_PER_MOL_K.hex() == '0x1.0a100e6afcce2p+3'
    assert state.GAS_CONSTANT.hex() == '0x1.0a100e6afcce2p+3'
    assert transport_regime.PIPE_GAS_CONSTANT_J_MOL_K.hex() == '0x1.0a100e6afcce2p+3'
    assert transport_regime.SURFACE_GAS_CONSTANT_J_MOL_K.hex() == '0x1.0a1013e883fc4p+3'
