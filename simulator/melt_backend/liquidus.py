"""Liquidus / solidus finder helpers for silicate melt backends."""

from __future__ import annotations

import inspect
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Tuple

from simulator.melt_backend.base import LiquidFractionInvalidError


# MAGEMin-facing default only - the generic finder stays unbounded so
# AlphaMELTS (and pure-function unit tests) are not silently wall-capped.
#
# Derivation for DEFAULT_LIQUIDUS_FINDER_BUDGET_S (900 s):
#   Premise: a legitimate real MAGEMin freeze-gate search on lunar mare
#   is documented at ~163 s wall (37-point 400-2200 C / 50 C grid +
#   bisection; AGENTS.md test-timeout invariant / M5 MacBook Pro).
#   Worst-case call count under defaults: ceil((2200-400)/50)+1 = 37
#   grid + 2 * max_bisection_iterations (32) = 37 + 64 = 101 engine
#   calls. Per-call timeout default is 60 s, so a between-calls-only
#   budget would admit up to ~budget + 60 s of overrun per call; under
#   per-call limits alone the pathological case is 101 * 60 s ~ 101 min.
#   Algebra: budget >= n_calls * p95_call * headroom with headroom >= 2.
#   Healthy p95 is far below the 60 s ceiling (order ~1-4 s); using a
#   conservative 4 s p95: 101 * 4 * 2.2 ~ 890 s -> round to 900 s.
#   Sanity: 900 / 163 ~ 5.5x headroom on the documented real search, and
#   900 << 101 min so the aggregate still bounds the spinel-hang class.
# Unit check: all terms in seconds; product is seconds.
DEFAULT_LIQUIDUS_FINDER_BUDGET_S = 900.0
MAX_LIQUIDUS_SCAN_POINTS = 100_000
LIQUIDUS_REFUSAL_STATUSES = frozenset({
    'not_converged',
    'out_of_domain',
    'unavailable',
})


class LiquidusSampleError(RuntimeError):
    """Typed backend sample rejection preserved through the finder."""

    def __init__(
        self,
        status: str,
        warnings: tuple[str, ...],
        diagnostics: Mapping[str, Any],
    ) -> None:
        self.status = str(status)
        if self.status not in LIQUIDUS_REFUSAL_STATUSES:
            raise ValueError(
                'LiquidusSampleError status must be a canonical refusal: '
                f'{self.status!r}'
            )
        self.warnings = tuple(str(warning) for warning in warnings)
        self.diagnostics = dict(diagnostics or {})
        super().__init__('; '.join(self.warnings) or self.status)


@dataclass(frozen=True)
class MeltFractionSample:
    temperature_C: float
    frac_M: float

    def __post_init__(self) -> None:
        object.__setattr__(self, 'temperature_C', float(self.temperature_C))
        object.__setattr__(self, 'frac_M', float(self.frac_M))
        _validate_temperature_C(self.temperature_C, 'sample_temperature_C')
        _validate_optional_fraction(self.frac_M, 'sample_frac_M')


@dataclass(frozen=True)
class LiquidusSolidusResult:
    liquidus_T_C: Optional[float] = None
    liquidus_T_K: Optional[float] = None
    solidus_T_C: Optional[float] = None
    liquid_fraction: Optional[float] = None
    status: str = 'unavailable'
    warnings: Tuple[str, ...] = ()
    samples: Tuple[MeltFractionSample, ...] = ()
    iterations: int = 0
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.liquidus_T_C is not None:
            object.__setattr__(self, 'liquidus_T_C', float(self.liquidus_T_C))
        if self.liquidus_T_K is not None:
            object.__setattr__(self, 'liquidus_T_K', float(self.liquidus_T_K))
        if self.liquidus_T_K is None and self.liquidus_T_C is not None:
            object.__setattr__(self, 'liquidus_T_K', self.liquidus_T_C + 273.15)
        if self.liquidus_T_C is None and self.liquidus_T_K is not None:
            object.__setattr__(self, 'liquidus_T_C', self.liquidus_T_K - 273.15)
        if self.solidus_T_C is not None:
            object.__setattr__(self, 'solidus_T_C', float(self.solidus_T_C))
        if self.liquid_fraction is not None:
            object.__setattr__(
                self,
                'liquid_fraction',
                float(self.liquid_fraction),
            )
        object.__setattr__(self, 'status', str(self.status))
        object.__setattr__(self, 'warnings', tuple(str(w) for w in self.warnings))
        object.__setattr__(
            self,
            'samples',
            tuple(_coerce_sample(sample) for sample in self.samples),
        )
        object.__setattr__(self, 'iterations', int(self.iterations))
        object.__setattr__(self, 'diagnostics', dict(self.diagnostics or {}))
        _validate_temperature_pair(
            self.liquidus_T_C,
            self.liquidus_T_K,
            context='liquidus',
        )
        _validate_temperature_C(self.solidus_T_C, 'solidus_T_C')
        _validate_optional_fraction(self.liquid_fraction, 'liquid_fraction')
        if (
            self.liquidus_T_C is not None
            and self.solidus_T_C is not None
            and self.liquidus_T_C < self.solidus_T_C
        ):
            raise ValueError('liquidus_T_C must not be below solidus_T_C')
        if self.status == 'ok':
            if self.liquidus_T_C is None:
                raise ValueError('status=ok requires liquidus_T_C')
            if self.liquid_fraction is None:
                raise ValueError('status=ok requires liquid_fraction')


