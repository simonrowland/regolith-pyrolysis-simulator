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
    builtin_vapor_pressure: bool = True,
) -> PyrolysisSimulator:
    """Build a PyrolysisSimulator with a fresh StubBackend.

    Helper -- intentionally not a pytest fixture so callers can pass
    feedstock-specific arguments per test.

    ``builtin_vapor_pressure``: when True (the default), the simulator
    is constructed with ``allow_fallback_vapor=True`` AND the
    :class:`VapoRockProvider` registered for the build is forced to
    report itself unavailable.  Together these route every
    ``VAPOR_PRESSURE`` dispatch through the registered builtin Antoine
    fallback -- the call path these chemistry-suite tests were written
    against under goal #7.  Goal #10
    ``VAPOROCK-AUTHORITY-PROMOTION`` made VapoRock the authoritative
    holder of the intent; setting ``builtin_vapor_pressure=False``
    opts into the new path (used by
    ``test_vaporock_authority_promotion.py``).
    """

    backend = StubBackend()
    backend.initialize({})
    if builtin_vapor_pressure:
        # Goal #10: ensure the simulator wiring opts into the fallback
        # path for these tests.  A shallow copy keeps the
        # module-scoped fixture immutable across tests; deepcopy is
        # avoided to keep the simulator's references to the
        # campaign/condensation dicts identical to the un-patched
        # path.
        setpoints_data = dict(setpoints_data)
        kernel_cfg = dict(setpoints_data.get('chemistry_kernel', {}) or {})
        kernel_cfg['allow_fallback_vapor'] = True
        # V1e-impl TIER-3 fail-loud (Cr/CrO2/Mn missing measured alpha)
        # requires opt-in for fallback path tests. Mirrors the per-species
        # measured-alpha policy: production stays fail-loud; test fixtures
        # that exercise the fallback chain opt in to alpha=1.0 prototype.
        kernel_cfg['allow_unmeasured_alpha_fallback'] = True
        setpoints_data['chemistry_kernel'] = kernel_cfg
    sim = PyrolysisSimulator(
        backend, setpoints_data, feedstocks_data, vapor_pressure_data
    )
    if builtin_vapor_pressure:
        # The kernel registry is built lazily by ``load_batch``; force
        # VapoRock's availability probe to fail before the first
        # dispatch so the fallback fires.  This mirrors the
        # environment-level expectation that VapoRock is optional --
        # the chemistry-suite tests stay green even when the
        # upstream library is installed.
        _force_vaporock_unavailable_for_sim(sim)
    sim.load_batch(feedstock_key, mass_kg=1000.0, additives_kg=additives_kg)
    if builtin_vapor_pressure:
        # ``load_batch`` rebuilds the kernel facade against a fresh
        # registry instance for the freshly seeded ledger, so the
        # availability monkeypatch above is re-applied after the
        # batch load to keep VapoRock unavailable for subsequent
        # dispatches.  The provider object lives on the registry; we
        # mutate it in place.
        _force_vaporock_unavailable_for_sim(sim)
    return sim


def _force_vaporock_unavailable_for_sim(sim: PyrolysisSimulator) -> None:
    """Make the simulator's registered :class:`VapoRockProvider` raise on
    dispatch so the kernel's fallback path takes over.

    Used by :func:`_build_sim` to keep the goal-#7 chemistry-suite
    parity tests green after goal #10's authority swap.  The function
    walks the registry's authoritative slot for ``VAPOR_PRESSURE`` and
    flips an internal flag so the provider's availability probe always
    reports False; the provider then raises
    :class:`ProviderUnavailableError` at dispatch time, the kernel sees
    the matching opt-in flag in
    :attr:`ChemistryKernel.allow_fallback_intents`, and the registered
    fallback (the builtin Antoine provider) answers the dispatch.
    """

    from simulator.chemistry.kernel.capabilities import ChemistryIntent

    registry = sim._chem_registry
    provider = registry.authoritative_for(ChemistryIntent.VAPOR_PRESSURE)
    if provider is None:
        return

    def _always_unavailable() -> bool:
        return False

    # Patch the live backend (if any) to report unavailable; also
    # clear the cached adapter so the lazy probe re-runs and pulls a
    # fresh value.  The two writes together force any future
    # ``_ensure_backend`` call to land in the ``unavailable`` branch
    # regardless of whether the upstream library imports cleanly.
    backend = getattr(provider, '_backend', None)
    if backend is not None and hasattr(backend, 'is_available'):
        backend.is_available = _always_unavailable  # type: ignore[assignment]
    # Replace ``_ensure_backend`` outright so the provider never
    # constructs a fresh adapter when the patched one is missing.
    provider._ensure_backend = lambda: backend  # type: ignore[method-assign]


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
