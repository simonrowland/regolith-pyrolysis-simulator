import pytest

from _ia_r6_digest_group_support import run_digest_group


@pytest.mark.cpu_budget(300)
@pytest.mark.timeout(6900)
def test_r6_mendybaev_digest_group_03():
    run_digest_group("mendybaev", 3)
