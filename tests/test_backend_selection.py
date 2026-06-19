"""
Active-backend selection policy tests (\\goal BACKEND-DEFAULT-SWITCH).

Exercises every branch in ``web.events._get_backend`` with mocked
``is_available()`` flags so the test suite runs without PetThermoTools,
ChemApp, VapoRock, or MAGEMin actually installed.

Policy under test:

* AlphaMELTS is probed first; selected when ``is_available()`` is True.
* VapoRock and MAGEMin are **never** selected as the active backend; an
  explicit request for either raises ``BackendUnavailableError``.
* StubBackend is the always-available fallback for ``auto`` / unset.
* Explicit unknown names fail loud.
* The selection emits one ``engine selection: ...`` log line per call.
"""

from __future__ import annotations

from typing import Optional

import pytest

import web.events as events
from simulator.backends import (
    BackendSelectionPolicy,
    assert_stage0_subprocess_backend_safe,
    resolve_backend,
)
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


def _install_fakes(
    monkeypatch,
    *,
    alphamelts_available: bool,
):
    """Replace the AlphaMELTS backend class with the test double."""

    def make_alphamelts():
        return _FakeAlphaMELTS(available=alphamelts_available)

    monkeypatch.setattr(events, 'AlphaMELTSBackend', make_alphamelts)


@pytest.fixture
def captured_logs(monkeypatch):
    """Capture every ``_safe_log`` line emitted during selection."""
    lines: list[str] = []
    monkeypatch.setattr(events, '_safe_log', lines.append)
    return lines


def test_shared_resolver_requires_explicit_policy():
    with pytest.raises(TypeError):
        resolve_backend('stub')


def test_runner_strict_rejects_auto():
    with pytest.raises(BackendUnavailableError, match='auto backend selection'):
        resolve_backend('auto', BackendSelectionPolicy.RUNNER_STRICT)


def test_runner_strict_keeps_legacy_exact_name_matching():
    with pytest.raises(BackendUnavailableError, match="unknown backend 'Auto'"):
        resolve_backend('Auto', BackendSelectionPolicy.RUNNER_STRICT)


def test_web_autodetect_policy_preserves_probe_order():
    calls: list[str] = []

    def make_alphamelts():
        calls.append('alphamelts')
        return _FakeAlphaMELTS(available=False)

    def make_stub():
        calls.append('stub')
        return StubBackend()

    backend = resolve_backend(
        'auto',
        BackendSelectionPolicy.WEB_AUTODETECT,
        alphamelts_backend_cls=make_alphamelts,
        stub_backend_cls=make_stub,
        log_selection=lambda selected: None,
    )

    assert isinstance(backend, StubBackend)
    assert calls == ['alphamelts', 'stub']


def test_stage0_required_alphamelts_resolution_forces_subprocess_copy():
    source_config = {
        "mode": "thermoengine",
        "python_bridge": "python_api",
        "alphamelts": {"mode": "thermoengine"},
    }
    instances: list[_FakeAlphaMELTS] = []

    class _RoutedAlphaMELTS(_FakeAlphaMELTS):
        def initialize(self, config):
            self._mode = str(config.get("mode") or "")
            return super().initialize(config)

    def make_alphamelts():
        backend = _RoutedAlphaMELTS(available=True)
        instances.append(backend)
        return backend

    backend = resolve_backend(
        "alphamelts",
        BackendSelectionPolicy.RUNNER_STRICT,
        alphamelts_backend_cls=make_alphamelts,
        backend_config=source_config,
        feedstock_id="spinel-feed",
        feedstocks={"spinel-feed": {"spinel_rich": True}},
    )

    assert backend is instances[0]
    assert getattr(backend, "_mode") == "subprocess"
    assert getattr(backend, "stage0_subprocess_required") is True
    assert instances[0].init_calls == [
        {
            "mode": "subprocess",
            "python_bridge": "subprocess",
            "alphamelts": {
                "mode": "subprocess",
                "python_bridge": "subprocess",
            },
        }
    ]
    assert source_config == {
        "mode": "thermoengine",
        "python_bridge": "python_api",
        "alphamelts": {"mode": "thermoengine"},
    }


def test_stage0_required_rejects_reused_non_subprocess_backend():
    backend = _FakeAlphaMELTS(available=True)
    backend._mode = "thermoengine"

    with pytest.raises(BackendUnavailableError, match="requires subprocess"):
        assert_stage0_subprocess_backend_safe(
            backend,
            subprocess_required=True,
            unavailable_error_cls=BackendUnavailableError,
        )


# ---------------------------------------------------------------------------
# Autodetect chain
# ---------------------------------------------------------------------------


def test_autodetect_all_primaries_unavailable_falls_back_to_stub(
        monkeypatch, captured_logs):
    _install_fakes(monkeypatch,
                   alphamelts_available=False)

    backend = _get_backend('auto')

    assert isinstance(backend, StubBackend)
    assert any('engine selection: StubBackend' in line
               for line in captured_logs)


def test_autodetect_alphamelts_available_picks_alphamelts(
        monkeypatch, captured_logs):
    _install_fakes(monkeypatch,
                   alphamelts_available=True)

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

    backend = _get_backend('auto')

    assert isinstance(backend, _FakeAlphaMELTS)