@dataclass(frozen=True)
class LiquidFractionPathPoint:
    temperature_C: float
    liquid_fraction: float
    liquid_composition_wt_pct: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'temperature_C', float(self.temperature_C))
        object.__setattr__(
            self,
            'liquid_fraction',
            _clamp_fraction(self.liquid_fraction),
        )
        object.__setattr__(
            self,
            'liquid_composition_wt_pct',
            _coerce_composition(self.liquid_composition_wt_pct),
        )


@dataclass(frozen=True)
class EquilibriumCrystallizationPathResult:
    liquidus_T_C: Optional[float] = None
    liquidus_T_K: Optional[float] = None
    solidus_T_C: Optional[float] = None
    liquid_fraction: Optional[float] = None
    status: str = 'unavailable'
    warnings: Tuple[str, ...] = ()
    liquid_fraction_path: Tuple[LiquidFractionPathPoint, ...] = ()
    samples: Tuple[MeltFractionSample, ...] = ()
    iterations: int = 0
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.liquidus_T_C is not None:
            object.__setattr__(self, 'liquidus_T_C', float(self.liquidus_T_C))
        if self.liquidus_T_K is not None:
            object.__setattr__(self, 'liquidus_T_K', float(self.liquidus_T_K))
        if self.liquidus_T_K is None and self.liquidus_T_C is not None:
            object.__setattr__(self, 'liquidus_T_K', self.liquidus_T_C + 273.15)
        if self.liquidus_T_C is None and self.liquidus_T_K is not None:
            object.__setattr__(self, 'liquidus_T_C', self.liquidus_T_K - 273.15)
        if self.solidus_T_C is not None:
            object.__setattr__(self, 'solidus_T_C', float(self.solidus_T_C))
        if self.liquid_fraction is not None:
            object.__setattr__(
                self,
                'liquid_fraction',
                float(self.liquid_fraction),
            )
        object.__setattr__(self, 'status', str(self.status))
        object.__setattr__(self, 'warnings', tuple(str(w) for w in self.warnings))
        object.__setattr__(
            self,
            'liquid_fraction_path',
            tuple(_coerce_path_point(p) for p in self.liquid_fraction_path),
        )
        object.__setattr__(
            self,
            'samples',
            tuple(_coerce_sample(sample) for sample in self.samples),
        )
        object.__setattr__(self, 'iterations', int(self.iterations))
        object.__setattr__(self, 'diagnostics', dict(self.diagnostics or {}))
        _validate_temperature_pair(
            self.liquidus_T_C,
            self.liquidus_T_K,
            context='liquidus',
        )
        _validate_temperature_C(self.solidus_T_C, 'solidus_T_C')
        _validate_optional_fraction(self.liquid_fraction, 'liquid_fraction')
        if (
            self.liquidus_T_C is not None
            and self.solidus_T_C is not None
            and self.liquidus_T_C < self.solidus_T_C
        ):
            raise ValueError('liquidus_T_C must not be below solidus_T_C')
        if self.status == 'ok':
            if self.liquidus_T_C is None or self.solidus_T_C is None:
                raise ValueError(
                    'EC status=ok requires liquidus_T_C and solidus_T_C'
                )
            if self.liquid_fraction is None:
                raise ValueError('EC status=ok requires liquid_fraction')


