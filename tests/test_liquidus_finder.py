"""Engine-independent tests for the liquidus/solidus bisection helper."""

from __future__ import annotations

import pytest

import simulator.melt_backend.liquidus as liquidus_module
from engines.alphamelts.thermoengine import (
    ThermoEngineFO2OmittedError,
    ThermoEngineFO2UndefinedError,
    ThermoEngineIsolationError,
    ThermoEngineNonFiniteField,
)
from simulator.melt_backend.liquidus import (
    LiquidusSampleError,
    LiquidusSolidusResult,
    MeltFractionSample,
    build_equilibrium_crystallization_path,
    find_liquidus_solidus_by_fraction,
)


def _piecewise_fraction(anchors: dict[float, float]):
    ordered = sorted((float(T), float(frac)) for T, frac in anchors.items())

    def sample(temperature_C: float) -> float:
        T = float(temperature_C)
        if T <= ordered[0][0]:
            return ordered[0][1]
        for (left_T, left_frac), (right_T, right_frac) in zip(
            ordered,
            ordered[1:],
        ):
            if T <= right_T:
                span = right_T - left_T
                weight = (T - left_T) / span
                return left_frac + (right_frac - left_frac) * weight
        return ordered[-1][1]

    return sample


def test_liquidus_finder_bisects_monotone_fraction_curve():
    def frac_M(temperature_C: float) -> float:
        return max(0.0, min(1.0, (temperature_C - 1000.0) / 300.0))

    result = find_liquidus_solidus_by_fraction(
        frac_M,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == 'ok'
    assert result.solidus_T_C == pytest.approx(1000.0, abs=1.0)
    assert result.liquidus_T_C == pytest.approx(1300.0, abs=1.0)
    assert result.liquidus_T_K == pytest.approx(result.liquidus_T_C + 273.15)
    assert result.liquidus_T_C >= result.solidus_T_C
    assert frac_M(result.solidus_T_C) <= 1.0e-3
    assert frac_M(result.liquidus_T_C) >= 1.0 - 1.0e-3
    assert result.iterations <= 64


@pytest.mark.parametrize(
    'overrides',
    (
        {'min_T_C': object()},
        {'scan_step_C': float('inf')},
        {'min_T_C': 1000.0, 'max_T_C': 1000.0},
        {'scan_step_C': 0.0},
        {'tolerance_C': 0.0},
        {'budget_s': float('nan')},
    ),
    ids=(
        'non-numeric-scalar',
        'non-finite-step',
        'inverted-window',
        'non-positive-step',
        'non-positive-tolerance',
        'non-finite-budget',
    ),
)
def test_liquidus_finder_invalid_declared_input_is_refused(overrides):
    calls = 0

    def sample(_temperature_C: float) -> float:
        nonlocal calls
        calls += 1
        return 0.5

    result = find_liquidus_solidus_by_fraction(sample, **overrides)

    assert result.status == 'refused'
    assert result.diagnostics['reason'] == 'invalid_input'
    assert calls == 0


def test_equilibrium_crystallization_invalid_interval_is_refused():
    result = build_equilibrium_crystallization_path(
        lambda _temperature_C: (0.5, {'SiO2': 100.0}),
        solidus_T_C=1200.0,
        liquidus_T_C=1000.0,
    )

    assert result.status == 'refused'
    assert result.diagnostics['reason'] == 'invalid_input'
    assert 'invalid EC interval' in result.warnings[0]


def test_equilibrium_crystallization_non_finite_step_is_refused():
    result = build_equilibrium_crystallization_path(
        lambda _temperature_C: (0.5, {'SiO2': 100.0}),
        solidus_T_C=800.0,
        liquidus_T_C=1000.0,
        grid_step_C=float('inf'),
    )

    assert result.status == 'refused'
    assert result.diagnostics['reason'] == 'invalid_input'
    assert 'grid_step_C' in result.warnings[0]


def test_liquidus_finder_is_deterministic():
    def frac_M(temperature_C: float) -> float:
        return max(0.0, min(1.0, (temperature_C - 925.0) / 250.0))

    first = find_liquidus_solidus_by_fraction(
        frac_M,
        min_T_C=700.0,
        max_T_C=1300.0,
        scan_step_C=75.0,
        tolerance_C=2.0,
    )
    second = find_liquidus_solidus_by_fraction(
        frac_M,
        min_T_C=700.0,
        max_T_C=1300.0,
        scan_step_C=75.0,
        tolerance_C=2.0,
    )

    assert first == second


def test_finder_propagates_internal_monotone_error(monkeypatch):
    def fail_local(*_args, **_kwargs):
        raise KeyError('monotone bookkeeping defect')

    monkeypatch.setattr(liquidus_module, '_monotone_point', fail_local)
    with pytest.raises(KeyError, match='monotone bookkeeping defect'):
        find_liquidus_solidus_by_fraction(
            lambda _temperature_C: 0.5,
            min_T_C=800.0,
            max_T_C=1000.0,
            scan_step_C=100.0,
        )


def test_finder_propagates_untyped_sampler_failure():
    def fail_sampler(_temperature_C):
        raise RuntimeError('engine sample failed')

    with pytest.raises(RuntimeError, match='engine sample failed'):
        find_liquidus_solidus_by_fraction(
            fail_sampler,
            min_T_C=800.0,
            max_T_C=1000.0,
            scan_step_C=100.0,
        )


def test_ec_path_propagates_internal_composition_error(monkeypatch):
    def fail_local(*_args, **_kwargs):
        raise KeyError('composition conversion defect')

    monkeypatch.setattr(liquidus_module, '_coerce_composition', fail_local)
    with pytest.raises(KeyError, match='composition conversion defect'):
        build_equilibrium_crystallization_path(
            lambda _temperature_C: (0.5, {'SiO2': 100.0}),
            solidus_T_C=1000.0,
            liquidus_T_C=1100.0,
            grid_step_C=100.0,
        )


def test_ec_path_propagates_local_sample_callback_error():
    def fail_local(_temperature_C):
        raise KeyError('local sample callback defect')

    with pytest.raises(KeyError, match='local sample callback defect'):
        build_equilibrium_crystallization_path(
            fail_local,
            solidus_T_C=1000.0,
            liquidus_T_C=1100.0,
            grid_step_C=100.0,
        )


@pytest.mark.parametrize(
    ('exception_type', 'expected_status', 'expected_reason'),
    [
        (
            ThermoEngineIsolationError,
            'refused',
            'thermoengine_isolation_required',
        ),
        (
            ThermoEngineFO2UndefinedError,
            'not_converged',
            'thermoengine_fo2_undefined',
        ),
        (
            ThermoEngineNonFiniteField,
            'not_converged',
            'thermoengine_nonfinite_field',
        ),
        (
            ThermoEngineFO2OmittedError,
            'not_converged',
            'thermoengine_fo2_omitted',
        ),
    ],
)
def test_ec_path_preserves_declared_engine_failure(
    exception_type,
    expected_status,
    expected_reason,
):
    def sample(_temperature_C):
        raise exception_type('declared engine reason')

    result = build_equilibrium_crystallization_path(
        sample,
        solidus_T_C=800.0,
        liquidus_T_C=900.0,
    )
    assert result.status == expected_status
    assert result.diagnostics['backend_status_reason'] == expected_reason


@pytest.mark.parametrize(
    'overrides',
    [
        {'max_points': '3.0'},
        {'max_points': float('nan')},
        {'max_points': float('inf')},
        {'grid_step_C': float('nan')},
        {'liquidus_T_C': float('inf')},
    ],
)
def test_ec_path_refuses_invalid_configuration(overrides):
    kwargs = {'solidus_T_C': 1000.0, 'liquidus_T_C': 1100.0}
    kwargs.update(overrides)
    result = build_equilibrium_crystallization_path(
        lambda _temperature_C: (0.5, {'SiO2': 100.0}),
        **kwargs,
    )
    assert result.status == 'refused'
    assert result.diagnostics['reason'] == 'invalid_input'
    assert result.liquid_fraction_path == ()


def test_ec_path_preserves_typed_backend_sample_failure():
    error = LiquidusSampleError(
        'not_converged',
        ('RuntimeError: engine state failed',),
        {
            'exception_class': 'RuntimeError',
            'exception_message': 'engine state failed',
        },
    )

    def fail_backend(_temperature_C):
        raise error

    result = build_equilibrium_crystallization_path(
        fail_backend,
        solidus_T_C=1000.0,
        liquidus_T_C=1100.0,
        grid_step_C=100.0,
    )
    assert result.status == 'not_converged'
    assert result.diagnostics['exception_class'] == 'RuntimeError'
    assert result.diagnostics['exception_message'] == 'engine state failed'


def test_liquidus_finder_reuses_duplicate_bisection_temperatures(monkeypatch):
    sampled_temperatures: list[float] = []
    remaining_seen: list[float] = []
    clock = {'t': 0.0}

    def step_fraction(temperature_C: float, remaining_budget_s=None) -> float:
        sampled_temperatures.append(float(temperature_C))
        remaining_seen.append(float(remaining_budget_s))
        clock['t'] += 0.01
        return 0.0 if temperature_C < 1250.0 else 1.0

    monkeypatch.setattr(liquidus_module.time, 'monotonic', lambda: clock['t'])
    result = find_liquidus_solidus_by_fraction(
        step_fraction,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
        budget_s=1.0,
    )

    assert result.status == 'ok'
    assert result.solidus_T_C == pytest.approx(1249.21875)
    assert result.liquidus_T_C == pytest.approx(1250.0)
    assert result.iterations == 14
    assert len(result.samples) == 22
    assert len(sampled_temperatures) == 15
    assert len(set(sampled_temperatures)) == 15
    assert remaining_seen[0] == pytest.approx(1.0)
    assert remaining_seen[-1] == pytest.approx(0.86)
    assert result.diagnostics == {}


def test_liquidus_finder_refuses_magemin_scale_nonmonotone_dip():
    # 0.09 / 0.33 / 0.05 MAGEMin frac_M dips were observed in the
    # 2026-05-26 freeze-gate flip blast-radius on lunar/mars C2A cases.
    result = find_liquidus_solidus_by_fraction(
        _piecewise_fraction({
            1000.0: 0.0,
            1100.0: 0.5,
            1200.0: 0.98,
            1250.0: 0.98075,
            1300.0: 0.890898,
            1350.0: 0.99,
            1400.0: 1.0,
            1450.0: 1.0,
            1500.0: 0.670427,
            1550.0: 0.945697,
            1600.0: 1.0,
        }),
        min_T_C=1000.0,
        max_T_C=1600.0,
        scan_step_C=50.0,
        tolerance_C=1.0,
    )

    assert result.status == 'not_converged'
    assert result.solidus_T_C is None
    assert result.liquidus_T_C is None
    assert any('non-monotone frac_M' in w for w in result.warnings)
    assert any('would require smoothing' in w for w in result.warnings)


def test_liquidus_finder_guards_non_monotone_fraction_curve():
    values = {
        800.0: 0.0,
        900.0: 1.0,
        1000.0: 0.0,
        1100.0: 1.0,
    }

    result = find_liquidus_solidus_by_fraction(
        lambda temperature_C: values[float(temperature_C)],
        min_T_C=800.0,
        max_T_C=1100.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == 'not_converged'
    assert any('non-monotone frac_M' in warning for warning in result.warnings)


def test_liquidus_finder_reports_missing_bracket_without_crashing():
    result = find_liquidus_solidus_by_fraction(
        lambda temperature_C: 0.2,
        min_T_C=800.0,
        max_T_C=1200.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == 'not_converged'
    assert any('solidus bracket absent' in warning for warning in result.warnings)
    assert any('liquidus bracket absent' in warning for warning in result.warnings)


def test_liquidus_finder_default_budget_is_unbounded():
    """Generic default must not silently bind AlphaMELTS (or unit callables)."""
    calls = {'n': 0}

    def sample(temperature_C: float) -> float:
        calls['n'] += 1
        # Fully solid everywhere -> missing brackets after full grid, not budget.
        return 0.0

    result = find_liquidus_solidus_by_fraction(
        sample,
        min_T_C=800.0,
        max_T_C=1000.0,
        scan_step_C=100.0,
    )
    assert result.status == 'not_converged'
    assert calls['n'] == 3  # 800, 900, 1000
    assert result.diagnostics.get('reason') != 'aggregate_budget_exceeded'


def test_liquidus_finder_rejects_non_advancing_scan_step_without_sampling():
    calls = 0

    def sample(_temperature_C: float) -> float:
        nonlocal calls
        calls += 1
        return 0.0

    result = find_liquidus_solidus_by_fraction(
        sample,
        min_T_C=400.0,
        max_T_C=500.0,
        scan_step_C=1.0e-300,
        budget_s=0.01,
    )

    assert result.status == 'refused'
    assert calls == 0
    assert result.diagnostics['reason'] == 'invalid_input'
    assert any('does not advance temperature' in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ('control', 'value'),
    (
        ('solid_epsilon', float('nan')),
        ('liquid_epsilon', float('inf')),
        ('monotonicity_tolerance', float('nan')),
        ('monotone_smoothing_max', float('inf')),
        ('max_bisection_iterations', float('nan')),
    ),
)
def test_liquidus_finder_rejects_non_finite_controls_before_sampling(control, value):
    calls = []
    result = find_liquidus_solidus_by_fraction(
        lambda temperature_C: calls.append(temperature_C) or 0.5,
        **{control: value},
    )
    assert result.status == 'refused'
    assert result.diagnostics['reason'] == 'invalid_input'
    assert calls == []


@pytest.mark.parametrize(
    ('min_T_C', 'max_T_C', 'scan_step_C'),
    (
        (0.0, 1.0, 1.0e-6),
        (400.0, 500.0, 1.0e-300),
        (0.0, 1000.0, 1.0e-308),
    ),
    ids=('grid-cap', 'non-advancing', 'non-finite-interval-count'),
)
def test_liquidus_finder_rejects_invalid_grid_before_sampling(
    min_T_C, max_T_C, scan_step_C,
):
    calls = []
    result = find_liquidus_solidus_by_fraction(
        lambda temperature_C: calls.append(temperature_C) or 0.5,
        min_T_C=min_T_C,
        max_T_C=max_T_C,
        scan_step_C=scan_step_C,
    )
    assert result.status == 'refused'
    assert result.diagnostics['reason'] == 'invalid_input'
    assert calls == []


@pytest.mark.parametrize(
    ('override', 'warning'),
    (
        ({'max_points': float('inf')}, 'max_points must be finite'),
        ({'liquidus_T_C': float('inf')}, 'liquidus_T_C must be finite'),
    ),
)
def test_liquidus_ec_rejects_non_finite_limits_before_sampling(override, warning):
    calls = []
    params = {'solidus_T_C': 800.0, 'liquidus_T_C': 1000.0}
    params.update(override)
    result = build_equilibrium_crystallization_path(
        lambda temperature_C: calls.append(temperature_C) or (0.5, {'SiO2': 100.0}),
        **params,
    )
    assert result.status == 'refused'
    assert result.diagnostics['reason'] == 'invalid_input'
    assert calls == []
    assert any(warning in message for message in result.warnings)


def test_liquidus_ec_rejects_non_finite_interval_count_before_sampling():
    calls = []
    result = build_equilibrium_crystallization_path(
        lambda temperature_C: calls.append(temperature_C) or (
            0.5,
            {'SiO2': 100.0},
        ),
        solidus_T_C=800.0,
        liquidus_T_C=1000.0,
        grid_step_C=1.0e-308,
    )
    assert result.status == 'refused'
    assert result.diagnostics['reason'] == 'invalid_input'
    assert calls == []
    assert any(
        'interval count is not finite' in message
        for message in result.warnings
    )


@pytest.mark.parametrize('fraction', [-0.5, 1.5])
def test_liquidus_finder_rejects_out_of_range_sampler_fraction(fraction):
    result = find_liquidus_solidus_by_fraction(
        lambda _temperature_C: fraction,
        min_T_C=800.0,
        max_T_C=1000.0,
        scan_step_C=100.0,
    )

    assert result.status == 'not_converged'
    assert any('outside [0, 1]' in warning for warning in result.warnings)


def test_liquidus_finder_preserves_typed_backend_sample_failure():
    diagnostics = {'backend_status': 'out_of_domain', 'crash_point': 900.0}

    def sample(_temperature_C: float) -> float:
        raise LiquidusSampleError(
            'out_of_domain',
            ('engine rejected composition',),
            diagnostics,
        )

    result = find_liquidus_solidus_by_fraction(
        sample,
        min_T_C=800.0,
        max_T_C=1000.0,
        scan_step_C=100.0,
    )

    assert result.status == 'out_of_domain'
    assert result.diagnostics == {
        **diagnostics,
        'exception_class': 'LiquidusSampleError',
        'exception_message': 'engine rejected composition',
    }
    assert result.warnings == ('engine rejected composition',)


def test_liquidus_sample_error_rejects_noncanonical_status():
    with pytest.raises(ValueError, match='canonical refusal'):
        LiquidusSampleError('failed', ('engine failed',), {})


def test_liquidus_sample_error_carries_a_policy_refusal():
    """A policy refusal must survive the finder as itself (b-300).

    ThermoEngine already computes 'refused' as the honest category for
    FO2_REQUIRES_IRON and FO2_TARGET_NOT_FINITE. Before this token was
    permitted, handing it to LiquidusSampleError raised ValueError INSIDE the
    sampler, which the finder's generic guard caught and minted as
    'not_converged' -- so a refusal to supply physics was reported as a solve
    that ran and failed to converge, and any wrap written without widening
    this set first would have reproduced the bug while looking like a fix.
    """
    error = LiquidusSampleError('refused', ('fo2 requires iron',), {})

    assert error.status == 'refused'
    assert error.warnings == ('fo2 requires iron',)


@pytest.mark.parametrize(
    'provider_token',
    ['not_attempted', 'unsupported', 'non_authoritative'],
)
def test_liquidus_sample_error_still_rejects_provider_level_tokens(
    provider_token,
):
    """Widening for 'refused' must not have widened for everything (b-300).

    These describe whether a probe ran at all, at the provider or intent
    level; they are not ways a frac_M sample can refuse. Admitting them here
    would let a token with no meaning at this seam ride out through the
    finder onto a LiquidusSolidusResult.
    """
    with pytest.raises(ValueError, match='canonical refusal'):
        LiquidusSampleError(provider_token, (), {})


def test_liquidus_result_rejects_contradictory_success_fields():
    with pytest.raises(ValueError, match='inconsistent'):
        LiquidusSolidusResult(
            liquidus_T_C=1000.0,
            liquidus_T_K=999.0,
            liquid_fraction=1.0,
            status='ok',
        )

    with pytest.raises(ValueError, match=r'\[0, 1\]'):
        LiquidusSolidusResult(
            liquidus_T_C=1000.0,
            liquid_fraction=1.5,
            status='ok',
        )

    with pytest.raises(ValueError, match=r'\[0, 1\]'):
        MeltFractionSample(temperature_C=1000.0, frac_M=1.5)

    with pytest.raises(ValueError, match='absolute zero'):
        LiquidusSolidusResult(
            liquidus_T_C=-274.0,
            liquid_fraction=1.0,
            status='ok',
        )

    with pytest.raises(ValueError, match='absolute zero'):
        LiquidusSolidusResult(solidus_T_C=-274.0)


@pytest.mark.parametrize(
    'sample',
    [
        {'temperature_C': 1000.0, 'frac_M': 1.01},
        {'temperature_C': -274.0, 'frac_M': 0.5},
    ],
)
def test_liquidus_result_validates_nested_samples(sample):
    with pytest.raises(ValueError):
        LiquidusSolidusResult(status='unavailable', samples=(sample,))


def test_liquidus_finder_stops_on_aggregate_budget(monkeypatch):
    clock = {'t': 0.0}
    remaining_seen: list[float] = []

    def fake_monotonic():
        return clock['t']

    def sample(temperature_C: float, remaining_budget_s=None) -> float:
        remaining_seen.append(remaining_budget_s)
        # Each engine call burns wall time after the residual is observed.
        clock['t'] += 0.1
        return 0.0

    monkeypatch.setattr(liquidus_module.time, 'monotonic', fake_monotonic)

    result = find_liquidus_solidus_by_fraction(
        sample,
        min_T_C=800.0,
        max_T_C=1000.0,
        scan_step_C=100.0,
        budget_s=0.05,
    )

    assert result.status == 'not_converged'
    assert len(result.samples) == 1
    assert any(
        'liquidus finder exceeded aggregate budget 0.05s after 1 calls'
        in warning
        for warning in result.warnings
    )
    diagnostics = dict(result.diagnostics)
    assert diagnostics['reason'] == 'aggregate_budget_exceeded'
    assert diagnostics['call_count'] == 1
    assert diagnostics['last_T_C'] == pytest.approx(800.0)
    assert diagnostics['budget_s'] == pytest.approx(0.05)
    assert diagnostics['elapsed_s'] == pytest.approx(0.1)
    # Residual budget threaded into the call that then overran.
    assert remaining_seen == [pytest.approx(0.05)]


def test_liquidus_finder_threads_remaining_budget_into_each_call(monkeypatch):
    clock = {'t': 0.0}
    remaining_seen: list[float] = []

    def fake_monotonic():
        return clock['t']

    def sample(temperature_C: float, remaining_budget_s=None) -> float:
        remaining_seen.append(float(remaining_budget_s))
        clock['t'] += 0.2
        # Monotone liquidus-ish curve so the finder keeps sampling.
        return max(0.0, min(1.0, (temperature_C - 850.0) / 100.0))

    monkeypatch.setattr(liquidus_module.time, 'monotonic', fake_monotonic)

    result = find_liquidus_solidus_by_fraction(
        sample,
        min_T_C=800.0,
        max_T_C=1000.0,
        scan_step_C=100.0,
        budget_s=0.55,
        tolerance_C=50.0,  # avoid deep bisection; grid alone may finish
        max_bisection_iterations=0,
    )
    # At least two residual observations, strictly decreasing by call cost.
    assert len(remaining_seen) >= 2
    assert remaining_seen[0] == pytest.approx(0.55)
    assert remaining_seen[1] == pytest.approx(0.35)
    assert all(
        earlier > later
        for earlier, later in zip(remaining_seen, remaining_seen[1:])
    )
    # Either ok (with max_bisection 0 path) or not_converged; budget must not
    # have been the failure mode for the first two cheap calls.
    if result.status == 'not_converged':
        assert result.diagnostics.get('reason') != 'aggregate_budget_exceeded' or len(
            result.samples
        ) >= 2


def test_liquidus_finder_engine_residual_timeout_emits_budget_diagnostics(
    monkeypatch,
):
    """Engine path that burns the residual must not fall through as generic fail.

    Simulates the subprocess residual-timeout shape: the sample callable
    observes the residual, burns wall equal to it, then raises a timeout-
    shaped error (never returns a fraction). Structured
    ``aggregate_budget_exceeded`` diagnostics must still be attached.
    """
    clock = {'t': 0.0}
    remaining_seen: list[float] = []

    def fake_monotonic():
        return clock['t']

    def sample(temperature_C: float, remaining_budget_s=None) -> float:
        remaining = float(remaining_budget_s)
        remaining_seen.append(remaining)
        # Burn residual wall as a real residual-clamped subprocess would.
        clock['t'] += remaining
        raise LiquidusSampleError(
            'not_converged',
            (f'MAGEMin binary timed out after {remaining:g}s',),
            {
                'exception_class': 'RuntimeError',
                'exception_message': (
                    f'MAGEMin binary timed out after {remaining:g}s'
                ),
            },
        )

    monkeypatch.setattr(liquidus_module.time, 'monotonic', fake_monotonic)

    result = find_liquidus_solidus_by_fraction(
        sample,
        min_T_C=800.0,
        max_T_C=1000.0,
        scan_step_C=100.0,
        budget_s=0.05,
    )

    assert result.status == 'not_converged'
    assert result.samples == ()  # timed out before a sample was recorded
    assert remaining_seen == [pytest.approx(0.05)]
    assert any(
        'liquidus finder exceeded aggregate budget 0.05s after 0 calls'
        in warning
        for warning in result.warnings
    )
    diagnostics = dict(result.diagnostics)
    assert diagnostics['reason'] == 'aggregate_budget_exceeded'
    assert diagnostics['call_count'] == 0
    assert diagnostics['last_T_C'] == pytest.approx(800.0)
    assert diagnostics['budget_s'] == pytest.approx(0.05)
    assert diagnostics['elapsed_s'] == pytest.approx(0.05)
    # Must not be the generic Exception envelope.
    assert not any(
        warning.startswith('liquidus finder failed:')
        for warning in result.warnings
    )


def test_finder_projects_tagged_external_failure():
    from engines.alphamelts.petthermo import _PetThermoToolsExternalCallFailure

    failure = _PetThermoToolsExternalCallFailure(
        'RuntimeError',
        'review-real-engine-runtime',
    )
    result = find_liquidus_solidus_by_fraction(
        lambda _temperature_C: (_ for _ in ()).throw(failure),
        min_T_C=800.0,
        max_T_C=900.0,
        scan_step_C=100.0,
    )
    assert result.status == 'not_converged'
    assert result.diagnostics['exception_class'] == (
        '_PetThermoToolsExternalCallFailure'
    )
    assert 'review-real-engine-runtime' in (
        result.diagnostics['exception_message']
    )


def test_ec_projects_tagged_external_failure_and_typed_absence():
    from engines.alphamelts.petthermo import (
        _PetThermoToolsExternalCallFailure,
        _PetThermoToolsUnavailable,
    )
    from engines.alphamelts.thermoengine import ThermoEngineNotConvergedError
    from simulator.engine_pool import EngineWorkerTimeout

    tagged = _PetThermoToolsExternalCallFailure(
        'RuntimeError',
        'review-real-engine-runtime',
    )
    tagged_result = build_equilibrium_crystallization_path(
        lambda _temperature_C: (_ for _ in ()).throw(tagged),
        solidus_T_C=1000.0,
        liquidus_T_C=1100.0,
        grid_step_C=100.0,
    )
    assert tagged_result.status == 'not_converged'
    assert tagged_result.diagnostics['exception_class'] == (
        '_PetThermoToolsExternalCallFailure'
    )
    assert 'review-real-engine-runtime' in tagged_result.warnings[0]

    absent = build_equilibrium_crystallization_path(
        lambda _temperature_C: (_ for _ in ()).throw(
            _PetThermoToolsUnavailable('MELTSdynamic loader not found')
        ),
        solidus_T_C=1000.0,
        liquidus_T_C=1100.0,
        grid_step_C=100.0,
    )
    assert absent.status == 'unavailable'
    assert 'MELTSdynamic loader not found' in absent.warnings[0]

    unsolved = build_equilibrium_crystallization_path(
        lambda _temperature_C: (_ for _ in ()).throw(
            ThermoEngineNotConvergedError(
                'ThermoEngine equilibrium status: failed'
            )
        ),
        solidus_T_C=1000.0,
        liquidus_T_C=1100.0,
        grid_step_C=100.0,
    )
    assert unsolved.status == 'not_converged'
    assert 'equilibrium status: failed' in unsolved.warnings[0]

    timed_out = build_equilibrium_crystallization_path(
        lambda _temperature_C: (_ for _ in ()).throw(
            EngineWorkerTimeout('worker', 1.0, phase='call')
        ),
        solidus_T_C=1000.0,
        liquidus_T_C=1100.0,
        grid_step_C=100.0,
    )
    assert timed_out.status == 'not_converged'


def test_ec_projects_timeout_contract_and_propagates_engine_crash():
    from simulator.melt_backend.alphamelts import (
        ALPHAMELTS_REASON_SUBPROCESS_DIED,
        ALPHAMELTS_REASON_TIMEOUT,
        AlphaMELTSSubprocessContractError,
        _alphamelts_backend_failure_error,
    )

    timeout = _alphamelts_backend_failure_error(
        ALPHAMELTS_REASON_TIMEOUT,
        'wall',
    )
    result = build_equilibrium_crystallization_path(
        lambda _temperature_C: (_ for _ in ()).throw(timeout),
        solidus_T_C=1000.0,
        liquidus_T_C=1100.0,
        grid_step_C=100.0,
    )
    assert result.status == 'not_converged'
    assert 'timed out' in result.warnings[0]

    crash = _alphamelts_backend_failure_error(
        ALPHAMELTS_REASON_SUBPROCESS_DIED,
        'worker exited',
    )
    with pytest.raises(AlphaMELTSSubprocessContractError) as captured:
        build_equilibrium_crystallization_path(
            lambda _temperature_C: (_ for _ in ()).throw(crash),
            solidus_T_C=1000.0,
            liquidus_T_C=1100.0,
            grid_step_C=100.0,
        )
    assert captured.value.backend_failure_category == 'engine_crash'


def test_finder_projects_timeout_contract_and_propagates_engine_crash():
    from simulator.melt_backend.alphamelts import (
        ALPHAMELTS_REASON_SUBPROCESS_DIED,
        ALPHAMELTS_REASON_TIMEOUT,
        AlphaMELTSSubprocessContractError,
        _alphamelts_backend_failure_error,
    )

    timeout = _alphamelts_backend_failure_error(
        ALPHAMELTS_REASON_TIMEOUT,
        'wall',
    )
    result = find_liquidus_solidus_by_fraction(
        lambda _temperature_C: (_ for _ in ()).throw(timeout),
        min_T_C=800.0,
        max_T_C=900.0,
        scan_step_C=100.0,
    )
    assert result.status == 'not_converged'
    assert 'timed out' in result.warnings[0]

    crash = _alphamelts_backend_failure_error(
        ALPHAMELTS_REASON_SUBPROCESS_DIED,
        'worker exited',
    )
    with pytest.raises(AlphaMELTSSubprocessContractError) as captured:
        find_liquidus_solidus_by_fraction(
            lambda _temperature_C: (_ for _ in ()).throw(crash),
            min_T_C=800.0,
            max_T_C=900.0,
            scan_step_C=100.0,
        )
    assert captured.value.backend_failure_category == 'engine_crash'


def test_ec_propagates_untagged_runtime_error():
    with pytest.raises(RuntimeError, match='review-local-runtime'):
        build_equilibrium_crystallization_path(
            lambda _temperature_C: (_ for _ in ()).throw(
                RuntimeError('review-local-runtime')
            ),
            solidus_T_C=1000.0,
            liquidus_T_C=1100.0,
            grid_step_C=100.0,
        )
