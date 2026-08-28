from __future__ import annotations

import pytest

from engines.alphamelts.thermoengine import (
    ThermoEngineFO2OmittedError,
    ThermoEngineFO2UndefinedError,
    ThermoEngineIsolationError,
    ThermoEngineNonFiniteField,
    ThermoEngineOutOfDomainError,
    ThermoEngineRefusalCause,
    ThermoEngineTimeoutCause,
    ThermoEngineTimeoutError,
    thermoengine_failure_disposition_from_exception,
)
from simulator.melt_backend.liquidus import (
    liquidus_sample_error_from_exception,
)
from simulator.melt_backend.thermoengine import ThermoEngineBackend


def _attribute_bearing_runtime_error() -> RuntimeError:
    exc = RuntimeError('solver iteration failed')
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
        ThermoEngineTimeoutError(
            ThermoEngineTimeoutCause.WARM_CALL_EQUILIBRIUM_TIMEOUT,
            timeout_s=1.0,
        ),
        RuntimeError('solver iteration failed'),
        _attribute_bearing_runtime_error(),
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
            RuntimeError('solver iteration failed'),
            _attribute_bearing_runtime_error(),
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
        _attribute_bearing_runtime_error()
    )

    assert policy_refusal is not None
    assert policy_refusal.status == 'refused'
    assert failure is not None
    assert failure.status == 'not_converged'
    assert failure.status != 'refused'
    assert failure.diagnostics['backend_status_reason'] == 'not_converged'


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
        (RuntimeError('solver iteration failed'), 'not_converged'),
        (_attribute_bearing_runtime_error(), 'not_converged'),
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

    absence = ModuleNotFoundError("No module named 'thermoengine'")
    current_exception[0] = absence
    backend = ThermoEngineBackend()
    with pytest.raises(ImportError) as raised_absence:
        backend.initialize({})
    assert raised_absence.value.__cause__ is absence
    assert 'transport unavailable' in str(raised_absence.value)