def find_liquidus_solidus_by_fraction(
    sample_fraction: Callable[..., float],
    *,
    min_T_C: float = 400.0,
    max_T_C: float = 2200.0,
    scan_step_C: float = 50.0,
    tolerance_C: float = 2.0,
    solid_epsilon: float = 1.0e-3,
    liquid_epsilon: float = 1.0e-3,
    monotonicity_tolerance: float = 2.0e-2,
    monotone_smoothing_max: float = 5.0e-1,
    max_bisection_iterations: int = 32,
    budget_s: Optional[float] = None,
) -> LiquidusSolidusResult:
    """Bracket and bisect solidus/liquidus on monotone melt fraction.

    ``budget_s`` defaults to ``None`` (unbounded). Callers that must bound
    aggregate wall time — MAGEMin in particular — pass a finite budget
    explicitly (see ``DEFAULT_LIQUIDUS_FINDER_BUDGET_S``). Remaining budget
    is threaded into each sample as ``remaining_budget_s`` when the
    callable accepts it, so engine call timeouts can be clamped to the
    residual rather than only checking the budget between calls.
    """
    try:
        min_T = float(min_T_C)
        max_T = float(max_T_C)
        step = float(scan_step_C)
        tolerance = float(tolerance_C)
        budget = None if budget_s is None else float(budget_s)
    except (TypeError, ValueError) as exc:
        return _not_converged(f'invalid finder parameter: {exc}')
    if not all(math.isfinite(value) for value in (min_T, max_T, step, tolerance)):
        return _not_converged('invalid finder parameter: temperatures and steps must be finite')
    if not min_T < max_T:
        return _not_converged('invalid finder window: min_T_C must be below max_T_C')
    if step <= 0.0:
        return _not_converged('invalid finder scan_step_C: must be positive')
    if tolerance <= 0.0:
        return _not_converged('invalid finder tolerance_C: must be positive')
    if budget is not None and (not math.isfinite(budget) or budget <= 0.0):
        return _not_converged('invalid finder budget_s: must be finite and positive')

    liquid_threshold = 1.0 - float(liquid_epsilon)
    solid_threshold = float(solid_epsilon)
    samples: list[MeltFractionSample] = []
    smoothing_warnings: list[str] = []
    iterations = 0
    start_time = time.monotonic()
    deadline = start_time + budget if budget is not None else None
    last_T_C: Optional[float] = None

    def _elapsed_s() -> float:
        return max(0.0, time.monotonic() - start_time)

    def _budget_diagnostics() -> dict[str, Any]:
        assert budget is not None
        return {
            'reason': 'aggregate_budget_exceeded',
            'elapsed_s': _elapsed_s(),
            'call_count': len(samples),
            'last_T_C': last_T_C,
            'budget_s': float(budget),
        }

    def budget_warning() -> str:
        assert budget is not None
        return (
            f'liquidus finder exceeded aggregate budget {budget:g}s '
            f'after {len(samples)} calls'
        )

    def remaining_budget_s() -> Optional[float]:
        if deadline is None:
            return None
        return max(0.0, deadline - time.monotonic())

    def check_budget() -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise _LiquidusFinderBudgetExceeded(budget_warning())

    def _raise_budget_if_engine_exhausted(exc: BaseException) -> None:
        """Promote residual-timeout / cancel failures to typed budget exhaustion.

        Between-call budget checks alone lose structure when the *engine*
        path burns the residual (subprocess timeout clamped to remaining,
        or a cancel for a non-positive residual). Without this promotion
        those paths surface as generic ``liquidus finder failed: …`` with
        empty diagnostics, while the in-process post-sample check still
        emits ``reason=aggregate_budget_exceeded``. Exhaustion must be
        equally legible on every execution path.
        """
        if deadline is None:
            return
        message = str(exc).lower()
        residual_exhausted = remaining_budget_s() is not None and (
            remaining_budget_s() <= 0.0
        )
        explicit_cancel = (
            'aggregate liquidus budget exhausted' in message
            or 'aggregate budget' in message
        )
        if residual_exhausted or explicit_cancel or time.monotonic() >= deadline:
            raise _LiquidusFinderBudgetExceeded(budget_warning()) from exc

    def sample(T_C: float) -> MeltFractionSample:
        nonlocal last_T_C
        check_budget()
        remaining = remaining_budget_s()
        if remaining is not None and remaining <= 0.0:
            raise _LiquidusFinderBudgetExceeded(budget_warning())
        last_T_C = float(T_C)
        try:
            raw = _invoke_sample_fraction(
                sample_fraction,
                float(T_C),
                remaining_budget_s=remaining,
            )
        except _LiquidusFinderBudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001 - engine-boundary sample guard
            _raise_budget_if_engine_exhausted(exc)
            raise
        frac = _clamp_fraction(raw)
        point = _monotone_point(
            MeltFractionSample(float(T_C), frac),
            samples,
            tolerance=monotonicity_tolerance,
            smoothing_max=monotone_smoothing_max,
        )
        samples.append(point)
        samples.sort(key=lambda p: p.temperature_C)
        check_budget()
        return point

    try:
        grid = _bounded_scan_grid(min_T, max_T, step)
        grid_points = [sample(T) for T in grid]

        solidus_bracket = None
        liquidus_bracket = None
        previous = grid_points[0]
        for current in grid_points[1:]:
            if (
                solidus_bracket is None
                and previous.frac_M <= solid_threshold
                and current.frac_M > solid_threshold
            ):
                solidus_bracket = (previous, current)
            if (
                liquidus_bracket is None
                and previous.frac_M < liquid_threshold
                and current.frac_M >= liquid_threshold
            ):
                liquidus_bracket = (previous, current)
            previous = current

        missing = []
        if solidus_bracket is None:
            missing.append(
                f'solidus bracket absent: frac_M never crossed {solid_threshold:g}'
            )
        if liquidus_bracket is None:
            missing.append(
                f'liquidus bracket absent: frac_M never reached {liquid_threshold:g}'
            )
        if missing:
            return LiquidusSolidusResult(
                status='not_converged',
                warnings=tuple([*smoothing_warnings, *missing]),
                samples=tuple(samples),
                iterations=iterations,
            )

        solidus, solidus_iterations = _bisect_solidus(
            sample,
            solidus_bracket[0],
            solidus_bracket[1],
            threshold=solid_threshold,
            tolerance_C=tolerance,
            max_iterations=max_bisection_iterations,
        )
        iterations += solidus_iterations
        liquidus, liquidus_iterations = _bisect_liquidus(
            sample,
            liquidus_bracket[0],
            liquidus_bracket[1],
            threshold=liquid_threshold,
            tolerance_C=tolerance,
            max_iterations=max_bisection_iterations,
        )
        iterations += liquidus_iterations
    except _LiquidusFinderBudgetExceeded as exc:
        return LiquidusSolidusResult(
            status='not_converged',
            warnings=tuple([*smoothing_warnings, str(exc)]),
            samples=tuple(samples),
            iterations=iterations,
            diagnostics=_budget_diagnostics(),
        )
    except LiquidusSampleError as exc:
        return LiquidusSolidusResult(
            status=exc.status,
            warnings=tuple([*smoothing_warnings, *exc.warnings]),
            samples=tuple(samples),
            iterations=iterations,
            diagnostics=exc.diagnostics,
        )
    except Exception as exc:  # noqa: BLE001 - library-boundary finder guard
        return LiquidusSolidusResult(
            status='not_converged',
            warnings=tuple([
                *smoothing_warnings,
                f'liquidus finder failed: {exc}',
            ]),
            samples=tuple(samples),
            iterations=iterations,
        )

    if liquidus.temperature_C < solidus.temperature_C:
        return LiquidusSolidusResult(
            status='not_converged',
            warnings=tuple([
                *smoothing_warnings,
                'liquidus below solidus after bisection',
            ]),
            samples=tuple(samples),
            iterations=iterations,
        )
    return LiquidusSolidusResult(
        liquidus_T_C=liquidus.temperature_C,
        solidus_T_C=solidus.temperature_C,
        liquid_fraction=1.0,
        status='ok',
        warnings=tuple(smoothing_warnings),
        samples=tuple(samples),
        iterations=iterations,
    )


