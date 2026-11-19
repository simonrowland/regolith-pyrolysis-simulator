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
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
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
# Bare "cylinder", "press", "GPa", "kbar", and "IHPV" are not this class.
# Internally heated pressure vessels are the same class: an unprinted
# run in one is not an ambient 1 bar experiment.
# Externally heated pressure vessels are a distinct high-pressure design.
# Cold-seal pressure vessels are another common high-pressure design.
# Gas-medium apparatus and presses use a confining gas as the pressure medium.
# Belt apparatus includes belt and belt-type spellings used in petrology.
_HIGH_PRESSURE_APPARATUS = re.compile(
    r"piston[\s_-]*cylinders?"
    r"|multi[\s_-]*anvils?"
    r"|diamond[\s_-]*anvils?"
    r"|internally[\s_-]*heated[\s_-]*pressure[\s_-]*vessels?"
    r"|externally[\s_-]*heated[\s_-]*pressure[\s_-]*vessels?"
    r"|cold[\s_-]*seal[\s_-]*pressure[\s_-]*vessels?"
    r"|gas[\s_-]*medium[\s_-]*(?:apparatus|press(?:es)?)"
    r"|belt[\s_-]*(?:type[\s_-]*)?apparatus"
    r"|\bautoclaves?\b",
    re.IGNORECASE,
)
_AMBIENT_FURNACE_RUN = re.compile(
    r"\b(?:run|runs|experiment|experiments)\b.{0,100}"
    r"\b(?:1\s*(?:atm|atmosphere|bar)|ambient)\b.{0,50}"
    r"\bfurnaces?\b",
    re.IGNORECASE,
)
_APPARATUS_ASSIGNMENT_VERB = (
    r"used|employed|utilized|measured|produced|generated|collected|obtained|"
    r"processed|heated|equilibrated|operated|prepared|treated|performed|"
    r"conducted|run|ran"
)
_APPARATUS_ACTIVE_ASSIGNMENT = re.compile(
    rf"(?<!not )\b(?:{_APPARATUS_ASSIGNMENT_VERB})\b",
    re.IGNORECASE,
)
_APPARATUS_ASSIGNMENT_BEFORE = re.compile(
    rf"(?<!not )\b(?:{_APPARATUS_ASSIGNMENT_VERB})\b[^.!?;]{{0,60}}"
    r"\b(?:in|using|with)\s+(?:(?:the|a|an)\s+)?$",
    re.IGNORECASE,
)
_ANAPHORIC_APPARATUS_ASSIGNMENT = re.compile(
    rf"(?:^|[.!?;])\s*(?:it|this apparatus|that apparatus|this press|that press)\b"
    rf"[^.!?;]*(?<!not )\b(?:{_APPARATUS_ASSIGNMENT_VERB})\b",
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
        _high_pressure_apparatus_assigned_to_run(text)
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


def _high_pressure_apparatus_assigned_to_run(text: str) -> bool:
    """Fail closed on apparatus mentions except an explicitly excluded one.

    An exclusion only permits the ambient route when this same note assigns
    an ambient furnace to the run. Other negative or unclear contexts remain
    ambiguous and therefore keep the high-pressure refusal.
    """

    if _PRESSURE_KEY_LIST in text.casefold():
        return False
    apparatus_mentions = tuple(_HIGH_PRESSURE_APPARATUS.finditer(text))
    if not apparatus_mentions:
        return False
    if _AMBIENT_FURNACE_RUN.search(text) is None:
        return True
    return any(
        not _apparatus_is_explicitly_excluded(text, apparatus)
        for apparatus in apparatus_mentions
    )


def _apparatus_is_explicitly_excluded(
    text: str,
    apparatus: re.Match[str],
) -> bool:
    """An apparatus exclusion must name that mention in the same sentence."""

    sentence_start = max(
        (text.rfind(mark, 0, apparatus.start()) for mark in ".!?;"),
        default=-1,
    ) + 1
    sentence_end = min(
        (
            index
            for mark in ".!?;"
            if (index := text.find(mark, apparatus.end())) >= 0
        ),
        default=len(text),
    )
    sentence = text[sentence_start:sentence_end]
    apparatus_start = apparatus.start() - sentence_start
    apparatus_end = apparatus.end() - sentence_start
    after = sentence[apparatus_end:]
    apparatus_phrase = re.escape(apparatus.group())
    before_exclusion = re.search(
        rf"\b(?:did\s+not\s+(?:use|employ)|excluded)\s+"
        rf"(?:(?:the|a|an)\s+)?{apparatus_phrase}\s*$",
        sentence[:apparatus_end],
        re.IGNORECASE,
    )
    after_exclusion = re.match(
        r"^\s*(?:(?:was|were)\s+)?not\s+(?:used|employed|utilized)\b|"
        r"^\s*(?:was|were)?\s+excluded\b",
        after,
        re.IGNORECASE,
    )
    if before_exclusion is None and after_exclusion is None:
        return False

    before = sentence[:apparatus_start]
    if _APPARATUS_ASSIGNMENT_BEFORE.search(before) is not None:
        return False
    if after_exclusion is not None:
        after_exclusion_tail = after[after_exclusion.end():]
        if _APPARATUS_ACTIVE_ASSIGNMENT.search(after_exclusion_tail) is not None:
            return False
    elif before_exclusion is not None:
        if _APPARATUS_ACTIVE_ASSIGNMENT.search(after) is not None:
            return False

    continuation = text[apparatus.end():]
    if _ANAPHORIC_APPARATUS_ASSIGNMENT.search(continuation) is not None:
        return False
    return True


def _locator_prose(locator: Locator | None) -> Iterator[str]:
    if locator is None:
        return
    for text in (locator.note, locator.section):
        if isinstance(text, str) and text.strip():
            yield text


def _source_value_prose(value: object) -> Iterator[str]:
    """Printed prose. Unknown-state reasons are migrator boilerplate.

    A ``State`` reason is the key list the migrator writes when a field
    is absent. The source's own sentence is a located value, such as
    ``pressure_environment.cell_internal_pressure_note``.
    """

    if isinstance(value, State):
        return
    if isinstance(value, Located):
        if value.state.is_value:
            yield from _source_value_prose(value.state.value)
        if isinstance(value.locator, Locator):
            yield from _locator_prose(value.locator)
        return
    if isinstance(value, Locator):
        yield from _locator_prose(value)
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text
        return
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _source_value_prose(item)
        return
    if isinstance(value, tuple):
        for item in value:
            yield from _source_value_prose(item)
        return
    if is_dataclass(value) and not isinstance(value, type):
        for item in fields(value):
            yield from _source_value_prose(getattr(value, item.name))


def _bench_prose(bench: Bench) -> Iterator[str]:
    """Equipment and pressure prose. Bench ids and citation titles are not it.

    ``identity.reason`` is the embedded-evidence sentence required when the
    bench was inferred. ``identity.ref.cited_as`` is another paper's title.
    """

    for item in fields(bench):
        if item.name in {"id", "work_id", "identity"}:
            continue
        yield from _source_value_prose(getattr(bench, item.name))
    reason = bench.identity.reason
    if isinstance(reason, str) and reason.strip():
        yield reason.strip()


def _method_texts(
    observation: Observation,
    experiment: Experiment | None,
    bench: Bench | None,
) -> Iterator[str]:
    """Prose on this experiment, this observation, and its bench.

    Unknown-state reasons stay out: they are the migrator's key list.
    Source-backed equipment and pressure values stay in, including the
    cell-internal pressure note the migrator stores beside that list.
    """

    if experiment is not None:
        method = experiment.method
        if method is not None and isinstance(method.reason, str) and method.reason.strip():
            yield method.reason
        yield from _locator_prose(experiment.locator)
        sample = experiment.sample
        for name in ("form", "container", "characterization", "pretreatment"):
            yield from _source_value_prose(getattr(sample, name))
        if experiment.apparatus is not None:
            yield from _source_value_prose(experiment.apparatus)
        yield from _source_value_prose(experiment.pressure_environment)
        if experiment.ambient_pressure_evidence is not None:
            yield from _source_value_prose(experiment.ambient_pressure_evidence)
        if experiment.thermal_schedule is not None:
            yield from _source_value_prose(experiment.thermal_schedule)
    yield from _locator_prose(observation.locator)
    # Point-condition values are the printed number or composition. Their
    # unknown-state reasons are the migrator's key list. Locator notes on
    # those conditions are source prose and stay in.
    for located in (observation.point_conditions or {}).values():
        locator = getattr(located, "locator", None)
        if isinstance(locator, Locator):
            yield from _locator_prose(locator)
    if bench is not None:
        yield from _bench_prose(bench)
