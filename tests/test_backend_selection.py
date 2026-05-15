"""
Active-backend selection policy tests (\\goal BACKEND-DEFAULT-SWITCH).

Exercises every branch in ``web.events._get_backend`` with mocked
``is_available()`` flags so the test suite runs without PetThermoTools,
ChemApp, VapoRock, or MAGEMin actually installed.

Policy under test:

* AlphaMELTS is probed first; selected when ``is_available()`` is True.
* FactSAGE is probed second; selected only when its strict-config gate
  passes (i.e. ``initialize`` returns True AND ``is_available()`` is True).
* VapoRock and MAGEMin are **never** selected as the active backend; an
  explicit request for either raises ``BackendUnavailableError``.
* StubBackend is the always-available fallback.
* The selection emits one ``engine selection: ...`` log line per call.
"""

from __future__ import annotations

from typing import Optional

import pytest

import web.events as events
from simulator.melt_backend.base import StubBackend
from web.events import BackendUnavailableError, _get_backend


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeBackend:
    """Minimal MeltBackend-shaped double for selection-logic tests."""

    name = 'fake'

    def __init__(self, *, available: bool, init_returns: Optional[bool] = None):
        self._available = available
        # initialize() defaults to mirroring availability so tests do not
        # have to thread two redundant flags through every fixture.
        self._init_returns = available if init_returns is None else init_returns
        self.init_calls: list[dict] = []

    def initialize(self, config):
        self.init_calls.append(dict(config or {}))
        return bool(self._init_returns)

    def is_available(self) -> bool:
        return self._available

    def capabilities(self):
        return {
            'silicate_melt': True,
            'gas_volatiles': False,
            'salt_phase': False,
            'sulfide_matte': False,
            'metal_alloy': False,
        }


class _FakeAlphaMELTS(_FakeBackend):
    name = 'alphamelts'


class _FakeFactSAGE(_FakeBackend):
    name = 'factsage'


def _install_fakes(
    monkeypatch,
    *,
    alphamelts_available: bool,
    factsage_available: bool,
    factsage_strict_config: bool = True,
):
    """Replace the four backend classes with the test doubles.

    ``factsage_strict_config`` controls whether the FactSAGE
    ``initialize()`` accepts the config; even when the binary "is
    available", a missing strict config means ``initialize`` returns
    False and the strict-config gate fails.
    """
    factsage_init = factsage_available and factsage_strict_config

    def make_alphamelts():
        return _FakeAlphaMELTS(available=alphamelts_available)

    def make_factsage():
        return _FakeFactSAGE(
            available=factsage_available and factsage_strict_config,
            init_returns=factsage_init,
        )

    monkeypatch.setattr(events, 'AlphaMELTSBackend', make_alphamelts)
    monkeypatch.setattr(events, 'FactSAGEBackend', make_factsage)
    monkeypatch.setattr(events, '_factsage_config', lambda: {})


@pytest.fixture
def captured_logs(monkeypatch):
    """Capture every ``_safe_log`` line emitted during selection."""
    lines: list[str] = []
    monkeypatch.setattr(events, '_safe_log', lines.append)
    return lines


# ---------------------------------------------------------------------------
# Autodetect chain
# ---------------------------------------------------------------------------


def test_autodetect_all_primaries_unavailable_falls_back_to_stub(
        monkeypatch, captured_logs):
    _install_fakes(monkeypatch,
                   alphamelts_available=False,
                   factsage_available=False)

    backend = _get_backend('auto')

    assert isinstance(backend, StubBackend)
    assert any('engine selection: StubBackend' in line
               for line in captured_logs)


def test_autodetect_alphamelts_available_picks_alphamelts(
        monkeypatch, captured_logs):
    _install_fakes(monkeypatch,
                   alphamelts_available=True,
                   factsage_available=True)

    backend = _get_backend('auto')

    assert isinstance(backend, _FakeAlphaMELTS)
    assert any('engine selection: _FakeAlphaMELTS' in line
               for line in captured_logs)


def test_autodetect_alphamelts_wins_even_when_vaporock_and_magemin_report_available(
        monkeypatch, captured_logs):
    """VapoRock / MAGEMin is_available() flags are irrelevant to selection."""

    class _AvailableVapoRock(_FakeBackend):
        name = 'vaporock'

    class _AvailableMAGEMin(_FakeBackend):
        name = 'magemin'

    monkeypatch.setattr(events, 'AlphaMELTSBackend',
                        lambda: _FakeAlphaMELTS(available=True))
    monkeypatch.setattr(events, 'FactSAGEBackend',
                        lambda: _FakeFactSAGE(available=False))
    # The selection logic must not probe these at all, but install them
    # under their module-level names so the test would catch any
    # regression that did probe them.
    monkeypatch.setattr(
        'simulator.melt_backend.vaporock.VapoRockBackend',
        lambda: _AvailableVapoRock(available=True),
        raising=False,
    )
    monkeypatch.setattr(
        'simulator.melt_backend.magemin.MAGEMinBackend',
        lambda: _AvailableMAGEMin(available=True),
        raising=False,
    )
    monkeypatch.setattr(events, '_factsage_config', lambda: {})

    backend = _get_backend('auto')

    assert isinstance(backend, _FakeAlphaMELTS)


