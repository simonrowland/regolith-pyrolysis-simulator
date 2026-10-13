from __future__ import annotations

import math

import numpy as np
import pytest

from simulator.fe_redox import (
    FE_FEO_BUFFER_STANDARD,
    REDOX_STATE_REFERENCE_T_K,
    RedoxState,
    RedoxStateInputError,
    feo_iw_log10_fO2_bar,
    normalize_redox_state,
)


def test_redox_state_equality_and_hash_use_normalized_values() -> None:
    integer = RedoxState.absolute(-9)
    floating = RedoxState.absolute(-9.0)

    assert integer == floating
    assert hash(integer) == hash(floating)
    assert integer.log_fO2 == -9.0
    with pytest.raises((AttributeError, TypeError)):
        integer.log_fO2 = -8.0


@pytest.mark.parametrize('value', [True, False, None, object(), 'not numeric'])
def test_absolute_rejects_non_numeric_or_boolean_values(value: object) -> None:
    with pytest.raises(TypeError):
        RedoxState.absolute(value)


@pytest.mark.parametrize('value', [math.nan, math.inf, -math.inf])
def test_absolute_rejects_non_finite_values(value: float) -> None:
    with pytest.raises(ValueError):
        RedoxState.absolute(value)


@pytest.mark.parametrize('temperature_K', [True, False, 'bad', None])
def test_absolute_temperature_requires_numeric_kelvin(temperature_K: object) -> None:
    with pytest.raises(TypeError):
        RedoxState.absolute(-9.0).log_fO2_at(temperature_K)


@pytest.mark.parametrize(
    'temperature_K',
    [math.nan, math.inf, -math.inf, 0.0, -1.0],
)
def test_absolute_temperature_requires_finite_positive_kelvin(
    temperature_K: float,
) -> None:
    with pytest.raises(ValueError):
        RedoxState.absolute(-9.0).log_fO2_at(temperature_K)


@pytest.mark.parametrize('delta', [True, False, None, object(), 'not numeric'])
def test_buffer_offset_rejects_non_numeric_or_boolean_delta(delta: object) -> None:
    with pytest.raises(TypeError):
        RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, delta)


@pytest.mark.parametrize('delta', [math.nan, math.inf, -math.inf])
def test_buffer_offset_rejects_non_finite_delta(delta: float) -> None:
    with pytest.raises(ValueError):
        RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, delta)


@pytest.mark.parametrize(
    'value',
    [
        pytest.param(np.bool_(True), id='numpy-bool-true'),
        pytest.param(np.bool_(False), id='numpy-bool-false'),
        pytest.param(np.array(True), id='zero-dimensional-bool-array'),
        pytest.param(np.array([True]), id='one-element-bool-array'),
        pytest.param(np.array([-9.0]), id='one-element-float-array'),
    ],
)
def test_redox_state_constructors_reject_numpy_bool_and_array_scalars(
    value: object,
) -> None:
    with pytest.raises(TypeError):
        RedoxState.absolute(value)
    with pytest.raises(TypeError):
        RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, value)


@pytest.mark.parametrize('value', [np.float64(-9.0), '-9.0'])
def test_redox_state_constructors_keep_compatible_numeric_scalars(
    value: object,
) -> None:
    assert RedoxState.absolute(value).log_fO2 == -9.0
    assert RedoxState.buffer_offset(
        FE_FEO_BUFFER_STANDARD,
        value,
    ).delta_log10 == -9.0


def test_buffer_offset_rejects_unknown_line_and_inconsistent_fields() -> None:
    with pytest.raises(ValueError, match='unregistered'):
        RedoxState.buffer_offset('unknown-line', 0.25)
    with pytest.raises(ValueError, match='requires buffer and delta_log10 only'):
        RedoxState(
            kind='buffer_offset',
            log_fO2=-9.0,
            buffer=FE_FEO_BUFFER_STANDARD,
            delta_log10=0.25,
        )
    with pytest.raises(ValueError, match='unsupported redox-state kind'):
        RedoxState(kind='other')


def test_registered_wustite_liquid_line_uses_production_buffer_function() -> None:
    temperature_K = 1873.15
    offset = -1.25
    state = RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, offset)

    expected = feo_iw_log10_fO2_bar(temperature_K, a_feo=1.0) + offset
    assert state.log_fO2_at(temperature_K) == expected
    assert state.to_descriptor() == {
        'kind': 'buffer_offset',
        'buffer': FE_FEO_BUFFER_STANDARD,
        'delta_log10': offset,
    }


def test_absolute_descriptor_contains_only_canonical_fields() -> None:
    assert RedoxState.absolute(-9).to_descriptor() == {
        'kind': 'absolute',
        'log_fO2': -9.0,
    }


def test_normalizer_preserves_two_absent_inputs() -> None:
    assert normalize_redox_state(None, None) == (None, None)


def test_normalizer_builds_absolute_from_finite_scalar() -> None:
    scalar, state = normalize_redox_state(-9)

    assert scalar == -9.0
    assert state == RedoxState.absolute(-9.0)


def test_normalizer_fills_scalar_from_state_reference_value() -> None:
    state = RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, -0.5)

    scalar, normalized = normalize_redox_state(None, state)

    assert scalar == state.log_fO2_at(REDOX_STATE_REFERENCE_T_K)
    assert normalized is state


def test_normalizer_checks_scalar_and_state_agreement() -> None:
    state = RedoxState.absolute(-9.0)

    assert normalize_redox_state(-9.0 + 5.0e-10, state) == (
        -9.0 + 5.0e-10,
        state,
    )
    with pytest.raises(RedoxStateInputError, match='disagrees'):
        normalize_redox_state(-8.999, state)


def test_normalizer_checks_buffer_offset_at_reference_temperature() -> None:
    state = RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, -0.5)
    reference_value = state.log_fO2_at(REDOX_STATE_REFERENCE_T_K)

    assert normalize_redox_state(reference_value, state) == (
        reference_value,
        state,
    )
    with pytest.raises(RedoxStateInputError, match='disagrees'):
        normalize_redox_state(reference_value + 0.01, state)


def test_reanchor_redox_state_moves_reference_value_without_changing_line_shape() -> None:
    from simulator.fe_redox import _reanchor_redox_state

    state = RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, 0.2)
    new_reference = state.log_fO2_at(REDOX_STATE_REFERENCE_T_K) + 0.05
    target_T_K = 1900.0

    reanchored = _reanchor_redox_state(state, new_reference)

    assert reanchored.log_fO2_at(REDOX_STATE_REFERENCE_T_K) == pytest.approx(
        new_reference
    )
    assert reanchored.log_fO2_at(target_T_K) == pytest.approx(
        state.log_fO2_at(target_T_K) + 0.05
    )
