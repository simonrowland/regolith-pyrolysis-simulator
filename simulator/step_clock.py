"""Committed simulation-step timing and interval bookkeeping.

Durations are hours. Rates integrate over ``dt_hr`` into interval amounts;
snapshot rates divide those amounts by the committed duration. Whole-hour
steps retain integer clock advances. The simulator applies the same duration
to overhead bleed (kg/s × seconds), bubbler and capacity flow (per-hour × h),
oxygen-reservoir exchange (``1 - exp(-dt_s / tau_s)``), native-iron flux,
MRE interval products, and campaign temperature ramps (°C/h × h). Keeping
these conversions tied to the committed clock avoids mixing an interval
amount with a rate or advancing campaign time by a reference hour.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from simulator.scalar_boundary import coerce_nonnegative_hours


def initialize_committed_step_state(simulator) -> None:
    simulator._dt_hr = 1.0
    simulator._last_committed_duration_hr = 1.0
    simulator._last_committed_campaign_interval_start_h = 0.0
    simulator._pending_shuttle_bakeout_cycle_increment = 0


def dt_hr(simulator) -> float:
    """Return the duration of the interval currently being simulated."""

    return simulator._dt_hr


def set_dt_hr(simulator, duration_h: float) -> None:
    """Set the active interval duration through the clock owner."""

    simulator._dt_hr = duration_h


def reset_campaign_interval_state(simulator) -> None:
    simulator._last_committed_campaign_interval_start_h = 0.0
    simulator._pending_shuttle_bakeout_cycle_increment = 0


def melt_time_fields(melt) -> dict[str, float]:
    return {
        "hour": getattr(melt, "hour", 0),
        "campaign_hour": getattr(melt, "campaign_hour", 0),
    }


def next_melt_hour(melt, dt_hr: float) -> float:
    return melt.hour + (1 if dt_hr == 1.0 else dt_hr)


def source_context_time_fields(
    source_context,
    prefix: str = "source",
) -> dict[str, float]:
    return {
        f"{prefix}_hour": coerce_nonnegative_hours(source_context.get("hour", 0)),
        f"{prefix}_campaign_hour": coerce_nonnegative_hours(
            source_context.get("campaign_hour", 0)
        ),
    }


def advance_melt_clock(melt, dt_hr: float) -> float:
    """Advance physical clocks and return the interval's campaign start."""

    campaign_start_h = float(melt.campaign_hour)
    # Keep whole-hour serialization stable for existing campaign pins.
    next_hour = next_melt_hour(melt, dt_hr)
    delta_h = next_hour - melt.hour
    melt.hour = next_hour
    melt.campaign_hour += delta_h
    return campaign_start_h


@contextmanager
def committed_snapshot_duration(simulator) -> Iterator[None]:
    """Expose the active interval duration while materializing its snapshot."""

    previous_dt_hr = simulator._last_committed_duration_hr
    simulator._last_committed_duration_hr = dt_hr(simulator)
    try:
        yield
    finally:
        simulator._last_committed_duration_hr = previous_dt_hr


def commit_interval(simulator, campaign_start_h: float) -> None:
    simulator._last_committed_duration_hr = dt_hr(simulator)
    simulator._last_committed_campaign_interval_start_h = campaign_start_h
    pending = simulator._pending_shuttle_bakeout_cycle_increment
    if simulator.melt.campaign.name == "C3_K":
        simulator.shuttle_cycle_K += pending
    elif simulator.melt.campaign.name == "C3_NA":
        simulator.shuttle_cycle_Na += pending
    simulator._pending_shuttle_bakeout_cycle_increment = 0


def projected_shuttle_cycle(simulator) -> int:
    committed = (
        simulator.shuttle_cycle_K
        if simulator.melt.campaign.name == "C3_K"
        else simulator.shuttle_cycle_Na
    )
    return committed + simulator._pending_shuttle_bakeout_cycle_increment


@dataclass(frozen=True)
class CommittedStepProjection:
    duration_h: float
    shuttle_cycle: int
    shuttle_injected_kg_hr: float
    shuttle_reduced_kg_hr: float
    shuttle_metal_produced_kg_hr: float
    mre_metals_kg_hr: dict[str, float]

    def shuttle_rate_fields(self) -> dict[str, object]:
        return {
            "shuttle_injected_kg_hr": self.shuttle_injected_kg_hr,
            "shuttle_reduced_kg_hr": self.shuttle_reduced_kg_hr,
            "shuttle_metal_produced_kg_hr": self.shuttle_metal_produced_kg_hr,
        }


def project_committed_step(simulator) -> CommittedStepProjection:
    duration_h = simulator._last_committed_duration_hr
    return CommittedStepProjection(
        duration_h=duration_h,
        shuttle_cycle=projected_shuttle_cycle(simulator),
        shuttle_injected_kg_hr=simulator._shuttle_injected_this_hr / duration_h,
        shuttle_reduced_kg_hr=simulator._shuttle_reduced_this_hr / duration_h,
        shuttle_metal_produced_kg_hr=(
            simulator._shuttle_metal_this_hr / duration_h
        ),
        mre_metals_kg_hr={
            species: kg / duration_h
            for species, kg in simulator._mre_metals_this_hr.items()
        },
    )
