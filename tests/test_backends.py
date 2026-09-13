import pytest

from simulator.backends import (
    INELIGIBLE_ACTIVE_BACKENDS,
    REAL_MELT_BACKEND_NAMES,
    BackendSelectionPolicy,
    BackendUnavailableError,
    backend_resolution_status,
    resolve_backend,
)
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.melt_backend.base import InternalAnalyticalBackend


def test_imcc_sf04_is_ineligible_active_not_a_real_melt_backend():
    assert "imcc-sf04" in INELIGIBLE_ACTIVE_BACKENDS
    assert "imcc-sf04-ext" in INELIGIBLE_ACTIVE_BACKENDS
    assert "imcc-sf04" not in REAL_MELT_BACKEND_NAMES
    assert "imcc-sf04-ext" not in REAL_MELT_BACKEND_NAMES
    with pytest.raises(
        BackendUnavailableError,
        match="pending battery qualification",
    ):
        resolve_backend("imcc-sf04", BackendSelectionPolicy.WEB_AUTODETECT)


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


def test_real_backend_out_of_domain_is_not_typed_backend_unavailable():
    """A present backend that cannot solve THIS composition is not an outage.

    assert_real_backend_feedstock_supported fires when the melt backend is
    installed and answering but the feedstock has no MELTS oxide basis. Reported
    as 'backend_unavailable' it sends the operator to reinstall a working engine;
    the real remedies are a different backend or a different feedstock.

    Both ids below are web-visible and unblocked, so this is reachable from the UI.
    The optimizer already treats the same condition as out-of-domain rather than a
    backend abort; this pins the session/web path to the same rule.
    """
    import pathlib as _pathlib

    import yaml

    from simulator.backends import assert_real_backend_feedstock_supported

    feedstocks = yaml.safe_load(
        (_pathlib.Path(__file__).resolve().parents[1] / "data" / "feedstocks.yaml").read_text()
    )
    feedstocks = feedstocks.get("feedstocks", feedstocks)

    for feedstock_id in ("m_type_metallic_phase", "targeted_super_kreep_ore"):
        with pytest.raises(Exception) as excinfo:
            assert_real_backend_feedstock_supported(
                "alphamelts", feedstock_id, feedstocks
            )
        assert getattr(excinfo.value, "reason_code", None) == (
            "real_backend_out_of_domain"
        ), (
            f"{feedstock_id} is out of domain for a PRESENT backend; got "
            f"reason_code={getattr(excinfo.value, 'reason_code', None)!r}"
        )

    # Negative control: an in-domain feedstock must not refuse at all, or the
    # assertion above would pass for the wrong reason.
    assert_real_backend_feedstock_supported(
        "alphamelts", "lunar_mare_low_ti", feedstocks
    )


def test_cached_real_config_errors_are_input_not_backend_unavailable():
    """A misconfigured cache is not a missing engine.

    normalize_cached_real_config raises the injected unavailable_error_cls for
    config mistakes (no db_path, bad miss_policy, malformed quantization). That
    class is correct -- callers catch it -- but resolve_backend first runs
    _unavailable_error_cls_with_reason, which stamps 'backend_unavailable' in
    __init__ regardless of what failed. So "you did not pass a cache path" was
    reported as a dead backend.

    The wrapped arm is the one that matters: it is the class resolve_backend
    actually hands the validators, and the one that was forcing the label.
    """
    from simulator.backends import (
        BackendUnavailableError,
        _unavailable_error_cls_with_reason,
        normalize_cached_real_config,
    )

    # ARMS MUST DIFFER. _unavailable_error_cls_with_reason SHORT-CIRCUITS when the
    # class already carries reason_code == 'backend_unavailable', which
    # BackendUnavailableError does -- so wrapping it returns the SAME class and a
    # (raw, wrapped) pair built from it is one arm run twice. An earlier version of
    # this test did exactly that and proved nothing about the wrapped path. Use a
    # class with no reason_code, as session_cli's RunnerError and
    # mre_reproduction's MREReproductionError are, to get a real wrap.
    class _InjectedError(RuntimeError):
        pass

    wrapped = _unavailable_error_cls_with_reason(_InjectedError)
    assert wrapped is not _InjectedError, (
        "wrapper short-circuited; this arm would not exercise the __init__ stamp"
    )
    assert getattr(wrapped("probe"), "reason_code", None) == "backend_unavailable", (
        "the wrapped class must stamp backend_unavailable in __init__, or there is "
        "nothing for the fix to override and the test below is vacuous"
    )

    for error_cls, arm in ((BackendUnavailableError, "raw"), (wrapped, "wrapped")):
        with pytest.raises(Exception) as excinfo:
            normalize_cached_real_config(None, unavailable_error_cls=error_cls)
        assert getattr(excinfo.value, "reason_code", None) == "invalid_run_input", (
            f"{arm} arm: a missing cache config is bad input, not an outage; got "
            f"{getattr(excinfo.value, 'reason_code', None)!r}"
        )

    # Negative control 1: a genuine availability error keeps its label, or the
    # assertions above would pass by having broken the stamp for everything.
    outage = wrapped("AlphaMELTS unavailable; run install-dependencies.py")
    assert getattr(outage, "reason_code", None) == "backend_unavailable"

    # Negative control 2: a well-formed config must not raise at all.
    import pathlib as _pathlib
    import tempfile

    db_path = _pathlib.Path(tempfile.mkdtemp()) / "cache.sqlite"
    db_path.write_text("")
    normalize_cached_real_config(
        {
            "db_path": str(db_path),
            "miss_policy": "fail-loud",
            "authorized_backend_name": "alphamelts",
            "authorized_backend_version": "1",
        },
        unavailable_error_cls=wrapped,
    )
