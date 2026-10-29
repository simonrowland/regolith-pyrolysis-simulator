from __future__ import annotations

import pytest

from engines.alphamelts.petthermo import _PetThermoToolsExternalCallFailure
from engines.alphamelts.thermoengine import (
    ThermoEngineFO2OmittedError,
    ThermoEngineFO2UndefinedError,
    ThermoEngineIsolationError,
    ThermoEngineNonFiniteField,
    ThermoEngineNotConvergedError,
    ThermoEngineOutOfDomainError,
    ThermoEnginePayload,
    ThermoEngineRefusalCause,
    ThermoEngineTimeoutCause,
    ThermoEngineTimeoutError,
    thermoengine_failure_disposition_from_exception,
)
from simulator.melt_backend.liquidus import (
    liquidus_sample_error_from_exception,
)
from simulator.melt_backend.thermoengine import ThermoEngineBackend
from simulator.melt_backend import thermoengine as thermoengine_module


def _attribute_bearing_runtime_error() -> RuntimeError:
    exc = RuntimeError('solver iteration failed')
    exc.backend_failure_category = 'refused'  # type: ignore[attr-defined]
    exc.backend_status_reason = 'forged_refusal'  # type: ignore[attr-defined]
    exc.backend_failure_reason_code = 'forged_refusal'  # type: ignore[attr-defined]
    return exc


def _tagged_solver_failure() -> _PetThermoToolsExternalCallFailure:
    return _PetThermoToolsExternalCallFailure(
        'RuntimeError',
        'solver iteration failed',
    )


def _attribute_bearing_tagged_solver_failure() -> _PetThermoToolsExternalCallFailure:
    exc = _tagged_solver_failure()
    exc.backend_failure_category = 'refused'  # type: ignore[attr-defined]
    exc.backend_status_reason = 'forged_refusal'  # type: ignore[attr-defined]
    exc.backend_failure_reason_code = 'forged_refusal'  # type: ignore[attr-defined]
    return exc


def test_thermoengine_failure_disposition_pins_both_directions() -> None:
    policy_refusal = ThermoEngineIsolationError('isolated worker required')
    missing_input = ThermoEngineOutOfDomainError(
        ThermoEngineRefusalCause.FO2_REQUIRES_IRON
    )
    out_of_domain = ThermoEngineOutOfDomainError(
        ThermoEngineRefusalCause.FO2_OUTSIDE_ATTAINABLE_BRACKET
    )
    engine_ran_failures = (
        ThermoEngineFO2UndefinedError('finite echo undefined'),
        ThermoEngineNonFiniteField('Liquid GibbsFreeEnergy is nan'),
        ThermoEngineFO2OmittedError('solved fO2 omitted'),
        ThermoEngineNotConvergedError('ThermoEngine equilibrium status: failed'),
        ThermoEngineTimeoutError(
            ThermoEngineTimeoutCause.WARM_CALL_EQUILIBRIUM_TIMEOUT,
            timeout_s=1.0,
        ),
        _tagged_solver_failure(),
        _attribute_bearing_tagged_solver_failure(),
    )

    assert thermoengine_failure_disposition_from_exception(
        policy_refusal
    ).status == 'refused'
    assert thermoengine_failure_disposition_from_exception(
        missing_input
    ).status == 'refused'
    assert thermoengine_failure_disposition_from_exception(
        out_of_domain
    ).status == 'out_of_domain'
    for exc in engine_ran_failures:
        disposition = thermoengine_failure_disposition_from_exception(exc)
        assert disposition.status == 'not_converged', type(exc).__name__
        assert disposition.status not in {'refused', 'out_of_domain'}


def test_unclassified_exception_propagates() -> None:
    with pytest.raises(KeyError, match='review-local'):
        thermoengine_failure_disposition_from_exception(
            KeyError('review-local')
        )
    with pytest.raises(KeyError, match='review-local'):
        liquidus_sample_error_from_exception(KeyError('review-local'))
    with pytest.raises(RuntimeError, match='review-local-runtime'):
        thermoengine_failure_disposition_from_exception(
            RuntimeError('review-local-runtime')
        )
    with pytest.raises(RuntimeError, match='review-local-runtime'):
        liquidus_sample_error_from_exception(
            RuntimeError('review-local-runtime')
        )
    with pytest.raises(RuntimeError, match='solver iteration failed'):
        thermoengine_failure_disposition_from_exception(
            _attribute_bearing_runtime_error()
        )


