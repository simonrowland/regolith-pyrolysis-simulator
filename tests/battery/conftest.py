from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def _close_openimcc_worker_between_tests():
    from simulator.battery.residue import _close_openimcc_evaluation_worker

    _close_openimcc_evaluation_worker()
    yield _close_openimcc_evaluation_worker
    _close_openimcc_evaluation_worker()


@pytest.fixture(scope="session")
def parsed_store_cache_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from simulator.battery.migrate import REPO_ROOT
    from tests.battery.store_cache import load_cached_test_store

    cache_root = tmp_path_factory.getbasetemp() / "t1745-parsed-store"
    load_cached_test_store(REPO_ROOT, cache_root=cache_root)
    return cache_root
