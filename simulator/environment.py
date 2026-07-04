"""Body/environment pressure floors for vacuum-sensitive chemistry."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

DEFAULT_VACUUM_FLOOR_BAR = 1.0e-9
MOON_VACUUM_FLOOR_BAR = 1.3e-12
ASTEROID_VACUUM_FLOOR_BAR = 1.0e-14
MARS_DATUM_PRESSURE_BAR = 6.1e-3
MARS_OLYMPUS_PRESSURE_BAR = 7.2e-4

_BODY_ALIASES = {
    "luna": "moon",
    "lunar": "moon",
    "moon": "moon",
    "asteroid": "asteroid",
    "asteroidal": "asteroid",
    "deep_space": "asteroid",
    "deep-space": "asteroid",
    "space": "asteroid",
    "mars": "mars",
    "martian": "mars",
}

_BODY_VACUUM_FLOOR_BAR = {
    "moon": MOON_VACUUM_FLOOR_BAR,
    "asteroid": ASTEROID_VACUUM_FLOOR_BAR,
    "mars": MARS_DATUM_PRESSURE_BAR,
}


def normalize_body_name(body: object) -> str:
    """Return the canonical body token, or empty string for unknown/missing."""

    if body is None:
        return ""
    token = str(body).strip().lower().replace(" ", "_")
    if not token:
        return ""
    return _BODY_ALIASES.get(token, token)


def _positive_finite_bar(value: object, *, field: str) -> float:
    try:
        pressure_bar = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric, got {value!r}") from exc
    if not math.isfinite(pressure_bar) or pressure_bar <= 0.0:
        raise ValueError(f"{field} must be finite and > 0, got {value!r}")
    return pressure_bar


def vacuum_floor_bar_for_body(body: object) -> float:
    """Resolve the chemistry vacuum floor for a body token.

    Grounding:
    - Moon: owner-reviewed nanotorr correction sets 1.3e-12 bar; NASA
      describes the lunar atmosphere as a tenuous exosphere.
    - Asteroid/deep space: owner-reviewed correction sets 1e-14 bar for
      airless small-body/deep-space processing.
    - Mars: pump-limited ambient, with 610 Pa datum and 72 Pa Olympus summit
      reference carried for CF-9 feasibility work.

    Missing or unknown bodies intentionally preserve the historical 1e-9 bar
    floor so old recipes stay golden-neutral until feedstock bodies are
    populated explicitly.
    """

    canonical = normalize_body_name(body)
    return _BODY_VACUUM_FLOOR_BAR.get(canonical, DEFAULT_VACUUM_FLOOR_BAR)


def vacuum_floor_bar_for_environment(
    *,
    body: object = None,
    ambient_pressure_bar: object = None,
) -> float:
    """Resolve the vacuum floor from explicit body plus optional ambient."""

    canonical = normalize_body_name(body)
    if canonical == "mars" and ambient_pressure_bar is not None:
        return _positive_finite_bar(
            ambient_pressure_bar,
            field="ambient_pressure_bar",
        )
    return vacuum_floor_bar_for_body(canonical)


def vacuum_floor_log10_for_environment(
    *,
    body: object = None,
    ambient_pressure_bar: object = None,
) -> float:
    return math.log10(
        vacuum_floor_bar_for_environment(
            body=body,
            ambient_pressure_bar=ambient_pressure_bar,
        )
    )


def feedstock_body(feedstock: Mapping[str, Any]) -> str:
    """Read an explicit body hook from a feedstock entry.

    No key-name inference here: missing/unknown body must default to the
    historical floor instead of silently reclassifying existing goldens.
    """

    environment = feedstock.get("environment", {}) or {}
    if not isinstance(environment, Mapping):
        environment = {}
    return normalize_body_name(
        feedstock.get("body")
        or environment.get("body")
        or feedstock.get("planetary_body")
        or environment.get("planetary_body")
    )

