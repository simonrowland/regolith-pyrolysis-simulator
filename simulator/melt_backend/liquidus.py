"""Liquidus / solidus finder helpers for silicate melt backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple


@dataclass(frozen=True)
class MeltFractionSample:
    temperature_C: float
    frac_M: float


@dataclass(frozen=True)
class LiquidusSolidusResult:
    liquidus_T_C: Optional[float] = None
    liquidus_T_K: Optional[float] = None
    solidus_T_C: Optional[float] = None
    status: str = 'unavailable'
    warnings: Tuple[str, ...] = ()
    samples: Tuple[MeltFractionSample, ...] = ()
    iterations: int = 0

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
        object.__setattr__(self, 'status', str(self.status))
        object.__setattr__(self, 'warnings', tuple(str(w) for w in self.warnings))
        object.__setattr__(self, 'samples', tuple(self.samples))
        object.__setattr__(self, 'iterations', int(self.iterations))


def find_liquidus_solidus_by_fraction(
    sample_fraction: Callable[[float], float],
    *,
    min_T_C: float = 400.0,
    max_T_C: float = 2200.0,
    scan_step_C: float = 50.0,
    tolerance_C: float = 2.0,
    solid_epsilon: float = 1.0e-3,
    liquid_epsilon: float = 1.0e-3,
    monotonicity_tolerance: float = 2.0e-2,
    max_bisection_iterations: int = 32,
) -> LiquidusSolidusResult:
    """Bracket and bisect solidus/liquidus on monotone melt fraction."""
    try:
        min_T = float(min_T_C)
        max_T = float(max_T_C)
        step = float(scan_step_C)
        tolerance = float(tolerance_C)
    except (TypeError, ValueError) as exc:
        return _not_converged(f'invalid finder parameter: {exc}')
    if not min_T < max_T:
        return _not_converged('invalid finder window: min_T_C must be below max_T_C')
    if step <= 0.0:
        return _not_converged('invalid finder scan_step_C: must be positive')
    if tolerance <= 0.0:
        return _not_converged('invalid finder tolerance_C: must be positive')

    liquid_threshold = 1.0 - float(liquid_epsilon)
    solid_threshold = float(solid_epsilon)
    samples: list[MeltFractionSample] = []
    iterations = 0

    def sample(T_C: float) -> MeltFractionSample:
        raw = sample_fraction(float(T_C))
        frac = _clamp_fraction(raw)
        point = _monotone_point(
            MeltFractionSample(float(T_C), frac),
            samples,
            tolerance=monotonicity_tolerance,
        )
        samples.append(point)
        samples.sort(key=lambda p: p.temperature_C)
        return point

    try:
        grid = []
        T = min_T
        while T < max_T:
            grid.append(T)
            T += step
        if not grid or grid[-1] != max_T:
            grid.append(max_T)
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
                warnings=tuple(missing),
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
    except Exception as exc:  # noqa: BLE001 - library-boundary finder guard
        return LiquidusSolidusResult(
            status='not_converged',
            warnings=(f'liquidus finder failed: {exc}',),
            samples=tuple(samples),
            iterations=iterations,
        )

    if liquidus.temperature_C < solidus.temperature_C:
        return LiquidusSolidusResult(
            status='not_converged',
            warnings=('liquidus below solidus after bisection',),
            samples=tuple(samples),
            iterations=iterations,
        )
    return LiquidusSolidusResult(
        liquidus_T_C=liquidus.temperature_C,
        solidus_T_C=solidus.temperature_C,
        status='ok',
        samples=tuple(samples),
        iterations=iterations,
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
) -> MeltFractionSample:
    lower = [p for p in samples if p.temperature_C < point.temperature_C]
    upper = [p for p in samples if p.temperature_C > point.temperature_C]
    frac = point.frac_M
    if lower:
        low = max(lower, key=lambda p: p.temperature_C)
        if frac < low.frac_M - tolerance:
            raise RuntimeError(
                'non-monotone frac_M(T): '
                f'{point.temperature_C:.3f} C gives {frac:.6g} below '
                f'{low.temperature_C:.3f} C value {low.frac_M:.6g}'
            )
        frac = max(frac, low.frac_M)
    if upper:
        high = min(upper, key=lambda p: p.temperature_C)
        if frac > high.frac_M + tolerance:
            raise RuntimeError(
                'non-monotone frac_M(T): '
                f'{point.temperature_C:.3f} C gives {frac:.6g} above '
                f'{high.temperature_C:.3f} C value {high.frac_M:.6g}'
            )
        frac = min(frac, high.frac_M)
    return MeltFractionSample(point.temperature_C, frac)


def _clamp_fraction(value: float) -> float:
    frac = float(value)
    if frac != frac or frac in (float('inf'), float('-inf')):
        raise RuntimeError(f'invalid frac_M value: {value!r}')
    return max(0.0, min(1.0, frac))


def _not_converged(message: str) -> LiquidusSolidusResult:
    return LiquidusSolidusResult(status='not_converged', warnings=(message,))


__all__ = (
    'LiquidusSolidusResult',
    'MeltFractionSample',
    'find_liquidus_solidus_by_fraction',
)
