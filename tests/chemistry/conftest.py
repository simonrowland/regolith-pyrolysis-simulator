"""Shared fixtures for the builtin chemistry provider tests.

Scoped to ``tests/chemistry/``. The YAML fixtures are module-scoped so they
are constructed once per test module. The canonical provider full-run fixture
is package-scoped so byte-identical consumers in separate provider modules can
share it. Pytest only injects these fixtures where a test explicitly requests
them, so the kernel test files (which do not request them) are unaffected.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import CampaignPhase, DecisionType, MOLAR_MASS
from simulator.vapour_rail.catalog import vapor_pressure_compatibility_view


DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _native_fe_saturating_feedstocks(feedstocks, feedstock_id):
    """Existing synthetic alkali-rich split with a finite saturation band."""
    feeds = deepcopy(feedstocks)
    feed = feeds[feedstock_id]
    feed.pop('iron_speciation', None)
    feed.pop('fe_redox_split_unknown', None)
    # q = 2*n(Fe2O3)/(n(FeO)+2*n(Fe2O3)); hence n(Fe2O3) =
    # n(FeO)*q/(2*(1-q)). Multiplying the FeO mass weight by
    # M(Fe2O3)/M(FeO) is dimensionless (both g/mol), retaining its mass basis.
    # q=.039 keeps the propagated precision band finite; q->0 gives no ferric.
    feed['composition_wt_pct'].update({
        'FeO': 60., 'K2O': 80., 'SiO2': 40.,
        'Fe2O3': 60. / MOLAR_MASS['FeO'] * 0.039 / (2 * (1 - 0.039)) * MOLAR_MASS['Fe2O3'],
    })
    feed['composition_basis'] = {
        oxide: {'method': 'synthetic test split', 'source': 'test fixture'}
        for oxide in ('FeO', 'Fe2O3')
    }
    return feeds


def _seed_melt_redox(sim, fO2_log: float) -> None:
    """Establish a test split through the real, funded Fe/O2 provider."""
    from simulator.accounting import MaterialLot
    from simulator.chemistry.kernel import ChemistryIntent
    from simulator.fe_redox import kress91_oxygen_demand_mol, melt_mol_fractions_for_kress91

    # Establish the requested environment when the fixture has no actual gas
    # probe yet. Preserve explicit projections, including finite N2 probes
    # beside an independently funded oxygen ledger.
    if not sim._melt_headspace_composition_mbar:
        sim._install_upstream_projection({'N2': sim.melt.p_total_mbar})
    original_T = sim.melt.temperature_C
    before_buffer_kg = sim.atom_ledger.kg_by_account('reservoir.fo2_buffer').get('O2', 0.0)
    if original_T < 1200.0:
        sim.melt.temperature_C = sim._current_melt_redox_reference_T_K() - 273.15
    T_K = sim.melt.temperature_C + 273.15
    inv = sim.atom_ledger.mol_by_account('process.cleaned_melt')
    demand = kress91_oxygen_demand_mol(
        feo_mol=inv.get('FeO', 0.0), fe2o3_mol=inv.get('Fe2O3', 0.0),
        fO2_log=fO2_log, T_K=T_K,
        pressure_bar=max(sim._melt_headspace_total_pressure_bar(), sim._vacuum_floor_bar()),
        mol_fractions=melt_mol_fractions_for_kress91(sim._melt_oxide_wt_pct()))
    if demand > 0.0:
        lot = sim.atom_ledger.load_external_mol('reservoir.fo2_buffer', {'O2': demand},
            source='test fixture redox oxygen', material_origin='feedstock')
        key = 'test_redox_fixture_oxygen'
        sim.inventory.stage0_external_inputs_kg[key] = (
            sim.inventory.stage0_external_inputs_kg.get(key, 0.0)
            + lot.total_mass_kg(sim.atom_ledger.registry))
    controls = {'oxygen_source': 'fo2_buffer', 'internal_o2_capacity_mol': max(demand, 0.0)}
    result = sim._dispatch_only(ChemistryIntent.FE_REDOX_RESPECIATION,
        fO2_log=fO2_log, control_inputs=controls)
    assert result.status == 'ok'
    if result.transition is not None:
        sim._commit_proposal(ChemistryIntent.FE_REDOX_RESPECIATION,
            result.transition, diagnostic=result.diagnostic, control_inputs=controls)
    # Park released fixture gas outside the test headspace/buffer so later
    # no-phantom-O2 tests start with the empty gas inventories they specify.
    gas = max(0.0, sim.atom_ledger.kg_by_account('reservoir.fo2_buffer').get('O2', 0.0)
              - before_buffer_kg)
    if gas > 0.0:
        sim.atom_ledger.transfer('test_fixture_redox_gas_removal',
            [MaterialLot('reservoir.fo2_buffer', {'O2': gas})],
            [MaterialLot('terminal.oxygen_melt_offgas_stored', {'O2': gas})])
    sim._project_cleaned_melt_from_atom_ledger()
    sim.melt.oxygen_reservoir.reference_T_K = T_K
    sim._sync_oxygen_reservoir_mirror()
    store = sim._pt0_store()
    tolerance = (store.control_quantization.log_fo2_quantum
                 if store is not None and store.quantize_live_controls else 2e-13)
    assert sim._current_melt_redox_fO2_log() == pytest.approx(fO2_log, abs=tolerance)
    sim.melt.temperature_C = original_T


def _load_yaml(name: str) -> dict:
    payload = yaml.safe_load((DATA_DIR / name).read_text())
    if name == "vapor_pressures.yaml":
        # VR-11: retain schema-v2 catalog_payload so sims compile
        # VapourRailCatalog / VapourBatch (legacy-only view left catalog=None
        # and made batch-required flux fail closed).
        return vapor_pressure_compatibility_view(payload)
    return payload


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
    """Build a PyrolysisSimulator with a fresh InternalAnalyticalBackend.

    Helper -- intentionally not a pytest fixture so callers can pass
    feedstock-specific arguments per test.

    ``builtin_vapor_pressure``: when True (the default), the simulator
    is constructed with ``allow_fallback_vapor=True`` AND the
    :class:`VapoRockProvider` registered for the build is forced to
    report itself unavailable.  Together these route every
    ``VAPOR_PRESSURE`` dispatch through the registered builtin Antoine
    provider -- the call path these chemistry-suite tests were written
    against under goal #7.  The
    ``VAPOROCK-AUTHORITY-PROMOTION`` name is historical only: builtin
    Antoine/Ellingham is the authoritative ``VAPOR_PRESSURE`` provider,
    and VapoRock is a diagnostic-only shadow. Setting
    ``builtin_vapor_pressure=False`` exercises the non-forced diagnostic
    wiring used by ``test_vaporock_authority_promotion.py``.
    """

    backend = InternalAnalyticalBackend()
    backend.initialize({})
    if builtin_vapor_pressure:
        # Goal #10 historical name only: keep these tests on the builtin
        # authoritative path. A shallow copy keeps the
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
        # dispatch so the builtin path is isolated from optional
        # VapoRock diagnostics. This mirrors the
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


def _simulator_ledger_fingerprint(sim: PyrolysisSimulator) -> tuple:
    sim.atom_ledger.assert_balanced()
    return (
        tuple(sorted(sim.atom_ledger.total_mol_by_account().items())),
        tuple(sorted(sim.atom_ledger.mol_by_species().items())),
        tuple(sorted(sim.melt.composition_kg.items())),
        tuple(sorted(sim.product_ledger().items())),
    )


@pytest.fixture(
    scope="package",
    params=[
        pytest.param(
            ("lunar_mare_low_ti", None),
            id="lunar_mare_low_ti-None",
        ),
        pytest.param(
            ("mars_basalt", {"C": 60.0}),
            id="mars_basalt-additives_kg1",
        ),
        pytest.param(
            ("s_type_asteroid_silicate", None),
            id="s_type_asteroid_silicate-None",
        ),
    ],
)
def full_builtin_provider_run(request):
    """Run one canonical C0 -> C6 input for all provider-family consumers."""

    feedstock_key, additives_kg = request.param
    sim = _build_sim(
        feedstock_key,
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
        additives_kg=additives_kg,
    )
    sim.start_campaign(CampaignPhase.C0)
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A",
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    steps = 0
    while not sim.is_complete() and steps < 5000:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        sim.step()
        steps += 1
    return feedstock_key, additives_kg, sim


@pytest.fixture(scope="package")
def _full_builtin_provider_run_fingerprint(full_builtin_provider_run):
    return _simulator_ledger_fingerprint(full_builtin_provider_run[2])


@pytest.fixture(autouse=True)
def _guard_full_builtin_provider_run(request):
    if "full_builtin_provider_run" not in request.fixturenames:
        yield
        return
    _, _, sim = request.getfixturevalue("full_builtin_provider_run")
    expected = request.getfixturevalue("_full_builtin_provider_run_fingerprint")
    yield
    assert _simulator_ledger_fingerprint(sim) == expected


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
