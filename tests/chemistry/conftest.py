"""Shared fixtures for the builtin chemistry provider tests.

Scoped to ``tests/chemistry/``. The fixtures are module-scoped so they
are constructed once per test module; pytest only injects them where a
test explicitly requests them as an argument, so the kernel test files
(which do not request them) are unaffected.
"""

from __future__ import annotations

from collections import defaultdict
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


def _atom_check(proposal, registry, *, tol: float) -> dict:
    """Independent atom-balance re-derivation for a LedgerTransitionProposal.

    Sums (credits - debits) per element across every (account, species,
    mol) entry on both sides of the proposal, asserts the worst absolute
    net is below ``tol``, and returns the per-element net dict.

    ``tol`` is REQUIRED (no default) — provider call sites have
    historically diverged on the appropriate tolerance band (1e-12 for
    pure-IEEE-754 sibling-stoich proposals; 1e-9 for proposals built
    from kg-side legacy spec payloads where mol→kg→mol round-trips
    accumulate ULP). Force every call site to declare its own band so
    the choice is auditable.

    Helper -- intentionally not a pytest fixture; lives at module scope
    so any test file can import it directly via
    ``from tests.chemistry.conftest import _atom_check``.
    """
    from simulator.accounting.formulas import resolve_species_formula

    net: dict = defaultdict(float)
    for side, sign in ((proposal.debits, -1.0), (proposal.credits, +1.0)):
        for _account, species_mol in side.items():
            for sp, mol in species_mol.items():
                formula = resolve_species_formula(sp, registry)
                for element, atoms in formula.atom_moles(float(mol)).items():
                    net[element] += sign * float(atoms)
    worst = max((abs(v) for v in net.values()), default=0.0)
    assert worst < tol, f"atom-balance net: {dict(net)}; worst {worst}"
    return dict(net)