def build_equilibrium_crystallization_path(
    sample_liquid_state: Callable[[float], tuple[float, Mapping[str, float]]],
    *,
    solidus_T_C: float,
    liquidus_T_C: float,
    grid_step_C: float = 50.0,
    max_points: int = 41,
    monotonicity_tolerance: float = 2.0e-2,
    monotone_smoothing_max: float = 5.0e-1,
) -> EquilibriumCrystallizationPathResult:
    """Build a monotone liquid-fraction path over solidus -> liquidus."""
    samples: list[MeltFractionSample] = []
    path: list[LiquidFractionPathPoint] = []
    smoothing_warnings: list[str] = []
    try:
        solidus_T = float(solidus_T_C)
        liquidus_T = float(liquidus_T_C)
        if not solidus_T <= liquidus_T:
            return EquilibriumCrystallizationPathResult(
                status='not_converged',
                warnings=('invalid EC interval: solidus_T_C exceeds liquidus_T_C',),
            )
        temperatures = _temperature_grid(
            solidus_T,
            liquidus_T,
            grid_step_C=grid_step_C,
            max_points=max_points,
        )
        for temperature_C in temperatures:
            raw_fraction, raw_composition = sample_liquid_state(float(temperature_C))
            fraction_point = _monotone_point(
                MeltFractionSample(
                    float(temperature_C),
                    _clamp_fraction(raw_fraction),
                ),
                samples,
                tolerance=monotonicity_tolerance,
                smoothing_max=monotone_smoothing_max,
            )
            samples.append(fraction_point)
            samples.sort(key=lambda p: p.temperature_C)
            path.append(
                LiquidFractionPathPoint(
                    temperature_C=fraction_point.temperature_C,
                    liquid_fraction=fraction_point.frac_M,
                    liquid_composition_wt_pct=_coerce_composition(raw_composition),
                )
            )
    except LiquidFractionInvalidError:
        raise
    except Exception as exc:  # noqa: BLE001 - engine sampler boundary
        return EquilibriumCrystallizationPathResult(
            liquidus_T_C=liquidus_T_C,
            solidus_T_C=solidus_T_C,
            status='not_converged',
            warnings=tuple([
                *smoothing_warnings,
                f'equilibrium crystallization path failed: {exc}',
            ]),
            liquid_fraction_path=tuple(path),
            samples=tuple(samples),
            iterations=len(samples),
        )
    return EquilibriumCrystallizationPathResult(
        liquidus_T_C=liquidus_T,
        solidus_T_C=solidus_T,
        liquid_fraction=(path[-1].liquid_fraction if path else None),
        status='ok',
        warnings=tuple(smoothing_warnings),
        liquid_fraction_path=tuple(path),
        samples=tuple(samples),
        iterations=len(samples),
    )


