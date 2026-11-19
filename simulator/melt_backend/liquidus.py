"""Liquidus / solidus finder helpers for silicate melt backends."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Mapping, Optional, Tuple

from simulator.melt_backend.base import (
    LiquidFractionInvalidError,
    MeltBackendError,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.scalar_boundary import is_declared_real_scalar
from simulator.melt_regime import liquid_fraction_invalidity


# MAGEMin-facing wall guard only. The solver's work bound is the deterministic
# sample count below; this guard refuses a hung run and never selects a
# different curve based on ordinary load. The documented cold gate search is
# about 163 s. Ten times that is 1630 s, rounded up to 1800 s (30 minutes) to
# leave normal runs ample headroom. Unit check: 163 s * 10 is seconds.
DEFAULT_LIQUIDUS_FINDER_BUDGET_S = 1800.0
MAX_LIQUIDUS_SCAN_POINTS = 100_000
# Expand a missing silicate transition only inside this temperature envelope.
# 500 C remains below typical silicate solidus temperatures; 2500 C covers
# typical high-temperature silicate melting ranges while keeping the scan finite.
LIQUIDUS_SEARCH_MIN_T_C = 500.0
LIQUIDUS_SEARCH_MAX_T_C = 2500.0
# frac_M >= 1 - LIQUIDUS_LIQUID_EPSILON is liquid. This is the finder's
# declaration tolerance. The freeze-gate consumer may snap a supplied
# liquidus fraction to 1 only inside this band. One owner: do not copy
# the literal. The solid threshold is a different rule and stays separate.
LIQUIDUS_LIQUID_EPSILON = 1.0e-3
_LIQUIDUS_TRANSITION_SUBTYPES = frozenset({'liquidus', 'solidus'})
# The statuses a SAMPLE may refuse with and still be carried out through the
# finder, so the caller learns WHICH refusal occurred rather than a generic
# solver failure.
#
# 'refused' is here because ThermoEngine already computes it as the honest
# category for two of its refusal causes -- FO2_REQUIRES_IRON and
# FO2_TARGET_NOT_FINITE, per STATUS_BY_REFUSAL_CAUSE in
# engines/alphamelts/thermoengine.py, whose own _ALLOWED_CAUSE_STATUSES is
# {out_of_domain, refused}. Those are raised as typed exceptions rather than
# returned as a status, so before this they fell to the finder's generic
# guard and became 'not_converged' -- a policy refusal to supply physics
# reported as an affirmative claim that a solve ran and failed to converge.
#
# ★ ORDER OF OPERATIONS MATTERS AND IS NOT COSMETIC (b-300): this set had to
# widen BEFORE any sampler wraps that category. LiquidusSampleError validates
# its status against this set, so handing it 'refused' while the set was the
# old three raised ValueError INSIDE the sampler, which the same generic
# guard caught, which minted 'not_converged' -- identical behaviour to the
# bug, with a typed raise sitting in the diff to convince the author it was
# fixed. Confirmed by execution before the widen.
#
# NOT widened with not_attempted, unsupported or non_authoritative: those are
# provider or intent-level tokens describing whether a probe ran at all, not
# how a frac_M sample refused. A timeout stays 'not_converged' (see
# STATUS_BY_TIMEOUT_CAUSE) rather than gaining a token, because a new member
# here would also have to exist in the kernel vocabulary or IntentResult
# construction raises.
LIQUIDUS_REFUSAL_STATUSES = frozenset({
    'not_converged',
    'out_of_domain',
    'unavailable',
    'refused',
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


def _sample_error_with_exception_context(
    exc: LiquidusSampleError,
) -> LiquidusSampleError:
    diagnostics = dict(exc.diagnostics)
    diagnostics.setdefault('exception_class', type(exc).__name__)
    diagnostics.setdefault('exception_message', str(exc))
    return LiquidusSampleError(exc.status, exc.warnings, diagnostics)


class _LiquidusNonMonotoneError(RuntimeError):
    """Expected invalid engine curve, distinct from a finder implementation bug."""


class _LiquidusCompositionInvalidError(RuntimeError):
    """Expected malformed liquid-composition sample from the engine."""


class _LiquidusFinderConfigurationError(RuntimeError):
    """Invalid temperature grid configuration rejected by the solver."""


class _LiquidusBisectionError(RuntimeError):
    """Bisection reached its declared iteration bound without convergence."""


def _sample_error_for_declared_engine_failure(
    exc: BaseException,
) -> LiquidusSampleError | None:
    """Project one declared ThermoEngine failure. Other types return None."""

    from engines.alphamelts.thermoengine import (
        DECLARED_THERMOENGINE_FAILURE_TYPES,
    )

    if not isinstance(exc, DECLARED_THERMOENGINE_FAILURE_TYPES):
        return None
    return liquidus_sample_error_from_exception(exc)


def liquidus_sample_error_from_exception(
    exc: BaseException,
) -> LiquidusSampleError | None:
    """Project a declared backend failure. Unknown types propagate."""

    if isinstance(exc, LiquidusSampleError):
        return exc
    if isinstance(exc, MeltBackendError):
        status = str(getattr(exc, 'backend_failure_category', '') or '')
        if status not in LIQUIDUS_REFUSAL_STATUSES:
            return None
        reason = (
            getattr(exc, 'backend_failure_reason_code', None)
            or getattr(exc, 'backend_status_reason', None)
            or status
        )
    else:
        # Deferred: importing the classifier module while this module is
        # still initialising crashes. The owner is the classifier module,
        # not the backend adapter that imports this module back.
        from engines.alphamelts.thermoengine import (
            thermoengine_failure_disposition_from_exception,
        )

        disposition = thermoengine_failure_disposition_from_exception(exc)
        status = disposition.status
        reason = disposition.reason_code
    diagnostics: dict[str, Any] = {
        'backend_status': status,
        'backend_status_reason': str(reason),
        'backend_failure_reason_code': str(reason),
        'backend_failure_category': status,
        'exception_class': type(exc).__name__,
        'exception_message': str(exc),
    }
    for field_name in ('requested', 'solved'):
        value = getattr(exc, field_name, None)
        if value is not None:
            diagnostics[f'{field_name}_fO2_log'] = value
    return LiquidusSampleError(status, (str(exc),), diagnostics)


@dataclass(frozen=True)
class MeltFractionSample:
    temperature_C: float
    frac_M: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            'temperature_C',
            _declared_float(self.temperature_C, 'sample_temperature_C'),
        )
        object.__setattr__(
            self,
            'frac_M',
            _declared_float(self.frac_M, 'sample_frac_M'),
        )
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
            object.__setattr__(self, 'liquidus_T_C', _declared_float(self.liquidus_T_C, 'liquidus_T_C'))
        if self.liquidus_T_K is not None:
            object.__setattr__(self, 'liquidus_T_K', _declared_float(self.liquidus_T_K, 'liquidus_T_K'))
        if self.liquidus_T_K is None and self.liquidus_T_C is not None:
            object.__setattr__(
                self,
                'liquidus_T_K',
                self.liquidus_T_C + CELSIUS_TO_KELVIN_OFFSET,
            )
        if self.liquidus_T_C is None and self.liquidus_T_K is not None:
            object.__setattr__(
                self,
                'liquidus_T_C',
                self.liquidus_T_K - CELSIUS_TO_KELVIN_OFFSET,
            )
        if self.solidus_T_C is not None:
            object.__setattr__(self, 'solidus_T_C', _declared_float(self.solidus_T_C, 'solidus_T_C'))
        if self.liquid_fraction is not None:
            object.__setattr__(
                self,
                'liquid_fraction',
                _declared_float(self.liquid_fraction, 'liquid_fraction'),
            )
        object.__setattr__(self, 'status', str(self.status))
        object.__setattr__(self, 'warnings', tuple(str(w) for w in self.warnings))
        object.__setattr__(
            self,
            'samples',
            tuple(_coerce_sample(sample) for sample in self.samples),
        )
        if not is_declared_real_scalar(self.iterations, allow_numeric_str=True):
            raise TypeError('iterations must be numeric')
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


def _liquidus_transition_temperature_K(
    result: object,
    subtype: str,
) -> float | None:
    """Kelvin of one field on a result the decision has already accepted.

    ``liquidus_transition_decision`` owns the result type, the status, and
    the subtype. This only reads the accepted field. The solidus field is
    degrees Celsius. T_K = t_C + 273.15; the interval unit is the same.
    At 0 °C the limiting value is 273.15 K.
    """

    if not isinstance(result, LiquidusSolidusResult):
        return None
    if subtype == 'liquidus':
        temperature_K = result.liquidus_T_K
    elif subtype == 'solidus' and result.solidus_T_C is not None:
        temperature_K = result.solidus_T_C + CELSIUS_TO_KELVIN_OFFSET
    else:
        return None
    if (
        temperature_K is None
        or not math.isfinite(temperature_K)
        or temperature_K <= 0.0
    ):
        return None
    return temperature_K


def liquidus_search_extension_notice(
    diagnostics: Mapping[str, Any] | None,
) -> str | None:
    """Name a search that grew past the caller window, or None.

    The finder stores the original bracket and the interval it actually
    searched. This is the only wording of that fact. The scorer copies it
    onto the produced temperature. Equal brackets are not an extension.
    A missing threshold is not an extension: that result carries its own
    reason and does not use this sentence.
    """

    if not isinstance(diagnostics, Mapping):
        return None
    # A missing threshold carries reason='not_converged' and the same bracket
    # keys. That is a refusal, not a produced temperature outside the window.
    if diagnostics.get("reason") not in (None, ""):
        return None
    original = _temperature_bracket(diagnostics.get("original_search_bracket_C"))
    searched = _temperature_bracket(diagnostics.get("bounded_search_interval_C"))
    if original is None or searched is None or original == searched:
        return None
    return (
        "liquidus finder extended beyond original search bracket "
        f"[{original[0]:g}, {original[1]:g}] C to "
        f"[{searched[0]:g}, {searched[1]:g}] C"
    )


def _temperature_bracket(value: object) -> tuple[float, float] | None:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    try:
        low = float(value[0])
        high = float(value[1])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(low) or not math.isfinite(high):
        return None
    return low, high


def liquidus_transition_decision(
    result: object,
    subtype: str,
) -> tuple[float | None, str | None]:
    """Kelvin of the requested transition, or the existing refusal reason.

    The scorer builds the prediction envelope. This is the only owner of
    which result field is the transition and which status is a refusal.
    The helper does not repeat those checks: a temperature from a refused
    result is still a refusal.
    """

    if not isinstance(result, LiquidusSolidusResult):
        return None, "liquidus_finder_result_invalid"
    if subtype not in _LIQUIDUS_TRANSITION_SUBTYPES:
        return None, "unsupported_subtype"
    if result.status != "ok":
        return None, f"liquidus_finder_{result.status}"
    temperature_K = _liquidus_transition_temperature_K(result, subtype)
    if temperature_K is None:
        return None, "liquidus_finder_requested_transition_missing"
    return temperature_K, None


@dataclass(frozen=True)
class LiquidFractionPathPoint:
    temperature_C: float
    liquid_fraction: float
    liquid_composition_wt_pct: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'temperature_C', _declared_float(self.temperature_C, 'temperature_C'))
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
            object.__setattr__(self, 'liquidus_T_C', _declared_float(self.liquidus_T_C, 'liquidus_T_C'))
        if self.liquidus_T_K is not None:
            object.__setattr__(self, 'liquidus_T_K', _declared_float(self.liquidus_T_K, 'liquidus_T_K'))
        if self.liquidus_T_K is None and self.liquidus_T_C is not None:
            object.__setattr__(
                self,
                'liquidus_T_K',
                self.liquidus_T_C + CELSIUS_TO_KELVIN_OFFSET,
            )
        if self.liquidus_T_C is None and self.liquidus_T_K is not None:
            object.__setattr__(
                self,
                'liquidus_T_C',
                self.liquidus_T_K - CELSIUS_TO_KELVIN_OFFSET,
            )
        if self.solidus_T_C is not None:
            object.__setattr__(self, 'solidus_T_C', _declared_float(self.solidus_T_C, 'solidus_T_C'))
        if self.liquid_fraction is not None:
            object.__setattr__(
                self,
                'liquid_fraction',
                _declared_float(self.liquid_fraction, 'liquid_fraction'),
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
        if not is_declared_real_scalar(self.iterations, allow_numeric_str=True):
            raise TypeError('iterations must be numeric')
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


def _threshold_bracket(
    points: Tuple[MeltFractionSample, ...],
    threshold: float,
    *,
    edge: str,
) -> tuple[MeltFractionSample, MeltFractionSample] | None:
    """One-step bracket at one edge of a threshold plateau.

    Convention: a threshold plateau is entered at one edge. Liquidus is
    the cold edge (the first temperature at or above the liquid
    threshold): the transition into the all-liquid plateau on heating.
    Solidus is the warm edge (the last temperature at or below the solid
    threshold): the transition into the all-solid plateau on cooling.

    Limiting case: frac_M equals the solid threshold from 900 °C to
    1000 °C and is above it afterwards. The solidus bracket is the
    equal-to-above pair at 1000 °C. Equality-on-the-low-side bisection
    then returns that warm edge within tolerance. The liquidus of a
    plateau sitting on the liquid threshold is the below-to-equal pair
    at the cold edge. A one-point equality has both edges at that sample.

    Equality only at an outer scan boundary, with no sample strictly on
    the far side, is not a crossing. Comparison is exact: a near miss is
    not equality. ``edge`` is ``cold`` or ``warm``; any other name raises.
    """

    if edge not in {"cold", "warm"}:
        raise ValueError(
            f"threshold edge must be 'cold' or 'warm', not {edge!r}"
        )
    strict: tuple[MeltFractionSample, MeltFractionSample] | None = None
    cold: tuple[MeltFractionSample, MeltFractionSample] | None = None
    warm: tuple[MeltFractionSample, MeltFractionSample] | None = None
    for index in range(1, len(points)):
        previous = points[index - 1]
        current = points[index]
        if (
            strict is None
            and previous.frac_M < threshold < current.frac_M
        ):
            strict = (previous, current)
        if (
            cold is None
            and previous.frac_M < threshold
            and current.frac_M == threshold
            and any(point.frac_M > threshold for point in points[index + 1:])
        ):
            cold = (previous, current)
        if (
            previous.frac_M == threshold
            and threshold < current.frac_M
            and any(point.frac_M < threshold for point in points[:index - 1])
        ):
            warm = (previous, current)
    if edge == "cold":
        return cold if cold is not None else strict
    return warm if warm is not None else strict


def find_liquidus_solidus_by_fraction(
    sample_fraction: Callable[..., float],
    *,
    min_T_C: float = 400.0,
    max_T_C: float = 2200.0,
    scan_step_C: float = 50.0,
    tolerance_C: float = 2.0,
    solid_epsilon: float = 1.0e-3,
    liquid_epsilon: float = LIQUIDUS_LIQUID_EPSILON,
    monotonicity_tolerance: float = 2.0e-2,
    monotone_smoothing_max: float = 5.0e-1,
    max_bisection_iterations: int = 32,
    budget_s: Optional[float] = None,
) -> LiquidusSolidusResult:
    """Bracket and bisect solidus/liquidus on monotone melt fraction.

    ``budget_s`` defaults to ``None``. A finite value is a generous hang
    guard, checked between samples; its residual is never passed to the
    engine. The search work is bounded by the scan grid and the two
    tolerance-bounded bisections.
    """
    from engines.alphamelts.thermoengine import (
        DECLARED_THERMOENGINE_FAILURE_TYPES,
    )

    try:
        min_T = _declared_float(min_T_C, 'min_T_C')
        max_T = _declared_float(max_T_C, 'max_T_C')
        step = _declared_float(scan_step_C, 'scan_step_C')
        tolerance = _declared_float(tolerance_C, 'tolerance_C')
        solid_threshold = _declared_float(solid_epsilon, 'solid_epsilon')
        liquid_epsilon_value = _declared_float(
            liquid_epsilon,
            'liquid_epsilon',
        )
        monotonicity_tolerance = _declared_float(
            monotonicity_tolerance,
            'monotonicity_tolerance',
        )
        monotone_smoothing_max = _declared_float(
            monotone_smoothing_max,
            'monotone_smoothing_max',
        )
        if not is_declared_real_scalar(max_bisection_iterations):
            raise TypeError('max_bisection_iterations must be numeric')
        budget = None if budget_s is None else _declared_float(budget_s, 'budget_s')
    except (TypeError, ValueError) as exc:
        return _refused_input(f'invalid finder parameter: {exc}')
    if not all(math.isfinite(value) for value in (
        min_T, max_T, step, tolerance, max_bisection_iterations,
        solid_threshold, liquid_epsilon_value, monotonicity_tolerance,
        monotone_smoothing_max,
    )):
        return _refused_input('invalid finder parameters must be finite')
    if not min_T < max_T:
        return _refused_input(
            'invalid finder window: min_T_C must be below max_T_C'
        )
    if step <= 0.0:
        return _refused_input('invalid finder scan_step_C: must be positive')
    if tolerance <= 0.0:
        return _refused_input('invalid finder tolerance_C: must be positive')
    if budget is not None and (not math.isfinite(budget) or budget <= 0.0):
        return _refused_input(
            'invalid finder budget_s: must be finite and positive'
        )

    expansion_min_T = min(min_T, LIQUIDUS_SEARCH_MIN_T_C)
    expansion_max_T = max(max_T, LIQUIDUS_SEARCH_MAX_T_C)
    try:
        grid = _bounded_scan_grid(min_T, max_T, step)
    except RuntimeError as exc:
        return _refused_input(str(exc))

    # Each bisection starts with a bracket no wider than scan_step_C and halves
    # its Celsius width. ceil(log2(step / tolerance)) steps reach the requested
    # width, with one extra step for endpoint rounding. The deterministic
    # evaluation cap is the original grid, plus expansion steps that
    # themselves fit under the scan cap, plus two capped bisections. The
    # untouched 500–2500 °C envelope is not added to the original count:
    # a fine bracket is searched first, and each expansion step is capped
    # on its own. Both the temperature ratio and logarithm argument are
    # dimensionless; this bounds calls, not seconds.
    iteration_cap = max(0, math.ceil(max_bisection_iterations))
    tolerance_steps = (
        0
        if step <= tolerance
        else max(1, math.ceil(math.log2(step) - math.log2(tolerance)) + 1)
    )
    # Expansion adds one sample for each clipped scan step beyond the
    # original endpoints. Count both lattices separately because an arbitrary
    # original minimum need not align with the 500 C envelope's grid.
    cold_expansion_points = _expansion_point_count(
        min_T,
        expansion_min_T,
        step,
    )
    warm_expansion_points = _expansion_point_count(
        max_T,
        expansion_max_T,
        step,
    )
    theoretical_expansion_points = cold_expansion_points + warm_expansion_points
    remaining_scan_points = max(0, MAX_LIQUIDUS_SCAN_POINTS - len(grid))
    maximum_grid_samples = len(grid) + min(
        theoretical_expansion_points,
        remaining_scan_points,
    )
    max_sample_evaluations = maximum_grid_samples + 2 * min(
        iteration_cap,
        tolerance_steps,
    )

    liquid_threshold = 1.0 - liquid_epsilon_value
    samples: list[MeltFractionSample] = []
    raw_fraction_by_temperature: dict[float, float] = {}
    # Mode-vector notices keyed by the sample temperature. The finder
    # annotates them when that sample takes part in a threshold decision.
    closure_notices: dict[float, dict] = {}
    smoothing_warnings: list[str] = []
    iterations = 0
    start_time = time.monotonic()
    deadline = start_time + budget if budget is not None else None
    last_T_C: Optional[float] = None
    cold_expansion_index = 0
    warm_expansion_index = 0

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

    def check_budget() -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise _LiquidusFinderBudgetExceeded(budget_warning())

    def sample(T_C: float) -> MeltFractionSample:
        nonlocal last_T_C
        check_budget()
        last_T_C = float(T_C)
        temperature_C = float(T_C)
        if temperature_C in raw_fraction_by_temperature:
            raw = raw_fraction_by_temperature[temperature_C]
        else:
            if len(raw_fraction_by_temperature) >= max_sample_evaluations:
                raise LiquidusSampleError(
                    'unavailable',
                    ('liquidus finder deterministic sample bound exceeded',),
                    {
                        'reason': 'sample_bound_exceeded',
                        'sample_limit': max_sample_evaluations,
                        'call_count': len(raw_fraction_by_temperature),
                    },
                )
            try:
                raw = _invoke_sample_fraction(
                    sample_fraction,
                    temperature_C,
                    closure_notices,
                )
            except _LiquidusFinderBudgetExceeded:
                raise
            except LiquidusSampleError as exc:
                raise _sample_error_with_exception_context(exc) from exc
            except DECLARED_THERMOENGINE_FAILURE_TYPES as exc:
                projected = _sample_error_for_declared_engine_failure(exc)
                if projected is None:
                    raise
                raise projected from exc
            except MeltBackendError as exc:
                projected = liquidus_sample_error_from_exception(exc)
                if projected is None:
                    raise
                raise projected from exc
            raw_fraction_by_temperature[temperature_C] = raw
        frac = _clamp_fraction(raw)
        point = _monotone_point(
            MeltFractionSample(temperature_C, frac),
            samples,
            tolerance=monotonicity_tolerance,
            smoothing_max=monotone_smoothing_max,
        )
        samples.append(point)
        samples.sort(key=lambda p: p.temperature_C)
        check_budget()
        return point

    try:
        for temperature_C in grid:
            sample(temperature_C)
        search_min_T = min_T
        search_max_T = max_T

        def threshold_brackets() -> tuple[
            tuple[MeltFractionSample, MeltFractionSample] | None,
            tuple[MeltFractionSample, MeltFractionSample] | None,
        ]:
            points = tuple(samples)
            return (
                _threshold_bracket(
                    points,
                    solid_threshold,
                    edge="warm",
                ),
                _threshold_bracket(
                    points,
                    liquid_threshold,
                    edge="cold",
                ),
            )

        solidus_bracket, liquidus_bracket = threshold_brackets()
        while (
            (solidus_bracket is None or liquidus_bracket is None)
            and (
                search_min_T > expansion_min_T
                or search_max_T < expansion_max_T
            )
        ):
            # Expand each edge by one existing scan step. The bounded outer
            # interval is computed from the requested bracket and the
            # 500-2500 C extension envelope above; the step is clipped at an
            # endpoint so the final sample never exceeds that interval.
            next_min_T = (
                max(expansion_min_T, min_T - (cold_expansion_index + 1) * step)
                if search_min_T > expansion_min_T
                else search_min_T
            )
            next_max_T = (
                min(expansion_max_T, max_T + (warm_expansion_index + 1) * step)
                if search_max_T < expansion_max_T
                else search_max_T
            )
            step_temperatures: list[float] = []
            if next_min_T < search_min_T:
                step_temperatures.append(next_min_T)
            if next_max_T > search_max_T:
                step_temperatures.append(next_max_T)
            # A step that adds no point is already on the envelope. A step
            # whose own count, or the scan after it, exceeds the cap is not
            # taken: the original bracket has already been searched.
            if not step_temperatures or not _expansion_step_within_scan_cap(
                len(step_temperatures),
                len(samples),
            ):
                break
            for temperature_C in step_temperatures:
                sample(temperature_C)
            if next_min_T < search_min_T:
                cold_expansion_index += 1
            if next_max_T > search_max_T:
                warm_expansion_index += 1
            search_min_T = next_min_T
            search_max_T = next_max_T
            solidus_bracket, liquidus_bracket = threshold_brackets()

        extension_diagnostics = None
        if search_min_T < min_T or search_max_T > max_T:
            extension_diagnostics = {
                'original_search_bracket_C': (min_T, max_T),
                'bounded_search_interval_C': (
                    search_min_T,
                    search_max_T,
                ),
            }
        extended_notice = liquidus_search_extension_notice(
            extension_diagnostics,
        )
        for bracket in (solidus_bracket, liquidus_bracket):
            if bracket is None:
                continue
            _annotate_renormalised_threshold(
                bracket,
                bracket[0],
                bracket[1],
                tolerance,
                closure_notices,
            )

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
                warnings=tuple([
                    *smoothing_warnings,
                    *missing,
                    'no threshold crossing within bounded search interval '
                    f'[{search_min_T:g}, {search_max_T:g}] C; original '
                    f'bracket was [{min_T:g}, {max_T:g}] C',
                ]),
                samples=tuple(samples),
                iterations=iterations,
                diagnostics={
                    'reason': 'not_converged',
                    'original_search_bracket_C': (min_T, max_T),
                    'bounded_search_interval_C': (
                        search_min_T,
                        search_max_T,
                    ),
                },
            )

        solidus, solidus_iterations = _bisect_solidus(
            sample,
            solidus_bracket[0],
            solidus_bracket[1],
            threshold=solid_threshold,
            tolerance_C=tolerance,
            max_iterations=max_bisection_iterations,
            closure_notices=closure_notices,
        )
        iterations += solidus_iterations
        liquidus, liquidus_iterations = _bisect_liquidus(
            sample,
            liquidus_bracket[0],
            liquidus_bracket[1],
            threshold=liquid_threshold,
            tolerance_C=tolerance,
            max_iterations=max_bisection_iterations,
            closure_notices=closure_notices,
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
    except (
        LiquidFractionInvalidError,
        _LiquidusNonMonotoneError,
        _LiquidusBisectionError,
    ) as exc:
        return LiquidusSolidusResult(
            status='not_converged',
            warnings=tuple([*smoothing_warnings, str(exc)]),
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
        warnings=tuple([
            *smoothing_warnings,
            *(
                [extended_notice]
                if extended_notice is not None
                and (
                    solidus.temperature_C < min_T
                    or solidus.temperature_C > max_T
                    or liquidus.temperature_C < min_T
                    or liquidus.temperature_C > max_T
                )
                else []
            ),
        ]),
        samples=tuple(samples),
        iterations=iterations,
        diagnostics=extension_diagnostics or {},
    )


def _validated_ec_configuration(
    *,
    solidus_T_C: float,
    liquidus_T_C: float,
    grid_step_C: float,
    max_points: int,
    monotonicity_tolerance: float,
    monotone_smoothing_max: float,
) -> tuple[float, float, float, float, float]:
    """Reject public EC settings before int() or math.ceil can raise."""

    try:
        solidus_T = _declared_float(solidus_T_C, 'solidus_T_C')
        liquidus_T = _declared_float(liquidus_T_C, 'liquidus_T_C')
        step = _declared_float(grid_step_C, 'grid_step_C')
        tolerance = _declared_float(
            monotonicity_tolerance,
            'monotonicity_tolerance',
        )
        smoothing = _declared_float(
            monotone_smoothing_max,
            'monotone_smoothing_max',
        )
    except TypeError as exc:
        raise _LiquidusFinderConfigurationError(str(exc)) from exc
    if not is_declared_real_scalar(max_points, allow_numeric_str=True):
        raise _LiquidusFinderConfigurationError(
            'max_points must be numeric'
        )
    if not math.isfinite(float(max_points)):
        raise _LiquidusFinderConfigurationError('max_points must be finite')
    try:
        int(max_points)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _LiquidusFinderConfigurationError(
            'max_points must be numeric'
        ) from exc
    if not math.isfinite(solidus_T) or not math.isfinite(liquidus_T):
        raise _LiquidusFinderConfigurationError(
            'invalid EC temperature bound: must be finite'
        )
    if not math.isfinite(step):
        raise _LiquidusFinderConfigurationError(
            'invalid EC grid_step_C: must be finite'
        )
    return solidus_T, liquidus_T, step, tolerance, smoothing


def _equilibrium_path_from_sample_error(
    exc: LiquidusSampleError,
    *,
    smoothing_warnings: list[str],
    path: list[LiquidFractionPathPoint],
    samples: list[MeltFractionSample],
) -> EquilibriumCrystallizationPathResult:
    exc = _sample_error_with_exception_context(exc)
    return EquilibriumCrystallizationPathResult(
        status=exc.status,
        warnings=tuple([
            *smoothing_warnings,
            *(
                f'equilibrium crystallization path failed: {warning}'
                for warning in exc.warnings
            ),
        ]),
        liquid_fraction_path=tuple(path),
        samples=tuple(samples),
        iterations=len(samples),
        diagnostics=exc.diagnostics,
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
    from engines.alphamelts.thermoengine import (
        DECLARED_THERMOENGINE_FAILURE_TYPES,
    )

    samples: list[MeltFractionSample] = []
    path: list[LiquidFractionPathPoint] = []
    smoothing_warnings: list[str] = []
    try:
        for name, value in (
            ('solidus_T_C', solidus_T_C),
            ('liquidus_T_C', liquidus_T_C),
        ):
            if (
                is_declared_real_scalar(value, allow_numeric_str=True)
                and not math.isfinite(float(value))
            ):
                raise _LiquidusFinderConfigurationError(
                    f'{name} must be finite'
                )
        (
            solidus_T,
            liquidus_T,
            grid_step_C,
            monotonicity_tolerance,
            monotone_smoothing_max,
        ) = _validated_ec_configuration(
            solidus_T_C=solidus_T_C,
            liquidus_T_C=liquidus_T_C,
            grid_step_C=grid_step_C,
            max_points=max_points,
            monotonicity_tolerance=monotonicity_tolerance,
            monotone_smoothing_max=monotone_smoothing_max,
        )
        for name, value in (
            ('monotonicity_tolerance', monotonicity_tolerance),
            ('monotone_smoothing_max', monotone_smoothing_max),
        ):
            if not math.isfinite(value):
                raise _LiquidusFinderConfigurationError(
                    f'{name} must be finite'
                )
        if not solidus_T <= liquidus_T:
            return EquilibriumCrystallizationPathResult(
                status='refused',
                warnings=('invalid EC interval: solidus_T_C exceeds liquidus_T_C',),
                diagnostics={'reason': 'invalid_input'},
            )
        temperatures = _temperature_grid(
            solidus_T,
            liquidus_T,
            grid_step_C=grid_step_C,
            max_points=max_points,
        )
    except RuntimeError as exc:
        return EquilibriumCrystallizationPathResult(
            status='refused',
            warnings=(f'invalid EC input: {exc}',),
            diagnostics={'reason': 'invalid_input'},
        )
    try:
        for temperature_C in temperatures:
            raw_fraction, raw_composition = sample_liquid_state(
                float(temperature_C)
            )
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
    except LiquidusSampleError as exc:
        return _equilibrium_path_from_sample_error(
            exc,
            smoothing_warnings=smoothing_warnings,
            path=path,
            samples=samples,
        )
    except DECLARED_THERMOENGINE_FAILURE_TYPES as exc:
        projected = _sample_error_for_declared_engine_failure(exc)
        if projected is None:
            raise
        return _equilibrium_path_from_sample_error(
            projected,
            smoothing_warnings=smoothing_warnings,
            path=path,
            samples=samples,
        )
    except MeltBackendError as exc:
        projected = liquidus_sample_error_from_exception(exc)
        if projected is None:
            raise
        return _equilibrium_path_from_sample_error(
            projected,
            smoothing_warnings=smoothing_warnings,
            path=path,
            samples=samples,
        )
    except (
        _LiquidusNonMonotoneError,
        _LiquidusCompositionInvalidError,
        _LiquidusFinderConfigurationError,
    ) as exc:
        return EquilibriumCrystallizationPathResult(
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
    closure_notices: dict[float, dict] | None = None,
) -> tuple[MeltFractionSample, int]:
    # Equality stays on the low side, so the returned sample is the highest
    # temperature still at or below the threshold. The bracket must be the
    # warm-edge pair from ``_threshold_bracket``; otherwise this is the cold
    # edge of the same plateau. Do not flip the inequality: a sample above
    # the solid threshold would break frac_M(solidus) <= solid_epsilon.
    iterations = 0
    while high.temperature_C - low.temperature_C > tolerance_C:
        if iterations >= max_iterations:
            raise _LiquidusBisectionError(
                'solidus bisection exceeded iteration bound'
            )
        mid = sample((low.temperature_C + high.temperature_C) / 2.0)
        _annotate_renormalised_threshold(
            (low, high, mid),
            low,
            high,
            tolerance_C,
            closure_notices or {},
        )
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
    closure_notices: dict[float, dict] | None = None,
) -> tuple[MeltFractionSample, int]:
    # Equality stays on the high side: the lowest temperature still at or
    # above the liquid threshold. The bracket is the cold-edge pair.
    iterations = 0
    while high.temperature_C - low.temperature_C > tolerance_C:
        if iterations >= max_iterations:
            raise _LiquidusBisectionError(
                'liquidus bisection exceeded iteration bound'
            )
        mid = sample((low.temperature_C + high.temperature_C) / 2.0)
        _annotate_renormalised_threshold(
            (low, high, mid),
            low,
            high,
            tolerance_C,
            closure_notices or {},
        )
        if mid.frac_M >= threshold:
            high = mid
        else:
            low = mid
        iterations += 1
    return high, iterations


def _annotate_renormalised_threshold(
    participants: tuple[MeltFractionSample, ...],
    left: MeltFractionSample,
    right: MeltFractionSample,
    tolerance_C: float,
    notices: Mapping[float, dict],
) -> None:
    """Attach ΔT_bound when a renormalised sample is in a threshold pair.

    The 1e-3 excess cap is derived in ``magemin.py`` (premise, algebra,
    unit check, sanity). This function evaluates that derivation's
    temperature map for one bracket. ΔT_bound = δ / s with
    s = |ΔF/ΔT| from the two bracketing samples and δ the sample's own
    raw-sum excess. Unit: δ is dimensionless and s is 1/K, so
    ΔT_bound is in K. A zero slope makes the shift unbounded. When
    ΔT_bound is missing or greater than tolerance_C the notice sets
    renormalisation_may_shift_bracket. The largest bound is kept. The
    melt fraction is not changed (d-367).
    """
    if not notices:
        return
    temperature_span = abs(right.temperature_C - left.temperature_C)
    fraction_span = abs(right.frac_M - left.frac_M)
    if temperature_span > 0.0:
        slope = fraction_span / temperature_span
    else:
        slope = 0.0
    seen: set[float] = set()
    for sample in participants:
        temperature_C = sample.temperature_C
        if temperature_C in seen:
            continue
        seen.add(temperature_C)
        notice = notices.get(temperature_C)
        if not isinstance(notice, dict):
            continue
        if notice.get('kind') != 'mode_vector_renormalised':
            continue
        try:
            delta = float(notice.get('raw_sum')) - 1.0
        except (TypeError, ValueError):
            continue
        if not math.isfinite(delta) or delta <= 0.0:
            continue
        if slope <= 0.0 or not math.isfinite(slope):
            bound = None
        else:
            bound = delta / slope
        _store_renormalisation_bound(notice, bound, tolerance_C)


def _store_renormalisation_bound(
    notice: dict,
    bound: float | None,
    tolerance_C: float,
) -> None:
    """Keep the larger ΔT_bound. None means the bracket has no slope."""
    if 'delta_T_bound' in notice and notice['delta_T_bound'] is None:
        notice['renormalisation_may_shift_bracket'] = True
        return
    if bound is None:
        notice['delta_T_bound'] = None
        notice['renormalisation_may_shift_bracket'] = True
        return
    previous = notice.get('delta_T_bound')
    if previous is None or float(bound) > float(previous):
        notice['delta_T_bound'] = float(bound)
    if bound > tolerance_C:
        notice['renormalisation_may_shift_bracket'] = True


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
            raise _LiquidusNonMonotoneError(
                'non-monotone frac_M(T): '
                f'{point.temperature_C:.3f} C gives {frac:.6g} below '
                f'{low.temperature_C:.3f} C value {low.frac_M:.6g}'
            )
        if drop > tolerance:
            raise _LiquidusNonMonotoneError(
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
            raise _LiquidusNonMonotoneError(
                'non-monotone frac_M(T): '
                f'{point.temperature_C:.3f} C gives {frac:.6g} above '
                f'{high.temperature_C:.3f} C value {high.frac_M:.6g}'
            )
        if rise > tolerance:
            raise _LiquidusNonMonotoneError(
                'non-monotone frac_M(T) would require smoothing: '
                f'{point.temperature_C:.3f} C gives raw {frac:.6g} above '
                f'{high.temperature_C:.3f} C value {high.frac_M:.6g}'
            )
        frac = min(frac, high.frac_M)
    return MeltFractionSample(point.temperature_C, frac)


def _clamp_fraction(value: float) -> float:
    frac = _declared_float(value, 'frac_M')
    if not math.isfinite(frac):
        raise LiquidFractionInvalidError(f'invalid frac_M value: {value!r}')
    if frac < -1.0e-12 or frac > 1.0 + 1.0e-12:
        raise LiquidFractionInvalidError(
            f'invalid frac_M value outside [0, 1]: {value!r}'
        )
    return max(0.0, min(1.0, frac))


def _expansion_step_within_scan_cap(
    step_point_count: int,
    scanned_point_count: int,
) -> bool:
    """Whether one expansion step fits the scan cap on its own.

    The original bracket is validated by ``_bounded_scan_grid`` before any
    sample. This does not add the untouched envelope to that count.
    """

    if step_point_count > MAX_LIQUIDUS_SCAN_POINTS:
        return False
    return scanned_point_count + step_point_count <= MAX_LIQUIDUS_SCAN_POINTS


def _expansion_point_count(
    start_T_C: float,
    endpoint_T_C: float,
    step_C: float,
) -> int:
    """Number of indexed, endpoint-clamped samples on one expansion edge."""

    distance = abs(Decimal(str(endpoint_T_C)) - Decimal(str(start_T_C)))
    step = Decimal(str(step_C))
    if distance == 0:
        return 0
    intervals = int(distance // step)
    if Decimal(intervals) * step < distance:
        intervals += 1
    return intervals


def _bounded_scan_grid(
    min_T_C: float,
    max_T_C: float,
    scan_step_C: float,
) -> Tuple[float, ...]:
    span = float(max_T_C) - float(min_T_C)
    step = float(scan_step_C)
    if float(min_T_C) + step == float(min_T_C):
        raise _LiquidusFinderConfigurationError(
            'invalid finder scan_step_C: step does not advance temperature'
        )
    interval_ratio = span / step
    if not math.isfinite(interval_ratio):
        raise RuntimeError(
            'invalid finder scan grid: interval count is not finite'
        )
    intervals = math.ceil(interval_ratio)
    point_count = intervals + 1
    if point_count > MAX_LIQUIDUS_SCAN_POINTS:
        raise _LiquidusFinderConfigurationError(
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
    step = _declared_float(grid_step_C, 'grid_step_C')
    if not is_declared_real_scalar(max_points, allow_numeric_str=True):
        raise TypeError('max_points must be numeric')
    if not math.isfinite(float(max_points)):
        raise _LiquidusFinderConfigurationError('max_points must be finite')
    point_cap = int(max_points)
    if not math.isfinite(step) or step <= 0.0:
        raise _LiquidusFinderConfigurationError(
            'invalid EC grid_step_C: must be finite and positive'
        )
    if point_cap < 2:
        raise _LiquidusFinderConfigurationError(
            'invalid EC max_points: must be at least 2'
        )
    span = float(liquidus_T_C) - float(solidus_T_C)
    if span == 0.0:
        return (float(solidus_T_C),)
    interval_ratio = span / step
    if not math.isfinite(interval_ratio):
        raise RuntimeError('invalid EC grid: interval count is not finite')
    intervals = min(max(1, math.ceil(interval_ratio)), point_cap - 1)
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
            temperature_C=_declared_float(temperature_C, 'temperature_C'),
            liquid_fraction=_declared_float(point.get('liquid_fraction'), 'liquid_fraction'),
            liquid_composition_wt_pct=point.get(
                'liquid_composition_wt_pct', {}
            ),
        )
    return LiquidFractionPathPoint(
        temperature_C=_declared_float(getattr(point, 'temperature_C'), 'temperature_C'),
        liquid_fraction=_declared_float(getattr(point, 'liquid_fraction'), 'liquid_fraction'),
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
            temperature_C=_declared_float(temperature_C, 'sample_temperature_C'),
            frac_M=_declared_float(frac_M, 'sample_frac_M'),
        )
    return MeltFractionSample(
        temperature_C=_declared_float(getattr(sample, 'temperature_C'), 'sample_temperature_C'),
        frac_M=_declared_float(getattr(sample, 'frac_M'), 'sample_frac_M'),
    )


def _coerce_composition(composition: Mapping[str, float]) -> dict[str, float]:
    result: dict[str, float] = {}
    for species, value in dict(composition or {}).items():
        amount = _declared_float(value, f'liquid_composition_wt_pct.{species}')
        if not math.isfinite(amount):
            raise _LiquidusCompositionInvalidError(
                f'invalid liquid_composition_wt_pct value for {species}: {value!r}'
            )
        if amount < 0.0:
            raise _LiquidusCompositionInvalidError(
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
    if liquid_fraction_invalidity(float(value)) is not None:
        raise ValueError(f'{name} must be in [0, 1]')


def _validate_temperature_C(value: Optional[float], name: str) -> None:
    _validate_optional_finite(value, name)
    if value is not None and float(value) < -CELSIUS_TO_KELVIN_OFFSET:
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
    # Premise: T_K = T_C + offset by thermodynamic temperature definition.
    # Algebra: residual = T_K - T_C - offset; unit check: kelvin increments
    # and Celsius increments are identical. Sanity: contradictory serialized
    # unit fields must fail; at 0 °C the offset is 273.15 K.
    if not math.isclose(
        float(temperature_K),
        float(temperature_C) + CELSIUS_TO_KELVIN_OFFSET,
        rel_tol=0.0,
        abs_tol=1.0e-9,
    ):
        raise ValueError(
            f'{context}_T_C and {context}_T_K are inconsistent'
        )


def _refused_input(message: str) -> LiquidusSolidusResult:
    return LiquidusSolidusResult(
        status='refused',
        warnings=(message,),
        diagnostics={'reason': 'invalid_input'},
    )


def _invoke_sample_fraction(
    sample_fraction: Callable[..., Any],
    temperature_C: float,
    closure_notices: dict[float, dict] | None = None,
) -> float:
    """Call one sample without coupling its timeout to wall elapsed.

    A float return is the melt fraction. A ``(fraction, notice)`` pair
    is the same fraction plus the mode-vector notice for a renormalised
    row. The notice is recorded under the sample temperature so a later
    threshold decision can attach ``delta_T_bound``.
    """
    raw = sample_fraction(float(temperature_C))
    notice = None
    if isinstance(raw, tuple):
        if len(raw) != 2:
            raise TypeError(
                'frac_M sample must be a fraction or (fraction, notice)'
            )
        raw, notice = raw
        if notice is not None and not isinstance(notice, dict):
            raise TypeError('mode closure notice must be a dict or None')
    fraction = _declared_float(raw, 'frac_M')
    if notice is not None and closure_notices is not None:
        closure_notices[float(temperature_C)] = notice
    return fraction


def _declared_float(value: Any, name: str) -> float:
    if not is_declared_real_scalar(value, allow_numeric_str=True):
        raise TypeError(f'{name} must be numeric')
    return float(value)


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