def test_liquidus_projection_pins_policy_and_execution_failures() -> None:
    refused = liquidus_sample_error_from_exception(
        ThermoEngineIsolationError('isolated worker required')
    )
    out_of_domain = liquidus_sample_error_from_exception(
        ThermoEngineOutOfDomainError(
            ThermoEngineRefusalCause.FO2_OUTSIDE_ATTAINABLE_BRACKET
        )
    )
    execution_failures = tuple(
        liquidus_sample_error_from_exception(exc)
        for exc in (
            ThermoEngineFO2UndefinedError('finite echo undefined'),
            ThermoEngineNonFiniteField('Liquid GibbsFreeEnergy is nan'),
            ThermoEngineFO2OmittedError('solved fO2 omitted'),
            ThermoEngineNotConvergedError(
                'ThermoEngine equilibrium status: failed'
            ),
            _tagged_solver_failure(),
            _attribute_bearing_tagged_solver_failure(),
        )
    )

    assert refused is not None and refused.status == 'refused'
    assert out_of_domain is not None and out_of_domain.status == 'out_of_domain'
    for failure in execution_failures:
        assert failure is not None
        assert failure.status == 'not_converged'
        assert failure.diagnostics['backend_failure_category'] == 'not_converged'
    forged = execution_failures[-1]
    assert forged is not None
    assert forged.diagnostics['backend_status_reason'] == 'not_converged'


def test_liquidus_ignores_refusal_attributes_on_generic_exception() -> None:
    policy_refusal = liquidus_sample_error_from_exception(
        ThermoEngineIsolationError('isolated worker required')
    )
    failure = liquidus_sample_error_from_exception(
        _attribute_bearing_tagged_solver_failure()
    )

    assert policy_refusal is not None
    assert policy_refusal.status == 'refused'
    assert failure is not None
    assert failure.status == 'not_converged'
    assert failure.status != 'refused'
    assert failure.diagnostics['backend_status_reason'] == 'not_converged'
    with pytest.raises(RuntimeError, match='solver iteration failed'):
        liquidus_sample_error_from_exception(
            _attribute_bearing_runtime_error()
        )


def test_initialize_preserves_execution_failure_and_real_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_exception: list[BaseException] = [RuntimeError('unset')]

    class FakeThermoEngineTransport:
        engine_version = 'thermoengine fake'

        def __init__(self, **_kwargs: object) -> None:
            self.close_calls = 0

        def initialize(self) -> None:
            raise current_exception[0]

        def close(self) -> None:
            self.close_calls += 1

    monkeypatch.setattr(
        'simulator.melt_backend.thermoengine.ThermoEngineTransport',
        FakeThermoEngineTransport,
    )
    monkeypatch.setattr(
        ThermoEngineBackend,
        '_initialize_vaporock_delegate',
        lambda _self: None,
    )

    classified_failures = (
        (
            ThermoEngineIsolationError('isolated worker required'),
            'refused',
        ),
        (
            ThermoEngineOutOfDomainError(
                ThermoEngineRefusalCause.FO2_OUTSIDE_ATTAINABLE_BRACKET
            ),
            'out_of_domain',
        ),
        (ThermoEngineFO2UndefinedError('finite echo undefined'), 'not_converged'),
        (
            ThermoEngineNonFiniteField('Liquid GibbsFreeEnergy is nan'),
            'not_converged',
        ),
        (ThermoEngineFO2OmittedError('solved fO2 omitted'), 'not_converged'),
        (_tagged_solver_failure(), 'not_converged'),
        (_attribute_bearing_tagged_solver_failure(), 'not_converged'),
    )
    for exc, expected_status in classified_failures:
        current_exception[0] = exc
        backend = ThermoEngineBackend()
        with pytest.raises(type(exc)) as raised:
            backend.initialize({})
        assert raised.value is exc
        assert not isinstance(raised.value, ImportError)
        assert (
            thermoengine_failure_disposition_from_exception(raised.value).status
            == expected_status
        )

    local_runtime = RuntimeError('review-local-runtime')
    current_exception[0] = local_runtime
    backend = ThermoEngineBackend()
    with pytest.raises(RuntimeError, match='review-local-runtime') as raised_local:
        backend.initialize({})
    assert raised_local.value is local_runtime
    with pytest.raises(RuntimeError, match='review-local-runtime'):
        thermoengine_failure_disposition_from_exception(raised_local.value)

    absence = ModuleNotFoundError("No module named 'thermoengine'")
    current_exception[0] = absence
    backend = ThermoEngineBackend()
    with pytest.raises(ImportError) as raised_absence:
        backend.initialize({})
    assert raised_absence.value.__cause__ is absence
    assert 'transport unavailable' in str(raised_absence.value)