def _bisect_solidus(
    sample: Callable[[float], MeltFractionSample],
    low: MeltFractionSample,
    high: MeltFractionSample,
    *,
    threshold: float,
    tolerance_C: float,
    max_iterations: int,
) -> tuple[MeltFractionSample, int]:
    iterations = 0
    while high.temperature_C - low.temperature_C > tolerance_C:
        if iterations >= max_iterations:
            raise RuntimeError('solidus bisection exceeded iteration bound')
        mid = sample((low.temperature_C + high.temperature_C) / 2.0)
        if mid.frac_M <= threshold:
            low = mid
        else:
            high = mid
        iterations += 1
    return low, iterations


def _bisect_liquidus(
    sample: Callable[[float], MeltFractionSample],
    low: MeltFractionSample,
    high: MeltFractionSample,
    *,
    threshold: float,
    tolerance_C: float,
    max_iterations: int,
) -> tuple[MeltFractionSample, int]:
    iterations = 0
    while high.temperature_C - low.temperature_C > tolerance_C:
        if iterations >= max_iterations:
            raise RuntimeError('liquidus bisection exceeded iteration bound')
        mid = sample((low.temperature_C + high.temperature_C) / 2.0)
        if mid.frac_M >= threshold:
            high = mid
        else:
            low = mid
        iterations += 1
    return high, iterations


