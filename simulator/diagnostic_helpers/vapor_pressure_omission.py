"""Shared validation for provider-recorded vapor-pressure floor omissions."""

from __future__ import annotations

import math
from collections.abc import Mapping


VAPOR_PRESSURE_OMISSION_CHANNEL_STATE = "evaluated_below_reporting_floor"


def vapor_pressure_omission_proof(
    record: object,
) -> tuple[float, float] | None:
    """Return threshold and computed pressure when the record is valid."""

    if not isinstance(record, Mapping):
        return None
    try:
        threshold_pa = float(record.get("reporting_threshold_Pa"))
        computed_pa = float(record.get("computed_pressure_Pa"))
    except (TypeError, ValueError):
        return None
    if (
        not math.isfinite(threshold_pa)
        or not math.isfinite(computed_pa)
        or computed_pa < 0.0
        or computed_pa > threshold_pa
    ):
        return None
    return threshold_pa, computed_pa