def test_initialize_propagates_local_model_resolution_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ThermoEngineBackend,
        '_initialize_vaporock_delegate',
        lambda _self: None,
    )

    def broken_model_resolver(*_args: object) -> str:
        raise KeyError('local model resolution defect')

    monkeypatch.setattr(
        thermoengine_module, 'resolve_grid_engine_model', broken_model_resolver
    )
    with pytest.raises(KeyError, match='local model resolution defect'):
        ThermoEngineBackend().initialize({})


def test_initialize_health_return_preserves_solver_failure_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = 'solver iteration failed'

    class FakeThermoEngineTransport:
        engine_version = 'thermoengine fake'

        def __init__(self, **_kwargs: object) -> None:
            pass

        def initialize(self) -> None:
            return None

        def health_check(
            self, timeout_s: float | None = None
        ) -> tuple[bool, str]:
            return False, reason

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        'simulator.melt_backend.thermoengine.ThermoEngineTransport',
        FakeThermoEngineTransport,
    )
    monkeypatch.setattr(
        ThermoEngineBackend,
        '_initialize_vaporock_delegate',
        lambda _self: None,
    )

    with pytest.raises(RuntimeError) as raised:
        ThermoEngineBackend().initialize({})

    assert str(raised.value) == reason
    with pytest.raises(RuntimeError, match=reason):
        thermoengine_failure_disposition_from_exception(raised.value)


