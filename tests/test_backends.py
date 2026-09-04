import pytest

from simulator.backends import (
    BackendSelectionPolicy,
    BackendUnavailableError,
    backend_resolution_status,
    resolve_backend,
)
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.melt_backend.base import InternalAnalyticalBackend


def test_backend_honesty_internal_analytical_resolution_surfaces_unavailable_status():
    backend = resolve_backend("internal-analytical", BackendSelectionPolicy.RUNNER_STRICT)

    status = backend_resolution_status(backend)

    assert isinstance(backend, InternalAnalyticalBackend)
    assert status.backend_status == "unavailable"
    assert status.authoritative is False
    assert backend.backend_status == "unavailable"
    assert backend.backend_authoritative is False


def test_backend_honesty_internal_analytical_rejected_for_real_liquid_fraction_intent():
    with pytest.raises(
        BackendUnavailableError,
        match="gate_liquid_fraction",
    ):
        resolve_backend(
            "internal-analytical",
            BackendSelectionPolicy.RUNNER_STRICT,
            required_intents=[ChemistryIntent.GATE_LIQUID_FRACTION],
        )


def test_never_installed_thermoengine_resolve_is_backend_unavailable(monkeypatch):
    """Genuine missing ThermoEngine must surface as BackendUnavailableError.

    Invert: restore _try_backend without catching ImportError and this
    raises ImportError instead of BackendUnavailableError. Forces the
    real resolve_backend / initialize path, not a FakeExecutor.
    """
    from simulator.melt_backend.thermoengine import ThermoEngineBackend

    def fail_init(self, config):
        del config
        raise ImportError("No module named 'thermoengine'")

    monkeypatch.setattr(ThermoEngineBackend, "initialize", fail_init)
    with pytest.raises(BackendUnavailableError, match="ThermoEngine unavailable"):
        resolve_backend("thermoengine", BackendSelectionPolicy.RUNNER_STRICT)


def test_backend_honesty_internal_analytical_equilibrate_does_not_claim_liquid_fraction():
    result = InternalAnalyticalBackend().equilibrate(temperature_C=1500.0)

    assert result.status == "unavailable"
    assert result.liquid_fraction is None
    assert result.phase_assemblage_available is False


def test_bare_import_error_for_missing_native_dylibs_is_absence():
    """Absence detection must not be narrowed to ModuleNotFoundError.

    engine_local_config raises a BARE ImportError when the ThermoEngine dylibs
    are absent -- no Python module is missing, so ModuleNotFoundError cannot
    express it. A reviewer narrowing _TYPED_ABSENCE_EXCEPTION_CLASSES to
    ModuleNotFoundError (a natural-looking tightening) would turn "install the
    engine" into "engine bug, aborting" on any fresh box. This pins the reason.
    """
    from simulator.optimize.evaluate import _is_backend_unavailable

    dylibs_absent = ImportError(
        "ThermoEngine dylibs not found: configure [paths].thermoengine_dylib_dir "
        "or run install-engines.py"
    )
    assert not isinstance(dylibs_absent, ModuleNotFoundError)
    assert _is_backend_unavailable(dylibs_absent) is True


def test_absence_is_detected_through_a_cause_chain():
    """Anti-vacuity: the walk, not just a top-level isinstance, is what is pinned."""
    from simulator.optimize.evaluate import _is_backend_unavailable

    inner = ImportError("MELTSdynamic loader not found")
    outer = RuntimeError("stage failed")
    outer.__cause__ = inner
    assert _is_backend_unavailable(outer) is True


def test_unrelated_failure_is_not_absence():
    """The breadth stops at ImportError; ordinary failures stay non-absence."""
    from simulator.optimize.evaluate import _is_backend_unavailable

    assert _is_backend_unavailable(RuntimeError("solver did not converge")) is False
    assert _is_backend_unavailable(ValueError("bad input")) is False
