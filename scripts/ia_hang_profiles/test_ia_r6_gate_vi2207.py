import pytest

from _ia_r6_digest_group_support import run_gate_node


@pytest.mark.cpu_budget(300)
@pytest.mark.timeout(6900)
def test_r6_gate_node_vi2207():
    run_gate_node("kems-026-markova-1984::markova_1984_table2_residual_melt_VI_lherzolite::T=2480.15:h=9e6722a89608")