def test_autodetect_with_vaporock_or_magemin_available_still_picks_stub(
        monkeypatch, captured_logs):
    """VapoRock/MAGEMin is_available()=True must not influence selection."""

    class _AvailableVapoRock(_FakeBackend):
        name = 'vaporock'

    class _AvailableMAGEMin(_FakeBackend):
        name = 'magemin'

    _install_fakes(monkeypatch,
                   alphamelts_available=False)
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
                   alphamelts_available=True)

    backend = _get_backend('alphamelts')

    assert isinstance(backend, _FakeAlphaMELTS)


def test_explicit_alphamelts_request_raises_when_unavailable(monkeypatch):
    _install_fakes(monkeypatch,
                   alphamelts_available=False)

    with pytest.raises(BackendUnavailableError,
                       match='AlphaMELTS unavailable'):
        _get_backend('alphamelts')


def test_explicit_stub_request_pins_stub_backend(
        monkeypatch, captured_logs):
    """``backend='stub'`` deterministically pins StubBackend (D1 fix).

    An explicit 'stub' request must NOT autodetect: even when AlphaMELTS
    is available, asking for the deterministic stub returns StubBackend.
    Only 'auto'/'' follow the autodetect chain. (Bug D1: 'stub'
    previously routed through autodetect and returned AlphaMELTS when it
    was installed, so a caller asking for a deterministic backend silently
    got AlphaMELTS.)
    """
    _install_fakes(monkeypatch,
                   alphamelts_available=True)

    backend = _get_backend('stub')

    assert isinstance(backend, StubBackend)
    assert any('engine selection: StubBackend' in line
               for line in captured_logs)


def test_web_autodetect_stub_bypasses_primary_probes():
    """Under WEB_AUTODETECT, 'stub' returns StubBackend without probing
    AlphaMELTS (D1 fix at the resolver level)."""
    calls: list[str] = []

    def make_alphamelts():
        calls.append('alphamelts')
        return _FakeAlphaMELTS(available=True)

    def make_stub():
        calls.append('stub')
        return StubBackend()

    backend = resolve_backend(
        'stub',
        BackendSelectionPolicy.WEB_AUTODETECT,
        alphamelts_backend_cls=make_alphamelts,
        stub_backend_cls=make_stub,
        log_selection=lambda selected: None,
    )

    assert isinstance(backend, StubBackend)
    assert calls == ['stub']  # primaries never probed


@pytest.mark.parametrize('name', ['something-else', 'factsage', 'FactSAGE'])
def test_unknown_backend_name_fails_loud(monkeypatch, name):
    _install_fakes(monkeypatch,
                   alphamelts_available=False)

    with pytest.raises(BackendUnavailableError,
                       match='unknown backend'):
        _get_backend(name)


def test_unset_backend_still_autodetects(monkeypatch):
    calls: list[str] = []

    def make_alphamelts():
        calls.append('alphamelts')
        return _FakeAlphaMELTS(available=False)

    def make_stub():
        calls.append('stub')
        return StubBackend()

    backend = resolve_backend(
        '',
        BackendSelectionPolicy.WEB_AUTODETECT,
        alphamelts_backend_cls=make_alphamelts,
        stub_backend_cls=make_stub,
        log_selection=lambda selected: None,
    )

    assert isinstance(backend, StubBackend)
    assert calls == ['alphamelts', 'stub']


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
                   alphamelts_available=False)

    with pytest.raises(BackendUnavailableError,
                       match='AlphaMELTS unavailable'):
        _get_backend(name)


@pytest.mark.parametrize(
    'name',
    ['auto', 'Auto', 'AUTO', ' auto '],
)
def test_autodetect_request_is_case_insensitive(
        monkeypatch, name, captured_logs):
    # 'auto' is accepted explicitly; case-folding is load-bearing because
    # uppercase auto must not be treated as an unknown backend.
    _install_fakes(monkeypatch,
                   alphamelts_available=True)

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
                   alphamelts_available=True)

    with pytest.raises(BackendUnavailableError,
                       match='not eligible as the active melt backend'):
        _get_backend(name)


def test_refusal_message_names_kernel_carve_out_prerequisite(monkeypatch):
    _install_fakes(monkeypatch,
                   alphamelts_available=False)

    with pytest.raises(BackendUnavailableError) as exc_info:
        _get_backend('vaporock')

    assert 'CHEMISTRY-KERNEL-CARVE-OUT' in str(exc_info.value)


# ---------------------------------------------------------------------------
# Boot-log emission
# ---------------------------------------------------------------------------


def test_engine_selection_log_emitted_on_every_selection(
        monkeypatch, captured_logs):
    _install_fakes(monkeypatch,
                   alphamelts_available=True)

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
                   alphamelts_available=False)

    _get_backend('auto')

    selection_lines = [line for line in captured_logs
                       if line.startswith('engine selection: StubBackend')]
    assert len(selection_lines) == 1


def test_engine_selection_log_records_capabilities_for_alphamelts(
        monkeypatch, captured_logs):
    _install_fakes(monkeypatch,
                   alphamelts_available=True)

    _get_backend('auto')

    selection_lines = [line for line in captured_logs
                       if line.startswith('engine selection:')]
    assert len(selection_lines) == 1
    assert '_FakeAlphaMELTS' in selection_lines[0]
    assert 'silicate_melt=true' in selection_lines[0]
