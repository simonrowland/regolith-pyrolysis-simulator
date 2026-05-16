"""Acceptance tests for goal #10 ``VAPOROCK-AUTHORITY-PROMOTION``.

Five scenarios bind the authority swap:

1. VapoRock available + no flag -> VapoRock dispatches; builtin is not
   called.
2. VapoRock unavailable + no flag -> ``ProviderUnavailableError``.
3. VapoRock unavailable + ``allow_fallback_vapor=True`` -> builtin
   dispatches; the kernel tags the result with ``kernel_fallback_used``.
4. VapoRock available + flag=True -> still VapoRock (flag only affects
   fallback, not preference).
5. ``capability_summary()`` reflects current authority + fallback +
   shadow registrations honestly.

Tests intentionally do NOT depend on the upstream ``vaporock`` library
being importable.  Availability is forced via monkeypatch on the
provider's ``_ensure_backend`` / backend ``is_available`` hooks so the
five scenarios are deterministic regardless of the host environment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from engines.vaporock import VapoRockDiagnostics, VapoRockProvider
from simulator.chemistry.kernel import (
    ChemistryIntent,
    ProviderUnavailableError,
)
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend


DATA_DIR = Path(__file__).resolve().parent.parent.parent / 'data'


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


@pytest.fixture(scope='module')
def vapor_pressure_data() -> dict:
    return _load_yaml('vapor_pressures.yaml')


@pytest.fixture(scope='module')
def feedstocks_data() -> dict:
    return _load_yaml('feedstocks.yaml')


@pytest.fixture(scope='module')
def setpoints_data() -> dict:
    return _load_yaml('setpoints.yaml')


def _build_sim(
    vapor_pressure_data: dict,
    feedstocks_data: dict,
    setpoints_data: dict,
    *,
    allow_fallback_vapor: bool = False,
) -> PyrolysisSimulator:
    """Build a simulator with the requested fallback opt-in flag."""

    backend = StubBackend()
    backend.initialize({})
    setpoints = dict(setpoints_data)
    kernel_cfg = dict(setpoints.get('chemistry_kernel', {}) or {})
    kernel_cfg['allow_fallback_vapor'] = bool(allow_fallback_vapor)
    setpoints['chemistry_kernel'] = kernel_cfg
    sim = PyrolysisSimulator(
        backend, setpoints, feedstocks_data, vapor_pressure_data
    )
    sim.load_batch('lunar_mare_low_ti', mass_kg=1000.0)
    return sim


def _force_vaporock_unavailable(sim: PyrolysisSimulator) -> VapoRockProvider:
    """Patch the registry's VapoRock provider to report unavailable.

    Returns the patched provider so the test can also spy on its
    dispatch surface if needed.  The patch:

    * replaces ``_ensure_backend`` so the provider never re-constructs
      the adapter after the patch is applied, and
    * sets ``is_available`` on the cached adapter (if any) to False so
      the provider's existing availability probe lands in the
      ``unavailable`` branch.
    """
    registry = sim._chem_registry
    provider = registry.authoritative_for(ChemistryIntent.VAPOR_PRESSURE)
    assert isinstance(provider, VapoRockProvider)
    cached = getattr(provider, '_backend', None)
    if cached is not None and hasattr(cached, 'is_available'):
        cached.is_available = lambda: False  # type: ignore[assignment]
    provider._ensure_backend = lambda: cached  # type: ignore[method-assign]
    return provider


def _force_vaporock_available(sim: PyrolysisSimulator) -> VapoRockProvider:
    """Install a deterministic VapoRock substitute on the registry slot.

    The substitute provider returns a fixed ``IntentResult`` so the test
    does not depend on the upstream ``vaporock`` library being
    importable.  The provider keeps the real VapoRockProvider's
    capability profile (authoritative VAPOR_PRESSURE) so the registry
    accepts it without further wiring.
    """
    registry = sim._chem_registry
    provider = registry.authoritative_for(ChemistryIntent.VAPOR_PRESSURE)
    assert isinstance(provider, VapoRockProvider)

    # Build a fake backend that the provider's existing code path can
    # exercise via _ensure_backend / equilibrate.  This is more
    # faithful than replacing ``dispatch`` outright: the provider's
    # control_audit + diagnostic build still runs, and the test
    # exercises the same code path the production swap will hit.
    class _FakeBackend:
        def is_available(self) -> bool:
            return True

        def get_engine_version(self) -> str:
            return 'fake-1.0'

        def equilibrate(self, **_: Any):
            from simulator.melt_backend.base import EquilibriumResult

            return EquilibriumResult(
                temperature_C=1500.0,
                pressure_bar=1e-6,
                fO2_log=-9.0,
                status='ok',
                # The SiO value is anchored to the SF2004 Table 9
                # back-solve (0.0131 Pa for tholeiitic basalt at 1900 K).
                # The earlier placeholder of 67.8 Pa was chosen to match
                # the builtin Antoine's wrong 33 Pa scale and would
                # mask any future literature-anchored validation of the
                # authoritative-path output. Anchored value chosen
                # 2026-05-16 per \\goal VAPOROCK-SIO-DIVERGENCE (chunk
                # 24/Phase-2) -- see docs-private/sio-parity-
                # investigation-2026-05-16.md for the literature
                # derivation.
                vapor_pressures_Pa={'Na': 1234.5, 'SiO': 0.0131},
            )

    fake = _FakeBackend()
    provider._backend = fake
    provider._backend_initialised = True
    provider._ensure_backend = lambda: fake  # type: ignore[method-assign]
    return provider


# ---------------------------------------------------------------------
# Scenario 1: VapoRock available + no flag -> VapoRock dispatches
# ---------------------------------------------------------------------


def test_vaporock_available_no_flag_dispatches_through_vaporock(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        allow_fallback_vapor=False,
    )
    _force_vaporock_available(sim)

    # Spy on the builtin so a fallback dispatch can be detected.
    builtin_dispatches = 0
    original_builtin_dispatch = BuiltinVaporPressureProvider.dispatch

    def _spy(self, request):
        nonlocal builtin_dispatches
        builtin_dispatches += 1
        return original_builtin_dispatch(self, request)

    BuiltinVaporPressureProvider.dispatch = _spy
    try:
        result = sim._chem_kernel.dispatch(
            ChemistryIntent.VAPOR_PRESSURE,
            temperature_C=1500.0,
            pressure_bar=1e-6,
            control_inputs={'pO2_bar': 1e-9},
        )
    finally:
        BuiltinVaporPressureProvider.dispatch = original_builtin_dispatch

    assert builtin_dispatches == 0, (
        'builtin provider must NOT be dispatched when VapoRock is available '
        'and the fallback flag is False'
    )
    assert result.status == 'ok'
    # The fake VapoRock backend returns Na + SiO at fixed pressures;
    # the provider's filter keeps both (both are in the YAML's
    # metals/oxide_vapors sections), so they appear in the diagnostic.
    vapor = dict(result.diagnostic.get('vapor_pressures_Pa') or {})
    assert vapor == {'Na': pytest.approx(1234.5), 'SiO': pytest.approx(0.0131)}
    assert 'kernel_fallback_used' not in dict(result.diagnostic or {})


# ---------------------------------------------------------------------
# Scenario 2: VapoRock unavailable + no flag -> ProviderUnavailableError
# ---------------------------------------------------------------------


def test_vaporock_unavailable_no_flag_raises_provider_unavailable(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        allow_fallback_vapor=False,
    )
    _force_vaporock_unavailable(sim)

    builtin_dispatches = 0
    original_builtin_dispatch = BuiltinVaporPressureProvider.dispatch

    def _spy(self, request):
        nonlocal builtin_dispatches
        builtin_dispatches += 1
        return original_builtin_dispatch(self, request)

    BuiltinVaporPressureProvider.dispatch = _spy
    try:
        with pytest.raises(ProviderUnavailableError):
            sim._chem_kernel.dispatch(
                ChemistryIntent.VAPOR_PRESSURE,
                temperature_C=1500.0,
                pressure_bar=1e-6,
                control_inputs={'pO2_bar': 1e-9},
            )
    finally:
        BuiltinVaporPressureProvider.dispatch = original_builtin_dispatch

    assert builtin_dispatches == 0, (
        'builtin fallback must NOT run when allow_fallback_vapor=False '
        '(silent fallback is forbidden by goal #10 spec)'
    )


# ---------------------------------------------------------------------
# Scenario 3: VapoRock unavailable + flag=True -> builtin dispatches
# ---------------------------------------------------------------------


def test_vaporock_unavailable_with_flag_uses_builtin_fallback(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        allow_fallback_vapor=True,
    )
    _force_vaporock_unavailable(sim)

    builtin_dispatches = 0
    original_builtin_dispatch = BuiltinVaporPressureProvider.dispatch

    def _spy(self, request):
        nonlocal builtin_dispatches
        builtin_dispatches += 1
        return original_builtin_dispatch(self, request)

    BuiltinVaporPressureProvider.dispatch = _spy
    try:
        result = sim._chem_kernel.dispatch(
            ChemistryIntent.VAPOR_PRESSURE,
            temperature_C=1500.0,
            pressure_bar=1e-6,
            control_inputs={'pO2_bar': 1e-9},
        )
    finally:
        BuiltinVaporPressureProvider.dispatch = original_builtin_dispatch

    assert builtin_dispatches == 1, (
        'builtin fallback must dispatch exactly once when VapoRock is '
        'unavailable and allow_fallback_vapor=True'
    )
    assert result.status == 'ok'
    # Kernel tags the fallback result with the fallback provider_id so
    # trace consumers can tell the authoritative slot did not answer.
    diagnostic = dict(result.diagnostic or {})
    assert diagnostic.get('kernel_fallback_used') == 'builtin-vapor-pressure'
    # The builtin's vapor_pressures_Pa surface stays on the diagnostic
    # (untouched by the fallback wrapper).
    assert 'vapor_pressures_Pa' in diagnostic


# ---------------------------------------------------------------------
# Scenario 4: VapoRock available + flag=True -> still VapoRock
# ---------------------------------------------------------------------


def test_flag_does_not_override_authoritative_when_vaporock_available(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        allow_fallback_vapor=True,
    )
    _force_vaporock_available(sim)

    builtin_dispatches = 0
    original_builtin_dispatch = BuiltinVaporPressureProvider.dispatch

    def _spy(self, request):
        nonlocal builtin_dispatches
        builtin_dispatches += 1
        return original_builtin_dispatch(self, request)

    BuiltinVaporPressureProvider.dispatch = _spy
    try:
        result = sim._chem_kernel.dispatch(
            ChemistryIntent.VAPOR_PRESSURE,
            temperature_C=1500.0,
            pressure_bar=1e-6,
            control_inputs={'pO2_bar': 1e-9},
        )
    finally:
        BuiltinVaporPressureProvider.dispatch = original_builtin_dispatch

    assert builtin_dispatches == 0, (
        'fallback flag must NOT divert dispatch when the authoritative '
        'provider is available'
    )
    # The fake VapoRock substitute returns Na + SiO; this confirms the
    # authoritative path produced the result.
    vapor = dict(result.diagnostic.get('vapor_pressures_Pa') or {})
    assert vapor.get('Na') == pytest.approx(1234.5)
    assert vapor.get('SiO') == pytest.approx(0.0131)
    assert 'kernel_fallback_used' not in dict(result.diagnostic or {})


# ---------------------------------------------------------------------
# Scenario 5: capability_summary() reflects authority + fallback honestly
# ---------------------------------------------------------------------


def test_capability_summary_reports_authority_swap(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        allow_fallback_vapor=False,
    )

    summary = sim._chem_registry.capability_summary()

    vapor_entry = summary.get(ChemistryIntent.VAPOR_PRESSURE.value)
    assert vapor_entry is not None, (
        'capability_summary must list VAPOR_PRESSURE -- goal #10 binds it'
    )
    assert vapor_entry['authoritative'] == 'vaporock', (
        'authoritative slot for VAPOR_PRESSURE must be vaporock after '
        'goal #10 authority promotion'
    )
    assert vapor_entry['fallback'] == 'builtin-vapor-pressure', (
        'fallback slot for VAPOR_PRESSURE must be the builtin Antoine '
        'provider after goal #10 demotion'
    )
    # The other authoritative builtins stay
    # authoritative; capability_summary is a single read of the
    # post-swap state and must show every kernel-registered intent.
    expected_builtin_intents = {
        'evaporation_flux': 'builtin-evaporation-flux',
        'evaporation_transition': 'builtin-evaporation-transition',
        'condensation_route': 'builtin-condensation-route',
        'electrolysis_step': 'builtin-electrolysis-step',
        'metallothermic_step': 'builtin-metallothermic-step',
        'stage0_pretreatment': 'builtin-stage0-pretreatment',
        'overhead_gas_equilibrium': 'builtin-overhead-gas-equilibrium',
        'overhead_bleed': 'builtin-overhead-bleed',
    }
    for intent, expected_provider_id in expected_builtin_intents.items():
        entry = summary.get(intent)
        assert entry is not None, (
            f'capability_summary must list {intent!r} (still authoritative '
            f'builtin under goal #7)'
        )
        assert entry['authoritative'] == expected_provider_id, (
            f'{intent}: authoritative provider must remain '
            f'{expected_provider_id} (goal #10 only swaps VAPOR_PRESSURE)'
        )
        assert entry['fallback'] is None, (
            f'{intent}: no fallback should be registered (only '
            f'VAPOR_PRESSURE has a fallback under goal #10)'
        )


# ---------------------------------------------------------------------
# Supporting invariant: VapoRockProvider is writer-pure (no LedgerTransitionProposal).
# ---------------------------------------------------------------------


def test_vaporock_provider_module_does_not_import_ledger_transition_proposal():
    """Mirrors the alphamelts / magemin AST guard.

    VAPOR_PRESSURE is a read-only intent.  The provider must NEVER
    construct a :class:`LedgerTransitionProposal`; an accidental import
    is the first signal that someone is about to plumb a write path
    through the diagnostic surface.
    """

    import ast
    import pathlib

    source_path = pathlib.Path(
        'engines/vaporock/provider.py'
    ).resolve()
    if not source_path.exists():
        # Fallback for repos installed off the worktree.
        from engines.vaporock import provider as vp_module

        source_path = pathlib.Path(vp_module.__file__).resolve()
    source = source_path.read_text(encoding='utf-8')
    tree = ast.parse(source)

    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == 'LedgerTransitionProposal':
                    bad.append(f'from {node.module} import {alias.name}')
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith('LedgerTransitionProposal'):
                    bad.append(f'import {alias.name}')

    assert not bad, (
        f'VapoRockProvider must not import LedgerTransitionProposal '
        f'(diagnostic-only intent); found: {bad}'
    )


def test_vaporock_diagnostics_payload_round_trips():
    """``VapoRockDiagnostics.as_diagnostic`` returns a plain dict
    matching the dataclass fields.  Trace consumers pin on these
    keys; the dataclass is the contract.
    """

    diag = VapoRockDiagnostics(
        vapor_pressures_Pa={'Na': 100.0},
        activities={},
        pO2_bar=1e-9,
        mode='fake',
        engine_version='1.0',
        backend_status='ok',
        backend_warnings=('hello',),
    )
    payload = diag.as_diagnostic()
    assert set(payload.keys()) == {
        'vapor_pressures_Pa',
        'activities',
        'pO2_bar',
        'mode',
        'engine_version',
        'backend_status',
        'backend_warnings',
    }
    assert payload['vapor_pressures_Pa'] == {'Na': 100.0}
    assert payload['backend_warnings'] == ('hello',)