def test_thermoengine_commissioning_notice_does_not_retype_to_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Out of the certified T band ThermoEngine still runs (predict-and-flag)."""
    backend = ThermoEngineBackend()
    backend._mode = 'thermoengine'
    backend._engine_version = 'test'
    backend._vaporock_available = False
    called: list[dict] = []

    class FakeTransport:
        def equilibrate(self, **kwargs):
            called.append(kwargs)
            return ThermoEnginePayload(
                phases_present=('liquid',),
                phase_masses_kg={'liquid': 1.0},
                liquid_fraction=1.0,
                liquid_composition_wt_pct=dict(kwargs['comp_wt']),
                activity_coefficients={'SiO2': 0.4},
                solved_fO2_log=kwargs['fO2_log'],
            )

        def close(self):
            return None

    backend._thermoengine_transport = FakeTransport()
    monkeypatch.setattr(
        backend,
        '_activities_times_antoine_or_fail',
        lambda *args, **kwargs: {},
    )
    result = backend.equilibrate(
        temperature_C=2200.0,
        composition_kg={'SiO2': 50.0, 'Al2O3': 15.0, 'MgO': 35.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
    )
    assert called
    assert result.status == 'ok'
    assert result.diagnostics['authority'] == 'extrapolated'
    assert result.diagnostics['commissioning_notice']['reason'] == (
        'temperature_range'
    )


def test_thermoengine_backend_preserves_native_environment_refusal() -> None:
    failure = PermissionError('ThermoEngine native library unavailable')

    class FailingTransport:
        closed = False

        def equilibrate(self, **_kwargs: object) -> ThermoEnginePayload:
            raise failure

        def close(self) -> None:
            self.closed = True

    backend = ThermoEngineBackend()
    backend._mode = 'thermoengine'
    transport = FailingTransport()
    backend._thermoengine_transport = transport

    with pytest.raises(PermissionError) as raised:
        backend._equilibrate_thermoengine(
            1400.0,
            {'SiO2': 100.0},
            -9.0,
            1.0,
            warnings=[],
        )

    assert raised.value is failure
    assert raised.value.terminal_refusal is True
    assert raised.value.reason_code == 'engine_compute_timeout'
    assert raised.value.timeout_kind == 'PermissionError'
    assert transport.closed is True


def _fo2_echo_transport(payload: ThermoEnginePayload):
    class FakeTransport:
        def equilibrate(self, **kwargs):
            return payload

        def close(self):
            return None

    return FakeTransport()


def test_gas_only_finished_assemblage_is_not_an_fo2_omission_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finished Fe-free solve that omits intrinsic fO2 still runs.

    The commissioning fixture echoes the melt fO2 argument. Gas-only
    passes None, so that echo has no typed undefined reason. That must
    stay a gas coordinate, not ThermoEngineFO2OmittedError. An empty
    assemblage and a Fe-bearing omitted echo stay refusals.
    """
    finished = ThermoEnginePayload(
        phases_present=('liquid',),
        phase_masses_kg={'liquid': 1.0},
        liquid_fraction=1.0,
        liquid_composition_wt_pct={'SiO2': 50.0, 'Al2O3': 15.0, 'MgO': 35.0},
        activity_coefficients={'SiO2': 0.4},
        solved_fO2_log=None,
        solved_fO2_reason=None,
    )
    backend = ThermoEngineBackend()
    backend._mode = 'thermoengine'
    backend._engine_version = 'test'
    backend._vaporock_available = False
    backend._thermoengine_transport = _fo2_echo_transport(finished)
    monkeypatch.setattr(
        backend,
        '_activities_times_antoine_or_fail',
        lambda *args, **kwargs: {},
    )

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg={'SiO2': 50.0, 'Al2O3': 15.0, 'MgO': 35.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert result.status == 'ok'
    assert result.fO2_log is None
    assert result.requested_fO2_log == pytest.approx(-9.0)
    assert result.diagnostics['fO2_transport'] == 'external_on_gas_only'

    empty = ThermoEngineBackend()
    empty._mode = 'thermoengine'
    empty._engine_version = 'test'
    empty._vaporock_available = False
    empty._thermoengine_transport = _fo2_echo_transport(ThermoEnginePayload())
    with pytest.raises(ThermoEngineFO2OmittedError, match='omitted solved fO2'):
        empty._equilibrate_thermoengine(
            1400.0,
            {'SiO2': 50.0, 'Al2O3': 15.0, 'MgO': 35.0},
            -9.0,
            1.0,
            gas_only_fo2_log=-9.0,
        )

    fe_bearing = ThermoEngineBackend()
    fe_bearing._mode = 'thermoengine'
    fe_bearing._engine_version = 'test'
    fe_bearing._vaporock_available = False
    fe_bearing._thermoengine_transport = _fo2_echo_transport(finished)
    with pytest.raises(ThermoEngineFO2OmittedError, match='omitted solved fO2'):
        fe_bearing.equilibrate(
            temperature_C=1400.0,
            composition_kg={'SiO2': 50.0, 'FeO': 20.0, 'MgO': 30.0},
            fO2_log=-9.0,
            pressure_bar=1.0,
        )


def test_thermoengine_solves_intrinsic_fo2_without_an_input_or_redox_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ThermoEngineBackend()
    backend._mode = 'thermoengine'
    backend._engine_version = 'test'
    backend._vaporock_available = False
    calls: list[dict] = []

    class FakeTransport:
        def equilibrate(self, **kwargs):
            calls.append(kwargs)
            return ThermoEnginePayload(
                phases_present=('liquid',),
                phase_masses_kg={'liquid': 1.0},
                liquid_fraction=1.0,
                liquid_composition_wt_pct=dict(kwargs['comp_wt']),
                activity_coefficients={'SiO2': 0.4},
                solved_fO2_log=-8.25,
            )

    backend._thermoengine_transport = FakeTransport()
    monkeypatch.setattr(
        backend,
        '_activities_times_antoine_or_fail',
        lambda *args, **kwargs: {},
    )

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg={'FeO': 20.0, 'SiO2': 80.0},
        fO2_log=None,
        pressure_bar=1.0,
    )

    assert len(calls) == 1
    assert calls[0]['fO2_log'] is None
    assert result.fO2_log == pytest.approx(-8.25)
    assert result.diagnostics['fO2_transport'] == 'thermoengine_intrinsic_closed'
