"""
Active-backend selection policy tests (\\goal BACKEND-DEFAULT-SWITCH).

Exercises every branch in ``web.events._get_backend`` with mocked
``is_available()`` flags so the test suite runs without PetThermoTools,
ChemApp, VapoRock, or MAGEMin actually installed.

Policy under test:

* AlphaMELTS is probed first; selected when ``is_available()`` is True.
* VapoRock and MAGEMin are **never** selected as the active backend; an
  explicit request for either raises ``BackendUnavailableError``.
* InternalAnalyticalBackend is the always-available fallback for ``auto`` / unset.
* Explicit unknown names fail loud.
* The selection emits one ``engine selection: ...`` log line per call.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

import pytest

import web.events as events
from simulator.backends import (
    BackendSelectionPolicy,
    STAGE0_SUBPROCESS_FEEDSTOCK_IDS,
    assert_real_backend_feedstock_supported,
    assert_stage0_subprocess_backend_safe,
    backend_resolution_status,
    is_spinel_rich_stage0_subprocess_feedstock,
    real_backend_feedstock_domain_reason,
    requires_stage0_subprocess,
    resolve_backend,
)
from simulator.config import load_config_bundle
from simulator.grind_preflight import (
    GrindSourceGateError,
    assert_grind_feedstock_stage0_route_coverage,
)
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.fidelity_vocabulary import (
    FidelityVocabularyTranslationError,
    canonicalize_fidelity_emission,
)
from simulator.optimize.evalspec import EvalSpec, cache_key, canonical_evalspec_json
from web.events import BackendUnavailableError, _get_backend

SPINEL_COMPOSITION_HANG_FEEDSTOCK_IDS = (
    "lunar_mare_low_ti",
    "lunar_mare_high_ti",
    "lunar_mare_lms1",
    "lunar_eac_1a",
    "s_type_asteroid_silicate",
    "m_type_silicate_phase",
    "v_type_vesta_hed",
    "e_type_enstatite_aubrite",
)
NON_SPINEL_COMPOSITION_FAST_PATH_FEEDSTOCK_IDS = (
    "lunar_highland",
    "lunar_highlands_lhs1",
    "lunar_pkt_kreep_average",
    "lunar_spa_kreep_influenced",
    "mars_global_mgs1",
    "targeted_super_kreep_ore",
)


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


class _FakeThermoEngine(_FakeBackend):
    name = 'thermoengine'


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
        resolve_backend('internal-analytical')


def test_runner_strict_rejects_auto():
    with pytest.raises(BackendUnavailableError, match='auto backend selection'):
        resolve_backend('auto', BackendSelectionPolicy.RUNNER_STRICT)


def test_runner_strict_keeps_legacy_exact_name_matching():
    with pytest.raises(BackendUnavailableError, match="unknown backend 'Auto'"):
        resolve_backend('Auto', BackendSelectionPolicy.RUNNER_STRICT)


def test_runner_strict_resolves_thermoengine_peer():
    backend = resolve_backend(
        'thermoengine',
        BackendSelectionPolicy.RUNNER_STRICT,
        thermoengine_backend_cls=lambda: _FakeThermoEngine(available=True),
    )

    assert isinstance(backend, _FakeThermoEngine)
    assert backend.init_calls == [{}]


def test_legacy_alphamelts_thermoengine_mode_resolves_peer_without_key_rename():
    backend = resolve_backend(
        'alphamelts',
        BackendSelectionPolicy.RUNNER_STRICT,
        thermoengine_backend_cls=lambda: _FakeThermoEngine(available=True),
        backend_config={'mode': 'thermoengine'},
    )

    assert isinstance(backend, _FakeThermoEngine)
    assert backend.init_calls == [{'mode': 'thermoengine'}]
    assert backend_resolution_status(backend).requested_backend == 'alphamelts'
    assert backend._legacy_alphamelts_cache_identity is True


@pytest.mark.parametrize(
    'backend_config',
    [
        {'mode': 'subprocess', 'alphamelts': {'mode': 'thermoengine'}},
        {'mode': 'thermoengine', 'alphamelts': {'mode': 'subprocess'}},
    ],
)
def test_legacy_alphamelts_mode_precedence_keeps_subprocess(backend_config):
    backend = resolve_backend(
        'alphamelts',
        BackendSelectionPolicy.RUNNER_STRICT,
        alphamelts_backend_cls=lambda: _FakeAlphaMELTS(available=True),
        thermoengine_backend_cls=lambda: pytest.fail(
            'ThermoEngine peer must not be selected'
        ),
        backend_config=backend_config,
    )

    assert isinstance(backend, _FakeAlphaMELTS)


def test_web_auto_preserves_legacy_thermoengine_mode_selection():
    backend = resolve_backend(
        'auto',
        BackendSelectionPolicy.WEB_AUTODETECT,
        alphamelts_backend_cls=lambda: pytest.fail(
            'AlphaMELTS subprocess peer must not be selected'
        ),
        thermoengine_backend_cls=lambda: _FakeThermoEngine(available=True),
        backend_config={'mode': 'thermoengine'},
    )

    assert isinstance(backend, _FakeThermoEngine)
    assert backend._legacy_alphamelts_cache_identity is True


def test_stage0_required_thermoengine_refuses_instead_of_blending_engines():
    class _RoutedThermoEngine(_FakeThermoEngine):
        def initialize(self, config):
            self._mode = 'thermoengine'
            return super().initialize(config)

    with pytest.raises(
        BackendUnavailableError,
        match='requires subprocess.*ThermoEngine',
    ):
        resolve_backend(
            'thermoengine',
            BackendSelectionPolicy.RUNNER_STRICT,
            thermoengine_backend_cls=lambda: _RoutedThermoEngine(available=True),
            stage0_subprocess_required=True,
        )


def test_web_autodetect_policy_preserves_probe_order():
    calls: list[str] = []

    def make_alphamelts():
        calls.append('alphamelts')
        return _FakeAlphaMELTS(available=False)

    def make_internal_analytical():
        calls.append('internal-analytical')
        return InternalAnalyticalBackend()

    backend = resolve_backend(
        'auto',
        BackendSelectionPolicy.WEB_AUTODETECT,
        alphamelts_backend_cls=make_alphamelts,
        internal_analytical_backend_cls=make_internal_analytical,
        log_selection=lambda selected: None,
    )

    assert isinstance(backend, InternalAnalyticalBackend)
    assert calls == ['alphamelts', 'internal-analytical']


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


def test_pregrind_route_feedstocks_require_subprocess_from_real_data():
    feedstocks = load_config_bundle().feedstocks
    route_digest = hashlib.md5(
        json.dumps(
            STAGE0_SUBPROCESS_FEEDSTOCK_IDS,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()

    assert route_digest == "9db09102b455449a1ba5b5a5b1e5a2d0"
    assert "lunar_mare_oprl2n" in STAGE0_SUBPROCESS_FEEDSTOCK_IDS
    assert {
        feedstock_id
        for feedstock_id in STAGE0_SUBPROCESS_FEEDSTOCK_IDS
        if not requires_stage0_subprocess(feedstock_id, feedstocks)
    } == set()
    assert requires_stage0_subprocess("mars_perchlorate_rich", feedstocks)
    assert (
        real_backend_feedstock_domain_reason(
            "alphamelts",
            "mars_perchlorate_rich",
            feedstocks,
        )
        is None
    )
    assert {
        feedstock_id
        for feedstock_id in STAGE0_SUBPROCESS_FEEDSTOCK_IDS
        if real_backend_feedstock_domain_reason("alphamelts", feedstock_id, feedstocks)
        is not None
    } == set()


def test_spinel_composition_route_predicate_separates_hang_from_safe_catalog():
    feedstocks = load_config_bundle().feedstocks

    assert {
        feedstock_id
        for feedstock_id in SPINEL_COMPOSITION_HANG_FEEDSTOCK_IDS
        if not is_spinel_rich_stage0_subprocess_feedstock(feedstocks[feedstock_id])
    } == set()
    assert {
        feedstock_id
        for feedstock_id in NON_SPINEL_COMPOSITION_FAST_PATH_FEEDSTOCK_IDS
        if is_spinel_rich_stage0_subprocess_feedstock(feedstocks[feedstock_id])
    } == set()

    renamed_feedstocks = {
        f"renamed_{feedstock_id}": {
            "composition_wt_pct": dict(feedstocks[feedstock_id]["composition_wt_pct"])
        }
        for feedstock_id in SPINEL_COMPOSITION_HANG_FEEDSTOCK_IDS
    }
    assert {
        feedstock_id
        for feedstock_id in SPINEL_COMPOSITION_HANG_FEEDSTOCK_IDS
        if not requires_stage0_subprocess(
            f"renamed_{feedstock_id}",
            renamed_feedstocks,
        )
    } == set()

    safe_clones = {
        f"safe_{feedstock_id}": {
            "composition_wt_pct": dict(feedstocks[feedstock_id]["composition_wt_pct"])
        }
        for feedstock_id in NON_SPINEL_COMPOSITION_FAST_PATH_FEEDSTOCK_IDS
    }
    assert {
        feedstock_id
        for feedstock_id in NON_SPINEL_COMPOSITION_FAST_PATH_FEEDSTOCK_IDS
        if requires_stage0_subprocess(
            f"safe_{feedstock_id}",
            safe_clones,
        )
    } == set()

    synthetic_feedstocks = {
        "new_spinel_rich_mare": {
            "composition_wt_pct": {
                "SiO2": 45.0,
                "Al2O3": 13.0,
                "FeO": 13.0,
                "MgO": 12.0,
                "TiO2": 1.0,
                "CaO": 10.0,
            }
        }
    }
    assert requires_stage0_subprocess(
        "new_spinel_rich_mare",
        synthetic_feedstocks,
    )

    perchlorate_composition_only = {
        "perchlorate_composition_only": {
            "composition_wt_pct": dict(
                feedstocks["mars_perchlorate_rich"]["composition_wt_pct"]
            )
        }
    }
    assert not requires_stage0_subprocess(
        "perchlorate_composition_only",
        perchlorate_composition_only,
    )
    assert requires_stage0_subprocess("mars_perchlorate_rich", feedstocks)


def test_catalog_has_no_clean_total_spinel_former_ceiling() -> None:
    feedstocks = load_config_bundle().feedstocks
    oxides = ("Cr2O3", "Al2O3", "FeO", "MgO", "TiO2")

    def total(feedstock_id: str) -> float:
        composition = feedstocks[feedstock_id]["composition_wt_pct"]
        return sum(float(composition.get(oxide, 0.0) or 0.0) for oxide in oxides)

    hang_floor = min(total(feedstock_id) for feedstock_id in SPINEL_COMPOSITION_HANG_FEEDSTOCK_IDS)
    overlapping_safe = {
        feedstock_id
        for feedstock_id in NON_SPINEL_COMPOSITION_FAST_PATH_FEEDSTOCK_IDS
        if total(feedstock_id) >= hang_floor
    }

    assert hang_floor == pytest.approx(38.9)
    assert {"mars_global_mgs1", "lunar_spa_kreep_influenced"} <= overlapping_safe


def test_interwindow_spinel_case_is_launch_preflight_not_predicate() -> None:
    synthetic_feedstocks = {
        "interwindow_spinel_rich": {
            "composition_wt_pct": {
                "SiO2": 42.0,
                "Al2O3": 12.0,
                "FeO": 12.0,
                "MgO": 18.0,
                "TiO2": 0.3,
                "CaO": 10.0,
            }
        }
    }

    assert not requires_stage0_subprocess(
        "interwindow_spinel_rich",
        synthetic_feedstocks,
    )
    with pytest.raises(GrindSourceGateError, match="interwindow_spinel_rich"):
        assert_grind_feedstock_stage0_route_coverage(
            ["interwindow_spinel_rich"],
            synthetic_feedstocks,
            backend_name="alphamelts",
            context="test-grind",
        )


def test_real_grind_feedstock_resolution_forces_subprocess_from_data():
    feedstocks = load_config_bundle().feedstocks
    source_config = {"mode": "thermoengine", "python_bridge": "python_api"}
    instances: list[_FakeAlphaMELTS] = []

    class _RoutedAlphaMELTS(_FakeAlphaMELTS):
        def initialize(self, config):
            self._mode = str(config.get("mode") or "")
            self._bridge = str(config.get("python_bridge") or "")
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
        feedstock_id="lunar_mare_low_ti",
        feedstocks=feedstocks,
    )

    assert backend is instances[0]
    assert backend._mode == "subprocess"
    assert backend._bridge == "subprocess"
    assert backend.stage0_subprocess_required is True
    assert source_config == {"mode": "thermoengine", "python_bridge": "python_api"}


def test_real_backend_rejects_metallic_feedstock_before_solver_call():
    feedstocks = load_config_bundle().feedstocks

    with pytest.raises(
        BackendUnavailableError,
        match=(
            "real_backend_out_of_domain: non_silicate_feedstock: "
            "feedstock 'm_type_metallic_phase'"
        ),
    ):
        assert_real_backend_feedstock_supported(
            "alphamelts",
            "m_type_metallic_phase",
            feedstocks,
            unavailable_error_cls=BackendUnavailableError,
        )


def test_stage0_required_auto_falls_back_when_forced_alphamelts_absent():
    source_config = {
        "mode": "thermoengine",
        "python_bridge": "python_api",
        "alphamelts": {"mode": "thermoengine"},
    }
    calls: list[str] = []
    instances: list[_FakeAlphaMELTS] = []

    class _AbsentSubprocessAlphaMELTS(_FakeAlphaMELTS):
        def initialize(self, config):
            self.init_calls.append(dict(config or {}))
            if config.get("mode") == "subprocess":
                raise RuntimeError("AlphaMELTS subprocess executable missing")
            return bool(self._init_returns)

    def make_alphamelts():
        calls.append("alphamelts")
        backend = _AbsentSubprocessAlphaMELTS(available=True)
        instances.append(backend)
        return backend

    def make_internal_analytical():
        calls.append("internal-analytical")
        return InternalAnalyticalBackend()

    backend = resolve_backend(
        "auto",
        BackendSelectionPolicy.WEB_AUTODETECT,
        alphamelts_backend_cls=make_alphamelts,
        internal_analytical_backend_cls=make_internal_analytical,
        log_selection=lambda selected: None,
        backend_config=source_config,
        feedstock_id="spinel-feed",
        feedstocks={"spinel-feed": {"spinel_rich": True}},
    )

    assert isinstance(backend, InternalAnalyticalBackend)
    assert calls == ["alphamelts", "internal-analytical"]
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

    resolution = backend_resolution_status(backend)
    assert resolution.requested_backend == "auto"
    assert resolution.active_backend == "InternalAnalyticalBackend"
    assert resolution.backend_status == "unavailable"
    assert resolution.authoritative is False
    assert resolution.message.startswith(
        "forced AlphaMELTS backend unavailable; substituted InternalAnalyticalBackend"
    )


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


def test_autodetect_all_primaries_unavailable_falls_back_to_internal_analytical(
        monkeypatch, captured_logs):
    _install_fakes(monkeypatch,
                   alphamelts_available=False)

    backend = _get_backend('auto')

    assert isinstance(backend, InternalAnalyticalBackend)
    assert any('engine selection: InternalAnalyticalBackend' in line
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


def test_autodetect_with_vaporock_or_magemin_available_still_picks_internal_analytical(
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

    assert isinstance(backend, InternalAnalyticalBackend)


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


def test_explicit_stub_request_pins_internal_analytical_backend(
        monkeypatch, captured_logs):
    """The legacy ``stub`` input still pins InternalAnalyticalBackend."""

    _install_fakes(monkeypatch,
                   alphamelts_available=True)

    backend = _get_backend('stub')

    assert isinstance(backend, InternalAnalyticalBackend)
    assert any('engine selection: InternalAnalyticalBackend' in line
               for line in captured_logs)


def test_web_autodetect_stub_bypasses_primary_probes():
    """Under WEB_AUTODETECT, 'internal-analytical' returns InternalAnalyticalBackend without probing
    AlphaMELTS (D1 fix at the resolver level)."""
    calls: list[str] = []

    def make_alphamelts():
        calls.append('alphamelts')
        return _FakeAlphaMELTS(available=True)

    def make_internal_analytical():
        calls.append('internal-analytical')
        return InternalAnalyticalBackend()

    backend = resolve_backend(
        'internal-analytical',
        BackendSelectionPolicy.WEB_AUTODETECT,
        alphamelts_backend_cls=make_alphamelts,
        internal_analytical_backend_cls=make_internal_analytical,
        log_selection=lambda selected: None,
    )

    assert isinstance(backend, InternalAnalyticalBackend)
    assert calls == ['internal-analytical']  # primaries never probed


# ---------------------------------------------------------------------------
# Analytical backend input aliases
# ---------------------------------------------------------------------------
#
# Trust-architecture vocabulary names the analytical model `internal-analytical`
# (design-fidelity-surface-2026-06-10.md §STUB REBRAND; AGENTS.md C3). The
# Legacy names are accepted on input, while serialization emits the 0.6 token.


@pytest.mark.parametrize(
    'name',
    ['internal-analytical', 'INTERNAL-ANALYTICAL', 'internal_analytical', 'stub',
     ' internal-analytical '],
)
def test_internal_analytical_alias_pins_internal_analytical_backend(monkeypatch, name):
    """``backend='internal-analytical'`` resolves exactly like ``'internal-analytical'``.

    Even when AlphaMELTS is available the alias deterministically pins
    InternalAnalyticalBackend (legacy aliases fold onto ``internal-analytical``
    before the autodetect branch).
    """
    _install_fakes(monkeypatch, alphamelts_available=True)

    backend = _get_backend(name)

    assert isinstance(backend, InternalAnalyticalBackend)


def test_internal_analytical_alias_runner_strict_resolves_like_stub():
    backend = resolve_backend(
        'internal-analytical',
        BackendSelectionPolicy.RUNNER_STRICT,
        internal_analytical_backend_cls=InternalAnalyticalBackend,
    )
    assert isinstance(backend, InternalAnalyticalBackend)


def test_internal_analytical_alias_serializes_new_token_and_denylists():
    cache_identities = []

    for name in ('internal-analytical', 'stub'):
        backend = resolve_backend(
            name,
            BackendSelectionPolicy.WEB_AUTODETECT,
            internal_analytical_backend_cls=InternalAnalyticalBackend,
            log_selection=lambda selected: None,
        )
        resolution = backend_resolution_status(backend)

        assert resolution.requested_backend == 'internal-analytical'
        assert resolution.active_backend == 'InternalAnalyticalBackend'
        assert resolution.message == (
            'internal-analytical backend selected; '
            'no authoritative melt result available'
        )
        assert resolution.backend_status == 'unavailable'
        assert resolution.authoritative is False

        with pytest.raises(
            FidelityVocabularyTranslationError,
            match=(
                "certification emission refused for denylisted "
                "evidence_class='internal-analytical'"
            ),
        ):
            canonicalize_fidelity_emission(
                backend_name=name,
                backend_status='ok',
                backend_authoritative=True,
                certification_shape=True,
            )

        spec = EvalSpec(
            recipe_id='recipe-id',
            feedstock_recipe_digest='feedstock-recipe-digest',
            feedstock_id='lunar_mare_low_ti',
            profile_id='profile-id',
            fidelity='fast',
            code_version='test-code-version',
            data_digests={
                'feedstocks': 'feedstocks-digest',
                'foulant_thermo': 'foulant-thermo-digest',
                'materials': 'materials-digest',
                'profile': 'profile-digest',
                'setpoints': 'setpoints-digest',
                'species_catalog': 'species-catalog-digest',
                'vapor_pressures': 'vapor-pressures-digest',
            },
            backend_name=name,
        )
        assert spec.backend_name == 'internal-analytical'
        assert (
            json.loads(canonical_evalspec_json(spec))['backend_name']
            == 'internal-analytical'
        )
        cache_identities.append(cache_key(spec))

    assert cache_identities[0] == cache_identities[1]


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

    def make_internal_analytical():
        calls.append('internal-analytical')
        return InternalAnalyticalBackend()

    backend = resolve_backend(
        '',
        BackendSelectionPolicy.WEB_AUTODETECT,
        alphamelts_backend_cls=make_alphamelts,
        internal_analytical_backend_cls=make_internal_analytical,
        log_selection=lambda selected: None,
    )

    assert isinstance(backend, InternalAnalyticalBackend)
    assert calls == ['alphamelts', 'internal-analytical']


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
    # and fall into autodetect, returning InternalAnalyticalBackend -- this
    # test catches that.
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


def test_engine_selection_log_also_emitted_on_internal_analytical_fallback(
        monkeypatch, captured_logs):
    _install_fakes(monkeypatch,
                   alphamelts_available=False)

    _get_backend('auto')

    selection_lines = [line for line in captured_logs
                       if line.startswith('engine selection: InternalAnalyticalBackend')]
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
