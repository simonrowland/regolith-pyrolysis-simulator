"""Shared fixtures for the builtin chemistry provider tests.

Scoped to ``tests/chemistry/``. The fixtures are module-scoped so they
are constructed once per test module; pytest only injects them where a
test explicitly requests them as an argument, so the kernel test files
(which do not request them) are unaffected.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend


DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


@pytest.fixture(scope="module")
def vapor_pressure_data() -> dict:
    return _load_yaml("vapor_pressures.yaml")


@pytest.fixture(scope="module")
def feedstocks_data() -> dict:
    return _load_yaml("feedstocks.yaml")


@pytest.fixture(scope="module")
def setpoints_data() -> dict:
    return _load_yaml("setpoints.yaml")


def _build_sim(
    feedstock_key: str,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    *,
    additives_kg: dict | None = None,
) -> PyrolysisSimulator:
    """Build a PyrolysisSimulator with a fresh StubBackend.

    Helper -- intentionally not a pytest fixture so callers can pass
    feedstock-specific arguments per test.
    """

    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend, setpoints_data, feedstocks_data, vapor_pressure_data
    )
    sim.load_batch(feedstock_key, mass_kg=1000.0, additives_kg=additives_kg)
    return sim