def test_autodetect_factsage_available_with_strict_config_picks_factsage(
        monkeypatch, captured_logs):
    _install_fakes(monkeypatch,
                   alphamelts_available=False,
                   factsage_available=True,
                   factsage_strict_config=True)

    backend = _get_backend('auto')

    assert isinstance(backend, _FakeFactSAGE)
    assert any('engine selection: _FakeFactSAGE' in line
               for line in captured_logs)


def test_autodetect_factsage_available_without_strict_config_falls_to_stub(
        monkeypatch, captured_logs):
    """Strict-config gate fails -> FactSAGE is skipped, not silently used."""
    _install_fakes(monkeypatch,
                   alphamelts_available=False,
                   factsage_available=True,
                   factsage_strict_config=False)

    backend = _get_backend('auto')

    assert isinstance(backend, StubBackend)
    assert any('engine selection: StubBackend' in line
               for line in captured_logs)


def test_autodetect_with_vaporock_or_magemin_available_still_picks_stub(
        monkeypatch, captured_logs):
    """VapoRock/MAGEMin is_available()=True must not influence selection."""

    class _AvailableVapoRock(_FakeBackend):
        name = 'vaporock'

    class _AvailableMAGEMin(_FakeBackend):
        name = 'magemin'

    _install_fakes(monkeypatch,
                   alphamelts_available=False,
                   factsage_available=False)
    monkeypatch.setattr(
        'simulator.melt_backend.vaporock.VapoRockBackend',
        lambda: _AvailableVapoRock(available=True),
        raising=False,
    )
    monkeypatch.setattr(
        'simulator.melt_backend.magemin.MAGEMinBackend',
        lambda: _AvailableMAGEMin(available=True),
        raising=False,
    )

    backend = _get_backend('auto')

    assert isinstance(backend, StubBackend)


# ---------------------------------------------------------------------------
# Explicit named selection
# ---------------------------------------------------------------------------


def test_explicit_alphamelts_request_succeeds_when_available(
        monkeypatch, captured_logs):
    _install_fakes(monkeypatch,
                   alphamelts_available=True,
                   factsage_available=False)

    backend = _get_backend('alphamelts')

    assert isinstance(backend, _FakeAlphaMELTS)


def test_explicit_alphamelts_request_raises_when_unavailable(monkeypatch):
    _install_fakes(monkeypatch,
                   alphamelts_available=False,
                   factsage_available=False)

    with pytest.raises(BackendUnavailableError,
                       match='AlphaMELTS unavailable'):
        _get_backend('alphamelts')


def test_explicit_factsage_request_picks_factsage_with_strict_config(
        monkeypatch, captured_logs):
    _install_fakes(monkeypatch,
                   alphamelts_available=True,  # ignored by explicit factsage
                   factsage_available=True,
                   factsage_strict_config=True)

    backend = _get_backend('factsage')

    assert isinstance(backend, _FakeFactSAGE)


def test_explicit_factsage_request_falls_to_stub_without_strict_config(
        monkeypatch, captured_logs):
    """An explicit FactSAGE request without a strict config drops to Stub.

    The existing diagnostic-without-strict-config posture is preserved:
    the user gets the built-in fallback, NOT a silent substitute primary
    like AlphaMELTS (which they did not ask for).
    """
    _install_fakes(monkeypatch,
                   alphamelts_available=True,
                   factsage_available=True,
                   factsage_strict_config=False)

    backend = _get_backend('factsage')

    assert isinstance(backend, StubBackend)


def test_explicit_stub_request_uses_autodetect_chain(
        monkeypatch, captured_logs):
    """``backend='stub'`` follows the new autodetect policy.

    This is the legacy default; the new active-backend policy prefers
    AlphaMELTS when available rather than honouring the old 'stub'
    default literally.
    """
    _install_fakes(monkeypatch,
                   alphamelts_available=True,
                   factsage_available=False)

    backend = _get_backend('stub')

    assert isinstance(backend, _FakeAlphaMELTS)


def test_unknown_backend_name_falls_through_to_autodetect(
        monkeypatch, captured_logs):
    _install_fakes(monkeypatch,
                   alphamelts_available=False,
                   factsage_available=False)

    backend = _get_backend('something-else')

    assert isinstance(backend, StubBackend)


# ---------------------------------------------------------------------------
# Case-folding policy
# ---------------------------------------------------------------------------
#
# ``_get_backend`` does ``name = (backend_name or '').strip().lower()`` for
# ALL backend names. The refusal parametrize below covers case variants of
# the ineligible backends, but the case-folding line itself is load-bearing
# for every backend name: if it were ever broken (e.g. removed
# ``.lower()``), an uppercase explicit request would fall through to the
# autodetect chain instead of routing to the eligible backend the caller
# asked for. Pin the eligible names here so the case-folding contract is
# covered for every name the selector accepts.


