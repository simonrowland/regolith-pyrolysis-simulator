"""Tests for the MAGEMin melt-backend adapter (simulator/melt_backend/magemin.py).

Distinct from ``tests/test_magemin_shadow_provider.py``, which covers the
``engines/magemin/`` kernel-shadow scaffold. This file defends the
``MeltBackend`` adapter contract -- in particular that the adapter is not
silently ignored if mis-selected as the active melt backend, that it fails
closed when MAGEMin is absent, that the mocked-present path converts the
oxide basis and pressure units correctly, and (skipif-guarded) that the
real MAGEMin binary runs end to end when one is built locally.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import threading
import time
import types
import warnings
from pathlib import Path

import pytest
import yaml

from simulator.engine_pool import EngineWorkerTimeout
from engines.alphamelts.domain import AlphaMELTSDomainGate
from engines.domain_reason import OutOfDomainReason
from engines.magemin.domain import MAGEMinDomainGate
import simulator.melt_backend.liquidus as liquidus_module
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import LiquidFractionInvalidError, MeltCompositionError
from simulator.feedstock_composition import FEOT_FROM_FE2O3
from simulator.melt_backend.magemin import (
    COMPOSITION_PROJECTED,
    INACTIVE_BUFFER_DECIDES_BOUNDARY,
    MAGEMIN_MODE_VECTOR_MASS_DEFICIT,
    MAGEMIN_WARM_CALL_TIMEOUT_S,
    MAGEMIN_WARM_LIQUIDUS_BUDGET_S,
    MAGEMinBackend,
    diagnostics_name_composition_projected,
)

# Verb=0 success now reads output/_matlab_output.txt. Stdout-only mocks
# that return a unit mode must plant this one-phase table.
_LIQ_ONLY_MATLAB = (
    "Oxide compositions [wt fr]:\n"
    " SiO2\n"
    " liq 1.0\n"
    "\n"
    "Stable mineral assemblage:\n"
    "phase fraction[wt]\n"
    "liq +1.00000\n"
)


def _plant_liq_matlab(cwd) -> None:
    dest = Path(cwd) / "output" / "_matlab_output.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_LIQ_ONLY_MATLAB, encoding="utf-8")

# Thread-rendezvous guards throughout this module: these bound a HANG, they do
# not assert a latency. Any test that means to claim "within N seconds" should
# use its own explicit figure and say so, rather than borrowing this one.
_RENDEZVOUS_TIMEOUT_S = 30.0


def _disable_configured_magemin_path(monkeypatch):
    import simulator.engine_local_config as engine_local_config

    monkeypatch.setattr(
        engine_local_config,
        "configured_magemin_binary_path",
        lambda: None,
    )


def _make_available_magemin(monkeypatch, fake_module):
    """Force a MAGEMinBackend to initialize() successfully with a fake bridge.

    The simulator constructor never calls ``initialize()``; tests that
    want an *available* MAGEMin backend must call it explicitly. We stub
    binary discovery and the Python-bridge import so no real MAGEMin
    install is required.
    """
    _disable_configured_magemin_path(monkeypatch)
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("pymagemin", fake_module),
    )


def _make_absent_magemin(monkeypatch):
    """Force all MAGEMin availability probes to report absent."""
    _disable_configured_magemin_path(monkeypatch)
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: None),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: (None, None),
    )


def _assert_magemin_database_exclusion(
    result,
    expected_kg,
    *,
    calls,
):
    """Out-of-database oxides are excluded and the majors still solve."""
    assert calls
    assert result.status == "ok"
    assert result.phases_present
    assert result.diagnostics.get("backend_status_reason") != COMPOSITION_PROJECTED
    projection = result.diagnostics["input_composition_projection"]
    assert COMPOSITION_PROJECTED not in projection
    assert "dropped_bulk_components" not in projection
    assert diagnostics_name_composition_projected(result.diagnostics) is False
    excluded = result.diagnostics["magemin_excluded_database_components_kg"]
    assert set(excluded) == set(expected_kg)
    for name, mass_kg in expected_kg.items():
        assert excluded[name] == pytest.approx(mass_kg)
    warning_text = " ".join(result.warnings)
    assert "dropped components outside documented bulk order" in warning_text
    assert "refused projected composition" not in warning_text



def test_magemin_defaults_to_subprocess_even_if_pymagemin_importable(monkeypatch):
    _disable_configured_magemin_path(monkeypatch)
    fake_module = types.SimpleNamespace(minimize=lambda **kwargs: {})
    monkeypatch.setitem(sys.modules, 'pymagemin', fake_module)
    monkeypatch.setattr(
        MAGEMinBackend,
        '_locate_binary',
        staticmethod(lambda explicit: Path('/fake/MAGEMin')),
    )

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        assert backend.initialize({}) is True

    assert backend._bridge == 'subprocess'
    assert backend._magemin_module is None


def test_magemin_reinitialize_closes_existing_warm_pool(monkeypatch):
    class FakePool:
        close_calls = 0

        def close(self, *, cancel_pending=False):
            assert cancel_pending is True
            self.close_calls += 1

    existing = FakePool()
    backend = MAGEMinBackend()
    backend._subprocess_pool = existing
    monkeypatch.setattr(
        MAGEMinBackend,
        '_locate_binary',
        staticmethod(lambda _explicit: Path('/fake/MAGEMin')),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        '_import_magemin_bridge',
        lambda self, *, requested: ('subprocess', None),
    )

    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        assert backend.initialize({'warm_worker': False}) is True

    assert existing.close_calls == 1
    assert backend._subprocess_pool is None


def test_magemin_runtime_pool_uses_right_sized_warm_timeout(monkeypatch):
    import simulator.melt_backend.magemin as magemin_module

    submitted = {}

    class FakeFuture:
        def result(self):
            return {'phases': {'liq': {'mass_kg': 1.0}}}

    class FakePool:
        def __init__(self, worker_factory, *, size):
            self.workers = (worker_factory(0),)
            self.size = size

        def submit(self, request, *, timeout_s=None):
            submitted.update(request=request, timeout_s=timeout_s)
            return FakeFuture()

        def close(self, *, cancel_pending=False):
            pass

    monkeypatch.setattr(magemin_module, 'EngineWorkerPool', FakePool)
    monkeypatch.setattr(
        MAGEMinBackend,
        '_locate_binary',
        staticmethod(lambda _explicit: Path('/fake/MAGEMin')),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        '_import_magemin_bridge',
        lambda self, *, requested: ('subprocess', None),
    )

    backend = MAGEMinBackend()
    assert backend.initialize({}) is True
    assert backend._subprocess_pool.size == 1
    assert backend._subprocess_pool.workers[0].call_timeout_s == pytest.approx(
        MAGEMIN_WARM_CALL_TIMEOUT_S
    )

    projection = backend._build_db_bulk_projection({
        'SiO2': 60.0,
        'MgO': 40.0,
    })
    assert backend._call_magemin(projection, 1200.0, 1.0, -8.0)
    assert submitted['timeout_s'] == pytest.approx(MAGEMIN_WARM_CALL_TIMEOUT_S)
    assert submitted['request']['call_timeout_s'] == pytest.approx(
        MAGEMIN_WARM_CALL_TIMEOUT_S + 0.5
    )


def test_magemin_equilibrate_surfaces_typed_hang_without_disabling_backend(
    monkeypatch,
):
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = 'subprocess'
    monkeypatch.setattr(
        backend,
        '_call_magemin',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            EngineWorkerTimeout('MAGEMin slot 0', 2.0, phase='job')
        ),
    )

    with pytest.raises(EngineWorkerTimeout):
        backend.equilibrate(
            1200.0,
            composition_mol={'SiO2': 1.0, 'MgO': 1.0},
        )
    assert backend.is_available() is True


@pytest.mark.parametrize(
    ('pooled', 'expected_budget_s'),
    ((True, MAGEMIN_WARM_LIQUIDUS_BUDGET_S),
     (False, liquidus_module.DEFAULT_LIQUIDUS_FINDER_BUDGET_S)),
)
def test_magemin_liquidus_budget_is_tight_only_for_warm_pool(
    monkeypatch,
    pooled,
    expected_budget_s,
):
    import simulator.melt_backend.magemin as magemin_module

    captured = {}

    def fake_finder(_sample_fraction, **kwargs):
        captured.update(kwargs)
        return liquidus_module.LiquidusSolidusResult(status='not_converged')

    monkeypatch.setattr(
        magemin_module,
        'find_liquidus_solidus_by_fraction',
        fake_finder,
    )
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = 'subprocess'
    backend._binary_path = Path('/fake/MAGEMin')
    backend._config = {}
    backend._subprocess_pool = object() if pooled else None

    backend.find_liquidus_solidus(
        composition_mol={'SiO2': 1.0, 'MgO': 1.0},
    )
    assert captured['budget_s'] == pytest.approx(expected_budget_s)


def test_magemin_cold_liquidus_budget_retains_bounded_diagnostic_headroom():
    assert liquidus_module.DEFAULT_LIQUIDUS_FINDER_BUDGET_S == pytest.approx(300.0)


# The test pins its OWN coarse grid rather than relying on the backend's
# defaults, so the arithmetic below stays true if those defaults change.
_B299_MIN_T_C = 1000.0
_B299_MAX_T_C = 1200.0
_B299_STEP_C = 50.0
_B299_COARSE_SAMPLES = int((_B299_MAX_T_C - _B299_MIN_T_C) / _B299_STEP_C) + 1


@pytest.mark.parametrize(
    'backend_token',
    ['out_of_domain', 'unavailable', 'not_converged'],
)
@pytest.mark.parametrize(
    'ok_samples_first',
    [0, _B299_COARSE_SAMPLES],
    ids=['refuses_on_first_sample', 'refuses_during_bisection'],
)
def test_magemin_liquidus_preserves_the_backend_refusal_token(
    backend_token,
    ok_samples_first,
):
    """The finder must report WHICH refusal the backend gave (b-299).

    A bare RuntimeError from the sampler falls through to the finder's
    generic library-boundary guard, which mints 'not_converged'.  That
    turns 'the engine was absent' (unavailable) or 'the physics is outside
    the model' (out_of_domain) into an affirmative claim that a solve ran
    and failed to converge.  The AlphaMELTS sampler already raises the
    typed LiquidusSampleError; MAGEMin was the outlier.

    The two parametrisations cover genuinely different finder phases.  The
    finder completes its ENTIRE coarse scan before it brackets, so a
    refusal on an early sample never reaches bracketing or bisection --
    letting the whole coarse grid succeed first is what forces the refusal
    into the bisection phase, and the off-grid assertion below is what
    proves it got there rather than merely claiming so.
    """
    from simulator.melt_backend.base import EquilibriumResult

    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = 'subprocess'
    backend._binary_path = Path('/fake/MAGEMin')
    backend._config = {}
    backend._subprocess_pool = None

    sampled_at: list[float] = []

    def fake_equilibrate(temperature_C, **_kwargs):
        sampled_at.append(float(temperature_C))
        healthy = len(sampled_at) <= ok_samples_first
        span = _B299_MAX_T_C - _B299_MIN_T_C
        return EquilibriumResult(
            temperature_C=float(temperature_C),
            pressure_bar=1.0e-6,
            fO2_log=-9.0,
            status='ok' if healthy else backend_token,
            liquid_fraction=(
                max(0.0, min(1.0, (float(temperature_C) - _B299_MIN_T_C) / span))
                if healthy else None
            ),
            phase_masses_kg={'liquid': 1.0} if healthy else {},
            warnings=[] if healthy else [f'MAGEMin said {backend_token}'],
        )

    backend.equilibrate = fake_equilibrate

    result = backend.find_liquidus_solidus(
        composition_mol={'SiO2': 1.0, 'MgO': 1.0, 'FeO': 1.0},
        min_T_C=_B299_MIN_T_C,
        max_T_C=_B299_MAX_T_C,
        scan_step_C=_B299_STEP_C,
    )

    # The backend has an early 'unavailable' return for an uninitialised
    # bridge, so a test that never reaches the sampler would pass vacuously
    # for backend_token='unavailable'.  Pin that the sampler actually ran.
    assert len(sampled_at) > ok_samples_first
    refused_at = sampled_at[ok_samples_first]

    if ok_samples_first:
        # Every coarse-grid temperature is an exact multiple of the step
        # above min_T_C; a bisection probe is not.  This is what makes the
        # 'refuses_during_bisection' id honest rather than aspirational.
        offset_steps = (refused_at - _B299_MIN_T_C) / _B299_STEP_C
        assert offset_steps != pytest.approx(round(offset_steps)), (
            f'refusal at {refused_at} C is still on the coarse grid; '
            'this case is not exercising bisection'
        )

    assert result.status == backend_token


def test_magemin_and_alphamelts_reject_exact_major_oxide_boundary():
    boundary = {'SiO2': 50.0, 'MgO': 45.0}

    alpha_valid, _alpha_warnings, alpha_reason = (
        AlphaMELTSDomainGate.validate_with_reason(boundary)
    )
    magemin_valid, _magemin_warnings, magemin_reason = (
        MAGEMinDomainGate.validate_with_reason(boundary)
    )

    assert alpha_valid is False
    assert magemin_valid is False
    assert alpha_reason == OutOfDomainReason.MAJOR_SUM.value
    assert magemin_reason == OutOfDomainReason.MAJOR_SUM.value


def test_magemin_as_active_backend_fails_closed_with_clear_message(monkeypatch):
    # MAGEMin is not wired into any active call site. If someone DOES
    # select it as the active melt backend, equilibrate() populates
    # phase_masses_kg but leaves ledger_transition None -- and core.py's
    # _get_equilibrium rejects exactly that combination. The adapter
    # docstring's "diagnostic" claim must mean "fails closed if
    # mis-selected", not "silently ignored".
    def minimize(**kwargs):
        # A populated post-equilibrium phase assemblage with NO ledger
        # transition -- the exact shape core.py must reject.
        return {
            "phases": {
                "liquid": {"mass_kg": 0.7},
                "olivine": {"mass_kg": 0.3},
            }
        }

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({}) is True
    assert backend.is_available() is True

    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"SiO2": 100.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("oxide", mass_kg=1.0)

    with pytest.raises(RuntimeError, match="without an AtomLedger transition"):
        sim.step()


def test_magemin_equilibrate_never_emits_ledger_transition(monkeypatch):
    # Even on a successful library call, the adapter must not fabricate a
    # ledger transition: MAGEMin holds no AtomLedger authority.
    def minimize(**kwargs):
        return {"phases": {"liquid": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({}) is True

    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert result.ledger_transition is None
    # phase_masses_kg IS populated -- which is precisely why the result
    # is unsafe to route through _get_equilibrium as the active backend.
    assert result.phase_masses_kg
    assert backend.ledger_account_policies() == ()


def test_magemin_explicit_pymagemin_runtime_failure_retries_subprocess(monkeypatch):
    def minimize(**kwargs):
        raise RuntimeError("pymagemin minimizer crashed")

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({"python_bridge": "pymagemin"}) is True
    assert backend._bridge == "pymagemin"

    fallback_calls = []

    def fake_subprocess(**kwargs):
        fallback_calls.append(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    monkeypatch.setattr(
        backend,
        "_call_magemin_subprocess",
        fake_subprocess,
    )

    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert fallback_calls
    assert result.status == "ok"
    assert result.phase_masses_kg == {"liq": 1.0}
    assert any(
        "pymagemin bridge failed; retried subprocess" in warning
        for warning in result.warnings
    )


def test_magemin_bridge_fallback_subtracts_elapsed_aggregate_budget(monkeypatch):
    clock = {'t': 0.0}

    def minimize(**_kwargs):
        clock['t'] += 39.0
        raise RuntimeError('bridge failed late')

    backend = MAGEMinBackend()
    backend._bridge = 'pymagemin'
    backend._magemin_module = types.SimpleNamespace(minimize=minimize)
    backend._binary_path = Path('/tmp/fake-MAGEMin')
    seen = []

    def fake_subprocess(**kwargs):
        seen.append(kwargs['call_timeout_s'])
        return {'phases': {'liq': {'mass_kg': 1.0}}}

    monkeypatch.setattr(
        'simulator.melt_backend.magemin.time.monotonic',
        lambda: clock['t'],
    )
    monkeypatch.setattr(backend, '_call_magemin_subprocess', fake_subprocess)

    backend._call_magemin(
        bulk_projection=types.SimpleNamespace(
            composition_wt_pct={'SiO2': 50.0, 'MgO': 50.0},
            vector=(50.0, 50.0),
        ),
        temperature_C=1200.0,
        pressure_bar=1000.0,
        fO2_log=-8.0,
        call_timeout_s=40.0,
    )

    assert seen == [pytest.approx(1.0)]


def test_magemin_negative_convergence_evidence_discards_phase_rows(monkeypatch):
    fake_module = types.SimpleNamespace(
        minimize=lambda **_kwargs: {
            'converged': False,
            'phases': {'liq': {'mass_kg': 0.8}, 'ol': {'mass_kg': 0.2}},
        }
    )
    _make_available_magemin(monkeypatch, fake_module)
    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        assert backend.initialize({'python_bridge': 'pymagemin'}) is True

    result = backend.equilibrate(
        1200.0,
        composition_mol={'SiO2': 1.0, 'MgO': 1.0},
        pressure_bar=1000.0,
        fO2_log=-8.0,
    )

    assert result.status == 'not_converged'
    assert result.phases_present == []
    assert result.diagnostics['backend_status_reason'] == 'negative_convergence_evidence'


def test_magemin_object_negative_convergence_discards_phase_rows(monkeypatch):
    fake_module = types.SimpleNamespace(
        minimize=lambda **_kwargs: types.SimpleNamespace(
            converged=False,
            phases={'liq': {'mass_kg': 0.8}, 'ol': {'mass_kg': 0.2}},
        )
    )
    _make_available_magemin(monkeypatch, fake_module)
    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        assert backend.initialize({'python_bridge': 'pymagemin'}) is True

    result = backend.equilibrate(
        1200.0,
        composition_mol={'SiO2': 1.0, 'MgO': 1.0},
        pressure_bar=1000.0,
        fO2_log=-8.0,
    )

    assert result.status == 'not_converged'
    assert result.phases_present == []
    assert result.diagnostics['backend_status_reason'] == 'negative_convergence_evidence'


@pytest.mark.parametrize(
    'mode_line',
    ['0.8 bad', '0.8 nan', '0.8 -0.2', '0.8 1.2'],
)
def test_magemin_subprocess_parser_rejects_any_invalid_mode_token(mode_line):
    with pytest.raises(RuntimeError, match='Mode token'):
        MAGEMinBackend._parse_subprocess_stdout(
            f'Phase: liq ol\nMode: {mode_line}\n'
        )


def test_magemin_subprocess_parser_rejects_nonunit_mode_vector():
    with pytest.raises(RuntimeError, match='Mode vector must sum to 1.0'):
        MAGEMinBackend._parse_subprocess_stdout(
            'Phase: liq ol\nMode: 0.8 0.8\n'
        )


def test_magemin_plante_mode_deficit_is_typed_out_of_domain(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=(
                'Phase :      cpx       ol       ol       ne      trd\n'
                'Mode  :  0.00000  0.00025  0.00010  0.00047  0.66605\n'
            ),
            stderr='',
        )

    monkeypatch.setattr(
        'simulator.melt_backend.magemin.subprocess.run',
        fake_run,
    )
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = 'subprocess'
    backend._binary_path = Path('/fake/MAGEMin')

    result = backend.equilibrate(
        1302.0 - 273.15,
        composition_kg={'K2O': 0.4394, 'SiO2': 0.5606},
        fO2_log=-9.0,
        pressure_bar=1.0e-6,
    )

    assert result.status == 'out_of_domain'
    assert result.diagnostics['backend_status'] == 'out_of_domain'
    assert result.diagnostics['backend_status_reason'] == (
        MAGEMIN_MODE_VECTOR_MASS_DEFICIT
    )
    assert result.diagnostics['magemin_mode_vector_raw_sum'] == pytest.approx(
        0.66687
    )
    assert result.diagnostics['magemin_mode_vector_deficit'] == pytest.approx(
        0.33313
    )
    assert result.diagnostics['magemin_mode_vector_input_components'] == {
        'K2O': pytest.approx(43.94),
        'SiO2': pytest.approx(56.06),
    }
    assert result.diagnostics[
        'magemin_mode_vector_unrepresented_input_components'
    ] is None
    assert result.diagnostics[
        'magemin_mode_vector_unrepresented_components_determined'
    ] is False
    assert result.phases_present == []
    assert result.phase_masses_kg == {}
    assert len(calls) == 1
    assert any('mass deficit=0.33313' in warning for warning in result.warnings)


def test_magemin_plante_mode_vector_is_not_renormalized():
    with pytest.raises(RuntimeError, match='Mode vector must sum to 1.0'):
        MAGEMinBackend._parse_subprocess_stdout(
            'Phase : cpx ol ol ne trd\n'
            'Mode  : 0.00000 0.00025 0.00010 0.00047 0.66605\n'
        )


def test_magemin_control_mode_vector_serialization_is_byte_identical():
    parsed = MAGEMinBackend._parse_subprocess_stdout(
        'Phase : liq cpx ol qfm\n'
        'Mode  : 0.72251 0.00001 0.23073 0.04675\n'
    )

    assert repr(parsed) == (
        "{'liq': {'mass_kg': 0.72251}, "
        "'cpx': {'mass_kg': 1e-05}, "
        "'ol': {'mass_kg': 0.23073}}"
    )


def test_magemin_explicit_julia_runtime_failure_retries_subprocess(monkeypatch):
    def single_point_minimization(*args):
        raise RuntimeError("julia minimizer crashed")

    fake_module = types.SimpleNamespace(
        Main=types.SimpleNamespace(
            MAGEMin=types.SimpleNamespace(
                single_point_minimization=single_point_minimization,
            ),
        ),
    )
    _disable_configured_magemin_path(monkeypatch)
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("julia", fake_module),
    )

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({"python_bridge": "julia"}) is True
    assert backend._bridge == "julia"

    fallback_calls = []

    def fake_subprocess(**kwargs):
        fallback_calls.append(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    monkeypatch.setattr(
        backend,
        "_call_magemin_subprocess",
        fake_subprocess,
    )

    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert fallback_calls
    assert result.status == "ok"
    assert result.phase_masses_kg == {"liq": 1.0}
    assert any(
        "julia bridge failed; retried subprocess" in warning
        for warning in result.warnings
    )


def test_magemin_liquidus_finder_bisects_fake_bridge(monkeypatch):
    def minimize(**kwargs):
        temperature_C = float(kwargs["T_C"])
        frac = max(0.0, min(1.0, (temperature_C - 1000.0) / 300.0))
        phases = {}
        if frac > 0.0:
            phases["liq"] = {"mass_kg": frac}
        if frac < 1.0:
            phases["ol"] = {"mass_kg": 1.0 - frac}
        return {"phases": phases}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({}) is True

    result = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == "ok"
    assert result.solidus_T_C == pytest.approx(1000.0, abs=1.0)
    assert result.liquidus_T_C == pytest.approx(1300.0, abs=1.0)
    assert result.liquidus_T_K == pytest.approx(result.liquidus_T_C + 273.15)


def test_magemin_liquidus_finder_honors_aggregate_budget_config(monkeypatch):
    clock = {"t": 0.0}

    def minimize(**kwargs):
        temperature_C = float(kwargs["T_C"])
        clock["t"] += 0.1
        frac = max(0.0, min(1.0, (temperature_C - 1000.0) / 300.0))
        phases = {}
        if frac > 0.0:
            phases["liq"] = {"mass_kg": frac}
        if frac < 1.0:
            phases["ol"] = {"mass_kg": 1.0 - frac}
        return {"phases": phases}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)
    monkeypatch.setattr(liquidus_module.time, "monotonic", lambda: clock["t"])

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({"liquidus_finder_budget_s": 0.05}) is True

    result = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == "not_converged"
    assert len(result.samples) == 1
    assert any(
        "liquidus finder exceeded aggregate budget 0.05s after 1 calls"
        in warning
        for warning in result.warnings
    )
    assert result.diagnostics["reason"] == "aggregate_budget_exceeded"
    assert result.diagnostics["call_count"] == 1
    assert result.diagnostics["last_T_C"] == pytest.approx(800.0)


def test_magemin_liquidus_finder_rejects_none_budget(monkeypatch):
    def minimize(**kwargs):
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)
    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({"liquidus_finder_budget_s": None}) is True

    result = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
        min_T_C=800.0,
        max_T_C=1000.0,
        scan_step_C=100.0,
    )
    assert result.status == "not_converged"
    assert any("None is rejected" in w for w in result.warnings)
    assert result.diagnostics.get("reason") == "invalid_liquidus_finder_budget"


def test_magemin_subprocess_timeout_clamped_to_remaining_budget(
    monkeypatch, tmp_path
):
    """Per-call subprocess timeout must be min(configured, remaining)."""
    import simulator.melt_backend.magemin as magemin_module

    # 2026-07-25 t-419 load-robustness: isolate the machine-wide K-slot
    # flock into a private namespace. With the shared lock dir, ANY
    # concurrent test's MAGEMin slot traffic could eat into the 1.5 s
    # budget during acquisition, surfacing 'cancelled while waiting for
    # subprocess slot' instead of the clamped-timeout message this test
    # pins — a flake that blamed the box instead of the shared lock.
    monkeypatch.setattr(
        magemin_module, '_MAGEMIN_SUBPROCESS_LOCK_DIR', tmp_path,
    )
    monkeypatch.setattr(
        magemin_module, '_MAGEMIN_SUBPROCESS_LOCK',
        tmp_path / 'magemin-subprocess.lock',
    )
    seen_timeouts: list[float] = []

    def fake_run(*args, **kwargs):
        seen_timeouts.append(float(kwargs["timeout"]))
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(
        "simulator.melt_backend.magemin.subprocess.run", fake_run
    )
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"
    backend._binary_path = Path("/tmp/fake-MAGEMin")
    backend._config = {"timeout_s": 60.0, "database": "ig"}
    backend._database = "ig"
    backend._magemin_module = None

    # Remaining aggregate budget of 1.5s must clamp the 60s default.
    with pytest.raises(RuntimeError, match="timed out after 1.5"):
        backend._call_magemin_subprocess(
            bulk_projection=types.SimpleNamespace(
                vector=[1.0] * 11,
                order=MAGEMinBackend._IG_BULK_ORDER,
                composition_wt_pct={"SiO2": 50.0, "MgO": 50.0},
                warnings=(),
            ),
            temperature_C=1200.0,
            pressure_kbar=0.001,
            fO2_log=-8.0,
            call_timeout_s=1.5,
        )
    assert len(seen_timeouts) == 1
    assert 0.0 < seen_timeouts[0] <= 1.5


def test_magemin_liquidus_subprocess_path_preserves_budget_diagnostics(
    monkeypatch,
):
    """Subprocess-shaped residual timeout must keep structured budget diagnostics.

    The in-process minimize mock already covers the post-sample budget check.
    The real default bridge is subprocess; residual-clamped timeouts used to
    fall through as generic ``liquidus finder failed`` with empty diagnostics.
    Mock the subprocess boundary (no real engine required).
    """
    clock = {"t": 0.0}
    seen_timeouts: list[float] = []

    def fake_run(*args, **kwargs):
        timeout = float(kwargs["timeout"])
        seen_timeouts.append(timeout)
        # Burn residual wall the way a real residual-clamped hang would.
        clock["t"] += timeout
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=timeout)

    monkeypatch.setattr(
        "simulator.melt_backend.magemin.subprocess.run", fake_run
    )
    monkeypatch.setattr(liquidus_module.time, "monotonic", lambda: clock["t"])

    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"
    backend._binary_path = Path("/tmp/fake-MAGEMin")
    backend._config = {
        "timeout_s": 60.0,
        "database": "ig",
        "liquidus_finder_budget_s": 0.05,
    }
    backend._database = "ig"
    backend._magemin_module = None

    result = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == "not_converged"
    assert seen_timeouts == [pytest.approx(0.05)]
    assert any(
        "liquidus finder exceeded aggregate budget 0.05s after 0 calls"
        in warning
        for warning in result.warnings
    )
    assert result.diagnostics["reason"] == "aggregate_budget_exceeded"
    assert result.diagnostics["call_count"] == 0
    assert result.diagnostics["last_T_C"] == pytest.approx(800.0)
    assert result.diagnostics["budget_s"] == pytest.approx(0.05)
    assert result.diagnostics["elapsed_s"] == pytest.approx(0.05)
    assert not any(
        warning.startswith("liquidus finder failed:")
        for warning in result.warnings
    )


def test_magemin_liquidus_finder_unavailable_without_backend():
    backend = MAGEMinBackend()

    result = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert result.status == "unavailable"
    assert "not initialized" in " ".join(result.warnings)


# ----------------------------------------------------------------------
# Mocked-absent path: no MAGEMin binary, no bridge.
# ----------------------------------------------------------------------


def test_magemin_absent_binary_marks_backend_unavailable(monkeypatch):
    # No binary anywhere -> initialize() returns False and the backend
    # stays unavailable. The simulator can then route around it.
    _make_absent_magemin(monkeypatch)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({}) is False
    assert backend.is_available() is False


def test_magemin_absent_equilibrate_returns_empty_result_with_warning(
    monkeypatch,
):
    # When MAGEMin is unavailable, equilibrate() must NOT raise: it
    # returns an empty EquilibriumResult carrying an explanatory warning,
    # and -- critically for the shadow posture -- no ledger transition.
    _make_absent_magemin(monkeypatch)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})
    assert backend.is_available() is False

    result = backend.equilibrate(
        1500.0,
        composition_mol={"SiO2": 1.0, "MgO": 0.5},
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert result.phases_present == []
    assert result.phase_masses_kg == {}
    assert result.ledger_transition is None
    assert result.warnings
    assert any("not initialized" in w for w in result.warnings)
    assert result.status == "unavailable"


# ----------------------------------------------------------------------
# Mocked-present path: a tiny fake bridge module.
# Verifies oxide-basis projection + pressure_bar -> P_GPa conversion +
# EquilibriumResult population.
# ----------------------------------------------------------------------


def test_magemin_fake_bridge_receives_oxide_wt_pct_basis(monkeypatch):
    # The fake bridge captures what the adapter handed it: the input must
    # be projected onto the MAGEMin database wt% basis and normalized to 100.
    # Non-basis species are a fail-closed out_of_domain path covered below;
    # this bridge-boundary test stays on in-domain oxides.
    captured = {}

    def minimize(**kwargs):
        captured.update(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    # Mol-native in-domain oxide input.
    result = backend.equilibrate(
        1400.0,
        composition_mol={
            "SiO2": 5.0,
            "Al2O3": 1.0,
            "MgO": 2.0,
            "CaO": 1.5,
        },
        fO2_log=-8.0,
        pressure_bar=5000.0,
    )

    assert result.status == "ok"
    comp = captured["composition"]
    assert set(comp).issubset(
        {
            "SiO2", "Al2O3", "CaO", "MgO", "FeOt", "K2O", "Na2O",
            "TiO2", "O", "Cr2O3", "H2O",
        }
    )
    assert "SiO2" in comp and comp["SiO2"] > 0.0
    # MAGEMin database wt% basis is normalized to 100.
    assert sum(comp.values()) == pytest.approx(100.0, rel=1e-6)


def test_magemin_fake_bridge_receives_pressure_in_gpa(monkeypatch):
    # The binding-spec contract (§4) is pressure in GPa. The adapter must
    # convert pressure_bar -> P_GPa with 1 GPa = 10000 bar before the
    # library boundary, and also expose the kbar form the binary's CLI
    # wants (1 GPa = 10 kbar).
    captured = {}

    def minimize(**kwargs):
        captured.update(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    # 15000 bar == 1.5 GPa == 15 kbar.
    backend.equilibrate(
        1450.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0},
        fO2_log=-8.0,
        pressure_bar=15000.0,
    )

    assert captured["P_GPa"] == pytest.approx(1.5)
    assert captured["P_kbar"] == pytest.approx(15.0)
    # Temperature is passed through in both C and K.
    assert captured["T_C"] == pytest.approx(1450.0)
    assert captured["T_K"] == pytest.approx(1450.0 + 273.15)


def test_magemin_bulk_projection_drop_is_composition_projected_refusal(
    monkeypatch,
):
    calls = []

    def minimize(**kwargs):
        calls.append(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    result = backend.equilibrate(
        1400.0,
        composition_kg={"SiO2": 50.0, "MgO": 50.0, "MnO": 1.0},
        fO2_log=-8.0,
        pressure_bar=5000.0,
    )

    assert calls
    assert "MnO" not in calls[0]["composition"]
    _assert_magemin_database_exclusion(
        result,
        {"MnO": 1.0},
        calls=calls,
    )
    assert result.phase_masses_kg["liq"] == pytest.approx(1.0)
    assert result.liquid_fraction == pytest.approx(1.0)


def test_magemin_p2o5_bulk_is_composition_projected_refusal(monkeypatch):
    calls = []

    def minimize(**kwargs):
        calls.append(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    result = backend.equilibrate(
        1400.0,
        composition_kg={"SiO2": 50.0, "MgO": 40.0, "P2O5": 10.0},
        fO2_log=-8.0,
        pressure_bar=5000.0,
    )

    assert calls
    assert "P2O5" not in calls[0]["composition"]
    _assert_magemin_database_exclusion(
        result,
        {"P2O5": 10.0},
        calls=calls,
    )
    assert result.liquid_fraction == pytest.approx(1.0)


def test_magemin_liquidus_of_excluded_bulk_solves_the_majors():
    """An ig-order drop excludes that mass and still solves the majors."""
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"
    backend._binary_path = Path("/fake/MAGEMin")
    backend._config = {}
    backend._subprocess_pool = None
    calls = []

    def fake_call(*, bulk_projection, temperature_C, **_kwargs):
        composition = dict(bulk_projection.composition_wt_pct)
        calls.append(composition)
        assert "P2O5" not in composition
        assert "MnO" not in composition
        frac = max(0.0, min(1.0, (float(temperature_C) - 1000.0) / 200.0))
        phases = {}
        if frac > 0.0:
            phases["liq"] = {"mass_kg": frac}
        if frac < 1.0:
            phases["ol"] = {"mass_kg": 1.0 - frac}
        return {"phases": phases, "converged": True}

    backend._call_magemin = fake_call
    bulk = {"SiO2": 50.0, "MgO": 39.0, "P2O5": 10.0, "MnO": 1.0}
    result = backend.find_liquidus_solidus(
        composition_kg=bulk,
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=900.0,
        max_T_C=1300.0,
        scan_step_C=50.0,
        tolerance_C=2.0,
    )

    assert calls
    assert result.status == "ok"
    assert result.solidus_T_C == pytest.approx(1000.0, abs=2.0)
    assert result.liquidus_T_C == pytest.approx(1200.0, abs=2.0)
    assert result.solidus_T_C < result.liquidus_T_C
    assert "composition_projected_notice" not in result.diagnostics
    excluded = result.diagnostics["magemin_excluded_database_components_kg"]
    assert excluded["P2O5"] == pytest.approx(10.0)
    assert excluded["MnO"] == pytest.approx(1.0)
    assert diagnostics_name_composition_projected(result.diagnostics) is False

    calls_before = len(calls)
    solved = backend.equilibrate(
        1400.0,
        composition_kg=bulk,
        fO2_log=-9.0,
        pressure_bar=1.0,
    )
    assert solved.status == "ok"
    solved_excluded = solved.diagnostics[
        "magemin_excluded_database_components_kg"
    ]
    assert solved_excluded["P2O5"] == pytest.approx(10.0)
    assert solved_excluded["MnO"] == pytest.approx(1.0)
    assert len(calls) == calls_before + 1


def test_magemin_pressure_conversion_helpers_are_exact():
    # The conversion is load-bearing: a wrong factor is a silent O(10^n)
    # pressure error. Pin both legs.
    assert MAGEMinBackend._pressure_bar_to_GPa(10000.0) == pytest.approx(1.0)
    assert MAGEMinBackend._pressure_bar_to_GPa(0.0) == pytest.approx(0.0)
    assert MAGEMinBackend._pressure_bar_to_GPa(2.5e5) == pytest.approx(25.0)
    assert MAGEMinBackend._GPa_to_kbar(1.0) == pytest.approx(10.0)
    assert MAGEMinBackend._GPa_to_kbar(1.5) == pytest.approx(15.0)


def test_magemin_feot_conversion_uses_iupac_2feo_mass():
    # Current standard atomic weights: Fe 55.845, O 15.999 g/mol.
    feo_molar_mass = (
        MAGEMinBackend._FE_MOLAR_MASS_G_PER_MOL
        + MAGEMinBackend._O_MOLAR_MASS_G_PER_MOL
    )
    fe2o3_molar_mass = (
        2 * MAGEMinBackend._FE_MOLAR_MASS_G_PER_MOL
        + 3 * MAGEMinBackend._O_MOLAR_MASS_G_PER_MOL
    )
    feot_numerator = 2 * feo_molar_mass

    assert feot_numerator == pytest.approx(143.688)
    assert FEOT_FROM_FE2O3 == pytest.approx(
        feot_numerator / fe2o3_molar_mass
    )


def test_magemin_db_bulk_orders_match_installed_binary_help():
    assert MAGEMinBackend._DB_BULK_ORDERS == {
        "ig": (
            "SiO2", "Al2O3", "CaO", "MgO", "FeOt", "K2O", "Na2O",
            "TiO2", "O", "Cr2O3", "H2O",
        ),
        "igad": (
            "SiO2", "Al2O3", "CaO", "MgO", "FeOt", "K2O", "Na2O",
            "TiO2", "O", "Cr2O3",
        ),
        "mp": (
            "SiO2", "Al2O3", "CaO", "MgO", "FeOt", "K2O", "Na2O",
            "TiO2", "O", "MnO", "H2O",
        ),
        "mb": (
            "SiO2", "Al2O3", "CaO", "MgO", "FeOt", "K2O", "Na2O",
            "TiO2", "O", "H2O",
        ),
        "um": ("SiO2", "Al2O3", "MgO", "FeOt", "O", "H2O", "S"),
        "ume": (
            "SiO2", "Al2O3", "MgO", "FeOt", "O", "H2O", "S", "CaO",
            "Na2O",
        ),
        "mtl": ("SiO2", "Al2O3", "CaO", "MgO", "FeOt", "Na2O"),
    }


def test_magemin_db_projection_keeps_mp_mno_and_records_igad_dry_drop():
    backend = MAGEMinBackend()
    composition = {
        "SiO2": 45.0,
        "Al2O3": 10.0,
        "FeO": 12.0,
        "MnO": 0.4,
        "Cr2O3": 0.3,
        "H2O": 2.0,
    }

    mp = backend._build_db_bulk_projection(composition, database="mp")
    assert mp.order == MAGEMinBackend._DB_BULK_ORDERS["mp"]
    assert mp.vector[mp.order.index("MnO")] == pytest.approx(0.4)
    assert "Cr2O3" not in mp.composition_wt_pct
    assert mp.dropped_components == ("Cr2O3",)
    assert mp.merged_components == ("FeO->FeOt",)
    assert any("Cr2O3" in warning for warning in mp.warnings)
    assert any("FeO->FeOt" in warning for warning in mp.warnings)

    igad = backend._build_db_bulk_projection(composition, database="igad")
    assert "H2O" not in igad.order
    assert "H2O" not in igad.composition_wt_pct
    assert "MnO" not in igad.composition_wt_pct
    assert igad.dropped_components == ("H2O", "MnO")
    assert any("H2O" in warning and "MnO" in warning for warning in igad.warnings)


def test_magemin_db_projection_ume_and_mtl_orders_are_not_ig_shaped():
    backend = MAGEMinBackend()
    composition = {
        "SiO2": 40.0,
        "Al2O3": 5.0,
        "MgO": 30.0,
        "FeO": 10.0,
        "K2O": 4.0,
        "Na2O": 3.0,
        "CaO": 2.0,
        "H2O": 1.0,
        "S": 0.5,
    }

    ume = backend._build_db_bulk_projection(composition, database="ume")
    assert ume.order == MAGEMinBackend._DB_BULK_ORDERS["ume"]
    assert "CaO" in ume.composition_wt_pct
    assert "Na2O" in ume.composition_wt_pct
    assert "S" in ume.composition_wt_pct
    assert "K2O" not in ume.composition_wt_pct

    mtl = backend._build_db_bulk_projection(composition, database="mtl")
    assert mtl.order == MAGEMinBackend._DB_BULK_ORDERS["mtl"]
    assert len(mtl.vector) == 6
    assert "H2O" not in mtl.composition_wt_pct
    assert "S" not in mtl.composition_wt_pct
    assert any("H2O" in warning and "S" in warning for warning in mtl.warnings)


def test_magemin_db_projection_rejects_unknown_bulk_component():
    backend = MAGEMinBackend()

    with pytest.raises(MeltCompositionError, match="unprojectable.*CO2"):
        backend._build_db_bulk_projection({"SiO2": 50.0, "CO2": 1.0})


def test_magemin_ig_bulk_vector_folds_fe2o3_to_feot():
    backend = MAGEMinBackend()
    vector = backend._build_ig_bulk_vector({"FeO": 10.0, "Fe2O3": 1.0})
    feot_index = MAGEMinBackend._IG_BULK_ORDER.index("FeOt")
    oxygen_index = MAGEMinBackend._IG_BULK_ORDER.index("O")

    expected_feot = 10.0 + FEOT_FROM_FE2O3
    assert vector[feot_index] == pytest.approx(expected_feot)
    assert vector[oxygen_index] == pytest.approx(
        MAGEMinBackend._EXCESS_O_FROM_FE2O3_FACTOR
    )
    assert vector[feot_index] + vector[oxygen_index] == pytest.approx(11.0)


def test_magemin_explicit_fe2o3_does_not_apply_total_iron_o_provision():
    backend = MAGEMinBackend()
    vector = backend._build_ig_bulk_vector({"FeO": 10.0, "Fe2O3": 1.0})
    oxygen_index = MAGEMinBackend._IG_BULK_ORDER.index("O")

    assert vector[oxygen_index] == pytest.approx(
        1.0 * MAGEMinBackend._EXCESS_O_FROM_FE2O3_FACTOR
    )


def test_magemin_ig_bulk_vector_feo_without_fe2o3_keeps_iron():
    """FeO with no Fe2O3 stays FeOt. No oxygen is invented from that FeO.

    16.5 wt% FeO is 16.5/M_FeO moles of Fe. The FeOt mass that preserves
    those moles is 16.5. Excess O is 0 because the input has no Fe2O3.
    """
    backend = MAGEMinBackend()
    vector = backend._build_ig_bulk_vector(
        {
            "SiO2": 49.0,
            "Al2O3": 14.0,
            "CaO": 11.0,
            "MgO": 8.0,
            "FeO": 16.5,
        }
    )
    feot_index = MAGEMinBackend._IG_BULK_ORDER.index("FeOt")
    oxygen_index = MAGEMinBackend._IG_BULK_ORDER.index("O")

    assert vector[feot_index] == pytest.approx(16.5)
    assert vector[oxygen_index] == pytest.approx(0.0)


def test_lunar_mare_low_ti_ig_bulk_vector_pin():
    """Pin the ig bulk vector for catalog lunar_mare_low_ti.

    The composition is data/feedstocks.yaml, restricted to the adapter
    input basis, then ``_build_db_bulk_projection``. With no Fe2O3, FeOt
    equals the feedstock FeO and the O slot is 0.
    """
    feedstocks = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "data" / "feedstocks.yaml").read_text()
    )
    composition = feedstocks["lunar_mare_low_ti"]["composition_wt_pct"]
    basis = set(MAGEMinBackend._MAGEMIN_INPUT_BASIS)
    in_basis = {
        str(name): float(value)
        for name, value in composition.items()
        if str(name) in basis and float(value or 0.0) > 0.0
    }
    projection = MAGEMinBackend()._build_db_bulk_projection(
        in_basis, database="ig"
    )

    assert projection.order == MAGEMinBackend._IG_BULK_ORDER
    assert projection.vector == (
        44.5,
        13.5,
        11.0,
        9.0,
        16.5,
        0.1,
        0.4,
        1.5,
        0.0,
        0.35,
        0.0,
    )
    assert projection.vector[projection.order.index("FeOt")] == in_basis["FeO"]
    assert projection.vector[projection.order.index("O")] == 0.0
    assert "Fe2O3" not in in_basis
    assert projection.dropped_components == ("MnO", "P2O5", "S")
    assert projection.merged_components == ("FeO->FeOt",)
    assert projection.source_sum_wt_pct == 97.22


def test_buffer_reservoir_adds_oxygen_without_moving_feot(monkeypatch):
    """The reservoir is extra O. FeOt stays the feedstock iron.

    An input that already carries excess O is not topped up. A
    non-positive factor adds nothing, which is the O = 0 path.
    """
    backend = MAGEMinBackend()
    projection = backend._build_db_bulk_projection(
        {"SiO2": 44.5, "FeO": 16.5, "MgO": 9.0},
        database="ig",
    )
    order = projection.order
    feot = projection.vector[order.index("FeOt")]
    assert feot == pytest.approx(16.5)
    assert projection.vector[order.index("O")] == pytest.approx(0.0)

    sent = backend._bulk_with_buffer_reservoir(projection, buffer_name="qfm")
    assert sent[order.index("FeOt")] == feot
    assert sent[order.index("O")] == pytest.approx(
        feot * MAGEMinBackend._EXCESS_O_RESERVOIR_PER_FEOT
    )
    assert projection.vector[order.index("O")] == pytest.approx(0.0)

    ferric = backend._build_db_bulk_projection(
        {"SiO2": 44.5, "FeO": 10.0, "Fe2O3": 1.0},
        database="ig",
    )
    resent = backend._bulk_with_buffer_reservoir(ferric, buffer_name="qfm")
    assert resent[ferric.order.index("FeOt")] == ferric.vector[
        ferric.order.index("FeOt")
    ]
    assert resent[ferric.order.index("O")] == pytest.approx(
        ferric.vector[ferric.order.index("O")]
    )

    monkeypatch.setattr(MAGEMinBackend, "_EXCESS_O_RESERVOIR_PER_FEOT", 0.0)
    bare = backend._bulk_with_buffer_reservoir(projection, buffer_name="qfm")
    assert bare[order.index("FeOt")] == feot
    assert bare[order.index("O")] == pytest.approx(0.0)


def test_magemin_fake_bridge_populates_equilibrium_result(monkeypatch):
    # A successful call must populate phases_present, phase_masses_kg and
    # liquid_fraction from the library's phase block -- and still leave
    # ledger_transition None (shadow posture).
    def minimize(**kwargs):
        return {
            "phases": {
                "liq": {"mass_kg": 0.8},
                "ol": {"mass_kg": 0.15},
                "spl": {"mass_kg": 0.05},
            }
        }

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    result = backend.equilibrate(
        1350.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0, "FeO": 1.0},
        fO2_log=-8.0,
        pressure_bar=2000.0,
    )

    assert set(result.phases_present) == {"liq", "ol", "spl"}
    assert result.phase_masses_kg["liq"] == pytest.approx(0.8)
    # liquid_fraction = liquid mass / total mass.
    assert result.liquid_fraction == pytest.approx(0.8 / 1.0)
    assert result.ledger_transition is None
    assert result.temperature_C == pytest.approx(1350.0)
    assert result.pressure_bar == pytest.approx(2000.0)
    assert result.status == "ok"


def test_magemin_ok_result_with_nonfinite_phase_mass_raises(monkeypatch):
    def minimize(**kwargs):
        return {
            "phases": {
                "liq": {"mass_kg": float("nan")},
                "ol": {"mass_kg": 0.2},
            }
        }

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    with pytest.raises(LiquidFractionInvalidError, match="phase_mass_invalid"):
        backend.equilibrate(
            1350.0,
            composition_mol={"SiO2": 5.0, "MgO": 3.0, "FeO": 1.0},
            fO2_log=-8.0,
            pressure_bar=2000.0,
        )


def test_magemin_fake_bridge_library_error_returns_warning(monkeypatch):
    # A library-boundary exception must be caught and surfaced as a
    # warning on an otherwise-empty result -- never raised.
    def minimize(**kwargs):
        raise RuntimeError("synthetic MAGEMin failure")

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    result = backend.equilibrate(
        1500.0,
        composition_mol={"SiO2": 1.0},
        fO2_log=-8.0,
        pressure_bar=1.0,
    )

    assert result.phases_present == []
    assert result.ledger_transition is None
    assert any("synthetic MAGEMin failure" in w for w in result.warnings)
    assert result.status == "not_converged"


def test_magemin_only_consumes_cleaned_melt_account(monkeypatch):
    # When called with the layered ABC's composition_mol_by_account, the
    # adapter must consume only process.cleaned_melt and warn about every
    # other account it dropped (binding spec §7 -- no metal/salt/sulfide).
    captured = {}

    def minimize(**kwargs):
        captured.update(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    result = backend.equilibrate(
        1400.0,
        composition_mol_by_account={
            "process.cleaned_melt": {"SiO2": 5.0, "MgO": 3.0, "NaCl": 0.25},
            "process.metal_alloy": {"Fe": 2.0},
            "process.sulfide_matte": {"FeS": 1.0},
        },
        fO2_log=-8.0,
        pressure_bar=1000.0,
    )

    assert captured == {}
    assert result.status == "out_of_domain"
    assert result.diagnostics["backend_status_reason"] == (
        OutOfDomainReason.FORBIDDEN_SPECIES.value
    )
    dropped_warnings = " ".join(result.warnings)
    assert "process.metal_alloy" in dropped_warnings
    assert "process.sulfide_matte" in dropped_warnings
    assert "dropped_non_basis_melt_mass" in dropped_warnings
    dropped = result.diagnostics["dropped_non_basis_melt_mass_kg_by_species"]
    assert "NaCl" in dropped
    assert dropped["NaCl"] > 0.0
    projection = result.diagnostics["input_composition_projection"]
    assert projection["status"] == "projected"
    assert projection["dropped_species"] == ["NaCl"]
    assert projection["dropped_accounts"] == [
        "process.metal_alloy",
        "process.sulfide_matte",
    ]
    assert projection["dropped_account_species"] == {
        "process.metal_alloy": ["Fe"],
        "process.sulfide_matte": ["FeS"],
    }
    assert projection["renormalization_delta"] > 0.0


# ----------------------------------------------------------------------
# Live smoke test: runs the real MAGEMin binary if one is built locally.
# Skipif-guarded so CI without a built MAGEMin still passes.
# ----------------------------------------------------------------------

# Resolve a real binary at collection time so the guard is a true
# pytest.mark.skipif rather than a runtime branch. MAGEMin v1.9.3 is built
# locally as a sibling clone (../MAGEMin/MAGEMin); _locate_binary also
# checks engines/magemin/{,bin/}MAGEMin and PATH.
_LIVE_MAGEMIN_BINARY = MAGEMinBackend._locate_binary(None)


def _seat_magemin_binary():
    """Binary from engines.local.toml, else the sibling/PATH probe.

    Sparse seats keep the engine path in the gitignored toml.
    ``_locate_binary(None)`` does not read that file, so a toml-only
    seat would skip every live test.
    """
    from simulator.engine_local_config import configured_magemin_binary_path

    configured = configured_magemin_binary_path()
    if configured is not None:
        return configured
    return _LIVE_MAGEMIN_BINARY


_SEAT_MAGEMIN_BINARY = _seat_magemin_binary()


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_smoke_runs_real_binary():
    # End-to-end against the real MAGEMin binary: a basalt analog at
    # crustal P/T must equilibrate, report a silicate liquid, and -- the
    # invariant that matters -- leave ledger_transition None. MAGEMin is
    # shadow/diagnostic: it never gets AtomLedger authority.
    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({})
    if not available:
        pytest.skip("MAGEMin binary present but backend failed to initialize")

    # The subprocess bridge is the supported default; the live binary
    # must resolve to it (ctypes is opt-in only, pymagemin/julia rare).
    assert backend._bridge == "subprocess"

    basalt_wt_pct = {
        "SiO2": 49.0,
        "TiO2": 1.5,
        "Al2O3": 14.0,
        "FeO": 10.0,
        "Fe2O3": 1.0,
        "MgO": 9.0,
        "CaO": 11.0,
        "Na2O": 2.5,
        "K2O": 0.8,
        "Cr2O3": 0.2,
    }

    # 2000 bar == 0.2 GPa == 2 kbar; well inside the igneous database's
    # crustal calibration. 1200 C is super-liquidus for this analog.
    result = backend.equilibrate(
        1200.0,
        composition_kg=basalt_wt_pct,
        fO2_log=-8.0,
        pressure_bar=2000.0,
    )

    # No library-boundary error. Oxides with no ig endmember (P2O5/MnO/NiO/CoO)
    # are excluded and their mass recorded, tested separately; this live smoke
    # stays inside the documented ig bulk order.
    assert result.status == "ok", result.warnings
    assert not any("failed" in w for w in result.warnings), result.warnings
    # MAGEMin reports a phase assemblage including a silicate liquid.
    assert result.phases_present
    assert any(
        name.lower().startswith("liq") for name in result.phases_present
    ), result.phases_present
    assert result.phase_masses_kg
    # At 1200 C this analog is liquid-dominated.
    assert 0.0 < result.liquid_fraction <= 1.0
    # Shadow posture: MAGEMin holds no AtomLedger authority, ever.
    assert result.ledger_transition is None
    assert backend.ledger_account_policies() == ()


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_subliquidus_run_reports_crystalline_phases():
    # A second live point below the liquidus: the binary must report
    # crystalline phases alongside (or instead of) the melt, and the
    # liquid fraction must drop relative to the super-liquidus case.
    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({})
    if not available:
        pytest.skip("MAGEMin binary present but backend failed to initialize")

    peridotite_wt_pct = {
        "SiO2": 45.0,
        "TiO2": 0.2,
        "Al2O3": 4.0,
        "FeO": 8.0,
        "MgO": 38.0,
        "CaO": 3.5,
        "Na2O": 0.3,
        "Cr2O3": 0.4,
    }

    # 1000 C at 10 kbar (1 GPa, 10000 bar) is sub-solidus to
    # low-melt-fraction for a peridotite -- expect crystalline phases.
    result = backend.equilibrate(
        1000.0,
        composition_kg=peridotite_wt_pct,
        fO2_log=-9.0,
        pressure_bar=10000.0,
    )

    assert not any("failed" in w for w in result.warnings), result.warnings
    assert result.phases_present
    crystalline = [
        name
        for name in result.phases_present
        if not name.lower().startswith("liq")
    ]
    assert crystalline, result.phases_present
    assert result.ledger_transition is None


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_liquidus_finder_lunar_mare_low_ti_sane():
    """Apollo low-Ti mare basalt sample 12009 begins crystallizing near 1230 C.

    Reference: Walker et al. 1971, Experimental petrology of Apollo 12 basalts,
    part 1, sample 12009. This feedstock is only an Apollo 12/15 low-Ti soil
    analog, so the test checks a sane bracket rather than a forced retune.
    """
    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({})
    if not available:
        pytest.skip("MAGEMin binary present but backend failed to initialize")

    lunar_mare_low_ti_wt_pct = {
        "SiO2": 44.5,
        "TiO2": 1.5,
        "Al2O3": 13.5,
        "FeO": 16.5,
        "MgO": 9.0,
        "CaO": 11.0,
        "Na2O": 0.4,
        "K2O": 0.10,
        "Cr2O3": 0.35,
    }

    result = backend.find_liquidus_solidus(
        composition_kg=lunar_mare_low_ti_wt_pct,
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=2.0,
    )

    assert result.status == "ok", result.warnings
    assert 900.0 <= result.solidus_T_C <= 1100.0
    assert 1200.0 <= result.liquidus_T_C <= 1450.0
    assert result.liquidus_T_C >= result.solidus_T_C

    phase_result = backend.equilibrate(
        1300.0,
        composition_kg=lunar_mare_low_ti_wt_pct,
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert phase_result.status == "ok", phase_result.warnings
    assert phase_result.phases_present


# Pure-endmember melting references retained for any future calibrated engine
# (not MAGEMin `ig` acceptance targets). Forsterite 2163 K (Akimoto et al.
# 1981); diopside 1391.5 C and anorthite 1553 C (standard Di-An calibration).
# Former collected xfail nodes always xfailed before any engine work (removed
# 2026-07-29 / re-landed 2026-08-03); suite no longer pays three placeholder
# xfail nodes.


def _run_magemin_gam_o(binary: Path, *, buffer_n: float) -> float:
    """Run the live binary at one ``buffer_n`` and return GAM[O] (mu of the
    oxygen system component, kJ).

    Builds the ``ig`` bulk vector directly with a **nonzero O component** so
    the fO2 buffer actually engages. P3-F showed MAGEMin's qfm buffer is inert
    when ``O=0`` (see
    ``docs-private/research/2026-06-05-p3f/findings.md`` Finding 2). This test
    therefore bypasses the adapter to probe the binary's real redox response.

    GAM is reported in IG component order
    ``SiO2 Al2O3 CaO MgO FeOt K2O Na2O TiO2 O Cr2O3 H2O`` -> O is index 8.
    """
    # Basalt analog (matches the live-smoke test) with O set nonzero.
    bulk = "49,14,11,9,10.899810,0.8,2.5,1.5,1.0,0.2,0"
    completed = subprocess.run(  # noqa: S603 - args are test-built constants
        [
            str(binary),
            "--Verb=2",
            "--db=ig",
            "--Temp=1200.0",
            "--Pres=2.0",
            "--sys_in=wt",
            f"--Bulk={bulk}",
            "--buffer=qfm",
            f"--buffer_n={buffer_n:.6f}",
        ],
        cwd=str(binary.parent),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    match = re.search(r"GAM = \[([^\]]+)\]", completed.stdout)
    assert match, f"no GAM vector in MAGEMin stdout:\n{completed.stdout}"
    gam = [float(x) for x in match.group(1).split(",")]
    # Full IG order has 11 components; the O-component mu is index 8.
    assert len(gam) == 11, gam
    return gam[8]


def _run_magemin_adapter_buffer_probe(
    binary: Path,
    *,
    fO2_log: float,
) -> tuple[float, tuple[str, ...], tuple[float, ...]]:
    """Run the live binary through the production adapter's ig vector path."""
    backend = MAGEMinBackend()
    basalt_wt_pct = {
        "SiO2": 49.0,
        "TiO2": 1.5,
        "Al2O3": 14.0,
        "FeO": 10.0,
        "Fe2O3": 1.0,
        "MgO": 9.0,
        "CaO": 11.0,
        "Na2O": 2.5,
        "K2O": 0.8,
        "Cr2O3": 0.2,
    }
    temperature_C = 1200.0
    pressure_bar = 2000.0
    pressure_kbar = backend._GPa_to_kbar(
        backend._pressure_bar_to_GPa(pressure_bar)
    )
    bulk = backend._build_ig_bulk_vector(basalt_wt_pct)
    buffer_name, buffer_n, _warnings = backend._resolve_buffer(
        temperature_C=temperature_C,
        fO2_log=fO2_log,
    )
    completed = subprocess.run(  # noqa: S603 - args are test-built constants
        [
            str(binary),
            "--Verb=2",
            f"--db={backend._database}",
            f"--Temp={temperature_C:.6f}",
            f"--Pres={pressure_kbar:.6f}",
            "--sys_in=wt",
            "--Bulk=" + ",".join(f"{value:.6f}" for value in bulk),
            f"--buffer={buffer_name}",
            f"--buffer_n={buffer_n:.6f}",
        ],
        cwd=str(binary.parent),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    gam_match = re.search(r"GAM = \[([^\]]+)\]", completed.stdout)
    assert gam_match, f"no GAM vector in MAGEMin stdout:\n{completed.stdout}"
    gam = [float(x) for x in gam_match.group(1).split(",")]
    assert len(gam) == 11, gam

    phase_match = re.search(r"^\s*Phase\s*:\s*(.+)$", completed.stdout, re.M)
    mode_match = re.search(r"^\s*Mode\s*:\s*(.+)$", completed.stdout, re.M)
    assert phase_match and mode_match, (
        f"no Phase/Mode block in MAGEMin stdout:\n{completed.stdout}"
    )
    phases = tuple(phase_match.group(1).split())
    modes = tuple(float(x) for x in mode_match.group(1).split())
    assert len(phases) == len(modes)
    return gam[8], phases, modes


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_buffer_n_sign_and_magnitude_round_trip():
    """P3-F: the live binary must honour ``--buffer_n`` with the correct sign
    AND magnitude, validating ``_resolve_buffer``'s
    ``buffer_n = fO2_log - QFM(T)`` translation against the real MAGEMin.

    The single-point ``ig`` CLI prints no explicit fO2/Fe3+; redox is carried
    by the oxygen component's chemical potential, reported as GAM[O]. We use
    GAM[O] as the non-speculative redox proxy (recon:
    ``docs-private/research/2026-06-05-p3f/findings.md`` Finding 1).

    Two invariants, both anchored to MAGEMin's documented buffer formula
    ``mu_offset(O2) = T_K * 0.019145 * buffer_n`` (0.019145 = R*ln10/1000):
      - SIGN: higher buffer_n => higher (less negative) GAM[O] => more
        oxidizing. So requesting fO2 above QFM (positive buffer_n) is more
        oxidizing, confirming the translation sign.
      - MAGNITUDE: d(GAM[O])/d(buffer_n) == T_K * 0.019145 / 2 (per single O;
        the formula is per O2 = 2 O), so a delta of 4 buffer_n units shifts
        GAM[O] by T_K * 0.019145 * 4 / 2 kJ.
    """
    binary = _LIVE_MAGEMIN_BINARY
    mu_o_reduced = _run_magemin_gam_o(binary, buffer_n=-2.0)
    mu_o_oxidized = _run_magemin_gam_o(binary, buffer_n=2.0)

    # SIGN: oxidizing (higher buffer_n) gives a less-negative oxygen mu.
    assert mu_o_oxidized > mu_o_reduced, (
        f"buffer_n=+2 mu_O={mu_o_oxidized} must exceed "
        f"buffer_n=-2 mu_O={mu_o_reduced} (higher buffer_n = more oxidizing)"
    )

    # MAGNITUDE: anchored to MAGEMin's own buffer formula, not a fitted
    # constant. T = 1200 C = 1473.15 K; delta buffer_n = 4.
    T_K = 1200.0 + 273.15
    expected_delta = T_K * 0.019145 * 4.0 / 2.0
    observed_delta = mu_o_oxidized - mu_o_reduced
    assert observed_delta == pytest.approx(expected_delta, abs=0.5), (
        f"GAM[O] shift {observed_delta:.4f} kJ over buffer_n delta=4 must "
        f"match MAGEMin's buffer formula prediction {expected_delta:.4f} kJ"
    )


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_adapter_path_fO2_changes_shadow_response():
    """Requested fO2 must move the MAGEMin shadow response.

    This intentionally drives the production adapter path
    (``_build_ig_bulk_vector`` + ``_resolve_buffer``). The probe bulk
    includes explicit Fe2O3, so the O component is that oxide's excess
    oxygen. P3-F showed MAGEMin's qfm buffer changes GAM[O] when ig O
    is nonzero.
    """
    binary = _LIVE_MAGEMIN_BINARY
    reduced = _run_magemin_adapter_buffer_probe(binary, fO2_log=-12.0)
    oxidized = _run_magemin_adapter_buffer_probe(binary, fO2_log=-4.0)

    gam_o_changed = abs(oxidized[0] - reduced[0]) > 1.0e-6
    assemblage_changed = oxidized[1] != reduced[1]
    modes_changed = oxidized[2] != pytest.approx(reduced[2], abs=1.0e-9)
    # Require the robust, production-parsed signals (phase assemblage / modes) to
    # move, not GAM[O] alone: the subprocess bridge reliably exposes Phase/Mode,
    # whereas GAM[O] is a secondary readout. GAM is kept as a sanity signal.
    assert assemblage_changed or modes_changed, (
        "MAGEMin adapter path must move phase assemblage or modes across widely "
        f"separated fO2_log values; reduced={reduced} oxidized={oxidized} "
        f"(gam_o_changed={gam_o_changed})"
    )


def _lunar_mare_low_ti_wt_pct() -> dict:
    """Catalog lunar_mare_low_ti restricted to the adapter input basis.

    Trace keys outside that basis are a typed refusal, not a MAGEMin
    bulk. MnO, P2O5 and S stay: they are in the basis and outside the
    ig order, which is the exclusion path.
    """
    feedstocks = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "data" / "feedstocks.yaml").read_text()
    )
    composition = feedstocks["lunar_mare_low_ti"]["composition_wt_pct"]
    basis = set(MAGEMinBackend._MAGEMIN_INPUT_BASIS)
    return {
        str(name): float(value)
        for name, value in composition.items()
        if str(name) in basis and float(value or 0.0) > 0.0
    }


