"""Score-time pressure for an unprinted condensed liquidus or solidus.

d-280 (owner 2026-10-23). A ``transition_temperature`` liquidus or solidus
with no printed total pressure, and no high-pressure apparatus on the row,
is scored at 1 bar. The notice reason is exactly ``ambient pressure assumed,
not printed``. This module does not write the store: the caller keeps the
observation's pressure axis as it was recorded.

The slope that makes 1 bar negligible is the equilibrium Clapeyron slope,
(dT/dP) = ΔV/ΔS. A typical silicate fusion volume is about 5 cm³/mol, which
is 5×10⁻⁶ m³/mol. Forsterite fusion is about 114 kJ/mol near 2163 K, so
ΔS is about 114000/2163 ≈ 53 J/mol/K. Then dT/dP ≈ 5×10⁻⁶/53 ≈ 9×10⁻⁸ K/Pa,
about 0.01 K/bar. The usual petrology figure, about 10 °C/kbar, is the same
slope. A shift of at most 1 bar, including the 0.013 bar gap between 1 atm
and 1 bar, moves the temperature by ≲ 0.01 K, below a liquidus uncertainty
of several kelvin. A piston-cylinder at 10 kbar is a 10⁴ bar shift and
about 100 K, so an unprinted high-pressure experiment still refuses. The
scorer does not apply this slope as a correction.

The caller has already established that the row printed no total pressure.
This module does not re-read the pressure axis and does not treat an
inferred or invalid pressure as ambient.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal

from simulator.battery.enums import MethodToken, NoticeKind, Quantity
from simulator.battery.records import (
    Bench,
    Experiment,
    Located,
    Locator,
    Notice,
    Observation,
)
from simulator.physical_constants import BAR_PA_DECIMAL, PA_PER_BAR
from simulator.state_types import State

AMBIENT_PRESSURE_ASSUMED_REASON = "ambient pressure assumed, not printed"

# Closed apparatus class from d-280. Optional plurals are the same words.
# A separator run covers the hyphen, space, underscore, and jammed forms.
# Bare "cylinder", "press", "GPa", and "kbar" are not this class.
_HIGH_PRESSURE_APPARATUS = re.compile(
    r"piston[\s_-]*cylinders?"
    r"|multi[\s_-]*anvils?"
    r"|diamond[\s_-]*anvils?"
    r"|\bautoclaves?\b",
    re.IGNORECASE,
)
# Migrator boilerplate names the keys it did not find, including P_GPa.
# That sentence is not an apparatus.
_PRESSURE_KEY_LIST = "no pressure field under keys"

UnprintedPressureBlock = str


@dataclass(frozen=True)
class UnprintedLiquidusPressure:
    """1 bar assumption for one unprinted liquidus or solidus row."""

    pressure_bar: float
    pressure_Pa: Decimal
    notice: Notice


def unprinted_liquidus_pressure_block(
    observation: Observation,
    experiment: Experiment | None,
    bench: Bench | None = None,
) -> UnprintedPressureBlock | None:
    """Why this row must keep refusing, or None when 1 bar may be assumed.

    ``knudsen_closed_cell`` is the method token only (d-142). A citation of
    Knudsen or KEMS in prose does not count. ``high_pressure_apparatus`` is
    the piston-cylinder token or a closed apparatus phrase on this row.
    """

    if _knudsen_closed_cell(experiment):
        return "knudsen_closed_cell"
    if _piston_cylinder_token(experiment):
        return "high_pressure_apparatus"
    if any(
        _names_high_pressure_apparatus(text)
        for text in _method_texts(observation, experiment, bench)
    ):
        return "high_pressure_apparatus"
    return None


def unprinted_liquidus_solidus_pressure(
    observation: Observation,
    experiment: Experiment | None,
    bench: Bench | None = None,
) -> UnprintedLiquidusPressure | None:
    """1 bar and the d-280 notice, or None when the caller must still refuse.

    None leaves the caller's existing ``missing_input`` / ``total_pressure_Pa``
    refusal unchanged. The returned pressure is 1 bar exactly
    (``BAR_PA_DECIMAL``). The observation is not modified.
    """

    if (
        unprinted_liquidus_pressure_block(observation, experiment, bench)
        is not None
    ):
        return None
    pressure_Pa = BAR_PA_DECIMAL
    return UnprintedLiquidusPressure(
        pressure_bar=float(pressure_Pa) / PA_PER_BAR,
        pressure_Pa=pressure_Pa,
        notice=Notice(
            kind=NoticeKind.AMBIENT_PRESSURE_ASSUMED,
            affected_quantities=(Quantity.TRANSITION_TEMPERATURE,),
            reason=AMBIENT_PRESSURE_ASSUMED_REASON,
            origin=observation.observation_id,
        ),
    )


def assumed_transition_pressure(
    notices: Sequence[Notice],
) -> State[Decimal] | None:
    """Comparison-axis pressure when this prediction assumed 1 bar.

    The value is the same ``BAR_PA_DECIMAL`` the finder was called with.
    Absent the d-280 notice, this returns None and does not invent a pressure.
    """

    if not any(
        notice.kind is NoticeKind.AMBIENT_PRESSURE_ASSUMED for notice in notices
    ):
        return None
    return State.of(BAR_PA_DECIMAL)


def _knudsen_closed_cell(experiment: Experiment | None) -> bool:
    if experiment is None:
        return False
    method = experiment.method
    return (
        method is not None
        and method.is_value
        and method.value is MethodToken.KNUDSEN_EFFUSION
    )


def _piston_cylinder_token(experiment: Experiment | None) -> bool:
    if experiment is None:
        return False
    method = experiment.method
    return (
        method is not None
        and method.is_value
        and method.value is MethodToken.PISTON_CYLINDER
    )


def _names_high_pressure_apparatus(text: str) -> bool:
    if _PRESSURE_KEY_LIST in text.casefold():
        return False
    return _HIGH_PRESSURE_APPARATUS.search(text) is not None


def _located_str(located: object) -> str | None:
    if not isinstance(located, Located):
        return None
    state = located.state
    if not state.is_value or not isinstance(state.value, str):
        return None
    text = state.value.strip()
    return text or None


def _locator_prose(locator: Locator | None) -> Iterator[str]:
    if locator is None:
        return
    for text in (locator.note, locator.section):
        if isinstance(text, str) and text.strip():
            yield text


def _method_texts(
    observation: Observation,
    experiment: Experiment | None,
    bench: Bench | None,
) -> Iterator[str]:
    """Prose on this experiment, this observation, and its bench.

    Point-condition state reasons are omitted: they are the migrator's
    key list, not the apparatus. ``pressure_environment`` is omitted for
    the same reason.
    """

    if experiment is not None:
        method = experiment.method
        if method is not None and isinstance(method.reason, str) and method.reason.strip():
            yield method.reason
        yield from _locator_prose(experiment.locator)
        sample = experiment.sample
        for name in ("form", "container", "characterization", "pretreatment"):
            text = _located_str(getattr(sample, name))
            if text is not None:
                yield text
        apparatus = experiment.apparatus
        if apparatus is not None:
            text = _located_str(apparatus.cell_material_and_liner)
            if text is not None:
                yield text
        schedule = experiment.thermal_schedule
        if schedule is not None:
            for name in ("method", "cooling_or_quench"):
                text = _located_str(getattr(schedule, name))
                if text is not None:
                    yield text
    yield from _locator_prose(observation.locator)
    for located in (observation.point_conditions or {}).values():
        locator = getattr(located, "locator", None)
        if isinstance(locator, Locator):
            yield from _locator_prose(locator)
    if bench is None:
        return
    for name in (
        "apparatus_family",
        "method",
        "heating_method",
        "cell_material_and_liner",
    ):
        text = _located_str(getattr(bench, name))
        if text is not None:
            yield text
