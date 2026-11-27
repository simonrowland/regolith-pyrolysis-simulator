import pytest

from _ia_r6_digest_group_support import run_gate_node


@pytest.mark.cpu_budget(300)
@pytest.mark.timeout(6900)
def test_r6_gate_node_partial():
    run_gate_node("kems-026-markova-1984::markova_1984_table2_residual_melt_VI_lherzolite::T=2156.15:h=c98ab06cc333")