@pytest.mark.parametrize(
    'name',
    ['alphamelts', 'AlphaMELTS', 'ALPHAMELTS', ' alphamelts '],
)
def test_explicit_alphamelts_request_is_case_insensitive_raises_when_unavailable(
        monkeypatch, name):
    # With AlphaMELTS unavailable, an explicit request (in any case) must
    # raise BackendUnavailableError instead of silently falling through to
    # the autodetect chain. If case-folding is broken in `_get_backend`,
    # the uppercase variants miss the `if name == 'alphamelts':` branch
    # and fall into autodetect, returning Stub -- this test catches that.
    _install_fakes(monkeypatch,
                   alphamelts_available=False,
                   factsage_available=False)

    with pytest.raises(BackendUnavailableError,
                       match='AlphaMELTS unavailable'):
        _get_backend(name)


@pytest.mark.parametrize(
    'name',
    ['factsage', 'FactSAGE', 'FACTSAGE', ' factsage '],
)
def test_explicit_factsage_request_is_case_insensitive(
        monkeypatch, name, captured_logs):
    # An explicit FactSAGE request without a strict config must drop to
    # Stub regardless of case. If case-folding is broken the uppercase
    # variants miss the `if name == 'factsage':` branch and fall through
    # to autodetect, which would pick AlphaMELTS (available=True) -- a
    # silent substitution this test catches.
    _install_fakes(monkeypatch,
                   alphamelts_available=True,
                   factsage_available=True,
                   factsage_strict_config=False)

    backend = _get_backend(name)

    assert isinstance(backend, StubBackend), backend


@pytest.mark.parametrize(
    'name',
    ['auto', 'Auto', 'AUTO', ' auto '],
)
def test_autodetect_request_is_case_insensitive(
        monkeypatch, name, captured_logs):
    # 'auto' is in the unknown-fall-through bucket, but the case-folding
    # is still load-bearing: with case-folding broken AND a backend
    # request that happens to match a literal branch by coincidence
    # (no ineligibility check), the policy would not raise. Pin the
    # autodetect path with the eligible backend resolved.
    _install_fakes(monkeypatch,
                   alphamelts_available=True,
                   factsage_available=False)

    backend = _get_backend(name)

    assert isinstance(backend, _FakeAlphaMELTS)


# ---------------------------------------------------------------------------
# Explicit refusal of vaporock / magemin as the active backend
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('name', ['vaporock', 'magemin', 'VapoRock', 'MAGEMin'])
def test_vaporock_and_magemin_refused_as_active_backend(monkeypatch, name):
    """Both adapters are explicitly refused, case-insensitive.

    Even if their ``is_available()`` returns True, the selection layer
    must not route them through ``_get_equilibrium`` — they would trip
    its fail-closed reject because they leave ``ledger_transition=None``
    while populating ``phase_masses_kg`` (magemin) or returning a vapor-
    only result (vaporock).
    """
    _install_fakes(monkeypatch,
                   alphamelts_available=True,
                   factsage_available=True)

    with pytest.raises(BackendUnavailableError,
                       match='not eligible as the active melt backend'):
        _get_backend(name)


def test_refusal_message_names_kernel_carve_out_prerequisite(monkeypatch):
    _install_fakes(monkeypatch,
                   alphamelts_available=False,
                   factsage_available=False)

    with pytest.raises(BackendUnavailableError) as exc_info:
        _get_backend('vaporock')

    assert 'CHEMISTRY-KERNEL-CARVE-OUT' in str(exc_info.value)


# ---------------------------------------------------------------------------
# Boot-log emission
# ---------------------------------------------------------------------------


def test_engine_selection_log_emitted_on_every_selection(
        monkeypatch, captured_logs):
    _install_fakes(monkeypatch,
                   alphamelts_available=True,
                   factsage_available=False)

    _get_backend('auto')

    selection_lines = [line for line in captured_logs
                       if line.startswith('engine selection:')]
    assert len(selection_lines) == 1
    line = selection_lines[0]
    assert 'silicate_melt=' in line
    assert 'gas_volatiles=' in line
    assert 'VapoRock/MAGEMin not eligible until kernel' in line


def test_engine_selection_log_also_emitted_on_stub_fallback(
        monkeypatch, captured_logs):
    _install_fakes(monkeypatch,
                   alphamelts_available=False,
                   factsage_available=False)

    _get_backend('auto')

    selection_lines = [line for line in captured_logs
                       if line.startswith('engine selection: StubBackend')]
    assert len(selection_lines) == 1


def test_engine_selection_log_records_capabilities_for_factsage(
        monkeypatch, captured_logs):
    _install_fakes(monkeypatch,
                   alphamelts_available=False,
                   factsage_available=True,
                   factsage_strict_config=True)

    _get_backend('auto')

    selection_lines = [line for line in captured_logs
                       if line.startswith('engine selection:')]
    assert len(selection_lines) == 1
    assert '_FakeFactSAGE' in selection_lines[0]
    assert 'silicate_melt=true' in selection_lines[0]