def _monotone_point(
    point: MeltFractionSample,
    samples: list[MeltFractionSample],
    *,
    tolerance: float,
    smoothing_max: float,
) -> MeltFractionSample:
    lower = [p for p in samples if p.temperature_C < point.temperature_C]
    upper = [p for p in samples if p.temperature_C > point.temperature_C]
    frac = point.frac_M
    if lower:
        low = max(lower, key=lambda p: p.temperature_C)
        drop = low.frac_M - frac
        if drop > smoothing_max:
            raise RuntimeError(
                'non-monotone frac_M(T): '
                f'{point.temperature_C:.3f} C gives {frac:.6g} below '
                f'{low.temperature_C:.3f} C value {low.frac_M:.6g}'
            )
        if drop > tolerance:
            raise RuntimeError(
                'non-monotone frac_M(T) would require smoothing: '
                f'{point.temperature_C:.3f} C gives raw {frac:.6g} below '
                f'{low.temperature_C:.3f} C value {low.frac_M:.6g}'
            )
        # Tiny engine jitter stays silent; material non-monotone dips fail
        # closed instead of being consumed as bracket points.
        frac = max(frac, low.frac_M)
    if upper:
        high = min(upper, key=lambda p: p.temperature_C)
        rise = frac - high.frac_M
        if rise > smoothing_max:
            raise RuntimeError(
                'non-monotone frac_M(T): '
                f'{point.temperature_C:.3f} C gives {frac:.6g} above '
                f'{high.temperature_C:.3f} C value {high.frac_M:.6g}'
            )
        if rise > tolerance:
            raise RuntimeError(
                'non-monotone frac_M(T) would require smoothing: '
                f'{point.temperature_C:.3f} C gives raw {frac:.6g} above '
                f'{high.temperature_C:.3f} C value {high.frac_M:.6g}'
            )
        frac = min(frac, high.frac_M)
    return MeltFractionSample(point.temperature_C, frac)


def _clamp_fraction(value: float) -> float:
    frac = float(value)
    if not math.isfinite(frac):
        raise LiquidFractionInvalidError(f'invalid frac_M value: {value!r}')
    if frac < -1.0e-12 or frac > 1.0 + 1.0e-12:
        raise LiquidFractionInvalidError(
            f'invalid frac_M value outside [0, 1]: {value!r}'
        )
    return max(0.0, min(1.0, frac))


def _bounded_scan_grid(
    min_T_C: float,
    max_T_C: float,
    scan_step_C: float,
) -> Tuple[float, ...]:
    span = float(max_T_C) - float(min_T_C)
    step = float(scan_step_C)
    if float(min_T_C) + step == float(min_T_C):
        raise RuntimeError(
            'invalid finder scan_step_C: step does not advance temperature'
        )
    intervals = math.ceil(span / step)
    point_count = intervals + 1
    if point_count > MAX_LIQUIDUS_SCAN_POINTS:
        raise RuntimeError(
            'invalid finder scan grid: '
            f'{point_count} points exceeds cap {MAX_LIQUIDUS_SCAN_POINTS}'
        )
    return tuple(
        min(float(max_T_C), float(min_T_C) + index * step)
        for index in range(point_count)
    )


def _temperature_grid(
    solidus_T_C: float,
    liquidus_T_C: float,
    *,
    grid_step_C: float,
    max_points: int,
) -> Tuple[float, ...]:
    step = float(grid_step_C)
    point_cap = int(max_points)
    if step <= 0.0:
        raise RuntimeError('invalid EC grid_step_C: must be positive')
    if point_cap < 2:
        raise RuntimeError('invalid EC max_points: must be at least 2')
    span = float(liquidus_T_C) - float(solidus_T_C)
    if span == 0.0:
        return (float(solidus_T_C),)
    intervals = min(max(1, math.ceil(span / step)), point_cap - 1)
    return tuple(
        float(solidus_T_C) + span * index / intervals
        for index in range(intervals + 1)
    )


def _coerce_path_point(point: object) -> LiquidFractionPathPoint:
    if isinstance(point, LiquidFractionPathPoint):
        return point
    if isinstance(point, Mapping):
        temperature_C = point.get('temperature_C')
        if temperature_C is None:
            temperature_C = point.get('T_C', point.get('T'))
        return LiquidFractionPathPoint(
            temperature_C=float(temperature_C),
            liquid_fraction=float(point.get('liquid_fraction')),
            liquid_composition_wt_pct=point.get(
                'liquid_composition_wt_pct', {}
            ),
        )
    return LiquidFractionPathPoint(
        temperature_C=float(getattr(point, 'temperature_C')),
        liquid_fraction=float(getattr(point, 'liquid_fraction')),
        liquid_composition_wt_pct=getattr(
            point,
            'liquid_composition_wt_pct',
            {},
        ),
    )


