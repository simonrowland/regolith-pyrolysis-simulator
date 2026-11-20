"""Process timestep for one simulator step.

The default step is one hour. A shorter step is a flux times ``dt``. The
one-hour value is the literal ``1.0`` hour, not ``3600 / 3600`` recomputed
at each caller, so the hourly path keeps today's bits.

This module does not import the simulator. Callers pass the numbers in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


PROCESS_HOUR_S = 3600


class ProcessTimestepRefusal(ValueError):
    """Typed refusal for a timestep that cannot be executed."""

    def __init__(
        self,
        reason: str,
        *,
        dt_s: float | None = None,
        duration_h: float | None = None,
    ) -> None:
        self.reason = str(reason)
        self.dt_s = dt_s
        self.duration_h = duration_h
        detail = self.reason
        if dt_s is not None or duration_h is not None:
            detail = (
                f"{self.reason}: dt_s={dt_s!r} duration_h={duration_h!r}"
            )
        super().__init__(detail)


@dataclass(frozen=True)
class ProcessTimestep:
    """One process step. ``dt_h`` is hours; ``dt_s`` is seconds."""

    dt_s: float
    dt_h: float

    @property
    def is_one_hour(self) -> bool:
        return self.dt_s == PROCESS_HOUR_S


ONE_HOUR = ProcessTimestep(dt_s=float(PROCESS_HOUR_S), dt_h=1.0)


def _positive_finite(value: object, reason: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProcessTimestepRefusal(reason)
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ProcessTimestepRefusal(reason, dt_s=number)
    return number


def resolve_process_timestep(dt_s: object) -> ProcessTimestep:
    """Return the process step. 3600 s is the one-hour singleton."""

    seconds = _positive_finite(dt_s, "process_dt_not_positive")
    if seconds == PROCESS_HOUR_S:
        return ONE_HOUR
    return ProcessTimestep(dt_s=seconds, dt_h=seconds / float(PROCESS_HOUR_S))


def duration_step_count(duration_h: object, timestep: ProcessTimestep) -> int:
    """How many steps of ``timestep`` fit in ``duration_h``.

    The ratio must be an integer within an absolute tolerance of 1e-8, so a
    binary 0.1 hour still divides 60 s and 3600/7 still refuses.
    """

    if isinstance(duration_h, bool) or not isinstance(duration_h, (int, float)):
        raise ProcessTimestepRefusal("duration_not_positive")
    duration = float(duration_h)
    if not math.isfinite(duration) or duration <= 0.0:
        raise ProcessTimestepRefusal(
            "duration_not_positive",
            dt_s=timestep.dt_s,
            duration_h=duration,
        )
    ratio = duration * float(PROCESS_HOUR_S) / float(timestep.dt_s)
    nearest = round(ratio)
    if nearest < 1 or not math.isclose(
        ratio,
        nearest,
        rel_tol=0.0,
        abs_tol=1.0e-8,
    ):
        raise ProcessTimestepRefusal(
            "timestep_does_not_divide_duration",
            dt_s=timestep.dt_s,
            duration_h=duration,
        )
    return int(nearest)


def step_end_sample_h(
    elapsed_h: object,
    dt_h: object,
    duration_h: object | None = None,
) -> float:
    """Time, in hours, at the end of the step that starts at ``elapsed_h``.

    The one-hour branch is ``float(elapsed) + 1.0``. A duration cap matches
    ``schedule_sample_time_h``: the sample never passes the declared window.
    """

    if float(dt_h) == 1.0:
        sample = float(elapsed_h) + 1.0
    else:
        sample = float(elapsed_h) + float(dt_h)
    if duration_h is None:
        return sample
    return min(float(duration_h), max(0.0, sample))


def advance_hour_counter(current: object, dt_h: object) -> float:
    """Advance an elapsed-hours counter by one step.

    At one hour this is ``current + 1``, so an integer counter stays an integer.
    """

    if float(dt_h) == 1.0:
        return current + 1
    return float(current) + float(dt_h)


def exponential_approach_fraction(tau_s: object, dt_s: object) -> float:
    """Exact ``1 - exp(-dt / tau)`` for a frozen first-order approach.

    The one-hour branch evaluates ``1 - exp(-3600 / tau)``. ``expm1`` is the
    short-step form: the two expressions differ in the last bits when
    ``dt / tau`` is small, and the hourly path must keep the original one.
    """

    tau = float(tau_s)
    if not math.isfinite(tau) or tau <= 0.0:
        raise ProcessTimestepRefusal(
            "exchange_tau_not_positive",
            dt_s=float(dt_s),
        )
    if float(dt_s) == float(PROCESS_HOUR_S):
        return 1.0 - math.exp(-3600.0 / tau)
    return -math.expm1(-float(dt_s) / tau)


def timeseries_t_s(step_index: object, timestep: ProcessTimestep) -> float | None:
    """Seconds at the end of step ``step_index``, or None on the one-hour path.

    Hourly rows keep no ``t_s`` key. Readers of those rows use ``hour * 3600``.
    """

    if timestep.is_one_hour:
        return None
    return float(step_index) * float(timestep.dt_s)