@pytest.mark.skipif(
    _SEAT_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_buffer_reservoir_is_independent_for_lunar_mare(monkeypatch):
    """Lunar FeO-only bulk at one T and one fO2 offset.

    The reservoir at 0.5x, 1x and 2x of FeOt * M_O / (2 * M_FeO) must
    keep qfm active and return the same liquid fraction, phase set and
    liquid FeO/O (the ig ferric signal) within the bulk-echo tolerance.
    With the factor at 0, O stays 0, qfm drops out, and the adapter must
    not report solved or requested fO2 authority.
    """
    binary = _SEAT_MAGEMIN_BINARY
    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({
            "binary_path": str(binary),
            "warm_worker": False,
        })
    if not available:
        pytest.skip("MAGEMin binary present but backend failed to initialize")

    composition = _lunar_mare_low_ti_wt_pct()
    base = MAGEMinBackend._EXCESS_O_RESERVOIR_PER_FEOT
    solved = []
    for scale in (0.5, 1.0, 2.0):
        monkeypatch.setattr(
            MAGEMinBackend,
            "_EXCESS_O_RESERVOIR_PER_FEOT",
            base * scale,
        )
        result = backend.equilibrate(
            1250.0,
            composition_kg=composition,
            fO2_log=-9.0,
            pressure_bar=1.0,
        )
        assert result.status == "ok", (scale, result.status, result.warnings)
        assert result.diagnostics["fO2_buffer_active"] is True
        assert result.diagnostics["fO2_buffer_mode"] > 0.0
        assert result.diagnostics["authoritative_for_solved_conditions"] is True
        assert result.diagnostics["solved_fO2_log"] == pytest.approx(-9.0)
        liquid = result.liquid_composition_wt_pct or {}
        solved.append((
            scale,
            float(result.liquid_fraction),
            tuple(sorted(result.phases_present)),
            float(liquid.get("FeO", 0.0)),
            float(liquid.get("O", 0.0)),
            float(result.diagnostics["fO2_buffer_mode"]),
        ))

    reference = solved[1]
    for scale, liquid_fraction, phases, feo, oxygen, _mode in solved:
        assert phases == reference[2], (scale, phases, reference[2])
        assert liquid_fraction == pytest.approx(reference[1], abs=1.0e-3)
        # Oxide wt% is printed to 0.001. 2x FeO moved by that one digit
        # (16.981 -> 16.982) while O stayed 0.139. The bulk-echo guard
        # is 0.3 wt%; 0.01 wt% still rejects a real ferric shift.
        assert feo == pytest.approx(reference[3], abs=1.0e-2)
        assert oxygen == pytest.approx(reference[4], abs=1.0e-2)
    # Extra reservoir oxygen reports as qfm mode, not as a new silicate.
    assert solved[0][5] < solved[1][5] < solved[2][5]

    monkeypatch.setattr(MAGEMinBackend, "_EXCESS_O_RESERVOIR_PER_FEOT", 0.0)
    bare = backend.equilibrate(
        1250.0,
        composition_kg=composition,
        fO2_log=-9.0,
        pressure_bar=1.0,
    )
    assert bare.diagnostics["fO2_buffer_active"] is False
    assert bare.diagnostics["operating_point"] == "unbuffered"
    assert bare.diagnostics["backend_status_reason"] == "buffer_inactive"
    assert bare.diagnostics["solved_fO2_log"] is None
    assert bare.diagnostics["authoritative_for_requested_conditions"] is False
    assert bare.diagnostics["authoritative_for_solved_conditions"] is False


def _inactive_buffer_solid(temperature_C):
    from simulator.melt_backend.base import EquilibriumResult

    return EquilibriumResult(
        temperature_C=float(temperature_C),
        status="out_of_domain",
        liquid_fraction=0.0,
        diagnostics={
            "backend_status_reason": "buffer_inactive",
            "fO2_buffer_active": False,
            "operating_point": "unbuffered",
            "solved_fO2_log": None,
            "authoritative_for_requested_conditions": False,
            "authoritative_for_solved_conditions": False,
        },
    )


def _buffered_melt(temperature_C, fraction):
    from simulator.melt_backend.base import EquilibriumResult

    return EquilibriumResult(
        temperature_C=float(temperature_C),
        status="ok",
        liquid_fraction=float(fraction),
        diagnostics={
            "fO2_buffer_active": True,
            "solved_fO2_log": -9.0,
        },
    )


def test_magemin_liquidus_keeps_solid_samples_when_qfm_mode_is_zero(
    monkeypatch,
):
    """Inactive qfm at 400 C and 450 C, buffered solid from 500 C.

    equilibrate still refuses solved fO2 on the cold points. Those
    points sit below a buffer-active solid, so the solidus and liquidus
    close on the buffered samples. A buffer_inactive sample that still
    has liquid stops the scan.
    """
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"
    backend._config["liquidus_finder_budget_s"] = 30.0

    def equilibrate(temperature_C, **_kwargs):
        temperature = float(temperature_C)
        if temperature < 500.0:
            return _inactive_buffer_solid(temperature)
        if temperature < 1100.0:
            fraction = 0.0
        elif temperature < 1400.0:
            fraction = (temperature - 1100.0) / 300.0
        else:
            fraction = 1.0
        return _buffered_melt(temperature, fraction)

    monkeypatch.setattr(backend, "equilibrate", equilibrate)
    found = backend.find_liquidus_solidus(
        composition_kg={"SiO2": 50.0, "FeO": 16.0, "MgO": 10.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=400.0,
        max_T_C=1600.0,
        scan_step_C=50.0,
        tolerance_C=5.0,
    )
    assert found.status == "ok", found.warnings
    assert found.solidus_T_C == pytest.approx(1100.0, abs=10.0)
    assert found.liquidus_T_C == pytest.approx(1400.0, abs=10.0)
    assert any("qfm mode is 0" in warning for warning in found.warnings)

    def equilibrate_with_liquid(temperature_C, **_kwargs):
        from simulator.melt_backend.base import EquilibriumResult

        return EquilibriumResult(
            temperature_C=float(temperature_C),
            status="out_of_domain",
            liquid_fraction=0.4,
            diagnostics={"backend_status_reason": "buffer_inactive"},
        )

    monkeypatch.setattr(backend, "equilibrate", equilibrate_with_liquid)
    refused = backend.find_liquidus_solidus(
        composition_kg={"SiO2": 50.0, "FeO": 16.0, "MgO": 10.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=400.0,
        max_T_C=800.0,
        scan_step_C=100.0,
        tolerance_C=5.0,
    )
    assert refused.status == "out_of_domain"
    assert refused.solidus_T_C is None


def test_magemin_liquidus_refuses_when_inactive_solid_decides_the_solidus(
    monkeypatch,
):
    """Inactive zeros up to the first buffered sample, which is molten.

    No buffer-active solid exists, so the solidus endpoint would be an
    unbuffered solid. That bracket is not ok.
    """
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"
    backend._config["liquidus_finder_budget_s"] = 30.0

    def equilibrate(temperature_C, **_kwargs):
        temperature = float(temperature_C)
        if temperature < 1100.0:
            return _inactive_buffer_solid(temperature)
        if temperature < 1400.0:
            fraction = 0.05 + 0.95 * (temperature - 1100.0) / 300.0
        else:
            fraction = 1.0
        return _buffered_melt(temperature, fraction)

    monkeypatch.setattr(backend, "equilibrate", equilibrate)
    refused = backend.find_liquidus_solidus(
        composition_kg={"SiO2": 50.0, "FeO": 16.0, "MgO": 10.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=400.0,
        max_T_C=1600.0,
        scan_step_C=50.0,
        tolerance_C=5.0,
    )
    assert refused.status == "out_of_domain"
    assert refused.solidus_T_C is None
    assert refused.liquidus_T_C is None
    assert refused.diagnostics["reason"] == INACTIVE_BUFFER_DECIDES_BOUNDARY
    assert (
        refused.diagnostics["backend_status_reason"]
        == INACTIVE_BUFFER_DECIDES_BOUNDARY
    )


def test_magemin_empty_melt_composition_marks_status_out_of_domain(monkeypatch):
    # A composition with no species in MAGEMin's 14-oxide basis (only
    # native Fe / sulfide / halide) collapses to an empty wt% projection.
    # The adapter labels this 'out_of_domain' -- the engine has nothing
    # valid to act on, not a runtime convergence failure.
    fake_module = types.SimpleNamespace(minimize=lambda **_: {"phases": {}})
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    result = backend.equilibrate(
        1600.0,
        composition_mol={"Fe": 1.0, "FeS": 0.5, "NaCl": 0.2},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert result.status == "out_of_domain"
    assert result.diagnostics["backend_status_reason"] == (
        OutOfDomainReason.FORBIDDEN_SPECIES.value
    )
    assert any("refused projected/dropped non-basis" in w for w in result.warnings)


def test_magemin_subprocess_runs_in_fresh_temp_cwd(monkeypatch, tmp_path):
    """MAGEMin appends _pseudosection_output.txt to its CWD; isolate per call."""
    captured: dict = {}

    class FakeCompleted:
        returncode = 0
        stderr = ""
        stdout = "Phase : liq\nMode  : 1.000\n"

    def fake_subprocess_run(args, **kwargs):
        captured["args"] = list(args)
        captured["cwd"] = kwargs.get("cwd")
        _plant_liq_matlab(kwargs["cwd"])
        return FakeCompleted()

    fake_binary = tmp_path / "MAGEMin"
    fake_binary.write_text("", encoding="utf-8")

    _disable_configured_magemin_path(monkeypatch)
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: fake_binary),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("subprocess", None),
    )
    import simulator.melt_backend.magemin as magemin_module
    monkeypatch.setattr(
        magemin_module.subprocess, "run", fake_subprocess_run
    )

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({"warm_worker": False}) is True

    result = backend.equilibrate(
        1200.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    # No FeOt and no O, and the stub stdout has no qfm row, so the
    # requested buffer is inactive. The call still ran in the temp cwd.
    assert result.status == "out_of_domain"
    assert result.diagnostics["backend_status_reason"] == "buffer_inactive"
    assert result.diagnostics["operating_point"] == "unbuffered"
    assert result.diagnostics["solved_fO2_log"] is None
    assert result.diagnostics["authoritative_for_requested_conditions"] is False
    assert result.diagnostics["authoritative_for_solved_conditions"] is False
    assert "cwd" in captured, "subprocess.run was not invoked"
    cwd = Path(captured["cwd"])
    assert cwd != fake_binary.parent.resolve()
    # Per-call TemporaryDirectory; path is under the system temp root.
    assert str(cwd).startswith(tempfile.gettempdir())
    assert cwd.name.startswith("tmp")
    binary_arg = Path(captured["args"][0])
    assert binary_arg.is_absolute()
    assert binary_arg == fake_binary.resolve()


def test_magemin_subprocess_launches_are_serialized_across_callers(
    monkeypatch, tmp_path
):
    """With REGOLITH_MAGEMIN_SLOTS=1 launches stay strictly serialized.

    2026-07-22 t-385: the machine-wide launch gate became a bounded K-slot
    semaphore (each call runs in a private tmpdir; the exclusive lock was a
    CPU bound, not correctness). K=1 must reproduce the old serialization
    exactly; the bounded-K contract is pinned by the companion test below.
    """
    import simulator.melt_backend.magemin as magemin_module

    monkeypatch.setenv('REGOLITH_MAGEMIN_SLOTS', '1')
    monkeypatch.setattr(
        magemin_module, '_MAGEMIN_SUBPROCESS_LOCK',
        tmp_path / 'isolated-magemin.lock',
    )
    monkeypatch.setattr(
        magemin_module, '_MAGEMIN_SUBPROCESS_LOCK_DIR', tmp_path,
    )

    first_entered = threading.Event()
    release_first = threading.Event()
    state_lock = threading.Lock()
    state = {'active': 0, 'calls': 0, 'max_active': 0}

    class FakeCompleted:
        returncode = 0
        stderr = ''
        stdout = 'Phase : liq\nMode  : 1.000\n'

    def fake_subprocess_run(_args, **_kwargs):
        _plant_liq_matlab(_kwargs["cwd"])
        with state_lock:
            state['active'] += 1
            state['calls'] += 1
            state['max_active'] = max(state['max_active'], state['active'])
            call_number = state['calls']
        try:
            if call_number == 1:
                first_entered.set()
                assert release_first.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
            return FakeCompleted()
        finally:
            with state_lock:
                state['active'] -= 1

    fake_binary = tmp_path / 'MAGEMin'
    fake_binary.write_text('', encoding='utf-8')
    monkeypatch.setattr(magemin_module.subprocess, 'run', fake_subprocess_run)

    backend = MAGEMinBackend()
    backend._binary_path = fake_binary
    backend._database = 'ig'
    backend._config = {'timeout_s': 1.0}
    projection = backend._build_db_bulk_projection(
        {'SiO2': 60.0, 'MgO': 40.0}, database='ig'
    )
    errors = []

    def solve():
        try:
            backend._call_magemin_subprocess(
                bulk_projection=projection,
                temperature_C=1200.0,
                pressure_kbar=0.001,
                fO2_log=-8.0,
            )
        except BaseException as exc:  # surfaced below from the test threads
            errors.append(exc)

    first = threading.Thread(target=solve)
    second = threading.Thread(target=solve)
    first.start()
    assert first_entered.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
    second.start()
    time.sleep(0.05)
    assert state['calls'] == 1
    release_first.set()
    first.join(timeout=_RENDEZVOUS_TIMEOUT_S)
    second.join(timeout=_RENDEZVOUS_TIMEOUT_S)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert state == {'active': 0, 'calls': 2, 'max_active': 1}


def test_magemin_subprocess_slots_bound_concurrency(monkeypatch, tmp_path):
    """K slots admit K concurrent launches; the K+1th blocks until release.

    Uses an ISOLATED lock namespace (tmp_path): the production slot files
    are contended by real engine calls when the suite runs the split
    chains, which broke the barrier under load (gate-1 flake).
    """
    import simulator.melt_backend.magemin as magemin_module

    monkeypatch.setenv('REGOLITH_MAGEMIN_SLOTS', '3')
    monkeypatch.setattr(
        magemin_module, '_MAGEMIN_SUBPROCESS_LOCK',
        tmp_path / 'isolated-magemin.lock',
    )
    monkeypatch.setattr(
        magemin_module, '_MAGEMIN_SUBPROCESS_LOCK_DIR', tmp_path,
    )
    held = []
    waited = []
    # 4 parties: the 3 slot holders + the main thread (a 3-party barrier
    # left main waiting on an unfillable second cycle — gate-1 self-bug).
    barrier = threading.Barrier(4, timeout=20.0)

    def hold(index):
        with magemin_module._magemin_subprocess_slot(5.0):
            held.append(index)
            barrier.wait()
            time.sleep(0.6)

    def fourth():
        started = time.monotonic()
        with magemin_module._magemin_subprocess_slot(5.0):
            waited.append(time.monotonic() - started)

    holders = [
        threading.Thread(target=hold, args=(index,)) for index in range(3)
    ]
    for thread in holders:
        thread.start()
    barrier.wait()
    late = threading.Thread(target=fourth)
    late.start()
    for thread in holders:
        thread.join(timeout=5.0)
    late.join(timeout=5.0)

    assert sorted(held) == [0, 1, 2]
    assert waited and waited[0] > 0.3


@pytest.mark.skipif(
    _SEAT_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_subprocess_does_not_append_dump_in_engine_tree():
    """Equilibrium calls must not grow _pseudosection_output.txt in-tree."""
    binary = _SEAT_MAGEMIN_BINARY.resolve()
    dump_path = binary.parent / "_pseudosection_output.txt"
    size_before = dump_path.stat().st_size if dump_path.exists() else 0

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({"binary_path": str(binary)})
    if not available:
        pytest.skip("MAGEMin binary present but backend failed to initialize")

    result = backend.equilibrate(
        1200.0,
        composition_kg={"SiO2": 49.0, "MgO": 9.0, "Al2O3": 14.0, "CaO": 11.0},
        fO2_log=-8.0,
        pressure_bar=2000.0,
    )
    # This bulk has no FeOt and no excess O, so qfm cannot stay active.
    # The binary still ran; the operating point must not claim that fO2.
    assert result.status == "out_of_domain", result.warnings
    assert result.diagnostics["backend_status_reason"] == "buffer_inactive"
    assert result.diagnostics["solved_fO2_log"] is None
    assert result.diagnostics["authoritative_for_solved_conditions"] is False
    assert result.diagnostics["authoritative_for_requested_conditions"] is False

    size_after = dump_path.stat().st_size if dump_path.exists() else 0
    assert size_after == size_before


def test_magemin_subprocess_fo2_log_substitution_recorded(monkeypatch):
    # MAGEMin's CLI only accepts a named buffer plus a numeric `buffer_n`
    # offset (see ``MAGEMin/examples/MAGEMin_C_single_point_with_buffer.jl``),
    # so the adapter must translate the caller's absolute log10(fO2) into
    # `--buffer=qfm --buffer_n=<delta>` using the O'Neill (1987) QFM
    # calibration. The previous "silently substitute qfm and ignore the
    # absolute value" path made a Mars reducing campaign at fO2_log=-12
    # land at QFM (~ -6 at 1450 C in the O'Neill fit) -- a multi-decade
    # error -- without any warning to the caller. This test pins the
    # honest translation: the binary receives the offset that reproduces
    # the requested absolute fO2, and the EquilibriumResult.warnings
    # surfaces the substitution so a downstream consumer cannot miss it.
    captured: dict = {}

    class FakeCompleted:
        returncode = 0
        stderr = ""
        stdout = (
            "Phase : liq qfm\n"
            "Mode  : 0.98000 0.02000\n"
        )

    def fake_subprocess_run(args, **kwargs):
        captured["args"] = list(args)
        _plant_liq_matlab(kwargs["cwd"])
        return FakeCompleted()

    # Force the subprocess bridge directly: stub the binary discovery so
    # initialize() picks up the subprocess path without needing a real
    # MAGEMin install.
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("subprocess", None),
    )
    import simulator.melt_backend.magemin as magemin_module
    monkeypatch.setattr(
        magemin_module.subprocess, "run", fake_subprocess_run
    )

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({"warm_worker": False}) is True
    assert backend._bridge == "subprocess"

    # Mars-reducing analog: T = 1450 C, fO2_log = -12 (well below QFM).
    result = backend.equilibrate(
        1450.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0, "FeO": 1.0},
        fO2_log=-12.0,
        pressure_bar=1e-6,
    )

    # The substitution result is OK (the subprocess ran), but the
    # warnings record the absolute -> buffer-offset translation in
    # detail so a downstream consumer cannot miss it.
    assert "args" in captured, "subprocess.run was not invoked"
    args = captured["args"]
    buffer_args = [a for a in args if a.startswith("--buffer")]
    # Both --buffer and --buffer_n must be passed so the absolute fO2
    # is honoured rather than silently snapped to QFM.
    assert any(a == "--buffer=qfm" for a in buffer_args), buffer_args
    buffer_n_args = [a for a in buffer_args if a.startswith("--buffer_n=")]
    assert len(buffer_n_args) == 1, buffer_args
    buffer_n = float(buffer_n_args[0].split("=", 1)[1])
    # O'Neill 1987: logfo2_QFM = 8.58 - 25050 / T_K. At T_C = 1450,
    # T_K = 1723.15, QFM ~ 8.58 - 14.537 = -5.957. So delta should be
    # ~-12 - (-5.957) = -6.043. Allow generous tolerance for the
    # calibration fit.
    expected_offset = -12.0 - (8.58 - 25050.0 / (1450.0 + 273.15))
    assert buffer_n == pytest.approx(expected_offset, abs=0.05), (
        f"buffer_n={buffer_n} should approximate {expected_offset}"
    )
    # Once the offset round-trips through QFM(T) we recover the
    # requested absolute fO2_log within calibration accuracy.
    recovered_fo2_log = buffer_n + (8.58 - 25050.0 / (1450.0 + 273.15))
    assert recovered_fo2_log == pytest.approx(-12.0, abs=0.05)
    # The warning chain must explicitly name the substitution so the
    # caller knows their absolute fO2 was translated, not ignored.
    substitution_warnings = [
        w for w in result.warnings if "fO2_log" in w and "qfm" in w
    ]
    assert substitution_warnings, result.warnings
    assert any("-12.0" in w for w in substitution_warnings), substitution_warnings
    assert result.diagnostics["fO2_buffer_active"] is True
    assert result.diagnostics["authoritative_for_solved_conditions"] is True


def test_magemin_inactive_qfm_does_not_claim_requested_fo2(monkeypatch):
    """A qfm solve whose stdout has no qfm row is unbuffered.

    The FeO bulk still sends FeOt intact. With the reservoir factor at
    0 the O slot stays 0, which is the path that deactivates qfm.
    """
    captured: dict = {}

    class FakeCompleted:
        returncode = 0
        stderr = ""
        stdout = "Phase : liq\nMode  : 1.000\n"

    def fake_subprocess_run(args, **kwargs):
        captured["args"] = list(args)
        _plant_liq_matlab(kwargs["cwd"])
        return FakeCompleted()

    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("subprocess", None),
    )
    monkeypatch.setattr(MAGEMinBackend, "_EXCESS_O_RESERVOIR_PER_FEOT", 0.0)
    import simulator.melt_backend.magemin as magemin_module
    monkeypatch.setattr(magemin_module.subprocess, "run", fake_subprocess_run)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({"warm_worker": False}) is True

    result = backend.equilibrate(
        1250.0,
        projected_oxide_wt_pct={"SiO2": 44.5, "FeO": 16.5, "MgO": 9.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    bulk_arg = next(arg for arg in captured["args"] if arg.startswith("--Bulk="))
    slots = [float(token) for token in bulk_arg.split("=", 1)[1].split(",")]
    order = MAGEMinBackend._IG_BULK_ORDER
    assert slots[order.index("FeOt")] == pytest.approx(16.5)
    assert slots[order.index("O")] == pytest.approx(0.0)
    assert result.status == "out_of_domain"
    assert result.diagnostics["operating_point"] == "unbuffered"
    assert result.diagnostics["backend_status_reason"] == "buffer_inactive"
    assert result.diagnostics["fO2_buffer_active"] is False
    assert result.diagnostics["solved_fO2_log"] is None
    assert result.diagnostics["authoritative_for_requested_conditions"] is False
    assert result.diagnostics["authoritative_for_solved_conditions"] is False
    assert result.diagnostics["applied_fO2_buffer"] == "qfm"


def test_magemin_subprocess_unknown_buffer_falls_back_with_warning(monkeypatch):
    # An unrecognised fO2_buffer config still drives the subprocess bridge,
    # but the adapter MUST surface the substitution as a warning rather
    # than silently swapping in 'qfm' (which would hide the requested fO2
    # mismatch from the caller). The previous _resolve_buffer routed the
    # warning into self._warnings, never reaching EquilibriumResult.
    captured: dict = {}

    class FakeCompleted:
        returncode = 0
        stderr = ""
        stdout = "Phase : liq\nMode  : 1.000\n"

    def fake_subprocess_run(args, **kwargs):
        captured["args"] = list(args)
        _plant_liq_matlab(kwargs["cwd"])
        return FakeCompleted()

    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("subprocess", None),
    )
    import simulator.melt_backend.magemin as magemin_module
    monkeypatch.setattr(
        magemin_module.subprocess, "run", fake_subprocess_run
    )

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({
            "fO2_buffer": "qfm-2",  # invalid: includes offset
            "warm_worker": False,
        })

    result = backend.equilibrate(
        1450.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0},
        fO2_log=-12.0,
        pressure_bar=1e-6,
    )

    assert any("qfm-2" in w and "qfm" in w for w in result.warnings), (
        result.warnings
    )


def test_magemin_configured_named_buffer_marks_requested_fo2_out_of_domain(
    monkeypatch,
):
    captured: dict = {}

    class FakeCompleted:
        returncode = 0
        stderr = ""
        stdout = "Phase : liq mw\nMode  : 1.000 0.000\n"

    def fake_subprocess_run(args, **kwargs):
        captured["args"] = list(args)
        _plant_liq_matlab(kwargs["cwd"])
        return FakeCompleted()

    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("subprocess", None),
    )
    import simulator.melt_backend.magemin as magemin_module
    monkeypatch.setattr(
        magemin_module.subprocess, "run", fake_subprocess_run
    )

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({"fO2_buffer": "mw", "warm_worker": False})

    result = backend.equilibrate(
        1450.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0},
        fO2_log=-12.0,
        pressure_bar=1e-6,
    )

    assert any(arg == "--buffer=mw" for arg in captured["args"])
    assert any(arg == "--buffer_n=0.000000" for arg in captured["args"])
    assert result.status == "out_of_domain"
    assert result.fO2_log == -12.0
    assert result.diagnostics["operating_point_clamped"] is True
    assert result.diagnostics["fO2_clamped"] is True
    assert result.diagnostics["requested_fO2_log"] == -12.0
    assert result.diagnostics["solved_fO2_log"] is None
    assert result.diagnostics["applied_fO2_buffer"] == "mw"
    assert result.diagnostics["authoritative_for_requested_conditions"] is False


def test_magemin_resolve_buffer_qfm_calibration_at_1450C():
    # Pin the O'Neill 1987 calibration math: at T = 1450 C (T_K = 1723.15),
    # logfo2_QFM should be ~-5.96. The conversion is load-bearing for the
    # A5 honest-substitution path; a wrong constant would silently shift
    # every Mars reducing fO2 by ~6 decades.
    qfm_at_1450C = MAGEMinBackend._qfm_logfo2_oneill(1450.0)
    expected = 8.58 - 25050.0 / (1450.0 + 273.15)
    assert qfm_at_1450C == pytest.approx(expected, abs=1e-9)
    assert -7.0 < qfm_at_1450C < -5.0, qfm_at_1450C