def _coerce_sample(sample: object) -> MeltFractionSample:
    if isinstance(sample, MeltFractionSample):
        return sample
    if isinstance(sample, Mapping):
        temperature_C = sample.get('temperature_C')
        if temperature_C is None:
            temperature_C = sample.get('T_C', sample.get('T'))
        frac_M = sample.get('frac_M')
        if frac_M is None:
            frac_M = sample.get('liquid_fraction')
        return MeltFractionSample(
            temperature_C=float(temperature_C),
            frac_M=float(frac_M),
        )
    return MeltFractionSample(
        temperature_C=float(getattr(sample, 'temperature_C')),
        frac_M=float(getattr(sample, 'frac_M')),
    )


def _coerce_composition(composition: Mapping[str, float]) -> dict[str, float]:
    result: dict[str, float] = {}
    for species, value in dict(composition or {}).items():
        amount = float(value)
        if not math.isfinite(amount):
            raise RuntimeError(
                f'invalid liquid_composition_wt_pct value for {species}: {value!r}'
            )
        if amount < 0.0:
            raise RuntimeError(
                'invalid liquid_composition_wt_pct negative value for '
                f'{species}: {value!r}'
            )
        result[str(species)] = amount
    return result


def _validate_optional_finite(value: Optional[float], name: str) -> None:
    if value is not None and not math.isfinite(float(value)):
        raise ValueError(f'{name} must be finite')


def _validate_optional_fraction(value: Optional[float], name: str) -> None:
    if value is None:
        return
    _validate_optional_finite(value, name)
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(f'{name} must be in [0, 1]')


def _validate_temperature_C(value: Optional[float], name: str) -> None:
    _validate_optional_finite(value, name)
    if value is not None and float(value) < -273.15:
        raise ValueError(f'{name} must not be below absolute zero')


def _validate_temperature_pair(
    temperature_C: Optional[float],
    temperature_K: Optional[float],
    *,
    context: str,
) -> None:
    _validate_temperature_C(temperature_C, f'{context}_T_C')
    _validate_optional_finite(temperature_K, f'{context}_T_K')
    if temperature_K is not None and float(temperature_K) < 0.0:
        raise ValueError(f'{context}_T_K must not be below absolute zero')
    if temperature_C is None or temperature_K is None:
        return
    # Premise: T_K = T_C + 273.15 by thermodynamic temperature definition.
    # Algebra: residual = T_K - T_C - 273.15; unit check: kelvin increments
    # and Celsius increments are identical. Sanity: contradictory serialized
    # unit fields must fail rather than let consumers choose different physics.
    if not math.isclose(
        float(temperature_K),
        float(temperature_C) + 273.15,
        rel_tol=0.0,
        abs_tol=1.0e-9,
    ):
        raise ValueError(
            f'{context}_T_C and {context}_T_K are inconsistent'
        )


def _not_converged(message: str) -> LiquidusSolidusResult:
    return LiquidusSolidusResult(status='not_converged', warnings=(message,))


def _invoke_sample_fraction(
    sample_fraction: Callable[..., float],
    temperature_C: float,
    *,
    remaining_budget_s: Optional[float],
) -> float:
    """Call sample_fraction, threading remaining budget when accepted.

    Budget-aware engines (MAGEMin) declare
    ``remaining_budget_s: Optional[float] = None`` (or a second positional)
    and clamp their per-call timeout/cancellation to that residual. Simple
    ``lambda T: ...`` unit-test callables keep working unchanged.
    """
    if remaining_budget_s is None:
        return float(sample_fraction(float(temperature_C)))
    try:
        parameters = inspect.signature(sample_fraction).parameters
    except (TypeError, ValueError):
        return float(sample_fraction(float(temperature_C)))
    if 'remaining_budget_s' in parameters:
        return float(
            sample_fraction(
                float(temperature_C),
                remaining_budget_s=float(remaining_budget_s),
            )
        )
    positional = [
        name
        for name, param in parameters.items()
        if param.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    if len(positional) >= 2:
        return float(
            sample_fraction(float(temperature_C), float(remaining_budget_s))
        )
    return float(sample_fraction(float(temperature_C)))


class _LiquidusFinderBudgetExceeded(RuntimeError):
    pass


__all__ = (
    'DEFAULT_LIQUIDUS_FINDER_BUDGET_S',
    'EquilibriumCrystallizationPathResult',
    'LiquidFractionPathPoint',
    'LiquidusSampleError',
    'LiquidusSolidusResult',
    'MeltFractionSample',
    'build_equilibrium_crystallization_path',
    'find_liquidus_solidus_by_fraction',
)
