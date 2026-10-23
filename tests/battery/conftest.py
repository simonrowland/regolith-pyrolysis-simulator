from __future__ import annotations

import pytest


@pytest.fixture
def _close_openimcc_worker_between_tests():
    from simulator.battery.residue import _close_openimcc_evaluation_worker

    _close_openimcc_evaluation_worker()
    yield
    _close_openimcc_evaluation_worker()
